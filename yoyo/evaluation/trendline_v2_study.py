"""Three-stage TP/SL study for the trendline-break V2 strategy.

Stages exist so that a parameter search cannot quietly become its own
validation. The BTC BB x Stoch search (2026-09-16) selected on one segment and
collapsed on the next; so did the ETH one before it. Running the whole grid on
everything and reporting the best cell is exactly the procedure that produced
those two results.

    dev      score the whole grid on 2022-01-03..2025-01-01 only. Review-segment
             signals are dropped before the grid loop runs, so this stage cannot
             observe them even by accident.
    select   apply the pre-registered neighbourhood-median rule to dev and write
             selection.json, which is committed before the next stage runs.
    review   refuse to start unless selection.json is byte-identical to HEAD,
             then score the selected and reference cells on
             2025-01-01..2026-05-04.

The holdout (>= 2026-05-04, CLAUDE.md rule 1) is not read by any stage. The
frozen `release_eth_prefix.read_prefix` refuses an endpoint past it and stops
parsing at the first row it may not see, so that is enforced by the reader
rather than by this module remembering to ask.

The review segment is pre-holdout data this repository has looked at many times
in other studies. It is a time split, not a blind set, and the report says so.

Each symbol's CSV is read once per stage because the byte-gated reader costs
about twelve seconds per symbol; the loop is therefore symbol-outer, and every
timeframe and execution path for that symbol is finished before the next.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import time
from pathlib import Path

import numpy as np
import pandas as pd

from yoyo.data.release_eth_prefix import read_prefix, aggregate
from yoyo.evaluation.trendline_v2_signals import TrendlineParams, detect, pine_atr
from yoyo.evaluation.trendline_v2_strategy import (
    ExitGrid, barrier_outcomes, causal_volatility_bucket, draw_control_indices,
    month_block_signflip, serial_path, summarise,
)

ROOT = Path(__file__).resolve().parents[2]
EXP_REL = "experiments/active/exp-trendline-v2-tbsl-20260918-v1"
EXP = ROOT / EXP_REL
BUILDERS = [
    "yoyo/evaluation/trendline_v2_signals.py",
    "yoyo/evaluation/trendline_v2_strategy.py",
    "yoyo/evaluation/trendline_v2_study.py",
    "yoyo/evaluation/pine/trendline_break_strategy_v2.pine",
    "yoyo/data/release_eth_prefix.py",
    "yoyo/contracts/holdout.py",
    f"{EXP_REL}/config.json",
    f"{EXP_REL}/PROJECT_PLAN.md",
]


def save(path: Path, payload) -> None:
    def clean(value):
        if isinstance(value, dict):
            return {str(k): clean(v) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return [clean(v) for v in value]
        if isinstance(value, np.generic):
            return clean(value.item())
        if isinstance(value, np.ndarray):
            return clean(value.tolist())
        if isinstance(value, float) and not np.isfinite(value):
            return None
        if isinstance(value, pd.Timestamp):
            return value.isoformat()
        return value
    path.write_text(json.dumps(clean(payload), ensure_ascii=False, indent=2) + "\n")


def freeze_receipt(extra: tuple[str, ...] = ()) -> dict:
    """Refuse to read prices while any builder differs from what is committed.

    docs/learnings/artifacts-built-before-their-builder-landed.md: a product
    whose builder is not in git has no reproducibility claim, and comparing
    timestamps was the only thing that ever caught it.
    """
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    files = {}
    for name in (*BUILDERS, *extra):
        current = (ROOT / name).read_bytes()
        committed = subprocess.check_output(["git", "show", f"{head}:{name}"], cwd=ROOT)
        if committed != current:
            raise ValueError(f"uncommitted builder or config: {name}")
        files[name] = hashlib.sha256(current).hexdigest()
    return dict(source_commit=head, files=files, code_frozen_before_price_read=True,
                generated_at=pd.Timestamp.now(tz="UTC").isoformat())


def config() -> dict:
    return json.loads((EXP / "config.json").read_text())


def universe(cfg: dict) -> list[tuple[str, Path]]:
    out = []
    for path in sorted((ROOT / cfg["source_dir"]).glob("okx_*_15m_*.csv")):
        out.append((path.name.split("_15m_")[0][len("okx_"):], path))
    return out


def prepare(symbol: str, base: pd.DataFrame, minutes: int, cfg: dict) -> dict | None:
    """Bars, ATR, trendline signals and causal volatility buckets for one symbol."""
    frame = base if minutes == 15 else aggregate(base, minutes)[0]
    frame = frame.set_index("open_time")
    if len(frame) < cfg["min_bars"]:
        return None
    high, low, close, opens = (frame[c].to_numpy(float) for c in ("high", "low", "close", "open"))
    atr = pine_atr(high, low, close, 14)
    tick = float(np.median(close)) * 1e-7
    signals = detect(high, low, close, atr, tick, TrendlineParams(**cfg["trendline_params"]))
    bucket, bucket_defined = causal_volatility_bucket(frame)
    closes_at = frame.index + pd.Timedelta(minutes=minutes)
    return dict(symbol=symbol, minutes=minutes, opens=opens, high=high, low=low, close=close,
                atr=atr, tick=tick, signals=signals, bucket=bucket, bucket_defined=bucket_defined,
                closes_at=closes_at, month=closes_at.strftime("%Y-%m").to_numpy(),
                bars=len(frame), first=frame.index[0], last=frame.index[-1],
                pivot_ties=signals.pivot_ties, lines=len(signals.lines))


def segment_of(times: pd.DatetimeIndex, cfg: dict) -> np.ndarray:
    return np.where(times < pd.Timestamp(cfg["dev_end_exclusive"]), "dev", "review").astype(object)


def events_for(state: dict, cfg: dict, segment: str) -> dict:
    """Signal bars of one segment that have a full horizon and a usable ATR."""
    n = len(state["close"])
    hold = cfg["max_hold_bars"]
    signal = np.flatnonzero(state["signals"].break_event)
    horizon_ok = signal + 1 + hold <= n
    ok = horizon_ok & np.isfinite(state["atr"][signal]) & state["bucket_defined"][signal]
    dropped_horizon = int((~horizon_ok).sum())
    signal = signal[ok]
    signal = signal[segment_of(state["closes_at"][signal], cfg) == segment]
    return dict(signal_i=signal, times=state["closes_at"][signal],
                month=state["month"][signal], bucket=state["bucket"][signal],
                dropped_horizon=dropped_horizon)


def control_pool(state: dict, cfg: dict, segment: str) -> dict:
    """Bars a random entry could legally have used: same window, same horizon."""
    n = len(state["close"])
    bars = np.arange(n)
    ok = (bars + 1 + cfg["max_hold_bars"] <= n) & np.isfinite(state["atr"]) & state["bucket_defined"]
    bars = bars[ok]
    bars = bars[segment_of(state["closes_at"][bars], cfg) == segment]
    return dict(i=bars, month=state["month"][bars], bucket=state["bucket"][bars])


def score_symbol(state: dict, cfg: dict, segment: str, cells, path: str) -> dict | None:
    """Every grid cell, for one symbol's signals and their matched controls."""
    events = events_for(state, cfg, segment)
    if not len(events["signal_i"]):
        return None
    pool = control_pool(state, cfg, segment)
    owners, controls, unmatched = draw_control_indices(
        state["symbol"], events["times"], events["signal_i"], pool["i"], pool["month"],
        pool["bucket"], events["month"], events["bucket"], cfg["controls_per_signal"],
        cfg["control_seed"])

    price = (state["opens"], state["high"], state["low"], state["close"])
    hold = cfg["max_hold_bars"]
    n_events, n_cells = len(events["signal_i"]), len(cells)
    actual_net = np.empty((n_events, n_cells), dtype=np.float32)
    actual_gross = np.empty_like(actual_net)
    actual_pnl = np.empty_like(actual_net)
    actual_return = np.empty_like(actual_net)
    actual_cost_r = np.empty_like(actual_net)
    actual_lev = np.empty_like(actual_net)
    exit_i = np.empty((n_events, n_cells), dtype=np.int64)
    control_net = np.empty((len(owners), n_cells), dtype=np.float32) if len(owners) else None
    reasons = []
    for c, (sl, tp) in enumerate(cells):
        out = barrier_outcomes(*price, events["signal_i"], state["atr"][events["signal_i"]],
                               sl, tp, hold, path=path)
        actual_net[:, c] = out["net_r"]
        actual_gross[:, c] = out["gross_r"]
        actual_pnl[:, c] = out["net_pnl"]
        actual_return[:, c] = out["net_return"]
        actual_cost_r[:, c] = out["cost_r"]
        actual_lev[:, c] = out["notional_per_r"]
        exit_i[:, c] = out["exit_i"]
        reasons.append(out["reason"])
        if control_net is not None:
            cout = barrier_outcomes(*price, controls, state["atr"][controls], sl, tp, hold, path=path)
            control_net[:, c] = cout["net_r"]
    return dict(symbol=state["symbol"], signal_i=events["signal_i"], times=events["times"],
                month=events["month"], owners=owners, exit_i=exit_i, actual_net=actual_net,
                actual_gross=actual_gross, actual_pnl=actual_pnl, actual_return=actual_return,
                actual_cost_r=actual_cost_r, actual_lev=actual_lev, control_net=control_net,
                reasons=reasons, unmatched=unmatched, dropped_horizon=events["dropped_horizon"],
                tick_floor_bound=int((state["atr"][events["signal_i"]] < state["tick"]).sum()))


