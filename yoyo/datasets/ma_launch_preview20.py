"""Build an Owner-review gallery of real sideways-to-upward MA releases.

Source: Owner's four MUBARAK/WIF screenshots on 2026-09-21 and the close-based
SMA/EMA20/60/120 palette in dual_ma_v1_1.pine. This is retrospective retrieval,
not a trading signal or a Gold-label builder. Ranking deliberately reads OHLC
through onset+40, while early views contain only bars through onset+3 and use
their own past-only vertical scale. No eligibility, labels, or splits are made.

The descriptive candidate features use OHLC through onset-1 over 20/90 bars,
SMA/EMA from the complete available prefix (at least 720 warmup bars), and
future close/high/low over 6/20/40 bars. These future fields NEVER enter a live
pipeline. Raw source files are read-only. The frozen pre-run plan names sources.
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import subprocess
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.collections import LineCollection, PolyCollection
from matplotlib.patches import Ellipse
import numpy as np
import pandas as pd
from PIL import Image, ImageDraw, ImageFont

from yoyo.datasets.ma_rope_filter import add_six_mas

ROOT = Path(__file__).resolve().parents[2]
EXPERIMENT = "exp-ma-launch-owner-preview20-20260921-v1"
DEFAULT_DIR = ROOT / "experiments/active" / EXPERIMENT
MA_NAMES = [f"{kind}{period}" for period in (20, 60, 120) for kind in ("sma", "ema")]
STEP = {"1H": 3600000, "5m": 300000, "3m": 180000}


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def dump(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def load_source(path):
    """Read valid chronological OHLC, retaining gaps for window-level rejection."""
    df = pd.read_csv(path, usecols=["ts", "open", "high", "low", "close", "volume"])
    if df.ts.duplicated().any() or not df.ts.is_monotonic_increasing:
        raise ValueError(f"Non-chronological source: {path}")
    if not np.isfinite(df.to_numpy(dtype=float)).all():
        raise ValueError(f"Non-finite source: {path}")
    valid = ((df.low > 0) & (df.high >= df[["open", "close"]].max(axis=1))
             & (df.low <= df[["open", "close"]].min(axis=1)) & (df.volume >= 0))
    if not valid.all():
        raise ValueError(f"Invalid candle: {path}")
    return add_six_mas(df)


def candidates(df, path):
    """Rank completed patterns; thresholds describe this review pack only."""
    name = path.stem[len("okx_"):]
    symbol, timeframe, _ = name.rsplit("_", 2)
    close, high, low = df.close, df.high, df.low
    prev = close.shift(1)
    tr = pd.concat([high-low, (high-prev).abs(), (low-prev).abs()], axis=1).max(axis=1)
    scale = tr.rolling(30).median().shift(1)
    upper, lower = df[MA_NAMES].max(axis=1), df[MA_NAMES].min(axis=1)
    band = (upper-lower).shift(1) / scale
    range90 = high.rolling(90).max().shift(1)-low.rolling(90).min().shift(1)
    drift90 = (prev-close.shift(90)).abs()/range90
    prior_top = high.rolling(20).max().shift(1)
    gain6 = (close.shift(-5)-prev)/scale
    gain40 = (close.shift(-39)-prev)/range90
    low20 = low.iloc[::-1].rolling(20).min().iloc[::-1]
    pullback = (prev-low20)/scale
    above = close > upper
    close_near = (prev-upper.shift(1)).abs()/scale
    gap = df.ts.diff().ne(STEP[timeframe]).astype(int)
    # Include the actual warmup and display/right-context dependencies.
    gaps = gap.rolling(901).sum().shift(-40)
    eligible = (
        (band < 1.8) & (close_near < 1.8) & (range90/scale < 18)
        & (drift90 < .55) & (close > prior_top) & above
        & (gain6 > 1.8) & (gain40 > .8) & (pullback < 2)
        & (scale/prev > .00005) & gaps.eq(0)
    )
    score = 3*np.log1p(gain40.clip(upper=6)) - .6*band - 1.2*drift90 - .1*close_near
    rows=[]
    for i in np.flatnonzero(eligible.fillna(False).to_numpy()):
        if i < 900 or i+45 >= len(df):
            continue
        rows.append({"symbol":symbol, "timeframe":timeframe,"source":str(path.relative_to(ROOT)),
                     "onset_i":int(i),"onset_open_ms":int(df.ts.iloc[i]),
                     "score":float(score.iloc[i]),"band_atr":float(band.iloc[i]),
                     "range90_atr":float((range90/scale).iloc[i]),
                     "future40_gain_over_prior90_range":float(gain40.iloc[i]),
                     "future6_gain_atr":float(gain6.iloc[i])})
    return rows


def render(df, row, destination, early=False):
    """Render true OHLC and causal MAs; early y limits exclude future data."""
    onset = row["onset_i"]
    start, stop = onset-140, onset+(4 if early else 41)
    part=df.iloc[start:stop].reset_index(drop=True)
    fig, ax=plt.subplots(figsize=(16,9), dpi=100)
    fig.subplots_adjust(left=.035,right=.94,bottom=.09,top=.90)
    ax.set_facecolor("white")
    lo=min(float(part.low.min()),float(part[MA_NAMES].min().min()))
    hi=max(float(part.high.max()),float(part[MA_NAMES].max().max()))
    span=max(hi-lo, hi*.001)
    ax.set_ylim(lo-span*.07,hi+span*.08)
    ax.set_xlim(-1,len(part)+1)
    colors=np.where(part.close >= part.open,"#3179f5","#713fc5")
    xs=np.arange(len(part)); width=.68
    wicks=[[(i,l),(i,h)] for i,l,h in zip(xs,part.low,part.high)]
    ax.add_collection(LineCollection(wicks,colors=colors,linewidths=.8,zorder=3))
    bodies=[]
    for i,o,c in zip(xs,part.open,part.close):
        bottom=min(o,c); top=max(o,c)
        top=max(top,bottom+span*.0007)
        bodies.append([(i-width/2,bottom),(i+width/2,bottom),(i+width/2,top),(i-width/2,top)])
    ax.add_collection(PolyCollection(bodies,facecolors=colors,edgecolors=colors,linewidths=.4,zorder=4))
    for period,color in [(20,"#585a64"),(60,"#496bff"),(120,"#ae45d0")]:
        for kind,alpha in [("sma",.64),("ema",.39)]:
            ax.plot(xs,part[f"{kind}{period}"],color=color,alpha=alpha,lw=1.15,zorder=2)
    center=float(df[MA_NAMES].iloc[onset-1].mean())
    circle_height=max(span*.115,float(df.high.iloc[onset:onset+4].max())-center+span*.025)
    ax.add_patch(Ellipse((141,center+circle_height*.22),width=14,height=circle_height,
                        fill=False,edgecolor="#ef2222",linewidth=1.7,zorder=6))
    ax.yaxis.tick_right(); ax.yaxis.set_label_position("right")
    ax.grid(True,ls=(0,(1,4)),color="#d6d9df",lw=.8)
    ax.tick_params(axis="both",length=0,labelsize=10,labelcolor="#797d85",pad=9)
    for s in ax.spines.values():s.set_visible(False)
    tick=np.linspace(0,len(part)-1,7).astype(int)
    times=pd.to_datetime(part.ts,unit="ms",utc=True).dt.tz_convert("Asia/Shanghai")
    ax.set_xticks(tick,[times.iloc[i].strftime("%m-%d\n%H:%M") for i in tick])
    ax.ticklabel_format(axis="y",style="plain",useOffset=False)
    symbol=row["symbol"].replace("_USDT_SWAP","USDT.P")
    onset_time=pd.to_datetime(row["onset_open_ms"],unit="ms",utc=True).tz_convert("Asia/Shanghai")
    title=f"{row['id']}   {symbol}  |  {row['timeframe']}  |  OKX  |  {onset_time:%Y-%m-%d %H:%M} UTC+8"
    fig.text(.035,.954,title,fontsize=16,color="#202938",weight="medium")
    subtitle=("EARLY VIEW: through onset + 3 closed bars; own past-only price scale" if early else
              "FULL REVIEW: includes 40 bars after proposed onset; retrospective example")
    fig.text(.035,.922,subtitle,fontsize=10,color="#757d88")
    fig.text(.035,.02,"SMA / EMA   20 gray    60 blue    120 purple     |     Red circle: proposed launch area, pending your review",
             fontsize=10,color="#757d88")
    fig.savefig(destination,facecolor="white",dpi=100)
    plt.close(fig)
    return {"start_i":start,"end_i_inclusive":stop-1,"bars":len(part),
            "visible_end_open_ms":int(part.ts.iloc[-1]),"image_sha256":digest(destination)}


def overview(rows, out):
    for page in range(2):
        subset=rows[page*10:page*10+10]
        canvas=Image.new("RGB",(1600,5*470),(238,241,246))
        draw=ImageDraw.Draw(canvas)
        for n,row in enumerate(subset):
            im=Image.open(out/row["full_image"]); im.thumbnail((790,445))
            x=(n%2)*800+5;y=(n//2)*470+5
            canvas.paste(im,(x,y))
        canvas.save(out/f"overview_{page+1}.jpg",quality=92)


def gallery(rows,out):
    items=json.dumps(rows,ensure_ascii=False).replace("</","<\\/")
    markup='''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>均线收拢启动 · 20 张真实行情预览</title><style>
*{box-sizing:border-box}body{margin:0;background:#f3f5f8;color:#172333;font:15px system-ui}header{padding:22px 3vw 14px;background:white;border-bottom:1px solid #e3e7ed}h1{font-size:23px;margin:0 0 8px}p{margin:6px 0;color:#657083;line-height:1.6}.toolbar{display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-top:15px}button,select,a.link{font:inherit;background:#fff;border:1px solid #ccd4df;border-radius:7px;padding:8px 13px;color:#273d57;cursor:pointer;text-decoration:none}button.active{background:#215ee7;color:white;border-color:#215ee7}main{max-width:1700px;margin:18px auto;padding:0 18px}#chart{width:100%;display:block;background:#fff;border-radius:8px}#caption{padding:10px 3px}.strip{display:flex;gap:8px;overflow:auto;padding:12px 0}.thumb{flex:0 0 150px;padding:3px}.thumb img{width:100%;display:block}.thumb span{font-size:12px}footer{font-size:13px;color:#687387;padding:15px 0 30px}kbd{background:#e9edf3;padding:2px 5px;border-radius:3px}</style>
<header><h1>均线收拢 → 向上启动</h1><p>20 个真实 OKX 历史事件。红圈是建议关注的启动区域，等待你确认形态；全部保留较长左侧背景。</p><div class="toolbar"><button id="prev">← 上一张</button><select id="pick"></select><button id="next">下一张 →</button><button id="full" class="active">完整走势</button><button id="early">只看启动时</button><a id="original" class="link" target="_blank">打开原图</a></div></header>
<main><img id="chart"><p id="caption"></p><div class="strip" id="strip"></div><footer>完整走势含启动后的 40 根，仅供形态预览；启动视图截到建议启动根之后第 3 根，价格轴也只按当时数据计算。历史筛选使用了后续走势，因此这些图尚不是独立测试集或已批准金标。用 <kbd>←</kbd> <kbd>→</kbd> 翻页，<kbd>空格</kbd> 切换视图。</footer></main><script>
const rows=ITEMS;let i=0,early=false;const $=s=>document.getElementById(s);rows.forEach((r,n)=>{const op=document.createElement('option');op.value=n;op.textContent=r.id+' · '+r.symbol.replace('_USDT_SWAP','')+' · '+r.timeframe;$('pick').append(op);const b=document.createElement('button');b.className='thumb';b.innerHTML='<img src="'+r.full_image+'" loading="lazy"><span>'+op.textContent+'</span>';b.onclick=()=>{i=n;show()};$('strip').append(b)});function show(){const r=rows[i],src=early?r.early_image:r.full_image;$('chart').src=src;$('chart').alt=r.id+' '+r.symbol;$('original').href=src;$('pick').value=i;$('full').classList.toggle('active',!early);$('early').classList.toggle('active',early);$('caption').textContent=(i+1)+' / 20 · '+r.onset_time_local+' · '+(early?'仅展示当时可见的启动附近':'完整历史走势，包含后文');document.querySelectorAll('.thumb').forEach((b,n)=>b.classList.toggle('active',n===i))}function move(d){i=(i+d+rows.length)%rows.length;show()}$('prev').onclick=()=>move(-1);$('next').onclick=()=>move(1);$('pick').onchange=e=>{i=+e.target.value;show()};$('full').onclick=()=>{early=false;show()};$('early').onclick=()=>{early=true;show()};document.addEventListener('keydown',e=>{if(e.target.tagName==='SELECT')return;if(e.key==='ArrowRight')move(1);if(e.key==='ArrowLeft')move(-1);if(e.code==='Space'){e.preventDefault();early=!early;show()}});show();</script></html>'''
    (out/"index.html").write_text(markup.replace("ITEMS",items))


def main():
    parser=argparse.ArgumentParser();parser.add_argument("--experiment-dir",type=Path,default=DEFAULT_DIR)
    args=parser.parse_args(); directory=args.experiment_dir
    plan=json.loads((directory/"plan.json").read_text());out=directory/"preview_v1"
    if out.exists():raise RuntimeError("Output already exists; use a new version")
    tracked=[str(Path(__file__).relative_to(ROOT)),str((directory/"plan.json").relative_to(ROOT))]
    status=subprocess.check_output(["git","status","--porcelain","--",*tracked],cwd=ROOT,text=True)
    if status:raise RuntimeError("Commit builder and plan before generating")
    commit=subprocess.check_output(["git","rev-parse","HEAD"],cwd=ROOT,text=True).strip()
    out.mkdir();(out/"full").mkdir();(out/"early").mkdir();(out/"ohlc").mkdir()
    pool=[];sources=[]
    for n,rel in enumerate(plan["sources"]):
        path=ROOT/rel;before=digest(path)
        df=load_source(path); rows=candidates(df,path)
        if digest(path)!=before:raise RuntimeError(f"Source changed during read: {path}")
        pool.extend(rows);sources.append({"path":rel,"sha256":before,"rows":len(df),"candidates":len(rows)})
        if n%10==0:print(f"Scanned {n+1}/{len(plan['sources'])}, candidates={len(pool)}",flush=True)
    pool.sort(key=lambda r:(-r["score"],r["source"],r["onset_i"]))
    dump(out/"candidate_pool.json",pool);dump(out/"source_manifest.json",sources)
    chosen=[];symbols=set();times=Counter()
    for row in pool:
        day=pd.to_datetime(row["onset_open_ms"],unit="ms",utc=True).strftime("%Y-%m-%d")
        if row["symbol"] in symbols or times[day]>=2:continue
        chosen.append(row);symbols.add(row["symbol"]);times[day]+=1
        if len(chosen)==20:break
    if len(chosen)!=20:raise RuntimeError(f"Only {len(chosen)} eligible independent symbols; no backfill")
    source_hashes={s["path"]:s["sha256"] for s in sources}
    for n,row in enumerate(chosen,1):
        row["id"]=f"{n:02d}"; row["source_sha256"]=source_hashes[row["source"]]
        row["training_eligible"]=False;row["production_eligible"]=False;row["owner_confirmed"]=False
        row["selection_uses_future"]=True;row["future_bars_for_selection"]=40
        path=ROOT/row["source"]
        if digest(path)!=row["source_sha256"]:raise RuntimeError("Source changed before rendering")
        df=load_source(path)
        row["onset_time_local"]=pd.to_datetime(row["onset_open_ms"],unit="ms",utc=True).tz_convert("Asia/Shanghai").isoformat()
        name=f"{n:02d}_{row['symbol']}_{row['timeframe']}.png"
        row["full_image"]="full/"+name;row["early_image"]="early/"+name
        row["full_view"]=render(df,row,out/row["full_image"])
        row["early_view"]=render(df,row,out/row["early_image"],True)
        row["ohlc_csv"]=f"ohlc/{n:02d}.csv"
        df.iloc[row["onset_i"]-900:row["onset_i"]+41].to_csv(out/row["ohlc_csv"],index=False)
        row["ohlc_sha256"]=digest(out/row["ohlc_csv"])
        print(f"Rendered {n}/20 {row['symbol']} {row['timeframe']}",flush=True)
    dump(out/"manifest.json",chosen);overview(chosen,out);gallery(chosen,out)
    dump(out/"receipt.json",{"experiment_id":EXPERIMENT,"builder_commit":commit,
         "builder_sha256":digest(__file__),"plan_sha256":digest(directory/"plan.json"),
         "generated_at":datetime.now(timezone.utc).isoformat(),"source_count":len(sources),
         "candidate_count":len(pool),"events":len(chosen),"unique_symbols":len(symbols),
         "timeframes":dict(Counter(r["timeframe"] for r in chosen)),
         "training_eligible":False,"production_eligible":False,
         "manifest_sha256":digest(out/"manifest.json")})
    print(str(out/"index.html"),flush=True)


if __name__=="__main__":main()
