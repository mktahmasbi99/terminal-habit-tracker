# Habit Tracker

A small terminal habit tracker with a navigable monthly calendar. It lets you add daily habits from a selected start date, mark each habit as pending, done, or missed for a day, and persist the data locally in SQLite.

## Features

- Interactive monthly calendar in the terminal
- Mouse support for selecting dates and controls
- Daily habit creation from any selected date
- Per-day habit status tracking: `Pending`, `Done`, or `Missed`
- Calendar markers for days with completed or missed habits
- Local SQLite persistence
- Automatic daily SQLite backups plus on-demand backup and restore commands
- Plain text month view for quick output or scripting

## Requirements

- Python 3.10 or newer
- A terminal with curses support

No third-party Python packages are required.

## Run

Start the interactive app:

```bash
python3 terminal_habit_tracker.py
```

Open a specific month:

```bash
python3 terminal_habit_tracker.py --month 6 --year 2026
```

Use a custom database path:

```bash
python3 terminal_habit_tracker.py --db /path/to/habits.sqlite3
```

Print a non-interactive calendar view:

```bash
python3 terminal_habit_tracker.py --plain
```

Create an on-demand timestamped backup beside the database:

```bash
python3 terminal_habit_tracker.py --backup
```

Create an on-demand backup at a specific path:

```bash
python3 terminal_habit_tracker.py --backup /path/to/habits-backup.sqlite3
```

Restore from a backup. If the target database already exists, type `RESTORE` when prompted:

```bash
python3 terminal_habit_tracker.py --restore /path/to/habits-backup.sqlite3
```

Restore without an interactive confirmation prompt:

```bash
python3 terminal_habit_tracker.py --restore /path/to/habits-backup.sqlite3 --force
```

## Controls

- Click a date to select it
- Click `< Prev` or `Next >` to change months
- Click `+ Add daily habit` or press `a` to add a habit for the selected date
- Press `Esc` while adding a habit to cancel without saving
- Click `Pending`, `Done`, or `Missed` to set a habit status for the selected date
- Press `/` to enter a command; matching commands are suggested as you type, and `Tab` completes a single match
- Press `h` to open help from the main screen
- Use `/help` to list hidden commands
- Use `/backup` to open backup tools, then choose `Create Backup` or `Manage Backups`
- Use `/delhabit` to open habit deletion
- Type `DELETE` when prompted to confirm an irreversible habit deletion
- Use `/renamehabit` to rename an existing habit
- Use `/quit` to quit from the command prompt
- Press left/right arrows or Page Up/Page Down to move between months
- Press `t` to jump to today
- Press `b` to go back from secondary screens
- Press `Esc` to go back from secondary screens or cancel a command prompt
- Press `q` or `Esc` on the main screen to quit

## Habit Status Behavior

When a habit is created, it becomes active starting on the selected date.

For active habits:

- Dates default to `Pending` until you mark them `Done` or `Missed`
- Explicit `Done` or `Missed` choices are saved for that specific date
- Choosing `Pending` clears the saved status for that habit and date

Calendar markers:

- `+` means at least one active habit on that date is done
- `!` means at least one habit is missed
- Pending dates are not marked as done

## Data Files

By default, the app stores data in:

```text
habit_tracker.sqlite3
```

Backups created without an explicit path are written to a `backups/` directory beside the active database. For the default database, backup files look like:

```text
backups/o-habit_tracker-20260627-143000.sqlite3
backups/a-habit_tracker-20260627.sqlite3
```

When using `--db /path/to/habits.sqlite3`, the default backup directory is `/path/to/backups/`. The in-app `/backup` command follows the same rule.

Automatic backups are created once per calendar day when the interactive app starts. The app keeps the latest 5 automatic backups and deletes older files matching the `a-` backup naming pattern.

On-demand backups are created by `--backup` or by choosing `Create Backup` from the `/backup` page. They use the `o-` backup naming pattern and are kept until you delete them manually.

The `/backup` page includes `Manage Backups`, where you can restore or delete backup files from the active database backup directory. The page legend explains that `a` means automatic and `o` means on-demand. Deleting a backup requires typing `DELETE`; restoring a backup requires typing `RESTORE`.

Restore replaces the active database selected by `--db`, or `habit_tracker.sqlite3` when `--db` is not provided. Existing databases require typing `RESTORE` unless `--force` is used.

The local database, backup directory, and prompt scratch file are ignored by git through `.gitignore`.
