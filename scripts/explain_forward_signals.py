#!/usr/bin/env python3
# /// script
# requires-python = ">=3.9"
# dependencies = [
#   "lightgbm>=4.0",
#   "numpy>=1.24",
#   "pandas>=2.0",
# ]
# ///
# --- How to run ---
# PYTHONPATH=. python3 scripts/explain_forward_signals.py
"""Audit frozen LightGBM forward scores without changing any paper ledger."""
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path
from typing import Final

import lightgbm as lgb
import numpy as np
import pandas as pd

from src.data.loader import iter_series
from src.judgment.candidates import add_indicators
from src.judgment.explain import summarize_contributions
from src.judgment.features import FEATURE_COLUMNS, add_features, extract_feature_rows
from src.judgment.forward_records import read_forward_log
from src.judgment.forward_scan import forward_candidate_indices
from src.judgment.forward_types import FORWARD_LOG_PATH
from src.judgment.frozen import DEFAULT_FROZEN_CONFIG, latest_artifact

PROJECT_DIR: Final = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT: Final = PROJECT_DIR / "output" / "offline_tasks" / "ma206_forward_explainability.json"
SCORE_TOLERANCE: Final = 1e-9


def _utc_key(value: str | pd.Timestamp) -> int:
    timestamp = pd.Timestamp(value)
    if timestamp.tzinfo is None:
        timestamp = timestamp.tz_localize("UTC")
    else:
        timestamp = timestamp.tz_convert("UTC")
    return int(timestamp.value)


def build_audit(ledger_path: Path) -> dict:
    artifact = latest_artifact(DEFAULT_FROZEN_CONFIG)
    if artifact is None:
        raise FileNotFoundError("missing frozen MA206 artifact")
    ledger = read_forward_log(ledger_path)
    if ledger.empty:
        return {"ledger": str(ledger_path), "total_rows": 0, "signals": []}

    wanted: dict[tuple[str, str], list[tuple[int, pd.Series]]] = defaultdict(list)
    for _, row in ledger.iterrows():
        wanted[(str(row["source"]), str(row["symbol"]))].append((_utc_key(row["signal_time"]), row))

    booster = lgb.Booster(model_file=str(artifact.model_path))
    signals: list[dict] = []
    contribution_totals = dict.fromkeys(FEATURE_COLUMNS, 0.0)
    missing = 0
    for source, symbol, frame in iter_series(bar="15m", min_bars=500):
        rows = wanted.pop((source, symbol), None)
        if rows is None:
            continue
        enriched = add_indicators(frame)
        time_to_index = {
            _utc_key(timestamp): int(index)
            for index, timestamp in enriched["open_time"].items()
        }
        current_candidates = set(forward_candidate_indices(enriched))
        featured = add_features(enriched)
        for time_key, ledger_row in rows:
            signal_i = time_to_index.get(time_key)
            if signal_i is None:
                missing += 1
                continue
            feature_row = extract_feature_rows(featured, [signal_i])
            score = float(
                booster.predict(
                    feature_row[FEATURE_COLUMNS],
                    num_iteration=artifact.best_iteration,
                    validate_features=True,
                )[0]
            )
            vector = np.asarray(
                booster.predict(
                    feature_row[FEATURE_COLUMNS],
                    num_iteration=artifact.best_iteration,
                    pred_contrib=True,
                    validate_features=True,
                )[0],
                dtype=float,
            )
            explanation = summarize_contributions(FEATURE_COLUMNS, vector, score)
            for item in explanation.contributions:
                contribution_totals[item.feature] += abs(item.contribution)
            ledger_score = float(ledger_row["score"])
            signals.append(
                {
                    "source": source,
                    "symbol": symbol,
                    "signal_time": str(pd.Timestamp(enriched["open_time"].iloc[signal_i])),
                    "status": str(ledger_row["status"]),
                    "current_candidate": signal_i in current_candidates,
                    "ledger_score": ledger_score,
                    "recomputed_score": score,
                    "score_matches": bool(abs(score - ledger_score) <= SCORE_TOLERANCE),
                    "expected_value": explanation.expected_value,
                    "top_positive": {
                        "feature": explanation.top_positive.feature,
                        "contribution": explanation.top_positive.contribution,
                    },
                    "top_negative": {
                        "feature": explanation.top_negative.feature,
                        "contribution": explanation.top_negative.contribution,
                    },
                }
            )

    missing += sum(len(rows) for rows in wanted.values())
    explained = len(signals)
    ranked_features = sorted(
        (
            {"feature": feature, "mean_abs_contribution": total / explained}
            for feature, total in contribution_totals.items()
        ),
        key=lambda item: item["mean_abs_contribution"],
        reverse=True,
    ) if explained else []
    return {
        "ledger": str(ledger_path),
        "model_path": artifact.relative_model_path,
        "threshold": artifact.threshold,
        "total_rows": int(len(ledger)),
        "explained_rows": explained,
        "missing_rows": missing,
        "current_candidate_rows": sum(bool(row["current_candidate"]) for row in signals),
        "stale_candidate_rows": sum(not bool(row["current_candidate"]) for row in signals),
        "score_match_rows": sum(bool(row["score_matches"]) for row in signals),
        "mean_abs_contribution_top10": ranked_features[:10],
        "signals": signals,
        "warning": "Read-only diagnostic; does not validate profitability or authorize threshold changes.",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ledger", type=Path, default=FORWARD_LOG_PATH)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    audit = build_audit(args.ledger)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({key: value for key, value in audit.items() if key != "signals"}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
