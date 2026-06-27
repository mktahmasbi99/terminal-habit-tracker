#!/usr/bin/env python3
"""Interactive daily habit tracker with a navigable monthly calendar."""

from __future__ import annotations

import argparse
import calendar
import curses
import sqlite3
import shutil
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Sequence


WEEKDAY_HEADER = "Mo Tu We Th Fr Sa Su"
CELL_WIDTH = 4
CALENDAR_LEFT = 4
CALENDAR_TOP = 5
DETAIL_LEFT = 38
STATUS_DONE = "done"
STATUS_MISSED = "missed"
STATUS_PENDING = "pending"
DEFAULT_DB_PATH = Path("habit_tracker.sqlite3")
ON_DEMAND_BACKUP_PREFIX = "o"
AUTOMATIC_BACKUP_PREFIX = "a"
MAX_AUTOMATIC_BACKUPS = 5
COMMANDS: tuple[tuple[str, str], ...] = (
    ("/help", "Show this command list."),
    ("/backup", "Open backup tools."),
    ("/delhabit", "Open habit deletion. Deletion requires typing DELETE."),
    ("/renamehabit", "Rename an existing habit."),
    ("/completehabit", "Mark a habit completed as of today."),
    ("/quit", "Quit the app."),
)


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
class Habit:
    habit_id: int
    name: str
    start_date: date
    completed_at: date | None = None


@dataclass(frozen=True)
class HabitStatus:
    habit_id: int
    name: str
    start_date: date
    status: str
    completed_at: date | None = None


@dataclass(frozen=True)
class HitBox:
    name: str
    y: int
    x1: int
    x2: int
    value: Any = None

    def contains(self, y: int, x: int) -> bool:
        return self.y == y and self.x1 <= x <= self.x2


def date_range(start: date, stop: date) -> list[date]:
    days: list[date] = []
    current = start
    while current < stop:
        days.append(current)
        current += timedelta(days=1)
    return days


