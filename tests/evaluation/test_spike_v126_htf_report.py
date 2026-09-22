"""Synthetic receipt-bound checks for the V12.6 HTF aggregate report."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pandas as pd
import pytest

from yoyo.evaluation.spike_v126_htf_report import TABLES, _matched, _metric_row, build, sha


def _write_run(root: Path) -> Path:
    config = {"start": "2025-08-01T00:00:00+00:00", "split": "2025-09-10T00:00:00+00:00", "end": "2025-11-01T00:00:00+00:00",
              "bootstrap_seed": 11, "bootstrap_reps": 2000, "permutation_seed": 12, "permutation_reps": 10000, "expected_symbols":1}
    identity = {"config": config, "symbols": ["AAA"], "inputs": {"AAA": "synthetic-input-sha"},"subset":False}; root.mkdir()
    (root / "identity.json").write_text(json.dumps(identity, sort_keys=True))
    run_hash = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
    stream = root / "streams" / "AAA"; stream.mkdir(parents=True)
    def row(arm, key, signal, exit_, r, censored=False):
        return {"arm": arm, "trade_key": key, "signal_close": signal, "exit_time": exit_, "net_r": r,
                "net_return": r / 100, "censored": censored}
    trades = pd.DataFrame([
        row("baseline", "common", "2025-08-01", "2025-08-02", 6),
        row("baseline", "cross", "2025-09-09", "2025-09-11", 1),
        row("baseline", "old_later", "2025-09-12", "2025-09-13", -1),
        row("baseline", "censored", "2025-09-14", "2025-09-15", 99, True),
        row("joint_h1_recheck", "common", "2025-08-01", "2025-08-02", 7),
        row("joint_h1_recheck", "new_later", "2025-09-12", "2025-09-13", 2),
        row("joint_h1_recheck", "new_tail", "2025-09-20", "2025-09-21", 11),
    ])
    controls = pd.DataFrame([{"arm": row.arm, "trade_key": row.trade_key, "matched": True,
                              "control_net_r": 0., "control_net_return": 0.}
                             for row in trades.itertuples() if not row.censored])
    tables = {"trades": trades, "controls": controls,
              "decisions": pd.DataFrame(), "statuses": pd.DataFrame(), "reference_boxes": pd.DataFrame(), "breaks": pd.DataFrame()}
    hashes = {}
    for name in TABLES:
        path = stream / f"{name}.csv.gz"; tables[name].to_csv(path, index=False, compression={"method": "gzip", "mtime": 0})
        hashes[path.name] = sha(path)
    receipt = {"status": "complete", "run_identity": run_hash, "input_sha256": "synthetic-input-sha", "files": hashes}
    receipt_path = stream / "receipt.json"; receipt_path.write_text(json.dumps(receipt, sort_keys=True))
    manifest = {"complete": True, "run_identity": run_hash, "symbols": ["AAA"], "receipts": {"AAA": sha(receipt_path)}}
    (root / "manifest.json").write_text(json.dumps(manifest, sort_keys=True))
    return root


def test_time_cutoff_censoring_win_tail_and_paired_accounting(tmp_path):
    output = tmp_path / "report"; build(_write_run(tmp_path / "run"), output)
    metrics = pd.read_csv(output / "metrics.csv")
    baseline = metrics.loc[(metrics.arm == "baseline") & (metrics.period == "full")].iloc[0]
    earlier = metrics.loc[(metrics.arm == "baseline") & (metrics.period == "earlier")].iloc[0]
    later = metrics.loc[(metrics.arm == "baseline") & (metrics.period == "later")].iloc[0]
    assert baseline.n == 3  # censored excluded, cross-split still belongs in full
    assert earlier.n == 1 and later.n == 1  # cross split is excluded from both maturity cohorts
    assert baseline.wins == 2 and baseline.gt5_final_net_r == 1 and baseline.matched_n == 3
    assert baseline.drawdown_basis == "exit_ordered_event_r_nonaccount"
    tails = pd.read_csv(output / "tail_retention.csv")
    full = tails.loc[(tails.period == "full") & (tails.threshold == ">5R")].iloc[0]
    assert (full.baseline_tail_n, full.treatment_tail_n, full.intersection_event_keys, full.new_event_keys) == (1, 2, 1, 1)
    paired = pd.read_csv(output / "paired_attribution.csv")
    assert paired.loc[paired.period.eq("full"), "common_event_keys"].iloc[0] == 1


def test_zero_month_bootstrap_and_inference_are_deterministic(tmp_path):
    run = _write_run(tmp_path / "run")
    one, two = tmp_path / "one", tmp_path / "two"
    build(run, one); build(run, two)
    monthly = pd.read_csv(one / "monthly.csv")
    assert (monthly.loc[monthly.month.eq("2025-10"), "n"] == 0).all()
    first, second = pd.read_csv(one / "inference.csv"), pd.read_csv(two / "inference.csv")
    pd.testing.assert_frame_equal(first, second)
    bootstrap = first.loc[first.method.eq("month_bootstrap")]
    assert set(bootstrap.period) == {"full", "earlier", "later"}
    assert len(bootstrap) == 12 and bootstrap.zero_trade_months_included.all()
    weekly = first.loc[first.method.eq("weekly_signflip_approximate")]
    assert len(weekly) == 4 and weekly.p_holm.notna().all()
    quality = weekly.loc[weekly.metric.eq("treatment_minus_baseline_mean_r")].iloc[0]
    assert "pooled_mean" in quality.formula


def test_earlier_paired_control_requires_its_own_mature_exit():
    frame = pd.DataFrame({"trade_key": ["x"], "arm": ["baseline"], "event_key": ["x"], "net_r": [1.], "net_bp": [100.],
                          "signal_close": [pd.Timestamp("2025-09-01", tz="UTC")], "exit_time": [pd.Timestamp("2025-09-02", tz="UTC")],
                          "month": ["2025-09"], "week": ["2025-09-01"], "cohort": ["earlier"]})
    controls = pd.DataFrame({"trade_key": ["x"], "arm": ["baseline"], "matched": [True], "control_net_r": [1.],
                             "control_net_return": [.01], "control_exit_time": ["2025-09-11T00:00:00Z"]})
    assert _matched(frame, controls, pd.Timestamp("2025-09-10", tz="UTC")).empty
    # A control settled later than the split is nevertheless mature for the
    # full report window, so it must not be silently removed there.
    split=pd.Timestamp("2025-09-10",tz="UTC")
    assert _metric_row(frame,controls,'baseline','full',split)['matched_n']==1
    assert _metric_row(frame,controls,'baseline','earlier',split)['matched_n']==0


def test_changed_ledger_is_rejected_before_any_summary(tmp_path):
    run=_write_run(tmp_path / 'run')
    ledger=run / 'streams/AAA/trades.csv.gz'
    ledger.write_bytes(ledger.read_bytes()+b'changed')
    output=tmp_path / 'report'
    with pytest.raises(ValueError,match='ledger drift'):
        build(run,output)
    assert not output.exists()


def test_receipt_complete_smoke_is_not_full_research(tmp_path):
    run=_write_run(tmp_path / 'run')
    identity=json.loads((run/'identity.json').read_text());identity['subset']=True
    (run/'identity.json').write_text(json.dumps(identity))
    run_hash=hashlib.sha256(json.dumps(identity,sort_keys=True).encode()).hexdigest()
    receipt_path=run/'streams/AAA/receipt.json'
    receipt=json.loads(receipt_path.read_text());receipt['run_identity']=run_hash
    receipt_path.write_text(json.dumps(receipt))
    manifest=json.loads((run/'manifest.json').read_text());manifest['run_identity']=run_hash
    manifest['receipts']['AAA']=sha(receipt_path)
    (run/'manifest.json').write_text(json.dumps(manifest))
    with pytest.raises(ValueError,match='full frozen universe'):
        build(run,tmp_path/'report')
    assert not (tmp_path/'report').exists()


def test_changed_split_cannot_relabel_frozen_control_strata(tmp_path):
    run=_write_run(tmp_path / 'run')
    identity=json.loads((run/'identity.json').read_text())
    identity['config']['split']='2025-10-01T00:00:00+00:00'
    (run/'identity.json').write_text(json.dumps(identity))
    run_hash=hashlib.sha256(json.dumps(identity,sort_keys=True).encode()).hexdigest()
    receipt_path=run/'streams/AAA/receipt.json'
    receipt=json.loads(receipt_path.read_text());receipt['run_identity']=run_hash
    receipt_path.write_text(json.dumps(receipt))
    manifest=json.loads((run/'manifest.json').read_text());manifest['run_identity']=run_hash
    manifest['receipts']['AAA']=sha(receipt_path)
    (run/'manifest.json').write_text(json.dumps(manifest))
    with pytest.raises(ValueError,match='frozen split'):
        build(run,tmp_path/'report')
    assert not (tmp_path/'report').exists()
