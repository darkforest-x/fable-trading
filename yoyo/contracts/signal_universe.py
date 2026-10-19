"""Owner-selected new-entry universe, extended 2026-10-17.

Owner excluded the ten historical 15m loss leaders across the signal center,
then removed non-USDT-linear swaps. This is an explicit scope decision, not a
validated predictive filter. Existing positions and historical events survive.
Owner subsequently requested the full-history worst twenty separately for
15m/1H long/short. Their immutable source is bottom20_20261017_symbols.csv in
exp-spike-v131-symbol-summary-20261017-v1/results_v1. Directional exclusions
apply to SPIKE signal entry/notification contexts only; instrument discovery
and manual entries retain their existing symbol-only scope. No market feature
is introduced; this is an explicitly authorized, retrospective scope decision.
"""
from __future__ import annotations

import re

VERSION = "signal-universe-20261017-bottom20-v1"
EXCLUDED_BASES = ("XMR", "BTCDOM", "ARPA", "SFP", "BIGTIME", "HOT", "ICP", "GMT", "EGLD", "MAGIC")
SCOPE = "OKX USDT 线性永续 · 全周期排除 10 币 · 15m/1H 多空各排除倒数 20 币"

# Preserve Binance research identifiers for exact audit against the frozen CSV.
DIRECTIONAL_EXCLUDED_BASES = {
    "15m": {
        "long": ("XMR", "SFP", "HOT", "MAGIC", "GMT", "EGLD", "BTCDOM", "AXL", "BAND", "INJ",
                 "AXS", "TRU", "STG", "HFT", "AEVO", "JOE", "LUNA2", "VANA", "ALPHA", "CHZ"),
        "short": ("ARPA", "BTCDOM", "NEAR", "WIF", "EDU", "DOGE", "BIGTIME", "ICP", "OGN", "CATI",
                  "SNX", "UB", "XMR", "C98", "FIL", "ALCH", "WET", "RARE", "SKL", "BEL"),
    },
    "1H": {
        "long": ("ACH", "KSM", "SEI", "STORJ", "1000XEC", "CVX", "DYDX", "JASMY", "GRT", "TRX",
                 "JUP", "MOVR", "SUN", "BCH", "ATH", "C98", "AERO", "ZRX", "FHE", "LA"),
        "short": ("ARC", "ONE", "ENJ", "PHB", "MANA", "NKN", "PHA", "GALA", "1000RATS", "STO",
                  "KERNEL", "XVG", "ICP", "BAKE", "APE", "W", "MEME", "HEI", "NXPC", "VIC"),
    },
}
# Explicit identities only: never strip arbitrary leading numbers or suffixes.
# Binance LUNA2 announcement identifies underlying LUNA; 1000XEC/1000RATS
# are the named multiplier contracts for XEC/RATS. Other names match exactly.
RESEARCH_TO_OKX_BASE = {"LUNA2": "LUNA", "1000XEC": "XEC", "1000RATS": "RATS"}


def exclusion_reason(symbol, *, timeframe=None, side=None):
    """Keep symbol-only callers unchanged; scope SPIKE contexts by TF and side."""
    if not isinstance(symbol, str) or not re.fullmatch(r"[A-Z0-9]+-[A-Z0-9]+-SWAP", symbol):
        return "invalid_swap_symbol"
    if symbol.split("-")[0] in EXCLUDED_BASES:
        return "owner_excluded_asset"
    if not symbol.endswith("-USDT-SWAP"):
        return "outside_usdt_linear_universe"
    if timeframe is not None or side is not None:
        if timeframe not in ("15m", "30m", "1H", "4H") or side not in ("long", "short"):
            return "invalid_signal_scope"
        research = DIRECTIONAL_EXCLUDED_BASES.get(timeframe, {}).get(side, ())
        bases = {RESEARCH_TO_OKX_BASE.get(base, base) for base in research} | set(research)
        if symbol.split("-")[0] in bases:
            return "owner_excluded_timeframe_side"
    return None


def instrument_exclusion(instrument):
    reason = exclusion_reason(instrument.get("instId"))
    if reason:
        return reason
    if (instrument.get("instType") != "SWAP" or instrument.get("state") != "live"
            or instrument.get("ctType") != "linear" or instrument.get("settleCcy") != "USDT"):
        return "outside_usdt_linear_universe"
    return None


def public_policy():
    return {"version": VERSION, "scope": SCOPE, "excluded_bases": list(EXCLUDED_BASES),
            "directional_excluded_bases": {tf: {side: list(bases) for side, bases in sides.items()}
                                           for tf, sides in DIRECTIONAL_EXCLUDED_BASES.items()},
            "research_to_okx_base": dict(RESEARCH_TO_OKX_BASE),
            "selection_basis": "full_history_retrospective_owner_selected",
            "settlement": "USDT", "contract_type": "linear", "existing_positions_managed": True}
