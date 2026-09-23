"""Independent formula checks, causality and data boundaries for imported factors."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from yoyo.research_workspace.open_factors import VERSION, compute_factors


def market_bars(n=80):
    rng = np.random.default_rng(91024)
    close = 100 * np.exp(np.cumsum(rng.normal(0, 0.015, n)))
    opening = close * np.exp(rng.normal(0, 0.004, n))
    return pd.DataFrame({
        "open": opening, "high": np.maximum(opening, close) + rng.uniform(0.2, 2, n),
        "low": np.minimum(opening, close) - rng.uniform(0.2, 2, n), "close": close,
        "volume": rng.uniform(20, 1000, n),
    }, index=pd.date_range("2026-01-01", periods=n, freq="15min", tz="UTC"))


def test_matches_pinned_qlib_expressions_with_independent_numpy_windows():
    bars = market_bars()
    result = compute_factors(bars)
    for t in range(20, len(bars)):
        o, h, l, c, v = (bars[key].to_numpy() for key in ("open", "high", "low", "close", "volume"))
        cw, vw = c[t-19:t+1], v[t-19:t+1]
        delta, vdelta = np.diff(c[t-20:t+1]), np.diff(v[t-20:t+1])
        ratio = c[t-19:t+1] / c[t-20:t]
        logvratio = np.log(v[t-19:t+1] / v[t-20:t] + 1)
        pv = np.abs(ratio - 1) * vw
        spread = h[t] - l[t] + 1e-12
        expected = {
            "kmid2": (c[t] - o[t]) / spread,
            "kup2": (h[t] - max(o[t], c[t])) / spread,
            "klow2": (min(o[t], c[t]) - l[t]) / spread,
            "ksft2": (2*c[t] - h[t] - l[t]) / spread,
            "std20": np.std(cw, ddof=1) / c[t],
            "rsv20": (c[t] - min(l[t-19:t+1])) / (max(h[t-19:t+1]) - min(l[t-19:t+1]) + 1e-12),
            "corr20": np.corrcoef(cw, np.log(vw + 1))[0, 1],
            "cord20": np.corrcoef(ratio, logvratio)[0, 1],
            "sumd20": delta.sum() / (np.abs(delta).sum() + 1e-12),
            "vstd20": np.std(vw, ddof=1) / (v[t] + 1e-12),
            "wvma20": np.std(pv, ddof=1) / (pv.mean() + 1e-12),
            "vsumd20": vdelta.sum() / (np.abs(vdelta).sum() + 1e-12),
        }
        for key, value in expected.items():
            assert result.iloc[t]["oss.qlib." + key] == pytest.approx(value, abs=2e-10)
        true_ranges = [max(h[k]-l[k], abs(h[k]-c[k-1]), abs(l[k]-c[k-1])) for k in range(t-13, t+1)]
        chop = 100 * np.log10(sum(true_ranges) / (max(h[t-13:t+1]) - min(l[t-13:t+1]))) / np.log10(14)
        cmf = sum(((2*c[k]-h[k]-l[k])/(h[k]-l[k])) * v[k] for k in range(t-19, t+1)) / sum(vw)
        assert result.iloc[t]["oss.qtpylib.chop14"] == pytest.approx(chop, abs=2e-10)
        assert result.iloc[t]["oss.ta.cmf20"] == pytest.approx(cmf, abs=2e-10)


def test_future_changes_and_appends_never_change_existing_values():
    bars = market_bars()
    full = compute_factors(bars)
    for cut in (1, 19, 20, 21, 45):
        pd.testing.assert_frame_equal(compute_factors(bars.iloc[:cut]), full.iloc[:cut])
    changed = bars.copy()
    changed.iloc[45:, :4] *= 8
    changed.iloc[45:, 4] *= 100
    pd.testing.assert_frame_equal(compute_factors(changed).iloc[:45], full.iloc[:45])


def test_metadata_exactly_covers_calculations_and_full_warmup():
    path = Path(__file__).resolve().parents[2] / "yoyo/research_workspace/open_factors.json"
    manifest = json.loads(path.read_text())
    result = compute_factors(market_bars())
    assert manifest["implementation_version"] == VERSION
    assert set(result.columns) == {f["id"] for f in manifest["factors"]}
    for factor in manifest["factors"]:
        series = result[factor["id"]]
        first = factor["warmup_bars"] - 1
        assert series.iloc[:first].isna().all()
        assert pd.notna(series.iloc[first])
        assert not factor["training_eligible"] and not factor["production_eligible"]
        assert factor["stage"] == "hypothesis"


def test_flat_prices_zero_volume_and_undefined_correlations():
    bars = market_bars(50)
    bars.loc[:, ["open", "high", "low", "close"]] = 10.0
    bars.loc[:, "volume"] = 0.0
    result = compute_factors(bars)
    assert not np.isinf(result.to_numpy()).any()
    assert result["oss.qlib.corr20"].isna().all()
    assert result["oss.qlib.cord20"].isna().all()
    assert (result["oss.qlib.kmid2"] == 0).all()
    assert (result["oss.qlib.sumd20"].dropna() == 0).all()
    assert (result["oss.qlib.vstd20"].dropna() == 0).all()
    assert result["oss.qtpylib.chop14"].isna().all()
    assert result["oss.ta.cmf20"].isna().all()
    bars["volume"] = 100.0
    assert (compute_factors(bars)["oss.ta.cmf20"].dropna() == 0).all()
    varying = market_bars(50)
    varying.iloc[21, varying.columns.get_loc("volume")] = 0
    values = compute_factors(varying)
    assert values["oss.qlib.cord20"].iloc[22:42].isna().all()
    assert pd.notna(values["oss.qlib.cord20"].iloc[42])


@pytest.mark.parametrize("problem", ["reverse", "duplicate", "gap", "nan", "negative_volume", "geometry", "mixed", "no_time"])
def test_rejects_ambiguous_or_invalid_input(problem):
    bars = market_bars()
    if problem == "reverse":
        bars = bars.iloc[::-1]
    elif problem == "duplicate":
        bars = pd.concat([bars.iloc[:1], bars])
    elif problem == "gap":
        bars = bars.drop(bars.index[20])
    elif problem == "nan":
        bars.iloc[30, 4] = np.nan
    elif problem == "negative_volume":
        bars.iloc[30, 4] = -1
    elif problem == "geometry":
        bars.iloc[30, 1] = bars.iloc[30, 2] - 1
    elif problem == "mixed":
        bars["symbol"] = ["A"] * 40 + ["B"] * 40
    elif problem == "no_time":
        bars = bars.reset_index(drop=True)
    with pytest.raises(ValueError):
        compute_factors(bars)


def test_cli_exports_real_values_and_receipt_without_overwriting(tmp_path):
    bars = market_bars(30)
    source, output, receipt = (tmp_path / name for name in ("bars.csv", "features.csv", "receipt.json"))
    bars.to_csv(source, index_label="timestamp")
    command = [sys.executable, "-m", "yoyo.research_workspace.open_factors", "--input", str(source),
               "--output", str(output), "--receipt", str(receipt)]
    run = subprocess.run(command, capture_output=True, text=True)
    assert run.returncode == 0, run.stderr
    meta = json.loads(receipt.read_text())
    assert meta["rows"] == 30 and len(meta["coverage"]) == 14
    assert meta["training_eligible"] is False and meta["production_eligible"] is False
    exported = pd.read_csv(output, index_col=0)
    np.testing.assert_allclose(exported.to_numpy(), compute_factors(bars).to_numpy(), atol=2e-9, equal_nan=True)
    before = output.read_bytes()
    assert subprocess.run(command, capture_output=True).returncode != 0
    assert output.read_bytes() == before
