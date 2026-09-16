"""Frozen three-arm ChartPrime RSI-zone comparison on the BB × Stoch v2 replay.

The owner asked for one added entry condition: long only while the Parabolic
RSI [ChartPrime] line is oversold, short only while it is overbought.  Every
other v2 rule, cost and default stays frozen, and the exit side of the two
filtered arms is stated explicitly rather than inherited by accident:
``rsi_entry`` (the arm the saved Pine implements) filters new entries only, so
the unfiltered opposite composite can still close a losing position, while
``rsi_both`` is a pre-registered sensitivity in which the gate also suppresses
that exit.

Removing entries reshuffles later serial opportunities, so the serial ledgers
alone cannot attribute the net difference to the blocked trades.  The
event-level section therefore replays every admissible raw signal independently
and tests the gate label against a seeded permutation null.

All price access goes through the timestamp-first prefix reader and stops
before the 2026-05-04 holdout.  No parameter search, training or live action.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

import numpy as np
import pandas as pd

from yoyo.data.spike_fanshen_prefix import read_prefix
from yoyo.evaluation.bb_stoch_replay import compute_features, replay_entry
from yoyo.evaluation.eth_bb_stoch_study import (ROOT, compare, draw_controls, enrich,
                                                match_context, save, sha, stats)
from yoyo.evaluation.ma_stoch_rsi_filter import zone_admission
from yoyo.evaluation.spike_v6_bb_squeeze import _rsi_wilder

EXP = ROOT / "experiments/active/exp-eth-bb-stoch-rsi-filter-20260916-v1"
BUILDERS = [
    "yoyo/evaluation/bb_stoch_rsi_study.py",
    "yoyo/evaluation/bb_stoch_rsi_report.py",
    "yoyo/evaluation/eth_bb_stoch_study.py",
    "yoyo/evaluation/bb_stoch_replay.py",
    "yoyo/evaluation/ma_stoch_rsi_filter.py",
    "yoyo/evaluation/spike_v6_bb_squeeze.py",
    "yoyo/evaluation/spike_fanshen_exit.py",
    "yoyo/data/spike_fanshen_prefix.py",
    "yoyo/contracts/holdout.py",
    "tests/evaluation/test_bb_stoch_rsi_study.py",
    "yoyo/evaluation/pine/eth_bb_stoch_strategy_v3.pine",
    "yoyo/evaluation/pine/eth_bb_stoch_strategy_v2.pine",
]


def freeze_receipt(cfg: dict) -> dict:
    """Refuse to read prices unless every builder, plan and Pine matches HEAD."""
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    names = BUILDERS + [str((EXP / name).relative_to(ROOT)) for name in ("config.json", "PROJECT_PLAN.md")]
    files = {}
    for name in names:
        committed = subprocess.check_output(["git", "show", f"{head}:{name}"], cwd=ROOT)
        if committed != (ROOT / name).read_bytes():
            raise ValueError(f"Uncommitted builder/config: {name}")
        files[name] = hashlib.sha256(committed).hexdigest()
    for key, source in (("pine_sha256", "pine_source"), ("parent_pine_sha256", "parent_pine_source"),
                        ("chartprime_sha256", "chartprime_source")):
        if sha(ROOT / cfg[source]) != cfg[key]:
            raise ValueError(f"Frozen source identity changed: {cfg[source]}")
    return dict(source_commit=head, files=files, code_frozen_before_price_read=True,
                generated_at=pd.Timestamp.now(tz="UTC"))


def rsi_series(frame: pd.DataFrame, length: int) -> np.ndarray:
    """Wilder RSI of close, restarted at each data gap exactly like the bands.

    ``compute_features`` already segments BB/target history on ``_data_gap``;
    joining RSI across a missing stretch would invent an indicator value that
    the chart never showed.
    """
    close = pd.to_numeric(frame.close, errors="coerce")
    segment = frame["_data_gap"].astype(bool).cumsum()
    out = pd.Series(np.nan, index=frame.index, dtype=float)
    for _, part in close.groupby(segment, sort=False):
        out.loc[part.index] = _rsi_wilder(part, length)
    return out.to_numpy(float)


def run_arm(frame: pd.DataFrame, context: dict, path_mode: str, entry_signal: np.ndarray,
            exit_frame: pd.DataFrame) -> list[dict]:
    """Replay one serial 1 ETH position stream for a single arm.

    ``entry_signal`` decides which confirmed bars may open a trade;
    ``exit_frame`` carries the signal column that the opposite-composite exit
    reads, which is the only difference between ``rsi_entry`` and ``rsi_both``.
    """
    candidates = np.flatnonzero((entry_signal != 0) & context["valid"])
    rows, earliest = [], 0
    for i in candidates:
        if i < earliest:
            continue
        row = enrich(replay_entry(exit_frame, int(i), side_override=int(entry_signal[i]),
                                  path_mode=path_mode), frame)
        rows.append(row)
        if row["censored"]:
            break
        earliest = int(row["exit_i"])
    return rows


def audit(rows: list[dict]) -> None:
    """Recompute every ledger identity from the fills, not from the summary."""
    for x in rows:
        if abs(x["gross_pnl"] - x["fees"] - x["net_pnl"]) > 1e-8:
            raise AssertionError("cash ledger")
        if abs(x["net_pnl"] / x["initial_risk"] - x["net_r"]) > 1e-8:
            raise AssertionError("net R ledger")
        fills = x["fills"]
        if abs(sum(f["price"] * f["qty"] * .001 for f in fills) - x["fees"]) > 1e-8:
            raise AssertionError("executed-notional fees")
        exit_qty = sum(f["qty"] for f in fills[1:])
        if not x["censored"] and abs(exit_qty - 1.) > 1e-12:
            raise AssertionError("exit quantities")
        gross = sum(x["side"] * (f["price"] - x["entry_price"]) * f["qty"] for f in fills[1:])
        if x["censored"]:
            gross += x["side"] * (x["exit_price"] - x["entry_price"]) * (1 - exit_qty)
        if abs(gross - x["gross_pnl"]) > 1e-8:
            raise AssertionError("independent fill P/L")


def baseline_parity(rows: list[dict], source: dict) -> dict:
    """Check the unfiltered arm against the already frozen v2 ledger."""
    previous = ROOT / "experiments/active/exp-eth-bb-stoch-backtest-20260916-v1"
    prior = json.loads((previous / "results.json").read_text())
    if source["prefix_sha256"] != prior["data"]["prefix_sha256"]:
        raise ValueError("frozen data prefix changed")
    old = pd.read_csv(previous / "tv_trades.csv")
    if len(rows) != len(old):
        raise AssertionError("baseline trade count changed")
    new = pd.DataFrame([{k: v for k, v in r.items() if k != "fills"} for r in rows])
    for column in ("signal_i", "entry_i", "exit_i", "side"):
        np.testing.assert_array_equal(new[column].to_numpy(int), old[column].to_numpy(int))
    for column in ("entry_price", "exit_price", "net_r", "net_pnl"):
        np.testing.assert_allclose(new[column], old[column], rtol=0, atol=1e-9)
    return dict(status="passed", trades=len(rows), results_sha256=sha(previous / "results.json"),
                trades_sha256=sha(previous / "tv_trades.csv"))


def event_level(frame: pd.DataFrame, context: dict, gate: np.ndarray, cfg: dict) -> tuple[dict, list[dict]]:
    """Replay every admissible raw signal independently and test the gate label.

    Serial occupancy decides which later signals a filtered account could even
    see, so this section deliberately ignores occupancy: one 1 ETH trade per
    admissible signal, grouped by whether the RSI zone admitted it.  The null
    is that the label carries no information; labels are permuted with a fixed
    seed inside the event set and the one-sided mean-difference is counted.
    """
    signals = frame.signal.to_numpy(int)
    admissible = np.flatnonzero((signals != 0) & context["valid"])
    rows = []
    for i in admissible:
        row = enrich(replay_entry(frame, int(i), path_mode=cfg["primary_path"]), frame)
        row["rsi"] = float(frame.rsi.iloc[int(i)]) if np.isfinite(frame.rsi.iloc[int(i)]) else None
        row["gate_passed"] = bool(gate[int(i)] != 0)
        rows.append(row)
    audit(rows)
    natural = [r for r in rows if not r["censored"]]
    passed = np.array([r["net_r"] for r in natural if r["gate_passed"]], dtype=float)
    blocked = np.array([r["net_r"] for r in natural if not r["gate_passed"]], dtype=float)
    result = dict(events=len(rows), natural=len(natural), censored=len(rows) - len(natural),
                  passed_n=len(passed), blocked_n=len(blocked),
                  passed_mean_net_r=float(passed.mean()) if len(passed) else None,
                  blocked_mean_net_r=float(blocked.mean()) if len(blocked) else None,
                  passed_win_rate=float((passed > 0).mean()) if len(passed) else None,
                  blocked_win_rate=float((blocked > 0).mean()) if len(blocked) else None,
                  passed_net_r=float(passed.sum()), blocked_net_r=float(blocked.sum()))
    if len(passed) and len(blocked):
        values = np.r_[passed, blocked]
        observed = float(passed.mean() - blocked.mean())
        rng = np.random.default_rng(cfg["seed"])
        draws = cfg["event_label_permutations"]
        null = np.empty(draws)
        for n in range(draws):
            shuffled = rng.permutation(values)
            null[n] = shuffled[:len(passed)].mean() - shuffled[len(passed):].mean()
        result.update(mean_difference=observed, permutations=draws,
                      p=float((1 + (null >= observed - 1e-12).sum()) / (1 + draws)),
                      method="seeded_event_label_permutation_one_sided")
    else:
        result.update(mean_difference=None, p=None,
                      method="not applicable: one side of the label had no natural event")
    return result, rows


def main() -> None:
    cfg = json.loads((EXP / "config.json").read_text())
    receipt = freeze_receipt(cfg)
    save(EXP / "code_receipt.json", receipt)
    raw, data_receipt = read_prefix(ROOT / cfg["source"], cfg["minutes"], cfg["end"])
    frame = compute_features(raw)
    frame["rsi"] = rsi_series(frame, cfg["rsi_length"])
    context = match_context(frame)
    midpoint = raw.index[0] + (pd.Timestamp(cfg["end"]) - raw.index[0]) / 2
    signals = frame.signal.to_numpy(int)
    gate = zone_admission(signals, frame.rsi.to_numpy(float), cfg["lower"], cfg["upper"])
    if np.any((gate != 0) & (gate != signals)):
        raise AssertionError("the zone gate may only remove signals, never flip a side")
    save(EXP / "source_receipt.json", data_receipt)
    columns = ["open", "high", "low", "close", "upper", "lower", "k", "d", "signal", "rsi"]
    admissible = (signals != 0) & context["valid"]
    candidates = frame.loc[:, columns].assign(valid=context["valid"], gate=gate,
                                              signal_close=context["confirmed"]).loc[signals != 0]
    candidates.to_csv(EXP / "signals.csv", index_label="open_time")
    counts = dict(raw_signals=int((signals != 0).sum()), admissible=int(admissible.sum()),
                  gate_passed=int((gate[admissible] != 0).sum()),
                  gate_blocked=int((gate[admissible] == 0).sum()),
                  undefined_rsi=int(np.isnan(frame.rsi.to_numpy(float)[admissible]).sum()))
    for label, side in (("long", 1), ("short", -1)):
        mask = admissible & (signals == side)
        counts[label] = dict(admissible=int(mask.sum()), gate_passed=int((gate[mask] != 0).sum()),
                             median_rsi=float(np.nanmedian(frame.rsi.to_numpy(float)[mask])) if mask.any() else None)
    results = dict(config=cfg, data=data_receipt, midpoint=midpoint, counts=counts,
                   valid_signal_close_start=context["confirmed"][np.flatnonzero(context["valid"])[0]],
                   source_commit=receipt["source_commit"], arms={})
    gated_frame = frame.assign(signal=gate)
    plans = {"unfiltered": (signals, frame), "rsi_entry": (gate, frame), "rsi_both": (gate, gated_frame)}
    accounting = []
    for arm in cfg["arms"]:
        entry_signal, exit_frame = plans[arm]
        results["arms"][arm] = {}
        for mode in [cfg["primary_path"], *cfg["sensitivity_paths"]]:
            rows = run_arm(frame, context, mode, entry_signal, exit_frame)
            audit(rows)
            if arm == "unfiltered" and mode == cfg["primary_path"] and cfg["baseline_parity_required"]:
                results["baseline_parity"] = baseline_parity(rows, data_receipt)
            draws = {int(x["signal_i"]): draw_controls(context, int(x["signal_i"]), int(x["side"]), cfg)
                     for x in rows}
            save(EXP / f"{arm}_{mode}_control_draws.json", draws)
            control_rows = []
            for x in rows:
                for number, j in enumerate(draws[int(x["signal_i"])], 1):
                    row = enrich(replay_entry(exit_frame, int(j), side_override=int(x["side"]),
                                              path_mode=mode), frame)
                    row.update(actual_signal_i=int(x["signal_i"]), draw_number=number,
                               actual_month=x["month"], vol_bucket=int(context["bucket"][int(j)]))
                    control_rows.append(row)
            audit(control_rows)
            accounting.append(dict(arm=arm, path=mode, actual=len(rows), controls=len(control_rows), passed=True))
            save(EXP / f"{arm}_{mode}_trades.json", rows)
            save(EXP / f"{arm}_{mode}_controls.json", control_rows)
            pd.DataFrame([{k: v for k, v in r.items() if k != "fills"} for r in rows]).to_csv(
                EXP / f"{arm}_{mode}_trades.csv", index=False)
            fills = [dict(trade_number=n, signal_i=r["signal_i"], **f)
                     for n, r in enumerate(rows, 1) for f in r["fills"]]
            pd.DataFrame(fills).to_csv(EXP / f"{arm}_{mode}_fills.csv", index=False)
            partitions = {"all": rows,
                          "first_half": [r for r in rows if pd.Timestamp(r["signal_close"]) < midpoint],
                          "second_half": [r for r in rows if pd.Timestamp(r["signal_close"]) >= midpoint],
                          "long": [r for r in rows if r["side"] == 1],
                          "short": [r for r in rows if r["side"] == -1]}
            partitions.update({m: [r for r in rows if r["month"] == m]
                               for m in sorted({r["month"] for r in rows})})
            results["arms"][arm][mode] = {name: dict(stats=stats(group), control=compare(group, control_rows, cfg))
                                          for name, group in partitions.items()}
            print(arm, mode, json.dumps(results["arms"][arm][mode]["all"]["stats"], ensure_ascii=False), flush=True)
    events, event_rows = event_level(frame, context, gate, cfg)
    results["event_level"] = events
    save(EXP / "event_level_trades.json", event_rows)
    pd.DataFrame([{k: v for k, v in r.items() if k != "fills"} for r in event_rows]).to_csv(
        EXP / "event_level_trades.csv", index=False)
    save(EXP / "accounting_validation.json", accounting)
    save(EXP / "results.json", results)
    print("event_level", json.dumps(events, ensure_ascii=False), flush=True)
    print("Saved", EXP, flush=True)


if __name__ == "__main__":
    main()