class HabitStore:
    """SQLite persistence for daily habits and explicit daily status edits."""

    def __init__(self, db_path: Path | str = DEFAULT_DB_PATH) -> None:
        self.db_path = Path(db_path)
        self.connection = sqlite3.connect(self.db_path)
        self.connection.row_factory = sqlite3.Row
        self.initialize()

    def close(self) -> None:
        self.connection.close()

    def backup(self, destination: Path | str | None = None) -> Path:
        backup_path = backup_destination(self.db_path, destination)
        backup_path.parent.mkdir(parents=True, exist_ok=True)

        with sqlite3.connect(backup_path) as backup_connection:
            self.connection.backup(backup_connection)
        return backup_path

    def initialize(self) -> None:
        self.connection.executescript(
            """
            PRAGMA foreign_keys = ON;

            CREATE TABLE IF NOT EXISTS habits (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                start_date TEXT NOT NULL,
                completed_at TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS habit_logs (
                habit_id INTEGER NOT NULL,
                log_date TEXT NOT NULL,
                status TEXT NOT NULL CHECK (status IN ('done', 'missed')),
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (habit_id, log_date),
                FOREIGN KEY (habit_id) REFERENCES habits(id) ON DELETE CASCADE
            );
            """
        )
        habit_columns = {row["name"] for row in self.connection.execute("PRAGMA table_info(habits)")}
        if "completed_at" not in habit_columns:
            self.connection.execute("ALTER TABLE habits ADD COLUMN completed_at TEXT")
        self.connection.commit()

    def create_habit(self, name: str, start_date: date) -> int:
        cleaned = " ".join(name.split())
        if not cleaned:
            raise ValueError("Habit name cannot be empty.")

        today = date.today()
        with self.connection:
            cursor = self.connection.execute(
                "INSERT INTO habits (name, start_date) VALUES (?, ?)",
                (cleaned, start_date.isoformat()),
            )
            habit_id = int(cursor.lastrowid)

            if start_date < today:
                past_days = [
                    (habit_id, current_day.isoformat(), STATUS_DONE)
                    for current_day in date_range(start_date, today)
                ]
                self.connection.executemany(
                    """
                    INSERT INTO habit_logs (habit_id, log_date, status)
                    VALUES (?, ?, ?)
                    """,
                    past_days,
                )

        return habit_id

    def set_status(self, habit_id: int, day: date, status: str) -> None:
        if status not in {STATUS_DONE, STATUS_MISSED, STATUS_PENDING}:
            raise ValueError(f"Unknown habit status: {status}")
        if not self.habit_active_on(habit_id, day):
            raise ValueError("Habit is not active on the selected date.")

        if status == STATUS_PENDING:
            self.connection.execute(
                "DELETE FROM habit_logs WHERE habit_id = ? AND log_date = ?",
                (habit_id, day.isoformat()),
            )
            self.connection.commit()
            return

        self.connection.execute(
            """
            INSERT INTO habit_logs (habit_id, log_date, status, updated_at)
            VALUES (?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(habit_id, log_date)
            DO UPDATE SET status = excluded.status, updated_at = CURRENT_TIMESTAMP
            """,
            (habit_id, day.isoformat(), status),
        )
        self.connection.commit()

    def list_habits(self) -> list[Habit]:
        rows = self.connection.execute(
            """
            SELECT id, name, start_date, completed_at
            FROM habits
            ORDER BY start_date, name
            """
        ).fetchall()
        return [
            Habit(
                habit_id=int(row["id"]),
                name=str(row["name"]),
                start_date=date.fromisoformat(str(row["start_date"])),
                completed_at=(
                    date.fromisoformat(str(row["completed_at"]))
                    if row["completed_at"] is not None
                    else None
                ),
            )
            for row in rows
        ]

    def list_active_habits(self) -> list[Habit]:
        return [habit for habit in self.list_habits() if habit.completed_at is None]

    def habit_active_on(self, habit_id: int, day: date) -> bool:
        row = self.connection.execute(
            """
            SELECT 1
            FROM habits
            WHERE id = ?
                AND start_date <= ?
                AND (completed_at IS NULL OR completed_at >= ?)
            """,
            (habit_id, day.isoformat(), day.isoformat()),
        ).fetchone()
        return row is not None

    def complete_habit(self, habit_id: int, completion_date: date) -> None:
        row = self.connection.execute(
            "SELECT start_date, completed_at FROM habits WHERE id = ?",
            (habit_id,),
        ).fetchone()
        if row is None:
            raise ValueError("Habit does not exist.")
        if row["completed_at"] is not None:
            raise ValueError("Habit is already completed.")

        start_date = date.fromisoformat(str(row["start_date"]))
        if completion_date < start_date:
            raise ValueError("Completion date cannot be before the habit start date.")

        self.connection.execute(
            "UPDATE habits SET completed_at = ? WHERE id = ?",
            (completion_date.isoformat(), habit_id),
        )
        self.connection.commit()

    def delete_habit(self, habit_id: int) -> None:
        self.connection.execute("DELETE FROM habits WHERE id = ?", (habit_id,))
        self.connection.commit()

    def rename_habit(self, habit_id: int, name: str) -> None:
        cleaned = " ".join(name.split())
        if not cleaned:
            raise ValueError("Habit name cannot be empty.")

        self.connection.execute(
            "UPDATE habits SET name = ? WHERE id = ?",
            (cleaned, habit_id),
        )
        self.connection.commit()

    def habits_for_day(self, day: date) -> list[HabitStatus]:
        rows = self.connection.execute(
            """
            SELECT
                habits.id,
                habits.name,
                habits.start_date,
                habits.completed_at,
                COALESCE(habit_logs.status, ?) AS status
            FROM habits
            LEFT JOIN habit_logs
                ON habit_logs.habit_id = habits.id
                AND habit_logs.log_date = ?
            WHERE habits.start_date <= ?
                AND (habits.completed_at IS NULL OR habits.completed_at >= ?)
            ORDER BY habits.start_date, habits.name
            """,
            (STATUS_PENDING, day.isoformat(), day.isoformat(), day.isoformat()),
        ).fetchall()

        return [
            HabitStatus(
                habit_id=int(row["id"]),
                name=str(row["name"]),
                start_date=date.fromisoformat(str(row["start_date"])),
                status=str(row["status"]),
                completed_at=(
                    date.fromisoformat(str(row["completed_at"]))
                    if row["completed_at"] is not None
                    else None
                ),
            )
            for row in rows
        ]

    def month_summary(self, year: int, month: int) -> dict[int, tuple[int, int]]:
        summary: dict[int, tuple[int, int]] = {}
        _, last_day = calendar.monthrange(year, month)
        for day_number in range(1, last_day + 1):
            current_day = date(year, month, day_number)
            statuses = self.habits_for_day(current_day)
            done_count = sum(1 for habit in statuses if habit.status == STATUS_DONE)
            missed_count = sum(1 for habit in statuses if habit.status == STATUS_MISSED)
            summary[day_number] = (done_count, missed_count)
        return summary


def default_backup_directory(db_path: Path | str) -> Path:
    resolved = Path(db_path)
    return resolved.parent / "backups"


def backup_destination(db_path: Path | str, destination: Path | str | None = None) -> Path:
    if destination is not None:
        return Path(destination)

    source = Path(db_path)
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_dir = default_backup_directory(source)
    candidate = backup_dir / f"{ON_DEMAND_BACKUP_PREFIX}-{source.stem}-{timestamp}{source.suffix}"
    if not candidate.exists():
        return candidate

    for counter in range(2, 1000):
        candidate = backup_dir / f"{ON_DEMAND_BACKUP_PREFIX}-{source.stem}-{timestamp}-{counter}{source.suffix}"
        if not candidate.exists():
            return candidate
    raise FileExistsError("Could not find an available backup filename.")


def automatic_backup_destination(db_path: Path | str, day: date | None = None) -> Path:
    source = Path(db_path)
    backup_day = day if day is not None else date.today()
    backup_dir = default_backup_directory(source)
    return backup_dir / f"{AUTOMATIC_BACKUP_PREFIX}-{source.stem}-{backup_day:%Y%m%d}{source.suffix}"


def automatic_backup_paths(db_path: Path | str) -> list[Path]:
    source = Path(db_path)
    backup_dir = default_backup_directory(source)
    prefix = f"{AUTOMATIC_BACKUP_PREFIX}-{source.stem}-"

    if not backup_dir.exists():
        return []

    return sorted(
        path
        for path in backup_dir.iterdir()
        if path.is_file() and path.name.startswith(prefix) and path.suffix == source.suffix
    )


