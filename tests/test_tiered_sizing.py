"""Tiered sizing (owner 2026-07-20): tier mapping, sidecar loading, and
executor multiplier behaviour.

Source experiment: analysis/p_weight_centric_val.md — val-score quantile bands
[q90,q95) / [q95,q99) / q99+ → 1x / 1.5x / 2x notional. These tests pin the
three live-money invariants: boundary scores land in the right band,
below-threshold scores can never size a position, and legacy forward-log rows
(pre-tier columns) keep trading the historic 1x.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import pandas as pd
import pytest

from src.execution.config import ExecutorConfig
from src.execution.executor import run_once, signal_size_mult
from src.judgment.features import FEATURE_COLUMNS
from src.judgment.frozen import (
    FrozenConfig,
    SizingTiers,
    latest_artifact,
)
from src.judgment.forward_records import merge_forward_log, normalize_log

Q90 = 0.02022141847381547
Q95 = 0.025479598857399685
Q99 = 0.04856856696929689
TIERS = SizingTiers(q95=Q95, q99=Q99)


class TestTierMapping:
    def test_exact_boundaries_belong_to_upper_band(self) -> None:
        # bands are [lo, hi): a score exactly on an edge takes the higher tier,
        # same as np.where(scores >= q, ...) in the experiment
        assert TIERS.tier_for_score(Q90, Q90) == ("q90_q95", 1.0)
        assert TIERS.tier_for_score(Q95, Q90) == ("q95_q99", 1.5)
        assert TIERS.tier_for_score(Q99, Q90) == ("q99_plus", 2.0)

    def test_interior_scores(self) -> None:
        assert TIERS.tier_for_score(0.021, Q90) == ("q90_q95", 1.0)
        assert TIERS.tier_for_score(0.03, Q90) == ("q95_q99", 1.5)
        assert TIERS.tier_for_score(0.9, Q90) == ("q99_plus", 2.0)

    def test_below_threshold_never_sizes(self) -> None:
        tier, mult = TIERS.tier_for_score(Q90 - 1e-12, Q90)
        assert tier == "below_q90" and mult == 0.0

    def test_nan_score_never_sizes(self) -> None:
        tier, mult = TIERS.tier_for_score(float("nan"), Q90)
        assert tier == "below_q90" and mult == 0.0


class TestSidecarLoading:
    def _write_artifact(self, project_dir: Path, extra: dict | None = None) -> FrozenConfig:
        models_dir = project_dir / "models"
        models_dir.mkdir(exist_ok=True)
        stem = "frozen_tp5_sl2_swap_20260720"
        (models_dir / f"{stem}.txt").write_text("fake model\n", encoding="utf-8")
        meta = {
            "artifact_version": 1,
            "config": "tp5_sl2_swap",
            "model_path": f"models/{stem}.txt",
            "dataset_path": "data/ds.csv",
            "dataset_sha256": "abc",
            "dataset_size_bytes": 1,
            "threshold_val_q90": Q90,
            "score_quantile": 0.9,
            "feature_columns": list(FEATURE_COLUMNS),
            "best_iteration": 1,
        }
        meta.update(extra or {})
        (models_dir / f"{stem}.json").write_text(json.dumps(meta), encoding="utf-8")
        return FrozenConfig(
            name="tp5_sl2_swap",
            project_dir=project_dir,
            dataset_path=project_dir / "data" / "ds.csv",
            models_dir=models_dir,
            score_quantile=0.9,
            horizon_bars=72,
            objective="binary",
        )

    def test_sidecar_without_tiers_loads_as_none(self, tmp_path: Path) -> None:
        config = self._write_artifact(tmp_path)
        artifact = latest_artifact(config)
        assert artifact is not None
        assert artifact.sizing_tiers is None

    def test_sidecar_with_tiers_loads_edges(self, tmp_path: Path) -> None:
        config = self._write_artifact(
            tmp_path, {"sizing_tiers": {"q95": Q95, "q99": Q99}}
        )
        artifact = latest_artifact(config)
        assert artifact is not None
        assert artifact.sizing_tiers == SizingTiers(q95=Q95, q99=Q99)

    def test_unordered_tiers_rejected(self, tmp_path: Path) -> None:
        config = self._write_artifact(
            tmp_path, {"sizing_tiers": {"q95": Q99, "q99": Q95}}
        )
        # latest_artifact skips corrupt sidecars rather than crashing callers
        assert latest_artifact(config) is None

    def test_live_v11_sidecar_carries_the_experiment_edges(self) -> None:
        meta = json.loads(
            (Path(__file__).resolve().parents[1]
             / "models" / "frozen_tp5_sl2_swap_yolo_v11_reg_20260718.json"
            ).read_text(encoding="utf-8")
        )
        tiers = meta["sizing_tiers"]
        # rounded values must match analysis/output/p_weight_centric_val.json
        assert round(tiers["q95"], 5) == 0.02548
        assert round(tiers["q99"], 5) == 0.04857


class TestForwardScanStamping:
    """The forward pulse must stamp tier/size_mult on new rows at detection."""

    def _synthetic_frame(self, n_bars: int = 400) -> pd.DataFrame:
        import numpy as np

        rng = np.random.default_rng(7)
        open_time = pd.date_range("2026-07-01", periods=n_bars, freq="15min", tz="UTC")
        base = 100 + np.cumsum(rng.normal(0, 0.35, n_bars))
        spread = np.abs(rng.normal(0.4, 0.1, n_bars)) + 0.2
        opens = base
        closes = base + rng.normal(0, 0.25, n_bars)
        return pd.DataFrame({
            "ts": (open_time.view("int64") // 10**6),
            "open": opens,
            "high": pd.Series(opens).combine(pd.Series(closes), max) + spread,
            "low": pd.Series(opens).combine(pd.Series(closes), min) - spread,
            "close": closes,
            "volume": abs(rng.normal(1000, 100, n_bars)),
            "open_time": open_time.astype(str),
        })

    def _scan(self, monkeypatch: pytest.MonkeyPatch, artifact) -> list[dict]:
        import numpy as np
        import types

        import src.judgment.forward_scan as fs
        from src.judgment.forward_types import ForwardScanInput

        frame = self._synthetic_frame()
        booster = types.SimpleNamespace(
            predict=lambda rows, num_iteration=None: np.full(len(rows), 0.05)
        )
        monkeypatch.setattr(fs, "CANDIDATE_SOURCE", "rules")
        monkeypatch.setattr(
            fs, "iter_series", lambda **kw: iter([("okx", "TESTCOIN_USDT_SWAP", frame)])
        )
        monkeypatch.setattr(
            fs, "forward_candidate_indices", lambda enriched, **kw: [len(frame) - 2]
        )
        result = fs.scan_forward_records(
            ForwardScanInput(
                artifact=artifact,
                booster=booster,
                detected_at="2026-07-20T00:00:00+00:00",
                start_time=pd.Timestamp("2026-07-01", tz="UTC"),
                existing_log=pd.DataFrame(),
            )
        )
        return result.records

    def _artifact(self, tiers):
        import types

        return types.SimpleNamespace(
            threshold=Q90,
            relative_model_path="models/stub.txt",
            dataset_sha256="stub",
            model_path="models/stub.txt",
            best_iteration=1,
            sizing_tiers=tiers,
        )

    def test_records_carry_tier_from_artifact(self, monkeypatch: pytest.MonkeyPatch) -> None:
        records = self._scan(monkeypatch, self._artifact(TIERS))
        assert records, "synthetic scan must yield a record"
        # stub booster scores 0.05 > q99=0.0486 → top tier
        assert records[0]["tier"] == "q99_plus"
        assert records[0]["size_mult"] == 2.0

    def test_artifact_without_tiers_stamps_legacy_1x(self, monkeypatch: pytest.MonkeyPatch) -> None:
        records = self._scan(monkeypatch, self._artifact(None))
        assert records
        assert records[0]["tier"] == ""
        assert records[0]["size_mult"] == 1.0


class TestForwardLogBackCompat:
    _LEGACY_COLUMNS = [
        "source", "symbol", "signal_time", "detected_at", "status", "score",
        "threshold", "model_path", "dataset_sha256", "signal_i", "entry_time",
        "entry_price", "maker_filled", "outcome", "label", "exit_offset",
        "exit_time", "realized_ret", "atr_pct", "dense_run_len",
    ]

    def _legacy_row(self) -> dict:
        return {
            "source": "okx", "symbol": "BTC_USDT_SWAP",
            "signal_time": "2026-07-18 17:00:00+00:00",
            "detected_at": "first-seen", "status": "open", "score": 0.03,
            "threshold": Q90, "model_path": "models/frozen.txt",
            "dataset_sha256": "abc", "signal_i": 1,
            "entry_time": "2026-07-18 17:15:00+00:00", "entry_price": 100.0,
            "maker_filled": True, "outcome": "", "label": -1,
            "exit_offset": 0, "exit_time": "", "realized_ret": math.nan,
            "atr_pct": 0.01, "dense_run_len": 8,
        }

    def test_legacy_log_normalizes_with_nan_tier(self) -> None:
        legacy = pd.DataFrame([self._legacy_row()], columns=self._LEGACY_COLUMNS)
        out = normalize_log(legacy)
        assert "tier" in out.columns and "size_mult" in out.columns
        assert pd.isna(out.iloc[0]["tier"]) and pd.isna(out.iloc[0]["size_mult"])

    def test_merge_keeps_legacy_row_untiered_when_closed(self) -> None:
        legacy = pd.DataFrame([self._legacy_row()], columns=self._LEGACY_COLUMNS)
        update = dict(self._legacy_row())
        update.update({
            "status": "closed", "outcome": "tp", "label": 1, "exit_offset": 2,
            "exit_time": "2026-07-18 17:45:00+00:00", "realized_ret": 0.05,
            "tier": "q95_q99", "size_mult": 1.5,
        })
        result = merge_forward_log(legacy, [update])
        row = result.frame.iloc[0]
        assert row["status"] == "closed"
        # a later tiered pulse must not retro-stamp a tier on a legacy entry
        assert pd.isna(row["size_mult"])

    def test_executor_reads_legacy_row_as_1x(self) -> None:
        assert signal_size_mult(pd.Series(self._legacy_row())) == 1.0
        row_nan = dict(self._legacy_row(), tier=math.nan, size_mult=math.nan)
        assert signal_size_mult(pd.Series(row_nan)) == 1.0


class TestExecutorSizing:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            (1.0, 1.0), (1.5, 1.5), (2.0, 2.0),
            ("1.5", 1.5),          # CSV round-trip as string
            (3.0, 2.0),            # corrupt log capped at approved max
            (-1.0, 0.0),           # negative can only shrink, never flip
            ("garbage", 1.0), (None, 1.0), (math.nan, 1.0),
        ],
    )
    def test_signal_size_mult(self, raw, expected) -> None:
        assert signal_size_mult(pd.Series({"size_mult": raw})) == expected

    def _write_forward_log(self, path: Path, size_mult: float | None) -> None:
        now = pd.Timestamp.now(tz="UTC")
        row = {
            "source": "okx", "symbol": "BTC_USDT_SWAP",
            "signal_time": str(now - pd.Timedelta(minutes=5)),
            "detected_at": str(now), "status": "open", "score": 0.05,
            "threshold": Q90, "model_path": "m", "dataset_sha256": "s",
            "signal_i": 1, "entry_time": str(now), "entry_price": 100.0,
            "maker_filled": True, "outcome": "", "label": -1,
            "exit_offset": 0, "exit_time": "", "realized_ret": "",
            "atr_pct": 0.01, "dense_run_len": 8,
        }
        if size_mult is not None:
            row["tier"] = "q99_plus"
            row["size_mult"] = size_mult
        pd.DataFrame([row]).to_csv(path, index=False)

    def _config(self, tmp_path: Path, log: Path, **kwargs) -> ExecutorConfig:
        base = dict(
            sizing_mode="fixed",
            notional_usdt=10.0,
            forward_log=str(log),
            ledger=str(tmp_path / "ledger.jsonl"),
            kill_switch_file=str(tmp_path / "KILL"),
        )
        base.update(kwargs)
        return ExecutorConfig(**base)

    @pytest.mark.parametrize(
        ("size_mult", "expected_notional"),
        [
            (1.0, 5.0),    # unit * 1x
            (1.5, 7.5),    # unit * 1.5x
            (2.0, 10.0),   # unit * 2x == full slot budget
        ],
    )
    def test_dry_run_headroom_tiers(
        self, tmp_path: Path, size_mult: float, expected_notional: float
    ) -> None:
        """Owner option ①: unit = base/2, then × tier — real notional, not bookkeeping."""
        log = tmp_path / "forward_log.csv"
        self._write_forward_log(log, size_mult=size_mult)
        cfg = self._config(tmp_path, log)
        summary = run_once(cfg, dry_run=True)
        assert summary["opened"] == 1
        sizing = summary["last_sizing"]
        assert sizing["size_mult"] == size_mult
        assert sizing["base_notional_usdt"] == 10.0
        assert sizing["unit_notional_usdt"] == 5.0
        assert sizing["notional_usdt"] == expected_notional
        assert sizing["tier_headroom"] is True

    def test_dry_run_applies_tier_multiplier(self, tmp_path: Path) -> None:
        log = tmp_path / "forward_log.csv"
        self._write_forward_log(log, size_mult=2.0)
        cfg = self._config(tmp_path, log)
        summary = run_once(cfg, dry_run=True)
        assert summary["opened"] == 1
        sizing = summary["last_sizing"]
        assert sizing["size_mult"] == 2.0
        assert sizing["base_notional_usdt"] == 10.0
        # Headroom option ①: unit = base / 2 so q99+ fills the slot budget.
        assert sizing["unit_notional_usdt"] == 5.0
        assert sizing["notional_usdt"] == 10.0  # unit * 2x
        assert sizing["tier_headroom"] is True

    def test_dry_run_legacy_log_stays_1x(self, tmp_path: Path) -> None:
        log = tmp_path / "forward_log.csv"
        self._write_forward_log(log, size_mult=None)
        cfg = self._config(tmp_path, log)
        summary = run_once(cfg, dry_run=True)
        assert summary["opened"] == 1
        assert summary["last_sizing"]["size_mult"] == 1.0
        # Legacy mult=1x still uses unit sizing so max tier can fit later.
        assert summary["last_sizing"]["notional_usdt"] == 5.0

    def test_vps_style_margin_2x_fits_equity(self) -> None:
        """Mirror VPS arithmetic: max_concurrent=1, lev=3, unit=budget/2.

        2x notional margin must be ≤ equity so OKX 51008 cannot fire on q99+.
        """
        from src.execution.executor import TIER_SIZE_MULT_CAP

        equity = 92.46246113495992
        leverage = 3.0
        base = equity * leverage  # full-slot budget under equity_times_leverage
        unit = base / TIER_SIZE_MULT_CAP
        table = {
            1.0: (unit * 1.0, unit * 1.0 / leverage),
            1.5: (unit * 1.5, unit * 1.5 / leverage),
            2.0: (unit * 2.0, unit * 2.0 / leverage),
        }
        assert abs(table[1.0][0] - 138.6937) < 0.01
        assert abs(table[1.5][0] - 208.0406) < 0.01
        assert abs(table[2.0][0] - 277.3874) < 0.01
        assert table[2.0][1] <= equity + 1e-9
        # Without headroom, naive base*2 would need 2× equity as margin → reject.
        naive_2x_margin = (base * 2.0) / leverage
        assert naive_2x_margin > equity
