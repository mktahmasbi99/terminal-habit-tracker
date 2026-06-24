# Habit Tracker

A small terminal habit tracker with a navigable monthly calendar. It lets you add daily habits from a selected start date, mark each habit as done or missed for a day, and persist the data locally in SQLite.

## Features

- Interactive monthly calendar in the terminal
- Mouse support for selecting dates and controls
- Daily habit creation from any selected date
- Per-day habit status tracking: `Done`, `Missed`, or future `Pending`
- Calendar markers for days with completed or missed habits
- Local SQLite persistence
- Plain text month view for quick output or scripting

## Requirements

- Python 3.10 or newer
- A terminal with curses support

No third-party Python packages are required.

## Run

Start the interactive app:

```bash
python3 calendar_view.py
```

Open a specific month:

```bash
python3 calendar_view.py --month 6 --year 2026
```

Use a custom database path:

```bash
python3 calendar_view.py --db /path/to/habits.sqlite3
```

Print a non-interactive calendar view:

```bash
python3 calendar_view.py --plain
```

## Controls

- Click a date to select it
- Click `< Prev` or `Next >` to change months
- Click `+ Add daily habit` or press `a` to add a habit for the selected date
- Press `Esc` while adding a habit to cancel without saving
- Click `Done` or `Missed` to set a habit status for the selected date
- Press left/right arrows or Page Up/Page Down to move between months
- Press `t` to jump to today
- Press `q` or `Esc` to quit

## Habit Status Behavior

When a habit is created, it becomes active starting on the selected date.

For active habits:

- Past dates and today default to `Done`
- Future dates default to `Pending`
- Explicit `Done` or `Missed` choices are saved for that specific date

Calendar markers:

- `+` means active habits on that date are done
- `!` means at least one habit is missed
- Future pending dates are not marked as done

## Data Files

By default, the app stores data in:

```text
habit_tracker.sqlite3
```

The local database and prompt scratch file are ignored by git through `.gitignore`.
