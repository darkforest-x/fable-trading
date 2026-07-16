"""OKX SWAP ticker ranking by 24h change (top gain / top loss)."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from src.scout_mtf.http import get_json

# Stable / non-crypto noise on USDT-m perps (skip).
_SKIP_BASES = {
    "USDT", "USDC", "USD", "DAI", "TUSD", "FDUSD", "USDE", "USDD",
    "BTCDOM", "ETHBTC",
}


@dataclass
class RankedSymbol:
    symbol: str          # BTC_USDT_SWAP
    inst_id: str         # BTC-USDT-SWAP
    last: float
    chg24h_pct: float    # percent, e.g. 12.3
    vol24h_usdt: float
    rank_side: str       # "gain" | "loss"
    rank: int            # 1-based within side


def _to_symbol(inst_id: str) -> str:
    return inst_id.replace("-", "_")


def fetch_swap_tickers() -> list[dict[str, Any]]:
    payload = get_json("/api/v5/market/tickers?instType=SWAP")
    if str(payload.get("code")) != "0":
        raise RuntimeError(f"tickers error: {payload.get('msg')}")
    return list(payload.get("data") or [])


def rank_pool(
    *,
    top_n: int = 15,
    min_vol_usdt: float = 5_000_000.0,
    include_loss: bool = True,
) -> list[RankedSymbol]:
    """Return top gainers (+ optional top losers) by 24h change among liquid swaps."""
    rows: list[tuple[float, float, str, float]] = []
    for t in fetch_swap_tickers():
        inst = str(t.get("instId") or "")
        if not inst.endswith("-USDT-SWAP"):
            continue
        base = inst.split("-")[0]
        if base in _SKIP_BASES or base.endswith("USD"):
            continue
        try:
            last = float(t.get("last") or 0)
            open24 = float(t.get("open24h") or 0)
            vol = float(t.get("volCcy24h") or t.get("vol24h") or 0)
            # volCcy24h is quote-ish on linear; prefer volCcy24h if large
            vol_q = float(t.get("volCcy24h") or 0)
            if vol_q > 0:
                vol = vol_q
        except (TypeError, ValueError):
            continue
        if last <= 0 or open24 <= 0 or vol < min_vol_usdt:
            continue
        chg = (last / open24 - 1.0) * 100.0
        rows.append((chg, vol, inst, last))

    gains = sorted(rows, key=lambda x: x[0], reverse=True)[:top_n]
    losses = sorted(rows, key=lambda x: x[0])[:top_n] if include_loss else []

    out: list[RankedSymbol] = []
    seen: set[str] = set()
    for i, (chg, vol, inst, last) in enumerate(gains, 1):
        sym = _to_symbol(inst)
        if sym in seen:
            continue
        seen.add(sym)
        out.append(RankedSymbol(sym, inst, last, chg, vol, "gain", i))
    for i, (chg, vol, inst, last) in enumerate(losses, 1):
        sym = _to_symbol(inst)
        if sym in seen:
            continue
        seen.add(sym)
        out.append(RankedSymbol(sym, inst, last, chg, vol, "loss", i))
    return out


def pool_as_dicts(pool: list[RankedSymbol]) -> list[dict]:
    return [asdict(r) for r in pool]
