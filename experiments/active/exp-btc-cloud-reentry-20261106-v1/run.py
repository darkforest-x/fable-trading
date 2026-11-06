"""Reproduce the BTC re-entry study, preserving the native replay independently of analysis."""

from __future__ import annotations

import argparse
import json
import platform
import subprocess
import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT))
from analysis import measure_paths, summarize

NY = "America/New_York"
CONFIG = json.loads((HERE / "config.json").read_text())


def save_json(name, obj):
    (HERE / name).write_text(
        json.dumps(obj, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    )


def load_bars():
    columns = ["open", "high", "low", "close", "volume"]
    chunks, receipts = [], []
    for rel in CONFIG["data"]:
        path = ROOT / rel
        data = pd.read_csv(path, usecols=["open_time", *columns])
        data.index = pd.DatetimeIndex(pd.to_datetime(data.pop("open_time"), utc=True))
        data = data[columns].sort_index()
        assert data.index.is_unique and data.notna().all().all()
        receipts.append(
            {
                "path": rel,
                "rows": len(data),
                "bytes": path.stat().st_size,
                "first_open": str(data.index.min()),
                "last_open": str(data.index.max()),
            }
        )
        chunks.append(data)
    left, right = chunks
    overlap = left.index.intersection(right.index)
    if not len(overlap):
        raise ValueError("Tail data must overlap the registered primary source")
    np.testing.assert_allclose(
        left.loc[overlap, columns[:4]], right.loc[overlap, columns[:4]], rtol=0, atol=0
    )
    bars = pd.concat([left, right.loc[right.index > left.index[-1]]])
    assert bars.index.to_series().diff().dropna().eq(pd.Timedelta(minutes=5)).all()
    assert (bars.low <= bars[["open", "close"]].min(axis=1)).all()
    assert (bars.high >= bars[["open", "close"]].max(axis=1)).all()
    return bars, {
        "sources": receipts,
        "identical_ohlc_overlap": len(overlap),
        "continuous_5m_rows": len(bars),
    }


def eval_weeks():
    return [
        d.date().isoformat()
        for d in pd.date_range(
            CONFIG["first_week"],
            CONFIG["end_exclusive"],
            inclusive="left",
            freq="W-MON",
        )
        if d.date().isoformat() not in CONFIG["exclude_ny_weeks"]
    ]


def stamp_week(times):
    local = pd.DatetimeIndex(times).tz_convert(NY).tz_localize(None).normalize()
    return (local - pd.to_timedelta(local.weekday, unit="D")).strftime("%Y-%m-%d")


def prepare():
    from signals import build_candidates

    bars, receipt = load_bars()
    c = build_candidates(bars, include_unarmed=True).copy()
    # The illustrated weeks are calibration fixtures only and are removed below.
    anchors = [
        ("2026-09-03 09:25", -4.585, 0, True, False),
        ("2026-09-03 19:25", 0.112, 9, False, True),
        ("2026-09-09 08:10", -5.724, 0, False, False),
        ("2026-09-09 20:10", 0.708, 1, False, True),
    ]
    by_signal = c.set_index("signal_bar_open")
    for wall, distance, hours, bottom, trend in anchors:
        row = by_signal.loc[pd.Timestamp(wall, tz="Asia/Shanghai")]
        assert row.setup_armed and row.bull12h and abs(row.D - distance) < 0.002
        assert (
            row.h1_streak == hours
            and row.bottom_pass == bottom
            and row.trend_pass == trend
        )
    receipt["excluded_illustration_anchors_matched"] = len(anchors)
    c["entry_time"] = pd.to_datetime(c.entry_time, utc=True)
    c["exit_time"] = pd.to_datetime(c.exit_time, utc=True)
    # Bind the assignment to the known signal week. A next-open exactly on a
    # new-week boundary cannot open against the prior week's Monday range.
    c["ny_week"] = stamp_week(c.entry_time)
    c = c[
        (c.ny_week == stamp_week(c.entry_time - pd.Timedelta(minutes=5)))
        & c.ny_week.isin(eval_weeks())
        & c.bull12h
        & c.monday_complete
    ].copy()
    boundaries = sorted(set([*CONFIG["exclude_ny_weeks"], CONFIG["end_exclusive"]]))
    c["boundary_exit"] = False
    for day in boundaries:
        boundary = pd.Timestamp(day, tz=NY).tz_convert("UTC")
        mask = (c.entry_time < boundary) & (boundary <= c.exit_time)
        c.loc[mask, "exit_time"] = boundary
        c.loc[mask, "boundary_exit"] = True
    c = c.sort_values("entry_time")
    c["vol_bin"] = np.searchsorted(
        CONFIG["volatility_buckets_bp"], 1e4 * c.atr5_pre / c.pre_close, side="right"
    )
    assert c.entry_time.is_unique and (c.entry_time < c.exit_time).all()
    assert (
        c.entry_time.iloc[1:].reset_index(drop=True)
        >= c.exit_time.iloc[:-1].reset_index(drop=True)
    ).all()
    c.to_csv(HERE / "candidates.csv.gz", index=False)
    receipt.update(
        {
            "eligible_calendar_weeks": len(eval_weeks()),
            "matched_pool_candidates": len(c),
            "armed_candidates": int(c.setup_armed.sum()),
            "native_boundary_schedules": int(c.boundary_exit.sum()),
            "rules_commit": subprocess.check_output(
                ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
            ).strip(),
        }
    )
    save_json("preparation.json", receipt)
    print(
        "Prepared",
        receipt["matched_pool_candidates"],
        "candidate cycles;",
        receipt["armed_candidates"],
        "armed.",
        flush=True,
    )


def read_schedule():
    return pd.read_csv(
        HERE / "candidates.csv.gz", parse_dates=["entry_time", "exit_time"]
    )


def execute():
    from native_fills import run_native_fills

    if (HERE / "native_fills.csv.gz").exists():
        raise FileExistsError("Native results already exist; use the summarize stage")
    bars, _ = load_bars()
    c = read_schedule()
    orders = c[["entry_time", "exit_time", "initial_stop"]].copy()
    orders["exit_reason"] = np.where(c.boundary_exit, "dataset_end", c.exit_reason)
    fills = run_native_fills(bars, orders)
    fills.to_csv(HERE / "native_fills.csv.gz", index=False)
    save_json("native_receipt.json", fills.attrs["audit"])
    print("Native result saved:", len(fills), flush=True)


def analyze():
    bars, _ = load_bars()
    c = read_schedule()
    native = pd.read_csv(HERE / "native_fills.csv.gz", parse_dates=["entry_time"])
    native = native.rename(columns={"exit_time": "actual_exit_time"})
    if set(c.entry_time) != set(native.entry_time) or not native.entry_time.is_unique:
        raise ValueError("Native rows and scheduled candidates differ")
    # Scheduled exit_time is the common end of a trend opportunity. Actual
    # stop/market timestamps remain separately available in the raw fill ledger.
    keep = [
        "entry_time",
        "entry_price",
        "exit_price",
        "entry_fee",
        "exit_fee",
        "qty",
        "exit_reason",
    ]
    features = c.drop(columns=[k for k in keep if k != "entry_time" and k in c])
    f = features.merge(native[keep], on="entry_time", validate="one_to_one")
    skipped = f[f.exit_reason.eq("invalid_open_risk")]
    f = f[~f.exit_reason.eq("invalid_open_risk")].copy()
    f.loc[
        f.boundary_exit & f.exit_reason.isin(["cloud_exit", "dataset_end"]),
        "exit_reason",
    ] = "boundary"
    assert set(f.exit_reason).issubset(
        {"cloud_exit", "hard_stop", "boundary", "dataset_end"}
    )
    f = measure_paths(f, bars, CONFIG)
    f["gross_usdt"] = CONFIG["notional_usdt_per_trade"] * f.gross_return
    f["fees_usdt"] = CONFIG["notional_usdt_per_trade"] * f.fee_return
    primary, periods, weeks = summarize(f, eval_weeks(), CONFIG)
    f["trend_hold_pass"] = (
        f.D.ge(0) & f.h1_streak_pass & f.previous_bull_leg_any_close_above_monday_low
    )
    f.to_csv(HERE / "ledger.csv.gz", index=False)
    fields = [
        "ny_week",
        "signal_bar_open",
        "entry_time",
        "exit_time",
        "entry_price",
        "initial_stop",
        "exit_price",
        "exit_reason",
        "bottom_pass",
        "trend_pass",
        "trend_hold_pass",
        "mismatch_abs_atr",
        "mid_rebound_atr",
        "D",
        "h1_streak",
        "net_usdt",
        "fees_usdt",
        "net_r",
        "wave_r",
        "wave_return",
    ]
    if "actual_exit_time" in native:
        f = f.merge(
            native[["entry_time", "actual_exit_time"]],
            on="entry_time",
            validate="one_to_one",
        )
        fields.insert(4, "actual_exit_time")
    f.loc[f.setup_armed, fields].rename(
        columns={"exit_time": "scheduled_exit_open"}
    ).to_csv(HERE / "trades.csv.gz", index=False)
    periods.to_csv(HERE / "by_period.csv", index=False)
    weeks.to_csv(HERE / "by_week.csv", index=False)
    import nautilus_trader

    summary = {
        "experiment": CONFIG["experiment_id"],
        "generated_at": pd.Timestamp.now(tz="UTC").isoformat(),
        "engine_version": nautilus_trader.__version__,
        "python": platform.python_version(),
        "preparation": json.loads((HERE / "preparation.json").read_text()),
        "skipped_invalid_open_all_pool": len(skipped),
        "skipped_invalid_open_armed": int(skipped.setup_armed.sum()),
        "shared_filter_candidates": int(
            (f.setup_armed & f.bottom_pass & f.trend_pass).sum()
        ),
        "major_r": CONFIG["major_multiple_r"],
        "arms": primary,
    }
    save_json("summary.json", summary)
    for arm, s in primary.items():
        print(
            arm,
            s["trades"],
            s["stopouts"],
            s["large_waves"],
            s["large_winners"],
            round(s["net_usdt"], 2),
        )
    print("Written", HERE / "summary.json", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("stage", choices=["prepare", "execute", "summarize", "all"])
    stage = parser.parse_args().stage
    for label, action in [
        ("prepare", prepare),
        ("execute", execute),
        ("summarize", analyze),
    ]:
        if stage in (label, "all"):
            action()
