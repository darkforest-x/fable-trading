"""Causal first-per-compression V7 entry ablations on receipt-bound caches.

Features use only the frozen BB-compression flags and OHLC through the current
close. Each price-escape decision compares with the previous close's episode
envelope; future returns are used exclusively for evaluation and illustration.
Entry masks are injected into a private StreamContext, preserving the original
raw V6 reversal feed and the existing exit/capital engines without monkeypatches.
"""
from __future__ import annotations

import argparse
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import time

import numpy as np
import pandas as pd
from pandas.testing import assert_frame_equal

from yoyo.evaluation.spike_exit_policy_study import (
    END, SPLIT, START, load_verified_stream, replay_policy, sha256,
)
from yoyo.evaluation.spike_exit_accounts import marked_account_grid, period_summary
from yoyo.evaluation.spike_v6_wvf_study_post import matched_random_controls

EXP = Path("experiments/active/exp-spike-v7-first-launch-20260912-v1")
ARMS = ("baseline", "first", "first_break")


def episode_admissions(bars, raw, bb, gap):
    """Track 3-bar compression clusters linked by <=10 bars between run ends.

    Columns: frozen long/short signals, bb_compressed/prior_squeeze_run3/v7_ready,
    high/low/close and explicit gap. Compression endpoints and envelopes update
    AFTER the current entry decision. The first left-censored episode passes
    through unchanged until the full-source prior-run flag certifies a reset.
    """
    ix = bars.index
    if not all(x.index.equals(ix) for x in (raw, bb, gap)):
        raise ValueError("episode inputs must have identical clocks")
    long = raw.long_signal.fillna(False).to_numpy(bool)
    short = raw.short_signal.fillna(False).to_numpy(bool)
    if (long & short).any():
        raise ValueError("ambiguous signal side")
    side = np.where(long, 1, np.where(short, -1, 0))
    compressed = bb.bb_compressed.fillna(False).to_numpy(bool)
    recent_run = bb.prior_squeeze_run3.fillna(False).to_numpy(bool)
    base = ((long | short) & bb.v7_ready.fillna(False).to_numpy(bool)
            & recent_run)
    h, l, c = (bars[k].to_numpy(float) for k in ("high", "low", "close"))
    gaps = gap.fillna(True).to_numpy(bool)
    n = len(ix)
    first, escape = base.copy(), base.copy()
    ep = np.full(n, -1, dtype=int)
    upper, lower = np.full(n, np.nan), np.full(n, np.nan)
    fallback = np.zeros(n, dtype=bool)
    why_a, why_c = np.full(n, "not_v7", object), np.full(n, "not_v7", object)
    serial, last_q, run = -1, None, 0
    known, used_a, used_c = False, False, False
    hi, lo = np.nan, np.nan
    for i in range(n):
        if gaps[i] or not np.isfinite([h[i], l[i], c[i]]).all():
            last_q, run, known = None, 0, True
            used_a = used_c = False
            hi = lo = np.nan
            first[i] = escape[i] = False
            continue
        # The receipt-bound flag was computed BEFORE cache truncation. A cache
        # index is not proof that a hidden qualifying endpoint has expired:
        # it may bridge into another run visible near the cache boundary.
        if not recent_run[i]:
            known = True
        active = last_q is not None and i - last_q <= 10
        ep[i] = serial if active else -1
        upper[i], lower[i] = hi, lo
        if base[i]:
            if not known or not active:
                # No new gate is imposed on unresolved cache-prefix state.
                fallback[i] = True
                why_a[i] = why_c[i] = "unknown_episode_passthrough"
            else:
                first[i] = not used_a
                why_a[i] = "first" if first[i] else "episode_consumed"
                used_a = True
                beyond = c[i] > hi if side[i] == 1 else c[i] < lo
                escape[i] = not used_c and beyond
                why_c[i] = "first_escape" if escape[i] else ("episode_consumed" if used_c else "inside_envelope")
                if escape[i]:
                    used_c = True
        # The current candle cannot enlarge its own breakout boundary.
        run = run + 1 if compressed[i] else 0
        if run >= 3:
            new = last_q is None or i - last_q > 10
            if new:
                serial += 1
                used_a = used_c = False
                hi, lo = float(np.max(h[i-2:i+1])), float(np.min(l[i-2:i+1]))
            else:
                hi, lo = max(hi, float(np.max(h[i-2:i+1]))), min(lo, float(np.min(l[i-2:i+1])))
            last_q = i
    return pd.DataFrame({"baseline": base, "first": first, "first_break": escape,
                         "episode": ep, "upper": upper, "lower": lower,
                         "fallback": fallback, "side": side,
                         "reason_first": why_a, "reason_first_break": why_c}, index=ix)


