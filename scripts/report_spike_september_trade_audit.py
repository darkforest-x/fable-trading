#!/usr/bin/env python3
"""Render the owner-approved September ETH3m V8 trade-audit payload.

This renderer never reads OHLCV, calls an exchange, or replays a strategy.  It
only reads the builder's ``results/audit_payload.json`` after its source receipt
exists, writes the required Markdown audit note, invokes ``md_to_html.py``, and
then appends an entirely local SVG/JavaScript inspection panel to that HTML.
"""
from __future__ import annotations

import argparse
import html
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXP = ROOT / "experiments/active/exp-spike-eth3m-september-trade-audit-20260914-v1"
RESULTS = EXP / "results"
PAYLOAD = RESULTS / "audit_payload.json"
RECEIPT = RESULTS / "source_receipt.json"
REPORT = ROOT / "analysis/p1_spike_eth3m_september_trade_audit_20260914.md"
HTML = ROOT / "analysis/html/p1_spike_eth3m_september_trade_audit_20260914.html"
ARMS = ("original", "next_bar", "ohlc", "olhc")


def cn_reason(value: object) -> str:
    return {
        "initial_stop": "初始止损", "initial_stop_gap": "初始止损缺口",
        "trailing_stop": "4ATR 跟踪止损", "trailing_stop_gap": "跟踪止损缺口",
        "cost_be": "成本保本", "cost_be_gap": "成本保本缺口",
        "take_profit": "止盈", "take_profit_gap": "止盈缺口",
        "opposite_v6_next_open": "V6 反向信号次开平仓",
        "boundary_mark": "截止收盘估值", "data_gap_censored": "数据缺口截断",
    }.get(str(value), str(value))


def n(value: object, digits: int = 4) -> str:
    try:
        return f"{float(value):,.{digits}f}"
    except (TypeError, ValueError):
        return "—"


def relative_result(name: str) -> str:
    return os.path.relpath(RESULTS / name, HTML.parent).replace(os.sep, "/")


