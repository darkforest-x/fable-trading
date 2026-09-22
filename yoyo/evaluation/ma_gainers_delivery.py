"""Build a retrospective, raw-model review gallery for the frozen top20 scan.

No selection or trading rule is applied to predictions. Overlapping predicted
time intervals of the same direction are merged for display only, never
represented as independent market events. Overview charts use later same-day
context and are physically distinct from the exact model inputs.
"""
from __future__ import annotations

from collections import defaultdict
import hashlib
import html
import json
from pathlib import Path
from zoneinfo import ZoneInfo

import matplotlib
matplotlib.use("Agg")
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle
import numpy as np
import pandas as pd
import cv2

from yoyo.datasets.ma_profit_dataset import add_hl2_mas

ROOT = Path(__file__).resolve().parents[2]
EXP = ROOT / "experiments/active/exp-ma-morphology-top20-20260922-v1"
BJ = ZoneInfo("Asia/Shanghai")
COLORS = {"long": "#2574eb", "short": "#733cba"}


def bj(value):
    return pd.Timestamp(value).tz_convert("Asia/Shanghai").strftime("%m-%d %H:%M")


def grouped_regions(rows):
    """Merge overlapping same-side core intervals, preserving all observations."""
    buckets = defaultdict(list)
    for row in rows:
        if row.get("status") != "scored":
            continue
        for box in row["boxes"]:
            buckets[(row["symbol"], row["minutes"], box["mapped_direction"])].append({"row": row, "box": box})
    result = []
    for (symbol, minutes, direction), items in sorted(buckets.items()):
        items.sort(key=lambda x: (x["box"]["predicted_core_start_i"], x["row"]["decision_time_utc"]))
        group = None
        for item in items:
            box, row = item["box"], item["row"]
            start, end = box["predicted_core_start_i"], box["predicted_core_end_i"]
            if group is None or start > group["end_i"]:
                if group is not None:
                    result.append(group)
                group = {"symbol":symbol,"minutes":minutes,"direction":direction,"start_i":start,"end_i":end,
                         "first_seen_utc":row["decision_time_utc"],"last_seen_utc":row["decision_time_utc"],
                         "observations":1,"representative":item}
            else:
                group["end_i"] = max(group["end_i"], end)
                group["first_seen_utc"] = min(group["first_seen_utc"], row["decision_time_utc"])
                group["last_seen_utc"] = max(group["last_seen_utc"], row["decision_time_utc"])
                group["observations"] += 1
                if box["confidence"] > group["representative"]["box"]["confidence"]:
                    group["representative"] = item
        if group is not None:
            result.append(group)
    result.sort(key=lambda g: (g["symbol"],g["minutes"],g["first_seen_utc"],g["direction"]))
    for index, group in enumerate(result, 1):
        group["region_id"] = index
    return result


