# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]


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

[Unreleased]: https://github.com/nordstad/zigporter/compare/v0.1.2...HEAD
[0.1.2]: https://github.com/nordstad/zigporter/compare/v0.1.1...v0.1.2
[0.1.1]: https://github.com/nordstad/zigporter/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/nordstad/zigporter/releases/tag/v0.1.0
