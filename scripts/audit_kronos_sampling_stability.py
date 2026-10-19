"""Measure Kronos sampling stability on deterministic synthetic K-lines only.

The three profiles form a one-variable ladder:

1. README example: T=1.0, one path.
2. Temperature only: T=0.6, one path.
3. Paper forecast profile: T=0.6, ten averaged paths.

No market file is opened, so this cannot consume holdout or make an economic
claim.  Exact model/tokenizer revisions come from the research contract.  The
loader's default training mode is recorded, then ``eval()`` is applied explicitly
before any inference, matching the upstream regression tests rather than the
underspecified README example.

Usage:
    PYTHONPATH=. .venv/bin/python scripts/audit_kronos_sampling_stability.py
"""
from __future__ import annotations

import argparse
import hashlib
import json
import random
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Tuple

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from yoyo.contracts.kronos_research import (  # noqa: E402
    load_kronos_research_contract,
)


DEFAULT_CONFIG = ROOT / "configs/research/kronos_small_forecast_v1.yaml"
DEFAULT_OUT = (
    ROOT
    / "analysis/output/kronos_architecture_transfer_20260901/sampling_stability.json"
)


def snapshot_path(cache_root: Path, repo_id: str, revision: str) -> Path:
    return cache_root / ("models--" + repo_id.replace("/", "--")) / "snapshots" / revision


