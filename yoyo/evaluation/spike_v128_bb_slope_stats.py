"""Summarize frozen V12.8 BB-boundary-slope cohorts without replaying trades.

This module consumes the slope-enriched V12.8 trades and matched-control
streams. Slope thresholds come only from closed, wholly pre-split trades, then
remain fixed for every cohort. The builder owns feature generation and the
replayer owns outcomes; this module does not alter entries, exits, or ledgers.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import subprocess
from typing import Any, Mapping
import warnings

import numpy as np
import pandas as pd
from scipy.stats import rankdata, spearmanr


VERSION = "spike-v128-bb-slope-stats-v1"
REPO_ROOT = Path(__file__).resolve().parents[2]
PRIMARY_FEATURE = "pre12_opp"
SINGLE_FEATURE_BASELINES = ("pre12_trend", "pre12_inward")
SECONDARY_FEATURES = ("now12_opp", "pre3_opp", "pre24_opp", "episode12_opp")
FEATURES = (PRIMARY_FEATURE, *SINGLE_FEATURE_BASELINES, *SECONDARY_FEATURES)
ARMS = ("v9_both", "joint")
SOURCE_FILES = (
    "yoyo/evaluation/pine/spike_burst_v12_8.pine",
    "yoyo/evaluation/spike_v128_bb_slope.py",
    "yoyo/evaluation/spike_v128_recent.py",
    "yoyo/evaluation/spike_v126_htf_recheck.py",
    "yoyo/evaluation/spike_burst_replay.py",
    "yoyo/evaluation/spike_v7_fast.py",
    "yoyo/evaluation/spike_v1_v8_be05.py",
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _json_hash(value: object) -> str:
    payload = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _load_config(config: Mapping[str, Any] | str | Path) -> tuple[dict[str, Any], str, str | None]:
    if isinstance(config, Mapping):
        value = dict(config)
        return value, _json_hash(value), None
    path = Path(config)
    raw = path.read_bytes()
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError("config must contain a JSON object")
    return value, hashlib.sha256(raw).hexdigest(), str(path)


def _config_axis(cfg: Mapping[str, Any], run_dir: Path, key: str, fallback: str) -> list[Any]:
    value = cfg.get(key)
    if value is None:
        value = cfg.get(fallback)
    if value is not None:
        return list(value)
    if key == "symbols":
        return sorted(path.name.rsplit("_", 1)[0] for path in (run_dir / "streams").glob("*_*m"))
    if key == "timeframes":
        result = []
        for path in (run_dir / "streams").glob("*_*m"):
            suffix = path.name.rsplit("_", 1)[-1]
            if suffix.endswith("m") and suffix[:-1].isdigit():
                result.append(int(suffix[:-1]))
        return sorted(set(result))
    return []


def _bool_column(series: pd.Series, name: str) -> pd.Series:
    if pd.api.types.is_bool_dtype(series.dtype):
        return series.fillna(False).astype(bool)
    mapping = {
        "true": True, "1": True, "yes": True, "false": False, "0": False, "no": False,
        "": False, "nan": False, "none": False,
    }
    values = series.astype(str).str.strip().str.lower().map(mapping)
    if values.isna().any():
        examples = series[values.isna()].astype(str).head(3).tolist()
        raise ValueError(f"unparseable boolean values in {name}: {examples}")
    return values.astype(bool)


def _utc(series: pd.Series) -> pd.Series:
    return pd.to_datetime(series, utc=True, errors="coerce")


def _bucket(values: pd.Series, q1: float, q2: float) -> pd.Series:
    """Assign fixed training terciles; duplicate cutpoints may leave a bin empty."""
    result = pd.Series(pd.NA, index=values.index, dtype="string")
    x = values.to_numpy(dtype=float, na_value=np.nan)
    finite = np.isfinite(x)
    result.loc[finite & (x <= q1)] = "low"
    result.loc[finite & (x > q1) & (x <= q2)] = "middle"
    result.loc[finite & (x > q2)] = "high"
    return result


def _tercile_thresholds(values: pd.Series) -> tuple[float, float, float]:
    finite = values[np.isfinite(values.to_numpy(dtype=float, na_value=np.nan))].astype(float)
    if len(finite) < 3:
        return math.nan, math.nan, float(len(finite))
    q1, q2 = finite.quantile([1 / 3, 2 / 3], interpolation="linear").to_numpy(float)
    return float(q1), float(q2), float(len(finite))


def _q90(values: pd.Series) -> tuple[float, float]:
    finite = values[np.isfinite(values.to_numpy(dtype=float, na_value=np.nan))].astype(float)
    return (float(finite.quantile(0.9, interpolation="linear")), float(len(finite))) if len(finite) else (math.nan, 0.0)


def _verify_run_manifest(run_dir: Path, cfg: Mapping[str, Any],
                         symbols: list[Any], timeframes: list[Any]) -> dict[str, Any]:
    identity_path, manifest_path = run_dir / "identity.json", run_dir / "manifest.json"
    if not identity_path.is_file() or not manifest_path.is_file():
        raise FileNotFoundError("run identity.json and complete manifest.json are required")
    identity = json.loads(identity_path.read_text())
    manifest = json.loads(manifest_path.read_text())
    if not manifest.get("complete") or manifest.get("errors"):
        raise ValueError("input run manifest is incomplete or reports errors")
    identity_hash = hashlib.sha256(json.dumps(identity, sort_keys=True).encode("utf-8")).hexdigest()
    if manifest.get("run_identity") != identity_hash:
        raise ValueError("run manifest identity hash mismatch")
    if identity.get("config") != dict(cfg):
        raise ValueError("supplied config differs from the config bound to the input run")
    selected = identity.get("selected")
    if selected is not None and sorted(int(x) for x in selected) != sorted(int(x) for x in timeframes):
        raise ValueError("run identity timeframes differ from the config")
    expected = {(str(symbol), int(minutes)) for symbol in symbols for minutes in timeframes}
    manifest_receipts = {}
    for receipt in manifest.get("receipts", []):
        key = (str(receipt.get("symbol", "")), int(receipt.get("minutes", -1)))
        if key in manifest_receipts:
            raise ValueError(f"duplicate stream receipt in run manifest: {key}")
        manifest_receipts[key] = receipt
    if set(manifest_receipts) != expected:
        raise ValueError("run manifest stream coverage differs from configured symbols/timeframes")
    return {"identity": identity, "manifest": manifest, "manifest_sha256": _sha256(manifest_path),
            "identity_sha256": _sha256(identity_path), "expected_streams": expected,
            "manifest_receipts": manifest_receipts}


def _verify_stream_receipt(folder: Path, symbol: str, minutes: int,
                           manifest_receipt: Mapping[str, Any], run_identity: str) -> tuple[dict[str, Any], str]:
    receipt_path = folder / "receipt.json"
    if not receipt_path.is_file():
        raise FileNotFoundError(f"stream receipt missing: {receipt_path}")
    receipt = json.loads(receipt_path.read_text())
    if str(receipt.get("symbol")) != symbol or int(receipt.get("minutes", -1)) != minutes:
        raise ValueError(f"stream receipt identity mismatch: {receipt_path}")
    if receipt.get("run_identity") != run_identity or manifest_receipt.get("run_identity") != run_identity:
        raise ValueError(f"stream/run identity mismatch: {receipt_path}")
    files = receipt.get("files")
    if not isinstance(files, dict) or not {"trades.csv.gz", "controls.csv.gz"}.issubset(files):
        raise ValueError(f"stream receipt does not bind trades and controls: {receipt_path}")
    for name, expected_hash in files.items():
        path = folder / name
        if not path.is_file() or _sha256(path) != expected_hash:
            raise ValueError(f"stream artifact hash mismatch: {path}")
    manifest_files = manifest_receipt.get("files")
    if manifest_files != files:
        raise ValueError(f"run manifest and stream receipt disagree: {receipt_path}")
    return receipt, _sha256(receipt_path)


def _load_streams(run_dir: Path, cfg: Mapping[str, Any]) -> tuple[pd.DataFrame, pd.DataFrame, list[dict[str, str]], dict[str, Any]]:
    streams = run_dir / "streams"
    symbols = _config_axis(cfg, run_dir, "symbols", "instruments")
    timeframes = _config_axis(cfg, run_dir, "timeframes", "timeframes_min")
    if not symbols or not timeframes:
        raise ValueError("config or run directory must identify symbols and timeframes")
    run_meta = _verify_run_manifest(run_dir, cfg, symbols, timeframes)
    trades_parts, controls_parts, input_hashes, receipt_hashes = [], [], [], []
    run_identity = str(run_meta["manifest"].get("run_identity", ""))
    for symbol in symbols:
        for minutes_value in timeframes:
            minutes = int(minutes_value)
            stem = f"{symbol}_{minutes}m"
            folder = streams / stem
            manifest_receipt = run_meta["manifest_receipts"][(str(symbol), minutes)]
            stream_receipt, receipt_hash = _verify_stream_receipt(folder, str(symbol), minutes, manifest_receipt, run_identity)
            trade_path, control_path = folder / "trades.csv.gz", folder / "controls.csv.gz"
            trades = pd.read_csv(trade_path)
            controls = pd.read_csv(control_path)
            if len(trades):
                if "symbol" not in trades:
                    trades["symbol"] = symbol
                if "timeframe_min" not in trades:
                    trades["timeframe_min"] = minutes
                if set(trades["symbol"].astype(str)) != {str(symbol)} or set(pd.to_numeric(trades["timeframe_min"], errors="coerce").dropna().astype(int)) != {minutes}:
                    raise ValueError(f"stream metadata mismatch in {trade_path}")
                trades_parts.append(trades)
            if len(controls):
                if "symbol" not in controls:
                    controls["symbol"] = symbol
                if "timeframe_min" not in controls:
                    controls["timeframe_min"] = minutes
                controls_parts.append(controls)
            input_hashes.extend({"path": str(path), "sha256": _sha256(path)} for path in (trade_path, control_path))
            receipt_hashes.append({"path": str(folder / "receipt.json"), "sha256": receipt_hash,
                                  "symbol": str(symbol), "timeframe_min": minutes,
                                  "trade_rows": int(stream_receipt.get("trades", len(trades)))})
    run_meta["stream_receipts"] = receipt_hashes
    return (pd.concat(trades_parts, ignore_index=True) if trades_parts else pd.DataFrame(),
            pd.concat(controls_parts, ignore_index=True) if controls_parts else pd.DataFrame(), input_hashes, run_meta)


def _prepare_trades(trades: pd.DataFrame, cfg: Mapping[str, Any]) -> pd.DataFrame:
    required = {"arm", "symbol", "timeframe_min", "side", "trade_key", "signal_close", "entry_time", "exit_time",
                "censored", "gross_return", "net_return", "net_r", "initial_risk_frac", "mfe_known_r", *FEATURES}
    missing = required - set(trades.columns)
    if missing:
        raise ValueError(f"trades missing required columns: {sorted(missing)}")
    if trades.trade_key.astype(str).duplicated().any():
        raise ValueError("trade_key must be unique across streams")
    if not set(trades.arm.dropna().astype(str)).issubset(ARMS):
        raise ValueError("unexpected V12.8 arm in trades")
    result = trades.copy()
    result["timeframe_min"] = pd.to_numeric(result.timeframe_min, errors="raise").astype(int)
    result["side"] = pd.to_numeric(result.side, errors="raise").astype(int)
    if not result.side.isin((-1, 1)).all():
        raise ValueError("side must be -1 or +1")
    for column in ("signal_close", "entry_time", "exit_time"):
        result[f"_{column}"] = _utc(result[column])
    result["_censored"] = _bool_column(result.censored, "censored")
    result["_closed"] = ~result["_censored"] & result["_exit_time"].notna()
    for column in ("gross_return", "net_return", "net_r", "initial_risk_frac", "mfe_known_r", *FEATURES):
        result[column] = pd.to_numeric(result[column], errors="coerce")
    start, split, end = (pd.Timestamp(cfg[key]) for key in ("start", "split", "end"))
    if start.tzinfo is None or split.tzinfo is None or end.tzinfo is None:
        raise ValueError("config start/split/end must include a timezone")
    if not start < split < end:
        raise ValueError("expected start < split < end")
    result = result.loc[result["_signal_close"].notna() & result["_signal_close"].ge(start) & result["_signal_close"].lt(end)].copy()
    signal, entry, exit_ = result["_signal_close"], result["_entry_time"], result["_exit_time"]
    earlier = signal.lt(split) & entry.notna() & entry.lt(split) & exit_.notna() & exit_.lt(split)
    later = signal.ge(split) & entry.notna() & entry.ge(split) & exit_.notna() & exit_.ge(split)
    result["_fold"] = "crosscut"
    result.loc[earlier, "_fold"] = "earlier"
    result.loc[later, "_fold"] = "later"
    result["_signal_week"] = signal.dt.strftime("%G-W%V")

    # Ledger returns are fractions of notional; R is net return / initial risk fraction.
    expected_cost = float(cfg.get("round_trip_cost", 0.002))
    closed = result.loc[result["_closed"]]
    financial = closed[["gross_return", "net_return", "net_r", "initial_risk_frac"]].to_numpy(float)
    if len(financial) and not np.isfinite(financial).all():
        raise ValueError("closed trades require finite return and risk fields")
    if len(closed) and not np.allclose(closed.gross_return - closed.net_return, expected_cost, atol=1e-8, rtol=0):
        raise ValueError("gross_return - net_return differs from configured round-trip cost")
    valid_r = closed.initial_risk_frac.gt(0)
    if valid_r.any() and not np.allclose(closed.loc[valid_r, "net_r"],
                                         closed.loc[valid_r, "net_return"] / closed.loc[valid_r, "initial_risk_frac"],
                                         atol=1e-7, rtol=1e-7):
        raise ValueError("net_r is inconsistent with net_return / initial_risk_frac")
    return result.reset_index(drop=True)


def _prepare_controls(controls: pd.DataFrame) -> pd.DataFrame:
    required = {"trade_key", "matched", "control_net_return", "control_exit_time"}
    missing = required - set(controls.columns)
    if missing:
        if controls.empty:
            return pd.DataFrame(columns=["trade_key", "_matched", "control_net_return", "_control_exit", "_control_signal"])
        raise ValueError(f"controls missing required columns: {sorted(missing)}")
    if controls.trade_key.astype(str).duplicated().any():
        raise ValueError("controls must have at most one row per trade_key")
    result = controls.copy()
    result["_matched"] = _bool_column(result.matched, "controls.matched")
    result["control_net_return"] = pd.to_numeric(result.control_net_return, errors="coerce")
    result["_control_exit"] = _utc(result.control_exit_time)
    signal_field = "control_signal_close" if "control_signal_close" in result else "control_signal_time" if "control_signal_time" in result else None
    result["_control_signal"] = _utc(result[signal_field]) if signal_field else pd.NaT
    return result


def _auc(labels: np.ndarray, scores: np.ndarray) -> float:
    labels = np.asarray(labels, dtype=bool)
    scores = np.asarray(scores, dtype=float)
    finite = np.isfinite(scores)
    labels, scores = labels[finite], scores[finite]
    n_pos, n_neg = int(labels.sum()), int((~labels).sum())
    if not n_pos or not n_neg:
        return math.nan
    ranks = rankdata(scores, method="average")
    return float((ranks[labels].sum() - n_pos * (n_pos + 1) / 2) / (n_pos * n_neg))


def _metric_values(rows: pd.DataFrame) -> dict[str, Any]:
    closed = rows.loc[rows["_closed"]]
    result: dict[str, Any] = {
        "n_rows": int(len(rows)), "n_closed": int(len(closed)),
        "n_censored": int(rows["_censored"].sum()),
        "net_win_rate": math.nan, "mean_gross_bp": math.nan, "mean_net_bp": math.nan,
        "mean_net_r": math.nan,
    }
    if len(closed):
        result.update(net_win_rate=float(closed.net_return.gt(0).mean()),
                      mean_gross_bp=float(closed.gross_return.mean() * 10000),
                      mean_net_bp=float(closed.net_return.mean() * 10000),
                      mean_net_r=float(closed.net_r.mean()))
    for threshold in (3, 5, 10):
        result[f"net_r_ge_{threshold}_count"] = int(closed.net_r.ge(threshold).sum())
        result[f"mfe_known_r_ge_{threshold}_count"] = int(closed.mfe_known_r.ge(threshold).sum())
    return result


def _control_values(rows: pd.DataFrame, controls: pd.DataFrame, cohort: str,
                    start: pd.Timestamp, split: pd.Timestamp, end: pd.Timestamp) -> dict[str, Any]:
    empty = {"matched_control_n": 0, "matched_target_net_bp": math.nan,
             "control_net_bp": math.nan, "paired_excess_bp": math.nan}
    if rows.empty or controls.empty:
        return empty
    target = rows.loc[rows["_closed"], ["trade_key", "net_return", "_fold"]]
    paired = target.merge(controls, on="trade_key", how="inner", validate="one_to_one")
    paired = paired.loc[paired["_matched"] & paired.control_net_return.notna() & paired["_control_exit"].notna()]
    signal = paired["_control_signal"]
    exit_ = paired["_control_exit"]
    if cohort == "earlier":
        paired = paired.loc[paired["_fold"].eq("earlier") & signal.lt(split) & exit_.lt(split)]
    elif cohort == "later":
        paired = paired.loc[paired["_fold"].eq("later") & signal.ge(split) & exit_.ge(split)]
    else:
        paired = paired.loc[signal.notna() & signal.ge(start) & signal.lt(end) & exit_.ge(start) & exit_.lt(end)]
    if paired.empty:
        return empty
    target_bp = paired.net_return.to_numpy(float) * 10000
    control_bp = paired.control_net_return.to_numpy(float) * 10000
    return {"matched_control_n": int(len(paired)),
            "matched_target_net_bp": float(np.mean(target_bp)),
            "control_net_bp": float(np.mean(control_bp)),
            "paired_excess_bp": float(np.mean(target_bp - control_bp))}


def _cohort_mask(rows: pd.DataFrame, cohort: str) -> pd.Series:
    if cohort == "all":
        return pd.Series(True, index=rows.index)
    return rows["_fold"].eq(cohort)


def _feature_tables(trades: pd.DataFrame, controls: pd.DataFrame,
                    cfg: Mapping[str, Any]) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    start, split, end = (pd.Timestamp(cfg[key]).tz_convert("UTC") for key in ("start", "split", "end"))
    metrics: list[dict[str, Any]] = []
    thresholds: list[dict[str, Any]] = []
    diagnostics: list[dict[str, Any]] = []
    side_metrics: list[dict[str, Any]] = []
    for (symbol, minutes, arm), group in trades.groupby(["symbol", "timeframe_min", "arm"], sort=True, dropna=False):
        for feature in FEATURES:
            role = ("primary" if feature == PRIMARY_FEATURE else
                    "single_feature" if feature in SINGLE_FEATURE_BASELINES else "lag_diagnostic")
            train = group.loc[group["_closed"] & group["_fold"].eq("earlier") & np.isfinite(group[feature])]
            q1, q2, n_train_float = _tercile_thresholds(train[feature])
            q90, _ = _q90(train[feature])
            n_train = int(n_train_float) if np.isfinite(n_train_float) else 0
            thresholds.append({"symbol": symbol, "timeframe_min": int(minutes), "arm": arm,
                               "feature": feature, "feature_role": role, "early_closed_n": n_train,
                               "q1_tercile": q1, "q2_tercile": q2, "q90_top10": q90,
                               "threshold_status": "ready" if n_train >= 3 else "fewer_than_3_early_closed"})
            assigned = _bucket(group[feature], q1, q2) if n_train >= 3 else pd.Series(pd.NA, index=group.index, dtype="string")
            top10 = group[feature].ge(q90) & np.isfinite(group[feature]) if np.isfinite(q90) else pd.Series(False, index=group.index)
            for cohort in ("all", "earlier", "later"):
                cohort_rows = group.loc[_cohort_mask(group, cohort) & np.isfinite(group[feature])].copy()
                cohort_rows["_bucket"] = assigned.loc[cohort_rows.index]
                cohort_rows["_top10"] = top10.loc[cohort_rows.index]
                categories: list[tuple[str, pd.DataFrame]] = [("baseline", cohort_rows)]
                if n_train >= 3:
                    categories.extend((name, cohort_rows.loc[cohort_rows["_bucket"].eq(name)]) for name in ("low", "middle", "high"))
                    categories.append(("top10", cohort_rows.loc[cohort_rows["_top10"]]))
                for bucket, selected in categories:
                    row = {"symbol": symbol, "timeframe_min": int(minutes), "arm": arm,
                           "feature": feature, "feature_role": role, "cohort": cohort,
                           "bucket": bucket, "early_closed_n": n_train,
                           "q1_tercile": q1, "q2_tercile": q2, "q90_top10": q90,
                           **_metric_values(selected)}
                    row.update(_control_values(selected, controls, cohort, start, split, end))
                    metrics.append(row)
                closed = cohort_rows.loc[cohort_rows["_closed"]]
                positive = closed.net_return.gt(0).to_numpy(bool)
                slope = closed[feature].to_numpy(float)
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore")
                    rho = spearmanr(slope, closed.net_return.to_numpy(float)).statistic if len(closed) >= 2 else math.nan
                selected_top = closed.loc[closed[feature].ge(q90)] if np.isfinite(q90) else closed.iloc[0:0]
                diag = {"symbol": symbol, "timeframe_min": int(minutes), "arm": arm,
                        "feature": feature, "feature_role": role, "cohort": cohort,
                        "early_closed_n": n_train, "q90_top10": q90,
                        "n_closed": int(len(closed)),
                        "auc_slope_net_positive": _auc(positive, slope),
                        "spearman_slope_net_return": float(rho) if np.isfinite(rho) else math.nan,
                        "top10_n": int(len(selected_top)),
                        **{f"top10_{key}": value for key, value in _metric_values(selected_top).items()}}
                diag.update({f"top10_{key}": value for key, value in _control_values(selected_top, controls, cohort, start, split, end).items()})
                diagnostics.append(diag)
        # Side rows use the same pooled early thresholds. They are descriptive
        # cuts, not separately calibrated long/short thresholds.
        feature = PRIMARY_FEATURE
        train = group.loc[group["_closed"] & group["_fold"].eq("earlier") & np.isfinite(group[feature])]
        q1, q2, n_train_float = _tercile_thresholds(train[feature])
        n_train = int(n_train_float) if np.isfinite(n_train_float) else 0
        assigned = _bucket(group[feature], q1, q2) if n_train >= 3 else pd.Series(pd.NA, index=group.index, dtype="string")
        for side, side_name in ((1, "long"), (-1, "short")):
            side_group = group.loc[group.side.eq(side)]
            side_buckets = assigned.loc[side_group.index]
            for cohort in ("all", "earlier", "later"):
                cohort_rows = side_group.loc[_cohort_mask(side_group, cohort) & np.isfinite(side_group[feature])].copy()
                cohort_rows["_bucket"] = side_buckets.loc[cohort_rows.index]
                for bucket in ("baseline", "low", "middle", "high"):
                    selected = cohort_rows if bucket == "baseline" else cohort_rows.loc[cohort_rows["_bucket"].eq(bucket)]
                    row = {"symbol": symbol, "timeframe_min": int(minutes), "arm": arm,
                           "feature": PRIMARY_FEATURE, "feature_role": "primary",
                           "side": side, "side_name": side_name, "cohort": cohort,
                           "bucket": bucket, "early_closed_n": n_train,
                           "q1_tercile": q1, "q2_tercile": q2,
                           **_metric_values(selected)}
                    row.update(_control_values(selected, controls, cohort, start, split, end))
                    side_metrics.append(row)
    return (pd.DataFrame(metrics), pd.DataFrame(thresholds), pd.DataFrame(diagnostics),
            pd.DataFrame(side_metrics))


def _difference(frame: pd.DataFrame, value: str) -> float:
    high = frame.loc[frame["_bucket"].eq("high"), value]
    low = frame.loc[frame["_bucket"].eq("low"), value]
    return float(high.mean() - low.mean()) if len(high) and len(low) else math.nan


def _cluster_bootstrap(frame: pd.DataFrame, value: str, draws: int, seed: int) -> dict[str, Any]:
    """Bootstrap complete signal weeks, retaining every selected week member."""
    eligible = frame.loc[frame["_bucket"].isin(("low", "high")) & frame["_signal_week"].notna()].copy()
    weeks = np.asarray(sorted(eligible["_signal_week"].unique()), dtype=object)
    observed = _difference(eligible, value)
    if not len(weeks) or not np.isfinite(observed):
        return {"observed": observed, "ci_low": math.nan, "ci_high": math.nan, "valid_draws": 0}
    # Precompute sufficient statistics once, then resample whole UTC weeks
    # with numpy. This preserves clustering without repeated DataFrame scans.
    grouped = eligible.groupby(["_signal_week", "_bucket"], sort=True)[value].agg(["sum", "count"])
    week_stats = np.zeros((len(weeks), 4), dtype=float)
    week_index = {week: index for index, week in enumerate(weeks)}
    for (week, bucket), stats in grouped.iterrows():
        index = week_index[week]
        offset = 0 if bucket == "high" else 2
        week_stats[index, offset] = float(stats["sum"])
        week_stats[index, offset + 1] = int(stats["count"])
    rng = np.random.default_rng(seed)
    sampled = week_stats[rng.integers(0, len(weeks), size=(draws, len(weeks)))].sum(axis=1)
    valid = (sampled[:, 1] > 0) & (sampled[:, 3] > 0)
    samples = sampled[valid, 0] / sampled[valid, 1] - sampled[valid, 2] / sampled[valid, 3]
    if len(samples) < 2:
        lower = upper = math.nan
    else:
        lower, upper = np.quantile(samples, [0.025, 0.975]).tolist()
    return {"observed": observed, "ci_low": float(lower), "ci_high": float(upper), "valid_draws": int(len(samples))}


def _stratified_permutation(frame: pd.DataFrame, value: str, draws: int, seed: int) -> dict[str, Any]:
    """Shuffle high/low labels only inside side × UTC signal-week blocks."""
    eligible = frame.loc[frame["_bucket"].isin(("low", "high")) & frame["_signal_week"].notna()].copy().reset_index(drop=True)
    observed = _difference(eligible, value)
    if not np.isfinite(observed):
        return {"observed": observed, "p_value": math.nan, "shufflable_n": 0,
                "extreme_n": int(len(eligible)), "strata_n": 0, "coverage": 0.0,
                "valid_draws": 0, "high_n_observed": int(eligible["_bucket"].eq("high").sum()),
                "high_n_draw_min": math.nan, "high_n_draw_max": math.nan}
    blocks = []
    for _, rows in eligible.groupby(["side", "_signal_week"], sort=True):
        labels = rows["_bucket"].eq("high").to_numpy(bool)
        if labels.any() and (~labels).any():
            blocks.append((rows.index.to_numpy(int), int(labels.sum())))
    covered = sum(len(indices) for indices, _ in blocks)
    coverage = float(covered / len(eligible)) if len(eligible) else 0.0
    if not blocks or draws <= 0:
        return {"observed": observed, "p_value": math.nan, "shufflable_n": int(covered),
                "extreme_n": int(len(eligible)), "strata_n": len(blocks), "coverage": coverage,
                "valid_draws": 0, "high_n_observed": int(eligible["_bucket"].eq("high").sum()),
                "high_n_draw_min": math.nan, "high_n_draw_max": math.nan}
    values = eligible[value].to_numpy(float)
    rng = np.random.default_rng(seed)
    exceed = 0
    observed_high_n = int(eligible["_bucket"].eq("high").sum())
    draw_high_counts: list[int] = []
    for _ in range(draws):
        # Keep labels fixed in non-mixed strata; only mixed side/week blocks
        # participate in the conditional shuffle.
        high_mask = eligible["_bucket"].eq("high").to_numpy(bool)
        for indices, n_high in blocks:
            high_mask[indices] = False
            high_mask[rng.choice(indices, size=n_high, replace=False)] = True
        draw_high_counts.append(int(high_mask.sum()))
        if high_mask.any() and (~high_mask).any():
            difference = float(values[high_mask].mean() - values[~high_mask].mean())
            exceed += abs(difference) >= abs(observed) - 1e-15
    p_value = (exceed + 1) / (draws + 1)
    return {"observed": observed, "p_value": float(p_value), "shufflable_n": int(covered),
            "extreme_n": int(len(eligible)), "strata_n": len(blocks),
            "coverage": coverage, "valid_draws": int(draws),
            "high_n_observed": observed_high_n,
            "high_n_draw_min": int(min(draw_high_counts)),
            "high_n_draw_max": int(max(draw_high_counts))}


def _holm(values: pd.Series, family_n: int) -> pd.Series:
    """Holm adjustment across fixed slots, treating unavailable tests as p=1."""
    result = pd.Series(np.nan, index=values.index, dtype=float)
    if family_n <= 0 or len(values) > family_n:
        raise ValueError("Holm family size must be positive and cover every configured slot")
    p_values = values.astype(float).fillna(1.0).sort_values(kind="mergesort")
    if not len(p_values):
        return result
    adjusted = []
    running = 0.0
    for rank, (index, p_value) in enumerate(p_values.items()):
        running = max(running, min(1.0, (family_n - rank) * p_value))
        if pd.notna(values.loc[index]):
            adjusted.append((index, running))
    for index, value in adjusted:
        result.loc[index] = value
    return result


def _primary_inference(trades: pd.DataFrame, cfg: Mapping[str, Any]) -> pd.DataFrame:
    bootstrap_draws = int(cfg.get("bootstrap", cfg.get("bootstrap_draws", 2000)))
    permutation_draws = int(cfg.get("permutations", cfg.get("flips", 10000)))
    base_seed = int(cfg.get("statistics_seed", cfg.get("stat_seed", 1282026)))
    symbols = _config_axis(cfg, Path("."), "symbols", "instruments")
    eth_symbol = next((s for s in symbols if str(s).upper().startswith("ETH")), "ETH_USDT_SWAP")
    timeframes = _config_axis(cfg, Path("."), "timeframes", "timeframes_min")
    output = []
    for minutes in timeframes:
        rows = trades.loc[(trades.symbol.astype(str) == str(eth_symbol))
                          & trades.timeframe_min.eq(int(minutes)) & trades.arm.eq("v9_both")
                          & trades["_closed"] & trades["_fold"].eq("later")
                          & np.isfinite(trades["pre12_opp"])].copy()
        train = trades.loc[(trades.symbol.astype(str) == str(eth_symbol))
                           & trades.timeframe_min.eq(int(minutes)) & trades.arm.eq("v9_both")
                           & trades["_closed"] & trades["_fold"].eq("earlier")
                           & np.isfinite(trades["pre12_opp"]), "pre12_opp"]
        q1, q2, n_train_float = _tercile_thresholds(train)
        n_train = int(n_train_float) if np.isfinite(n_train_float) else 0
        base = {"symbol": eth_symbol, "arm": "v9_both", "timeframe_min": int(minutes),
                "feature": "pre12_opp", "early_closed_n": n_train, "later_extreme_n": 0,
                "later_low_n": 0, "later_high_n": 0, "q1_tercile": q1, "q2_tercile": q2,
                "win_diff_high_minus_low": math.nan, "net_bp_diff_high_minus_low": math.nan,
                "win_bootstrap_ci_low": math.nan, "win_bootstrap_ci_high": math.nan,
                "net_bp_bootstrap_ci_low": math.nan, "net_bp_bootstrap_ci_high": math.nan,
                "win_bootstrap_valid_draws": 0, "net_bp_bootstrap_valid_draws": 0,
                "win_permutation_p": math.nan, "net_bp_permutation_p": math.nan,
                "win_permutation_shufflable_n": 0, "net_bp_permutation_shufflable_n": 0,
                "win_permutation_coverage": 0.0, "net_bp_permutation_coverage": 0.0,
                "permutation_strata_n": 0, "permutation_draws": permutation_draws,
                "primary_status": "insufficient_early_closed_n" if n_train < 3 else "insufficient_later_high_low"}
        if n_train >= 3:
            rows["_bucket"] = _bucket(rows.pre12_opp, q1, q2)
            rows["_net_win"] = rows.net_return.gt(0).astype(float)
            rows["_net_bp"] = rows.net_return * 10000
            extreme = rows.loc[rows["_bucket"].isin(("low", "high"))].copy()
            base.update(later_extreme_n=int(len(extreme)),
                        later_low_n=int(extreme["_bucket"].eq("low").sum()),
                        later_high_n=int(extreme["_bucket"].eq("high").sum()),
                        win_diff_high_minus_low=_difference(extreme, "_net_win"),
                        net_bp_diff_high_minus_low=_difference(extreme, "_net_bp"))
            win_boot = _cluster_bootstrap(extreme, "_net_win", bootstrap_draws, base_seed + int(minutes) * 11)
            bp_boot = _cluster_bootstrap(extreme, "_net_bp", bootstrap_draws, base_seed + int(minutes) * 13)
            base.update(win_bootstrap_ci_low=win_boot["ci_low"], win_bootstrap_ci_high=win_boot["ci_high"],
                        win_bootstrap_valid_draws=win_boot["valid_draws"],
                        net_bp_bootstrap_ci_low=bp_boot["ci_low"], net_bp_bootstrap_ci_high=bp_boot["ci_high"],
                        net_bp_bootstrap_valid_draws=bp_boot["valid_draws"])
            win_perm = _stratified_permutation(extreme, "_net_win", permutation_draws, base_seed + int(minutes) * 17)
            bp_perm = _stratified_permutation(extreme, "_net_bp", permutation_draws, base_seed + int(minutes) * 19)
            base.update(win_permutation_p=win_perm["p_value"], net_bp_permutation_p=bp_perm["p_value"],
                        win_permutation_shufflable_n=win_perm["shufflable_n"],
                        net_bp_permutation_shufflable_n=bp_perm["shufflable_n"],
                        win_permutation_coverage=win_perm["coverage"],
                        net_bp_permutation_coverage=bp_perm["coverage"],
                        permutation_strata_n=min(win_perm["strata_n"], bp_perm["strata_n"]))
            if not len(extreme.loc[extreme["_bucket"].eq("low")]) or not len(extreme.loc[extreme["_bucket"].eq("high")]):
                base["primary_status"] = "insufficient_later_high_low"
            elif permutation_draws <= 0:
                base["primary_status"] = "insufficient_no_permutation_draws"
            elif min(win_perm["strata_n"], bp_perm["strata_n"]) == 0:
                base["primary_status"] = "insufficient_shufflable_blocks"
            else:
                base["primary_status"] = "ready"
        output.append(base)
    result = pd.DataFrame(output)
    if len(result):
        family_n = len(timeframes)
        result["holm_family_n"] = family_n
        result["win_permutation_p_holm"] = _holm(result.win_permutation_p, family_n)
        result["net_bp_permutation_p_holm"] = _holm(result.net_bp_permutation_p, family_n)
    return result


def run(run_dir: str | Path, config: Mapping[str, Any] | str | Path,
        output: str | Path) -> dict[str, Any]:
    """Build cohort CSVs and an input/source receipt from a complete parent run."""
    run_path, output_path = Path(run_dir), Path(output)
    output_path.mkdir(parents=True, exist_ok=True)
    expected_outputs = ("cohort_metrics.csv", "thresholds.csv", "feature_diagnostics.csv",
                        "primary_inference.csv", "side_metrics.csv", "receipt.json")
    existing = [name for name in expected_outputs if (output_path / name).exists()]
    if existing:
        raise FileExistsError(f"statistics output already exists; choose a new directory: {existing}")
    cfg, config_hash, config_path = _load_config(config)
    trades_raw, controls_raw, input_hashes, run_meta = _load_streams(run_path, cfg)
    trades = _prepare_trades(trades_raw, cfg)
    controls = _prepare_controls(controls_raw)
    metrics, thresholds, diagnostics, side_metrics = _feature_tables(trades, controls, cfg)
    primary = _primary_inference(trades, cfg)
    tables = {
        "cohort_metrics.csv": metrics,
        "thresholds.csv": thresholds,
        "feature_diagnostics.csv": diagnostics,
        "primary_inference.csv": primary,
        "side_metrics.csv": side_metrics,
    }
    outputs = {}
    for name, table in tables.items():
        path = output_path / name
        with path.open("x", encoding="utf-8", newline="") as handle:
            table.to_csv(handle, index=False, float_format="%.12g", na_rep="")
        outputs[name] = {"sha256": _sha256(path), "rows": int(len(table))}
    source_hashes = []
    test_name = "tests/evaluation/test_spike_v128_bb_slope_stats.py"
    for name in ("yoyo/evaluation/spike_v128_bb_slope_stats.py", test_name, *SOURCE_FILES,
                 *cfg.get("source_files", [])):
        path = REPO_ROOT / name
        if path.is_file():
            source_hashes.append({"path": str(path), "sha256": _sha256(path)})
    git_commit_result = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, capture_output=True, text=True, check=False
    )
    git_commit = git_commit_result.stdout.strip() if git_commit_result.returncode == 0 else None
    module_commit_result = subprocess.run(
        ["git", "log", "-1", "--format=%H", "--", "yoyo/evaluation/spike_v128_bb_slope_stats.py"],
        cwd=REPO_ROOT, capture_output=True, text=True, check=False
    )
    module_commit = module_commit_result.stdout.strip() if module_commit_result.returncode == 0 else None
    test_path = REPO_ROOT / test_name
    receipt = {
        "version": VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "statistics_git_commit": git_commit,
        "statistics_file_commit": module_commit,
        "statistics_source_sha256": _sha256(REPO_ROOT / "yoyo/evaluation/spike_v128_bb_slope_stats.py"),
        "statistics_test_source_sha256": _sha256(test_path) if test_path.is_file() else None,
        "config_sha256": config_hash,
        "config_path": config_path,
        "run_dir": str(run_path),
        "run_identity": run_meta["manifest"].get("run_identity"),
        "run_identity_sha256": run_meta["identity_sha256"],
        "run_manifest_sha256": run_meta["manifest_sha256"],
        "stream_receipts": run_meta["stream_receipts"],
        "inputs": input_hashes,
        "sources": source_hashes,
        "outputs": outputs,
        "trade_rows_in_window": int(len(trades)),
        "closed_trade_rows": int(trades["_closed"].sum()),
        "censored_trade_rows": int(trades["_censored"].sum()),
        "fold_counts": {str(key): int(value) for key, value in trades["_fold"].value_counts().items()},
        "primary_definition": "ETH v9_both pre12_opp later high-minus-low tercile; thresholds from closed wholly earlier trades; UTC signal-week cluster bootstrap; side x UTC signal-week permutation; Holm separately across configured timeframes",
        "return_units": {"gross_return": "fraction of notional", "net_return": "fraction of notional",
                         "basis_points": "return fraction * 10000", "net_r": "net return / initial_risk_frac"},
        "threshold_rule": "linear q=1/3 and q=2/3 from closed earlier trades only; low <= q1, middle <= q2, high > q2; top10 diagnostic uses early q90",
        "control_rule": "only matched, closed targets with finite control return; earlier/later require control signal and exit to stay within the same fold",
    }
    receipt_path = output_path / "receipt.json"
    with receipt_path.open("x", encoding="utf-8") as handle:
        handle.write(json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False) + "\n")
    return receipt


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", required=True, help="V12.8 run directory containing streams/")
    parser.add_argument("--config", required=True, help="Frozen JSON config path")
    parser.add_argument("--output", required=True, help="Summary output directory")
    args = parser.parse_args(argv)
    receipt = run(args.run, args.config, args.output)
    print(json.dumps({"version": receipt["version"], "outputs": receipt["outputs"],
                      "closed_trade_rows": receipt["closed_trade_rows"],
                      "censored_trade_rows": receipt["censored_trade_rows"]}, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
