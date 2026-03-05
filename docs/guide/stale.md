# Find and clean up offline devices

Scan Home Assistant for devices whose entities are all `unavailable` or `unknown` and
interactively decide what to do with each one.

```bash
zigporter stale
```

## How detection works

A device is considered offline when **all of its enabled entities** report `unavailable`
or `unknown`. Devices with no entities at all (ghost entries) are also included.

Two categories are automatically excluded to avoid false positives:

- **Service entries** — integration-level entries such as hubs and coordinators whose own
  entities (firmware update sensors, identify buttons) may report unavailable even when the
  devices they manage are working fine.
- **Active hubs** — gateway devices (e.g. Plejd GWY-01, UniFi controller) that have at
  least one non-offline child device. If any child is responsive, the hub is excluded.

## The picker

Offline devices are displayed grouped as **New → Stale → Ignored**, sorted by area and
name within each group. Decisions persist across runs in
`~/.config/zigporter/stale.json`.

Each run automatically **prunes resolved entries**: if a device from a previous run is no
longer offline (came back online or was deleted from HA), its state entry is removed from
the file and a brief `(Pruned N resolved entries from stale.json)` note is printed.

Devices marked **Suppressed** are filtered out of the picker entirely and never shown again.
A dim count at the top of each loop indicates how many suppressed devices are hidden. To
un-suppress, select the device from within HA and use **Clear status**.

## Actions

For each device you can:

| Action | What it does |
|---|---|
| **Remove** | Deletes the device from the HA registry. For ZHA devices, falls back to `zha.remove` if the standard registry command is unsupported. Confirms the device is gone before reporting success. |
| **Mark as stale** | Adds an optional note and moves the device into the Stale group for follow-up. |
| **Ignore** | Moves the device into the Ignored group — suppresses it from the New list on future runs. Useful for devices you know are intentionally offline (e.g. seasonal equipment). |
| **Suppress** | Permanently hides the device from all future runs. Use this for ghost entries or known false positives that you never want to see again. The device vanishes from the picker immediately. |
| **Clear status** | Resets a Stale, Ignored, or Suppressed device back to New (and makes it visible again if it was suppressed). |

## Notes

- Removing a device **cannot be undone** from the CLI. Re-pairing or re-adding the
  integration is required to bring it back.
- If a device is actively managed by an integration, HA may re-register it immediately
  after removal. In that case, remove it from within the integration's own settings instead.
- Jinja2 template expressions and YAML-mode dashboards that reference the device's entity
  IDs are not updated automatically — review them after removing a device.
