# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Shape

This is a small, personal, vibe-coded terminal habit tracker. It is intentionally a
single-file Python application (`terminal_habit_tracker.py`, ~3100 lines) with no
third-party dependencies, no test suite, and no package metadata/formatter/linter
config. There is also an optional, deliberately incomplete Textual GUI prototype
(`textual_habit_tracker.py`) that only covers the main workflow (add habits, set
daily status, show streaks) — the `curses` app remains the source of truth for the
full feature set.

Runtime data is local SQLite (`habit_tracker.sqlite3` by default) with timestamped
backups in a `backups/` directory beside the active database.

## Common Commands

Run the interactive app:
```bash
python3 terminal_habit_tracker.py
```

Run the optional Textual prototype (needs `pip install -r requirements-textual.txt`):
```bash
python3 textual_habit_tracker.py
```

Open a specific month:
```bash
python3 terminal_habit_tracker.py --month 6 --year 2026
```

Use a custom database path (do this for any manual testing — never touch the real
`habit_tracker.sqlite3` / `backups/`):
```bash
python3 terminal_habit_tracker.py --db /tmp/habit-tracker-test.sqlite3
```

Print a non-interactive month view (useful for quick checks without curses):
```bash
python3 terminal_habit_tracker.py --plain
```

Create an on-demand backup / restore one:
```bash
python3 terminal_habit_tracker.py --backup
python3 terminal_habit_tracker.py --restore /path/to/backup.sqlite3 --force
```

### Verification

