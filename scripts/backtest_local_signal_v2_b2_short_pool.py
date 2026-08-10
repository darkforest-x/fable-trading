#!/usr/bin/env python3
"""Replay the frozen Local Signal V2 B2 detector on the immutable short-L2 pool.

This is a pre-holdout, candidate-pool economic replay, not a market-wide scan
and not a production backtest.  It answers whether B2's fixed-30 local visual
filter improves the already-frozen short candidate pool under the existing
next-open, TP5/SL2/72-bar outcomes.

Inputs and causal semantics:
  - L2 rows: ``data/p1/p1_short_l2_preholdout_aade2a334448d644.csv``.
  - B2 image uses bars [mapped_signal_i-29, mapped_signal_i]; no future bars.
  - B2 confidence is frozen at 0.35.
  - Primary edge gate accepts box right edge on tip/tip-1/tip-2.
  - Same-symbol signals are causally deduplicated with an 18-bar gap.
  - Rows within +/-72 bars of any B2 validation endpoint on the same symbol are
    excluded before inference to remove direct event/outcome-horizon overlap.
  - Evaluation ends before the 2026-05-04 holdout and never reads it.

The frozen L2 CSV already contains short-side gross and swap-taker net returns.
The report additionally applies the repository's conservative 0.20% round-trip
cost to gross return as a sensitivity; no economic parameter is tuned here.
"""
from __future__ import annotations

import argparse
import itertools
import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT = Path(__file__).resolve().parents[1]
DEFAULT_DATASET = PROJECT / "data/p1/p1_short_l2_preholdout_aade2a334448d644.csv"
DEFAULT_WEIGHTS = (
    PROJECT / "analysis/output/p1_local_signal_v2/training/B2/weights/best.pt"
)
DEFAULT_OUT_PREFIX = PROJECT / "analysis/output/p1_b2_short_l2_backtest_20260811"
DEFAULT_START = pd.Timestamp("2026-03-20T06:00:00Z")
HOLDOUT_START = pd.Timestamp("2026-05-04T00:00:00Z")
WINDOW_BARS = 30
CONFIDENCE = 0.35
PRIMARY_EDGE_BARS = 3
SENSITIVITY_EDGE_BARS = 2
OVERLAP_EXCLUSION_BARS = 72
MIN_GAP_BARS = 18
CONSERVATIVE_COST = 0.002
CONTROL_PER_TRADE = 8
SEED = 20260807


def validation_endpoints(dataset: Path) -> dict[str, list[int]]:
    """Return all B2 val positive/negative decision endpoints by symbol."""
    pos = json.loads((dataset / "w20_manifest.json").read_text())
    neg = json.loads((dataset / "w20_neg_manifest.json").read_text())
    endpoints: dict[str, list[int]] = {}
    for row in pos:
        if row["split"] == "val":
            endpoints.setdefault(row["symbol"], []).append(int(row["decision_bar"]))
    for row in neg:
        if row["split"] == "val":
            end = int(row["win_start"]) + int(row["win_len"]) - 1
            endpoints.setdefault(row["symbol"], []).append(end)
    return endpoints


def remove_validation_overlap(
    rows: pd.DataFrame,
    endpoints: dict[str, list[int]],
    *,
    tolerance: int = OVERLAP_EXCLUSION_BARS,
) -> tuple[pd.DataFrame, int]:
    """Exclude same-symbol rows close to any detector validation endpoint."""
    overlap = []
    for row in rows.itertuples():
        signal_i = int(row.mapped_signal_i)
        overlap.append(
            any(abs(signal_i - end) <= tolerance for end in endpoints.get(row.symbol, ()))
        )
    mask = np.asarray(overlap, dtype=bool)
    return rows.loc[~mask].copy(), int(mask.sum())


def causal_gap_dedup(
    rows: pd.DataFrame,
    *,
    fire_col: str,
    gap_bars: int = MIN_GAP_BARS,
) -> pd.DataFrame:
    """Keep the first fired signal after a same-symbol causal gap."""
    fired = rows.loc[rows[fire_col]].sort_values(
        ["signal_time", "symbol", "mapped_signal_i", "candidate_id"]
    )
    keep = []
    last_by_symbol: dict[str, int] = {}
    for row in fired.itertuples():
        signal_i = int(row.mapped_signal_i)
        last = last_by_symbol.get(row.symbol)
        accepted = last is None or signal_i - last >= gap_bars
        keep.append(accepted)
        if accepted:
            last_by_symbol[row.symbol] = signal_i
    return fired.loc[np.asarray(keep, dtype=bool)].copy()


