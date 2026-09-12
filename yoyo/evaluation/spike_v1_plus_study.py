"""Receipt-bound, single-worker full-default V1+ replay study.

Reads the authenticated V7/V1 raw source segments, never the trimmed control
cache as a state origin.  It materializes the display-derived master-off arm
and the current full-default arm separately, retaining reference-clock and
next-open execution fields in every trade.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import pandas as pd

from yoyo.evaluation.spike_v1_plus_replay import replay_references, simulate_next_open
from yoyo.evaluation.spike_v7_v1_compare import ROOT, covered_streams, stream_key

EXP = ROOT / "experiments/active/exp-spike-v1-plus-backtest-20260912-v1"
CONFIG = EXP / "config.json"
RAW = ROOT / "experiments/active/exp-spike-v7-v1-compare-20260912-v1/results/replay_two_year_20260912_v3"
VARIANTS = (("v1_display_both", False), ("v1_plus_default_both", True))


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_json(path: Path, item: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(item, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    temp.replace(path)


def _identity(config: dict) -> dict:
    pine = ROOT / config["pine_path"]
    if digest(pine) != config["pine_sha256"]:
        raise ValueError("pinned V1+ Pine bytes drifted")
    manifest = RAW / "manifest.json"
    if digest(manifest) != config["raw_manifest_sha256"]:
        raise ValueError("upstream raw manifest drifted")
    input_manifest = RAW / "input_manifest.json"
    helper = Path(__file__).with_name("spike_burst_replay.py")
    stream_loader = Path(__file__).with_name("spike_v7_v1_compare.py")
    return {"config_sha256": digest(CONFIG), "pine_sha256": digest(pine),
            "raw_manifest_sha256": digest(manifest),
            "raw_input_manifest_sha256": digest(input_manifest),
            "study_sha256": digest(Path(__file__)),
            "replay_sha256": digest(Path(__file__).with_name("spike_v1_plus_replay.py")),
            "feature_helper_sha256": digest(helper),
            "stream_loader_sha256": digest(stream_loader)}


def _completed(folder: Path, identity: dict) -> bool:
    receipt = folder / "completion.json"
    if not receipt.is_file(): return False
    try: item = json.loads(receipt.read_text())
    except json.JSONDecodeError: return False
    required = ("signals.csv.gz", "trades.csv.gz", "fills.csv.gz", "events.csv.gz", "summary.csv")
    files = item.get("files", {})
    return (item.get("identity") == identity and all(
        (folder / name).is_file() and files.get(name.removesuffix(".csv.gz")) == digest(folder / name)
        for name in required if name != "summary.csv"
    ) and (folder / "summary.csv").is_file()
           and files.get("summary") == digest(folder / "summary.csv"))


def _summarize(trades: pd.DataFrame, *, identity: dict, variant: str) -> list[dict]:
    records=[]
    if trades.empty: return records
    trades=trades.copy(); trades["year"] = pd.to_datetime(trades.entry_time, utc=True).dt.year
    for (year,side), part in trades.groupby(["year","side"], dropna=False):
        done=part.loc[~part.censored.astype(bool)]; wins=done.net_return.gt(0)
        gain=done.loc[done.net_return>0,"net_return"].sum(); loss=-done.loc[done.net_return<0,"net_return"].sum()
        records.append({**identity,"variant":variant,"year":int(year),"side":int(side),"trades":len(part),"realized":len(done),"censored":int(part.censored.sum()),"net_win_rate":float(wins.mean()) if len(done) else None,"profit_factor":float(gain/loss) if loss>0 else None,"total_net_r":float(done.net_r.sum()),"mean_net_r":float(done.net_r.mean()) if len(done) else None,"net_return_sum":float(done.net_return.sum()),"realized_ge_5r":int(done.net_r.ge(5).sum()),"realized_ge_10r":int(done.net_r.ge(10).sum()),"mfe_ge_10r":int(done.mfe_r.ge(10).sum())})
    return records


def run(output: Path, *, limit: int | None = None) -> None:
    config=json.loads(CONFIG.read_text())
    if config.get("variants") != [name for name,_ in VARIANTS] or config.get("max_workers") != 1:
        raise ValueError("unexpected frozen arm or worker contract")
    identity=_identity(config)
    output.mkdir(parents=True, exist_ok=True)
    prior=output/"run_identity.json"
    if prior.exists() and json.loads(prior.read_text()) != identity: raise ValueError("output identity drift")
    _write_json(prior,identity)
    start=pd.Timestamp(config["window_start"]); end=pd.Timestamp(config["window_end"])
    roots=output/"streams"; roots.mkdir(exist_ok=True); processed=0
    for stream in covered_streams():
        key=stream_key(stream); folder=roots/key
        if _completed(folder,identity): continue
        source=stream["bars"].copy(); source.attrs["minutes"]=int(stream["minutes"])
        # The full receipt-authenticated segment preserves pre-window indicator
        # seeds and reference state; only outcome rows are sliced to the study clock.
        per=[]; signal_frames=[]; trade_frames=[]; fill_frames=[]; event_frames=[]
        for variant,enabled in VARIANTS:
            refs=replay_references(source,float(stream["tick"]),enable_plus=enabled)
            close_time = refs.index + pd.Timedelta(minutes=int(stream["minutes"]))
            # The END-close bar is part of the realised path/mark but cannot
            # originate a new close-confirmed order after the study window.
            mask = (close_time >= start) & (close_time <= end)
            entry_eligible = close_time < end
            refs_eval=refs.loc[mask].copy(); bars_eval=source.loc[refs_eval.index].copy()
            refs_eval["eligible_for_entry"] = entry_eligible[mask]
            refs_eval.loc[~refs_eval["eligible_for_entry"], "signal"] = False
            trades,fills=simulate_next_open(
                bars_eval, refs_eval, tick=float(stream["tick"]),
                trade_id_prefix=f"{key}:{variant}",
            )
            for table in (refs_eval,trades,fills):
                # Keep a readable schema even for a zero-signal arm so the
                # account postprocessor can distinguish an empty ledger from
                # a truncated file.
                table["variant"]=variant; table["cohort"]=variant; table["policy"]="default"; table["stream_key"]=key; table["venue"]=stream["venue"]; table["symbol"]=stream["symbol"]; table["asset"]=stream["asset"]; table["timeframe_min"]=stream["minutes"]; table["segment"]=stream["segment"]
            signals=refs_eval.loc[refs_eval.raw_signal | refs_eval.reference_exit].copy()
            events=refs_eval.loc[refs_eval.reference_exit].copy()
            signal_frames.append(signals.reset_index()); trade_frames.append(trades); fill_frames.append(fills); event_frames.append(events.reset_index())
            per.extend(_summarize(trades,identity={"stream_key":key,"venue":stream["venue"],"symbol":stream["symbol"],"asset":stream["asset"],"timeframe_min":stream["minutes"],"segment":stream["segment"]},variant=variant))
        staging=roots/f".{key}.staging"; folder.exists() and (_ for _ in ()).throw(FileExistsError(folder)); staging.mkdir()
        for name,parts in (("signals",signal_frames),("trades",trade_frames),("fills",fill_frames),("events",event_frames)):
            out=pd.concat(parts,ignore_index=True) if parts else pd.DataFrame(); out.to_csv(staging/f"{name}.csv.gz",index=False,compression={"method":"gzip","mtime":0})
        pd.DataFrame(per).to_csv(staging/"summary.csv",index=False)
        completion={"key":key,"identity":identity,"source_path":stream["source_path"],"source_sha256":stream["source_sha256"],"tick":stream["tick"],"status":"complete","files":{**{name:digest(staging/f"{name}.csv.gz") for name in ("signals","trades","fills","events")}, "summary":digest(staging/"summary.csv")}}
        _write_json(staging/"completion.json",completion); staging.replace(folder); processed += 1
        print(f"complete {processed} {key}",flush=True)
        if limit is not None and processed >= limit: break
    # A resumed run must publish a summary of every receipt-verified stream,
    # not only folders completed by this invocation.
    all_summaries = [pd.read_csv(p / "summary.csv") for p in roots.iterdir()
                     if p.is_dir() and not p.name.startswith(".") and _completed(p, identity)]
    table=pd.concat(all_summaries, ignore_index=True) if all_summaries else pd.DataFrame()
    if len(table): table.to_csv(output/"stream_summary.csv",index=False)
    complete=sum(_completed(p,identity) for p in roots.iterdir() if p.is_dir() and not p.name.startswith("."))
    _write_json(output/"manifest.json",{"status":"complete" if complete==config["expected_streams"] else "partial","identity":identity,"completed_streams":complete,"expected_streams":config["expected_streams"],"window":{"start":str(start),"end":str(end)},"limitations":"Python causal translation; no native Pine runtime parity claim. Independent account/NAV and matched controls are postprocessed separately."})


if __name__ == "__main__":
    parser=argparse.ArgumentParser(); parser.add_argument("output",type=Path); parser.add_argument("--limit",type=int)
    args=parser.parse_args(); run(args.output,limit=args.limit)