def markdown(payload: dict) -> str:
    cfg, source = payload.get("config", {}), payload.get("source", {})
    trades = payload.get("trades", {})
    counts = {arm: len(trades.get(arm, [])) for arm in ARMS}
    closed = {
        arm: sum(not bool(row.get("censored")) for row in trades.get(arm, []))
        for arm in ARMS
    }
    censored = {arm: counts[arm] - closed[arm] for arm in ARMS}
    cutoff = source.get("cutoff", cfg.get("cutoff", "2026-09-14T13:19:00Z"))
    last = source.get("last_close", "2026-09-14T13:18:00Z")
    return f"""# ETHUSDT.P 3 分钟 SPIKE V8：2026-09-01 至 09-14 逐笔核对

这是所有固定规则交易与全部 V8 信号的**对账审计**，不是参数优化、收益宣称或实盘指令。默认交互视图为原始 V8 串行回放；可切换净目标 `next_bar`、`ohlc`、`olhc` 情景。`ohlc`/`olhc` 仅是明确假设的 bar 内路径，不是观察到的真实路径，也不是收益上下界。

- 时段：北京时间 2026-09-01 00:00 至 2026-09-14 21:19；最后完整 3 分钟 bar 在北京时间 21:18 收盘（UTC `{last}`）。
- 数据：OKX `ETH-USDT-SWAP` 已完成 3 分钟 candle；来源端点 `{source.get('endpoint', '见 source_receipt.json')}`，来源收据和尾部哈希保留在实验结果目录。
- 原始 V8 有 `{counts['original']}` 条串行记录（`{closed['original']}` 条自然平仓 + `{censored['original']}` 条截止估值）；`next_bar` / `ohlc` / `olhc` 分别有 `{counts['next_bar']}` / `{counts['ohlc']}` / `{counts['olhc']}` 条记录（自然平仓分别 `{closed['next_bar']}` / `{closed['ohlc']}` / `{closed['olhc']}`，截止估值分别 `{censored['next_bar']}` / `{censored['ohlc']}` / `{censored['olhc']}`）。原始 arm 的 `carry_in` 若存在，表示入场早于 9 月但仓位延续到审计窗；净目标 arms 从 9 月起始空仓，且不应用账户容量过滤。

## 怎样检查

下方 HTML 的交互面板默认显示原始 V8 全部交易。点击交易行或用前后箭头，可查看信号前 30 根至退出后最多 15 根的本地 OHLC 图。图中叠加入场、初始止损，以及适用时的成本保本与净目标价格。悬停 K 线可见北京时间和 OHLC。`截止仍持仓，仅收盘估值` 是 censor，不应与自然平仓混为一谈。

MFE 给出的是保守范围：止损退出 bar 的有利极值可能发生在止损之后，不能称为退出前已实现最高点。图表同样不把 `ohlc`/`olhc` 路径称作真实交易路径。

下载原始 CSV：

- [全部 V8 信号]({relative_result('all_v8_signals.csv')})
- [原始 V8 交易（中文列）]({relative_result('original_trades_zh.csv')})
- [原始 V8 交易]({relative_result('original_trades.csv')})
- [原始 V8 实际止损路径]({relative_result('original_stop_path.csv')})
- [next_bar 交易]({relative_result('next_bar_trades.csv')})
- [ohlc 交易]({relative_result('ohlc_trades.csv')})
- [olhc 交易]({relative_result('olhc_trades.csv')})
- [检查用 bars]({relative_result('inspection_bars.csv')})

## 可复现命令

```bash
# Owner-approved source acquisition and fixed-rule builder (只在收据授权后运行)
PYTHONPATH=. .venv/bin/python -m yoyo.evaluation.spike_september_trade_audit fetch
PYTHONPATH=. .venv/bin/python -m yoyo.evaluation.spike_september_trade_audit run

# 本报告：仅消费 results/audit_payload.json
PYTHONPATH=. .venv/bin/python scripts/report_spike_september_trade_audit.py
```

## 审计口径与零假设

原始 arm 保持冻结 V8 准入、5-bar / 0.2 ATR / 2 ATR floor 初始止损、2R 收盘 arm、4 ATR trail 与 20bp 往返成本。独立原始回放的 parity 是零变化对照：它只检验同一入场/退出实现的一致性，不是随机基准，也不构成新策略证据。

AUC、随机对照收益、置换检验和方向性收益宣称不适用于这份逐笔对账；本交付的等价严格检查是：每笔可追溯到信号、入场、退出、成本和 cutoff，且原始/情景 arms 的范围与是否串行明确披露。

## 风险与诚实声明

本审计不改变冻结规则，不产生交易指令。费用之外未模拟资金费、滑点、撮合队列或断线风险。OKX candle 的 OHLC 不能辨识真实 intrabar 次序；仅 `ohlc` / `olhc` 情景把该不确定性显式暴露。
"""


