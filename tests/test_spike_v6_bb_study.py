import json
import hashlib
import numpy as np
import pandas as pd
from yoyo.evaluation.spike_v6_bb_study import SPLIT, bb_diagnostics, run

def test_runner_masks_fold_end_and_preserves_empty_trade_schema(tmp_path):
    ix=pd.date_range("2026-08-01",periods=1000,freq="h",tz="UTC"); c=pd.Series(100. + np.arange(len(ix))*.01,index=ix)
    bars=pd.DataFrame({"open":c,"high":c+1,"low":c-1,"close":c,"atr":1.,"ready":True},index=ix); bars.attrs["minutes"]=60
    signals=pd.DataFrame({"long_signal":False,"short_signal":False},index=ix)
    signals.loc[pd.Timestamp("2026-09-10T00:00:00Z")-pd.Timedelta(hours=3),"long_signal"]=True
    signals.iloc[-1,0]=True  # Confirmation is after validation end and must not count.
    cache=tmp_path/'cache.pkl.gz'; pd.to_pickle({"bars":bars,"signals":signals,"data_gap":pd.Series(False,index=ix)},cache)
    manifest=tmp_path/'input_manifest.json'
    manifest.write_text(json.dumps({"streams":[{"symbol":"T","timeframe_min":60,"feature_cache":str(cache),"tick":.1,"sha256":hashlib.sha256(cache.read_bytes()).hexdigest()}]}))
    prereg=tmp_path/'config.json'; prereg.write_text('{"pre_registered": true}\n')
    out=tmp_path/'out'; result=run(manifest,out,prereg_config=prereg)
    validation=result.loc[result.fold.eq("validation")]
    assert validation.raw_events.sum()==len(("A0","A","B","C","D"))
    assert (validation.loc[validation.variant.eq("A0"),"admitted"] == 1).all()
    assert validation.loc[validation.variant.eq("A0"),"actual_positions"].iloc[0] == 1
    assert "net_r" in pd.read_csv(next(out.glob('*development_A0_trades.csv.gz'))).columns
    events=pd.read_csv(out/"T_60m_validation_A0_signals.csv.gz")
    assert pd.Timestamp(events.signal_confirm_time.iloc[0]) == pd.Timestamp("2026-09-09T22:00:00Z")
    assert pd.read_csv(out/"T_60m_validation_A_signals.csv.gz").rejection_reason.iloc[0] == "admitted"
    receipt=json.loads((out/"manifest.json").read_text())
    assert receipt["preparation_manifest"]["path"] == str(manifest.resolve())
    assert receipt["pre_registration_config"]["sha256"] == hashlib.sha256(prereg.read_bytes()).hexdigest()
    assert receipt["source_code"]["execution"]["sha256"]
    assert json.loads((out/"progress.jsonl").read_text())["status"] == "completed"
    assert pd.read_csv(out/"T_60m_validation_A0_signals.csv.gz").timeframe_min.iloc[0] == 60


def test_runner_rejects_mismatched_stream_hash_before_loading(tmp_path):
    cache=tmp_path/'cache.pkl.gz'; pd.to_pickle({"bars":pd.DataFrame(),"signals":pd.DataFrame(),"data_gap":pd.Series(dtype=bool)},cache)
    manifest=tmp_path/'input_manifest.json'
    manifest.write_text(json.dumps({"streams":[{"symbol":"T","timeframe_min":60,"feature_cache":str(cache),"tick":.1,"sha256":"0"*64}]}))
    prereg=tmp_path/'config.json'; prereg.write_text('{}\n')
    try:
        run(manifest,tmp_path/'out',prereg_config=prereg)
    except ValueError as exc:
        assert "SHA256 mismatch" in str(exc)
    else:
        raise AssertionError("mismatched feature cache must be rejected")
    assert not (tmp_path/'out').exists()


def test_common_readiness_waits_twelve_bars_after_threshold():
    ix=pd.date_range("2024-01-01",periods=730,freq="h",tz="UTC")
    close=pd.Series(100 + (pd.Series(range(len(ix)),index=ix) % 7),index=ix,dtype=float)
    bars=pd.DataFrame({"open":close,"high":close+1,"low":close-1,"close":close},index=ix)
    diagnostic = bb_diagnostics(bars,data_gap=pd.Series(False,index=ix))
    threshold_at=int(diagnostic.bb_width_p10_prior500.first_valid_index() == ix[699])
    assert threshold_at == 1
    assert not diagnostic.ready.loc[ix[710]]
    assert diagnostic.ready.loc[ix[711]]
