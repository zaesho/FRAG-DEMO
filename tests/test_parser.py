"""Tests for the DemoAnalyzer helpers."""

from __future__ import annotations

import pandas as pd

from frag_demo.parser.demo_parser import DemoAnalyzer


class _FakeParser:
    def __init__(self, events: pd.DataFrame, tick_results: dict[int, pd.DataFrame]) -> None:
        self._events = events
        self._tick_results = tick_results

    def parse_event(self, *_args, **_kwargs) -> pd.DataFrame:
        return self._events.copy()

    def parse_ticks(self, _fields: list[str], ticks: list[int] | None = None) -> pd.DataFrame:
        tick = ticks[0] if ticks else 0
        return self._tick_results.get(tick, pd.DataFrame()).copy()


class TestParseKills:
    def test_warmup_kills_are_filtered_out(self) -> None:
        analyzer = DemoAnalyzer.__new__(DemoAnalyzer)
        analyzer.parser = _FakeParser(
            pd.DataFrame(
                {
                    "tick": [100, 200],
                    "is_warmup_period": [True, False],
                }
            ),
            {},
        )

        result = analyzer.parse_kills()

        assert result["tick"].tolist() == [200]


class TestGetPlayers:
    def test_roster_probe_falls_back_from_128_to_64(self) -> None:
        analyzer = DemoAnalyzer.__new__(DemoAnalyzer)
        analyzer.parser = _FakeParser(
            pd.DataFrame(),
            {
                64: pd.DataFrame(
                    {
                        "player_name": ["ZywOo"],
                        "player_steamid": ["1"],
                        "team_name": ["CT"],
                    }
                )
            },
        )

        result = analyzer.get_players()

        assert result.iloc[0]["player_name"] == "ZywOo"

    def test_fallback_includes_victims_without_kills(self) -> None:
        analyzer = DemoAnalyzer.__new__(DemoAnalyzer)
        analyzer.parser = _FakeParser(
            pd.DataFrame(
                {
                    "attacker_name": ["killer"],
                    "attacker_steamid": ["1"],
                    "attacker_team_name": ["CT"],
                    "user_name": ["victim"],
                    "user_steamid": ["2"],
                    "user_team_name": ["TERRORIST"],
                    "is_warmup_period": [False],
                }
            ),
            {},
        )

        result = analyzer.get_players()

        assert set(result["player_name"]) == {"killer", "victim"}


class TestGetPlayerSlots:
    def test_player_slots_prefer_user_id_plus_one(self) -> None:
        analyzer = DemoAnalyzer.__new__(DemoAnalyzer)
        analyzer.parser = _FakeParser(
            pd.DataFrame(),
            {
                128: pd.DataFrame(
                    {
                        "player_name": ["dup"],
                        "player_steamid": ["765"],
                        "user_id": [4],
                        "entity_id": [99],
                    }
                )
            },
        )

        result = analyzer.get_player_slots()

        assert result["765"] == 5
        assert result["dup"] == 5

    def test_player_slots_include_steamid_keys(self) -> None:
        analyzer = DemoAnalyzer.__new__(DemoAnalyzer)
        analyzer.parser = _FakeParser(
            pd.DataFrame(),
            {
                128: pd.DataFrame(
                    {
                        "player_name": ["dup"],
                        "player_steamid": ["765"],
                        "entity_id": [7],
                    }
                )
            },
        )

        result = analyzer.get_player_slots()

        assert result["765"] == 7
        assert result["dup"] == 7
