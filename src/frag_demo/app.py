"""Flask web UI for frag-demo."""

from __future__ import annotations

import os
import threading
import webbrowser
from pathlib import Path
from typing import Any

import shutil

import numpy as np
import pandas as pd
from flask import Flask, jsonify, render_template, request, send_file

from frag_demo.encoder.ffmpeg import VideoEncoder
from frag_demo.launcher.cs2 import CS2Launcher
from frag_demo.parser.demo_parser import DemoAnalyzer
from frag_demo.query.engine import QueryEngine
from frag_demo.sequences.builder import SequenceBuilder

_HERE = os.path.dirname(os.path.abspath(__file__))

app = Flask(
    __name__,
    template_folder=os.path.join(_HERE, "templates"),
    static_folder=os.path.join(_HERE, "static"),
)
app.config["JSON_AS_ASCII"] = False
app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 0  # Disable static file caching

_CS2_TICKRATE = 64  # CS2 always uses 64 tick (sub-tick system)
_KILL_ID_COL = "kill_id"

# ---------------------------------------------------------------------------
# Server-side state (single-user desktop tool)
# ---------------------------------------------------------------------------

_state: dict[str, Any] = {
    "demo_path": None,
    "header": None,
    "kills_df": None,
    "player_slots": None,
}

# Launch guard — prevent concurrent CS2 launches
_launch_lock = threading.Lock()
_cs2_running = False


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _clean_value(v: Any) -> Any:
    """Convert a single value to a JSON-safe Python type."""
    if v is None:
        return None
    if isinstance(v, (np.integer,)):
        return int(v)
    if isinstance(v, (np.floating,)):
        f = float(v)
        return None if (np.isnan(f) or np.isinf(f)) else f
    if isinstance(v, (np.bool_,)):
        return bool(v)
    if isinstance(v, float) and (np.isnan(v) or np.isinf(v)):
        return None
    return v


def _kills_to_list(df: pd.DataFrame) -> list[dict[str, Any]]:
    """Convert a kills DataFrame to a JSON-safe list of dicts."""
    records = df.to_dict(orient="records")
    return [{k: _clean_value(v) for k, v in row.items()} for row in records]


def _clean_header(header: dict) -> dict:
    """Make header dict JSON-safe."""
    return {k: _clean_value(v) for k, v in header.items()}


def _reset_state() -> None:
    """Clear the cached demo state after a failed load or explicit reset."""
    _state["demo_path"] = None
    _state["header"] = None
    _state["kills_df"] = None
    _state["player_slots"] = None


def _prepare_kills_df(kills_df: pd.DataFrame) -> pd.DataFrame:
    """Attach a stable row id so the UI can distinguish same-tick kills."""
    prepared = kills_df.reset_index(drop=True).copy()
    prepared[_KILL_ID_COL] = prepared.index.astype(int)
    return prepared


def _parse_bool(value: Any, default: bool | None = False) -> bool | None:
    """Parse JSON booleans and common string/int equivalents."""
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "1", "yes", "on"}:
            return True
        if normalized in {"false", "0", "no", "off"}:
            return False
    if isinstance(value, int) and value in {0, 1}:
        return bool(value)
    raise ValueError("must be a boolean")


def _parse_float_field(
    data: dict[str, Any],
    key: str,
    default: float,
    *,
    minimum: float | None = None,
) -> float:
    """Parse a float request field with optional lower-bound validation."""
    raw_value = data.get(key, default)
    value = float(raw_value)
    if minimum is not None and value < minimum:
        raise ValueError(f"{key} must be at least {minimum}")
    return value


def _parse_int_field(
    data: dict[str, Any],
    key: str,
    default: int,
    *,
    minimum: int | None = None,
) -> int:
    """Parse an integer request field with optional lower-bound validation."""
    raw_value = data.get(key, default)
    value = int(raw_value)
    if minimum is not None and value < minimum:
        raise ValueError(f"{key} must be at least {minimum}")
    return value


def _clean_old_clips(demo_path: str) -> int:
    """Remove old clip directories and MP4s from previous recordings."""
    demo_stem = Path(demo_path).stem
    demo_dir = Path(demo_path).parent
    removed = 0

    for item in demo_dir.iterdir():
        if not item.name.startswith(demo_stem + "_"):
            continue
        # Remove clip directories (contain TGA takes)
        if item.is_dir():
            shutil.rmtree(str(item), ignore_errors=True)
            removed += 1
        # Remove old MP4 files
        elif item.suffix.lower() == ".mp4":
            item.unlink(missing_ok=True)
            removed += 1

    return removed


