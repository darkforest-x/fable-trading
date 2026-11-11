"""Apply the authorized 72-hour max-hold change to the frozen 168-hour fills.

Rows with exit_hour <= 72 are copied exactly from the Nautilus ledger. Only
rows that were still open after the 72nd H1 close are replayed through
VectorBT's order ledger as a market timeout at the 72nd close with taker fees.
The archived H1 reader is re-used; this script does not fetch or rebuild data.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import sys

import numpy as np
import pandas as pd
import vectorbt as vbt


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
REPLAY_DIR = ROOT / "experiments/active/exp-month-firstweek-all-replay-20261111-v1"
DATA_DIR = ROOT / "data/research/alt-isolated-12h-wicks-20261111/expanded12/all_replay_v1/h4_strategy"
DEFAULT_INPUT = DATA_DIR / "fills.csv"
DEFAULT_OUTPUT = DATA_DIR / "fills_72h.csv"

# replay.read_raw points to the registered archived Binance H1 arrays.
sys.path.insert(0, str(REPLAY_DIR))
from replay import CUTOFF, HOUR, read_raw  # noqa: E402


HORIZON_HOURS = 72
TAKER_FEE = 0.0005
MAKER_FEE = 0.0002
REQUIRED_COLUMNS = {
    "trade_id", "pair_id", "group", "sample", "symbol", "side", "entry_ms",
    "exit_ms", "entry_px", "exit_px", "qty", "entry_fee_u", "exit_fee_u",
    "fee_u", "net_pct", "exit_reason", "exit_hour", "exit_phase", "mfe_pct",
    "mae_pct",
}


def load_fills(path: Path) -> pd.DataFrame:
    if not path.is_file():
        raise FileNotFoundError(f"Nautilus fill ledger not found: {path}")
    frame = pd.read_csv(path, float_precision="round_trip")
    missing = REQUIRED_COLUMNS - set(frame.columns)
    if missing:
        raise ValueError(f"Fill ledger missing required columns: {sorted(missing)}")
    if frame.empty:
        raise ValueError("Fill ledger is empty")
    if frame["trade_id"].isna().any() or frame["trade_id"].astype(str).duplicated().any():
        raise ValueError("trade_id must be non-null and unique")
    if frame["pair_id"].isna().any():
        raise ValueError("pair_id must be present for the matched signal/random controls")
    if not frame["sample"].isin(("signal", "random")).all():
        raise ValueError("sample must be 'signal' or 'random'")
    if not frame["side"].isin(("long", "short")).all():
        raise ValueError("side must be 'long' or 'short'")

    for name in ("entry_ms", "exit_ms", "exit_hour"):
        values = pd.to_numeric(frame[name], errors="raise")
        if not np.isfinite(values).all() or not np.equal(values, np.floor(values)).all():
            raise ValueError(f"{name} must contain finite integer values")
        frame[name] = values.astype("int64")
    for name in ("entry_px", "exit_px", "qty", "entry_fee_u", "exit_fee_u", "fee_u", "net_pct"):
        frame[name] = pd.to_numeric(frame[name], errors="raise").astype(float)
        if not np.isfinite(frame[name]).all():
            raise ValueError(f"{name} must contain only finite values")
    if (frame[["entry_px", "exit_px", "qty"]] <= 0).any().any():
        raise ValueError("entry_px, exit_px and qty must be positive")
    if (frame["exit_hour"] < 1).any():
        raise ValueError("exit_hour is one-based and must be positive")

    if not np.allclose(frame["fee_u"], frame["entry_fee_u"] + frame["exit_fee_u"],
                       rtol=1e-10, atol=1e-9):
        raise ValueError("fee_u does not equal entry_fee_u + exit_fee_u")
    exit_rate = np.where(frame["exit_reason"].eq("target"), MAKER_FEE, TAKER_FEE)
    observed_entry_rate = frame["entry_fee_u"] / (frame["entry_px"] * frame["qty"])
    observed_exit_rate = frame["exit_fee_u"] / (frame["exit_px"] * frame["qty"])
    if not np.allclose(observed_entry_rate, TAKER_FEE, rtol=2e-7, atol=1e-10):
        raise ValueError("baseline entry fees do not match the configured 5bp taker rate")
    if not np.allclose(observed_exit_rate, exit_rate, rtol=2e-7, atol=1e-10):
        raise ValueError("baseline exits do not match maker 2bp / taker 5bp classification")

    sign = np.where(frame["side"].eq("long"), 1.0, -1.0)
    expected_net_pct = 100.0 * (
        sign * (frame["exit_px"] - frame["entry_px"]) * frame["qty"] - frame["fee_u"]
    ) / (frame["entry_px"] * frame["qty"])
    if not np.allclose(frame["net_pct"], expected_net_pct, rtol=1e-8, atol=1e-8):
        raise ValueError("baseline net_pct disagrees with directional PnL and fill fees")

    pair_sizes = frame.groupby("pair_id", sort=False).size()
    if not pair_sizes.eq(2).all():
        raise ValueError("each pair_id must contain exactly one signal and one random fill")
    sample_counts = frame.groupby("pair_id", sort=False)["sample"].nunique()
    if not sample_counts.eq(2).all():
        raise ValueError("each pair_id must contain one signal and one random fill")
    group_counts = frame.groupby("pair_id", sort=False)["group"].nunique()
    if not group_counts.eq(1).all():
        raise ValueError("matched signal/random rows must retain the same group")
    return frame


def load_archived_h1(symbol: str) -> tuple[np.ndarray, np.ndarray]:
    raw = np.asarray(read_raw(symbol), dtype=np.float64)
    if raw.ndim != 2 or raw.shape[1] < 5:
        raise ValueError(f"{symbol}: archived source must return [open_ms, open, high, low, close]")
    # Match the source replay's fully-closed-bar cutoff; do not use partial H1 bars.
    raw = raw[raw[:, 0] + HOUR <= int(CUTOFF), :5]
    times = raw[:, 0].astype(np.int64)
    if (np.diff(times) <= 0).any():
        raise ValueError(f"{symbol}: archived H1 timestamps are not strictly increasing")
    if (times % HOUR).any():
        raise ValueError(f"{symbol}: archived H1 timestamps are not hour-aligned")
    return raw, times


def horizon_window(raw: np.ndarray, times: np.ndarray, row: pd.Series) -> np.ndarray:
    entry_ms = int(row.entry_ms)
    i = int(np.searchsorted(times, entry_ms))
    if i >= len(times) or int(times[i]) != entry_ms:
        raise ValueError(f"{row.trade_id}: entry H1 open is absent from archived data")
    if i + HORIZON_HOURS > len(raw):
        raise ValueError(f"{row.trade_id}: fewer than 72 complete H1 bars after entry")
    window = raw[i:i + HORIZON_HOURS]
    expected = entry_ms + np.arange(HORIZON_HOURS, dtype=np.int64) * HOUR
    if not np.array_equal(window[:, 0].astype(np.int64), expected):
        raise ValueError(f"{row.trade_id}: a data gap occurs before the 72nd H1 close")
    ohlc = window[:, 1:5]
    if not np.isfinite(ohlc).all() or (ohlc <= 0).any():
        raise ValueError(f"{row.trade_id}: invalid OHLC within the 72-hour window")
    if (ohlc[:, 1] < np.maximum(ohlc[:, 0], ohlc[:, 3])).any():
        raise ValueError(f"{row.trade_id}: invalid high in the 72-hour window")
    if (ohlc[:, 2] > np.minimum(ohlc[:, 0], ohlc[:, 3])).any():
        raise ValueError(f"{row.trade_id}: invalid low in the 72-hour window")
    if not np.isclose(float(window[0, 1]), float(row.entry_px), rtol=1e-10, atol=1e-10):
        raise ValueError(f"{row.trade_id}: entry price does not match the archived H1 open")
    return window


def timeout_excursions(window: np.ndarray, side: int, fill: float) -> tuple[float, float]:
    """Match baseline.py's timeout mark convention, through the 72nd H1 close."""
    prices = [float(window[0, 1]), float(fill)]
    if len(window) > 1:
        prices.extend([float(window[:-1, 2].max()), float(window[:-1, 3].min())])
    columns = [1, 3, 2, 4] if side > 0 else [1, 2, 3, 4]
    prices.extend(window[-1, columns[:3]].astype(float).tolist())
    signed = side * (np.asarray(prices, dtype=np.float64) / float(window[0, 1]) - 1.0) * 100.0
    return max(0.0, float(signed.max())), max(0.0, float(-signed.min()))


