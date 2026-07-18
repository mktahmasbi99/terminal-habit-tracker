#!/usr/bin/env python3
"""Interactive daily habit tracker with a navigable monthly calendar."""

from __future__ import annotations

import argparse
import calendar
import curses
import re
import sqlite3
import shutil
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Sequence


WEEKDAY_NAMES = ("Mo", "Tu", "We", "Th", "Fr", "Sa", "Su")
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
    ("/managehabit", "Open habit management."),
    ("/notes", "Browse saved habit notes."),
    ("/stats", "Open habit streak stats."),
    ("/viewall", "Show all habits on stats pages."),
    ("/viewactive", "Show only active habits on stats pages."),
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
    archived_at: date | None = None


@dataclass(frozen=True)
class HabitStatus:
    habit_id: int
    name: str
    start_date: date
    status: str
    archived_at: date | None = None


@dataclass(frozen=True)
class HabitChallenge:
    challenge_id: int
    habit_id: int
    start_date: date
    end_date: date
    completed_at: date | None = None


@dataclass(frozen=True)
class ChallengeProgress:
    challenge: HabitChallenge
    current_streak: int
    duration_days: int


@dataclass(frozen=True)
class HabitNoteCount:
    habit_id: int
    name: str
    note_count: int


@dataclass(frozen=True)
class HabitNoteRef:
    habit_id: int
    habit_name: str
    note_date: date


@dataclass(frozen=True)
class HabitStreak:
    start_date: date
    end_date: date
    length: int


@dataclass(frozen=True)
class HabitStats:
    habit: Habit
    current_streak: int
    longest_streak: HabitStreak | None
    streaks: list[HabitStreak]
    note_count: int
    active_today: bool


@dataclass(frozen=True)
class HabitActivePeriod:
    period_number: int
    start_date: date
    end_date: date


@dataclass(frozen=True)
class PendingNotification:
    day: date
    pending_count: int


@dataclass(frozen=True)
class HitBox:
    name: str
    y: int
    x1: int
    x2: int
    value: Any = None

    def contains(self, y: int, x: int) -> bool:
        return self.y == y and self.x1 <= x <= self.x2