def profit_factor(values: pd.Series) -> float | None:
    values = pd.to_numeric(values, errors="coerce").dropna()
    gains = float(values[values > 0].sum())
    losses = float(values[values < 0].sum())
    return gains / -losses if losses < 0 else None


def unit_sum_max_drawdown(rows: pd.DataFrame, return_col: str) -> float:
    """Max drawdown of time-ordered unit-return cumulative sum, not a portfolio."""
    ordered = rows.sort_values(["signal_time", "candidate_id"])
    equity = pd.to_numeric(ordered[return_col], errors="coerce").fillna(0).cumsum()
    with_zero = pd.concat([pd.Series([0.0]), equity.reset_index(drop=True)], ignore_index=True)
    drawdown = with_zero - with_zero.cummax()
    return float(drawdown.min())


def summarize_returns(rows: pd.DataFrame) -> dict:
    gross = pd.to_numeric(rows["gross_ret"], errors="coerce")
    taker = pd.to_numeric(rows["net_ret_swap_taker"], errors="coerce")
    conservative = gross - CONSERVATIVE_COST
    return {
        "n": int(len(rows)),
        "symbols": int(rows["symbol"].nunique()) if len(rows) else 0,
        "mean_gross_bp": float(gross.mean() * 1e4) if len(rows) else None,
        "median_gross_bp": float(gross.median() * 1e4) if len(rows) else None,
        "mean_net_taker_10bp": float(taker.mean() * 1e4) if len(rows) else None,
        "mean_net_conservative_20bp": (
            float(conservative.mean() * 1e4) if len(rows) else None
        ),
        "win_rate_net_taker": float((taker > 0).mean()) if len(rows) else None,
        "win_rate_net_conservative": (
            float((conservative > 0).mean()) if len(rows) else None
        ),
        "profit_factor_net_taker": profit_factor(taker),
        "profit_factor_net_conservative": profit_factor(conservative),
        "total_net_taker_units": float(taker.sum()) if len(rows) else None,
        "total_net_conservative_units": float(conservative.sum()) if len(rows) else None,
        "unit_sum_max_drawdown_taker": (
            unit_sum_max_drawdown(rows.assign(_net=taker), "_net") if len(rows) else None
        ),
        "unit_sum_max_drawdown_conservative": (
            unit_sum_max_drawdown(rows.assign(_net=conservative), "_net")
            if len(rows)
            else None
        ),
        "tp_before_sl_rate": (
            float(pd.to_numeric(rows["label_tp_before_sl"], errors="coerce").mean())
            if len(rows)
            else None
        ),
        "outcomes": (
            rows["exit_reason"].value_counts(dropna=False).to_dict() if len(rows) else {}
        ),
    }


def exact_week_signflip_p(matched: pd.DataFrame) -> tuple[float | None, int]:
    """Exact two-sided UTC-week block sign-flip p-value for matched lift."""
    if matched.empty:
        return None, 0
    weeks = list(pd.unique(matched["week"]))
    if len(weeks) > 20:
        raise ValueError("exact sign-flip is intentionally capped at 20 weeks")
    by_week = {week: matched.loc[matched["week"] == week, "excess"].to_numpy() for week in weeks}
    obs = abs(float(matched["excess"].mean()))
    ge = total = 0
    for signs in itertools.product((-1.0, 1.0), repeat=len(weeks)):
        perm = np.concatenate([by_week[week] * sign for week, sign in zip(weeks, signs)])
        ge += abs(float(perm.mean())) >= obs - 1e-15
        total += 1
    return ge / total, len(weeks)


