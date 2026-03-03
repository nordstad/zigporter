# zigporter

Home Assistant device management from the command line — migrate from ZHA to Zigbee2MQTT,
rename entities and devices with full cascade across automations, scripts, and dashboards.

> **Try it before you run it** — [Interactive demo →](interactive-demo.md) walks through a full ZHA → Z2M migration with simulated devices, right in your browser.

## Features

<table>
  <thead>
    <tr><th style="white-space: nowrap">Command</th><th>Description</th></tr>
  </thead>
  <tbody>
    <tr><td style="white-space: nowrap"><a href="guide/migration-wizard/"><code>migrate</code></a></td><td>Interactive wizard: remove from ZHA → factory reset → pair with Z2M → restore names, areas, and entity IDs</td></tr>
    <tr><td style="white-space: nowrap"><a href="guide/rename/#rename-an-entity"><code>rename&#x2011;entity</code></a></td><td>Rename a HA entity ID and cascade the change across automations, scripts, scenes, and all Lovelace dashboards</td></tr>
    <tr><td style="white-space: nowrap"><a href="guide/rename/#rename-a-device"><code>rename&#x2011;device</code></a></td><td>Rename any HA device by name and cascade the change to all its entities and references</td></tr>
    <tr><td style="white-space: nowrap"><code>check</code></td><td>Verify HA and Z2M connectivity before making changes</td></tr>
    <tr><td style="white-space: nowrap"><code>inspect</code></td><td>Show a device's current state across ZHA, Z2M, and the HA registry</td></tr>
    <tr><td style="white-space: nowrap"><code>export</code></td><td>Snapshot your ZHA device inventory to JSON</td></tr>
    <tr><td style="white-space: nowrap"><code>list&#x2011;z2m</code></td><td>List all devices currently paired with Zigbee2MQTT</td></tr>
    <tr><td style="white-space: nowrap"><code>fix&#x2011;device</code></td><td>Post-migration cleanup: remove stale ZHA device entries, delete their entities, and rename any <code>_2</code>/<code>_3</code> suffixed Z2M entities back to their original IDs</td></tr>
    <tr><td style="white-space: nowrap"><code>stale</code></td><td>Scan all integrations for offline devices and interactively remove, annotate, or ignore them</td></tr>
  </tbody>
</table>

## Installation

```bash
uv tool install zigporter
```

## Quick start

### Migrate ZHA → Zigbee2MQTT

```bash
zigporter setup   # configure credentials once
zigporter check   # verify connectivity
zigporter migrate # start the interactive wizard
```

### Rename an entity

```bash
# Preview what would change
zigporter rename-entity light.old_name light.new_name

# Apply the rename
zigporter rename-entity light.old_name light.new_name --apply
```

### Rename a device

```bash
zigporter rename-device "Old Device Name" "New Device Name" --apply
```

### Find offline devices

```bash
zigporter stale
```

---

See [Installation](getting-started/installation.md) and [Configuration](getting-started/configuration.md) to get set up.