def replay_mask(context, mask):
    """Replace only admission flags; protective raw reversals remain untouched."""
    cache = dict(context.cache)
    cache["bb"] = context.cache["bb"].copy()
    cache["bb"]["prior_squeeze_run3"] = mask.astype(bool)
    return replay_policy(replace(context, cache=cache), cohort="v7_both", policy="baseline")


def verify_baseline(context, trades):
    """Validate every natural trade against the prior frozen V7 ledger."""
    old = pd.read_csv(context.path / "trades.csv.gz")
    old = old.loc[old.variant.eq("v7_bb_both") & ~old.censored.astype(bool)]
    new = trades.loc[~trades.censored.astype(bool)]
    cols = ["signal_i", "entry_i", "side", "exit_i", "exit_reason", "entry_price",
            "exit_price", "initial_stop", "initial_risk", "net_return", "net_r"]
    assert_frame_equal(old.reindex(columns=cols).sort_values("signal_i").reset_index(drop=True),
                       new.reindex(columns=cols).sort_values("signal_i").reset_index(drop=True),
                       check_dtype=False, rtol=1e-9, atol=1e-9)


def period(stamps):
    return np.where(pd.to_datetime(stamps, utc=True) < SPLIT, "development", "validation")


def trade_metrics(t):
    closed = t.loc[~t.censored.astype(bool)]
    gains, losses = closed.net_return.clip(lower=0).sum(), -closed.net_return.clip(upper=0).sum()
    return {"entries": len(t), "closed": len(closed), "censored": len(t)-len(closed),
            "wins": int(closed.net_return.gt(0).sum()), "losses": int(closed.net_return.lt(0).sum()),
            "gain_sum": gains, "loss_sum": losses, "net_r_sum": closed.net_r.sum(),
            "gross_return_sum": closed.gross_return.sum(), "net_return_sum": closed.net_return.sum(),
            "realized_10r": int(closed.net_r.ge(10).sum()), "mfe_10r": int(closed.mfe_r.ge(10).sum())}


def sample_controls(context, trades):
    """One hash-selected closed event per arm/side/year; matched prior-vol null."""
    closed = trades.loc[~trades.censored.astype(bool)].copy()
    if closed.empty:
        return pd.DataFrame(columns=["arm", "period", "matched", "net_return_difference", "net_r_difference"])
    closed["_hash"] = [hashlib.sha256(f"episode-v1|{t}|{int(s)}".encode()).hexdigest()
                       for t, s in zip(closed.signal_bar_open, closed.side)]
    chosen = closed.sort_values("_hash").groupby(["arm", "side", "period"], sort=False).head(1)
    rows = []
    for side, subset in chosen.groupby("side"):
        cache = dict(context.cache)
        cache["bars"] = cache["bars"].copy()
        cache["bars"].attrs["minutes"] = context.minutes
        opposite = cache["signals"].short_signal if side == 1 else cache["signals"].long_signal
        cache["bars"]["ready"] &= cache["bb_ready"].astype(bool) & ~opposite.astype(bool)
        keys = ["signal_bar_open", "side"]
        targets = subset.drop_duplicates(keys)
        pairs, _ = matched_random_controls(cache, targets, tick=float(cache["tick"]),
                                           fold_start=START, fold_end=END, seeds=[0])
        pairs = pairs.rename(columns={"target_time": "signal_bar_open"})
        pairs["signal_bar_open"] = pd.to_datetime(pairs.signal_bar_open, utc=True)
        ids = subset[[*keys, "arm", "period"]].copy()
        ids["signal_bar_open"] = pd.to_datetime(ids.signal_bar_open, utc=True)
        rows.append(ids.merge(pairs, on=keys, how="left", validate="many_to_one"))
    return pd.concat(rows, ignore_index=True)