def prune_automatic_backups(db_path: Path | str, keep: int = MAX_AUTOMATIC_BACKUPS) -> None:
    backups = automatic_backup_paths(db_path)
    for backup_path in backups[: max(0, len(backups) - keep)]:
        backup_path.unlink()


def create_automatic_backup(store: "HabitStore", keep: int = MAX_AUTOMATIC_BACKUPS) -> Path | None:
    backup_path = automatic_backup_destination(store.db_path)
    if backup_path.exists():
        prune_automatic_backups(store.db_path, keep)
        return None

    created_path = store.backup(backup_path)
    prune_automatic_backups(store.db_path, keep)
    return created_path


def list_backup_files(db_path: Path | str) -> list[Path]:
    backup_dir = default_backup_directory(db_path)
    if not backup_dir.exists():
        return []

    return sorted(
        (path for path in backup_dir.iterdir() if path.is_file()),
        key=lambda path: (path.stat().st_mtime, path.name),
        reverse=True,
    )


def is_backup_file_for_database(db_path: Path | str, backup_path: Path | str) -> bool:
    backup_dir = default_backup_directory(db_path).resolve()
    candidate = Path(backup_path).resolve()
    return candidate.parent == backup_dir and candidate.is_file()


def restore_database(db_path: Path | str, backup_path: Path | str, force: bool = False) -> None:
    destination = Path(db_path)
    source = Path(backup_path)

    if not source.exists():
        raise FileNotFoundError(f"Backup file does not exist: {source}")
    if not source.is_file():
        raise ValueError(f"Backup path is not a file: {source}")
    if destination.exists() and not force:
        raise FileExistsError(
            f"Database already exists at {destination}. Re-run with --force to restore over it."
        )

    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)


def status_label(status: str) -> str:
    if status == STATUS_DONE:
        return "Done"
    if status == STATUS_MISSED:
        return "Missed"
    if status == STATUS_PENDING:
        return "Pending"
    return status.title()