@dataclass(frozen=True)
class NoteDisplayLine:
    line_index: int
    start: int
    end: int
    text: str
    show_line_number: bool


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
                archived_at TEXT,
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

            CREATE TABLE IF NOT EXISTS habit_notes (
                habit_id INTEGER NOT NULL,
                note_date TEXT NOT NULL,
                body TEXT NOT NULL,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (habit_id, note_date),
                FOREIGN KEY (habit_id) REFERENCES habits(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS habit_challenges (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                habit_id INTEGER NOT NULL,
                start_date TEXT NOT NULL,
                end_date TEXT NOT NULL,
                completed_at TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (habit_id) REFERENCES habits(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS habit_archive_periods (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                habit_id INTEGER NOT NULL,
                archived_at TEXT NOT NULL,
                resurrected_at TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (habit_id) REFERENCES habits(id) ON DELETE CASCADE
            );
            """
        )
        habit_columns = {row["name"] for row in self.connection.execute("PRAGMA table_info(habits)")}
        if "completed_at" not in habit_columns:
            self.connection.execute("ALTER TABLE habits ADD COLUMN completed_at TEXT")
        if "archived_at" not in habit_columns:
            self.connection.execute("ALTER TABLE habits ADD COLUMN archived_at TEXT")
        self._migrate_completed_at()
        self._ensure_archive_periods()
        self.connection.commit()

    def _migrate_completed_at(self) -> None:
        today = date.today()
        rows = self.connection.execute(
            """
            SELECT id, start_date, completed_at, archived_at
            FROM habits
            WHERE completed_at IS NOT NULL
            """
        ).fetchall()
        for row in rows:
            habit_id = int(row["id"])
            completed_at = date.fromisoformat(str(row["completed_at"]))
            if row["archived_at"] is None and completed_at > today:
                challenge_exists = self.connection.execute(
                    """
                    SELECT 1
                    FROM habit_challenges
                    WHERE habit_id = ?
                        AND start_date = ?
                        AND end_date = ?
                    """,
                    (habit_id, today.isoformat(), completed_at.isoformat()),
                ).fetchone()
                if challenge_exists is None:
                    self.connection.execute(
                        """
                        INSERT INTO habit_challenges (habit_id, start_date, end_date)
                        VALUES (?, ?, ?)
                        """,
                        (habit_id, today.isoformat(), completed_at.isoformat()),
                    )
            elif row["archived_at"] is None:
                self.connection.execute(
                    "UPDATE habits SET archived_at = ? WHERE id = ?",
                    (completed_at.isoformat(), habit_id),
                )
            self.connection.execute("UPDATE habits SET completed_at = NULL WHERE id = ?", (habit_id,))

    def _ensure_archive_periods(self) -> None:
        rows = self.connection.execute(
            """
            SELECT id, archived_at
            FROM habits
            WHERE archived_at IS NOT NULL
            """
        ).fetchall()
        for row in rows:
            period_exists = self.connection.execute(
                """
                SELECT 1
                FROM habit_archive_periods
                WHERE habit_id = ?
                    AND archived_at = ?
                    AND resurrected_at IS NULL
                """,
                (int(row["id"]), str(row["archived_at"])),
            ).fetchone()
            if period_exists is None:
                self.connection.execute(
                    """
                    INSERT INTO habit_archive_periods (habit_id, archived_at)
                    VALUES (?, ?)
                    """,
                    (int(row["id"]), str(row["archived_at"])),
                )

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
            SELECT id, name, start_date, archived_at
            FROM habits
            ORDER BY start_date, name
            """
        ).fetchall()
        return [
            Habit(
                habit_id=int(row["id"]),
                name=str(row["name"]),
                start_date=date.fromisoformat(str(row["start_date"])),
                archived_at=(
                    date.fromisoformat(str(row["archived_at"]))
                    if row["archived_at"] is not None
                    else None
                ),
            )
            for row in rows
        ]

    def list_active_habits(self) -> list[Habit]:
        return [habit for habit in self.list_habits() if habit.archived_at is None]

    def list_archived_habits(self) -> list[Habit]:
        return [habit for habit in self.list_habits() if habit.archived_at is not None]

    def habit_active_on(self, habit_id: int, day: date) -> bool:
        row = self.connection.execute(
            """
            SELECT 1
            FROM habits
            WHERE id = ?
                AND start_date <= ?
                AND archived_at IS NULL
                AND NOT EXISTS (
                    SELECT 1
                    FROM habit_archive_periods
                    WHERE habit_archive_periods.habit_id = habits.id
                        AND habit_archive_periods.archived_at <= ?
                        AND (
                            habit_archive_periods.resurrected_at IS NULL
                            OR habit_archive_periods.resurrected_at > ?
                        )
                )
            """,
            (habit_id, day.isoformat(), day.isoformat(), day.isoformat()),
        ).fetchone()
        return row is not None

    def archive_habit(self, habit_id: int, archive_date: date) -> None:
        row = self.connection.execute(
            "SELECT start_date, archived_at FROM habits WHERE id = ?",
            (habit_id,),
        ).fetchone()
        if row is None:
            raise ValueError("Habit does not exist.")
        if row["archived_at"] is not None:
            raise ValueError("Habit is already archived.")

        start_date = date.fromisoformat(str(row["start_date"]))
        if archive_date < start_date:
            raise ValueError("Archive date cannot be before the habit start date.")

        self.connection.execute(
            "UPDATE habits SET archived_at = ? WHERE id = ?",
            (archive_date.isoformat(), habit_id),
        )
        self.connection.execute(
            """
            INSERT INTO habit_archive_periods (habit_id, archived_at)
            VALUES (?, ?)
            """,
            (habit_id, archive_date.isoformat()),
        )
        self.connection.commit()

    def resurrect_habit(self, habit_id: int) -> None:
        row = self.connection.execute(
            "SELECT archived_at FROM habits WHERE id = ?",
            (habit_id,),
        ).fetchone()
        if row is None:
            raise ValueError("Habit does not exist.")
        if row["archived_at"] is None:
            raise ValueError("Habit is already active.")

        today = date.today()
        self.connection.execute("UPDATE habits SET archived_at = NULL WHERE id = ?", (habit_id,))
        self.connection.execute(
            """
            UPDATE habit_archive_periods
            SET resurrected_at = ?
            WHERE habit_id = ?
                AND resurrected_at IS NULL
            """,
            (today.isoformat(), habit_id),
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
                habits.archived_at,
                COALESCE(habit_logs.status, ?) AS status
            FROM habits
            LEFT JOIN habit_logs
                ON habit_logs.habit_id = habits.id
                AND habit_logs.log_date = ?
            WHERE habits.start_date <= ?
                AND habits.archived_at IS NULL
                AND NOT EXISTS (
                    SELECT 1
                    FROM habit_archive_periods
                    WHERE habit_archive_periods.habit_id = habits.id
                        AND habit_archive_periods.archived_at <= ?
                        AND (
                            habit_archive_periods.resurrected_at IS NULL
                            OR habit_archive_periods.resurrected_at > ?
                        )
                )
            ORDER BY habits.start_date, habits.name
            """,
            (STATUS_PENDING, day.isoformat(), day.isoformat(), day.isoformat(), day.isoformat()),
        ).fetchall()

        return [
            HabitStatus(
                habit_id=int(row["id"]),
                name=str(row["name"]),
                start_date=date.fromisoformat(str(row["start_date"])),
                status=str(row["status"]),
                archived_at=(
                    date.fromisoformat(str(row["archived_at"]))
                    if row["archived_at"] is not None
                    else None
                ),
            )
            for row in rows
        ]

    def get_note(self, habit_id: int, note_date: date) -> str:
        row = self.connection.execute(
            "SELECT body FROM habit_notes WHERE habit_id = ? AND note_date = ?",
            (habit_id, note_date.isoformat()),
        ).fetchone()
        if row is None:
            return ""
        return str(row["body"])

    def save_note(self, habit_id: int, note_date: date, body: str) -> None:
        if not self.habit_active_on(habit_id, note_date):
            raise ValueError("Habit is not active on the selected date.")

        if not body.strip():
            self.connection.execute(
                "DELETE FROM habit_notes WHERE habit_id = ? AND note_date = ?",
                (habit_id, note_date.isoformat()),
            )
            self.connection.commit()
            return

        self.connection.execute(
            """
            INSERT INTO habit_notes (habit_id, note_date, body, updated_at)
            VALUES (?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(habit_id, note_date)
            DO UPDATE SET body = excluded.body, updated_at = CURRENT_TIMESTAMP
            """,
            (habit_id, note_date.isoformat(), body),
        )
        self.connection.commit()

    def note_habit_ids_for_day(self, note_date: date) -> set[int]:
        rows = self.connection.execute(
            "SELECT habit_id FROM habit_notes WHERE note_date = ?",
            (note_date.isoformat(),),
        ).fetchall()
        return {int(row["habit_id"]) for row in rows}

    def note_counts_by_habit(self) -> list[HabitNoteCount]:
        rows = self.connection.execute(
            """
            SELECT
                habits.id,
                habits.name,
                COUNT(habit_notes.note_date) AS note_count
            FROM habits
            LEFT JOIN habit_notes
                ON habit_notes.habit_id = habits.id
            GROUP BY habits.id, habits.name
            ORDER BY lower(habits.name), habits.name, habits.id
            """
        ).fetchall()
        return [
            HabitNoteCount(
                habit_id=int(row["id"]),
                name=str(row["name"]),
                note_count=int(row["note_count"]),
            )
            for row in rows
        ]

    def notes_for_habit(self, habit_id: int) -> list[HabitNoteRef]:
        rows = self.connection.execute(
            """
            SELECT habits.id, habits.name, habit_notes.note_date
            FROM habit_notes
            JOIN habits
                ON habits.id = habit_notes.habit_id
            WHERE habits.id = ?
            ORDER BY habit_notes.note_date DESC
            """,
            (habit_id,),
        ).fetchall()
        return [
            HabitNoteRef(
                habit_id=int(row["id"]),
                habit_name=str(row["name"]),
                note_date=date.fromisoformat(str(row["note_date"])),
            )
            for row in rows
        ]

    def note_count_for_habit(self, habit_id: int) -> int:
        row = self.connection.execute(
            "SELECT COUNT(*) AS note_count FROM habit_notes WHERE habit_id = ?",
            (habit_id,),
        ).fetchone()
        return int(row["note_count"]) if row is not None else 0

    def create_challenge(self, habit_id: int, start_date: date, end_date: date) -> int:
        if end_date < start_date:
            raise ValueError("Challenge end date cannot be before its start date.")
        if not self.habit_active_on(habit_id, start_date):
            raise ValueError("Habit is not active for the challenge start date.")

        overlapping = self.connection.execute(
            """
            SELECT 1
            FROM habit_challenges
            WHERE habit_id = ?
                AND start_date <= ?
                AND end_date >= ?
            """,
            (habit_id, end_date.isoformat(), start_date.isoformat()),
        ).fetchone()
        if overlapping is not None:
            raise ValueError("Habit already has a challenge in this date range.")

        cursor = self.connection.execute(
            """
            INSERT INTO habit_challenges (habit_id, start_date, end_date)
            VALUES (?, ?, ?)
            """,
            (habit_id, start_date.isoformat(), end_date.isoformat()),
        )
        self.connection.commit()
        return int(cursor.lastrowid)

    def challenge_for_habit_day(self, habit_id: int, day: date) -> HabitChallenge | None:
        row = self.connection.execute(
            """
            SELECT id, habit_id, start_date, end_date, completed_at
            FROM habit_challenges
            WHERE habit_id = ?
                AND start_date <= ?
                AND end_date >= ?
            ORDER BY start_date DESC, id DESC
            LIMIT 1
            """,
            (habit_id, day.isoformat(), day.isoformat()),
        ).fetchone()
        if row is None:
            return None
        return HabitChallenge(
            challenge_id=int(row["id"]),
            habit_id=int(row["habit_id"]),
            start_date=date.fromisoformat(str(row["start_date"])),
            end_date=date.fromisoformat(str(row["end_date"])),
            completed_at=(
                date.fromisoformat(str(row["completed_at"]))
                if row["completed_at"] is not None
                else None
            ),
        )

    def challenge_progress_for_habit_day(self, habit_id: int, day: date) -> ChallengeProgress | None:
        challenge = self.challenge_for_habit_day(habit_id, day)
        if challenge is None:
            return None

        progress_day = min(day, challenge.end_date)
        challenge_habit = self._get_habit(habit_id)
        challenge_habit = Habit(
            habit_id=challenge_habit.habit_id,
            name=challenge_habit.name,
            start_date=challenge.start_date,
            archived_at=challenge_habit.archived_at,
        )
        streaks = self._streaks_for_habit(challenge_habit, progress_day)
        current_streak = 0
        if streaks:
            day_status = self._status_for_habit_day(habit_id, progress_day)
            current_end = progress_day if day_status == STATUS_DONE else progress_day - timedelta(days=1)
            latest_streak = max(streaks, key=lambda streak: streak.end_date)
            if latest_streak.end_date == current_end:
                current_streak = latest_streak.length

        return ChallengeProgress(
            challenge=challenge,
            current_streak=current_streak,
            duration_days=(challenge.end_date - challenge.start_date).days + 1,
        )

    def habit_stats_list(self, include_archived: bool = False) -> list[HabitStats]:
        today = date.today()
        habits = self.list_habits()
        if not include_archived:
            habits = [habit for habit in habits if self._habit_active_on_date(habit, today)]
        return [self.habit_stats(habit.habit_id) for habit in habits]

    def habit_stats(self, habit_id: int) -> HabitStats:
        habit = self._get_habit(habit_id)
        today = date.today()
        streaks = self._streaks_for_habit(habit, today)
        active_today = self._habit_active_on_date(habit, today)
        current_streak = self.current_streak_for_habit(habit.habit_id, today)

        longest_streak = streaks[0] if streaks else None
        return HabitStats(
            habit=habit,
            current_streak=current_streak,
            longest_streak=longest_streak,
            streaks=streaks,
            note_count=self.note_count_for_habit(habit.habit_id),
            active_today=active_today,
        )

    def active_periods_for_habit(self, habit_id: int) -> list[HabitActivePeriod]:
        """Reconstructs closed active stretches from habit_archive_periods.

        Only returns stretches that have already ended (bounded by an
        archived_at date); it never includes a still-open current stretch,
        since callers only need this for habits that are currently archived
        (where the trailing archive_periods row is guaranteed to exist and
        be unresurrected).
        """
        habit = self._get_habit(habit_id)
        rows = self.connection.execute(
            """
            SELECT archived_at, resurrected_at
            FROM habit_archive_periods
            WHERE habit_id = ?
            ORDER BY archived_at ASC, id ASC
            """,
            (habit_id,),
        ).fetchall()

        periods: list[HabitActivePeriod] = []
        stretch_start = habit.start_date
        for index, row in enumerate(rows, start=1):
            archived_at = date.fromisoformat(str(row["archived_at"]))
            periods.append(
                HabitActivePeriod(period_number=index, start_date=stretch_start, end_date=archived_at)
            )
            resurrected_at = row["resurrected_at"]
            if resurrected_at is None:
                break
            stretch_start = date.fromisoformat(str(resurrected_at))
        return periods

    def note_count_for_habit_in_range(self, habit_id: int, start_date: date, end_date: date) -> int:
        row = self.connection.execute(
            """
            SELECT COUNT(*) AS note_count
            FROM habit_notes
            WHERE habit_id = ?
                AND note_date >= ?
                AND note_date <= ?
            """,
            (habit_id, start_date.isoformat(), end_date.isoformat()),
        ).fetchone()
        return int(row["note_count"]) if row is not None else 0

    def notes_for_habit_in_range(self, habit_id: int, start_date: date, end_date: date) -> list[HabitNoteRef]:
        rows = self.connection.execute(
            """
            SELECT habits.id, habits.name, habit_notes.note_date
            FROM habit_notes
            JOIN habits
                ON habits.id = habit_notes.habit_id
            WHERE habits.id = ?
                AND habit_notes.note_date >= ?
                AND habit_notes.note_date <= ?
            ORDER BY habit_notes.note_date DESC
            """,
            (habit_id, start_date.isoformat(), end_date.isoformat()),
        ).fetchall()
        return [
            HabitNoteRef(
                habit_id=int(row["id"]),
                habit_name=str(row["name"]),
                note_date=date.fromisoformat(str(row["note_date"])),
            )
            for row in rows
        ]

    def habit_stats_for_period(self, habit_id: int, period_start: date, period_end: date) -> HabitStats:
        habit = self._get_habit(habit_id)
        scoped_habit = Habit(
            habit_id=habit.habit_id,
            name=habit.name,
            start_date=period_start,
            archived_at=period_end,
        )
        streaks = self._streaks_for_habit(scoped_habit, period_end)
        longest_streak = streaks[0] if streaks else None

        current_streak = 0
        if streaks:
            day_status = self._status_for_habit_day(habit_id, period_end)
            current_end = period_end if day_status == STATUS_DONE else period_end - timedelta(days=1)
            latest_streak = max(streaks, key=lambda streak: streak.end_date)
            if latest_streak.end_date == current_end:
                current_streak = latest_streak.length

        return HabitStats(
            habit=scoped_habit,
            current_streak=current_streak,
            longest_streak=longest_streak,
            streaks=streaks,
            note_count=self.note_count_for_habit_in_range(habit_id, period_start, period_end),
            active_today=False,
        )

    def current_streak_for_habit(self, habit_id: int, through_day: date) -> int:
        habit = self._get_habit(habit_id)
        if not self._habit_active_on_date(habit, through_day):
            return 0

        streaks = self._streaks_for_habit(habit, through_day)
        if not streaks:
            return 0

        day_status = self._status_for_habit_day(habit.habit_id, through_day)
        current_end = through_day if day_status == STATUS_DONE else through_day - timedelta(days=1)
        latest_streak = max(streaks, key=lambda streak: streak.end_date)
        if latest_streak.end_date == current_end:
            return latest_streak.length
        return 0

    def _get_habit(self, habit_id: int) -> Habit:
        row = self.connection.execute(
            "SELECT id, name, start_date, archived_at FROM habits WHERE id = ?",
            (habit_id,),
        ).fetchone()
        if row is None:
            raise ValueError("Habit does not exist.")
        return Habit(
            habit_id=int(row["id"]),
            name=str(row["name"]),
            start_date=date.fromisoformat(str(row["start_date"])),
            archived_at=(
                date.fromisoformat(str(row["archived_at"]))
                if row["archived_at"] is not None
                else None
            ),
        )

    @staticmethod
    def _habit_active_on_date(habit: Habit, day: date) -> bool:
        return habit.start_date <= day and habit.archived_at is None

    def _status_for_habit_day(self, habit_id: int, day: date) -> str:
        row = self.connection.execute(
            "SELECT status FROM habit_logs WHERE habit_id = ? AND log_date = ?",
            (habit_id, day.isoformat()),
        ).fetchone()
        if row is None:
            return STATUS_PENDING
        return str(row["status"])

    def _streaks_for_habit(self, habit: Habit, today: date) -> list[HabitStreak]:
        end_date = today
        if habit.archived_at is not None:
            end_date = min(end_date, habit.archived_at)
        if end_date < habit.start_date:
            return []

        rows = self.connection.execute(
            """
            SELECT log_date, status
            FROM habit_logs
            WHERE habit_id = ?
                AND log_date >= ?
                AND log_date <= ?
            ORDER BY log_date
            """,
            (habit.habit_id, habit.start_date.isoformat(), end_date.isoformat()),
        ).fetchall()
        statuses = {date.fromisoformat(str(row["log_date"])): str(row["status"]) for row in rows}

        streaks: list[HabitStreak] = []
        streak_start: date | None = None
        streak_end: date | None = None
        for current_day in date_range(habit.start_date, end_date + timedelta(days=1)):
            if statuses.get(current_day) == STATUS_DONE:
                if streak_start is None:
                    streak_start = current_day
                streak_end = current_day
                continue

            if streak_start is not None and streak_end is not None:
                streaks.append(
                    HabitStreak(
                        start_date=streak_start,
                        end_date=streak_end,
                        length=(streak_end - streak_start).days + 1,
                    )
                )
                streak_start = None
                streak_end = None

        if streak_start is not None and streak_end is not None:
            streaks.append(
                HabitStreak(
                    start_date=streak_start,
                    end_date=streak_end,
                    length=(streak_end - streak_start).days + 1,
                )
            )

        return sorted(
            streaks,
            key=lambda streak: (-streak.length, streak.start_date, streak.end_date),
        )

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

    def past_pending_notifications(self, today: date | None = None) -> list[PendingNotification]:
        current_day = today if today is not None else date.today()
        habits = self.list_active_habits()
        if not habits:
            return []

        earliest_start = min(habit.start_date for habit in habits)
        if earliest_start >= current_day:
            return []

        logged_rows = self.connection.execute(
            """
            SELECT habit_id, log_date
            FROM habit_logs
            WHERE log_date >= ?
                AND log_date < ?
            """,
            (earliest_start.isoformat(), current_day.isoformat()),
        ).fetchall()
        logged_dates = {
            (int(row["habit_id"]), date.fromisoformat(str(row["log_date"])))
            for row in logged_rows
        }

        pending_by_date: dict[date, int] = {}
        yesterday = current_day - timedelta(days=1)
        for habit in habits:
            if habit.start_date > yesterday:
                continue
            end_date = yesterday
            if end_date < habit.start_date:
                continue
            for active_day in date_range(habit.start_date, end_date + timedelta(days=1)):
                if not self.habit_active_on(habit.habit_id, active_day):
                    continue
                if (habit.habit_id, active_day) not in logged_dates:
                    pending_by_date[active_day] = pending_by_date.get(active_day, 0) + 1

        return [
            PendingNotification(day=day, pending_count=count)
            for day, count in sorted(pending_by_date.items())
        ]


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


def note_title_for(habit_name: str, note_date: date) -> str:
    cleaned_name = re.sub(r"[^a-z0-9]+", "-", habit_name.lower()).strip("-")
    if not cleaned_name:
        cleaned_name = "habit"
    return f"{cleaned_name}-{note_date.isoformat()}"


def weekday_cell_label(weekday_name: str) -> str:
    return f"{weekday_name:>2}"


def plain_weekday_header() -> str:
    return " ".join(weekday_cell_label(weekday_name) for weekday_name in WEEKDAY_NAMES)


@dataclass
class CalendarApp:
    selection: CalendarSelection
    store: HabitStore
    selected_day: int | None = None
    use_color: bool = True
    view: str = "main"
    message: str = ""
    hitboxes: list[HitBox] = field(default_factory=list)
    clicked_notification_dates: set[date] = field(default_factory=set)
    notification_scroll: int = 0
    main_habit_scroll: int = 0
    notes_scroll: int = 0
    habit_notes_scroll: int = 0
    stats_scroll: int = 0
    streaks_scroll: int = 0
    selected_stats_habit_id: int | None = None
    selected_stats_habit_name: str = ""
    stats_include_archived: bool = False
    selected_notes_habit_id: int | None = None
    selected_notes_habit_name: str = ""
    note_return_view: str = "main"
    selected_period_habit_id: int | None = None
    selected_period_habit_name: str = ""
    selected_period_number: int | None = None
    selected_period_start: date | None = None
    selected_period_end: date | None = None
    challenge_habit_id: int | None = None
    challenge_habit_name: str = ""
    challenge_new_habit_name: str = ""
    note_habit_id: int | None = None
    note_habit_name: str = ""
    note_date: date | None = None
    note_lines: list[str] = field(default_factory=lambda: [""])
    note_saved_body: str = ""
    note_cursor_y: int = 0
    note_cursor_x: int = 0
    note_editing: bool = False
    note_command: str | None = None
    note_scroll: int = 0

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
        self.main_habit_scroll = 0
        if self.view == "challenge_date_picker":
            self.message = "Pick the challenge ending date."
        else:
            self.message = ""

    def select_day(self, day: int) -> None:
        self.selected_day = day
        self.main_habit_scroll = 0
        selected = self.selected_date
        if selected is not None and self.view == "challenge_date_picker":
            self.apply_challenge_end_date(selected)
            return
        self.message = ""

    def open_manage_habits(self) -> None:
        self.view = "manage_habits"
        self.message = ""

    def open_delete_habits(self) -> None:
        self.view = "delete_habits"
        self.message = ""

    def open_archive_mode(self) -> None:
        self.view = "archive_mode"
        self.message = ""

    def open_archive_habits(self) -> None:
        self.view = "archive_habits"
        self.message = ""

    def open_archived_habits(self) -> None:
        self.view = "archived_habits"
        self.message = ""

    def open_archive_period_list(self, habit_id: int, habit_name: str) -> None:
        self.selected_period_habit_id = habit_id
        self.selected_period_habit_name = habit_name
        self.view = "archive_period_list"
        self.message = ""

    def open_archive_period_stats(self, period_number: int, start_date: date, end_date: date) -> None:
        self.selected_period_number = period_number
        self.selected_period_start = start_date
        self.selected_period_end = end_date
        self.view = "archive_period_stats"
        self.message = ""

    def open_archive_period_streak_history(self) -> None:
        if self.selected_period_start is None or self.selected_period_end is None:
            self.message = "No archive period selected."
            return
        self.streaks_scroll = 0
        self.view = "archive_period_streak_history"
        self.message = ""

    def open_archive_period_notes(self) -> None:
        if self.selected_period_start is None or self.selected_period_end is None:
            self.message = "No archive period selected."
            return
        self.habit_notes_scroll = 0
        self.view = "archive_period_notes"
        self.message = ""

    def open_rename_habits(self) -> None:
        self.view = "rename_habits"
        self.message = ""

    def open_challenge_mode(self) -> None:
        self.view = "challenge_mode"
        self.message = ""

    def open_create_challenge(self) -> None:
        self.clear_challenge_target()
        self.view = "create_challenge"
        self.message = ""

    def open_existing_challenge_habits(self) -> None:
        self.view = "challenge_existing_habits"
        self.message = ""

    def open_challenge_end_options(self) -> None:
        self.view = "challenge_end_options"
        self.message = ""

    def open_help(self) -> None:
        self.view = "help"
        self.message = ""

    def open_backups(self) -> None:
        self.view = "backups"
        self.message = ""

    def open_notifications(self) -> None:
        self.view = "notifications"
        self.notification_scroll = 0
        self.message = ""

    def open_notes_browser(self) -> None:
        self.view = "notes"
        self.notes_scroll = 0
        self.message = ""

    def open_stats(self) -> None:
        self.view = "stats"
        self.stats_include_archived = False
        self.stats_scroll = 0
        self.message = ""

    def set_stats_filter(self, include_archived: bool) -> None:
        self.stats_include_archived = include_archived
        self.stats_scroll = 0
        self.view = "stats"
        self.message = "Showing all habits." if include_archived else "Showing active habits."

    def open_habit_stats(self, habit_id: int, habit_name: str) -> None:
        self.selected_stats_habit_id = habit_id
        self.selected_stats_habit_name = habit_name
        self.view = "habit_stats"
        self.message = ""

    def open_streak_history(self) -> None:
        if self.selected_stats_habit_id is None:
            self.message = "No habit selected."
            return
        self.streaks_scroll = 0
        self.view = "streak_history"
        self.message = ""

    def open_habit_notes(self, habit_id: int, habit_name: str, note_count: int) -> None:
        if note_count == 0:
            self.message = f"No notes exist for {habit_name}."
            return
        self.selected_notes_habit_id = habit_id
        self.selected_notes_habit_name = habit_name
        self.habit_notes_scroll = 0
        self.view = "habit_notes"
        self.message = ""

    def scroll_notifications(self, delta: int, page_size: int) -> None:
        notifications = self.store.past_pending_notifications()
        max_scroll = max(0, len(notifications) - max(1, page_size))
        self.notification_scroll = min(max_scroll, max(0, self.notification_scroll + delta))
        self.message = ""

    def scroll_notes(self, delta: int, page_size: int) -> None:
        habits = self.store.note_counts_by_habit()
        max_scroll = max(0, len(habits) - max(1, page_size))
        self.notes_scroll = min(max_scroll, max(0, self.notes_scroll + delta))
        self.message = ""

    def scroll_habit_notes(self, delta: int, page_size: int) -> None:
        if self.selected_notes_habit_id is None:
            self.habit_notes_scroll = 0
            return
        notes = self.store.notes_for_habit(self.selected_notes_habit_id)
        max_scroll = max(0, len(notes) - max(1, page_size))
        self.habit_notes_scroll = min(max_scroll, max(0, self.habit_notes_scroll + delta))
        self.message = ""

    def scroll_main_habits(self, delta: int, page_size: int) -> None:
        selected = self.selected_date
        if selected is None:
            self.main_habit_scroll = 0
            return
        habits = self.store.habits_for_day(selected)
        max_scroll = max(0, len(habits) - max(1, page_size))
        self.main_habit_scroll = min(max_scroll, max(0, self.main_habit_scroll + delta))
        self.message = ""

    def scroll_stats(self, delta: int, page_size: int) -> None:
        stats = self.store.habit_stats_list(self.stats_include_archived)
        max_scroll = max(0, len(stats) - max(1, page_size))
        self.stats_scroll = min(max_scroll, max(0, self.stats_scroll + delta))
        self.message = ""

    def scroll_streak_history(self, delta: int, page_size: int) -> None:
        if self.selected_stats_habit_id is None:
            self.streaks_scroll = 0
            return
        stats = self.store.habit_stats(self.selected_stats_habit_id)
        max_scroll = max(0, len(stats.streaks) - max(1, page_size))
        self.streaks_scroll = min(max_scroll, max(0, self.streaks_scroll + delta))
        self.message = ""

    def open_notification_date(self, notification_day: date) -> None:
        self.clicked_notification_dates.add(notification_day)
        self.selection = CalendarSelection(notification_day.year, notification_day.month)
        self.selected_day = notification_day.day
        self.main_habit_scroll = 0
        self.view = "main"
        self.message = f"Selected {notification_day.isoformat()} from notifications."

    def open_manage_backups(self) -> None:
        self.view = "manage_backups"
        self.message = ""

    def open_note_editor(
        self,
        habit_id: int,
        habit_name: str,
        note_date: date,
        return_view: str = "main",
    ) -> None:
        body = self.store.get_note(habit_id, note_date)
        self.note_habit_id = habit_id
        self.note_habit_name = habit_name
        self.note_date = note_date
        self.note_saved_body = body
        self.note_lines = body.split("\n") if body else [""]
        self.note_cursor_y = 0
        self.note_cursor_x = 0
        self.note_editing = False
        self.note_command = None
        self.note_scroll = 0
        self.note_return_view = return_view
        self.view = "note_editor"
        self.message = "Locked. Press i to insert, : for commands."

    def note_body(self) -> str:
        return "\n".join(self.note_lines)

    def note_dirty(self) -> bool:
        return self.note_body() != self.note_saved_body

    def save_current_note(self) -> bool:
        if self.note_habit_id is None or self.note_date is None:
            self.message = "No note is open."
            return False
        body = self.note_body()
        try:
            self.store.save_note(self.note_habit_id, self.note_date, body)
        except ValueError as exc:
            self.message = str(exc)
            return False
        self.note_saved_body = body if body.strip() else ""
        if not body.strip():
            self.note_lines = [""]
            self.note_cursor_y = 0
            self.note_cursor_x = 0
        self.message = "Note saved."
        return True

    def close_note_editor(self) -> None:
        self.view = self.note_return_view
        self.note_editing = False
        self.note_command = None
        self.message = ""

    def note_title(self) -> str:
        if self.note_date is None:
            return "note"
        return note_title_for(self.note_habit_name, self.note_date)

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
        elif self.view == "challenge_date_picker":
            self.view = "challenge_end_options"
        elif self.view in {"challenge_existing_habits", "challenge_end_options"}:
            self.view = "create_challenge"
        elif self.view == "create_challenge":
            self.view = "challenge_mode"
        elif self.view in {"archive_period_streak_history", "archive_period_notes"}:
            self.view = "archive_period_stats"
        elif self.view == "archive_period_stats":
            self.view = "archive_period_list"
        elif self.view == "archive_period_list":
            self.view = "archived_habits"
        elif self.view in {"archive_habits", "archived_habits"}:
            self.view = "archive_mode"
        elif self.view in {"rename_habits", "challenge_mode", "delete_habits", "archive_mode"}:
            self.view = "manage_habits"
        elif self.view == "habit_notes":
            if self.selected_stats_habit_id is not None:
                self.view = "habit_stats"
            else:
                self.view = "notes"
        elif self.view == "streak_history":
            self.view = "habit_stats"
        elif self.view == "habit_stats":
            self.view = "stats"
        elif self.view in {"help", "manage_habits", "backups", "notifications", "notes", "stats"}:
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
        if normalized == "/managehabit":
            self.open_manage_habits()
            return True
        if normalized == "/notes":
            self.selected_stats_habit_id = None
            self.open_notes_browser()
            return True
        if normalized == "/stats":
            self.open_stats()
            return True
        if normalized == "/viewall":
            self.set_stats_filter(True)
            return True
        if normalized == "/viewactive":
            self.set_stats_filter(False)
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
        self.main_habit_scroll = 0
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

    def archive_habit(self, habit_id: int, habit_name: str) -> None:
        today = date.today()
        try:
            self.store.archive_habit(habit_id, today)
        except ValueError as exc:
            self.message = str(exc)
            return
        self.message = f"Archived {habit_name}. Use View Archive to resurrect it."

    def resurrect_habit(self, habit_id: int, habit_name: str) -> None:
        try:
            self.store.resurrect_habit(habit_id)
        except ValueError as exc:
            self.message = str(exc)
            return
        self.message = f"Resurrected {habit_name}."

    def clear_challenge_target(self) -> None:
        self.challenge_habit_id = None
        self.challenge_habit_name = ""
        self.challenge_new_habit_name = ""

    def challenge_pick_target(self) -> str | None:
        if self.challenge_new_habit_name:
            return self.challenge_new_habit_name
        if self.challenge_habit_id is not None:
            return self.challenge_habit_name
        return None

    def create_new_challenge_habit(self, screen: "curses.window") -> None:
        name = self._prompt(screen, "New challenge habit name: ")
        if name is None or not name:
            self.message = "Challenge creation cancelled."
            return
        cleaned = " ".join(name.split())
        if not cleaned:
            self.message = "Habit name cannot be empty."
            return
        self.challenge_habit_id = None
        self.challenge_habit_name = ""
        self.challenge_new_habit_name = cleaned
        self.open_challenge_end_options()

    def choose_existing_challenge_habit(self, habit_id: int, habit_name: str) -> None:
        self.challenge_habit_id = habit_id
        self.challenge_habit_name = habit_name
        self.challenge_new_habit_name = ""
        self.open_challenge_end_options()

    def set_challenge_duration(self, screen: "curses.window") -> None:
        if self.challenge_pick_target() is None:
            self.message = "Choose a challenge habit first."
            self.view = "create_challenge"
            return

        raw_duration = self._prompt(screen, "Challenge duration in days: ")
        if raw_duration is None or not raw_duration:
            self.message = "Challenge creation cancelled."
            return
        try:
            duration_days = int(raw_duration)
        except ValueError:
            self.message = "Duration must be a whole number of days."
            return
        if duration_days < 1:
            self.message = "Duration must be at least 1 day."
            return

        end_date = date.today() + timedelta(days=duration_days - 1)
        self.apply_challenge_end_date(end_date)

    def start_challenge_end_date_pick(self) -> None:
        if self.challenge_pick_target() is None:
            self.message = "Choose a challenge habit first."
            self.view = "create_challenge"
            return
        today = date.today()
        self.selection = CalendarSelection(today.year, today.month)
        self.selected_day = today.day
        self.view = "challenge_date_picker"
        self.message = "Pick the challenge ending date."

    def apply_challenge_end_date(self, end_date: date) -> None:
        target = self.challenge_pick_target()
        if target is None:
            self.message = "Choose a challenge habit first."
            self.view = "create_challenge"
            return
        if end_date < date.today():
            self.message = "Challenge end date cannot be before today."
            return

        try:
            if self.challenge_new_habit_name:
                habit_id = self.store.create_habit(self.challenge_new_habit_name, date.today())
                self.store.create_challenge(habit_id, date.today(), end_date)
            elif self.challenge_habit_id is not None:
                self.store.create_challenge(self.challenge_habit_id, date.today(), end_date)
        except ValueError as exc:
            self.message = str(exc)
            return

        duration_days = (end_date - date.today()).days + 1
        self.clear_challenge_target()
        self.view = "challenge_end_options"
        self.message = f"Challenge for {target} runs {duration_days} days through {end_date.isoformat()}."

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
            elif hitbox.name == "notifications":
                self.open_notifications()
            elif hitbox.name == "notification" and hitbox.value is not None:
                self.open_notification_date(hitbox.value)
            elif hitbox.name == "notes_habit" and hitbox.value is not None:
                habit_id, habit_name, note_count = hitbox.value
                self.open_habit_notes(int(habit_id), str(habit_name), int(note_count))
            elif hitbox.name == "habit_note" and hitbox.value is not None:
                habit_id, habit_name, note_date = hitbox.value
                self.open_note_editor(int(habit_id), str(habit_name), note_date, "habit_notes")
            elif hitbox.name == "stats_habit" and hitbox.value is not None:
                habit_id, habit_name = hitbox.value
                self.open_habit_stats(int(habit_id), str(habit_name))
            elif hitbox.name == "stats_streak_history":
                self.open_streak_history()
            elif hitbox.name == "stats_notes" and hitbox.value is not None:
                habit_id, habit_name, note_count = hitbox.value
                self.open_habit_notes(int(habit_id), str(habit_name), int(note_count))
            elif hitbox.name == "delete_backup" and hitbox.value is not None:
                self.delete_backup(screen, Path(str(hitbox.value)))
            elif hitbox.name == "restore_backup" and hitbox.value is not None:
                self.restore_backup(screen, Path(str(hitbox.value)))
            elif hitbox.name == "manage_rename":
                self.open_rename_habits()
            elif hitbox.name == "manage_challenge":
                self.open_challenge_mode()
            elif hitbox.name == "challenge_create":
                self.open_create_challenge()
            elif hitbox.name == "challenge_existing":
                self.open_existing_challenge_habits()
            elif hitbox.name == "challenge_new":
                self.create_new_challenge_habit(screen)
            elif hitbox.name == "challenge_existing_habit" and hitbox.value is not None:
                habit_id, habit_name = hitbox.value
                self.choose_existing_challenge_habit(int(habit_id), str(habit_name))
            elif hitbox.name == "challenge_duration":
                self.set_challenge_duration(screen)
            elif hitbox.name == "challenge_end_date":
                self.start_challenge_end_date_pick()
            elif hitbox.name == "manage_delete":
                self.open_delete_habits()
            elif hitbox.name == "manage_archive_mode":
                self.open_archive_mode()
            elif hitbox.name == "archive_mode_view":
                self.open_archived_habits()
            elif hitbox.name == "archive_mode_archive":
                self.open_archive_habits()
            elif hitbox.name == "delete_habit" and hitbox.value is not None:
                habit_id, habit_name = hitbox.value
                self.delete_habit(screen, int(habit_id), str(habit_name))
            elif hitbox.name == "archive_habit" and hitbox.value is not None:
                habit_id, habit_name = hitbox.value
                self.archive_habit(int(habit_id), str(habit_name))
            elif hitbox.name == "resurrect_habit" and hitbox.value is not None:
                habit_id, habit_name = hitbox.value
                self.resurrect_habit(int(habit_id), str(habit_name))
            elif hitbox.name == "archived_habit_stats" and hitbox.value is not None:
                habit_id, habit_name = hitbox.value
                self.open_archive_period_list(int(habit_id), str(habit_name))
            elif hitbox.name == "archive_period_row" and hitbox.value is not None:
                period_number, start_iso, end_iso = hitbox.value
                self.open_archive_period_stats(
                    int(period_number),
                    date.fromisoformat(str(start_iso)),
                    date.fromisoformat(str(end_iso)),
                )
            elif hitbox.name == "archive_period_streak_history_link":
                self.open_archive_period_streak_history()
            elif hitbox.name == "archive_period_notes_link":
                self.open_archive_period_notes()
            elif hitbox.name == "archive_period_note" and hitbox.value is not None:
                habit_id, habit_name, note_date = hitbox.value
                self.open_note_editor(int(habit_id), str(habit_name), note_date, "archive_period_notes")
            elif hitbox.name == "rename_habit" and hitbox.value is not None:
                habit_id, habit_name = hitbox.value
                self.rename_habit(screen, int(habit_id), str(habit_name))
            elif hitbox.name == "day" and hitbox.value is not None:
                self.select_day(int(hitbox.value))
            elif hitbox.name == "add_habit":
                self.add_habit(screen)
            elif hitbox.name == "set_status" and hitbox.value is not None:
                habit_id, status = hitbox.value
                self.set_habit_status(int(habit_id), str(status))
            elif hitbox.name == "note" and hitbox.value is not None:
                habit_id, habit_name, note_date = hitbox.value
                self.open_note_editor(int(habit_id), str(habit_name), note_date)
            return

    def render(self, screen: "curses.window") -> None:
        screen.erase()
        self.hitboxes.clear()
        height, width = screen.getmaxyx()

        if height < 17 or width < 76:
            screen.addstr(0, 0, "Make the terminal at least 76x17.")
            screen.refresh()
            return

        try:
            curses.curs_set(1 if self.view == "note_editor" and self.note_command is None else 0)
        except curses.error:
            pass

        if self.view == "help":
            self._draw_help_page(screen)
        elif self.view == "manage_habits":
            self._draw_manage_habits_page(screen)
        elif self.view == "delete_habits":
            self._draw_delete_habits_page(screen)
        elif self.view == "rename_habits":
            self._draw_rename_habits_page(screen)
        elif self.view == "archive_mode":
            self._draw_archive_mode_page(screen)
        elif self.view == "archive_habits":
            self._draw_archive_habits_page(screen)
        elif self.view == "archived_habits":
            self._draw_archived_habits_page(screen)
        elif self.view == "archive_period_list":
            self._draw_archive_period_list_page(screen)
        elif self.view == "archive_period_stats":
            self._draw_archive_period_stats_page(screen)
        elif self.view == "archive_period_streak_history":
            self._draw_archive_period_streak_history_page(screen)
        elif self.view == "archive_period_notes":
            self._draw_archive_period_notes_page(screen)
        elif self.view == "challenge_mode":
            self._draw_challenge_mode_page(screen)
        elif self.view == "create_challenge":
            self._draw_create_challenge_page(screen)
        elif self.view == "challenge_existing_habits":
            self._draw_existing_challenge_habits_page(screen)
        elif self.view == "challenge_end_options":
            self._draw_challenge_end_options_page(screen)
        elif self.view == "challenge_date_picker":
            self._draw_challenge_date_picker_page(screen, height, width)
        elif self.view == "backups":
            self._draw_backups_page(screen)
        elif self.view == "manage_backups":
            self._draw_manage_backups_page(screen)
        elif self.view == "notifications":
            self._draw_notifications_page(screen)
        elif self.view == "notes":
            self._draw_notes_page(screen)
        elif self.view == "habit_notes":
            self._draw_habit_notes_page(screen)
        elif self.view == "stats":
            self._draw_stats_page(screen)
        elif self.view == "habit_stats":
            self._draw_habit_stats_page(screen)
        elif self.view == "streak_history":
            self._draw_streak_history_page(screen)
        elif self.view == "note_editor":
            self._draw_note_editor_page(screen)
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
        pending_days = {notification.day for notification in self.store.past_pending_notifications(today)}
        for col_index, weekday_name in enumerate(WEEKDAY_NAMES):
            x = CALENDAR_LEFT + col_index * CELL_WIDTH
            self._addstr(screen, CALENDAR_TOP - 2, x, weekday_cell_label(weekday_name), curses.A_BOLD)

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
                current_date = date(self.selection.year, self.selection.month, day)
                if current_date in pending_days:
                    style |= self._color(6) | curses.A_BOLD

                done_count, missed_count = summary.get(day, (0, 0))
                marker = "!" if missed_count else "+" if done_count else " "
                label = f"{day:2}{marker}"
                self._addstr(screen, y, x, label, style)
                self.hitboxes.append(HitBox("day", y, x, x + CELL_WIDTH - 2, day))

    def _draw_day_panel(self, screen: "curses.window") -> None:
        height, _ = screen.getmaxyx()
        selected = self.selected_date
        if selected is None:
            self.main_habit_scroll = 0
            self._addstr(screen, 1, DETAIL_LEFT, "Select a day", curses.A_BOLD)
            self._addstr(screen, 3, DETAIL_LEFT, "Click a date to manage daily habits.")
            return

        self._addstr(screen, 1, DETAIL_LEFT, selected.isoformat(), self._color(1) | curses.A_BOLD)
        notifications = self.store.past_pending_notifications()
        if notifications:
            notifications_label = "Notifications"
            notifications_x = DETAIL_LEFT + 14
            self._addstr(screen, 1, notifications_x, notifications_label, self._color(6) | curses.A_BOLD)
            self.hitboxes.append(
                HitBox(
                    "notifications",
                    1,
                    notifications_x,
                    notifications_x + len(notifications_label) - 1,
                )
            )

        add_label = "+ Add daily habit"
        self._addstr(screen, 3, DETAIL_LEFT, add_label, curses.A_BOLD)
        self.hitboxes.append(HitBox("add_habit", 3, DETAIL_LEFT, DETAIL_LEFT + len(add_label) - 1))

        note_habit_ids = self.store.note_habit_ids_for_day(selected)
        habits = self.store.habits_for_day(selected)
        if not habits:
            self.main_habit_scroll = 0
            self._addstr(screen, 5, DETAIL_LEFT, "No habits active yet.")
            self._addstr(screen, 6, DETAIL_LEFT, "Add one from this date to start tracking.")
            return

        list_top = 5
        footer_y = self._main_footer_y(height)
        list_bottom = footer_y - 1
        visible_count = max(1, (list_bottom - list_top + 2) // 3)
        max_scroll = max(0, len(habits) - visible_count)
        self.main_habit_scroll = min(self.main_habit_scroll, max_scroll)
        visible_habits = habits[self.main_habit_scroll : self.main_habit_scroll + visible_count]

        for index, habit in enumerate(visible_habits):
            y = list_top + index * 3
            challenge_progress = self.store.challenge_progress_for_habit_day(habit.habit_id, selected)
            if challenge_progress is not None:
                habit_label = (
                    f"{habit.name} "
                    f"({challenge_progress.current_streak}/{challenge_progress.duration_days})"
                )
            else:
                streak = self.store.current_streak_for_habit(habit.habit_id, selected)
                habit_label = f"{habit.name} ({streak})"
            self._addstr(screen, y, DETAIL_LEFT, self._truncate(habit_label, 24), curses.A_BOLD)

            pending_label = "Pending"
            done_label = "Done"
            missed_label = "Missed"
            pending_x = DETAIL_LEFT + 2
            done_x = DETAIL_LEFT + 12
            note_label = "Note" if habit.habit_id in note_habit_ids else "+Note"
            missed_x = DETAIL_LEFT + 21
            note_x = DETAIL_LEFT + 30
            note_style = self._color(7) if habit.habit_id in note_habit_ids else curses.A_DIM
            self._addstr(screen, y + 1, pending_x, pending_label, self._action_style(habit.status, STATUS_PENDING))
            self._addstr(screen, y + 1, done_x, done_label, self._action_style(habit.status, STATUS_DONE))
            self._addstr(screen, y + 1, missed_x, missed_label, self._action_style(habit.status, STATUS_MISSED))
            self._addstr(screen, y + 1, note_x, note_label, note_style)
            self.hitboxes.append(HitBox("set_status", y + 1, pending_x, pending_x + len(pending_label) - 1, (habit.habit_id, STATUS_PENDING)))
            self.hitboxes.append(HitBox("set_status", y + 1, done_x, done_x + len(done_label) - 1, (habit.habit_id, STATUS_DONE)))
            self.hitboxes.append(HitBox("set_status", y + 1, missed_x, missed_x + len(missed_label) - 1, (habit.habit_id, STATUS_MISSED)))
            self.hitboxes.append(HitBox("note", y + 1, note_x, note_x + len(note_label) - 1, (habit.habit_id, habit.name, selected)))

        if len(habits) > visible_count:
            first = self.main_habit_scroll + 1
            last = self.main_habit_scroll + len(visible_habits)
            self._addstr(screen, 3, DETAIL_LEFT + 20, f"{first}-{last}/{len(habits)} Up/Down", curses.A_DIM)

    def _draw_help_page(self, screen: "curses.window") -> None:
        back_label = "< Back"
        self._addstr(screen, 1, CALENDAR_LEFT, back_label, curses.A_BOLD)
        self._addstr(screen, 1, DETAIL_LEFT, "Commands", self._color(1) | curses.A_BOLD)
        self.hitboxes.append(HitBox("back", 1, CALENDAR_LEFT, CALENDAR_LEFT + len(back_label) - 1))

        for index, (command, description) in enumerate(COMMANDS):
            y = 3 + index
            self._addstr(screen, y, CALENDAR_LEFT, command, curses.A_BOLD)
            self._addstr(screen, y, CALENDAR_LEFT + 14, description)
        footer_y = 3 + len(COMMANDS) + 1
        self._addstr(screen, footer_y, CALENDAR_LEFT, "Press / to enter a command. Press b to go back.")
        self._draw_message(screen, footer_y + 1)

    def handle_note_key(self, key: int) -> None:
        if self.view != "note_editor":
            return

        if self.note_command is not None:
            self._handle_note_command_key(key)
            return

        if self.note_editing:
            self._handle_note_edit_key(key)
            return

        if key == 27:
            self.message = "Use :q to close or :wq to save and close."
        elif key == ord("^"):
            self._move_note_cursor_first_nonblank()
        elif key == ord("$"):
            self.note_cursor_x = len(self.note_lines[self.note_cursor_y])
        elif key in (ord("w"), ord("W")):
            self._move_note_cursor_next_word()
        elif key in (ord("i"), ord("I")):
            self.note_editing = True
            self.message = "Insert mode. Esc locks the note."
        elif key == ord(":"):
            self.note_command = ""
            self.message = ""

    def _handle_note_command_key(self, key: int) -> None:
        if key == 27:
            self.note_command = None
            self.message = "Command cancelled."
            return
        if key in (curses.KEY_ENTER, 10, 13):
            command = (self.note_command or "").strip().lower()
            self.note_command = None
            if command == "w":
                self.save_current_note()
            elif command == "q":
                self.close_note_editor()
            elif command == "wq":
                if self.save_current_note():
                    self.close_note_editor()
            else:
                self.message = f"Unknown note command: :{command}"
            return
        if key in (curses.KEY_BACKSPACE, 8, 127):
            if self.note_command:
                self.note_command = self.note_command[:-1]
            return
        if 32 <= key <= 126 and len(self.note_command or "") < 20:
            self.note_command = (self.note_command or "") + chr(key)

    def _handle_note_edit_key(self, key: int) -> None:
        if key == 27:
            self.note_editing = False
            self.message = "Locked. Press : for commands."
            return
        if key in (curses.KEY_LEFT,):
            self._move_note_cursor_left()
            return
        if key in (curses.KEY_RIGHT,):
            self._move_note_cursor_right()
            return
        if key in (curses.KEY_UP,):
            self.note_cursor_y = max(0, self.note_cursor_y - 1)
            self.note_cursor_x = min(self.note_cursor_x, len(self.note_lines[self.note_cursor_y]))
            return
        if key in (curses.KEY_DOWN,):
            self.note_cursor_y = min(len(self.note_lines) - 1, self.note_cursor_y + 1)
            self.note_cursor_x = min(self.note_cursor_x, len(self.note_lines[self.note_cursor_y]))
            return
        if key in (curses.KEY_HOME,):
            self.note_cursor_x = 0
            return
        if key in (curses.KEY_END,):
            self.note_cursor_x = len(self.note_lines[self.note_cursor_y])
            return
        if key in (curses.KEY_ENTER, 10, 13):
            line = self.note_lines[self.note_cursor_y]
            before = line[: self.note_cursor_x]
            after = line[self.note_cursor_x :]
            self.note_lines[self.note_cursor_y] = before
            self.note_lines.insert(self.note_cursor_y + 1, after)
            self.note_cursor_y += 1
            self.note_cursor_x = 0
            return
        if key in (curses.KEY_BACKSPACE, 8, 127):
            self._delete_note_character_before_cursor()
            return
        if key == curses.KEY_DC:
            self._delete_note_character_at_cursor()
            return
        if 32 <= key <= 126:
            line = self.note_lines[self.note_cursor_y]
            self.note_lines[self.note_cursor_y] = line[: self.note_cursor_x] + chr(key) + line[self.note_cursor_x :]
            self.note_cursor_x += 1

    def _move_note_cursor_left(self) -> None:
        if self.note_cursor_x > 0:
            self.note_cursor_x -= 1
        elif self.note_cursor_y > 0:
            self.note_cursor_y -= 1
            self.note_cursor_x = len(self.note_lines[self.note_cursor_y])

    def _move_note_cursor_right(self) -> None:
        line_length = len(self.note_lines[self.note_cursor_y])
        if self.note_cursor_x < line_length:
            self.note_cursor_x += 1
        elif self.note_cursor_y < len(self.note_lines) - 1:
            self.note_cursor_y += 1
            self.note_cursor_x = 0

    def _move_note_cursor_first_nonblank(self) -> None:
        line = self.note_lines[self.note_cursor_y]
        self.note_cursor_x = len(line) - len(line.lstrip()) if line.strip() else 0

    def _move_note_cursor_next_word(self) -> None:
        y = self.note_cursor_y
        x = self.note_cursor_x
        skip_current_word = x < len(self.note_lines[y]) and not self.note_lines[y][x].isspace()

        while y < len(self.note_lines):
            line = self.note_lines[y]
            if skip_current_word:
                while x < len(line) and not line[x].isspace():
                    x += 1
                skip_current_word = False
            while x < len(line) and line[x].isspace():
                x += 1
            if x < len(line):
                self.note_cursor_y = y
                self.note_cursor_x = x
                return
            y += 1
            x = 0

        self.note_cursor_y = len(self.note_lines) - 1
        self.note_cursor_x = len(self.note_lines[self.note_cursor_y])

    def _delete_note_character_before_cursor(self) -> None:
        if self.note_cursor_x > 0:
            line = self.note_lines[self.note_cursor_y]
            self.note_lines[self.note_cursor_y] = line[: self.note_cursor_x - 1] + line[self.note_cursor_x :]
            self.note_cursor_x -= 1
        elif self.note_cursor_y > 0:
            current_line = self.note_lines.pop(self.note_cursor_y)
            self.note_cursor_y -= 1
            self.note_cursor_x = len(self.note_lines[self.note_cursor_y])
            self.note_lines[self.note_cursor_y] += current_line

    def _delete_note_character_at_cursor(self) -> None:
        line = self.note_lines[self.note_cursor_y]
        if self.note_cursor_x < len(line):
            self.note_lines[self.note_cursor_y] = line[: self.note_cursor_x] + line[self.note_cursor_x + 1 :]
        elif self.note_cursor_y < len(self.note_lines) - 1:
            next_line = self.note_lines.pop(self.note_cursor_y + 1)
            self.note_lines[self.note_cursor_y] += next_line

    def _draw_note_editor_page(self, screen: "curses.window") -> None:
        height, width = screen.getmaxyx()
        title = self.note_title()
        mode = "INSERT" if self.note_editing else "LOCKED"
        save_state = "modified" if self.note_dirty() else "saved"
        self._addstr(screen, 1, CALENDAR_LEFT, self._truncate(title, max(10, width - 30)), self._color(1) | curses.A_BOLD)
        self._addstr(screen, 1, max(CALENDAR_LEFT, width - 22), f"{mode} {save_state}", curses.A_BOLD)

        body_top = 3
        command_y = max(body_top + 1, height - 3)
        footer_y = max(body_top + 2, height - 2)
        message_y = max(body_top + 3, height - 1)
        visible_count = max(1, command_y - body_top)
        text_width = max(1, width - CALENDAR_LEFT - 5)
        display_lines = self._note_display_lines(text_width)
        self._ensure_note_cursor_visible(display_lines, visible_count)

        visible_lines = display_lines[self.note_scroll : self.note_scroll + visible_count]
        for offset, display_line in enumerate(visible_lines):
            prefix = f"{display_line.line_index + 1:>3} " if display_line.show_line_number else "    "
            self._addstr(screen, body_top + offset, CALENDAR_LEFT, prefix, curses.A_DIM)
            self._addstr(screen, body_top + offset, CALENDAR_LEFT + 4, display_line.text)

        if self.note_command is not None:
            self._addstr(screen, command_y, CALENDAR_LEFT, ":" + self.note_command, curses.A_BOLD)
        else:
            self._addstr(screen, command_y, CALENDAR_LEFT, "i insert | Esc lock | :w save | :q quit | :wq save quit")
        self._addstr(screen, footer_y, CALENDAR_LEFT, "Locked: ^ start, $ end, w next word. Insert: arrows move, Enter inserts line breaks.", curses.A_DIM)
        self._draw_message(screen, message_y)

        if self.note_command is None:
            cursor_display_y, cursor_display_x = self._note_cursor_display_position(display_lines)
            cursor_y = body_top + cursor_display_y - self.note_scroll
            cursor_x = CALENDAR_LEFT + 4 + cursor_display_x
            if body_top <= cursor_y < command_y and cursor_x < width:
                try:
                    screen.move(cursor_y, cursor_x)
                except curses.error:
                    pass

    def _note_display_lines(self, text_width: int) -> list[NoteDisplayLine]:
        display_lines: list[NoteDisplayLine] = []
        width = max(1, text_width)
        for line_index, line in enumerate(self.note_lines):
            if not line:
                display_lines.append(NoteDisplayLine(line_index, 0, 0, "", True))
                continue

            start = 0
            show_line_number = True
            while start < len(line):
                end = min(len(line), start + width)
                next_start = end
                if end < len(line):
                    break_at = line.rfind(" ", start + 1, end + 1)
                    if break_at > start:
                        end = break_at
                        next_start = break_at + 1
                display_lines.append(
                    NoteDisplayLine(line_index, start, end, line[start:end], show_line_number)
                )
                start = max(next_start, start + 1)
                show_line_number = False
        return display_lines or [NoteDisplayLine(0, 0, 0, "", True)]

    def _note_cursor_display_position(self, display_lines: list[NoteDisplayLine]) -> tuple[int, int]:
        fallback_y = 0
        fallback_x = 0
        for display_y, display_line in enumerate(display_lines):
            if display_line.line_index != self.note_cursor_y:
                continue
            fallback_y = display_y
            fallback_x = min(self.note_cursor_x, display_line.end) - display_line.start
            if display_line.start <= self.note_cursor_x <= display_line.end:
                return display_y, max(0, fallback_x)
        return fallback_y, max(0, fallback_x)

    def _ensure_note_cursor_visible(
        self, display_lines: list[NoteDisplayLine], visible_count: int
    ) -> None:
        cursor_display_y, _ = self._note_cursor_display_position(display_lines)
        if cursor_display_y < self.note_scroll:
            self.note_scroll = cursor_display_y
        elif cursor_display_y >= self.note_scroll + visible_count:
            self.note_scroll = cursor_display_y - visible_count + 1
        self.note_scroll = max(0, min(self.note_scroll, max(0, len(display_lines) - visible_count)))

    def _draw_stats_page(self, screen: "curses.window") -> None:
        height, _ = screen.getmaxyx()
        status_y = max(4, height - 3)
        footer_y = max(4, height - 2)
        message_y = max(4, height - 1)
        visible_count = max(1, status_y - 5)

        back_label = "< Back"
        title = "Stats" if not self.stats_include_archived else "Stats: All Habits"
        self._addstr(screen, 1, CALENDAR_LEFT, back_label, curses.A_BOLD)
        self._addstr(screen, 1, DETAIL_LEFT, title, self._color(1) | curses.A_BOLD)
        self.hitboxes.append(HitBox("back", 1, CALENDAR_LEFT, CALENDAR_LEFT + len(back_label) - 1))

        stats = self.store.habit_stats_list(self.stats_include_archived)
        if not stats:
            self.stats_scroll = 0
            empty = "No habits yet." if self.stats_include_archived else "No active habits."
            self._addstr(screen, 4, CALENDAR_LEFT, empty)
            self._draw_command_footer(screen, footer_y)
            self._draw_message(screen, message_y)
            return

        self._addstr(screen, 3, CALENDAR_LEFT, "Habit", curses.A_BOLD)
        self._addstr(screen, 3, DETAIL_LEFT + 20, "Current", curses.A_BOLD)

        max_scroll = max(0, len(stats) - visible_count)
        self.stats_scroll = min(self.stats_scroll, max_scroll)
        visible_stats = stats[self.stats_scroll : self.stats_scroll + visible_count]
        for index, item in enumerate(visible_stats):
            y = 4 + index
            label = self._truncate(item.habit.name, 32)
            current_label = f"({item.current_streak})"
            style = curses.A_BOLD if item.active_today else curses.A_NORMAL
            self._addstr(screen, y, CALENDAR_LEFT, label, style)
            self._addstr(screen, y, DETAIL_LEFT + 20, current_label, style)
            self.hitboxes.append(
                HitBox(
                    "stats_habit",
                    y,
                    CALENDAR_LEFT,
                    DETAIL_LEFT + 20 + len(current_label) - 1,
                    (item.habit.habit_id, item.habit.name),
                )
            )

        if len(stats) > visible_count:
            first = self.stats_scroll + 1
            last = self.stats_scroll + len(visible_stats)
            self._addstr(screen, status_y, CALENDAR_LEFT, f"Showing {first}-{last} of {len(stats)}. Up/Down scroll.")
        self._draw_command_footer(screen, footer_y)
        self._draw_message(screen, message_y)

    def _draw_habit_stats_page(self, screen: "curses.window") -> None:
        height, _ = screen.getmaxyx()
        footer_y = max(4, height - 2)
        message_y = max(4, height - 1)

        back_label = "< Back"
        self._addstr(screen, 1, CALENDAR_LEFT, back_label, curses.A_BOLD)
        self._addstr(screen, 1, DETAIL_LEFT, "Habit Stats", self._color(1) | curses.A_BOLD)
        self.hitboxes.append(HitBox("back", 1, CALENDAR_LEFT, CALENDAR_LEFT + len(back_label) - 1))

        if self.selected_stats_habit_id is None:
            self._addstr(screen, 4, CALENDAR_LEFT, "No habit selected.")
            self._draw_command_footer(screen, footer_y)
            self._draw_message(screen, message_y)
            return

        try:
            stats = self.store.habit_stats(self.selected_stats_habit_id)
        except ValueError as exc:
            self._addstr(screen, 4, CALENDAR_LEFT, str(exc))
            self._draw_command_footer(screen, footer_y)
            self._draw_message(screen, message_y)
            return

        habit = stats.habit
        self.selected_stats_habit_name = habit.name
        self._addstr(screen, 3, CALENDAR_LEFT, self._truncate(habit.name, 50), curses.A_BOLD if stats.active_today else curses.A_NORMAL)
        self._addstr(screen, 5, CALENDAR_LEFT, f"Current streak: {stats.current_streak} {self._day_word(stats.current_streak)}")

        if stats.longest_streak is None:
            longest_label = "Longest streak: 0 days"
        else:
            longest_label = (
                f"Longest streak: {stats.longest_streak.length} {self._day_word(stats.longest_streak.length)} "
                f"({stats.longest_streak.start_date.isoformat()} to {stats.longest_streak.end_date.isoformat()})"
            )
        self._addstr(screen, 6, CALENDAR_LEFT, self._truncate(longest_label, 70))

        history_label = f"Streak History: {len(stats.streaks)}"
        self._addstr(screen, 8, CALENDAR_LEFT, history_label, curses.A_BOLD)
        self.hitboxes.append(HitBox("stats_streak_history", 8, CALENDAR_LEFT, CALENDAR_LEFT + len(history_label) - 1))

        self._addstr(screen, 10, CALENDAR_LEFT, f"Start date: {habit.start_date.isoformat()}")
        if habit.archived_at is not None:
            self._addstr(screen, 11, CALENDAR_LEFT, f"Archive date: {habit.archived_at.isoformat()}")

        notes_y = 13 if habit.archived_at is not None else 12
        notes_label = f"Notes: {stats.note_count}"
        notes_style = curses.A_BOLD if stats.note_count else curses.A_DIM
        self._addstr(screen, notes_y, CALENDAR_LEFT, notes_label, notes_style)
        self.hitboxes.append(
            HitBox(
                "stats_notes",
                notes_y,
                CALENDAR_LEFT,
                CALENDAR_LEFT + len(notes_label) - 1,
                (habit.habit_id, habit.name, stats.note_count),
            )
        )

        self._draw_command_footer(screen, footer_y)
        self._draw_message(screen, message_y)

    def _draw_streak_history_page(self, screen: "curses.window") -> None:
        height, _ = screen.getmaxyx()
        status_y = max(4, height - 3)
        footer_y = max(4, height - 2)
        message_y = max(4, height - 1)
        visible_count = max(1, status_y - 5)

        back_label = "< Back"
        self._addstr(screen, 1, CALENDAR_LEFT, back_label, curses.A_BOLD)
        self._addstr(screen, 1, DETAIL_LEFT, "Streak History", self._color(1) | curses.A_BOLD)
        self.hitboxes.append(HitBox("back", 1, CALENDAR_LEFT, CALENDAR_LEFT + len(back_label) - 1))

        if self.selected_stats_habit_id is None:
            self.streaks_scroll = 0
            self._addstr(screen, 4, CALENDAR_LEFT, "No habit selected.")
            self._draw_command_footer(screen, footer_y)
            self._draw_message(screen, message_y)
            return

        stats = self.store.habit_stats(self.selected_stats_habit_id)
        self._addstr(screen, 3, CALENDAR_LEFT, self._truncate(stats.habit.name, 42), curses.A_BOLD if stats.active_today else curses.A_NORMAL)
        self._addstr(screen, 4, CALENDAR_LEFT, "Length", curses.A_BOLD)
        self._addstr(screen, 4, DETAIL_LEFT, "Dates", curses.A_BOLD)

        if not stats.streaks:
            self.streaks_scroll = 0
            self._addstr(screen, 6, CALENDAR_LEFT, "No done streaks yet.")
            self._draw_command_footer(screen, footer_y)
            self._draw_message(screen, message_y)
            return

        max_scroll = max(0, len(stats.streaks) - visible_count)
        self.streaks_scroll = min(self.streaks_scroll, max_scroll)
        visible_streaks = stats.streaks[self.streaks_scroll : self.streaks_scroll + visible_count]
        for index, streak in enumerate(visible_streaks):
            y = 6 + index
            length_label = f"{streak.length} {self._day_word(streak.length)}"
            date_label = f"{streak.start_date.isoformat()} to {streak.end_date.isoformat()}"
            self._addstr(screen, y, CALENDAR_LEFT, length_label)
            self._addstr(screen, y, DETAIL_LEFT, date_label)

        if len(stats.streaks) > visible_count:
            first = self.streaks_scroll + 1
            last = self.streaks_scroll + len(visible_streaks)
            self._addstr(screen, status_y, CALENDAR_LEFT, f"Showing {first}-{last} of {len(stats.streaks)}. Up/Down scroll.")
        self._draw_command_footer(screen, footer_y)
        self._draw_message(screen, message_y)

    def _draw_notes_page(self, screen: "curses.window") -> None:
        height, _ = screen.getmaxyx()
        status_y = max(4, height - 2)
        message_y = max(4, height - 1)
        visible_count = max(1, status_y - 4)

        back_label = "< Back"
        self._addstr(screen, 1, CALENDAR_LEFT, back_label, curses.A_BOLD)
        self._addstr(screen, 1, DETAIL_LEFT, "Notes", self._color(1) | curses.A_BOLD)
        self.hitboxes.append(HitBox("back", 1, CALENDAR_LEFT, CALENDAR_LEFT + len(back_label) - 1))

        habits = self.store.note_counts_by_habit()
        if not habits:
            self.notes_scroll = 0
            self._addstr(screen, 4, CALENDAR_LEFT, "No habits yet.")
            self._draw_message(screen, message_y)
            return

        max_scroll = max(0, len(habits) - visible_count)
        self.notes_scroll = min(self.notes_scroll, max_scroll)
        visible_habits = habits[self.notes_scroll : self.notes_scroll + visible_count]

        self._addstr(screen, 3, CALENDAR_LEFT, "Habit Notes", curses.A_BOLD)
        for index, habit in enumerate(visible_habits):
            y = 5 + index
            label = f"{habit.name} ({habit.note_count})"
            style = curses.A_BOLD if habit.note_count else curses.A_DIM
            self._addstr(screen, y, CALENDAR_LEFT, self._truncate(label, 70), style)
            self.hitboxes.append(
                HitBox(
                    "notes_habit",
                    y,
                    CALENDAR_LEFT,
                    CALENDAR_LEFT + min(len(label), 70) - 1,
                    (habit.habit_id, habit.name, habit.note_count),
                )
            )

        if len(habits) > visible_count:
            first = self.notes_scroll + 1
            last = self.notes_scroll + len(visible_habits)
            self._addstr(screen, status_y, CALENDAR_LEFT, f"Showing {first}-{last} of {len(habits)}. Up/Down scroll.")
        self._draw_message(screen, message_y)

    def _draw_habit_notes_page(self, screen: "curses.window") -> None:
        height, _ = screen.getmaxyx()
        status_y = max(4, height - 2)
        message_y = max(4, height - 1)
        visible_count = max(1, status_y - 4)

        back_label = "< Back"
        self._addstr(screen, 1, CALENDAR_LEFT, back_label, curses.A_BOLD)
        self._addstr(screen, 1, DETAIL_LEFT, "Habit Notes", self._color(1) | curses.A_BOLD)
        self.hitboxes.append(HitBox("back", 1, CALENDAR_LEFT, CALENDAR_LEFT + len(back_label) - 1))
        self._addstr(screen, 3, CALENDAR_LEFT, self._truncate(self.selected_notes_habit_name, 42), curses.A_BOLD)

        if self.selected_notes_habit_id is None:
            self.habit_notes_scroll = 0
            self._addstr(screen, 5, CALENDAR_LEFT, "No habit selected.")
            self._draw_message(screen, message_y)
            return

        notes = self.store.notes_for_habit(self.selected_notes_habit_id)
        if not notes:
            self.habit_notes_scroll = 0
            self._addstr(screen, 5, CALENDAR_LEFT, "No notes exist for this habit.")
            self._draw_message(screen, message_y)
            return

        max_scroll = max(0, len(notes) - visible_count)
        self.habit_notes_scroll = min(self.habit_notes_scroll, max_scroll)
        visible_notes = notes[self.habit_notes_scroll : self.habit_notes_scroll + visible_count]

        for index, note in enumerate(visible_notes):
            y = 5 + index
            label = note_title_for(note.habit_name, note.note_date)
            self._addstr(screen, y, CALENDAR_LEFT, self._truncate(label, 70), curses.A_BOLD)
            self.hitboxes.append(
                HitBox(
                    "habit_note",
                    y,
                    CALENDAR_LEFT,
                    CALENDAR_LEFT + min(len(label), 70) - 1,
                    (note.habit_id, note.habit_name, note.note_date),
                )
            )

        if len(notes) > visible_count:
            first = self.habit_notes_scroll + 1
            last = self.habit_notes_scroll + len(visible_notes)
            self._addstr(screen, status_y, CALENDAR_LEFT, f"Showing {first}-{last} of {len(notes)}. Up/Down scroll.")
        self._draw_message(screen, message_y)

    def _draw_notifications_page(self, screen: "curses.window") -> None:
        height, _ = screen.getmaxyx()
        status_y = max(4, height - 2)
        message_y = max(4, height - 1)
        visible_count = max(1, status_y - 3)

        back_label = "< Back"
        self._addstr(screen, 1, CALENDAR_LEFT, back_label, curses.A_BOLD)
        self._addstr(screen, 1, DETAIL_LEFT, "Notifications", self._color(1) | curses.A_BOLD)
        self.hitboxes.append(HitBox("back", 1, CALENDAR_LEFT, CALENDAR_LEFT + len(back_label) - 1))

        notifications = self.store.past_pending_notifications()
        if not notifications:
            self.notification_scroll = 0
            self._addstr(screen, 4, CALENDAR_LEFT, "No notifications.")
            self._draw_message(screen, message_y)
            return

        max_scroll = max(0, len(notifications) - visible_count)
        self.notification_scroll = min(self.notification_scroll, max_scroll)
        visible_notifications = notifications[self.notification_scroll : self.notification_scroll + visible_count]

        for index, notification in enumerate(visible_notifications):
            y = 3 + index
            task_word = "task" if notification.pending_count == 1 else "tasks"
            label = f"- {notification.day.isoformat()} has {notification.pending_count} pending {task_word}"
            style = curses.A_NORMAL
            if notification.day not in self.clicked_notification_dates:
                style |= curses.A_BOLD
            self._addstr(screen, y, CALENDAR_LEFT, self._truncate(label, 70), style)
            self.hitboxes.append(
                HitBox("notification", y, CALENDAR_LEFT, CALENDAR_LEFT + min(len(label), 70) - 1, notification.day)
            )

        if len(notifications) > visible_count:
            first = self.notification_scroll + 1
            last = self.notification_scroll + len(visible_notifications)
            self._addstr(screen, status_y, CALENDAR_LEFT, f"Showing {first}-{last} of {len(notifications)}. Up/Down scroll.")
        self._draw_message(screen, message_y)

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
            self._addstr(screen, y, delete_x, delete_label, self._danger_style())
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

        rename_label = "Rename"
        challenge_label = "Challenge Mode"
        archive_label = "Archive"
        delete_label = "Delete [DANGER]"
        self._addstr(screen, 4, CALENDAR_LEFT, rename_label, curses.A_BOLD)
        self._addstr(screen, 6, CALENDAR_LEFT, challenge_label, curses.A_BOLD)
        self._addstr(screen, 8, CALENDAR_LEFT, archive_label, curses.A_BOLD)
        self._addstr(screen, 10, CALENDAR_LEFT, delete_label, self._danger_style())
        self.hitboxes.append(HitBox("manage_rename", 4, CALENDAR_LEFT, CALENDAR_LEFT + len(rename_label) - 1))
        self.hitboxes.append(HitBox("manage_challenge", 6, CALENDAR_LEFT, CALENDAR_LEFT + len(challenge_label) - 1))
        self.hitboxes.append(HitBox("manage_archive_mode", 8, CALENDAR_LEFT, CALENDAR_LEFT + len(archive_label) - 1))
        self.hitboxes.append(HitBox("manage_delete", 10, CALENDAR_LEFT, CALENDAR_LEFT + len(delete_label) - 1))
        self._draw_message(screen, 16)

    def _draw_archive_mode_page(self, screen: "curses.window") -> None:
        back_label = "< Back"
        self._addstr(screen, 1, CALENDAR_LEFT, back_label, curses.A_BOLD)
        self._addstr(screen, 1, DETAIL_LEFT, "Archive", self._color(1) | curses.A_BOLD)
        self.hitboxes.append(HitBox("back", 1, CALENDAR_LEFT, CALENDAR_LEFT + len(back_label) - 1))

        view_label = "View Archive"
        archive_label = "Archive Habit"
        self._addstr(screen, 4, CALENDAR_LEFT, view_label, curses.A_BOLD)
        self._addstr(screen, 6, CALENDAR_LEFT, archive_label, curses.A_BOLD)
        self.hitboxes.append(HitBox("archive_mode_view", 4, CALENDAR_LEFT, CALENDAR_LEFT + len(view_label) - 1))
        self.hitboxes.append(HitBox("archive_mode_archive", 6, CALENDAR_LEFT, CALENDAR_LEFT + len(archive_label) - 1))
        self._draw_message(screen, 16)

    def _draw_delete_habits_page(self, screen: "curses.window") -> None:
        back_label = "< Back"
        self._addstr(screen, 1, CALENDAR_LEFT, back_label, curses.A_BOLD)
        self._addstr(screen, 1, DETAIL_LEFT, "Delete Habits", self._color(1) | curses.A_BOLD)
        self.hitboxes.append(HitBox("back", 1, CALENDAR_LEFT, CALENDAR_LEFT + len(back_label) - 1))

        habits = self.store.list_habits()
        if not habits:
            self._addstr(screen, 4, CALENDAR_LEFT, "No habits to delete.")
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
            self._addstr(screen, y, delete_x, delete_label, self._danger_style())
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

    def _draw_archive_habits_page(self, screen: "curses.window") -> None:
        back_label = "< Back"
        self._addstr(screen, 1, CALENDAR_LEFT, back_label, curses.A_BOLD)
        self._addstr(screen, 1, DETAIL_LEFT, "Archive Habit", self._color(1) | curses.A_BOLD)
        self.hitboxes.append(HitBox("back", 1, CALENDAR_LEFT, CALENDAR_LEFT + len(back_label) - 1))

        habits = self.store.list_active_habits()
        if not habits:
            self._addstr(screen, 4, CALENDAR_LEFT, "No active habits to archive.")
            self._draw_message(screen)
            return

        self._addstr(screen, 3, CALENDAR_LEFT, "Archive Habit", curses.A_BOLD)
        self._addstr(screen, 4, CALENDAR_LEFT, "Note: Archived habits will not appear in your daily tasks anymore.")

        for index, habit in enumerate(habits[:9]):
            y = 6 + index
            habit_label = f"{self._truncate(habit.name, 28)} ({habit.start_date.isoformat()})"
            archive_label = "Archive"
            archive_x = DETAIL_LEFT + 20
            self._addstr(screen, y, CALENDAR_LEFT, habit_label)
            self._addstr(screen, y, archive_x, archive_label, curses.A_BOLD)
            self.hitboxes.append(
                HitBox("archive_habit", y, archive_x, archive_x + len(archive_label) - 1, (habit.habit_id, habit.name))
            )

        if len(habits) > 9:
            self._addstr(screen, 15, CALENDAR_LEFT, f"Showing 9 of {len(habits)} active habits.")
        self._draw_message(screen, 16)

    def _draw_archived_habits_page(self, screen: "curses.window") -> None:
        back_label = "< Back"
        self._addstr(screen, 1, CALENDAR_LEFT, back_label, curses.A_BOLD)
        self._addstr(screen, 1, DETAIL_LEFT, "Archived Habits", self._color(1) | curses.A_BOLD)
        self.hitboxes.append(HitBox("back", 1, CALENDAR_LEFT, CALENDAR_LEFT + len(back_label) - 1))

        habits = self.store.list_archived_habits()
        if not habits:
            self._addstr(screen, 4, CALENDAR_LEFT, "No archived habits.")
            self._draw_message(screen)
            return

        self._addstr(screen, 3, CALENDAR_LEFT, "Archived Habit", curses.A_BOLD)
        self._addstr(screen, 4, CALENDAR_LEFT, "Choose Resurrect to return a habit to active tracking.")

        for index, habit in enumerate(habits[:9]):
            y = 6 + index
            periods = self.store.active_periods_for_habit(habit.habit_id)
            if periods:
                latest = periods[-1]
                date_range = f"{latest.start_date.isoformat()}-{latest.end_date.isoformat()}"
            else:
                archived_at = habit.archived_at.isoformat() if habit.archived_at is not None else "unknown"
                date_range = archived_at
            habit_label = f"{self._truncate(habit.name, 24)} ({date_range})"
            resurrect_label = "Resurrect"
            stats_label = "Stats"
            resurrect_x = DETAIL_LEFT + 20
            stats_x = resurrect_x + len(resurrect_label) + 3
            self._addstr(screen, y, CALENDAR_LEFT, habit_label)
            self._addstr(screen, y, resurrect_x, resurrect_label, curses.A_BOLD)
            self._addstr(screen, y, stats_x, stats_label, curses.A_BOLD)
            self.hitboxes.append(
                HitBox(
                    "resurrect_habit",
                    y,
                    resurrect_x,
                    resurrect_x + len(resurrect_label) - 1,
                    (habit.habit_id, habit.name),
                )
            )
            self.hitboxes.append(
                HitBox(
                    "archived_habit_stats",
                    y,
                    stats_x,
                    stats_x + len(stats_label) - 1,
                    (habit.habit_id, habit.name),
                )
            )

        if len(habits) > 9:
            self._addstr(screen, 15, CALENDAR_LEFT, f"Showing 9 of {len(habits)} archived habits.")
        self._draw_message(screen, 16)

    def _draw_archive_period_list_page(self, screen: "curses.window") -> None:
        back_label = "< Back"
        self._addstr(screen, 1, CALENDAR_LEFT, back_label, curses.A_BOLD)
        self._addstr(screen, 1, DETAIL_LEFT, "Archive History", self._color(1) | curses.A_BOLD)
        self.hitboxes.append(HitBox("back", 1, CALENDAR_LEFT, CALENDAR_LEFT + len(back_label) - 1))

        if self.selected_period_habit_id is None:
            self._addstr(screen, 4, CALENDAR_LEFT, "No habit selected.")
            self._draw_message(screen)
            return

        self._addstr(screen, 3, CALENDAR_LEFT, self._truncate(self.selected_period_habit_name, 42), curses.A_BOLD)

        periods = self.store.active_periods_for_habit(self.selected_period_habit_id)
        if not periods:
            self._addstr(screen, 5, CALENDAR_LEFT, "No archive history yet.")
            self._draw_message(screen)
            return

        ordered = list(reversed(periods))
        for index, period in enumerate(ordered[:9]):
            y = 5 + index
            row_label = (
                f"{period.period_number}) {self._truncate(self.selected_period_habit_name, 24)} "
                f"({period.start_date.isoformat()}-{period.end_date.isoformat()})"
            )
            self._addstr(screen, y, CALENDAR_LEFT, row_label)
            self.hitboxes.append(
                HitBox(
                    "archive_period_row",
                    y,
                    CALENDAR_LEFT,
                    CALENDAR_LEFT + len(row_label) - 1,
                    (period.period_number, period.start_date.isoformat(), period.end_date.isoformat()),
                )
            )

        if len(ordered) > 9:
            self._addstr(screen, 15, CALENDAR_LEFT, f"Showing 9 of {len(ordered)} archive periods.")
        self._draw_message(screen, 16)

    def _draw_archive_period_stats_page(self, screen: "curses.window") -> None:
        height, _ = screen.getmaxyx()
        footer_y = max(4, height - 2)
        message_y = max(4, height - 1)

        back_label = "< Back"
        self._addstr(screen, 1, CALENDAR_LEFT, back_label, curses.A_BOLD)
        self._addstr(screen, 1, DETAIL_LEFT, "Archive Stats", self._color(1) | curses.A_BOLD)
        self.hitboxes.append(HitBox("back", 1, CALENDAR_LEFT, CALENDAR_LEFT + len(back_label) - 1))

        if (
            self.selected_period_habit_id is None
            or self.selected_period_number is None
            or self.selected_period_start is None
            or self.selected_period_end is None
        ):
            self._addstr(screen, 4, CALENDAR_LEFT, "No archive period selected.")
            self._draw_command_footer(screen, footer_y)
            self._draw_message(screen, message_y)
            return

        stats = self.store.habit_stats_for_period(
            self.selected_period_habit_id, self.selected_period_start, self.selected_period_end
        )

        title = f"{self.selected_period_number}) {self._truncate(self.selected_period_habit_name, 40)}"
        self._addstr(screen, 3, CALENDAR_LEFT, title, curses.A_BOLD)
        self._addstr(screen, 5, CALENDAR_LEFT, f"Current streak: {stats.current_streak} {self._day_word(stats.current_streak)}")

        if stats.longest_streak is None:
            longest_label = "Longest streak: 0 days"
        else:
            longest_label = (
                f"Longest streak: {stats.longest_streak.length} {self._day_word(stats.longest_streak.length)} "
                f"({stats.longest_streak.start_date.isoformat()} to {stats.longest_streak.end_date.isoformat()})"
            )
        self._addstr(screen, 6, CALENDAR_LEFT, self._truncate(longest_label, 70))

        history_label = f"Streak History: {len(stats.streaks)}"
        self._addstr(screen, 8, CALENDAR_LEFT, history_label, curses.A_BOLD)
        self.hitboxes.append(
            HitBox("archive_period_streak_history_link", 8, CALENDAR_LEFT, CALENDAR_LEFT + len(history_label) - 1)
        )

        self._addstr(screen, 10, CALENDAR_LEFT, f"Period start: {self.selected_period_start.isoformat()}")
        self._addstr(screen, 11, CALENDAR_LEFT, f"Period end: {self.selected_period_end.isoformat()}")

        notes_label = f"Notes: {stats.note_count}"
        notes_style = curses.A_BOLD if stats.note_count else curses.A_DIM
        self._addstr(screen, 13, CALENDAR_LEFT, notes_label, notes_style)
        self.hitboxes.append(
            HitBox("archive_period_notes_link", 13, CALENDAR_LEFT, CALENDAR_LEFT + len(notes_label) - 1)
        )

        self._draw_command_footer(screen, footer_y)
        self._draw_message(screen, message_y)

    def _draw_archive_period_streak_history_page(self, screen: "curses.window") -> None:
        height, _ = screen.getmaxyx()
        status_y = max(4, height - 3)
        footer_y = max(4, height - 2)
        message_y = max(4, height - 1)
        visible_count = max(1, status_y - 5)

        back_label = "< Back"
        self._addstr(screen, 1, CALENDAR_LEFT, back_label, curses.A_BOLD)
        self._addstr(screen, 1, DETAIL_LEFT, "Streak History", self._color(1) | curses.A_BOLD)
        self.hitboxes.append(HitBox("back", 1, CALENDAR_LEFT, CALENDAR_LEFT + len(back_label) - 1))

        if (
            self.selected_period_habit_id is None
            or self.selected_period_start is None
            or self.selected_period_end is None
        ):
            self.streaks_scroll = 0
            self._addstr(screen, 4, CALENDAR_LEFT, "No archive period selected.")
            self._draw_command_footer(screen, footer_y)
            self._draw_message(screen, message_y)
            return

        stats = self.store.habit_stats_for_period(
            self.selected_period_habit_id, self.selected_period_start, self.selected_period_end
        )
        title = f"{self.selected_period_number}) {self._truncate(self.selected_period_habit_name, 40)}"
        self._addstr(screen, 3, CALENDAR_LEFT, title, curses.A_BOLD)
        self._addstr(screen, 4, CALENDAR_LEFT, "Length", curses.A_BOLD)
        self._addstr(screen, 4, DETAIL_LEFT, "Dates", curses.A_BOLD)

        if not stats.streaks:
            self.streaks_scroll = 0
            self._addstr(screen, 6, CALENDAR_LEFT, "No done streaks yet.")
            self._draw_command_footer(screen, footer_y)
            self._draw_message(screen, message_y)
            return

        max_scroll = max(0, len(stats.streaks) - visible_count)
        self.streaks_scroll = min(self.streaks_scroll, max_scroll)
        visible_streaks = stats.streaks[self.streaks_scroll : self.streaks_scroll + visible_count]
        for index, streak in enumerate(visible_streaks):
            y = 6 + index
            length_label = f"{streak.length} {self._day_word(streak.length)}"
            date_label = f"{streak.start_date.isoformat()} to {streak.end_date.isoformat()}"
            self._addstr(screen, y, CALENDAR_LEFT, length_label)
            self._addstr(screen, y, DETAIL_LEFT, date_label)

        if len(stats.streaks) > visible_count:
            first = self.streaks_scroll + 1
            last = self.streaks_scroll + len(visible_streaks)
            self._addstr(screen, status_y, CALENDAR_LEFT, f"Showing {first}-{last} of {len(stats.streaks)}. Up/Down scroll.")
        self._draw_command_footer(screen, footer_y)
        self._draw_message(screen, message_y)

    def _draw_archive_period_notes_page(self, screen: "curses.window") -> None:
        height, _ = screen.getmaxyx()
        status_y = max(4, height - 2)
        message_y = max(4, height - 1)
        visible_count = max(1, status_y - 4)

        back_label = "< Back"
        self._addstr(screen, 1, CALENDAR_LEFT, back_label, curses.A_BOLD)
        self._addstr(screen, 1, DETAIL_LEFT, "Archive Notes", self._color(1) | curses.A_BOLD)
        self.hitboxes.append(HitBox("back", 1, CALENDAR_LEFT, CALENDAR_LEFT + len(back_label) - 1))
        self._addstr(screen, 3, CALENDAR_LEFT, self._truncate(self.selected_period_habit_name, 42), curses.A_BOLD)

        if (
            self.selected_period_habit_id is None
            or self.selected_period_start is None
            or self.selected_period_end is None
        ):
            self.habit_notes_scroll = 0
            self._addstr(screen, 5, CALENDAR_LEFT, "No archive period selected.")
            self._draw_message(screen, message_y)
            return

        notes = self.store.notes_for_habit_in_range(
            self.selected_period_habit_id, self.selected_period_start, self.selected_period_end
        )
        if not notes:
            self.habit_notes_scroll = 0
            self._addstr(screen, 5, CALENDAR_LEFT, "No notes exist for this archive period.")
            self._draw_message(screen, message_y)
            return

        max_scroll = max(0, len(notes) - visible_count)
        self.habit_notes_scroll = min(self.habit_notes_scroll, max_scroll)
        visible_notes = notes[self.habit_notes_scroll : self.habit_notes_scroll + visible_count]

        for index, note in enumerate(visible_notes):
            y = 5 + index
            label = note_title_for(note.habit_name, note.note_date)
            self._addstr(screen, y, CALENDAR_LEFT, self._truncate(label, 70), curses.A_BOLD)
            self.hitboxes.append(
                HitBox(
                    "archive_period_note",
                    y,
                    CALENDAR_LEFT,
                    CALENDAR_LEFT + min(len(label), 70) - 1,
                    (note.habit_id, note.habit_name, note.note_date),
                )
            )

        if len(notes) > visible_count:
            first = self.habit_notes_scroll + 1
            last = self.habit_notes_scroll + len(visible_notes)
            self._addstr(screen, status_y, CALENDAR_LEFT, f"Showing {first}-{last} of {len(notes)}. Up/Down scroll.")
        self._draw_message(screen, message_y)

    def _draw_challenge_mode_page(self, screen: "curses.window") -> None:
        back_label = "< Back"
        self._addstr(screen, 1, CALENDAR_LEFT, back_label, curses.A_BOLD)
        self._addstr(screen, 1, DETAIL_LEFT, "Challenge Mode", self._color(1) | curses.A_BOLD)
        self.hitboxes.append(HitBox("back", 1, CALENDAR_LEFT, CALENDAR_LEFT + len(back_label) - 1))

        create_label = "Create Challenge"
        self._addstr(screen, 4, CALENDAR_LEFT, create_label, curses.A_BOLD)
        self._addstr(screen, 6, CALENDAR_LEFT, "Challenges are goals; habits keep running after they end.")
        self.hitboxes.append(HitBox("challenge_create", 4, CALENDAR_LEFT, CALENDAR_LEFT + len(create_label) - 1))
        self._draw_message(screen, 16)

    def _draw_create_challenge_page(self, screen: "curses.window") -> None:
        back_label = "< Back"
        self._addstr(screen, 1, CALENDAR_LEFT, back_label, curses.A_BOLD)
        self._addstr(screen, 1, DETAIL_LEFT, "Create Challenge", self._color(1) | curses.A_BOLD)
        self.hitboxes.append(HitBox("back", 1, CALENDAR_LEFT, CALENDAR_LEFT + len(back_label) - 1))

        existing_label = "Existing Habit"
        new_label = "New Habit"
        self._addstr(screen, 4, CALENDAR_LEFT, existing_label, curses.A_BOLD)
        self._addstr(screen, 6, CALENDAR_LEFT, new_label, curses.A_BOLD)
        self.hitboxes.append(HitBox("challenge_existing", 4, CALENDAR_LEFT, CALENDAR_LEFT + len(existing_label) - 1))
        self.hitboxes.append(HitBox("challenge_new", 6, CALENDAR_LEFT, CALENDAR_LEFT + len(new_label) - 1))
        self._draw_message(screen, 16)

    def _draw_existing_challenge_habits_page(self, screen: "curses.window") -> None:
        back_label = "< Back"
        self._addstr(screen, 1, CALENDAR_LEFT, back_label, curses.A_BOLD)
        self._addstr(screen, 1, DETAIL_LEFT, "Existing Habit", self._color(1) | curses.A_BOLD)
        self.hitboxes.append(HitBox("back", 1, CALENDAR_LEFT, CALENDAR_LEFT + len(back_label) - 1))

        habits = self.store.list_active_habits()
        if not habits:
            self._addstr(screen, 4, CALENDAR_LEFT, "No active habits available.")
            self._draw_message(screen)
            return

        self._addstr(screen, 3, CALENDAR_LEFT, "Choose an active habit for this challenge.", curses.A_BOLD)
        for index, habit in enumerate(habits[:9]):
            y = 5 + index
            habit_label = f"{self._truncate(habit.name, 28)} ({habit.start_date.isoformat()})"
            choose_label = "Choose"
            choose_x = DETAIL_LEFT + 20
            self._addstr(screen, y, CALENDAR_LEFT, habit_label)
            self._addstr(screen, y, choose_x, choose_label, curses.A_BOLD)
            self.hitboxes.append(
                HitBox("challenge_existing_habit", y, choose_x, choose_x + len(choose_label) - 1, (habit.habit_id, habit.name))
            )

        if len(habits) > 9:
            self._addstr(screen, 15, CALENDAR_LEFT, f"Showing 9 of {len(habits)} active habits.")
        self._draw_message(screen, 16)

    def _draw_challenge_end_options_page(self, screen: "curses.window") -> None:
        back_label = "< Back"
        self._addstr(screen, 1, CALENDAR_LEFT, back_label, curses.A_BOLD)
        self._addstr(screen, 1, DETAIL_LEFT, "Challenge End", self._color(1) | curses.A_BOLD)
        self.hitboxes.append(HitBox("back", 1, CALENDAR_LEFT, CALENDAR_LEFT + len(back_label) - 1))

        target = self.challenge_pick_target() or "Selected habit"
        self._addstr(screen, 3, CALENDAR_LEFT, self._truncate(target, 42), curses.A_BOLD)
        duration_label = "Set Duration"
        end_date_label = "Pick Ending Date"
        self._addstr(screen, 5, CALENDAR_LEFT, duration_label, curses.A_BOLD)
        self._addstr(screen, 7, CALENDAR_LEFT, end_date_label, curses.A_BOLD)
        self.hitboxes.append(HitBox("challenge_duration", 5, CALENDAR_LEFT, CALENDAR_LEFT + len(duration_label) - 1))
        self.hitboxes.append(HitBox("challenge_end_date", 7, CALENDAR_LEFT, CALENDAR_LEFT + len(end_date_label) - 1))
        self._draw_message(screen, 16)

    def _draw_challenge_date_picker_page(self, screen: "curses.window", height: int, width: int) -> None:
        today = date.today()
        target = self.challenge_pick_target() or "Selected habit"
        calendar_width = 7 * CELL_WIDTH - 1
        left = max(0, (width - calendar_width) // 2)
        top = max(4, (height - 10) // 2)
        title = f"{calendar.month_name[self.selection.month]} {self.selection.year}"
        title_x = max(0, (width - len(title)) // 2)
        previous_label = "< Prev"
        next_label = "Next >"
        previous_x = max(0, title_x - len(previous_label) - 4)
        next_x = min(max(0, width - len(next_label) - 1), title_x + len(title) + 4)

        self._addstr(screen, 1, max(0, (width - 21) // 2), "Pick Challenge End", self._color(1) | curses.A_BOLD)
        self._addstr(screen, 3, max(0, (width - min(len(target), 42)) // 2), self._truncate(target, 42), curses.A_BOLD)
        self._addstr(screen, top - 3, previous_x, previous_label, curses.A_BOLD)
        self._addstr(screen, top - 3, title_x, title, self._color(1) | curses.A_BOLD)
        self._addstr(screen, top - 3, next_x, next_label, curses.A_BOLD)
        self.hitboxes.append(HitBox("previous", top - 3, previous_x, previous_x + len(previous_label) - 1))
        self.hitboxes.append(HitBox("next", top - 3, next_x, next_x + len(next_label) - 1))

        for col_index, weekday_name in enumerate(WEEKDAY_NAMES):
            x = left + col_index * CELL_WIDTH
            self._addstr(screen, top - 1, x, weekday_cell_label(weekday_name), curses.A_BOLD)
        for row_index, week in enumerate(calendar.monthcalendar(self.selection.year, self.selection.month)):
            y = top + row_index
            for col_index, day in enumerate(week):
                x = left + col_index * CELL_WIDTH
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

                self._addstr(screen, y, x, f"{day:2}", style)
                self.hitboxes.append(HitBox("day", y, x, x + 1, day))

        self._draw_message(screen, min(height - 2, top + 8))

    def _draw_command_footer(self, screen: "curses.window", y: int) -> None:
        self._addstr(screen, y, CALENDAR_LEFT, "Type / for commands. Stats: /viewall and /viewactive switch the habit list.", curses.A_DIM)

    @staticmethod
    def _day_word(count: int) -> str:
        return "day" if count == 1 else "days"

    def _draw_footer(self, screen: "curses.window") -> None:
        height, _ = screen.getmaxyx()
        footer_y = self._main_footer_y(height)
        self._draw_message(screen, footer_y)
        self._addstr(screen, footer_y + 2, CALENDAR_LEFT, "Keys: / cmd, h help, q quit, Left/Right month, Up/Down habits, t, a.")
        self._addstr(screen, footer_y + 3, CALENDAR_LEFT, "Calendar: + done, ! missed, yellow dates have past pending tasks.")

    @staticmethod
    def _main_footer_y(height: int) -> int:
        return max(CALENDAR_TOP + 8, height - 4)

    @classmethod
    def _main_habit_page_size(cls, height: int) -> int:
        list_top = 5
        list_bottom = cls._main_footer_y(height) - 1
        return max(1, (list_bottom - list_top + 2) // 3)

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

    def _danger_style(self) -> int:
        return self._color(4) | curses.A_BOLD

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
    lines = [
        f"{calendar.month_name[selection.month]} {selection.year}".center(20),
        plain_weekday_header(),
    ]

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
    try:
        curses.curs_set(0)
    except curses.error:
        pass
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
        curses.init_pair(7, curses.COLOR_WHITE, curses.COLOR_MAGENTA)

    while True:
        app.render(screen)
        key = screen.getch()

        if app.view == "note_editor":
            app.handle_note_key(key)
            continue

        if key in (ord("q"), ord("Q")):
            break
        if app.view != "main" and key in (ord("b"), ord("B")):
            app.go_back()
        elif key == 27:
            if app.view == "main":
                break
            app.go_back()
        elif key == ord("/"):
            if not app.run_command(screen):
                break
        elif app.view == "main" and key in (ord("h"), ord("H")):
            app.open_help()
        elif app.view == "main" and key == curses.KEY_LEFT:
            app.move_month(-1)
        elif app.view == "main" and key == curses.KEY_RIGHT:
            app.move_month(1)
        elif app.view == "main" and key == curses.KEY_UP:
            height, _ = screen.getmaxyx()
            app.scroll_main_habits(-1, app._main_habit_page_size(height))
        elif app.view == "main" and key == curses.KEY_DOWN:
            height, _ = screen.getmaxyx()
            app.scroll_main_habits(1, app._main_habit_page_size(height))
        elif app.view == "main" and key == curses.KEY_PPAGE:
            height, _ = screen.getmaxyx()
            page_size = app._main_habit_page_size(height)
            app.scroll_main_habits(-page_size, page_size)
        elif app.view == "main" and key == curses.KEY_NPAGE:
            height, _ = screen.getmaxyx()
            page_size = app._main_habit_page_size(height)
            app.scroll_main_habits(page_size, page_size)
        elif app.view == "notifications" and key in (curses.KEY_UP, ord("k"), ord("K")):
            height, _ = screen.getmaxyx()
            app.scroll_notifications(-1, max(1, height - 5))
        elif app.view == "notifications" and key in (curses.KEY_DOWN, ord("j"), ord("J")):
            height, _ = screen.getmaxyx()
            app.scroll_notifications(1, max(1, height - 5))
        elif app.view == "notifications" and key == curses.KEY_PPAGE:
            height, _ = screen.getmaxyx()
            page_size = max(1, height - 5)
            app.scroll_notifications(-page_size, page_size)
        elif app.view == "notifications" and key == curses.KEY_NPAGE:
            height, _ = screen.getmaxyx()
            page_size = max(1, height - 5)
            app.scroll_notifications(page_size, page_size)
        elif app.view == "notes" and key in (curses.KEY_UP, ord("k"), ord("K")):
            height, _ = screen.getmaxyx()
            app.scroll_notes(-1, max(1, height - 6))
        elif app.view == "notes" and key in (curses.KEY_DOWN, ord("j"), ord("J")):
            height, _ = screen.getmaxyx()
            app.scroll_notes(1, max(1, height - 6))
        elif app.view == "notes" and key == curses.KEY_PPAGE:
            height, _ = screen.getmaxyx()
            page_size = max(1, height - 6)
            app.scroll_notes(-page_size, page_size)
        elif app.view == "notes" and key == curses.KEY_NPAGE:
            height, _ = screen.getmaxyx()
            page_size = max(1, height - 6)
            app.scroll_notes(page_size, page_size)
        elif app.view == "habit_notes" and key in (curses.KEY_UP, ord("k"), ord("K")):
            height, _ = screen.getmaxyx()
            app.scroll_habit_notes(-1, max(1, height - 6))
        elif app.view == "habit_notes" and key in (curses.KEY_DOWN, ord("j"), ord("J")):
            height, _ = screen.getmaxyx()
            app.scroll_habit_notes(1, max(1, height - 6))
        elif app.view == "habit_notes" and key == curses.KEY_PPAGE:
            height, _ = screen.getmaxyx()
            page_size = max(1, height - 6)
            app.scroll_habit_notes(-page_size, page_size)
        elif app.view == "habit_notes" and key == curses.KEY_NPAGE:
            height, _ = screen.getmaxyx()
            page_size = max(1, height - 6)
            app.scroll_habit_notes(page_size, page_size)
        elif app.view == "stats" and key in (curses.KEY_UP, ord("k"), ord("K")):
            height, _ = screen.getmaxyx()
            app.scroll_stats(-1, max(1, height - 8))
        elif app.view == "stats" and key in (curses.KEY_DOWN, ord("j"), ord("J")):
            height, _ = screen.getmaxyx()
            app.scroll_stats(1, max(1, height - 8))
        elif app.view == "stats" and key == curses.KEY_PPAGE:
            height, _ = screen.getmaxyx()
            page_size = max(1, height - 8)
            app.scroll_stats(-page_size, page_size)
        elif app.view == "stats" and key == curses.KEY_NPAGE:
            height, _ = screen.getmaxyx()
            page_size = max(1, height - 8)
            app.scroll_stats(page_size, page_size)
        elif app.view == "streak_history" and key in (curses.KEY_UP, ord("k"), ord("K")):
            height, _ = screen.getmaxyx()
            app.scroll_streak_history(-1, max(1, height - 8))
        elif app.view == "streak_history" and key in (curses.KEY_DOWN, ord("j"), ord("J")):
            height, _ = screen.getmaxyx()
            app.scroll_streak_history(1, max(1, height - 8))
        elif app.view == "streak_history" and key == curses.KEY_PPAGE:
            height, _ = screen.getmaxyx()
            page_size = max(1, height - 8)
            app.scroll_streak_history(-page_size, page_size)
        elif app.view == "streak_history" and key == curses.KEY_NPAGE:
            height, _ = screen.getmaxyx()
            page_size = max(1, height - 8)
            app.scroll_streak_history(page_size, page_size)
        elif app.view == "main" and key in (ord("a"), ord("A")):
            app.add_habit(screen)
        elif app.view == "main" and key == ord("t"):
            today = date.today()
            app.selection = CalendarSelection(today.year, today.month)
            app.selected_day = today.day
            app.main_habit_scroll = 0
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