def collect_all(cfg: dict, segment: str, cells_by_tf: dict[int, list], paths: list[str]) -> dict:
    """Symbol-outer sweep: {minutes: {path: {"rows": [...], "skipped": {...}}}}."""
    out = {m: {p: {"rows": [], "skipped": {}, "bars": {}} for p in paths} for m in cells_by_tf}
    for symbol, source in universe(cfg):
        started = time.time()
        try:
            base, receipt = read_prefix(source, cfg["end_exclusive"])
        except ValueError as problem:
            for m in cells_by_tf:
                for p in paths:
                    out[m][p]["skipped"][symbol] = f"read: {problem}"
            print(f"  {symbol:22s} SKIPPED {problem}", flush=True)
            continue
        counts = []
        for minutes, cells in cells_by_tf.items():
            try:
                state = prepare(symbol, base, minutes, cfg)
            except ValueError as problem:
                for p in paths:
                    out[minutes][p]["skipped"][symbol] = f"prepare: {problem}"
                continue
            if state is None:
                for p in paths:
                    out[minutes][p]["skipped"][symbol] = "fewer bars than min_bars"
                continue
            for p in paths:
                out[minutes][p]["bars"][symbol] = dict(
                    bars=state["bars"], first=state["first"].isoformat(),
                    last=state["last"].isoformat(), lines=state["lines"],
                    pivot_ties=state["pivot_ties"],
                    breaks=int(state["signals"].break_event.sum()),
                    prefix_sha256=receipt["consumed_prefix_sha256"])
                scored = score_symbol(state, cfg, segment, cells, p)
                if scored is not None:
                    out[minutes][p]["rows"].append(scored)
                if p == paths[0]:
                    counts.append(f"{minutes}m={0 if scored is None else len(scored['signal_i'])}")
        print(f"  {symbol:22s} {' '.join(counts):32s} {time.time() - started:5.1f}s", flush=True)
    return out


