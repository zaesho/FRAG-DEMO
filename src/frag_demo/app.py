"""Flask web UI for frag-demo."""

from __future__ import annotations

import json
import os
import re
import threading
import webbrowser
from datetime import datetime, timezone
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
_RECORD_NAME_RE = re.compile(r'^mirv_streams record name "(.+)"$')
_HUD_MODES = {"deathnotices", "all", "none"}
_UI_STATE_DIR = Path.home() / ".frag-demo"
_UI_STATE_PATH = _UI_STATE_DIR / "ui_state.json"
_MAX_RECENT_DEMOS = 50
_MAX_DISCOVERED_DEMOS = 1000

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
_ui_state_lock = threading.Lock()
_auto_encode_lock = threading.Lock()
_auto_encode_status: dict[str, Any] = {
    "running": False,
    "event_id": 0,
    "last_result": None,
}


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


def _parse_str_choice_field(
    data: dict[str, Any],
    key: str,
    default: str,
    *,
    allowed: set[str],
) -> str:
    """Parse a string choice request field against an allowed set."""
    raw_value = data.get(key, default)
    if not isinstance(raw_value, str):
        raise ValueError(f"{key} must be a string")
    value = raw_value.strip().lower()
    if value not in allowed:
        raise ValueError(f"{key} must be one of: {', '.join(sorted(allowed))}")
    return value


def _utc_iso_now() -> str:
    """Return an RFC3339 UTC timestamp without fractional seconds."""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _path_key(path: str) -> str:
    """Normalize a path for case-insensitive dedupe."""
    return path.replace("\\", "/").casefold()


def _normalize_path(path: str, *, strict: bool) -> str:
    """Resolve a path to an absolute normalized string."""
    return str(Path(path).expanduser().resolve(strict=strict))


def _is_demo_path(path: str) -> bool:
    """Return True when a path looks like a .dem file."""
    return path.lower().endswith(".dem")


def _default_ui_state() -> dict[str, Any]:
    """Return default persisted UI state."""
    return {
        "watched_folders": [],
        "recent_demos": [],
        "selected_demo_path": None,
    }


def _coerce_ui_state(raw: Any) -> dict[str, Any]:
    """Validate and normalize persisted UI state payload."""
    state = _default_ui_state()
    if not isinstance(raw, dict):
        return state

    watched: list[dict[str, Any]] = []
    watched_seen: set[str] = set()
    for entry in raw.get("watched_folders", []):
        if not isinstance(entry, dict):
            continue
        raw_path = entry.get("path")
        if not isinstance(raw_path, str) or not raw_path.strip():
            continue
        try:
            normalized = _normalize_path(raw_path.strip(), strict=False)
        except (OSError, RuntimeError, ValueError):
            continue
        key = _path_key(normalized)
        if key in watched_seen:
            continue
        watched_seen.add(key)
        watched.append({"path": normalized, "recursive": True})
    state["watched_folders"] = watched

    recent: list[dict[str, Any]] = []
    recent_seen: set[str] = set()
    for entry in raw.get("recent_demos", []):
        if not isinstance(entry, dict):
            continue
        raw_path = entry.get("path")
        if not isinstance(raw_path, str) or not raw_path.strip():
            continue
        try:
            normalized = _normalize_path(raw_path.strip(), strict=False)
        except (OSError, RuntimeError, ValueError):
            continue
        key = _path_key(normalized)
        if key in recent_seen:
            continue
        recent_seen.add(key)
        timestamp = entry.get("last_loaded_at")
        last_loaded_at = timestamp if isinstance(timestamp, str) else ""
        recent.append({"path": normalized, "last_loaded_at": last_loaded_at})
        if len(recent) >= _MAX_RECENT_DEMOS:
            break
    state["recent_demos"] = recent

    selected = raw.get("selected_demo_path")
    if isinstance(selected, str) and selected.strip() and _is_demo_path(selected.strip()):
        try:
            state["selected_demo_path"] = _normalize_path(selected.strip(), strict=False)
        except (OSError, RuntimeError, ValueError):
            state["selected_demo_path"] = None

    return state


