"""Shared dashboard data cache for universe-scoped model scores.

The dashboard is read-only over experiment artifacts, but score CSVs are
runtime caches. This module keeps cache identity tied to universe, dataset
fingerprint, and frozen artifact metadata so spot/swap views cannot reuse the
wrong model scores.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd
from fastapi import HTTPException

from src.backtest.run import BAR, SCORE_QUANTILE, build_signals, simulate
from src.judgment.features import FEATURE_COLUMNS
from src.judgment.frozen import (
    DEFAULT_FROZEN_CONFIG,
    cache_matches_artifact,
    cache_metadata,
    file_sha256,
    latest_artifact,
)
from src.judgment.train import DEFAULT_HORIZON_BARS, HOLDOUT_START, TRAIN_FRACTION, train_model

PROJECT_DIR = Path(__file__).resolve().parents[2]
OUTPUT_DIR = PROJECT_DIR / "analysis" / "output"
LEGACY_SPOT_DATA = PROJECT_DIR / "data" / "sweep_v3" / "judgment_v3_tp5_sl2_h72.csv"
DEFAULT_UNIVERSE = "swap"
CACHE_COLUMNS = [
    "source",
    "symbol",
    "signal_time",
    "entry_time",
    "exit_time",
    "score",
    "outcome",
    "realized_ret",
    "entry_price",
    "label",
    "atr_pct",
    "dense_run_len",
]


@dataclass(frozen=True)
class UniverseSpec:
    key: str
    label: str
    dataset_path: Path


UNIVERSES = {
    "swap": UniverseSpec("swap", "合约/SWAP", DEFAULT_FROZEN_CONFIG.dataset_path),
    "spot": UniverseSpec("spot", "现货/SPOT", LEGACY_SPOT_DATA),
}

_signals: dict[str, pd.DataFrame] = {}
_thresholds: dict[str, float] = {}
_trades: dict[str, pd.DataFrame] = {}


def load_json(name: str) -> dict:
    path = OUTPUT_DIR / name
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def universe_spec(universe: str = DEFAULT_UNIVERSE) -> UniverseSpec:
    key = universe.lower()
    if key not in UNIVERSES:
        raise HTTPException(400, f"unknown universe {universe!r}; expected spot or swap")
    return UNIVERSES[key]


def scored_signals(universe: str = DEFAULT_UNIVERSE) -> tuple[pd.DataFrame, float]:
    spec = universe_spec(universe)
    if spec.key not in _signals:
        if not spec.dataset_path.exists():
            raise HTTPException(503, f"dataset missing: {spec.dataset_path.relative_to(PROJECT_DIR)}")
        cache_path, meta_path = _score_cache_paths(spec)
        artifact = _artifact_for_spec(spec)
        rebuild = True
        if cache_path.exists() and meta_path.exists():
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            cached = pd.read_csv(cache_path, parse_dates=["signal_time", "entry_time", "exit_time"])
            if set(CACHE_COLUMNS) <= set(cached.columns) and _cache_matches_universe(meta, spec, artifact):
                _signals[spec.key] = cached
                _thresholds[spec.key] = float(meta["threshold"])
                rebuild = False
        if rebuild:
            print(f"scoring {spec.key} signals (first boot, ~10s)...", flush=True)
            signals, threshold = _build_signals_for_spec(spec)
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            signals[CACHE_COLUMNS].to_csv(cache_path, index=False)
            meta_path.write_text(
                json.dumps(_score_cache_metadata(spec, threshold, _artifact_for_spec(spec)), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            _signals[spec.key], _thresholds[spec.key] = signals[CACHE_COLUMNS], threshold
    return _signals[spec.key], float(_thresholds[spec.key])


def trades(universe: str = DEFAULT_UNIVERSE) -> pd.DataFrame:
    spec = universe_spec(universe)
    if spec.key not in _trades:
        signals, threshold = scored_signals(spec.key)
        _trades[spec.key] = simulate(signals, threshold)
    return _trades[spec.key]


def relative_path(path: Path) -> str:
    try:
        return path.relative_to(PROJECT_DIR).as_posix()
    except ValueError:
        return str(path)


def symbol_matches_universe(symbol: str, universe: str) -> bool:
    is_swap = symbol.endswith("_USDT_SWAP")
    if universe == "swap":
        return is_swap
    return symbol.endswith("_USDT") and not is_swap


def _score_cache_paths(spec: UniverseSpec) -> tuple[Path, Path]:
    data_dir = PROJECT_DIR / "data"
    return data_dir / f"scored_signals_{spec.key}.csv", data_dir / f"scored_signals_{spec.key}_meta.json"


def _artifact_for_spec(spec: UniverseSpec):
    artifact = latest_artifact(DEFAULT_FROZEN_CONFIG)
    if artifact is not None and spec.dataset_path.resolve() == artifact.dataset_path.resolve():
        return artifact
    return None


def _score_cache_metadata(spec: UniverseSpec, threshold: float, artifact) -> dict:
    meta = dict(cache_metadata(threshold, artifact))
    meta.update({
        "universe": spec.key,
        "dataset_path": relative_path(spec.dataset_path),
        "dataset_sha256": file_sha256(spec.dataset_path),
    })
    return meta


def _cache_matches_universe(meta: dict, spec: UniverseSpec, artifact) -> bool:
    if "threshold" not in meta or meta.get("universe") != spec.key:
        return False
    if meta.get("dataset_path") != relative_path(spec.dataset_path):
        return False
    if meta.get("dataset_sha256") != file_sha256(spec.dataset_path):
        return False
    if artifact is not None:
        return cache_matches_artifact(meta, artifact)
    return "model_path" not in meta


def _build_signals_for_spec(spec: UniverseSpec) -> tuple[pd.DataFrame, float]:
    if spec.key == "swap":
        return build_signals(spec.dataset_path)
    data = pd.read_csv(spec.dataset_path, parse_dates=["signal_time"])
    data = data[data["symbol"].map(lambda symbol: symbol_matches_universe(str(symbol), spec.key))]
    train, val, _ = _split_universe_frame(data)
    model = train_model(train, val)
    val_scores = model.predict(val[FEATURE_COLUMNS], num_iteration=model.best_iteration)
    threshold = float(np.quantile(val_scores, SCORE_QUANTILE))
    full = data.sort_values("signal_time").reset_index(drop=True)
    full["score"] = model.predict(full[FEATURE_COLUMNS], num_iteration=model.best_iteration)
    full["entry_time"] = full["signal_time"] + BAR
    full["exit_time"] = full["entry_time"] + full["exit_offset"] * BAR
    return full.sort_values(["entry_time", "score"], ascending=[True, False]), threshold


def _split_universe_frame(data: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    purge = pd.Timedelta(minutes=15 * (DEFAULT_HORIZON_BARS + 1))
    ordered = data.sort_values("signal_time").reset_index(drop=True)
    dev = ordered[ordered["signal_time"] < HOLDOUT_START - purge].reset_index(drop=True)
    holdout = ordered[ordered["signal_time"] >= HOLDOUT_START].reset_index(drop=True)
    split_i = int(len(dev) * TRAIN_FRACTION)
    train, val = dev.iloc[:split_i], dev.iloc[split_i:]
    val_start = val["signal_time"].min()
    train = train[train["signal_time"] < val_start - purge]
    return train, val, holdout
