"""Frozen 28-month BB × Stoch diagnosis: owner's BE-with-cost plus three gates.

Every arm differs from the ``be_cost`` reference by exactly one thing, and the
gates admit entries only -- the opposite-composite exit always reads the
unfiltered v2 signal, so no filter can hold a losing position open.

The window deliberately extends 23 months before anything this strategy family
has been run on.  That data is spent by looking at it once, so this module runs
only the pre-registered arms: no parameter search exists anywhere in it.  Price
access stops at 2026-04-30T16:00Z through the frozen pre-holdout reader, whose
``end > 2026-05-01`` refusal is not relaxed.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

import numpy as np
import pandas as pd

from yoyo.data.spike_fanshen_prefix import read_prefix
from yoyo.evaluation.bb_stoch_parameter_replay import ParamSpec, compute_features, prepare, replay_entry
from yoyo.evaluation.bb_stoch_rsi_study import audit, rsi_series
from yoyo.evaluation.eth_bb_stoch_study import (ROOT, compare, draw_controls, enrich,
                                                match_context, save, sha, stats)
from yoyo.evaluation.ma_stoch_rsi_filter import zone_admission
from yoyo.evaluation.parabolic_rsi_sar import diamonds, pine_sar, within

EXP = ROOT / "experiments/active/exp-eth-bb-stoch-longrun-diagnosis-20260916-v1"
BUILDERS = [
    "yoyo/evaluation/bb_stoch_longrun_study.py",
    "yoyo/evaluation/bb_stoch_longrun_report.py",
    "yoyo/evaluation/bb_stoch_parameter_replay.py",
    "yoyo/evaluation/bb_stoch_rsi_study.py",
    "yoyo/evaluation/eth_bb_stoch_study.py",
    "yoyo/evaluation/parabolic_rsi_sar.py",
    "yoyo/evaluation/ma_stoch_rsi_filter.py",
    "yoyo/evaluation/spike_v6_bb_squeeze.py",
    "yoyo/evaluation/spike_fanshen_exit.py",
    "yoyo/data/spike_fanshen_prefix.py",
    "yoyo/contracts/holdout.py",
    "tests/evaluation/test_bb_stoch_longrun_study.py",
    "tests/evaluation/test_parabolic_rsi_sar.py",
    "tests/evaluation/test_bb_stoch_parameter_replay.py",
]
FEE = 0.001


def freeze_receipt() -> dict:
    """Refuse to read prices unless every builder and plan matches HEAD."""
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    names = BUILDERS + [str((EXP / n).relative_to(ROOT)) for n in ("config.json", "PROJECT_PLAN.md")]
    files = {}
    for name in names:
        committed = subprocess.check_output(["git", "show", f"{head}:{name}"], cwd=ROOT)
        if committed != (ROOT / name).read_bytes():
            raise ValueError(f"Uncommitted builder/config: {name}")
        files[name] = hashlib.sha256(committed).hexdigest()
    return dict(source_commit=head, files=files, code_frozen_before_price_read=True,
                generated_at=pd.Timestamp.now(tz="UTC"))


def cross_check(frame: pd.DataFrame, cfg: dict) -> dict:
    """Compare the archive-derived prefix against the independently fetched CSV.

    Two OKX products disagreeing on a shared bar would make every downstream
    number source-dependent, so the overlap is compared before any replay.
    """
    other = pd.read_csv(ROOT / cfg["cross_check_source"])
    other["open_time"] = pd.to_datetime(other.open_time, utc=True)
    other = other.set_index("open_time")
    common = frame.index.intersection(other.index)
    columns = ["open", "high", "low", "close"]
    delta = (frame.loc[common, columns] - other.loc[common, columns]).abs()
    return dict(other=cfg["cross_check_source"], other_sha256=sha(ROOT / cfg["cross_check_source"]),
                overlapping_bars=int(len(common)),
                max_abs_ohlc_difference=float(delta.to_numpy().max()) if len(common) else None,
                rows_differing=int((delta > 1e-9).any(axis=1).sum()))


def build_gates(frame: pd.DataFrame, signals: np.ndarray, cfg: dict) -> tuple[dict, dict]:
    """Return each pre-registered gate's admitted-entry array and its diagnostics."""
    rsi = frame.rsi.to_numpy(float)
    sar, below = pine_sar(rsi, start=cfg["sar_start"], increment=cfg["sar_increment"],
                          maximum=cfg["sar_maximum"], init_bars=cfg["rsi_length"] + 2)
    marks = diamonds(sar, below, lower=cfg["rsi_lower"], upper=cfg["rsi_upper"])
    window = cfg["sar_window_bars"]
    allow = {
        "rsi_line": ((signals == 1) & (rsi < cfg["rsi_lower"])) | ((signals == -1) & (rsi > cfg["rsi_upper"])),
        "sar_strong_same_bar": ((signals == 1) & marks["strong_up"]) | ((signals == -1) & marks["strong_dn"]),
        "sar_strong_window": ((signals == 1) & within(marks["strong_up"], window))
                             | ((signals == -1) & within(marks["strong_dn"], window)),
        "sar_direction": ((signals == 1) & below) | ((signals == -1) & ~below),
    }
    gates = {name: np.where(mask, signals, 0).astype(int) for name, mask in allow.items()}
    # The RSI gate has an independent implementation; agreeing with it is a
    # cheap guard against a transcription error in the array algebra above.
    check = zone_admission(signals, rsi, cfg["rsi_lower"], cfg["rsi_upper"])
    if not np.array_equal(gates["rsi_line"], check):
        raise AssertionError("rsi_line gate disagrees with zone_admission")
    for name, gate in gates.items():
        if np.any((gate != 0) & (gate != signals)):
            raise AssertionError(f"gate {name} flipped a side")
    frame["sar"] = sar
    frame["sar_below"] = below
    return gates, {name: marks[name].sum() for name in marks}


