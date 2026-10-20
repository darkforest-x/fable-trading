"""Guard the history-only change; native TradingView checks measure coverage."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PINE = ROOT / "yoyo/evaluation/pine"
OLD = (PINE / "monday_range_v2_11.pine").read_text()
NEW = (PINE / "monday_range_v2_12.pine").read_text()


def executable_lines(source):
    return [
        line.strip()
        for line in source.splitlines()
        if line.strip() and not line.lstrip().startswith("//")
    ]


def test_only_version_and_key_history_change():
    expected = OLD.replace("V2.11", "V2.12").replace(
        "calc_bars_count=txEnabled ? 12000 : 3200", "calc_bars_count=12000"
    )
    assert executable_lines(NEW) == executable_lines(expected)


def test_key_history_is_independent_of_trade_reference_and_chart_branch():
    requests = [
        line for line in NEW.splitlines()
        if "request.security" in line
        and ("confirmedKeyEngine()" in line or "keyEnginePair()" in line)
    ]
    assert len(requests) == 2
    assert all("calc_bars_count=12000" in line for line in requests)
    assert all("txEnabled" not in line for line in requests)
    assert 'input.bool(false, "启用多周期观察交易参考"' in NEW


def test_delivered_script_excludes_temporary_diagnostics():
    assert "QA_HISTORY" not in NEW
    assert "qaHistory" not in NEW