There is no automated test suite. Useful manual checks:
```bash
python3 -m py_compile terminal_habit_tracker.py
python3 terminal_habit_tracker.py --db /tmp/habit-tracker-check.sqlite3 --plain
python3 terminal_habit_tracker.py --db /tmp/habit-tracker-check.sqlite3 --backup
```
For storage logic changes, consider adding focused unit tests around `HabitStore`
alongside the change (there's no existing test harness to plug into yet).

## Architecture

Everything lives in `terminal_habit_tracker.py`:

- `HabitStore` — all SQLite schema/migrations and persistence: habits, daily
  statuses, challenges, notes, backups, rename/archive/delete.
- `CalendarApp` — all curses UI state, rendering, click/key handling, prompts,
  and screen navigation. `CalendarApp.view` is a plain string naming the current
  screen (`main`, `manage_habits`, `create_challenge`, `notes`, `stats`, etc.);
  navigation flows through `go_back`, `handle_click`, and the key loop in
  `run_curses`.
- `CalendarSelection` — selected year/month plus month navigation.
- Small dataclasses for domain/UI state: `Habit`, `HabitStatus`, `HabitChallenge`,
  `ChallengeProgress`, `HabitNoteCount`, `HabitNoteRef`, `HabitStreak`,
  `HabitStats`, `HabitActivePeriod`, `PendingNotification`, `HitBox`,
  `NoteDisplayLine`.
- `build_month_view` — plain-text calendar output used by `--plain`.
- Backup helpers: `default_backup_directory`, `backup_destination`,
  `automatic_backup_destination`, `automatic_backup_paths`,
  `prune_automatic_backups`, `create_automatic_backup`, `list_backup_files`,
  `restore_database`.

The in-app slash commands are the `COMMANDS` list: `/help`, `/backup`,
`/managehabit`, `/notes`, `/stats`, `/viewall`, `/viewactive`, `/quit`. The
command prompt supports suggestion/tab-completion when there's a single match.

`CalendarApp.view` is string-based; known views are `main`, `help`, `backups`,
`manage_backups`, `manage_habits`, `rename_habits`, `archive_mode`,
`archive_habits`, `archived_habits`, `archive_period_list`,
`archive_period_stats`, `archive_period_streak_history`,
`archive_period_notes`, `complete_habits`, `complete_challenge_habits`,
`create_challenge`, `challenge_existing_habits`, `challenge_end_options`,
`challenge_date_picker`, `delete_habits`, `notifications`, `notes`,
`habit_notes`, `stats`, `habit_stats`, `streak_history`, `note_editor`.

`Manage Habits` nests two sub-flows one level deep, mirroring each other:
`Challenge Mode` → `create_challenge` (and its own sub-screens), and
`Archive` (`archive_mode`) → `archive_habits` (pick an active habit to
archive) or `archived_habits` (browse/resurrect already-archived habits).
`go_back()` returns each nested view to its category page, and each
category page back to `manage_habits`.

`archived_habits` nests a further, separate sub-flow: its "Stats" button
opens `archive_period_list` (every historical active stretch for that
habit, derived from `HabitStore.active_periods_for_habit`, most recent
first), and each numbered stretch opens `archive_period_stats` →
optionally `archive_period_streak_history`/`archive_period_notes`. This
mirrors the shape of the main `habit_stats`/`streak_history`/`habit_notes`
flow, but every number (current streak, longest streak, streak history,
note count) is computed via `HabitStore.habit_stats_for_period` scoped
strictly to that one stretch's `[start, end]` window — it does not touch
`/stats` or `HabitStore.habit_stats`, which remain whole-lifetime and
unscoped.

### Domain model / lifecycle rules

- A habit has `id`, `name`, `start_date`, optional `completed_at`, `created_at`.
- Daily statuses live in `habit_logs` (`habit_id`, `log_date`, `status`); only
  explicit `done`/`missed` are persisted. `pending` is the default derived state
  represented by *no row* — selecting `Pending` deletes the saved row.
- Habit notes live in `habit_notes` by `habit_id`/`note_date` with multiline
  `body`; saving an empty note deletes the row.
- A habit is active on a date when `start_date <= date` and (`completed_at` is
  null or on/after that date).
- Creating a habit with a past start date auto-marks every day from start date
  through yesterday as `done`; today stays `pending`.
- Archiving (setting `completed_at`) hides a habit from active tracking but
  preserves history; it only ever happens when explicitly chosen from
  `/managehabit`, never automatically. Archived habits can be resurrected.
- Every archive/resurrect cycle is a row in `habit_archive_periods`
  (`archived_at`, `resurrected_at` nullable). `active_periods_for_habit`
  reconstructs each closed active stretch from this table plus the habit's
  `start_date`; it assumes the habit is currently archived (guaranteed by
  `_ensure_archive_periods`) and does not handle a still-open trailing
  stretch — don't reuse it for currently-active habits without adjusting for
  that.
- "Challenge mode" reuses `completed_at` set to a future end date instead of
  archiving. Challenge duration is inclusive: a 90-day challenge created today
  ends 89 days from today. Dates after a challenge end still show the habit as
  active unless it's explicitly archived.
- Backups: on-demand backups use prefix `o-` and are never auto-pruned;
  automatic backups use prefix `a-`, are created once per day on interactive
  startup, and only the latest 5 are kept. Backup management (restore/delete)
  is scoped to files inside the active database's own backup directory.
  Restore requires typing `RESTORE` (unless `--force`); delete requires typing
  `DELETE` — follow this same "type the exact word" pattern for any new
  irreversible action.

## Working conventions

- Keep changes narrowly scoped — this is a small personal utility, not a
  platform. Avoid broad rewrites of the single-file structure unless the task
  specifically asks for refactoring.
- Prefer the existing standard-library-only approach; don't add a dependency
  unless a feature clearly needs one (the Textual prototype is the one
  existing exception, and it's optional/off the main path).
- Be careful with date logic — use concrete dates when reasoning through
  behavior, and prefer structured SQLite queries/date objects over string-only
  date logic.
- Keep curses layout stable at the documented minimum terminal size of 76x17.
- When adding a screen, wire up both rendering and click/key navigation paths.
- When changing lifecycle semantics (active/archived/challenge rules), update
  all of: schema/migration logic in `HabitStore.initialize`, active-date
  queries (`habit_active_on`, `habits_for_day`), month summary behavior, and
  the README's behavior docs.
- Preserve local SQLite data and backup safety: never delete/overwrite
  `habit_tracker.sqlite3` or `backups/` while testing — always use `--db
  /tmp/...` or another temporary path.
- Commit subject lines use Conventional Commits (`feat:`, `fix:`, `docs:`,
  `refactor:`, `chore:`, etc.), starting from this convention's adoption —
  earlier history in this repo predates it and uses plain imperative
  subjects instead.
