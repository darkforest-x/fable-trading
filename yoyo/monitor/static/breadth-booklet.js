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
  const money = value => (value > 0 ? '+' : '') + value.toLocaleString('en-US', {minimumFractionDigits:2,maximumFractionDigits:2});
  const flow = rows => '<ol class="bb-flow">'+rows.map(r=>'<li>'+esc(r)+'</li>').join('')+'</ol>';
  function originHTML() {
    return '<div class="bb-fragment">'+panel('这套策略是怎么得来的',flow([
      '起点是一个观察。同根 5 分钟有 50–99 个币首次触及周一下轨，随后 30 分钟没有新币触轨，历史上共有 13 根。这组反弹让我们进一步检验，同时下跌的币数和随后跌势是否扩散，能否帮助选择买点。',
      '先在全市场首触记录上比较 5 分钟到 12 小时的聚合窗口、等待时间、触轨数量和收回比例，再看 BTC、ETH、SOL 和触轨山寨。六天最高价展示曾出现的浮盈，策略收益按能执行的入场、止损、止盈和到期价格计算。',
      '为增加机会，按 2023 年前、2023–2024 年和 2025 年依次筛选，在 2025 年检验至少 36 或 72 次入场的两档候选。现版沿用 36 次档的普通 BTC 和山寨篮子规则。不同资产共享一轮下跌时，山寨合成一笔等权交易。',
      '另外保留了针对急跌后反弹的 BTC 分支。9 月 16 日的已知行情参与了“一万点卖一半、余仓持有六天”的设计。两条 BTC 规则合用一份资金，同一时点急跌优先。',
      '最后比较普通 BTC 入场前最近已收盘的 4 小时或日线与 EMA20 / EMA50 的相对位置，选择 4 小时收盘价 ≤ EMA20。原组合与加条件后的收益、回撤和开单数都保存在统计分析中。2026 年的数据参与了这一步选择。'
    ]))+'</div>';
  }
  function caseExamples(data, scope) {
    const btc=data.events.find(e=>e.event_id==='BTC|2026-09-15T19:20:00Z');
    const alt=data.events.find(e=>e.event_id==='ALT|2026-10-23T22:55:00Z');
    const member=alt.members.find(m=>m.symbol==='1000LUNCUSDT');
    const funded=scope==='2026_actual_funded';
    const basis=funded?'含手续费和资金费':'仅扣手续费';
    const net=funded?'funded_pct':'net_pct';
    const replay=(event, label, symbol='')=>'<button type="button" class="bb-period-link" data-breadth-case="'+esc(event.event_id)+'" data-member="'+esc(symbol)+'">'+esc(label)+'</button>';
    const btcSignal=btc.signal_snapshot;
    const sep=data.summaries.september_case_review;
    return panel('两笔真实历史信号 · 北京时间',
      note('9 月 16 日 02:50，'+btcSignal.initial_touch_count+' 个币首触。随后 30 分钟新增 '+btcSignal.new_coins+' 个，初始山寨涨幅中位数 '+pct(btcSignal.alt_move_pct)+'。03:20 按 '+number(btc.entry_price)+' 买 BTC。六天内，一半仓位在 '+number(btc.first_exit_price)+' 成交，另一半到期在 '+number(btc.exit_price)+' 卖出。'+basis+'，整笔 '+pct(btc[net])+'，折合每 BTC '+money(btc.entry_price*btc[net]/100)+' 点。')+
      (sep?note('2026 含资金费口径剔除这笔后，组合收益为 '+pct(sep.without_case_growth_pct)+'。若每次市价成交另外计 10 bp 滑点，这笔的限价目标未触发，到期净得 '+number(sep.slipped_funded_points)+' 点。'):'')+
      replay(btc,'查看 BTC 成交 K 线')+
      note('10 月 24 日 06:55，同根首触 '+alt.signal_snapshot.initial_touch_count+' 币，占近 28 天活跃币的 '+pct(alt.signal_snapshot.market_share_28d_pct,false)+'。随即等权买入 '+alt.observed_members+' 个合格成员，12:55 到期。'+basis+'，篮子 '+pct(alt[net])+'。其中 1000LUNC 从 '+number(member.entry_price,5)+' 到 '+number(member.exit_price,5)+'，单币 '+pct(member[net])+'。')+
      replay(alt,'查看山寨篮子与 1000LUNC K 线',member.symbol));
  }
  function rulesHTML(data, {scope='all_history_fee_only'}={}) {
    const {btc_medium:m,btc_case:c,alt:a} = validateData(data).rules;
    return '<div class="bb-fragment"><div class="bb-sections">'+panel('全市场下轨怎么数',note('取纽约时间周一的最高价 H 和最低价 L。周一结束后冻结下轨 L − 0.618 × (H − L)。每个币每周第一次触及下轨才计数。用 5 分钟 K 线记录首触，按 UTC 对齐的窗口汇总，窗口收盘后才知道数量。研究账本包含通知冷却期间的触价。'))+
      '<div class="bb-rules">'+
      panel('普通 BTC',flow(['在 '+m.initial_window_min/60+' 小时窗口，至少 '+m.initial_touch_count_min+' 个币首次触及下轨。','再观察 '+m.post_window_wait_min+' 分钟，新增币为 '+m.new_coins_max+'，且至少 '+number(100*m.reclaim_fraction_min)+'% 的可观测成员收回下轨。','最近一根已收盘的 4 小时 BTC 收盘价 ≤ 20 周期 EMA 时，按观察结束时刚开盘的 5 分钟 K 线开盘价买 BTC。','持仓 '+m.max_hold_hours/24+' 天，到期开盘价退出。期间没有价格止损。']))+
      panel('急跌 BTC',flow([c.initial_window_min+' 分钟触价币数占近 28 天活跃币数至少 '+number(c.market_share_28d_min_pct)+'%。','再观察 '+c.post_window_wait_min+' 分钟，新增币数不超过初始数的 '+number(c.new_coins_to_initial_max_pct)+'%，山寨成员观察期涨幅中位数 ≥ '+number(c.alt_move_min_pct)+'%。','按决策开盘价买 BTC，初始保护止损为 6%。按手续费和已结算资金费更新一万点目标价，触及时卖出一半。','目标成交前，6% 止损保护整仓。卖出一半后，余仓沿用初始止损价，最晚持有 '+c.max_hold_hours/24+' 天。此分支直接使用急跌条件。']))+
      panel('山寨篮子',flow([a.initial_window_min+' 分钟内至少 '+a.initial_touch_count_min+' 个币触轨，占近 28 天活跃币数至少 '+number(a.market_share_28d_min_pct)+'%。','等待时间为 0，窗口收盘即可等权买入初始成员。账本中的“新增 0”指零分钟观察窗，这一分支不要求后续 30 分钟停止扩散。排除 BTC、ETH、SOL、USTC。','持仓 '+a.hold_hours+' 小时，各成员按到期开盘价卖出。两次篮子入场至少间隔 24 小时。']))+'</div>'+caseExamples(data,scope)+
      panel('仓位与成本',note('初始资金 1,000 USDT，BTC 和山寨各分 500。两边各自以可用资金的 100% 入场、独立复利，不跨侧调仓。两类 BTC 信号共用一份仓位，原有的六天预留期继续生效，EMA 筛掉的时段不会重新补单。')+note('活跃币数取截至窗口收盘前 28 天有任一方向首触记录的不同币数。市价单边手续费 0.05%，限价单边 0.02%。全历史统计扣手续费。2026 专项另计资金费。'))+'</div></div>';
  }
  function spacingHTML(review, funded) {
    const period=funded?'2026_to_2026-10-27':'all_history';
    const rows=review.spacing.filter(r=>r.period===period);
    const tableRows=rows.map(r=>[r.minimum_gap_hours+' 小时',r.alt_legs,r.total_legs,pct(r.combo_50_50_fee_only_growth_pct),pct(r.combo_50_50_settlement_drawdown_pct,false),r.new_vs_24h+' / '+r.replaced_24h,pct(r.new_mean_net_pct),pct(r.new_win_pct,false)]);
    const extra=review.extra_baskets_2026||[];
    const extras=extra.map(r=>[r.entry_bjt,r.member_count,pct(r.net_pct),[r.selected_12h?'12 小时':'',r.selected_6h?'6 小时':''].filter(Boolean).join(' / ')]);
    const markedRows=review.marks_2026.map(r=>[r.minimum_gap_hours+' 小时',pct(r.fee_marked_dd_pct,false),r.funded_events+' / '+r.events,pct(r.funded_growth_pct),pct(r.funded_marked_dd_pct,false)]);
    return note((funded?'2026 年至 10 月 27 日':'2020 年至 2026 年 10 月 27 日')+'。共有 '+rows[0].raw_rule_signals+' 个山寨信号满足价格和币数条件。只改两次入场之间的最短间隔，持仓仍为六小时。BTC 的入场、退出和 50% 起始配置沿用现版。三行都从 1,000 USDT 起步，仅扣手续费。')+
      table(['最短间隔','山寨笔数','组合笔数','组合净收益','结算回撤','新入选 / 被替换','新增笔均收益','新增胜率'],tableRows)+
      note('新入选和被替换均与 24 小时原版逐笔比较。收益按每次实际占用资金复利。结算回撤只看平仓时点，持仓期间可能承受更大回撤。新增事件缺少匹配随机对照。')+
      note(funded?'这一年，缩到 12 小时多出 3 笔。再缩到 6 小时，入选的仍是同样 3 笔。三笔中仅一笔赚钱。':'全历史缩到 6 小时，实际新增 64 笔，同时挤掉原版 3 笔。新增单的胜率为 40.63%，更多的成交降低了组合收益。')+
      (funded&&extras.length?'<details class="bb-panel bb-details"><summary>查看 2026 年新入选的山寨篮子</summary>'+table(['开仓（北京时间）','成员数','六小时手续费后收益','入选间隔'],extras)+'</details>':'')+
      (funded?table(['最短间隔','仅手续费盯市回撤','资金费完整篮子','含资金费组合收益','含资金费盯市回撤'],markedRows)+note('盯市按两边资金的同期 5 分钟收盘净值计算。含资金费结果仅列出每笔均有覆盖的组合。'):'');
  }

  function frequencyHTML(data, funded, entries) {
    const dates=[...new Set(entries.map(e=>Date.parse(e.entry_time)))].sort((a,b)=>a-b);
    const gaps=dates.slice(1).map((end,i)=>(end-dates[i])/86400000).sort((a,b)=>a-b);
    const mid=Math.floor(gaps.length/2);
    const medianGap=gaps.length%2?gaps[mid]:(gaps[mid-1]+gaps[mid])/2;
    const review=data.summaries.frequency_review;
    const stats=data.summaries;
    const scope=funded?'2026_actual_funded':'all_history_fee_only';
    const original=stats.baseline_original_half_then_6d[scope];
    const current=stats[scope];
    const timing=note('同一时间的多币篮子计一次入场。所选区间共有 '+dates.length+' 个不同的开仓时点，相邻两次通常相隔 '+number(medianGap,1)+' 天（中位数），最长相隔 '+number(gaps.at(-1))+' 天。');
    const admission=note('普通 BTC 与急跌 BTC 共用一笔资金，按时间先后接单，同一时点急跌优先。原版先预留六天，再检查普通 BTC 的 EMA 条件。原组合有 '+original.event_count+' 笔，加 EMA 后剩 '+current.event_count+' 笔。山寨资金独立，每篮持有六小时，原版连续两篮最少相隔一天。');
    if(!review)return panel('为什么开单少',timing+admission);
    const frequent=review.higher_frequency_2026.map(r=>[r.candidate_id==='account_36'?'36 次档':'72 次档',r.n,pct(r.funded_mean),pct(r.marked_close_dd_before_funding,false)]);
    return panel('为什么开单少',timing+admission)+
      panel('缩短山寨入场间隔',spacingHTML(review,funded))+
      '<details class="bb-panel bb-details"><summary>曾试过的高频组合 · 2026 年至 10 月 27 日</summary>'+
      note('将 BTC、ETH、SOL 和山寨篮子四路提醒合到一个账户，空仓时接最早一笔。下表中的 36 和 72 是选规则时设定的年度笔数门槛。')+
      table(['候选','入场数','含资金费笔均收益','5 分钟盯市回撤'],frequent)+
      note('表中回撤扣双边手续费、未计资金费。加上资金费，两档的笔均收益都是负数。两个账户与匹配随机入场的差异均未达到显著水平。')+'</details>';
  }

  function statsHTML(data, {scope='2026_actual_funded',history}={}) {
    validateData(data);
    if(!SCOPES.includes(scope))throw Error('统计区间不受支持');
    const entries=[...data.events].sort((a,b)=>b.entry_time.localeCompare(a.entry_time)||a.asset.localeCompare(b.asset));
    const s=data.summaries[scope];
    const funded=scope==='2026_actual_funded';
    const sample=entries.filter(e=>!funded||epochYear(e)==='2026');
    const result=e=>funded?e.funded_pct:e.net_pct;
    if(!history){
      const adapter=typeof module==='object'?require('./breadth-book-history.js'):window.SpikeBreadthHistory;
      history=adapter.createBreadthBookHistory(data,{scope});
    }
    const settled=Object.values(history.dayRows).flat();
    const sizes=Object.entries(NAMES).map(([code,name])=>{
      const rows=settled.filter(e=>branch(e)===code);
      const wins=rows.filter(e=>e.return_pct>0).length;
      const mean=rows.reduce((sum,e)=>sum+e.return_pct,0)/rows.length;
      const pnl=rows.reduce((sum,e)=>sum+e.pnl,0);
      return [name,rows.length,wins,pct(100*wins/rows.length,false),pct(mean),money(pnl)];
    });
    const caseTrades=sample.filter(e=>branch(e)==='case');
    const worstOrdinary=Math.min(...sample.filter(e=>branch(e)==='medium').map(result));
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
      panel('按入场类型',table(['类型','入场数','盈利笔数','胜率','笔均净收益','组合盈亏（USDT）'],sizes)+note('每个区间从 1,000 USDT 起步。组合盈亏按每笔当时实际分配的资金计算，三行相加就是组合总盈亏。每个等权篮子计一笔。')+note('本区间普通 BTC 最差一笔为 '+pct(worstOrdinary)+'。急跌 BTC 共 '+caseTrades.length+' 笔，其中 '+caseTrades.filter(e=>e.exit_legs>1).length+' 笔实际触发了分批止盈。'))+
      panel('加 EMA20 条件前后',table(['组合','入场数','组合净收益','最大回撤'],[['原组合',base.event_count,pct(base.growth_pct),pct(base.max_drawdown_pct,false)],['普通 BTC 加 4H EMA20',s.event_count,pct(s.growth_pct),pct(s.max_drawdown_pct,false)]])+note('在所选区间内，对新组合每笔额外扣 0.20 个百分点的往返成本，净收益为 '+pct(stress.growth_pct)+'，最大回撤为 '+pct(stress.max_drawdown_pct,false)+'。'))+
      (funded?'<details class="bb-panel bb-details"><summary>对照图 · 2026 年净值和日内最深回撤</summary><a href="'+BASE+'breadth-ema20-2026-equity.png" target="_blank" rel="noopener"><img loading="lazy" class="bb-comparison" src="'+BASE+'breadth-ema20-2026-equity.png" alt="原组合与加入 EMA20 条件的组合：每日收盘净值，以及每日最深的五分钟盯市回撤"></a></details>':'')+
      frequencyHTML(data,funded,sample)+
      panel('逐年表现',table(['UTC 年','入场数','BTC','山寨篮子','年度收益'],years,true)+note('每年重新等额分配，统一只扣手续费。2026 年统计截至 10 月 27 日 UTC，已入场持仓继续跟踪至退出。'))+
      '<details class="bb-panel bb-details"><summary>查看所选区间的逐月入场统计</summary>'+table(['UTC 月','入场数','盈利笔数','胜率','笔均净收益'],monthRows,true)+'</details>'+
      (control?panel('2026 匹配随机入场对照',note(control.matched+' / '+control.total+' 次有可比入场，覆盖 '+control.matched_months+' 个月。两组笔均收益相差 '+number(control.edge_pp,3)+' 个百分点，按月检验 p = '+number(control.p_month,3)+'。急跌 BTC 的可匹配样本为 '+control.case_matched+'。目前不能据此确认信号比随机入场有稳定优势。')):'')+
      panel('核算口径',note((funded?'本区间计入实际资金费。':'全历史按手续费后、资金费前统计。')+'以收盘盯市计算最大回撤，按入场事件的净收益是否大于零计算胜率。2026 年行情曾参与研究和案例选择。'))+'</div></div>';
  }

  const api={validateData,rulesHTML,statsHTML,originHTML};
  if(typeof module!=='undefined' && module.exports)module.exports=api;
  else window.SpikeBreadthBook=api;
})();
