"""Guard the independent daily A-share indicator's timing and risk contract.

Pure numeric helper expressions are evaluated from the actual Pine source,
without reimplementing their formulas. This small evaluator covers those two
helpers only: it is not a Pine compiler or a TradingView execution backtest.
Lifecycle contract checks complement the Python research engine's path tests.
"""

from pathlib import Path
import math
import re
from types import SimpleNamespace

import pytest


SOURCE = Path(__file__).resolve().parents[1] / "yoyo/evaluation/pine/imacd_ashare_daily_long_v1.pine"
TEXT = SOURCE.read_text(encoding="utf-8")


def helper(name):
    lines = TEXT.splitlines()
    index = next(i for i, line in enumerate(lines) if line.startswith(name + "("))
    body = []
    for line in lines[index + 1 :]:
        if line and not line.startswith("    "):
            break
        if line.strip():
            body.append(line)
    return body


def evaluate(name, **arguments):
    """Execute only simple scalar statements actually present in Pine helpers."""
    env = dict(arguments, math=SimpleNamespace(min=min, max=max, abs=abs, floor=math.floor, round=lambda value: None if value is None else math.floor(value + .5)), PINE_NA=None)
    env["na"] = lambda value: value is None
    env["nz"] = lambda value, replacement=0.0: replacement if value is None else value
    env["f_lte"] = lambda a, b: evaluate("f_lte", a=a, b=b)

    def expression(value):
        value = re.sub(r"\bna\b(?!\s*\()", "PINE_NA", value)
        if "?" in value:
            condition, branches = value.split("?", 1)
            yes, no = branches.split(":", 1)
            value = "(" + yes + " if " + condition + " else " + no + ")"
        return eval(value.strip(), {"__builtins__": {}}, env)

    enabled = True
    result = None
    for raw in helper(name):
        statement = raw.strip()
        if statement.startswith("//"):
            continue
        depth = len(raw) - len(raw.lstrip())
        if depth == 4:
            enabled = True
        if statement.startswith("if "):
            enabled = bool(expression(statement[3:]))
            continue
        if not enabled:
            continue
        match = re.match(r"(?:(?:bool|float|int) )?(\w+)\s*(?::=|=)\s*(.*)$", statement)
        if match:
            env[match.group(1)] = expression(match.group(2))
        else:
            result = expression(statement)
    return result


def test_independent_indicator_has_no_short_orders_signals_or_fixed_targets():
    assert TEXT.startswith("//@version=6")
    assert 'indicator("SPIKE A股主板' in TEXT
    code = "\n".join(line for line in TEXT.splitlines() if not line.lstrip().startswith("//"))
    assert "strategy(" not in code
    assert "strategy." not in code
    assert not re.search(r"\b(?:shortSignal|exitShort|shortStart|shortEntry|trendSide|pendingSide)\b", code)
    assert not any(token in code for token in ("空头", "做空", "卖空", "固定R参考", "止盈价", "takeProfit"))
    alerts = [line for line in code.splitlines() if line.startswith("alertcondition(")]
    assert len(alerts) == 2
    assert alerts[0].startswith('alertcondition(longStart, "SPIKE 主板 · 多头启动"')
    assert alerts[1].startswith('alertcondition(longExit, "SPIKE 主板 · 持仓退出"')


def test_market_and_standard_daily_scope_is_enforced():
    assert 'syminfo.type == "stock"' in TEXT
    assert 'syminfo.prefix == "SSE"' in TEXT
    assert 'syminfo.prefix == "SZSE"' in TEXT
    assert 'syminfo.prefix == "SSE_DLY"' in TEXT
    assert 'syminfo.prefix == "SZSE_DLY"' in TEXT
    assert "timeframe.isdaily and timeframe.multiplier == 1 and chart.is_standard" in TEXT
    assert "if barstate.isfirst and not (ashare and dailyChart)\n    runtime.error(" in TEXT
    patterns = re.findall(r'str.match\(syminfo.ticker, "([^"]+)"\) != ""', TEXT)
    assert len(patterns) == 2
    for code in ("600519", "601138", "603501", "605499", "000001", "001979", "002594", "003816"):
        assert any(re.fullmatch(pattern, code) for pattern in patterns)
    for code in ("900901", "200002", "510300", "159915", "BTCUSDT.P", "600519X", "300750", "301308", "688981", "689009", "920002", "830001"):
        assert not any(re.fullmatch(pattern, code) for pattern in patterns)


