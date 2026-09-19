"""Source-backed diagnostic charts in the owner's existing dark TV-like style.

Inputs are the immutable 570-entry review and a declared illustrative selection.
All candles/MA/IMACD/anchors are reconstructed; future path is shaded and labeled.
The orange stair is the actual stop effective during each bar, not an ideal exit.
"""
from __future__ import annotations

import json
from pathlib import Path
import unicodedata

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from yoyo.evaluation import spike_v112_trade_review as review
from yoyo.evaluation import spike_v112_trade_book as book

BG, FG, GRID = book.BG, book.FG, book.GRID
GOLD, RED, GREEN = "#ffb454", "#ff7299", "#33d0a0"


def wrap(text, width=40):
    lines, line, used = [], "", 0
    for char in text:
        cost = 2 if unicodedata.east_asian_width(char) in ("F", "W") else 1
        if used + cost > width:
            lines.append(line); line, used = "", 0
        line += char; used += cost
    if line: lines.append(line)
    return "\n".join(lines)


def draw(path, pick, r, ctx, trace, parent, jt, lines):
    plt.rcParams["font.sans-serif"] = ["PingFang SC", "Heiti SC", "Arial Unicode MS", "DejaVu Sans"]
    plt.rcParams["axes.unicode_minus"] = False
    f, minutes = ctx["frame"], ctx["minutes"]
    i, s, e, end = [int(r[k]) for k in ("signal_i", "box_entry_i", "entry_i", "exit_i")]
    anchors = [review.time_x(f.index, ln["a_time"], minutes) for ln in lines]
    lo = int(max(0, min([s-16, i-70] + [a-10 for a in anchors]), i-300))
    hi = min(len(f)-1, max(end+48, i+60))
    assert lo <= i < e <= end <= hi
    part, x = f.iloc[lo:hi+1], np.arange(lo, hi+1)
    fig = plt.figure(figsize=(18, 10.5), facecolor=BG)
    ax = fig.add_axes([.055, .325, .63, .51], facecolor=BG)
    sub = fig.add_axes([.055, .105, .63, .17], facecolor=BG, sharex=ax)
    side = fig.add_axes([.755, .09, .225, .745], facecolor=BG)
    side.set_axis_off()
    for a in (ax, sub):
        a.tick_params(colors=FG, labelsize=9)
        a.grid(color=GRID, linewidth=.6)
        a.yaxis.tick_right()
        for spine in a.spines.values(): spine.set_color(GRID)
        if r["status"] == "closed": a.axvspan(end+.5, hi+.5, color="#7088a9", alpha=.055)
    colors = np.where(part.close >= part.open, book.UP, book.DOWN)
    ax.vlines(x, part.low, part.high, color=colors, linewidth=.7)
    ax.bar(x, np.maximum((part.close-part.open).abs(), part.close*1e-5),
           bottom=np.minimum(part.close,part.open), width=.72, color=colors, linewidth=0)
    for col,color in (("s20","#1db9a0"),("e20","#127a6c"),("s60","#4679c9"),
                      ("e60","#2f5796"),("s120","#9aa0a6"),("e120","#5f6368")):
        ax.plot(x, part[col], color=color, linewidth=.85, alpha=.9)
    # The parent's outcome is shown as a hindsight comparison, not another live order.
    pe, px = int(parent["entry_i"]), int(parent["exit_i"])
    pentry, pstop, prisk = [parent[k] for k in ("entry_price", "initial_stop", "initial_risk")]
    if px >= lo and pe <= hi:
        left, right = max(pe,lo), min(px,hi)
        ax.add_patch(plt.Rectangle((left,pstop),max(.8,right-left),pentry-pstop,
                                  facecolor=RED,edgecolor=RED,alpha=.14,lw=.7))
        ax.add_patch(plt.Rectangle((left,pentry),max(.8,right-left),3*prisk,
                                  facecolor=GREEN,edgecolor=GREEN,alpha=.075,lw=.7))
        for k in (1,2,3): ax.hlines(pentry+k*prisk,left,right,color=GREEN,ls="--",lw=.55,alpha=.6)
        if lo <= pe <= hi: ax.plot(pe,pentry,"^",color=GREEN,ms=8)
        if not parent["censored"] and lo <= px <= hi: ax.plot(px,parent["exit_price"],"x",color=FG,ms=8,mew=1.6)
    anchor_notes=[]
    for ln in lines:
        color="#e1e5ee" if ln["kind"]=="chart" else "#9db7ff"
        a=review.time_x(f.index,ln["a_time"],minutes)
        lx=np.arange(max(lo,int(np.ceil(a))),min(hi,i+24)+1)
        ax.plot(lx,ln["values"][lx],color=color,lw=1.5)
        for label in "abc":
            at=review.time_x(f.index,ln[f"{label}_time"],minutes)
            if lo<=at<=hi:
                ax.annotate(label.upper(),(at,ln[f"{label}p"]),xytext=(0,9),textcoords="offset points",
                            ha="center",color=color,fontsize=11,fontweight="bold")
            else: anchor_notes.append(f"{ln['source_tf']} {label.upper()}在图外：{book.bj(ln[f'{label}_time'])}")
        born=review.time_x(f.index,ln["born_time"],minutes)
        if lo<=born<=i:
            yy=np.interp(born,np.arange(len(f)),ln["values"])
            ax.annotate("三点确认",(born,yy),xytext=(0,27),textcoords="offset points",ha="center",
                        color=color,fontsize=8,arrowprops={"arrowstyle":"-","color":color,"lw":.6})
    # Stop value at bar k is the value active before bar k's price is evaluated.
    tx=trace.bar_i.to_numpy()-.45
    ax.step(np.r_[tx,end+.45],np.r_[trace.active_stop,trace.active_stop.iloc[-1]],
            where="post",color=GOLD,lw=1.9)
    ax.hlines(jt["initial_stop"],e,max(end,e+1),color=GOLD,ls=":",lw=.9,alpha=.6)
    ax.hlines(jt["entry_price"],e,max(end,e+1),color=GOLD,ls="--",lw=.7,alpha=.5)
    ax.axvline(i,color=GOLD,lw=.8,alpha=.55)
    ax.plot(e,jt["entry_price"],"^",color=GOLD,ms=10)
    if r["status"] == "closed": ax.plot(end,jt["exit_price"],"X",color=GOLD,ms=10)
    ax.annotate(f"突破+spike\n次根开盘 {jt['entry_price']:.6g}",(e,jt["entry_price"]),
                xytext=(15,42),textcoords="offset points",color=GOLD,fontsize=9,ha="left",
                bbox={"boxstyle":"round,pad=.35","fc":BG,"ec":GOLD,"alpha":.92},
                arrowprops={"arrowstyle":"-","color":GOLD,"lw":.8})
    exit_label=(f"退出 {jt['exit_price']:.6g}\n净 {r['net_r']:+.2f}R" if r["status"]=="closed" else "数据结束\n仍持仓")
    ax.annotate(exit_label,(end,jt["exit_price"]),xytext=(-12,-46),textcoords="offset points",
                color=GOLD,fontsize=9,ha="right",bbox={"boxstyle":"round,pad=.3","fc":BG,"ec":"none","alpha":.88},
                arrowprops={"arrowstyle":"-","color":GOLD,"lw":.8})
    low=min(part.low.min(),jt["initial_stop"])
    high=part.high.max()
    pad=(high-low)*.14
    ax.set_ylim(low-pad,high+pad)
    ax.set_xlim(lo-1,hi+1)
    ax.ticklabel_format(axis="y",style="plain",useOffset=False)
    ax.text(.01,.015,"六均线：SMA/EMA 20 · 60 · 120",transform=ax.transAxes,color="#8b96a9",fontsize=8)
    if r["status"]=="closed" and end+6<hi:
        ax.text((end+hi)/2,high+pad*.63,"退出后路径（事后）",ha="center",color="#94a3b8",fontsize=9)
    sub.axhline(0,color="#555",lw=.7)
    sub.plot(x,part.md,color="#679dff",lw=1.2,label="IMACD")
    sub.plot(x,part.sb,color=GOLD,lw=1.1,label="信号线")
    sub.legend(loc="upper left",facecolor=BG,edgecolor=GRID,labelcolor=FG,fontsize=8,ncol=2)
    ticks=np.unique(np.linspace(lo,hi,7).astype(int))
    sub.set_xticks(ticks)
    sub.set_xticklabels([f.index[k].tz_convert(book.BEIJING).strftime("%m-%d\n%H:%M") for k in ticks])
    sub.set_xlabel("北京时间 · 每根为原周期K线",color=FG,fontsize=9)
    plt.setp(ax.get_xticklabels(),visible=False)

    side.text(0,1,"逐笔诊断",color="white",fontsize=14,fontweight="bold",va="top")
    y=.93
    blocks=[("实际退出",f"{book.REASON.get(r['exit_reason'],r['exit_reason'])}；净{r['net_r']:+.2f}R。" if r["status"]=="closed" else "数据边界仍持仓，未实现收益。",GOLD),
            ("入场时",f"V9后第{r['bars_after_v9']}根；相对V9入场{r['entry_premium_pct']:+.2f}%。初始风险{r['initial_risk_frac']*100:.2f}%。",FG),
            ("追踪怎样生效",f"最高存活收盘{r['max_close_r']:.2f}R；追踪{'已激活' if r['trail_ever_armed'] else '未激活'}。末端保护{r['exit_stop_r']:+.2f}R。" if np.isfinite(r['max_close_r']) else "入场当根退出，未产生存活持仓收盘，追踪未激活。",FG),
            ("浮盈证据",f"止损前可确定至少{r['mfe_lower_r']:.2f}R；5m顺序上界{r['mfe_upper_r']:.2f}R。" if "stop" in r["exit_reason"] else f"账本持仓内最大浮盈{r['mfe_r']:.2f}R。",FG),
            ("这张图要看什么",pick["message"],GREEN)]
    if r.get("post_exit48_complete"):
        blocks.append(("退出后48根（事后）",f"最高{r['post_exit48_max_r']:+.2f}R；最低{r['post_exit48_min_r']:+.2f}R。未计入这笔实际收益。",FG))
    for title,body,color in blocks:
        body=wrap(body)
        side.text(0,y,title,color="#8e9db3",fontsize=9.5,va="top")
        y-=.032
        side.text(0,y,body,color=color,fontsize=10.5,va="top",linespacing=1.4)
        y-=.031*(body.count("\n")+1)+.034
    assert y > -.1, (path,y)
    fig.text(.055,.965,f"{r['review_id']}  {r['symbol']} · {r['timeframe']}   |   {pick['title']}",
             color="white",fontsize=18,fontweight="bold")
    fig.text(.055,.925,f"入场 {book.bj(r['entry_time'])} 北京   ·   突破来源 {r['source']}   ·   {r['category']}",color=FG,fontsize=11)
    ptext=f"{parent['net_r']:+.2f}R" if not parent["censored"] else "未平仓"
    fig.text(.055,.889,f"绿▲ / 白×：父V9模拟入场与退出（事后参照 {ptext}）   橙▲ / X：本笔入场与退出   橙阶梯：逐根已生效保护价",
             color=FG,fontsize=10)
    fig.text(.055,.858,"绿红框仅标父V9的 0～3R / 初始风险区；父V9与联合单是两次独立模拟，未合并收益。",color="#8b96a9",fontsize=9)
    fig.text(.055,.019,"Binance USDT永续原始数据重绘，非 TradingView 截图 · 20bp 往返成本 · 按机制选例，非随机样本 · 改法均待验证",color="#8b96a9",fontsize=9)
    if anchor_notes: fig.text(.055,.045,"；".join(anchor_notes),color="#8b96a9",fontsize=8)
    fig.savefig(path,dpi=115,facecolor=BG)
    plt.close(fig)
    return {"image":str(path),"trade_key":r["trade_key"],"lo":lo,"hi":hi,"entry_i":e,"exit_i":end,
            "anchors_outside":anchor_notes,"sha256":review.study.digest(path)}