def run_one(context):
    bars = context.cache["bars"]
    gates = episode_admissions(bars, context.cache["signals"], context.cache["bb"], context.cache["data_gap"])
    gates["signal_bar_open"] = gates.index
    gates["period"] = period(gates.index + pd.Timedelta(minutes=context.minutes))
    in_window = (gates.index + pd.Timedelta(minutes=context.minutes) >= START) & (gates.index + pd.Timedelta(minutes=context.minutes) < END)
    trades_all, accounts, events = [], [], []
    for arm in ARMS:
        trades, fills, _ = replay_mask(context, gates[arm])
        if arm == "baseline":
            verify_baseline(context, trades)
        trades["arm"] = arm
        trades["period"] = period(trades.entry_time)
        trades["episode"] = [int(gates.loc[pd.Timestamp(t), "episode"]) for t in trades.signal_bar_open]
        times, nav, _ = marked_account_grid(bars, trades, fills, settings=((.01, 1.),), minutes=context.minutes)
        for row in period_summary(pd.DataFrame({"time": times, "equity": nav[:, 0]})):
            accounts.append({"arm": arm, **row})
        for part in ("development", "validation"):
            group = trades.loc[trades.period.eq(part)]
            events.append({"arm": arm, "period": part, **trade_metrics(group),
                           "signals": int(gates.loc[in_window & gates.period.eq(part), arm].sum()),
                           "fallback_signals": int((gates.loc[in_window & gates.period.eq(part), arm] & gates.loc[in_window & gates.period.eq(part), "fallback"]).sum())})
        trades_all.append(trades)
    trades = pd.concat(trades_all, ignore_index=True)
    # Exact entry retention is the primary contract. Episode matches are separate.
    base = trades.loc[trades.arm.eq("baseline") & ~trades.censored.astype(bool)]
    retention = []
    for arm in ARMS[1:]:
        alt = trades.loc[trades.arm.eq(arm)]
        exact = {(str(t), int(s)) for t, s in zip(alt.signal_bar_open, alt.side)}
        episode_keys = {(int(e), int(s)) for e, s in zip(alt.episode, alt.side) if e >= 0}
        for r in base.itertuples(index=False):
            retained = (str(r.signal_bar_open), int(r.side)) in exact
            retention.append({"arm": arm, "period": r.period, "side": r.side, "episode": r.episode,
                              "signal_bar_open": r.signal_bar_open, "entry_time": r.entry_time,
                              "net_r": r.net_r, "mfe_r": r.mfe_r, "net_return": r.net_return,
                              "exact_retained": retained,
                              "same_episode_entry": (int(r.episode), int(r.side)) in episode_keys if r.episode >= 0 else retained,
                              "reason": gates.loc[pd.Timestamp(r.signal_bar_open), "reason_"+arm]})
    identity = {"stream_key": context.key, **context.identity}
    result = {"accounts": pd.DataFrame(accounts), "events": pd.DataFrame(events), "trades": trades,
              "retention": pd.DataFrame(retention, columns=["arm", "period", "side", "episode", "signal_bar_open", "entry_time", "net_r", "mfe_r", "net_return", "exact_retained", "same_episode_entry", "reason"]),
              "signals": gates.loc[in_window & gates.baseline].reset_index(drop=True),
              "controls": sample_controls(context, trades)}
    for table in result.values():
        for key, value in identity.items():
            table[key] = value
    return result


def run(output, limit=None):
    config = json.loads((EXP / "config.json").read_text())
    raw = Path(config["raw"])
    if sha256(raw / "manifest.json") != config["raw_manifest_sha256"]:
        raise ValueError("frozen manifest changed")
    sources = [Path(__file__), Path(__file__).with_name("spike_exit_policy_study.py"),
               Path(__file__).with_name("spike_exit_accounts.py"), Path(__file__).with_name("spike_v6_wvf_study_post.py"),
               Path(__file__).with_name("spike_v7_fast.py"), Path(__file__).with_name("spike_v6_wvf_study.py"),
               EXP / "config.json", EXP / "PROJECT_PLAN.md"]
    identity = {str(p): sha256(p) for p in sources}
    output.mkdir(parents=True, exist_ok=True)
    (output / "streams").mkdir(exist_ok=True)
    ip = output / "identity.json"
    if ip.exists() and json.loads(ip.read_text()) != identity:
        raise ValueError("run identity changed; use a new output directory")
    ip.write_text(json.dumps(identity, indent=2))
    folders = sorted(p for p in (raw / "streams").iterdir() if (p / "completion.json").exists())
    if len(folders) != config["expected_streams"]:
        raise ValueError("unexpected frozen stream count")
    selected = folders if limit is None else folders[:limit]
    began = time.monotonic()
    for number, folder in enumerate(selected, 1):
        receipt = output / "streams" / (folder.name + ".json")
        if receipt.exists():
            saved = json.loads(receipt.read_text())
            if any(sha256(output / "streams" / name) != digest for name, digest in saved["files"].items()):
                raise ValueError("output stream identity changed")
            continue
        context = load_verified_stream(folder)
        tables = run_one(context)
        files = {}
        for kind, table in tables.items():
            path = output / "streams" / f"{folder.name}.{kind}.csv.gz"
            table.to_csv(path, index=False, compression={"method": "gzip", "mtime": 0, "compresslevel": 1})
            files[path.name] = sha256(path)
        receipt.write_text(json.dumps({"files": files, "cache_sha256": context.receipt["cache_sha256"], "baseline_parity": True}, indent=2))
        if number % 50 == 0 or number == len(selected):
            print(json.dumps({"done": number, "scope": len(selected), "seconds": round(time.monotonic()-began, 1)}), flush=True)
    (output / "manifest.json").write_text(json.dumps({"complete": len(selected) == config["expected_streams"],
        "streams": len(selected), "expected": config["expected_streams"], "identity_sha256": sha256(ip),
        "generated_at": pd.Timestamp.now(tz="UTC").isoformat(), "source_manifest_sha256": config["raw_manifest_sha256"]}, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--limit", type=int)
    args = parser.parse_args()
    run(args.output, args.limit)
