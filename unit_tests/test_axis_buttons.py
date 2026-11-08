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
