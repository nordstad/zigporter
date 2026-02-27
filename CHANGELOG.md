# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Show entity and dashboard summary in the migration wizard between step 5 (Restore entity IDs) and step 6 (Validate). After renaming, the wizard now displays the device's current entity IDs and all Lovelace dashboard cards that reference them — the same inspect-style output as `zigporter inspect`.

## [0.1.3] - 2026-02-27

### Fixed

- fix: skip entity ID rename when target is already occupied by a stale entity (#12)

## [0.1.3] - 2026-02-27

### Fixed

- Skip entity ID rename when the target is already occupied by a stale entity (e.g. an old ZHA leftover), preventing broken dashboards after migration (#12)

### Thanks

- [@mrpuurple](https://github.com/mrpuurple) for reporting stale entities breaking dashboards after migration

## [0.1.2] - 2026-02-27

### Fixed

- fix: skip entity ID renames that HA already applied after Z2M rename (#11)

## [0.1.2] - 2026-02-27

### Fixed

- Skip entity ID renames that HA already applied after Z2M device rename, eliminating a spurious "Entity not found" warning in step 5 of the migration wizard (#11)

### Thanks

- [@mrpuurple](https://github.com/mrpuurple) for reporting the entity ID rename warning

## [0.1.1] - 2026-02-27

### Fixed

- Potential fix for code scanning alert no. 1: Workflow does not contain permissions (#9)
- fix: use XDG config dir (~/.config/zigporter) on all platforms (#10)

## [0.1.0] - 2026-02-26

### Added

- Add streamlined UX: preflight check, auto-export, config dir (#7)

### Changed

- Update uv-build requirement from <0.10.0,>=0.9.26 to >=0.9.26,<0.11.0 (#6)
- Streamlined UX: setup wizard, check command, improved migrate flow (#8)

### Dependencies

- Bump astral-sh/setup-uv from 6 to 7 (#1)
- Bump actions/upload-artifact from 4 to 6 (#5)
- Bump actions/download-artifact from 4 to 7 (#4)
- Bump codecov/codecov-action from 4 to 5 (#3)
- Bump actions/github-script from 7 to 8 (#2)

[Unreleased]: https://github.com/nordstad/zigporter/compare/v0.1.3...HEAD
[0.1.3]: https://github.com/nordstad/zigporter/compare/v0.1.3...v0.1.3
[0.1.3]: https://github.com/nordstad/zigporter/compare/v0.1.2...v0.1.3
[0.1.2]: https://github.com/nordstad/zigporter/compare/v0.1.1...v0.1.2
[0.1.1]: https://github.com/nordstad/zigporter/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/nordstad/zigporter/releases/tag/v0.1.0
