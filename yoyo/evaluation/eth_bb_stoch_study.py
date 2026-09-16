"""Frozen BB/Stoch v2 study with pre-only data and outcome-blind controls.

Source: owner v2 Pine and TradingView's documented OHLC broker path. BB/Stoch
features consume current closed bars and prior history. Control volatility
uses OHLC true range SMA14 / current close, with preceding120 ratio terciles.
Only exits may read subsequent permitted bars. No parameter selection occurs.
"""
from __future__ import annotations

import hashlib
import itertools
import json
import subprocess
from collections import Counter
from pathlib import Path

import numpy as np
import pandas as pd

from yoyo.data.spike_fanshen_prefix import read_prefix
from yoyo.evaluation.bb_stoch_replay import compute_features, replay_entry

ROOT = Path(__file__).resolve().parents[2]
EXP = ROOT / "experiments/active/exp-eth-bb-stoch-backtest-20260916-v1"
BUILDERS = [
    "yoyo/evaluation/eth_bb_stoch_study.py",
    "yoyo/evaluation/eth_bb_stoch_report.py",
    "yoyo/evaluation/bb_stoch_replay.py",
    "yoyo/evaluation/spike_fanshen_exit.py",
    "yoyo/data/spike_fanshen_prefix.py",
    "yoyo/data/release_eth_prefix.py",
    "yoyo/contracts/holdout.py",
    "yoyo/evaluation/pine/eth_bb_stoch_strategy_v2.pine",
    str((EXP / "config.json").relative_to(ROOT)),
    str((EXP / "PROJECT_PLAN.md").relative_to(ROOT)),
]


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def save(path, obj):
    def clean(x):
        if isinstance(x, dict): return {str(k): clean(v) for k, v in x.items()}
        if isinstance(x, (list, tuple)): return [clean(v) for v in x]
        if isinstance(x, np.generic): return clean(x.item())
        if isinstance(x, float) and not np.isfinite(x): return None
        if isinstance(x, pd.Timestamp): return x.isoformat()
        return x
    Path(path).write_text(json.dumps(clean(obj), ensure_ascii=False, indent=2) + "\n")


def freeze_receipt(cfg):
    """Refuse to read prices when any frozen builder differs from HEAD."""
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    files = {}
    for name in BUILDERS:
        committed = subprocess.check_output(["git", "show", f"{head}:{name}"], cwd=ROOT)
        current = (ROOT / name).read_bytes()
        if committed != current:
            raise ValueError(f"Uncommitted builder/config: {name}")
        files[name] = hashlib.sha256(current).hexdigest()
    if sha(ROOT / cfg["pine_source"]) != cfg["pine_sha256"]:
        raise ValueError("Canonical v2 Pine identity changed")
    return dict(source_commit=head, files=files,
                code_frozen_before_price_read=True, generated_at=pd.Timestamp.now(tz="UTC"))


def match_context(frame, minutes=5):
    """Causal controls use current TR-SMA14 / close and prior120 terciles.

    ``minutes`` is the source bar duration; it only positions the confirmed
    close, and the default keeps every existing five-minute caller identical.
    """
    previous = frame.close.shift()
    tr = pd.concat([frame.high-frame.low, (frame.high-previous).abs(),
                    (frame.low-previous).abs()], axis=1).max(axis=1)
    vol = tr.rolling(14, min_periods=14).mean() / frame.close
    prior = vol.shift().rolling(120, min_periods=120)
    q1, q2 = prior.quantile(1/3), prior.quantile(2/3)
    bucket = np.where(vol <= q1, 0, np.where(vol <= q2, 1, 2))
    confirmed = frame.index + pd.Timedelta(minutes=minutes)
    valid = (q1.notna() & frame.upper.notna() & frame.lower.notna()).to_numpy()
    valid[-1] = False
    return dict(month=confirmed.strftime("%Y-%m").to_numpy(), bucket=bucket,
                valid=valid, confirmed=confirmed)


def draw_controls(context, i, side, cfg):
    """Draw indices without looking at any exit/censor outcome."""
    month, bucket = context["month"][i], context["bucket"][i]
    pool = np.flatnonzero(context["valid"] & (context["month"] == month)
                          & (context["bucket"] == bucket))
    pool = pool[pool != i]
    rng = np.random.default_rng(np.random.SeedSequence([cfg["seed"], int(i), int(side)+1]))
    return rng.choice(pool, min(cfg["controls_per_trade"], len(pool)), replace=False).tolist()


def enrich(row, frame, minutes=5):
    """Attach times, month and hold length for one replayed trade.

    ``minutes`` is the source bar duration; the default reproduces every
    existing five-minute caller exactly.
    """
    if row is None: raise ValueError("Valid replay produced no result")
    row = dict(row)
    for name in ("signal", "entry", "exit"):
        row[name + "_time"] = frame.index[int(row[name + "_i"])].isoformat()
    row["signal_close"] = (pd.Timestamp(row["signal_time"]) + pd.Timedelta(minutes=minutes)).isoformat()
    row["month"] = row["signal_close"][:7]
    row["hold_hours"] = (row["exit_i"]-row["entry_i"]+1) * minutes / 60
    return row


