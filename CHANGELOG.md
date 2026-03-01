# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.2.0] - 2026-03-01

### Added

- `rename-entity` and `rename-device` commands: cascade entity ID changes across HA config files, dashboards, and automations (#19)
- `fix-device` command: post-migration cleanup for stale ZHA registry entries — renames suffixed entities (e.g. `sensor.light_2`) back to their originals (#23)
- `rename-device` now syncs the Z2M friendly name in addition to updating HA entity IDs (#22)

## [0.1.5] - 2026-02-27

### Fixed

- fix: detect wrong device joining Z2M, force-continue at timeout, fix 7-step wizard (#14)

## [0.1.5] - 2026-02-27

### Fixed

- Detect when the wrong device joins Z2M during pairing and warn immediately with the interloper's IEEE address (#14)
- Replace binary "Retry pairing?" timeout prompt with a 3-choice menu: Retry, Force continue (when device is visibly in Z2M but auto-detection failed), and Mark as failed (#14)
- Broaden `get_devices()` fallback to catch `httpx.HTTPStatusError` and `httpx.RequestError` in addition to `RuntimeError`, preventing transient HTTP errors from silently aborting the polling loop (#14)
- Fix wizard step count: `WIZARD_STEPS` updated to 7, "Review entities & dashboards" registered as step 6, Validate renumbered to step 7 — closing the gap left by PR #13 (#14)


## [0.1.4] - 2026-02-27

### Changed

- feat: show entity and dashboard summary in migrate wizard before validate (#13)

## [0.1.4] - 2026-02-27

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

[Unreleased]: https://github.com/nordstad/zigporter/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/nordstad/zigporter/compare/v0.1.5...v0.2.0
[0.1.5]: https://github.com/nordstad/zigporter/compare/v0.1.5...v0.1.5
[0.1.5]: https://github.com/nordstad/zigporter/compare/v0.1.4...v0.1.5
[0.1.4]: https://github.com/nordstad/zigporter/compare/v0.1.4...v0.1.4
[0.1.3]: https://github.com/nordstad/zigporter/compare/v0.1.3...v0.1.3
[0.1.3]: https://github.com/nordstad/zigporter/compare/v0.1.2...v0.1.3
[0.1.2]: https://github.com/nordstad/zigporter/compare/v0.1.1...v0.1.2
[0.1.1]: https://github.com/nordstad/zigporter/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/nordstad/zigporter/releases/tag/v0.1.0
