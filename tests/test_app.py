"""Tests for the Flask app endpoints and request validation."""

from __future__ import annotations

import os
from pathlib import Path

import pandas as pd
import pytest

import frag_demo.app as app_module


@pytest.fixture()
def client(tmp_path: Path, monkeypatch):
    ui_state_dir = tmp_path / "ui-state"
    ui_state_path = ui_state_dir / "ui_state.json"
    monkeypatch.setattr(app_module, "_UI_STATE_DIR", ui_state_dir)
    monkeypatch.setattr(app_module, "_UI_STATE_PATH", ui_state_path)
    app_module.app.config["TESTING"] = True
    app_module._reset_state()
    with app_module.app.test_client() as test_client:
        yield test_client
    app_module._reset_state()


class StubAnalyzer:
    def __init__(self, demo_path: str) -> None:
        self.demo_path = demo_path

    def parse_kills(self) -> pd.DataFrame:
        return pd.DataFrame(
            [
                {
                    "tick": 1000,
                    "attacker_name": "NiKo",
                    "weapon": "ak47",
                    "total_rounds_played": 2,
                }
            ]
        )

    def parse_header(self) -> dict[str, object]:
        return {"map_name": "de_mirage", "tickrate": 64}

    def get_player_slots(self) -> dict[str, int]:
        return {}


class TestLoad:
    def test_failed_load_clears_previous_state(
        self, client, tmp_path: Path, monkeypatch
    ) -> None:
        demo_path = tmp_path / "broken.dem"
        demo_path.touch()

        app_module._state["demo_path"] = "stale.dem"
        app_module._state["header"] = {"map_name": "de_dust2"}
        app_module._state["kills_df"] = pd.DataFrame([{"tick": 1}])
        app_module._state["player_slots"] = {"old": 1}

        class BrokenAnalyzer:
            def __init__(self, demo_path: str) -> None:
                self.demo_path = demo_path

            def parse_kills(self) -> pd.DataFrame:
                raise RuntimeError("boom")

            def parse_header(self) -> dict:
                return {}

            def get_player_slots(self) -> dict[str, int]:
                return {}

        monkeypatch.setattr(app_module, "DemoAnalyzer", BrokenAnalyzer)

        response = client.post("/api/load", json={"demo_path": str(demo_path)})

        assert response.status_code == 500
        assert app_module._state["demo_path"] is None
        assert app_module._state["header"] is None
        assert app_module._state["kills_df"] is None
        assert app_module._state["player_slots"] is None

    def test_load_updates_recent_demo_history(self, client, tmp_path: Path, monkeypatch) -> None:
        demo_a = tmp_path / "match_a.dem"
        demo_b = tmp_path / "match_b.dem"
        demo_a.touch()
        demo_b.touch()

        monkeypatch.setattr(app_module, "DemoAnalyzer", StubAnalyzer)

        response_a = client.post("/api/load", json={"demo_path": str(demo_a)})
        assert response_a.status_code == 200

        response_b = client.post("/api/load", json={"demo_path": str(demo_b)})
        assert response_b.status_code == 200

        response_a2 = client.post("/api/load", json={"demo_path": str(demo_a)})
        assert response_a2.status_code == 200

        library = client.get("/api/library")
        assert library.status_code == 200
        payload = library.get_json()
        assert payload["ok"] is True
        assert payload["selected_demo_path"] == str(demo_a.resolve())
        assert [entry["path"] for entry in payload["recent_demos"]] == [
            str(demo_a.resolve()),
            str(demo_b.resolve()),
        ]


