#!/usr/bin/env python3
"""Textual prototype UI for the terminal habit tracker."""

from __future__ import annotations

import argparse
import calendar
from datetime import date
from pathlib import Path

from terminal_habit_tracker import (
    DEFAULT_DB_PATH,
    STATUS_DONE,
    STATUS_MISSED,
    STATUS_PENDING,
    CalendarSelection,
    HabitStore,
    status_label,
)

try:
    from textual.app import App, ComposeResult
    from textual.containers import Horizontal, Vertical
    from textual.reactive import reactive
    from textual.widgets import Button, Footer, Header, Input, Label, Static
except ModuleNotFoundError as exc:
    TEXTUAL_IMPORT_ERROR: ModuleNotFoundError | None = exc
else:
    TEXTUAL_IMPORT_ERROR = None


if TEXTUAL_IMPORT_ERROR is None:

    class HabitTrackerTextualApp(App[None]):
        """A more ergonomic Textual UI over the existing SQLite habit store."""

        CSS = """
        Screen {
            background: $surface;
            color: $text;
        }

        #main {
            height: 1fr;
            padding: 1;
        }

        #calendar-panel {
            width: 40;
            min-width: 40;
            border: solid $primary;
            padding: 1 1;
        }

        #day-panel {
            width: 1fr;
            border: solid $secondary;
            padding: 1 2;
        }

        #month-row {
            height: 1;
            margin-bottom: 1;
        }

        #prev-month,
        #next-month {
            width: 5;
            min-width: 5;
            height: 1;
            min-height: 1;
            padding: 0;
        }

        #month-title {
            width: 1fr;
            min-width: 16;
            height: 1;
            content-align: center middle;
            text-style: bold;
        }

        .weekday-cell {
            width: 5;
            min-width: 5;
            height: 1;
            content-align: center middle;
            color: $text-muted;
        }

        .weekday-row {
            height: 1;
            margin-bottom: 1;
        }

        .week-row {
            height: 1;
            margin-bottom: 1;
        }

        .day-button {
            width: 5;
            min-width: 5;
            height: 1;
            min-height: 1;
            padding: 0;
        }

        .day-spacer {
            width: 5;
            min-width: 5;
            height: 1;
        }

        .selected-day {
            text-style: bold;
        }

        #selected-title {
            text-style: bold;
            margin-bottom: 1;
        }

        #message {
            height: 1;
            color: $warning;
            margin-top: 1;
        }

        #add-row {
            height: auto;
            margin-bottom: 1;
        }

        #habit-input {
            width: 1fr;
            margin-right: 1;
        }

        #add-habit {
            width: 8;
            min-width: 8;
        }

        .habit-card {
            height: auto;
            border-bottom: solid $panel;
            padding: 1 0;
        }

        .habit-name {
            text-style: bold;
            margin-bottom: 1;
        }

        .status-row {
            height: auto;
        }

        .status-button {
            width: 10;
            min-width: 8;
            margin-right: 1;
        }

        .note-button {
            width: 8;
            min-width: 7;
        }

        .active-status {
            text-style: bold;
        }
        """

        BINDINGS = [
            ("q", "quit", "Quit"),
            ("left", "previous_month", "Previous month"),
            ("right", "next_month", "Next month"),
            ("t", "today", "Today"),
            ("r", "refresh", "Refresh"),
        ]

        selected_day: reactive[int | None] = reactive(None)
        message: reactive[str] = reactive("")

        def __init__(
            self,
            selection: CalendarSelection,
            store: HabitStore,
        ) -> None:
            super().__init__()
            self.selection = selection
            self.store = store
            today = date.today()
            if selection.year == today.year and selection.month == today.month:
                self.selected_day = today.day

        def compose(self) -> ComposeResult:
            yield Header(show_clock=True)
            with Horizontal(id="main"):
                with Vertical(id="calendar-panel"):
                    yield from self._compose_calendar()
                with Vertical(id="day-panel"):
                    yield from self._compose_day_panel()
            yield Footer()

        def _compose_calendar(self) -> ComposeResult:
            month_summary = self.store.month_summary(self.selection.year, self.selection.month)

            with Horizontal(id="month-row"):
                yield Button("<", id="prev-month", compact=True)
                yield Label(self.month_title, id="month-title")
                yield Button(">", id="next-month", compact=True)

            with Horizontal(classes="weekday-row"):
                for weekday_name in ("Mo", "Tu", "We", "Th", "Fr", "Sa", "Su"):
                    yield Static(weekday_name, classes="weekday-cell")

            for week_index, week in enumerate(self.month_weeks):
                with Horizontal(classes="week-row"):
                    for weekday_index, day_number in enumerate(week):
                        if day_number == 0:
                            yield Static("", classes="day-spacer")
                            continue

                        label = self._day_label(day_number, month_summary)
                        classes = "day-button"
                        if day_number == self.selected_day:
                            classes += " selected-day"
                        yield Button(label, id=f"day-{day_number}", classes=classes, compact=True)

        def _compose_day_panel(self) -> ComposeResult:
            selected = self.selected_date
            if selected is None:
                yield Label("Select a day", id="selected-title")
                yield Static("Choose a date from the calendar to manage habits.")
                yield Static(self.message, id="message")
                return

            yield Label(selected.isoformat(), id="selected-title")
            with Horizontal(id="add-row"):
                yield Input(placeholder="New habit name", id="habit-input")
                yield Button("+ Add", id="add-habit", variant="primary")

            habits = self.store.habits_for_day(selected)
            if not habits:
                yield Static("No habits active yet.")
                yield Static("Add one from this date to start tracking.")
                yield Static(self.message, id="message")
                return

            note_habit_ids = self.store.note_habit_ids_for_day(selected)
            for habit in habits:
                streak = self.store.current_streak_for_habit(habit.habit_id, selected)
                with Vertical(classes="habit-card"):
                    yield Static(f"{habit.name} ({streak})", classes="habit-name")
                    with Horizontal(classes="status-row"):
                        for status in (STATUS_PENDING, STATUS_DONE, STATUS_MISSED):
                            classes = "status-button"
                            variant = "default"
                            if habit.status == status:
                                classes += " active-status"
                                variant = "success" if status == STATUS_DONE else "warning"
                            yield Button(
                                status_label(status),
                                id=f"status-{habit.habit_id}-{status}",
                                classes=classes,
                                compact=True,
                                variant=variant,
                            )

                        note_label = "Note" if habit.habit_id in note_habit_ids else "+Note"
                        yield Button(note_label, id=f"note-{habit.habit_id}", classes="note-button", compact=True)

            yield Static(self.message, id="message")

        @property
        def month_title(self) -> str:
            return f"{calendar.month_name[self.selection.month]} {self.selection.year}"

        @property
        def month_weeks(self) -> list[list[int]]:
            return calendar.monthcalendar(self.selection.year, self.selection.month)

        @property
        def selected_date(self) -> date | None:
            if self.selected_day is None:
                return None
            return date(self.selection.year, self.selection.month, self.selected_day)

        def _day_label(self, day_number: int, month_summary: dict[int, tuple[int, int]]) -> str:
            done_count, missed_count = month_summary.get(day_number, (0, 0))
            marker = "!"
            if missed_count == 0 and done_count > 0:
                marker = "+"
            if missed_count == 0 and done_count == 0:
                marker = " "
            return f"{day_number:2}{marker}"

        def on_button_pressed(self, event: Button.Pressed) -> None:
            button_id = event.button.id or ""

            if button_id == "prev-month":
                self.action_previous_month()
                return
            if button_id == "next-month":
                self.action_next_month()
                return
            if button_id == "add-habit":
                self.focus_habit_input()
                return
            if button_id.startswith("day-"):
                self.selected_day = int(button_id.removeprefix("day-"))
                self.message = ""
                self.refresh(recompose=True)
                return
            if button_id.startswith("status-"):
                self.set_status_from_button_id(button_id)
                return
            if button_id.startswith("note-"):
                self.message = "Notes are still handled in the classic curses UI."
                self.refresh(recompose=True)

        def on_input_submitted(self, event: Input.Submitted) -> None:
            if event.input.id == "habit-input":
                self.add_habit_from_input()

        def focus_habit_input(self) -> None:
            selected = self.selected_date
            if selected is None:
                self.message = "Select a date first."
                self.refresh(recompose=True)
                return

            habit_input = self.query_one("#habit-input", Input)
            habit_input.focus()
            self.message = "Type a habit name and press Enter."
            self.refresh()

        def action_previous_month(self) -> None:
            self.selection = self.selection.previous_month()
            self.selected_day = None
            self.message = ""
            self.refresh(recompose=True)

        def action_next_month(self) -> None:
            self.selection = self.selection.next_month()
            self.selected_day = None
            self.message = ""
            self.refresh(recompose=True)

        def action_today(self) -> None:
            today = date.today()
            self.selection = CalendarSelection(today.year, today.month)
            self.selected_day = today.day
            self.message = ""
            self.refresh(recompose=True)

        def action_refresh(self) -> None:
            self.refresh(recompose=True)

        def add_habit_from_input(self) -> None:
            selected = self.selected_date
            if selected is None:
                self.message = "Select a date first."
                self.refresh(recompose=True)
                return

            habit_input = self.query_one("#habit-input", Input)
            name = habit_input.value.strip()
            if not name:
                self.message = "Enter a habit name first."
                self.refresh(recompose=True)
                return

            self.store.create_habit(name, selected)
            self.message = f"Added {name}."
            habit_input.value = ""
            self.refresh(recompose=True)

        def set_status_from_button_id(self, button_id: str) -> None:
            selected = self.selected_date
            if selected is None:
                self.message = "Select a date first."
                self.refresh(recompose=True)
                return

            _, habit_id_text, status = button_id.split("-", 2)
            self.store.set_status(int(habit_id_text), selected, status)
            self.message = f"Marked habit as {status_label(status)}."
            self.refresh(recompose=True)

        def on_unmount(self) -> None:
            self.store.close()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the Textual habit tracker prototype.")
    parser.add_argument(
        "--db",
        type=Path,
        default=DEFAULT_DB_PATH,
        help="Path to the SQLite database.",
    )
    parser.add_argument("--month", type=int, choices=range(1, 13), help="Month to open.")
    parser.add_argument("--year", type=int, help="Year to open.")
    return parser.parse_args()


def main() -> None:
    if TEXTUAL_IMPORT_ERROR is not None:
        raise SystemExit(
            "Textual is not installed. Install it with:\n\n"
            "    python3 -m pip install textual\n\n"
            "Then run:\n\n"
            "    python3 textual_habit_tracker.py"
        )

    args = parse_args()
    app = HabitTrackerTextualApp(
        selection=CalendarSelection.from_args(args.year, args.month),
        store=HabitStore(args.db),
    )
    app.run()


if __name__ == "__main__":
    main()
