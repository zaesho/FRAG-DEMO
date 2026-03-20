"""Quick test: parse the demo and show kills."""
import sys
import os
sys.stdout.reconfigure(encoding="utf-8")

from demoparser2 import DemoParser

demo_path = sys.argv[1] if len(sys.argv) > 1 else "demos/test_demo.dem"
p = DemoParser(demo_path)
kills = p.parse_event(
    "player_death",
    player=["last_place_name", "team_name"],
    other=["total_rounds_played", "is_warmup_period"],
)

# Filter warmup
kills = kills[kills["is_warmup_period"] == False]
print(f"Total kills (non-warmup): {len(kills)}")
print()

attackers = kills["attacker_name"].dropna().unique()
print(f"Players ({len(attackers)}):")
for name in sorted(attackers):
    kdf = kills[kills["attacker_name"] == name]
    weapons = kdf["weapon"].value_counts().to_dict()
    w_str = ", ".join(f"{w}:{c}" for w, c in weapons.items())
    print(f"  {name}: {len(kdf)} kills ({w_str})")

print()
print("Sample kills:")
for _, k in kills.head(15).iterrows():
    hs = " (HS)" if k.get("headshot", False) else ""
    rnd = k.get("total_rounds_played", "?")
    tick = k["tick"]
    attacker = k["attacker_name"]
    weapon = k.get("weapon", "?")
    victim = k["user_name"]
    print(f"  R{rnd} T{tick} | {attacker} [{weapon}{hs}] {victim}")
