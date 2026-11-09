"""An add-on for Anki, adding support for controllers and gamepads"""

from __future__ import annotations

from functools import partial
from typing import Any, Callable
from collections import defaultdict

from aqt import gui_hooks
from aqt.qt import QAction, QTimer, qconnect
from aqt.utils import current_window, tooltip
from aqt.webview import AnkiWebView

from .quick import QuickSelectMenu
from .icons import IconHighlighter
from .config import ContankiConfig
from .funcs import (
    get_config,
    get_custom_actions,
    get_state,
    move_mouse_build,
    scroll_build,
    smooth_scroll_build,
)
from .utils import State, get_file, DEBUG, dbg
from .overlay import ControlsOverlay
from .controller import identify_controller
from .profile import (
    Profile,
    get_profile,
    find_profile,
)
from .actions import button_actions, release_actions, update_actions
from .axis_buttons import (
    AXIS_BUTTON_OFFSET,
    AxisButtons,
    HatSwitches,
    axis_button_pressed,
)

from aqt import mw as _mw

assert _mw is not None
mw = _mw

move_mouse = move_mouse_build()
scroll = scroll_build()
smooth_scroll_step = smooth_scroll_build()

# The controller only reports changes, so held sticks and smooth scrolling are driven by
# a timer instead, at the rate the old fixed poll ran.
MOTION_INTERVAL_MS = 50


