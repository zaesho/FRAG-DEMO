"""Inspect the generated sequences JSON file."""
import json
import sys

sys.stdout.reconfigure(encoding="utf-8")

path = sys.argv[1] if len(sys.argv) > 1 else "demos/test_demo.dem.json"
with open(path, "r", encoding="utf-8") as f:
    data = json.load(f)

count = len(data)
print("Sequences: %d" % count)
for i, seq in enumerate(data):
    actions = seq["actions"]
    num = len(actions)
    print("\nSequence %d: %d actions" % (i + 1, num))
    for a in actions:
        tick = a["tick"]
        cmd = a["cmd"]
        print("  T%6d | %s" % (tick, cmd))