def run_serial(frame, context, path_mode):
    candidates = np.flatnonzero((frame.signal.to_numpy() != 0) & context["valid"])
    rows, earliest = [], 0
    for i in candidates:
        if i < earliest: continue
        row = enrich(replay_entry(frame, int(i), path_mode=path_mode), frame)
        rows.append(row)
        if row["censored"]: break
        # An intrabar exit permits that bar's later confirmed signal. A REV
        # signal closes the old trade at close and enters the opposite next open.
        earliest = int(row["exit_i"])
    return rows


def max_streak(flags):
    current = best = 0
    for flag in flags:
        current = current+1 if flag else 0
        best = max(best, current)
    return best


def stats(rows):
    natural = [x for x in rows if not x["censored"]]
    values = np.array([x["net_r"] for x in natural], dtype=float)
    cash = np.array([x["net_pnl"] for x in natural], dtype=float)
    eq, cash_eq = np.r_[0., values.cumsum()], np.r_[0., cash.cumsum()]
    wins, losses = values[values > 0], values[values < 0]
    return dict(total_entries=len(rows), natural=len(natural), censored=len(rows)-len(natural),
        wins=len(wins), losses=len(losses), win_rate=len(wins)/len(values) if len(values) else None,
        gross_r=sum(x["gross_r"] for x in natural), net_r=float(values.sum()),
        mean_net_r=float(values.mean()) if len(values) else None,
        profit_factor=float(wins.sum() / -losses.sum()) if len(losses) else None,
        avg_win_r=float(wins.mean()) if len(wins) else None,
        avg_loss_r=float(losses.mean()) if len(losses) else None,
        max_realized_drawdown_r=float((np.maximum.accumulate(eq)-eq).max()),
        max_realized_drawdown_usdt=float((np.maximum.accumulate(cash_eq)-cash_eq).max()),
        max_loss_streak=max_streak(values < 0), gross_pnl=sum(x["gross_pnl"] for x in natural),
        fees=sum(x["fees"] for x in natural), net_pnl=float(cash.sum()),
        partial=sum(x["partial"] for x in natural), reasons=dict(Counter(x["exit_reason"] for x in natural)),
        mean_hold_hours=float(np.mean([x["hold_hours"] for x in natural])) if natural else None,
        boundary_mark_net_r=sum(x["net_r"] for x in rows if x["censored"]),
        boundary_mark_net_pnl=sum(x["net_pnl"] for x in rows if x["censored"]),
        ambiguous_bar_count=sum(x["ambiguous_bar_count"] for x in rows),
        entries_open_beyond_target=sum(x["entry_open_beyond_target"] for x in rows),
        same_open_be_approximations=sum(x.get("same_open_be_approximation", False) for x in rows),
        marketable_be_approximations=sum(x.get("marketable_be_approximation", False) for x in rows))


def signflip(pairs, cfg):
    blocks = pd.DataFrame(pairs).groupby("month").excess_net_r.sum().to_numpy() if pairs else np.array([])
    if not len(blocks): return dict(months=0, p=None)
    if len(blocks) <= 16:
        signs = np.array(list(itertools.product([-1, 1], repeat=len(blocks))))
        null = signs @ blocks
        p = float(np.mean(null >= blocks.sum()-1e-12))
        method = "exact_month_block_sign_symmetry_one_sided"
    else:
        rng = np.random.default_rng(cfg["seed"])
        null = rng.choice([-1, 1], (cfg["max_signflip_permutations"], len(blocks))) @ blocks
        p = float((1+(null >= blocks.sum()-1e-12).sum())/(1+len(null)))
        method = "seeded_month_block_sign_symmetry_one_sided"
    return dict(months=len(blocks), p=p, method=method, null_draws=len(null))


def compare(rows, controls, cfg):
    selected = {int(x["signal_i"]): x for x in rows}
    groups = {i: [] for i in selected}
    for x in controls:
        if int(x["actual_signal_i"]) in groups: groups[int(x["actual_signal_i"])].append(x)
    natural_pairs, all_pairs = [], []
    for i, actual in selected.items():
        group = groups[i]
        if len(group) != cfg["controls_per_trade"]: continue
        r = float(np.mean([x["net_r"] for x in group]))
        pair = dict(signal_i=i, month=actual["month"], actual_net_r=actual["net_r"],
                    random_net_r=r, excess_net_r=actual["net_r"]-r)
        all_pairs.append(pair)
        if not actual["censored"] and not any(x["censored"] for x in group): natural_pairs.append(pair)
    return dict(paired_n=len(natural_pairs), unpaired_n=len(rows)-len(natural_pairs),
        paired_actual_mean_net_r=float(np.mean([x["actual_net_r"] for x in natural_pairs])) if natural_pairs else None,
        random_mean_net_r=float(np.mean([x["random_net_r"] for x in natural_pairs])) if natural_pairs else None,
        excess_mean_net_r=float(np.mean([x["excess_net_r"] for x in natural_pairs])) if natural_pairs else None,
        controls_drawn=sum(len(g) for g in groups.values()),
        controls_censored=sum(x["censored"] for g in groups.values() for x in g),
        all_draw_boundary_mark_excess=float(np.mean([x["excess_net_r"] for x in all_pairs])) if all_pairs else None,
        **signflip(natural_pairs, cfg))