def vbt_timeout_accounting(changed: pd.DataFrame, windows: list[np.ndarray]) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Use VectorBT for the 72h exit ledger; each row is an independent trade."""
    n = len(changed)
    if n != len(windows) or not n:
        raise ValueError("VectorBT accounting requires one H1 window per changed fill")
    closes = np.column_stack([w[:, 4] for w in windows])
    sides = np.where(changed["side"].eq("long").to_numpy(), 1, -1)

    # The unchanged fixed barriers were executed by Nautilus in the 7d run.
    # Only rows surviving past hour 72 enter this VectorBT timeout ledger.
    entry_px = changed["entry_px"].to_numpy(dtype=np.float64)
    exit_px = closes[-1, :]
    qty = changed["qty"].to_numpy(dtype=np.float64)
    prices = np.vstack((entry_px, exit_px))
    sizes = np.vstack((sides * qty, -sides * qty))
    fee_rates = np.full((2, n), TAKER_FEE, dtype=np.float64)
    init_cash = np.maximum(entry_px, exit_px) * qty * 4.0 + 1.0
    portfolio = vbt.Portfolio.from_orders(
        close=prices,
        size=sizes,
        price=prices,
        size_type="amount",
        direction="both",
        fees=fee_rates,
        init_cash=init_cash,
        cash_sharing=False,
        allow_partial=False,
        raise_reject=True,
        max_orders=2 * n,
        freq="1h",
    )
    orders = portfolio.orders.records.sort_values(["col", "idx"], kind="stable")
    entry_orders = orders[orders["idx"].eq(0)]
    exit_orders = orders[orders["idx"].eq(1)]
    if len(entry_orders) != n or len(exit_orders) != n:
        raise AssertionError("VectorBT did not record one entry and one timeout exit for each changed fill")
    entry_fees = np.full(n, np.nan, dtype=np.float64)
    exit_fees = np.full(n, np.nan, dtype=np.float64)
    entry_fees[entry_orders["col"].to_numpy(dtype=int)] = entry_orders["fees"].to_numpy(dtype=float)
    exit_fees[exit_orders["col"].to_numpy(dtype=int)] = exit_orders["fees"].to_numpy(dtype=float)

    trades = portfolio.trades.records
    closed = trades[trades["status"].eq(1)]
    if len(closed) != n:
        raise AssertionError(f"VectorBT closed {len(closed)} trades; expected {n}")
    net_pct = np.full(n, np.nan, dtype=np.float64)
    for trade in closed.itertuples(index=False):
        col = int(trade.col)
        net_pct[col] = float(trade.pnl) / (entry_px[col] * qty[col]) * 100.0
    if not np.isfinite(entry_fees).all() or not np.isfinite(exit_fees).all() or not np.isfinite(net_pct).all():
        raise AssertionError("VectorBT accounting returned non-finite fees or returns")
    return entry_fees, exit_fees, net_pct


def build_variant(frame: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    changed_indices = frame.index[frame["exit_hour"].gt(HORIZON_HOURS)].to_numpy(dtype=int)
    if not len(changed_indices):
        return frame.copy(), 0

    changed = frame.loc[changed_indices].copy()
    windows_by_index: dict[int, np.ndarray] = {}
    for symbol, rows in changed.groupby("symbol", sort=True):
        raw, times = load_archived_h1(str(symbol))
        for index, row in rows.iterrows():
            windows_by_index[int(index)] = horizon_window(raw, times, row)
    windows = [windows_by_index[int(index)] for index in changed.index]
    entry_fees, exit_fees, net_pct = vbt_timeout_accounting(changed, windows)

    out = frame.copy()
    timeout_ms = changed["entry_ms"].to_numpy(dtype=np.int64) + HORIZON_HOURS * HOUR
    close_prices = np.asarray([window[-1, 4] for window in windows], dtype=np.float64)
    mfe_mae = [timeout_excursions(window, 1 if row.side == "long" else -1, float(window[-1, 4]))
               for row, window in zip(changed.itertuples(), windows)]
    out.loc[changed.index, "exit_ms"] = timeout_ms
    out.loc[changed.index, "exit_px"] = close_prices
    out.loc[changed.index, "exit_hour"] = HORIZON_HOURS
    out.loc[changed.index, "exit_phase"] = 3
    out.loc[changed.index, "exit_reason"] = "timeout"
    out.loc[changed.index, "entry_fee_u"] = entry_fees
    out.loc[changed.index, "exit_fee_u"] = exit_fees
    out.loc[changed.index, "fee_u"] = entry_fees + exit_fees
    out.loc[changed.index, "net_pct"] = net_pct
    out.loc[changed.index, "mfe_pct"] = [pair[0] for pair in mfe_mae]
    out.loc[changed.index, "mae_pct"] = [pair[1] for pair in mfe_mae]
    if "both_barriers" in out.columns:
        out.loc[changed.index, "both_barriers"] = 0

    # The early end-of-data cutoff mark is not a realized fill. It is copied
    # unchanged when its observation window ends before the new 72h horizon.
    if not out.loc[frame["exit_reason"].eq("cutoff_mark") & frame["exit_hour"].le(HORIZON_HOURS)].equals(
        frame.loc[frame["exit_reason"].eq("cutoff_mark") & frame["exit_hour"].le(HORIZON_HOURS)]
    ):
        raise AssertionError("An early cutoff_mark row was changed or reclassified")
    return out, len(changed)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    frame = load_fills(args.input)
    variant, changed_count = build_variant(frame)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    tmp = args.output.with_name(args.output.name + ".tmp")
    variant.to_csv(tmp, index=False, float_format="%.17g")
    tmp.replace(args.output)

    counts = variant.loc[variant["exit_hour"].eq(HORIZON_HOURS)].groupby(
        ["group", "sample"], sort=True
    ).size().to_dict()
    print(f"VectorBT {vbt.__version__}; source fills={len(frame)}; changed={changed_count}", flush=True)
    print(f"72h timeout counts by group/sample: {counts}", flush=True)
    print(f"wrote {args.output}", flush=True)


if __name__ == "__main__":
    main()
