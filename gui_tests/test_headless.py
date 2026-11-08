"""
Drives the add-on inside a real, headless Anki with simulated 8BitDo Micro input.

Run: uv run --no-project --with "aqt[qt6]==25.9.5" --with pytest pytest gui_tests

No gamepad is attached: the JavaScript side is bypassed and the messages it would send
("contanki::on_connect::…", "contanki::poll::…") are fed straight to the Python handler.
"""

import os
import shutil
import sys
import time
from pathlib import Path

import pytest

ADDON = Path(__file__).resolve().parents[1] / "contanki"
MICRO_ID = "8BitDo Micro gamepad (Vendor: 2dc8 Product: 9020)"

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--disable-gpu --no-sandbox")
os.environ.setdefault("ANKI_NOHIGHDPI", "1")
os.environ.setdefault("DISABLE_QT5_COMPAT", "1")


def pump(seconds: float) -> None:
    from aqt.qt import QApplication

    end = time.monotonic() + seconds
    while time.monotonic() < end:
        QApplication.processEvents()
        time.sleep(0.01)


def wait_until(condition, timeout: float = 60, what: str = "condition") -> None:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if condition():
            return
        pump(0.05)
    raise AssertionError(f"timed out waiting for {what}")


@pytest.fixture(scope="module")
def mw(tmp_path_factory):
    base = tmp_path_factory.mktemp("anki_base")
    addons = base / "addons21"
    addons.mkdir()
    shutil.copytree(
        ADDON, addons / "contanki", ignore=shutil.ignore_patterns("__pycache__", "meta.json")
    )

    import anki.lang
    import aqt
    from aqt.profiles import ProfileManager

    # Skip the first-run language dialog and the update checks, whose modal dialogs
    # would block a headless run.
    anki.lang.set_lang("en_US")
    seed = ProfileManager(base)
    seed.setupMeta()
    seed.meta["defaultLang"] = "en_US"
    seed.meta["check_for_updates"] = False
    seed.meta["last_addon_update_check"] = 2**31
    seed.db.execute("update profiles set data = ? where name = '_global'", seed._pickle(seed.meta))
    seed.db.commit()
    seed.db.close()

    app = aqt._run(argv=["anki", "-b", str(base)], exec=False)
    assert app is not None
    main_window = aqt.mw
    wait_until(
        lambda: main_window.col is not None and main_window.state == "deckBrowser",
        what="profile to load",
    )
    assert hasattr(main_window, "contanki"), "add-on was not loaded"
    # Offscreen windows never get focus, which the add-on treats as "ignore input".
    sys.modules["contanki.contanki"].get_state = lambda: "deckBrowser"
    yield main_window

    done = {}
    main_window.unloadProfile(lambda: done.setdefault("ok", True))
    wait_until(lambda: done, 30, "profile to unload")
    app.quit()
    pump(0.5)


def send(mw, message: str) -> None:
    handled = mw.contanki.on_receive_message((False, None), f"contanki::{message}", None)
    assert handled == (True, None)


def poll(mw, pressed=(), x=0.0, y=0.0) -> None:
    """One poll from a Micro: 16 buttons, 10 axes, D-pad on axes 0 and 1."""
    buttons = ",".join("true" if i in pressed else "false" for i in range(16))
    axes = ",".join(str(v) for v in [x, y] + [0.0] * 8)
    send(mw, f"poll::{buttons}::{axes}")


def bind(contanki, button: int, action: str) -> None:
    # The tests run on the deck browser, whose own bindings override the "all" ones.
    contanki.profile.bindings[("deckBrowser", button)] = action


@pytest.fixture
def micro(mw):
    send(mw, f"on_connect::16::10::{MICRO_ID}")
    contanki = mw.contanki
    assert contanki.connected and contanki.profile is not None
    assert contanki.profile.controller == "8BitDo Micro"
    poll(mw)
    yield contanki
    send(mw, "on_disconnect::test finished")


def test_javascript_helpers_load(mw):
    result = {}
    mw.contanki.evalWithCallback("typeof stop_polling", lambda v: result.setdefault("v", v))
    wait_until(lambda: "v" in result, what="JavaScript result")
    assert result["v"] == "function"


def test_smooth_scroll_stops_when_dpad_released(micro, mw):
    bind(micro, 102, "Scroll Up Smooth")  # D-pad up
    poll(mw, y=-1.0)
    assert micro.scroll_up
    poll(mw)
    assert not micro.scroll_up


def test_second_press_in_same_direction_registers(micro, mw):
    bind(micro, 102, "Scroll Up Smooth")
    for _ in range(2):
        poll(mw, y=-1.0)
        assert micro.scroll_up
        poll(mw)
        assert not micro.scroll_up


def test_show_quick_select_hides_on_release_of_axis_button(micro, mw):
    bind(micro, 101, "Show Quick Select")  # D-pad right
    poll(mw, x=1.0)
    assert micro.quick_select.is_shown
    poll(mw)
    assert not micro.quick_select.is_shown


def test_toggle_quick_select_closes_with_same_button(micro, mw):
    bind(micro, 7, "Toggle Quick Select")  # R1
    poll(mw, pressed={7})
    assert micro.quick_select.is_shown
    poll(mw)
    poll(mw, pressed={7})
    assert not micro.quick_select.is_shown
    poll(mw)


def test_quick_select_follows_axis_dpad(micro, mw):
    assert micro.profile.controller.dpad_buttons == (102, 103, 100, 101)
    bind(micro, 7, "Toggle Quick Select")
    poll(mw, pressed={7})
    assert micro.quick_select.is_shown
    poll(mw, y=-1.0)  # D-pad up selects the top item
    assert micro.quick_select.current_action == micro.quick_select.actions["deckBrowser"][0]
    poll(mw)
    poll(mw, pressed={7})
    assert not micro.quick_select.is_shown
    poll(mw)


def test_disconnect_clears_state(micro, mw):
    bind(micro, 102, "Scroll Up Smooth")
    poll(mw, y=-1.0)
    assert micro.scroll_up
    send(mw, "on_disconnect::controller lost")
    assert not micro.connected
    assert not micro.scroll_up
    assert micro.profile is None
    # Reconnecting must not resume the old scroll.
    send(mw, f"on_connect::16::10::{MICRO_ID}")
    assert micro.connected and not micro.scroll_up


def test_unusable_controller_is_rejected_quietly(mw):
    send(mw, "on_connect::16::10::8BitDo Micro gamepad (STANDARD GAMEPAD)")
    assert not mw.contanki.connected
    # A stray poll from before polling stopped must not raise.
    poll(mw)
