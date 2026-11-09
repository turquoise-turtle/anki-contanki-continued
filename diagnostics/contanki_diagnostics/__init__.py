"""
Contanki Diagnostics: a guided run-through that records how Anki sees a controller.

It is a separate add-on so it works whether or not Contanki itself loads. It reads the
controller through the same embedded Chromium as Contanki, recreates Contanki's hidden
polling web view to measure its timing, and saves everything to a JSON report.
"""

from __future__ import annotations

import json
import os
import platform
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from aqt import mw
from aqt.qt import QAction, QDialog, QVBoxLayout, qconnect
from aqt.utils import tooltip
from aqt.webview import AnkiWebView

TOOL_VERSION = "1.1"
ADDON_DIR = Path(__file__).resolve().parent


def report_dir() -> Path:
    """Where reports are saved: the Desktop, or the home folder if there is none."""
    if override := os.environ.get("CONTANKI_DIAGNOSTICS_DIR"):
        return Path(override)
    desktop = Path.home() / "Desktop"
    return desktop if desktop.is_dir() else Path.home()


def environment() -> dict[str, Any]:
    """Versions of everything between the controller and Contanki."""
    from aqt import appVersion  # pylint: disable=import-outside-toplevel
    from aqt.qt import PYQT_VERSION_STR, qVersion  # pylint: disable=import-outside-toplevel

    try:
        from PyQt6.QtWebEngineCore import (  # pylint: disable=import-outside-toplevel
            qWebEngineChromiumVersion,
        )

        chromium = qWebEngineChromiumVersion()
    except Exception:  # pylint: disable=broad-except
        chromium = None
    return {
        "anki": appVersion,
        "qt": qVersion(),
        "pyqt": PYQT_VERSION_STR,
        "chromium": chromium,
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "macos": platform.mac_ver()[0] or None,
        "machine": platform.machine(),
    }


def contanki_installs() -> list[dict[str, Any]]:
    """Every loaded copy of Contanki, with its version and settings."""
    installs = []
    for name, module in list(sys.modules.items()):
        if not name.endswith(".controller") or not hasattr(module, "identify_controller"):
            continue
        package = name.rsplit(".", 1)[0]
        manifest = ADDON_DIR.parent / package / "manifest.json"
        try:
            version = json.loads(manifest.read_text(encoding="utf8")).get("human_version")
        except (OSError, ValueError):
            version = None
        installs.append(
            {
                "package": package,
                "version": version,
                "config": mw.addonManager.getConfig(package),
                "module": module,
            }
        )
    return installs


