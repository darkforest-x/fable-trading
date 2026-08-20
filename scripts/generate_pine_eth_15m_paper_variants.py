#!/usr/bin/env python3
"""Generate single-variable V10/V11 paper Pine variants from frozen V9.

V10 adds only the causal project volume gate ``vol_ratio_mean8 >= 1``.  V11
changes only entry-direction eligibility: short signals close longs but cannot
open shorts.  The variants inherit all V9 timing, stop, break-even, cooldown,
cost, and sizing constants.  They are paper-only post-selection hypotheses and
must never overwrite the canonical V9 file or be combined without owner
approval.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path


PROJECT = Path(__file__).resolve().parents[1]
PINE_DIR = PROJECT / "experiments/active/exp-pine-eth-15m-v1/pine"
SOURCE = PINE_DIR / "allin_eth_15m_v9_research.pine"
V10_OUTPUT = PINE_DIR / "allin_eth_15m_v10_volume_paper.pine"
V11_OUTPUT = PINE_DIR / "allin_eth_15m_v11_long_only_paper.pine"
MANIFEST = PINE_DIR / "paper_variants_manifest.json"


def replace_once(text: str, old: str, new: str, *, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected one source block, found {count}")
    return text.replace(old, new, 1)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build_v10(source: str) -> str:
    text = source.replace("V9", "V10")
    text = replace_once(
        text,
        "// ALLIN ETH 15m V10 Research — frozen causal research candidate.\n"
        "// Research only: not TradingView-parity-approved, forward-eligible or production-eligible.",
        "// ALLIN ETH 15m V10 Volume — post-selection paper hypothesis.\n"
        "// Paper only: not TradingView-parity-approved, forward-eligible or production-eligible.",
        label="V10 header",
    )
    text = replace_once(
        text,
        "const int SLOW_SLOPE_LAG = 12\n",
        "const int SLOW_SLOPE_LAG = 12\n"
        "const int VOLUME_BASE_LEN = 20\n"
        "const int VOLUME_SMOOTH_LEN = 8\n"
        "const float VOLUME_RATIO_THRESHOLD = 1.0\n",
        label="V10 volume constants",
    )
    text = replace_once(
        text,
        "float atr = ta.atr(ATR_LEN)\n",
        "float atr = ta.atr(ATR_LEN)\n"
        "float volumeBase = ta.sma(volume, VOLUME_BASE_LEN)\n"
        "float volumeRatio = not na(volumeBase) and volumeBase > 0.0 ? volume / volumeBase : 0.0\n"
        "float volRatioMean8 = ta.sma(volumeRatio, VOLUME_SMOOTH_LEN)\n"
        "bool volumeExpansion = not na(volRatioMean8) and volRatioMean8 >= VOLUME_RATIO_THRESHOLD\n",
        label="V10 volume feature",
    )
    text = replace_once(
        text,
        "bool rawLong = percentileSafe and crossUp and close > slowMa and close > regimeMa and slowSlope12 > 0.0 and oscillatorRising",
        "bool rawLong = percentileSafe and volumeExpansion and crossUp and close > slowMa and close > regimeMa and slowSlope12 > 0.0 and oscillatorRising",
        label="V10 long gate",
    )
    text = replace_once(
        text,
        "bool rawShort = percentileSafe and crossDown and close < slowMa and close < regimeMa and slowSlope12 < 0.0 and oscillatorFalling",
        "bool rawShort = percentileSafe and volumeExpansion and crossDown and close < slowMa and close < regimeMa and slowSlope12 < 0.0 and oscillatorFalling",
        label="V10 short gate",
    )
    text = text.replace("RESEARCH ONLY | ALLIN ETH 15m V10", "PAPER ONLY | ALLIN ETH 15m V10")
    text = text.replace("RESEARCH ONLY | V10", "PAPER ONLY | V10")
    text = text.replace("RESEARCH / PARITY REQUIRED", "PAPER VOLUME / PARITY REQUIRED")
    return text


def build_v11(source: str) -> str:
    text = source.replace("V9", "V11")
    text = replace_once(
        text,
        "// ALLIN ETH 15m V11 Research — frozen causal research candidate.\n"
        "// Research only: not TradingView-parity-approved, forward-eligible or production-eligible.",
        "// ALLIN ETH 15m V11 Long-only — post-selection paper hypothesis.\n"
        "// Paper only: short signals close longs but can never open a short.",
        label="V11 header",
    )
    text = text.replace("RESEARCH ONLY | ALLIN ETH 15m V11", "PAPER ONLY | ALLIN ETH 15m V11")
    text = replace_once(
        text,
        "string shortEntryAlert = \"PAPER ONLY | ALLIN ETH 15m V11 | OPEN SHORT | {{ticker}} | {{strategy.order.price}}\"\n",
        "",
        label="V11 unused short entry alert",
    )
    text = replace_once(
        text,
        "string shortExitAlert = \"PAPER ONLY | ALLIN ETH 15m V11 | CLOSE SHORT | {{ticker}} | {{strategy.order.price}}\"\n",
        "",
        label="V11 unused short exit alert",
    )
    text = replace_once(
        text,
        "var int pendingLongStopTicks = na\nvar int pendingShortStopTicks = na\n",
        "var int pendingLongStopTicks = na\n",
        label="V11 pending stop state",
    )
    text = replace_once(
        text,
        "if shortSignal and strategy.position_size >= 0.0 and targetQuantity > 0.0\n"
        "    pendingShortStopTicks := signalStopTicks\n"
        "    strategy.entry(\"Short\", strategy.short, qty = targetQuantity, comment = \"V11 confirmed short\", alert_message = shortEntryAlert)\n"
        "    strategy.exit(\"Exit Short\", \"Short\", loss = signalStopTicks, comment = \"Initial stop\", alert_message = shortExitAlert)\n",
        "if shortSignal and strategy.position_size > 0.0\n"
        "    strategy.close(\"Long\", comment = \"V11 short signal exits long\", alert_message = longExitAlert)\n",
        label="V11 short entry replacement",
    )
    text = replace_once(
        text,
        "bool newLongPosition = strategy.position_size > 0.0 and nz(strategy.position_size[1]) <= 0.0\n"
        "bool newShortPosition = strategy.position_size < 0.0 and nz(strategy.position_size[1]) >= 0.0\n"
        "var float stopPrice = na\n"
        "if newLongPosition\n"
        "    stopPrice := strategy.position_avg_price - nz(pendingLongStopTicks, signalStopTicks) * syminfo.mintick\n"
        "if newShortPosition\n"
        "    stopPrice := strategy.position_avg_price + nz(pendingShortStopTicks, signalStopTicks) * syminfo.mintick\n",
        "bool newLongPosition = strategy.position_size > 0.0 and nz(strategy.position_size[1]) <= 0.0\n"
        "var float stopPrice = na\n"
        "if newLongPosition\n"
        "    stopPrice := strategy.position_avg_price - nz(pendingLongStopTicks, signalStopTicks) * syminfo.mintick\n",
        label="V11 new position state",
    )
    text = replace_once(
        text,
        "if strategy.position_size < 0.0 and not na(stopPrice)\n"
        "    float entryPrice = strategy.position_avg_price\n"
        "    if low <= entryPrice * (1.0 - BREAK_EVEN_TRIGGER_PERCENT / 100.0)\n"
        "        stopPrice := math.min(stopPrice, entryPrice * (1.0 - BREAK_EVEN_OFFSET_PERCENT / 100.0))\n"
        "    strategy.exit(\"Exit Short\", \"Short\", stop = stopPrice, comment = \"Managed stop\", alert_message = shortExitAlert)\n\n",
        "",
        label="V11 short managed stop",
    )
    text = text.replace("RESEARCH / PARITY REQUIRED", "PAPER LONG-ONLY / PARITY REQUIRED")
    text = text.replace("RESEARCH ONLY | V11", "PAPER ONLY | V11")
    return text


def main() -> None:
    source = SOURCE.read_text(encoding="utf-8")
    v10 = build_v10(source)
    v11 = build_v11(source)
    V10_OUTPUT.write_text(v10, encoding="utf-8")
    V11_OUTPUT.write_text(v11, encoding="utf-8")
    manifest = {
        "source": str(SOURCE.relative_to(PROJECT)),
        "source_sha256": sha256(SOURCE),
        "variants": [
            {
                "version": "V10",
                "path": str(V10_OUTPUT.relative_to(PROJECT)),
                "sha256": sha256(V10_OUTPUT),
                "single_variable": "add vol_ratio_mean8 >= 1 to both entry directions",
                "status": "post-selection paper-only",
            },
            {
                "version": "V11",
                "path": str(V11_OUTPUT.relative_to(PROJECT)),
                "sha256": sha256(V11_OUTPUT),
                "single_variable": "allow long entries only; short signals remain exits",
                "status": "post-selection paper-only",
            },
        ],
        "combined_v10_v11_generated": False,
        "tradingview_parity_passed": False,
        "production_eligible": False,
    }
    MANIFEST.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    main()
