#!/usr/bin/env python3
"""Causal development replay for the ETH 3m short-detector pilot.

The detector is evaluated only on bars before the sealed 2026-05-04 holdout.
Every replay image contains 200 completed bars ending at the decision bar.  A
bar is eligible only when that whole image is disjoint from every train/val
image listed in the pilot manifest.  The strict-OOS slice is the eligible tail
after the final train/val anchor; the larger gap replay is diagnostic because
its disjoint windows are interleaved with the training period.

Protocol is fixed for this experiment: conf=0.30, the existing A' two-bar tip
gate, 18-bar signal deduplication, entry at the next 3m open, exit after 60
bars (3h), and the owner-standard 0.20% round-trip reporting cost.  Matched
random shorts use the same untouched run and ATR quintile.  No threshold,
barrier, cost, holdout, model promotion, or production setting is changed.

Usage:
  MPLCONFIGDIR=/private/tmp/mpl-eth3m-backtest PYTHONPATH=. .venv/bin/python \
    scripts/backtest_eth3m_short_pilot_v1.py --device mps
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import shutil
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import pandas as pd

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))

from src.detection.data import add_mas, load_ohlcv_csv  # noqa: E402
from src.detection.render import render_chart  # noqa: E402
from src.judgment.candidates import MIN_GAP_BARS, WARMUP_BARS, add_indicators  # noqa: E402
from src.judgment.yolo_candidates import (  # noqa: E402
    DEFAULT_CONF,
    TIP_EDGE_BARS,
    WINDOW,
    load_yolo_model,
    right_edge_to_bar,
)

HOLDOUT_START = pd.Timestamp("2026-05-04", tz="UTC")
FUTURE_BARS = 60
BAR_MINUTES = 3
ROUND_TRIP_COST = 0.002
ATR_BUCKETS = 5
N_CONTROLS = 3
SEED = 20260729

DEFAULT_DATA = PROJECT / "data/kline_fetched/okx_ETH_USDT_SWAP_3m_57705.csv"
DEFAULT_MANIFEST = PROJECT / "datasets/eth_3m_short_pilot_v1/manifest.csv"
DEFAULT_WEIGHTS = (
    PROJECT
    / "runs/detect/runs/detect/eth3m_short_pilot_v1_mac_cold/weights/best.pt"
)
DEFAULT_OUT = PROJECT / "analysis/output/eth3m_short_pilot_v1_backtest"
SCAN_SCHEMA_VERSION = 2


def _utc(values: Any) -> pd.Series:
    return pd.to_datetime(values, utc=True)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def scan_fingerprint(
    *,
    data_path: Path,
    manifest_path: Path,
    weights_path: Path,
    eligible: pd.DataFrame,
    device: str,
    batch_size: int,
) -> dict[str, Any]:
    """Identity of every input/protocol field that can change predictions."""
    eligible_bytes = eligible[
        ["bar_i", "signal_time", "gap_run_id", "strict_oos"]
    ].to_csv(index=False).encode("utf-8")
    payload: dict[str, Any] = {
        "schema_version": SCAN_SCHEMA_VERSION,
        "data_sha256": _sha256(data_path),
        "manifest_sha256": _sha256(manifest_path),
        "weights_sha256": _sha256(weights_path),
        "eligible_sha256": hashlib.sha256(eligible_bytes).hexdigest(),
        "eligible_rows": int(len(eligible)),
        "window": WINDOW,
        "confidence": DEFAULT_CONF,
        "tip_edge_bars": TIP_EDGE_BARS,
        "device": str(device),
        "batch_size": int(batch_size),
    }
    payload["signature"] = hashlib.sha256(
        json.dumps(payload, sort_keys=True).encode("utf-8")
    ).hexdigest()
    return payload


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def prepare_frame(path: Path) -> pd.DataFrame:
    """Load OHLC and physically remove holdout before indicators are computed."""
    frame = load_ohlcv_csv(path)
    frame = frame.loc[frame["open_time"] < HOLDOUT_START].copy().reset_index(drop=True)
    if frame.empty or frame["open_time"].max() >= HOLDOUT_START:
        raise ValueError("development frame truncation failed")
    if frame["open_time"].duplicated().any():
        raise ValueError("duplicate OHLC timestamps")
    return add_indicators(add_mas(frame))


def load_manifest(path: Path) -> pd.DataFrame:
    manifest = pd.read_csv(path)
    required = {"sample_id", "split", "causal_start_time", "anchor_time"}
    missing = sorted(required - set(manifest.columns))
    if missing:
        raise ValueError(f"manifest missing columns: {missing}")
    manifest = manifest.copy()
    manifest["causal_start_time"] = _utc(manifest["causal_start_time"])
    manifest["anchor_time"] = _utc(manifest["anchor_time"])
    if (manifest["anchor_time"] >= HOLDOUT_START).any():
        raise ValueError("training manifest touches holdout")
    if not set(manifest["split"].astype(str)).issubset({"train", "val"}):
        raise ValueError("unexpected manifest split")
    return manifest


def build_eligible(frame: pd.DataFrame, manifest: pd.DataFrame) -> pd.DataFrame:
    """Return replay tips whose 200-bar pixels never appear in train or val.

    Uses only ``open_time`` and ``atr_pct`` at or before the signal bar.  Future
    OHLC is consulted later only for outcome labels.
    """
    times = _utc(frame["open_time"])
    if times.max() >= HOLDOUT_START:
        raise ValueError("frame must be physically pre-holdout")
    used = np.zeros(len(frame), dtype=bool)
    for row in manifest.itertuples(index=False):
        lo = int(times.searchsorted(pd.Timestamp(row.causal_start_time), side="left"))
        hi = int(times.searchsorted(pd.Timestamp(row.anchor_time), side="right"))
        if hi > lo:
            used[lo:hi] = True

    csum = np.concatenate([[0], np.cumsum(used, dtype=np.int64)])
    bars = np.arange(len(frame), dtype=int)
    overlap = np.full(len(frame), -1, dtype=int)
    valid_window = bars >= WINDOW - 1
    starts = bars[valid_window] - WINDOW + 1
    overlap[valid_window] = csum[bars[valid_window] + 1] - csum[starts]
    # Match the production scanner's full-series warmup as well as its image size.
    warm = WARMUP_BARS + WINDOW - 1
    eligible_mask = (
        (bars >= warm)
        & (overlap == 0)
        & (bars + FUTURE_BARS < len(frame))
    )
    eligible_i = bars[eligible_mask]
    if len(eligible_i) == 0:
        raise ValueError("no non-overlapping pre-holdout replay bars")

    new_run = np.r_[True, np.diff(eligible_i) != 1]
    run_id = np.cumsum(new_run).astype(int)
    last_anchor = pd.Timestamp(manifest["anchor_time"].max())
    out = pd.DataFrame(
        {
            "bar_i": eligible_i,
            "signal_time": times.iloc[eligible_i].astype(str).to_numpy(),
            "gap_run_id": run_id,
            "strict_oos": (times.iloc[eligible_i] > last_anchor).to_numpy(),
            "atr_pct": frame["atr_pct"].iloc[eligible_i].astype(float).to_numpy(),
            "train_pixel_overlap_bars": overlap[eligible_i],
        }
    )
    finite = np.isfinite(out["atr_pct"].to_numpy())
    if not finite.all():
        out = out.loc[finite].reset_index(drop=True)
    if (out["train_pixel_overlap_bars"] != 0).any():
        raise AssertionError("eligible replay window overlaps training pixels")
    return out


def dedupe_fire_indices(indices: Iterable[int], min_gap: int = MIN_GAP_BARS) -> list[int]:
    """Left-to-right absolute-bar deduplication, identical to current live semantics."""
    chosen: list[int] = []
    last = -(10**12)
    for value in sorted({int(v) for v in indices}):
        if value - last >= min_gap:
            chosen.append(value)
            last = value
    return chosen


def short_hold_outcome(frame: pd.DataFrame, signal_i: int) -> dict[str, Any]:
    """Three-hour short label; future data is used only inside this function."""
    entry_i = int(signal_i) + 1
    exit_i = int(signal_i) + FUTURE_BARS
    if entry_i >= len(frame) or exit_i >= len(frame):
        raise IndexError("incomplete 3h outcome")
    entry = float(frame["open"].iloc[entry_i])
    exit_close = float(frame["close"].iloc[exit_i])
    if not np.isfinite(entry) or entry <= 0 or not np.isfinite(exit_close):
        raise ValueError("invalid outcome price")
    future = frame.iloc[entry_i : exit_i + 1]
    gross = 1.0 - exit_close / entry
    return {
        "entry_i": entry_i,
        "exit_i": exit_i,
        "entry_time": str(frame["open_time"].iloc[entry_i]),
        "exit_time": str(frame["open_time"].iloc[exit_i]),
        "entry_open": entry,
        "exit_close": exit_close,
        "gross_ret_3h": float(gross),
        "net_ret_3h": float(gross - ROUND_TRIP_COST),
        "mfe_3h": float(1.0 - float(future["low"].min()) / entry),
        "mae_3h": float(float(future["high"].max()) / entry - 1.0),
    }


def _render_one(frame: pd.DataFrame, bar_i: int, path: Path) -> tuple[int, Any, Path]:
    sub = frame.iloc[bar_i - WINDOW + 1 : bar_i + 1]
    _, transform = render_chart(sub, out_path=path)
    return bar_i, transform, path


def _prediction_row(bar_i: int, transform: Any, result: Any) -> dict[str, Any]:
    boxes = result.boxes
    if boxes is None or len(boxes) == 0:
        return {
            "bar_i": bar_i,
            "n_boxes": 0,
            "n_tip_boxes": 0,
            "raw_fire": False,
            "max_conf_all": np.nan,
            "max_tip_conf": np.nan,
            "max_right_bar": -1,
            "mapped_box_bar_i": -1,
            "box_cx": np.nan,
            "box_cy": np.nan,
            "box_w": np.nan,
            "box_h": np.nan,
            "scan_error": "",
        }
    xywhn = boxes.xywhn.cpu().numpy()
    confs = boxes.conf.cpu().numpy() if boxes.conf is not None else np.ones(len(xywhn))
    enriched: list[tuple[float, int, np.ndarray]] = []
    for box, score in zip(xywhn, confs):
        right = right_edge_to_bar(float(box[0]), float(box[2]), transform, n_bars=WINDOW)
        enriched.append((float(score), int(right), box))
    tip = [row for row in enriched if row[1] >= WINDOW - TIP_EDGE_BARS]
    best = max(tip, key=lambda row: row[0]) if tip else max(enriched, key=lambda row: row[0])
    score, best_right_bar, box = best
    return {
        "bar_i": bar_i,
        "n_boxes": len(enriched),
        "n_tip_boxes": len(tip),
        "raw_fire": bool(tip),
        "max_conf_all": max(row[0] for row in enriched),
        "max_tip_conf": max((row[0] for row in tip), default=np.nan),
        "max_right_bar": max(row[1] for row in enriched),
        # Attribution only.  Entry stays after decision ``bar_i`` because this
        # mapped bar is known only once the current window-tip bar has closed.
        "mapped_box_bar_i": int(bar_i - ((WINDOW - 1) - best_right_bar)),
        "box_cx": float(box[0]),
        "box_cy": float(box[1]),
        "box_w": float(box[2]),
        "box_h": float(box[3]),
        "scan_error": "",
    }


def scan_eligible(
    frame: pd.DataFrame,
    eligible: pd.DataFrame,
    model: Any,
    out_path: Path,
    *,
    device: str,
    batch_size: int,
    render_workers: int,
    resume: bool,
    fingerprint: dict[str, Any],
) -> pd.DataFrame:
    """Render and infer every eligible causal tip, checkpointing each batch."""
    previous = pd.DataFrame()
    done: set[int] = set()
    meta_path = out_path.with_name("scan_meta.json")
    if resume and out_path.exists():
        if not meta_path.exists():
            raise RuntimeError(
                "scan checkpoint has no identity metadata; refuse unsafe resume. "
                "Use --no-resume to rebuild."
            )
        saved = json.loads(meta_path.read_text(encoding="utf-8"))
        if saved != fingerprint:
            raise RuntimeError("scan checkpoint fingerprint mismatch; refuse unsafe resume")
        previous = pd.read_csv(out_path)
        required = {
            "bar_i",
            "raw_fire",
            "max_right_bar",
            "mapped_box_bar_i",
            "scan_error",
        }
        if not required.issubset(previous.columns):
            raise RuntimeError("scan checkpoint schema mismatch")
        eligible_bars = set(eligible["bar_i"].astype(int))
        if not set(previous["bar_i"].astype(int)).issubset(eligible_bars):
            raise RuntimeError("scan checkpoint contains ineligible bars")
        done = set(previous["bar_i"].astype(int))
        print(f"resume: {len(done)} scan rows already present", flush=True)
    elif out_path.exists() and not resume:
        out_path.unlink()
    _write_json_atomic(meta_path, fingerprint)
    todo = [int(v) for v in eligible["bar_i"] if int(v) not in done]
    tmp_dir = out_path.parent / "_scan_tmp"
    if tmp_dir.exists():
        shutil.rmtree(tmp_dir)
    tmp_dir.mkdir(parents=True)
    rows: list[dict[str, Any]] = []
    started = time.monotonic()
    pool = ThreadPoolExecutor(max_workers=max(1, int(render_workers)))
    try:
        for offset in range(0, len(todo), batch_size):
            chunk = todo[offset : offset + batch_size]

            def render_job(bar_i: int) -> tuple[int, Any, Path]:
                return _render_one(frame, bar_i, tmp_dir / f"bar_{bar_i}.png")

            rendered: list[tuple[int, Any, Path]] = []
            errors: list[dict[str, Any]] = []
            futures = [(bar_i, pool.submit(render_job, bar_i)) for bar_i in chunk]
            for bar_i, future in futures:
                try:
                    rendered.append(future.result())
                except Exception as exc:  # noqa: BLE001
                    errors.append({"bar_i": bar_i, "raw_fire": False,
                                   "scan_error": f"render:{type(exc).__name__}:{exc}"})
            batch_rows: list[dict[str, Any]] = list(errors)
            if rendered:
                try:
                    results = model.predict(
                        [str(item[2]) for item in rendered],
                        conf=DEFAULT_CONF,
                        verbose=False,
                        device=device,
                    )
                    batch_rows.extend(
                        _prediction_row(bar_i, transform, result)
                        for (bar_i, transform, _), result in zip(rendered, results)
                    )
                    if len(results) != len(rendered):
                        raise RuntimeError("prediction result count mismatch")
                except Exception as exc:  # noqa: BLE001
                    batch_rows.extend(
                        {"bar_i": bar_i, "raw_fire": False,
                         "scan_error": f"predict:{type(exc).__name__}:{exc}"}
                        for bar_i, _, _ in rendered
                    )
            rows.extend(batch_rows)
            current = pd.concat([previous, pd.DataFrame(rows)], ignore_index=True, sort=False)
            current = current.sort_values("bar_i").drop_duplicates("bar_i", keep="last")
            tmp_csv = out_path.with_suffix(out_path.suffix + ".tmp")
            current.to_csv(tmp_csv, index=False)
            tmp_csv.replace(out_path)
            for _, _, path in rendered:
                path.unlink(missing_ok=True)
            elapsed = time.monotonic() - started
            raw = int(pd.to_numeric(current.get("raw_fire"), errors="coerce").fillna(0).sum())
            print(
                f"scan {len(current)}/{len(eligible)}  raw_fires={raw}  "
                f"elapsed={elapsed / 60:.1f}m",
                flush=True,
            )
    finally:
        pool.shutdown(wait=True)
        shutil.rmtree(tmp_dir, ignore_errors=True)
    out = pd.read_csv(out_path)
    if len(out) != len(eligible) or out["bar_i"].nunique() != len(eligible):
        raise RuntimeError(f"incomplete scan checkpoint: {len(out)}/{len(eligible)}")
    if out.get("scan_error", pd.Series(dtype=str)).fillna("").astype(str).str.len().gt(0).any():
        n = int(out["scan_error"].fillna("").astype(str).str.len().gt(0).sum())
        raise RuntimeError(f"{n} scan rows failed; checkpoint retained for diagnosis")
    return out.sort_values("bar_i").reset_index(drop=True)


def attach_outcomes(frame: pd.DataFrame, rows: pd.DataFrame) -> pd.DataFrame:
    outcomes = [short_hold_outcome(frame, int(i)) for i in rows["bar_i"]]
    return pd.concat([rows.reset_index(drop=True), pd.DataFrame(outcomes)], axis=1)


def assign_atr_buckets(eligible: pd.DataFrame) -> pd.DataFrame:
    out = eligible.copy()
    out["atr_bucket"] = pd.qcut(
        out["atr_pct"], ATR_BUCKETS, labels=False, duplicates="drop"
    ).astype(int)
    return out


def build_signals(frame: pd.DataFrame, scan: pd.DataFrame, eligible: pd.DataFrame) -> pd.DataFrame:
    base = eligible.merge(scan, on="bar_i", how="left", validate="one_to_one")
    base["raw_fire"] = base["raw_fire"].fillna(False).astype(bool)
    outputs: list[pd.DataFrame] = []
    for scope, mask in (
        ("gap_replay", pd.Series(True, index=base.index)),
        ("strict_oos", base["strict_oos"].astype(bool)),
    ):
        candidates = base.loc[mask & base["raw_fire"]].copy()
        keep = set(dedupe_fire_indices(candidates["bar_i"]))
        chosen = candidates[candidates["bar_i"].isin(keep)].copy()
        chosen.insert(0, "scope", scope)
        outputs.append(attach_outcomes(frame, chosen))
    if not outputs:
        return pd.DataFrame()
    return pd.concat(outputs, ignore_index=True, sort=False)


def matched_controls(
    frame: pd.DataFrame,
    eligible: pd.DataFrame,
    signals: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Match each signal inside its untouched run and causal ATR quintile."""
    rng = np.random.default_rng(SEED)
    universe = assign_atr_buckets(eligible)
    all_outcomes = attach_outcomes(frame, universe)
    control_rows: list[dict[str, Any]] = []
    paired_rows: list[dict[str, Any]] = []
    for scope, sig_scope in signals.groupby("scope", sort=False):
        pool_scope = all_outcomes if scope == "gap_replay" else all_outcomes[all_outcomes["strict_oos"]]
        signal_bars = set(sig_scope["bar_i"].astype(int))
        for signal in sig_scope.itertuples(index=False):
            exact = pool_scope[
                (pool_scope["gap_run_id"] == signal.gap_run_id)
                & (pool_scope["atr_bucket"] == signal.atr_bucket)
                & (~pool_scope["bar_i"].isin(signal_bars))
            ]
            match_tier = "same_run_atr_quintile"
            candidates = exact
            if candidates.empty:
                continue
            chosen_n = min(N_CONTROLS, len(candidates))
            selected = candidates.iloc[rng.choice(len(candidates), size=chosen_n, replace=False)]
            for rank, ctrl in enumerate(selected.itertuples(index=False), 1):
                control_rows.append(
                    {
                        "scope": scope,
                        "signal_bar_i": int(signal.bar_i),
                        "signal_time": signal.signal_time,
                        "signal_gap_run_id": int(signal.gap_run_id),
                        "signal_atr_bucket": int(signal.atr_bucket),
                        "control_rank": rank,
                        "control_bar_i": int(ctrl.bar_i),
                        "control_time": ctrl.signal_time,
                        "control_gap_run_id": int(ctrl.gap_run_id),
                        "control_atr_bucket": int(ctrl.atr_bucket),
                        "match_tier": match_tier,
                        "control_gross_ret_3h": float(ctrl.gross_ret_3h),
                        "control_net_ret_3h": float(ctrl.net_ret_3h),
                        "control_mfe_3h": float(ctrl.mfe_3h),
                        "control_mae_3h": float(ctrl.mae_3h),
                    }
                )
            ctrl_mean = float(selected["gross_ret_3h"].mean())
            paired_rows.append(
                {
                    "scope": scope,
                    "bar_i": int(signal.bar_i),
                    "control_n": chosen_n,
                    "control_gross_mean": ctrl_mean,
                    "control_net_mean": ctrl_mean - ROUND_TRIP_COST,
                    "paired_excess": float(signal.gross_ret_3h - ctrl_mean),
                    "match_tier": match_tier,
                }
            )
    controls = pd.DataFrame(control_rows)
    paired = pd.DataFrame(paired_rows)
    if paired.empty:
        enriched_signals = signals.copy()
        for column in (
            "control_n",
            "control_gross_mean",
            "control_net_mean",
            "paired_excess",
            "match_tier",
        ):
            enriched_signals[column] = np.nan
    else:
        enriched_signals = signals.merge(
            paired,
            on=["scope", "bar_i"],
            how="left",
            validate="one_to_one",
        )
    return controls, enriched_signals


