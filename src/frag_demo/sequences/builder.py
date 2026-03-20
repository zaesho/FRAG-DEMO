"""Sequence builder -- generates JSON action files for the CS2 server plugin."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd


def _to_unix_path(path: str | Path) -> str:
    """Convert a filesystem path to forward-slash notation.

    The CS Demo Manager plugin (and MIRV) require forward-slash separators
    in all console-command strings, even on Windows.
    """
    return str(Path(path).resolve()).replace("\\", "/")


class SequenceBuilder:
    """Builds the JSON actions file consumed by CS Demo Manager's CS2 plugin.

    The generated file contains a list of *sequences*, each describing a set
    of console commands that the plugin executes while playing back a demo.
    Commands are keyed to absolute demo ticks.

    The structure mirrors cs-demo-manager's ``create-cs2-video-json-file.ts``:

    1. At **tick 64** (the minimum valid tick) — global one-time setup commands.
    2. At **tick 1** — a ``demo_gototick`` jump to just before the setup tick.
    3. At **setup_tick** — per-clip setup (stream name, fps, clear death msgs,
       spectate target).
    4. At **start_tick - 4** — ``pause_playback`` so the loading screen clears.
    5. At **start_tick** — ``mirv_streams record start``.
    6. At **end_tick** — ``mirv_streams record end``.
    7. At **end_tick + 64** — ``go_to_next_sequence``.

    Example output format::

        [
          {
            "actions": [
              {"tick": 64,   "cmd": "sv_cheats 1"},
              {"tick": 64,   "cmd": "volume 1"},
              ...
              {"tick": 1,    "cmd": "demo_gototick 9700"},
              {"tick": 9764, "cmd": "mirv_streams record name \\"C:/clips/clip_0000\\""},
              {"tick": 9764, "cmd": "mirv_streams record fps 60"},
              ...
              {"tick": 9796, "cmd": "pause_playback"},
              {"tick": 9800, "cmd": "mirv_streams record start"},
              {"tick": 9928, "cmd": "mirv_streams record end"},
              {"tick": 9992, "cmd": "go_to_next_sequence"}
            ]
          }
        ]
    """

    # Ticks used as padding between the last end tick and
    # the go_to_next_sequence command.
    _NEXT_SEQ_PADDING: int = 64

    # The CS Demo Manager plugin floors all ticks to this minimum.
    _MIN_VALID_TICK: int = 64

    # How many ticks before start_tick to issue pause_playback so that the
    # demo loading screen has cleared before recording begins.
    _PAUSE_BEFORE_START: int = 4

    def __init__(
        self,
        tickrate: float = 64.0,
        recording_system: str = "hlae",
        start_seconds_before: float = 3.0,
        end_seconds_after: float = 2.0,
        framerate: int = 60,
        output_path: str = "output",
        player_slots: dict[str, int] | None = None,
    ) -> None:
        self.tickrate = float(tickrate)
        self.recording_system = recording_system
        self.start_seconds_before = start_seconds_before
        self.end_seconds_after = end_seconds_after
        self.framerate = framerate
        self.output_path = output_path
        # Maps player name -> entity slot number for spec_player commands.
        self.player_slots: dict[str, int] = player_slots or {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def build_sequences(
        self, kills_df: pd.DataFrame, demo_path: str
    ) -> list[dict[str, Any]]:
        """Build sequence dicts from a kills DataFrame.

        Kills that fall within 10 seconds of each other are merged into a
        single sequence so that back-to-back frags are captured together.

        Args:
            kills_df: DataFrame of kill events (as returned by
                :meth:`DemoAnalyzer.parse_kills`).
            demo_path: Path to the .dem file (used for naming the output
                stream).

        Returns:
            List of sequence dicts ready to be serialised as JSON.
        """
        if kills_df.empty:
            return []

        demo_name = Path(demo_path).stem

        # Resolve the output directory to an absolute path so that the
        # MIRV command string always contains a full, forward-slash path.
        output_dir = Path(self.output_path).resolve()

        # Determine the tick column name produced by demoparser2
        tick_col = self._find_col(kills_df, "tick")
        attacker_col = self._find_col(kills_df, "attacker_name")
        attacker_steamid_col = self._find_col(kills_df, "attacker_steamid")

        if tick_col is None:
            raise ValueError(
                "kills_df must contain a 'tick' column. "
                "Available columns: " + str(list(kills_df.columns))
            )

        # Sort by tick so grouping works correctly
        kills_sorted = kills_df.sort_values(tick_col).reset_index(drop=True)

        group_threshold = self._ticks_from_seconds(10.0)
        groups: list[list[Any]] = []
        current_group: list[Any] = []
        prev_tick: int | None = None

        for _, row in kills_sorted.iterrows():
            tick = int(row[tick_col])
            if prev_tick is None or (tick - prev_tick) <= group_threshold:
                current_group.append(row)
            else:
                groups.append(current_group)
                current_group = [row]
            prev_tick = tick

        if current_group:
            groups.append(current_group)

        sequences: list[dict[str, Any]] = []
        for group_idx, group in enumerate(groups):
            first_kill_tick = int(group[0][tick_col])
            last_kill_tick = int(group[-1][tick_col])

            start_tick = max(
                self._MIN_VALID_TICK,
                first_kill_tick - self._ticks_from_seconds(self.start_seconds_before),
            )
            end_tick = last_kill_tick + self._ticks_from_seconds(self.end_seconds_after)
            # Setup happens a bit before start; clamp to the minimum valid tick.
            setup_tick = max(
                self._MIN_VALID_TICK,
                start_tick - self._ticks_from_seconds(1.0),
            )
            next_seq_tick = end_tick + self._NEXT_SEQ_PADDING

            # Pause a few ticks before recording starts so the loading
            # screen has fully cleared.
            pause_tick = max(setup_tick, start_tick - self._PAUSE_BEFORE_START)

            # Use attacker from the first kill in the group as the POV
            first_row = group[0]
            attacker_name = str(first_row[attacker_col]) if attacker_col else "unknown"

            clip_label = f"{demo_name}_{group_idx:04d}_{attacker_name}"
            # Build an absolute, unix-style path for the MIRV stream name.
            clip_path_unix = _to_unix_path(output_dir / clip_label)

            # ----------------------------------------------------------
            # Spectate command
            # ----------------------------------------------------------
            # Prefer entity slot if we have it (spec_player <slot>).
            # Fall back to account-ID based command, or plain spec_mode 1.
            slot = self.player_slots.get(attacker_name)
            if slot is not None:
                spec_cmd = f"spec_player {slot}"
            elif attacker_steamid_col and not pd.isna(first_row.get(attacker_steamid_col)):
                # Derive the 32-bit Steam account ID from the 64-bit SteamID.
                try:
                    steam64 = int(first_row[attacker_steamid_col])
                    account_id = steam64 & 0xFFFFFFFF
                    spec_cmd = f"spec_lock_to_accountid {account_id}"
                except (ValueError, TypeError):
                    spec_cmd = "spec_mode 1"
            else:
                spec_cmd = "spec_mode 1"

            # ----------------------------------------------------------
            # Build the actions list
            # ----------------------------------------------------------
            # Tick 64 — global one-time setup (mirrors getValidTick floor).
            global_setup_tick = self._MIN_VALID_TICK

            actions: list[dict[str, Any]] = [
                # ---- Global setup (tick 64) ----
                {"tick": global_setup_tick, "cmd": "sv_cheats 1"},
                {"tick": global_setup_tick, "cmd": "volume 1"},
                {"tick": global_setup_tick, "cmd": "cl_hud_telemetry_frametime_show 0"},
                {"tick": global_setup_tick, "cmd": "cl_hud_telemetry_net_misdelivery_show 0"},
                {"tick": global_setup_tick, "cmd": "cl_hud_telemetry_ping_show 0"},
                {"tick": global_setup_tick, "cmd": "cl_hud_telemetry_serverrecvmargin_graph_show 0"},
                {"tick": global_setup_tick, "cmd": "r_show_build_info 0"},
                {"tick": global_setup_tick, "cmd": "cl_draw_only_deathnotices 1"},
                {"tick": global_setup_tick, "cmd": "mirv_deathmsg lifetime 5"},
                {"tick": global_setup_tick, "cmd": "mirv_deathmsg filter clear"},
                {"tick": global_setup_tick, "cmd": "demo_ui_mode 0"},
                {"tick": global_setup_tick, "cmd": "demo_timescale 1"},
                # ---- Jump to just before setup_tick ----
                # Tick 1 so this fires immediately and jumps forward.
                {"tick": 1, "cmd": "demo_gototick " + str(setup_tick - 1)},
                # ---- Per-clip setup (setup_tick) ----
                {"tick": setup_tick, "cmd": "mirv_deathmsg clear"},
                {"tick": setup_tick, "cmd": "spec_show_xray 0"},
                # First-person POV locked to the killer
                {"tick": setup_tick, "cmd": spec_cmd},
                {"tick": setup_tick, "cmd": "spec_mode 6"},
                {"tick": setup_tick, "cmd": "spec_autodirector 0"},
                # ---- Pause just before recording to clear loading screen ----
                {"tick": pause_tick, "cmd": "pause_playback"},
                # ---- Recording via startmovie/endmovie (HLAE hooks this) ----
                {
                    "tick": start_tick,
                    "cmd": "host_framerate " + str(self.framerate),
                },
                {
                    "tick": start_tick,
                    "cmd": 'startmovie "' + clip_path_unix + '" tga',
                },
                {"tick": end_tick, "cmd": "endmovie"},
                {"tick": end_tick, "cmd": "host_framerate 0"},
                # ---- Advance to next sequence ----
                {"tick": next_seq_tick, "cmd": "go_to_next_sequence"},
            ]

            # Sort actions by tick (they should already be, but be safe)
            actions.sort(key=lambda a: a["tick"])

            sequences.append({"actions": actions})

        return sequences

    def write_json(self, sequences: list[dict[str, Any]], demo_path: str) -> Path:
        """Write sequences to ``{demo_path}.json``.

        The CS2 server plugin looks for ``{demo_path}.json`` by appending
        ``.json`` to the full demo filename (including ``.dem``), so
        ``test_demo.dem`` → ``test_demo.dem.json``.

        Args:
            sequences: List of sequence dicts as returned by
                :meth:`build_sequences`.
            demo_path: Path to the .dem file -- the JSON is written
                alongside it with ``.json`` appended to the full name.

        Returns:
            Path to the written JSON file.
        """
        p = Path(demo_path)
        out_path = p.with_name(p.name + ".json")
        out_path.write_text(
            json.dumps(sequences, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        return out_path

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _ticks_from_seconds(self, seconds: float) -> int:
        """Convert seconds to an integer tick count at the current tickrate."""
        return round(self.tickrate * seconds)

    @staticmethod
    def _find_col(df: pd.DataFrame, name: str) -> str | None:
        """Return column name if present, else None."""
        return name if name in df.columns else None
