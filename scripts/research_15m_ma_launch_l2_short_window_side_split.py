#!/usr/bin/env python3
"""Train and audit LONG/SHORT L2 regressors on exact L1 short windows.

This owner-corrected experiment starts from the frozen 3,779 pre-holdout L1
episodes.  For every row it recreates the original 1280x742 W18/W19 image,
requires the pixel hash to match, and derives model features only from the OHLC
and SMA/EMA 20/60/120 values visible in that image plus the current raw YOLO
box/confidence.  The future TP5/SL2/72 outcome is a label only.

The source chronological splits, outcome, cost, L1 candidates, L1 threshold and
LightGBM parameters remain fixed.  Dependency blocks are recomputed from the
actual W18/W19 input exposure plus the 72-bar label exposure; the existing
60-hour purges are retained as a conservative superset.  LONG and SHORT are
always trained separately.  No holdout, network, promotion, deployment,
forward state, Telegram, or order path is reachable from this script.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import platform
import subprocess
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import cv2
import lightgbm as lgb
import numpy as np
import pandas as pd

from scripts.research_15m_ma_launch_l2_global_context import (
    BAR_DELTA,
    L2_DETERMINISTIC_PARAMS,
    causal_atr_quintile,
    matched_control_metrics,
    outcome_permutation_pvalue,
    safe_metrics,
    selected_metrics,
)
from scripts.retrain_15m_ma_launch_l2_by_side import empirical_percentile
from yoyo.layers.l2_judgment.short_window_features import (
    ALLOWED_WINDOW_BARS,
    SHORT_WINDOW_FEATURE_COLUMNS,
    extract_short_window_features,
)


ROOT = Path(__file__).resolve().parents[1]
EXPERIMENT_ID = "exp-15m-ma-launch-l2-short-window-side-split-v1"
EXPERIMENT_DIR = ROOT / "experiments" / "active" / EXPERIMENT_ID
PREREG_PATH = EXPERIMENT_DIR / "preregistration.json"
RESULTS_DIR = EXPERIMENT_DIR / "results"
OUTPUT_DIR = ROOT / "analysis" / "output" / "ma_launch_l2_short_window_side_split_v1"
SIDES = ("long", "short")
LEARNING_SPLITS = ("train", "tune", "final_validation")
SEED = 42


class ShortWindowL2Error(RuntimeError):
    """Fail closed when the short-window experiment contract drifts."""


def utc(value: object) -> pd.Timestamp:
    stamp = pd.Timestamp(value)
    return stamp.tz_convert("UTC") if stamp.tzinfo else stamp.tz_localize("UTC")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def pixel_sha256(image: np.ndarray) -> str:
    return hashlib.sha256(np.ascontiguousarray(image).tobytes()).hexdigest()


def repo_path(value: object) -> Path:
    path = (ROOT / str(value).replace("\\", "/")).resolve()
    try:
        path.relative_to(ROOT.resolve())
    except ValueError as exc:
        raise ShortWindowL2Error(f"path escapes repository: {value}") from exc
    return path


def repo_relative(path: Path) -> str:
    return path.resolve().relative_to(ROOT.resolve()).as_posix()


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    building = path.with_suffix(path.suffix + ".building")
    building.write_text(
        json.dumps(dict(payload), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    building.replace(path)


def git_head() -> str:
    return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()


def runtime_versions() -> dict[str, Any]:
    packages = ("lightgbm", "numpy", "pandas", "scikit-learn", "scipy")
    return {
        "python": platform.python_version(),
        "platform": platform.platform(),
        "packages": {name: importlib.metadata.version(name) for name in packages},
    }


def verify_file(path: Path, expected_sha256: str, label: str) -> None:
    if not path.is_file():
        raise ShortWindowL2Error(f"missing {label}: {path}")
    actual = sha256_file(path)
    if actual != str(expected_sha256):
        raise ShortWindowL2Error(f"{label} SHA drifted: {actual} != {expected_sha256}")


def bool_series(series: pd.Series) -> pd.Series:
    if pd.api.types.is_bool_dtype(series):
        return series.astype(bool)
    normalized = series.astype(str).str.strip().str.lower()
    if not normalized.isin(("true", "false")).all():
        raise ShortWindowL2Error("boolean column contains values other than true/false")
    return normalized.map({"true": True, "false": False}).astype(bool)


def load_preregistration(path: Path = PREREG_PATH) -> dict[str, Any]:
    payload = read_json(path)
    if payload.get("experiment_id") != EXPERIMENT_ID:
        raise ShortWindowL2Error("experiment_id mismatch")
    auth = payload["owner_authorization"]
    if auth.get("training_authorized") is not True:
        raise ShortWindowL2Error("this short-window training run is not owner-authorized")
    if auth.get("holdout_read_authorized") is not False:
        raise ShortWindowL2Error("holdout read must remain disabled")
    contract = payload["short_window_contract"]
    if tuple(map(int, contract["window_lengths"])) != ALLOWED_WINDOW_BARS:
        raise ShortWindowL2Error("window length contract drifted")
    if int(contract["feature_count"]) != len(SHORT_WINDOW_FEATURE_COLUMNS):
        raise ShortWindowL2Error("short-window feature count drifted")
    feature_contract_hash = hashlib.sha256(
        "\n".join(SHORT_WINDOW_FEATURE_COLUMNS).encode()
    ).hexdigest()
    if contract["feature_columns_sha256"] != feature_contract_hash:
        raise ShortWindowL2Error("short-window feature order drifted")
    if int(contract["image_width"]) != 1280 or int(contract["image_height"]) != 742:
        raise ShortWindowL2Error("native L1 image geometry drifted")
    if payload["model"]["deterministic_params"] != L2_DETERMINISTIC_PARAMS:
        raise ShortWindowL2Error("deterministic LightGBM parameters drifted")
    observed = runtime_versions()["packages"]
    for package, expected in payload["model"]["runtime_contract"].items():
        if observed[package] != str(expected):
            raise ShortWindowL2Error(
                f"{package} version drifted: {observed[package]} != {expected}"
            )
    if any(payload["safety"].values()):
        raise ShortWindowL2Error("one or more safety switches drifted true")
    holdout = utc(payload["source"]["holdout_start"])
    if utc(payload["source"]["maximum_available_at_exclusive"]) > holdout:
        raise ShortWindowL2Error("source contract reaches holdout")
    return payload


def verify_declared_inputs(prereg: Mapping[str, Any]) -> dict[str, dict[str, str]]:
    declared = prereg["immutable_inputs"]
    verified: dict[str, dict[str, str]] = {}
    for label, spec in declared.items():
        path = repo_path(spec["path"])
        verify_file(path, str(spec["sha256"]), label)
        verified[label] = {"path": repo_relative(path), "sha256": sha256_file(path)}
    return verified


def validate_candidate_ledger(frame: pd.DataFrame, prereg: Mapping[str, Any]) -> pd.DataFrame:
    required = {
        "candidate_id",
        "symbol",
        "side",
        "class_id",
        "confidence",
        "available_at",
        "window_len",
        "window_start_i",
        "window_end_i",
        "window_end_time",
        "core_start_i",
        "core_end_i",
        "confirmation_bars",
        "prediction_cx_norm",
        "prediction_cy_norm",
        "prediction_w_norm",
        "prediction_h_norm",
        "input_pixel_sha256",
    }
    missing = sorted(required - set(frame.columns))
    if missing:
        raise ShortWindowL2Error(f"candidate ledger missing columns: {missing}")
    expected_rows = int(prereg["source"]["candidate_rows"])
    if len(frame) != expected_rows:
        raise ShortWindowL2Error(f"candidate rows drifted: {len(frame)} != {expected_rows}")
    if frame["candidate_id"].duplicated().any():
        raise ShortWindowL2Error("candidate_id is not unique")
    if set(frame["side"].astype(str)) != set(SIDES):
        raise ShortWindowL2Error("candidate side values drifted")
    if set(frame["window_len"].astype(int)) != set(ALLOWED_WINDOW_BARS):
        raise ShortWindowL2Error("candidate ledger contains a window other than 18/19 bars")
    if (pd.to_datetime(frame["window_end_time"], utc=True) + BAR_DELTA != pd.to_datetime(
        frame["available_at"], utc=True
    )).any():
        raise ShortWindowL2Error("candidate decision clock is not final-bar close")
    if (pd.to_datetime(frame["available_at"], utc=True) >= utc(prereg["source"]["holdout_start"])).any():
        raise ShortWindowL2Error("candidate ledger contains holdout rows")
    if not (frame["input_width"].astype(int) == 1280).all() or not (
        frame["input_height"].astype(int) == 742
    ).all():
        raise ShortWindowL2Error("candidate image geometry drifted")
    return frame.copy()


def build_side_homogeneous_episodes(candidates: pd.DataFrame) -> pd.DataFrame:
    """Cluster frozen candidates independently by symbol and predicted side."""

    episodes: list[dict[str, Any]] = []
    for (symbol, side), group in candidates.groupby(["symbol", "side"], sort=True):
        ordered = sorted(
            group.to_dict("records"),
            key=lambda row: (
                int(row["core_start_i"]),
                int(row["window_end_i"]),
                -float(row["confidence"]),
                int(row["window_len"]),
            ),
        )
        clusters: list[list[dict[str, Any]]] = []
        active: list[dict[str, Any]] = []
        active_end: int | None = None
        for row in ordered:
            start_i, end_i = int(row["core_start_i"]), int(row["window_end_i"])
            if active and active_end is not None and start_i > active_end:
                clusters.append(active)
                active, active_end = [], None
            active.append(row)
            active_end = end_i if active_end is None else max(active_end, end_i)
        if active:
            clusters.append(active)
        for sequence, cluster in enumerate(clusters, 1):
            representative = min(
                cluster,
                key=lambda row: (
                    int(row["window_end_i"]),
                    -float(row["confidence"]),
                    int(row["core_end_i"]),
                    int(row["window_len"]),
                ),
            )
            stamp = utc(representative["available_at"])
            episode_id = (
                f"short_l2_{str(symbol).replace('_USDT_SWAP', '')}_{str(side)}_"
                f"{stamp:%Y%m%dT%H%M}_{sequence:04d}"
            )
            episodes.append(
                {
                    **representative,
                    "source_cross_side_episode_id": str(representative["episode_id"]),
                    "episode_id": episode_id,
                    "representative_candidate_id": str(representative["candidate_id"]),
                    "side_candidate_count": len(cluster),
                    "side_episode_sequence": sequence,
                    "side_episode_interval_start_i": min(
                        int(row["core_start_i"]) for row in cluster
                    ),
                    "side_episode_interval_end_i": max(
                        int(row["window_end_i"]) for row in cluster
                    ),
                    "representative_rule": "per_side_earliest_window_end_then_highest_confidence",
                }
            )
    out = pd.DataFrame(episodes).sort_values(["available_at", "symbol", "side", "episode_id"])
    if out["episode_id"].duplicated().any() or out["representative_candidate_id"].duplicated().any():
        raise ShortWindowL2Error("side episode identity is not unique")
    if set(out["side"].astype(str)) != set(SIDES):
        raise ShortWindowL2Error("side episode builder lost one direction")
    return out


def snapshot_specs(prereg: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    receipt_path = repo_path(prereg["source"]["snapshot_receipt_path"])
    receipt = read_json(receipt_path)
    if int(receipt.get("holdout_rows_read", -1)) != 0:
        raise ShortWindowL2Error("snapshot receipt reports holdout rows")
    if int(receipt.get("network_reads", -1)) != 0:
        raise ShortWindowL2Error("snapshot receipt reports network reads")
    specs: dict[str, Mapping[str, Any]] = {}
    base = repo_path(prereg["source"]["snapshot_root"])
    for item in receipt["files"]:
        symbol = str(item["symbol"])
        path = base / Path(str(item["snapshot_path"])).name
        verify_file(path, str(item["sha256"]), f"snapshot {symbol}")
        specs[symbol] = {**item, "resolved_path": path}
    return specs


def assign_short_dependency_blocks(events: pd.DataFrame) -> pd.DataFrame:
    """Group overlaps using only W18/W19 input plus the fixed label path."""

    out = events.copy()
    out["_start"] = pd.to_datetime(out["exposure_start_time"], utc=True)
    out["_end"] = pd.to_datetime(out["exposure_end_exclusive"], utc=True)
    out["_available"] = pd.to_datetime(out["available_at"], utc=True)
    if (out["_start"] >= out["_available"]).any() or (out["_available"] >= out["_end"]).any():
        raise ShortWindowL2Error("invalid short-window exposure interval")

    learning = out[out["split"] != "purge"].sort_values(
        ["symbol", "_start", "_end", "_available", "episode_id"]
    )
    for symbol, group in learning.groupby("symbol", sort=True):
        active_end: pd.Timestamp | None = None
        active_split: str | None = None
        for _, row in group.iterrows():
            if (
                active_end is not None
                and str(row["split"]) != active_split
                and row["_start"] < active_end
            ):
                raise ShortWindowL2Error(
                    f"short exposure crosses splits for {symbol}: {active_split} -> {row['split']}"
                )
            active_end = row["_end"] if active_end is None else max(active_end, row["_end"])
            active_split = str(row["split"])

    block_for_index: dict[int, str] = {}
    for (symbol, split), group in out.sort_values(
        ["symbol", "split", "_start", "_end", "_available", "episode_id"]
    ).groupby(["symbol", "split"], sort=True):
        sequence = 0
        active_end: pd.Timestamp | None = None
        active_id = ""
        for index, row in group.iterrows():
            if active_end is None or row["_start"] >= active_end:
                sequence += 1
                active_id = f"{symbol}_{split}_short_dependency_{sequence:06d}"
                active_end = row["_end"]
            else:
                active_end = max(active_end, row["_end"])
            block_for_index[int(index)] = active_id
    out["dependency_block_id"] = pd.Series(block_for_index)
    out["dependency_block_size"] = out.groupby("dependency_block_id")[
        "episode_id"
    ].transform("size").astype(int)
    representatives = (
        out.sort_values(["_available", "episode_id"])
        .groupby("dependency_block_id", sort=False)
        .head(1)
        .index
    )
    out["dependency_representative"] = out.index.isin(representatives)
    if (out.groupby("dependency_block_id")["split"].nunique() > 1).any():
        raise ShortWindowL2Error("dependency block crosses chronological splits")
    return out.drop(columns=["_start", "_end", "_available"]).sort_values(
        ["available_at", "symbol", "episode_id"]
    )


def _snapshot_frame(path: Path) -> pd.DataFrame:
    frame = pd.read_csv(path)
    frame["open_time"] = pd.to_datetime(frame["open_time"], utc=True)
    for column in ("open", "high", "low", "close", "volume"):
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    if frame[["open", "high", "low", "close", "volume"]].isna().any().any():
        raise ShortWindowL2Error(f"snapshot contains non-numeric OHLCV: {path}")
    if frame["open_time"].duplicated().any() or not frame["open_time"].is_monotonic_increasing:
        raise ShortWindowL2Error(f"snapshot clock invalid: {path}")
    if not (frame["open_time"].diff().dropna() == BAR_DELTA).all():
        raise ShortWindowL2Error(f"snapshot has non-15m gap: {path}")
    return frame.reset_index(drop=True)


def build_short_window_dataset(prereg: Mapping[str, Any]) -> dict[str, Any]:
    """Recluster by side, recreate exact L1 inputs, and label future outcomes."""

    terminal = RESULTS_DIR / "dataset_receipt.json"
    if terminal.exists():
        return read_json(terminal)
    immutable = verify_declared_inputs(prereg)
    source_path = repo_path(prereg["source"]["dataset_path"])
    candidates = validate_candidate_ledger(pd.read_csv(source_path), prereg)
    episodes = build_side_homogeneous_episodes(candidates)
    specs = snapshot_specs(prereg)
    from yoyo.layers.l1_detection.data import add_mas
    from yoyo.layers.l1_detection.render import render_chart
    from yoyo.data.indicators import add_indicators
    from yoyo.layers.l2_judgment.labeling import label_candidate, label_short_candidate
    from scripts.research_15m_ma_launch_l2_global_context import split_name

    outcome_spec = prereg["outcome"]
    records: list[dict[str, Any]] = []
    parity_failures: list[str] = []
    rejected: Counter[str] = Counter()
    for number, symbol in enumerate(sorted(episodes["symbol"].unique()), 1):
        if symbol not in specs:
            raise ShortWindowL2Error(f"snapshot receipt missing {symbol}")
        raw = _snapshot_frame(Path(specs[symbol]["resolved_path"]))
        visual = add_mas(raw)
        labeled = add_indicators(raw)
        labeled["atr_quintile"] = causal_atr_quintile(labeled["atr_pct"])
        subset = episodes[episodes["symbol"] == symbol]
        for row in subset.to_dict("records"):
            start, end = int(row["window_start_i"]), int(row["window_end_i"])
            window = visual.iloc[start : end + 1].copy()
            if len(window) != int(row["window_len"]):
                raise ShortWindowL2Error(f"window length mismatch for {row['episode_id']}")
            image, transform = render_chart(window)
            observed_hash = pixel_sha256(image)
            if observed_hash != str(row["input_pixel_sha256"]):
                parity_failures.append(str(row["episode_id"]))
                continue
            end_time = utc(window["open_time"].iloc[-1])
            if end_time != utc(row["window_end_time"]):
                raise ShortWindowL2Error(f"window endpoint mismatch for {row['episode_id']}")
            available_at = end_time + BAR_DELTA
            if available_at != utc(row["available_at"]):
                raise ShortWindowL2Error(f"available_at mismatch for {row['episode_id']}")
            side = str(row["side"])
            labeler = label_candidate if side == "long" else label_short_candidate
            outcome = labeler(
                labeled,
                end,
                tp_mult=float(outcome_spec["tp_atr_multiple"]),
                sl_mult=float(outcome_spec["sl_atr_multiple"]),
                horizon=int(outcome_spec["horizon_bars"]),
                entry="next_open",
            )
            if outcome is None:
                rejected["outcome_unavailable"] += 1
                continue
            atr_bucket = labeled["atr_quintile"].iloc[end]
            if pd.isna(atr_bucket):
                rejected["atr_quintile_unavailable"] += 1
                continue
            detection = {
                **row,
                "l1_confidence": float(row["confidence"]),
                "window_start_i": start,
                "window_len": len(window),
            }
            features = extract_short_window_features(
                window,
                detection,
                price_min=transform.price_min,
                price_max=transform.price_max,
            )
            exposure_end = available_at + int(outcome_spec["horizon_bars"]) * BAR_DELTA
            if exposure_end > utc(prereg["source"]["holdout_start"]):
                raise ShortWindowL2Error(f"outcome exposure crosses holdout for {row['episode_id']}")
            input_start = utc(window["open_time"].iloc[0])
            record = {
                "episode_id": str(row["episode_id"]),
                "representative_candidate_id": str(row["representative_candidate_id"]),
                "source_cross_side_episode_id": str(row["source_cross_side_episode_id"]),
                "symbol": symbol,
                "side": side,
                "class_id": int(row["class_id"]),
                "side_candidate_count": int(row["side_candidate_count"]),
                "feature_bar_i": end,
                "feature_bar_time": end_time.isoformat(),
                "available_at": available_at.isoformat(),
                "signal_time": available_at.isoformat(),
                "input_start_time": input_start.isoformat(),
                "input_end_time": end_time.isoformat(),
                "input_visible_bars": len(window),
                "exposure_start_time": input_start.isoformat(),
                "exposure_end_exclusive": exposure_end.isoformat(),
                "split": split_name(available_at, prereg),
                "l1_confidence": float(row["confidence"]),
                "window_len": len(window),
                "window_start_i": start,
                "window_end_i": end,
                "core_start_i": int(row["core_start_i"]),
                "core_end_i": int(row["core_end_i"]),
                "confirmation_bars": int(row["confirmation_bars"]),
                "prediction_cx_norm": float(row["prediction_cx_norm"]),
                "prediction_cy_norm": float(row["prediction_cy_norm"]),
                "prediction_w_norm": float(row["prediction_w_norm"]),
                "prediction_h_norm": float(row["prediction_h_norm"]),
                "input_pixel_sha256": str(row["input_pixel_sha256"]),
                "atr_quintile": int(atr_bucket),
                "label": int(outcome.label),
                "outcome": str(outcome.outcome),
                "exit_offset": int(outcome.exit_offset),
                "entry_price": float(outcome.entry_price),
                "realized_ret": float(outcome.realized_ret),
                "net_ret": float(outcome.realized_ret)
                - float(outcome_spec["round_trip_cost_fraction"]),
            }
            record.update(features)
            records.append(record)
        print(
            f"short-dataset [{number:02d}/{episodes['symbol'].nunique():02d}] "
            f"{symbol} rows={len(records):,}",
            flush=True,
        )
    if parity_failures:
        raise ShortWindowL2Error(
            f"exact L1 pixel parity failed for {len(parity_failures)} rows: {parity_failures[:10]}"
        )
    dataset = assign_short_dependency_blocks(pd.DataFrame(records))
    if dataset["episode_id"].duplicated().any():
        raise ShortWindowL2Error("short-window dataset has duplicate episode_id")
    forbidden = {
        "pre_range48",
        "pre_range168",
        "spread_pos96",
        "dense_frac48",
        "close_vs_ema200",
        "atr_pct_ratio96",
        "ret_24",
        "ret_48",
        "l1_episode_max_confidence",
    }
    if forbidden & set(dataset.columns):
        raise ShortWindowL2Error("long-context or later-episode feature leaked into dataset")
    numeric = dataset[list(SHORT_WINDOW_FEATURE_COLUMNS)].apply(pd.to_numeric, errors="coerce")
    valid_columns = [column for column in SHORT_WINDOW_FEATURE_COLUMNS if not column.startswith("t18_")]
    if not np.isfinite(numeric[valid_columns].to_numpy(dtype=float)).all():
        raise ShortWindowL2Error("required short-window features contain non-finite values")
    t18 = [column for column in SHORT_WINDOW_FEATURE_COLUMNS if column.startswith("t18_")]
    eighteen = dataset["window_len"].astype(int) == 18
    expected_missing = [column for column in t18 if column not in ("t18_valid", "t18_in_core")]
    if not numeric.loc[eighteen, expected_missing].isna().all().all():
        raise ShortWindowL2Error("18-bar padding must remain missing")
    if not np.isfinite(numeric.loc[~eighteen, t18].to_numpy(dtype=float)).all():
        raise ShortWindowL2Error("19-bar oldest slot must be fully populated")

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    dataset_path = OUTPUT_DIR / "l2_short_window_dataset.csv"
    dataset.to_csv(dataset_path, index=False)
    controls = build_matched_controls(dataset, prereg, specs)
    controls_path = OUTPUT_DIR / "matched_controls.csv"
    pd.DataFrame(controls).to_csv(controls_path, index=False)
    representatives = dataset[dataset["dependency_representative"]]
    payload = {
        "protocol": prereg["protocol"],
        "experiment_id": EXPERIMENT_ID,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_commit": git_head(),
        "immutable_inputs": immutable,
        "candidate_ledger_path": repo_relative(source_path),
        "candidate_ledger_sha256": sha256_file(source_path),
        "candidate_rows": len(candidates),
        "side_homogeneous_episodes": len(episodes),
        "side_episode_counts": dict(sorted(Counter(episodes["side"]).items())),
        "rows_out": len(dataset),
        "reject_reasons": dict(sorted(rejected.items())),
        "pixel_parity": {
            "checked": len(episodes),
            "passed": len(episodes),
            "failed": 0,
            "image_width": 1280,
            "image_height": 742,
        },
        "window_counts": {
            str(key): int(value) for key, value in Counter(dataset["window_len"]).items()
        },
        "side_counts": dict(sorted(Counter(dataset["side"]).items())),
        "split_counts": dict(sorted(Counter(dataset["split"]).items())),
        "representative_counts": {
            f"{side}_{split}": int(
                ((representatives["side"] == side) & (representatives["split"] == split)).sum()
            )
            for side in SIDES
            for split in ("train", "purge", "tune", "final_validation")
        },
        "dependency_blocks": int(dataset["dependency_block_id"].nunique()),
        "feature_columns": list(SHORT_WINDOW_FEATURE_COLUMNS),
        "feature_count": len(SHORT_WINDOW_FEATURE_COLUMNS),
        "forbidden_long_context_feature_count": 0,
        "dataset_path": repo_relative(dataset_path),
        "dataset_sha256": sha256_file(dataset_path),
        "matched_controls": len(controls),
        "matched_events": len({str(row["episode_id"]) for row in controls}),
        "matched_controls_path": repo_relative(controls_path),
        "matched_controls_sha256": sha256_file(controls_path),
        "holdout_rows_read": 0,
        "network_reads": 0,
        "production_eligible": False,
    }
    write_json(terminal, payload)
    return payload


def _control_pool(
    featured: pd.DataFrame,
    episode_indices: Sequence[int],
    prereg: Mapping[str, Any],
) -> dict[tuple[str, int, int], list[int]]:
    from yoyo.contracts.outcomes import ATR_PCT_MIN

    times = pd.to_datetime(featured["open_time"], utc=True)
    available = times + BAR_DELTA
    split = prereg["splits"]["final_preholdout_validation"]
    start = utc(split["available_at_start"])
    end = utc(split["available_at_end_exclusive"])
    horizon = int(prereg["outcome"]["horizon_bars"])
    prohibited = np.zeros(len(featured), dtype=bool)
    for index in episode_indices:
        lo, hi = max(0, int(index) - horizon), min(len(featured), int(index) + horizon + 1)
        prohibited[lo:hi] = True
    atr = featured["atr_pct"].to_numpy(dtype=float)
    bucket = featured["atr_quintile"]
    valid = (
        (available >= start).to_numpy()
        & (available < end).to_numpy()
        & np.isfinite(atr)
        & (atr >= float(ATR_PCT_MIN))
        & bucket.notna().to_numpy()
        & (~prohibited)
    )
    valid &= np.arange(len(featured)) + horizon < len(featured)
    pools: dict[tuple[str, int, int], list[int]] = defaultdict(list)
    for index in np.flatnonzero(valid):
        stamp = utc(available.iloc[int(index)])
        key = (stamp.strftime("%Y-%m"), int(stamp.hour // 8), int(bucket.iloc[int(index)]))
        pools[key].append(int(index))
    return dict(pools)


def build_matched_controls(
    dataset: pd.DataFrame,
    prereg: Mapping[str, Any],
    specs: Mapping[str, Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Build eight exact-match control assignments for new dependency reps."""

    from yoyo.data.indicators import add_indicators
    from yoyo.layers.l2_judgment.labeling import label_candidate, label_short_candidate

    final = dataset[
        (dataset["split"] == "final_validation") & dataset["dependency_representative"]
    ].copy()
    pools: dict[str, dict[tuple[str, int, int], list[int]]] = {}
    frames: dict[str, pd.DataFrame] = {}
    all_episode_indices = {
        symbol: dataset.loc[dataset["symbol"] == symbol, "window_end_i"].astype(int).tolist()
        for symbol in final["symbol"].unique()
    }
    for symbol in sorted(final["symbol"].unique()):
        featured = add_indicators(_snapshot_frame(Path(specs[symbol]["resolved_path"])))
        featured["atr_quintile"] = causal_atr_quintile(featured["atr_pct"])
        frames[symbol] = featured
        pools[symbol] = _control_pool(featured, all_episode_indices[symbol], prereg)

    outcome = prereg["outcome"]
    assignments = int(prereg["matched_control"]["deterministic_assignments"])
    rows: list[dict[str, Any]] = []
    for assignment in range(assignments):
        used: set[tuple[str, int]] = set()
        order = sorted(
            final.to_dict("records"),
            key=lambda row: hashlib.sha256(
                f"event-order:{SEED}:{assignment}:{row['episode_id']}".encode()
            ).hexdigest(),
        )
        for event in order:
            symbol = str(event["symbol"])
            stamp = utc(event["available_at"])
            key = (stamp.strftime("%Y-%m"), int(stamp.hour // 8), int(event["atr_quintile"]))
            ranked = sorted(
                pools[symbol].get(key, []),
                key=lambda index: hashlib.sha256(
                    f"control:{SEED}:{assignment}:{event['episode_id']}:{index}".encode()
                ).hexdigest(),
            )
            chosen = next((index for index in ranked if (symbol, index) not in used), None)
            if chosen is None:
                continue
            side = str(event["side"])
            labeler = label_candidate if side == "long" else label_short_candidate
            result = labeler(
                frames[symbol],
                chosen,
                tp_mult=float(outcome["tp_atr_multiple"]),
                sl_mult=float(outcome["sl_atr_multiple"]),
                horizon=int(outcome["horizon_bars"]),
                entry="next_open",
            )
            if result is None:
                continue
            used.add((symbol, chosen))
            control_available = utc(frames[symbol]["open_time"].iloc[chosen]) + BAR_DELTA
            rows.append(
                {
                    "assignment": assignment,
                    "episode_id": str(event["episode_id"]),
                    "symbol": symbol,
                    "side": side,
                    "event_available_at": stamp.isoformat(),
                    "control_feature_bar_i": chosen,
                    "control_available_at": control_available.isoformat(),
                    "month": key[0],
                    "utc_8h_bucket": key[1],
                    "atr_quintile": key[2],
                    "control_label": int(result.label),
                    "control_outcome": str(result.outcome),
                    "control_realized_ret": float(result.realized_ret),
                    "control_net_ret": float(result.realized_ret)
                    - float(outcome["round_trip_cost_fraction"]),
                }
            )
    return rows


def _learning_rows(data: pd.DataFrame, split: str) -> pd.DataFrame:
    return data[(data["split"] == split) & data["dependency_representative"]].copy()


def _train_side(
    data: pd.DataFrame,
    *,
    side: str,
    feature_columns: Sequence[str],
    prefix: str,
) -> tuple[dict[str, Any], pd.DataFrame, np.ndarray]:
    from yoyo.layers.l2_judgment.train import train_model

    subset = data[data["side"] == side].copy()
    train = _learning_rows(subset, "train")
    tune = _learning_rows(subset, "tune")
    final_events = subset[subset["split"] == "final_validation"].copy()
    final = final_events[final_events["dependency_representative"]].copy()
    if min(len(train), len(tune), len(final)) == 0:
        raise ShortWindowL2Error(f"{side} has an empty train/tune/final split")
    model = train_model(
        train,
        tune,
        feature_columns=feature_columns,
        objective="regression",
        params_override=L2_DETERMINISTIC_PARAMS,
    )
    tune_score = model.predict(
        tune[list(feature_columns)], num_iteration=model.best_iteration
    )
    threshold = float(np.quantile(tune_score, 0.9))
    event_score = model.predict(
        final_events[list(feature_columns)], num_iteration=model.best_iteration
    )
    scored = final_events.copy()
    scored[f"{prefix}_score"] = event_score
    scored[f"{prefix}_percentile"] = empirical_percentile(tune_score, event_score)
    scored[f"{prefix}_threshold"] = threshold
    scored[f"{prefix}_keep"] = event_score >= threshold
    model_dir = OUTPUT_DIR / "models"
    model_dir.mkdir(parents=True, exist_ok=True)
    model_path = model_dir / f"l2_{side}_{prefix}_{len(feature_columns)}f.txt"
    model.save_model(str(model_path))
    importance = pd.DataFrame(
        {
            "feature": list(feature_columns),
            "gain": model.feature_importance(importance_type="gain"),
            "split": model.feature_importance(importance_type="split"),
        }
    ).sort_values("gain", ascending=False)
    importance_path = OUTPUT_DIR / f"feature_importance_{side}_{prefix}.csv"
    importance.to_csv(importance_path, index=False)
    return (
        {
            "side": side,
            "prefix": prefix,
            "feature_columns": list(feature_columns),
            "splits": {"train": len(train), "tune": len(tune), "final_validation": len(final)},
            "best_iteration": int(model.best_iteration),
            "tune_q90_threshold": threshold,
            "model_path": repo_relative(model_path),
            "model_sha256": sha256_file(model_path),
            "importance_path": repo_relative(importance_path),
            "importance_sha256": sha256_file(importance_path),
            "importance_top10": importance.head(10).to_dict("records"),
        },
        scored,
        tune_score,
    )


def _metric_bundle(
    scored_by_side: Mapping[str, pd.DataFrame],
    controls: pd.DataFrame,
    prereg: Mapping[str, Any],
    *,
    prefix: str,
) -> dict[str, Any]:
    cost = float(prereg["outcome"]["round_trip_cost_fraction"])
    combined = pd.concat([scored_by_side[side] for side in SIDES], ignore_index=True)
    final = combined[combined["dependency_representative"]].copy()
    score = final[f"{prefix}_percentile"].to_numpy(dtype=float)
    keep = final[f"{prefix}_keep"].to_numpy(dtype=bool)
    returns = final["realized_ret"].to_numpy(dtype=float)
    labels = final["label"].to_numpy(dtype=int)
    selected_ids = set(final.loc[keep, "episode_id"].astype(str))
    by_side: dict[str, Any] = {}
    for side in SIDES:
        arm = final[final["side"] == side]
        arm_keep = arm[f"{prefix}_keep"].to_numpy(dtype=bool)
        arm_score = arm[f"{prefix}_score"].to_numpy(dtype=float)
        by_side[side] = {
            "final_validation": safe_metrics(
                arm["label"].to_numpy(dtype=int),
                arm_score,
                arm["realized_ret"].to_numpy(dtype=float),
                cost,
            ),
            "frozen_threshold": selected_metrics(arm, arm_keep, cost),
            "outcome_permutation_p": outcome_permutation_pvalue(
                arm_score, arm["realized_ret"].to_numpy(dtype=float)
            ),
        }
    return {
        "final_validation": safe_metrics(labels, score, returns, cost),
        "frozen_threshold": selected_metrics(final, keep, cost),
        "outcome_permutation_p": outcome_permutation_pvalue(score, returns),
        "matched_control": matched_control_metrics(
            final,
            controls,
            selected_ids,
            required_assignments=int(prereg["matched_control"]["deterministic_assignments"]),
        ),
        "by_side": by_side,
        "scored": combined,
    }


def train_evaluate(prereg: Mapping[str, Any]) -> dict[str, Any]:
    terminal = RESULTS_DIR / "training_receipt.json"
    if terminal.exists():
        return read_json(terminal)
    dataset_receipt = read_json(RESULTS_DIR / "dataset_receipt.json")
    dataset_path = repo_path(dataset_receipt["dataset_path"])
    controls_path = repo_path(dataset_receipt["matched_controls_path"])
    verify_file(dataset_path, dataset_receipt["dataset_sha256"], "short-window dataset")
    verify_file(controls_path, dataset_receipt["matched_controls_sha256"], "matched controls")
    data = pd.read_csv(dataset_path)
    data["dependency_representative"] = bool_series(data["dependency_representative"])
    controls = pd.read_csv(controls_path)
    feature_columns = list(SHORT_WINDOW_FEATURE_COLUMNS)

    main_summaries: dict[str, Any] = {}
    main_scored: dict[str, pd.DataFrame] = {}
    baseline_summaries: dict[str, Any] = {}
    baseline_scored: dict[str, pd.DataFrame] = {}
    for side in SIDES:
        summary, scored, _ = _train_side(
            data, side=side, feature_columns=feature_columns, prefix="short_window"
        )
        main_summaries[side], main_scored[side] = summary, scored
        base_summary, base_scored, _ = _train_side(
            data, side=side, feature_columns=["l1_confidence"], prefix="l1_confidence"
        )
        baseline_summaries[side], baseline_scored[side] = base_summary, base_scored

    main = _metric_bundle(main_scored, controls, prereg, prefix="short_window")
    baseline = _metric_bundle(
        baseline_scored, controls, prereg, prefix="l1_confidence"
    )
    scored = main.pop("scored")
    baseline_scored_frame = baseline.pop("scored")
    scored = scored.merge(
        baseline_scored_frame[
            [
                "episode_id",
                "l1_confidence_score",
                "l1_confidence_percentile",
                "l1_confidence_threshold",
                "l1_confidence_keep",
            ]
        ],
        on="episode_id",
        validate="one_to_one",
    )
    scored_path = OUTPUT_DIR / "final_validation_scored.csv"
    scored.to_csv(scored_path, index=False)

    selection = main["frozen_threshold"]
    gate = {
        "top_decile_net_positive": bool(main["final_validation"]["top_decile"]["net_mean"] > 0),
        "frozen_threshold_net_positive": bool(
            selection["net_mean"] is not None and selection["net_mean"] > 0
        ),
        "outcome_permutation_p_lt_0_01": bool(main["outcome_permutation_p"] < 0.01),
        "minimum_30_selected_dependency_blocks": bool(selection["n"] >= 30),
        "beats_matched_controls_every_assignment": bool(
            main["matched_control"]["all_assignments_positive"]
        ),
        "each_side_minimum_10_selected_dependency_blocks": all(
            main["by_side"][side]["frozen_threshold"]["n"] >= 10 for side in SIDES
        ),
        "neither_side_frozen_threshold_net_negative": all(
            main["by_side"][side]["frozen_threshold"]["net_mean"] is not None
            and main["by_side"][side]["frozen_threshold"]["net_mean"] >= 0
            for side in SIDES
        ),
    }
    gate["passed"] = all(gate.values())
    payload = {
        "protocol": prereg["protocol"],
        "experiment_id": EXPERIMENT_ID,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_commit": git_head(),
        "objective": "separate_long_short_regression_on_gross_realized_ret",
        "dataset_path": repo_relative(dataset_path),
        "dataset_sha256": sha256_file(dataset_path),
        "feature_columns": feature_columns,
        "feature_count": len(feature_columns),
        "runtime": runtime_versions(),
        "models": main_summaries,
        "single_feature_baseline_models": baseline_summaries,
        "main": main,
        "single_feature_l1_confidence_baseline": baseline,
        "primary_gate": gate,
        "scored_validation_path": repo_relative(scored_path),
        "scored_validation_sha256": sha256_file(scored_path),
        "holdout_consumed": False,
        "promoted": False,
        "deployed": False,
        "active_or_frozen_changed": False,
        "forward_state_changed": False,
        "orders_placed": False,
        "telegram_sent": False,
        "production_eligible": False,
    }
    write_json(terminal, payload)
    return payload


def _box_corners(row: Mapping[str, Any], width: int, height: int) -> tuple[int, int, int, int]:
    cx = float(row["prediction_cx_norm"]) * width
    cy = float(row["prediction_cy_norm"]) * height
    bw = float(row["prediction_w_norm"]) * width
    bh = float(row["prediction_h_norm"]) * height
    return (
        max(0, int(round(cx - bw / 2))),
        max(0, int(round(cy - bh / 2))),
        min(width - 1, int(round(cx + bw / 2))),
        min(height - 1, int(round(cy + bh / 2))),
    )


def render_review(prereg: Mapping[str, Any]) -> dict[str, Any]:
    terminal = RESULTS_DIR / "render_receipt.json"
    if terminal.exists():
        return read_json(terminal)
    dataset_receipt = read_json(RESULTS_DIR / "dataset_receipt.json")
    training = read_json(RESULTS_DIR / "training_receipt.json")
    data = pd.read_csv(repo_path(dataset_receipt["dataset_path"]))
    scored = pd.read_csv(repo_path(training["scored_validation_path"]))
    scored["dependency_representative"] = bool_series(scored["dependency_representative"])
    specs = snapshot_specs(prereg)
    from yoyo.layers.l1_detection.data import add_mas
    from yoyo.layers.l1_detection.render import render_chart

    final = scored[scored["dependency_representative"]].copy()
    selected = final[final["short_window_keep"].astype(str).str.lower().eq("true")]
    picks: list[tuple[str, pd.Series]] = []
    for side in SIDES:
        arm_selected = selected[selected["side"] == side].nlargest(10, "short_window_percentile")
        arm_rejected = final[
            (final["side"] == side)
            & (~final["short_window_keep"].astype(str).str.lower().eq("true"))
        ].nlargest(10, "l1_confidence")
        picks.extend(("selected", row) for _, row in arm_selected.iterrows())
        picks.extend(("rejected_high_l1", row) for _, row in arm_rejected.iterrows())
    review_root = OUTPUT_DIR / "review"
    manifest_rows: list[dict[str, Any]] = []
    cached: dict[str, pd.DataFrame] = {}
    for group, row in picks:
        symbol = str(row["symbol"])
        if symbol not in cached:
            cached[symbol] = add_mas(_snapshot_frame(Path(specs[symbol]["resolved_path"])))
        start, end = int(row["window_start_i"]), int(row["window_end_i"])
        image, _ = render_chart(cached[symbol].iloc[start : end + 1])
        if pixel_sha256(image) != str(row["input_pixel_sha256"]):
            raise ShortWindowL2Error(f"render parity failed for {row['episode_id']}")
        stem = (
            f"{str(row['side']).upper()}_{str(row['symbol']).replace('_USDT_SWAP','')}_"
            f"{utc(row['available_at']):%Y%m%dT%H%M}_{str(row['episode_id'])[-8:]}"
        )
        raw_path = review_root / group / "raw" / f"{stem}.png"
        overlay_path = review_root / group / "overlay" / f"{stem}.png"
        raw_path.parent.mkdir(parents=True, exist_ok=True)
        overlay_path.parent.mkdir(parents=True, exist_ok=True)
        cv2.imwrite(str(raw_path), image)
        overlay = image.copy()
        x0, y0, x1, y1 = _box_corners(row, overlay.shape[1], overlay.shape[0])
        color = (20, 20, 235) if str(row["side"]) == "short" else (20, 170, 30)
        cv2.rectangle(overlay, (x0, y0), (x1, y1), color, 3, cv2.LINE_AA)
        cv2.imwrite(str(overlay_path), overlay)
        manifest_rows.append(
            {
                "group": group,
                "episode_id": row["episode_id"],
                "symbol": symbol,
                "side": row["side"],
                "available_at": row["available_at"],
                "window_len": int(row["window_len"]),
                "l1_confidence": float(row["l1_confidence"]),
                "l2_percentile": float(row["short_window_percentile"]),
                "l2_keep": bool(str(row["short_window_keep"]).lower() == "true"),
                "realized_ret": float(row["realized_ret"]),
                "raw_path": repo_relative(raw_path),
                "raw_sha256": sha256_file(raw_path),
                "overlay_path": repo_relative(overlay_path),
                "overlay_sha256": sha256_file(overlay_path),
            }
        )
    manifest_path = review_root / "manifest.csv"
    pd.DataFrame(manifest_rows).to_csv(manifest_path, index=False)
    payload = {
        "protocol": prereg["protocol"],
        "experiment_id": EXPERIMENT_ID,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "images": len(manifest_rows),
        "raw_pixel_parity_passed": len(manifest_rows),
        "future_bars_rendered": 0,
        "manifest_path": repo_relative(manifest_path),
        "manifest_sha256": sha256_file(manifest_path),
        "review_root": repo_relative(review_root),
        "holdout_consumed": False,
    }
    write_json(terminal, payload)
    return payload


def verify_outputs(prereg: Mapping[str, Any]) -> dict[str, Any]:
    dataset = read_json(RESULTS_DIR / "dataset_receipt.json")
    training = read_json(RESULTS_DIR / "training_receipt.json")
    render = read_json(RESULTS_DIR / "render_receipt.json")
    rejected = sum(int(value) for value in dataset["reject_reasons"].values())
    checks = {
        "candidate_rows_exact": int(dataset["candidate_rows"])
        == int(prereg["source"]["candidate_rows"]),
        "side_episode_accounting": int(dataset["rows_out"]) + rejected
        == int(dataset["side_homogeneous_episodes"]),
        "pixel_parity_all": int(dataset["pixel_parity"]["passed"])
        == int(dataset["side_homogeneous_episodes"]),
        "pixel_parity_failures_zero": int(dataset["pixel_parity"]["failed"]) == 0,
        "feature_count_exact": int(dataset["feature_count"]) == len(SHORT_WINDOW_FEATURE_COLUMNS),
        "long_context_features_zero": int(dataset["forbidden_long_context_feature_count"]) == 0,
        "dataset_hash": sha256_file(repo_path(dataset["dataset_path"])) == dataset["dataset_sha256"],
        "controls_hash": sha256_file(repo_path(dataset["matched_controls_path"]))
        == dataset["matched_controls_sha256"],
        "scored_hash": sha256_file(repo_path(training["scored_validation_path"]))
        == training["scored_validation_sha256"],
        "model_hashes": all(
            sha256_file(repo_path(spec["model_path"])) == spec["model_sha256"]
            for spec in training["models"].values()
        ),
        "render_manifest_hash": sha256_file(repo_path(render["manifest_path"]))
        == render["manifest_sha256"],
        "future_bars_zero": int(render["future_bars_rendered"]) == 0,
        "holdout_zero": not bool(training["holdout_consumed"]),
        "production_false": not bool(training["production_eligible"]),
    }
    if not all(checks.values()):
        raise ShortWindowL2Error(f"verification failed: {checks}")
    payload = {
        "protocol": prereg["protocol"],
        "experiment_id": EXPERIMENT_ID,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "checks": checks,
        "passed": True,
        "holdout_consumed": False,
        "production_eligible": False,
    }
    write_json(RESULTS_DIR / "verify_receipt.json", payload)
    return payload


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prereg", type=Path, default=PREREG_PATH)
    parser.add_argument("--build-dataset", action="store_true")
    parser.add_argument("--train-evaluate", action="store_true")
    parser.add_argument("--render", action="store_true")
    parser.add_argument("--verify", action="store_true")
    parser.add_argument("--all", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    prereg = load_preregistration(args.prereg)
    if not any((args.build_dataset, args.train_evaluate, args.render, args.verify, args.all)):
        raise SystemExit("choose --build-dataset/--train-evaluate/--render/--verify/--all")
    if args.all or args.build_dataset:
        print(json.dumps(build_short_window_dataset(prereg), ensure_ascii=False, indent=2))
    if args.all or args.train_evaluate:
        print(json.dumps(train_evaluate(prereg), ensure_ascii=False, indent=2))
    if args.all or args.render:
        print(json.dumps(render_review(prereg), ensure_ascii=False, indent=2))
    if args.all or args.verify:
        print(json.dumps(verify_outputs(prereg), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
