"""Read-only ONE/OKX screenshot diagnosis, with frozen local-cache inputs.

Source: owner's 2026-09-20 ONE 15m screenshot, showing Sep15-17 price action.
Uses the unchanged line engine. Raw/soft pivots are known only after right=8
bars. Diagnostic triplets use only bars through C+8; no strategy change.
Inputs are a read-only SQLite checkpoint snapshot; never write the monitor DB.
"""
from pathlib import Path
import gzip
import hashlib
import json
import sqlite3
import subprocess

import numpy as np
import pandas as pd

from yoyo.monitor import spike_lines as sl
from yoyo.evaluation import spike_v10_4 as lines
from yoyo.evaluation.spike_v8_six_filters import _committed

EXP=Path("experiments/active/exp-spike-v112-one-line-audit-20260920-v1")
PLAN=EXP/"PROJECT_PLAN.md"


def bj(stamp):
    return pd.Timestamp(stamp).tz_convert("Asia/Shanghai").isoformat()


def main():
    assert _committed((Path(__file__),PLAN)),"Commit diagnostic source before replay"
    snap=EXP/"input.json.gz"
    if not snap.exists():
        dbpath=Path.home()/"Library/Application Support/Fable/ImpulseMonitor/monitor.sqlite3"
        db=sqlite3.connect(f"file:{dbpath}?mode=ro",uri=True)
        db.execute("BEGIN")
        inputs={}
        for tf in ("15m","1H"):
            payload,updated=db.execute("SELECT payload,updated_ms FROM candle_checkpoints WHERE symbol=? AND timeframe=?",("ONE-USDT-SWAP",tf)).fetchone()
            inputs[tf]={"candles":json.loads(gzip.decompress(payload)),"updated_ms":updated}
        market=json.loads(db.execute("SELECT payload FROM markets WHERE symbol=? AND timeframe=?",("ONE-USDT-SWAP","15m")).fetchone()[0])
        inputs["tick"]=float(market["tick_size"]);db.close()
        snap.write_bytes(gzip.compress(json.dumps(inputs).encode(),mtime=0))
    inputs=json.loads(gzip.decompress(snap.read_bytes()));tick=inputs["tick"]
    frames={tf:sl.frame_of(inputs[tf]["candles"]) for tf in ("15m","1H")}
    summaries={};alltriplets=[]
    for tf,minutes in (("15m",15),("1H",60)):
        facts=sl.study.v9_facts(frames[tf],minutes,"ONE",tick);f=facts["frame"];p=lines.V104Params();trace={}
        result=lines.joint_events(f.open,f.high,f.low,f.close,f.atr,can_run=facts["can_run"],confirmed_long=facts["v9_long"],
            parent_high=facts["parent_high"],parent_low=facts["parent_low"],raw_side=facts["side"],long_alive=facts["long_alive"],
            momentum=facts["momentum"],current_gate=facts["current_gate"],ref_long_exit=facts["ref_long_exit"],tick=tick,params=p,trace=trace)
        box={};lines.reference_long_exits(f.high,f.low,f.close,f.atr,ready=facts["ready"],gap=facts["gap"],raw_side=facts["side"],
            signal_side=np.where(facts["v9"],facts["side"],0),tick=tick,state=box)
        f=f.copy();f["bar_bj"]=[bj(x) for x in f.index];f["v9"]=facts["v9"];f["v9_long"]=facts["v9_long"]
        f["raw_side"]=facts["side"];f["long_open"]=box["long_open"];f["box_entry_i"]=box["box_entry"]
        f["break_event"]=result.break_event;f["main_uid"]=trace["main_uid"];f["gap"]=facts["gap"]
        f.to_csv(EXP/f"{tf}_facts.csv.gz",compression={"method":"gzip","mtime":0})
        lineframe=pd.DataFrame(trace["lines"])
        for field in ("ax","bx","cx","born_i"):
            if len(lineframe):lineframe[field+"_bj"]=[bj(f.index[int(i)]+(pd.Timedelta(minutes=minutes) if field=="born_i" else pd.Timedelta(0))) for i in lineframe[field]]
        lineframe.to_csv(EXP/f"{tf}_lines.csv",index=False)
        events=pd.DataFrame(trace["line_events"])
        if len(events):events["bar_bj"]=[bj(f.index[int(i)]) for i in events.i]
        events.to_csv(EXP/f"{tf}_line_events.csv",index=False)
        piv=[]
        for source,price in (("raw",f.high.to_numpy()),("soft",lines.soft_peak(f.open.to_numpy(),f.high.to_numpy(),f.close.to_numpy(),f.atr.to_numpy(),p.wick_cap))):
            which,ties,extra=lines.pivots(price,p.left,p.right)
            xs=which[which>=0]
            for x in xs:
                piv.append({"source":source,"i":int(x),"bar_bj":bj(f.index[x]),"price":price[x],"atr":f.atr.iloc[x],"known_close_bj":bj(f.index[x+p.right]+pd.Timedelta(minutes=minutes))})
            # Explain all near-collinear triples whose A is the visible major top.
            if tf=="15m":
                local=f.index.tz_convert("Asia/Shanghai")
                aa=[int(x) for x in xs if pd.Timestamp("2026-09-15 09:00",tz="Asia/Shanghai")<=local[x]<=pd.Timestamp("2026-09-15 15:00",tz="Asia/Shanghai")]
                for a in aa:
                    for b in xs[xs>a]:
                        for c in xs[xs>b]:
                            if local[c]>pd.Timestamp("2026-09-17 07:00",tz="Asia/Shanghai"):continue
                            y=price[a]+(price[b]-price[a])*(c-a)/(b-a)
                            fit=abs(price[c]-y)/f.atr.iloc[c]
                            if fit>1 or not price[a]>price[b]>price[c]:continue
                            i=int(c+p.right);atnow=price[a]+(price[b]-price[a])*(i-a)/(b-a)
                            ab=(min(price[a],price[b])-f.low.iloc[a+1:b].min())/max(f.atr.iloc[a],f.atr.iloc[b])
                            bc=(min(price[b],price[c])-f.low.iloc[b+1:c].min())/max(f.atr.iloc[b],f.atr.iloc[c])
                            ln=lines.Line(int(a),float(price[a]),int(b),float(price[b]),int(c),float(price[c]),float(fit),i,0 if source=="raw" else 1)
                            code,spikes=lines._validate(ln,i,f.high.to_numpy(),f.close.to_numpy(),np.maximum(f.open,f.close).to_numpy(),f.atr.to_numpy(),tick,p)
                            alltriplets.append({"source":source,"a":int(a),"b":int(b),"c":int(c),"a_bj":bj(f.index[a]),"b_bj":bj(f.index[b]),"c_bj":bj(f.index[c]),
                                "ap":price[a],"bp":price[b],"cp":price[c],"gap_ab":int(b-a),"gap_bc":int(c-b),"span":int(c-a),"fit_atr":float(fit),"ab_pullback":float(ab),"bc_pullback":float(bc),
                                "drop_ab_atr":float((price[a]-price[b])/max(f.atr.iloc[a],f.atr.iloc[b])),"close_below_at_confirmation":bool(f.close.iloc[i]<=atnow),"validation_code":int(code),"spikes":int(spikes)})
        pd.DataFrame(piv).to_csv(EXP/f"{tf}_pivots.csv",index=False)
        window=(f.index>=pd.Timestamp("2026-09-14T16:00Z"))&(f.index<pd.Timestamp("2026-09-17T16:00Z"))
        summaries[tf]={"bars":len(f),"start":bj(f.index[0]),"end":bj(f.index[-1]),"v9_closes":[bj(f.index[i]+pd.Timedelta(minutes=minutes)) for i in np.flatnonzero(window&facts["v9"])],
            "break_closes":[bj(f.index[i]+pd.Timedelta(minutes=minutes)) for i in np.flatnonzero(window&result.break_event)],"zero_volume_window":int((window&f.volume.eq(0)).sum()),"gaps_window":int((window&facts["gap"]).sum())}
    pd.DataFrame(alltriplets).to_csv(EXP/"visible_peak_triplets.csv",index=False)
    # Screenshot cursor is Sep17 03:30 Beijing. Verify the supplied OHLC exactly.
    cursor=frames["15m"].loc[pd.Timestamp("2026-09-17 03:30",tz="Asia/Shanghai")]
    np.testing.assert_allclose(cursor[["open","high","low","close"]].to_numpy(float),[.0006415,.0006443,.0006380,.0006420],rtol=0,atol=1e-12)
    summary={"symbol":"ONE-USDT-SWAP","tick":tick,"cursor_ohlc_matches":True,"timeframes":summaries,"strategy_changed":False}
    (EXP/"summary.json").write_text(json.dumps(summary,ensure_ascii=False,indent=2)+"\n")
    paths=[p for p in EXP.iterdir() if p.is_file() and p.name!="receipt.json"]+[Path(__file__)]
    receipt={"source_commit":subprocess.check_output(["git","rev-parse","HEAD"],text=True).strip(),"files":{str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}}
    (EXP/"receipt.json").write_text(json.dumps(receipt,indent=2)+"\n")
    print(json.dumps(summary,ensure_ascii=False,indent=2))
    print(pd.DataFrame(alltriplets).to_string(index=False))


if __name__=="__main__":main()
