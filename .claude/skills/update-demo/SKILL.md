---
name: update-demo
description: >
  Audit and update the interactive demo at docs/demo/index.html to match the current CLI.
  Run this after adding, changing, or removing a CLI command to keep the demo in sync.
---

## Steps

**Note:** The demo source is `docs/demo/index.html` — tracked by git as part of the MkDocs
source tree. It is edited directly in place. `site/` is the build output and is gitignored.

1. **Find what changed in the CLI since the last demo update**

```bash
git log --oneline -- docs/demo/index.html | head -1   # last demo commit SHA
# If the demo has never been committed, use: git rev-list --max-parents=0 HEAD
git log <demo-sha>..HEAD --oneline -- src/zigporter/main.py src/zigporter/commands/
```

2. **Audit commands vs demo scenarios**
   - Read `src/zigporter/main.py` — collect every registered Typer command name
   - Read `docs/demo/index.html` — extract the `DEMOS` array (keys: `check`, `list-z2m`, `migrate`,
     `rename`, `fix`, `inspect`, plus any others)
   - Identify **gaps**: CLI commands that exist but have no demo entry (and would benefit from one)
   - Identify **stale entries**: demo scenarios referencing commands that have been removed

3. **For each command with changed output — update its demo script**
   - Read the relevant `src/zigporter/commands/<file>.py` to understand new steps, flags, or output
   - Edit the matching `DEMO_<NAME>` constant in the HTML:
     - Update step counters (e.g., `[n/7]` headers) if the wizard gained or lost steps
     - Adjust table columns/widths to reflect new Rich output
     - Update command-line invocations if flag names changed
     - Keep timing values consistent with existing entries (~300–900 ms per line)
   - **Mobile width constraint:** the mobile breakpoint uses `font-size: 8px` which fits ~78 chars
     on a 390px viewport. Keep table widths ≤ 78 chars; the widest table (`list-z2m`) is already
     at that limit. Wider content will overflow on mobile.

4. **For new commands — add a demo entry**
   - Write a new `DEMO_<NAME>` constant following the existing format:
     - First item: prompt line `$ zigporter <command>` (type `0` or plain text)
     - Use the existing HTML helper functions: `g()` (green), `c()` (cyan), `y()` (yellow),
       `r()` (red), `ok()` (green ✓), `bd()` (bold), `d()` (dim), `rule()`, `qline()`
     - Spinner lines use type `1`; spinner-resolve lines use type `2`
   - Add a corresponding entry to the `DEMOS` array:
     ```js
     { key: "<name>", label: "<Label>", desc: "<One-line description>", script: DEMO_<NAME> }
     ```

5. **For removed commands — remove their demo entry**
   - Delete the `DEMO_<NAME>` constant
   - Remove the matching entry from the `DEMOS` array

6. **Verify the HTML is self-consistent using playwright-cli**
   - Start a local server in the background:
     ```bash
     python3 -m http.server 8765 --directory docs/demo > /tmp/demo-server.log 2>&1 &
     SERVER_PID=$!
     ```
   - Open the browser, resize to mobile, click each new/changed card, and check the snapshot:
     ```bash
     playwright-cli open http://localhost:8765/index.html
     playwright-cli resize 390 844
     playwright-cli snapshot
     # click the card under test (use ref from snapshot)
     playwright-cli click <ref>
     # wait for animation to finish, then snapshot to confirm all rows rendered
     playwright-cli snapshot
     playwright-cli console   # check for JS errors
     # repeat at desktop size
     playwright-cli resize 1440 900
     playwright-cli snapshot
     playwright-cli close
     ```
   - Confirm:
     - All tested cards play to completion without stalling
     - Tables render fully without clipping on mobile (all rows visible, no overflow)
     - No JS console errors
   - Kill the server when done:
     ```bash
     kill $SERVER_PID
     ```

7. **Show a summary diff** of what changed in `docs/demo/index.html` and confirm with the user
   before finishing.
