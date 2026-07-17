"""OKX SWAP ticker ranking: movers + always-on majors / top volume.

Owner 2026-07-17: radar must separately cover mainstream coins (BTC/ETH/SOL …)
and recent top-volume names — not only 24h gain/loss leaders (where majors rarely appear).
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from src.data.loader import BLOCKED_BASES
from src.data.universe import STOCKISH_BASES
from src.scout_mtf.http import get_json

# Stable / non-crypto / equity-perp noise on USDT-m (skip for crypto radar).
# Reuse project blocked + stockish lists so volume-top is not gold/NVDA/MU spam.
_SKIP_BASES = {
    "USDT", "USDC", "USD", "DAI", "TUSD", "FDUSD", "USDE", "USDD",
    "BTCDOM", "ETHBTC",
    # leveraged equity / commodity wrappers not always in BLOCKED_BASES
    "SOXL", "SOXS", "TQQQ", "SQQQ", "SKHY", "DRAM", "SNXX", "BILL",
} | set(BLOCKED_BASES) | set(STOCKISH_BASES)

# Always pinned (when listed on OKX USDT-m). Order = display rank.
CORE_MAJORS: tuple[str, ...] = (
    "BTC", "ETH", "SOL", "BNB", "XRP", "DOGE", "ADA", "AVAX", "LINK", "DOT",
)
DEFAULT_VOLUME_TOP = 10


@dataclass
class RankedSymbol:
    symbol: str          # BTC_USDT_SWAP
    inst_id: str         # BTC-USDT-SWAP
    last: float
    chg24h_pct: float    # percent, e.g. 12.3
    vol24h_usdt: float
    rank_side: str       # "major" | "volume" | "gain" | "loss"
    rank: int            # 1-based within side


def _to_symbol(inst_id: str) -> str:
    return inst_id.replace("-", "_")


def fetch_swap_tickers() -> list[dict[str, Any]]:
    payload = get_json("/api/v5/market/tickers?instType=SWAP")
    if str(payload.get("code")) != "0":
        raise RuntimeError(f"tickers error: {payload.get('msg')}")
    return list(payload.get("data") or [])


def _parse_usdt_swap_rows(
    tickers: list[dict[str, Any]] | None = None,
    *,
    min_vol_usdt: float = 0.0,
) -> list[tuple[str, str, float, float, float]]:
    """Return (base, inst_id, last, chg_pct, vol_usdt) for liquid USDT-m swaps."""
    raw = tickers if tickers is not None else fetch_swap_tickers()
    rows: list[tuple[str, str, float, float, float]] = []
    for t in raw:
        inst = str(t.get("instId") or "")
        if not inst.endswith("-USDT-SWAP"):
            continue
        base = inst.split("-")[0]
        if base in _SKIP_BASES or base.endswith("USD"):
            continue
        try:
            last = float(t.get("last") or 0)
            open24 = float(t.get("open24h") or 0)
            # OKX SWAP: volCcy24h = base-coin amount; convert to ~USDT quote volume.
            # Ranking by raw volCcy24h would put micro-priced memes (SATS/PEPE) first.
            vol_ccy = float(t.get("volCcy24h") or 0)
            vol = vol_ccy * last if vol_ccy > 0 and last > 0 else float(t.get("vol24h") or 0)
        except (TypeError, ValueError):
            continue
        if last <= 0 or open24 <= 0:
            continue
        if vol < min_vol_usdt:
            continue
        chg = (last / open24 - 1.0) * 100.0
        rows.append((base, inst, last, chg, vol))
    return rows


def major_and_volume_pool(
    *,
    volume_top: int = DEFAULT_VOLUME_TOP,
    core: tuple[str, ...] = CORE_MAJORS,
    tickers: list[dict[str, Any]] | None = None,
) -> list[RankedSymbol]:
    """Core majors (pinned) + top-N by 24h quote volume. Deduped.

    Market-cap ranking is not available from public tickers alone; volume top-N
    is the liquidity proxy. Core majors are always first when listed.
    """
    rows = _parse_usdt_swap_rows(tickers, min_vol_usdt=0.0)
    by_base = {base: (inst, last, chg, vol) for base, inst, last, chg, vol in rows}

    out: list[RankedSymbol] = []
    seen: set[str] = set()

    # 1) always-on majors
    for i, base in enumerate(core, 1):
        hit = by_base.get(base)
        if not hit:
            continue
        inst, last, chg, vol = hit
        sym = _to_symbol(inst)
        if sym in seen:
            continue
        seen.add(sym)
        out.append(RankedSymbol(sym, inst, last, chg, vol, "major", i))

    # 2) top volume (fill up to volume_top additional slots that aren't already major)
    by_vol = sorted(rows, key=lambda x: x[4], reverse=True)
    vol_rank = 0
    for base, inst, last, chg, vol in by_vol:
        sym = _to_symbol(inst)
        if sym in seen:
            continue
        vol_rank += 1
        if vol_rank > max(1, int(volume_top)):
            break
        seen.add(sym)
        out.append(RankedSymbol(sym, inst, last, chg, vol, "volume", vol_rank))

    return out


def rank_pool(
    *,
    top_n: int = 15,
    min_vol_usdt: float = 5_000_000.0,
    include_loss: bool = True,
    tickers: list[dict[str, Any]] | None = None,
) -> list[RankedSymbol]:
    """Return top gainers (+ optional top losers) by 24h change among liquid swaps."""
    rows = _parse_usdt_swap_rows(tickers, min_vol_usdt=min_vol_usdt)
    # (chg, vol, inst, last)
    packed = [(chg, vol, inst, last) for _base, inst, last, chg, vol in rows]

    gains = sorted(packed, key=lambda x: x[0], reverse=True)[:top_n]
    losses = sorted(packed, key=lambda x: x[0])[:top_n] if include_loss else []

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


def build_scan_pool(
    *,
    top_n: int = 15,
    min_vol_usdt: float = 5_000_000.0,
    include_loss: bool = True,
    volume_top: int = DEFAULT_VOLUME_TOP,
    include_majors: bool = True,
    max_symbols: int | None = None,
) -> list[RankedSymbol]:
    """Majors + top volume first, then gain/loss movers (deduped).

    Order: major → volume → gain → loss. Optional max_symbols trims from the
    end (movers first cut) so majors are never dropped before alts.
    """
    tickers = fetch_swap_tickers()
    majors = (
        major_and_volume_pool(volume_top=volume_top, tickers=tickers)
        if include_majors
        else []
    )
    movers = rank_pool(
        top_n=top_n,
        min_vol_usdt=min_vol_usdt,
        include_loss=include_loss,
        tickers=tickers,
    )
    out: list[RankedSymbol] = []
    seen: set[str] = set()
    for item in majors + movers:
        if item.symbol in seen:
            continue
        seen.add(item.symbol)
        out.append(item)

    if max_symbols is not None:
        cap = max(1, int(max_symbols))
        # Prefer keeping all majors/volume; only trim movers if over cap
        core = [x for x in out if x.rank_side in {"major", "volume"}]
        rest = [x for x in out if x.rank_side not in {"major", "volume"}]
        if len(core) >= cap:
            out = core[:cap]
        else:
            out = core + rest[: cap - len(core)]
    return out


def pool_as_dicts(pool: list[RankedSymbol]) -> list[dict]:
    return [asdict(r) for r in pool]