def overview(frame, groups, symbol, minutes, day_start, output):
    """Show full-day review context, explicitly outside model input images."""
    f = add_hl2_mas(frame)
    times = pd.to_datetime(f.open_time, utc=True)
    begin = max(0, int(np.searchsorted(times.astype("int64"), day_start.value)) - 24)
    v = f.iloc[begin:].copy()
    dates = mdates.date2num(pd.to_datetime(v.open_time, utc=True).dt.to_pydatetime())
    width = minutes / 1440 * .64
    fig, ax = plt.subplots(figsize=(16, 7.7), dpi=110)
    fig.patch.set_facecolor("#f7f9fc")
    ax.set_facecolor("white")
    for x, (_, r) in zip(dates, v.iterrows()):
        color = "#2878ee" if r.close >= r.open else "#713fbd"
        ax.plot([x,x],[r.low,r.high],color=color,lw=.65)
        bottom = min(r.open,r.close)
        height = max(abs(r.close-r.open),abs(r.close)*1e-6)
        ax.add_patch(Rectangle((x-width/2,bottom),width,height,facecolor=color,edgecolor=color,lw=.4))
    for name,color in zip(("sma20","ema20","sma60","ema60","sma120","ema120"),
                          ("#858b96","#b8bdc6","#618bff","#a9bbff","#c981e3","#e0b6f1")):
        ax.plot(dates,v[name],lw=.9,color=color,label=name)
    for group in sorted(groups,key=lambda g:-g["representative"]["box"]["confidence"])[:5]:
        item = group["representative"]
        r,b = item["row"],item["box"]
        x0,y0,x1,y1 = b["xyxy_pixels"]
        start_float = r["window_start_i"] + (x0-12)/1256*(r["n_bars"]-1)
        end_float = r["window_start_i"] + (x1-12)/1256*(r["n_bars"]-1)
        first_date = mdates.date2num(pd.Timestamp(f.open_time.iloc[0]).to_pydatetime())
        start_x = first_date + start_float*minutes/1440
        end_x = first_date + end_float*minutes/1440
        pmin,pmax = r["visible"]["price_min"],r["visible"]["price_max"]
        high = pmax-(y0-12)/718*(pmax-pmin)
        low = pmax-(y1-12)/718*(pmax-pmin)
        color = COLORS[group["direction"]]
        ax.add_patch(Rectangle((start_x,low),end_x-start_x,high-low,fill=False,edgecolor=color,lw=1.8))
        ax.annotate(f"#{group['region_id']} {group['direction'][0].upper()} {b['confidence']:.2f}",
                    (start_x,high),xytext=(0,6),textcoords="offset points",fontsize=8,color=color)
    ax.axvline(mdates.date2num(day_start.to_pydatetime()),color="#bbc4d2",lw=1,ls="--")
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%m-%d %H:%M",tz=BJ))
    ax.grid(alpha=.15)
    ax.set_title(f"{symbol} | {minutes}m | today's full context (Beijing time)\n"
                 "Review only; up to 5 highest-confidence region representatives. Model saw separate W18/W19 inputs.",loc="left",fontsize=12,pad=14)
    ax.legend(ncol=6,loc="upper left",fontsize=8)
    ax.tick_params(axis="both",labelsize=8)
    fig.autofmt_xdate(rotation=0)
    fig.tight_layout()
    output.parent.mkdir(exist_ok=True)
    fig.savefig(output)
    plt.close(fig)


