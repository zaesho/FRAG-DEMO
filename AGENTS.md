# FRAG-DEMO

## Project Purpose

CS2 auto-broadcast replay creator. It parses CS2 `.dem` files, filters kill
events, generates a JSON sequences file for the CS Demo Manager CS2 server
plugin, optionally launches CS2 + HLAE to record clips, and encodes recorded
frames into MP4 output.

---

## Architecture

```text
server/
  index.ts             Bun/Elysia API + watcher/upload orchestration
  index.test.ts        Bun server integration tests
web/
  index.html           Vite entry HTML
  src/
    App.tsx            React operator UI
    app.css            Bundled client styling
    App.test.tsx       Frontend integration test
src/frag_demo/
  app.py              Python launcher for the Bun app (console script: frag-demo)
  runtime.py          Shared worker/runtime helpers
  worker.py           Python worker bridge used by the Bun server
  parser/
    demo_parser.py    DemoAnalyzer — wraps demoparser2
  query/
    engine.py         QueryEngine — structured filters + natural-language parsing
  sequences/
    builder.py        SequenceBuilder — generates JSON actions files
  launcher/
    cs2.py            CS2Launcher — HLAE + CS2 launch/helper logic
  encoder/
    ffmpeg.py         VideoEncoder — TGA/WAV -> MP4 + concat
tests/
  test_encoder.py     VideoEncoder unit tests
  test_launcher.py    CS2Launcher unit tests
  test_parser.py      DemoAnalyzer unit tests
  test_query.py       QueryEngine unit tests
  test_runtime.py     Runtime helper unit tests
  test_sequences.py   SequenceBuilder unit tests
```

Data flow:
  DemoAnalyzer -> kills DataFrame -> QueryEngine -> filtered DataFrame
  -> SequenceBuilder -> JSON actions file -> CS2Launcher -> recorded clips
  -> VideoEncoder -> final MP4

---

## Build & Run Commands

```bash
# Create and activate a local virtualenv, then install in editable mode
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
bun install

# Run tests
pytest
bun run test

# Start the local web UI
bun run dev
```

The app runs a Bun server with a React/Vite client at `http://127.0.0.1:5000`.
On Windows PowerShell, activate the virtualenv with `.\.venv\Scripts\Activate.ps1`.

---

## Key Conventions

- Python >= 3.10; use `from __future__ import annotations` in Python modules.
- Type hints on all public methods; use `pathlib.Path` for filesystem paths.
- DataFrame column names follow demoparser2 output: `attacker_name`,
  `attacker_steamid`, `attacker_team_name`, `user_name`, `weapon`,
  `headshot`, `tick`, `total_rounds_played`, `is_warmup_period`.
- Tests should stay self-contained; use synthetic DataFrames and `tmp_path`
  instead of real demo files.
- The sequences JSON format is a list of `{"actions": [...]}` objects where
  each action has `"tick"` (int) and `"cmd"` (str), sorted ascending by tick.
- Kills within 10 seconds of each other are grouped into a single sequence.
- `SequenceBuilder._ticks_from_seconds()` uses `round(tickrate * seconds)`.

---

## Plugin Installation Details

`CS2Launcher.install_plugin()` copies `server.dll` to two locations under the
`csgo/csdm` subtree so both CS2 search paths are satisfied:

| Destination | Why |
|---|---|
| `game/csgo/csdm/bin/server.dll` | Legacy / generic bin search path |
| `game/csgo/csdm/bin/win64/server.dll` | CS2's real `server.dll` path |

`gameinfo.gi` is patched to prepend `Game\tcsgo/csdm` before the existing
`Game\tcsgo` entry so CS2 discovers the plugin directory at startup. A
`.backup` copy is created before patching and restored by `uninstall_plugin()`.

---

## Dependencies

| Package       | Purpose                              |
|---------------|--------------------------------------|
| demoparser2   | Parse CS2 `.dem` files               |
| numpy         | JSON-safe numeric cleanup in the UI  |
| pandas        | DataFrames for kill events           |
| pytest (dev)  | Unit testing                         |
