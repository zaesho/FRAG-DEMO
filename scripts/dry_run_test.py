"""Dry run test script to show the JSON output for frag-demo record demos/test_demo.dem awp kills."""
import json
import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from frag_demo.sequences.builder import SequenceBuilder
import pandas as pd

builder = SequenceBuilder(tickrate=64.0, output_path="output")
df = pd.DataFrame([{
    "tick": 10000,
    "attacker_name": "zywoo",
    "attacker_steamid": "76561198025798240",
    "attacker_team_name": "CT",
    "user_name": "victim",
    "weapon": "awp",
    "headshot": False,
    "total_rounds_played": 1,
    "is_warmup_period": False,
}])
seqs = builder.build_sequences(df, "demos/test_demo.dem")
print(json.dumps(seqs, indent=2))
