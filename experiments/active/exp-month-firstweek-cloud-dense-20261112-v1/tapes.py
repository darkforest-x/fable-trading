"""Extract continuous 5m OHLC tapes for monthly-first-week cloud studies.

Each complete tape starts five days before the hourly signal close becomes
known and ends 24 hours after the old review.py stop boundary. The five-day
warmup therefore contains exactly 480 complete UTC-aligned 15m candles (1440
5m candles), and no bar opening at or after the signal close is used as
indicator warmup.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache
from pathlib import Path
import re
import sys

import numpy as np
import pandas as pd


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
OLD = ROOT / "experiments/active/exp-month-firstweek-intraday-retest-20261112-v1"
WICKS = ROOT / "experiments/active/exp-alt-isolated-12h-firstweek-wicks-20261111-v1"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(OLD))
sys.path.insert(0, str(WICKS))

from bars import load_bars  # noqa: E402
from yoyo.evaluation.month_first_week_touch_review import bounds  # noqa: E402


INPUT = (ROOT / "data/research/alt-isolated-12h-wicks-20261111/expanded12/"
         "all_replay_v1/intraday_retest/events.csv.gz")
OUT = ROOT / "data/research/month-firstweek-cloud-dense-20261112-v1"
TAPE_DIR = OUT / "tapes"
MANIFEST = OUT / "manifest.csv.gz"

FIVE_MS = 5 * 60_000
FIFTEEN_MS = 15 * 60_000
HOUR_MS = 60 * 60_000
DAY_MS = 24 * HOUR_MS
WARMUP_15M_BARS = 480
WARMUP_MS = WARMUP_15M_BARS * FIFTEEN_MS
EXPECTED_IDS = 7761
EMPTY = np.empty((0, 5), dtype=np.float64)
BASE_COLUMNS = (
    "id", "symbol", "month", "side", "touch_ms", "direction_close",
    "rail", "near_edge", "stop_ms", "coverage", "first_20_bar_ms",
)
DETAIL_COLUMNS = (
    "known_ms", "warmup_start_ms", "end_exclusive_ms", "bars_expected",
    "bars_found", "missing_bars", "first_bar_ms", "last_bar_ms",
    "tape_path", "error",
)


@lru_cache(maxsize=None)
def _expiry_ms(month: str) -> int:
    # This is the same New York calendar expiry used by the prior review.py.
    return int(bounds(month)[2].value // 1_000_000)


def _input_rows() -> pd.DataFrame:
    if not INPUT.is_file():
        raise FileNotFoundError(f"prior review events are missing: {INPUT}")
    events = pd.read_csv(INPUT, float_precision="round_trip")
    needed = set(BASE_COLUMNS) - {"stop_ms", "coverage"}
    missing = sorted(needed - set(events.columns))
    if missing:
        raise ValueError(f"prior review events lack required columns: {missing}")
    complete = events.loc[events["coverage"].eq("complete")].copy()
    counts = complete.groupby("id", dropna=False).size()
    if len(counts) != EXPECTED_IDS or not counts.eq(2).all():
        raise ValueError(
            f"expected {EXPECTED_IDS} complete ids with 2 rows each; "
            f"found {len(counts)} ids and row counts {counts.value_counts().to_dict()}"
        )
    check_fields = [
        "symbol", "month", "side", "touch_ms", "direction_close", "rail",
        "near_edge", "first_20_bar_ms",
    ]
    for field in check_fields:
        inconsistent = complete.groupby("id", dropna=False)[field].nunique(dropna=False)
        if inconsistent.gt(1).any():
            raise ValueError(f"prior review rows disagree within id for {field}")
    rows = complete.drop_duplicates("id", keep="first").copy()
    rows["id"] = rows["id"].astype(str)
    if not rows["id"].map(lambda value: bool(re.fullmatch(
            r"[A-Z0-9]+USDT\|\d{4}-\d{2}-\d{2}", value))).all():
        raise ValueError("unexpected id format; refusing unsafe tape filenames")
    rows["symbol"] = rows["symbol"].astype(str).str.upper()
    rows["month"] = rows["month"].astype(str)
    rows["touch_ms"] = pd.to_numeric(rows["touch_ms"], errors="raise").astype("int64")
    return rows.sort_values(["symbol", "touch_ms", "id"], kind="stable")


def _read_window(symbol: str, start_ms: int, end_ms: int,
                 month_cache) -> tuple[np.ndarray, str | None]:
    first_month = pd.Timestamp(start_ms, unit="ms", tz="UTC").strftime("%Y-%m")
    last_month = pd.Timestamp(end_ms - 1, unit="ms", tz="UTC").strftime("%Y-%m")
    parts: list[np.ndarray] = []
    try:
        for period in pd.period_range(first_month, last_month, freq="M"):
            rows = month_cache(str(period))
            if not len(rows):
                continue
            lo, hi = rows[:, 0].searchsorted([start_ms, end_ms])
            if hi > lo:
                parts.append(rows[lo:hi])
    except (OSError, ValueError, EOFError) as exc:
        return EMPTY.copy(), f"{type(exc).__name__}: {exc}"
    if not parts:
        return EMPTY.copy(), None
    tape = np.concatenate(parts, axis=0).astype(np.float64, copy=False)
    # bars.py validates each source and resolves identical duplicates. Keep a
    # final window-level check before writing a reusable raw tape.
    times = tape[:, 0]
    if (not np.isfinite(tape).all() or not np.equal(times, np.floor(times)).all()
            or (times % FIVE_MS != 0).any() or (np.diff(times) <= 0).any()):
        return EMPTY.copy(), "invalid timestamps or values in merged 5m window"
    if ((tape[:, 1:] <= 0).any()
            or (tape[:, 2] < np.maximum.reduce(
                (tape[:, 1], tape[:, 3], tape[:, 4]))).any()
            or (tape[:, 3] > np.minimum.reduce(
                (tape[:, 1], tape[:, 2], tape[:, 4]))).any()):
        return EMPTY.copy(), "invalid OHLC range in merged 5m window"
    return tape, None


def _write_tape(identifier: str, tape: np.ndarray) -> str:
    path = TAPE_DIR / f"{identifier}.npy"
    temp = TAPE_DIR / f".{identifier}.tmp.npy"
    with temp.open("wb") as stream:
        np.save(stream, np.asarray(tape, dtype=np.float64), allow_pickle=False)
        stream.flush()
    temp.replace(path)
    return str(path.relative_to(ROOT))


def _one_symbol(item: tuple[str, pd.DataFrame]) -> list[dict]:
    symbol, rows = item
    # Reuse each same-symbol monthly OHLC source across all events in that
    # symbol/month, including windows that cross a UTC calendar-month boundary.
    month_cache = lru_cache(maxsize=4)(lambda month: load_bars(symbol, month))
    result: list[dict] = []
    for row in rows.itertuples(index=False):
        touch = int(row.touch_ms)
        known = touch + HOUR_MS
        expiry = _expiry_ms(str(row.month))
        stop = min(known + DAY_MS, expiry)
        start = known - WARMUP_MS
        end = stop + DAY_MS
        expected_n = (end - start) // FIVE_MS
        item = {
            "id": str(row.id), "symbol": symbol, "month": str(row.month),
            "side": str(row.side), "touch_ms": touch,
            "direction_close": float(row.direction_close), "rail": float(row.rail),
            "near_edge": float(row.near_edge), "stop_ms": stop,
            "coverage": "", "first_20_bar_ms": row.first_20_bar_ms,
            "known_ms": known, "warmup_start_ms": start,
            "end_exclusive_ms": end, "bars_expected": expected_n,
            "bars_found": 0, "missing_bars": expected_n,
            "first_bar_ms": np.nan, "last_bar_ms": np.nan,
            "tape_path": "", "error": "",
        }
        if known >= expiry:
            item["coverage"] = "rail_expired"
            item["error"] = "direction close is at or after the NY expiry"
            result.append(item)
            continue
        if (touch % FIVE_MS) or (known % FIFTEEN_MS) or (start % FIFTEEN_MS) or (end % FIVE_MS):
            item["coverage"] = "invalid_time_alignment"
            item["error"] = "window boundary is not UTC-aligned to the required grid"
            result.append(item)
            continue

        tape, error = _read_window(symbol, start, end, month_cache)
        if error:
            item["coverage"] = "invalid_5m_source"
            item["error"] = error
            result.append(item)
            continue
        if len(tape):
            item["bars_found"] = len(tape)
            item["missing_bars"] = expected_n - len(tape)
            item["first_bar_ms"] = int(tape[0, 0])
            item["last_bar_ms"] = int(tape[-1, 0])
        if len(tape) != expected_n or not np.array_equal(
                tape[:, 0].astype(np.int64, copy=False),
                np.arange(start, end, FIVE_MS, dtype=np.int64)):
            if not len(tape):
                coverage = "missing_5m_data"
            elif int(tape[0, 0]) > start:
                coverage = "insufficient_warmup"
            elif int(tape[-1, 0]) + FIVE_MS < end:
                coverage = "insufficient_end"
            else:
                coverage = "gapped_5m"
            item["coverage"] = coverage
            item["error"] = (
                f"expected {expected_n} continuous 5m bars on [{start}, {end}); "
                f"found {len(tape)}"
            )
            result.append(item)
            continue

        # The final 5m bar that closes at known_ms must reproduce the saved
        # 1H direction close. Bars opening at known_ms are outside warmup.
        close_row = np.searchsorted(tape[:, 0], known - FIVE_MS)
        if (close_row >= len(tape) or tape[close_row, 0] != known - FIVE_MS
                or not np.isclose(tape[close_row, 4], float(row.direction_close),
                                  rtol=1e-8, atol=0.0)):
            item["coverage"] = "source_disagrees_on_h1"
            item["error"] = "5m close at the 1H known boundary differs from saved direction_close"
            result.append(item)
            continue
        if not np.array_equal(tape[:WARMUP_15M_BARS * 3, 0],
                              np.arange(start, known, FIVE_MS, dtype=np.int64)):
            item["coverage"] = "invalid_warmup_window"
            item["error"] = "warmup does not contain exactly 1440 pre-known 5m bars"
            result.append(item)
            continue

        item["coverage"] = "complete"
        item["tape_path"] = _write_tape(str(row.id), tape)
        result.append(item)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Extract continuous 5m tapes with 480 complete 15m warmup bars."
    )
    parser.add_argument("--workers", type=int, default=6,
                        help="parallel symbol readers (default: 6)")
    parser.add_argument("--limit", type=int,
                        help="process only the first N ids after deterministic sorting")
    args = parser.parse_args()
    if args.workers < 1:
        parser.error("--workers must be at least 1")
    rows = _input_rows()
    if args.limit is not None:
        if args.limit < 1:
            parser.error("--limit must be at least 1")
        rows = rows.head(args.limit)
    OUT.mkdir(parents=True, exist_ok=True)
    TAPE_DIR.mkdir(parents=True, exist_ok=True)
    print(f"input_ids={len(rows)} workers={args.workers} output={OUT}", flush=True)
    grouped = list(rows.groupby("symbol", sort=True))
    manifest_rows: list[dict] = []
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        for index, symbol_rows in enumerate(pool.map(_one_symbol, grouped), start=1):
            manifest_rows.extend(symbol_rows)
            if index % 50 == 0 or index == len(grouped):
                counts = pd.Series([r["coverage"] for r in manifest_rows]).value_counts().to_dict()
                print(f"symbols={index}/{len(grouped)} ids={len(manifest_rows)} coverage={counts}",
                      flush=True)
    manifest = pd.DataFrame(manifest_rows, columns=[*BASE_COLUMNS, *DETAIL_COLUMNS])
    manifest = manifest.sort_values(["touch_ms", "symbol", "id"], kind="stable")
    temp = MANIFEST.with_suffix(".tmp")
    manifest.to_csv(temp, index=False, compression="gzip")
    temp.replace(MANIFEST)
    print(f"manifest={MANIFEST} ids={manifest.id.nunique()} tapes="
          f"{int(manifest.coverage.eq('complete').sum())}", flush=True)
    print("coverage=" + repr(manifest.coverage.value_counts().to_dict()), flush=True)


if __name__ == "__main__":
    main()
