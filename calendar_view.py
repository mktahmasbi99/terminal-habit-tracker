#!/usr/bin/env python3
"""Interactive monthly calendar for a habit tracker terminal app."""

from __future__ import annotations

import argparse
import calendar
import curses
from dataclasses import dataclass, field
from datetime import date


WEEKDAY_HEADER = "Mo Tu We Th Fr Sa Su"
CELL_WIDTH = 4
CALENDAR_LEFT = 4
CALENDAR_TOP = 5


@dataclass(frozen=True)
class CalendarSelection:
    year: int
    month: int

    @classmethod
    def from_args(cls, year: int | None, month: int | None) -> "CalendarSelection":
        current = date.today()
        selected_year = year if year is not None else current.year
        selected_month = month if month is not None else current.month
        return cls(selected_year, selected_month)

    def previous_month(self) -> "CalendarSelection":
        if self.month == 1:
            return CalendarSelection(self.year - 1, 12)
        return CalendarSelection(self.year, self.month - 1)

    def next_month(self) -> "CalendarSelection":
        if self.month == 12:
            return CalendarSelection(self.year + 1, 1)
        return CalendarSelection(self.year, self.month + 1)


@dataclass(frozen=True)
class HitBox:
    name: str
    y: int
    x1: int
    x2: int
    value: int | None = None

    def contains(self, y: int, x: int) -> bool:
        return self.y == y and self.x1 <= x <= self.x2