def _is_cs2_running() -> bool:
    """Check if cs2.exe is currently running."""
    import subprocess
    try:
        result = subprocess.run(
            ["powershell", "-Command",
             "Get-Process cs2 -ErrorAction SilentlyContinue | Select-Object -First 1 Id"],
            capture_output=True, text=True, timeout=5,
        )
        return bool(result.stdout.strip())
    except Exception:
        # Fallback to tasklist
        try:
            result = subprocess.run(
                ["tasklist", "/fi", "imagename eq cs2.exe", "/nh"],
                capture_output=True, text=True, timeout=5,
            )
            return "cs2.exe" in result.stdout.lower()
        except Exception:
            return False


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/status")
def api_status():
    result: dict[str, Any] = {
        "loaded": _state["demo_path"] is not None,
        "cs2_running": _is_cs2_running(),
    }
    if _state["demo_path"]:
        result["demo_path"] = _state["demo_path"]
        result["map"] = _state["header"].get("map_name", "?") if _state["header"] else "?"
    return jsonify(result)


@app.route("/api/browse")
def api_browse():
    try:
        from tkinter import Tk, filedialog
        root = Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        path = filedialog.askopenfilename(
            title="Select CS2 Demo",
            filetypes=[("Demo files", "*.dem"), ("All files", "*.*")],
        )
        root.destroy()
        if path:
            return jsonify({"ok": True, "path": path})
        return jsonify({"ok": False, "error": "No file selected."})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


@app.route("/api/load", methods=["POST"])
def api_load():
    data = request.get_json(silent=True) or {}
    demo_path = data.get("demo_path", "").strip()

    if not demo_path:
        return jsonify({"ok": False, "error": "No demo path provided."}), 400

    p = Path(demo_path)
    if not p.exists():
        return jsonify({"ok": False, "error": f"File not found: {demo_path}"}), 400
    if not p.suffix.lower() == ".dem":
        return jsonify({"ok": False, "error": "File must be a .dem file."}), 400

    _reset_state()

    try:
        analyzer = DemoAnalyzer(demo_path)
        kills_df = _prepare_kills_df(analyzer.parse_kills())
        header = analyzer.parse_header()
        player_slots = analyzer.get_player_slots()
    except Exception as exc:
        _reset_state()
        return jsonify({"ok": False, "error": f"Failed to parse demo: {exc}"}), 500

    # Cache state
    _state["demo_path"] = demo_path
    _state["header"] = header
    _state["kills_df"] = kills_df
    _state["player_slots"] = player_slots

    # Extract dropdown values
    players = sorted(kills_df["attacker_name"].dropna().unique().tolist()) if "attacker_name" in kills_df.columns else []
    weapons = sorted(kills_df["weapon"].dropna().unique().tolist()) if "weapon" in kills_df.columns else []
    rounds = sorted(int(r) for r in kills_df["total_rounds_played"].dropna().unique()) if "total_rounds_played" in kills_df.columns else []

    clean_header = _clean_header(header)
    # CS2 demos don't include tickrate in the header — it's always 64
    if "tickrate" not in clean_header:
        clean_header["tickrate"] = _CS2_TICKRATE

    return jsonify({
        "ok": True,
        "header": clean_header,
        "total_kills": len(kills_df),
        "players": players,
        "weapons": weapons,
        "rounds": rounds,
    })


@app.route("/api/kills", methods=["POST"])
def api_kills():
    if _state["kills_df"] is None:
        return jsonify({"ok": False, "error": "No demo loaded."}), 400

    data = request.get_json(silent=True) or {}
    player = data.get("player") or None
    weapon = data.get("weapon") or None
    headshot = data.get("headshot")  # True, False, or None
    round_num = data.get("round_num")
    side = data.get("side") or None

    if round_num is not None:
        try:
            round_num = int(round_num)
        except (ValueError, TypeError):
            round_num = None

    try:
        headshot = _parse_bool(headshot, None)
    except ValueError:
        headshot = None

    engine = QueryEngine(_state["kills_df"])
    filtered = engine.query(
        player=player,
        weapon=weapon,
        headshot=headshot,
        round_num=round_num,
        side=side,
    )

    return jsonify({
        "ok": True,
        "total": len(filtered),
        "kills": _kills_to_list(filtered),
    })


