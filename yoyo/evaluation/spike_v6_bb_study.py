"""Frozen cached-input runner for causal V6 BB squeeze admissions.

It reads cached V6 bars/signals/data_gap only; it never reconstructs V6 or
fetches prices.  CLI execution is intentionally separate from this build step.
"""
from __future__ import annotations
import argparse, hashlib, json, os, subprocess
from dataclasses import asdict
from pathlib import Path
import numpy as np
import pandas as pd
from yoyo.evaluation.spike_v6_bb_squeeze import admissions, features
from yoyo.evaluation.spike_v6_wvf_study import ExecutionSpec, simulate_v6_variant, summarize_account

START=pd.Timestamp("2024-09-10T00:00:00Z"); SPLIT=pd.Timestamp("2025-09-10T00:00:00Z"); END=pd.Timestamp("2026-09-10T00:00:00Z")
VARIANTS=("A0","A","B","C","D")

def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()

def _empty_trades():
    return pd.DataFrame(columns=["signal_bar_open","entry_time","exit_time","side","entry_price","exit_price","initial_stop","initial_risk","net_r","mfe_r","net_return","censored"])


def bb_diagnostics(bars: pd.DataFrame, *, data_gap: pd.Series) -> pd.DataFrame:
    """Calculate frozen BB diagnostics and all A0--D V6 admission decisions.

    The additional shifted threshold requirement makes ``ready`` mean that the
    signal and every one of its preceding twelve bars had a defined 500-bar
    squeeze threshold.  It is deliberately applied once per complete cached
    stream, before any fold is selected.
    """
    diagnostic = features(bars, data_gap=data_gap)
    diagnostic["ready"] &= diagnostic.bb_width_p10_prior500.shift(12).notna()
    return diagnostic


def _event_reason(gate: pd.DataFrame, variant: str) -> pd.Series:
    """Return the admission reason under one explicit variant, for raw events."""
    if variant == "A0":
        return pd.Series("raw_baseline", index=gate.index)
    conditions = [~gate.ready.astype(bool)]
    reasons = ["cold_or_gap"]
    if variant in {"B", "C", "D"}:
        conditions.append(~gate.prior_squeeze_run3.astype(bool))
        reasons.append("no_prior_run3")
    if variant in {"C", "D"}:
        conditions.append(~gate.width_expanding.astype(bool))
        reasons.append("width_not_expanding")
    if variant == "D":
        conditions.append(~gate.rsi_direction_ok.astype(bool))
        reasons.append("rsi_direction")
    return pd.Series(np.select(conditions, reasons, default="admitted"), index=gate.index)

def _resolve_cache_path(manifest: Path, value: str) -> Path:
    path = Path(value).expanduser()
    return path if path.is_absolute() else manifest.parent / path


def _validated_streams(manifest: Path, cfg: dict[str, object]) -> list[dict[str, object]]:
    """Validate every frozen stream receipt before any cache is deserialized."""
    streams = cfg.get("streams")
    if not isinstance(streams, list) or not streams:
        raise ValueError("preparation manifest requires a non-empty streams list")
    validated: list[dict[str, object]] = []
    for item in streams:
        if not isinstance(item, dict):
            raise ValueError("each preparation stream must be an object")
        for field in ("symbol", "timeframe_min", "feature_cache", "tick", "sha256"):
            if field not in item:
                raise ValueError(f"stream missing {field}")
        cache_path = _resolve_cache_path(manifest, str(item["feature_cache"]))
        if not cache_path.is_file():
            raise FileNotFoundError(cache_path)
        actual = _sha(cache_path)
        if actual != str(item["sha256"]):
            raise ValueError(f"cache SHA256 mismatch for {cache_path}: expected {item['sha256']}, got {actual}")
        validated.append({**item, "feature_cache": str(cache_path.resolve()), "verified_feature_cache_sha256": actual})
    return validated


def _prereg_path(manifest: Path, prereg_config: Path | None) -> Path:
    path = prereg_config if prereg_config is not None else manifest.parents[2] / "config.json"
    path = path.resolve()
    if not path.is_file():
        raise FileNotFoundError(f"pre-registration config not found: {path}")
    return path


def _source_receipt(path: Path) -> dict[str, str]:
    return {"path": str(path.resolve()), "sha256": _sha(path)}


