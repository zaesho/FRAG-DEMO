"""Tests for the QueryEngine."""

from __future__ import annotations

import pandas as pd
import pytest

from frag_demo.query.engine import QueryEngine


@pytest.fixture()
def sample_kills() -> pd.DataFrame:
    """A small synthetic kills DataFrame matching the demoparser2 schema."""
    return pd.DataFrame(
        {
            "tick": [1000, 2000, 3000, 4000, 5000, 6000],
            "attacker_name": ["ZywOo", "s1mple", "NiKo", "ZywOo", "s1mple", "NiKo"],
            "attacker_steamid": ["76561198025798240"] * 2
            + ["76561198049899620"] * 2
            + ["76561198025798240"] * 2,
            "attacker_team_name": [
                "CT",
                "TERRORIST",
                "CT",
                "CT",
                "TERRORIST",
                "TERRORIST",
            ],
            "user_name": [
                "victim1",
                "victim2",
                "victim3",
                "victim4",
                "victim5",
                "victim6",
            ],
            "weapon": ["awp", "ak47", "deagle", "awp", "ak47", "m4a1"],
            "headshot": [False, True, True, False, False, True],
            "total_rounds_played": [1, 1, 2, 2, 3, 3],
            "is_warmup_period": [False] * 6,
        }
    )


@pytest.fixture()
def engine(sample_kills: pd.DataFrame) -> QueryEngine:
    return QueryEngine(sample_kills)


class TestFilterByPlayer:
    def test_exact_match(self, engine: QueryEngine) -> None:
        result = engine.query(player="ZywOo")
        assert len(result) == 2
        assert all(result["attacker_name"] == "ZywOo")

    def test_partial_match(self, engine: QueryEngine) -> None:
        result = engine.query(player="zyw")
        assert len(result) == 2

    def test_case_insensitive(self, engine: QueryEngine) -> None:
        result_lower = engine.query(player="zywoo")
        result_upper = engine.query(player="ZYWOO")
        assert len(result_lower) == len(result_upper) == 2

    def test_no_match(self, engine: QueryEngine) -> None:
        result = engine.query(player="nonexistent_player_xyz")
        assert result.empty

    def test_regex_characters_are_treated_literally(self) -> None:
        kills = pd.DataFrame(
            {
                "attacker_name": ["broky+", "brokyyy"],
                "weapon": ["awp", "awp"],
                "attacker_team_name": ["CT", "CT"],
            }
        )
        result = QueryEngine(kills).query(player="broky+")
        assert len(result) == 1
        assert result.iloc[0]["attacker_name"] == "broky+"


class TestFilterByWeapon:
    def test_exact_weapon(self, engine: QueryEngine) -> None:
        result = engine.query(weapon="awp")
        assert len(result) == 2
        assert all(result["weapon"] == "awp")

    def test_partial_weapon(self, engine: QueryEngine) -> None:
        result = engine.query(weapon="ak")
        assert len(result) == 2

    def test_pipe_separated_alternatives(self, engine: QueryEngine) -> None:
        result = engine.query(weapon="awp|deagle")
        assert len(result) == 3

    def test_comma_separated_alternatives(self, engine: QueryEngine) -> None:
        result = engine.query(weapon="awp,deagle")
        assert len(result) == 3

    def test_case_insensitive_weapon(self, engine: QueryEngine) -> None:
        result = engine.query(weapon="AWP")
        assert len(result) == 2


class TestFilterByHeadshot:
    def test_headshot_true(self, engine: QueryEngine) -> None:
        result = engine.query(headshot=True)
        assert len(result) == 3
        assert all(result["headshot"])

    def test_headshot_false(self, engine: QueryEngine) -> None:
        result = engine.query(headshot=False)
        assert len(result) == 3
        assert not any(result["headshot"])

    def test_headshot_none_returns_all(self, engine: QueryEngine) -> None:
        result = engine.query(headshot=None)
        assert len(result) == 6


class TestFilterByRound:
    def test_specific_round(self, engine: QueryEngine) -> None:
        result = engine.query(round_num=1)
        assert len(result) == 2
        assert all(result["total_rounds_played"] == 1)

    def test_round_range(self, engine: QueryEngine) -> None:
        result = engine.query(round_start=2, round_end=3)
        assert len(result) == 4
        assert result["total_rounds_played"].tolist() == [2, 2, 3, 3]

    def test_round_range_with_only_lower_bound(self, engine: QueryEngine) -> None:
        result = engine.query(round_start=2)
        assert len(result) == 4
        assert result["total_rounds_played"].tolist() == [2, 2, 3, 3]

    def test_round_range_with_only_upper_bound(self, engine: QueryEngine) -> None:
        result = engine.query(round_end=2)
        assert len(result) == 4
        assert result["total_rounds_played"].tolist() == [1, 1, 2, 2]

    def test_round_range_swaps_reversed_bounds(self, engine: QueryEngine) -> None:
        result = engine.query(round_start=3, round_end=2)
        assert len(result) == 4
        assert result["total_rounds_played"].tolist() == [2, 2, 3, 3]

    def test_nonexistent_round(self, engine: QueryEngine) -> None:
        result = engine.query(round_num=99)
        assert result.empty


