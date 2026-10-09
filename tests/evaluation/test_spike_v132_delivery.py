"""SPIKE V13.2 = V13.1 + version strings + line budget + an isolated distance layer."""
from __future__ import annotations

import re

from yoyo.evaluation import spike_v132_delivery as d


def test_render_matches_the_delivered_file():
    assert d.OUTPUT.read_text() == d.render()


def test_reversing_the_edits_gives_v131_byte_for_byte():
    made = d.render()
    body, layer = made.rsplit("\n" + d.LAYER.read_text(), 1)
    assert layer == ""
    body = body.replace(d.HEADER, "", 1).replace(d.TITLE_NEW, d.TITLE_OLD).replace(d.LINES_NEW, d.LINES_OLD)
    body = "".join(line.replace("SPIKE V13.2", "SPIKE V13.1") if line.startswith("alertcondition(") else line
                   for line in body.splitlines(keepends=True))
    assert body == d.PARENT.read_text()


def test_every_alert_and_the_title_carry_v132():
    made = d.render()
    assert 'shorttitle="SPIKE V13.2"' in made and "max_lines_count=500" in made
    alerts = [line for line in made.splitlines() if line.startswith("alertcondition(")]
    assert alerts and all("SPIKE V13.1" not in a for a in alerts)
    assert len(alerts) == sum(line.startswith("alertcondition(") for line in d.PARENT.read_text().splitlines())
    assert made.count('alert("SPIKE V13.2 线下方离线') == 1 and made.count('alert("SPIKE V13.2 线上方离线') == 1


def test_layer_only_writes_its_own_state_and_reads_the_v131_lines():
    layer = d.LAYER.read_text()
    targets = set(re.findall(r"^\s*([A-Za-z_]\w*)\s*:=", layer, flags=re.M))
    assert targets and all(t.startswith("v132") for t in targets)
    declared = set(re.findall(r"^(?:var\s+)?(?:bool|float|int|string|color|line|label|array<line>)\s+(\w+)\s*=", layer, flags=re.M))
    assert declared and all(name.startswith(("v132", "gV132")) for name in declared)
    assert "m15Ema120" in layer and "h1Sma60" in layer
    for name in ("v130", "signalSide", "confirmedSignal", "protection", "v128", "v10", "v11", "v112"):
        assert name not in layer


def test_layer_drawing_budget():
    layer = d.LAYER.read_text()
    assert layer.count("line.new(") == 2 and layer.count("label.new(") == 4
    assert 'maxval=40' in layer.split('"每个方向保留的历史射线"', 1)[1].split("\n", 1)[0]
    # V13.1 sits at Pine's plot limit: the layer must not add any plot-type output (RE10140 on the first save)
    # V13.1 is exactly at Pine's 64-plot limit; plot-type calls and alertcondition() both count
    # (RE10140: 74 plots on the first save, 67 with only the three alertconditions left)
    code = "\n".join(line for line in layer.splitlines() if not line.lstrip().startswith("//"))
    for call in ("plot(", "plotshape(", "plotchar(", "plotcandle(", "plotbar(", "bgcolor(", "barcolor(", "fill(",
                 "hline(", "alertcondition("):
        assert call not in code
