# Changelog

All notable changes to this project will be documented in this file.

## Unreleased

### Added
- Added habit completion/archiving through `/completehabit`.
- Completed habits remain visible through their completion date and disappear from future daily lists.
- Added database support for nullable habit completion dates while preserving historical logs.

### Changed
- Documented completion/archive behavior in the README.
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
