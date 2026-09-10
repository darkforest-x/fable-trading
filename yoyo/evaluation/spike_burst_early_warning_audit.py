"""Independent reconciliation of saved V3 alerts, controls and return algebra.

Reconstructs structural edges/parent confirmations directly from authenticated
causal feature columns, without calling detect/progressive_fields. Future bars
are inspected only to check already frozen labels and trade outcomes. This is
verification of one frozen experiment, not another parameter-selection run.
"""
import json
import math
from pathlib import Path
import subprocess

import numpy as np
import pandas as pd

from yoyo.evaluation import spike_burst_early_warning as study
from yoyo.evaluation import spike_burst_recall_study as old


def independently_detect(f):
    """Use current fast MAs/close and only previous12 highs; no outcome labels."""
    if not np.all(np.diff(f.index.asi8) == old.HOUR.value):
        raise ValueError("Audit inputs must be frozen continuous source segments")
    boundary = f.high.shift(1).rolling(12).max()
    condition = f.ready & f.close.gt(boundary) & f.close.gt(f.s20) & f.close.gt(f.e20)
    edges = condition & ~condition.shift(1, fill_value=False)
    selected, last = [], -100000
    for i in np.flatnonzero(edges):
        if i-last >= 12:
            selected.append(int(i)); last = int(i)
    dense = f.ready & f.pastWidth.le(3) & f.pastCrosses.ge(2)
    dense.iloc[:12] = False
    recent = dense.astype(int).shift(1).rolling(12).sum().gt(0)
    advance = (f.close-f.close.shift(3))/f.atr.shift(3)
    base = f.volume.shift(3).dropna().rolling(20).median().reindex(f.index).ffill()
    vol = (f.volume.rolling(3).sum()/(3*base)).where(base.gt(0))
    qualifies = recent & advance.ge(1.5) & vol.ge(1.5) & f.md.ge(f.sb) & f.middle.gt(f.middle.shift(1)) & f.ready
    children = []
    for parent in selected:
        for i in range(parent, min(len(f), parent+4)):
            if qualifies.iloc[i] and f.close.iloc[i] > boundary.iloc[parent]:
                children.append((i, parent)); break
    return selected, children, boundary