def cell_serial(collected: dict, index: int) -> dict:
    """One position at a time per symbol, then the symbols pooled.

    Positions in different symbols may overlap, which is what a portfolio does;
    positions in the same symbol may not, which is what one account does.
    """
    parts = [serial_path(row["signal_i"], row["exit_i"][:, index],
                         row["actual_net"][:, index].astype(float),
                         row["actual_pnl"][:, index].astype(float))
             for row in collected["rows"]]
    chunks = [row["actual_net"][part["taken_positions"], index].astype(float)
              for row, part in zip(collected["rows"], parts) if part["n"]]
    values = np.concatenate(chunks) if chunks else np.array([])
    return dict(n=int(sum(p["n"] for p in parts)),
                skipped_overlapping=int(sum(p["skipped"] for p in parts)),
                net_r=float(sum(p["net_r"] for p in parts)),
                mean_net_r=float(values.mean()) if len(values) else None,
                win_rate=float((values > 0).mean()) if len(values) else None,
                worst_symbol_drawdown_r=float(max((p["max_drawdown_r"] for p in parts), default=0.0)),
                worst_symbol_loss_streak=int(max((p["max_loss_streak"] for p in parts), default=0)))


def cell_stats(collected: dict, cells, index: int, seed: int) -> dict:
    """Pooled statistics and the matched-control comparison for one grid cell."""
    if not collected["rows"]:
        return dict(sl_mult=cells[index][0], tp_mult=cells[index][1],
                    stats=dict(n=0), control=dict(paired_n=0), serial=dict(n=0))
    def stack(name):
        return np.concatenate([r[name][:, index] for r in collected["rows"]]).astype(float)
    reason = np.concatenate([r["reasons"][index] for r in collected["rows"]])
    stats = summarise(stack("actual_net"), stack("actual_gross"), reason,
                      stack("actual_return"), stack("actual_cost_r"), stack("actual_lev"))

    paired_actual, paired_control, paired_month = [], [], []
    for row in collected["rows"]:
        if row["control_net"] is None or not len(row["owners"]):
            continue
        frame = pd.DataFrame({"owner": row["owners"], "r": row["control_net"][:, index].astype(float)})
        means = frame.groupby("owner", sort=False)["r"].mean()
        lookup = dict(zip(means.index.to_numpy().tolist(), means.to_numpy().tolist()))
        for position, bar in enumerate(row["signal_i"]):
            if int(bar) in lookup:
                paired_actual.append(float(row["actual_net"][position, index]))
                paired_control.append(lookup[int(bar)])
                paired_month.append(row["month"][position])
    excess = (np.asarray(paired_actual) - np.asarray(paired_control)) if paired_actual else np.array([])
    control = dict(paired_n=int(len(excess)),
                   paired_actual_mean_net_r=float(np.mean(paired_actual)) if len(excess) else None,
                   random_mean_net_r=float(np.mean(paired_control)) if len(excess) else None,
                   excess_mean_net_r=float(np.mean(excess)) if len(excess) else None,
                   **month_block_signflip(np.asarray(paired_month), excess, seed))
    return dict(sl_mult=cells[index][0], tp_mult=cells[index][1], stats=stats, control=control,
                serial=cell_serial(collected, index))


