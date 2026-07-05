# Changelog

All notable changes to this project will be documented in this file.

## Unreleased

### Added
- Added current streak counts beside habit names on the main page.
- Added habit completion/archiving behavior.
- Completed habits remain visible through their completion date and disappear from future daily lists.
- Added database support for nullable habit completion dates while preserving historical logs.
- Added a `/managehabit` page with `Rename`, `Challenge Mode`, and `Delete [DANGER]` actions.
- Added `Complete Habit` and `Create Challenge` options under `Challenge Mode`.
- Added challenge creation from an active existing habit or a new habit with either a duration or calendar-picked ending date.
- Added a dedicated centered calendar picker for challenge ending dates.
- Added highlighted past-pending notifications with a clickable notifications page.
- Added per-habit daily notes with a locked multiline editor and `:w`, `:q`, and `:wq` commands.
- Added `/notes` for browsing all habits with note counts and opening saved notes by habit.
- Added `/stats` for browsing habit statistics, including current streaks, longest streaks, streak history, start dates, completion dates, and note counts.
- Added `/viewall` and `/viewactive` filters for switching stats pages between all habits and active habits only.
- Wrapped long note lines inside the editor and moved saved-note highlighting off yellow.
- Added locked-mode note navigation shortcuts for `^`, `$`, and `w`.
- Changed note editing entry from `e` to Vim-style `i` insert mode.

### Changed
- Documented completion/archive and habit management behavior in the README.
- Replaced direct `/delhabit`, `/renamehabit`, and `/completehabit` command flows with the `/managehabit` menu.
- Changed habit completion confirmation from typing `COMPLETE` to a Y/N prompt.
- Changed challenge confirmation messages to include both duration and ending date.
- Changed the main screen to keep the calendar and footer fixed while the selected day's habit list scrolls independently.
- Documented `/stats` habit statistics, stats filters, and main habit-list scrolling in the README.
- Marked the completed completion/archive TODO as `#DONE`.

## Initial development

### Added
- Built the terminal monthly calendar interface with mouse and keyboard navigation.
- Added SQLite-backed daily habit persistence.
- Added daily habit creation from a selected start date.
- Added per-day habit statuses: `Pending`, `Done`, and `Missed`.
- Added calendar markers for completed and missed habits.
- Added plain text month output with `--plain`.
- Added slash-command flows for deleting and renaming habits.
- Added hidden command suggestions and tab completion.
- Added backup creation, restore, backup management, and automatic backup retention.
- Added shortcut keys including `h` for help and `b` for back.
- Added MIT license and project README documentation.

### Changed
- Backdated habits now default past days to `Done` and today to `Pending`.
- New daily habits default to `Pending` unless explicitly marked otherwise.
- Refined status styling so habit names and statuses are easier to distinguish.
