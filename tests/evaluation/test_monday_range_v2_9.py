"""Guard the single Pine entry-clock change; native fixtures test transitions."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OLD = (ROOT / "yoyo/evaluation/pine/monday_range_v2_8.pine").read_text()
NEW = (ROOT / "yoyo/evaluation/pine/monday_range_v2_9.pine").read_text()


def section(source, begin, end):
    return source.split(begin, 1)[1].split(end, 1)[0]


def test_range_signals_alerts_and_exit_management_are_preserved():
    assert section(OLD, "string timeGroup", "// === V2.2 fixed-timeframe") == section(
        NEW, "string timeGroup", "// === V2.2 fixed-timeframe")
    assert section(OLD, "txAdvance(", "txSignals() =>") == section(
        NEW, "txAdvance(", "txSignals() =>")
    assert section(OLD, "txSignals() =>", "txEngine() =>") == section(
        NEW, "txSignals() =>", "txEngine() =>")
    assert section(OLD, "txConfirmed() =>", "// The drawing layer") == section(
        NEW, "txConfirmed() =>", "// The drawing layer")


def test_default_15m_engine_only_changes_signal_router():
    old = section(OLD, "txEngine() =>", "txConfirmed() =>")
    new = section(NEW, "txEngine() =>", "txConfirmed() =>")
    new = new.replace("    [fiveEnd, fiveSide, fiveAtr] = txSignals()\n", "")
    new = new.replace(
        "if txEntryEligible(watch, txEntryTf, qClose, fiveSide, fiveEnd, quarterSide, quarterEnd)",
        "if qClose and txEligible(watch, quarterSide, quarterEnd)",
    )
    assert old == new
    assert 'input.string("15", "入场信号周期（分钟）", options=["15", "5"]' in NEW
    assert 'input.bool(false, "启用 1H 观察交易参考"' in NEW


def test_fixed_source_and_dynamic_entry_captions():
    assert 'request.security(syminfo.tickerid, "5", txPair()' in NEW
    assert '"\\n" + txEntryTf + "m信号收盘参考 "' in NEW
    assert '"观察 · 每个同向" + txEntryTf + "m信号独立入场"' in NEW
    assert "chart." not in section(NEW, "txEngine() =>", "txConfirmed() =>").replace(
        "user's chart.", "")