@dataclass
class CalendarApp:
    selection: CalendarSelection
    store: HabitStore
    selected_day: int | None = None
    use_color: bool = True
    view: str = "main"
    message: str = ""
    hitboxes: list[HitBox] = field(default_factory=list)

    def __post_init__(self) -> None:
        current = date.today()
        if self.selected_day is None and (
            self.selection.year == current.year and self.selection.month == current.month
        ):
            self.selected_day = current.day

    @property
    def selected_date(self) -> date | None:
        if self.selected_day is None:
            return None
        return date(self.selection.year, self.selection.month, self.selected_day)

    def move_month(self, delta: int) -> None:
        if delta < 0:
            self.selection = self.selection.previous_month()
        else:
            self.selection = self.selection.next_month()
        self.selected_day = None
        self.message = ""

    def select_day(self, day: int) -> None:
        self.selected_day = day
        self.message = ""

    def open_manage_habits(self) -> None:
        self.view = "manage_habits"
        self.message = ""

    def open_rename_habits(self) -> None:
        self.view = "rename_habits"
        self.message = ""

    def open_complete_habits(self) -> None:
        self.view = "complete_habits"
        self.message = ""

    def open_help(self) -> None:
        self.view = "help"
        self.message = ""

    def open_backups(self) -> None:
        self.view = "backups"
        self.message = ""

    def open_manage_backups(self) -> None:
        self.view = "manage_backups"
        self.message = ""

    def create_backup(self) -> None:
        backup_path = self.store.backup()
        self.message = f"Backup saved to {backup_path}"

    def delete_backup(self, screen: "curses.window", backup_path: Path) -> None:
        if not is_backup_file_for_database(self.store.db_path, backup_path):
            self.message = "Backup file is not in this database backup directory."
            return

        confirmation = self._prompt(
            screen,
            f"Irreversible. Type DELETE to delete '{self._truncate(backup_path.name, 24)}': ",
        )
        if confirmation != "DELETE":
            self.message = "Backup deletion cancelled."
            return

        try:
            backup_path.unlink()
        except OSError as exc:
            self.message = f"Backup deletion failed: {exc}"
            return
        self.message = f"Deleted backup '{backup_path.name}'."

    def restore_backup(self, screen: "curses.window", backup_path: Path) -> None:
        if not is_backup_file_for_database(self.store.db_path, backup_path):
            self.message = "Backup file is not in this database backup directory."
            return

        confirmation = self._prompt(
            screen,
            f"Type RESTORE to restore '{self._truncate(backup_path.name, 24)}': ",
        )
        if confirmation != "RESTORE":
            self.message = "Backup restore cancelled."
            return

        db_path = self.store.db_path
        try:
            self.store.close()
            restore_database(db_path, backup_path, force=True)
            self.store = HabitStore(db_path)
        except Exception as exc:
            try:
                self.store = HabitStore(db_path)
            except Exception:
                pass
            self.message = f"Backup restore failed: {exc}"
            return

        self.view = "main"
        self.message = f"Restored from '{backup_path.name}'."

    def go_back(self) -> None:
        if self.view == "manage_backups":
            self.view = "backups"
        elif self.view in {"help", "manage_habits", "rename_habits", "complete_habits", "backups"}:
            self.view = "main"
        self.message = ""

    def run_command(self, screen: "curses.window") -> bool:
        command = self._prompt(screen, "Command: ", initial_value="/", suggestions=COMMANDS)
        if command is None:
            self.message = "Command cancelled."
            return True

        normalized = command.strip().lower()
        normalized = f"/{normalized.lstrip('/')}"

        if normalized == "/help":
            self.open_help()
            return True
        if normalized == "/backup":
            self.open_backups()
            return True
        if normalized == "/delhabit":
            self.open_manage_habits()
            return True
        if normalized == "/renamehabit":
            self.open_rename_habits()
            return True
        if normalized == "/completehabit":
            self.open_complete_habits()
            return True
        if normalized == "/quit":
            return False

        self.message = f"Unknown command: {command}. Try /help."
        return True

    def add_habit(self, screen: "curses.window") -> None:
        selected = self.selected_date
        if selected is None:
            self.message = "Select a day first."
            return

        name = self._prompt(screen, f"New daily habit from {selected.isoformat()} (Esc cancels): ")
        if name is None:
            self.message = "Habit creation cancelled."
            return
        if not name:
            self.message = "Habit creation cancelled."
            return

        try:
            self.store.create_habit(name, selected)
        except ValueError as exc:
            self.message = str(exc)
            return
        self.message = f"Added '{name}'. Dates default to Pending."

    def set_habit_status(self, habit_id: int, status: str) -> None:
        selected = self.selected_date
        if selected is None:
            self.message = "Select a day first."
            return
        try:
            self.store.set_status(habit_id, selected, status)
        except ValueError as exc:
            self.message = str(exc)
            return
        self.message = f"Marked habit as {status_label(status)} on {selected.isoformat()}."

    def delete_habit(self, screen: "curses.window", habit_id: int, habit_name: str) -> None:
        confirmation = self._prompt(screen, f"Irreversible. Type DELETE to delete '{self._truncate(habit_name, 18)}': ")
        if confirmation != "DELETE":
            self.message = "Habit deletion cancelled."
            return
        self.store.delete_habit(habit_id)
        self.message = f"Deleted '{habit_name}'."

    def rename_habit(self, screen: "curses.window", habit_id: int, habit_name: str) -> None:
        new_name = self._prompt(
            screen,
            f"Rename '{self._truncate(habit_name, 18)}' to: ",
            initial_value=habit_name,
        )
        if new_name is None or not new_name:
            self.message = "Habit rename cancelled."
            return

        try:
            self.store.rename_habit(habit_id, new_name)
        except ValueError as exc:
            self.message = str(exc)
            return
        self.message = f"Renamed '{habit_name}' to '{new_name}'."

    def complete_habit(self, screen: "curses.window", habit_id: int, habit_name: str) -> None:
        today = date.today()
        confirmation = self._prompt(
            screen,
            f"Complete {self._truncate(habit_name, 18)} as of {today.isoformat()}? Type COMPLETE: ",
        )
        if confirmation != "COMPLETE":
            self.message = "Habit completion cancelled."
            return

        try:
            self.store.complete_habit(habit_id, today)
        except ValueError as exc:
            self.message = str(exc)
            return
        self.message = f"Completed {habit_name}. It will not appear after {today.isoformat()}."

    def handle_click(self, screen: "curses.window", y: int, x: int) -> None:
        for hitbox in self.hitboxes:
            if not hitbox.contains(y, x):
                continue
            if hitbox.name == "previous":
                self.move_month(-1)
            elif hitbox.name == "next":
                self.move_month(1)
            elif hitbox.name == "back":
                self.go_back()
            elif hitbox.name == "create_backup":
                self.create_backup()
            elif hitbox.name == "manage_backups":
                self.open_manage_backups()
            elif hitbox.name == "delete_backup" and hitbox.value is not None:
                self.delete_backup(screen, Path(str(hitbox.value)))
            elif hitbox.name == "restore_backup" and hitbox.value is not None:
                self.restore_backup(screen, Path(str(hitbox.value)))
            elif hitbox.name == "delete_habit" and hitbox.value is not None:
                habit_id, habit_name = hitbox.value
                self.delete_habit(screen, int(habit_id), str(habit_name))
            elif hitbox.name == "rename_habit" and hitbox.value is not None:
                habit_id, habit_name = hitbox.value
                self.rename_habit(screen, int(habit_id), str(habit_name))
            elif hitbox.name == "complete_habit" and hitbox.value is not None:
                habit_id, habit_name = hitbox.value
                self.complete_habit(screen, int(habit_id), str(habit_name))
            elif hitbox.name == "day" and hitbox.value is not None:
                self.select_day(int(hitbox.value))
            elif hitbox.name == "add_habit":
                self.add_habit(screen)
            elif hitbox.name == "set_status" and hitbox.value is not None:
                habit_id, status = hitbox.value
                self.set_habit_status(int(habit_id), str(status))
            return

    def render(self, screen: "curses.window") -> None:
        screen.erase()
        self.hitboxes.clear()
        height, width = screen.getmaxyx()

        if height < 17 or width < 76:
            screen.addstr(0, 0, "Make the terminal at least 76x17.")
            screen.refresh()
            return

        if self.view == "help":
            self._draw_help_page(screen)
        elif self.view == "manage_habits":
            self._draw_manage_habits_page(screen)
        elif self.view == "rename_habits":
            self._draw_rename_habits_page(screen)
        elif self.view == "complete_habits":
            self._draw_complete_habits_page(screen)
        elif self.view == "backups":
            self._draw_backups_page(screen)
        elif self.view == "manage_backups":
            self._draw_manage_backups_page(screen)
        else:
            self._draw_header(screen)
            self._draw_calendar(screen)
            self._draw_day_panel(screen)
            self._draw_footer(screen)
        screen.refresh()

    def _draw_header(self, screen: "curses.window") -> None:
        title = f"{calendar.month_name[self.selection.month]} {self.selection.year}"
        previous_label = "< Prev"
        next_label = "Next >"
        title_x = CALENDAR_LEFT + 8
        next_x = title_x + len(title) + 4

        self._addstr(screen, 1, CALENDAR_LEFT, previous_label, curses.A_BOLD)
        self._addstr(screen, 1, title_x, title, self._color(1) | curses.A_BOLD)
        self._addstr(screen, 1, next_x, next_label, curses.A_BOLD)

        self.hitboxes.append(HitBox("previous", 1, CALENDAR_LEFT, CALENDAR_LEFT + len(previous_label) - 1))
        self.hitboxes.append(HitBox("next", 1, next_x, next_x + len(next_label) - 1))

    def _draw_calendar(self, screen: "curses.window") -> None:
        today = date.today()
        summary = self.store.month_summary(self.selection.year, self.selection.month)
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

                done_count, missed_count = summary.get(day, (0, 0))
                marker = "!" if missed_count else "+" if done_count else " "
                label = f"{day:2}{marker}"
                self._addstr(screen, y, x, label, style)
                self.hitboxes.append(HitBox("day", y, x, x + CELL_WIDTH - 2, day))

    def _draw_day_panel(self, screen: "curses.window") -> None:
        selected = self.selected_date
        if selected is None:
            self._addstr(screen, 1, DETAIL_LEFT, "Select a day", curses.A_BOLD)
            self._addstr(screen, 3, DETAIL_LEFT, "Click a date to manage daily habits.")
            return

        self._addstr(screen, 1, DETAIL_LEFT, selected.isoformat(), self._color(1) | curses.A_BOLD)
        add_label = "+ Add daily habit"
        self._addstr(screen, 3, DETAIL_LEFT, add_label, curses.A_BOLD)
        self.hitboxes.append(HitBox("add_habit", 3, DETAIL_LEFT, DETAIL_LEFT + len(add_label) - 1))

        habits = self.store.habits_for_day(selected)
        if not habits:
            self._addstr(screen, 5, DETAIL_LEFT, "No habits active yet.")
            self._addstr(screen, 6, DETAIL_LEFT, "Add one from this date to start tracking.")
            return

        for index, habit in enumerate(habits):
            y = 5 + index * 3
            self._addstr(screen, y, DETAIL_LEFT, self._truncate(habit.name, 24), curses.A_BOLD)

            pending_label = "Pending"
            done_label = "Done"
            missed_label = "Missed"
            pending_x = DETAIL_LEFT + 2
            done_x = DETAIL_LEFT + 12
            missed_x = DETAIL_LEFT + 21
            self._addstr(screen, y + 1, pending_x, pending_label, self._action_style(habit.status, STATUS_PENDING))
            self._addstr(screen, y + 1, done_x, done_label, self._action_style(habit.status, STATUS_DONE))
            self._addstr(screen, y + 1, missed_x, missed_label, self._action_style(habit.status, STATUS_MISSED))
            self.hitboxes.append(HitBox("set_status", y + 1, pending_x, pending_x + len(pending_label) - 1, (habit.habit_id, STATUS_PENDING)))
            self.hitboxes.append(HitBox("set_status", y + 1, done_x, done_x + len(done_label) - 1, (habit.habit_id, STATUS_DONE)))
            self.hitboxes.append(HitBox("set_status", y + 1, missed_x, missed_x + len(missed_label) - 1, (habit.habit_id, STATUS_MISSED)))

    def _draw_help_page(self, screen: "curses.window") -> None:
        back_label = "< Back"
        self._addstr(screen, 1, CALENDAR_LEFT, back_label, curses.A_BOLD)
        self._addstr(screen, 1, DETAIL_LEFT, "Commands", self._color(1) | curses.A_BOLD)
        self.hitboxes.append(HitBox("back", 1, CALENDAR_LEFT, CALENDAR_LEFT + len(back_label) - 1))

        for index, (command, description) in enumerate(COMMANDS):
            y = 4 + index * 2
            self._addstr(screen, y, CALENDAR_LEFT, command, curses.A_BOLD)
            self._addstr(screen, y, CALENDAR_LEFT + 14, description)
        self._addstr(screen, 15, CALENDAR_LEFT, "Press / from the main screen to enter a command. Press b to go back.")
        self._draw_message(screen, 16)

    def _draw_backups_page(self, screen: "curses.window") -> None:
        back_label = "< Back"
        self._addstr(screen, 1, CALENDAR_LEFT, back_label, curses.A_BOLD)
        self._addstr(screen, 1, DETAIL_LEFT, "Backups", self._color(1) | curses.A_BOLD)
        self.hitboxes.append(HitBox("back", 1, CALENDAR_LEFT, CALENDAR_LEFT + len(back_label) - 1))

        create_label = "Create Backup"
        manage_label = "Manage Backups"
        self._addstr(screen, 3, CALENDAR_LEFT, create_label, curses.A_BOLD)
        self._addstr(screen, 5, CALENDAR_LEFT, manage_label, curses.A_BOLD)
        self.hitboxes.append(HitBox("create_backup", 3, CALENDAR_LEFT, CALENDAR_LEFT + len(create_label) - 1))
        self.hitboxes.append(HitBox("manage_backups", 5, CALENDAR_LEFT, CALENDAR_LEFT + len(manage_label) - 1))
        self._draw_message(screen, 16)

    def _draw_manage_backups_page(self, screen: "curses.window") -> None:
        back_label = "< Back"
        self._addstr(screen, 1, CALENDAR_LEFT, back_label, curses.A_BOLD)
        self._addstr(screen, 1, DETAIL_LEFT, "Manage Backups", self._color(1) | curses.A_BOLD)
        self.hitboxes.append(HitBox("back", 1, CALENDAR_LEFT, CALENDAR_LEFT + len(back_label) - 1))

        backups = list_backup_files(self.store.db_path)
        legend = "Legend: a automatic, o on-demand"
        if not backups:
            self._addstr(screen, 4, CALENDAR_LEFT, "No backups found.")
            self._addstr(screen, 15, CALENDAR_LEFT, legend)
            self._draw_message(screen, 16)
            return

        self._addstr(screen, 3, CALENDAR_LEFT, "Backup File", curses.A_BOLD)
        self._addstr(screen, 3, DETAIL_LEFT + 20, "Actions", curses.A_BOLD)

        for index, backup_path in enumerate(backups[:9]):
            y = 5 + index
            backup_label = self._truncate(backup_path.name, 42)
            restore_label = "Restore"
            delete_label = "Delete"
            restore_x = DETAIL_LEFT + 20
            delete_x = restore_x + len(restore_label) + 3
            self._addstr(screen, y, CALENDAR_LEFT, backup_label)
            self._addstr(screen, y, restore_x, restore_label, curses.A_BOLD)
            self._addstr(screen, y, delete_x, delete_label, curses.A_BOLD)
            self.hitboxes.append(
                HitBox("restore_backup", y, restore_x, restore_x + len(restore_label) - 1, backup_path)
            )
            self.hitboxes.append(
                HitBox("delete_backup", y, delete_x, delete_x + len(delete_label) - 1, backup_path)
            )

        self._addstr(screen, 15, CALENDAR_LEFT, legend)
        if len(backups) > 9:
            self._addstr(screen, 16, CALENDAR_LEFT, f"Showing 9 of {len(backups)} backups.")
        self._draw_message(screen, 17)

    def _draw_manage_habits_page(self, screen: "curses.window") -> None:
        back_label = "< Back"
        self._addstr(screen, 1, CALENDAR_LEFT, back_label, curses.A_BOLD)
        self._addstr(screen, 1, DETAIL_LEFT, "Manage Habits", self._color(1) | curses.A_BOLD)
        self.hitboxes.append(HitBox("back", 1, CALENDAR_LEFT, CALENDAR_LEFT + len(back_label) - 1))

        habits = self.store.list_habits()
        if not habits:
            self._addstr(screen, 4, CALENDAR_LEFT, "No habits to manage.")
            self._draw_message(screen)
            return

        self._addstr(screen, 3, CALENDAR_LEFT, "Delete Habit", curses.A_BOLD)
        self._addstr(screen, 4, CALENDAR_LEFT, "Deleting a habit permanently removes its saved daily statuses.")

        for index, habit in enumerate(habits[:9]):
            y = 6 + index
            habit_label = f"{self._truncate(habit.name, 28)} ({habit.start_date.isoformat()})"
            delete_label = "Delete"
            delete_x = DETAIL_LEFT + 20
            self._addstr(screen, y, CALENDAR_LEFT, habit_label)
            self._addstr(screen, y, delete_x, delete_label, curses.A_BOLD)
            self.hitboxes.append(HitBox("delete_habit", y, delete_x, delete_x + len(delete_label) - 1, (habit.habit_id, habit.name)))

        if len(habits) > 9:
            self._addstr(screen, 15, CALENDAR_LEFT, f"Showing 9 of {len(habits)} habits.")
        self._draw_message(screen, 16)

    def _draw_rename_habits_page(self, screen: "curses.window") -> None:
        back_label = "< Back"
        self._addstr(screen, 1, CALENDAR_LEFT, back_label, curses.A_BOLD)
        self._addstr(screen, 1, DETAIL_LEFT, "Rename Habits", self._color(1) | curses.A_BOLD)
        self.hitboxes.append(HitBox("back", 1, CALENDAR_LEFT, CALENDAR_LEFT + len(back_label) - 1))

        habits = self.store.list_habits()
        if not habits:
            self._addstr(screen, 4, CALENDAR_LEFT, "No habits to rename.")
            self._draw_message(screen)
            return

        self._addstr(screen, 3, CALENDAR_LEFT, "Rename Habit", curses.A_BOLD)
        self._addstr(screen, 4, CALENDAR_LEFT, "Choose a habit and enter its new name.")

        for index, habit in enumerate(habits[:9]):
            y = 6 + index
            habit_label = f"{self._truncate(habit.name, 28)} ({habit.start_date.isoformat()})"
            rename_label = "Rename"
            rename_x = DETAIL_LEFT + 20
            self._addstr(screen, y, CALENDAR_LEFT, habit_label)
            self._addstr(screen, y, rename_x, rename_label, curses.A_BOLD)
            self.hitboxes.append(HitBox("rename_habit", y, rename_x, rename_x + len(rename_label) - 1, (habit.habit_id, habit.name)))

        if len(habits) > 9:
            self._addstr(screen, 15, CALENDAR_LEFT, f"Showing 9 of {len(habits)} habits.")
        self._draw_message(screen, 16)

    def _draw_complete_habits_page(self, screen: "curses.window") -> None:
        back_label = "< Back"
        self._addstr(screen, 1, CALENDAR_LEFT, back_label, curses.A_BOLD)
        self._addstr(screen, 1, DETAIL_LEFT, "Complete Habits", self._color(1) | curses.A_BOLD)
        self.hitboxes.append(HitBox("back", 1, CALENDAR_LEFT, CALENDAR_LEFT + len(back_label) - 1))

        habits = self.store.list_active_habits()
        if not habits:
            self._addstr(screen, 4, CALENDAR_LEFT, "No active habits to complete.")
            self._draw_message(screen)
            return

        self._addstr(screen, 3, CALENDAR_LEFT, "Complete Habit", curses.A_BOLD)
        self._addstr(screen, 4, CALENDAR_LEFT, "Completion archives the habit after today and keeps its history.")

        for index, habit in enumerate(habits[:9]):
            y = 6 + index
            habit_label = f"{self._truncate(habit.name, 28)} ({habit.start_date.isoformat()})"
            complete_label = "Complete"
            complete_x = DETAIL_LEFT + 20
            self._addstr(screen, y, CALENDAR_LEFT, habit_label)
            self._addstr(screen, y, complete_x, complete_label, curses.A_BOLD)
            self.hitboxes.append(
                HitBox("complete_habit", y, complete_x, complete_x + len(complete_label) - 1, (habit.habit_id, habit.name))
            )

        if len(habits) > 9:
            self._addstr(screen, 15, CALENDAR_LEFT, f"Showing 9 of {len(habits)} active habits.")
        self._draw_message(screen, 16)

    def _draw_footer(self, screen: "curses.window") -> None:
        footer_y = CALENDAR_TOP + 8
        self._draw_message(screen)
        self._addstr(screen, footer_y + 2, CALENDAR_LEFT, "Mouse: click days, add habits, mark Pending/Done/Missed. Keys: / commands, h help, q quit, arrows/PgUp/PgDn, t today, a add.")
        self._addstr(screen, footer_y + 3, CALENDAR_LEFT, "Calendar markers: + at least one done, ! at least one missed, unmarked days are pending.")

    def _draw_message(self, screen: "curses.window", y: int = CALENDAR_TOP + 8) -> None:
        if self.message:
            self._addstr(screen, y, CALENDAR_LEFT, self._truncate(self.message, 70))

    def _prompt(
        self,
        screen: "curses.window",
        prompt: str,
        initial_value: str = "",
        suggestions: Sequence[tuple[str, str]] = (),
    ) -> str | None:
        y = CALENDAR_TOP + 10
        value = list(initial_value)

        def clear_line(row: int) -> None:
            height, _ = screen.getmaxyx()
            if 0 <= row < height:
                screen.move(row, 0)
                screen.clrtoeol()

        def matching_suggestions() -> list[tuple[str, str]]:
            typed = "".join(value).strip().lower()
            if not typed:
                return []
            normalized = f"/{typed.lstrip('/')}"
            return [item for item in suggestions if item[0].startswith(normalized)]

        def render_prompt() -> None:
            clear_line(y)
            self._addstr(screen, y, CALENDAR_LEFT, prompt)
            self._addstr(screen, y, CALENDAR_LEFT + len(prompt), "".join(value))
            for offset in range(1, 4):
                clear_line(y + offset)
            for offset, (command, description) in enumerate(matching_suggestions()[:3], start=1):
                self._addstr(
                    screen,
                    y + offset,
                    CALENDAR_LEFT + len(prompt),
                    f"{command}  {description}",
                    curses.A_DIM,
                )

        render_prompt()
        curses.curs_set(1)
        try:
            while True:
                input_x = CALENDAR_LEFT + len(prompt) + len(value)
                screen.move(y, input_x)
                screen.refresh()
                key = screen.getch()
                if key == 27:
                    return None
                if key in (curses.KEY_ENTER, 10, 13):
                    return "".join(value).strip()
                if key == 9 and suggestions:
                    matches = matching_suggestions()
                    if len(matches) == 1:
                        value = list(matches[0][0])
                    render_prompt()
                    continue
                if key in (curses.KEY_BACKSPACE, 8, 127):
                    if value:
                        value.pop()
                        render_prompt()
                    continue
                if 32 <= key <= 126 and len(value) < 40:
                    value.append(chr(key))
                    render_prompt()
        finally:
            curses.curs_set(0)

    def _status_style(self, status: str) -> int:
        if status == STATUS_MISSED:
            return self._color(4) | curses.A_BOLD
        if status == STATUS_PENDING:
            return self._color(6) | curses.A_BOLD
        return self._color(5) | curses.A_BOLD

    def _action_style(self, current_status: str, action_status: str) -> int:
        if current_status == action_status:
            return self._status_style(action_status)
        return curses.A_DIM

    def _color(self, pair_number: int) -> int:
        if not self.use_color or not curses.has_colors():
            return curses.A_REVERSE
        return curses.color_pair(pair_number)

    @staticmethod
    def _truncate(text: str, max_length: int) -> str:
        if len(text) <= max_length:
            return text
        return text[: max(0, max_length - 3)] + "..."

    @staticmethod
    def _addstr(screen: "curses.window", y: int, x: int, text: str, style: int = curses.A_NORMAL) -> None:
        height, width = screen.getmaxyx()
        if y < 0 or y >= height or x >= width:
            return
        screen.addstr(y, max(0, x), text[: max(0, width - x - 1)], style)