@app.route("/api/record", methods=["POST"])
def api_record():
    global _cs2_running

    if _state["kills_df"] is None or _state["demo_path"] is None:
        return jsonify({"ok": False, "error": "No demo loaded."}), 400

    data = request.get_json(silent=True) or {}
    selected_ids = data.get("selected_ids")
    selected_ticks = data.get("selected_ticks", [])
    try:
        before = _parse_float_field(data, "before", 3.0, minimum=0.0)
        after = _parse_float_field(data, "after", 2.0, minimum=0.0)
        framerate = _parse_int_field(data, "framerate", 60, minimum=1)
        launch = bool(_parse_bool(data.get("launch"), False))
    except (TypeError, ValueError) as exc:
        return jsonify({"ok": False, "error": f"Invalid record request: {exc}"}), 400

    if selected_ids is None and not selected_ticks:
        return jsonify({"ok": False, "error": "No kills selected."}), 400

    kills_df = _state["kills_df"]
    if selected_ids is not None:
        if not isinstance(selected_ids, list):
            return jsonify({"ok": False, "error": "Invalid selected_ids value."}), 400
        try:
            parsed_ids = [int(value) for value in selected_ids]
        except (TypeError, ValueError):
            return jsonify({"ok": False, "error": "Invalid selected_ids value."}), 400
        if not parsed_ids:
            return jsonify({"ok": False, "error": "No kills selected."}), 400
        selected = kills_df[kills_df[_KILL_ID_COL].isin(parsed_ids)]
    else:
        if not isinstance(selected_ticks, list):
            return jsonify({"ok": False, "error": "Invalid selected_ticks value."}), 400
        try:
            parsed_ticks = [int(value) for value in selected_ticks]
        except (TypeError, ValueError):
            return jsonify({"ok": False, "error": "Invalid selected_ticks value."}), 400
        selected = kills_df[kills_df["tick"].isin(parsed_ticks)]

    if selected.empty:
        return jsonify({"ok": False, "error": "None of the selected kills matched."}), 400

    demo_path = _state["demo_path"]
    header = _state["header"] or {}
    tickrate = float(header.get("tickrate", 64))
    out_dir = str(Path(demo_path).parent)

    # Clean up old clips and MP4s from previous recordings
    cleaned = _clean_old_clips(demo_path)
    if cleaned:
        print(f"[frag-demo] Cleaned {cleaned} old clip(s)/video(s)")

    builder = SequenceBuilder(
        tickrate=tickrate,
        start_seconds_before=before,
        end_seconds_after=after,
        framerate=framerate,
        output_path=out_dir,
        player_slots=_state["player_slots"] or {},
    )
    sequences = builder.build_sequences(selected, demo_path)
    json_path = builder.write_json(sequences, demo_path)

    # Verify JSON was actually written
    if not json_path.exists():
        return jsonify({"ok": False, "error": f"Failed to write JSON to {json_path}"}), 500

    result: dict[str, Any] = {
        "ok": True,
        "sequences_count": len(sequences),
        "json_path": str(json_path),
        "launched": False,
    }

    if launch:
        # Detect tools
        launcher = CS2Launcher()
        diagnostics: list[str] = []
        diagnostics.append(f"HLAE: {launcher.hlae_path or 'NOT FOUND'}")
        diagnostics.append(f"CS2: {launcher.cs2_path or 'NOT FOUND'}")
        plugin_dll = launcher.find_plugin_dll()
        diagnostics.append(f"Plugin DLL: {plugin_dll or 'NOT FOUND'}")
        diagnostics.append(f"JSON: {json_path} ({json_path.stat().st_size} bytes)")

        if not launcher.hlae_path or not launcher.cs2_path:
            result["diagnostics"] = diagnostics
            result["error"] = "HLAE and/or CS2 not found. Cannot launch."
            result["ok"] = False
            return jsonify(result), 400

        if not plugin_dll:
            result["diagnostics"] = diagnostics
            result["error"] = "server.dll not found. Install CS Demo Manager or place it in tools/plugin/."
            result["ok"] = False
            return jsonify(result), 400

        result["diagnostics"] = diagnostics

        with _launch_lock:
            if _cs2_running or _is_cs2_running():
                return jsonify({
                    "ok": False,
                    "error": "CS2 is already running. Close CS2 first, then try again. "
                             "(JSON was still updated on disk.)",
                    "json_path": str(json_path),
                    "sequences_count": len(sequences),
                }), 409
            _cs2_running = True

        def _launch_bg():
            global _cs2_running
            try:
                launcher.launch(demo_path=demo_path)
            finally:
                with _launch_lock:
                    _cs2_running = False

        try:
            thread = threading.Thread(target=_launch_bg, daemon=True)
            thread.start()
        except Exception:
            with _launch_lock:
                _cs2_running = False
            raise
        result["launched"] = True

    return jsonify(result)