DASHBOARD_CSS = r"""
<style>
#audit-app{max-width:1180px;margin:2.5rem auto;padding:1.2rem;border:1px solid #c8d2df;border-radius:12px;background:#fff;color:#172033;font:14px/1.5 -apple-system,BlinkMacSystemFont,"PingFang SC","Microsoft YaHei",sans-serif}
#audit-app *{box-sizing:border-box} #audit-app h2{margin:.1rem 0 .35rem;border:0;font-size:1.45rem} #audit-app .note{color:#526173;margin:.3rem 0 1rem}
#audit-app .controls{display:flex;gap:.7rem;align-items:center;flex-wrap:wrap;margin:.8rem 0} #audit-app select,#audit-app button{font:inherit;padding:.35rem .55rem;border:1px solid #9aa9ba;border-radius:6px;background:#fff;color:#172033} #audit-app button:disabled{opacity:.45}
#audit-app .layout{display:grid;grid-template-columns:minmax(0,1fr) 310px;gap:1rem} #audit-app .chartbox{border:1px solid #d4dde8;border-radius:8px;padding:.5rem;overflow:hidden} #audit-app svg{display:block;width:100%;height:370px;background:#fbfcfe} #audit-app .tip{min-height:2.2em;padding:.35rem .5rem;background:#f1f5f9;border-radius:6px;font-family:ui-monospace,monospace}
#audit-app .detail{border:1px solid #d4dde8;border-radius:8px;padding:.7rem;background:#f8fafc;overflow-wrap:anywhere} #audit-app .detail dl{margin:.3rem 0} #audit-app .detail dt{font-weight:700;color:#415066;margin-top:.45rem} #audit-app .detail dd{margin:.08rem 0}
#audit-app .tablewrap{max-height:390px;overflow:auto;border:1px solid #d4dde8;border-radius:8px;margin-top:1rem} #audit-app table{width:max-content;min-width:100%;border-collapse:collapse;font-size:12px} #audit-app th,#audit-app td{padding:.34rem .48rem;border-bottom:1px solid #e1e7ef;white-space:nowrap;text-align:left} #audit-app th{position:sticky;top:0;background:#eef3f8;z-index:1} #audit-app tr[data-i]{cursor:pointer} #audit-app tr[data-i]:hover,#audit-app tr.selected{background:#e2efff}
#audit-app .signals{margin-top:1rem} #audit-app .signals summary{cursor:pointer;font-weight:700} #audit-app .signals .tablewrap{margin-top:.5rem;max-height:270px}.green{color:#087443}.red{color:#b42318}.amber{color:#9a6700}.muted{color:#68778a}@media(max-width:850px){#audit-app .layout{grid-template-columns:1fr}#audit-app svg{height:310px}}
@media(prefers-color-scheme:dark){#audit-app{background:#121820;color:#e7edf5;border-color:#3a4756}#audit-app .detail,#audit-app svg{background:#18212c;color:#e7edf5}#audit-app .tip,#audit-app th{background:#202c39;color:#e7edf5}#audit-app .chartbox,#audit-app .detail,#audit-app .tablewrap{border-color:#3a4756}#audit-app td{border-color:#2c3948}#audit-app select,#audit-app button{background:#202c39;color:#e7edf5;border-color:#5b6c7d}}
</style>"""


