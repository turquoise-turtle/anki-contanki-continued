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