def run_arm(frame: pd.DataFrame, prepared, context: dict, entry_signal: np.ndarray,
            path_mode: str, minutes: int = 5) -> list[dict]:
    """One serial 1-unit stream; exits read the unfiltered signals in ``prepared``.

    ``minutes`` is the source bar duration, used only to stamp times and hold
    length; the default keeps every five-minute caller identical.
    """
    candidates = np.flatnonzero((entry_signal != 0) & context["valid"])
    rows, earliest = [], 0
    for i in candidates:
        if i < earliest:
            continue
        row = replay_entry(prepared, int(i), side_override=int(entry_signal[i]), path_mode=path_mode)
        if row is None:
            continue
        row = enrich(row, frame, minutes)
        rows.append(row)
        if row["censored"]:
            break
        earliest = int(row["exit_i"])
    return rows


def fee_curve(rows: list[dict], rates) -> list[dict]:
    """Recompute net totals at other fee rates exactly, without a new replay.

    A fee never moves a stop, a target or a fill, so the frozen price path is
    still the right one; only the deduction changes.  ``f*`` is the rate that
    would make the cash total zero, and it is negative when the rule loses
    money before costs.
    """
    gross = sum(r["gross_pnl"] for r in rows)
    notional = sum(r["fees"] / FEE for r in rows)
    out = []
    for rate in rates:
        out.append(dict(fee_rate=rate,
                        net_pnl=gross - rate * notional,
                        net_r=sum((r["gross_pnl"] - rate * r["fees"] / FEE) / r["initial_risk"] for r in rows)))
    return out + [dict(fee_rate="breakeven_f_star", net_pnl=0.0,
                       value=gross / notional if notional else None, gross_pnl=gross, notional=notional)]


