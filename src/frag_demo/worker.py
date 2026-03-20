"""CLI worker for the Node server bridge."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pandas as pd

from frag_demo.runtime import (
    _CS2_TICKRATE,
    _KILL_ID_COL,
    _clean_header,
    _clean_old_clips,
    _clips_payload,
    _encode_recorded_clips,
    _kills_to_list,
    _prepare_kills_df,
)
from frag_demo.launcher.cs2 import CS2Launcher
from frag_demo.parser.demo_parser import DemoAnalyzer
from frag_demo.sequences.builder import SequenceBuilder


def _read_payload() -> dict[str, Any]:
    raw = sys.stdin.read()
    if not raw.strip():
        return {}
    loaded = json.loads(raw)
    if not isinstance(loaded, dict):
        raise ValueError("Worker payload must be a JSON object")
    return loaded


def _write_payload(payload: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(payload, ensure_ascii=False))
    sys.stdout.flush()


def _normalize_demo_path(raw_path: str) -> str:
    demo_path = Path(raw_path).expanduser()
    if not demo_path.exists():
        raise FileNotFoundError(f"File not found: {raw_path}")
    if demo_path.suffix.lower() != ".dem":
        raise ValueError("File must be a .dem file.")
    return str(demo_path.resolve(strict=False))


def _select_kills(
    kills_df: pd.DataFrame,
    payload: dict[str, Any],
) -> pd.DataFrame:
    selected_ids = payload.get("selected_ids")
    selected_ticks = payload.get("selected_ticks") or []

    if selected_ids is None and not selected_ticks:
        raise ValueError("No kills selected.")

    if selected_ids is not None:
        if not isinstance(selected_ids, list):
            raise ValueError("Invalid selected_ids value.")
        parsed_ids = [int(value) for value in selected_ids]
        if not parsed_ids:
            raise ValueError("No kills selected.")
        selected = kills_df[kills_df[_KILL_ID_COL].isin(parsed_ids)]
    else:
        if not isinstance(selected_ticks, list):
            raise ValueError("Invalid selected_ticks value.")
        parsed_ticks = [int(value) for value in selected_ticks]
        selected = kills_df[kills_df["tick"].isin(parsed_ticks)]

    if selected.empty:
        raise ValueError("None of the selected kills matched.")
    return selected


def _parse_float(payload: dict[str, Any], key: str, default: float) -> float:
    return float(payload.get(key, default))


def _parse_int(payload: dict[str, Any], key: str, default: int) -> int:
    return int(payload.get(key, default))


def _check_launch_impl(demo_path: str) -> dict[str, Any]:
    launcher = CS2Launcher()
    diagnostics: list[str] = [
        f"HLAE: {launcher.hlae_path or 'NOT FOUND'}",
        f"CS2: {launcher.cs2_path or 'NOT FOUND'}",
    ]
    plugin_dll = launcher.find_plugin_dll()
    diagnostics.append(f"Plugin DLL: {plugin_dll or 'NOT FOUND'}")

    json_path = Path(demo_path).with_name(Path(demo_path).name + ".json")
    if json_path.exists():
        diagnostics.append(f"JSON: {json_path} ({json_path.stat().st_size} bytes)")
    else:
        diagnostics.append(f"JSON: {json_path} (NOT FOUND)")

    if not launcher.hlae_path or not launcher.cs2_path:
        return {
            "ok": False,
            "diagnostics": diagnostics,
            "error": "HLAE and/or CS2 not found. Cannot launch.",
        }
    if not plugin_dll:
        return {
            "ok": False,
            "diagnostics": diagnostics,
            "error": "server.dll not found. Install CS Demo Manager or place it in tools/plugin/.",
        }
    return {"ok": True, "diagnostics": diagnostics}


def cmd_load(payload: dict[str, Any]) -> dict[str, Any]:
    demo_path = _normalize_demo_path(str(payload.get("demo_path", "")))
    analyzer = DemoAnalyzer(demo_path)
    kills_df = _prepare_kills_df(analyzer.parse_kills())
    header = analyzer.parse_header()
    player_slots = analyzer.get_player_slots()

    clean_header = _clean_header(header)
    if "tickrate" not in clean_header:
        clean_header["tickrate"] = _CS2_TICKRATE

    players = (
        sorted(kills_df["attacker_name"].dropna().unique().tolist())
        if "attacker_name" in kills_df.columns
        else []
    )
    weapons = (
        sorted(kills_df["weapon"].dropna().unique().tolist())
        if "weapon" in kills_df.columns
        else []
    )
    rounds = (
        sorted(int(r) for r in kills_df["total_rounds_played"].dropna().unique())
        if "total_rounds_played" in kills_df.columns
        else []
    )

    return {
        "ok": True,
        "demo_path": demo_path,
        "header": clean_header,
        "total_kills": len(kills_df),
        "players": players,
        "weapons": weapons,
        "rounds": rounds,
        "player_slots": player_slots,
        "kills": _kills_to_list(kills_df),
    }


def cmd_generate_json(payload: dict[str, Any]) -> dict[str, Any]:
    demo_path = _normalize_demo_path(str(payload.get("demo_path", "")))
    kills = payload.get("kills")
    if not isinstance(kills, list):
        raise ValueError("kills must be a list")
    kills_df = pd.DataFrame(kills)
    selected = _select_kills(kills_df, payload)
    header = payload.get("header") if isinstance(payload.get("header"), dict) else {}
    player_slots = (
        payload.get("player_slots")
        if isinstance(payload.get("player_slots"), dict)
        else {}
    )

    before = _parse_float(payload, "before", 2.0)
    after = _parse_float(payload, "after", 1.0)
    framerate = _parse_int(payload, "framerate", 60)
    hud_mode = str(payload.get("hud_mode", "deathnotices"))
    launch = bool(payload.get("launch", False))
    tickrate = float(header.get("tickrate", _CS2_TICKRATE))

    cleaned = _clean_old_clips(demo_path)
    builder = SequenceBuilder(
        tickrate=tickrate,
        start_seconds_before=before,
        end_seconds_after=after,
        framerate=framerate,
        output_path=str(Path(demo_path).parent),
        player_slots=player_slots,
        hud_mode=hud_mode,
        close_game_after_recording=launch,
    )
    sequences = builder.build_sequences(selected, demo_path)
    json_path = builder.write_json(sequences, demo_path)

    return {
        "ok": True,
        "sequences_count": len(sequences),
        "json_path": str(json_path),
        "cleaned": cleaned,
    }


def cmd_check_launch(payload: dict[str, Any]) -> dict[str, Any]:
    demo_path = _normalize_demo_path(str(payload.get("demo_path", "")))
    return _check_launch_impl(demo_path)


def cmd_launch_and_encode(payload: dict[str, Any]) -> dict[str, Any]:
    demo_path = _normalize_demo_path(str(payload.get("demo_path", "")))
    framerate = _parse_int(payload, "framerate", 60)

    check = _check_launch_impl(demo_path)
    if not check["ok"]:
        return check

    launcher = CS2Launcher()
    launcher.launch(demo_path=demo_path)
    encoded = _encode_recorded_clips(
        demo_path,
        framerate=framerate,
        concatenate=True,
    )
    encoded["diagnostics"] = check["diagnostics"]
    return encoded


def cmd_encode(payload: dict[str, Any]) -> dict[str, Any]:
    demo_path = _normalize_demo_path(str(payload.get("demo_path", "")))
    framerate = _parse_int(payload, "framerate", 60)
    concatenate = bool(payload.get("concatenate", True))
    return _encode_recorded_clips(
        demo_path,
        framerate=framerate,
        concatenate=concatenate,
    )


def cmd_clean(payload: dict[str, Any]) -> dict[str, Any]:
    demo_path = _normalize_demo_path(str(payload.get("demo_path", "")))
    removed = _clean_old_clips(demo_path)
    return {"ok": True, "removed": removed}


def cmd_clips(payload: dict[str, Any]) -> dict[str, Any]:
    demo_path = _normalize_demo_path(str(payload.get("demo_path", "")))
    return {"ok": True, "clips": _clips_payload(demo_path)}


COMMANDS = {
    "load": cmd_load,
    "generate_json": cmd_generate_json,
    "check_launch": cmd_check_launch,
    "launch_and_encode": cmd_launch_and_encode,
    "encode": cmd_encode,
    "clean": cmd_clean,
    "clips": cmd_clips,
}


def main() -> None:
    if len(sys.argv) < 2:
        raise SystemExit("Usage: python -m frag_demo.worker <command>")

    command = sys.argv[1]
    handler = COMMANDS.get(command)
    if handler is None:
        raise SystemExit(f"Unknown command: {command}")

    try:
        payload = _read_payload()
        result = handler(payload)
    except Exception as exc:  # pragma: no cover - bridge safety
        _write_payload({"ok": False, "error": str(exc)})
        raise SystemExit(1)

    _write_payload(result)


if __name__ == "__main__":
    main()
