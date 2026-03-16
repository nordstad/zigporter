# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]


## [1.3.1] - 2026-03-16

### Fixed

- fix: guard check command against non-TTY crash (#69)

## [1.3.1] - 2026-03-16

### Fixed

- `check` command no longer crashes with `OSError` when run in a non-TTY environment (CI, pipes, IDE terminals) — exits cleanly with code 1 instead (#68)

### Changed

- Homebrew tap added with auto-updating CI for simplified installation
- Documentation updates for Homebrew installation options and reverse migration wizard

## [1.3.0] - 2026-03-15

### Changed

- feat: Z2M to ZHA reverse migration (#66)

## [1.3.0] - 2026-03-15

### Added

- `migrate --direction z2m-to-zha`: full reverse migration wizard (Z2M → ZHA) — removes device from Z2M via MQTT, pairs with ZHA, restores name/area/entity IDs, resolves stale MQTT entity suffix conflicts, validates, optional cascade rename (#66)
- `export-z2m` command: snapshot Z2M devices (names, areas, entities, automation refs) to JSON before reverse migration (#66)

## [1.2.0] - 2026-03-12

### Changed

- chore(deps-dev): bump zensical from 0.0.24 to 0.0.26 (#60)
- chore(deps-dev): bump ruff from 0.15.4 to 0.15.5 (#59)
- feat: machine-readable output and headless modes for AI-agent workflows (#61)

### Fixed

- fix(export): handle missing ZHA integration gracefully (#58)

## [1.2.0] - 2026-03-12

### Added

- `--json` flag on `list-z2m` and `list-devices` for machine-readable output (#61)
- `--json` flag on `inspect` for structured JSON device dependency report (#61)
- Headless mode for `stale` and `fix-device` — pass a device arg to skip the picker; `--action` / `--apply` for fully non-interactive execution (#61)
- `inspect` now supports `--backend z2m` and `--backend all` to inspect non-ZHA devices (#61)
- `rename-entity` and `rename-device` warn when entity IDs appear inside Jinja2 template strings that cannot be patched automatically (#61)

### Fixed

- `inspect`: `--backend z2m` now accepts all valid Z2M MQTT identifier forms; `--json --debug` no longer mixes debug text into stdout; unknown `--backend` values now fail fast with a clear error (#61)
- `export`: handle missing ZHA integration gracefully (#58)


## [1.1.0] - 2026-03-10

### Added

- feat: add list-devices command, /smart-rename skill, and naming convention (#49)

### Fixed

- fix(network-map): show both LQI directions for asymmetric depth-1 links (#52)
- fix(network-map): update docs, handle cancelled backend prompt, isolate config tests (#57)

## [1.1.0] - 2026-03-11

### Added

- `list-devices` command: list all HA devices with optional `--integration` filter (#49)
- `/smart-rename` skill: AI-assisted naming convention audit and bulk rename workflow (#49)
- `naming_convention.py`: pluggable naming convention validation (#49)

### Fixed

- fix(network-map): show both LQI directions for asymmetric depth-1 links — tree output appends `(up: N)`, SVG edges show `↓N ↑N` for hop-1 devices (#52)
- fix(network-map): handle cancelled backend prompt instead of silently defaulting to Z2M (#57)
- fix(tests): isolate config tests from host `~/.config/zigporter/.env` (#57)
- docs(network-map): update guide for PR #52 — add `(up: N)` section, document `↓N ↑N` SVG labels, fix `--svg` → `--output`, rename `(coord: N)` → `(direct coord: N)` (#57)

## [1.0.3] - 2026-03-09

### Fixed

- fix(network-map): skip coordinator in path_min_lqi iterative walk (#51)

## [1.0.3] - 2026-03-09

### Fixed

- fix(network-map): SVG badges show `0` for all non-coordinator devices — iterative `_compute_path_min_lqi` walker included the coordinator (absent from `lqi_map`) and clamped every path-min LQI to `0` via the `get(node, 0)` default (#51)
- fix(network-map): label overlaps and lopsided layout in SVG — collision resolver used `min_dist = 2r + COLLISION_GAP` (128–140 px) while label pills require ≥ 142 px; also compresses subtree weights with `ceil(√leaves)` so a large hub no longer dominates the angular layout (#51)

## [1.0.2] - 2026-03-09

### Fixed

- fix(network-map): prevent parent_map cycles in BFS re-placement (#50)

## [1.0.2] - 2026-03-09

### Fixed

- fix(network-map): prevent `RecursionError` crash in SVG export caused by cycles in `parent_map` — adds `_is_ancestor` guard in BFS re-placement loop and converts recursive `_compute_path_min_lqi` to iterative (#50)

## [1.0.1] - 2026-03-09

### Fixed

- Fix/ws ssl incompatible uri (#48)

## [1.0.1] - 2026-03-08

### Fixed

- fix(ha-client): don't pass ssl context for ws:// WebSocket connections — fixes `network-map` and all WebSocket commands on HTTP HA instances with `HA_VERIFY_SSL=false` (#48)

## [1.0.0] - 2026-03-08

### Added

- feat(network-map): add ZHA backend with --backend flag (#47)

### Fixed

- fix(rename-device): auto-accept suggested IDs for odd entities when --apply is set (#46)

## [1.0.0] - 2026-03-08

### Added

- `network-map`: ZHA backend support via `--backend auto|z2m|zha`.  Auto-detect picks the
  available integration and prompts when both Z2M and ZHA are present.  Multi-hop routing
  paths are read from ZHA device neighbor tables; falls back to a flat single-hop view with
  per-device LQI when no topology scan data is available yet.  `--backend zha` validates
  ZHA reachability upfront and prints an actionable error if ZHA is not installed.

### Changed

- docs: expand Confirmed Working section — split into Platform/Software tables, add OS, HA install type, and Python 3.12/3.13/3.14 rows
- docs: add compatibility report issue template so users can submit their environment details


## [0.9.0] - 2026-03-06

### Changed

- feat(migrate): interactive area picker in step 4 (#43)
- feat(network-map): content-aware ring radii + distinct hop colors in SVG (#45)

### Fixed

- fix(migrate): detect interview completion via MQTT bridge events (#44)

## [0.9.0] - 2026-03-06

### Added

- `network-map`: content-aware ring radii scale automatically to fit all devices
  without label overlap; each hop ring uses a distinct colour (#45)
- `migrate` wizard: interactive area picker in step 4 — choose from existing HA
  areas instead of typing a name (#43)

### Fixed

- `migrate`: detect Z2M interview completion via MQTT bridge events instead of
  polling `get_devices()`, which never returned `interview_completed` in Z2M 2.x (#44)
- `network-map`: increase `MIN_RING_GAP`, `COLLISION_GAP`, and `COLLISION_ITERS`
  to eliminate label overlap in dense networks

## [0.8.0] - 2026-03-06

### Added

- feat(network-map): add network-map command with polished SVG export (#42)

## [0.8.0] - 2026-03-06

### Added

- `network-map` command: renders Zigbee mesh topology as a Rich tree or table
  with LQI colour-coding (WEAK/CRITICAL annotations); `--output` flag exports a
  polished radial SVG diagram with glow filters, in-circle path-min LQI badges,
  and pill label backgrounds (#42)
- Z2M MQTT fallback for `network-map`: when Z2M 2.x returns 404 on the REST
  endpoint, the client subscribes to the MQTT response topic and publishes the
  request via HA WebSocket `call_service` (#42)
- Docs: guide pages for `fix-device` and utility commands; all commands now
  linked from the docs index

## [0.7.0] - 2026-03-05

### Added

- feat(migrate): add optional post-migration rename step (step 8) (#41)

## [0.7.0] - 2026-03-05

### Added

- `stale` command: **Suppress** action permanently hides ghost entries or false positives
  from all future runs; the device vanishes from the picker immediately
- `stale` command: auto-prune resolved entries — devices that came back online are removed
  from `stale.json` on each run with a brief log line
- `migrate` wizard: optional step 8 — rename the device in Home Assistant immediately after
  migration, before leaving the wizard (#41)
- Demo: add `rename-entity` and `setup` interactive scenarios; sort command cards
  alphabetically

### Fixed

- Demo: fix mobile layout issues — button spacing and card height on narrow viewports

## [0.6.1] - 2026-03-05

### Fixed

- fix: handle YAML_MODE sentinel in inspect dashboard scan (#40)

## [0.6.1] - 2026-03-05

### Fixed

- Handle `YAML_MODE` sentinel in inspect dashboard scan — fixes `AttributeError` crash in migration step 6 and the `inspect` command when a dashboard is in YAML mode (#40)

## [0.6.0] - 2026-03-04

### Changed

- chore(deps): bump python-dotenv from 1.2.1 to 1.2.2 (#39)
- chore(deps-dev): bump zensical from 0.0.23 to 0.0.24 (#38)
- chore(deps-dev): bump ruff from 0.15.2 to 0.15.4 (#37)
- chore(deps): bump actions/upload-pages-artifact from 3 to 4 (#36)
- chore(deps): bump actions/upload-artifact from 4 to 7 (#35)
- chore(deps): bump actions/download-artifact from 4 to 8 (#34)
- chore(deps): bump actions/github-script from 7 to 8 (#33)
- chore(deps): bump actions/checkout from 4 to 6 (#32)
- feat(rename): interactive wizard mode for rename-device and rename-entity (#31)

## [0.6.0] - 2026-03-04

### Added

- `rename-device` accepts `--filter=zigbee` to restrict the interactive picker to ZHA and Zigbee2MQTT devices only.

### Changed

- `rename-device` new-name prompt now starts empty instead of pre-filling the current name.

### Refactored

- `ha_client`: `YAML_MODE` sentinel changed from a plain `dict` to a typed `_YamlMode` instance for
  safer identity checks. All callers must use `is_yaml_mode()` — direct equality comparisons (`== YAML_MODE`)
  are no longer meaningful. `is_yaml_mode` is a public export.

## [0.5.1] - 2026-03-03

### Fixed

- Use absolute URL for logo so it renders correctly on PyPI
- Fix Documentation link in package metadata to point to the MkDocs site

## [0.5.0] - 2026-03-03

### Added

- `stale` command: scan all integrations for offline devices and interactively
  remove, annotate, or ignore them; state persists across runs in
  `~/.config/zigporter/stale.json`

### Fixed

- Handle `unknown_command` fallback for ZHA device removal in stale command
- Reduce false positives: exclude HA core device, `entry_type=service` devices,
  and hub/gateway devices with responsive children
- Newly-seen offline devices now appear under the "New" group in the picker
  instead of being mixed into "Stale"

## [0.4.0] - 2026-03-03

### Fixed

- fix: handle lovelace strategy dashboards and save failures gracefully (#28)

## [0.4.0] - 2026-03-03

### Fixed

- Handle Lovelace strategy dashboards and save failures gracefully (#28)

### Changed

- Clarify Z2M is optional: `Z2M_URL` is only required for `migrate` and `list-z2m`
- Remove 'Try it' section from README; use GitHub NOTE alert for early development notice
- Add Google Analytics to the docs site

## [0.3.0] - 2026-03-02

### Added

- docs: add interactive migrate wizard playground (#26)

### Changed

- refactor: safety, parser consistency, and code quality improvements (#25)
- feat: cascade rename-entity into Helper / Group config entries (#27)

## [0.3.0] - 2026-03-02

### Added

- `rename-entity` now cascades changes into HA Helper / Group config entries,
  keeping group members and template helpers in sync after a rename (#27)
- Interactive migrate wizard playground added to the site (#26)

### Changed

- Safety, parser consistency, and code quality improvements (#25)

## [0.2.1] - 2026-03-01

### Fixed

- fix: reload Z2M integration after rename-device to restore MQTT subscriptions (#24)

## [0.2.1] - 2026-03-01

### Fixed

- Reload the Zigbee2MQTT integration after `rename-device` so HA re-subscribes to the
  new MQTT topic, fixing entities showing as Unknown after a rename (#24)

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

[Unreleased]: https://github.com/nordstad/zigporter/compare/v1.3.1...HEAD
[1.3.1]: https://github.com/nordstad/zigporter/compare/v1.3.1...v1.3.1
[1.3.1]: https://github.com/nordstad/zigporter/compare/v1.3.0...v1.3.1
[1.3.0]: https://github.com/nordstad/zigporter/compare/v1.2.0...v1.3.0
[1.2.0]: https://github.com/nordstad/zigporter/compare/v1.2.0...v1.2.0
[1.2.0]: https://github.com/nordstad/zigporter/compare/v1.1.0...v1.2.0
[1.1.0]: https://github.com/nordstad/zigporter/compare/v1.1.0...v1.1.0
[1.1.0]: https://github.com/nordstad/zigporter/compare/v1.0.3...v1.1.0
[1.0.3]: https://github.com/nordstad/zigporter/compare/v1.0.3...v1.0.3
[1.0.3]: https://github.com/nordstad/zigporter/compare/v1.0.2...v1.0.3
[1.0.2]: https://github.com/nordstad/zigporter/compare/v1.0.2...v1.0.2
[1.0.2]: https://github.com/nordstad/zigporter/compare/v1.0.1...v1.0.2
[1.0.1]: https://github.com/nordstad/zigporter/compare/v1.0.1...v1.0.1
[1.0.1]: https://github.com/nordstad/zigporter/compare/v1.0.0...v1.0.1
[1.0.0]: https://github.com/nordstad/zigporter/compare/v0.9.0...v1.0.0
[0.9.0]: https://github.com/nordstad/zigporter/compare/v0.9.0...v0.9.0
[0.9.0]: https://github.com/nordstad/zigporter/compare/v0.8.0...v0.9.0
[0.8.0]: https://github.com/nordstad/zigporter/compare/v0.8.0...v0.8.0
[0.8.0]: https://github.com/nordstad/zigporter/compare/v0.7.0...v0.8.0
[0.7.0]: https://github.com/nordstad/zigporter/compare/v0.7.0...v0.7.0
[0.7.0]: https://github.com/nordstad/zigporter/compare/v0.6.1...v0.7.0
[0.6.1]: https://github.com/nordstad/zigporter/compare/v0.6.0...v0.6.1
[0.6.0]: https://github.com/nordstad/zigporter/compare/v0.5.1...v0.6.0
[0.5.1]: https://github.com/nordstad/zigporter/compare/v0.5.0...v0.5.1
[0.5.0]: https://github.com/nordstad/zigporter/compare/v0.4.0...v0.5.0
[0.4.0]: https://github.com/nordstad/zigporter/compare/v0.4.0...v0.4.0
[0.4.0]: https://github.com/nordstad/zigporter/compare/v0.3.0...v0.4.0
[0.3.0]: https://github.com/nordstad/zigporter/compare/v0.2.1...v0.3.0
[0.2.1]: https://github.com/nordstad/zigporter/compare/v0.2.1...v0.2.1
[0.2.1]: https://github.com/nordstad/zigporter/compare/v0.2.0...v0.2.1
[0.2.0]: https://github.com/nordstad/zigporter/compare/v0.1.5...v0.2.0
[0.1.5]: https://github.com/nordstad/zigporter/compare/v0.1.5...v0.1.5
[0.1.5]: https://github.com/nordstad/zigporter/compare/v0.1.4...v0.1.5
[0.1.4]: https://github.com/nordstad/zigporter/compare/v0.1.4...v0.1.4
[0.1.3]: https://github.com/nordstad/zigporter/compare/v0.1.3...v0.1.3
[0.1.3]: https://github.com/nordstad/zigporter/compare/v0.1.2...v0.1.3
[0.1.2]: https://github.com/nordstad/zigporter/compare/v0.1.1...v0.1.2
[0.1.1]: https://github.com/nordstad/zigporter/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/nordstad/zigporter/releases/tag/v0.1.0
