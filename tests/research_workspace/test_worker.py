"""Prove job state and authenticated replay summaries without network or scanning."""
import hashlib
import json
from pathlib import Path

import pandas as pd
import pytest

from yoyo.research_workspace.store import WorkspaceStore
from yoyo.research_workspace.worker import digest, run, save, summarize_replay


def test_queue_persists_cancel_and_failure_without_deleting_prior_runs(tmp_path):
    store = WorkspaceStore(tmp_path / "runtime")
    first = store.create_job("exp-a", "verify-evidence", {}, tmp_path / "runs")
    second = store.create_job("exp-b", "verify-evidence", {}, tmp_path / "runs")
    store.cancel(second["id"])
    claimed = WorkspaceStore(store.runtime).claim()
    assert claimed["id"] == first["id"] and claimed["status"] == "running"
    store.finish(first["id"], "failed", "source missing")
    assert store.claim() is None
    assert {j["status"] for j in store.jobs()} == {"failed", "cancelled"}
    assert store.job(first["id"])["error"] == "source missing"


def test_recovery_marks_abandoned_running_as_interrupted(tmp_path):
    store = WorkspaceStore(tmp_path)
    job = store.create_job("exp-a", "verify-evidence", {}, tmp_path / "runs")
    store.claim()
    store.recover()
    assert store.job(job["id"])["status"] == "interrupted"
    assert store.claim() is None


def replay_fixture(tmp_path):
    run_dir = tmp_path / "replay"
    stream = run_dir / "streams/ETHUSDT_15m"
    stream.mkdir(parents=True)
    cfg = {"split": "2026-08-23T00:00:00Z", "stat_seed": 1, "bootstrap": 100}
    identity = {"config": cfg, "symbols": ["ETHUSDT"], "timeframes": [15], "subset": True}
    run_hash = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
    trades = []
    controls = []
    for i, (when, net_r, censored) in enumerate([
        ("2026-08-01T00:00:00Z", 1., False), ("2026-08-09T00:00:00Z", -1., False),
        ("2026-09-01T00:00:00Z", 2., False), ("2026-09-08T00:00:00Z", 100., True),
    ]):
        trades.append(dict(trade_key=str(i), symbol="ETHUSDT", timeframe_min=15, arm="v9_both", side=1,
                           signal_close=when, entry_time=when, exit_time=when, censored=censored,
                           net_r=net_r, gross_r=net_r+.2, net_return=net_r*.01,
                           gross_return=net_r*.01+.002, initial_risk_frac=.01,
                           exit_reason="initial_stop" if net_r<0 else "trailing_stop", mfe_r=3., close_peak_r=2.))
        controls.append(dict(trade_key=str(i), matched=not censored, control_net_return=.005,
                             control_exit_time=when, timeframe_min=15, arm="v9_both"))
    pd.DataFrame(trades).to_csv(stream / "trades.csv.gz", index=False)
    pd.DataFrame(controls).to_csv(stream / "controls.csv.gz", index=False)
    receipt = {"status": "complete", "run_identity": run_hash,
               "files": {p.name: digest(p) for p in stream.iterdir()}}
    save(stream / "receipt.json", receipt)
    save(run_dir / "identity.json", identity)
    save(run_dir / "manifest.json", {"complete": False, "errors": [], "run_identity": run_hash,
         "stream_keys": [stream.name], "receipts": {stream.name: digest(stream / "receipt.json")}})
    return stream


def test_summary_keeps_subset_censoring_cost_and_matched_comparison(tmp_path):
    replay_fixture(tmp_path)
    result = summarize_replay(tmp_path)
    row = result["tables"][0]["rows"][0]
    assert row["closed"] == 3
    assert row["win_rate"] == pytest.approx(2/3)
    assert row["mean_net_bp"] == pytest.approx(200/3)
    assert row["mean_gross_bp"] == pytest.approx(200/3+20)
    assert row["control_net_bp"] == 50
    assert row["mean_excess_bp"] == pytest.approx(200/3-50)
    assert row["matched"] == 3
    assert set(r["period"] for r in result["tables"][1]["rows"]) == {"earlier", "later"}
    assert result["subset"] is True and result["production_eligible"] is False
    assert json.loads((tmp_path / "replay/manifest.json").read_text())["complete"] is False


def test_modified_trade_bytes_fail_before_summarization(tmp_path):
    stream = replay_fixture(tmp_path)
    with (stream / "trades.csv.gz").open("ab") as handle:
        handle.write(b"altered")
    with pytest.raises(ValueError, match="逐笔文件哈希"):
        summarize_replay(tmp_path)


def test_missing_stream_cannot_be_reported_as_complete(tmp_path):
    replay_fixture(tmp_path)
    identity = json.loads((tmp_path / "replay/identity.json").read_text())
    identity["symbols"].append("BTCUSDT")
    save(tmp_path / "replay/identity.json", identity)
    with pytest.raises(ValueError, match="身份或范围"):
        summarize_replay(tmp_path)