def build_month_view(selection: CalendarSelection, store: HabitStore | None = None) -> str:
    today = date.today()
    weeks = calendar.monthcalendar(selection.year, selection.month)
    summary = store.month_summary(selection.year, selection.month) if store else {}
    lines = [f"{calendar.month_name[selection.month]} {selection.year}".center(20), WEEKDAY_HEADER]

    for week in weeks:
        day_cells: list[str] = []
        for day in week:
            if day == 0:
                day_cells.append("  ")
                continue
            done_count, missed_count = summary.get(day, (0, 0))
            marker = "!" if missed_count else "+" if done_count else "*" if (
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
        curses.init_pair(4, curses.COLOR_WHITE, curses.COLOR_RED)
        curses.init_pair(5, curses.COLOR_BLACK, curses.COLOR_GREEN)
        curses.init_pair(6, curses.COLOR_BLACK, curses.COLOR_YELLOW)

    while True:
        app.render(screen)
        key = screen.getch()

        if key in (ord("q"), ord("Q")):
            break
        if app.view != "main" and key in (ord("b"), ord("B")):
            app.go_back()
        elif key == 27:
            if app.view == "main":
                break
            app.go_back()
        elif app.view == "main" and key == ord("/"):
            if not app.run_command(screen):
                break
        elif app.view == "main" and key in (ord("h"), ord("H")):
            app.open_help()
        elif app.view == "main" and key in (curses.KEY_LEFT, curses.KEY_PPAGE):
            app.move_month(-1)
        elif app.view == "main" and key in (curses.KEY_RIGHT, curses.KEY_NPAGE):
            app.move_month(1)
        elif app.view == "main" and key in (ord("a"), ord("A")):
            app.add_habit(screen)
        elif app.view == "main" and key == ord("t"):
            today = date.today()
            app.selection = CalendarSelection(today.year, today.month)
            app.selected_day = today.day
            app.message = ""
        elif key == curses.KEY_MOUSE:
            try:
                _, x, y, _, button_state = curses.getmouse()
            except curses.error:
                continue
            if button_state & (curses.BUTTON1_CLICKED | curses.BUTTON1_PRESSED):
                app.handle_click(screen, y, x)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Open an interactive daily habit tracker with a monthly calendar."
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
        "--db",
        type=Path,
        default=DEFAULT_DB_PATH,
        help=f"SQLite database path; defaults to {DEFAULT_DB_PATH}",
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
    parser.add_argument(
        "--backup",
        nargs="?",
        const="",
        metavar="PATH",
        help="create an on-demand SQLite backup and exit; defaults to a timestamped file beside the database",
    )
    parser.add_argument(
        "--restore",
        type=Path,
        metavar="PATH",
        help="restore the SQLite database from a backup file and exit",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="allow --restore to overwrite an existing database without confirmation",
    )
    return parser.parse_args()


def confirm_restore(db_path: Path, backup_path: Path) -> bool:
    prompt = (
        f"Restore {backup_path} over existing database {db_path}? "
        "Type RESTORE to continue: "
    )
    try:
        return input(prompt) == "RESTORE"
    except EOFError:
        return False


def main() -> None:
    args = parse_args()

    if args.restore is not None:
        if Path(args.db).exists() and not args.force and not confirm_restore(args.db, args.restore):
            raise SystemExit("Restore cancelled.")
        restore_database(args.db, args.restore, force=True)
        print(f"Restored {args.db} from {args.restore}")
        return

    selection = CalendarSelection.from_args(args.year, args.month)
    store = HabitStore(args.db)

    try:
        if args.backup is not None:
            destination = Path(args.backup) if args.backup else None
            backup_path = store.backup(destination)
            print(f"Backup saved to {backup_path}")
            return

        if args.plain:
            print(build_month_view(selection, store))
            return

        app = CalendarApp(selection=selection, store=store, use_color=not args.no_color)
        try:
            automatic_backup_path = create_automatic_backup(store)
        except OSError as exc:
            app.message = f"Automatic backup failed: {exc}"
        else:
            if automatic_backup_path is not None:
                app.message = f"Automatic backup saved to {automatic_backup_path}"
        curses.wrapper(run_curses, app)
    finally:
        store.close()


if __name__ == "__main__":
    main()