class TestLibrary:
    def test_library_bootstrap_is_empty(self, client) -> None:
        response = client.get("/api/library")
        assert response.status_code == 200
        payload = response.get_json()
        assert payload["ok"] is True
        assert payload["watched_folders"] == []
        assert payload["recent_demos"] == []
        assert payload["discovered_demos"] == []
        assert payload["selected_demo_path"] is None
        assert app_module._UI_STATE_PATH.exists()

    def test_add_and_remove_watch_folder(self, client, tmp_path: Path) -> None:
        watch_dir = tmp_path / "watch"
        watch_dir.mkdir()

        add_response = client.post(
            "/api/library/watch/add",
            json={"folder_path": str(watch_dir)},
        )
        assert add_response.status_code == 200
        add_payload = add_response.get_json()
        assert [f["path"] for f in add_payload["watched_folders"]] == [str(watch_dir.resolve())]

        remove_response = client.post(
            "/api/library/watch/remove",
            json={"folder_path": str(watch_dir)},
        )
        assert remove_response.status_code == 200
        remove_payload = remove_response.get_json()
        assert remove_payload["watched_folders"] == []

    def test_duplicate_watch_folder_is_deduped(self, client, tmp_path: Path) -> None:
        watch_dir = tmp_path / "watch"
        watch_dir.mkdir()

        first = client.post("/api/library/watch/add", json={"folder_path": str(watch_dir)})
        second = client.post("/api/library/watch/add", json={"folder_path": str(watch_dir)})

        assert first.status_code == 200
        assert second.status_code == 200
        payload = second.get_json()
        assert len(payload["watched_folders"]) == 1
        assert payload["watched_folders"][0]["path"] == str(watch_dir.resolve())

    def test_recursive_discovery_sorted_newest_first(self, client, tmp_path: Path) -> None:
        watch_dir = tmp_path / "watch"
        nested = watch_dir / "nested" / "deep"
        nested.mkdir(parents=True)
        top_demo = watch_dir / "old.dem"
        nested_demo = nested / "new.dem"
        top_demo.touch()
        nested_demo.touch()

        old_ts = 1_700_000_000
        new_ts = 1_700_000_100
        os.utime(top_demo, (old_ts, old_ts))
        os.utime(nested_demo, (new_ts, new_ts))

        add_response = client.post(
            "/api/library/watch/add",
            json={"folder_path": str(watch_dir)},
        )
        assert add_response.status_code == 200

        library_response = client.get("/api/library")
        assert library_response.status_code == 200
        payload = library_response.get_json()
        discovered_paths = [item["path"] for item in payload["discovered_demos"]]
        assert discovered_paths == [
            str(nested_demo.resolve()),
            str(top_demo.resolve()),
        ]

    def test_missing_watched_folder_is_reported(self, client, tmp_path: Path) -> None:
        watch_dir = tmp_path / "watch"
        watch_dir.mkdir()

        add_response = client.post(
            "/api/library/watch/add",
            json={"folder_path": str(watch_dir)},
        )
        assert add_response.status_code == 200

        watch_dir.rmdir()

        library_response = client.get("/api/library")
        payload = library_response.get_json()
        assert payload["watched_folders"][0]["exists"] is False

    def test_invalid_watch_folder_returns_400(self, client, tmp_path: Path) -> None:
        missing = tmp_path / "missing"
        response = client.post(
            "/api/library/watch/add",
            json={"folder_path": str(missing)},
        )
        assert response.status_code == 400

    def test_select_demo_path_persists(self, client, tmp_path: Path) -> None:
        demo_path = tmp_path / "selected.dem"
        response = client.post(
            "/api/library/select",
            json={"demo_path": str(demo_path)},
        )
        assert response.status_code == 200
        payload = response.get_json()
        assert payload["selected_demo_path"] == str(demo_path.resolve())

        library_response = client.get("/api/library")
        library_payload = library_response.get_json()
        assert library_payload["selected_demo_path"] == str(demo_path.resolve())


