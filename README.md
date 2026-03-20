# frag-demo

**CS2 auto-broadcast replay creator with FRAG-STAT observer integration.**

The app now runs with a Bun/Elysia backend and a React/Vite client, while the
existing Python modules remain the worker layer for demo parsing, sequence
generation, launch, and encoding.

---

## Installation

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
bun install
```

Requirements: Python >= 3.10, Bun >= 1.3, `demoparser2`, `numpy`, `pandas`.
On Windows PowerShell, activate the environment with `.\.venv\Scripts\Activate.ps1`.

---

## Quick Start

```bash
bun run dev
```

Then open `http://127.0.0.1:5000`, configure the FRAG-STAT server connection,
select an event, load a `.dem` file, filter kills, queue highlights, generate
the `{demo_path}.json` actions file, optionally launch CS2/HLAE, and encode the
recorded clips.

---

## Usage

The primary runtime is now the Bun/Elysia app:

```bash
bun run dev
```

The UI supports:

1. Loading a `.dem` file and inspecting the parsed header.
2. Filtering kills by player, weapon, round range, side, and headshot.
3. Queueing specific kills for sequence generation and automated recording.
4. Writing the CS Demo Manager `{demo_path}.json` actions file next to the demo.
5. Launching CS2 via HLAE with automatic plugin install/uninstall.
6. Encoding recorded TGA clips to MP4 and concatenating them into a combined video.
7. Selecting FRAG-STAT events and optionally existing matches before upload.
8. Watching a local replay folder and auto-uploading new demos into FRAG-STAT.

---

## Project Structure

```text
frag-demo/
├── server/
│   ├── index.ts              # Bun/Elysia API + local app backend
│   └── index.test.ts         # Bun server integration tests
├── web/
│   ├── index.html            # Vite entry HTML
│   ├── src/
│   │   ├── App.tsx           # React operator UI
│   │   ├── App.test.tsx      # Frontend integration test
│   │   └── app.css           # Bundled client styling
│   └── test/
│       └── setup.ts          # Vitest setup
├── src/frag_demo/
│   ├── app.py                # Python launcher for Bun (`frag-demo`)
│   ├── runtime.py            # Shared worker/runtime helpers
│   ├── worker.py             # Python worker bridge for the Node server
│   ├── parser/
│   │   └── demo_parser.py    # DemoAnalyzer (demoparser2 wrapper)
│   ├── query/
│   │   └── engine.py         # QueryEngine (filter + NL query)
│   ├── sequences/
│   │   └── builder.py        # SequenceBuilder (JSON actions file)
│   ├── launcher/
│   │   └── cs2.py            # CS2Launcher (HLAE integration)
│   ├── encoder/
│   │   └── ffmpeg.py         # VideoEncoder (ffmpeg wrapper)
└── tests/
    ├── test_encoder.py
    ├── test_launcher.py
    ├── test_parser.py
    ├── test_query.py
    ├── test_runtime.py
    └── test_sequences.py
```

---

## Running Tests

```bash
.venv/bin/python -m pytest
bun run typecheck
bun run test
```

---

## How It Works

1. The Bun/Elysia server owns local UI state, watched folders, FRAG-STAT
   integration, and browser-facing APIs.
2. The Python worker uses `demoparser2` plus the existing Python sequence,
   launcher, and encoder modules for demo-specific operations.
3. Structured kill filtering happens in the Bun server against the loaded kill
   payload.
4. Uploads go directly into FRAG-STAT via `/api/import/demo`, with event-aware
   and optional match-aware linking.
5. The watcher and upload status are exposed back to the local operator UI.
