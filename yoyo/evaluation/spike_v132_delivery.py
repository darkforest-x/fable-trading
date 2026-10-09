"""Render SPIKE V13.2 from V13.1 (owner 2026-10-09, Notion #44 follow-up).

Owner: spikev13.1升级一个版本到 v13.2 当离线大于5%的时候给出提示 以及自动在最低点画水平射线 假如新低了
则射线更新为最低点. Per the SPIKE Pine delivery rule (CLAUDE.md, 2026-09-20) V13.1's source stays
untouched and V13.2 is a new file and a new private TradingView script. V13.2 = V13.1 plus:

  - one header line and the version in the indicator title and alert names (V13.1 -> V13.2),
  - max_lines_count 400 -> 500: V13.1 declared 400 for a 360-line peak; the new layer keeps at most
    2 x 40 retired rays plus 2 live ones,
  - the appended layer pine/spike_v132_distance_layer.pine (display and alerts only).

Nothing else changes; tests/evaluation/test_spike_v132_delivery.py reverses the edits and requires
V13.1 byte for byte.

Run: .venv/bin/python -m yoyo.evaluation.spike_v132_delivery
"""
from __future__ import annotations

import hashlib
from pathlib import Path

PINE = Path(__file__).parent / "pine"
PARENT = PINE / "spike_burst_v13_1.pine"
LAYER = PINE / "spike_v132_distance_layer.pine"
OUTPUT = PINE / "spike_burst_v13_2.pine"
HEADER = "// V13.2: V13.1 unchanged; adds the distance alert and the extreme ray (V132 DISTANCE LAYER).\n"
TITLE_OLD = 'indicator("SPIKE V13.1 · 回踩再突破", shorttitle="SPIKE V13.1"'
TITLE_NEW = 'indicator("SPIKE V13.2 · 回踩再突破", shorttitle="SPIKE V13.2"'
LINES_OLD, LINES_NEW = "max_lines_count=400", "max_lines_count=500"


def render() -> str:
    parent = PARENT.read_text()
    if parent.count(TITLE_OLD) != 1 or parent.count(LINES_OLD) != 1:
        raise ValueError("V13.1 header changed; review the V13.2 edits")
    first, rest = parent.split("\n", 1)
    body = first + "\n" + HEADER + rest
    body = body.replace(TITLE_OLD, TITLE_NEW).replace(LINES_OLD, LINES_NEW)
    body = "".join(line.replace("SPIKE V13.1", "SPIKE V13.2") if line.startswith("alertcondition(") else line
                   for line in body.splitlines(keepends=True))
    if not body.endswith("\n"):
        body += "\n"
    return body + "\n" + LAYER.read_text()


def main() -> None:
    made = render()
    OUTPUT.write_text(made)
    print(OUTPUT, len(made.splitlines()), "lines", hashlib.sha256(made.encode()).hexdigest())


if __name__ == "__main__":
    main()