def _load_ui_state_unlocked() -> dict[str, Any]:
    """Read persisted UI state from disk."""
    if not _UI_STATE_PATH.exists():
        return _default_ui_state()

    try:
        loaded = json.loads(_UI_STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return _default_ui_state()

    return _coerce_ui_state(loaded)


def _save_ui_state_unlocked(state: dict[str, Any]) -> None:
    """Persist UI state to disk."""
    _UI_STATE_DIR.mkdir(parents=True, exist_ok=True)
    _UI_STATE_PATH.write_text(
        json.dumps(state, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def _upsert_recent_demo(ui_state: dict[str, Any], demo_path: str) -> None:
    """Move demo to the front of recents and trim max length."""
    demo_key = _path_key(demo_path)
    recents = [
        item
        for item in ui_state["recent_demos"]
        if _path_key(str(item.get("path", ""))) != demo_key
    ]
    recents.insert(0, {"path": demo_path, "last_loaded_at": _utc_iso_now()})
    ui_state["recent_demos"] = recents[:_MAX_RECENT_DEMOS]


def _scan_watched_demos(
    watched_folders: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Scan watched folders for .dem files and return folder/demo payloads."""
    folders_payload: list[dict[str, Any]] = []
    discovered_by_key: dict[str, dict[str, Any]] = {}

    for folder in watched_folders:
        folder_path = str(folder.get("path", ""))
        recursive = bool(folder.get("recursive", True))
        folder_obj = Path(folder_path)
        exists = folder_obj.exists() and folder_obj.is_dir()
        folders_payload.append(
            {
                "path": folder_path,
                "recursive": recursive,
                "exists": exists,
            }
        )

        if not exists:
            continue

        walker = folder_obj.rglob("*") if recursive else folder_obj.glob("*")
        try:
            for candidate in walker:
                try:
                    if not candidate.is_file() or candidate.suffix.lower() != ".dem":
                        continue
                    normalized = str(candidate.resolve(strict=False))
                    stat = candidate.stat()
                except (OSError, RuntimeError, ValueError):
                    continue

                key = _path_key(normalized)
                payload = {
                    "name": candidate.name,
                    "path": normalized,
                    "folder_path": str(candidate.parent),
                    "modified_ts": float(stat.st_mtime),
                    "size_mb": round(stat.st_size / (1024 * 1024), 1),
                    "exists": True,
                }
                existing = discovered_by_key.get(key)
                if existing is None or payload["modified_ts"] > existing["modified_ts"]:
                    discovered_by_key[key] = payload
        except OSError:
            continue

    discovered = sorted(
        discovered_by_key.values(),
        key=lambda item: item["modified_ts"],
        reverse=True,
    )[:_MAX_DISCOVERED_DEMOS]
    return folders_payload, discovered


def _recent_demos_payload(recent_demos: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Attach display metadata to recent demo items."""
    payload: list[dict[str, Any]] = []
    for entry in recent_demos:
        path = str(entry.get("path", ""))
        if not path:
            continue
        item_path = Path(path)
        payload.append(
            {
                "name": item_path.name,
                "path": path,
                "folder_path": str(item_path.parent),
                "last_loaded_at": str(entry.get("last_loaded_at", "")),
                "exists": item_path.exists() and item_path.is_file(),
            }
        )
    return payload


def _build_library_payload() -> dict[str, Any]:
    """Build current library payload including scanned demos."""
    with _ui_state_lock:
        ui_state = _load_ui_state_unlocked()
        # Keep the on-disk schema normalized even when the file was missing/corrupt.
        _save_ui_state_unlocked(ui_state)
        watched_snapshot = [dict(item) for item in ui_state["watched_folders"]]
        recent_snapshot = [dict(item) for item in ui_state["recent_demos"]]
        selected_demo_path = ui_state.get("selected_demo_path")

    watched_folders, discovered_demos = _scan_watched_demos(watched_snapshot)
    return {
        "ok": True,
        "watched_folders": watched_folders,
        "recent_demos": _recent_demos_payload(recent_snapshot),
        "discovered_demos": discovered_demos,
        "selected_demo_path": selected_demo_path,
        "scanned_at": _utc_iso_now(),
    }


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


def _expected_clip_dirs_from_json(json_path: Path) -> list[Path]:
    """Return clip directories referenced by MIRV in the actions JSON."""
    if not json_path.exists():
        return []

    try:
        loaded = json.loads(json_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []

    if not isinstance(loaded, list):
        return []

    clip_dirs: list[Path] = []
    seen: set[str] = set()

    for sequence in loaded:
        if not isinstance(sequence, dict):
            continue
        actions = sequence.get("actions")
        if not isinstance(actions, list):
            continue
        for action in actions:
            if not isinstance(action, dict):
                continue
            cmd = action.get("cmd")
            if not isinstance(cmd, str):
                continue
            match = _RECORD_NAME_RE.match(cmd)
            if match is None:
                continue
            clip_dir = Path(match.group(1))
            key = str(clip_dir)
            if key in seen:
                continue
            seen.add(key)
            clip_dirs.append(clip_dir)

    return clip_dirs


def _reset_auto_encode_status() -> None:
    """Clear any previous auto-encode result for a new recording run."""
    with _auto_encode_lock:
        _auto_encode_status["running"] = False
        _auto_encode_status["last_result"] = None


def _begin_auto_encode() -> None:
    """Mark the automatic encode job as running."""
    with _auto_encode_lock:
        _auto_encode_status["event_id"] = int(_auto_encode_status["event_id"]) + 1
        _auto_encode_status["running"] = True
        _auto_encode_status["last_result"] = None


def _finish_auto_encode(result: dict[str, Any]) -> None:
    """Store the automatic encode result for status polling."""
    with _auto_encode_lock:
        _auto_encode_status["event_id"] = int(_auto_encode_status["event_id"]) + 1
        stored = dict(result)
        stored["event_id"] = _auto_encode_status["event_id"]
        _auto_encode_status["running"] = False
        _auto_encode_status["last_result"] = stored


def _auto_encode_snapshot() -> dict[str, Any]:
    """Return a shallow copy of the current auto-encode state."""
    with _auto_encode_lock:
        return {
            "auto_encode_running": bool(_auto_encode_status["running"]),
            "auto_encode_event_id": int(_auto_encode_status["event_id"]),
            "last_auto_encode": (
                dict(_auto_encode_status["last_result"])
                if isinstance(_auto_encode_status["last_result"], dict)
                else None
            ),
        }


def _find_recorded_clip_dirs(demo_path: str) -> tuple[list[Path], str | None]:
    """Return recorded clip directories and an error message when none exist."""
    demo_path_obj = Path(demo_path)
    demo_stem = demo_path_obj.stem
    demo_dir = demo_path_obj.parent
    json_path = demo_path_obj.with_name(demo_path_obj.name + ".json")

    expected_clip_dirs = _expected_clip_dirs_from_json(json_path)
    clip_dirs = [clip_dir for clip_dir in expected_clip_dirs if clip_dir.is_dir()]

    if not clip_dirs:
        clip_dirs = sorted(
            d for d in demo_dir.iterdir()
            if d.is_dir() and d.name.startswith(demo_stem + "_")
        )

    if clip_dirs:
        return clip_dirs, None

    if expected_clip_dirs:
        expected = ", ".join(str(path) for path in expected_clip_dirs[:3])
        if len(expected_clip_dirs) > 3:
            expected += ", ..."
        return [], (
            "No recorded clip directories were found for the generated JSON. "
            f"Expected: {expected}"
        )

    return [], f"No clip directories found matching '{demo_stem}_*' in {demo_dir}"


def _encode_recorded_clips(
    demo_path: str,
    *,
    framerate: int,
    concatenate: bool = True,
) -> dict[str, Any]:
    """Encode recorded MIRV clip directories into MP4 files."""
    demo_path_obj = Path(demo_path)
    demo_stem = demo_path_obj.stem
    demo_dir = demo_path_obj.parent

    clip_dirs, error = _find_recorded_clip_dirs(demo_path)
    if not clip_dirs:
        return {
            "ok": False,
            "encoded": [],
            "errors": [],
            "error": error,
        }

    encoder = VideoEncoder()
    encoded_videos: list[str] = []
    errors: list[str] = []

    for clip_dir in clip_dirs:
        take_dir = None

        if any(clip_dir.glob("*.tga")):
            take_dir = clip_dir
        else:
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

    if concatenate and len(encoded_videos) > 1:
        concat_output = demo_dir / f"{demo_stem}_all.mp4"
        try:
            print(f"[frag-demo] Concatenating {len(encoded_videos)} clips...")
            encoder.concatenate(encoded_videos, str(concat_output))
            result["concatenated"] = str(concat_output)
            print(f"[frag-demo] -> {concat_output.name}")
        except Exception as exc:
            errors.append(f"Concatenation failed: {exc}")

    if not result["ok"] and "error" not in result and errors:
        result["error"] = "Encoding failed"

    return result


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
    result.update(_auto_encode_snapshot())
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


@app.route("/api/browse-folder")
def api_browse_folder():
    try:
        from tkinter import Tk, filedialog

        root = Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        path = filedialog.askdirectory(title="Select Demo Watch Folder")
        root.destroy()
        if path:
            return jsonify({"ok": True, "path": path})
        return jsonify({"ok": False, "error": "No folder selected."})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


@app.route("/api/library")
def api_library():
    return jsonify(_build_library_payload())


@app.route("/api/library/select", methods=["POST"])
def api_library_select():
    data = request.get_json(silent=True) or {}
    raw_demo_path = data.get("demo_path", "")
    if not isinstance(raw_demo_path, str) or not raw_demo_path.strip():
        return jsonify({"ok": False, "error": "No demo path provided."}), 400

    demo_path = raw_demo_path.strip()
    if not _is_demo_path(demo_path):
        return jsonify({"ok": False, "error": "demo_path must end with .dem"}), 400

    try:
        normalized = _normalize_path(demo_path, strict=False)
    except (OSError, RuntimeError, ValueError) as exc:
        return jsonify({"ok": False, "error": f"Invalid demo path: {exc}"}), 400

    with _ui_state_lock:
        ui_state = _load_ui_state_unlocked()
        ui_state["selected_demo_path"] = normalized
        _save_ui_state_unlocked(ui_state)

    return jsonify({"ok": True, "selected_demo_path": normalized})


@app.route("/api/library/watch/add", methods=["POST"])
def api_library_watch_add():
    data = request.get_json(silent=True) or {}
    raw_folder_path = data.get("folder_path", "")
    if not isinstance(raw_folder_path, str) or not raw_folder_path.strip():
        return jsonify({"ok": False, "error": "No folder path provided."}), 400

    try:
        normalized = _normalize_path(raw_folder_path.strip(), strict=True)
    except (OSError, RuntimeError, ValueError):
        return jsonify({"ok": False, "error": "Folder not found."}), 400

    folder = Path(normalized)
    if not folder.is_dir():
        return jsonify({"ok": False, "error": "Path is not a directory."}), 400

    new_key = _path_key(normalized)
    with _ui_state_lock:
        ui_state = _load_ui_state_unlocked()
        watched = ui_state["watched_folders"]
        if all(_path_key(str(item.get("path", ""))) != new_key for item in watched):
            watched.append({"path": normalized, "recursive": True})
            _save_ui_state_unlocked(ui_state)
        else:
            # Keep on-disk format normalized.
            _save_ui_state_unlocked(ui_state)

    return jsonify(_build_library_payload())


@app.route("/api/library/watch/remove", methods=["POST"])
def api_library_watch_remove():
    data = request.get_json(silent=True) or {}
    raw_folder_path = data.get("folder_path", "")
    if not isinstance(raw_folder_path, str) or not raw_folder_path.strip():
        return jsonify({"ok": False, "error": "No folder path provided."}), 400

    try:
        normalized = _normalize_path(raw_folder_path.strip(), strict=False)
    except (OSError, RuntimeError, ValueError) as exc:
        return jsonify({"ok": False, "error": f"Invalid folder path: {exc}"}), 400

    remove_key = _path_key(normalized)
    with _ui_state_lock:
        ui_state = _load_ui_state_unlocked()
        ui_state["watched_folders"] = [
            item
            for item in ui_state["watched_folders"]
            if _path_key(str(item.get("path", ""))) != remove_key
        ]
        _save_ui_state_unlocked(ui_state)

    return jsonify(_build_library_payload())


@app.route("/api/load", methods=["POST"])
def api_load():
    data = request.get_json(silent=True) or {}
    input_demo_path = data.get("demo_path", "").strip()

    if not input_demo_path:
        return jsonify({"ok": False, "error": "No demo path provided."}), 400

    p = Path(input_demo_path).expanduser()
    if not p.exists():
        return jsonify({"ok": False, "error": f"File not found: {input_demo_path}"}), 400
    if not p.suffix.lower() == ".dem":
        return jsonify({"ok": False, "error": "File must be a .dem file."}), 400
    demo_path = str(p.resolve(strict=False))

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

    with _ui_state_lock:
        ui_state = _load_ui_state_unlocked()
        ui_state["selected_demo_path"] = demo_path
        _upsert_recent_demo(ui_state, demo_path)
        _save_ui_state_unlocked(ui_state)

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
        before = _parse_float_field(data, "before", 2.0, minimum=0.0)
        after = _parse_float_field(data, "after", 1.0, minimum=0.0)
        framerate = _parse_int_field(data, "framerate", 60, minimum=1)
        hud_mode = _parse_str_choice_field(
            data,
            "hud_mode",
            "deathnotices",
            allowed=_HUD_MODES,
        )
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
        hud_mode=hud_mode,
        close_game_after_recording=launch,
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
        _reset_auto_encode_status()

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
                _begin_auto_encode()
                auto_encode_result = _encode_recorded_clips(
                    demo_path,
                    framerate=framerate,
                    concatenate=True,
                )
                _finish_auto_encode(auto_encode_result)
            except Exception as exc:
                _finish_auto_encode({
                    "ok": False,
                    "encoded": [],
                    "errors": [],
                    "error": f"Auto-encode failed: {exc}",
                })
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

    result = _encode_recorded_clips(
        _state["demo_path"],
        framerate=framerate,
        concatenate=concatenate,
    )
    if not result["ok"] and result.get("error"):
        return jsonify(result), 400
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