def _profit_factor(values: pd.Series) -> float | None:
    x = pd.to_numeric(values, errors="coerce").dropna().to_numpy(dtype=float)
    loss = x[x < 0].sum()
    if len(x) == 0 or loss >= 0:
        return None
    return float(x[x > 0].sum() / -loss)


def _block_signflip_p(paired: pd.DataFrame) -> tuple[float | None, int]:
    if paired.empty:
        return None, 0
    work = paired.copy()
    work["day"] = _utc(work["signal_time"]).dt.strftime("%Y-%m-%d")
    blocks = work.groupby(["gap_run_id", "day"])["paired_excess"].mean().to_numpy(dtype=float)
    n = len(blocks)
    if n == 0:
        return None, 0
    observed = float(blocks.mean())
    if n <= 18:
        masks = np.arange(2**n, dtype=np.uint64)[:, None]
        bits = ((masks >> np.arange(n, dtype=np.uint64)) & 1).astype(float)
        null = ((bits * 2 - 1) * blocks).mean(axis=1)
    else:
        rng = np.random.default_rng(SEED + 1)
        signs = rng.choice((-1.0, 1.0), size=(50000, n))
        null = (signs * blocks).mean(axis=1)
    p = float((np.count_nonzero(null >= observed) + 1) / (len(null) + 1))
    return p, n


