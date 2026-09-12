"""Resume the receipt-verified tail of V1+ replay_full_v3 without re-reading done streams.

This one-off operational wrapper preserves the frozen V1+ builder identity. It
checks every pre-existing completion file byte-for-byte, reconstructs only the
pending source segments with the same upstream reader/aggregation functions,
and writes the original per-stream schema and receipts. It exists because the
host terminates a full resumptive traversal before ``covered_streams`` reaches
the tail: that iterator reconstructs each completed source before yielding its
key.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys

import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from yoyo.evaluation.spike_v1_plus_replay import replay_references, simulate_next_open
from yoyo.evaluation.spike_v1_plus_study import (
    CONFIG, RAW, SUMMARY_COLUMNS, VARIANTS, _completed, _identity, _summarize, _write_json, digest,
)
from yoyo.evaluation.spike_v7_v1_compare import _catalog_tick_map, _continuous, _read_utc_source, _source_maps, aggregate

EXP = Path(__file__).resolve().parent
OUTPUT = EXP / "results/replay_full_v3"
RECEIPT = EXP / "receipts/replay_full_v3_resume_receipt.json"


def _driver_sha() -> str:
    return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


def _frozen_metadata() -> dict[tuple[str, int], dict]:
    """Map authenticated source paths to frozen venue/symbol/asset metadata."""
    coverage = pd.read_csv(ROOT / "experiments/active/exp-spike-v7-v1-compare-20260912-v1/results/replay_two_year_20260912_v3/coverage_limited.csv")
    coverage = coverage.loc[coverage.status.eq("evaluated") & coverage.timeframe_min.isin((30, 60, 240))]
    market, gate = _source_maps()
    out: dict[tuple[str, int], dict] = {}
    for row in coverage.itertuples(index=False):
        venue, symbol, minutes = str(row.venue), str(row.symbol), int(row.timeframe_min)
        receipt = gate.get((venue, symbol, minutes)) if venue == "gate" else market.get((venue, symbol))
        if receipt is None:
            continue
        path = str(Path(receipt["path"]).resolve())
        item = {"venue": venue, "symbol": symbol, "asset": str(row.asset), "minutes": minutes}
        prior = out.setdefault((path, minutes), item)
        if prior != item:
            raise ValueError(f"ambiguous frozen source metadata: {path}/{minutes}")
    return out


def _stream_from_frozen(row: dict, metadata: dict[tuple[str, int], dict], ticks: dict[tuple[str, str], float]) -> dict:
    """Use the original reader, aggregation and continuous segmentation for one pending row."""
    path = Path(row["source_path"])
    minutes, segment = int(row["minutes"]), int(row["segment"])
    meta = metadata[(str(path), minutes)]
    tick = ticks[(meta["venue"], meta["symbol"])]
    if digest(path) != row["source_sha256"]:
        raise ValueError(f"frozen source hash drift: {path}")
    source = _read_utc_source(path)
    if meta["venue"] != "gate":
        source = aggregate(source, minutes)
    segments = list(_continuous(source, minutes))
    if segment >= len(segments):
        raise ValueError(f"missing frozen segment {segment}: {path}")
    bars = segments[segment]
    if len(bars) < 341:
        raise ValueError(f"frozen segment became too short: {path}/{segment}")
    bars.attrs["minutes"] = minutes
    return {**meta, "segment": segment, "tick": tick, "bars": bars,
            "source_path": str(path), "source_sha256": row["source_sha256"], "key": row["key"]}


def _write_stream(root: Path, stream: dict, *, config: dict, identity: dict) -> None:
    key = stream["key"]
    start, end = pd.Timestamp(config["window_start"]), pd.Timestamp(config["window_end"])
    source = stream["bars"].copy()
    per: list[dict] = []
    signal_frames: list[pd.DataFrame] = []
    trade_frames: list[pd.DataFrame] = []
    fill_frames: list[pd.DataFrame] = []
    event_frames: list[pd.DataFrame] = []
    for variant, enabled in VARIANTS:
        refs = replay_references(source, float(stream["tick"]), enable_plus=enabled)
        close_time = refs.index + pd.Timedelta(minutes=int(stream["minutes"]))
        mask = (close_time >= start) & (close_time <= end)
        refs_eval = refs.loc[mask].copy()
        refs_eval["eligible_for_entry"] = (close_time < end)[mask]
        refs_eval.loc[~refs_eval["eligible_for_entry"], "signal"] = False
        trades, fills = simulate_next_open(
            source.loc[refs_eval.index], refs_eval, tick=float(stream["tick"]),
            trade_id_prefix=f"{key}:{variant}",
        )
        for table in (refs_eval, trades, fills):
            table["variant"] = variant; table["cohort"] = variant; table["policy"] = "default"
            table["stream_key"] = key; table["venue"] = stream["venue"]; table["symbol"] = stream["symbol"]
            table["asset"] = stream["asset"]; table["timeframe_min"] = stream["minutes"]; table["segment"] = stream["segment"]
        signal_frames.append(refs_eval.loc[refs_eval.raw_signal | refs_eval.reference_exit].reset_index())
        event_frames.append(refs_eval.loc[refs_eval.reference_exit].reset_index())
        trade_frames.append(trades); fill_frames.append(fills)
        per.extend(_summarize(trades, identity={k: stream[k] for k in ("venue", "symbol", "asset", "minutes", "segment")}
                   | {"stream_key": key, "timeframe_min": stream["minutes"]}, variant=variant))
    folder = root / key
    if folder.exists():
        raise FileExistsError(f"pending folder already exists: {folder}")
    staging = root / f".{key}.staging"
    if staging.exists():
        raise FileExistsError(f"stale staging directory: {staging}")
    staging.mkdir()
    for name, parts in (("signals", signal_frames), ("trades", trade_frames), ("fills", fill_frames), ("events", event_frames)):
        pd.concat(parts, ignore_index=True).to_csv(staging / f"{name}.csv.gz", index=False, compression={"method": "gzip", "mtime": 0})
    pd.DataFrame(per, columns=SUMMARY_COLUMNS).to_csv(staging / "summary.csv", index=False)
    completion = {"key": key, "identity": identity, "source_path": stream["source_path"],
                  "source_sha256": stream["source_sha256"], "tick": stream["tick"], "status": "complete",
                  "files": {**{name: digest(staging / f"{name}.csv.gz") for name in ("signals", "trades", "fills", "events")},
                            "summary": digest(staging / "summary.csv")}}
    _write_json(staging / "completion.json", completion)
    staging.replace(folder)


def run(*, dry_run: bool = False) -> None:
    config = json.loads(CONFIG.read_text())
    identity = _identity(config)
    if json.loads((OUTPUT / "run_identity.json").read_text()) != identity:
        raise ValueError("engine identity drift")
    frozen = json.loads((RAW / "input_manifest.json").read_text())["streams"]
    frozen_sha = digest(RAW / "input_manifest.json")
    root = OUTPUT / "streams"
    expected = {row["key"] for row in frozen}
    folders = {p.name for p in root.iterdir() if p.is_dir() and not p.name.startswith(".")}
    if not folders.issubset(expected):
        raise ValueError("engine contains a key absent from frozen manifest")
    skipped = sorted(key for key in expected if _completed(root / key, identity))
    pending_rows = [row for row in frozen if row["key"] not in set(skipped)]
    if any((root / row["key"]).exists() for row in pending_rows):
        raise ValueError("unverified existing folder prevents recovery")
    before = {"driver_sha256": _driver_sha(), "original_identity": identity,
              "frozen_input_manifest_sha256": frozen_sha, "expected_streams": len(expected),
              "skipped_keys": skipped, "pending_keys": [row["key"] for row in pending_rows]}
    if dry_run:
        print(json.dumps({"skipped": len(skipped), "pending": len(pending_rows), "pending_keys": before["pending_keys"]}, ensure_ascii=False))
        return
    metadata = _frozen_metadata()
    catalog = pd.read_json(ROOT / "experiments/active/exp-spike-v7-v1-compare-20260912-v1/data/catalog.json")
    ticks = _catalog_tick_map(catalog)
    for n, row in enumerate(pending_rows, 1):
        stream = _stream_from_frozen(row, metadata, ticks)
        _write_stream(root, stream, config=config, identity=identity)
        print(f"resume_complete {n}/{len(pending_rows)} {stream['key']}", flush=True)
    all_summaries = [pd.read_csv(folder / "summary.csv") for folder in root.iterdir()
                     if folder.is_dir() and not folder.name.startswith(".") and _completed(folder, identity)]
    pd.concat(all_summaries, ignore_index=True).to_csv(OUTPUT / "stream_summary.csv", index=False)
    completed = sorted(key for key in expected if _completed(root / key, identity))
    if completed != sorted(expected):
        raise ValueError("recovery did not complete every frozen stream")
    manifest = {"status": "complete", "identity": identity, "completed_streams": len(completed),
                "expected_streams": len(expected), "window": {"start": config["window_start"], "end": config["window_end"]},
                "limitations": "Python causal translation; no native Pine runtime parity claim. Independent account/NAV and matched controls are postprocessed separately.",
                "resume_receipt": str(RECEIPT.relative_to(ROOT))}
    _write_json(OUTPUT / "manifest.json", manifest)
    after = {**before, "completed_keys": completed, "stream_summary_sha256": digest(OUTPUT / "stream_summary.csv"),
             "engine_manifest_sha256": digest(OUTPUT / "manifest.json"), "status": "complete"}
    _write_json(RECEIPT, after)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(); parser.add_argument("--dry-run", action="store_true")
    run(dry_run=parser.parse_args().dry_run)
