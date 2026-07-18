# Habit Tracker

A small terminal habit tracker with a navigable monthly calendar. It lets you add daily habits from a selected start date, mark each habit as pending, done, or missed for a day, and persist the data locally in SQLite.

**NOTE: THIS PROJECT IS VIBE CODED AND MAINLY EXISTS TO SCRATCH A PERSONAL ITCH.**

## Features

- Interactive monthly calendar in the terminal
- Optional Textual prototype UI for a more structured terminal interface
- Mouse support for selecting dates and controls
- Daily habit creation from any selected date
- Explicit habit archiving and resurrection without deleting history
- Challenge goals that track progress without archiving the underlying habit
- Per-day habit status tracking: `Pending`, `Done`, or `Missed`
- Per-habit daily notes with a locked text editor
- Hidden `/notes` browser for reviewing saved notes by habit
- Hidden `/stats` browser for habit streaks, note counts, and streak history
- Calendar markers for days with completed, missed, or past pending habits
- Current streak counts shown beside each habit on the main page
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

Run the optional Textual prototype:

```bash
python3 -m pip install -r requirements-textual.txt
python3 textual_habit_tracker.py
```

The Textual GUI is only a prototype at this time. It currently focuses on the main workflow: choosing dates, adding habits, updating daily statuses, and showing current streak counts beside habit names. Click `+ Add` to focus the habit name field, then press `Enter` to create the habit. The classic `curses` app still contains the complete feature set.

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
- Click `+Note` to add a note for a habit on the selected date, or highlighted `Note` to reopen an existing note
- Use Up/Down or Page Up/Page Down on the main screen to scroll the selected day's habit list when needed
- Click `Notifications` when shown to review past dates with pending habits, then click a notification to jump to that date
- Use Up/Down or Page Up/Page Down to scroll the notifications page when needed
- Press `/` to enter a command; matching commands are suggested as you type, and `Tab` completes a single match
- Press `h` to open help from the main screen
- Use `/help` to list hidden commands
- Use `/backup` to open backup tools, then choose `Create Backup` or `Manage Backups`
- Use `/managehabit` to open habit management, then choose `Rename`, `Challenge Mode`, `Archive`, or `Delete [DANGER]`
- Use `/notes` to browse all habits with note counts, then open a habit's saved notes in reverse chronological order
- Use `/stats` to browse habit streak stats; active habits are bold, and archived habits are plain when shown
- Use `/viewall` on stats pages to include archived habits, or `/viewactive` to return to active habits only
- In `Challenge Mode`, choose `Create Challenge` to set an end date for a challenge
- Create a challenge from an active existing habit or a new habit, then choose `Set Duration` or `Pick Ending Date`
- `Pick Ending Date` opens a centered calendar picker and confirms both the challenge duration and end date
- In `Archive`, choose `Archive Habit` to hide a habit from active tracking; a note explains that archived habits will no longer appear in your daily tasks
- In `Archive`, choose `View Archive` to view archived habits and resurrect them
- Type `DELETE` when prompted to confirm an irreversible habit deletion
- Use `/quit` to quit from the command prompt
- Press left/right arrows to move between months
- Press `t` to jump to today
- Press `b` to go back from secondary screens
- Press `Esc` to go back from secondary screens or cancel a command prompt
- Press `q` or `Esc` on the main screen to quit

## Habit Status Behavior

When a habit is created, it becomes active starting on the selected date.

If the selected start date is before today, the app automatically marks every day from the start date through yesterday as `Done`. Today remains `Pending`.

Archived habits and challenges:

- Archiving a habit hides it from active daily tracking without deleting its history
- Archiving only happens when explicitly chosen from `/managehabit`; it never happens automatically
- Archived habits can be resurrected from `/managehabit` > `Archive` > `View Archive`
- Creating a challenge sets an ending date for an active habit or a newly created habit
- Challenge duration is inclusive: a 90-day challenge created today ends 89 days from today
- Challenge completion means the goal has been met; the underlying habit keeps running
- Dates after a challenge end keep showing the habit as active unless the habit is explicitly archived
- Saved history is preserved for future export or charting

Archive period stats:

- On `View Archive`, each archived habit shows the date range of its most
  recent active stretch, such as `meditation (2026-08-01-2026-09-10)`
- Click `Stats` next to `Resurrect` to see every active stretch the habit has
  had, most recent first and numbered from the original creation-to-first-archive
  stretch onward (a never-resurrected habit shows only `1)`)
- Clicking a numbered stretch shows current streak, longest streak, streak
  history, and note count computed only within that stretch's own dates
- This per-stretch view is separate from `/stats`, which is unaffected

For active habits:

- Dates without a saved status default to `Pending`
- Explicit `Done` or `Missed` choices are saved for that specific date
- Choosing `Pending` clears the saved status for that habit and date
- The main page shows the current streak beside each habit name, calculated through the selected date
- Habits with an active challenge show challenge progress as `current/duration`, such as `meditation (3/30)`

Notifications:

- The main screen shows `Notifications` only when one or more past dates have pending habits
- Today and future dates do not create notifications
- The notifications page lists each past pending date on its own line
- Notification lines are bold until clicked during the current app session
- Clicking a notification jumps to that date so its habits can be marked `Done` or `Missed`

Habit stats:

- `/stats` lists active habits with the current streak count beside each habit
- Current streaks count consecutive explicit `Done` days; `Missed` and historical `Pending` days break streaks
- If today is still `Pending`, the current streak is counted through yesterday; if today is `Done`, today is included
- Clicking a habit opens current streak, longest streak, streak history, start date, archive date when present, and note count
- Clicking `Streak History` shows all streaks from longest to shortest with their start and end dates
- Clicking `Notes` opens that habit's saved notes when notes exist
- `/viewall` includes archived habits in the stats list; active habits are bold and archived habits are plain

Daily notes:

- `+Note` means the habit has no note on the selected date
- Highlighted `Note` means the habit has a saved note on the selected date
- Notes are stored per habit and date, and multiline notes are supported
- `/notes` lists every habit alphabetically with its note count, including habits with zero notes
- Clicking a habit with notes opens its notes newest-first by associated note date
- Long note lines wrap inside the editor instead of running past the frame
- Note pages open locked so typing does not edit them by accident
- Press `i` to insert text in a note, then `Esc` to lock it again
- In insert mode, printable keys insert text, `Enter` adds a line break, `Backspace` deletes, and arrow keys move the cursor
- In locked mode, use `^` for first nonblank character, `$` for line end, `w` for next word, `:w` to save, `:q` to quit without saving unsaved edits, or `:wq` to save and quit
- Saving an empty or whitespace-only note deletes it

Calendar markers:

- `+` means at least one active habit on that date is done
- `!` means at least one habit is missed
- Past dates with pending habits are highlighted in yellow
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
