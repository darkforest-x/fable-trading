"""Replay one identified WIF screenshot using only completed hourly structures.

Source: Owner's OKX2026-08-20 01:00 UTC+8 chart and original100U ledger.
Features read OHLC through current closed hour; entry/add executes next open.
The running high and pullback low use only post-entry bars. Funding is charged
only at its actual historical settlement time to the then-held quantity.
Current public risk tiers are an explicit maintenance sensitivity, not evidence
of the schedule in force during the historical trade. No venue orders or cache
writes. OHLC input is read into memory; artifacts contain execution ledgers only.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import subprocess
import sys
from urllib.parse import urlencode

import pandas as pd

ENTRY_MS = 1787162400000  # 2026-08-19T18:00Z,after chart01:00 UTC+8 bar closes.
END_MS = 1787616000000    # 2026-08-25T00:00Z,exclusive.
HOUR = 3_600_000
EXP = Path("experiments/active/exp-wif-screenshot-roll-20260921-v1")


def stamp(ts):
    return pd.Timestamp(ts,unit="ms",tz="UTC").isoformat()


def sha(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(",",":"),allow_nan=False).encode()).hexdigest()


def tier_for(q,tiers):
    for row in sorted(tiers,key=lambda x:float(x["maxSz"])):
        if q <= float(row["maxSz"]):
            return float(row["mmr"]),float(row["maxLever"])
    raise ValueError("Position exceeds the supplied current tier schedule")


def replay(rows,marks,rates,tiers,*,quantity,leverage,cap,capital=100.,fee=.001,stop0=.1362,tick=.0001):
    """Conditional filled-trade replay; rows are consecutive UTC bar-open OHLCV."""
    if not rows or any(b[0]-a[0]!=HOUR for a,b in zip(rows,rows[1:])):
        raise ValueError("Missing hourly price bars")
    if any(r[0] not in marks for r in rows):
        raise ValueError("Missing matching mark bars")
    p0=float(rows[0][1]);q=float(quantity);cost=q*p0;paid=fee*cost;funding=0.
    s=float(stop0);r0=p0-s
    if r0<=0 or q<=0 or q!=int(q):raise ValueError("Invalid initial risk or contract quantity")
    mm,lev=tier_for(q,tiers)
    initial_margin=q*float(marks[rows[0][0]][1])/min(leverage,lev)
    if initial_margin+paid+fee*q*float(marks[rows[0][0]][1])>capital+1e-9:
        return {"status":"infeasible_initial","initial_margin":initial_margin,"initial_fee":paid,
                "capital":capital,"quantity":q,"leverage":leverage},[]
    events=[];adds=0;last=p0;running_high=float(rows[0][2]);pull=None;pending=None
    floor=q*s-cost-paid-fee*q*s;peak=capital-paid;min_buffer=math.inf

    def net(price):return q*price-cost-paid-fee*q*price-funding

    def emit(kind,ts,price,qty=0.,**kw):
        events.append({"kind":kind,"time_utc":stamp(ts),"price":price,"quantity":qty,
            "total_quantity":q,"common_stop":s,"net_at_stop":net(s),"committed_floor":floor,
            "equity_after_exit_fee":capital+net(price),"entry_notional":cost,
            "entry_fees":paid,"funding_paid":funding,**kw})

    def finish(i,price,reason,censored=False,ambiguous=False):
        ts=rows[i][0];balance=None if ambiguous else capital+net(price)
        emit("exit",ts,price,q,reason=reason,exit_window_end=stamp(ts+HOUR))
        return {"status":"ambiguous" if ambiguous else "complete","capital":capital,
            "initial_quantity":quantity,"leverage":leverage,"cap":cap,"entry_price":p0,
            "entry_time":stamp(rows[0][0]),"exit_bar":stamp(ts),"exit_window_end":stamp(ts+HOUR),
            "exit_price":None if ambiguous else price,"exit_reason":reason,"censored":censored,
            "final_balance":balance,"net_profit":None if ambiguous else net(price),"adds_count":adds,
            "final_quantity":q,"entry_notional":cost,"entry_fees":paid,"funding_paid":funding,
            "exit_fee":None if ambiguous else fee*q*price,"initial_margin":initial_margin,
            "minimum_maintenance_buffer":None if math.isinf(min_buffer) else min_buffer,
            "peak_close_equity_after_entry_fee":peak},events

    emit("entry",rows[0][0],p0,q,margin=initial_margin,maintenance_ratio=mm)
    for i,row in enumerate(rows):
        ts,o,h,lo,c=map(float,row[:5]);ts=int(ts);mo=float(marks[ts][1]);ml=float(marks[ts][3])
        if i>0 and ts in rates:
            charge=q*mo*rates[ts];funding+=charge
            emit("funding",ts,mo,rate=rates[ts],payment=charge)
        mm,_=tier_for(q,tiers)
        open_buffer=capital+q*mo-cost-paid-funding-q*mo*(mm+fee)
        if open_buffer<=0:
            return finish(i,o,"current_tier_open_liquidation_screen",ambiguous=True)
        if i>0 and o<=s:
            return finish(i,o,"opening_stop")
        if pending is not None:
            before=pending;pending=None
            hnet=net(s);retained=max(before,.5*hnet,0.)
            q_risk=max(0,math.floor((hnet-retained)/(o-s+fee*(o+s))+1e-9)) if o>s else 0
            if o<=last:q_risk=0

            def affordable(n):
                total=q+n;_,limit=tier_for(total,tiers)
                eq=capital+q*mo-cost-paid-funding+n*(mo-o)-fee*n*o
                required=total*mo/min(leverage,limit)+fee*total*mo
                return eq>=required-1e-9

            left,right=0,int(q_risk)
            while left<right:
                middle=(left+right+1)//2
                if affordable(middle):left=middle
                else:right=middle-1
            dq=left
            if dq>0:
                q+=dq;cost+=dq*o;paid+=fee*dq*o;last=o;adds+=1
                floor=max(before,net(s))
                mm,tlev=tier_for(q,tiers)
                emit("add",ts,o,dq,risk_quantity_cap=q_risk,retained_target=retained,
                    floor_before=before,margin=q*mo/min(leverage,tlev),maintenance_ratio=mm)
                if net(s)<retained-1e-7:raise AssertionError("Add consumes promised stop profit")
            else:
                floor=max(before,net(s));emit("reject",ts,o,reason="risk_funding_or_price")
        mm,_=tier_for(q,tiers)
        buffer=capital+q*ml-cost-paid-funding-q*ml*(mm+fee)
        min_buffer=min(min_buffer,buffer)
        if buffer<=0:
            return finish(i,o,"stop_and_maintenance_same_hour" if lo<=s else "current_tier_intrabar_liquidation_screen",ambiguous=True)
        if lo<=s:return finish(i,s,"intrabar_stop")
        peak=max(peak,capital+q*c-cost-paid-funding)
        old_s=s;old_floor=floor;raised=False
        if i>0:
            if pull is None:
                if c<float(rows[i-1][4]):pull=lo
                else:running_high=max(running_high,h)
            else:
                pull=min(pull,lo)
                if c>running_high:
                    candidate=math.floor((pull-tick)/tick+1e-8)*tick
                    if s<candidate<c:s=candidate;raised=True
                    emit("structure",ts+HOUR,c,frozen_high=running_high,pullback_low=pull,raised=raised)
                    running_high=h;pull=None
        eligible=raised and c>=p0+2*r0-1e-12 and (cap is None or adds<cap)
        if eligible:pending=old_floor
        elif s>old_s:floor=max(floor,net(s))
        if s>old_s:emit("stop_update",ts+HOUR,c,prior_stop=old_s,deferred=eligible)
    return finish(len(rows)-1,float(rows[-1][4]),"boundary_mark",censored=True)


def fetch_public():
    """Use the existing approved fetcher's public GET utility; no local cache write."""
    from src.data.fetch_okx import _request
    api="https://www.okx.com/api/v5/"
    def get(endpoint,params):
        p=_request(api+endpoint+"?"+urlencode(params))
        if p.get("code")!="0":raise ValueError(p)
        return p["data"]
    def candles(endpoint):
        rows=[];cursor=END_MS
        while cursor>ENTRY_MS:
            batch=get(endpoint,{"instId":"WIF-USDT-SWAP","bar":"1H","after":cursor,"limit":100})
            if not batch:raise ValueError("Missing public history")
            if any(str(r[-1])!="1" for r in batch):raise ValueError("Unconfirmed historical candle")
            rows.extend(batch);new=min(int(r[0]) for r in batch)
            if new>=cursor:raise ValueError("Pagination did not progress")
            cursor=new
        return sorted({int(r[0]):r for r in rows if ENTRY_MS<=int(r[0])<END_MS}.values(),key=lambda r:int(r[0]))
    return {"price":candles("market/history-candles"),"mark":candles("market/history-mark-price-candles"),
        "funding":get("public/funding-rate-history",{"instId":"WIF-USDT-SWAP","after":END_MS,"before":ENTRY_MS,"limit":100}),
        "tiers":get("public/position-tiers",{"instType":"SWAP","tdMode":"cross","instFamily":"WIF-USDT"})}


