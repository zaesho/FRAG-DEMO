# frag-demo

**CS2 auto-broadcast replay creator.**

Parse a CS2 demo, filter kills with a natural-language query, generate a
sequences JSON file for the CS Demo Manager CS2 server plugin, and
optionally launch CS2 + HLAE to record the clips automatically.

---

## Installation

```bash
pip install -e ".[dev]"
```

Requirements: Python >= 3.10, `demoparser2`, `click`, `pandas`.

---

## Quick Start

```bash
# Show demo metadata
frag-demo info match.dem

# List all AWP kills by ZywOo
frag-demo kills match.dem -p zywoo -w awp

# Dry-run: preview matched kills without generating any output
frag-demo record match.dem zywoo awp kills --dry-run

# Generate sequences JSON for recording
frag-demo record match.dem s1mple headshot

# Custom padding and framerate
frag-demo record match.dem niko deagle --before 5 --after 3 --framerate 120
```

---

## Commands

### `frag-demo info <demo_path>`

Prints the demo header (map name, tickrate, server, etc.).

### `frag-demo kills <demo_path> [OPTIONS]`

Lists all kills (or filtered subset) in a formatted kill feed.

| Option | Description |
|---|---|
| `-p / --player` | Case-insensitive partial player name filter |
| `-w / --weapon` | Case-insensitive partial weapon name filter |
| `-hs / --headshot` | Show only headshot kills |
| `-r / --round N` | Filter by round number |

### `frag-demo record <demo_path> [QUERY...] [OPTIONS]`

The main command. Parses the demo, applies the natural-language query,
and writes a `{demo_path}.json` sequences file.

**Natural-language query examples:**

```
zywoo awp kills
s1mple headshot
niko deagle round 5
ct ak hs
```

Recognised tokens:

| Token | Meaning |
|---|---|
| Player name | Partial match on attacker name |
| Weapon name | Any CS2 weapon or alias (see below) |
| `headshot` / `hs` | Headshot-only filter |
| `round N` | Filter by round number |
| `ct` / `t` | Filter by side |
| `kills` / `with` / `using` | Ignored filler words |

**Weapon aliases:**

| Alias | Resolves to |
|---|---|
| `ak` | `ak47` |
| `deag` | `deagle` |
| `m4` | `m4a1`, `m4a1_silencer` |
| `scout` | `ssg08` |
| `auto` | `g3sg1`, `scar20` |
| `usp` | `usp_silencer` |

| Option | Default | Description |
|---|---|---|
| `-o / --output` | demo directory | Output directory for clip frames |
| `--before` | `3.0` | Seconds before kill to start recording |
| `--after` | `2.0` | Seconds after kill to stop recording |
| `--framerate` | `60` | Recording frame rate |
| `--dry-run` | off | Preview only, skip JSON generation |

---

## Project Structure

```
frag-demo/
├── src/frag_demo/
│   ├── cli.py            # Click CLI entry point
│   ├── parser/
│   │   └── demo_parser.py    # DemoAnalyzer (demoparser2 wrapper)
│   ├── query/
│   │   └── engine.py         # QueryEngine (filter + NL query)
│   ├── sequences/
│   │   └── builder.py        # SequenceBuilder (JSON actions file)
│   ├── launcher/
│   │   └── cs2.py            # CS2Launcher (HLAE integration)
│   └── encoder/
│       └── ffmpeg.py         # VideoEncoder (ffmpeg wrapper)
└── tests/
    ├── test_query.py
    └── test_sequences.py
```

---

## Running Tests

```bash
pytest
```

---

## How It Works

1. **Parse** — `DemoAnalyzer` uses `demoparser2` to extract `player_death`
   events from the `.dem` file into a pandas DataFrame.

2. **Query** — `QueryEngine.parse_natural_query()` tokenises the query string,
   recognises weapon names/aliases, keywords (`hs`, `round N`, `ct`/`t`), and
   treats the remainder as a player name fragment.

3. **Build sequences** — `SequenceBuilder` groups kills within 10 seconds of
   each other into a single sequence, calculates start/end ticks with
   configurable padding, and emits a list of tick-keyed console commands.

4. **Write JSON** — The sequences list is written to `{demo_path}.json`,
   ready to be loaded by the CS Demo Manager CS2 server plugin.

5. **Launch** (optional) — `CS2Launcher` builds the HLAE `-customLoader`
   command line and can start CS2 for automated recording.

6. **Encode** (optional) — `VideoEncoder` wraps ffmpeg to encode the TGA
   frames + WAV produced by HLAE/MIRV into a final MP4 and concatenate
   multiple clips.
