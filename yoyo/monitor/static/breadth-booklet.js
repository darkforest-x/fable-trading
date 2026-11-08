/* Read-only projection of below_plus_case. Stitch screen 4d91a88e8b4149c2bcf61b09fc342b58. */
(() => {
  'use strict';
  const BASE = '/static/strategy-booklet/';
  const DIRECT = BASE + 'breadth-ema20.html';
  const CASE = 'BTC|2026-09-15T19:20:00Z';
  const NAMES = {medium:'普通 BTC', case:'急跌 BTC', ALT:'山寨篮子'};
  const PAGES = {rules:'策略规则', history:'历史开仓', stats:'统计分析'};
  const LIMIT = 8;
  const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const finite = Number.isFinite;
  const number = (v, digits = 2) => finite(v) ? v.toLocaleString('en-US',{maximumFractionDigits:digits}) : '—';
  const pct = (v, signed = true) => finite(v) ? (signed && v > 0 ? '+' : '') + v.toFixed(2) + '%' : '—';
  const tone = v => v > 0 ? 'bb-up' : v < 0 ? 'bb-down' : '';
  const price = v => number(v, v >= 100 ? 2 : v >= 1 ? 5 : 10);
  const bjt = time => new Date(Date.parse(time) + 8*3600000).toISOString().slice(0,16).replace('T',' ');
  const epochYear = row => row.entry_time.slice(0,4);
  const branch = row => row.asset === 'ALT' ? 'ALT' : row.source;
  const held = row => number((Date.parse(row.exit_time)-Date.parse(row.entry_time))/3600000,1) + 'h';
  const table = (heads, rows, linkPeriod=false) => '<div class="bb-scroll"><table><thead><tr>' + heads.map(h=>'<th scope="col">'+esc(h)+'</th>').join('') + '</tr></thead><tbody>' + rows.map(row=>'<tr>'+row.map((v,i)=>'<td>'+(linkPeriod && i===0?'<button class="bb-period-link" data-period="'+esc(v)+'" aria-label="查看 '+esc(v)+' 的历史开仓">'+esc(v)+'</button>':esc(v))+'</td>').join('')+'</tr>').join('') + '</tbody></table></div>';
  const tile = (label,value) => '<div class="bb-tile"><small>'+esc(label)+'</small><b>'+esc(value)+'</b></div>';
  const panel = (title,body) => '<section class="bb-panel"><h3>'+esc(title)+'</h3>'+body+'</section>';
  const note = text => '<p class="bb-note">'+esc(text)+'</p>';
  const options = (choices, active) => choices.map(([v,t])=>'<option value="'+esc(v)+'"'+(String(active)===String(v)?' selected':'')+'>'+esc(t)+'</option>').join('');
  async function readJSON(url, signal) {
    const r = await fetch(url,{signal,credentials:'same-origin'});
    if (!r.ok) throw Error('读取失败（HTTP '+r.status+'）');
    if (!url.endsWith('.gz')) return r.json();
    const bytes = await r.arrayBuffer();
    const head = new Uint8Array(bytes,0,Math.min(2,bytes.byteLength));
    if (head[0] !== 31 || head[1] !== 139) return JSON.parse(new TextDecoder().decode(bytes));
    return new Response(new Blob([bytes]).stream().pipeThrough(new DecompressionStream('gzip'))).json();
  }
  function rulesHTML(data) {
    const {btc_medium:m,btc_case:c,alt:a} = data.rules;
    const flow = rows => '<ol class="bb-flow">'+rows.map(r=>'<li>'+esc(r)+'</li>').join('')+'</ol>';
    return '<div class="bb-sections">'+panel('全市场下轨怎么数',note('取纽约时间周一的最高价 H 和最低价 L。周一结束后冻结下轨 L − 0.618 × (H − L)。每个币每周第一次触及下轨才计数。用 5 分钟 K 线记录首触，按 UTC 对齐的窗口汇总，窗口收盘后才知道数量。研究账本包含通知冷却期间的触价。'))+
      '<div class="bb-rules">'+
      panel('普通 BTC',flow(['在 '+m.initial_window_min/60+' 小时窗口，至少 '+m.initial_touch_count_min+' 个币首次触及下轨。','再观察 '+m.post_window_wait_min+' 分钟，新增币为 '+m.new_coins_max+'，且至少 '+number(100*m.reclaim_fraction_min)+'% 的可观测成员收回下轨。','最近一根已收盘的 4 小时 BTC 收盘价 ≤ 20 周期 EMA 时，按观察结束时刚开盘的 5 分钟 K 线开盘价买 BTC。','持仓 '+m.max_hold_hours/24+' 天，到期开盘价退出。']))+
      panel('急跌 BTC',flow([c.initial_window_min+' 分钟触价币数占近 28 天活跃币数至少 '+number(c.market_share_28d_min_pct)+'%。','再观察 '+c.post_window_wait_min+' 分钟，新增币数不超过初始数的 '+number(c.new_coins_to_initial_max_pct)+'%，山寨成员观察期涨幅中位数 ≥ '+number(c.alt_move_min_pct)+'%。','按决策开盘价买 BTC，初始保护止损为 6%。按手续费和已结算资金费更新一万点目标价，触及时卖出一半。','余仓沿用 6% 止损，最晚持有 '+c.max_hold_hours/24+' 天。此分支直接使用急跌条件。']))+
      panel('山寨篮子',flow([a.initial_window_min+' 分钟内至少 '+a.initial_touch_count_min+' 个币触轨，占近 28 天活跃币数至少 '+number(a.market_share_28d_min_pct)+'%。','窗口收盘即可决策，按等权买入信号中的合格山寨。排除 BTC、ETH、SOL、USTC。','持仓 '+a.hold_hours+' 小时，各成员按到期开盘价卖出。两次篮子入场至少间隔 24 小时。']))+'</div>'+
      panel('仓位与成本',note('初始资金 1,000 USDT，BTC 和山寨各分 500；两边各自以可用资金的 100% 入场、独立复利，不跨侧调仓。两类 BTC 信号共用一份仓位；原有的六天预留期继续生效，EMA 筛掉的时段不会重新补单。')+note('活跃币数取截至窗口收盘前 28 天有任一方向首触记录的不同币数。市价单边手续费 0.05%，限价单边 0.02%。全历史统计扣手续费；2026 专项另计资金费。'))+'</div>';
  }
  let current = null;
  function open({origin=null, standalone=false, tab='history', eventId=null}={}) {
    if (current) return;
    let data, entries=[], chosen=null, chosenSymbol='', section=Object.hasOwn(PAGES,tab)?tab:'history', year='all', kind='all', page=0, scope='2026_actual_funded';
    let plot=null, chartRequest=null, catalog=null, block=null, busyId=0;
    const lifetime=new AbortController();
    const root = standalone ? document.getElementById('breadth-standalone') : document.createElement('dialog');
    root.className='bb-sheet';
    root.setAttribute('aria-labelledby','bb-title');
    root.innerHTML='<header class="bb-top"><div><div class="bb-kicker">SPIKE · 策略小册</div><h1 id="bb-title">全市场下轨反弹 · 4H EMA20</h1></div><button type="button" class="bb-close" data-action="close" aria-label="关闭策略小册">×</button></header><nav class="bb-tabs" aria-label="小册章节">'+Object.entries(PAGES).map(([v,t])=>'<button type="button" data-page="'+v+'">'+t+'</button>').join('')+'</nav><div class="bb-body" aria-live="off"></div>';
    const body=root.querySelector('.bb-body');
    if (!standalone) {document.body.append(root);root.showModal();root.querySelector('.bb-close').focus();}
    current=root;
    const watcher=new ResizeObserver(()=>plot?.resize());
    const themeWatcher=new MutationObserver(()=>{if(plot)renderPlotAgain();});
    themeWatcher.observe(document.documentElement,{attributes:true,attributeFilter:['data-theme']});
    function stopPlot() {watcher.disconnect();plot?.dispose();plot=null;}
    function clearDetails() {busyId++;chartRequest?.abort();chartRequest=null;stopPlot();}
    function close() {
      clearDetails();lifetime.abort();themeWatcher.disconnect();current=null;
      if (standalone) {location.assign('/#signals?pane=reclaim');return;}
      root.close();root.remove();
      if (origin?.getClientRects().length) origin.focus({preventScroll:true});
      else (document.querySelector('.sa-trigger') && origin?.closest('#spike-alarm-dock') ? document.querySelector('.sa-trigger') : document.querySelector('[aria-label="选择策略"]'))?.focus({preventScroll:true});
    }
    const nav=()=>root.querySelectorAll('[data-page]').forEach(b=>b.setAttribute('aria-current',b.dataset.page===section?'page':'false'));
    function filtered() {return entries.filter(e=>(year==='all'||e.entry_time.startsWith(year))&&(kind==='all'||branch(e)===kind));}
    function setUrl() {
      if(!standalone)return;
      const u=new URL(location.href);u.searchParams.set('tab',section);
      if(section==='history' && chosen)u.searchParams.set('event',chosen.event_id);else u.searchParams.delete('event');
      history.replaceState(history.state,'',u);
    }
    const actual = row => epochYear(row)==='2026'?row.funded_pct:row.net_pct;
    function historyHTML() {
      const rows=filtered();
      if(!rows.includes(chosen)){chosen=rows[0]||null;chosenSymbol='';block=null;}
      const i=rows.indexOf(chosen);
      if(i>=0 && (i<page*LIMIT||i>=(page+1)*LIMIT))page=Math.floor(i/LIMIT);
      const pages=Math.max(1,Math.ceil(rows.length/LIMIT));
      const shown=rows.slice(page*LIMIT,(page+1)*LIMIT);
      const years=[...new Set(entries.map(epochYear))];
      const periods=[['all','全部年份'],...years.map(y=>[y,y])];
      if(year.length===7)periods.push([year,year]);
      const list='<div class="bb-filter"><label>区间（UTC）<select data-field="year">'+options(periods,year)+'</select></label><label>入场类型<select data-field="kind">'+options([['all','全部类型'],...Object.entries(NAMES)],kind)+'</select></label><button type="button" class="bb-case" data-action="case">9/16 BTC 案例</button></div>'+
        '<table class="bb-trades"><thead><tr><th>开仓 · 北京时间</th><th>类型</th><th>净收益</th></tr></thead><tbody>'+shown.map(e=>{const when=bjt(e.entry_time),v=actual(e);return '<tr data-id="'+esc(e.event_id)+'" aria-current="'+(e===chosen)+'"><td><button class="bb-open-event" type="button" aria-label="查看 '+esc(when+' '+NAMES[branch(e)])+' 开仓图">'+when.slice(0,10)+'<small>'+when.slice(11)+'</small></button></td><td>'+NAMES[branch(e)]+'<small>'+held(e)+'</small></td><td class="'+tone(v)+'">'+pct(v)+'</td></tr>';}).join('')+'</tbody></table>'+
        (!shown.length?'<p role="status">此条件没有历史入场。</p>':'')+'<div class="bb-pager"><button data-action="prev" '+(!page?'disabled':'')+'>上一页</button><span>'+rows.length+' 笔 · '+(page+1)+' / '+pages+'</span><button data-action="next" '+(page>=pages-1?'disabled':'')+'>下一页</button></div>';
      return '<div class="bb-ledger"><section class="bb-panel"><h2>历史开仓</h2>'+list+note('每行是一笔 BTC 或一次篮子入场。2026 年含资金费，其余年份仅扣手续费。')+'</section><section class="bb-panel bb-detail" aria-label="历史开仓图"><div data-detail></div></section></div>';
    }
    function render() {
      clearDetails();nav();if(!data)return;
      body.innerHTML=section==='rules'?rulesHTML(data):section==='stats'?statsHTML():historyHTML();
      if(section==='history')detail();
      if(section==='stats')equity();
      setUrl();
    }
    function statsHTML() {
      const s=data.summaries[scope], funded=scope==='2026_actual_funded';
      const sample=entries.filter(e=>!funded||epochYear(e)==='2026');
      const result=e=>funded?e.funded_pct:e.net_pct;
      const wins=sample.filter(e=>result(e)>0).length,valid=sample.filter(e=>finite(result(e))).length;
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
      return '<div class="bb-sections"><div class="bb-head"><h2>统计分析</h2><label>区间 <select data-field="scope">'+options([['2026_actual_funded','2026 · 含资金费'],['all_history_fee_only','2020–2026 · 仅手续费']],scope)+'</select></label></div>'+
        '<div class="bb-tiles">'+tile('组合净收益',pct(s.growth_pct))+tile('最大盯市回撤',pct(s.max_drawdown_pct,false))+tile('入场胜率 · '+wins+'/'+valid,pct(100*wins/valid,false))+'</div>'+
        '<div class="bb-chart-grid">'+panel('结算净值 · 初始 1,000 USDT','<div data-equity class="bb-equity"></div>'+note('曲线按已退出事件结算，两边各从 500 USDT 独立复利。上方最大回撤采用同步 5 分钟盯市。'))+
        panel('按入场类型',table(['类型','入场数','胜率','笔均净收益'],sizes)+note('篮子成员等权，一篮计一笔。笔均净收益与组合复利收益分别计算。'))+'</div>'+
        panel('加 EMA20 条件前后',table(['组合','入场数','组合净收益','最大回撤'],[['原组合',base.event_count,pct(base.growth_pct),pct(base.max_drawdown_pct,false)],['普通 BTC 加 4H EMA20',s.event_count,pct(s.growth_pct),pct(s.max_drawdown_pct,false)]])+note('在所选区间内，对新组合每笔额外扣 0.20 个百分点的往返成本，净收益为 '+pct(stress.growth_pct)+'，最大回撤为 '+pct(stress.max_drawdown_pct,false)+'。'))+
        panel('逐年表现',table(['UTC 年','入场数','BTC','山寨篮子','年度收益'],years,true)+note('每年重新等额分配，统一只扣手续费。2026 年统计截至 10 月 27 日 UTC，已入场持仓继续跟踪至退出。'))+
        '<details class="bb-panel bb-details"><summary>查看所选区间的逐月入场统计</summary>'+table(['UTC 月','入场数','盈利笔数','胜率','笔均净收益'],monthRows,true)+'</details>'+
        (control?panel('2026 匹配随机入场对照',note(control.matched+' / '+control.total+' 次有可比入场，覆盖 '+control.matched_months+' 个月。两组笔均收益相差 '+number(control.edge_pp,3)+' 个百分点，按月检验 p = '+number(control.p_month,3)+'。急跌 BTC 的可匹配样本为 '+control.case_matched+'。')):'')+
        panel('核算口径',note((funded?'本区间计入实际资金费。':'全历史按手续费后、资金费前统计。')+'以收盘盯市计算最大回撤，按入场事件的净收益是否大于零计算胜率。2026 年行情曾参与研究和案例选择。'))+'</div>';
    }
    function facts(row) {
      const items=[],signal=row.signal_snapshot||{};
      if(finite(signal.initial_touch_count)) items.push('初始触轨 '+signal.initial_touch_count+' 币');
      if(finite(signal.new_coins) && branch(row)!=='ALT') items.push('观察期新增 '+signal.new_coins+' 币');
      if(finite(signal.market_share_28d_pct)) items.push('占活跃币 '+pct(signal.market_share_28d_pct,false));
      if(finite(signal.alt_move_pct)) items.push('山寨观察期涨幅中位数 '+pct(signal.alt_move_pct));
      if(finite(signal.ema_filter?.close))items.push('4H 收盘 '+price(signal.ema_filter.close)+' ≤ EMA20 '+price(signal.ema_filter.ema20));
      if(row.asset==='ALT')items.push('篮子 '+row.observed_members+' / '+row.expected_members+' 个成员有可用收益');
      if(signal.known_time && signal.known_time!==row.entry_time)items.push('触轨窗口收盘 '+bjt(signal.known_time));
      return items.join(' · ');
    }
    function detailHeader(row) {
      const net=actual(row);
      const points=row.asset==='BTC' && finite(net) && finite(row.entry_price)?' · 每枚 BTC 净收益 '+number(row.entry_price*net/100)+' 点':'';
      return '<div class="bb-head"><div><h2>'+NAMES[branch(row)]+' · 开仓图</h2><div class="bb-meta">'+bjt(row.entry_time)+' 北京时间 · '+held(row)+'</div></div></div>'+
        '<p><strong class="'+tone(net)+'">'+pct(net)+'</strong>'+esc(points)+'</p>'+note(facts(row))+(row.asset==='ALT'?'<label class="bb-chart-select">成分币<select data-field="symbol"></select></label>':'')+
        '<div data-chart-area role="status">正在读取 Binance 永续 K 线…</div><div data-fill-list></div>';
    }
    const labelOf = (i,count) => i===0?'买入':count===3?(i===1?'卖出 50%':'卖出余仓'):'卖出';
    function fillList(row) {
      const alt=row.asset==='ALT',trade=alt?row.members.find(m=>m.symbol===chosenSymbol):row;
      if(!trade)return '';
      const legs=alt?[{time:trade.selected_exit_time,price:trade.exit_price}]:trade.exit_legs_detail;
      const fills=[{time:trade.entry_time,price:trade.entry_price},...legs].filter(m=>m.time);
      const reason=row.exit_reason_code===2?'保护止损':'持仓到期';
      return '<ol class="bb-marks">'+fills.map((m,i)=>'<li><b>'+labelOf(i,fills.length)+(i===0?'':i===fills.length-1?' · '+reason:' · 目标限价')+'</b><time>'+bjt(m.time)+'</time><strong>'+(finite(m.price)?price(m.price)+' USDT':'缺成交价')+'</strong></li>').join('')+'</ol>'+
        note((alt?'成分 '+chosenSymbol+' · '+(epochYear(row)==='2026'?'含资金费':'仅手续费')+'净收益 '+pct(epochYear(row)==='2026'?trade.funded_pct:trade.net_pct)+'。 ':'')+'成交时间为北京时间，标记落在包含该次成交的 K 线上。');
    }
    async function detail() {
      const here=body.querySelector('[data-detail]');if(!here)return;
      if(!chosen){here.textContent='选择一笔入场可查看开仓图。';return;}
      const row=chosen,token=++busyId;
      here.innerHTML=detailHeader(row);
      if(row.asset==='ALT'){
        const members=row.members||[];
        if(!members.some(m=>m.symbol===chosenSymbol))chosenSymbol=members[0]?.symbol||'';
        here.querySelector('[data-field="symbol"]').innerHTML=options(members.map(m=>[m.symbol,m.symbol]),chosenSymbol);
      }
      here.querySelector('[data-fill-list]').innerHTML=fillList(row);
      try {
        chartRequest?.abort();chartRequest=new AbortController();
        if(!block||block.event_id!==row.event_id) {
          catalog??=await readJSON(BASE+'breadth-charts/manifest.json',lifetime.signal);
          if(token!==busyId)return;
          const item=catalog.events.find(e=>e.event_id===row.event_id);
          if(!item)throw Error('该笔事件缺少已登记的行情文件');
          const loaded=await readJSON(BASE+'breadth-charts/'+item.path,chartRequest.signal);
          if(token!==busyId)return;
          if(loaded.event_id!==row.event_id)throw Error('行情与开仓记录不匹配');
          block=loaded;
        }
        await drawCandle(here,token);
      } catch(err) {if(token===busyId){const area=here.querySelector('[data-chart-area]');if(area)area.innerHTML='<span class="bb-error">'+esc(err.message)+'</span> <button data-action="retry-chart">重试开仓图</button>';}}
    }
    function makeChart(node) {
      stopPlot();plot=window.echarts.init(node,null,{renderer:'canvas'});watcher.observe(node);return plot;
    }
    function renderPlotAgain() {
      if(section==='history' && block?.event_id===chosen?.event_id){const el=body.querySelector('[data-detail]');if(el)drawCandle(el,busyId).catch(()=>{});}
      if(section==='stats')equity();
    }
    function colors() {
      const style=getComputedStyle(root),read=k=>style.getPropertyValue(k).trim();
      return {text:read('--muted'),grid:read('--line'),up:read('--trade-long'),down:read('--trade-short'),blue:read('--brand-primary')};
    }
    async function drawCandle(here,token) {
      const row=chosen,alt=row.asset==='ALT';
      const member=alt?block.members.find(m=>m.symbol===chosenSymbol):null;
      const chart=alt?member?.chart:block.chart;
      const area=here.querySelector('[data-chart-area]');
      stopPlot();
      const series = chart?.bars || [];
      const fills = chart?.markers || [];
      if(!series.length){area.textContent='此币在本次持仓窗口缺少可核实的 Binance K 线。';return;}
      const symbol=chart.symbol,minutes=chart.bar_minutes||block.bar_minutes;
      area.innerHTML='<div class="bb-meta">'+esc(symbol)+' · Binance USDⓈ-M · '+minutes+' 分钟'+(chart.missing_bars?' · 缺失 '+chart.missing_bars+' 根':'')+'</div><div class="bb-chart" role="img" aria-label="'+esc(symbol)+' 历史 K 线与进出场"></div>';
      const target=area.querySelector('.bb-chart');await window.SpikeModules.ensure('signal-charts');
      if(token!==busyId || !target.isConnected)return;
      const c=colors(),step=minutes*60000,byTime=new Map(series.map(b=>[b[0],b]));
      const ticks=[],values=[];
      for(let t=series[0][0];t<=series.at(-1)[0];t+=step){
        ticks.push(t);const b=byTime.get(t);values.push(b?[b[1],b[4],b[3],b[2]]:['-','-','-','-']);
      }
      const markers=fills.map((m,i)=>{
        const t=Math.floor(Date.parse(m.time)/step)*step,index=ticks.indexOf(t);
        if(index<0||!byTime.has(t)||!finite(m.price))return null;
        return {name:labelOf(i,fills.length),coord:[index,m.price],symbol:'triangle',symbolRotate:i?180:0,symbolSize:13,itemStyle:{color:i?c.down:c.blue},label:{show:true,position:i?'top':'bottom',formatter:i?(fills.length===3?(i===1?'卖½':'余仓'):'卖'):'买',fontSize:10,color:i?c.down:c.blue}};
      }).filter(Boolean);
      makeChart(target).setOption({animation:false,grid:{left:9,right:10,top:24,bottom:48,containLabel:true},
        tooltip:{trigger:'axis',confine:true,axisPointer:{type:'cross'},formatter:params=>{const i=params[0]?.dataIndex,b=byTime.get(ticks[i]);return b?bjt(new Date(b[0]).toISOString())+' 北京时间<br>开 '+price(b[1])+'　高 '+price(b[2])+'<br>低 '+price(b[3])+'　收 '+price(b[4]):'此处缺 K 线';}},
        xAxis:{type:'category',data:ticks.map(String),axisLabel:{color:c.text,hideOverlap:true,formatter:t=>bjt(new Date(Number(t)).toISOString()).slice(minutes>=60?5:11,minutes>=60?10:16)},axisLine:{lineStyle:{color:c.grid}}},
        yAxis:{type:'value',scale:true,splitNumber:4,axisLabel:{color:c.text,formatter:v=>price(v)},splitLine:{lineStyle:{color:c.grid}}},
        dataZoom:[{type:'inside',zoomOnMouseWheel:'ctrl',preventDefaultMouseMove:false},{type:'slider',height:16,bottom:4,showDetail:false}],
        series:[{type:'candlestick',data:values,itemStyle:{color:c.up,color0:c.down,borderColor:c.up,borderColor0:c.down},markPoint:{animation:false,data:markers}}]});
    }
    async function equity() {
      const target=body.querySelector('[data-equity]');if(!target)return;
      const token=busyId;
      try {
        await window.SpikeModules.ensure('signal-charts');if(token!==busyId||!target.isConnected)return;
        const curve=data.equity_curves[scope+'_settlement'],points=curve.points;
        const c=colors();
        makeChart(target).setOption({animation:false,grid:{left:6,right:12,top:10,bottom:16,containLabel:true},
          xAxis:{type:'time',axisLabel:{color:c.text,hideOverlap:true},splitNumber:4,axisLine:{lineStyle:{color:c.grid}}},
          yAxis:{type:'value',scale:true,splitNumber:4,axisLabel:{color:c.text},splitLine:{lineStyle:{color:c.grid}}},
          tooltip:{trigger:'axis',confine:true,formatter:items=>{const [t,v]=items[0].value;return bjt(new Date(t).toISOString())+'<br>'+number(v)+' USDT';}},
          series:[{type:'line',step:'end',showSymbol:false,lineStyle:{color:c.blue,width:2},data:points.map(p=>[Date.parse(p.time),1000*p.combined_equity])}]});
      }catch(err){if(token===busyId)target.textContent='净值曲线暂时无法绘制：'+err.message;}
    }
    async function load() {
      nav();body.innerHTML='<p role="status">正在读取回测账本…</p>';
      try {
        const received=await readJSON(BASE+'breadth-ema20-data.json',lifetime.signal);
        if(lifetime.signal.aborted)return;
        if(received.schema_version!=='breadth-ema20.v1'||!Array.isArray(received.events))throw Error('回测账本版本不匹配');
        data=received;entries=[...data.events].sort((a,b)=>b.entry_time.localeCompare(a.entry_time)||a.asset.localeCompare(b.asset));
        chosen=entries.find(e=>e.event_id===eventId)||entries[0];render();
        if(standalone && section==='history' && eventId===chosen?.event_id && innerWidth<=720)body.querySelector('.bb-detail')?.scrollIntoView({block:'start'});
      } catch(err) {
        if(!lifetime.signal.aborted)body.innerHTML='<p role="status" class="bb-error">'+esc(err.message)+'</p><button data-action="retry">重新读取账本</button>';
      }
    }
    root.addEventListener('cancel',event=>{event.preventDefault();close();});
    root.addEventListener('click',event=>{
      const btn=event.target.closest('button'),action=btn?.dataset.action;
      if(event.target===root && !standalone && (event.clientX<root.getBoundingClientRect().left || event.clientX>root.getBoundingClientRect().right)){close();return;}
      if(action==='close'){close();return;}
      if(action==='retry'){load();return;}
      if(btn?.dataset.page){section=btn.dataset.page;render();return;}
      if(!data)return;
      if(btn?.dataset.period){
        year=btn.dataset.period;kind='all';page=0;chosen=null;block=null;chosenSymbol='';section='history';render();
        body.querySelector('.bb-open-event')?.focus({preventScroll:true});return;
      }
      if(action==='retry-chart'){detail();return;}
      const row=event.target.closest('[data-id]');
      if(row || action==='case'){
        const id=action==='case'?CASE:row.dataset.id;
        if(action==='case'){year='all';kind='all';}
        chosen=entries.find(e=>e.event_id===id)||null;chosenSymbol='';block=null;render();
        const detailNode=body.querySelector('.bb-detail');
        if(innerWidth<=720) detailNode?.scrollIntoView({block:'start'});
        else body.querySelector('tr[aria-current="true"] button')?.focus({preventScroll:true});
      }
      if(action==='prev'||action==='next'){
        page+=action==='prev'?-1:1;chosen=filtered()[page*LIMIT];chosenSymbol='';block=null;render();
        body.querySelector('.bb-open-event')?.focus({preventScroll:true});
      }
    });
    root.addEventListener('change',event=>{
      const key=event.target.dataset.field,value=event.target.value;
      if(key==='symbol'){
        chosenSymbol=value;clearDetails();detail();body.querySelector('[data-field="symbol"]')?.focus({preventScroll:true});return;
      }
      if(key==='scope')scope=value;
      else if(key==='year'||key==='kind'){
        if(key==='year')year=value;else kind=value;
        page=0;chosen=null;chosenSymbol='';block=null;
      } else return;
      render();body.querySelector('[data-field="'+key+'"]')?.focus({preventScroll:true});
    });
    load();
  }
  window.SpikeBreadthBook={open};
  if(document.getElementById('breadth-standalone')){
    const q=new URLSearchParams(location.search);open({standalone:true,tab:q.get('tab')||'history',eventId:q.get('event')});
  }
})();