def build_matched_controls(
    selected: pd.DataFrame,
    pool: pd.DataFrame,
    *,
    seed: int = SEED,
    n_per: int = CONTROL_PER_TRADE,
) -> tuple[pd.DataFrame, dict]:
    """Match same-symbol/month/ATR-quintile controls within the frozen pool."""
    rng = np.random.default_rng(seed)
    work = pool.copy()
    work["month"] = work["signal_time"].dt.strftime("%Y-%m")
    work["week"] = work["signal_time"].dt.strftime("%G-W%V")
    atr = pd.to_numeric(work["atr_pct"], errors="coerce")
    edges = np.unique(np.quantile(atr.dropna(), [0, 0.2, 0.4, 0.6, 0.8, 1.0]))
    edges[0], edges[-1] = -np.inf, np.inf
    work["atr_q"] = np.clip(
        np.searchsorted(edges, atr.to_numpy(), side="right") - 1,
        0,
        len(edges) - 2,
    )
    pool_by_cell = {
        key: frame.index.to_numpy()
        for key, frame in work.groupby(["symbol", "month", "atr_q"], sort=False)
    }
    pool_by_month = {
        key: frame.index.to_numpy()
        for key, frame in work.groupby(["symbol", "month"], sort=False)
    }
    rows = []
    misses = fallbacks = 0
    for row in selected.itertuples():
        month = row.signal_time.strftime("%Y-%m")
        q = int(
            np.clip(
                np.searchsorted(edges, float(row.atr_pct), side="right") - 1,
                0,
                len(edges) - 2,
            )
        )
        candidates = pool_by_cell.get((row.symbol, month, q), np.array([], dtype=int))
        candidates = np.asarray(
            [
                idx
                for idx in candidates
                if work.at[idx, "candidate_id"] != row.candidate_id
                and work.at[idx, "event_group_id"] != row.event_group_id
            ],
            dtype=int,
        )
        used_fallback = len(candidates) < 3
        if used_fallback:
            fallbacks += 1
            candidates = pool_by_month.get((row.symbol, month), np.array([], dtype=int))
            candidates = np.asarray(
                [
                    idx
                    for idx in candidates
                    if work.at[idx, "candidate_id"] != row.candidate_id
                    and work.at[idx, "event_group_id"] != row.event_group_id
                ],
                dtype=int,
            )
        if len(candidates) < 3:
            misses += 1
            continue
        picked = rng.choice(candidates, size=min(n_per, len(candidates)), replace=False)
        control = pd.to_numeric(work.loc[picked, "gross_ret"], errors="coerce") - CONSERVATIVE_COST
        selected_net = float(row.gross_ret) - CONSERVATIVE_COST
        ctrl_mean = float(control.mean())
        rows.append(
            {
                "candidate_id": row.candidate_id,
                "symbol": row.symbol,
                "signal_time": row.signal_time,
                "week": row.signal_time.strftime("%G-W%V"),
                "selected_net_20bp": selected_net,
                "control_mean_net_20bp": ctrl_mean,
                "excess": selected_net - ctrl_mean,
                "n_controls": int(len(control)),
                "fallback_same_symbol_month": used_fallback,
            }
        )
    matched = pd.DataFrame(rows)
    return matched, {
        "n_matched": int(len(matched)),
        "n_missed": int(misses),
        "n_month_fallback_attempts": int(fallbacks),
        "atr_quintile_edges": [
            float(atr.min()),
            *[float(value) for value in edges[1:-1]],
            float(atr.max()),
        ],
    }