DASHBOARD_JS = r"""
<script>
(function(){
const data=JSON.parse(document.getElementById('audit-payload').textContent),arms=['original','next_bar','ohlc','olhc'];
const armLabels={original:'original｜原始V8',next_bar:'next_bar｜下根生效',ohlc:'ohlc｜先高后低',olhc:'olhc｜先低后高'};
const esc=s=>String(s??'—').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const finite=x=>x!==null&&x!==undefined&&x!==''&&Number.isFinite(Number(x));
const num=(x,d=4)=>finite(x)?Number(x).toLocaleString('zh-CN',{minimumFractionDigits:d,maximumFractionDigits:d}):'—';
const reason={initial_stop:'初始止损',initial_stop_gap:'初始止损缺口',trailing_stop:'4ATR 跟踪止损',trailing_stop_gap:'跟踪止损缺口',cost_be:'成本保本',cost_be_gap:'成本保本缺口',take_profit:'止盈',take_profit_gap:'止盈缺口',opposite_v6_next_open:'V6反向次开平仓',boundary_mark:'截止收盘估值',data_gap_censored:'数据缺口截断'};
const app=document.getElementById('audit-app'); let arm='original',pos=0;
app.innerHTML=`<h2>逐笔检查器</h2><p class="note">OKX ETH-USDT-SWAP，北京时间 9月1日–9月14日21:19；默认原始 V8 的 20 笔为 19 笔自然平仓 + 1 笔截止持仓估值。OHLC/OLHC 是假设路径；悬停图表查看本地 candle。</p><div class="controls"><label>Arm <select id="arm">${arms.map(a=>`<option value="${a}">${armLabels[a]}</option>`).join('')}</select></label><button id="prev">← 上一笔</button><button id="next">下一笔 →</button><span id="counter" class="muted"></span></div><div class="layout"><div class="chartbox"><svg id="chart" viewBox="0 0 860 370" role="img" aria-label="OHLC trade chart"></svg><div id="tip" class="tip">选择一笔交易查看。</div></div><aside id="detail" class="detail"></aside></div><div class="tablewrap"><table><thead><tr><th>#</th><th>信号确认（北京时间）</th><th>入场</th><th>方向</th><th>入场价</th><th>初始SL</th><th>初始风险</th><th>退出时间（北京时间）</th><th>退出价</th><th>毛收益率</th><th>毛R</th><th>净R</th><th>成本R</th><th>退出原因</th><th>MFE范围（R）</th><th>状态</th></tr></thead><tbody id="rows"></tbody></table></div><details class="signals"><summary>全部 V8 信号与各 arm 准入/串行状态</summary><div class="tablewrap"><table><thead><tr><th>#</th><th>信号确认</th><th>方向</th><th>收盘</th>${arms.map(a=>`<th>${armLabels[a]}</th>`).join('')}</tr></thead><tbody id="signals"></tbody></table></div></details>`;
const $=id=>document.getElementById(id), tradeRows=()=>data.trades?.[arm]||[];
function status(t){return t.censored?'截止仍持仓，仅收盘估值':'已平仓'}
function renderRows(){let rows=tradeRows(); if(pos>=rows.length)pos=Math.max(0,rows.length-1); $('counter').textContent=`${rows.length?pos+1:0} / ${rows.length} 笔`; $('prev').disabled=!pos;$('next').disabled=pos>=rows.length-1;
 $('rows').innerHTML=rows.map((t,i)=>`<tr data-i="${i}" class="${i===pos?'selected':''}"><td>${t.trade_no??i+1}</td><td>${esc(t.signal_confirm_bj||t.signal_id)}</td><td>${esc(t.entry_bj)}</td><td>${esc(t.direction)}</td><td>${num(t.entry_price,2)}</td><td>${num(t.initial_stop,2)}</td><td>${num(t.initial_risk,2)}</td><td>${esc(t.exit_bj_from||'—')} ～ ${esc(t.exit_bj_to||'—')}</td><td>${num(t.exit_price,2)}</td><td>${finite(t.gross_return)?num(Number(t.gross_return)*100,3)+'%':'—'}</td><td>${num(t.gross_r)}</td><td class="${finite(t.net_r)&&Number(t.net_r)>=0?'green':'red'}">${num(t.net_r)}</td><td>${num(t.cost_r)}</td><td>${esc(reason[t.exit_reason]||t.exit_reason)}</td><td>${num(t.favorable_r_lower)} ~ ${num(t.favorable_r_upper)}</td><td class="${t.censored?'amber':''}">${status(t)}</td></tr>`).join('');
 [...document.querySelectorAll('#rows tr')].forEach(x=>x.onclick=()=>{pos=+x.dataset.i;renderRows();renderChart()});renderDetail();renderChart()}
function renderDetail(){let t=tradeRows()[pos]; if(!t){$('detail').innerHTML='没有该 arm 的交易。';return} let stopNote=arm==='original'?'实际有效止损请看图中的红色阶梯线；成本BE参考价不代表已武装。':'此 arm 无原始止损阶梯线；成本BE参考价不代表已武装。'; $('detail').innerHTML=`<dl><dt>交易 #${t.trade_no??pos+1} · ${esc(status(t))}</dt><dd>${esc(reason[t.exit_reason]||t.exit_reason)}</dd><dt>时间</dt><dd>信号确认：${esc(t.signal_confirm_bj||t.signal_id)}<br>入场：${esc(t.entry_bj)}<br>退出区间：${esc(t.exit_bj_from||'—')} ～ ${esc(t.exit_bj_to||'—')}</dd><dt>价格层</dt><dd>入场 ${num(t.entry_price,2)}<br>退出 ${num(t.exit_price,2)}<br>初始SL参考 ${num(t.initial_stop,2)}<br>成本BE参考 ${num(t.cost_protection,2)}<br>净目标 ${num(t.target_price,2)}<br><span class="muted">${stopNote}</span></dd><dt>保本触发</dt><dd>触发 bar：${esc(t.be_trigger_bar_bj||'—')}<br>生效时序：${esc(t.activation_timing||'—')}</dd><dt>R 与成本</dt><dd>原始/毛R ${num(t.gross_r)}<br>净R ${num(t.net_r)}；成本R ${num(t.cost_r)}<br>MFE下界/上界 ${num(t.favorable_r_lower)} / ${num(t.favorable_r_upper)}<br><span class="muted">止损退出 bar 的有利极值可能发生在止损之后；上界不是已实现 MFE。</span></dd><dt>语义</dt><dd>${arm==='original'?'原始串行 V8；可能含 carry-in。':'9月起始空仓；不应用账户容量过滤。'}${t.carry_in?'<br><b>carry-in：</b>入场早于9月窗口。':''}</dd></dl>`}
function renderChart(){
 let t=tradeRows()[pos],svg=$('chart');if(!t){svg.innerHTML='';return}
 let lo=Number(t.signal_i)-30,hi=Number(t.exit_i)+15,bars=(data.bars||[]).filter(b=>Number(b.bar_i)>=lo&&Number(b.bar_i)<=hi);
 if(!bars.length){svg.innerHTML='<text x="20" y="35">可用审计 bars 不覆盖这笔交易。</text>';return}
 let path=(t.stop_path||[]).filter(p=>Number(p.bar_i)>=lo&&Number(p.bar_i)<=hi&&finite(p.active_stop));
 let levels=bars.flatMap(b=>[b.low,b.high]).filter(finite).map(Number);
 ['entry_price','target_price','initial_stop','cost_protection'].forEach(key=>{if(finite(t[key]))levels.push(Number(t[key]))});
 path.forEach(p=>levels.push(Number(p.active_stop)));
 let min=Math.min(...levels),max=Math.max(...levels),pad=(max-min)*.08||1;min-=pad;max+=pad;
 let W=860,H=370,L=52,R=14,T=16,B=35,x=i=>L+i*(W-L-R)/Math.max(1,bars.length-1),y=p=>T+(max-p)*(H-T-B)/(max-min),step=(W-L-R)/Math.max(1,bars.length-1),body=Math.max(1,step*.58),out=[];
 for(let k=0;k<5;k++){let p=min+(max-min)*k/4,yy=y(p);out.push(`<line x1="${L}" x2="${W-R}" y1="${yy}" y2="${yy}" stroke="#dbe4ee"/><text x="2" y="${yy+4}" font-size="10" fill="#667085">${p.toFixed(2)}</text>`)}
 bars.forEach((b,i)=>{let xx=x(i),up=Number(b.close)>=Number(b.open),col=up?'#159a6a':'#d94c4c',top=y(Math.max(Number(b.open),Number(b.close))),h=Math.max(1,Math.abs(y(Number(b.open))-y(Number(b.close))));out.push(`<g class="bar" data-k="${i}"><line x1="${xx}" x2="${xx}" y1="${y(Number(b.high))}" y2="${y(Number(b.low))}" stroke="${col}"/><rect x="${xx-body/2}" y="${top}" width="${body}" height="${h}" fill="${col}"/></g>`)});
 [['entry_price','入场','#2463eb'],['target_price','TP','#087443']].forEach(([key,label,col])=>{if(finite(t[key])){let yy=y(Number(t[key]));out.push(`<line x1="${L}" x2="${W-R}" y1="${yy}" y2="${yy}" stroke="${col}" stroke-dasharray="5 4"/><text x="${W-R-4}" y="${yy-3}" text-anchor="end" font-size="10" fill="${col}">${label} ${Number(t[key]).toFixed(2)}</text>`)}});
 let refs=[['initial_stop','初始SL','#c12f2f'],['cost_protection','成本BE参考','#a16207']];refs.forEach(([key,label,col],j)=>{if(finite(t[key]))out.push(`<text x="${L+4}" y="${T+13+j*13}" font-size="10" fill="${col}">${label} ${Number(t[key]).toFixed(2)}</text>`)});
 if(path.length){let points=path.map(p=>({i:bars.findIndex(b=>Number(b.bar_i)===Number(p.bar_i)),stop:Number(p.active_stop)})).filter(p=>p.i>=0);if(points.length){let d=`M${x(points[0].i)},${y(points[0].stop)}`;points.slice(1).forEach(p=>{d+=` H${x(p.i)} V${y(p.stop)}`});out.push(`<path d="${d}" fill="none" stroke="#c12f2f" stroke-width="1.8"/><text x="${W-R-4}" y="${T+13}" text-anchor="end" font-size="10" fill="#c12f2f">实际有效止损路径</text>`)}}
 let mark=(bar,label,col)=>{let i=bars.findIndex(b=>Number(b.bar_i)===Number(bar));if(i>=0)out.push(`<text x="${x(i)}" y="${T+38}" text-anchor="middle" font-size="11" fill="${col}">${label}</text>`)};mark(t.entry_i,'入','#2463eb');mark(t.exit_i,t.censored?'估':'出','#7c3aed');
 out.push(`<text x="${L}" y="${H-9}" font-size="10" fill="#667085">${esc(bars[0].open_bj||'')}</text><text x="${W-R}" y="${H-9}" text-anchor="end" font-size="10" fill="#667085">${esc(bars[bars.length-1].open_bj||'')}</text>`);svg.innerHTML=out.join('');
 [...svg.querySelectorAll('.bar')].forEach(g=>g.onmousemove=()=>{let b=bars[Number(g.dataset.k)];$('tip').textContent=`${b.open_bj||''}  O ${num(b.open,2)}  H ${num(b.high,2)}  L ${num(b.low,2)}  C ${num(b.close,2)}  ATR ${num(b.atr,2)}`})
}
$('arm').onchange=e=>{arm=e.target.value;pos=0;renderRows()};$('prev').onclick=()=>{if(pos){pos--;renderRows()}};$('next').onclick=()=>{if(pos<tradeRows().length-1){pos++;renderRows()}};$('signals').innerHTML=(data.signals||[]).map(s=>`<tr><td>${s.signal_no??'—'}</td><td>${esc(s.signal_id)}</td><td>${esc(s.direction)}</td><td>${num(s.close,2)}</td>${arms.map(a=>`<td>${esc(s[a+'_status']||'—')}</td>`).join('')}</tr>`).join('');renderRows();
})();
</script>"""


