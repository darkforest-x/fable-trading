"""Protect V12.10 trading semantics when adding the V12.12 15m 8-day trend entry gate.

V12.12 = V12.10 + filter F1 of exp-spike-v1210-filters-20261012-v1 (owner 2026-10-12): on 15m a long needs the
confirmed close above SMA(close, 800) of chart bars and a short needs it below. Owner chose to build it from
V12.10, not from the separate V12.11 display cleanup. The round trip proves every V12.10 expression survives
byte for byte; the block checks keep the new gate from writing parent state. These are source contracts, not a
Pine interpreter; native TradingView compilation is checked separately.
"""
import hashlib
from pathlib import Path
import re


HERE = Path(__file__).parent
PINE = HERE.parents[1] / "yoyo/evaluation/pine"
PARENT_SHA256 = "212dff05b7b27b0f7d26f876433469faea38c91468f6f8413a28ebcca2f3cd8b"
BLOCK = re.compile(r"// BEGIN V12\.12 TREND8D DIRECTION GATE\n(.*?)// END V12\.12 TREND8D DIRECTION GATE\n", re.S)
REASON = re.compile(r'v9Reasons \+= not trend8dApplies .*\n')
PLOTS = re.compile(r"^\s*(plot|plotshape|plotchar|plotcandle|plotbar|barcolor|bgcolor|fill)\(", re.M)


def _source() -> str:
    return (PINE / "spike_burst_v12_12.pine").read_text()


def _block() -> str:
    found = BLOCK.findall(_source())
    assert len(found) == 1
    return found[0]


def test_every_v1210_expression_is_preserved_byte_for_byte():
    parent = (PINE / "spike_burst_v12_10.pine").read_bytes()
    assert hashlib.sha256(parent).hexdigest() == PARENT_SHA256
    restored = BLOCK.sub("", _source())
    assert len(REASON.findall(restored)) == 1
    restored = REASON.sub("", restored)
    restored = restored.replace(" and volumaticEntryAllowed and trend8dEntryAllowed\n", " and volumaticEntryAllowed\n", 1)
    header = re.match(r"//@version=6\n((?://.*\n)+?)// New private version preserving V12\.10 behavior and adding the owner-requested entry gate\.\n", restored)
    assert header and header.group(1).startswith("// V12.12: V12.10 plus one 15m entry gate")
    restored = restored.replace(header.group(0), "//@version=6\n// V12.10: V12.9 entry admission additionally requires the same-timeframe Volumatic VIDYA trend.\n"
                                "// Stops, exits, fills and every other V12.9 gate are unchanged.\n"
                                "// New private version preserving V12.9 behavior and adding the owner-requested entry gate.\n", 1)
    restored = restored.replace('indicator("SPIKE V12.12 · 8天均线方向门", shorttitle="SPIKE V12.12",',
                                'indicator("SPIKE V12.10 · Volumatic VIDYA方向门", shorttitle="SPIKE V12.10",', 1)
    # Version identity in titles, alerts and the panel is the only other permitted edit.
    assert restored.replace("SPIKE V12.12", "SPIKE V12.10").encode() == parent


def test_gate_matches_the_tested_filter():
    block = _block()
    assert "const int TREND8D_LEN = 800" in block
    assert "float trend8dSma = ta.sma(close, TREND8D_LEN)" in block
    assert "bool trend8dApplies = useTrend8dGate and timeframe.isminutes and timeframe.multiplier == 15" in block
    assert "bool trend8dReady = not na(trend8dSma)" in block
    assert "rawSignalSide == 1 ? close > trend8dSma : rawSignalSide == -1 ? close < trend8dSma : false" in block
    assert "bool trend8dEntryAllowed = not trend8dApplies or (trend8dReady and trend8dDirectionAllowed)" in block
    assert re.search(r"bool v9EntryAllowed = .* and volumaticEntryAllowed and trend8dEntryAllowed\n", _source())
    assert 'input.bool(true, "15分钟只顺8天均线开仓"' in block


def test_gate_block_cannot_write_parent_state_or_place_orders():
    code = "\n".join(line.split("//")[0] for line in _block().splitlines())
    assert not re.findall(r"^\s*[\w.]+\s*(?::=|\+=|-=)", code, re.M)
    declared = re.findall(r"^\s*(?:string|bool|float|int|const int)\s+(\w+)\s*=", code, re.M)
    assert declared and all(name.lower().startswith(("trend8d", "usetrend8d", "gtrend8d")) for name in declared), declared
    assert not re.search(r"\bstrategy\.|\balert\(|\btable\.|\bplot", code)


def test_no_new_plots_and_built_from_v1210_not_v1211():
    source, parent = _source(), (PINE / "spike_burst_v12_10.pine").read_text()
    assert len(PLOTS.findall(source)) == len(PLOTS.findall(parent))
    assert "清爽显示" not in source and "SPK V12.11" not in source
    assert 'shorttitle="SPIKE V12.12"' in source
