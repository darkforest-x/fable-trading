"""Explicitly recover relocated images and timestamp-bind current same-feed OHLC.

The original221 manifest is never changed. Image recovery requires its exact
historical SHA. OHLC rebinding uses same OKX symbol/15m only, original window
timestamps and bar spacing; original byte parity remains unverified. No
alternative exchange, guessed positional offset, new labels or fitting.
"""
import json
from pathlib import Path
import subprocess
import numpy as np
import pandas as pd
from yoyo.evaluation.ma_morphology_reuse_inventory import EXP,MANIFEST,sha


def main():
    own=Path(__file__).relative_to(Path.cwd())
    assert not subprocess.check_output(["git","status","--porcelain","--",str(own),str(EXP/"PROJECT_PLAN.md")],text=True).strip()
    rows=pd.DataFrame([json.loads(x) for x in MANIFEST.read_text().splitlines()])
    recovered=[];inputs={}
    for symbol,group in rows.groupby("symbol"):
        paths=sorted({p for root in (Path("data/kline_fetched"),Path("data/kline_deep")) for p in root.glob(f"okx_{symbol}_15m_*.csv")})
        sources=[]
        for p in paths:
            f=pd.read_csv(p)
            clock=pd.to_datetime(f.ts,unit="ms",utc=True) if "ts" in f else pd.to_datetime(f.open_time,utc=True)
            if not clock.is_unique or not clock.is_monotonic_increasing: continue
            f.index=pd.DatetimeIndex(clock);sources.append((p,f))
        for r in group.to_dict("records"):
            start=pd.Timestamp(r["window_start_time"]);end=pd.Timestamp(r["decision_time"])
            expected=pd.date_range(start,end,freq="15min")
            assert len(expected)==int(r["window_end_bar"])-int(r["window_start_bar"])+1
            matches=[]
            for p,f in sources:
                if not expected.isin(f.index).all():continue
                z=f.loc[expected,["open","high","low","close","volume"]]
                if not np.isfinite(z.to_numpy(float)).all():continue
                end_i=int(f.index.get_loc(end));begin_i=int(f.index.get_loc(start))
                if end_i-begin_i+1 != len(expected):continue
                matches.append((p,f,z,begin_i,end_i))
            agreement=all(np.array_equal(matches[0][2].to_numpy(float),m[2].to_numpy(float)) for m in matches[1:]) if matches else False
            chosen=max(matches,key=lambda m:m[4]) if matches and agreement else None
            original=Path(r["image_path"])
            archived=Path("archive/consolidated/yoyo-trading")/original
            image_path=next((p for p in (original,archived) if p.exists() and sha(p)==r["image_sha256"]),None)
            row=dict(sample_id=r["sample_id"],symbol=symbol,class_status=r["class_status"],box_status=r["box_status"],
                     split=r["split"],window_start_time=str(start),decision_time=str(end),original_source_path=r["source_path"],
                     matching_current_sources=len(matches),current_window_ohlcv_agreement=agreement,
                     recovered_image_path=str(image_path) if image_path else None,image_exact_sha=image_path is not None,
                     recovered_source_path=None,numeric_timestamp_link=False,full_historical_ohlc_parity="unverified_no_original_source_sha",
                     core_start_time=None,core_end_time=None,post_core_visible_bars=None,
                     original_window_start_bar=int(r["window_start_bar"]),original_window_end_bar=int(r["window_end_bar"]))
            if chosen:
                p,f,z,lo,hi=chosen
                inputs.setdefault(str(p),dict(sha256=sha(p),rows=len(f)))
                row.update(recovered_source_path=str(p),numeric_timestamp_link=True,recovered_window_start_bar=lo,recovered_window_end_bar=hi)
                if r["class_status"]=="confirmed":
                    core_start=start+pd.Timedelta(minutes=15*(int(r["box_start_bar"])-int(r["window_start_bar"])))
                    core_end=start+pd.Timedelta(minutes=15*(int(r["box_end_bar"])-int(r["window_start_bar"])))
                    assert core_start in expected and core_end in expected
                    row.update(core_start_time=str(core_start),core_end_time=str(core_end),post_core_visible_bars=int((end-core_end)/pd.Timedelta(minutes=15)))
            recovered.append(row)
    table=pd.DataFrame(recovered)
    out=EXP/"recovery_v2";out.mkdir(exist_ok=False)
    table.to_csv(out/"lineage.csv",index=False)
    summary=dict(rows=len(table),exact_relocated_images=int(table.image_exact_sha.sum()),numeric_timestamp_links=int(table.numeric_timestamp_link.sum()),
                 linked_positive=int((table.numeric_timestamp_link & table.class_status.eq("confirmed")).sum()),
                 linked_negative=int((table.numeric_timestamp_link & table.class_status.eq("rejected")).sum()),
                 current_sources=len(inputs),conflicting_windows=int((table.matching_current_sources.gt(0)&~table.current_window_ohlcv_agreement).sum()),
                 same_feed_only=True,original_manifest_unchanged=True,historical_ohlc_hash_parity=False,training_eligible=False)
    (out/"summary.json").write_text(json.dumps(summary,indent=2)+"\n")
    (out/"identity.json").write_text(json.dumps(dict(source_commit=subprocess.check_output(["git","rev-parse","HEAD"],text=True).strip(),
        code_and_manifests={str(p):sha(p) for p in (own,MANIFEST,EXP/"PROJECT_PLAN.md")},inputs=inputs,
        files={p.name:sha(p) for p in out.iterdir() if p.is_file()}),indent=2)+"\n")
    print(json.dumps(summary,indent=2))


if __name__=="__main__":main()
