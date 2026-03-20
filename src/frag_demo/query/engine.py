"""Query engine for filtering CS2 kill events."""

from __future__ import annotations

import pandas as pd

# ---------------------------------------------------------------------------
# Canonical CS2 weapon names
# ---------------------------------------------------------------------------

CS2_WEAPONS: set[str] = {
    "ak47",
    "m4a1",
    "m4a1_silencer",
    "awp",
    "deagle",
    "usp_silencer",
    "glock",
    "p250",
    "fiveseven",
    "tec9",
    "cz75_auto",
    "mp9",
    "mac10",
    "ump45",
    "p90",
    "mp7",
    "mp5sd",
    "bizon",
    "mag7",
    "nova",
    "xm1014",
    "sawedoff",
    "negev",
    "m249",
    "ssg08",
    "aug",
    "sg556",
    "famas",
    "galilar",
    "g3sg1",
    "scar20",
    "knife",
    "hegrenade",
    "flashbang",
    "smokegrenade",
    "molotov",
    "incgrenade",
    "decoy",
    "taser",
}

# Aliases map a friendly token to one or more canonical weapon names
_WEAPON_ALIASES: dict[str, list[str]] = {
    "deag": ["deagle"],
    "ak": ["ak47"],
    "m4": ["m4a1", "m4a1_silencer"],
    "scout": ["ssg08"],
    "auto": ["g3sg1", "scar20"],
    "usp": ["usp_silencer"],
    "galil": ["galilar"],
    "sg": ["sg556"],
    "hs": [],  # handled separately as headshot keyword
}

# Keywords that have special meaning and should NOT be treated as player names
_RESERVED_KEYWORDS: set[str] = {
    "kill",
    "kills",
    "headshot",
    "hs",
    "round",
    "ct",
    "t",
} | CS2_WEAPONS | set(_WEAPON_ALIASES.keys())


class QueryEngine:
    """Filters a kills DataFrame produced by :class:`DemoAnalyzer`."""

    def __init__(self, kills_df: pd.DataFrame) -> None:
        self.kills_df = kills_df.copy()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def query(
        self,
        player: str | None = None,
        weapon: str | None = None,
        headshot: bool | None = None,
        round_num: int | None = None,
        side: str | None = None,
    ) -> pd.DataFrame:
        """Filter kills based on criteria.

        Args:
            player: Case-insensitive partial match against
                ``attacker_name``.
            weapon: Case-insensitive partial match against ``weapon``
                column. May be a pipe- or comma-separated list of
                alternatives (used internally for weapon aliases).
            headshot: When ``True`` return only headshots; ``False``
                returns only non-headshots.
            round_num: Filter by ``total_rounds_played`` value.
            side: Case-insensitive match against
                ``attacker_team_name`` (e.g. ``"CT"`` or ``"T"``).

        Returns:
            Filtered DataFrame (may be empty).
        """
        df = self.kills_df

        if df.empty:
            return df

        if player is not None:
            col = self._find_col(df, "attacker_name")
            if col:
                values = df[col].fillna("").astype(str)
                mask = values.str.contains(player, case=False, na=False, regex=False)
                df = df[mask]

        if weapon is not None:
            col = self._find_col(df, "weapon")
            if col:
                values = df[col].fillna("").astype(str).str.lower()
                mask = pd.Series(False, index=df.index)
                separators_normalized = weapon.replace(",", "|")
                for part in (
                    w.strip().lower() for w in separators_normalized.split("|") if w.strip()
                ):
                    mask |= values.str.contains(part, na=False, regex=False)
                df = df[mask]

        if headshot is not None:
            col = self._find_col(df, "headshot")
            if col:
                df = df[df[col] == headshot]

        if round_num is not None:
            col = self._find_col(df, "total_rounds_played")
            if col:
                df = df[df[col] == round_num]

        if side is not None:
            col = self._find_col(df, "attacker_team_name")
            if col:
                values = df[col].fillna("").astype(str).str.upper()
                normalized_side = self._normalize_side(side)
                if normalized_side is None:
                    df = df[values.str.contains(side, case=False, na=False, regex=False)]
                else:
                    df = df[values == normalized_side]

        return df.reset_index(drop=True)

    def parse_natural_query(self, query_str: str) -> pd.DataFrame:
        """Parse a natural language query and return matching kills.

        Examples::

            engine.parse_natural_query("zywoo awp kills")
            engine.parse_natural_query("s1mple headshot kills")
            engine.parse_natural_query("niko deagle round 5")
            engine.parse_natural_query("ct ak hs")

        Recognised patterns:

        * Weapon names / aliases — mapped to the ``weapon`` filter.
        * ``headshot`` / ``hs`` — enables the headshot filter.
        * ``round <N>`` — filters by round number.
        * ``ct`` / ``t`` — filters by side.
        * Anything else is treated as a **player name**.

        Args:
            query_str: Free-form query string.

        Returns:
            Filtered DataFrame.
        """
        if not query_str.strip():
            return self.kills_df.copy()

        tokens = self._tokenize(query_str)

        player_tokens: list[str] = []
        weapon_alternatives: list[str] = []
        headshot: bool | None = None
        round_num: int | None = None
        side: str | None = None

        i = 0
        while i < len(tokens):
            token = tokens[i]

            # "round N" pattern
            if token == "round":
                if i + 1 < len(tokens):
                    try:
                        round_num = int(tokens[i + 1])
                    except ValueError:
                        i += 2
                        continue
                    i += 2
                    continue

                i += 1
                continue

            # headshot keywords
            if token in ("headshot", "hs"):
                headshot = True
                i += 1
                continue

            # side keywords
            if token == "ct":
                side = "CT"
                i += 1
                continue
            if token == "t" and len(token) == 1:
                side = "TERRORIST"
                i += 1
                continue

            # ignored filler words
            if token in ("kills", "kill", "with", "using", "in"):
                i += 1
                continue

            # weapon alias
            if token in _WEAPON_ALIASES:
                resolved = _WEAPON_ALIASES[token]
                if resolved:  # empty list means it's a special keyword handled above
                    weapon_alternatives.extend(resolved)
                i += 1
                continue

            # canonical weapon name
            if token in CS2_WEAPONS:
                weapon_alternatives.append(token)
                i += 1
                continue

            # everything else → player name fragment
            player_tokens.append(token)
            i += 1

        player = " ".join(player_tokens) if player_tokens else None

        result = self.query(
            player=player,
            headshot=headshot,
            round_num=round_num,
            side=side,
        )

        if weapon_alternatives:
            col = self._find_col(result, "weapon")
            if col:
                exact_weapons = {weapon.lower() for weapon in weapon_alternatives}
                values = result[col].fillna("").astype(str).str.lower()
                result = result[values.isin(exact_weapons)]

        return result.reset_index(drop=True)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _find_col(df: pd.DataFrame, name: str) -> str | None:
        """Return the actual column name if it exists, else None."""
        if name in df.columns:
            return name
        return None

    @staticmethod
    def _normalize_side(side: str) -> str | None:
        """Normalize common side labels to demoparser's team names."""
        normalized = side.strip().lower()
        if normalized in {"ct", "counterterrorist", "counter-terrorist"}:
            return "CT"
        if normalized in {"t", "terrorist"}:
            return "TERRORIST"
        return None

    @staticmethod
    def _tokenize(query_str: str) -> list[str]:
        """Split a natural-language query while stripping common punctuation."""
        return [
            token
            for token in (
                raw.strip(".,;:!?()[]{}\"'")
                for raw in query_str.lower().split()
            )
            if token
        ]
