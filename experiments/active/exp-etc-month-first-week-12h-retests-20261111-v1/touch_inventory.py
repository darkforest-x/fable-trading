"""Index each actual five-minute rail intersection. Group only adjacent intersecting candles."""
import argparse
import csv
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
import json
from pathlib import Path
from urllib.parse import urlencode

import numpy as np
import pandas as pd

from touch_source import CACHE, END, SYMBOL, HERE, ROOT, fetch_okx
from yoyo.evaluation.month_first_week_touch_review import bounds

OUT = ROOT / "output/ETC月首周全部触价"
SCALE = 1_000_000
SIDES = {"upper": "上轨", "lower": "下轨"}
MONTHS = [day.strftime("%Y-%m") for day in pd.date_range("2019-11-01", "2026-11-01", freq="MS")]
SNAPSHOT = "OKX · ETC-USDT-SWAP · 北京时间截至 2026-11-11 16:10"
H1 = CACHE / "first-week-hourly"


def fetch_anchor(month):
    H1.mkdir(exist_ok=True)
    target = H1 / (month + ".json")
    if target.exists():
        return
    start, ready, _ = bounds(month)
    query = dict(instId=SYMBOL, bar="1H", before=int(start.timestamp())*1000-1,
                 after=int(ready.timestamp())*1000, limit=300)
    result = fetch_okx._request(fetch_okx.API+"?"+urlencode(query))
    if result.get("code") != "0":
        raise RuntimeError(f"{month} anchor request: {result.get('msg')}")
    rows = sorted([row[:5] for row in result["data"] if row[8]=="1"], key=lambda r:int(r[0]))
    target.with_suffix(".tmp").write_text(json.dumps(rows, separators=(",",":")))
    target.with_suffix(".tmp").replace(target)


def local(stamp):
    return pd.Timestamp(stamp, unit="s", tz="UTC").tz_convert("Asia/Shanghai").strftime("%Y-%m-%d %H:%M")


def px(value):
    return float(Decimal(int(value)) / (SCALE * 1000))


def exact_prices(raw):
    scaled = np.rint(raw*SCALE).astype(np.int64)
    if not np.allclose(raw, scaled/SCALE, rtol=0, atol=1e-10):
        raise ValueError("OHLC exceeds the declared six-decimal precision")
    return scaled


def format_price(x):
    return f"{x:.9f}".rstrip("0").rstrip(".")


def write_csv(name, rows):
    with (OUT / name).open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def prior_ledgers():
    first = pd.read_csv(HERE / "recomputed_first_touches.csv").dropna(subset=["first_h1_touch_utc"])
    orig = pd.read_csv(HERE / "first_touch_target_trades.csv")
    orig = orig.loc[(~orig.is_control) & orig.cap.eq("near_edge")]
    old_h1 = {(r.month, r.side): int(pd.Timestamp(r.first_h1_touch_utc).timestamp()) for r in first.itertuples()}
    h1_fills = {(r.month, r.rail_side): r for r in orig.itertuples()}
    retests = pd.read_csv(HERE / "entries.csv")
    retests = retests.loc[retests.arm.eq("rearmed_retests") & retests.first_touch_hour_utc.notna()]
    old_h12 = {(r.month, r.side, int(pd.Timestamp(r.first_touch_hour_utc).timestamp())): r for r in retests.itertuples()}
    return old_h1, h1_fills, old_h12