def main():
    exp=review.EXP
    selection=exp/"figure_selection.json"
    declared=(*review._local_transitive_python((Path(__file__),)),selection)
    assert review._committed(declared),"Commit renderer and selection before generating charts"
    ledger=pd.read_csv(exp/"reviews.csv").set_index("trade_key",drop=False)
    picks=json.loads(selection.read_text())["examples"]
    files,meta=review.study.series_files(),review.study.symbol_meta()
    cache,receipts={},[]
    out=exp/"figures";out.mkdir(exist_ok=True)
    for n,pick in enumerate(picks,1):
        trade=ledger.loc[pick["trade_key"]].to_dict()
        symbol,tf=trade["symbol"],trade["timeframe"]
        if symbol not in cache:
            cache[symbol]=(review.inc.guarded_5m(files[symbol],review.study.START-pd.Timedelta(days=review.study.WARMUP_BARS)),{})
        base,contexts=cache[symbol]
        if tf not in contexts: contexts[tf]=review.context(symbol,tf,base,meta[symbol])
        ctx=contexts[tf]
        r,trace,parent,jt,lines=review.review_one(trade,ctx,base)
        path=out/f"{n:02d}_{r['review_id']}_{symbol}_{tf}.png"
        receipts.append(draw(path,pick,r,ctx,trace,parent,jt,lines))
        print(str(path),flush=True)
    (exp/"figure_receipt.json").write_text(json.dumps({"declared":{str(p):review.study.digest(p) for p in declared},"figures":receipts},ensure_ascii=False,indent=2)+"\n")


if __name__=="__main__": main()