def summarize_scope(
    scope: str,
    eligible: pd.DataFrame,
    scan: pd.DataFrame,
    signals: pd.DataFrame,
    controls: pd.DataFrame,
) -> dict[str, Any]:
    elig = eligible if scope == "gap_replay" else eligible[eligible["strict_oos"]]
    joined = elig[["bar_i"]].merge(scan, on="bar_i", how="left")
    sig = signals[signals["scope"] == scope].copy()
    ctl = controls[controls["scope"] == scope] if not controls.empty else controls
    p, n_blocks = _block_signflip_p(sig.dropna(subset=["paired_excess"]))
    exposure_days = len(elig) * BAR_MINUTES / 1440.0
    paired = sig["paired_excess"].dropna().astype(float)
    se = float(paired.std(ddof=1) / math.sqrt(len(paired))) if len(paired) > 1 else None
    return {
        "eligible_bars": int(len(elig)),
        "eligible_bar_days": round(exposure_days, 4),
        "start": str(_utc(elig["signal_time"]).min()) if len(elig) else None,
        "end": str(_utc(elig["signal_time"]).max()) if len(elig) else None,
        "raw_fires": int(joined["raw_fire"].astype(bool).sum()),
        "raw_fire_rate": float(joined["raw_fire"].astype(bool).mean()) if len(joined) else None,
        "raw_fires_per_day": float(joined["raw_fire"].astype(bool).sum() / exposure_days)
        if exposure_days else None,
        "dedup_signals": int(len(sig)),
        "dedup_signals_per_day": float(len(sig) / exposure_days) if exposure_days else None,
        "gross_mean": float(sig["gross_ret_3h"].mean()) if len(sig) else None,
        "net_mean_at_20bp": float(sig["net_ret_3h"].mean()) if len(sig) else None,
        "net_win_rate_at_20bp": float((sig["net_ret_3h"] > 0).mean()) if len(sig) else None,
        "net_profit_factor_at_20bp": _profit_factor(sig["net_ret_3h"]),
        "median_net_at_20bp": float(sig["net_ret_3h"].median()) if len(sig) else None,
        "matched_control_rows": int(len(ctl)),
        # Signal-weighted: every signal contributes its own matched-control mean.
        "matched_control_net_mean_at_20bp": float(sig["control_net_mean"].mean())
        if sig["control_net_mean"].notna().any() else None,
        "matched_signal_coverage": float(sig["paired_excess"].notna().mean())
        if len(sig) else None,
        "paired_excess_mean": float(paired.mean()) if len(paired) else None,
        "paired_excess_se": se,
        "paired_excess_t": float(paired.mean() / se) if se and se > 0 else None,
        "block_signflip_p_one_sided": p,
        "signflip_blocks": n_blocks,
    }