def dashboard(payload: dict) -> str:
    payload_json = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    return "\n<section id=\"audit-app\"></section>\n" + DASHBOARD_CSS + (
        "\n<script id=\"audit-payload\" type=\"application/json\">" + payload_json + "</script>\n" + DASHBOARD_JS
    )


def render(payload: dict) -> None:
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(markdown(payload), encoding="utf-8")
    subprocess.run([sys.executable, str(ROOT / "scripts/md_to_html.py"), "--out-dir", "analysis/html", str(REPORT)],
                   cwd=ROOT, check=True)
    if not HTML.is_file():
        raise RuntimeError(f"markdown renderer did not create {HTML}")
    content = HTML.read_text(encoding="utf-8")
    fragment = dashboard(payload)
    body_marker = "<body>\n"
    if body_marker not in content:
        raise RuntimeError("markdown renderer emitted no HTML body marker")
    HTML.write_text(content.replace(body_marker, body_marker + fragment + "\n", 1), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--payload", type=Path, default=PAYLOAD)
    args = parser.parse_args()
    if not RECEIPT.is_file():
        raise SystemExit(f"refusing to render before source receipt exists: {RECEIPT}")
    if not args.payload.is_file():
        raise SystemExit(f"audit payload does not exist: {args.payload}")
    payload = json.loads(args.payload.read_text(encoding="utf-8"))
    if not isinstance(payload.get("trades"), dict) or not isinstance(payload.get("bars"), list):
        raise SystemExit("audit payload lacks trades/bars schema")
    render(payload)
    print(REPORT)
    print(HTML)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