def event_level(frame: pd.DataFrame, prepared, context: dict, gates: dict, cfg: dict):
    """Replay each admissible signal independently, then score every gate label."""
    signals = frame.signal.to_numpy(int)
    admissible = np.flatnonzero((signals != 0) & context["valid"])
    rows = []
    for i in admissible:
        row = replay_entry(prepared, int(i), path_mode=cfg["primary_path"])
        if row is None:
            continue
        row = enrich(row, frame)
        row["rsi"] = float(frame.rsi.iloc[int(i)])
        for name, gate in gates.items():
            row[f"gate_{name}"] = bool(gate[int(i)] != 0)
        rows.append(row)
    audit(rows)
    natural = [r for r in rows if not r["censored"]]
    summary = dict(events=len(rows), natural=len(natural), censored=len(rows) - len(natural))
    rng = np.random.default_rng(cfg["seed"])
    draws = cfg["event_label_permutations"]
    for name in gates:
        passed = np.array([r["net_r"] for r in natural if r[f"gate_{name}"]], dtype=float)
        blocked = np.array([r["net_r"] for r in natural if not r[f"gate_{name}"]], dtype=float)
        record = dict(passed_n=len(passed), blocked_n=len(blocked),
                      passed_mean_net_r=float(passed.mean()) if len(passed) else None,
                      blocked_mean_net_r=float(blocked.mean()) if len(blocked) else None,
                      passed_win_rate=float((passed > 0).mean()) if len(passed) else None,
                      blocked_win_rate=float((blocked > 0).mean()) if len(blocked) else None)
        if len(passed) and len(blocked):
            values = np.r_[passed, blocked]
            observed = float(passed.mean() - blocked.mean())
            null = np.empty(draws)
            for n in range(draws):
                shuffled = rng.permutation(values)
                null[n] = shuffled[:len(passed)].mean() - shuffled[len(passed):].mean()
            record.update(mean_difference=observed, permutations=draws,
                          p=float((1 + (null >= observed - 1e-12).sum()) / (1 + draws)),
                          method="seeded_event_label_permutation_one_sided")
        else:
            record.update(mean_difference=None, p=None,
                          method="not applicable: a label side had no natural event")
        summary[name] = record
    return summary, rows


def partitions(rows: list[dict], cfg: dict) -> dict:
    """Fixed diagnostic splits; none of them selects anything."""
    seen = pd.Timestamp(cfg["previously_used_window_start"])
    out = {"all": rows, "long": [r for r in rows if r["side"] == 1],
           "short": [r for r in rows if r["side"] == -1],
           "newly_used_window": [r for r in rows if pd.Timestamp(r["signal_close"]) < seen],
           "previously_used_window": [r for r in rows if pd.Timestamp(r["signal_close"]) >= seen]}
    out.update({year: [r for r in rows if r["month"][:4] == year]
                for year in sorted({r["month"][:4] for r in rows})})
    return out


