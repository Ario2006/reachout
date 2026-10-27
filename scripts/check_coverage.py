"""Fail if any listed safety/sending/tracking/extraction module is below 90% line coverage."""
import json
import sys
from pathlib import Path

THRESHOLD = 90.0
root = Path(__file__).resolve().parent.parent
files = json.loads(Path(sys.argv[1]).read_text())["files"]
bad = []
for line in (root / "scripts" / "safety_modules.txt").read_text().splitlines():
    mod = line.strip()
    if not mod or mod.startswith("#") or not (root / mod).exists():
        continue
    pct = files.get(mod, {}).get("summary", {}).get("percent_covered")
    if pct is None or pct < THRESHOLD:
        bad.append(f"{mod}: {'not measured' if pct is None else f'{pct:.1f}%'} (< {THRESHOLD:.0f}%)")
print("\n".join(bad) or "safety coverage OK")
sys.exit(1 if bad else 0)