def test_no_future_inputs_and_focus_does_not_backfill_release_signals():
    code = "\n".join(line for line in TEXT.splitlines() if not line.lstrip().startswith("//"))
    assert "request." not in code
    assert "lookahead" not in code
    assert not re.search(r"\[\s*-\d", code)
    assert "offset=" not in code
    assert "float candidateBand = focusAtrBand * atr[1]" in code
    assert "if magnitude <= focusBand" in code
    assert "focusBand := candidateBand" in code
    assert "releaseUp := md > focusBand" in code
    assert "focusRail := line.new(time, 0.0, time_close, 0.0" in code
    assert "bar_index >= warmupBars" in code
    assert "math.max(340, lengthMA * 10)" in code
    assert code.index("releaseHigh := focusHigh") < code.index("focusHigh := na", code.index("releaseHigh := focusHigh"))


def test_buy_and_exit_flags_are_only_set_inside_confirmed_bar_blocks():
    contexts = []
    for line in TEXT.splitlines():
        if not line.strip() or line.lstrip().startswith("//"):
            continue
        indent = len(line) - len(line.lstrip())
        contexts = [(depth, statement) for depth, statement in contexts if depth < indent]
        if re.search(r"\b(?:longStart|longExit|releaseUp)\s*:=", line):
            assert any("barstate.isconfirmed and ready" in statement for _, statement in contexts), line
        if line.strip().startswith("if "):
            contexts.append((indent, line.strip()))


@pytest.mark.parametrize("entry,structure,atr,floor,tick,expected", [
    (100.0, 95.0, 2.0, 3.0, 0.01, 94.0),
    (100.0, 90.0, 2.0, 3.0, 0.01, 90.0),
    (100.0, 90.006, 2.0, 3.0, 0.01, 90.0),
    (95.0, 95.0, 2.0, 3.0, 0.01, None),
    (90.0, 95.0, 2.0, 3.0, 0.01, None),
    (1.0, 0.5, 2.0, 3.0, 0.01, None),
    (100.0, 95.0, None, 3.0, 0.01, None),
])
def test_actual_initial_stop_expression_widens_rounds_outward_and_rejects_gap(entry, structure, atr, floor, tick, expected):
    actual = evaluate("f_initialStop", entry=entry, structureStop=structure, signalAtr=atr, floorAtr=floor, tick=tick)
    assert actual == pytest.approx(expected) if expected is not None else actual is None


@pytest.mark.parametrize("previous,highest,atr,close,expected", [
    (None, 110.0, 3.0, 109.0, 98.0),
    (102.0, 110.0, 3.0, 109.0, 102.0),
    (102.0, 120.0, 3.0, 116.0, 108.0),
    (102.0, 112.0, 3.0, 99.0, 102.0),
])
def test_actual_trail_expression_only_ratchets_and_rejects_line_above_close(previous, highest, atr, close, expected):
    actual = evaluate("f_trail", previous=previous, initialStop=90.0, highestClose=highest, currentAtr=atr, distance=4.0, currentClose=close)
    assert actual == pytest.approx(expected)


def test_frozen_r_t_plus_one_and_prior_protection_order():
    assert TEXT.count("initialStop :=") == 1
    assert TEXT.count("initialRisk :=") == 1
    assert "else if bar_index > entryBar and f_lte(low, initialStop)" in TEXT
    assert "exitPrice := math.min(open, initialStop)" in TEXT
    state = TEXT.split("if barstate.isconfirmed and ready\n    // Queued close decisions", 1)[1]
    assert state.index("if exitPending") < state.index("f_lte(low, initialStop)") < state.index("f_lte(close, protection)") < state.index("protection := f_trail")
    entry = state.split("if entryPending and bar_index > signalBar", 1)[1].split("if exitReference", 1)[0]
    assert entry.index("entryPending := false") < entry.index("if not onePrice")
    assert "if f_lte(low, initialStop)\n                exitPending := true" in entry
    assert "holding := false" not in entry
    assert "highestClose >= entryPrice + 1.5 * initialRisk" in state
    assert "structureCandidate = recentLow - 0.5 * atr" in TEXT
    assert "recentLow = ta.lowest(low, structureBars)" in TEXT
    assert 'int structureBars = input.int(10, "初始结构回看交易日"' in TEXT


