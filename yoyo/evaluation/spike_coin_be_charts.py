#!/usr/bin/env python3
"""Build a local interactive chart book from completed SPIKE coin-policy ledgers.

This is a display-only consumer of the coin report's selected actual fills and
the authenticated frozen cache.  It does not contact TradingView or an exchange,
and candles following a signal exist only to explain historical exits.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import shutil
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import pandas as pd

from yoyo.evaluation.spike_coin_be_report import ROOT, sha256


VENDOR = ROOT / "yoyo/evaluation/static/spike_v1_review/vendor"
MA_COLUMNS = (("s20", "SMA20", "#60a5fa"), ("e20", "EMA20", "#93c5fd"), ("s60", "SMA60", "#f5c85b"),
              ("e60", "EMA60", "#f9df98"), ("s120", "SMA120", "#a78bfa"), ("e120", "EMA120", "#c4b5fd"))


def _ms(value: object) -> int | None:
    if value is None or pd.isna(value): return None
    return int(pd.Timestamp(value).tz_convert("UTC").timestamp() * 1000)


def tv_url(venue: str, symbol: str, minutes: int, asset: str | None = None) -> str:
    """Make a best-effort same-contract, same-period TradingView page link."""
    # CCXT forms such as ETH/USDT:USDT are not safely handled by string replace.
    base = asset or symbol.split("/")[0].split("-")[0].replace("USDT", "")
    return "https://www.tradingview.com/chart/?" + urlencode({"symbol": f"{venue.upper()}:{base.upper()}USDT.P", "interval": str(minutes)})


def _finite(value: object) -> float | None:
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) else None


def _record_payload(case: dict[str, Any], trades: pd.DataFrame, raw_root: Path, engine_root: Path) -> dict[str, Any]:
    base_id = case["baseline_trade_id"]
    baseline = trades.loc[trades.trade_id.eq(base_id)]
    if len(baseline) != 1: raise ValueError(f"missing unique baseline trade {base_id}")
    baseline = baseline.iloc[0]
    common = trades.loc[(trades.stream_key.eq(baseline.stream_key)) & (trades.cohort.eq(baseline.cohort))
                        & (trades.signal_bar_open.eq(baseline.signal_bar_open)) & (trades.entry_time.eq(baseline.entry_time))
                        & (trades.side.eq(baseline.side)) & trades.policy.isin(["baseline", "be1_price", "be1_cost"])].copy()
    if not {"baseline", "be1_price"}.issubset(set(common.policy)):
        raise ValueError(f"case is not a baseline/BE same-entry pair: {base_id}")
    folder = raw_root / "streams" / str(baseline.stream_key)
    receipt = json.loads((folder / "control_cache.receipt.json").read_text())
    cache_path = folder / "control_cache.pkl.gz"
    if sha256(cache_path) != receipt.get("cache_sha256"):
        raise ValueError(f"frozen cache hash mismatch: {folder.name}")
    bars = pd.read_pickle(cache_path)["bars"]
    entry = pd.Timestamp(baseline.entry_time)
    signal = pd.Timestamp(baseline.signal_bar_open)
    exits = pd.to_datetime(common.exit_time, utc=True, errors="coerce")
    latest = exits.max() if exits.notna().any() else entry
    minutes = int(baseline.timeframe_min)
    start, end = signal - pd.Timedelta(minutes=minutes * 40), latest + pd.Timedelta(minutes=minutes * 40)
    shown = bars.loc[(bars.index >= start) & (bars.index <= end)].copy()
    if shown.empty or entry not in shown.index or signal not in shown.index:
        raise ValueError(f"case timeframe is absent from frozen cache: {base_id}")
    candles = []
    for stamp, row in shown.iterrows():
        candle = {"t": _ms(stamp), "o": _finite(row.open), "h": _finite(row.high), "l": _finite(row.low), "c": _finite(row.close), "v": _finite(row.volume),
                  "md": _finite(row.md), "sb": _finite(row.sb)}
        for column, _, _ in MA_COLUMNS: candle[column] = _finite(row[column])
        if None in (candle["o"], candle["h"], candle["l"], candle["c"]): raise ValueError(f"non-finite OHLC in source cache: {base_id}")
        candles.append(candle)
    exit_rows = []
    for policy in ("baseline", "be1_price", "be1_cost"):
        hit = common.loc[common.policy.eq(policy)]
        if len(hit) != 1: continue
        row = hit.iloc[0]
        exit_rows.append({"policy": policy, "trade_id": row.trade_id, "time_ms": _ms(row.exit_time), "price": _finite(row.exit_price),
                          "reason": row.exit_reason, "net_r": _finite(row.net_r), "mfe_r": _finite(row.mfe_r), "censored": bool(row.censored)})
    engine_base = engine_root / "streams" / str(baseline.stream_key)
    completion = json.loads(engine_base.with_suffix(".completion.json").read_text())
    events_path = engine_base.with_suffix(".events.csv.gz")
    if sha256(events_path) != completion.get("output_sha256", {}).get(events_path.name):
        raise ValueError(f"engine event SHA-256 mismatch: {events_path.name}")
    events = pd.read_csv(events_path)
    selected_ids = set(common.trade_id)
    protections = events.loc[events.trade_id.isin(selected_ids) & events.event_kind.eq("protection_update")
                             & events.reason.isin(["be1_price", "be1_cost"])].copy()
    protection_rows = [{"policy": str(row.policy), "active_from_ms": _ms(row.event_time), "price": _finite(row.protection_after),
                        "reason": str(row.reason), "execution_phase": str(row.execution_phase)} for row in protections.itertuples(index=False)]
    entry_price, risk = float(baseline.entry_price), float(baseline.initial_risk)
    return {"case_id": case["case_id"], "case_kind": case["case_kind"], "asset": baseline.asset, "venue": baseline.venue, "symbol": baseline.symbol,
            "timeframe_min": minutes, "cohort": baseline.cohort, "side": int(baseline.side), "signal_bar_open_ms": _ms(signal),
            "signal_close_ms": _ms(signal + pd.Timedelta(minutes=minutes)), "signal_close_price": _finite(bars.loc[signal, "close"]),
            "entry": {"time_ms": _ms(entry), "price": entry_price}, "initial_stop": float(baseline.initial_stop), "initial_risk": risk,
            "r_levels": [{"label": f"{multiple}R", "price": entry_price + int(baseline.side) * multiple * risk} for multiple in (1, 2, 3)],
            "exits": exit_rows, "protections": protection_rows, "candles": candles,
            "coverage": {"start_ms": _ms(shown.index[0]), "end_ms": _ms(shown.index[-1]), "bars": len(shown)},
            "provenance": {"stream_key": baseline.stream_key, "raw_cache_sha256": receipt["cache_sha256"], "source_path": receipt.get("source_path"),
                             "source_sha256": receipt.get("source_sha256")}, "tick": _finite(receipt.get("tick")),
            "tradingview_url": tv_url(str(baseline.venue), str(baseline.symbol), minutes, str(baseline.asset))}


def html(records: list[dict[str, Any]]) -> str:
    """Return a self-contained data page using the installed Lightweight Charts v4 API."""
    data = json.dumps(records, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    template = '''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><link rel="icon" href="data:,">
<title>SPIKE 逐币 1R 保本复盘</title><style>body{margin:0;background:#0d1522;color:#dce7f5;font:14px system-ui,sans-serif}header,footer{padding:18px 24px;border-bottom:1px solid #26364d}h1{margin:0;font-size:20px}p{color:#afbed1}main{display:grid;grid-template-columns:300px 1fr}aside{padding:16px;border-right:1px solid #26364d}button,select,a{background:#19273a;color:#dce7f5;border:1px solid #364d6d;border-radius:5px;padding:8px;text-decoration:none}button{width:100%;text-align:left;margin:5px 0}.active{border-color:#eabf5f;background:#2b3546}label{display:block;margin-bottom:10px}select{width:100%}section{padding:16px 22px}#meta{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:8px}.fact{background:#152134;border:1px solid #26364d;padding:8px;border-radius:5px}.fact b{display:block;color:#eabf5f}.chart{height:380px}.small{height:160px}@media(max-width:800px){main{display:block}aside{border:0}}</style>
<header><h1>SPIKE 逐币：原退出 vs 1R 推保本</h1><p>TradingView Lightweight Charts v4.2.0（本地开源图表库），不是 TradingView 官网回测。信号后的 K 线只作历史退出复盘。</p></header><main><aside><label>筛选币种<select id="asset"><option value="">全部</option></select></label><div id="list"></div></aside><section><div id="title"></div><div id="meta"></div><p>绿点：次开盘入场；红/蓝/橙点：baseline／1R价格BE／1R含费BE实际成交价；虚线：SL、1R、2R、3R。</p><div id="price" class="chart"></div><div id="momentum" class="chart small"></div><div id="volume" class="chart small"></div></section></main><footer>本地冻结 OHLC 与已完成账本；北京时区。原站链接未保证定位历史 K 线。</footer><script src="vendor/lightweight-charts.standalone.production.js"></script><script>
const RECORDS=__DATA__;let active,charts=[],syncing=false;const fmt=new Intl.DateTimeFormat('zh-CN',{timeZone:'Asia/Shanghai',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false}),$=id=>document.getElementById(id),time=ms=>fmt.format(new Date(ms))+' BJT';
function precision(tick){let s=String(tick||.01);return s.includes('e-')?Number(s.split('e-')[1]):(s.split('.')[1]||'').length}function pf(r){let p=precision(r.tick);return{type:'price',precision:p,minMove:Math.pow(10,-p)}}function clean(){charts.forEach(x=>{x.observer.disconnect();x.chart.remove()});charts=[];['price','momentum','volume'].forEach(x=>$(x).innerHTML='')}
function setup(id,height,format){let el=$(id),chart=LightweightCharts.createChart(el,{width:el.clientWidth,height:el.clientHeight,layout:{background:{color:'#0d1522'},textColor:'#dce7f5'},grid:{vertLines:{color:'#1a2a40'},horzLines:{color:'#1a2a40'}},rightPriceScale:{borderColor:'#364d6d'},timeScale:{timeVisible:true,tickMarkFormatter:t=>fmt.format(new Date(Number(t)*1000))},localization:{timeFormatter:t=>fmt.format(new Date(Number(t)*1000))}});let observer=new ResizeObserver(()=>chart.resize(el.clientWidth,el.clientHeight));observer.observe(el);charts.push({chart,observer});return chart}function line(chart,color,format,dash=false){return chart.addLineSeries({color,lineWidth:1,lineStyle:dash?LightweightCharts.LineStyle.Dashed:LightweightCharts.LineStyle.Solid,crosshairMarkerVisible:false,lastValueVisible:false,priceLineVisible:false,priceFormat:format})}function sync(){charts.forEach(({chart})=>chart.timeScale().subscribeVisibleLogicalRangeChange(range=>{if(!range||syncing)return;syncing=true;charts.forEach(x=>{if(x.chart!==chart)x.chart.timeScale().setVisibleLogicalRange(range)});syncing=false}))}
function render(){clean();let r=active,rows=r.candles,format=pf(r),pc=setup('price',380,format),mc=setup('momentum',160,format),vc=setup('volume',160,format),side=r.side===1;let candles=pc.addCandlestickSeries({upColor:'#3fbf8f',downColor:'#e6636d',wickUpColor:'#62d7ab',wickDownColor:'#f18890',borderVisible:false,priceFormat:format});candles.setData(rows.map(x=>({time:x.t/1000,open:x.o,high:x.h,low:x.l,close:x.c})));[['s20','#60a5fa'],['e20','#93c5fd'],['s60','#f5c85b'],['e60','#f9df98'],['s120','#a78bfa'],['e120','#c4b5fd']].forEach(([k,c])=>line(pc,c,format).setData(rows.filter(x=>x[k]!=null).map(x=>({time:x.t/1000,value:x[k]}))));let end=Math.max(...r.exits.map(x=>x.time_ms||0),r.entry.time_ms+r.timeframe_min*60000);[['SL',r.initial_stop,'#f2777a'],...r.r_levels.map(x=>[x.label,x.price,'#eabf5f'])].forEach(([,v,c])=>line(pc,c,format,true).setData([{time:r.entry.time_ms/1000,value:v},{time:end/1000,value:v}]));let marks=[{time:r.signal_bar_open_ms/1000,position:side?'belowBar':'aboveBar',color:'#eabf5f',shape:side?'arrowUp':'arrowDown',text:'信号收盘'},{time:r.entry.time_ms/1000,position:side?'belowBar':'aboveBar',color:'#62d7ab',shape:'circle',text:'次开盘入场'}],colors={baseline:'#f2777a',be1_price:'#61aef7',be1_cost:'#eabf5f'},exitByPolicy=Object.fromEntries(r.exits.map(x=>[x.policy,x]));(r.protections||[]).forEach(x=>{let exit=exitByPolicy[x.policy];if(x.active_from_ms&&x.price!=null&&exit&&exit.time_ms){line(pc,colors[x.policy],format,true).setData([{time:x.active_from_ms/1000,value:x.price},{time:Math.max(exit.time_ms,x.active_from_ms+r.timeframe_min*60000)/1000,value:x.price}]);marks.push({time:x.active_from_ms/1000,position:side?'belowBar':'aboveBar',color:colors[x.policy],shape:'circle',text:x.reason+' 保护启用·次K生效'})}});r.exits.forEach(x=>{if(x.time_ms&&x.price!=null){marks.push({time:x.time_ms/1000,position:side?'aboveBar':'belowBar',color:colors[x.policy],shape:side?'arrowDown':'arrowUp',text:x.policy+' · '+(x.reason||'删失')});let point=pc.addLineSeries({color:colors[x.policy],lineVisible:false,pointMarkersVisible:true,pointMarkersRadius:5,priceFormat:format,lastValueVisible:false,priceLineVisible:false});point.setData([{time:x.time_ms/1000,value:x.price}])}});candles.setMarkers(marks.sort((a,b)=>a.time-b.time));line(mc,'#61aef7',format).setData(rows.filter(x=>x.md!=null).map(x=>({time:x.t/1000,value:x.md})));line(mc,'#eabf5f',format).setData(rows.filter(x=>x.sb!=null).map(x=>({time:x.t/1000,value:x.sb})));vc.addHistogramSeries({priceFormat:{type:'volume'},priceScaleId:''}).setData(rows.map(x=>({time:x.t/1000,value:x.v,color:x.c>=x.o?'#3fbf8f99':'#e6636d99'})));charts.forEach(x=>x.chart.timeScale().fitContent());sync();$('title').innerHTML='<h2>'+r.case_id+' · '+r.asset+' · '+r.venue+' · '+r.timeframe_min+'m · '+r.cohort+'</h2><p>'+r.case_kind+' · <a href="'+r.tradingview_url+'" target="_blank" rel="noreferrer">TradingView 同币同周期 ↗</a></p>';$('meta').innerHTML=[['信号收盘',r.signal_close_price+' · '+time(r.signal_close_ms)],['入场',r.entry.price+' · '+time(r.entry.time_ms)],['初始SL',r.initial_stop],['图表范围',r.coverage.bars+' 根'],...r.exits.map(x=>[x.policy,(x.censored?'删失 ':'')+(x.reason||'—')+' · '+x.price+' · '+time(x.time_ms)+' · '+(x.net_r==null?'—':x.net_r.toFixed(2)+'R')])].map(x=>'<div class="fact">'+x[0]+'<b>'+x[1]+'</b></div>').join('')}
function list(){let f=$('asset').value,rs=RECORDS.filter(r=>!f||r.asset===f);$('list').innerHTML=rs.map(r=>'<button class="'+(active&&r.case_id===active.case_id?'active':'')+'" data-case="'+r.case_id+'">'+r.case_id+' · '+r.asset+' '+r.timeframe_min+'m<br><small>'+r.case_kind+'</small></button>').join('');document.querySelectorAll('#list [data-case]').forEach(node=>node.addEventListener('click',()=>pick(node.dataset.case)))}function pick(id){active=RECORDS.find(r=>r.case_id===id);list();render()}[...new Set(RECORDS.map(x=>x.asset))].sort().forEach(x=>$('asset').insertAdjacentHTML('beforeend','<option>'+x+'</option>'));$('asset').onchange=list;pick(RECORDS[0].case_id);</script></html>'''
    # Keep the line labels visible in the fact strip, where they remain legible
    # even when a dense price pane has no spare right-scale room.
    template = template.replace(
        "line(mc,'#eabf5f',format).setData(rows.filter(x=>x.sb!=null).map(x=>({time:x.t/1000,value:x.sb})));vc.addHistogramSeries",
        "line(mc,'#eabf5f',format).setData(rows.filter(x=>x.sb!=null).map(x=>({time:x.t/1000,value:x.sb})));line(mc,'#8292aa',format,true).setData([{time:rows[0].t/1000,value:0},{time:rows.at(-1).t/1000,value:0}]);vc.addHistogramSeries",
    )
    template = template.replace(
        "['初始SL',r.initial_stop],['图表范围',r.coverage.bars+' 根']",
        "['初始SL',r.initial_stop],...r.r_levels.map(x=>[x.label,x.price]),['图表范围',r.coverage.bars+' 根']",
    )
    return template.replace("__DATA__", data)


def build(review: Path) -> dict[str, object]:
    """Build charts plus a receipt under an already-created coin report directory."""
    manifest = json.loads((review / "case_manifest.json").read_text())
    source = Path(manifest["source"])
    raw_root = Path(json.loads((source / "config.json").read_text())["raw"])
    trades = pd.read_csv(review / "statistics/coin_trade_ledger.csv.gz")
    records = [_record_payload(case, trades, raw_root, source / "engine_results/full_v1") for case in manifest["cases"]]
    charts = review / "charts"; charts.mkdir(parents=True, exist_ok=True)
    for record in records:
        (charts / f"{record['case_id']}.json").write_text(json.dumps(record, ensure_ascii=False, allow_nan=False, indent=2) + "\n")
    shutil.copytree(VENDOR, charts / "vendor", dirs_exist_ok=True)
    (charts / "index.html").write_text(html(records))
    receipt = {"records": len(records), "case_manifest_sha256": sha256(review / "case_manifest.json"),
               "index_sha256": sha256(charts / "index.html"), "records_sha256": {record["case_id"]: sha256(charts / f"{record['case_id']}.json") for record in records}}
    (charts / "chart_receipt.json").write_text(json.dumps(receipt, indent=2) + "\n")
    return receipt


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--review", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(build(args.review), indent=2))


if __name__ == "__main__": main()
