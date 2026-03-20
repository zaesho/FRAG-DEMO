# FRAG-DEMO

## Project Purpose

CS2 auto-broadcast replay creator. Parses CS2 `.dem` files, filters kill
events with a natural-language query, generates a JSON sequences file for the
CS Demo Manager CS2 server plugin, and optionally drives CS2 + HLAE to record
video clips automatically.

---

## Architecture

```
src/frag_demo/
  cli.py              Click CLI (entry point: frag-demo)
  parser/
    demo_parser.py    DemoAnalyzer — wraps demoparser2
  query/
    engine.py         QueryEngine — filter & NL query
  sequences/
    builder.py        SequenceBuilder — generates JSON actions file
  launcher/
    cs2.py            CS2Launcher — HLAE + CS2 launch helper
  encoder/
    ffmpeg.py         VideoEncoder — ffmpeg TGA→MP4 + concat
tests/
  test_query.py       QueryEngine unit tests (no .dem file needed)
  test_sequences.py   SequenceBuilder unit tests (no .dem file needed)
```

Data flow:
  DemoAnalyzer → kills DataFrame → QueryEngine → filtered DataFrame
  → SequenceBuilder → JSON actions file → CS2Launcher → records clips
  → VideoEncoder → final MP4

---

## Build & Run Commands

```bash
# Install in editable mode with dev tools
pip install -e ".[dev]"

# Run tests
pytest

# CLI help
frag-demo --help
frag-demo record --help

# Show demo header
frag-demo info match.dem

# Dry-run query (no file written)
frag-demo record match.dem zywoo awp --dry-run

# Generate sequences JSON
frag-demo record match.dem s1mple headshot
```

---

## Key Conventions

- Python ≥ 3.10; use `from __future__ import annotations` for PEP 604 unions.
- Type hints on all public methods; use `pathlib.Path` for file paths.
- DataFrame column names follow demoparser2 output: `attacker_name`,
  `attacker_steamid`, `attacker_team_name`, `user_name`, `weapon`,
  `headshot`, `tick`, `total_rounds_played`, `is_warmup_period`.
- Tests are fully self-contained (no `.dem` file required); use synthetic
  DataFrames and `tmp_path` for file I/O tests.
- The sequences JSON format is a list of `{"actions": [...]}` objects where
  each action has `"tick"` (int) and `"cmd"` (str) keys, sorted ascending
  by tick.
- Kills within 10 seconds of each other are grouped into a single sequence.
- `SequenceBuilder._ticks_from_seconds()` uses `round(tickrate * seconds)`.

---

## Plugin Installation Details

`CS2Launcher.install_plugin()` copies `server.dll` to **two** locations inside
the `csgo/csdm` subtree so that CS2's different search paths are both satisfied:

| Destination | Why |
|---|---|
| `game/csgo/csdm/bin/server.dll` | Legacy / generic bin search path |
| `game/csgo/csdm/bin/win64/server.dll` | CS2's real `server.dll` path (mirrors `game/bin/win64/`) |

`gameinfo.gi` is patched to prepend `Game\tcsgo/csdm` before the existing
`Game\tcsgo` entry so CS2 discovers the plugin directory at startup.  A
`.backup` copy is created before patching and restored by `uninstall_plugin()`.

---

## Dependencies

| Package       | Purpose                       |
|---------------|-------------------------------|
| demoparser2   | Parse CS2 .dem files          |
| click         | CLI framework                 |
| pandas        | DataFrames for kill events    |
| pytest (dev)  | Unit testing                  |
