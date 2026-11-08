/* Embedded, read-only projection of the below_plus_case research ledger. */
(() => {
  'use strict';
  const BASE = '/static/strategy-booklet/';
  const NAMES = {medium:'普通 BTC', case:'急跌 BTC', ALT:'山寨篮子'};
  const SCOPES = ['2026_actual_funded','all_history_fee_only'];
  const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const finite = Number.isFinite;
  const number = (v, digits = 2) => finite(v) ? v.toLocaleString('en-US',{maximumFractionDigits:digits}) : '—';
  const pct = (v, signed = true) => finite(v) ? (signed && v > 0 ? '+' : '') + v.toFixed(2) + '%' : '—';
  const epochYear = row => row.entry_time.slice(0,4);
  const branch = row => row.asset === 'ALT' ? 'ALT' : row.source;
  const table = (heads, rows, linkPeriod=false) => '<div class="bb-scroll"><table><thead><tr>' + heads.map(h=>'<th scope="col">'+esc(h)+'</th>').join('') + '</tr></thead><tbody>' + rows.map(row=>'<tr>'+row.map((v,i)=>'<td>'+(linkPeriod && i===0?'<button class="bb-period-link" type="button" data-period="'+esc(v)+'" aria-label="查看 '+esc(v)+' 的历史开仓">'+esc(v)+'</button>':esc(v))+'</td>').join('')+'</tr>').join('') + '</tbody></table></div>';
  const panel = (title,body) => '<section class="bb-panel"><h3>'+esc(title)+'</h3>'+body+'</section>';
  const note = text => '<p class="bb-note">'+esc(text)+'</p>';
  function validateData(data) {
    if(data?.schema_version!=='breadth-ema20.v1'||!Array.isArray(data.events)||!data.rules||!data.summaries||!data.equity_curves)throw Error('回测账本版本不匹配');
    return data;
  }
  function rulesHTML(data) {
    const {btc_medium:m,btc_case:c,alt:a} = validateData(data).rules;
    const flow = rows => '<ol class="bb-flow">'+rows.map(r=>'<li>'+esc(r)+'</li>').join('')+'</ol>';
    return '<div class="bb-fragment"><div class="bb-sections">'+panel('全市场下轨怎么数',note('取纽约时间周一的最高价 H 和最低价 L。周一结束后冻结下轨 L − 0.618 × (H − L)。每个币每周第一次触及下轨才计数。用 5 分钟 K 线记录首触，按 UTC 对齐的窗口汇总，窗口收盘后才知道数量。研究账本包含通知冷却期间的触价。'))+
      '<div class="bb-rules">'+
      panel('普通 BTC',flow(['在 '+m.initial_window_min/60+' 小时窗口，至少 '+m.initial_touch_count_min+' 个币首次触及下轨。','再观察 '+m.post_window_wait_min+' 分钟，新增币为 '+m.new_coins_max+'，且至少 '+number(100*m.reclaim_fraction_min)+'% 的可观测成员收回下轨。','最近一根已收盘的 4 小时 BTC 收盘价 ≤ 20 周期 EMA 时，按观察结束时刚开盘的 5 分钟 K 线开盘价买 BTC。','持仓 '+m.max_hold_hours/24+' 天，到期开盘价退出。']))+
      panel('急跌 BTC',flow([c.initial_window_min+' 分钟触价币数占近 28 天活跃币数至少 '+number(c.market_share_28d_min_pct)+'%。','再观察 '+c.post_window_wait_min+' 分钟，新增币数不超过初始数的 '+number(c.new_coins_to_initial_max_pct)+'%，山寨成员观察期涨幅中位数 ≥ '+number(c.alt_move_min_pct)+'%。','按决策开盘价买 BTC，初始保护止损为 6%。按手续费和已结算资金费更新一万点目标价，触及时卖出一半。','余仓沿用 6% 止损，最晚持有 '+c.max_hold_hours/24+' 天。此分支直接使用急跌条件。']))+
      panel('山寨篮子',flow([a.initial_window_min+' 分钟内至少 '+a.initial_touch_count_min+' 个币触轨，占近 28 天活跃币数至少 '+number(a.market_share_28d_min_pct)+'%。','窗口收盘即可决策，按等权买入信号中的合格山寨。排除 BTC、ETH、SOL、USTC。','持仓 '+a.hold_hours+' 小时，各成员按到期开盘价卖出。两次篮子入场至少间隔 24 小时。']))+'</div>'+
      panel('仓位与成本',note('初始资金 1,000 USDT，BTC 和山寨各分 500；两边各自以可用资金的 100% 入场、独立复利，不跨侧调仓。两类 BTC 信号共用一份仓位；原有的六天预留期继续生效，EMA 筛掉的时段不会重新补单。')+note('活跃币数取截至窗口收盘前 28 天有任一方向首触记录的不同币数。市价单边手续费 0.05%，限价单边 0.02%。全历史统计扣手续费；2026 专项另计资金费。'))+'</div></div>';
  }
  function statsHTML(data, {scope='2026_actual_funded'}={}) {
    validateData(data);
    if(!SCOPES.includes(scope))throw Error('统计区间不受支持');
    const entries=[...data.events].sort((a,b)=>b.entry_time.localeCompare(a.entry_time)||a.asset.localeCompare(b.asset));
    const s=data.summaries[scope];
    const funded=scope==='2026_actual_funded';
    const sample=entries.filter(e=>!funded||epochYear(e)==='2026');
    const result=e=>funded?e.funded_pct:e.net_pct;
    const sizes=Object.entries(NAMES).map(([code,name])=>{const g=sample.filter(e=>branch(e)===code),v=g.map(result).filter(finite);return [name,g.length,v.length?pct(100*v.filter(p=>p>0).length/v.length,false):'—',v.length?pct(v.reduce((a,b)=>a+b,0)/v.length):'—'];});
    const years=data.summaries.calendar_year_fee_only_reset.map(y=>[y.year,y.event_count,y.btc_event_count,y.alt_event_count,pct(y.equal_sleeves_growth_pct)]);
    const months=new Map();
    for(const row of sample){
      const month=row.entry_time.slice(0,7);
      if(!months.has(month))months.set(month,{count:0,valid:0,wins:0,total:0});
      const group=months.get(month),net=result(row);group.count++;
      if(finite(net)){group.valid++;group.total+=net;if(net>0)group.wins++;}
    }
    const monthRows=[...months].map(([month,g])=>[month,g.count,g.wins,pct(g.valid?100*g.wins/g.valid:null,false),pct(g.valid?g.total/g.valid:null)]);
    const control=data.summaries.matched_control_2026;
    const base=data.summaries.baseline_original_half_then_6d[scope],stress=data.summaries.stress_round_trip_0_20pp[scope];
    return '<div class="bb-fragment" data-summary-scope="'+scope+'"><div class="bb-sections"><div class="bb-head"><span class="bb-meta">'+(funded?'2026 · 含资金费':'2020–2026 · 仅手续费')+'</span></div>'+
      panel('按入场类型',table(['类型','入场数','胜率','笔均净收益'],sizes)+note('篮子成员等权，一篮计一笔。笔均净收益与组合复利收益分别计算。'))+
      panel('加 EMA20 条件前后',table(['组合','入场数','组合净收益','最大回撤'],[['原组合',base.event_count,pct(base.growth_pct),pct(base.max_drawdown_pct,false)],['普通 BTC 加 4H EMA20',s.event_count,pct(s.growth_pct),pct(s.max_drawdown_pct,false)]])+note('在所选区间内，对新组合每笔额外扣 0.20 个百分点的往返成本，净收益为 '+pct(stress.growth_pct)+'，最大回撤为 '+pct(stress.max_drawdown_pct,false)+'。'))+
      (funded?'<details class="bb-panel bb-details"><summary>对照图 · 2026 年净值和日内最深回撤</summary><a href="'+BASE+'breadth-ema20-2026-equity.png" target="_blank" rel="noopener"><img loading="lazy" class="bb-comparison" src="'+BASE+'breadth-ema20-2026-equity.png" alt="原组合与加入 EMA20 条件的组合：每日收盘净值，以及每日最深的五分钟盯市回撤"></a></details>':'')+
      panel('逐年表现',table(['UTC 年','入场数','BTC','山寨篮子','年度收益'],years,true)+note('每年重新等额分配，统一只扣手续费。2026 年统计截至 10 月 27 日 UTC，已入场持仓继续跟踪至退出。'))+
      '<details class="bb-panel bb-details"><summary>查看所选区间的逐月入场统计</summary>'+table(['UTC 月','入场数','盈利笔数','胜率','笔均净收益'],monthRows,true)+'</details>'+
      (control?panel('2026 匹配随机入场对照',note(control.matched+' / '+control.total+' 次有可比入场，覆盖 '+control.matched_months+' 个月。两组笔均收益相差 '+number(control.edge_pp,3)+' 个百分点，按月检验 p = '+number(control.p_month,3)+'。急跌 BTC 的可匹配样本为 '+control.case_matched+'。')):'')+
      panel('核算口径',note((funded?'本区间计入实际资金费。':'全历史按手续费后、资金费前统计。')+'以收盘盯市计算最大回撤，按入场事件的净收益是否大于零计算胜率。2026 年行情曾参与研究和案例选择。'))+'</div></div>';
  }

  const api={validateData,rulesHTML,statsHTML};
  if(typeof module!=='undefined' && module.exports)module.exports=api;
  else window.SpikeBreadthBook=api;
})();
