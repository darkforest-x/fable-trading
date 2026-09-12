"""Causal signal-level diagnostics for the frozen SPIKE V7 population.

The discovery stage reads the authenticated V7 caches and emits one row for
each admitted V7 signal.  Every feature is available at the signal close:

* ``efficiency3`` uses OHLC true range over bars ``t-2..t`` and close ``t-3``;
* BB release fields use current/prior BB200 widths and the frozen prior-500 P10;
* current volume/TR ratios use V7's already-causal per-symbol baselines;
* rope distance and close-risk use current OHLC/ATR plus the preceding four bars.

Future prices are joined only to development rows from the previously frozen
baseline trade ledger.  Validation rows carry explicit withholding markers and
never contain trade or outcome values.  A later, separately frozen replay owns
that exposure.  This module neither changes monitor admissions nor touches
orders.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd

from yoyo.evaluation.spike_exit_policy_study import END, SPLIT, START, load_verified_stream, sha256


EXP = Path("experiments/active/exp-spike-v8-noise-filter-20260913-v1")
FEATURE_VERSION = "spike-v8-causal-signal-features-v1"

# These values are all derived from the future-facing baseline trade ledger.
# They may be materialized for development discovery only; validation rows must
# retain nulls so the output artifact cannot be used to inspect validation
# outcomes accidentally.
TRADE_OUTCOME_COLUMNS = (
    "trade_id", "entry_time", "exit_time", "exit_reason", "initial_risk_frac", "mfe_r",
    "net_return", "net_r", "gross_return", "gross_r", "censored", "holding_bars",
)
DERIVED_OUTCOME_COLUMNS = (
    "executed", "closed", "net_positive", "realized_10r", "mfe_10r", "failure_reason",
)
OUTCOME_VALUE_COLUMNS = TRADE_OUTCOME_COLUMNS + DERIVED_OUTCOME_COLUMNS


def _safe_div(numerator: pd.Series, denominator: pd.Series) -> pd.Series:
    denominator = pd.to_numeric(denominator, errors="coerce")
    return pd.to_numeric(numerator, errors="coerce").div(denominator.where(denominator.gt(0)))


def _close_risk_fraction(
    bars: pd.DataFrame,
    side: pd.Series,
    *,
    tick: float,
    stop_bars: int = 5,
    stop_buffer_atr: float = 0.2,
    risk_floor_atr: float = 2.0,
) -> pd.Series:
    """Return signal-close risk / close from current and four prior OHLC bars."""
    if not math.isfinite(float(tick)) or tick <= 0:
        raise ValueError("tick must be a positive finite value")
    close = pd.to_numeric(bars.close, errors="coerce")
    atr = pd.to_numeric(bars.atr, errors="coerce")
    recent_low = bars.low.rolling(stop_bars, min_periods=stop_bars).min()
    recent_high = bars.high.rolling(stop_bars, min_periods=stop_bars).max()
    long_raw = np.minimum(recent_low - stop_buffer_atr * atr, close - risk_floor_atr * atr)
    short_raw = np.maximum(recent_high + stop_buffer_atr * atr, close + risk_floor_atr * atr)
    long_stop = np.floor(long_raw / tick) * tick
    short_stop = np.ceil(short_raw / tick) * tick
    stop = pd.Series(np.where(side.eq(1), long_stop, short_stop), index=bars.index)
    risk = side * (close - stop)
    return risk.div(close.where(close.gt(0))).where(stop.gt(0) & risk.gt(0))


def signal_feature_frame(
    bars: pd.DataFrame,
    signals: pd.DataFrame,
    bb: pd.DataFrame,
    *,
    tick: float,
    minutes: int,
) -> pd.DataFrame:
    """Build one causal row per frozen V7 admission on aligned closed bars."""
    if not bars.index.equals(signals.index) or not bars.index.equals(bb.index):
        raise ValueError("bars, signals and BB diagnostics must share one clock")
    needed_bars = {
        "open", "high", "low", "close", "volume", "tr", "atr", "rv", "expansion",
        "ropeHigh", "ropeLow", "width", "md", "sb", "middle", "pastWidth", "pastCrosses",
    }
    missing = needed_bars - set(bars.columns)
    if missing:
        raise ValueError("missing V7 bar columns: " + ", ".join(sorted(missing)))
    needed_bb = {"bb_width", "bb_width_p10_prior500", "bb_compressed", "prior_squeeze_run3", "v7_ready"}
    missing_bb = needed_bb - set(bb.columns)
    if missing_bb:
        raise ValueError("missing V7 BB columns: " + ", ".join(sorted(missing_bb)))
    if minutes not in (30, 60, 240):
        raise ValueError("discovery is frozen to 30m, 1H and 4H")

    long = signals.long_signal.fillna(False).astype(bool)
    short = signals.short_signal.fillna(False).astype(bool)
    if (long & short).any():
        raise ValueError("ambiguous V7 side")
    raw = long | short
    admitted = raw & bb.v7_ready.fillna(False).astype(bool) & bb.prior_squeeze_run3.fillna(False).astype(bool)
    side = pd.Series(np.where(long, 1, np.where(short, -1, 0)), index=bars.index, dtype=int)

    close = pd.to_numeric(bars.close, errors="coerce")
    open_ = pd.to_numeric(bars.open, errors="coerce")
    high = pd.to_numeric(bars.high, errors="coerce")
    low = pd.to_numeric(bars.low, errors="coerce")
    atr = pd.to_numeric(bars.atr, errors="coerce")
    span = high - low
    move3 = close - close.shift(3)
    tr3 = pd.to_numeric(bars.tr, errors="coerce").rolling(3, min_periods=3).sum()
    bb_ratio = _safe_div(bb.bb_width, bb.bb_width_p10_prior500)
    bb_step1 = _safe_div(bb.bb_width, bb.bb_width.shift(1)) - 1.0
    bb_step3 = _safe_div(bb.bb_width, bb.bb_width.shift(3)) - 1.0
    compressed = bb.bb_compressed.fillna(False).astype(float)
    prior_compressed_bars = compressed.shift(1).rolling(12, min_periods=12).sum()
    prior_min_ratio = bb_ratio.shift(1).rolling(12, min_periods=12).min()
    body_fraction = _safe_div((close - open_).abs(), span)
    end_position = pd.Series(
        np.where(side.eq(1), _safe_div(close - low, span), _safe_div(high - close, span)),
        index=bars.index,
    )
    rope = pd.Series(np.where(side.eq(1), bars.ropeHigh, bars.ropeLow), index=bars.index)
    rope_distance = side * (close - rope)
    md_gap = side * (pd.to_numeric(bars.md, errors="coerce") - pd.to_numeric(bars.sb, errors="coerce"))
    md_step = side * (pd.to_numeric(bars.md, errors="coerce") - pd.to_numeric(bars.md, errors="coerce").shift(1))
    risk_fraction = _close_risk_fraction(bars, side, tick=tick)
    confirm_time = bars.index + pd.Timedelta(minutes=minutes)
    in_window = (confirm_time >= START) & (confirm_time < END)

    frame = pd.DataFrame(
        {
            "bar_i": np.arange(len(bars), dtype=int),
            "signal_bar_open": bars.index,
            "signal_confirm_time": confirm_time,
            "period": np.where(confirm_time < SPLIT, "development", "validation"),
            "side": side,
            "close": close,
            "atr_fraction": _safe_div(atr, close),
            "advance3_atr": side * _safe_div(move3, atr.shift(3)),
            "efficiency3": side * _safe_div(move3, tr3),
            "body_fraction": body_fraction,
            "directional_end_position": end_position,
            "current_volume_ratio": pd.to_numeric(bars.rv, errors="coerce"),
            "current_tr_expansion": pd.to_numeric(bars.expansion, errors="coerce"),
            "bb_width_ratio_p10": bb_ratio,
            "bb_width_step1": bb_step1,
            "bb_width_step3": bb_step3,
            "prior_compressed_bars12": prior_compressed_bars,
            "prior_min_bb_ratio12": prior_min_ratio,
            "ma_width_atr": pd.to_numeric(bars.width, errors="coerce"),
            "rope_distance_atr": _safe_div(rope_distance, atr),
            "md_gap_atr": _safe_div(md_gap, atr),
            "md_step_atr": _safe_div(md_step, atr),
            "close_risk_fraction": risk_fraction,
            "cost_share_of_close_r": 0.002 / risk_fraction,
            "dense_width": pd.to_numeric(bars.pastWidth, errors="coerce"),
            "dense_crosses": pd.to_numeric(bars.pastCrosses, errors="coerce"),
        },
        index=bars.index,
    )
    frame["gate_efficiency55"] = frame.efficiency3.ge(0.55)
    frame["gate_bb_rising1"] = frame.bb_width_step1.gt(0)
    frame["gate_bb_released"] = frame.bb_width_ratio_p10.gt(1.0)
    frame["gate_current_volume15"] = frame.current_volume_ratio.ge(1.5)
    frame["gate_current_tr15"] = frame.current_tr_expansion.ge(1.5)
    frame["gate_v1_hard_impulse"] = frame.current_volume_ratio.ge(4.0) & frame.current_tr_expansion.ge(3.0)
    frame["gate_cost_share25"] = frame.cost_share_of_close_r.le(0.25)
    frame["gate_not_overheated3"] = frame.rope_distance_atr.le(3.0)
    frame = frame.loc[admitted & in_window].reset_index(drop=True)
    if frame[[c for c in frame if c.startswith("gate_")]].isna().any().any():
        raise ValueError("candidate gates must be explicit booleans")
    frame.attrs["feature_version"] = FEATURE_VERSION
    return frame


def _auc(score: pd.Series, label: pd.Series) -> float:
    """Tie-aware rank AUC without adding a machine-learning dependency."""
    known = pd.to_numeric(score, errors="coerce").notna() & label.notna()
    x = pd.to_numeric(score.loc[known], errors="coerce")
    y = label.loc[known].astype(bool)
    positives, negatives = int(y.sum()), int((~y).sum())
    if positives == 0 or negatives == 0:
        return math.nan
    ranks = x.rank(method="average")
    return float((ranks.loc[y].sum() - positives * (positives + 1) / 2) / (positives * negatives))


def _profit_factor(values: pd.Series) -> float:
    values = pd.to_numeric(values, errors="coerce").dropna()
    loss = -values.clip(upper=0).sum()
    return float(values.clip(lower=0).sum() / loss) if loss > 0 else math.nan


def _failure_reason(row: pd.Series) -> str:
    if bool(row.get("censored", False)):
        return "censored"
    if float(row.get("net_r", math.nan)) >= 10:
        return "realized_ge10r"
    if float(row.get("net_return", math.nan)) > 0:
        return "positive_below10r"
    duration = float(row.get("holding_bars", math.nan))
    reason = str(row.get("exit_reason", ""))
    if float(row.get("cost_share_of_close_r", math.nan)) > 0.5:
        return "cost_dominated_tiny_risk"
    if reason.startswith("initial_stop") and duration <= 3:
        return "initial_stop_within3"
    if reason.startswith("initial_stop"):
        return "initial_stop_later"
    if "opposite" in reason:
        return "opposite_signal_loss"
    if "trailing" in reason:
        return "trailing_giveback_loss"
    return "other_nonpositive"


def _assert_validation_outcomes_withheld(frame: pd.DataFrame) -> None:
    """Reject an artifact that carries any future-facing values on validation rows."""
    required = {"period", "outcome_available", "outcome_withheld_validation", *OUTCOME_VALUE_COLUMNS}
    missing = required - set(frame.columns)
    if missing:
        raise AssertionError("outcome isolation columns missing: " + ", ".join(sorted(missing)))
    validation = frame.loc[frame.period.eq("validation")]
    leaked = [column for column in OUTCOME_VALUE_COLUMNS if validation[column].notna().any()]
    if leaked:
        raise AssertionError("validation rows contain withheld outcome values: " + ", ".join(leaked))
    if validation.outcome_available.fillna(True).any():
        raise AssertionError("validation rows cannot mark outcomes available")
    if not validation.outcome_withheld_validation.fillna(False).all():
        raise AssertionError("validation rows must declare outcome withholding")


def _outcome_scope_metadata(frame: pd.DataFrame) -> dict[str, object]:
    """Return the manifest declaration only after verifying physical isolation."""
    _assert_validation_outcomes_withheld(frame)
    return {
        "validation_outcomes_present": False,
        "outcome_scope": "development_only",
    }


def join_frozen_outcomes(features: pd.DataFrame, trades: pd.DataFrame) -> pd.DataFrame:
    """Attach prior baseline outcomes to development rows and withhold validation rows."""
    if "period" not in features:
        raise ValueError("features must declare development or validation period")
    if "period" not in trades:
        raise ValueError("outcome input must declare period and be physically development-only")
    nondevelopment = trades.loc[~trades.period.eq("development")]
    if len(nondevelopment):
        raise ValueError(
            "outcome input contains non-development rows; supply a separate development-only artifact"
        )
    unexpected = set(OUTCOME_VALUE_COLUMNS) & set(features.columns)
    if unexpected:
        raise ValueError("features must not already carry outcome values: " + ", ".join(sorted(unexpected)))
    features = features.copy()
    features["_outcome_join_order"] = np.arange(len(features), dtype=int)
    keys = ["stream_key", "signal_bar_open", "side"]
    outcome = trades.loc[trades.arm.eq("baseline")].copy()
    outcome["signal_bar_open"] = pd.to_datetime(outcome.signal_bar_open, utc=True)
    outcome["entry_time"] = pd.to_datetime(outcome.entry_time, utc=True)
    outcome["exit_time"] = pd.to_datetime(outcome.exit_time, utc=True, errors="coerce")
    outcome["holding_bars"] = (
        (outcome.exit_time - outcome.entry_time).dt.total_seconds() / (60 * outcome.timeframe_min)
    )
    columns = keys + list(TRADE_OUTCOME_COLUMNS)
    if outcome.duplicated(keys).any():
        raise ValueError("baseline outcomes are not unique by signal and side")

    development = features.loc[features.period.eq("development")].merge(
        outcome[columns], on=keys, how="left", validate="one_to_one",
    )
    development["executed"] = development.trade_id.notna().astype("boolean")
    development["closed"] = (development.executed & ~development.censored.fillna(False).astype(bool)).astype("boolean")
    development["net_positive"] = development.net_return.gt(0).astype("boolean")
    development["realized_10r"] = development.net_r.ge(10).astype("boolean")
    development["mfe_10r"] = development.mfe_r.ge(10).astype("boolean")
    development["failure_reason"] = development.apply(_failure_reason, axis=1).where(
        development.executed, "not_executed_occupied",
    )
    development["outcome_available"] = development.executed.astype("boolean")
    development["outcome_withheld_validation"] = pd.Series(False, index=development.index, dtype="boolean")

    validation = features.loc[features.period.eq("validation")].copy()
    for column in OUTCOME_VALUE_COLUMNS:
        validation[column] = development[column].iloc[:0].reindex(validation.index)
    validation["outcome_available"] = pd.Series(False, index=validation.index, dtype="boolean")
    validation["outcome_withheld_validation"] = pd.Series(True, index=validation.index, dtype="boolean")

    joined = (
        pd.concat([development, validation], ignore_index=True)
        .sort_values("_outcome_join_order", kind="stable")
        .drop(columns="_outcome_join_order")
        .reset_index(drop=True)
    )
    _assert_validation_outcomes_withheld(joined)
    return joined


def summarize_development(joined: pd.DataFrame, output: Path) -> dict[str, pd.DataFrame]:
    """Describe only the development year and fixed, single-variable gates."""
    dev = joined.loc[joined.period.eq("development")].copy()
    closed = dev.loc[dev.closed].copy()
    gates = [c for c in joined.columns if c.startswith("gate_")]
    rows: list[dict[str, object]] = []
    for minutes in (30, 60, 240):
        all_signals = dev.loc[dev.timeframe_min.eq(minutes)]
        all_trades = closed.loc[closed.timeframe_min.eq(minutes)]
        for gate in ["baseline", *gates]:
            signal_mask = pd.Series(True, index=all_signals.index) if gate == "baseline" else all_signals[gate]
            trade_mask = pd.Series(True, index=all_trades.index) if gate == "baseline" else all_trades[gate]
            kept = all_trades.loc[trade_mask]
            tails = all_trades.realized_10r
            rows.append(
                {
                    "gate": gate,
                    "timeframe_min": minutes,
                    "signals": len(all_signals),
                    "signals_kept": int(signal_mask.sum()),
                    "signal_reduction": 1 - float(signal_mask.mean()) if len(signal_mask) else math.nan,
                    "closed_trades": len(all_trades),
                    "closed_kept": len(kept),
                    "net_win_rate": float(kept.net_positive.mean()) if len(kept) else math.nan,
                    "event_pf": _profit_factor(kept.net_return),
                    "mean_net_return": float(kept.net_return.mean()) if len(kept) else math.nan,
                    "realized_10r": int(tails.sum()),
                    "realized_10r_kept": int((tails & trade_mask).sum()),
                    "realized_10r_retention": float((tails & trade_mask).sum() / tails.sum()) if tails.sum() else math.nan,
                    "mfe_10r": int(all_trades.mfe_10r.sum()),
                    "mfe_10r_kept": int((all_trades.mfe_10r & trade_mask).sum()),
                    "losers_removed": int((~all_trades.net_positive & ~trade_mask).sum()),
                    "winners_removed": int((all_trades.net_positive & ~trade_mask).sum()),
                }
            )

    score_directions = {
        "efficiency3": 1,
        "bb_width_step1": 1,
        "bb_width_ratio_p10": 1,
        "current_volume_ratio": 1,
        "current_tr_expansion": 1,
        "close_risk_fraction": 1,
        "cost_share_of_close_r": -1,
        "rope_distance_atr": -1,
        "md_gap_atr": 1,
        "md_step_atr": 1,
    }
    auc_rows: list[dict[str, object]] = []
    bucket_rows: list[dict[str, object]] = []
    for minutes, group in closed.groupby("timeframe_min"):
        for feature, direction in score_directions.items():
            score = direction * pd.to_numeric(group[feature], errors="coerce")
            auc_rows.append(
                {
                    "timeframe_min": minutes,
                    "feature": feature,
                    "direction": direction,
                    "auc_net_positive": _auc(score, group.net_positive),
                    "auc_realized_10r": _auc(score, group.realized_10r),
                    "known": int(score.notna().sum()),
                }
            )
            known = group.loc[score.notna()].copy()
            if len(known) < 20:
                continue
            known["bucket"] = pd.qcut(score.loc[known.index].rank(method="first"), 10, labels=False) + 1
            for bucket, part in known.groupby("bucket"):
                bucket_rows.append(
                    {
                        "timeframe_min": minutes,
                        "feature": feature,
                        "score_decile": int(bucket),
                        "trades": len(part),
                        "net_win_rate": float(part.net_positive.mean()),
                        "event_pf": _profit_factor(part.net_return),
                        "mean_net_return": float(part.net_return.mean()),
                        "realized_10r": int(part.realized_10r.sum()),
                    }
                )

    failure = (
        dev.groupby(["timeframe_min", "failure_reason"], dropna=False)
        .agg(signals=("side", "size"), mean_net_return=("net_return", "mean"), mean_net_r=("net_r", "mean"))
        .reset_index()
    )
    coverage = (
        joined.groupby(["venue", "timeframe_min"])
        .agg(streams=("stream_key", "nunique"), signals=("side", "size"), assets=("asset", "nunique"))
        .reset_index()
    )
    tables = {
        "candidate_screen_development": pd.DataFrame(rows),
        "feature_auc_development": pd.DataFrame(auc_rows),
        "feature_deciles_development": pd.DataFrame(bucket_rows),
        "failure_taxonomy_development": failure,
        "coverage_all_periods": coverage,
    }
    output.mkdir(parents=True, exist_ok=True)
    for name, table in tables.items():
        table.to_csv(output / f"{name}.csv", index=False)
    return tables


def collect(config: dict[str, object], *, limit: int | None = None) -> pd.DataFrame:
    raw = Path(str(config["raw"]))
    if sha256(raw / "manifest.json") != str(config["raw_manifest_sha256"]):
        raise ValueError("frozen V7 manifest changed")
    folders = sorted(p for p in (raw / "streams").iterdir() if (p / "completion.json").exists())
    expected = int(config["expected_streams"])
    if len(folders) != expected:
        raise ValueError(f"expected {expected} authenticated streams, found {len(folders)}")
    if limit is not None:
        folders = folders[:limit]
    pieces: list[pd.DataFrame] = []
    for number, folder in enumerate(folders, 1):
        context = load_verified_stream(folder)
        frame = signal_feature_frame(
            context.cache["bars"], context.cache["signals"], context.cache["bb"],
            tick=float(context.cache["tick"]), minutes=context.minutes,
        )
        for key, value in {"stream_key": context.key, **context.identity}.items():
            frame[key] = value
        pieces.append(frame)
        if number % 250 == 0 or number == len(folders):
            print(json.dumps({"feature_streams": number, "scope": len(folders)}), flush=True)
    return pd.concat(pieces, ignore_index=True) if pieces else pd.DataFrame()


def run_discovery(output: Path, *, limit: int | None = None) -> None:
    config_path = EXP / "config.json"
    config = json.loads(config_path.read_text())
    sources: Iterable[Path] = (Path(__file__), config_path, EXP / "PROJECT_PLAN.md")
    identity = {str(path): sha256(path) for path in sources}
    if output.exists() and any(output.iterdir()):
        raise ValueError("discovery output exists; use a new immutable directory")
    output.mkdir(parents=True, exist_ok=True)
    outcome_source = config.get("baseline_trades_development")
    if not outcome_source:
        raise ValueError(
            "config must point baseline_trades_development to a physically separate development-only artifact"
        )
    trades = pd.read_csv(str(outcome_source))
    if "period" not in trades or not trades.period.eq("development").all():
        raise ValueError("baseline_trades_development contains non-development outcomes")
    features = collect(config, limit=limit)
    if limit is None and len(features) != int(config["expected_v7_signals"]):
        raise ValueError(f"expected {config['expected_v7_signals']} V7 signals, found {len(features)}")
    joined = join_frozen_outcomes(features, trades)
    outcome_scope = _outcome_scope_metadata(joined)
    joined.to_csv(
        output / "signal_features.csv.gz", index=False,
        compression={"method": "gzip", "compresslevel": 1, "mtime": 0},
    )
    tables = summarize_development(joined, output)
    manifest = {
        "complete": limit is None,
        "feature_version": FEATURE_VERSION,
        "streams": int(joined.stream_key.nunique()),
        "signals": len(joined),
        "executed_baseline_rows": int(joined.executed.sum()),
        "validation_outcomes_summarized": False,
        **outcome_scope,
        "identity": identity,
        "files": {path.name: sha256(path) for path in output.iterdir() if path.is_file()},
        "generated_at": pd.Timestamp.now(tz="UTC").isoformat(),
    }
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2))
    print(json.dumps({k: v for k, v in manifest.items() if k not in {"identity", "files"}}, ensure_ascii=False), flush=True)
    print(tables["candidate_screen_development"].to_string(index=False), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--limit", type=int)
    args = parser.parse_args()
    run_discovery(args.output, limit=args.limit)
