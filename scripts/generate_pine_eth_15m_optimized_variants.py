#!/usr/bin/env python3
"""Generate causal ETH 15m Pine optimization variants from frozen V9.

The owner requested executable Pine versions of the development-only six-MA
W8 cross-imbalance candidate and the separately selected trend TP/SL arm.
This generator keeps the variants physically separate so entry attribution,
reversal semantics and barrier attribution cannot be mixed accidentally:

* V12F gates the complete signal state machine, matching the existing dynamic
  W8 research replay (rejected guarded signals do not reverse or consume
  cooldown);
* V12E leaves raw signal/cooldown semantics intact, always closes an opposite
  position, and applies W8 only to opening the next position;
* V12T leaves signals unchanged and freezes a 30% target distance in ticks at
  the confirmed signal close, with ATR3 and the existing 3% stop cap.

All six moving averages use close and the causal SMA/EMA 20/60/120 renderer
contract. Crosses at bar t use only t and t-1, while the W8 rolling counts use
[t-7, t]. The outputs remain paper-only until official TradingView trade
export parity passes; this script reads no market data or holdout artifact.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path


PROJECT = Path(__file__).resolve().parents[1]
PINE_DIR = PROJECT / "experiments/active/exp-pine-eth-15m-v1/pine"
SOURCE = PINE_DIR / "allin_eth_15m_v9_research.pine"
V12_FULL_OUTPUT = PINE_DIR / "allin_eth_15m_v12f_ma6_w8_full_gate_paper.pine"
V12_ENTRY_OUTPUT = PINE_DIR / "allin_eth_15m_v12e_ma6_w8_entry_only_paper.pine"
V12_TBSL_OUTPUT = PINE_DIR / "allin_eth_15m_v12t_tbsl_paper.pine"
MANIFEST = PINE_DIR / "optimized_variants_manifest.json"

SIX_MA_PAIRS = (
    ("ropeSma20", "ropeSma60"),
    ("ropeSma20", "ropeEma60"),
    ("ropeEma20", "ropeSma60"),
    ("ropeEma20", "ropeEma60"),
    ("ropeSma20", "ropeSma120"),
    ("ropeSma20", "ropeEma120"),
    ("ropeEma20", "ropeSma120"),
    ("ropeEma20", "ropeEma120"),
    ("ropeSma60", "ropeSma120"),
    ("ropeSma60", "ropeEma120"),
    ("ropeEma60", "ropeSma120"),
    ("ropeEma60", "ropeEma120"),
)


def replace_once(text: str, old: str, new: str, *, label: str) -> str:
    """Replace one exact source block and fail on source drift."""

    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected one source block, found {count}")
    return text.replace(old, new, 1)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _cross_terms(function: str) -> str:
    return " +\n".join(
        f"     {function}({fast}, {slow})" for fast, slow in SIX_MA_PAIRS
    )


def _six_ma_function_block() -> str:
    return (
        "f_crossUpEvent(float fast, float slow) =>\n"
        "    ta.crossover(fast, slow) ? 1.0 : 0.0\n\n"
        "f_crossDownEvent(float fast, float slow) =>\n"
        "    ta.crossunder(fast, slow) ? 1.0 : 0.0\n\n"
    )


def _six_ma_feature_block() -> str:
    return (
        "float ropeSma20 = ta.sma(close, 20)\n"
        "float ropeEma20 = ta.ema(close, 20)\n"
        "float ropeSma60 = ta.sma(close, 60)\n"
        "float ropeEma60 = ta.ema(close, 60)\n"
        "float ropeSma120 = ta.sma(close, 120)\n"
        "float ropeEma120 = ta.ema(close, 120)\n"
        "float sixMaCrossUpEvents =\n"
        f"{_cross_terms('f_crossUpEvent')}\n"
        "float sixMaCrossDownEvents =\n"
        f"{_cross_terms('f_crossDownEvent')}\n"
        "float sixMaCrossUpCountW8 = math.sum(sixMaCrossUpEvents, SIX_MA_CROSS_WINDOW)\n"
        "float sixMaCrossDownCountW8 = math.sum(sixMaCrossDownEvents, SIX_MA_CROSS_WINDOW)\n"
        "bool sixMaReady = not na(ropeSma120) and not na(ropeEma120) and "
        "not na(sixMaCrossUpCountW8) and not na(sixMaCrossDownCountW8)\n"
        "float sixMaCrossImbalanceLongW8 = sixMaCrossUpCountW8 - sixMaCrossDownCountW8\n"
        "float sixMaCrossImbalanceShortW8 = -sixMaCrossImbalanceLongW8\n"
        "bool sixMaLongPass = sixMaReady and "
        "sixMaCrossImbalanceLongW8 >= SIX_MA_CROSS_THRESHOLD\n"
        "bool sixMaShortPass = sixMaReady and "
        "sixMaCrossImbalanceShortW8 >= SIX_MA_CROSS_THRESHOLD\n"
    )


def _add_six_ma_contract(text: str) -> str:
    text = replace_once(
        text,
        "const int SLOW_SLOPE_LAG = 12\n",
        "const int SLOW_SLOPE_LAG = 12\n"
        "const int SIX_MA_CROSS_WINDOW = 8\n"
        "const float SIX_MA_CROSS_THRESHOLD = 0.0\n",
        label="six-MA constants",
    )
    text = replace_once(
        text,
        "float source = hl2\n",
        _six_ma_function_block() + "float source = hl2\n",
        label="six-MA event helpers",
    )
    text = replace_once(
        text,
        "float atr = ta.atr(ATR_LEN)\n",
        "float atr = ta.atr(ATR_LEN)\n" + _six_ma_feature_block(),
        label="six-MA causal features",
    )
    return text


def _paper_header(source: str, *, version: str, title: str, explanation: str) -> str:
    text = source.replace("V9", version)
    text = replace_once(
        text,
        f"// ALLIN ETH 15m {version} Research — frozen causal research candidate.\n"
        "// Research only: not TradingView-parity-approved, forward-eligible or production-eligible.",
        f"// ALLIN ETH 15m {version} {title} — post-selection paper hypothesis.\n"
        f"// Paper only: {explanation}",
        label=f"{version} header",
    )
    return replace_once(
        text,
        f'     "ALLIN ETH 15m {version} Research",\n',
        f'     "ALLIN ETH 15m {version} Paper",\n',
        label=f"{version} strategy title",
    )


def _mark_paper(text: str, *, version: str, hud_status: str) -> str:
    text = text.replace(
        f"RESEARCH ONLY | ALLIN ETH 15m {version}",
        f"PAPER ONLY | ALLIN ETH 15m {version}",
    )
    text = text.replace(f"RESEARCH ONLY | {version}", f"PAPER ONLY | {version}")
    text = text.replace("RESEARCH / PARITY REQUIRED", hud_status)
    return text


def build_v12_full_gate(source: str) -> str:
    """Build W8 full-state gate matching the selected dynamic replay."""

    text = _paper_header(
        source,
        version="V12F",
        title="MA6-W8 full gate",
        explanation="W8 gates entries, reversals and guarded cooldown transitions.",
    )
    text = _add_six_ma_contract(text)
    text = replace_once(
        text,
        "bool rawSignal = rawLong or rawShort\n"
        "bool skippedSignal = rawSignal and tradesToSkip > 0\n"
        "if skippedSignal\n"
        "    tradesToSkip -= 1\n\n"
        "bool commonAllowed = barstate.isconfirmed and dateAllowed and timeAllowed and dayAllowed and volatilityAllowed and not skippedSignal\n"
        "bool longSignal = rawLong and commonAllowed\n"
        "bool shortSignal = rawShort and commonAllowed\n",
        "// The historical dynamic gate scores only guarded candidates. A raw\n"
        "// signal outside those guards must still consume cooldown like V9,\n"
        "// while a guarded W8 rejection suppresses the entire state transition.\n"
        "bool gateCandidateEligible = dateAllowed and timeAllowed and dayAllowed and volatilityAllowed\n"
        "bool gatedRawLong = rawLong and (not gateCandidateEligible or sixMaLongPass)\n"
        "bool gatedRawShort = rawShort and (not gateCandidateEligible or sixMaShortPass)\n"
        "bool rawSignal = gatedRawLong or gatedRawShort\n"
        "bool skippedSignal = rawSignal and tradesToSkip > 0\n"
        "if skippedSignal\n"
        "    tradesToSkip -= 1\n\n"
        "bool commonAllowed = barstate.isconfirmed and dateAllowed and timeAllowed and dayAllowed and volatilityAllowed and not skippedSignal\n"
        "bool longSignal = gatedRawLong and commonAllowed\n"
        "bool shortSignal = gatedRawShort and commonAllowed\n",
        label="V12F full-state gate",
    )
    return _mark_paper(
        text,
        version="V12F",
        hud_status="PAPER MA6-W8 FULL / PARITY REQUIRED",
    )


def build_v12_entry_only(source: str) -> str:
    """Build W8 entry-only gate with unconditional opposite-signal exits."""

    text = _paper_header(
        source,
        version="V12E",
        title="MA6-W8 entry-only",
        explanation="W8 filters new positions; original signals still close opposites.",
    )
    text = _add_six_ma_contract(text)
    text = replace_once(
        text,
        "if longSignal and strategy.position_size <= 0.0 and targetQuantity > 0.0\n"
        "    pendingLongStopTicks := signalStopTicks\n"
        "    strategy.entry(\"Long\", strategy.long, qty = targetQuantity, comment = \"V12E confirmed long\", alert_message = longEntryAlert)\n"
        "    strategy.exit(\"Exit Long\", \"Long\", loss = signalStopTicks, comment = \"Initial stop\", alert_message = longExitAlert)\n\n"
        "if shortSignal and strategy.position_size >= 0.0 and targetQuantity > 0.0\n"
        "    pendingShortStopTicks := signalStopTicks\n"
        "    strategy.entry(\"Short\", strategy.short, qty = targetQuantity, comment = \"V12E confirmed short\", alert_message = shortEntryAlert)\n"
        "    strategy.exit(\"Exit Short\", \"Short\", loss = signalStopTicks, comment = \"Initial stop\", alert_message = shortExitAlert)\n",
        "if longSignal and strategy.position_size <= 0.0\n"
        "    if sixMaLongPass and targetQuantity > 0.0\n"
        "        pendingLongStopTicks := signalStopTicks\n"
        "        strategy.entry(\"Long\", strategy.long, qty = targetQuantity, comment = \"V12E W8 long\", alert_message = longEntryAlert)\n"
        "        strategy.exit(\"Exit Long\", \"Long\", loss = signalStopTicks, comment = \"Initial stop\", alert_message = longExitAlert)\n"
        "    else if strategy.position_size < 0.0\n"
        "        strategy.close(\"Short\", comment = \"V12E rejected long closes short\", alert_message = shortExitAlert)\n\n"
        "if shortSignal and strategy.position_size >= 0.0\n"
        "    if sixMaShortPass and targetQuantity > 0.0\n"
        "        pendingShortStopTicks := signalStopTicks\n"
        "        strategy.entry(\"Short\", strategy.short, qty = targetQuantity, comment = \"V12E W8 short\", alert_message = shortEntryAlert)\n"
        "        strategy.exit(\"Exit Short\", \"Short\", loss = signalStopTicks, comment = \"Initial stop\", alert_message = shortExitAlert)\n"
        "    else if strategy.position_size > 0.0\n"
        "        strategy.close(\"Long\", comment = \"V12E rejected short closes long\", alert_message = longExitAlert)\n",
        label="V12E entry-only orders",
    )
    return _mark_paper(
        text,
        version="V12E",
        hud_status="PAPER MA6-W8 ENTRY / PARITY REQUIRED",
    )


def build_v12_tbsl(source: str) -> str:
    """Build signal-close tick-frozen TP30/ATR3 Pine arm."""

    text = _paper_header(
        source,
        version="V12T",
        title="TP30-ATR3",
        explanation="signals stay V9; TP distance is frozen causally at signal close.",
    )
    text = replace_once(
        text,
        "const float ATR_MULT = 4.0\n",
        "const float ATR_MULT = 3.0\n",
        label="V12T ATR multiplier",
    )
    text = replace_once(
        text,
        "const float MAX_SL_PERCENT = 3.0\n",
        "const float MAX_SL_PERCENT = 3.0\n"
        "const float TAKE_PROFIT_PERCENT = 30.0\n",
        label="V12T take-profit constant",
    )
    text = replace_once(
        text,
        "int signalStopTicks = math.max(1, int(math.round(signalStopDistance / syminfo.mintick)))\n",
        "int signalStopTicks = math.max(1, int(math.round(signalStopDistance / syminfo.mintick)))\n"
        "float signalTakeProfitDistance = close * TAKE_PROFIT_PERCENT / 100.0\n"
        "int signalTakeProfitTicks = math.max(1, int(math.round(signalTakeProfitDistance / syminfo.mintick)))\n",
        label="V12T causal target ticks",
    )
    text = replace_once(
        text,
        "var int pendingLongStopTicks = na\nvar int pendingShortStopTicks = na\n",
        "var int pendingLongStopTicks = na\n"
        "var int pendingShortStopTicks = na\n"
        "var int pendingLongTakeProfitTicks = na\n"
        "var int pendingShortTakeProfitTicks = na\n",
        label="V12T pending target ticks",
    )
    text = replace_once(
        text,
        "    pendingLongStopTicks := signalStopTicks\n"
        "    strategy.entry(\"Long\", strategy.long, qty = targetQuantity, comment = \"V12T confirmed long\", alert_message = longEntryAlert)\n"
        "    strategy.exit(\"Exit Long\", \"Long\", loss = signalStopTicks, comment = \"Initial stop\", alert_message = longExitAlert)\n",
        "    pendingLongStopTicks := signalStopTicks\n"
        "    pendingLongTakeProfitTicks := signalTakeProfitTicks\n"
        "    strategy.entry(\"Long\", strategy.long, qty = targetQuantity, comment = \"V12T confirmed long\", alert_message = longEntryAlert)\n"
        "    strategy.exit(\"Exit Long\", \"Long\", loss = signalStopTicks, profit = signalTakeProfitTicks, comment = \"Initial stop + TP\", alert_message = longExitAlert)\n",
        label="V12T long initial bracket",
    )
    text = replace_once(
        text,
        "    pendingShortStopTicks := signalStopTicks\n"
        "    strategy.entry(\"Short\", strategy.short, qty = targetQuantity, comment = \"V12T confirmed short\", alert_message = shortEntryAlert)\n"
        "    strategy.exit(\"Exit Short\", \"Short\", loss = signalStopTicks, comment = \"Initial stop\", alert_message = shortExitAlert)\n",
        "    pendingShortStopTicks := signalStopTicks\n"
        "    pendingShortTakeProfitTicks := signalTakeProfitTicks\n"
        "    strategy.entry(\"Short\", strategy.short, qty = targetQuantity, comment = \"V12T confirmed short\", alert_message = shortEntryAlert)\n"
        "    strategy.exit(\"Exit Short\", \"Short\", loss = signalStopTicks, profit = signalTakeProfitTicks, comment = \"Initial stop + TP\", alert_message = shortExitAlert)\n",
        label="V12T short initial bracket",
    )
    text = replace_once(
        text,
        "var float stopPrice = na\n"
        "if newLongPosition\n"
        "    stopPrice := strategy.position_avg_price - nz(pendingLongStopTicks, signalStopTicks) * syminfo.mintick\n"
        "if newShortPosition\n"
        "    stopPrice := strategy.position_avg_price + nz(pendingShortStopTicks, signalStopTicks) * syminfo.mintick\n"
        "if strategy.position_size == 0.0 and nz(strategy.position_size[1]) != 0.0\n"
        "    stopPrice := na\n",
        "var float stopPrice = na\n"
        "var float takeProfitPrice = na\n"
        "if newLongPosition\n"
        "    stopPrice := strategy.position_avg_price - nz(pendingLongStopTicks, signalStopTicks) * syminfo.mintick\n"
        "    takeProfitPrice := strategy.position_avg_price + nz(pendingLongTakeProfitTicks, signalTakeProfitTicks) * syminfo.mintick\n"
        "if newShortPosition\n"
        "    stopPrice := strategy.position_avg_price + nz(pendingShortStopTicks, signalStopTicks) * syminfo.mintick\n"
        "    takeProfitPrice := strategy.position_avg_price - nz(pendingShortTakeProfitTicks, signalTakeProfitTicks) * syminfo.mintick\n"
        "if strategy.position_size == 0.0 and nz(strategy.position_size[1]) != 0.0\n"
        "    stopPrice := na\n"
        "    takeProfitPrice := na\n",
        label="V12T filled target state",
    )
    text = text.replace(
        "strategy.exit(\"Exit Long\", \"Long\", stop = stopPrice, comment = \"Managed stop\", alert_message = longExitAlert)",
        "strategy.exit(\"Exit Long\", \"Long\", stop = stopPrice, limit = takeProfitPrice, comment = \"Managed stop + TP\", alert_message = longExitAlert)",
    )
    text = text.replace(
        "strategy.exit(\"Exit Short\", \"Short\", stop = stopPrice, comment = \"Managed stop\", alert_message = shortExitAlert)",
        "strategy.exit(\"Exit Short\", \"Short\", stop = stopPrice, limit = takeProfitPrice, comment = \"Managed stop + TP\", alert_message = shortExitAlert)",
    )
    text = replace_once(
        text,
        "plot(strategy.position_size != 0.0 ? stopPrice : na, \"Managed stop\", color = shortColor, style = plot.style_linebr)\n",
        "plot(strategy.position_size != 0.0 ? stopPrice : na, \"Managed stop\", color = shortColor, style = plot.style_linebr)\n"
        "plot(strategy.position_size != 0.0 ? takeProfitPrice : na, \"Fixed TP\", color = #16A34A, style = plot.style_linebr)\n",
        label="V12T target plot",
    )
    return _mark_paper(
        text,
        version="V12T",
        hud_status="PAPER TP30 ATR3 / PARITY REQUIRED",
    )


def main() -> None:
    source = SOURCE.read_text(encoding="utf-8")
    outputs = (
        (V12_FULL_OUTPUT, build_v12_full_gate(source)),
        (V12_ENTRY_OUTPUT, build_v12_entry_only(source)),
        (V12_TBSL_OUTPUT, build_v12_tbsl(source)),
    )
    for path, content in outputs:
        path.write_text(content, encoding="utf-8")
    manifest = {
        "artifact": "ETH 15m optimized Pine paper variants",
        "source": str(SOURCE.relative_to(PROJECT)),
        "source_sha256": sha256(SOURCE),
        "six_ma_contract": {
            "bundle": ["SMA20", "EMA20", "SMA60", "EMA60", "SMA120", "EMA120"],
            "source": "close",
            "directional_pairs": len(SIX_MA_PAIRS),
            "window_bars": 8,
            "threshold": 0.0,
            "future_bars": 0,
        },
        "variants": [
            {
                "version": "V12F",
                "path": str(V12_FULL_OUTPUT.relative_to(PROJECT)),
                "sha256": sha256(V12_FULL_OUTPUT),
                "change_contract": "MA6 W8 full-state gate",
                "strict_single_variable": True,
                "reversal_semantics": "rejected guarded signals do not reverse",
            },
            {
                "version": "V12E",
                "path": str(V12_ENTRY_OUTPUT.relative_to(PROJECT)),
                "sha256": sha256(V12_ENTRY_OUTPUT),
                "change_contract": "MA6 W8 entry-only gate",
                "strict_single_variable": True,
                "reversal_semantics": "rejected opposite signal closes but does not reopen",
            },
            {
                "version": "V12T",
                "path": str(V12_TBSL_OUTPUT.relative_to(PROJECT)),
                "sha256": sha256(V12_TBSL_OUTPUT),
                "change_contract": "staged-selected TP30 plus ATR3 composite; stop cap stays 3%",
                "strict_single_variable": False,
                "selection_history": "TP selected first, then ATR multiple; replayed as one pre-frozen TBSL composite",
                "take_profit_distance_basis": "signal_close",
            },
        ],
        "combined_variant_generated": False,
        "official_pine_compiler_run": False,
        "tradingview_parity_passed": False,
        "holdout_rows_read": 0,
        "forward_eligible": False,
        "production_eligible": False,
    }
    MANIFEST.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    main()