def test_visuals_keep_zero_double_lines_six_ma_and_achieved_only_reward():
    assert 'hline(0, "零轴"' in TEXT
    assert 'plot(md, "IMACD"' in TEXT
    assert 'plot(sb, "信号线"' in TEXT
    assert "plot.style_histogram" not in TEXT
    for average in ("SMA20", "EMA20", "SMA60", "EMA60", "SMA120", "EMA120"):
        assert re.search(r'^plot\(showMa .*"' + average + '".*linewidth=1, force_overlay=true', TEXT, re.M)
    assert "rewardBox := box.new(time, highestPrice, time_close, entryPrice" in TEXT
    assert "box.set_top(rewardBox, highestPrice)" in TEXT
    assert "价格为信号收盘，并非实际买入成交" in TEXT
    assert "日线不能证明涨跌停排队可成交" in TEXT


@pytest.mark.parametrize("exchange,ticker,kind,expected", [
    ("SSE", "600519", "stock", True),
    ("SSE_DLY", "600519", "stock", True),
    ("SZSE", "000001", "stock", True),
    ("SZSE_DLY", "002594", "stock", True),
    ("SSE_DLY", "510300", "stock", False),
    ("SZSE_DLY", "200002", "stock", False),
    ("SSE_DLY", "600519", "index", False),
    ("OTHER", "600519", "stock", False),
    ("SZSE_DLY", "300750", "stock", False),
    ("SSE_DLY", "688981", "stock", False),
    ("BSE", "920002", "stock", False),
])
def test_delayed_feeds_keep_stock_and_numeric_a_share_checks(exchange, ticker, kind, expected):
    patterns = re.findall(r'str.match\(syminfo.ticker, "([^"]+)"\) != ""', TEXT)
    sh_code, sz_code = [bool(re.fullmatch(pattern, ticker)) for pattern in patterns]
    line = next(line for line in TEXT.splitlines() if line.startswith("bool ashare ="))
    expression = line.split(" = ", 1)[1].replace("syminfo.type", "kind").replace("syminfo.prefix", "exchange")
    assert eval(expression, {"__builtins__": {}}, dict(kind=kind, exchange=exchange, shCode=sh_code, szCode=sz_code)) is expected


@pytest.mark.parametrize("description,expected", [
    ("ST曙光", True),
    ("*ST鹏博", True),
    ("＊ST景峰", True),
    ("ST PANGDA", True),
    ("COMPANY (*ST)", False),
    ("KWEICHOW MOUTAI CO., LTD.", False),
    ("FIRST CAPITAL SECURITIES", False),
    ("BEST CORPORATION", False),
    ("STOCK EXAMPLE", False),
    ("STEEL INDUSTRY", False),
    ("INDUSTRY", False),
    ("  *ST TEST  ", True),
    ("", False),
])
def test_current_st_name_rejection_does_not_match_st_inside_ordinary_words(description, expected):
    pattern = re.search(r'str.match\(currentName, "([^"]+)"\)', TEXT).group(1)
    assert bool(re.search(pattern, description.strip().upper())) is expected
    assert 'if barstate.isfirst and currentStMarked\n    runtime.error(' in TEXT
    assert "名称中没有风险标识不证明当前或历史非ST" in TEXT
    assert "请核对当日ST状态" in TEXT


@pytest.mark.parametrize("factor", [0.01, 1.0, 1.1, 1.7, 3.7, 100.11])
def test_risk_helpers_are_invariant_to_price_coordinate_scale_at_exact_touch(factor):
    stop = evaluate("f_initialStop", entry=10.5*factor, structureStop=10.3*factor, signalAtr=.1*factor, floorAtr=3., tick=.01*factor)
    assert stop/factor == pytest.approx(10.2, abs=1e-10)
    assert evaluate("f_lte", a=10.2*factor, b=stop)
    assert evaluate("f_lte", a=(10.2-1e-7)*factor, b=stop)
    assert not evaluate("f_lte", a=(10.2+1e-7)*factor, b=stop)
    # The same comparator protects a close exactly on the prior trail.
    prior = evaluate("f_trail", previous=10.2*factor, initialStop=9.*factor, highestClose=11.*factor, currentAtr=.2*factor, distance=4., currentClose=11.*factor)
    assert evaluate("f_lte", a=10.2*factor, b=prior)
    assert not evaluate("f_lte", a=(10.2+1e-7)*factor, b=prior)


def test_tick_snapping_does_not_round_up_a_meaningfully_subtick_stop():
    stop = evaluate("f_initialStop", entry=100., structureStop=95.0099999, signalAtr=1., floorAtr=3., tick=.01)
    assert stop == pytest.approx(95.)
    assert not evaluate("f_lte", a=95.0000001, b=95.)
