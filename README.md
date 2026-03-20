# frag-demo

**CS2 auto-broadcast replay creator.**

Parse a CS2 demo, filter kills, generate the CS Demo Manager `.json` actions
file, optionally launch CS2 + HLAE to record the clips, and encode the result
to MP4.

---

## Installation

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

Requirements: Python >= 3.10, `demoparser2`, `flask`, `numpy`, `pandas`.
On Windows PowerShell, activate the environment with `.\.venv\Scripts\Activate.ps1`.

---

## Quick Start

```bash
# Start the local web UI
frag-demo
```

Then open `http://127.0.0.1:5000`, load a `.dem` file, filter kills, queue the
ones you want, generate the `{demo_path}.json` actions file, optionally launch
CS2/HLAE, and encode the recorded clips.

---

## Usage

The current repo entrypoint is the Flask-based desktop web UI:

```bash
frag-demo
```

The UI supports:

1. Loading a `.dem` file and inspecting the parsed header.
2. Filtering kills by player, weapon, round, side, and headshot.
3. Queueing specific kills for sequence generation and automated recording.
4. Writing the CS Demo Manager `{demo_path}.json` actions file next to the demo.
5. Launching CS2 via HLAE with automatic plugin install/uninstall.
6. Encoding recorded TGA clips to MP4 and concatenating them into a combined video.

---

## Project Structure

```text
frag-demo/
├── src/frag_demo/
│   ├── app.py                 # Flask web UI entry point
│   ├── parser/
│   │   └── demo_parser.py     # DemoAnalyzer (demoparser2 wrapper)
│   ├── query/
│   │   └── engine.py          # QueryEngine (filter + NL query)
│   ├── sequences/
│   │   └── builder.py         # SequenceBuilder (JSON actions file)
│   ├── launcher/
│   │   └── cs2.py             # CS2Launcher (HLAE integration)
│   ├── encoder/
│   │   └── ffmpeg.py          # VideoEncoder (ffmpeg wrapper)
│   ├── static/
│   │   ├── app.js             # Web UI behavior
│   │   └── style.css          # Web UI styling
│   └── templates/
│       └── index.html         # Web UI HTML
└── tests/
    ├── test_app.py
    ├── test_encoder.py
    ├── test_launcher.py
    ├── test_parser.py
    ├── test_query.py
    └── test_sequences.py
```

---

## Running Tests

```bash
. .venv/bin/activate
pytest
```

---

## How It Works

1. `DemoAnalyzer` uses `demoparser2` to extract `player_death` events from the
   `.dem` file into a pandas DataFrame.
2. `QueryEngine` filters the kill list by player, weapon, round, side, and
   headshot semantics.
3. `SequenceBuilder` groups kills within 10 seconds, adds padding, and emits the
   tick-keyed JSON actions consumed by the CS Demo Manager plugin.
4. `CS2Launcher` installs the plugin, starts CS2 via HLAE, and removes the
   plugin afterward.
5. `VideoEncoder` wraps ffmpeg to encode recorded TGA frames and concatenate the
   generated MP4 clips.