@dataclass
class CalendarApp:
    selection: CalendarSelection
    selected_day: int | None = None
    use_color: bool = True
    hitboxes: list[HitBox] = field(default_factory=list)

    def __post_init__(self) -> None:
        current = date.today()
        if self.selected_day is None and (
            self.selection.year == current.year and self.selection.month == current.month
        ):
            self.selected_day = current.day

    def move_month(self, delta: int) -> None:
        if delta < 0:
            self.selection = self.selection.previous_month()
        else:
            self.selection = self.selection.next_month()
        self.selected_day = None

    def select_day(self, day: int) -> None:
        self.selected_day = day

    def handle_click(self, y: int, x: int) -> None:
        for hitbox in self.hitboxes:
            if not hitbox.contains(y, x):
                continue
            if hitbox.name == "previous":
                self.move_month(-1)
            elif hitbox.name == "next":
                self.move_month(1)
            elif hitbox.name == "day" and hitbox.value is not None:
                self.select_day(hitbox.value)
            return

    def render(self, screen: "curses.window") -> None:
        screen.erase()
        self.hitboxes.clear()
        height, width = screen.getmaxyx()

        if height < 14 or width < 36:
            screen.addstr(0, 0, "Make the terminal at least 36x14.")
            screen.refresh()
            return

        self._draw_header(screen)
        self._draw_calendar(screen)
        self._draw_footer(screen)
        screen.refresh()

    def _draw_header(self, screen: "curses.window") -> None:
        title = f"{calendar.month_name[self.selection.month]} {self.selection.year}"
        previous_label = "< Prev"
        next_label = "Next >"
        title_x = max(0, (screen.getmaxyx()[1] - len(title)) // 2)
        next_x = title_x + len(title) + 4

        self._addstr(screen, 1, CALENDAR_LEFT, previous_label, curses.A_BOLD)
        self._addstr(screen, 1, title_x, title, self._color(1) | curses.A_BOLD)
        self._addstr(screen, 1, next_x, next_label, curses.A_BOLD)

        self.hitboxes.append(HitBox("previous", 1, CALENDAR_LEFT, CALENDAR_LEFT + len(previous_label) - 1))
        self.hitboxes.append(HitBox("next", 1, next_x, next_x + len(next_label) - 1))

    def _draw_calendar(self, screen: "curses.window") -> None:
        today = date.today()
        self._addstr(screen, CALENDAR_TOP - 2, CALENDAR_LEFT, WEEKDAY_HEADER, curses.A_BOLD)

        for row_index, week in enumerate(calendar.monthcalendar(self.selection.year, self.selection.month)):
            y = CALENDAR_TOP + row_index
            for col_index, day in enumerate(week):
                x = CALENDAR_LEFT + col_index * CELL_WIDTH
                if day == 0:
                    self._addstr(screen, y, x, "  ")
                    continue

                style = curses.A_NORMAL
                if day == self.selected_day:
                    style |= self._color(2) | curses.A_BOLD
                if (
                    day == today.day
                    and self.selection.month == today.month
                    and self.selection.year == today.year
                ):
                    style |= self._color(3) | curses.A_BOLD

                label = f"{day:2}"
                self._addstr(screen, y, x, label, style)
                self.hitboxes.append(HitBox("day", y, x, x + CELL_WIDTH - 2, day))

    def _draw_footer(self, screen: "curses.window") -> None:
        selected = "No day selected"
        if self.selected_day is not None:
            selected = f"Selected: {self.selection.year:04d}-{self.selection.month:02d}-{self.selected_day:02d}"

        footer_y = CALENDAR_TOP + 8
        self._addstr(screen, footer_y, CALENDAR_LEFT, selected)
        self._addstr(screen, footer_y + 2, CALENDAR_LEFT, "Mouse: click days or month controls. Keys: q quit, arrows/PgUp/PgDn navigate.")

    def _color(self, pair_number: int) -> int:
        if not self.use_color or not curses.has_colors():
            return curses.A_REVERSE
        return curses.color_pair(pair_number)

    @staticmethod
    def _addstr(screen: "curses.window", y: int, x: int, text: str, style: int = curses.A_NORMAL) -> None:
        height, width = screen.getmaxyx()
        if y < 0 or y >= height or x >= width:
            return
        screen.addstr(y, max(0, x), text[: max(0, width - x - 1)], style)


def build_month_view(selection: CalendarSelection) -> str:
    today = date.today()
    weeks = calendar.monthcalendar(selection.year, selection.month)
    lines = [f"{calendar.month_name[selection.month]} {selection.year}".center(20), WEEKDAY_HEADER]

    for week in weeks:
        day_cells: list[str] = []
        for day in week:
            if day == 0:
                day_cells.append("  ")
                continue
            marker = "*" if (
                day == today.day and selection.month == today.month and selection.year == today.year
            ) else " "
            day_cells.append(f"{day:2}{marker}".rstrip())
        lines.append(" ".join(day_cells))

    return "\n".join(lines)


def run_curses(screen: "curses.window", app: CalendarApp) -> None:
    curses.curs_set(0)
    curses.mousemask(curses.ALL_MOUSE_EVENTS | curses.REPORT_MOUSE_POSITION)
    curses.mouseinterval(0)

    if curses.has_colors() and app.use_color:
        curses.start_color()
        curses.use_default_colors()
        curses.init_pair(1, curses.COLOR_BLACK, curses.COLOR_WHITE)
        curses.init_pair(2, curses.COLOR_BLACK, curses.COLOR_CYAN)
        curses.init_pair(3, curses.COLOR_WHITE, curses.COLOR_BLUE)

    while True:
        app.render(screen)
        key = screen.getch()

        if key in (ord("q"), ord("Q"), 27):
            break
        if key in (curses.KEY_LEFT, curses.KEY_PPAGE):
            app.move_month(-1)
        elif key in (curses.KEY_RIGHT, curses.KEY_NPAGE):
            app.move_month(1)
        elif key == ord("t"):
            today = date.today()
            app.selection = CalendarSelection(today.year, today.month)
            app.selected_day = today.day
        elif key == curses.KEY_MOUSE:
            try:
                _, x, y, _, button_state = curses.getmouse()
            except curses.error:
                continue
            if button_state & (curses.BUTTON1_CLICKED | curses.BUTTON1_PRESSED):
                app.handle_click(y, x)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Open an interactive monthly calendar view for a habit tracker."
    )
    parser.add_argument(
        "-m",
        "--month",
        type=int,
        choices=range(1, 13),
        metavar="1-12",
        help="month to display; defaults to the current month",
    )
    parser.add_argument(
        "-y",
        "--year",
        type=int,
        help="year to display; defaults to the current year",
    )
    parser.add_argument(
        "--no-color",
        action="store_true",
        help="disable terminal colors and highlighting",
    )
    parser.add_argument(
        "--plain",
        action="store_true",
        help="print a non-interactive month view and exit",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    selection = CalendarSelection.from_args(args.year, args.month)

    if args.plain:
        print(build_month_view(selection))
        return

    app = CalendarApp(selection=selection, use_color=not args.no_color)
    curses.wrapper(run_curses, app)


if __name__ == "__main__":
    main()
