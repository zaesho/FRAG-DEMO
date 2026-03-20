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
