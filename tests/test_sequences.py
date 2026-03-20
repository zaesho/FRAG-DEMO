"""Tests for the SequenceBuilder."""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from frag_demo.sequences.builder import SequenceBuilder


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

TICKRATE = 64.0


@pytest.fixture()
def builder() -> SequenceBuilder:
    return SequenceBuilder(
        tickrate=TICKRATE,
        start_seconds_before=3.0,
        end_seconds_after=2.0,
        framerate=60,
        output_path="output",
    )


def _make_kill(
    tick: int,
    attacker: str = "zywoo",
    weapon: str = "awp",
    steamid: str = "76561198025798240",
) -> dict:
    return {
        "tick": tick,
        "attacker_name": attacker,
        "attacker_steamid": steamid,
        "attacker_team_name": "CT",
        "user_name": "victim",
        "weapon": weapon,
        "headshot": False,
        "total_rounds_played": 1,
        "is_warmup_period": False,
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestSingleKillSequence:
    def test_returns_one_sequence(self, builder: SequenceBuilder) -> None:
        df = pd.DataFrame([_make_kill(tick=10000)])
        seqs = builder.build_sequences(df, "match.dem")
        assert len(seqs) == 1

    def test_sequence_has_actions_key(self, builder: SequenceBuilder) -> None:
        df = pd.DataFrame([_make_kill(tick=10000)])
        seqs = builder.build_sequences(df, "match.dem")
        assert "actions" in seqs[0]

    def test_actions_is_nonempty_list(self, builder: SequenceBuilder) -> None:
        df = pd.DataFrame([_make_kill(tick=10000)])
        seqs = builder.build_sequences(df, "match.dem")
        assert isinstance(seqs[0]["actions"], list)
        assert len(seqs[0]["actions"]) > 0

    def test_empty_dataframe_returns_no_sequences(self, builder: SequenceBuilder) -> None:
        seqs = builder.build_sequences(pd.DataFrame(), "match.dem")
        assert seqs == []


class TestGroupedKills:
    def test_kills_within_10_seconds_are_grouped(
        self, builder: SequenceBuilder
    ) -> None:
        gap = int(TICKRATE * 5)  # 5 seconds — within threshold
        df = pd.DataFrame(
            [
                _make_kill(tick=10000),
                _make_kill(tick=10000 + gap),
            ]
        )
        seqs = builder.build_sequences(df, "match.dem")
        assert len(seqs) == 1

    def test_kills_beyond_10_seconds_are_separate(
        self, builder: SequenceBuilder
    ) -> None:
        gap = int(TICKRATE * 15)  # 15 seconds — beyond threshold
        df = pd.DataFrame(
            [
                _make_kill(tick=10000),
                _make_kill(tick=10000 + gap),
            ]
        )
        seqs = builder.build_sequences(df, "match.dem")
        assert len(seqs) == 2

    def test_exactly_10_seconds_is_grouped(self, builder: SequenceBuilder) -> None:
        gap = int(TICKRATE * 10)  # exactly at threshold
        df = pd.DataFrame(
            [
                _make_kill(tick=10000),
                _make_kill(tick=10000 + gap),
            ]
        )
        seqs = builder.build_sequences(df, "match.dem")
        assert len(seqs) == 1

    def test_three_kills_two_groups(self, builder: SequenceBuilder) -> None:
        df = pd.DataFrame(
            [
                _make_kill(tick=5000),
                _make_kill(tick=5320),   # +5 s → same group
                _make_kill(tick=15000),  # +~150 s → new group
            ]
        )
        seqs = builder.build_sequences(df, "match.dem")
        assert len(seqs) == 2

    def test_kills_from_different_attackers_are_not_grouped(
        self, builder: SequenceBuilder
    ) -> None:
        df = pd.DataFrame(
            [
                _make_kill(tick=10000, attacker="zywoo", steamid="1"),
                _make_kill(tick=10200, attacker="s1mple", steamid="2"),
            ]
        )
        seqs = builder.build_sequences(df, "match.dem")
        assert len(seqs) == 2


class TestSequenceStructure:
    def test_required_cmds_present(self, builder: SequenceBuilder) -> None:
        df = pd.DataFrame([_make_kill(tick=10000)])
        seqs = builder.build_sequences(df, "match.dem")
        cmds = {a["cmd"] for a in seqs[0]["actions"]}

        assert "sv_cheats 1" in cmds
        assert "cl_drawhud 1" in cmds
        assert "mirv_streams record startMovieWav 1" in cmds
        assert "mirv_streams record start" in cmds
        assert "mirv_streams record end" in cmds
        assert any("go_to_next_sequence" in c for c in cmds)

    def test_hlae_uses_mirv_record_fps_not_host_framerate(
        self, builder: SequenceBuilder
    ) -> None:
        df = pd.DataFrame([_make_kill(tick=10000)])
        seqs = builder.build_sequences(df, "match.dem")
        cmds = [a["cmd"] for a in seqs[0]["actions"]]

        assert "mirv_streams record fps 60" in cmds
        assert "host_framerate 60" not in cmds
        assert "host_framerate 0" not in cmds

    def test_actions_sorted_by_tick(self, builder: SequenceBuilder) -> None:
        df = pd.DataFrame([_make_kill(tick=10000)])
        seqs = builder.build_sequences(df, "match.dem")
        ticks = [a["tick"] for a in seqs[0]["actions"]]
        assert ticks == sorted(ticks)

    def test_all_action_ticks_are_at_least_min_valid_tick(
        self, builder: SequenceBuilder
    ) -> None:
        df = pd.DataFrame([_make_kill(tick=10000)])
        seqs = builder.build_sequences(df, "match.dem")
        ticks = [a["tick"] for a in seqs[0]["actions"]]
        assert min(ticks) == builder._MIN_VALID_TICK

    def test_initial_demo_gototick_uses_min_valid_tick(
        self, builder: SequenceBuilder
    ) -> None:
        df = pd.DataFrame([_make_kill(tick=10000)])
        seqs = builder.build_sequences(df, "match.dem")
        action = next(a for a in seqs[0]["actions"] if a["cmd"].startswith("demo_gototick "))
        assert action["tick"] == builder._MIN_VALID_TICK

    def test_demo_gototick_target_is_clamped_for_early_kill(
        self, builder: SequenceBuilder
    ) -> None:
        df = pd.DataFrame([_make_kill(tick=100)])
        seqs = builder.build_sequences(df, "match.dem")
        action = next(a for a in seqs[0]["actions"] if a["cmd"].startswith("demo_gototick "))
        assert action["cmd"] == f"demo_gototick {builder._MIN_VALID_TICK}"

    def test_record_end_after_record_start(self, builder: SequenceBuilder) -> None:
        df = pd.DataFrame([_make_kill(tick=10000)])
        seqs = builder.build_sequences(df, "match.dem")
        actions = seqs[0]["actions"]

        start_tick = next(
            a["tick"] for a in actions if a["cmd"] == "mirv_streams record start"
        )
        end_tick = next(
            a["tick"] for a in actions if a["cmd"] == "mirv_streams record end"
        )
        assert end_tick > start_tick

    def test_go_to_next_after_record_end(self, builder: SequenceBuilder) -> None:
        df = pd.DataFrame([_make_kill(tick=10000)])
        seqs = builder.build_sequences(df, "match.dem")
        actions = seqs[0]["actions"]

        end_tick = next(
            a["tick"] for a in actions if a["cmd"] == "mirv_streams record end"
        )
        next_seq_tick = next(
            a["tick"] for a in actions if a["cmd"] == "go_to_next_sequence"
        )
        assert next_seq_tick > end_tick

    def test_start_tick_before_kill(self, builder: SequenceBuilder) -> None:
        kill_tick = 10000
        df = pd.DataFrame([_make_kill(tick=kill_tick)])
        seqs = builder.build_sequences(df, "match.dem")
        actions = seqs[0]["actions"]

        start_tick = next(
            a["tick"] for a in actions if a["cmd"] == "mirv_streams record start"
        )
        assert start_tick < kill_tick

    def test_end_tick_after_kill(self, builder: SequenceBuilder) -> None:
        kill_tick = 10000
        df = pd.DataFrame([_make_kill(tick=kill_tick)])
        seqs = builder.build_sequences(df, "match.dem")
        actions = seqs[0]["actions"]

        end_tick = next(
            a["tick"] for a in actions if a["cmd"] == "mirv_streams record end"
        )
        assert end_tick > kill_tick

    def test_spec_player_is_reapplied_at_start_and_kill_ticks(
        self, tmp_path: Path
    ) -> None:
        builder = SequenceBuilder(
            tickrate=TICKRATE,
            output_path=str(tmp_path),
            player_slots={"2": 9},
        )
        kill_tick = 10000
        df = pd.DataFrame([_make_kill(tick=kill_tick, attacker="zywoo", steamid="2")])
        seqs = builder.build_sequences(df, "match.dem")
        actions = seqs[0]["actions"]

        spec_player_ticks = [
            action["tick"] for action in actions if action["cmd"] == "spec_player 9"
        ]
        start_tick = next(
            a["tick"] for a in actions if a["cmd"] == "mirv_streams record start"
        )

        assert len(spec_player_ticks) >= 3
        assert start_tick in spec_player_ticks
        assert kill_tick in spec_player_ticks

    def test_record_name_uses_sanitized_attacker_name(
        self, builder: SequenceBuilder
    ) -> None:
        df = pd.DataFrame([_make_kill(tick=10000, attacker='../b:ad"name')])
        seqs = builder.build_sequences(df, "match.dem")
        record_name_cmd = next(
            a["cmd"]
            for a in seqs[0]["actions"]
            if a["cmd"].startswith('mirv_streams record name "')
        )
        assert "b_ad_name" in record_name_cmd
        assert '../b:ad"name' not in record_name_cmd

    def test_record_name_uses_sanitized_demo_name(
        self, builder: SequenceBuilder
    ) -> None:
        df = pd.DataFrame([_make_kill(tick=10000)])
        seqs = builder.build_sequences(df, 'bad demo"name?.dem')
        record_name_cmd = next(
            a["cmd"]
            for a in seqs[0]["actions"]
            if a["cmd"].startswith('mirv_streams record name "')
        )
        assert "bad_demo_name_0000_zywoo" in record_name_cmd
        assert 'bad demo"name?' not in record_name_cmd

    def test_non_hlae_recording_uses_startmovie_endmovie(self, tmp_path: Path) -> None:
        builder = SequenceBuilder(
            tickrate=TICKRATE,
            output_path=str(tmp_path),
            recording_system="startmovie",
        )
        df = pd.DataFrame([_make_kill(tick=10000)])
        seqs = builder.build_sequences(df, "match.dem")
        cmds = {a["cmd"] for a in seqs[0]["actions"]}

        assert any(cmd.startswith('startmovie "') for cmd in cmds)
        assert "endmovie" in cmds

    def test_spec_player_prefers_steamid_mapping_over_name(
        self, tmp_path: Path
    ) -> None:
        builder = SequenceBuilder(
            tickrate=TICKRATE,
            output_path=str(tmp_path),
            player_slots={"zywoo": 1, "2": 9},
        )
        df = pd.DataFrame([_make_kill(tick=10000, attacker="zywoo", steamid="2")])
        seqs = builder.build_sequences(df, "match.dem")

        spec_cmd = next(
            a["cmd"]
            for a in seqs[0]["actions"]
            if a["cmd"].startswith("spec_player")
        )
        assert spec_cmd == "spec_player 9"

    def test_spec_mode_precedes_spec_player_at_setup_tick(
        self, tmp_path: Path
    ) -> None:
        builder = SequenceBuilder(
            tickrate=TICKRATE,
            output_path=str(tmp_path),
            player_slots={"2": 9},
        )
        df = pd.DataFrame([_make_kill(tick=10000, attacker="zywoo", steamid="2")])
        seqs = builder.build_sequences(df, "match.dem")
        actions = seqs[0]["actions"]

        spec_mode_index = next(
            index
            for index, action in enumerate(actions)
            if action["cmd"] == "spec_mode 1"
        )
        spec_player_index = next(
            index
            for index, action in enumerate(actions)
            if action["cmd"] == "spec_player 9"
        )

        assert spec_mode_index < spec_player_index

    def test_hud_mode_all_keeps_full_hud(self, tmp_path: Path) -> None:
        builder = SequenceBuilder(
            tickrate=TICKRATE,
            output_path=str(tmp_path),
            hud_mode="all",
        )
        df = pd.DataFrame([_make_kill(tick=10000)])
        seqs = builder.build_sequences(df, "match.dem")
        cmds = {a["cmd"] for a in seqs[0]["actions"]}

        assert "cl_drawhud 1" in cmds
        assert "cl_draw_only_deathnotices 0" in cmds

    def test_hud_mode_none_hides_hud(self, tmp_path: Path) -> None:
        builder = SequenceBuilder(
            tickrate=TICKRATE,
            output_path=str(tmp_path),
            hud_mode="none",
        )
        df = pd.DataFrame([_make_kill(tick=10000)])
        seqs = builder.build_sequences(df, "match.dem")
        cmds = {a["cmd"] for a in seqs[0]["actions"]}

        assert "cl_drawhud 0" in cmds
        assert "cl_draw_only_deathnotices 0" in cmds


class TestWriteJson:
    def test_creates_file(self, builder: SequenceBuilder, tmp_path: Path) -> None:
        demo = tmp_path / "match.dem"
        demo.touch()
        df = pd.DataFrame([_make_kill(tick=10000)])
        seqs = builder.build_sequences(df, str(demo))
        out = builder.write_json(seqs, str(demo))
        assert out.exists()
        assert out.suffix == ".json"

    def test_json_is_valid_list(
        self, builder: SequenceBuilder, tmp_path: Path
    ) -> None:
        demo = tmp_path / "match.dem"
        demo.touch()
        df = pd.DataFrame([_make_kill(tick=10000)])
        seqs = builder.build_sequences(df, str(demo))
        out = builder.write_json(seqs, str(demo))

        loaded = json.loads(out.read_text(encoding="utf-8"))
        assert isinstance(loaded, list)
        assert len(loaded) == 1

    def test_json_roundtrip(self, builder: SequenceBuilder, tmp_path: Path) -> None:
        demo = tmp_path / "match.dem"
        demo.touch()
        df = pd.DataFrame(
            [_make_kill(tick=10000), _make_kill(tick=25000, attacker="s1mple")]
        )
        seqs = builder.build_sequences(df, str(demo))
        out = builder.write_json(seqs, str(demo))

        loaded = json.loads(out.read_text(encoding="utf-8"))
        assert len(loaded) == len(seqs)
        for orig, restored in zip(seqs, loaded):
            assert orig["actions"] == restored["actions"]

    def test_output_path_next_to_demo(
        self, builder: SequenceBuilder, tmp_path: Path
    ) -> None:
        demo = tmp_path / "mymatch.dem"
        demo.touch()
        df = pd.DataFrame([_make_kill(tick=1000)])
        seqs = builder.build_sequences(df, str(demo))
        out = builder.write_json(seqs, str(demo))
        # The CS2 server plugin appends ".json" to the full demo filename
        # (including ".dem"), so "mymatch.dem" -> "mymatch.dem.json".
        assert out.name == "mymatch.dem.json"
        assert out.parent == tmp_path


class TestTicksFromSeconds:
    def test_basic_conversion(self, builder: SequenceBuilder) -> None:
        assert builder._ticks_from_seconds(1.0) == 64
        assert builder._ticks_from_seconds(2.0) == 128
        assert builder._ticks_from_seconds(0.5) == 32

    def test_rounds_correctly(self, builder: SequenceBuilder) -> None:
        b = SequenceBuilder(tickrate=64.0)
        # 64 * 3.0 = 192
        assert b._ticks_from_seconds(3.0) == 192

    def test_128_tickrate(self) -> None:
        b = SequenceBuilder(tickrate=128.0)
        assert b._ticks_from_seconds(1.0) == 128


class TestBuilderValidation:
    def test_negative_start_padding_rejected(self) -> None:
        with pytest.raises(ValueError, match="start_seconds_before"):
            SequenceBuilder(start_seconds_before=-1.0)

    def test_negative_end_padding_rejected(self) -> None:
        with pytest.raises(ValueError, match="end_seconds_after"):
            SequenceBuilder(end_seconds_after=-1.0)

    def test_non_positive_framerate_rejected(self) -> None:
        with pytest.raises(ValueError, match="framerate"):
            SequenceBuilder(framerate=0)

    def test_invalid_hud_mode_rejected(self) -> None:
        with pytest.raises(ValueError, match="hud_mode"):
            SequenceBuilder(hud_mode="bad-mode")
