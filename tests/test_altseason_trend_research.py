"""Synthetic integration regressions; never load project market data."""
import json
import numpy as np
import pandas as pd
import pytest

from yoyo.evaluation import altseason_trend_research as r


def artificial_bars(n=60):
    idx = pd.date_range("2024-01-01", periods=n, freq="4h", tz="UTC")
    close = 100 + np.arange(n) * .1
    return pd.DataFrame({"open": close, "high": close + .5, "low": close - .5,
                         "close": close, "volume": 1.}, index=idx)


def artificial_features(bars, ready=True):
    return pd.DataFrame({"ready": ready, "atr": 1., "prior_high20": bars.close - .1,
                         "prior_low10": bars.close - 10., "ewmac_forecast": 1.,
                         "donchian_signal": True, "ewmac_signal": True, "vol_bucket": 0,
                         "regime": "strong", "breakout_score": .1}, index=bars.index)


def test_candidate_daily_clock_and_both_fold_edges():
    f = artificial_features(artificial_bars())
    d, _ = r.candidate_indexes(f, 6, 30, "D")
    e, _ = r.candidate_indexes(f, 6, 30, "E")
    assert d.min() == 6 and d.max() == 29
    assert set(e) == {11, 17, 23, 29}
    assert (f.index[e].hour == 20).all()


def test_cash_allocation_fixed_denominator_and_causal_state_reconciliation():
    idx = pd.date_range("2024-01-01", periods=3, freq="4h", tz="UTC")
    frame = pd.DataFrame({"equity": [1.1, 1.05, 1.2], "notional": 0.}, index=idx)
    curve = r.aggregate_sleeves({"a": frame}, ["a", "b"], idx[0], idx[-1] + r.STEP)
    assert curve.equity.tolist() == pytest.approx([1.05, 1.025, 1.1])
    regidx = pd.date_range(idx[0] - r.STEP, periods=4, freq="4h")
    reg = pd.DataFrame({"strong": pd.array([True, False, True, False], dtype="boolean")}, index=regidx)
    state = {v["state"]: v["pnl_contribution_pp"] for v in r.state_contributions(curve, reg)}
    assert state == pytest.approx({"strong": 12.5, "other": -2.5, "unknown": 0.})


def test_positive_overlapping_events_cannot_pass_negative_actual_cash_gate():
    summary, portfolios = [], []
    for fold, _, _ in r.FOLDS:
        for arm in r.ARMS:
            summary.append(dict(fold=fold, arm=arm, cohort="all", mean_net_bp=600,
                                block_risk_excess_bp=2, top_mean_net_bp=100, top_p_holm=.001,
                                matched_n=100, matched_symbols=12, n_blocks=12))
            portfolios.append(dict(fold=fold, portfolio=arm, net_pct=-.03))
    verdicts = r.gate_verdicts(pd.DataFrame(summary), portfolios)
    assert not any(v["research_gate_pass"] for v in verdicts)
    assert all(not v["checks"]["both_folds_positive_actual_portfolio_net"] for v in verdicts)


@pytest.mark.parametrize("ready", [False, True])
def test_entire_runner_writes_honest_empty_or_trading_evidence(tmp_path, monkeypatch, ready):
    bars = artificial_bars()
    bars.attrs["source_receipt"] = {"synthetic": True}
    records = [{"symbol": s} for s in ["BTC", "ETH"] + [f"ALT{i:02d}" for i in range(52)]]
    folds = [("development", "2024-01-01T00:00:00Z", "2024-01-06T00:00:00Z"),
             ("validation", "2024-01-06T00:00:00Z", "2024-01-11T00:00:00Z")]
    monkeypatch.setattr(r, "FOLDS", folds)
    monkeypatch.setattr(r, "committed_sources", lambda: {"generator_commit": "synthetic", "source_hashes": {}})
    monkeypatch.setattr(r, "load_universe", lambda path: records)
    monkeypatch.setattr(r, "load_bars", lambda record: bars.copy())
    monkeypatch.setattr(r, "build_features", lambda b: artificial_features(b, ready=ready))
    monkeypatch.setattr(r, "market_regime", lambda f: pd.DataFrame({"strong": True}, index=bars.index))
    manifest_path = tmp_path / "input.json"
    manifest_path.write_text("{}")
    outdir = tmp_path / "results"
    r.run(outdir, manifest_path)
    manifest = json.loads((outdir / "manifest.json").read_text())
    assert manifest["holdout_consumed"] is False
    assert not any(v["research_gate_pass"] for v in manifest["verdicts"])
    for name, receipt in manifest["artifacts"].items():
        assert r.sha(outdir / name) == receipt["sha256"]
    portfolios = pd.read_csv(outdir / "portfolios.csv")
    events = pd.read_csv(outdir / "events.csv.gz")
    ledger = pd.read_csv(outdir / "portfolio_ledger.csv.gz")
    pairs = pd.read_csv(outdir / "same_entry_exit_pairs.csv.gz")
    summary = pd.read_csv(outdir / "event_summary.csv")
    assert len(summary) == 24 and len(portfolios) == 14
    if not ready:
        assert events.empty and ledger.empty
        assert portfolios.net_pct.eq(0).all()
        assert summary.matched_n.eq(0).all()
    else:
        assert len(pairs) and pairs.valid_pair.all()
        assert pairs.delta_net_return.eq(0).all()
        assert ledger.accepted.any() and (~ledger.accepted).any()
        assert summary.loc[summary.cohort.eq("all"), "matched_n"].gt(0).all()
    with pytest.raises(ValueError, match="already exists"):
        r.run(outdir, manifest_path)
    # Exercise every report table/chart from the same generated synthetic files.
    # Only the external converter is stubbed; its real rendering is checked on
    # the deliverable. No report or market data is written to the real project.
    from yoyo.evaluation import altseason_trend_report as report
    monkeypatch.setattr(report, "ROOT", tmp_path)
    report_dir = tmp_path / "analysis"
    report_dir.mkdir()
    (report_dir / "html").mkdir()
    report_path = report_dir / "synthetic.md"
    def fake_converter(*args, **kwargs):
        (report_dir / "html/synthetic.html").write_text("<p>Synthetic converter placeholder</p>")
    monkeypatch.setattr(report.subprocess, "run", fake_converter)
    receipt = report.build(outdir, report_path)
    assert report_path.exists() and len(receipt["charts"]) == 3
    assert "全部月份" in report_path.read_text()
