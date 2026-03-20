"""Tests for the Flask app endpoints and request validation."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

import frag_demo.app as app_module


@pytest.fixture()
def client():
    app_module.app.config["TESTING"] = True
    app_module._reset_state()
    with app_module.app.test_client() as test_client:
        yield test_client
    app_module._reset_state()


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
