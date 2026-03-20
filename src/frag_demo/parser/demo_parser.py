"""Demo parsing module using demoparser2."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
from demoparser2 import DemoParser


class DemoAnalyzer:
    """Parses CS2 .dem files and extracts structured data."""

    def __init__(self, demo_path: str) -> None:
        self.demo_path = Path(demo_path)
        self.parser = DemoParser(str(self.demo_path))

    def parse_kills(self) -> pd.DataFrame:
        """Parse all player_death events from the demo.

        Returns a DataFrame with kill events including attacker/victim
        positions, team names, weapon used, and round information.
        """
        df = self.parser.parse_event(
            "player_death",
            player=["X", "Y", "Z", "last_place_name", "team_name", "player_steamid"],
            other=["total_rounds_played", "is_warmup_period"],
        )
        return df

    def parse_header(self) -> dict:
        """Parse the demo file header.

        Returns a dict with metadata such as map name, tickrate, and
        server name.
        """
        return dict(self.parser.parse_header())

    def parse_ticks(
        self,
        fields: list[str],
        ticks: list[int] | None = None,
    ) -> pd.DataFrame:
        """Parse per-tick data for the given fields.

        Args:
            fields: List of field names to extract (e.g. player_name,
                X, Y, Z).
            ticks: Optional list of specific ticks to retrieve. If None
                all ticks are returned.

        Returns:
            DataFrame indexed by tick with the requested columns.
        """
        return self.parser.parse_ticks(fields, ticks=ticks)

    def get_player_slots(self, probe_tick: int = 128) -> dict[str, int]:
        """Return a mapping of player name -> entity slot number.

        The entity slot (also called the player index) is required for the
        ``spec_player {slot}`` console command used by the CS Demo Manager
        plugin.  demoparser2 exposes it through the ``entity_id`` column
        when parsing per-tick data.

        Args:
            probe_tick: The demo tick to sample.  Defaults to 128 so that
                players are fully loaded.  Falls back to 64 if 128 yields
                no data.

        Returns:
            Dict mapping player_name -> entity_id (int).  Returns an empty
            dict if the information cannot be retrieved.
        """
        for tick in (probe_tick, 64, 1):
            try:
                df = self.parser.parse_ticks(
                    ["player_name", "entity_id"],
                    ticks=[tick],
                )
                if df is not None and not df.empty and "entity_id" in df.columns:
                    result: dict[str, int] = {}
                    for _, row in df.iterrows():
                        name = str(row.get("player_name", ""))
                        eid = row.get("entity_id")
                        if name and eid is not None and not pd.isna(eid):
                            if name not in result:
                                result[name] = int(eid)
                    if result:
                        return result
            except Exception:
                continue
        return {}

    def get_players(self) -> pd.DataFrame:
        """Return a DataFrame of unique players in the demo.

        Uses tick 1 to capture the player roster as present at the very
        start of the demo.  Falls back to extracting attacker
        name/steamid pairs from kill events if the tick parse produces
        an empty result.

        Returns:
            DataFrame with at least columns: player_name, player_steamid,
            team_name.
        """
        try:
            df = self.parser.parse_ticks(
                ["player_name", "player_steamid", "team_name"], ticks=[1]
            )
            if df is not None and not df.empty:
                return df.drop_duplicates(subset=["player_steamid"]).reset_index(
                    drop=True
                )
        except Exception:
            pass

        # Fallback: derive players from kill events
        kills = self.parse_kills()
        if kills.empty:
            return pd.DataFrame(
                columns=["player_name", "player_steamid", "team_name"]
            )

        attackers = (
            kills[["attacker_name", "attacker_steamid", "attacker_team_name"]]
            .rename(
                columns={
                    "attacker_name": "player_name",
                    "attacker_steamid": "player_steamid",
                    "attacker_team_name": "team_name",
                }
            )
            .dropna(subset=["player_steamid"])
            .drop_duplicates(subset=["player_steamid"])
        )
        return attackers.reset_index(drop=True)
