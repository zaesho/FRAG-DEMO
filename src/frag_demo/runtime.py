"""Shared runtime helpers for the Bun server worker bridge."""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from frag_demo.encoder.ffmpeg import VideoEncoder

_CS2_TICKRATE = 64
_KILL_ID_COL = "kill_id"
_RECORD_NAME_RE = re.compile(r'^mirv_streams record name "(.+)"$')


def _clean_value(value: Any) -> Any:
    """Convert a single value to a JSON-safe Python type."""
    if value is None:
        return None
    if isinstance(value, np.integer):
        return int(value)
    if isinstance(value, np.floating):
        parsed = float(value)
        return None if np.isnan(parsed) or np.isinf(parsed) else parsed
    if isinstance(value, np.bool_):
        return bool(value)
    if isinstance(value, float) and (np.isnan(value) or np.isinf(value)):
        return None
    return value


def _kills_to_list(df: pd.DataFrame) -> list[dict[str, Any]]:
    """Convert a kills DataFrame to a JSON-safe list of dicts."""
    records = df.to_dict(orient="records")
    return [{key: _clean_value(item) for key, item in row.items()} for row in records]


def _clean_header(header: dict[str, Any]) -> dict[str, Any]:
    """Make a header dict JSON-safe."""
    return {key: _clean_value(value) for key, value in header.items()}


def _prepare_kills_df(kills_df: pd.DataFrame) -> pd.DataFrame:
    """Attach a stable row id so the UI can distinguish same-tick kills."""
    prepared = kills_df.reset_index(drop=True).copy()
    prepared[_KILL_ID_COL] = prepared.index.astype(int)
    return prepared


def _clean_old_clips(demo_path: str) -> int:
    """Remove old clip directories and MP4s from previous recordings."""
    demo_stem = Path(demo_path).stem
    demo_dir = Path(demo_path).parent
    removed = 0

    for item in demo_dir.iterdir():
        if not item.name.startswith(demo_stem + "_"):
            continue
        if item.is_dir():
            shutil.rmtree(str(item), ignore_errors=True)
            removed += 1
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
            command = action.get("cmd")
            if not isinstance(command, str):
                continue
            match = _RECORD_NAME_RE.match(command)
            if match is None:
                continue
            clip_dir = Path(match.group(1))
            key = str(clip_dir)
            if key in seen:
                continue
            seen.add(key)
            clip_dirs.append(clip_dir)

    return clip_dirs


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
            entry
            for entry in demo_dir.iterdir()
            if entry.is_dir() and entry.name.startswith(demo_stem + "_")
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
                entry
                for entry in clip_dir.iterdir()
                if entry.is_dir() and entry.name.startswith("take")
            )
            for take in reversed(takes):
                if any(take.glob("*.tga")):
                    take_dir = take
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
        "encoded": [str(Path(video).name) for video in encoded_videos],
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


def _clips_payload(demo_path: str) -> list[dict[str, Any]]:
    """List encoded MP4 clips for a demo path."""
    demo_stem = Path(demo_path).stem
    demo_dir = Path(demo_path).parent

    clips: list[dict[str, Any]] = []
    for clip_file in sorted(demo_dir.iterdir()):
        if clip_file.suffix.lower() != ".mp4":
            continue
        if not clip_file.name.startswith(demo_stem + "_"):
            continue
        clips.append(
            {
                "name": clip_file.name,
                "size_mb": round(clip_file.stat().st_size / (1024 * 1024), 1),
                "is_combined": clip_file.stem.endswith("_all"),
            }
        )
    return clips
