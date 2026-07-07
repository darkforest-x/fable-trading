"""15m OHLCV loader over the old project's cached CSVs (read-only via symlink).

Cache files live under data/kline_cache (symlink to the old project's
runs/cache). Two schemas exist:

- okx:  ts,open,high,low,close,volume,vol_ccy,vol_ccy_quote,confirm,open_time
- gate: open_time,open,high,low,close,volume

File naming: [gate_|okx_]{SYMBOL}_{bar}_{rows}[_latest].csv. Multiple files per
(source, symbol) are merged and deduplicated on open_time.
"""
from __future__ import annotations

import re
from pathlib import Path

import pandas as pd

CACHE_DIR = Path(__file__).resolve().parents[2] / "data" / "kline_cache"
# Freshly fetched history (src.data.fetch_okx) lives beside the old cache and
# is merged per (source, symbol); load_series dedupes on open_time.
FETCHED_DIR = Path(__file__).resolve().parents[2] / "data" / "kline_fetched"
CACHE_PATTERN = re.compile(
    r"^(?:(?P<prefix>gate|okx)_)?(?P<symbol>.+?)_(?P<bar>5m|15m)_(?P<rows>[0-9]+)(?:_latest)?\.csv$"
)
# Ported from the old project's build_strict_dense_review_pack.py: stablecoins,
# gold and tokenized stocks are excluded from candidate mining.
BLOCKED_BASES = {
    "USDC", "USDG", "USDT", "DAI", "FDUSD", "TUSD", "USDE", "USDS", "BUSD",
    "XAU", "XAG", "XAUT", "PAXG", "QQQX", "NVDAX", "TSLAX", "MSTRX", "CRCLX",
    "SPYX", "AAPLX", "SKHYNIX", "AAOI", "CBRS", "GLW", "MU", "RKLB", "SOXS",
    "MRVL", "EWY", "SPCX", "SNDK", "CL", "INTC",
}
OHLCV_COLUMNS = ["open_time", "open", "high", "low", "close", "volume"]


def list_series(cache_dir: Path | None = None, *, bar: str = "15m") -> dict[tuple[str, str], list[Path]]:
    """Group cache CSVs by (market_source, symbol) for the given bar.

    Scans both the old-project cache and data/kline_fetched when `cache_dir`
    is None; a broken symlink (old cache unavailable) is silently skipped.
    """
    dirs = [cache_dir] if cache_dir is not None else [CACHE_DIR, FETCHED_DIR]
    paths: list[Path] = []
    for d in dirs:
        if d.is_dir():
            paths.extend(d.glob("*.csv"))
    groups: dict[tuple[str, str], list[Path]] = {}
    for path in sorted(paths):
        matched = CACHE_PATTERN.match(path.name)
        if matched is None or matched.group("bar") != bar:
            continue
        source = matched.group("prefix") or "okx"
        symbol = matched.group("symbol")
        if symbol.split("_", 1)[0] in BLOCKED_BASES:
            continue
        groups.setdefault((source, symbol), []).append(path)
    return groups


def load_series(paths: list[Path]) -> pd.DataFrame:
    """Read, merge and dedupe one (source, symbol) series, sorted by time."""
    frames: list[pd.DataFrame] = []
    for path in paths:
        try:
            frame = pd.read_csv(path)
        except (OSError, pd.errors.EmptyDataError, pd.errors.ParserError):
            continue
        if not set(OHLCV_COLUMNS).issubset(frame.columns):
            continue
        if "confirm" in frame.columns:
            frame = frame[frame["confirm"] != 0]
        frames.append(frame[OHLCV_COLUMNS])
    if not frames:
        return pd.DataFrame(columns=OHLCV_COLUMNS)
    out = pd.concat(frames, ignore_index=True)
    out["open_time"] = pd.to_datetime(out["open_time"], utc=True, errors="coerce")
    for column in ("open", "high", "low", "close", "volume"):
        out[column] = pd.to_numeric(out[column], errors="coerce")
    out = (
        out.dropna(subset=["open_time", "open", "high", "low", "close"])
        .drop_duplicates("open_time", keep="last")
        .sort_values("open_time")
        .reset_index(drop=True)
    )
    return out


def iter_series(
    cache_dir: Path | None = None,
    *,
    bar: str = "15m",
    min_bars: int = 500,
):
    """Yield (source, symbol, frame) for every usable series."""
    for (source, symbol), paths in sorted(list_series(cache_dir, bar=bar).items()):
        frame = load_series(paths)
        if len(frame) >= min_bars:
            yield source, symbol, frame