def build():
    OUT.mkdir(exist_ok=True, parents=True)
    chunks = []
    for month in MONTHS:
        with np.load(CACHE / (month+".npz")) as data:
            chunks.append(data["ohlc"])
    raw = np.concatenate(chunks)
    t = raw[:,0].astype(np.int64)//1000
    assert np.all(np.diff(t)>0) and np.all(t%300==0)
    quotes = exact_prices(raw[:,1:5])
    old_h1, h1_fills, old_h12 = prior_ledgers()
    old_months = pd.read_csv(HERE/"anchors.csv").set_index("month")
    months,segments,covered = [],[],[]
    for month in MONTHS:
        start, ready, end = [int(x.timestamp()) for x in bounds(month)]
        source = json.loads((H1 / (month+".json")).read_text())
        verified_hour_times = [int(r[0])//1000 for r in source]
        ok = verified_hour_times == list(range(start,ready,3600))
        left,right=np.searchsorted(t,[ready,min(end,END//1000)])
        missing = (min(end,END//1000)-ready)//300-(right-left)
        cover = dict(月份=month,首周开始_北京=local(start),区间确定_北京=local(ready),月线到期_北京=local(end),
                     首周实有小时=len(source),首周应有小时=(ready-start)//3600,已到期观察窗缺失5分钟根数=max(0,missing),
                     覆盖状态="完整" if ok and end<=END//1000 else "截至快照" if ok else "首周不完整",
                     上轨价="",下轨价="",上轨段数=0,下轨段数=0,触线K根数=0)
        covered.append(cover)
        if not ok:
            continue
        high,low = exact_prices(np.array([max(float(r[2]) for r in source),min(float(r[3]) for r in source)]))
        levels = {"upper":high*1000+(high-low)*618,"lower":low*1000-(high-low)*618}
        week = quotes[np.searchsorted(t,start):np.searchsorted(t,ready)]
        if len(week) == (ready-start)//300:
            assert high==week[:,1].max() and low==week[:,2].min(), month
        if month in old_months.index:
            old=old_months.loc[month]
            np.testing.assert_allclose([old.week_high,old.week_low,old.upper,old.lower],
                                       [high/SCALE,low/SCALE,px(levels["upper"]),px(levels["lower"])],rtol=0,atol=1e-10)
        m = dict(month=month,start=start,ready=ready,end=end,hi=high/SCALE,lo=low/SCALE,
                 upper=px(levels["upper"]),lower=px(levels["lower"]))
        months.append(m)
        cover.update(上轨价=m["upper"],下轨价=m["lower"])
        for side, level in levels.items():
            hit=(quotes[left:right,2]*1000<=level)&(quotes[left:right,1]*1000>=level)
            positions=left+np.flatnonzero(hit)
            if not len(positions):
                continue
            spans=np.split(positions,np.flatnonzero(np.diff(t[positions])!=300)+1)
            for seq,span in enumerate(spans,1):
                segments.append((int(span[0]),int(span[-1]),side,int(level),seq,m))
            cover[SIDES[side]+"段数"]=len(spans)
            cover["触线K根数"]+=len(positions)

    segments.sort(key=lambda row:(row[0],row[2]))
    events, segment_rows, touch_rows = [], [], []
    matched_h12, matched_h1, fills = set(),set(),set()
    old_status = {"account_funded":"成交","account_busy_skipped":"持仓占用，跳过",
                  "no_reclaim":"未收回","target_already_reached":"确认前已触止盈",
                  "no_entry":"无可用入场","wrong_side_entry":"开盘价不符"}
    for number,(a,b,side,level,seq,m) in enumerate(segments,1):
        eid = f"E{number:04d}"
        month = m["month"]
        ts,last=int(t[a]),int(t[b])
        e=dict(id=eid, month=month, side=side, time=ts, last=last, count=b-a+1)
        events.append(e)
        direction=1 if side=="upper" else -1
        prev="缺前一根" if a==0 or t[a]-t[a-1]!=300 else ("轨内" if (quotes[a-1,3]*1000-level)*direction<0 else "轨外" if (quotes[a-1,3]*1000-level)*direction>0 else "线上")
        key=(month,side)
        hist="历史回测范围外" if month not in old_months.index else "同月同侧再次碰线，首触策略跳过"
        h1=old_h1.get(key)
        trade=None
        if seq==1 and h1 is not None:
            if ts//3600*3600!=h1:
                raise ValueError((key,"old first hour disagrees with first literal intersection",local(ts),local(h1)))
            matched_h1.add(key)
            trade=h1_fills.get(key)
            if trade is not None:
                fills.add(key)
            hist="已成交" if trade is not None else "首触，未进入原1小时回测"
        hourkey=(*key,ts//3600*3600)
        candidate=old_h12.get(hourkey)
        h12="历史回测范围外" if month not in old_months.index else "该次未重新触发"
        if candidate is not None and hourkey not in matched_h12:
            matched_h12.add(hourkey)
            h12=old_status.get(candidate.status,candidate.status)
        common=dict(编号=eid,月份=month,方向=SIDES[side],当月同侧第几段=seq,轨价=format_price(px(level)))
        segment_rows.append(dict(**common,首根_北京=local(ts),末根_北京=local(last),连续触线根数=b-a+1,
                       前一根收盘位置=prev,首周高=m["hi"],首周低=m["lo"],
                       原1小时首触策略=hist,原入场时间_北京=local(int(pd.Timestamp(trade.entry_time).timestamp())) if trade else "",
                       原入场价=trade.entry_fill if trade else "",原止损价=trade.stop_price if trade else "",
                       原止盈价=trade.target_price if trade else "",原出场原因={"stop":"止损","target":"止盈","timeout":"到期"}.get(trade.exit_reason,trade.exit_reason) if trade else "",
                       原近侧止盈净收益百分比_滑点1bp=round(trade.net_return*100,4) if trade else "",
                       原12小时重复触轨策略=h12,我的说明=""))
        for j in range(a,b+1):
            o,h,l,c=quotes[j]/SCALE
            rel=(int(quotes[j,3])*1000-level)*direction
            touch_rows.append(dict(**common,段内根序号=j-a+1,开盘时间_北京=local(int(t[j])),
                                   开=o,高=h,低=l,收=c,收盘位置="轨内" if rel<0 else "轨外" if rel>0 else "线上",我的说明=""))
    assert matched_h1==set(old_h1) and fills==set(h1_fills)
    if matched_h12!=set(old_h12):
        raise ValueError(("12h joins",set(old_h12)-matched_h12))
    assert len(touch_rows)==sum(c["触线K根数"] for c in covered)
    write_csv("全部触价段.csv",segment_rows)
    write_csv("逐根5分钟触线.csv",touch_rows)
    write_csv("逐月覆盖.csv",covered)
    bars = np.column_stack((t,raw[:,1:5])).tolist()
    for bar in bars:
        bar[0]=int(bar[0])
    payload=dict(source=SNAPSHOT,bars=bars,months=months,events=events)
    template=(HERE/"touch_viewer.html").read_text()
    if template.count("__ETC_DATA__")!=1:
        raise ValueError("unexpected chart template")
    (OUT/"ETC全部触价.html").write_text(template.replace("__ETC_DATA__",json.dumps(payload,ensure_ascii=False,separators=(",",":"),allow_nan=False)))
    result=dict(source=SNAPSHOT,source_first_open=local(int(t[0])),source_last_open=local(int(t[-1])),
                n_bars=len(t),anchors=len(months),n_events=len(segments),n_touch_bars=len(touch_rows),
                n_first=sum(row["当月同侧第几段"]==1 for row in segment_rows),gaps_after_first_bar=int((t[-1]-t[0])//300+1-len(t)),
                n_upper=sum(row["方向"]=="上轨" for row in segment_rows),matched_prior_fills=len(fills))
    (OUT/"coverage.json").write_text(json.dumps(result,ensure_ascii=False,indent=2))
    print(json.dumps(result,ensure_ascii=False))


if __name__=="__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("--anchors",action="store_true")
    options=parser.parse_args()
    if options.anchors:
        with ThreadPoolExecutor(max_workers=3) as pool:
            list(pool.map(fetch_anchor,MONTHS))
    else:
        build()