def main():
    inputs,out = EXP/"inputs",EXP/"results"
    rank = json.loads((inputs/"ranking.json").read_text())
    summary = json.loads((out/"summary.json").read_text())
    rows = [json.loads(line) for line in (out/"predictions.jsonl").read_text().splitlines()]
    image_audit=[]
    for row in rows:
        if row.get("status") != "scored":
            continue
        assert row["tensor_geometry"]["observed_preprocess_shape"] == [768,1280]
        raw = out/"raw"/(row["stem"]+".png")
        if raw.exists():
            image = cv2.imread(str(raw))
            assert image.shape == (742,1280,3)
            ok, encoded = cv2.imencode(".png",image,[cv2.IMWRITE_PNG_COMPRESSION,3])
            assert ok and hashlib.sha256(encoded.tobytes()).hexdigest()==row["input_png_sha256"]
            image_audit.append({"stem":row["stem"],"input_encoded_sha256":row["input_png_sha256"],
                                "saved_png_sha256":hashlib.sha256(raw.read_bytes()).hexdigest(),
                                "decoded_bgr_pixel_sha256":hashlib.sha256(image.tobytes()).hexdigest(),"input_pixel_parity":True})
    (out/"image_provenance.json").write_text(json.dumps({"status":"passed","images":image_audit},indent=2))
    groups = grouped_regions(rows)
    by_stream = defaultdict(list)
    for group in groups:
        by_stream[(group["symbol"],group["minutes"])].append(group)
    streams = {(s["symbol"],s["minutes"]):s for s in summary["streams"]}
    by_stem = {r["stem"]:r for r in rows if r.get("status")=="scored"}
    table,sections,coin_summary = [],[],[]
    def directions(items):
        return " / ".join(("多" if side=="long" else "空")+str(sum(g["direction"]==side for g in items)) for side in ("long","short") if any(g["direction"]==side for g in items)) or "未检出"
    for coin in rank["ranked"]:
        symbol = coin["symbol"]
        body,cells,latest,coin_groups = [],[],[],[]
        for minutes in (15,30,60):
            stream = streams[(symbol,minutes)]
            selected = by_stream[(symbol,minutes)]
            coin_groups.extend(selected)
            if stream["status"] != "ok":
                cells.append("历史不足")
                body.append(f"<h3>{minutes}分钟 · 历史不足</h3><p>无法满足与训练一致的1200根预热，未评分。</p>")
                continue
            latest_boxes = [b for r in stream["latest_rows"] if r.get("stem") in by_stem for b in by_stem[r["stem"]]["boxes"]]
            latest_sides = sorted({"多" if b["mapped_direction"]=="long" else "空" for b in latest_boxes})
            if latest_sides:
                latest.append(str(minutes)+"m "+"/".join(latest_sides))
            cells.append(directions(selected))
            chart = f"overview/{symbol}_{minutes}m.png"
            f = pd.read_csv(ROOT/stream["source_path"])
            f["open_time"] = pd.to_datetime(f.open_time,utc=True)
            overview(f,selected,symbol,minutes,pd.Timestamp(rank["day_start_utc"]),out/chart)
            detail = []
            for group in sorted(selected,key=lambda g:g["first_seen_utc"]):
                r,b = group["representative"]["row"],group["representative"]["box"]
                image = "overlay/"+r["stem"]+".png"
                detail.append(f'<article><a target="_blank" href="{image}"><img loading="lazy" src="{image}"></a>'
                              f'<p>#{group["region_id"]} {"多头" if group["direction"]=="long" else "空头"} · {b["confidence"]:.3f}'
                              f'<br>首次检出 {bj(group["first_seen_utc"])} · 代表图截至 {bj(r["decision_time_utc"])}'
                              f'<br>框内K线 {bj(b["predicted_core_start_open_time_utc"])} — {bj(b["predicted_core_end_close_time_utc"])}'
                              f' · 框右侧还有{r["window_end_i"]-b["predicted_core_end_i"]}根'
                              f'<br>同区段原始框 {group["observations"]} 个 · <a target="_blank" href="raw/{r["stem"]}.png">无框模型输入</a></p></article>')
            latest_imgs=[]
            for item in stream["latest_rows"]:
                if item.get("stem"):
                    image=("overlay" if item["detected"] else "raw")+"/"+item["stem"]+".png"
                    latest_imgs.append(f'<a href="{image}" target="_blank">最新{item["n_bars"]}根窗：{"有框" if item["detected"] else "无框"}</a>')
            body.append(f'<details><summary>{minutes}分钟 · {directions(selected)} · 最新窗 {"/".join(latest_sides) or "无框"}</summary>'
                        f'<p>{" ｜ ".join(latest_imgs)}</p><a target="_blank" href="{chart}"><img loading="lazy" class="overview" src="{chart}"></a>'
                        f'<p>上图是审核总览，包含检出后的当日走势：蓝框多头、紫框空头。下方是实际模型输入与预测框：绿框多头、橙红框空头。</p>'
                        f'<div class="grid">{"".join(detail) or "今日无达到0.25阈值的预测框。"}</div></details>')
        status="；".join(latest) or "无检出（历史不足项另列）"
        table.append(f'<tr><td>{coin["rank"]}</td><td><a href="#{symbol}">{symbol.replace("-USDT-SWAP","")}</a></td><td>+{coin["change_today_pct"]:.2f}%</td>'+"".join(f"<td>{c}</td>" for c in cells)+f"<td>{status}</td></tr>")
        sections.append(f'<section id="{symbol}" data-symbol="{symbol}"><h2>#{coin["rank"]} {symbol} · 今日 +{coin["change_today_pct"]:.2f}%</h2>{"".join(body)}</section>')
        coin_summary.append({**coin,"regions":len(coin_groups),"latest_detections":latest,
                             "periods":{str(m):{"regions":len(by_stream[(symbol,m)]),"status":streams[(symbol,m)]["status"],"latest_detected":streams[(symbol,m)]["latest_endpoint_detected"]} for m in (15,30,60)}})
    (out/"regions.json").write_text(json.dumps(groups,ensure_ascii=False,indent=2))
    (out/"board_summary.json").write_text(json.dumps({"ranking":rank,"coins":coin_summary,"regions":len(groups),"scan_totals":summary["totals"]},ensure_ascii=False,indent=2))
    flat=[{"rank":r["rank"],"symbol":r["symbol"],"change_today_pct":r["change_today_pct"],"merged_regions":r["regions"],"latest":";".join(r["latest_detections"])} for r in coin_summary]
    pd.DataFrame(flat).to_csv(out/"board_summary.csv",index=False)
    page='''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>A组 · 今日涨幅前20扫描</title>
<style>body{font:15px/1.65 -apple-system,BlinkMacSystemFont,sans-serif;background:#f3f6fa;color:#202d41;margin:0}main{max-width:1500px;margin:auto;padding:30px}h1{font-size:29px}h2{font-size:23px}h3{font-size:18px}a{color:#286bd3}header,section{background:white;padding:24px;border-radius:14px;margin:20px 0}table{width:100%;border-collapse:collapse;font-size:14px}th,td{border-bottom:1px solid #e3e8ef;padding:10px;text-align:left}th{background:#eef3fa}small,p{color:#596679}summary{cursor:pointer;padding:13px;background:#edf3fb;border-radius:8px;margin:12px 0}.overview{width:100%;height:auto}.grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:18px}.grid img{width:100%;height:auto;border:1px solid #e3e8ef}article p{font-size:13px}.note{border-left:4px solid #b87617;padding:10px 15px;background:#fff7e9}.toolbar{position:sticky;top:0;background:#f3f6fa;padding:12px;z-index:2}select,button{padding:8px;font:inherit}@media(max-width:800px){.grid{grid-template-columns:1fr}main{padding:10px}header,section{padding:12px}table{font-size:12px}}</style><main>'''
    page+=f'<header><h1>A组模型 · 今日涨幅前20</h1><p>OKX USDT 永续 · 快照 {html.escape(rank["snapshot_beijing"])} · 相对北京时间零点涨幅<br>15分 / 30分 / 1小时，逐个已收盘窗口扫描；模型阈值0.25，A权重不变。</p><p class="note">这是模型原始检出，不是人工认可或盈利信号。相邻窗口重叠框合并为阅览区段，不能当独立事件数。当前涨幅榜回看不代表当时能选中这些币；30分/1小时训练样本极少。历史不足不算未检出。</p><p><a href="board_summary.csv">下载榜单CSV</a> · <a href="predictions.jsonl">全部原始预测（含无框）</a> · <a href="run_metadata.json">模型与扫描参数</a></p></header>'
    page+='<div style="overflow:auto"><table><thead><tr><th>排名</th><th>合约</th><th>今日涨幅</th><th>15m 区段</th><th>30m 区段</th><th>1h 区段</th><th>最新收盘窗</th></tr></thead><tbody>'+"".join(table)+'</tbody></table></div>'
    page+='<div class="toolbar">筛选合约 <select id="filter"><option value="">全部20名</option>'+"".join(f'<option value="{r["symbol"]}">{r["rank"]}. {r["symbol"]}</option>' for r in rank["ranked"])+ '</select></div>'+"".join(sections)
    page+='''</main><script>document.querySelector('#filter').onchange=e=>document.querySelectorAll('section').forEach(s=>s.hidden=!!e.target.value&&s.dataset.symbol!==e.target.value);</script></html>'''
    (out/"index.html").write_text(page)
    print(json.dumps({"gallery":str(out/"index.html"),"regions":len(groups),"coins_with_regions":sum(r["regions"]>0 for r in coin_summary),"coins_with_latest":sum(bool(r["latest_detections"]) for r in coin_summary)},ensure_ascii=False))


if __name__ == "__main__":
    main()