def _flush_progress(path: Path, payload: dict[str, object]) -> None:
    """Append a durable completed-stream receipt after all its ledgers exist."""
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def run(manifest: Path, output: Path, *, prereg_config: Path | None = None) -> pd.DataFrame:
    """Materialize each stream/fold/variant ledger from a frozen cache manifest."""
    manifest = manifest.resolve()
    cfg=json.loads(manifest.read_text())
    streams = _validated_streams(manifest, cfg)
    prereg_path = _prereg_path(manifest, prereg_config)
    output.mkdir(parents=True, exist_ok=False)
    progress_path = output / "progress.jsonl"
    summary=[]; completed=[]
    for stream_i, item in enumerate(streams, start=1):
        cache_path=Path(str(item["feature_cache"])); cache=pd.read_pickle(cache_path); bars=cache["bars"].copy(); signals=cache["signals"].copy(); gap=pd.Series(cache["data_gap"], index=bars.index).fillna(True).astype(bool)
        if not bars.index.equals(signals.index):
            raise ValueError(f"unaligned bars/signals in {cache_path}")
        minutes=int(item["timeframe_min"]); bars.attrs["minutes"]=minutes
        diag=bb_diagnostics(bars, data_gap=gap)
        gate=admissions(signals,diag)
        pd.to_pickle({"bars":bars,"signals":signals,"data_gap":gap,"diagnostic":diag,"admissions":gate},output/f"{item['symbol']}_{minutes}m_bb_diagnostic.pkl.gz",compression="gzip")
        for fold,begin,finish in (("development",START,SPLIT),("validation",SPLIT,END)):
            lower=begin-pd.Timedelta(minutes=minutes*5); mask=(bars.index>=lower)&(bars.index<finish)
            b=bars.loc[mask].copy(); b.attrs["minutes"]=minutes; s=signals.loc[mask].copy(); g=gate.loc[mask].copy(); dg=gap.loc[mask]
            known=(s.index+pd.Timedelta(minutes=minutes)>=begin)&(s.index+pd.Timedelta(minutes=minutes)<finish)
            s.loc[~known,:]=False
            for variant in VARIANTS:
                admission=(g[variant].astype(bool) & known)
                raw=(s.long_signal|s.short_signal)
                event=pd.DataFrame({"symbol":item["symbol"],"timeframe_min":minutes,"signal_bar_open":s.index[raw],"signal_confirm_time":s.index[raw]+pd.Timedelta(minutes=minutes),"side":g.loc[raw,"side"].to_numpy(),"raw_v6":True,"admitted":admission.loc[raw].to_numpy(),"rejection_reason":_event_reason(g,variant).loc[raw].to_numpy(),"variant":variant,"fold":fold})
                _, trades=simulate_v6_variant(b,s,admission=admission,variant=variant,data_gap=dg,spec=ExecutionSpec(tick=float(item["tick"])))
                if trades.empty: trades=_empty_trades()
                trades=trades.assign(symbol=item["symbol"],timeframe_min=minutes,fold=fold,variant=variant)
                event.to_csv(output/f"{item['symbol']}_{minutes}m_{fold}_{variant}_signals.csv.gz",index=False,compression="gzip"); trades.to_csv(output/f"{item['symbol']}_{minutes}m_{fold}_{variant}_trades.csv.gz",index=False,compression="gzip")
                summary.append({"symbol":item["symbol"],"timeframe_min":minutes,"fold":fold,"variant":variant,"raw_events":len(event),"admitted":int(event.admitted.sum()),"actual_positions":int(len(trades)),"censored_positions":int(trades.censored.astype(bool).sum()),"a0_to_a_warmup_excluded":int((raw & ~g.ready.astype(bool)).sum()),**summarize_account(trades)})
        receipt={"stream_index":stream_i,"stream_count":len(streams),"symbol":item["symbol"],"timeframe_min":minutes,"feature_cache":str(cache_path),"feature_cache_sha256":item["verified_feature_cache_sha256"],"status":"completed"}
        _flush_progress(progress_path, receipt)
        completed.append(receipt)
    result=pd.DataFrame(summary); result.to_csv(output/"summary.csv",index=False)
    frozen_config={"folds":{"development":[str(START),str(SPLIT)],"validation":[str(SPLIT),str(END)]},"variants":list(VARIANTS),"common_readiness":"bb_width_p10_prior500.shift(12).notna()","execution_spec_defaults":asdict(ExecutionSpec()),"tick_source":"per-stream manifest tick"}
    config_path=output/"frozen_config.json"; config_path.write_text(json.dumps(frozen_config,indent=2)+"\n")
    execution_source=Path(__file__).with_name("spike_v6_wvf_study.py")
    feature_source=Path(__file__).with_name("spike_v6_bb_squeeze.py")
    (output/"manifest.json").write_text(json.dumps({
        "preparation_manifest":_source_receipt(manifest),
        "pre_registration_config":_source_receipt(prereg_path),
        "frozen_runner_config":_source_receipt(config_path),
        "source_code":{"execution":_source_receipt(execution_source),"features":_source_receipt(feature_source),"runner":_source_receipt(Path(__file__))},
        "streams":completed,
        "builder_commit":subprocess.check_output(["git","rev-parse","HEAD"],text=True).strip(),
    },indent=2)+"\n")
    return result

if __name__=="__main__":
    p=argparse.ArgumentParser(); p.add_argument("--input-manifest",type=Path,required=True); p.add_argument("--output",type=Path,required=True); p.add_argument("--prereg-config",type=Path); a=p.parse_args(); run(a.input_manifest,a.output,prereg_config=a.prereg_config)
