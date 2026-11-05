"""Build yoyo/monitor/static/spike-alarm-replay.json: replayed >= 25-coin alarm times for the alert chart.

Owner 2026-11-05: major-coin alert charts should show every alarm over about a year. The live alert book
starts 2026-10-27, so older alarms come from the frozen replay of the live rule in
experiments/active/exp-monday-limit-alarm-entry-20261029-v1/alarms.csv.gz (Binance 5m first touches,
>= 25 distinct same-side 1.618 touches within 5 minutes, 30-minute cooldown, ledger ends 2026-09-15).
Only time and side are exported (side 1 = lower / long, -1 = upper / short in that file); the coin lists are
not in the replay output. The page labels these as 回测复算 and never mixes them with live acknowledgements.
"""
from __future__ import annotations

import csv
import gzip
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "experiments/active/exp-monday-limit-alarm-entry-20261029-v1/alarms.csv.gz"
OUT = ROOT / "yoyo/monitor/static/spike-alarm-replay.json"


def main() -> None:
    with gzip.open(SOURCE, "rt") as fh:
        rows = [r for r in csv.DictReader(fh) if r["threshold"] == "25"]
    alarms = sorted(({"ms": int(r["ms"]), "side": "lower" if r["side"] == "1" else "upper"} for r in rows), key=lambda a: a["ms"])
    if any(r["side"] not in ("1", "-1") for r in rows) or len({a["ms"] for a in alarms}) != len(alarms):
        raise SystemExit("unexpected side value or duplicate alarm time")
    payload = {"schema": 1, "source": "exp-monday-limit-alarm-entry-20261029-v1/alarms.csv.gz", "threshold": 25,
               "basis": "Binance 5m first touches, replayed live rule", "end_ms": alarms[-1]["ms"], "alarms": alarms}
    OUT.write_text(json.dumps(payload, separators=(",", ":")) + "\n")
    print(f"{len(alarms)} alarms -> {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
