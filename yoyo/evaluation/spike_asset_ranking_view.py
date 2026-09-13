"""Render a searchable local leaderboard from frozen ranking CSVs only.

No returns or entry rules are recomputed. All metrics preserve their source
units; cumulative event R is never labeled as an account return.
"""
from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path

ROOT = Path('experiments/active/exp-spike-v1-v8-asset-ranking-20260914-v1')
OUT = Path('analysis/html/p1_spike_v1_v8_asset_leaderboard_20260914.html')
PAGE = r'''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>SPIKE · 币种回测排行榜</title><style>
:root{color-scheme:light dark;--bg:#f1f5f6;--card:#fff;--fg:#192e34;--muted:#62777d;--line:#dce5e7;--green:#137965;--red:#bf4658}
@media(prefers-color-scheme:dark){:root{--bg:#0c1419;--card:#15212a;--fg:#e0edef;--muted:#98aab5;--line:#2b3c48;--green:#65d2ac;--red:#ef8d9a}}
*{box-sizing:border-box}body{margin:0;padding:32px;font:15px/1.6 system-ui,-apple-system,"PingFang SC",sans-serif;background:var(--bg);color:var(--fg)}main{max-width:1450px;margin:auto}h1{font-size:28px;margin:0 0 6px}p{color:var(--muted);margin:4px 0 20px}.controls{display:flex;flex-wrap:wrap;gap:12px;margin:20px 0}label{font-size:12px;color:var(--muted)}select,input{display:block;background:var(--card);color:var(--fg);border:1px solid var(--line);border-radius:8px;padding:10px;font:inherit;min-height:44px}input{max-width:180px}.table{background:var(--card);border:1px solid var(--line);border-radius:12px;overflow:auto;max-height:72vh}table{border-collapse:collapse;width:100%;font-variant-numeric:tabular-nums}th,td{text-align:right;padding:11px 14px;border-bottom:1px solid var(--line);white-space:nowrap}th{position:sticky;top:0;background:var(--card);cursor:pointer;text-align:right;color:var(--muted);font-size:12px}td:first-child,th:first-child{text-align:left;position:sticky;left:0;background:var(--card)}th:first-child{z-index:2}td.pos{color:var(--green)}td.neg{color:var(--red)}a{color:var(--green)}.count{font-size:13px;color:var(--muted);margin-bottom:10px}.links{display:flex;flex-wrap:wrap;gap:20px;margin-top:22px}@media(max-width:700px){body{padding:16px}h1{font-size:23px}}
</style><main><h1>SPIKE · 币种回测排行榜</h1>
<p>Binance / OKX / Gate · 2024-09-10 至 2026-09-10 UTC · 30m / 1H / 4H<br>V1 为归档共同执行多头；V8 多头从双向回放抽取，可能受同流空头占仓影响。累计净 R 是逐笔合计，跨所与周期含相关交易，不是账户收益率。</p>
<div class="controls">
<label>系统<select id="view"><option value="v1_common_execution_long">V1 共同执行 · 多头</option><option value="v8_long">V8 · 多头分组</option><option value="v8_both" selected>V8 · 双向</option></select></label>
<label>时间范围<select id="scope"><option value="full_closed">完整两年 · 已平仓</option><option value="development">前一年 · 去除跨切点交易</option><option value="validation" selected>后一年 · 已研究历史</option></select></label>
<label>统计维度<select id="dimension"><option value="asset">底层币种</option><option value="venue_asset">交易所 × 币种</option><option value="asset_timeframe_side">币种 × 周期 × 方向</option></select></label>
<label>搜索币种<input id="search" placeholder="BTC、ETH、RAVE"></label>
<label>最低交易数<input id="minimum" type="number" value="30" min="1"></label>
<label>排序<select id="sort"><option value="sum_net_r">累计净 R</option><option value="mean_net_r">平均净 R</option><option value="win_rate">净胜率</option><option value="pf_net_r">PF · 按 R</option><option value="realized_ge10r">兑现 ≥10R 笔数</option><option value="n_closed">交易数</option></select></label>
<label>顺序<select id="direction"><option value="desc">从高到低</option><option value="asc">从低到高</option></select></label>
</div><div class="count" id="count"></div><div class="table"><table><thead><tr id="head"></tr></thead><tbody id="body"></tbody></table></div>
<p style="margin-top:16px">点击表头也可排序。V1 信号稀疏，切换 V1 时默认显示低样本观察榜；可自行提高最低笔数。全期收益榜是事后描述，不能据此直接剔除同期亏损币。低样本只供查阅；PF 按 R 与按名义价格收益会不同。R 分母过小、费用占 R 过大及少数极端盈利，都可能扭曲排名。</p>
<div class="links"><a href="p1_spike_v1_v8_asset_ranking_20260914.html">研究结论与排除检验</a><a href="__ASSET_CSV__">下载完整币种统计 CSV</a><a href="__LEDGER__">下载全部逐笔账本 CSV.gz</a></div>
</main><script type="application/json" id="data">__DATA__</script><script>
const data=JSON.parse(document.getElementById('data').textContent),by=id=>document.getElementById(id);
const cols=[['label','币种 / 分组','text'],['n_closed','交易数','int'],['sum_net_r','累计净 R','number'],['mean_net_r','平均净 R','number'],['win_rate','净胜率','pct'],['pf_net_r','PF · R','number'],['pf_net_return','PF · 价格收益','number'],['realized_ge10r','兑现 ≥10R','int'],['top1_positive_r_share','最大单笔 / 正收益','pct'],['median_risk_fraction_at_entry','初始风险中位','pct'],['median_cost_r','费用 R 中位','number']];
for(const [key,label] of cols){const th=document.createElement('th');th.textContent=label;th.onclick=()=>{if(!Array.from(by('sort').options).some(o=>o.value===key)){const o=new Option(label,key);by('sort').add(o)}by('sort').value=key;by('direction').value=by('direction').value==='desc'?'asc':'desc';render()};by('head').append(th)}
function render(){const dim=by('dimension').value,query=by('search').value.trim().toUpperCase(),key=by('sort').value,sign=by('direction').value==='desc'?-1:1;const rows=data[dim].filter(r=>r.view===by('view').value&&r.scope===by('scope').value&&Number(r.n_closed)>=Math.max(1,Number(by('minimum').value)||1)&&r.asset.toUpperCase().includes(query));for(const r of rows){r.label=r.asset+(dim==='venue_asset'?' · '+r.venue:dim==='asset_timeframe_side'?' · '+r.timeframe_min+'m · '+(Number(r.side)===1?'多':'空'):'')};rows.sort((a,b)=>key==='label'?sign*a.label.localeCompare(b.label):sign*(Number(a[key])-Number(b[key]))||a.label.localeCompare(b.label));by('count').textContent=rows.length+' 个分组 · '+rows.reduce((s,r)=>s+Number(r.n_closed),0).toLocaleString()+' 笔已平仓事件';by('body').replaceChildren();for(const r of rows){const tr=document.createElement('tr');for(const [key,,type]of cols){const td=document.createElement('td'),n=Number(r[key]);td.textContent=type==='text'?r[key]:r[key]===''||r[key]===null?'—':!Number.isFinite(n)?String(r[key]):type==='pct'?(n*100).toFixed(2)+'%':type==='int'?n.toLocaleString():n.toLocaleString(undefined,{minimumFractionDigits:2,maximumFractionDigits:3});if(key.includes('net_r'))td.className=n>=0?'pos':'neg';tr.append(td)}by('body').append(tr)}}
by('view').addEventListener('input',()=>{by('minimum').value=by('view').value==='v1_common_execution_long'?1:30;render()});
for(const id of ['scope','dimension','search','minimum','sort','direction'])by(id).addEventListener('input',render);render();
</script></html>'''


def main() -> None:
    inputs = {}
    data = {}
    for dim in ['asset', 'venue_asset', 'asset_timeframe_side']:
        path = ROOT / f'results/full_v1/{dim}_ranking_all.csv'
        inputs[str(path)] = hashlib.sha256(path.read_bytes()).hexdigest()
        with path.open() as source:
            data[dim] = list(csv.DictReader(source))
    text = PAGE.replace('__DATA__', json.dumps(data, ensure_ascii=False).replace('<', '\\u003c'))
    text = text.replace('__ASSET_CSV__', str((ROOT/'results/full_v1/asset_ranking_all.csv').resolve()))
    text = text.replace('__LEDGER__', str((ROOT/'results/full_v1/normalized_ledger.csv.gz').resolve()))
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(text)
    (ROOT/'leaderboard_receipt.json').write_text(json.dumps({'inputs': inputs, 'builder_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(), 'output': str(OUT), 'output_sha256': hashlib.sha256(OUT.read_bytes()).hexdigest()}, indent=2))


if __name__ == '__main__':
    main()
