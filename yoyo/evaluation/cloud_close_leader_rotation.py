"""Rank long OKX breakouts once weekly and compare with same-clock random picks."""
from __future__ import annotations

import argparse
import gc
import json
import math
from collections import Counter, defaultdict
from functools import lru_cache
from pathlib import Path

import numpy as np
import pandas as pd

from yoyo.evaluation import cloud_close_weak_rally_branch as universe
from yoyo.evaluation import cloud_close_reward_paths as engine
from yoyo.monitor.cloud_signal_plans import fixed_stop


wallet = universe.wallet
ROOT = wallet.ROOT
EXP = ROOT / "experiments/active/exp-cloud-close-leader-rotation-20261109-v1"
STEP = universe.STEP
HOUR = wallet.HOUR
DAY = 24 * HOUR
SEEDS = (20261109, 20261110, 20261111, 20261112, 20261113)
SLOTS = 3


def _complete(index: np.ndarray, start: int, count: int, step: int) -> bool:
    loc = int(np.searchsorted(index, start, side="left"))
    if loc + count > len(index):
        return False
    return bool(np.array_equal(index[loc:loc + count], start + step * np.arange(count, dtype=np.int64)))


def _config() -> dict:
    cfg = json.loads((EXP / "config.json").read_text(encoding="utf-8"))
    if cfg.get("id") != "exp-cloud-close-leader-rotation-20261109-v1":
        raise ValueError("Unexpected rotation experiment config")
    if tuple(cfg["controls"]["fixed_seeds"]) != SEEDS:
        raise ValueError("Random control seeds changed")
    return cfg