def synthetic_frame(lookback: int) -> Tuple[pd.DataFrame, pd.Series]:
    """Return plausible, deterministic 15-minute OHLCV with no market-data lineage."""

    rng = np.random.default_rng(20260901)
    steps = np.arange(lookback)
    log_return = 0.00035 * np.sin(steps / 19.0) + rng.normal(0.0, 0.0018, lookback)
    close = 100.0 * np.exp(np.cumsum(log_return))
    open_ = np.r_[100.0, close[:-1]]
    wick = 0.0008 + rng.uniform(0.0, 0.0012, lookback)
    high = np.maximum(open_, close) * (1.0 + wick)
    low = np.minimum(open_, close) * (1.0 - wick)
    volume = np.exp(
        9.0 + 0.25 * np.sin(steps / 11.0) + rng.normal(0.0, 0.15, lookback)
    )
    frame = pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close, "volume": volume}
    )
    x_timestamp = pd.Series(
        pd.date_range("2024-12-27T20:15:00", periods=lookback, freq="15min")
    )
    return frame, x_timestamp


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--external-root", type=Path, default=ROOT / "external/Kronos")
    parser.add_argument(
        "--cache-root",
        type=Path,
        default=Path.home() / ".cache/huggingface/hub",
    )
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--seeds", default="101,202,303,404,505")
    args = parser.parse_args()

    contract = load_kronos_research_contract(args.config)
    seeds = [int(value) for value in args.seeds.split(",") if value.strip()]
    if len(seeds) < 2:
        raise ValueError("at least two seeds are required to measure sampling spread")

    if str(args.external_root) not in sys.path:
        sys.path.insert(0, str(args.external_root))
    import torch  # noqa: E402
    from model import Kronos, KronosPredictor, KronosTokenizer  # noqa: E402

    model_path = snapshot_path(args.cache_root, contract.model_id, contract.model_revision)
    tokenizer_path = snapshot_path(
        args.cache_root, contract.tokenizer_id, contract.tokenizer_revision
    )
    for path in (model_path, tokenizer_path):
        if not (path / "config.json").is_file() or not (path / "model.safetensors").is_file():
            raise FileNotFoundError(f"pinned offline snapshot is incomplete: {path}")

    frame, x_timestamp = synthetic_frame(contract.lookback)
    y_timestamp = pd.Series(
        pd.date_range(
            x_timestamp.iloc[-1] + pd.Timedelta(minutes=15),
            periods=contract.pred_len,
            freq="15min",
        )
    )

    load_start = time.perf_counter()
    tokenizer = KronosTokenizer.from_pretrained(str(tokenizer_path))
    model = Kronos.from_pretrained(str(model_path))
    default_training_modes = {
        "model": bool(model.training),
        "tokenizer": bool(tokenizer.training),
    }
    model.eval()
    tokenizer.eval()
    device = "mps" if torch.backends.mps.is_available() else (
        "cuda:0" if torch.cuda.is_available() else "cpu"
    )
    predictor = KronosPredictor(model, tokenizer, device=device, max_context=contract.max_context)
    load_seconds = time.perf_counter() - load_start

    profiles = (
        ("readme_t1_n1", 1.0, 1),
        ("temperature_only_t06_n1", contract.temperature, 1),
        ("paper_forecast_t06_n10", contract.temperature, contract.sample_count),
    )
    entry = float(frame["close"].iloc[-1])
    rows: List[Dict[str, Any]] = []
    for profile, temperature, sample_count in profiles:
        for seed in seeds:
            random.seed(seed)
            np.random.seed(seed)
            torch.manual_seed(seed)
            started = time.perf_counter()
            prediction = predictor.predict(
                df=frame,
                x_timestamp=x_timestamp,
                y_timestamp=y_timestamp,
                pred_len=contract.pred_len,
                T=temperature,
                top_p=contract.top_p,
                sample_count=sample_count,
                verbose=False,
            )
            wall = time.perf_counter() - started
            ohlc = prediction[["open", "high", "low", "close"]]
            values = ohlc.to_numpy(dtype=np.float64)
            invalid = int(np.sum(~np.isfinite(values)))
            invalid += int((prediction["high"] < ohlc[["open", "close"]].max(axis=1)).sum())
            invalid += int((prediction["low"] > ohlc[["open", "close"]].min(axis=1)).sum())
            invalid += int((ohlc <= 0).sum().sum())
            rows.append(
                {
                    "profile": profile,
                    "seed": seed,
                    "temperature": temperature,
                    "sample_count": sample_count,
                    "seconds": wall,
                    "end_return": float(prediction["close"].iloc[-1] / entry - 1.0),
                    "min_low_return": float(prediction["low"].min() / entry - 1.0),
                    "max_high_return": float(prediction["high"].max() / entry - 1.0),
                    "path_digest": hashlib.sha256(values.tobytes()).hexdigest(),
                    "invalid_ohlc_checks": invalid,
                }
            )

    summary: Dict[str, Any] = {}
    for profile, _, _ in profiles:
        group = [row for row in rows if row["profile"] == profile]
        end_returns = np.asarray([row["end_return"] for row in group], dtype=float)
        summary[profile] = {
            "n_seeds": len(group),
            "end_return_mean": float(end_returns.mean()),
            "end_return_std_across_seeds": float(end_returns.std(ddof=1)),
            "end_return_min": float(end_returns.min()),
            "end_return_max": float(end_returns.max()),
            "mean_seconds": float(np.mean([row["seconds"] for row in group])),
            "invalid_ohlc_checks_total": int(
                sum(row["invalid_ohlc_checks"] for row in group)
            ),
            "unique_path_digests": len({row["path_digest"] for row in group}),
        }
    readme_std = summary["readme_t1_n1"]["end_return_std_across_seeds"]
    paper_std = summary["paper_forecast_t06_n10"]["end_return_std_across_seeds"]

    receipt = {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "scope": {
            "input": "deterministic_synthetic_ohlcv_only",
            "market_data_rows_read": 0,
            "holdout_rows_read": 0,
            "training_runs": 0,
            "economic_metrics_computed": 0,
        },
        "source": {
            "upstream_commit": contract.source_commit,
            "model_revision": contract.model_revision,
            "tokenizer_revision": contract.tokenizer_revision,
        },
        "runtime": {
            "python": sys.version.split()[0],
            "numpy": np.__version__,
            "pandas": pd.__version__,
            "torch": torch.__version__,
            "device": device,
            "default_training_modes_before_explicit_eval": default_training_modes,
            "explicit_eval_applied": True,
            "load_seconds": load_seconds,
        },
        "lookback": contract.lookback,
        "pred_len": contract.pred_len,
        "profile_ladder": [profile for profile, _, _ in profiles],
        "summary": summary,
        "paper_vs_readme_seed_std_ratio": (
            float(paper_std / readme_std) if readme_std else None
        ),
        "rows": rows,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(receipt, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(json.dumps(receipt, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
