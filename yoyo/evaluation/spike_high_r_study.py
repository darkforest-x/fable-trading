"""Receipt-bound High-R V1 replay on the complete frozen V9 universe.

Source: V9 full_v1, unchanged admissions and initial-risk/cost contract. The
only treatment is the long trailing anchor implemented in spike_high_r_v1.
Every stream, including losses and empty streams, remains in the scope. This
runner reads frozen caches; it never fetches, trains or changes production.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor, as_completed
import hashlib
import json
from pathlib import Path
import subprocess
import time

import pandas as pd

from yoyo.evaluation import spike_exit_policy_study as base
from yoyo.evaluation import spike_v1_v8_be05 as parent
from yoyo.evaluation.spike_v9_full_replay import prepare_v9
from yoyo.evaluation.spike_v8_six_filters import _committed, assert_baseline_parity

EXP = Path("experiments/active/exp-spike-high-r-20260921-v1")
SOURCE = Path("experiments/active/exp-spike-v9-full-backtest-20260915-v1")
STATS = SOURCE / "statistics/full_v1"
DEPENDENCIES = (
    Path(__file__), Path("yoyo/evaluation/spike_high_r_v1.py"),
    Path("yoyo/evaluation/spike_high_r_report.py"),
    Path("tests/evaluation/test_spike_high_r_v1.py"),
    Path("tests/evaluation/test_spike_high_r_study.py"), EXP / "config.json", EXP / "PROJECT_PLAN.md",
    Path(parent.__file__), Path(base.__file__),
    Path("yoyo/evaluation/spike_v9_full_replay.py"), Path("yoyo/evaluation/spike_v9.py"),
    Path("yoyo/evaluation/spike_v8_replay.py"), Path("yoyo/evaluation/spike_v7_fast.py"),
    Path("yoyo/evaluation/spike_burst_replay.py"), Path("yoyo/evaluation/spike_v6_wvf_study.py"),
)


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def dump(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str) + "\n")


def verified_sources():
    """Bind the full stream list and statistics files to their frozen receipt."""
    path = STATS / "statistics_receipt.json"
    cfg = json.loads((EXP / "config.json").read_text())
    if digest(path) != cfg["statistics_receipt_sha256"]:
        raise ValueError("frozen statistics receipt identity changed")
    receipt = json.loads(path.read_text())
    if not receipt.get("files"):
        raise ValueError("statistics file hashes missing")
    for name, expected in receipt["files"].items():
        if digest(STATS / name) != expected:
            raise ValueError(f"statistics hash changed: {name}")
    sources = receipt["source_receipts"]
    keys = [x["key"] for x in sources]
    expected = cfg["expected_streams"]
    if len(keys) != expected or len(set(keys)) != expected:
        raise ValueError("full frozen stream coverage mismatch")
    return sorted(sources, key=lambda x: x["key"]), digest(path)


def completed(folder, identity):
    path = folder / "completion.json"
    if not path.exists():
        return None
    r = json.loads(path.read_text())
    if r.get("status") != "complete" or r.get("run_identity") != identity or r.get("key") != folder.name:
        raise ValueError("completion identity mismatch")
    for name, expected in r["files"].items():
        if digest(folder / name) != expected:
            raise ValueError("saved output hash changed")
    return r


def tag(frame, arm):
    out = frame.copy()
    out["arm"] = arm
    if "signal_i" in out:
        out["event_key"] = (out.stream_key.astype(str) + ":" + out.signal_i.astype(int).astype(str)
                            + ":" + out.side.astype(int).astype(str))
        if out.event_key.duplicated().any():
            raise ValueError("duplicate event identity")
    return out


def one(args):
    from yoyo.evaluation import spike_high_r_v1 as engine
    source, output_text, identity = args
    key, output = source["key"], Path(output_text)
    source_dir = SOURCE / "results/full_v1/streams" / key
    source_receipt_path = source_dir / "completion.json"
    if digest(source_receipt_path) != source["receipt_sha256"]:
        raise ValueError(f"V9 completion hash changed: {key}")
    src = json.loads(source_receipt_path.read_text())
    raw_dir = base.SOURCE_STREAMS / key
    if digest(raw_dir / "completion.json") != src["raw_completion_sha256"]:
        raise ValueError(f"raw completion changed: {key}")
    cache_path = raw_dir / "control_cache.pkl.gz"
    if digest(cache_path) != src["cache_sha256"]:
        raise ValueError(f"raw cache changed: {key}")
    final = output / "streams" / key
    saved = completed(final, identity)
    if saved:
        if saved["source_completion_sha256"] != source["receipt_sha256"] or saved["cache_sha256"] != src["cache_sha256"]:
            raise ValueError("resumed source identity mismatch")
        return saved
    staging = output / "streams" / ("." + key + ".staging")
    staging.mkdir()
    began = time.monotonic()
    try:
        for name in ("v9.trades.csv.gz", "controls.csv.gz"):
            if digest(source_dir / name) != src["files"][name]:
                raise ValueError(f"source artifact changed: {name}")
        context = base.load_verified_stream(raw_dir)
        prepared, decisions = prepare_v9(parent.prepare_arm(context, arm="v8"))
        baseline, bf, _ = engine.replay_serial(prepared, peak_long=False)
        assert_baseline_parity(baseline, pd.read_csv(source_dir / "v9.trades.csv.gz"))
        treatment, tf, events = engine.replay_serial(prepared, peak_long=True)
        baseline, treatment = tag(baseline, "baseline"), tag(treatment, "high_r_v1")
        oldc = pd.read_csv(source_dir / "controls.csv.gz")
        oldc = tag(oldc.loc[oldc.arm.eq("v9") & oldc.side.eq(1)], "baseline")
        newc = tag(engine.matched_controls(prepared, treatment.loc[treatment.side.eq(1)], peak_long=True), "high_r_v1")
        tables = {"trades": pd.concat([baseline, treatment], ignore_index=True),
                  "controls": pd.concat([oldc, newc], ignore_index=True),
                  "fills": pd.concat([tag(bf, "baseline"), tag(tf, "high_r_v1")], ignore_index=True),
                  "events": events}
        for name, frame in tables.items():
            frame.to_csv(staging / (name + ".csv.gz"), index=False,
                         compression={"method": "gzip", "mtime": 0, "compresslevel": 1})
        if digest(cache_path) != src["cache_sha256"]:
            raise ValueError("raw cache modified during replay")
        summary = []
        for arm, frame in (("baseline", baseline), ("high_r_v1", treatment)):
            p = frame.loc[frame.side.eq(1)]
            closed = p.loc[~p.censored.astype(bool)]
            summary.append(dict(arm=arm, events=len(p), closed=len(closed), net_r=float(closed.net_r.sum()),
                                gt10=int(closed.net_r.gt(10).sum())))
        r = dict(status="complete", key=key, run_identity=identity, baseline_parity=True,
                 source_completion_sha256=source["receipt_sha256"], cache_sha256=src["cache_sha256"],
                 files={p.name: digest(p) for p in staging.iterdir()}, summaries=summary,
                 v9_candidates=int(decisions.v9.sum()), wall_seconds=time.monotonic()-began)
        dump(staging / "completion.json", r)
        staging.replace(final)
        return r
    except Exception as exc:
        dump(staging / "failure.json", {"key": key, "type": type(exc).__name__, "error": str(exc)})
        staging.replace(output / "failures" / f"{key}.{time.time_ns()}")
        raise


def run(output, workers=3, limit=None):
    if not _committed(DEPENDENCIES):
        raise ValueError("commit unchanged builders/tests/config before market replay")
    sources, source_sha = verified_sources()
    identity_data = {str(p): digest(p) for p in DEPENDENCIES}
    identity_data["statistics_receipt_sha256"] = source_sha
    identity = hashlib.sha256(json.dumps(identity_data, sort_keys=True).encode()).hexdigest()
    output.mkdir(parents=True, exist_ok=True)
    for name in ("streams", "failures"):
        (output / name).mkdir(exist_ok=True)
    ip = output / "identity.json"
    if ip.exists() and json.loads(ip.read_text()) != identity_data:
        raise ValueError("builder identity changed; choose new output")
    dump(ip, identity_data)
    start_path = output / "started.json"
    if not start_path.exists():
        dump(start_path, dict(source_commit=subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
                              started_at=pd.Timestamp.now(tz="UTC").isoformat(), run_identity=identity,
                              history="reused exposed history; not blind validation"))
    selected = sources if limit is None else sources[:limit]
    began = time.monotonic()
    receipts, errors = [], []
    with ProcessPoolExecutor(max_workers=max(1, min(workers, 4))) as pool:
        jobs = {pool.submit(one, (s, str(output), identity)): s["key"] for s in selected}
        for n, f in enumerate(as_completed(jobs), 1):
            try:
                receipts.append(f.result())
            except Exception as exc:
                errors.append(dict(key=jobs[f], error=str(exc)))
                print(json.dumps(errors[-1]), flush=True)
            if n == 1 or n % 50 == 0 or n == len(selected):
                print(json.dumps(dict(done=n, target=len(selected), failures=len(errors),
                                      seconds=round(time.monotonic()-began, 1))), flush=True)
    dump(output / "failures.json", errors)
    dump(output / "manifest.json", dict(complete=not errors and limit is None, selected=len(selected),
        completed=len(receipts), expected=len(sources), run_identity=identity,
        source_receipt_sha256=source_sha, failures=len(errors),
        receipts={r["key"]: digest(output / "streams" / r["key"] / "completion.json") for r in receipts}))
    if errors:
        raise RuntimeError(f"{len(errors)} stream failures; receipts retained")


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--workers", type=int, default=3)
    p.add_argument("--limit", type=int)
    a = p.parse_args()
    run(a.output, a.workers, a.limit)
