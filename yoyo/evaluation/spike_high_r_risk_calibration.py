"""Unlabelled point-in-time risk percentiles from authenticated V9 candidates.

Source columns: side, v9, timeframe_min, scheduled_open_utc and the reference
risk fraction computed from the completed signal close/ATR and preceding five
bar extremes. No trade outcome is loaded to construct a cutoff. For each UTC
decision month, only candidate confirmations in the previous three complete
calendar months contribute; the current month and future are excluded. The
lowest-risk tenth is a research admission hypothesis, not a learned classifier.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import json
import subprocess

import numpy as np
import pandas as pd

from yoyo.evaluation.spike_high_r_risk_study import EXP, SOURCE, DEPENDENCIES, digest, dump, verified_sources
from yoyo.evaluation.spike_v8_six_filters import _committed
from yoyo.evaluation.spike_six_filter_statistics import strict_bool
from yoyo.evaluation import spike_exit_policy_study as base


def build_thresholds(candidates, months):
    """Return fixed .1 quantiles over [month-3 calendar months, month), n>=100.

    Only feature values and confirmation timestamps are consumed. Output rows
    include warmup/unknown states rather than borrowing observations backward.
    """
    c = candidates.copy()
    c["available_at"] = pd.to_datetime(c.available_at, utc=True)
    valid = np.isfinite(c.reference_risk_fraction) & c.reference_risk_fraction.gt(0)
    c = c.loc[valid]
    rows = []
    for month in months:
        end = pd.Timestamp(str(month)+"-01", tz="UTC")
        start = end-pd.DateOffset(months=3)
        for minutes in (30,60,240):
            history = c.loc[c.timeframe_min.eq(minutes) & c.available_at.ge(start) & c.available_at.lt(end), "reference_risk_fraction"]
            known = len(history) >= 100 and start >= base.START
            rows.append(dict(timeframe_min=minutes, month=str(month), history_start=start, history_end=end,
                n_history=len(history), known=known, cutoff=float(history.quantile(.1)) if known else np.nan))
    return pd.DataFrame(rows)


def run(output):
    dependencies = [p for p in DEPENDENCIES if "calibration_v1" not in str(p)]
    if not _committed(dependencies):
        raise ValueError("commit calibration builders/tests/config before calibration")
    if output.exists():
        raise ValueError("refuse to overwrite calibration")
    sources, source_sha = verified_sources()
    parts, bindings = [], {}
    for source in sources:
        folder = SOURCE / "results/full_v1/streams" / source["key"]
        path = folder / "completion.json"
        if digest(path) != source["receipt_sha256"]:
            raise ValueError("candidate source receipt drift")
        receipt = json.loads(path.read_text())
        path = folder / "decisions.csv.gz"
        if digest(path) != receipt["files"][path.name]:
            raise ValueError("candidate source data drift")
        frame = pd.read_csv(path)
        frame = frame.loc[strict_bool(frame.v9) & frame.side.eq(1), ["stream_key", "signal_i", "timeframe_min", "scheduled_open_utc", "reference_risk_fraction"]]
        parts.append(frame.rename(columns={"scheduled_open_utc":"available_at"}))
        bindings[source["key"]] = {"receipt_sha256":source["receipt_sha256"], "decisions_sha256":receipt["files"][path.name]}
    c = pd.concat(parts, ignore_index=True)
    if c.duplicated(["stream_key","signal_i"]).any():
        raise ValueError("duplicate original candidate")
    cfg = json.loads((EXP / "config.json").read_text())
    c["available_at"] = pd.to_datetime(c.available_at, utc=True)
    c = c.loc[c.available_at.ge(pd.Timestamp(cfg["start"])) & c.available_at.lt(pd.Timestamp(cfg["end"]))]
    months = pd.date_range(pd.Timestamp(cfg["start"]).normalize().replace(day=1), pd.Timestamp(cfg["end"]), freq="MS").strftime("%Y-%m")
    thresholds = build_thresholds(c, months)
    output.mkdir(parents=True)
    c.to_csv(output / "candidates.csv.gz", index=False, compression={"method":"gzip", "mtime":0})
    thresholds.to_csv(output / "thresholds.csv", index=False)
    dump(output / "sources.json", bindings)
    dump(output / "receipt.json", dict(source_commit=subprocess.check_output(["git","rev-parse","HEAD"],text=True).strip(),
        builder_sha256=digest(Path(__file__)), source_statistics_receipt_sha256=source_sha,
        generated_at=pd.Timestamp.now(tz="UTC").isoformat(), candidate_count=len(c), source_streams=len(sources),
        thresholds_sha256=digest(output / "thresholds.csv"), files={p.name:digest(p) for p in output.iterdir()},
        labels_used=False, warning="Hypothesis chosen after exposed V2 diagnostic; this is not blind validation"))
    print(json.dumps(dict(candidates=len(c), thresholds=len(thresholds), known=int(thresholds.known.sum()))))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    run(parser.parse_args().output)