def main() -> None:
    cfg = json.loads((EXP / "config.json").read_text())
    if cfg["parameter_search"] or cfg["holdout_consumed"]:
        raise ValueError("this study runs frozen arms on pre-holdout data only")
    receipt = freeze_receipt()
    save(EXP / "code_receipt.json", receipt)
    raw, data_receipt = read_prefix(ROOT / cfg["source"], cfg["minutes"], cfg["end"])
    frame = compute_features(raw)
    frame["rsi"] = rsi_series(frame, cfg["rsi_length"])
    signals = frame.signal.to_numpy(int)
    gates, diamond_counts = build_gates(frame, signals, cfg)
    context = match_context(frame)
    save(EXP / "source_receipt.json", dict(data_receipt, cross_check=cross_check(raw, cfg)))
    admissible = (signals != 0) & context["valid"]
    counts = dict(bars=int(len(frame)), raw_signals=int((signals != 0).sum()),
                  admissible=int(admissible.sum()),
                  admissible_long=int((admissible & (signals == 1)).sum()),
                  admissible_short=int((admissible & (signals == -1)).sum()),
                  strong_diamonds_in_sample={k: int(v) for k, v in diamond_counts.items()})
    for name, gate in gates.items():
        counts[name] = dict(passed=int((gate[admissible] != 0).sum()),
                            passed_long=int((gate[admissible & (signals == 1)] != 0).sum()),
                            passed_short=int((gate[admissible & (signals == -1)] != 0).sum()))
    frame.loc[signals != 0, ["open", "high", "low", "close", "upper", "lower", "k", "d",
                             "signal", "rsi", "sar"]].to_csv(EXP / "signals.csv", index_label="open_time")
    results = dict(config=cfg, data=data_receipt, counts=counts,
                   source_commit=receipt["source_commit"], arms={}, fee_curves={})
    accounting = []
    for arm, plan in cfg["arms"].items():
        spec = ParamSpec(stop_fraction=cfg["stop_fraction"], partial_fraction=plan["partial"],
                         be_cost_fraction=plan["be_cost"])
        prepared = prepare(frame, spec)
        entry_signal = signals if plan["gate"] is None else gates[plan["gate"]]
        results["arms"][arm] = {}
        for mode in [cfg["primary_path"], *cfg["sensitivity_paths"].get(arm, [])]:
            rows = run_arm(frame, prepared, context, entry_signal, mode)
            audit(rows)
            draws = {int(x["signal_i"]): draw_controls(context, int(x["signal_i"]), int(x["side"]), cfg)
                     for x in rows}
            control_rows = []
            for x in rows:
                for number, j in enumerate(draws[int(x["signal_i"])], 1):
                    control = replay_entry(prepared, int(j), side_override=int(x["side"]), path_mode=mode)
                    if control is None:
                        continue
                    control = enrich(control, frame)
                    control.update(actual_signal_i=int(x["signal_i"]), draw_number=number,
                                   actual_month=x["month"], vol_bucket=int(context["bucket"][int(j)]))
                    control_rows.append(control)
            audit(control_rows)
            accounting.append(dict(arm=arm, path=mode, actual=len(rows), controls=len(control_rows), passed=True))
            save(EXP / f"{arm}_{mode}_trades.json", rows)
            pd.DataFrame([{k: v for k, v in r.items() if k != "fills"} for r in rows]).to_csv(
                EXP / f"{arm}_{mode}_trades.csv", index=False)
            if mode == cfg["primary_path"]:
                save(EXP / f"{arm}_controls.json", control_rows)
                results["fee_curves"][arm] = fee_curve([r for r in rows if not r["censored"]],
                                                       cfg["fee_sensitivity_rates"])
            results["arms"][arm][mode] = {name: dict(stats=stats(group), control=compare(group, control_rows, cfg))
                                          for name, group in partitions(rows, cfg).items()}
            s = results["arms"][arm][mode]["all"]["stats"]
            print(f"{arm:24s} {mode:14s} n={s['natural']:4d} netR={s['net_r']:+9.3f} "
                  f"perTrade={s['mean_net_r'] if s['mean_net_r'] is None else round(s['mean_net_r'], 4)} "
                  f"PF={s['profit_factor'] if s['profit_factor'] is None else round(s['profit_factor'], 3)}", flush=True)
    events, event_rows = event_level(frame, prepare(frame, ParamSpec(
        stop_fraction=cfg["stop_fraction"], partial_fraction=cfg["partial_fraction"],
        be_cost_fraction=cfg["be_cost_fraction"])), context, gates, cfg)
    results["event_level"] = events
    pd.DataFrame([{k: v for k, v in r.items() if k != "fills"} for r in event_rows]).to_csv(
        EXP / "event_level_trades.csv", index=False)
    save(EXP / "accounting_validation.json", accounting)
    save(EXP / "results.json", results)
    print("event_level", json.dumps({k: v for k, v in events.items() if not isinstance(v, dict)},
                                    ensure_ascii=False), flush=True)
    print("Saved", EXP, flush=True)


if __name__ == "__main__":
    main()