def main():
    cfg = json.loads((EXP / "config.json").read_text())
    receipt = freeze_receipt(cfg)
    save(EXP / "code_receipt.json", receipt)
    raw, data_receipt = read_prefix(ROOT / cfg["source"], 5, cfg["end"])
    frame = compute_features(raw)
    context = match_context(frame)
    midpoint = raw.index[0] + (pd.Timestamp(cfg["end"])-raw.index[0])/2
    save(EXP / "source_receipt.json", data_receipt)
    signal_rows = frame.loc[frame.signal != 0, ["open", "high", "low", "close", "upper", "lower", "k", "d", "signal"]]
    signal_rows.to_csv(EXP / "signals.csv", index_label="open_time")
    results = dict(config=cfg, data=data_receipt, midpoint=midpoint,
                   raw_signals=len(signal_rows), valid_signals=int(((frame.signal != 0) & context["valid"]).sum()),
                   valid_signal_close_start=context["confirmed"][np.flatnonzero(context["valid"])[0]],
                   paths={}, source_commit=receipt["source_commit"])
    accounting = []
    for mode in [cfg["primary_path"], *cfg["sensitivity_paths"]]:
        rows = run_serial(frame, context, mode)
        # Freeze every draw before replaying any of the control exits.
        draws = {int(x["signal_i"]): draw_controls(context, int(x["signal_i"]), int(x["side"]), cfg) for x in rows}
        save(EXP / f"{mode}_control_draws.json", draws)
        control_rows = []
        for x in rows:
            for draw_number, j in enumerate(draws[int(x["signal_i"])], 1):
                row = enrich(replay_entry(frame, int(j), side_override=int(x["side"]), path_mode=mode), frame)
                row.update(actual_signal_i=int(x["signal_i"]), draw_number=draw_number,
                           actual_month=x["month"], vol_bucket=int(context["bucket"][int(j)]))
                control_rows.append(row)
        for x in [*rows, *control_rows]:
            risk = x["initial_risk"]
            if abs(x["gross_pnl"]-x["fees"]-x["net_pnl"]) > 1e-8: raise AssertionError("cash ledger")
            if abs(x["net_pnl"]/risk-x["net_r"]) > 1e-8: raise AssertionError("net R ledger")
            fills = x["fills"]
            executed_fees = sum(f["price"]*f["qty"]*.001 for f in fills)
            exit_qty = sum(f["qty"] for f in fills[1:])
            if abs(executed_fees-x["fees"]) > 1e-8: raise AssertionError("executed-notional fees")
            if not x["censored"] and abs(exit_qty-1.) > 1e-12: raise AssertionError("exit quantities")
            fill_gross = sum(x["side"]*(f["price"]-x["entry_price"])*f["qty"] for f in fills[1:])
            if x["censored"]: fill_gross += x["side"]*(x["exit_price"]-x["entry_price"])*(1-exit_qty)
            if abs(fill_gross-x["gross_pnl"]) > 1e-8: raise AssertionError("independent fill P/L")
        accounting.append(dict(path=mode, actual=len(rows), controls=len(control_rows), passed=True))
        save(EXP / f"{mode}_trades.json", rows)
        save(EXP / f"{mode}_controls.json", control_rows)
        pd.DataFrame([{k: v for k, v in r.items() if k != "fills"} for r in rows]).to_csv(EXP / f"{mode}_trades.csv", index=False)
        fills = [dict(trade_number=n, signal_i=r["signal_i"], **f) for n, r in enumerate(rows, 1) for f in r["fills"]]
        pd.DataFrame(fills).to_csv(EXP / f"{mode}_fills.csv", index=False)
        partitions = {"all": rows,
                      "first_half": [r for r in rows if pd.Timestamp(r["signal_close"]) < midpoint],
                      "second_half": [r for r in rows if pd.Timestamp(r["signal_close"]) >= midpoint],
                      "long": [r for r in rows if r["side"] == 1],
                      "short": [r for r in rows if r["side"] == -1]}
        partitions.update({m: [r for r in rows if r["month"] == m] for m in sorted(set(r["month"] for r in rows))})
        results["paths"][mode] = {name: dict(stats=stats(group), control=compare(group, control_rows, cfg)) for name, group in partitions.items()}
        print(mode, json.dumps(results["paths"][mode]["all"], ensure_ascii=False), flush=True)
    save(EXP / "accounting_validation.json", accounting)
    save(EXP / "results.json", results)
    print("Saved", EXP, flush=True)


if __name__ == "__main__":
    main()