class Contanki(AnkiWebView):
    """Main add-on object. The webview contains JavaScript code that interfaces with
    the controller, and this class handles translating the controller's input into
    actions and managing other aspects of the add-on"""

    connected = False
    config = get_config()
    overlay: ControlsOverlay | None = None
    quick_select = QuickSelectMenu(None, {})
    buttons: list[bool] = []
    axes: list[bool] = []
    len_buttons = 0
    len_axes = 0
    icons = IconHighlighter()
    controllers: list[QAction] = list()
    debug_info: list[list[str]] = []
    custom_actions = get_custom_actions()
    _script = get_file("controller.js")
    if _script is None:
        raise FileNotFoundError("controller.js not found")
    script = _script
    scroll_up = False
    scroll_down = False

    def __init__(self, parent):
        super().__init__(parent=parent)
        self.axis_buttons = AxisButtons()
        self.hats = HatSwitches()
        self.latest_axes: list[float] = []
        # The screen each held input was pressed on, so its release matches its press.
        self.press_states: dict[int, State] = {}
        self.motion_timer = QTimer(self)
        self.motion_timer.setInterval(MOTION_INTERVAL_MS)
        qconnect(self.motion_timer.timeout, self.on_motion_tick)
        mw.addonManager.setConfigAction(__name__, self.on_config)
        self.menu_item = QAction("Controller Options", mw)
        qconnect(self.menu_item.triggered, self.on_config)
        gui_hooks.webview_did_receive_js_message.append(self.on_receive_message)
        gui_hooks.profile_will_close.append(self.suspend)
        gui_hooks.profile_did_open.append(self.resume)
        # Polls no longer arrive continuously, so an always-on overlay is refreshed when
        # the screen changes instead.
        gui_hooks.state_did_change.append(self.refresh_overlay)
        gui_hooks.reviewer_did_show_question.append(self.refresh_overlay)
        gui_hooks.reviewer_did_show_answer.append(self.refresh_overlay)
        self.resume()
        self.profile = None
        self.update_debug_info()

        if DEBUG:
            self.mock_item = QAction("Mock Controller", mw)
            qconnect(self.mock_item.triggered, lambda: self.eval("mock_controller()"))
            mw.form.menuTools.addAction(self.mock_item)
            self.setFixedSize(10, 10)
            from .tests import run_tests  # pylint: disable=import-outside-toplevel

            run_tests()
        else:
            self.setFixedSize(0, 0)

    def suspend(self):
        """Suspends JS execution. Necessary to prevent crash when Anki exits."""
        self.stdHtml("")

    def resume(self):
        """Resumes the add-on"""
        self.stdHtml(f"""<script type="text/javascript">\n{self.script}\n</script>""")

    @property
    def profile(self) -> Profile | None:
        """Returns the profile object"""
        return self._profile

    @profile.setter
    def profile(self, profile: Profile | str | None) -> None:
        """Sets the profile object"""
        self.config = get_config()
        if isinstance(profile, str):
            profile = get_profile(profile)
        self._profile = profile
        if profile is None:
            return
        if self.overlay is not None:
            self.overlay.close()
        if self.quick_select is not None:
            self.quick_select.destroy()
        self.overlay = ControlsOverlay(mw, profile)
        self.quick_select = QuickSelectMenu(self, profile.quick_select)
        self.quick_select.update_icon(profile.controller)
        update_actions()
        globals()["move_mouse"] = move_mouse_build()
        globals()["scroll"] = scroll_build()
        globals()["smooth_scroll_step"] = smooth_scroll_build()
        self.custom_actions = get_custom_actions()

    def on_config(self) -> None:
        """Opens the config dialog"""
        if focus := current_window():
            ContankiConfig(focus, self, self.profile)

    def on_receive_message(
        self, handled: tuple[bool, Any], message: str, _
    ) -> tuple[bool, Any]:
        """Called when a message is received from the JavaScript interface"""
        funcs: dict[str, Callable] = {
            "on_connect": self.on_connect,
            "on_disconnect": self.on_disconnect,
            "poll": self.poll,
            "register": self.register_controllers,
            "initialise": lambda *args, **kwargs: None,
        }

        if message[:8] == "contanki":
            _, func, *args = message.split("::")
            if func == "message":
                tooltip(str("::".join(args)))
            else:
                funcs[func](*args)
            return (True, None)
        else:
            return handled

    def on_error(self, _error: str) -> None:
        """Reinitialises the controller when an error occurs."""
        dbg(_error)
        self.eval("on_controller_disconnect()")

    def if_connected(func: Callable) -> Callable:  # pylint: disable=no-self-argument
        """Checks if the controller is connected before running a function."""

        def if_connected_wrapper(self, *args, **kwargs):
            if self.profile is None:
                self.on_error(f"Function '{func}' called with no profile loaded")
            elif not self.connected:
                self.on_error(f"Function '{func}' called when controller not connected")
            else:
                func(self, *args, **kwargs)

        return if_connected_wrapper

    @if_connected
    def poll(self, input_buttons: str, input_axes: str) -> None:
        """
        Handles a report of the controller's state. The JavaScript side sends one when
        any input changes, and once a second regardless.
        """
        buttons, axes = self.parse_controller_inputs(input_buttons, input_axes)

        if not buttons:
            self.on_error("No buttons")
            return

        changed = [(i, v) for i, v in enumerate(buttons) if v != self.buttons[i]]
        self.buttons = buttons

        assert self.profile is not None
        button_axes = [
            axis
            for axis, role in self.profile.axes_bindings.items()
            if role == "Buttons"
        ]
        axis_changes = self.axis_buttons.update(axes, button_axes)
        self.latest_axes = axes

        # State is tracked above even without focus: reports only arrive on changes, so
        # skipping them would leave a button that was released elsewhere looking held,
        # and its next press would be lost.
        state = get_state()
        if state in ("NoFocus", "config"):
            # No new actions here, but anything already held is still released.
            self.release_held(changed + axis_changes)
            if state == "config":
                # The dialog highlights axes its own way; the tracker only has to keep up.
                self.handle_poll_in_config(axes, changed)
            return

        changed += axis_changes

        if self.quick_select.is_shown:
            self.update_quick_select(state, buttons, axes, changed)
            return

        for i, value in changed:
            self.do_action(state, i, not value)

        self.update_motion()

        assert self.overlay is not None
        if self.config["Overlays Always On"]:
            self.overlay.appear(state)

    def refresh_overlay(self, *_) -> None:
        """Updates an always-on overlay for the current screen."""
        if (
            self.connected
            and self.overlay is not None
            and self.config["Overlays Always On"]
        ):
            self.overlay.appear(get_state())

    def motion_needed(self) -> bool:
        """Whether a stick is pushed past the deadzone or a smooth scroll is held."""
        if self.scroll_up or self.scroll_down:
            return True
        if self.profile is None:
            return False
        deadzone = self.config["Stick Deadzone"] / 100
        movements = self.axis_movements(self.latest_axes)
        return any(
            abs(movements[x]) + abs(movements[y]) >= deadzone and (movements[x] or movements[y])
            for x, y in (
                ("Cursor Horizontal", "Cursor Vertical"),
                ("Scroll Horizontal", "Scroll Vertical"),
            )
        )

    def update_motion(self) -> None:
        """Runs the motion timer only while there is something to move."""
        if not self.motion_needed():
            self.motion_timer.stop()
        elif not self.motion_timer.isActive():
            self.motion_timer.start()
            self.on_motion_tick()

    def parse_controller_inputs(
        self, input_buttons: str, input_axes: str
    ) -> tuple[list[bool], list[float]]:
        """Parses the controller inputs"""
        buttons = [button == "true" for button in input_buttons.split(",")]
        axes = [float(axis) for axis in input_axes.split(",")]
        while len(self.buttons) < len(buttons):
            self.buttons.append(buttons[len(self.buttons)])
        # A controller can report more axes than it did when it connected.
        while len(self.axes) < len(axes):
            self.axes.append(False)
        assert self.profile is not None
        self.hats.apply(buttons, axes, self.profile.controller.dpad_buttons)
        self.controller_specific_fixes(buttons, axes)
        return buttons, axes

    def controller_specific_fixes(self, buttons: list[bool], axes: list[float]):
        """Fixes for specific controllers"""
        assert self.profile is not None
        if (
            self.profile.controller.parent == "8BitDo Zero (X Input)"
            and len(buttons) >= 16
            and len(axes) >= 4
            and any(axes)
            and not any(buttons[12:16])
        ):
            buttons[12] = axes[1] < -0.5 or axes[3] < -0.5
            buttons[13] = axes[1] > 0.5 or axes[3] > 0.5
            buttons[14] = axes[0] < -0.5 or axes[2] < -0.5
            buttons[15] = axes[0] > 0.5 or axes[2] > 0.5

        if (
            self.profile.controller.parent == "8BitDo Zero (D Input)"
            and any(axes)
            and len(axes) > 5
        ):
            axes[0] = axes[0] or axes[2]
            axes[1] = axes[1] or axes[5]

    def handle_poll_in_config(
        self, axes: list[float], changed: list[tuple[int, bool]]
    ) -> None:
        """Handles the polling of the controller when in the config dialog"""
        for i, value in enumerate(axes):
            pressed = abs(value) > 0.5
            if pressed != self.axes[i]:
                changed.append((i + 200, pressed))
                if pressed:
                    changed.append((i * 2 + 100 + (value > 0), pressed))
                    changed.append((i * 2 + 100 + (value < 0), not pressed))
                else:
                    changed.append((i * 2 + 100, pressed))
                    changed.append((i * 2 + 101, pressed))
                self.axes[i] = pressed
        for i, value in changed:
            self.icons.set_highlight(i, value)

    @staticmethod
    def input_pressed(index: int, buttons: list[bool], axes: list[float]) -> bool:
        """Whether a button or axis button is held, whatever role its axis has."""
        if index >= AXIS_BUTTON_OFFSET:
            return axis_button_pressed(index, axes)
        return index < len(buttons) and buttons[index]

    @if_connected
    def update_quick_select(
        self,
        state: State,
        buttons: list[bool],
        axes: list[float],
        changed: list[tuple[int, bool]],
    ) -> None:
        """
        Update the quick select menu. Other actions are not triggered while it is open,
        but releasing a held Show Quick Select or pressing Toggle Quick Select closes it.
        """
        assert self.profile is not None
        assert self.overlay is not None
        if not self.quick_select.is_shown:
            return

        for index, pressed in changed:
            action = self.profile.get(state, index)
            if (action == "Show Quick Select" and not pressed) or (
                action == "Toggle Quick Select" and pressed
            ):
                self.press_states.pop(index, None)
                self.hide_quick_select()
                return
        self.release_held(changed)

        dpad = self.profile.controller.dpad_buttons
        if (
            self.quick_select.settings["Select with D-Pad"]
            and dpad is not None
            and any(self.input_pressed(index, buttons, axes) for index in dpad)
        ):
            up, down, left, right = (
                self.input_pressed(index, buttons, axes) for index in dpad
            )
            self.quick_select.dpad_select(state, (up, down, left, right))
        elif self.profile.controller.has_stick and len(axes) >= 2:
            self.quick_select.stick_select(state, axes[0], axes[1])

        pressed_now = {index for index, pressed in changed if pressed}
        if (
            (stick_button := self.profile.controller.stick_button) is not None
            and self.quick_select.settings["Do Action on Stick Press"]
            and stick_button in pressed_now
        ):
            self.quick_select.disappear(True)
        elif 0 in pressed_now:
            self.quick_select.disappear(True)

    @if_connected
    def show_quick_select(self, state: State) -> None:
        """Shows the quick select menu"""
        if not self.quick_select.is_shown:
            self.quick_select.appear(state)
        if (
            self.overlay is not None
            and self.config["Enable Control Overlays"]
            and self.config["Overlays in Quick Select"]
        ):
            self.overlay.appear(state)

    @if_connected
    def hide_quick_select(self) -> None:
        """Hides the quick select menu and the overlays if they are shown."""
        self.quick_select.disappear()
        if self.overlay is not None:
            self.overlay.disappear()

    @if_connected
    def toggle_quick_select(self, state: State) -> None:
        """Toggles the quick select menu and overlays."""
        if self.quick_select.is_shown or (
            self.overlay is not None
            and self.overlay.is_shown
            and not self.config["Overlays Always On"]
        ):
            self.hide_quick_select()
        else:
            self.show_quick_select(state)

    @if_connected
    def do_action(self, state: State, button: int, release: bool = False) -> None:
        """Calls the appropriate function on button press."""
        assert self.profile is not None
        if release:
            return self.do_release_action(self.press_states.pop(button, state), button)
        self.press_states[button] = state
        action = self.profile.get(state, button)
        if action == "Toggle Quick Select":
            self.toggle_quick_select(state)
        elif action == "Show Quick Select":
            self.show_quick_select(state)
        else:
            try:
                if action in button_actions:
                    button_actions[action]()
                elif action in self.custom_actions:
                    self.custom_actions[action]()
            except Exception as err:  # pylint: disable=broad-except
                tooltip("Error: " + repr(err))

    def release_held(self, changed: list[tuple[int, bool]]) -> None:
        """Runs the release action of each input in `changed` whose press was handled."""
        for button, pressed in changed:
            if not pressed and button in self.press_states:
                self.do_release_action(self.press_states.pop(button), button)

    @if_connected
    def do_release_action(self, state: State, button: int) -> None:
        """Calls the appropriate function on button release."""
        assert self.profile is not None
        action = self.profile.get(state, button)
        if action == "Show Quick Select":
            self.hide_quick_select()
        elif action in release_actions:
            try:
                release_actions[action]()
            except Exception as err:  # pylint: disable=broad-except
                tooltip("Error: " + repr(err))

    def axis_movements(self, axes: list[float]) -> dict[str, float]:
        """Totals of the axes bound to cursor and scroll roles."""
        assert self.profile is not None
        movements: dict[str, float] = defaultdict(float)
        for axis, value in enumerate(axes):
            if axis not in self.profile.axes_bindings:
                continue
            action = self.profile.axes_bindings[axis]
            if action != "Buttons" and value:
                movements[action] += -value if self.profile.invert_axis[axis] else value
        return movements

    def on_motion_tick(self) -> None:
        """Moves the cursor, scrolls and smooth-scrolls for whatever is held."""
        if not self.connected or self.profile is None:
            self.motion_timer.stop()
            return
        if not self.motion_needed():
            self.motion_timer.stop()
            return
        if self.quick_select.is_shown or get_state() in ("NoFocus", "config"):
            return
        movements = self.axis_movements(self.latest_axes)
        try:
            if movements["Cursor Horizontal"] or movements["Cursor Vertical"]:
                move_mouse(movements["Cursor Horizontal"], movements["Cursor Vertical"])
            if movements["Scroll Horizontal"] or movements["Scroll Vertical"]:
                scroll(movements["Scroll Horizontal"], movements["Scroll Vertical"])
            if self.scroll_up:
                smooth_scroll_step(-1)
            elif self.scroll_down:
                smooth_scroll_step(1)
        except Exception as err:  # pylint: disable=broad-except
            self.motion_timer.stop()
            tooltip("Error: " + str(err))

    def on_connect(self, buttons: str | int, axes: str | int, *con: str) -> None:
        """Called when a controller connects through the JavaScript interface"""
        dbg("on_connect", [buttons, axes, con])
        self.reset_controller()
        controller_id = "::".join(con)
        buttons, axes = int(buttons), int(axes)

        _controller = identify_controller(
            controller_id,
            buttons,
            axes,
            self.config["Detect 8BitDo Controllers"],
        )
        if _controller is None:
            dbg(
                f"identify_controller returned None for {controller_id}, {buttons}, {axes}"
            )
            self.reject_controller()
            return
        else:
            controller = _controller[0] or controller_id

        profile = find_profile(controller, buttons, axes)
        self.profile = get_profile(profile)

        if self.profile is None:
            dbg(f"Error: get_profile returned None for {profile}")
            tooltip(f"Error: Unable to load profile {profile}")
            self.reject_controller()
            return

        self.len_buttons, self.len_axes = buttons, axes
        self.buttons = [False] * self.len_buttons
        self.axes = [False] * self.len_axes
        self.controller_id = controller_id

        mw.form.menuTools.addAction(self.menu_item)
        self.update_debug_info()
        self.connected = True
        tooltip(f"{controller} Connected")

    def reject_controller(self) -> None:
        """
        Stops polling a controller that could not be set up. Otherwise every poll would
        report an error, which reconnects the same controller and fails again, endlessly.
        """
        self.connected = False
        self.eval("stop_polling()")

    def on_disconnect(self, err="", *_) -> None:
        """Called when a controller is disconnected through the JavaScript interface"""
        assert mw is not None
        dbg(f"Disconnected: {err}")
        was_connected = self.connected
        if self.controllers is not None:
            for controller in self.controllers:
                mw.form.menuTools.removeAction(controller)
        self.controllers = list()
        self.reset_controller()
        self.update_debug_info()
        if was_connected:
            tooltip("Controller Disconnected")
        self.connected = False

    def reset_controller(self) -> None:
        """Clears the current controller"""
        if self.overlay:
            self.overlay.disappear()
        self.quick_select.disappear()
        mw.form.menuTools.removeAction(self.menu_item)
        self.buttons = []
        self.axes = []
        self.axis_buttons.reset()
        self.hats.reset()
        # A held smooth scroll would otherwise carry on once a controller reconnects.
        self.scroll_up = False
        self.scroll_down = False
        self.latest_axes = []
        self.press_states = {}
        self.motion_timer.stop()
        self.profile = None
        self.update_debug_info()

    def register_controllers(self, *controllers) -> None:
        """
        When multiple controllers are detected, this function adds them to the menu.
        """
        assert mw is not None
        dbg("register_controllers", controllers)
        for controller_action in self.controllers:
            mw.form.menuTools.removeAction(controller_action)
        self.controllers.clear()
        to_connect = None
        for i, controller in enumerate(controllers):
            id_, buttons, axes, *_ = controller.split("%%%")
            con = identify_controller(
                id_, buttons, axes, self.config["Detect 8BitDo Controllers"]
            )
            if con is None:
                continue
            if to_connect is None:
                to_connect = i
            self.controllers.append(QAction(con[0], mw))
            qconnect(self.controllers[-1].triggered, partial(self.change_controller, i))
        if len(self.controllers) > 1:
            for controller in self.controllers:
                mw.form.menuTools.addAction(controller)
            tooltip(
                f"{len(self.controllers)} controllers detected - select from the Tools menu."
            )
        if to_connect is not None:
            self.change_controller(to_connect, None)
        else:
            dbg("register_controllers called but no valid controllers found")

    def change_controller(self, index: int, _) -> None:
        """Calls JavaScript to change the controller"""
        dbg(f"Changing controller to {index}")
        self._evalWithCallback(
            f"connect_controller(indices[{index}]);", None  # type: ignore
        )

    def update_debug_info(self):
        """Updates the debug info. View by pressing help in the config dialog."""
        # evalWithCallback waits for the page to load; called straight after resume(),
        # the script was not defined yet.
        self.evalWithCallback(
            "typeof get_controller_info === 'function' ? get_controller_info() : null",
            self._update_debug_info,
        )

    def _update_debug_info(self, controllers: str) -> None:
        """Callback to receive the controller info from the JavaScript interface"""
        if controllers is None:
            self.debug_info: list[list[str]] = []
        else:
            self.debug_info = [
                con.split("%") for con in controllers.split("%%%") if con
            ]
        dbg(self.debug_info)

    def smooth_scroll(self, direction: bool, scroll: bool):
        """Smoothly scrolls the page"""
        if direction:
            self.scroll_up = scroll
        else:
            self.scroll_down = scroll
        self.update_motion()