def identify(pads: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """What each installed Contanki would make of each pad."""
    results = []
    for install in contanki_installs():
        detect_8bitdo = bool((install["config"] or {}).get("Detect 8BitDo Controllers"))
        for pad in pads:
            try:
                found = install["module"].identify_controller(
                    pad["id"], pad["buttons"], pad["axes"], detect_8bitdo
                )
                result: Any = list(found) if found else None
            except Exception as err:  # pylint: disable=broad-except
                result = f"error: {err!r}"
            results.append(
                {
                    "package": install["package"],
                    "version": install["version"],
                    "detect_8bitdo": detect_8bitdo,
                    "pad": pad["id"],
                    "identified_as": result,
                }
            )
    live = getattr(mw, "contanki", None)
    if live is not None:
        profile = getattr(live, "_profile", None)
        results.append(
            {
                "package": "running instance (mw.contanki)",
                "connected": getattr(live, "connected", None),
                "profile": str(profile) if profile else None,
                "controller": str(profile.controller) if profile else None,
            }
        )
    return results


HIDDEN_VIEW_SCRIPT = """
<script>
// Mirrors Contanki's poller: a 50 ms interval in a 0x0 web view that is never shown.
let last = performance.now(), intervals = [], updates = 0, lastStamp = null, samples = 0;
setInterval(() => {
    const now = performance.now();
    intervals.push(now - last);
    last = now;
    const pad = Array.from(navigator.getGamepads()).find(p => p);
    samples += 1;
    if (pad && pad.timestamp !== lastStamp) {
        updates += 1;
        lastStamp = pad.timestamp;
    }
}, 50);
setInterval(() => {
    pycmd("hidden:" + JSON.stringify({
        t: Date.now(),
        intervals: intervals.map(v => Math.round(v * 10) / 10),
        polls: samples,
        pad_updates: updates,
        pad_seen: Array.from(navigator.getGamepads()).some(p => p),
        visibility: document.visibilityState,
        focused: document.hasFocus(),
    }));
    intervals = [];
    updates = 0;
    samples = 0;
}, 1000);
</script>
"""


class DiagnosticsDialog(QDialog):
    """The run-through window."""

    def __init__(self) -> None:
        super().__init__(mw)
        self.setWindowTitle("Controller Diagnostics")
        self.resize(960, 760)
        self.hidden_stats: list[dict[str, Any]] = []
        self.identified: list[dict[str, Any]] = []
        self.saved_path: Path | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.web = AnkiWebView(parent=self, title="contanki_diagnostics")
        self.web.set_bridge_command(self.on_message, self)
        layout.addWidget(self.web)

        script = (ADDON_DIR / "diagnostics.js").read_text(encoding="utf8")
        style = (ADDON_DIR / "diagnostics.css").read_text(encoding="utf8")
        setup = (
            f"window.DIAG_ENV = {json.dumps(environment())};"
            f"window.DIAG_TOOL_VERSION = {json.dumps(TOOL_VERSION)};"
        )
        self.web.stdHtml(
            f"<style>{style}</style><div id='app'></div>"
            f"<script>{setup}\n{script}</script>",
            context=self,
        )

        # Recreated exactly as Contanki makes its poller: a child of the main window,
        # fixed at 0x0 and never shown.
        self.hidden = AnkiWebView(parent=mw, title="contanki_diagnostics_hidden")
        self.hidden.set_bridge_command(self.on_hidden_message, self)
        self.hidden.setFixedSize(0, 0)
        self.hidden.stdHtml(HIDDEN_VIEW_SCRIPT, context=self)

    def on_hidden_message(self, message: str) -> None:
        if message.startswith("hidden:"):
            self.hidden_stats.append(json.loads(message[len("hidden:") :]))
            # A long session should not grow without bound.
            del self.hidden_stats[:-600]

    def on_message(self, message: str) -> Any:
        kind, _, payload = message.partition(":")
        if kind != "diag":
            return None
        command, _, data = payload.partition(":")
        if command == "ping":
            self.web.eval(f"diagPong({json.dumps(data)}, {time.time() * 1000})")
        elif command == "identify":
            self.identified = identify(json.loads(data))
            self.web.eval(f"diagIdentified({json.dumps(self.identified)})")
        elif command == "save":
            self.save(json.loads(data))
        return None

    def save(self, report: dict[str, Any]) -> None:
        report["environment"] = environment()
        report["contanki"] = identify(report.get("pads", []))
        report["hidden_view"] = summarise_hidden(self.hidden_stats)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        path = report_dir() / f"contanki-diagnostics-{stamp}.json"
        path.write_text(json.dumps(report, indent=1), encoding="utf8")
        self.saved_path = path
        self.web.eval(f"diagSaved({json.dumps(str(path))}, {json.dumps(report['hidden_view'])})")
        tooltip(f"Saved {path.name}")

    def closeEvent(self, event) -> None:  # pylint: disable=invalid-name
        self.hidden.cleanup()
        self.hidden.deleteLater()
        self.web.cleanup()
        super().closeEvent(event)


def summarise_hidden(stats: list[dict[str, Any]]) -> dict[str, Any]:
    """Interval and update figures for the hidden view, plus the raw per-second records."""
    intervals = sorted(v for s in stats for v in s.get("intervals", []))

    def percentile(share: float) -> float | None:
        if not intervals:
            return None
        return intervals[min(len(intervals) - 1, int(share * len(intervals)))]

    return {
        "seconds_recorded": len(stats),
        "interval_ms_median": percentile(0.5),
        "interval_ms_p95": percentile(0.95),
        "interval_ms_max": intervals[-1] if intervals else None,
        "polls_per_second_mean": (
            sum(s["polls"] for s in stats) / len(stats) if stats else None
        ),
        "seconds_with_pad_updates": sum(1 for s in stats if s.get("pad_updates")),
        "visibility_states": sorted({s.get("visibility") for s in stats}),
        "pad_ever_seen": any(s.get("pad_seen") for s in stats),
        "per_second": stats[-120:],
    }


def open_dialog() -> None:
    dialog = DiagnosticsDialog()
    dialog.show()
    mw.contanki_diagnostics = dialog  # type: ignore[attr-defined]


if mw is not None:
    action = QAction("Controller Diagnostics", mw)
    qconnect(action.triggered, open_dialog)
    mw.form.menuTools.addAction(action)