class TestKills:
    def test_kills_filters_by_round_range(self, client) -> None:
        app_module._state["kills_df"] = pd.DataFrame(
            [
                {
                    app_module._KILL_ID_COL: 0,
                    "tick": 1000,
                    "attacker_name": "NiKo",
                    "weapon": "ak47",
                    "total_rounds_played": 1,
                },
                {
                    app_module._KILL_ID_COL: 1,
                    "tick": 2000,
                    "attacker_name": "NiKo",
                    "weapon": "ak47",
                    "total_rounds_played": 2,
                },
                {
                    app_module._KILL_ID_COL: 2,
                    "tick": 3000,
                    "attacker_name": "NiKo",
                    "weapon": "ak47",
                    "total_rounds_played": 3,
                },
            ]
        )

        response = client.post(
            "/api/kills",
            json={"round_start": 2, "round_end": 3},
        )

        assert response.status_code == 200
        payload = response.get_json()
        assert payload["ok"] is True
        assert payload["total"] == 2
        assert [kill["total_rounds_played"] for kill in payload["kills"]] == [2, 3]

    def test_kills_round_range_tolerates_reversed_bounds(self, client) -> None:
        app_module._state["kills_df"] = pd.DataFrame(
            [
                {
                    app_module._KILL_ID_COL: 0,
                    "tick": 1000,
                    "attacker_name": "NiKo",
                    "weapon": "ak47",
                    "total_rounds_played": 1,
                },
                {
                    app_module._KILL_ID_COL: 1,
                    "tick": 2000,
                    "attacker_name": "NiKo",
                    "weapon": "ak47",
                    "total_rounds_played": 2,
                },
                {
                    app_module._KILL_ID_COL: 2,
                    "tick": 3000,
                    "attacker_name": "NiKo",
                    "weapon": "ak47",
                    "total_rounds_played": 3,
                },
            ]
        )

        response = client.post(
            "/api/kills",
            json={"round_start": 3, "round_end": 2},
        )

        assert response.status_code == 200
        payload = response.get_json()
        assert payload["ok"] is True
        assert payload["total"] == 2
        assert [kill["total_rounds_played"] for kill in payload["kills"]] == [2, 3]