def monthly_block(collected: dict, index: int) -> dict:
    """Per-UTC-month net R, so a result standing on one month is visible."""
    if not collected["rows"]:
        return {}
    month = np.concatenate([r["month"] for r in collected["rows"]])
    net = np.concatenate([r["actual_net"][:, index] for r in collected["rows"]]).astype(float)
    grouped = pd.DataFrame({"month": month, "net_r": net}).groupby("month")["net_r"]
    counts, sums = grouped.count().to_dict(), grouped.sum().to_dict()
    return {str(key): dict(n=int(counts[key]), net_r=float(sums[key])) for key in counts}


def stage_dev(cfg: dict) -> None:
    receipt = freeze_receipt()
    save(EXP / "code_receipt_dev.json", receipt)
    cells = ExitGrid(tuple(cfg["sl_mults"]), tuple(cfg["tp_mults"])).cells()
    cells_by_tf = {m: cells for m in cfg["minutes"]}
    collected = collect_all(cfg, "dev", cells_by_tf, [cfg["primary_path"]])
    results = {"config": cfg, "segment": "dev", "source_commit": receipt["source_commit"],
               "path": cfg["primary_path"], "cells": cells, "timeframes": {}}
    for minutes in cfg["minutes"]:
        block = collected[minutes][cfg["primary_path"]]
        results["timeframes"][str(minutes)] = dict(
            skipped=block["skipped"], per_symbol=block["bars"],
            symbols=[r["symbol"] for r in block["rows"]],
            signals=int(sum(len(r["signal_i"]) for r in block["rows"])),
            unmatched=int(sum(r["unmatched"] for r in block["rows"])),
            dropped_horizon=int(sum(r["dropped_horizon"] for r in block["rows"])),
            tick_floor_bound=int(sum(r["tick_floor_bound"] for r in block["rows"])),
            cells=[cell_stats(block, cells, index, cfg["seed"]) for index in range(len(cells))])
        print(f"[dev] {minutes}m signals={results['timeframes'][str(minutes)]['signals']}", flush=True)
    save(EXP / "dev_grid.json", results)
    print("saved dev_grid.json", flush=True)