def run(payload,output):
    """Write only execution artifacts, never raw price history or account state."""
    rows=sorted([[int(r[0]),*map(float,r[1:6])] for r in payload["price"] if ENTRY_MS<=int(r[0])<END_MS])
    mark_rows=sorted([[int(r[0]),*map(float,r[1:5])] for r in payload["mark"] if ENTRY_MS<=int(r[0])<END_MS])
    if len(rows)!=126 or rows[0][0]!=ENTRY_MS or rows[-1][0]!=END_MS-HOUR:
        raise ValueError(f"Unexpected case coverage:{len(rows)}")
    if not math.isclose(rows[0][1],.1402) or not math.isclose(max(r[2] for r in rows),.2296):
        raise ValueError("Screenshot anchors do not match")
    marks={r[0]:r for r in mark_rows}
    if len(mark_rows)!=126 or set(marks)!={r[0] for r in rows}:
        raise ValueError("Incomplete or duplicate mark history")
    rates={int(r["fundingTime"]):float(r["realizedRate"]) for r in payload["funding"] if ENTRY_MS<int(r["fundingTime"])<END_MS}
    expected=set(range(((ENTRY_MS//(4*HOUR))+1)*4*HOUR,END_MS,4*HOUR))
    if set(rates)!=expected:raise ValueError("Funding settlement coverage differs from supplied4h archive")
    canonical={"price":rows,"mark":mark_rows,"funding":sorted(rates.items()),"tiers":payload["tiers"]}
    output=Path(output);output.mkdir(parents=True,exist_ok=False)
    receipt={"data_sha256":sha(canonical),"price_sha256":sha(rows),"mark_sha256":sha(mark_rows),
        "funding_sha256":sha(sorted(rates.items())),"current_tiers_sha256":sha(payload["tiers"]),
        "price_count":len(rows),"mark_count":len(mark_rows),"funding_count":len(rates),
        "source_commit":subprocess.check_output(["git","rev-parse","HEAD"],text=True).strip(),
        "generated_at":pd.Timestamp.now(tz="UTC").isoformat(),"source":"OKX public endpoints viaCCXT MCP or repository public GET utility",
        "raw_candles_persisted_locally":False,"historical_risk_tiers_verified":False}
    results=[];events=[]
    for profile,q,lev in (("original_base_repaired_margin",25000,40),("previous_adjusted_base",19000,30)):
        answers={};prefix={}
        for label,cap in (("none",0),("two",2),("continuous",None)):
            result,detail=replay(rows,marks,rates,payload["tiers"],quantity=q,leverage=lev,cap=cap)
            answers[label]=result;prefix[label]=[(x["time_utc"],x["price"],x["quantity"]) for x in detail if x["kind"]=="add"][:2]
            results.append({"profile":profile,"arm":label,**result})
            events.extend({"profile":profile,"arm":label,**d} for d in detail)
        if prefix["two"]!=prefix["continuous"]:raise AssertionError("First two adds differ")
        if all(a["status"]=="complete" for a in answers.values()):
            assert len({(a["exit_bar"],a["exit_price"]) for a in answers.values()})==1
    invalid,_=replay(rows,marks,rates,payload["tiers"],quantity=25000,leverage=35.05,cap=2)
    assert invalid["status"]=="infeasible_initial"
    receipt["original_35_05x_opening_control"]=invalid
    (output/"source_receipt.json").write_text(json.dumps(receipt,indent=2)+"\n")
    (output/"results.json").write_text(json.dumps(results,indent=2)+"\n")
    pd.DataFrame(events).to_csv(output/"events.csv",index=False)
    print(json.dumps(results,indent=2))


if __name__=="__main__":
    parser=argparse.ArgumentParser();parser.add_argument("--stdin-json",action="store_true")
    parser.add_argument("--output",type=Path,default=EXP/"run_v1");args=parser.parse_args()
    declared=[Path(__file__).resolve(),EXP/"PROJECT_PLAN.md",Path("tests/evaluation/test_wif_screenshot_case.py"),
              Path("src/data/fetch_okx.py"),Path("yoyo/evaluation/spike_v1_v8_be05.py")]
    from yoyo.evaluation.spike_v1_v8_be05 import _committed
    if not _committed(tuple(declared)):raise ValueError("Commit unchanged builder/tests/protocol before replay")
    run(json.load(sys.stdin) if args.stdin_json else fetch_public(),args.output)
