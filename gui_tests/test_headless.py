"""
Drives the add-on inside a real, headless Anki with simulated 8BitDo Micro input.

Run: uv run --no-project --with "aqt[qt6]==25.9.5" --with pytest pytest gui_tests

No gamepad is attached: the JavaScript side is bypassed and the messages it would send
("contanki::on_connect::…", "contanki::poll::…") are fed straight to the Python handler.
"""

import json
import os
import shutil
import sys
import time
from pathlib import Path

import pytest

ADDON = Path(__file__).resolve().parents[1] / "contanki"
DIAGNOSTICS = Path(__file__).resolve().parents[1] / "diagnostics" / "contanki_diagnostics"
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
    shutil.copytree(
        DIAGNOSTICS,
        addons / "contanki_diagnostics",
        ignore=shutil.ignore_patterns("__pycache__", "meta.json"),
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


def poll(mw, pressed=(), x=0.0, y=0.0, axes=None) -> None:
    """One poll from a Micro: 16 buttons, 10 axes, D-pad on axes 0 and 1."""
    buttons = ",".join("true" if i in pressed else "false" for i in range(16))
    axes = ",".join(str(v) for v in (axes or [x, y] + [0.0] * 8))
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
    # That item is Quit, which closing the menu would run.
    micro.quick_select.current_action = ""
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


def eval_js(web, expression: str):
    result = {}
    web.evalWithCallback(expression, lambda value: result.setdefault("value", value))
    wait_until(lambda: "value" in result, what=f"JavaScript result of {expression!r}")
    return result["value"]


def load_page(mw, body: str) -> None:
    """Replaces the main web view's content and scrolls everything back to the top."""
    js_body = body.replace("\\", "\\\\").replace("`", "\\`")
    eval_js(mw.web, f"document.body.innerHTML = `{js_body}`; window.scrollTo(0, 0); 1")


TALL = '<div style="height: 5000px"></div>'
FULL_SCREEN_BOX = (
    '<div id="box" style="position: fixed; inset: 0; overflow: auto">' + TALL + "</div>"
)
CORNER_BOX = (
    '<div id="box" style="width: 100px; height: 100px; overflow: auto">' + TALL + "</div>"
)


def test_scroll_down_moves_half_a_view_at_default_speed(micro, mw):
    # Issue 137: Scroll Down used to jump to the bottom on macOS.
    bind(micro, 103, "Scroll Down")
    load_page(mw, TALL)
    poll(mw, y=1.0)
    poll(mw)
    scrolled, view = eval_js(mw.web, "[window.scrollY, window.innerHeight]")
    assert abs(scrolled - view / 2) <= 1


def test_scroll_moves_a_full_screen_container(micro, mw):
    # Issue 155: note types and pop-ups that scroll inside a box did not scroll.
    bind(micro, 103, "Scroll Down")
    load_page(mw, FULL_SCREEN_BOX)
    poll(mw, y=1.0)
    poll(mw)
    scrolled, view = eval_js(
        mw.web, "[document.getElementById('box').scrollTop, document.getElementById('box').clientHeight]"
    )
    assert abs(scrolled - view / 2) <= 1


def test_scroll_falls_back_to_largest_box_when_page_cannot_scroll(micro, mw):
    bind(micro, 103, "Scroll Down")
    load_page(mw, CORNER_BOX)
    poll(mw, y=1.0)
    poll(mw)
    assert eval_js(mw.web, "document.getElementById('box').scrollTop") == 50


def test_smooth_scroll_keeps_going_while_held_and_stops_on_release(micro, mw):
    bind(micro, 103, "Scroll Down Smooth")
    load_page(mw, TALL)
    poll(mw, y=1.0)  # one report: the controller only reports changes
    pump(0.5)
    poll(mw)
    held = eval_js(mw.web, "window.scrollY")
    # Scroll Speed 10: 10 px every 50 ms while held.
    assert held >= 50 and held % 10 == 0
    pump(0.3)
    assert eval_js(mw.web, "window.scrollY") == held
    assert not micro.motion_timer.isActive()


def test_held_stick_scrolls_without_new_reports(micro, mw):
    micro.profile.axes_bindings[1] = "Scroll Vertical"
    try:
        load_page(mw, TALL)
        poll(mw, y=1.0)
        pump(0.5)
        moved = eval_js(mw.web, "window.scrollY")
        assert moved > 100
        poll(mw)
        pump(0.1)
        stopped = eval_js(mw.web, "window.scrollY")
        pump(0.3)
        assert eval_js(mw.web, "window.scrollY") == stopped
    finally:
        micro.profile.axes_bindings[1] = "Buttons"


def test_scroll_down_in_the_reviewer(micro, mw):
    model = mw.col.models.by_name("Basic")
    note = mw.col.new_note(model)
    note["Front"] = '<div style="height: 5000px">tall card</div>'
    mw.col.add_note(note, mw.col.decks.id("Default"))
    mw.col.decks.select(mw.col.decks.id("Default"))
    mw.moveToState("review")
    wait_until(
        lambda: mw.state == "review"
        and eval_js(mw.web, "document.documentElement.scrollHeight") > 4000,
        what="the tall card to show",
    )
    bind(micro, 103, "Scroll Down")
    poll(mw, y=1.0)
    poll(mw)
    scrolled, view = eval_js(mw.web, "[window.scrollY, window.innerHeight]")
    mw.moveToState("deckBrowser")
    assert abs(scrolled - view / 2) <= 1


def test_hat_switch_dpad_on_macos(micro, mw):
    # Issue 154: on macOS 26 the Micro reports its D-pad as a hat in one axis, which
    # rests at 9/7 and used to read as held.
    def hat(direction):
        return [0.0] * 9 + [2 * direction / 7 - 1]

    bind(micro, 101, "Scroll Down Smooth")  # D-pad right
    bind(micro, 102, "Scroll Up Smooth")  # D-pad up
    poll(mw, axes=hat(8))
    assert not micro.scroll_up and not micro.scroll_down
    poll(mw, axes=hat(2))
    assert micro.scroll_down
    poll(mw, axes=hat(8))
    assert not micro.scroll_down
    poll(mw, axes=hat(0))
    assert micro.scroll_up
    poll(mw, axes=hat(8))
    assert not micro.scroll_up


FAKE_PAD_JS = """
window.__pad = {
    index: 0, id: "%s", mapping: "", connected: true, timestamp: 0,
    buttons: Array.from({length: 16}, () => ({pressed: false, value: 0})),
    axes: [0, 0, 0, 0, 0, 0, 0, 0, 0, 9 / 7],
};
window.__setPad = (pressed, hat) => {
    __pad.buttons.forEach((b, i) => { b.pressed = pressed.includes(i); b.value = b.pressed ? 1 : 0; });
    __pad.axes[9] = hat === null ? 9 / 7 : 2 * hat / 7 - 1;
    __pad.timestamp += 1;
};
Object.defineProperty(navigator, "getGamepads", {value: () => [__pad], configurable: true});
1
""" % MICRO_ID


def test_diagnostics_run_through(mw, tmp_path):
    os.environ["CONTANKI_DIAGNOSTICS_DIR"] = str(tmp_path)
    sys.modules["contanki_diagnostics"].open_dialog()
    web = mw.contanki_diagnostics.web
    wait_until(lambda: eval_js(web, "typeof window.diagSaved") == "function", what="page")
    eval_js(web, FAKE_PAD_JS)

    def set_pad(pressed=(), hat=None):
        eval_js(web, f"__setPad({json.dumps(list(pressed))}, {json.dumps(hat)}); 1")

    # Rest: 1.5 s grace plus 3 s of recording.
    wait_until(lambda: eval_js(web, "rest !== null"), what="rest measurement")
    set_pad(hat=0)  # D-pad up on a macOS-style hat
    pump(0.5)
    set_pad()
    wait_until(lambda: eval_js(web, "report.inputs.length") == 1, what="first input")
    for _ in range(16):
        eval_js(web, "onSkip && onSkip(); 1")
        pump(0.05)
    wait_until(lambda: eval_js(web, "report.inputs.length") == 17, what="all inputs")
    for _ in range(10):
        set_pad(pressed=[0])
        pump(0.08)
        set_pad()
        pump(0.08)
    wait_until(lambda: eval_js(web, "report.taps.length") == 1, what="tap test")
    eval_js(web, "onSkip && onSkip(); 1")
    wait_until(lambda: eval_js(web, "!!document.getElementById('finish')"), 30, "notes step")
    eval_js(web, "document.getElementById('notes').value = 'D mode'; document.getElementById('finish').click(); 1")
    wait_until(lambda: eval_js(web, "window.diagDone === true"), what="report saved")

    (saved,) = tmp_path.glob("contanki-diagnostics-*.json")
    report = json.loads(saved.read_text())
    assert report["rest"]["hat_like_axes"] == [9]
    first = report["inputs"][0]
    assert first["label"] == "D-pad Up" and first["status"] == "ok"
    assert [a["index"] for a in first["changes"]["axes"]] == [9]
    assert first["changes"]["axes"][0]["value"] == -1
    assert all(i["status"] == "skipped" for i in report["inputs"][1:])
    taps = report["taps"][0]
    assert taps["status"] == "ok" and len(taps["taps"]) == 10
    assert len(report["bridge_round_trip_ms"]) == 20
    assert report["notes"] == "D mode"
    assert report["environment"]["anki"]
    assert any(
        r.get("identified_as") and r["identified_as"][0] == "8BitDo Micro"
        for r in report["contanki"]
    )
    hidden = report["hidden_view"]
    assert hidden["seconds_recorded"] > 0 and hidden["interval_ms_median"]
    mw.contanki_diagnostics.close()


def test_release_while_unfocused_still_releases(micro, mw):
    module = sys.modules["contanki.contanki"]
    bind(micro, 0, "Scroll Up Smooth")
    poll(mw, pressed={0})
    assert micro.scroll_up
    module.get_state = lambda: "NoFocus"
    try:
        poll(mw)  # released while Anki is in the background
        assert not micro.scroll_up
    finally:
        module.get_state = lambda: "deckBrowser"
    poll(mw, pressed={0})  # the next press must not be lost
    assert micro.scroll_up
    poll(mw)


def test_reconnecting_does_not_leak_widgets(mw):
    from aqt.qt import QCoreApplication, QEvent, QWidget

    quick = sys.modules["contanki.quick"]
    overlay = sys.modules["contanki.overlay"]

    def count():
        # deleteLater deletions run from Anki's main event loop; processEvents alone skips them.
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        return (
            len(mw.findChildren(quick.QuickSelectButton)),
            len(mw.findChildren(overlay.OverlayItem)),
            len([w for w in mw.findChildren(QWidget) if w.parent() is mw]),
        )

    send(mw, f"on_connect::16::10::{MICRO_ID}")
    first = count()
    for _ in range(5):
        send(mw, f"on_connect::16::10::{MICRO_ID}")
    assert count() == first
    send(mw, "on_disconnect::test finished")


def test_quick_select_settings_are_not_shared(mw):
    quick = sys.modules["contanki.quick"]
    first = quick.QuickSelectMenu(None, {"Select with D-Pad": False})
    second = quick.QuickSelectMenu(None, {})
    try:
        assert first.settings["Select with D-Pad"] is False
        assert second.settings["Select with D-Pad"] is True
    finally:
        first.destroy()
        second.destroy()


def test_config_is_not_rewritten_on_every_read(mw):
    funcs = sys.modules["contanki.funcs"]
    writes = []
    original = mw.addonManager.writeConfig
    mw.addonManager.writeConfig = lambda *args: writes.append(args)
    try:
        funcs.get_config()
        funcs.get_config()
    finally:
        mw.addonManager.writeConfig = original
    assert writes == []


CONTANKI_FAKE_PAD_JS = """
window.__pad = {
    index: 0, id: "%s", mapping: "", connected: true, timestamp: 0,
    buttons: Array.from({length: 16}, () => ({pressed: false, value: 0})),
    axes: [0, 0, 0, 0, 0, 0, 0, 0, 0, 0],
};
window.__press = (pressed) => {
    __pad.buttons[0].pressed = pressed;
    __pad.buttons[0].value = pressed ? 1 : 0;
};
Object.defineProperty(navigator, "getGamepads", {value: () => [__pad], configurable: true});
on_controller_connect();
1
""" % MICRO_ID


def test_javascript_reports_only_changes_and_catches_quick_taps(mw):
    """End to end through Contanki's own poller, with a fake gamepad."""
    contanki = mw.contanki
    reports = []
    original = contanki.poll
    contanki.poll = lambda *args: (reports.append(args), original(*args))
    try:
        eval_js(contanki, CONTANKI_FAKE_PAD_JS)
        wait_until(lambda: contanki.connected and reports, what="the poller to start")
        pump(2.2)
        # Idle: one first report plus the once-a-second heartbeat, instead of 20 a second.
        assert len(reports) <= 4
        before = len(reports)
        eval_js(contanki, "__press(true); setTimeout(() => __press(false), 30); 1")
        pump(0.3)
        tap = [r for r in reports[before:] if r[0].startswith("true")]
        assert tap, "a 30 ms tap was missed"
        assert reports[-1][0].startswith("false")
    finally:
        contanki.poll = original
        eval_js(contanki, "stop_polling(); connected_index = null; 1")
        send(mw, "on_disconnect::test finished")