def build_daily(
    eligible: pd.DataFrame,
    scan: pd.DataFrame,
    signals: pd.DataFrame,
) -> pd.DataFrame:
    base = eligible.merge(scan[["bar_i", "raw_fire"]], on="bar_i", validate="one_to_one")
    base["date"] = _utc(base["signal_time"]).dt.strftime("%Y-%m-%d")
    daily = base.groupby("date", as_index=False).agg(
        eligible_bars=("bar_i", "size"), raw_fires=("raw_fire", "sum")
    )
    daily["raw_fire_rate"] = daily["raw_fires"] / daily["eligible_bars"]
    for scope in ("gap_replay", "strict_oos"):
        sig = signals[signals["scope"] == scope].copy()
        if len(sig):
            sig["date"] = _utc(sig["signal_time"]).dt.strftime("%Y-%m-%d")
            agg = sig.groupby("date").agg(
                **{
                    f"{scope}_signals": ("bar_i", "size"),
                    f"{scope}_mean_net": ("net_ret_3h", "mean"),
                }
            )
            daily = daily.merge(agg, left_on="date", right_index=True, how="left")
        else:
            daily[f"{scope}_signals"] = 0
            daily[f"{scope}_mean_net"] = np.nan
    count_cols = [c for c in daily if c.endswith("_signals")]
    daily[count_cols] = daily[count_cols].fillna(0).astype(int)
    return daily


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=DEFAULT_DATA)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--weights", type=Path, default=DEFAULT_WEIGHTS)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--device", default="mps")
    parser.add_argument("--batch", type=int, default=32)
    parser.add_argument("--render-workers", type=int, default=6)
    parser.add_argument("--no-resume", action="store_true")
    args = parser.parse_args()
    if DEFAULT_CONF != 0.30 or TIP_EDGE_BARS != 2 or MIN_GAP_BARS != 18:
        raise RuntimeError("fixed protocol constants changed upstream; owner review required")
    for path in (args.data, args.manifest, args.weights):
        if not path.exists():
            raise FileNotFoundError(path)
    args.out.mkdir(parents=True, exist_ok=True)

    print("loading physically pre-holdout ETH 3m data...", flush=True)
    frame = prepare_frame(args.data)
    manifest = load_manifest(args.manifest)
    eligible = assign_atr_buckets(build_eligible(frame, manifest))
    eligible.to_csv(args.out / "eligible.csv", index=False)
    print(
        f"eligible={len(eligible)} strict_oos={int(eligible['strict_oos'].sum())} "
        f"runs={eligible['gap_run_id'].nunique()} holdout_untouched=True",
        flush=True,
    )

    model = load_yolo_model(args.weights)
    scan_path = args.out / "scan_rows.csv"
    fingerprint = scan_fingerprint(
        data_path=args.data,
        manifest_path=args.manifest,
        weights_path=args.weights,
        eligible=eligible,
        device=args.device,
        batch_size=max(1, args.batch),
    )
    scan = scan_eligible(
        frame,
        eligible,
        model,
        scan_path,
        device=args.device,
        batch_size=max(1, args.batch),
        render_workers=max(1, args.render_workers),
        resume=not args.no_resume,
        fingerprint=fingerprint,
    )
    signals = build_signals(frame, scan, eligible)
    controls, signals = matched_controls(frame, eligible, signals)
    signals.to_csv(args.out / "signals.csv", index=False)
    controls.to_csv(args.out / "matched_controls.csv", index=False)
    daily = build_daily(eligible, scan, signals)
    daily.to_csv(args.out / "daily.csv", index=False)

    summary = {
        "generated_at": pd.Timestamp.now(tz="UTC").isoformat(),
        "model": str(args.weights.relative_to(PROJECT)),
        "data": str(args.data.relative_to(PROJECT)),
        "manifest": str(args.manifest.relative_to(PROJECT)),
        "protocol": {
            "symbol": "ETH_USDT_SWAP",
            "bar": "3m",
            "window_bars": WINDOW,
            "confidence": DEFAULT_CONF,
            "tip_edge_bars": TIP_EDGE_BARS,
            "min_gap_bars": MIN_GAP_BARS,
            "entry": "next_bar_open",
            "exit": "close_after_60_bars_3h",
            "round_trip_cost": ROUND_TRIP_COST,
            "matched_control": "same untouched run x ATR quintile; up to 3 controls/signal",
        },
        "holdout_start": HOLDOUT_START.isoformat(),
        "holdout_touched": False,
        "training_images": int(len(manifest)),
        "last_training_anchor": pd.Timestamp(manifest["anchor_time"].max()).isoformat(),
        "replay": {
            scope: summarize_scope(scope, eligible, scan, signals, controls)
            for scope in ("strict_oos", "gap_replay")
        },
        "honesty": {
            "strict_oos_is_primary": True,
            "gap_replay_is_interleaved_diagnostic": True,
            "all_replay_windows_have_zero_training_pixel_overlap": True,
            "overlapping_3h_outcomes_make_signal_level_t_optimistic": True,
            "block_signflip_uses_untouched_run_x_utc_day_blocks": True,
            "no_threshold_tuning": True,
            "no_holdout": True,
            "no_promotion": True,
        },
    }
    (args.out / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(summary["replay"], ensure_ascii=False, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
