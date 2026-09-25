"""Build the versioned SPIKE V13.0 indicator from the preserved V12.8 source.

The owner requested the previously studied breakout/retest/rebreak policy on
2026-09-25. The parent detector and trendline/joint state remain intact; only
the visible entry/reference layer is switched. Economic evidence remains the
frozen V12.8 retest experiment, not a new native-market backtest.

Pine v6 execution and confirmed bars:
https://www.tradingview.com/pine-script-docs/language/execution-model/
https://www.tradingview.com/pine-script-docs/concepts/bar-states/
"""
from __future__ import annotations

import argparse
import hashlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PINE = ROOT / "yoyo/evaluation/pine"
PARENT = PINE / "spike_burst_v12_8.pine"
OUTPUT = PINE / "spike_burst_v13_0.pine"
CORE = PINE / "spike_v130_retest_core.pine"
LAYER = PINE / "spike_v130_retest_layer.pine"

INPUTS = '''// BEGIN V130 INPUTS
// Only the entry timing policy changes. Original signal/line calculations
// still define the anchors, independently of this display-mode selection.
string gV130 = "00 · V13.0 回踩入场"
string v130Mode = input.string("回踩确认", "信号模式", options=["回踩确认", "原版参考"], group=gV130, tooltip="回踩确认：原信号后，依次收盘突破、触位守住、再突破首段极值；下一根开盘建立模型参考。原版参考恢复V12.8图表行为。", display=display.none)
bool v130Retest = v130Mode == "回踩确认"
string v130Arm = input.string("普通多空", "回踩候选来源", options=["普通多空", "联合多头"], group=gV130, tooltip="两种来源独立比较，不混合占仓。联合多头以原版突破+spike出现的K线为锚点；普通多空以原SPIKE确认为锚点。", display=display.none)
const int V130_WAIT = 24
bool v130ShowBase = input.bool(false, "叠加原始候选标记", group=gV130, tooltip="只显示原始候选，候选本身不是回踩入场；历史趋势线始终保留。", display=display.none)
bool v130LegacyVisible = not v130Retest or v130ShowBase
bool v130ShowLevels = input.bool(true, "显示最早待确认价位与首段极值", group=gV130, display=display.none)
bool v130ShowRisk = input.bool(true, "显示回踩风险参考", group=gV130, tooltip="沿用原信号绝对止损，按确认后次开重算R。收盘2R启动4ATR追踪；反向信号后次开结束。3R框是观察位，不是止盈。", display=display.none)
// END V130 INPUTS

'''


def render() -> str:
    """Apply bounded presentation edits without altering the parent engines."""
    text = PARENT.read_text()
    text = text.replace("//@version=6\n", "//@version=6\n// V13.0: frozen-anchor breakout/retest/rebreak; confirmed close, next-open model.\n// Dedicated delayed-entry risk state; preserve prior candidate and trend engines.\n", 1)
    text = text.replace('SPIKE V12.8 · 两次加仓提示', 'SPIKE V13.0 · 回踩再突破')
    text = text.replace('shorttitle="SPIKE V12.8"', 'shorttitle="SPIKE V13.0"')
    marker = "// ───────────── 原有参数与显示 ─────────────"
    assert text.count(marker) == 1
    text = text.replace(marker, INPUTS + marker)
    # The parent reference is still calculated for joint-anchor generation.
    # Its visuals/hints must not masquerade as delayed-trade references.
    # Pine counts data-window plots and alertcondition calls toward 64, even
    # when hidden. Retire ten duplicate legacy diagnostics (not calculations,
    # visible drawings or alerts) to reserve the ten V13 outputs. Native run
    # rejected the initial combined source at 74 plots (RE10140).
    retired_diagnostics = (
        '"收盘量比 RV"', '"前12根密集命中"', '"三根净进展 ATR"',
        '"三根量比"', '"V9 离六线距离 ATR"', '"V9 全部入场过滤通过"',
        '"确认参考起点"', '"参考结束事件（1保护 / 2反向 / 3未突破离场）"',
        '"V12.8 滚仓候选累计数（最多2）"', '"V12.8 滚仓候选事件（1=候选）"',
    )
    lines = []
    for line in text.splitlines():
        if line.startswith("plot(") and any(title in line for title in retired_diagnostics):
            continue
        if line.startswith(("bool showRisk =", "bool showExitLabels =", "bool showMilestones =", "bool v128Enabled =")):
            line += " and not v130Retest"
        if line.startswith("label signalTag ="):
            raise AssertionError("unexpected indentation in joint drawing")
        if "label signalTag = v126Minimal ?" in line or "label htfTag = v126Minimal ?" in line:
            line = line.replace("= v126Minimal ?", "= (v126Minimal or not v130LegacyVisible) ?")
        if line.startswith("plotshape(not v126Minimal") or line.startswith("plot(not v126Minimal and not v10OnlyJoint and confirmed"):
            line = line.replace("not v126Minimal", "v130LegacyVisible and not v126Minimal", 1)
        if line.startswith("if barstate.isconfirmed and not v10OnlyJoint and signalSide"):
            line = line.replace("if barstate", "if v130LegacyVisible and barstate", 1)
        if line.startswith("color v126LegacyTone ="):
            line = line.replace("= not v126Minimal", "= v130LegacyVisible and not v126Minimal", 1)
        if line == "if barstate.islast and showPanel":
            line += " and not v130Retest"
        if 'table.cell(panel, 0, 0, "SPIKE V12.8"' in line:
            line = line.replace('"SPIKE V12.8"', '"V13.0 · 原版参考"')
        # Preserve parent alert choices with explicit 'original' provenance.
        if line.startswith("alertcondition("):
            line = line.replace("alertcondition(", "alertcondition(not v130Retest and ", 1)
            line = line.replace("SPIKE V12.8", "SPIKE V13.0 原版").replace('"SPIKE 参考', '"SPIKE V13.0 原版参考')
        lines.append(line)
    text = "\n".join(lines) + "\n"
    # Preserve all historical region logic, polyline creation and A/B/C marks.
    text += "\n" + CORE.read_text() + "\n" + LAYER.read_text()
    return text


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    text = render()
    if args.check:
        if not OUTPUT.exists() or OUTPUT.read_text() != text:
            raise SystemExit("V13.0 source differs from its builder")
    else:
        OUTPUT.write_text(text)
    print(f"{OUTPUT.relative_to(ROOT)} sha256={hashlib.sha256(text.encode()).hexdigest()}")


if __name__ == "__main__":
    main()
