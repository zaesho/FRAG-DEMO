# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

CS2 auto-broadcast replay creator. Parses CS2 `.dem` files, lets you select kills via a browser-based UI, generates JSON sequences for the CS Demo Manager server plugin, and optionally drives CS2 + HLAE to record and encode video clips.

## Build & Run

```bash
pip install -e ".[dev]"       # Install in editable mode with pytest
bun install                   # Install Bun/React toolchain
pytest                         # Run all tests
bun run test                   # Run frontend + server integration tests
pytest tests/test_query.py     # Run a single test file
pytest -k "test_name"          # Run a single test by name
bun run dev                    # Launch the Bun + React app on localhost:5000
```

Entry point: `frag-demo` now launches the Bun runtime from the Python package, and `bun run dev` is the primary development entrypoint.

## Architecture & Data Flow

```
React client (localhost:5000)
  ├── GET/POST /api/*      → Bun/Elysia server
  ├── POST /api/load       → Python worker → DemoAnalyzer
  ├── POST /api/kills      → Bun-side filtering
  ├── POST /api/record     → Python worker → SequenceBuilder / CS2Launcher
  ├── POST /api/encode     → Python worker → VideoEncoder
  └── POST /api/upload     → FRAG-STAT import API
```

Core modules:

- **`server/index.ts`** — Bun/Elysia backend. Owns UI state, watcher lifecycle, FRAG-STAT integration, and the browser-facing API.
- **`web/src/App.tsx`** — React operator UI.
- **`src/frag_demo/app.py`** — Thin Python launcher for the Bun app (`frag-demo` console script).
- **`src/frag_demo/runtime.py`** — Shared helper functions used by the Python worker.
- **`src/frag_demo/worker.py`** — Python subprocess bridge for demo parsing, sequence generation, clip cleanup, and encoding.
- **`parser/demo_parser.py`** — `DemoAnalyzer` wraps demoparser2. `get_player_slots()` probes ticks 128→64→1 for entity IDs, falling back to kill events if tick data is unavailable.
- **`query/engine.py`** — `QueryEngine` provides structured `query()` and free-form `parse_natural_query()`. The web UI uses `query()` directly with dropdown values. Weapon aliases resolve multi-weapon matches with pipe-separated format (e.g., `m4` → `m4a1|m4a1_silencer`).
- **`sequences/builder.py`** — `SequenceBuilder` groups kills within 10 seconds into single sequences. Generates tick-keyed console commands with a setup/record/teardown structure. Uses Unix-style paths in MIRV commands even on Windows. Spectate command fallback: `spec_player {slot}` → `spec_lock_to_accountid` (32-bit from 64-bit SteamID via `& 0xFFFFFFFF`) → `spec_mode 1`. All ticks clamped to minimum 64.
- **`launcher/cs2.py`** — `CS2Launcher` discovers HLAE/CS2 paths (registry, PATH, hardcoded candidates), manages plugin install/uninstall lifecycle with guaranteed cleanup in `finally`. Copies server.dll to both `bin/` and `bin/win64/` to satisfy CS2's dual search paths.
- **`encoder/ffmpeg.py`** — `VideoEncoder` wraps ffmpeg for TGA→MP4 encoding and clip concatenation.

## Key Conventions

- Python >= 3.10; use `from __future__ import annotations` for PEP 604 unions.
- Type hints on public methods; use `pathlib.Path` for file paths.
- DataFrame columns follow demoparser2 naming: `attacker_name`, `attacker_steamid`, `attacker_team_name`, `user_name`, `weapon`, `headshot`, `tick`, `total_rounds_played`, `is_warmup_period`.
- Tests are fully self-contained — no `.dem` file required. Use synthetic DataFrames and `tmp_path` for file I/O.
- Sequences JSON format: list of `{"actions": [{"tick": int, "cmd": str}, ...]}` sorted ascending by tick. Output file is `{demo_path}.json`.
- Tick calculations use `round(tickrate * seconds)`.
- Windows-specific code (registry, tasklist polling, gameinfo.gi patching) — not cross-platform.

## Helper Scripts (`scripts/`)

- `download_demos.py` — HLTV demo downloader using Playwright + Stealth (bypasses Cloudflare).
- `test_parse.py` / `dry_run_test.py` / `inspect_json.py` — Quick validation and inspection utilities.