class TestFilterBySide:
    def test_ct_side(self, engine: QueryEngine) -> None:
        result = engine.query(side="CT")
        assert len(result) == 3
        assert all(result["attacker_team_name"] == "CT")

    def test_t_side(self, engine: QueryEngine) -> None:
        result = engine.query(side="TERRORIST")
        assert len(result) == 3

    def test_case_insensitive_side(self, engine: QueryEngine) -> None:
        result_upper = engine.query(side="TERRORIST")
        result_lower = engine.query(side="terrorist")
        assert len(result_upper) == len(result_lower) == 3

    def test_short_t_alias_does_not_match_ct(self, engine: QueryEngine) -> None:
        result = engine.query(side="T")
        assert len(result) == 3
        assert all(result["attacker_team_name"] == "TERRORIST")


class TestCombinedFilters:
    def test_player_and_weapon(self, engine: QueryEngine) -> None:
        result = engine.query(player="ZywOo", weapon="awp")
        assert len(result) == 2

    def test_player_and_headshot(self, engine: QueryEngine) -> None:
        result = engine.query(player="s1mple", headshot=True)
        assert len(result) == 1

    def test_all_filters_no_match(self, engine: QueryEngine) -> None:
        result = engine.query(player="ZywOo", weapon="ak47")
        assert result.empty


class TestEmptyQuery:
    def test_empty_query_returns_all(self, engine: QueryEngine) -> None:
        result = engine.parse_natural_query("")
        assert len(result) == 6

    def test_empty_dataframe(self) -> None:
        empty_engine = QueryEngine(pd.DataFrame())
        result = empty_engine.query(player="zywoo")
        assert result.empty

    def test_whitespace_query_returns_all(self, engine: QueryEngine) -> None:
        result = engine.parse_natural_query("   ")
        assert len(result) == 6


class TestParseNaturalQuery:
    def test_player_only(self, engine: QueryEngine) -> None:
        result = engine.parse_natural_query("ZywOo")
        assert len(result) == 2

    def test_weapon_only(self, engine: QueryEngine) -> None:
        result = engine.parse_natural_query("awp")
        assert len(result) == 2

    def test_player_and_weapon(self, engine: QueryEngine) -> None:
        result = engine.parse_natural_query("zywoo awp")
        assert len(result) == 2

    def test_player_weapon_kills_filler(self, engine: QueryEngine) -> None:
        result = engine.parse_natural_query("zywoo awp kills")
        assert len(result) == 2

    def test_headshot_keyword(self, engine: QueryEngine) -> None:
        result = engine.parse_natural_query("headshot")
        assert len(result) == 3
        assert all(result["headshot"])

    def test_hs_alias(self, engine: QueryEngine) -> None:
        result = engine.parse_natural_query("hs")
        assert len(result) == 3
        assert all(result["headshot"])

    def test_player_hs(self, engine: QueryEngine) -> None:
        result = engine.parse_natural_query("s1mple hs")
        assert len(result) == 1
        assert result.iloc[0]["attacker_name"] == "s1mple"
        assert result.iloc[0]["headshot"] == True  # noqa: E712

    def test_round_keyword(self, engine: QueryEngine) -> None:
        result = engine.parse_natural_query("round 2")
        assert len(result) == 2
        assert all(result["total_rounds_played"] == 2)

    def test_player_weapon_with_aliases(self, engine: QueryEngine) -> None:
        result = engine.parse_natural_query("NiKo deag")
        assert len(result) == 1
        assert result.iloc[0]["weapon"] == "deagle"

    def test_weapon_alias_deag(self, engine: QueryEngine) -> None:
        result = engine.parse_natural_query("deag")
        assert len(result) == 1
        assert result.iloc[0]["weapon"] == "deagle"

    def test_weapon_alias_ak(self, engine: QueryEngine) -> None:
        result = engine.parse_natural_query("ak")
        assert len(result) == 2
        assert all(result["weapon"] == "ak47")

    def test_weapon_alias_m4(self, engine: QueryEngine) -> None:
        result = engine.parse_natural_query("m4")
        assert len(result) == 1
        assert result.iloc[0]["weapon"] == "m4a1"

    def test_canonical_weapon_query_is_exact(self) -> None:
        kills = pd.DataFrame(
            {
                "attacker_name": ["NiKo", "NiKo"],
                "weapon": ["m4a1", "m4a1_silencer"],
                "attacker_team_name": ["CT", "CT"],
                "headshot": [False, False],
                "total_rounds_played": [1, 1],
            }
        )
        result = QueryEngine(kills).parse_natural_query("m4a1")
        assert len(result) == 1
        assert result.iloc[0]["weapon"] == "m4a1"

    def test_ct_side_filter(self, engine: QueryEngine) -> None:
        result = engine.parse_natural_query("ct ak")
        assert result.empty

    def test_player_round(self, engine: QueryEngine) -> None:
        result = engine.parse_natural_query("s1mple round 1")
        assert len(result) == 1
        assert result.iloc[0]["attacker_name"] == "s1mple"
        assert result.iloc[0]["total_rounds_played"] == 1

    def test_invalid_round_clause_is_ignored(self, engine: QueryEngine) -> None:
        result = engine.parse_natural_query("NiKo round five")
        assert len(result) == 2
        assert all(result["attacker_name"] == "NiKo")

    def test_punctuation_is_ignored_while_parsing(self, engine: QueryEngine) -> None:
        result = engine.parse_natural_query("NiKo, deag!")
        assert len(result) == 1
        assert result.iloc[0]["weapon"] == "deagle"

    def test_full_complex_query(self, engine: QueryEngine) -> None:
        result = engine.parse_natural_query("s1mple headshot kills")
        assert len(result) == 1
        assert result.iloc[0]["attacker_name"] == "s1mple"
        assert result.iloc[0]["headshot"] == True  # noqa: E712
