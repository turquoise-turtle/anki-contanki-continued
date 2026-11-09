"""Unit tests that run outside Anki: uv run --no-project --with pytest pytest unit_tests"""

import importlib.util
from pathlib import Path

# Importing through the package would run contanki/__init__.py, which needs a running Anki.
_path = Path(__file__).parent.parent / "contanki" / "axis_buttons.py"
_spec = importlib.util.spec_from_file_location("axis_buttons", _path)
assert _spec is not None and _spec.loader is not None
axis_buttons = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(axis_buttons)

AxisButtons = axis_buttons.AxisButtons
axis_button_pressed = axis_buttons.axis_button_pressed

# 8BitDo Micro in D-input: axis 0 is the D-pad's horizontal, axis 1 its vertical.
LEFT, RIGHT, UP, DOWN = 100, 101, 102, 103
REST = [0.0] * 10


def micro(x=0.0, y=0.0):
    return [x, y] + [0.0] * 8


def test_press_then_release():
    tracker = AxisButtons()
    assert tracker.update(micro(y=-1.0), [0, 1]) == [(UP, True)]
    assert tracker.update(micro(y=-1.0), [0, 1]) == []
    assert tracker.update(REST, [0, 1]) == [(UP, False)]


def test_second_press_after_all_axes_return_to_zero():
    # Issue 163: the second press in the same direction used to be ignored.
    tracker = AxisButtons()
    tracker.update(micro(x=1.0), [0, 1])
    tracker.update(REST, [0, 1])
    assert tracker.update(micro(x=1.0), [0, 1]) == [(RIGHT, True)]


def test_direction_change_releases_first():
    tracker = AxisButtons()
    tracker.update(micro(y=-1.0), [0, 1])
    assert tracker.update(micro(y=1.0), [0, 1]) == [(UP, False), (DOWN, True)]


def test_diagonal_presses_both():
    tracker = AxisButtons()
    assert tracker.update(micro(x=-1.0, y=1.0), [0, 1]) == [(LEFT, True), (DOWN, True)]


def test_small_values_are_not_presses():
    tracker = AxisButtons()
    assert tracker.update(micro(x=0.5, y=-0.004), [0, 1]) == []


def test_untracked_axes_are_ignored():
    tracker = AxisButtons()
    assert tracker.update(micro(x=1.0, y=1.0), [1]) == [(DOWN, True)]


def test_missing_axis_counts_as_released():
    tracker = AxisButtons()
    tracker.update(micro(y=1.0), [1])
    assert tracker.update([0.0], [1]) == [(DOWN, False)]


def test_reset_forgets_without_releasing():
    tracker = AxisButtons()
    tracker.update(micro(y=1.0), [0, 1])
    tracker.reset()
    assert tracker.update(REST, [0, 1]) == []


def test_axis_button_pressed():
    axes = micro(x=-1.0, y=0.2)
    assert axis_button_pressed(LEFT, axes)
    assert not axis_button_pressed(RIGHT, axes)
    assert not axis_button_pressed(UP, axes)
    assert not axis_button_pressed(DOWN, axes)
    assert not axis_button_pressed(140, axes)


decode_hat = axis_buttons.decode_hat
HatSwitches = axis_buttons.HatSwitches
MICRO_DPAD = (UP, DOWN, LEFT, RIGHT)


def hat(direction):
    """Chromium's value for a hat pushed in direction 0 (up) to 7 (up-left), 8 for released."""
    return 2 * direction / 7 - 1


def test_decode_hat_directions():
    assert [decode_hat(hat(v)) for v in range(8)] == [
        (0, -1), (1, -1), (1, 0), (1, 1), (0, 1), (-1, 1), (-1, 0), (-1, -1)
    ]
    assert decode_hat(hat(8)) == (0, 0)


def micro_with_hat(value):
    """Micro on macOS 26 as reported in issue 154: D-pad on a hat in the last axis."""
    return [0.0] * 9 + [value]


def test_hat_becomes_the_micro_dpad():
    hats = HatSwitches()
    tracker = AxisButtons()
    buttons = [False] * 16

    def step(value):
        axes = micro_with_hat(value)
        hats.apply(buttons, axes, MICRO_DPAD)
        return tracker.update(axes, [0, 1])

    # Released hat (9/7) used to read as held; now it is nothing.
    assert step(hat(8)) == []
    assert step(hat(2)) == [(RIGHT, True)]
    assert step(hat(8)) == [(RIGHT, False)]
    assert step(hat(4)) == [(DOWN, True)]
    assert step(hat(5)) == [(LEFT, True)]
    assert step(hat(8)) == [(LEFT, False), (DOWN, False)]


def test_hat_values_measured_on_a_micro():
    # From controller-reports: macOS 27.2, Anki 26.09, D-pad in hat mode, axis 9.
    released, up, right, down, left = 3.2857, -1.0, -0.4286, 0.1429, 0.7143
    hats = HatSwitches()
    tracker = AxisButtons()
    buttons = [False] * 20

    def step(value):
        axes = micro_with_hat(value)
        hats.apply(buttons, axes, MICRO_DPAD)
        return tracker.update(axes, [0, 1])

    assert step(released) == []
    assert step(up) == [(UP, True)]
    assert step(released) == [(UP, False)]
    assert step(right) == [(RIGHT, True)]
    assert step(down) == [(RIGHT, False), (DOWN, True)]
    assert step(left) == [(LEFT, True), (DOWN, False)]
    assert step(released) == [(LEFT, False)]


def test_hat_axis_is_neutralised_without_a_dpad():
    hats = HatSwitches()
    axes = micro_with_hat(hat(8))
    hats.apply([], axes, None)
    assert axes == [0.0] * 10


def test_hat_presses_dpad_buttons():
    hats = HatSwitches()
    buttons = [False] * 16
    hats.apply(buttons, [0.0, 0.0, hat(8)], (12, 13, 14, 15))
    axes = [0.0, 0.0, hat(0)]
    hats.apply(buttons, axes, (12, 13, 14, 15))
    assert buttons[12] and not any(buttons[13:16])
    assert axes == [0.0, 0.0, 0.0]


def test_ordinary_axes_are_left_alone():
    hats = HatSwitches()
    axes = [1.0, -1.0, 0.5]
    hats.apply([], axes, MICRO_DPAD)
    assert axes == [1.0, -1.0, 0.5]
    assert not hats.hat_axes