def infer_b2(
    rows: pd.DataFrame,
    *,
    weights: Path,
    device: str,
    batch_size: int,
) -> pd.DataFrame:
    """Render causal 30-bar windows and attach frozen B2 edge-gate outputs."""
    from ultralytics import YOLO

    from scripts.build_local_signal_v2_p1_eval import resolve_series
    from yoyo.layers.l1_detection.data import add_mas
    from yoyo.layers.l1_detection.render import render_chart
    from yoyo.layers.l1_detection.candidates import right_edge_to_bar

    model = YOLO(str(weights))
    output: dict[str, dict] = {}
    batch = []

    def flush() -> None:
        if not batch:
            return
        images = [item[1] for item in batch]
        results = model.predict(
            images,
            conf=CONFIDENCE,
            iou=0.70,
            imgsz=960,
            device=device,
            verbose=False,
        )
        for (candidate_id, _image, tf), result in zip(batch, results):
            edge2 = edge3 = False
            best2 = best3 = 0.0
            n_boxes = 0
            if result.boxes is not None and len(result.boxes):
                xywhn = result.boxes.xywhn.cpu().numpy()
                conf = result.boxes.conf.cpu().numpy()
                n_boxes = len(conf)
                for box, score in zip(xywhn, conf):
                    bar = right_edge_to_bar(
                        float(box[0]), float(box[2]), tf, n_bars=WINDOW_BARS
                    )
                    if bar >= WINDOW_BARS - SENSITIVITY_EDGE_BARS:
                        edge2 = True
                        best2 = max(best2, float(score))
                    if bar >= WINDOW_BARS - PRIMARY_EDGE_BARS:
                        edge3 = True
                        best3 = max(best3, float(score))
            output[candidate_id] = {
                "b2_n_boxes": n_boxes,
                "b2_fire_edge2": edge2,
                "b2_fire_edge3": edge3,
                "b2_conf_edge2": best2 if edge2 else np.nan,
                "b2_conf_edge3": best3 if edge3 else np.nan,
            }
        batch.clear()

    total = len(rows)
    processed = skipped = 0
    for symbol, group in rows.groupby("symbol", sort=True):
        frame = resolve_series(symbol)
        if frame is None:
            skipped += len(group)
            continue
        enriched = add_mas(frame)
        times = pd.to_datetime(enriched["open_time"], utc=True)
        for row in group.itertuples():
            signal_i = int(row.mapped_signal_i)
            start_i = signal_i - WINDOW_BARS + 1
            if start_i < 0 or signal_i >= len(enriched):
                skipped += 1
                continue
            actual_time = pd.Timestamp(times.iloc[signal_i])
            if actual_time != row.signal_time:
                raise RuntimeError(
                    f"signal index/time mismatch {symbol} i={signal_i}: "
                    f"{actual_time} != {row.signal_time}"
                )
            image, tf = render_chart(
                enriched.iloc[start_i : signal_i + 1].reset_index(drop=True),
                out_path=None,
            )
            batch.append((row.candidate_id, image, tf))
            processed += 1
            if len(batch) >= batch_size:
                flush()
                if processed % 256 < batch_size:
                    print(f"predict {processed}/{total} skipped={skipped}", flush=True)
    flush()
    print(f"predict {processed}/{total} skipped={skipped}", flush=True)
    pred = pd.DataFrame.from_dict(output, orient="index")
    pred.index.name = "candidate_id"
    return rows.merge(pred.reset_index(), on="candidate_id", how="left", validate="one_to_one")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--weights", type=Path, default=DEFAULT_WEIGHTS)
    parser.add_argument("--out-prefix", type=Path, default=DEFAULT_OUT_PREFIX)
    parser.add_argument("--start", default=str(DEFAULT_START))
    parser.add_argument("--device", default="mps")
    parser.add_argument("--batch", type=int, default=12)
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args()

    start = pd.Timestamp(args.start)
    if start.tzinfo is None:
        start = start.tz_localize("UTC")
    else:
        start = start.tz_convert("UTC")
    rows = pd.read_csv(args.dataset, parse_dates=["signal_time"])
    rows = rows.loc[
        (rows["signal_time"] >= start)
        & (rows["signal_time"] < HOLDOUT_START)
        & (rows["side"] == "short")
    ].copy()
    if rows.empty:
        raise SystemExit("no eligible pre-holdout short rows")
    if pd.to_datetime(rows["interval_end"], utc=True).max() >= HOLDOUT_START:
        raise SystemExit("refusing rows whose outcome interval touches holdout")
    source_n = len(rows)
    endpoints = validation_endpoints(PROJECT / "datasets/local_signal_v2_p1_b2_w30")
    rows, overlap_n = remove_validation_overlap(rows, endpoints)
    rows = rows.sort_values(["signal_time", "candidate_id"]).reset_index(drop=True)
    if args.limit:
        rows = rows.head(args.limit).copy()
    scored = infer_b2(rows, weights=args.weights, device=args.device, batch_size=args.batch)
    if scored["b2_fire_edge3"].isna().any():
        missing = int(scored["b2_fire_edge3"].isna().sum())
        raise RuntimeError(f"missing predictions for {missing} rows")
    primary = causal_gap_dedup(scored, fire_col="b2_fire_edge3")
    edge2 = causal_gap_dedup(scored, fire_col="b2_fire_edge2")
    matched, control_meta = build_matched_controls(primary, scored)
    perm_p, n_weeks = exact_week_signflip_p(matched)

    scored["net_ret_conservative_20bp"] = scored["gross_ret"] - CONSERVATIVE_COST
    primary["net_ret_conservative_20bp"] = primary["gross_ret"] - CONSERVATIVE_COST
    edge2["net_ret_conservative_20bp"] = edge2["gross_ret"] - CONSERVATIVE_COST
    monthly = []
    for month, frame in primary.groupby(primary["signal_time"].dt.strftime("%Y-%m")):
        monthly.append({"month": month, **summarize_returns(frame)})

    confidence = []
    if len(primary) >= 8:
        primary = primary.copy()
        quartile_codes = pd.qcut(
            primary["b2_conf_edge3"], 4, labels=False, duplicates="drop"
        )
        primary["confidence_quartile"] = quartile_codes.map(
            lambda value: f"Q{int(value) + 1}" if pd.notna(value) else None
        )
        for bucket, frame in primary.groupby("confidence_quartile", observed=True):
            confidence.append(
                {
                    "quartile": str(bucket),
                    "conf_min": float(frame["b2_conf_edge3"].min()),
                    "conf_max": float(frame["b2_conf_edge3"].max()),
                    **summarize_returns(frame),
                }
            )

    confidence_cutoff = float(primary["b2_conf_edge3"].quantile(0.90))
    confidence_top_decile = primary.loc[
        primary["b2_conf_edge3"] >= confidence_cutoff
    ].copy()
    top_decile_ids = set(confidence_top_decile["candidate_id"])
    matched_top_decile = matched.loc[matched["candidate_id"].isin(top_decile_ids)].copy()
    top_decile_p, top_decile_weeks = exact_week_signflip_p(matched_top_decile)

    summary = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "scope": "preholdout_short_l2_candidate_pool_replay",
        "decision_use": "economic feasibility of B2 as a visual filter before P2; not production",
        "dataset": str(args.dataset.relative_to(PROJECT)),
        "weights": str(args.weights.relative_to(PROJECT)),
        "weights_sha256": __import__("hashlib").sha256(args.weights.read_bytes()).hexdigest(),
        "source_rows_in_time_range": source_n,
        "validation_overlap_excluded": overlap_n,
        "eligible_rows": int(len(scored)),
        "symbols": int(scored["symbol"].nunique()),
        "time_range": {
            "min_signal": str(scored["signal_time"].min()),
            "max_signal": str(scored["signal_time"].max()),
            "max_outcome_end": str(pd.to_datetime(scored["interval_end"], utc=True).max()),
            "holdout_start": str(HOLDOUT_START),
            "holdout_read": False,
        },
        "protocol": {
            "window_bars": WINDOW_BARS,
            "confidence": CONFIDENCE,
            "primary_edge_bars": PRIMARY_EDGE_BARS,
            "sensitivity_edge_bars": SENSITIVITY_EDGE_BARS,
            "overlap_exclusion_bars": OVERLAP_EXCLUSION_BARS,
            "same_symbol_min_gap_bars": MIN_GAP_BARS,
            "side": "short (frozen L2 pool)",
            "entry": "next_bar_open (frozen L2 outcome)",
            "barriers": "TP5 ATR / SL2 ATR / 72 bars / conservative same-bar SL",
            "costs": {
                "swap_taker_round_trip": 0.001,
                "conservative_report_round_trip": CONSERVATIVE_COST,
            },
        },
        "unfiltered_pool": summarize_returns(scored),
        "raw_fire": {
            "edge3_n": int(scored["b2_fire_edge3"].sum()),
            "edge3_rate": float(scored["b2_fire_edge3"].mean()),
            "edge2_n": int(scored["b2_fire_edge2"].sum()),
            "edge2_rate": float(scored["b2_fire_edge2"].mean()),
        },
        "selected_primary_edge3_dedup": summarize_returns(primary),
        "selected_sensitivity_edge2_dedup": summarize_returns(edge2),
        "matched_control": {
            **control_meta,
            "mean_selected_net_20bp": (
                float(matched["selected_net_20bp"].mean()) if len(matched) else None
            ),
            "mean_control_net_20bp": (
                float(matched["control_mean_net_20bp"].mean()) if len(matched) else None
            ),
            "mean_excess_bp": float(matched["excess"].mean() * 1e4) if len(matched) else None,
            "exact_week_signflip_p": perm_p,
            "n_utc_week_blocks": n_weeks,
        },
        "detector_confidence_top_decile": {
            "cutoff": confidence_cutoff,
            **summarize_returns(confidence_top_decile),
        },
        "matched_control_top_decile": {
            "n_matched": int(len(matched_top_decile)),
            "mean_selected_net_20bp": (
                float(matched_top_decile["selected_net_20bp"].mean())
                if len(matched_top_decile)
                else None
            ),
            "mean_control_net_20bp": (
                float(matched_top_decile["control_mean_net_20bp"].mean())
                if len(matched_top_decile)
                else None
            ),
            "mean_excess_bp": (
                float(matched_top_decile["excess"].mean() * 1e4)
                if len(matched_top_decile)
                else None
            ),
            "exact_week_signflip_p": top_decile_p,
            "n_utc_week_blocks": top_decile_weeks,
        },
        "monthly": monthly,
        "confidence_quartiles": confidence,
        "limit": args.limit,
    }
    out = args.out_prefix
    out.parent.mkdir(parents=True, exist_ok=True)
    scored.to_csv(out.with_name(out.name + "_rows.csv"), index=False)
    primary.to_csv(out.with_name(out.name + "_selected.csv"), index=False)
    matched.to_csv(out.with_name(out.name + "_matched.csv"), index=False)
    out.with_suffix(".json").write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n")
    print(json.dumps(summary, indent=2, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