def _source_meta(src: dict) -> tuple[dict[str, int], dict[str, str]]:
    directory_cfg = json.loads((universe.breadth.UNIVERSE / "config.json").read_text())
    delivery = {s: int(pd.Timestamp(n["delivery_from_utc"]).value // 1_000_000)
                for n in directory_cfg["notices"] for s in n["symbols"]}
    if len(delivery) != 69 or len(src) != 360:
        raise ValueError(f"Historical universe changed: sources={len(src)}, retired={len(delivery)}")
    kinds = {s: ("retired" if s in delivery else "original") for s in src}
    return delivery, kinds


def _eligible_for_symbol(symbol: str, source, delivery: dict[str, int], kind: str) -> tuple[list[dict], dict]:
    h1, bars = universe.frames(source)
    end_ms = int(source[2])
    t1 = h1.index.to_numpy(dtype=np.int64)
    t12 = bars.index.to_numpy(dtype=np.int64)
    close = bars.close.to_numpy(dtype=float)
    records = bars.reset_index().rename(columns={"index": "time"})
    ema = engine._ema_points_12h(records[["time", "open", "high", "low", "close"]].to_dict("records"))
    ema20 = ema.ema20.to_numpy(dtype=float)
    ema50 = ema.ema50.to_numpy(dtype=float)
    ready = ema.ready.to_numpy(dtype=bool)
    prior = np.full(len(close), np.nan, dtype=float)
    for i in range(60, len(close)):
        if t12[i] - t12[i - 60] == 60 * STEP and close[i - 60] > 0:
            prior[i] = close[i] / close[i - 60] - 1.0
    decision = t12 + STEP
    dt = pd.to_datetime(decision, unit="ms", utc=True)
    is_wed = (dt.day_of_week == 2) & (dt.hour == 0) & (dt.minute == 0)
    records_out: list[dict] = []
    counts = Counter()
    anchors: dict[str, dict | None] = {}
    for i in np.flatnonzero(is_wed):
        key, at = int(t12[i]), int(decision[i])
        window = universe.breadth.ny(key)
        if not (int(window["monday_end"]) <= key and at < int(window["expires"])):
            continue
        week = str(window["week"])
        if week not in anchors:
            if _complete(t1, int(window["start"]), 24, HOUR):
                loc = int(np.searchsorted(t1, int(window["start"]), side="left"))
                monday = h1.iloc[loc:loc + 24]
                mh, ml = float(monday.high.max()), float(monday.low.min())
                anchors[week] = ({"high": mh, "low": ml,
                                 "upper": mh + .618 * (mh - ml)} if mh > ml > 0 else None)
            else:
                anchors[week] = None
        anchor = anchors[week]
        if anchor is None:
            counts["incomplete_monday_anchor"] += 1
            continue
        upper = float(anchor["upper"])
        reference_stop, stop_pct = fixed_stop(symbol, 1, upper)
        if not (ready[i] and ema20[i] > ema50[i] and close[i] > upper):
            continue
        counts["cloud_and_extension_close"] += 1
        if not np.isfinite(prior[i]):
            counts["trailing_30d_not_contiguous"] += 1
            records_out.append({"symbol": symbol, "week": week, "decision_ms": at,
                                "bar_open_ms": key, "entry_ms": at, "upper_extension": upper,
                                "reference_stop": reference_stop, "fixed_stop_pct": stop_pct,
                                "monday_high": anchor["high"], "monday_low": anchor["low"],
                                "signal_close": float(close[i]), "ema20": float(ema20[i]),
                                "ema50": float(ema50[i]), "momentum30": np.nan,
                                "source_type": kind, "source_end_ms": end_ms,
                                "delivery_ms": delivery.get(symbol),
                                "eligibility_status": "trailing_30d_not_contiguous"})
            continue
        if at < wallet.START or at >= wallet.END:
            counts["outside_wallet_window"] += 1
            continue
        if at >= end_ms:
            counts["entry_at_or_after_source_expiry"] += 1
            records_out.append({"symbol": symbol, "week": week, "decision_ms": at,
                                "bar_open_ms": key, "entry_ms": at, "upper_extension": upper,
                                "reference_stop": reference_stop, "fixed_stop_pct": stop_pct,
                                "monday_high": anchor["high"], "monday_low": anchor["low"],
                                "signal_close": float(close[i]), "ema20": float(ema20[i]),
                                "ema50": float(ema50[i]), "momentum30": float(prior[i]),
                                "source_type": kind, "source_end_ms": end_ms,
                                "delivery_ms": delivery.get(symbol),
                                "eligibility_status": "entry_at_or_after_source_expiry"})
            continue
        j = int(np.searchsorted(t1, at, side="left"))
        if j >= len(t1) or int(t1[j]) != at:
            counts["missing_exact_h1_entry_open"] += 1
            records_out.append({"symbol": symbol, "week": week, "decision_ms": at,
                                "bar_open_ms": key, "entry_ms": at, "upper_extension": upper,
                                "reference_stop": reference_stop, "fixed_stop_pct": stop_pct,
                                "monday_high": anchor["high"], "monday_low": anchor["low"],
                                "signal_close": float(close[i]), "ema20": float(ema20[i]),
                                "ema50": float(ema50[i]), "momentum30": float(prior[i]),
                                "source_type": kind, "source_end_ms": end_ms,
                                "delivery_ms": delivery.get(symbol),
                                "eligibility_status": "missing_exact_h1_entry_open"})
            continue
        entry_price = float(h1.open.iloc[j])
        if not np.isfinite(entry_price) or entry_price <= 0:
            counts["invalid_entry_open"] += 1
            continue
        records_out.append({
            "symbol": symbol, "week": week, "decision_ms": at,
            "decision_utc": pd.Timestamp(at, unit="ms", tz="UTC").isoformat(),
            "bar_open_ms": key, "entry_ms": at, "entry_price": entry_price,
            "signal_close": float(close[i]), "upper_extension": upper,
            "reference_stop": float(reference_stop), "fixed_stop_pct": float(stop_pct),
            "monday_high": anchor["high"], "monday_low": anchor["low"],
            "ema20": float(ema20[i]), "ema50": float(ema50[i]),
            "momentum30": float(prior[i]), "source_type": kind,
            "source_end_ms": end_ms, "delivery_ms": delivery.get(symbol),
            "entry_stop_distance": float(entry_price - reference_stop),
            "entry_stop_valid": bool(entry_price > reference_stop),
            "eligibility_status": "eligible",
        })
        counts["eligible"] += 1
    coverage = {
        "symbol": symbol, "source_type": kind, "source_end_ms": end_ms,
        "delivery_ms": delivery.get(symbol), "h1_path": str(source[0]),
        "h1_rows": len(h1), "h1_first_ms": int(t1[0]) if len(t1) else None,
        "h1_last_open_ms": int(t1[-1]) if len(t1) else None,
        "h1_gaps": int(np.count_nonzero(np.diff(t1) != HOUR)),
        "h12_rows": len(bars), "h12_first_ms": int(t12[0]) if len(t12) else None,
        "h12_last_open_ms": int(t12[-1]) if len(t12) else None,
        "h12_gaps": int(np.count_nonzero(np.diff(t12) != STEP)),
        "weekly_monday_anchors": len(anchors), "valid_monday_anchors": sum(v is not None for v in anchors.values()),
        "counts": dict(counts),
    }
    return records_out, coverage


def _rank_and_select(rows: list[dict], seeds=SEEDS) -> tuple[pd.DataFrame, pd.DataFrame]:
    frame = pd.DataFrame(rows)
    if frame.empty:
        return frame, frame
    pool = frame[frame.eligibility_status.eq("eligible")].copy()
    pool = pool.sort_values(["decision_ms", "momentum30", "symbol"],
                            ascending=[True, False, True]).reset_index(drop=True)
    pool["actual_rank"] = pool.groupby("decision_ms").cumcount() + 1
    pool["selected_actual"] = pool.actual_rank.le(SLOTS)
    selected: list[dict] = []
    for row in pool[pool.selected_actual].to_dict("records"):
        row.update({"event_id": f"actual:{row['decision_ms']}:{row['symbol']}",
                    "parent_id": f"actual:{row['decision_ms']}", "is_control": False,
                    "control_seed": None, "draw_order": None,
                    "entry_order": -float(row["momentum30"]), "rank": int(row["actual_rank"])})
        selected.append(row)
    pool["control_seeds"] = ""
    for seed in seeds:
        rng = np.random.default_rng(int(seed))
        for at, group in pool.groupby("decision_ms", sort=True):
            eligible = group.sort_values("symbol").index.to_numpy()
            n = min(SLOTS, len(eligible))
            if not n:
                continue
            chosen = rng.choice(eligible, size=n, replace=False)
            for order, idx in enumerate(chosen, 1):
                row = pool.loc[idx].to_dict()
                row.update({"event_id": f"control{seed}:{int(at)}:{row['symbol']}",
                            "parent_id": f"control{seed}:{int(at)}", "is_control": True,
                            "control_seed": int(seed), "draw_order": order,
                            "entry_order": order, "rank": None})
                selected.append(row)
                pool.at[idx, "control_seeds"] = (pool.at[idx, "control_seeds"] + ";" + str(seed)).strip(";")
    selected_frame = pd.DataFrame(selected)
    return pool, selected_frame


def _path_book(selected: pd.DataFrame, sources: dict, config: dict) -> tuple[pd.DataFrame, pd.DataFrame]:
    by_symbol: dict[str, list[dict]] = defaultdict(list)
    for row in selected.to_dict("records"):
        by_symbol[str(row["symbol"])].append(row)
    results = []
    unfilled = []
    batch_size = int(config["execution"]["event_batch_size"])
    for idx, (symbol, events) in enumerate(sorted(by_symbol.items()), 1):
        h1, bars = universe.frames(sources[symbol])
        q, clock, _ = engine._prepare_symbol(bars, h1)
        executable = []
        for event in events:
            stamp = int(event["entry_ms"])
            i = int(clock.searchsorted(stamp, side="left"))
            if i >= len(clock) or int(clock[i]) != stamp:
                raise ValueError(f"Decision H1 open disappeared for {symbol} at {stamp}")
            entry = float(q[i, 0])
            if not np.isclose(entry, float(event["entry_price"]), rtol=1e-10, atol=1e-12):
                raise ValueError(f"Entry price differs from frozen H1 open for {symbol} at {stamp}")
            stop, _ = fixed_stop(symbol, 1, float(event["upper_extension"]))
            if stop is None:
                raise ValueError(f"Missing V1.7 upper-reference fixed stop for {symbol} at {stamp}")
            if not np.isclose(float(stop), float(event["reference_stop"]), rtol=1e-10, atol=1e-12):
                raise ValueError(f"Stored stop differs from upper-reference fixed stop for {symbol} at {stamp}")
            if entry <= float(stop):
                unfilled.append({**event, "entry_status": "entry_at_or_below_upper_reference_stop"})
                continue
            risk = entry - float(stop)
            if risk <= 0 or not np.isfinite(risk):
                raise ValueError(f"Invalid original fixed stop for {symbol} at {stamp}")
            end_i, cap_reason = engine._safe_path_end(i, 1, q, clock)
            path_event = dict(event, source_row_number=0, side=1, entry_i=i,
                              entry_price=entry, stop_price=float(stop),
                              risk_distance=float(risk), risk_fraction=float(risk / entry),
                              end_i=end_i, path_upper_bound_reason=cap_reason)
            executable.append(path_event)
        if not executable:
            del h1, bars, q, clock
            gc.collect()
            continue
        rows, _ = engine._simulate_symbol(executable, q, clock, (0.0,), batch_size)
        if not rows or any(r["entry_status"] != "executed" for r in rows):
            raise ValueError(f"VectorBT failed to close all weekly candidates for {symbol}")
        path_frame = pd.DataFrame(rows)
        metadata = pd.DataFrame(executable).drop_duplicates("event_id").set_index("event_id")
        for column in metadata.columns:
            if column not in path_frame:
                path_frame[column] = path_frame.event_id.map(metadata[column])
            else:
                missing = path_frame[column].isna()
                if missing.any():
                    path_frame.loc[missing, column] = path_frame.loc[missing, "event_id"].map(metadata[column])
        results.extend(path_frame.to_dict("records"))
        print(f"native paths {idx}/{len(by_symbol)} {symbol}: {len(rows)}", flush=True)
        del h1, bars, q, clock
        gc.collect()
    paths = pd.DataFrame(results)
    if paths.empty:
        raise ValueError("No selected event has an entry above its upper-reference stop")
    if paths.event_id.duplicated().any():
        raise ValueError("Duplicate native event IDs")
    if not np.isclose(paths.net_pnl.sum(), paths.gross_pnl.sum()
                      - paths.entry_fee_amount.sum() - paths.exit_fee_amount.sum(),
                      rtol=1e-9, atol=1e-6):
        raise ValueError("Independent native path fees did not reconcile")
    return paths, pd.DataFrame(unfilled)


def _run_account(paths: pd.DataFrame, sources: dict, slots: int, scenario: str) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    @lru_cache(maxsize=None)
    def quotes(symbol: str):
        p, _, end = sources[symbol]
        data = pd.read_csv(p, usecols=["time", "open", "high", "low", "close"],
                           float_precision="round_trip")
        data = data[data.time < int(end)]
        if data.time.duplicated().any() or not data.time.is_monotonic_increasing:
            raise ValueError(f"Unsorted/duplicate source H1 quotes: {symbol}")
        return data.time.to_numpy(dtype=np.int64), data[["open", "high", "low", "close"]].to_numpy(dtype=float)

    data = wallet.prepare(paths, slots=slots, quote_loader=quotes)
    trades, equity = wallet.account(data)
    if not np.isclose(1000 + trades.cash_pnl.sum(), equity.nav.iat[-1], rtol=1e-10, atol=.005):
        raise ValueError(f"Funded {scenario} cash does not reconcile")
    periods = universe.breadth.pair.periods.PERIODS
    metrics = pd.DataFrame([{"scenario": scenario, "period": p, **wallet.summarize(trades, equity, *limits)}
                            for p, limits in periods.items()])
    summary = {
        "candidate_opportunities": len(paths), "admitted": int(len(data["selected"])),
        "funded": len(trades), "unquoted_holding_hours": int(data["gap_marks"]),
        **data["skips"],
        "data_exit_reasons": trades.exit_reason.value_counts().to_dict(),
        "administrative_delivery_tail_closes": int((trades.source_type.eq("retired")
            & trades.exit_reason.eq("series_tail_close")
            & trades.exit_bar_ms.ge(trades.source_end_ms - HOUR)).sum()) if len(trades) else 0,
        "cash_pnl_usdt": float(trades.cash_pnl.sum()),
        "reconciled_final_usdt": float(1000 + trades.cash_pnl.sum()),
        "hourly_final_usdt": float(equity.nav.iat[-1]),
    }
    return metrics, trades, {"equity": equity, "admission": summary}


def run(config: dict, *, symbols: list[str] | None = None, smoke: bool = False) -> dict:
    sources = universe.sources()
    delivery, kinds = _source_meta(sources)
    if symbols:
        sources = {s: sources[s] for s in symbols}
        kinds = {s: kinds[s] for s in symbols}
        delivery = {s: d for s, d in delivery.items() if s in sources}
    all_rows, coverage = [], []
    for idx, (symbol, source) in enumerate(sorted(sources.items()), 1):
        rows, cover = _eligible_for_symbol(symbol, source, delivery, kinds[symbol])
        all_rows.extend(rows)
        coverage.append(cover)
        if idx % 25 == 0 or idx == len(sources):
            print(f"scanned {idx}/{len(sources)} contracts; eligible candidate rows={sum(r['eligibility_status']=='eligible' for r in all_rows)}", flush=True)
    candidate_audit = pd.DataFrame(all_rows)
    pool, selected = _rank_and_select(all_rows)
    if smoke:
        if pool.empty:
            raise ValueError("Smoke scope produced no eligible Wednesday candidates")
        decisions = sorted(pool.decision_ms.unique())[:2]
        pool = pool[pool.decision_ms.isin(decisions)].copy()
        _, selected = _rank_and_select(pool.to_dict("records"))
        selected = selected[selected.is_control | selected.selected_actual].copy()
    paths, unfilled = _path_book(selected, sources, config)
    if smoke:
        actual = paths[~paths.is_control]
        m, trades, receipt = _run_account(actual, sources, SLOTS, "smoke_actual")
        if trades.empty or not np.isfinite(trades.cash_pnl).all():
            raise ValueError("Smoke actual account failed funded reconciliation")
        controls = paths[paths.is_control]
        seed = int(controls.control_seed.iloc[0]) if not controls.empty else None
        if seed is not None:
            cm, ct, cr = _run_account(controls[controls.control_seed.eq(seed)], sources, SLOTS, f"smoke_control_{seed}")
        print(json.dumps({"smoke_candidates": len(pool), "smoke_paths": len(paths),
                          "actual_funded": len(trades), "actual_final": receipt["admission"]["hourly_final_usdt"],
                          "control_seed": seed,
                          "unfilled_at_reference_stop": int(len(unfilled)),
                          "stop_anchor_check": bool(np.allclose(paths.stop_price, paths.reference_stop,
                                                               rtol=1e-10, atol=1e-12))},
                         indent=2), flush=True)
        return {"pool": pool, "selected": selected, "paths": paths, "unfilled": unfilled}

    EXP.mkdir(parents=True, exist_ok=True)
    pool.to_csv(EXP / "candidate_pool.csv.gz", index=False)
    candidate_audit.to_csv(EXP / "candidate_audit.csv.gz", index=False)
    selected.to_csv(EXP / "selected_events.csv.gz", index=False)
    unfilled.to_csv(EXP / "unfilled_events.csv.gz", index=False)
    pd.DataFrame(coverage).to_csv(EXP / "source_coverage.csv", index=False)
    paths.to_csv(EXP / "native_paths.csv.gz", index=False)
    metric_frames, results = [], {}
    groups = [("actual", paths[~paths.is_control])]
    groups.extend((f"control_seed{seed}", paths[paths.control_seed.eq(seed)]) for seed in SEEDS)
    for label, events in groups:
        metrics, funded, bundle = _run_account(events, sources, SLOTS, label)
        metric_frames.append(metrics)
        funded.to_csv(EXP / f"funded_{label}.csv.gz", index=False)
        bundle["equity"].to_csv(EXP / f"hourly_{label}.csv.gz", index_label="hour_end_utc")
        results[label] = {"metrics": metrics.drop(columns="scenario").set_index("period").to_dict("index"),
                          "account": bundle["admission"]}
        print(label, results[label]["metrics"].get("all"), flush=True)
        gc.collect()
    metrics_out = pd.concat(metric_frames, ignore_index=True)
    metrics_out.to_csv(EXP / "account_metrics.csv", index=False)
    controls_summary = {}
    for period in sorted(set(metrics_out.period)):
        values = [results[f"control_seed{s}"]["metrics"][period]["final_usdt"] for s in SEEDS]
        controls_summary[period] = {"mean_final_usdt": float(np.mean(values)),
                                    "median_final_usdt": float(np.median(values)),
                                    "min_final_usdt": float(np.min(values)),
                                    "max_final_usdt": float(np.max(values)),
                                    "seed_values": dict(zip(map(str, SEEDS), map(float, values)))}
    selected_actual = selected[~selected.is_control]
    summary = {
        "engine": "VectorBT native hourly event paths + shared cash-sharing account",
        "vectorbt_version": engine.vbt.__version__,
        "contracts": len(sources), "retired_contracts": len(delivery),
        "candidate_dates": int(pool.decision_ms.nunique()) if not pool.empty else 0,
        "eligible_candidate_rows": len(pool), "actual_top3_events": len(selected_actual),
        "selected_but_unfilled_at_reference_stop": len(unfilled),
        "control_events_per_seed": {str(s): int(selected[selected.is_control & selected.control_seed.eq(s)].shape[0]) for s in SEEDS},
        "source_coverage_summary": {"h1_gaps": int(pd.DataFrame(coverage).h1_gaps.sum()),
                                    "h12_gaps": int(pd.DataFrame(coverage).h12_gaps.sum()),
                                    "retired_contracts_with_delivery_time": len(delivery)},
        "results": results, "random_control_summary": controls_summary,
        "limitations": config["limitations"],
    }
    (EXP / "summary.json").write_text(json.dumps(summary, indent=2, allow_nan=False) + "\n")
    return summary


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_mutually_exclusive_group(required=True)
    actions.add_argument("--smoke", action="store_true")
    actions.add_argument("--run", action="store_true")
    parser.add_argument("--symbols", nargs="+", help="Small frozen subset for smoke only")
    args = parser.parse_args(argv)
    config = _config()
    if args.run and args.symbols:
        parser.error("--symbols is supported only with --smoke")
    run(config, symbols=args.symbols, smoke=args.smoke)


if __name__ == "__main__":
    main()
