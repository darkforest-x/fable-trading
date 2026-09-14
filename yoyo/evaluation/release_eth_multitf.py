"""Preregistered development/frozen validation driver for the ETH release study.

Features use each bar and prior bars only. Volatility matching thresholds use
ATR-percent shifted one bar and a trailing 252-bar window. Future OHLC appears
only inside replay outcomes. The timestamp-first reader refuses restricted
prices. Selection reads only the already completed 2023/2024 folds.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict, replace
import hashlib
import json
from pathlib import Path
import subprocess

import numpy as np
import pandas as pd

from yoyo.data.release_eth_prefix import aggregate, read_prefix
from yoyo.evaluation.release_eth_metrics import account_stats, holm, inference, monthly_returns, settle, trade_stats
from yoyo.layers.l3_backtest.pine_allin_v7 import SignalParameters, add_indicators
from yoyo.layers.l3_backtest.release_eth_multitf import Policy, Replay

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "data/kline_deep/okx_ETH_USDT_SWAP_15m_158499.csv"
OUT = ROOT / "experiments/active/exp-release-eth-multitf-20260914-v1/results"
FILES = ["yoyo/data/release_eth_prefix.py", "yoyo/evaluation/release_eth_metrics.py",
         "yoyo/evaluation/release_eth_multitf.py", "yoyo/layers/l3_backtest/release_eth_multitf.py",
         "yoyo/layers/l3_backtest/pine_allin_v7.py",
         "experiments/active/exp-release-eth-multitf-20260914-v1/PROJECT_PLAN.md"]
PERIODS = {"dev2023": ("2023-01-01", "2024-01-01"),
           "dev2024": ("2024-01-01", "2025-01-01"),
           "development": ("2023-01-01", "2025-01-01"),
           "validation2025": ("2025-01-01", "2026-01-01"),
           "later2026": ("2026-01-01", "2026-05-01"),
           "continuous": ("2023-01-01", "2026-05-01")}


def clean(value):
    if isinstance(value, dict):
        return {str(k): clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [clean(v) for v in value]
    if isinstance(value, (np.integer, np.bool_)):
        return value.item()
    if isinstance(value, (float, np.floating)):
        return float(value) if np.isfinite(value) else None
    if isinstance(value, (pd.Timestamp, Path)):
        return str(value)
    return value


def dump(path: Path, value) -> None:
    path.write_text(json.dumps(clean(value), ensure_ascii=False, indent=2) + "\n")


def fingerprint() -> dict:
    return {f: hashlib.sha256((ROOT / f).read_bytes()).hexdigest() for f in FILES}


def assert_frozen_prefix(current: dict, frozen: dict) -> None:
    for key in ("consumed_prefix_sha256", "rows", "first_open", "last_close", "end_exclusive"):
        if current[key] != frozen[key]:
            raise ValueError(f"development source changed after selection: {key}")


def policies() -> dict[str, Policy]:
    o = Policy(name="O1")
    e1 = replace(o, name="E1", unit_leverage=True)
    e2 = replace(e1, name="E2", immediate_stop=True)
    e3 = replace(e2, name="E3", ratchet_only=True)
    e4 = replace(e3, name="E4", isolate_stops=True)
    e5 = replace(e4, name="E5", fresh_cooldown=True)
    result = {p.name: p for p in [replace(o, name="O0", fee=0), o, e1, e2, e3, e4, e5,
                                  replace(o, name="O1_immediate", immediate_stop=True)]}
    result.update({f"C{i}": replace(e5, name=f"C{i}") for i in range(6)})
    result["C3"] = replace(e5, name="C3", entry_gate="slope")
    result["C4"] = replace(e5, name="C4", skip_enabled=False)
    result["SMA"] = replace(e5, name="SMA", cross_only=True)
    return result


def features(raw: pd.DataFrame, minutes: int, arm: str) -> pd.DataFrame:
    f = add_indicators(raw, SignalParameters(slow_len={"C1": 40, "C2": 80}.get(arm, 60)))
    utc = pd.to_datetime(f.open_time, utc=True)
    f["calendar_allowed"] = ~utc.dt.hour.between(21, 22)
    if arm != "C5":
        f["calendar_allowed"] &= utc.dt.dayofweek.ne(6)
    f["entry_allowed"] = f.calendar_allowed & f.volatility_allowed
    prior = f.atr_percent.shift(1).rolling(252, min_periods=252)
    thresholds = np.column_stack([prior.quantile(q).to_numpy() for q in (.2, .4, .6, .8)])
    f["vol_bin"] = np.where(np.isfinite(thresholds).all(axis=1),
                            (f.atr_percent.to_numpy()[:, None] > thresholds).sum(axis=1), -1)
    # Only trim indicator warmup, never an internal gap or a calendar bar.
    ready = np.isfinite(f[["atr", "osc", "slow_ma"]]).all(axis=1) & f.vol_bin.ge(0)
    first = int(np.flatnonzero(ready.to_numpy())[0])
    if not ready.iloc[first:].all():
        raise ValueError("non-finite internal feature; no silent row removal")
    f = f.iloc[first:].reset_index(drop=True)
    if minutes == 240 and int((f.open_time < pd.Timestamp("2023-01-01", tz="UTC")).sum()) < 512:
        raise ValueError("insufficient common 4h warmup")
    return f


def match_controls(engine: Replay, result: dict, start: int, end: int, minutes: int) -> pd.DataFrame:
    """Match on decision-time facts only; outcomes never rank the pool."""
    t = result["trades"]
    if t.empty:
        for col in ("matched", "control_mean", "paired_excess", "control_count"):
            t[col] = pd.Series(dtype=bool if col == "matched" else float)
        return pd.DataFrame()
    frame = engine.frame
    month = frame.open_time.dt.strftime("%Y-%m").to_numpy()
    block = frame.hk_hour.to_numpy() // 6
    bins = frame.vol_bin.to_numpy()
    pools = {}
    for side in (-1, 1):
        for j in range(start, end - 1):
            if bins[j] >= 0 and engine.allowed[j] and not engine.raw[j] and engine._entry_allowed(j, side, None):
                pools.setdefault((side, month[j], block[j], bins[j]), []).append(j)
    used, rows = set(), []
    t["matched"], t["control_mean"], t["paired_excess"], t["control_count"] = False, np.nan, np.nan, 0
    exclusion = 48 * 60 // minutes
    for case_id, case in t.iterrows():
        i, side = int(case.signal_i), int(case.direction)
        pool = [j for j in pools.get((side, month[i], block[i], bins[i]), [])
                if abs(j - i) > exclusion and (j, side) not in used]
        seed = int(hashlib.sha256(f"20260914|{minutes}|{start}|{end}|{i}|{side}".encode()).hexdigest()[:16], 16)
        chosen = np.random.default_rng(seed).permutation(pool)[:3]
        values = []
        for j in chosen:
            j = int(j)
            used.add((j, side))
            control = settle(engine.run(j, end, injected=(j, side), record_equity=False),
                             frame, end, engine.policy.fee, minutes)
            if len(control["trades"]) != 1:
                raise AssertionError("eligible injected control must produce one settled outcome")
            row = control["trades"].iloc[0].to_dict()
            values.append(float(row["net_return"]))
            rows.append(dict(row, case_id=int(case_id), case_signal_i=i, match_month=month[i],
                             match_hk_block=int(block[i]), match_vol_bin=int(bins[i])))
        if values:
            mean = float(np.mean(values))
            t.loc[case_id, ["matched", "control_mean", "paired_excess", "control_count"]] = [True, mean, float(case.net_return) - mean, len(values)]
    return pd.DataFrame(rows)


def run_one(frame: pd.DataFrame, policy: Policy, minutes: int, period: str,
            directory: Path, execution: pd.DataFrame | None = None) -> dict:
    begin, finish = (pd.Timestamp(s, tz="UTC") for s in PERIODS[period])
    start, end = (int(frame.open_time.searchsorted(s)) for s in (begin, finish))
    if frame.open_time.iloc[end - 1] + pd.Timedelta(minutes=minutes) != finish:
        raise ValueError("period endpoint missing")
    engine = Replay(frame, policy, minutes, execution)
    result = settle(engine.run(start, end), frame, end, policy.fee, minutes)
    controls = match_controls(engine, result, start, end, minutes)
    t = result["trades"]
    if not t.empty:
        if not np.isclose(500 + t.net_pnl.sum(), result["final_equity"], rtol=1e-10, atol=1e-7):
            raise AssertionError("ledger/account reconciliation failed")
        if not np.isclose(t.fees.sum(), result["fees"], rtol=1e-10, atol=1e-7):
            raise AssertionError("ledger fees failed")
        if (t.exit_time > finish).any() or (t.signal_time < begin).any():
            raise AssertionError("labels crossed declared period")
    key = f"{minutes}m_{period}_{policy.name}" + ("_precision15m" if execution is not None else "")
    directory.mkdir(parents=True, exist_ok=True)
    t.to_csv(directory / f"{key}_trades.csv.gz", index=False)
    controls.to_csv(directory / f"{key}_controls.csv.gz", index=False)
    result["equity"].to_csv(directory / f"{key}_equity.csv.gz", index=False)
    result["events"].to_csv(directory / f"{key}_events.csv.gz", index=False)
    month = monthly_returns(result["equity"])
    month.to_csv(directory / f"{key}_months.csv", index=False)
    split = {}
    if len(t):
        for grouping, values in [("side", t.direction.map({1: "long", -1: "short"})),
                                 ("year", pd.to_datetime(t.signal_time, utc=True).dt.year.astype(str))]:
            split[grouping] = {str(k): trade_stats(t.loc[g.index]) for k, g in t.groupby(values)}
    answer = {"key": key, "minutes": minutes, "period": period, "arm": policy.name,
              "policy": asdict(policy), "start": begin, "end_exclusive": finish,
              "bars": end - start, "raw_signals": int(np.count_nonzero(engine.raw[start:end])),
              "eligible_raw_signals": int(np.count_nonzero(engine.raw[start:end] * engine.allowed[start:end])),
              "precision": result["execution_precision"], "stats": account_stats(result),
              "inference": inference(t), "breakdowns": split,
              "boundary_was_open": result["boundary_open_position"] is not None,
              "controls": len(controls), "ledger_account_reconciled": True}
    dump(directory / f"{key}_summary.json", answer)
    print(json.dumps(clean({"done": key, "return_pct": answer["stats"]["return_pct"],
                           "dd_pct": answer["stats"]["path_dd_pct"], "trades": len(t)})), flush=True)
    return answer


def select(rows: list[dict]) -> dict:
    """Frozen conjunction; validation rows cannot enter this function."""
    if any(r["period"] not in {"dev2023", "dev2024"} for r in rows):
        raise ValueError("selection accepts development folds only")
    out = {}
    for minutes in (15, 60, 240):
        lookup = {(r["arm"], r["period"]): r["stats"] for r in rows if r["minutes"] == minutes}
        base = [lookup[("C0", fold)] for fold in ("dev2023", "dev2024")]
        checks, admitted = {}, []
        for arm in ("C1", "C2", "C3", "C4", "C5"):
            a = [lookup[(arm, fold)] for fold in ("dev2023", "dev2024")]
            conditions = {"natural_trades_20": sum(x["natural_trades"] for x in a) >= 20,
                          "both_returns_positive": all(x["return_pct"] > 0 for x in a),
                          "both_excess_positive": all(x["excess_bp"] is not None and x["excess_bp"] > 0 for x in a),
                          "solvent": not any(x["nonpositive_equity_seen"] for x in a),
                          "worst_dd_not_above_C0": max(x["path_dd_pct"] for x in a) <= max(x["path_dd_pct"] for x in base),
                          "worst_return_above_C0": min(x["return_pct"] for x in a) > min(x["return_pct"] for x in base)}
            checks[arm] = conditions
            if all(conditions.values()):
                admitted.append((-min(x["return_pct"] for x in a), max(x["path_dd_pct"] for x in a), arm))
        out[str(minutes)] = {"selected": min(admitted)[2] if admitted else "C0",
                             "challenger_admitted": bool(admitted), "checks": checks}
    return out


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("phase", choices=["development", "validation"])
    args = parser.parse_args()
    phase_path = OUT / args.phase
    if phase_path.exists():
        raise SystemExit("existing phase evidence must not be overwritten")
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    # Builder tracked and clean before either run; unrelated workspace edits do not matter.
    for f in FILES:
        tracked = subprocess.check_output(["git", "show", f"HEAD:{f}"], cwd=ROOT)
        if hashlib.sha256(tracked).hexdigest() != fingerprint()[f]:
            raise SystemExit(f"commit builder before replay: {f}")
    selection_path = OUT / "selection.json"
    if args.phase == "validation":
        frozen = json.loads(selection_path.read_text())
        if frozen["builder_sha256"] != fingerprint():
            raise SystemExit("frozen development builder changed")
        _, prior_receipt = read_prefix(SOURCE, "2025-01-01T00:00Z")
        assert_frozen_prefix(prior_receipt, frozen["source_receipt"])
    safe_end = "2025-01-01T00:00Z" if args.phase == "development" else "2026-05-01T00:00Z"
    raw, source = read_prefix(SOURCE, safe_end)
    phase_path.mkdir(parents=True)
    dump(phase_path / "source_receipt.json", {**source, "builder_commit": commit,
                                             "builder_sha256": fingerprint(), "phase": args.phase})
    if args.phase == "validation":
        dump(phase_path / "development_prefix_recheck.json", prior_receipt)
    all_rows, fold_rows = [], []
    arms = policies()
    for minutes in (15, 60, 240):
        bars, aggregation = aggregate(raw, minutes)
        dump(phase_path / f"{minutes}m_aggregation.json", aggregation)
        cache = {}
        def get_frame(arm):
            identity = arm if arm in {"C1", "C2", "C5"} else "C0"
            if identity not in cache:
                cache[identity] = features(bars, minutes, identity)
            return cache[identity]
        if args.phase == "development":
            for arm in ("O0", "O1", "E1", "E2", "E3", "E4", "E5", "O1_immediate", "SMA"):
                all_rows.append(run_one(get_frame(arm), arms[arm], minutes, "development", phase_path))
            for period in ("dev2023", "dev2024"):
                for arm in ("C0", "C1", "C2", "C3", "C4", "C5", "SMA"):
                    row = run_one(get_frame(arm), arms[arm], minutes, period, phase_path)
                    all_rows.append(row)
                    if arm != "SMA":
                        fold_rows.append(row)
        else:
            chosen = frozen["selection"][str(minutes)]["selected"]
            for period in ("validation2025", "later2026", "continuous"):
                for arm in dict.fromkeys(("O0", "O1", "E1", "O1_immediate", "C0", chosen, "SMA")):
                    all_rows.append(run_one(get_frame(arm), arms[arm], minutes, period, phase_path))
            if minutes != 15:
                for arm in ("O1", chosen):
                    f = get_frame(arm)
                    all_rows.append(run_one(f, arms[arm], minutes, "validation2025", phase_path, raw))
    dump(phase_path / "all_summaries.json", all_rows)
    if args.phase == "development":
        if selection_path.exists():
            raise SystemExit("selection already frozen")
        dump(selection_path, {"selection": select(fold_rows), "selected_using": ["dev2023", "dev2024"],
                              "frozen_at": pd.Timestamp.now(tz="UTC"), "builder_commit": commit,
                              "builder_sha256": fingerprint(), "source_receipt": source})
    else:
        chosen_rows = [next(r for r in all_rows if r["minutes"] == m and r["period"] == "validation2025"
                           and r["arm"] == frozen["selection"][str(m)]["selected"] and r["precision"] == "parent_ohlc")
                       for m in (15, 60, 240)]
        adjusted = holm([r["inference"]["matched_p"] for r in chosen_rows])
        dump(phase_path / "validation_family.json", [{"minutes": r["minutes"], "arm": r["arm"],
              "matched_p": r["inference"]["matched_p"], "holm_p": p, "threshold": .01,
              "positive_net_and_excess": r["stats"]["net_bp"] > 0 and (r["stats"]["excess_bp"] or 0) > 0}
              for r, p in zip(chosen_rows, adjusted)])


if __name__ == "__main__":
    main()
