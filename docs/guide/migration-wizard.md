# Migration wizard

The wizard migrates one device at a time. Run it with:

```bash
zigporter migrate [ZHA_EXPORT]
```

`ZHA_EXPORT` defaults to the most recent `zha-export-*.json` in the current directory.
Check progress without entering the wizard:

```bash
zigporter migrate --status
```

## Steps

Each device passes through five steps:

1. **Remove from ZHA** — triggers removal via the HA WebSocket API and polls the device registry until the device is gone
2. **Reset device** — prompts you to factory-reset the physical device to clear the old pairing
3. **Pair with Z2M** — opens a 120 s permit-join window and polls Z2M every 3 s by IEEE address
4. **Rename** — applies the original ZHA friendly name and area assignment in Z2M and HA
5. **Validate** — polls HA entity states until all entities come online

## State persistence

Progress is written to `zha-migration-state.json` after every transition. Pressing `Ctrl-C` at any point marks the current device `FAILED` and saves — rerun the wizard to retry.

## Flow

```mermaid
flowchart TD
    A([Start]) --> B[Load export + state file]
    B --> C{State file\nexists?}
    C -- yes --> D[Resume — skip MIGRATED devices]
    C -- no --> E[Initialise all devices as PENDING]
    D & E --> F[/Pick a device from the list/]
    F --> G[1 · Remove from ZHA\nConfirm deletion in HA UI\nPoll registry until gone]
    G --> H[2 · Reset physical device\nFactory-reset to clear old pairing]
    H --> I[3 · Pair with Z2M\nEnable permit_join 120 s\nPoll Z2M every 3 s by IEEE]
    I --> J{Device\nfound?}
    J -- no --> K{Retry?}
    K -- yes --> I
    K -- no --> L[Mark FAILED · Save state]
    J -- yes --> M[4 · Rename\nApply ZHA name + area in Z2M + HA]
    M --> N[5 · Validate\nPoll HA entity states until online]
    N --> O{All entities\nonline?}
    O -- yes --> P[Mark MIGRATED · Save state]
    O -- no --> Q[Mark MIGRATED with warning\nCheck HA manually]
    P & Q --> R([Done — run again for next device])
    L --> R
```

## Device state machine

```mermaid
stateDiagram-v2
    [*] --> PENDING
    PENDING --> IN_PROGRESS : wizard started
    IN_PROGRESS --> MIGRATED : all steps passed
    IN_PROGRESS --> FAILED : pairing failed / Ctrl-C
    FAILED --> IN_PROGRESS : retry
```
