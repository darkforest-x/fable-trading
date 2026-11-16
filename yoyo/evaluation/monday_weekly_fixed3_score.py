"""Score the fixed-3%-risk near-edge Monday-wick outcomes.

Training labels come from the fixed-stop experiment's ``outcomes.csv.gz``.
All 40 frozen close-time features are retained; the legacy
``signal_close_risk_frac`` slot is set to the experiment's fixed 3% risk, while
the wick-shape features remain unchanged. Decision-time reward geometry uses
the signal candle close as a proxy entry, the known near Monday edge as target,
and the configured maker/taker fees. Realized ``net_r`` labels still use the
actual next-H1 entry and fixed-3% stop supplied by the outcome ledger.

Run after the outcome ledger exists and this builder is committed:

    .venv/bin/python -m yoyo.evaluation.monday_weekly_fixed3_score

Score prepared feature rows with the frozen 2026 models:

    .venv/bin/python -m yoyo.evaluation.monday_weekly_fixed3_score --score rows.csv
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from time import perf_counter
from typing import Any, Mapping

import joblib
import lightgbm as lgb
import numpy as np
import pandas as pd
import sklearn
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
from threadpoolctl import threadpool_limits

from yoyo.evaluation import monthweek_wick_score3r as shared
from yoyo.evaluation.monday_weekly_wick_features import FEATURES


ROOT = Path(__file__).resolve().parents[2]
EXPERIMENT_ID = "exp-binance-monday-wick-fixed3-near-20261116-v1"
EXP = ROOT / "experiments/active" / EXPERIMENT_ID
CONFIG_FILE = EXP / "config.json"
OUTCOME_FILE = EXP / "outcomes.csv.gz"
FROZEN_FEATURES = "monday_weekly_wick_features.csv.gz"
FROZEN_BREACHES = "first_breaches.csv.gz"
STOP_FEATURE = "signal_close_risk_frac"
SETTLED = ("tp", "sl")
RESOLUTION_MS = {
    "h1": 3_600_000,
    "h1_fallback": 3_600_000,
    "1m": 60_000,
    "1m_stop_first": 60_000,
}
RANKING_ORDER = ("win_probability", "expected_net_r", "balanced_percentiles")


def write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n")


def json_value(value: Any) -> Any:
    """Convert numpy/pandas values and non-finite numbers to JSON-safe values."""
    if isinstance(value, Mapping):
        return {str(k): json_value(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_value(v) for v in value]
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating, float)):
        return float(value) if np.isfinite(value) else None
    if isinstance(value, (np.bool_, bool)):
        return bool(value)
    if value is pd.NA or value is pd.NaT:
        return None
    return value


def year_start(year: int) -> int:
    return int(pd.Timestamp(f"{year}-01-01", tz="UTC").timestamp() * 1000)


def load_config() -> dict[str, Any]:
    if not CONFIG_FILE.is_file():
        raise FileNotFoundError(f"Experiment config is missing: {CONFIG_FILE}")
    cfg = json.loads(CONFIG_FILE.read_text())
    if cfg.get("id") != EXPERIMENT_ID:
        raise ValueError("Experiment config id does not match this builder")
    if float(cfg.get("stop_frac", np.nan)) != 0.03:
        raise ValueError("This builder requires the frozen fixed stop_frac=0.03")
    if tuple(cfg.get("ranking", ())) != RANKING_ORDER:
        raise ValueError(f"Unexpected ranking order: {cfg.get('ranking')}")
    if tuple(cfg.get("candidates", ())) != ("logit", "lgb_d3"):
        raise ValueError(f"Unexpected candidates: {cfg.get('candidates')}")
    if tuple(float(x) for x in cfg.get("high_fractions", ())) != (0.2, 0.4):
        raise ValueError(f"Unexpected high_fractions: {cfg.get('high_fractions')}")
    return cfg


def source_experiment(cfg: Mapping[str, Any]) -> Path:
    """Resolve the original frozen feature source through the near-label parent."""
    parent = ROOT / "experiments/active" / str(cfg["parent"])
    parent_config = parent / "config.json"
    if not parent_config.is_file():
        raise FileNotFoundError(f"Near-label parent config is missing: {parent_config}")
    parent_cfg = json.loads(parent_config.read_text())
    source_id = parent_cfg.get("parent")
    if not source_id:
        raise ValueError("Near-label parent config has no frozen feature source id")
    return ROOT / "experiments/active" / str(source_id)


def _assert_frozen_identity(features: pd.DataFrame, breaches: pd.DataFrame) -> None:
    if features.event_id.duplicated().any() or breaches.event_id.duplicated().any():
        raise ValueError("Frozen feature and breach files must have unique event_id values")
    check_cols = ["event_id", "week", "side", "tf", "close_ms"]
    missing = (set(check_cols) - set(features.columns)) | (set(check_cols) - set(breaches.columns))
    if missing:
        raise ValueError(f"Frozen feature/breach files are missing identity fields: {sorted(missing)}")
    # The census contains the full event universe, while the frozen feature
    # ledger is the confirmed subset. Require every feature event to have one
    # matching census row; extra census rows are expected and ignored.
    check = features[check_cols].merge(
        breaches[check_cols], on="event_id", suffixes=("_feature", "_breach"),
        how="left", validate="one_to_one", indicator=True,
    )
    if not check._merge.eq("both").all():
        raise ValueError("Some frozen feature event ids are absent from first-breach metadata")
    if not check.week_feature.astype(str).equals(check.week_breach.astype(str)):
        raise ValueError("Frozen feature and breach metadata disagree on week")
    for column in ("side", "tf", "close_ms"):
        left = pd.to_numeric(check[f"{column}_feature"], errors="raise").to_numpy()
        right = pd.to_numeric(check[f"{column}_breach"], errors="raise").to_numpy()
        if not np.array_equal(left, right):
            raise ValueError(f"Frozen feature and breach metadata disagree on {column}")


def add_geometry(rows: pd.DataFrame, cfg: Mapping[str, Any]) -> pd.DataFrame:
    """Attach close-proxy near-target and fixed-3%-stop net R geometry.

    The proxy entry is the confirmed signal bar close. Actual outcome labels
    remain based on the separate next-H1 actual entry in ``outcomes.csv.gz``.
    """
    result = rows.copy()
    required = {"side", "bar_close", "week_low", "week_high"}
    missing = required.difference(result.columns)
    if missing:
        raise ValueError(f"Cannot calculate close-proxy reward geometry; missing {sorted(missing)}")
    side = pd.to_numeric(result.side, errors="raise").to_numpy(dtype=np.float64)
    proxy_entry = pd.to_numeric(result.bar_close, errors="coerce").to_numpy(dtype=np.float64)
    week_low = pd.to_numeric(result.week_low, errors="coerce").to_numpy(dtype=np.float64)
    week_high = pd.to_numeric(result.week_high, errors="coerce").to_numpy(dtype=np.float64)
    if not np.isin(side, (-1.0, 1.0)).all():
        raise ValueError("side must be -1 or +1")
    target = np.where(side > 0, week_low, week_high)
    stop_frac = float(cfg["stop_frac"])
    risk = proxy_entry * stop_frac
    stop = proxy_entry * (1.0 - side * stop_frac)
    entry_fee_rate = float(cfg["fees"]["entry_taker"])
    stop_fee_rate = float(cfg["fees"]["stop_exit_taker"])
    target_fee_rate = float(cfg["fees"]["tp_exit_maker"])
    entry_fee = proxy_entry * entry_fee_rate
    stop_fee = stop * stop_fee_rate
    target_fee = target * target_fee_rate
    with np.errstate(divide="ignore", invalid="ignore"):
        stop_net_r = (side * (stop - proxy_entry) - entry_fee - stop_fee) / risk
        near_potential_net_r = (
            side * (target - proxy_entry) - entry_fee - target_fee
        ) / risk
    invalid = (
        ~np.isfinite(proxy_entry) | ~np.isfinite(target) | ~np.isfinite(risk)
        | (proxy_entry <= 0) | (target <= 0) | (risk <= 0)
        | ~np.isfinite(near_potential_net_r) | ~np.isfinite(stop_net_r)
    )
    near_potential_net_r[invalid] = np.nan
    stop_net_r[invalid] = np.nan
    result["proxy_entry"] = proxy_entry
    result["proxy_stop"] = stop
    result["near_target_price"] = target
    result["near_potential_net_r"] = near_potential_net_r
    result["stop_net_r"] = stop_net_r
    return result


def load_rows(cfg: Mapping[str, Any]) -> pd.DataFrame:
    source = source_experiment(cfg)
    feature_file = source / FROZEN_FEATURES
    breach_file = source / FROZEN_BREACHES
    for path in (feature_file, breach_file, OUTCOME_FILE):
        if not path.is_file():
            raise FileNotFoundError(f"Required experiment input is missing: {path}")
    features = pd.read_csv(feature_file)
    breaches = pd.read_csv(breach_file)
    outcomes = pd.read_csv(OUTCOME_FILE)
    _assert_frozen_identity(features, breaches)
    missing_features = set(FEATURES).difference(features.columns)
    if missing_features:
        raise ValueError(f"Frozen feature file lacks model columns: {sorted(missing_features)}")
    if "signal_close_risk_frac" not in FEATURES:
        raise ValueError("Expected legacy signal_close_risk_frac feature is absent")
    if "event_id" not in outcomes or outcomes.event_id.duplicated().any():
        raise ValueError("outcomes.csv.gz must contain exactly one row per event_id")
    required_outcomes = {"event_id", "outcome", "net_r", "exit_ms", "resolution"}
    missing = required_outcomes.difference(outcomes.columns)
    if missing:
        raise ValueError(f"outcomes.csv.gz is missing fields: {sorted(missing)}")
    if not set(outcomes.event_id).issubset(set(features.event_id)):
        raise ValueError("outcomes.csv.gz contains event ids absent from frozen features")

    metadata_cols = ["event_id", "week_high", "week_low", "bar_close", "tip"]
    absent_meta = set(metadata_cols).difference(breaches.columns)
    if absent_meta:
        raise ValueError(f"first_breaches.csv.gz is missing geometry fields: {sorted(absent_meta)}")
    rows = features.merge(
        breaches[metadata_cols], on="event_id", how="left", validate="one_to_one",
    )
    if rows[["week_high", "week_low", "bar_close"]].isna().any().any():
        raise ValueError("Some frozen features lack signal-close/near-edge geometry")
    if "bar_close" in features.columns:
        left = pd.to_numeric(rows.bar_close_x, errors="raise").to_numpy()
        right = pd.to_numeric(rows.bar_close_y, errors="raise").to_numpy()
        if not np.array_equal(left, right):
            raise ValueError("Frozen feature and breach close prices disagree")
        rows = rows.rename(columns={"bar_close_y": "breach_bar_close"})
        rows["bar_close"] = rows["bar_close_x"]
        rows = rows.drop(columns=["bar_close_x"])
    rows = rows.merge(outcomes, on="event_id", how="left", validate="one_to_one", suffixes=("", "_outcome"))
    rows["close_ms"] = pd.to_numeric(rows.close_ms, errors="raise").astype("int64")
    rows["tf"] = pd.to_numeric(rows.tf, errors="raise").astype("int64")
    rows["side"] = pd.to_numeric(rows.side, errors="raise").astype("int64")
    rows["net_r"] = pd.to_numeric(rows.net_r, errors="coerce")
    rows["exit_ms"] = pd.to_numeric(rows.exit_ms, errors="coerce")
    if set(rows.tf.unique()) != {1, 4, 12}:
        raise ValueError(f"Unexpected timeframes: {sorted(rows.tf.unique())}")

    if "eligible" in rows.columns:
        rows["eligible"] = rows.eligible.fillna(False).astype(bool)
    else:
        # Missing outcome rows are known non-executable for this target (for
        # example, the near edge was already beyond the actual next-H1 entry).
        rows["eligible"] = rows.outcome.notna()
    settled = rows.outcome.isin(SETTLED)
    if not set(rows.loc[rows.eligible & rows.outcome.notna(), "outcome"].unique()).issubset(
        {*SETTLED, "censored"}
    ):
        raise ValueError("Eligible outcomes must be tp, sl or censored")
    duration = rows.resolution.map(RESOLUTION_MS)
    if duration[settled].isna().any():
        raise ValueError("Settled outcome has an unknown resolution")
    rows["known_ms"] = np.nan
    rows.loc[settled, "known_ms"] = rows.loc[settled, "exit_ms"] + duration[settled]
    if rows.loc[settled, "known_ms"].isna().any():
        raise ValueError("Settled outcomes need exit_ms/resolution or explicit known_ms")
    if (rows.loc[settled, "known_ms"] < rows.loc[settled, "close_ms"]).any():
        raise ValueError("Outcome label is known before its signal close")

    # The old column's value described wick-relative risk and is not meaningful
    # for this fixed stop. Keep the 40-column schema but give it the new constant.
    rows[STOP_FEATURE] = float(cfg["stop_frac"])
    rows = add_geometry(rows, cfg)
    return rows.sort_values(["close_ms", "event_id"], kind="mergesort").reset_index(drop=True)


def matrix(rows: pd.DataFrame) -> pd.DataFrame:
    return rows[list(FEATURES)].astype("float64").replace([np.inf, -np.inf], np.nan)


def mature_rows(rows: pd.DataFrame, cutoff: int) -> pd.DataFrame:
    return rows[
        rows.eligible
        & rows.close_ms.lt(cutoff)
        & rows.outcome.isin(SETTLED)
        & rows.net_r.notna()
        & rows.known_ms.le(cutoff)
        & rows.near_potential_net_r.notna()
    ].copy()


def candidate_model(name: str, seed: int):
    if name == "logit":
        return make_pipeline(
            SimpleImputer(strategy="median"),
            StandardScaler(),
            LogisticRegression(C=1, max_iter=1500, tol=1e-6),
        )
    if name == "lgb_d3":
        return lgb.LGBMClassifier(
            objective="binary", max_depth=3, num_leaves=8, n_estimators=150,
            min_child_samples=50, learning_rate=0.04, reg_lambda=3.0,
            colsample_bytree=0.85, subsample=0.9, subsample_freq=1,
            random_state=seed, n_jobs=3, verbosity=-1, deterministic=True,
            force_col_wise=True,
        )
    raise ValueError(f"Unsupported candidate: {name}")


def empirical_percentile(reference: np.ndarray, values: np.ndarray) -> np.ndarray:
    """Training-reference midrank CDF, with no validation/test refitting."""
    ref = np.sort(np.asarray(reference, dtype=np.float64))
    val = np.asarray(values, dtype=np.float64)
    left = np.searchsorted(ref, val, side="left")
    right = np.searchsorted(ref, val, side="right")
    result = (left + right) / (2.0 * len(ref))
    result[~np.isfinite(val)] = np.nan
    return result


def score_parts(bundle: Mapping[str, Any], rows: pd.DataFrame) -> pd.DataFrame:
    if not rows.tf.eq(int(bundle["tf"])).all():
        raise ValueError("Timeframe does not match fitted model")
    p = bundle["estimator"].predict_proba(matrix(rows))[:, 1]
    potential = rows.near_potential_net_r.to_numpy(dtype=np.float64)
    stop_net_r = rows.stop_net_r.to_numpy(dtype=np.float64)
    expected = p * potential + (1.0 - p) * stop_net_r
    p_pct = empirical_percentile(np.asarray(bundle["reference_p"]), p)
    reward_pct = empirical_percentile(np.asarray(bundle["reference_potential"]), potential)
    balanced = np.minimum(p_pct, reward_pct)
    return pd.DataFrame({
        "p": p,
        "expected_net_r": expected,
        "balanced_percentile": balanced,
        "p_percentile": p_pct,
        "potential_percentile": reward_pct,
    }, index=rows.index)


def score_for(parts: pd.DataFrame, ranking: str) -> np.ndarray:
    column = {
        "win_probability": "p",
        "expected_net_r": "expected_net_r",
        "balanced_percentiles": "balanced_percentile",
    }[ranking]
    return parts[column].to_numpy(dtype=np.float64)


def score_key(fraction: float) -> str:
    return f"{fraction:.2f}"


def quantile_cut(values: np.ndarray, fraction: float) -> float:
    finite = np.asarray(values, dtype=np.float64)
    finite = finite[np.isfinite(finite)]
    if not len(finite):
        raise ValueError("Cannot establish a score threshold from an empty fit sample")
    return float(np.quantile(finite, 1.0 - fraction))


def fit_model(rows: pd.DataFrame, candidate: str, cutoff: int, cfg: Mapping[str, Any]) -> dict[str, Any]:
    train = mature_rows(rows, cutoff)
    if train.tf.nunique() != 1 or train.net_r.gt(0).nunique() != 2:
        raise ValueError(
            f"Fit requires both profitable and losing settled labels in one timeframe; "
            f"tf={sorted(train.tf.unique())}, n={len(train)}"
        )
    estimator = candidate_model(candidate, int(cfg["seed"]))
    x = matrix(train)
    y = train.net_r.gt(0).astype("int8")
    estimator.fit(x, y)
    p_train = estimator.predict_proba(x)[:, 1]
    potential_train = train.near_potential_net_r.to_numpy(dtype=np.float64)
    stop_train = train.stop_net_r.to_numpy(dtype=np.float64)
    expected_train = p_train * potential_train + (1.0 - p_train) * stop_train
    balanced_train = np.minimum(
        empirical_percentile(p_train, p_train),
        empirical_percentile(potential_train, potential_train),
    )
    training_scores = {
        "win_probability": p_train,
        "expected_net_r": expected_train,
        "balanced_percentiles": balanced_train,
    }
    thresholds = {
        ranking: {
            score_key(float(fraction)): quantile_cut(scores, float(fraction))
            for fraction in cfg["high_fractions"]
        }
        for ranking, scores in training_scores.items()
    }
    geometry_thresholds = {
        score_key(float(fraction)): quantile_cut(potential_train, float(fraction))
        for fraction in cfg["high_fractions"]
    }
    return {
        "tf": int(train.tf.iloc[0]),
        "candidate": candidate,
        "estimator": estimator,
        "features": list(FEATURES),
        "train_n": int(len(train)),
        "train_profitable": int(y.sum()),
        "train_win_rate": float(y.mean()),
        "train_mean_net_r": float(train.net_r.mean()),
        "train_until_exclusive_ms": int(cutoff),
        "reference_p": p_train.astype(float),
        "reference_potential": potential_train.astype(float),
        "score_thresholds": thresholds,
        "geometry_thresholds": geometry_thresholds,
        "fractions": [float(x) for x in cfg["high_fractions"]],
        "ranking_order": list(RANKING_ORDER),
        "stop_frac": float(cfg["stop_frac"]),
        "fees": dict(cfg["fees"]),
    }


def set_bands(
    rows: pd.DataFrame,
    parts: pd.DataFrame,
    bundle: Mapping[str, Any],
    ranking: str,
    fraction: float,
) -> pd.DataFrame:
    result = rows.copy()
    score = score_for(parts, ranking)
    threshold = float(bundle["score_thresholds"][ranking][score_key(fraction)])
    geometry_threshold = float(bundle["geometry_thresholds"][score_key(fraction)])
    eligible = result.eligible.to_numpy(dtype=bool)
    result["score"] = score
    result["p"] = parts.p.to_numpy(dtype=np.float64)
    result["expected_net_r"] = parts.expected_net_r.to_numpy(dtype=np.float64)
    result["band"] = np.where(eligible & (score >= threshold), "top", "rest")
    result["baseline_band"] = np.where(
        eligible & result.near_potential_net_r.ge(geometry_threshold), "top", "rest"
    )
    result["score_threshold"] = threshold
    result["geometry_threshold"] = geometry_threshold
    result["ranking_method"] = ranking
    result["selection_fraction"] = float(fraction)
    result["candidate"] = str(bundle["candidate"])
    return result


def economics(group: pd.DataFrame) -> dict[str, Any]:
    settled = group[group.outcome.isin(SETTLED) & group.net_r.notna()]
    if settled.empty:
        return {
            "n": 0, "wins": 0, "losses": 0, "win_rate": None,
            "payoff_ratio": None, "mean_net_r": None,
            "mean_win_r": None, "mean_loss_abs_r": None,
        }
    values = settled.net_r.to_numpy(dtype=np.float64)
    wins = values[values > 0]
    losses = values[values < 0]
    win_rate = float(np.mean(values > 0))
    mean_win = float(wins.mean()) if len(wins) else 0.0
    mean_loss = float(-losses.mean()) if len(losses) else None
    payoff = mean_win / mean_loss if mean_loss and mean_loss > 0 else None
    return {
        "n": int(len(settled)),
        "wins": int(len(wins)),
        "losses": int(len(losses)),
        "win_rate": win_rate,
        "payoff_ratio": payoff,
        "mean_net_r": float(values.mean()),
        "mean_win_r": mean_win if len(wins) else None,
        "mean_loss_abs_r": mean_loss,
    }


def safe_ratio(numerator: Any, denominator: Any) -> float | None:
    if numerator is None or denominator is None:
        return None
    a, b = float(numerator), float(denominator)
    if not np.isfinite([a, b]).all() or b <= 0:
        return None
    return a / b


def comparison_metrics(selected: pd.DataFrame, baseline: pd.DataFrame) -> dict[str, Any]:
    top_stats = economics(selected)
    base_stats = economics(baseline)
    win_ratio = safe_ratio(top_stats["win_rate"], base_stats["win_rate"])
    payoff_ratio = safe_ratio(top_stats["payoff_ratio"], base_stats["payoff_ratio"])
    score = min(win_ratio, payoff_ratio) if win_ratio is not None and payoff_ratio is not None else None
    return {
        **top_stats,
        "all_n": base_stats["n"],
        "all_win_rate": base_stats["win_rate"],
        "all_payoff_ratio": base_stats["payoff_ratio"],
        "win_rate_ratio_vs_all": win_ratio,
        "payoff_ratio_vs_all": payoff_ratio,
        "selection_score": score,
        "all_mean_net_r": base_stats["mean_net_r"],
    }


def matched_inference(group: pd.DataFrame, cfg: Mapping[str, Any], selection_column: str) -> dict[str, Any]:
    settled = group[group.eligible & group.outcome.isin(SETTLED) & group.net_r.notna()].copy()
    if settled.empty or not settled[selection_column].eq("top").any():
        return {"settled_selected": 0, "matched_signal_mean_net_r": None, "one_sided_p": None, "weeks": 0}
    settled["month"] = settled.week.astype(str)
    settled["band"] = np.where(settled[selection_column].eq("top"), "top10", "rest")
    result = shared.inference(settled, {"draws": int(cfg["draws"]), "seed": int(cfg["seed"])})
    if "months" in result:
        result["weeks"] = result.pop("months")
    if "high_month_bootstrap_95" in result:
        result["week_bootstrap_95"] = result.pop("high_month_bootstrap_95")
    if "excess_month_bootstrap_95" in result:
        result["excess_week_bootstrap_95"] = result.pop("excess_month_bootstrap_95")
    return result


def validation_folds(rows: pd.DataFrame, cfg: Mapping[str, Any]) -> tuple[pd.DataFrame, pd.DataFrame]:
    records: list[dict[str, Any]] = []
    test_start = int(pd.Timestamp(cfg["test_start"]).timestamp() * 1000)
    for tf in cfg["timeframes"]:
        group = rows[rows.tf.eq(int(tf))].copy()
        for candidate in cfg["candidates"]:
            for year in cfg["validation_years"]:
                start, end = year_start(int(year)), year_start(int(year) + 1)
                bundle = fit_model(group, str(candidate), start, cfg)
                val = group[group.close_ms.ge(start) & group.close_ms.lt(end)].copy()
                parts = score_parts(bundle, val)
                val = val.join(parts)
                eval_rows = val[
                    val.eligible & val.outcome.isin(SETTLED) & val.net_r.notna()
                    & val.known_ms.le(test_start)
                ].copy()
                all_stats = economics(eval_rows)
                for ranking in cfg["ranking"]:
                    score_values = score_for(parts, str(ranking))
                    threshold_scores = bundle["score_thresholds"][str(ranking)]
                    for fraction_value in cfg["high_fractions"]:
                        fraction = float(fraction_value)
                        threshold = float(threshold_scores[score_key(fraction)])
                        # The score vector follows validation-frame row order;
                        # align it once by index for this held-out year's labels.
                        rank_score = pd.Series(score_values, index=val.index)
                        selected = eval_rows.loc[rank_score.loc[eval_rows.index].ge(threshold)]
                        metrics = comparison_metrics(selected, eval_rows)
                        geometry_cut = float(bundle["geometry_thresholds"][score_key(fraction)])
                        geometry_selected = eval_rows[
                            eval_rows.near_potential_net_r.ge(geometry_cut)
                        ]
                        geometry_metrics = comparison_metrics(geometry_selected, eval_rows)
                        records.append({
                            "tf": int(tf), "candidate": str(candidate), "year": int(year),
                            "ranking_method": str(ranking), "selection_fraction": fraction,
                            "train_n": int(bundle["train_n"]),
                            "train_profitable": int(bundle["train_profitable"]),
                            "fit_until_exclusive_ms": int(start),
                            "score_threshold": threshold,
                            "geometry_threshold": geometry_cut,
                            "validation_signals": int(val.eligible.sum()),
                            "validation_settled_by_2026": all_stats["n"],
                            "high_n": metrics["n"], "high_win_rate": metrics["win_rate"],
                            "all_win_rate": all_stats["win_rate"],
                            "win_rate_ratio": metrics["win_rate_ratio_vs_all"],
                            "high_payoff_ratio": metrics["payoff_ratio"],
                            "all_payoff_ratio": all_stats["payoff_ratio"],
                            "payoff_ratio_ratio": metrics["payoff_ratio_vs_all"],
                            "selection_score": metrics["selection_score"],
                            "high_mean_net_r": metrics["mean_net_r"],
                            "all_mean_net_r": all_stats["mean_net_r"],
                            "geometry_high_n": geometry_metrics["n"],
                            "geometry_win_rate_ratio": geometry_metrics["win_rate_ratio_vs_all"],
                            "geometry_payoff_ratio_ratio": geometry_metrics["payoff_ratio_vs_all"],
                            "geometry_selection_score": geometry_metrics["selection_score"],
                            "geometry_high_mean_net_r": geometry_metrics["mean_net_r"],
                        })
    folds = pd.DataFrame(records)
    if folds.empty:
        raise ValueError("No validation folds were generated")
    candidate_order = {str(name): i for i, name in enumerate(cfg["candidates"])}
    ranking_order = {str(name): i for i, name in enumerate(cfg["ranking"])}
    fraction_order = {float(value): i for i, value in enumerate(cfg["high_fractions"])}
    stats = folds.groupby(
        ["tf", "candidate", "ranking_method", "selection_fraction"], sort=False, dropna=False
    ).agg(
        validation_years=("year", "nunique"),
        mean_year_selection_score=("selection_score", "mean"),
        valid_score_years=("selection_score", "count"),
        min_year_selection_score=("selection_score", "min"),
        mean_year_win_rate_ratio=("win_rate_ratio", "mean"),
        mean_year_payoff_ratio_ratio=("payoff_ratio_ratio", "mean"),
        mean_high_net_r=("high_mean_net_r", "mean"),
        mean_high_n=("high_n", "mean"),
        mean_geometry_selection_score=("geometry_selection_score", "mean"),
        mean_geometry_high_net_r=("geometry_high_mean_net_r", "mean"),
    ).reset_index()
    expected_years = len(cfg["validation_years"])
    if not stats.validation_years.eq(expected_years).all():
        raise ValueError("Every model/ranking/fraction needs all validation years")
    stats["candidate_order"] = stats.candidate.map(candidate_order)
    stats["ranking_order"] = stats.ranking_method.map(ranking_order)
    stats["fraction_order"] = stats.selection_fraction.map(fraction_order)
    stats["selected"] = False
    winners: dict[str, dict[str, Any]] = {}
    for tf, group in stats.groupby("tf", sort=True):
        viable = group[
            group.valid_score_years.eq(expected_years)
            & group.mean_year_selection_score.notna()
            & group.mean_high_net_r.notna()
        ]
        if viable.empty:
            raise ValueError(f"No valid 2023-2025 candidate combination for {tf}h")
        ranked = viable.sort_values(
            ["mean_year_selection_score", "mean_high_net_r", "candidate_order", "ranking_order", "fraction_order"],
            ascending=[False, False, True, True, True], kind="mergesort",
        )
        chosen_idx = ranked.index[0]
        stats.loc[chosen_idx, "selected"] = True
        winner = stats.loc[chosen_idx]
        winners[str(int(tf))] = {
            "candidate": str(winner.candidate),
            "ranking_method": str(winner.ranking_method),
            "selection_fraction": float(winner.selection_fraction),
            "mean_year_selection_score": float(winner.mean_year_selection_score),
            "min_year_selection_score": float(winner.min_year_selection_score),
            "mean_high_net_r": float(winner.mean_high_net_r),
            "candidate_order": int(winner.candidate_order),
        }
    return folds, stats, winners


def final_test(rows: pd.DataFrame, cfg: Mapping[str, Any], winners: Mapping[str, Mapping[str, Any]]):
    test_start = int(pd.Timestamp(cfg["test_start"]).timestamp() * 1000)
    cutoff = int(pd.Timestamp(cfg["cutoff_exclusive"]).timestamp() * 1000)
    test_rows: list[pd.DataFrame] = []
    model_results: dict[str, Any] = {}
    bundles: dict[str, dict[str, Any]] = {}
    for tf in cfg["timeframes"]:
        key = str(int(tf))
        chosen = winners[key]
        group = rows[rows.tf.eq(int(tf))].copy()
        bundle = fit_model(group, str(chosen["candidate"]), test_start, cfg)
        test = group[group.close_ms.ge(test_start) & group.close_ms.lt(cutoff)].copy()
        parts = score_parts(bundle, test)
        scored = set_bands(test, parts, bundle, str(chosen["ranking_method"]), float(chosen["selection_fraction"]))
        scored["eligible"] = scored.eligible.astype(bool)
        scored["known_by_cutoff"] = scored.known_ms.le(cutoff) & scored.outcome.isin(SETTLED)
        eval_rows = scored[
            scored.eligible & scored.known_by_cutoff & scored.net_r.notna()
        ].copy()
        all_stats = economics(eval_rows)
        selected = eval_rows[eval_rows.band.eq("top")]
        geometry = eval_rows[eval_rows.baseline_band.eq("top")]
        model_file = f"model_{int(tf)}h.joblib"
        bundle.update(
            ranking_method=str(chosen["ranking_method"]),
            selection_fraction=float(chosen["selection_fraction"]),
            model_file=model_file,
            sklearn_version=sklearn.__version__,
            lightgbm_version=lgb.__version__,
            feature_definition="40 frozen close-time features; signal_close_risk_frac fixed to 0.03; wick morphology retained",
            potential_definition="Close-proxy entry at signal bar close; near Monday edge target; maker/taker fill fees; 3% proxy risk",
            realized_label_definition="Actual next-H1 entry and actual fixed-3% stop as recorded in outcomes.csv.gz",
        )
        bundles[key] = bundle
        joblib.dump(bundle, EXP / model_file, compress=3)
        model_results[key] = {
            "candidate": str(chosen["candidate"]),
            "ranking_method": str(chosen["ranking_method"]),
            "selection_fraction": float(chosen["selection_fraction"]),
            "validation_selection_score": chosen["mean_year_selection_score"],
            "validation_mean_high_net_r": chosen["mean_high_net_r"],
            "train_n": int(bundle["train_n"]),
            "train_profitable": int(bundle["train_profitable"]),
            "score_threshold": float(bundle["score_thresholds"][str(chosen["ranking_method"])][score_key(float(chosen["selection_fraction"]))]),
            "geometry_threshold": float(bundle["geometry_thresholds"][score_key(float(chosen["selection_fraction"]))]),
            "model_file": model_file,
            "test_feature_rows": int(len(test)),
            "test_eligible_rows": int(test.eligible.sum()),
            "test_settled_by_cutoff": int(len(eval_rows)),
            "all": all_stats,
            "selected": comparison_metrics(selected, eval_rows),
            "geometry_only": comparison_metrics(geometry, eval_rows),
            "selected_matched_same_week_side_tf": matched_inference(scored, cfg, "band"),
            "geometry_matched_same_week_side_tf": matched_inference(scored, cfg, "baseline_band"),
            "outcome_counts": scored.loc[scored.eligible, "outcome"].fillna("missing").value_counts().to_dict(),
        }
        # Keep an explicit machine-readable frontier and actual outcome labels for
        # the requested paired/cluster comparisons downstream.
        test_columns = [
            "event_id", "tf", "week", "side", "net_r", "outcome", "eligible",
            "band", "score", "p", "baseline_band", "candidate", "ranking_method",
            "selection_fraction", "expected_net_r", "near_potential_net_r", "stop_net_r",
            "proxy_entry", "near_target_price", "score_threshold", "geometry_threshold",
            "close_ms", "known_ms", "known_by_cutoff", "exit_ms", "resolution",
        ]
        test_rows.append(scored.reindex(columns=test_columns))
    return pd.concat(test_rows, ignore_index=True), bundles, model_results


def main() -> None:
    cfg = load_config()
    EXP.mkdir(parents=True, exist_ok=True)
    rows = load_rows(cfg)
    cutoff = int(pd.Timestamp(cfg["cutoff_exclusive"]).timestamp() * 1000)
    test_start = int(pd.Timestamp(cfg["test_start"]).timestamp() * 1000)
    if rows.close_ms.max() > cutoff:
        raise ValueError("Frozen feature rows extend beyond cutoff_exclusive")
    if rows.close_ms.min() >= year_start(int(cfg["validation_years"][0])):
        raise ValueError("No expanding-fold history exists before the first validation year")
    tic = perf_counter()
    folds, selection_stats, winners = validation_folds(rows, cfg)
    folds.to_csv(EXP / "folds.csv", index=False)
    selection_stats.to_csv(EXP / "selection_stats.csv", index=False)
    test_scores, bundles, test_summary = final_test(rows, cfg, winners)
    test_scores = test_scores.sort_values(["close_ms", "event_id"], kind="mergesort")
    test_scores.to_csv(
        EXP / "test_scores.csv.gz", index=False,
        compression={"method": "gzip", "mtime": 0},
    )
    selection_records = selection_stats.to_dict(orient="records")
    summary = {
        "experiment": EXPERIMENT_ID,
        "parent": cfg["parent"],
        "source_experiment": str(source_experiment(cfg).relative_to(ROOT)),
        "input_outcomes": str(OUTCOME_FILE.relative_to(ROOT)),
        "outcome_rows": int(rows.outcome.notna().sum()),
        "feature_rows": int(len(rows)),
        "eligible_rows": int(rows.eligible.sum()),
        "eligible_outcomes": rows.loc[rows.eligible, "outcome"].fillna("missing").value_counts().to_dict(),
        "timeframes": {key: json_value(value) for key, value in test_summary.items()},
        "selected_configurations": json_value(winners),
        "candidate_frontier": json_value(selection_records),
        "feature_names": list(FEATURES),
        "feature_count": len(FEATURES),
        "risk_feature_override": {"name": STOP_FEATURE, "value": float(cfg["stop_frac"]),
                                   "wick_shape_features_retained": True},
        "probability_target": "realized net_r > 0; actual entry and stop from fixed-3% outcomes.csv.gz",
        "potential_geometry": {
            "entry_proxy": "signal bar close",
            "target": "week_low for long; week_high for short",
            "risk_fraction": float(cfg["stop_frac"]),
            "fees": dict(cfg["fees"]),
            "used_as_model_feature": False,
            "expected_net_r": "p * near_potential_net_r + (1-p) * stop_net_r",
        },
        "validation_years": [int(year) for year in cfg["validation_years"]],
        "validation_label_maturity_cutoff": cfg["test_start"],
        "test_start": cfg["test_start"],
        "cutoff_exclusive": cfg["cutoff_exclusive"],
        "ranking_methods": list(cfg["ranking"]),
        "high_fractions": [float(x) for x in cfg["high_fractions"]],
        "band_semantics": "top means above the frozen training-sample threshold for the selected 20% or 40% proportion; rest otherwise",
        "draws": int(cfg["draws"]),
        "seed": int(cfg["seed"]),
        "train_runtime": "project .venv",
        "sklearn": sklearn.__version__,
        "lightgbm": lgb.__version__,
        "elapsed_seconds": round(perf_counter() - tic, 2),
        "production_eligible": False,
    }
    write_json(EXP / "summary.json", json_value(summary))
    print(json.dumps(json_value(summary), ensure_ascii=False, indent=2, allow_nan=False), flush=True)


def score_prepared(path: Path) -> pd.DataFrame:
    cfg = load_config()
    observations = pd.read_csv(path)
    missing = set(FEATURES).difference(observations.columns)
    if missing:
        raise ValueError(f"Prepared feature rows lack model columns: {sorted(missing)}")
    if "event_id" not in observations or "tf" not in observations:
        raise ValueError("Prepared feature rows must include event_id and tf")
    if STOP_FEATURE in observations:
        observations[STOP_FEATURE] = float(cfg["stop_frac"])
    if "near_potential_net_r" not in observations or "stop_net_r" not in observations:
        observations = add_geometry(observations, cfg)
    if "eligible" not in observations:
        observations["eligible"] = True
    summary_path = EXP / "summary.json"
    if not summary_path.is_file():
        raise FileNotFoundError(f"Frozen scored models are not available: {summary_path}")
    summary = json.loads(summary_path.read_text())
    results = []
    for tf, group in observations.groupby("tf", sort=False):
        key = str(int(tf))
        if key not in summary["timeframes"]:
            raise ValueError(f"No frozen model for timeframe {tf}")
        model_info = summary["timeframes"][key]
        bundle = joblib.load(EXP / model_info["model_file"])
        parts = score_parts(bundle, group)
        scored = set_bands(
            group, parts, bundle, str(model_info["ranking_method"]),
            float(model_info["selection_fraction"]),
        )
        keep = [column for column in (
            "event_id", "tf", "week", "side", "close_ms", "eligible", "band", "score", "p",
            "baseline_band", "candidate", "ranking_method", "selection_fraction",
            "expected_net_r", "near_potential_net_r", "stop_net_r", "proxy_entry", "near_target_price",
        ) if column in scored]
        results.append(scored[keep])
    if not results:
        raise ValueError("Prepared input contains no 1h/4h/12h rows")
    return pd.concat(results).sort_values([c for c in ("close_ms", "event_id") if c in results[0]], kind="mergesort")


def main_cli() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--score", type=Path, metavar="FEATURES_CSV",
                        help="score prepared causal feature rows with frozen models")
    parser.add_argument("--output", type=Path,
                        help="write --score output here (default: stdout)")
    args = parser.parse_args()
    with threadpool_limits(limits=3):
        if args.score:
            result = score_prepared(args.score)
            if args.output:
                result.to_csv(args.output, index=False)
            else:
                print(result.to_csv(index=False), end="")
        else:
            if args.output:
                parser.error("--output is only valid with --score")
            main()


if __name__ == "__main__":
    main_cli()