@app.route("/api/encode", methods=["POST"])
def api_encode():
    """Encode recorded MIRV TGA clips into MP4 videos."""
    if _state["demo_path"] is None:
        return jsonify({"ok": False, "error": "No demo loaded."}), 400

    data = request.get_json(silent=True) or {}
    try:
        framerate = _parse_int_field(data, "framerate", 60, minimum=1)
        concatenate = bool(_parse_bool(data.get("concatenate"), True))
    except (TypeError, ValueError) as exc:
        return jsonify({"ok": False, "error": f"Invalid encode request: {exc}"}), 400

    demo_path = _state["demo_path"]
    demo_stem = Path(demo_path).stem
    demo_dir = Path(demo_path).parent

    # Find all clip directories matching this demo
    clip_dirs = sorted(
        d for d in demo_dir.iterdir()
        if d.is_dir() and d.name.startswith(demo_stem + "_")
    )

    if not clip_dirs:
        return jsonify({
            "ok": False,
            "error": f"No clip directories found matching '{demo_stem}_*' in {demo_dir}",
        }), 400

    encoder = VideoEncoder()
    encoded_videos: list[str] = []
    errors: list[str] = []

    for clip_dir in clip_dirs:
        # Find TGA frames — could be in:
        #   1. clip_dir/ directly (startmovie output)
        #   2. clip_dir/take0000/ (MIRV streams output)
        take_dir = None

        # Check clip_dir itself first
        if any(clip_dir.glob("*.tga")):
            take_dir = clip_dir
        else:
            # Check take subdirectories (use latest with TGA files)
            takes = sorted(
                d for d in clip_dir.iterdir()
                if d.is_dir() and d.name.startswith("take")
            )
            for t in reversed(takes):
                if any(t.glob("*.tga")):
                    take_dir = t
                    break

        if take_dir is None:
            errors.append(f"{clip_dir.name}: no TGA frames found")
            continue

        tga_count = len(list(take_dir.glob("*.tga")))
        if tga_count == 0:
            errors.append(f"{clip_dir.name}: no TGA frames found")
            continue

        output_mp4 = clip_dir.with_suffix(".mp4")
        try:
            print(f"[frag-demo] Encoding {clip_dir.name} ({tga_count} frames)...")
            encoder.encode_sequence(
                input_dir=str(take_dir),
                output_path=str(output_mp4),
                framerate=framerate,
            )
            encoded_videos.append(str(output_mp4))
            print(f"[frag-demo] -> {output_mp4.name}")
        except Exception as exc:
            errors.append(f"{clip_dir.name}: {exc}")

    result: dict[str, Any] = {
        "ok": len(encoded_videos) > 0,
        "encoded": [str(Path(v).name) for v in encoded_videos],
        "errors": errors,
    }

    # Concatenate all clips into one video
    if concatenate and len(encoded_videos) > 1:
        concat_output = demo_dir / f"{demo_stem}_all.mp4"
        try:
            print(f"[frag-demo] Concatenating {len(encoded_videos)} clips...")
            encoder.concatenate(encoded_videos, str(concat_output))
            result["concatenated"] = str(concat_output)
            print(f"[frag-demo] -> {concat_output.name}")
        except Exception as exc:
            errors.append(f"Concatenation failed: {exc}")

    return jsonify(result)


@app.route("/api/clean", methods=["POST"])
def api_clean():
    """Remove old clip directories and MP4s for the current demo."""
    if _state["demo_path"] is None:
        return jsonify({"ok": False, "error": "No demo loaded."}), 400
    removed = _clean_old_clips(_state["demo_path"])
    return jsonify({"ok": True, "removed": removed})


@app.route("/api/clips")
def api_clips():
    """List encoded MP4 clips for the current demo."""
    if _state["demo_path"] is None:
        return jsonify({"ok": False, "error": "No demo loaded."}), 400

    demo_path = _state["demo_path"]
    demo_stem = Path(demo_path).stem
    demo_dir = Path(demo_path).parent

    clips = []
    for f in sorted(demo_dir.iterdir()):
        if f.suffix.lower() == ".mp4" and f.name.startswith(demo_stem + "_"):
            clips.append({
                "name": f.name,
                "size_mb": round(f.stat().st_size / (1024 * 1024), 1),
                "is_combined": f.stem.endswith("_all"),
            })

    return jsonify({"ok": True, "clips": clips})


@app.route("/clips/<path:filename>")
def serve_clip(filename: str):
    """Serve an encoded MP4 file for the video player."""
    if _state["demo_path"] is None:
        return "No demo loaded", 404

    demo_dir = Path(_state["demo_path"]).parent
    clip_path = demo_dir / filename

    if not clip_path.exists() or not clip_path.suffix.lower() == ".mp4":
        return "Not found", 404

    # Security: ensure the file is within the demo directory
    try:
        clip_path.resolve().relative_to(demo_dir.resolve())
    except ValueError:
        return "Forbidden", 403

    return send_file(str(clip_path), mimetype="video/mp4")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    """Start the Flask dev server and open the browser."""
    host = "127.0.0.1"
    port = 5000
    url = f"http://{host}:{port}"
    print(f"[frag-demo] Starting web UI at {url}")
    threading.Timer(1.5, lambda: webbrowser.open(url)).start()
    app.run(host=host, port=port, debug=False)


if __name__ == "__main__":
    main()