class TestRecord:
    def test_selected_ids_pick_only_requested_rows(
        self, client, tmp_path: Path, monkeypatch
    ) -> None:
        demo_path = tmp_path / "match.dem"
        demo_path.touch()

        app_module._state["demo_path"] = str(demo_path)
        app_module._state["header"] = {"tickrate": 64}
        app_module._state["player_slots"] = {}
        app_module._state["kills_df"] = pd.DataFrame(
            [
                {
                    app_module._KILL_ID_COL: 0,
                    "tick": 1000,
                    "attacker_name": "NiKo",
                    "attacker_steamid": "1",
                },
                {
                    app_module._KILL_ID_COL: 1,
                    "tick": 1000,
                    "attacker_name": "s1mple",
                    "attacker_steamid": "2",
                },
            ]
        )

        captured_ids: list[int] = []

        class StubBuilder:
            def __init__(self, **kwargs) -> None:
                self.kwargs = kwargs

            def build_sequences(
                self, selected: pd.DataFrame, demo_path: str
            ) -> list[dict[str, list[dict[str, int | str]]]]:
                captured_ids.extend(selected[app_module._KILL_ID_COL].tolist())
                return [{"actions": [{"tick": 64, "cmd": "sv_cheats 1"}]}]

            def write_json(
                self, sequences: list[dict[str, object]], demo_path: str
            ) -> Path:
                out_path = Path(demo_path).with_name(Path(demo_path).name + ".json")
                out_path.write_text("[]", encoding="utf-8")
                return out_path

        monkeypatch.setattr(app_module, "SequenceBuilder", StubBuilder)

        response = client.post(
            "/api/record",
            json={
                "selected_ids": [1],
                "before": 3.0,
                "after": 2.0,
                "framerate": 60,
                "launch": False,
            },
        )

        assert response.status_code == 200
        assert captured_ids == [1]

    def test_invalid_record_values_return_400(self, client) -> None:
        app_module._state["demo_path"] = "match.dem"
        app_module._state["header"] = {"tickrate": 64}
        app_module._state["player_slots"] = {}
        app_module._state["kills_df"] = pd.DataFrame(
            [{app_module._KILL_ID_COL: 0, "tick": 1000}]
        )

        response = client.post(
            "/api/record",
            json={"selected_ids": [0], "before": "abc", "after": 2.0, "framerate": 60},
        )

        assert response.status_code == 400

    def test_zero_before_and_after_are_accepted(
        self, client, tmp_path: Path, monkeypatch
    ) -> None:
        demo_path = tmp_path / "match.dem"
        demo_path.touch()

        app_module._state["demo_path"] = str(demo_path)
        app_module._state["header"] = {"tickrate": 64}
        app_module._state["player_slots"] = {}
        app_module._state["kills_df"] = pd.DataFrame(
            [
                {
                    app_module._KILL_ID_COL: 0,
                    "tick": 1000,
                    "attacker_name": "NiKo",
                    "attacker_steamid": "1",
                }
            ]
        )

        captured_kwargs: dict[str, object] = {}

        class StubBuilder:
            def __init__(self, **kwargs) -> None:
                captured_kwargs.update(kwargs)

            def build_sequences(self, selected: pd.DataFrame, demo_path: str):
                return [{"actions": [{"tick": 64, "cmd": "sv_cheats 1"}]}]

            def write_json(self, sequences, demo_path: str) -> Path:
                out_path = Path(demo_path).with_name(Path(demo_path).name + ".json")
                out_path.write_text("[]", encoding="utf-8")
                return out_path

        monkeypatch.setattr(app_module, "SequenceBuilder", StubBuilder)

        response = client.post(
            "/api/record",
            json={
                "selected_ids": [0],
                "before": 0.0,
                "after": 0.0,
                "framerate": 60,
                "launch": False,
            },
        )

        assert response.status_code == 200
        assert captured_kwargs["start_seconds_before"] == 0.0
        assert captured_kwargs["end_seconds_after"] == 0.0

    def test_hud_mode_is_passed_to_sequence_builder(
        self, client, tmp_path: Path, monkeypatch
    ) -> None:
        demo_path = tmp_path / "match.dem"
        demo_path.touch()

        app_module._state["demo_path"] = str(demo_path)
        app_module._state["header"] = {"tickrate": 64}
        app_module._state["player_slots"] = {}
        app_module._state["kills_df"] = pd.DataFrame(
            [
                {
                    app_module._KILL_ID_COL: 0,
                    "tick": 1000,
                    "attacker_name": "NiKo",
                    "attacker_steamid": "1",
                }
            ]
        )

        captured_kwargs: dict[str, object] = {}

        class StubBuilder:
            def __init__(self, **kwargs) -> None:
                captured_kwargs.update(kwargs)

            def build_sequences(self, selected: pd.DataFrame, demo_path: str):
                return [{"actions": [{"tick": 64, "cmd": "sv_cheats 1"}]}]

            def write_json(self, sequences, demo_path: str) -> Path:
                out_path = Path(demo_path).with_name(Path(demo_path).name + ".json")
                out_path.write_text("[]", encoding="utf-8")
                return out_path

        monkeypatch.setattr(app_module, "SequenceBuilder", StubBuilder)

        response = client.post(
            "/api/record",
            json={
                "selected_ids": [0],
                "before": 3.0,
                "after": 2.0,
                "framerate": 60,
                "hud_mode": "none",
                "launch": False,
            },
        )

        assert response.status_code == 200
        assert captured_kwargs["hud_mode"] == "none"

    def test_invalid_hud_mode_returns_400(self, client) -> None:
        app_module._state["demo_path"] = "match.dem"
        app_module._state["header"] = {"tickrate": 64}
        app_module._state["player_slots"] = {}
        app_module._state["kills_df"] = pd.DataFrame(
            [{app_module._KILL_ID_COL: 0, "tick": 1000}]
        )

        response = client.post(
            "/api/record",
            json={
                "selected_ids": [0],
                "before": 3.0,
                "after": 2.0,
                "framerate": 60,
                "hud_mode": "bad-value",
                "launch": False,
            },
        )

        assert response.status_code == 400

    def test_launch_auto_closes_and_auto_encodes_after_recording(
        self, client, tmp_path: Path, monkeypatch
    ) -> None:
        demo_path = tmp_path / "match.dem"
        demo_path.touch()
        plugin_dll = tmp_path / "server.dll"
        plugin_dll.write_bytes(b"\x00")

        app_module._state["demo_path"] = str(demo_path)
        app_module._state["header"] = {"tickrate": 64}
        app_module._state["player_slots"] = {}
        app_module._state["kills_df"] = pd.DataFrame(
            [
                {
                    app_module._KILL_ID_COL: 0,
                    "tick": 1000,
                    "attacker_name": "NiKo",
                    "attacker_steamid": "1",
                }
            ]
        )

        captured_builder_kwargs: dict[str, object] = {}
        launched_demo_paths: list[str] = []
        auto_encoded: list[tuple[str, int, bool]] = []

        class StubBuilder:
            def __init__(self, **kwargs) -> None:
                captured_builder_kwargs.update(kwargs)

            def build_sequences(self, selected: pd.DataFrame, demo_path: str):
                return [{"actions": [{"tick": 64, "cmd": "sv_cheats 1"}]}]

            def write_json(self, sequences, demo_path: str) -> Path:
                out_path = Path(demo_path).with_name(Path(demo_path).name + ".json")
                out_path.write_text("[]", encoding="utf-8")
                return out_path

        class StubLauncher:
            def __init__(self) -> None:
                self.hlae_path = "HLAE.exe"
                self.cs2_path = "cs2.exe"

            def find_plugin_dll(self) -> Path:
                return plugin_dll

            def launch(self, demo_path: str) -> None:
                launched_demo_paths.append(demo_path)

        class InlineThread:
            def __init__(self, target, daemon: bool = False) -> None:
                self.target = target
                self.daemon = daemon

            def start(self) -> None:
                self.target()

        def fake_auto_encode(
            demo_path: str, *, framerate: int, concatenate: bool = True
        ) -> dict[str, object]:
            auto_encoded.append((demo_path, framerate, concatenate))
            return {"ok": True, "encoded": ["clip.mp4"], "errors": []}

        monkeypatch.setattr(app_module, "SequenceBuilder", StubBuilder)
        monkeypatch.setattr(app_module, "CS2Launcher", StubLauncher)
        monkeypatch.setattr(app_module.threading, "Thread", InlineThread)
        monkeypatch.setattr(app_module, "_encode_recorded_clips", fake_auto_encode)
        monkeypatch.setattr(app_module, "_is_cs2_running", lambda: False)

        response = client.post(
            "/api/record",
            json={
                "selected_ids": [0],
                "before": 2.0,
                "after": 1.0,
                "framerate": 60,
                "launch": True,
            },
        )

        assert response.status_code == 200
        payload = response.get_json()
        assert payload["ok"] is True
        assert payload["launched"] is True
        assert captured_builder_kwargs["close_game_after_recording"] is True
        assert launched_demo_paths == [str(demo_path)]
        assert auto_encoded == [(str(demo_path), 60, True)]