def run():
    root, folder = study.ROOT, study.EXPERIMENT / "results"
    own = Path(__file__).resolve()
    relative = str(own.relative_to(root))
    assert subprocess.check_output(["git", "show", "HEAD:"+relative], cwd=root) == own.read_bytes()
    output = study.EXPERIMENT / "qa/independent_audit.json"
    if output.exists():
        raise ValueError("Refusing to overwrite audit")
    validation_path = folder / "validation_manifest.json"
    validation = json.loads(validation_path.read_text())
    prepared_path = old.checked(folder / "prepared_manifest.json", validation["prepared_manifest_sha256"])
    prepared = json.loads(prepared_path.read_text())
    assert prepared["status"] == validation["status"] == "complete"
    assert prepared["config"] == validation["config"] == study.CONFIG
    hashes = dict(prepared["sources"])
    for receipt in (prepared, validation):
        for item in receipt["artifacts"]:
            if item["path"] in hashes:
                assert hashes[item["path"]] == item["sha256"]
            hashes[item["path"]] = item["sha256"]
    for path, digest in hashes.items():
        old.checked(path, digest)
    assert old.sha(folder / "labels.csv.gz") == prepared["label_source_sha256"]
    assert old.sha(folder / "labels.csv.gz") == old.sha(study.V2 / "labels.csv.gz")
    for rel, digest in prepared["source_pins"].items():
        old.checked(root / rel, digest)
    # Preserve the exact binary floats written by pandas. Default C parsing
    # changes tiny prices enough to invalidate strict risk subtraction checks.
    signals = pd.read_csv(folder / "signals.csv.gz", float_precision="round_trip")
    controls = pd.read_csv(folder / "controls.csv.gz", float_precision="round_trip")
    trades = pd.read_csv(folder / "trade_events.csv.gz", float_precision="round_trip")
    control_trades = pd.read_csv(folder / "trade_controls.csv.gz", float_precision="round_trip")
    labels = pd.read_csv(folder / "labels.csv.gz", float_precision="round_trip")
    detections = pd.read_csv(folder / "detections.csv.gz", float_precision="round_trip")
    jobs = json.loads((folder / "matching.json").read_text())["jobs"]
    assert not signals.event_id.duplicated().any()
    assert not controls.event_id.duplicated().any()
    assert set(trades.event_id) == set(signals.event_id)
    assert set(control_trades.event_id) == set(controls.event_id)
    counts = dict(jobs=0, signals=0, controls=0, trade_algebra=0, positive_recall_checks=0, breadth_hours=0)
    breadth_rows = []
    for job in jobs:
        old.checked(job["features_path"], job["features_sha256"])
        f = pd.read_pickle(job["features_path"])
        f = f.loc[f.index+old.HOUR <= study.END]
        early, children, boundary = independently_detect(f)
        clocks = f.index+old.HOUR
        all_ids = dict(early=early, confirmed=[i for i, p in children])
        selected = signals.loc[signals.instrument.eq(job["instrument"])]
        for arm, ids in all_ids.items():
            wanted = [i for i in ids if study.START <= clocks[i] < study.END]
            observed = selected.loc[selected.arm.eq(arm)].decision_i.astype(int).tolist()
            assert wanted == observed, (job["instrument"], arm, "different alerts")
            positive = labels.loc[labels.instrument.eq(job["instrument"]) & labels.label.eq("positive")]
            saved = detections.loc[detections.instrument.eq(job["instrument"]) & detections.arm.eq(arm)].set_index("event_i")
            for event in positive.itertuples():
                for lag in (0, 1, 2, 6):
                    assert bool(saved.loc[event.event_i, "hit_"+str(lag)]) == any(event.event_i <= i <= event.event_i+lag for i in ids)
                    counts["positive_recall_checks"] += 1
        child_map = dict(children)
        for row in selected.itertuples():
            i, p = int(row.decision_i), int(row.parent_i)
            assert pd.Timestamp(row.decision_time) == clocks[i]
            assert pd.Timestamp(row.parent_decision_time) == clocks[p]
            assert p in early and np.isclose(row.frozen_parent_high, boundary.iloc[p], rtol=1e-12, atol=0)
            if row.arm == "confirmed":
                assert child_map[i] == p and int(row.confirm_age) == i-p
            else:
                assert i == p
        rank = f.atr_pct.rolling(240, min_periods=60).rank(pct=True)
        buckets = np.ceil(rank.to_numpy()*5)
        union = set(early) | {i for i,p in children}
        smap = selected.set_index("event_id")
        for row in controls.loc[controls.instrument.eq(job["instrument"])].itertuples():
            i = int(row.decision_i); p = int(smap.loc[row.matched_event_id, "decision_i"])
            assert clocks[i].isocalendar()[:2] == clocks[p].isocalendar()[:2]
            assert buckets[i] == buckets[p] and f.history_count.iloc[i] >= 340 and f.ready.iloc[i]
            assert not any(j in union for j in range(max(0,i-12),i+1))
            assert study.START <= clocks[i] < study.END
            counts["controls"] += 1
        combined = pd.concat([trades.loc[trades.instrument.eq(job["instrument"])], control_trades.loc[control_trades.instrument.eq(job["instrument"])]])
        for row in combined.loc[combined.valid.eq(True)].itertuples():
            i, e = int(row.decision_i), int(row.entry_i)
            expected_stop = math.floor(min(f.low.iloc[max(0,i-4):i+1].min()-.2*f.atr.iloc[i], f.close.iloc[i]-2*f.atr.iloc[i])/float(job["tick"]))*float(job["tick"])
            assert e == i+1 and pd.Timestamp(row.entry_time) == f.index[e]
            assert np.isclose(row.entry_price, f.open.iloc[e], atol=0, rtol=1e-12)
            assert np.isclose(row.initial_stop, expected_stop, atol=0, rtol=1e-12)
            assert np.isclose(row.initial_risk, row.entry_price-row.initial_stop, atol=0, rtol=1e-12)
            assert np.isclose(row.net_return, row.exit_price/row.entry_price-1-.002, atol=1e-12, rtol=1e-10)
            counts["trade_algebra"] += 1
        valid = f.ready & np.isfinite(f.close) & np.isfinite(f.close.shift()) & f.close.shift().gt(0) & np.isfinite(f.s20) & np.isfinite(f.e20)
        above = valid & f.close.gt(f.s20) & f.close.gt(f.e20)
        rising = valid & f.close.gt(f.close.shift())
        mask = (clocks >= study.START) & (clocks < study.END)
        breadth_rows.append(pd.DataFrame(dict(decision_time=clocks[mask], valid_denominator=valid[mask].astype(int).to_numpy(), above_fast=above[mask].astype(int).to_numpy(), positive_return=rising[mask].astype(int).to_numpy(), joint=(above & rising)[mask].astype(int).to_numpy())))
        counts["signals"] += len(selected); counts["jobs"] += 1
    breadth = pd.concat(breadth_rows).groupby("decision_time").sum()
    saved_breadth = pd.read_csv(folder / "breadth.csv.gz", parse_dates=["decision_time"], float_precision="round_trip").set_index("decision_time")
    pd.testing.assert_frame_equal(breadth, saved_breadth[breadth.columns], check_dtype=False)
    counts["breadth_hours"] = len(breadth)
    for path, digest in hashes.items():
        old.checked(path, digest)
    old.write_json(output, dict(status="passed", errors=0, counts=counts,
        builder=old.artifact(own), prepared_manifest_sha256=old.sha(prepared_path),
        validation_manifest_sha256=old.sha(validation_path), verified_sources=len(hashes),
        limitations=["Accounting algebra checked; not a second full intrabar simulation", "Seen historical verification, not new OOS"]))
    print(json.dumps(counts))


if __name__ == "__main__":
    run()