def neighbourhood_median(cfg: dict, entries: list[dict]) -> dict:
    """The pre-registered selection rule, applied to the development segment.

    A cell scores as the median of its own and its four grid neighbours' mean
    net R, so an isolated peak surrounded by losses scores like its
    neighbourhood. That is the point: the 2026-09-16 BTC search took a
    diagnostic peak and it fell to -0.2843R per trade on the next segment.
    """
    sl_list, tp_list = list(cfg["sl_mults"]), list(cfg["tp_mults"])
    lookup = {(e["sl_mult"], e["tp_mult"]): e for e in entries}
    scored = []
    for a, sl in enumerate(sl_list):
        for b, tp in enumerate(tp_list):
            here = lookup[(sl, tp)]
            if here["stats"].get("n", 0) < cfg["min_signals_for_selection"]:
                continue
            # A cell nobody can fund is not a candidate. Risking 1% of equity in
            # a cell wanting 1000 notional per R is a 10x position; the R axis
            # would happily walk there because the fee shrinks with the stop.
            if here["stats"]["median_notional_per_r"] > cfg["max_notional_per_r"]:
                continue
            values = [here["stats"]["mean_net_r"]]
            for da, db in ((-1, 0), (1, 0), (0, -1), (0, 1)):
                if 0 <= a + da < len(sl_list) and 0 <= b + db < len(tp_list):
                    values.append(lookup[(sl_list[a + da], tp_list[b + db])]["stats"]["mean_net_r"])
            scored.append(dict(sl_mult=sl, tp_mult=tp, n=here["stats"]["n"],
                               own_mean_net_r=here["stats"]["mean_net_r"],
                               neighbourhood_median=float(np.median(values)),
                               neighbours=len(values) - 1))
    if not scored:
        return dict(selected=None, reason="no cell reached min_signals_for_selection", scored=[])
    best = max(scored, key=lambda x: (x["neighbourhood_median"], -x["sl_mult"], -x["tp_mult"]))
    return dict(selected={"sl_mult": best["sl_mult"], "tp_mult": best["tp_mult"]},
                rule="max neighbourhood median of mean net R; ties to smaller sl then smaller tp",
                best=best, scored=scored)


def stage_select(cfg: dict) -> None:
    dev = json.loads((EXP / "dev_grid.json").read_text())
    selection = {"generated_at": pd.Timestamp.now(tz="UTC").isoformat(),
                 "dev_grid_sha256": hashlib.sha256((EXP / "dev_grid.json").read_bytes()).hexdigest(),
                 "source_commit": dev["source_commit"],
                 "reference_cells": cfg["reference_cells"], "timeframes": {}}
    for minutes in cfg["minutes"]:
        selection["timeframes"][str(minutes)] = neighbourhood_median(
            cfg, dev["timeframes"][str(minutes)]["cells"])
        print(minutes, selection["timeframes"][str(minutes)]["selected"], flush=True)
    save(EXP / "selection.json", selection)
    print("saved selection.json", flush=True)


def stage_review(cfg: dict) -> None:
    receipt = freeze_receipt(extra=(f"{EXP_REL}/selection.json",))
    save(EXP / "code_receipt_review.json", receipt)
    selection = json.loads((EXP / "selection.json").read_text())
    paths = [cfg["primary_path"], *cfg["sensitivity_paths"]]
    cells_by_tf, chosen_by_tf = {}, {}
    for minutes in cfg["minutes"]:
        chosen = selection["timeframes"][str(minutes)]["selected"]
        cells = [tuple(c) for c in cfg["reference_cells"]]
        if chosen is not None and (chosen["sl_mult"], chosen["tp_mult"]) not in cells:
            cells.insert(0, (chosen["sl_mult"], chosen["tp_mult"]))
        cells_by_tf[minutes], chosen_by_tf[minutes] = cells, chosen
    collected = collect_all(cfg, "review", cells_by_tf, paths)
    results = {"config": cfg, "segment": "review", "source_commit": receipt["source_commit"],
               "selection_sha256": receipt["files"][f"{EXP_REL}/selection.json"], "timeframes": {}}
    for minutes in cfg["minutes"]:
        cells = cells_by_tf[minutes]
        block = {"selected": chosen_by_tf[minutes], "scored_cells": [list(c) for c in cells],
                 "paths": {}}
        for path in paths:
            data = collected[minutes][path]
            block["paths"][path] = dict(
                signals=int(sum(len(r["signal_i"]) for r in data["rows"])),
                symbols=[r["symbol"] for r in data["rows"]],
                unmatched=int(sum(r["unmatched"] for r in data["rows"])),
                dropped_horizon=int(sum(r["dropped_horizon"] for r in data["rows"])),
                monthly=[monthly_block(data, index) for index in range(len(cells))],
                cells=[cell_stats(data, cells, index, cfg["seed"]) for index in range(len(cells))])
        results["timeframes"][str(minutes)] = block
    save(EXP / "review_results.json", results)
    print("saved review_results.json", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="trendline V2 TP/SL study")
    parser.add_argument("stage", choices=("dev", "select", "review"))
    args = parser.parse_args()
    cfg = config()
    {"dev": stage_dev, "select": stage_select, "review": stage_review}[args.stage](cfg)


if __name__ == "__main__":
    main()
