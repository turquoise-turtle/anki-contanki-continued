"""
Turns axes into press and release events for the axis "buttons" (indices 100 and up).

Controllers such as the 8BitDo Micro and Zero 2 in D-input mode report their D-pad as
axes rather than buttons, so each direction of an axis is treated as a button of its own.
"""

# Kept free of Anki imports so it can be unit tested outside Anki.

from __future__ import annotations

from typing import Iterable, Sequence

AXIS_BUTTON_OFFSET = 100
PRESS_THRESHOLD = 0.5


def axis_button(axis: int, direction: int) -> int:
    """Index of the axis button for an axis pushed in a direction (-1 or 1)."""
    return AXIS_BUTTON_OFFSET + axis * 2 + (direction > 0)


def axis_direction(value: float) -> int:
    """Returns -1, 0 or 1 depending on which way, if any, the axis is pushed."""
    if value > PRESS_THRESHOLD:
        return 1
    if value < -PRESS_THRESHOLD:
        return -1
    return 0


def axis_button_pressed(index: int, axes: Sequence[float]) -> bool:
    """Whether the axis button with this index is currently held."""
    axis, positive = divmod(index - AXIS_BUTTON_OFFSET, 2)
    if not 0 <= axis < len(axes):
        return False
    return axis_direction(axes[axis]) == (1 if positive else -1)


class AxisButtons:
    """Remembers which way each axis was pushed, to report presses and releases."""

    def __init__(self) -> None:
        self.directions: dict[int, int] = {}

    def update(
        self, axes: Sequence[float], tracked: Iterable[int]
    ) -> list[tuple[int, bool]]:
        """
        Returns (axis button, pressed) events for the tracked axes since the last update.

        Moving straight from one direction to the other releases the first button before
        pressing the second, so a held action is never left running.
        """
        events: list[tuple[int, bool]] = []
        for axis in tracked:
            direction = axis_direction(axes[axis]) if axis < len(axes) else 0
            previous = self.directions.get(axis, 0)
            if direction == previous:
                continue
            if previous:
                events.append((axis_button(axis, previous), False))
            if direction:
                events.append((axis_button(axis, direction), True))
            self.directions[axis] = direction
        return events

    def reset(self) -> None:
        """Forgets all held directions, without reporting releases."""
        self.directions.clear()


# Chromium reports a HID hat switch (a D-pad on some controllers, such as the 8BitDo Micro
# on macOS 26) as a single axis: 2v/7 - 1 for the directions v = 0 (up) to 7 (up-left),
# clockwise, and 9/7 when released. No ordinary axis goes beyond 1, which is how a hat is
# recognised.
HAT_RELEASED_MIN = 1.05
HAT_DIRECTIONS = [(0, -1), (1, -1), (1, 0), (1, 1), (0, 1), (-1, 1), (-1, 0), (-1, -1)]


def decode_hat(value: float) -> tuple[int, int]:
    """Returns the (x, y) direction of a hat axis value, y pointing down; (0, 0) if released."""
    step = round((value + 1) * 3.5)
    if value >= HAT_RELEASED_MIN or not 0 <= step <= 7:
        return (0, 0)
    return HAT_DIRECTIONS[step]


class HatSwitches:
    """Recognises hat-switch axes and presents them as the controller's usual D-pad."""

    def __init__(self) -> None:
        self.hat_axes: set[int] = set()

    def apply(
        self,
        buttons: list[bool],
        axes: list[float],
        dpad: tuple[int, int, int, int] | None,
    ) -> None:
        """
        Rewrites the inputs in place: each hat axis is set to 0, so it never reads as held,
        and its direction is copied to the D-pad buttons or axes given as (up, down, left,
        right) indices, as returned by Controller.get_dpad_buttons.
        """
        for axis, value in enumerate(axes):
            if value >= HAT_RELEASED_MIN:
                self.hat_axes.add(axis)
        for axis in self.hat_axes:
            if axis >= len(axes):
                continue
            x, y = decode_hat(axes[axis])
            axes[axis] = 0.0
            if dpad is None:
                continue
            up, down, left, right = dpad
            for index, held in ((up, y < 0), (down, y > 0), (left, x < 0), (right, x > 0)):
                if not held:
                    continue
                if index < AXIS_BUTTON_OFFSET:
                    if index < len(buttons):
                        buttons[index] = True
                    continue
                dpad_axis, positive = divmod(index - AXIS_BUTTON_OFFSET, 2)
                if dpad_axis < len(axes) and dpad_axis not in self.hat_axes:
                    axes[dpad_axis] = 1.0 if positive else -1.0

    def reset(self) -> None:
        """Forgets which axes are hats, for when another controller connects."""
        self.hat_axes.clear()