class TestEncode:
    def test_encode_uses_clip_dirs_from_generated_json(
        self, client, tmp_path: Path, monkeypatch
    ) -> None:
        demo_path = tmp_path / "match.dem"
        demo_path.touch()

        clip_dir = tmp_path / "custom_clip_output"
        take_dir = clip_dir / "take0000"
        take_dir.mkdir(parents=True)
        (take_dir / "00000.tga").touch()
        (take_dir / "00001.tga").touch()

        json_path = demo_path.with_name(demo_path.name + ".json")
        json_path.write_text(
            (
                '[{"actions": ['
                f'{{"tick": 64, "cmd": "mirv_streams record name \\"{clip_dir.as_posix()}\\""}}'
                "]}]"
            ),
            encoding="utf-8",
        )

        app_module._state["demo_path"] = str(demo_path)

        captured_inputs: list[str] = []

        class StubEncoder:
            def encode_sequence(
                self, input_dir: str, output_path: str, framerate: int = 60
            ) -> None:
                captured_inputs.append(input_dir)

            def concatenate(self, video_paths: list[str], output_path: str) -> None:
                raise AssertionError("concatenate should not be called for one clip")

        monkeypatch.setattr(app_module, "VideoEncoder", StubEncoder)

        response = client.post(
            "/api/encode",
            json={"framerate": 60, "concatenate": True},
        )

        assert response.status_code == 200
        payload = response.get_json()
        assert payload["ok"] is True
        assert payload["encoded"] == ["custom_clip_output.mp4"]
        assert captured_inputs == [str(take_dir)]
