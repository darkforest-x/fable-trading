/* Monday limit-reclaim v2 monitor.
 * Contract: monday_reclaim_paper.py snapshot(); position allocation owns P&L,
 * signal.taken only records the score gate. API equity excludes floating P&L.
 * Design: Stitch 9b7f625827da4e60a41b5bf1361c7829 (synthetic examples only).
 */
(() => {
  'use strict';
  const finite = value => typeof value === 'number' && Number.isFinite(value);
  const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const num = (value, digits=2) => finite(value) ? value.toLocaleString('en-US', {minimumFractionDigits:digits,maximumFractionDigits:digits}) : '—';
  const signed = (value, digits=2) => finite(value) ? `${value > 0 ? '+' : ''}${num(value,digits)}` : '—';
  const bp = value => finite(value) ? `${signed(value/100)}%` : '—';
  const price = value => finite(value) ? value.toLocaleString('en-US', {maximumSignificantDigits:9}) : '—';
  const tone = value => !finite(value) || value === 0 ? '' : value > 0 ? 'rp-positive' : 'rp-negative';
  const dateFormat = new Intl.DateTimeFormat('zh-CN', {timeZone:'Asia/Shanghai',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false});
  const date = value => finite(value) && value > 0 && !Number.isNaN(new Date(value).getTime()) ? dateFormat.format(new Date(value)) : '—';
  const validSymbol = value => /^[A-Z0-9]{1,32}-USDT-SWAP$/.test(String(value || ''));
  const base = value => validSymbol(value) ? value.slice(0,-10) : String(value || '未知合约');
  const funded = row => row.taken === 1 && finite(row.notional) && row.notional > 0;
  const targetLabel = row => row.side === 1 ? '周一低点' : row.side === -1 ? '周一高点' : '周一目标';
  const sideLabel = row => row.side === 1 ? '做多' : row.side === -1 ? '做空' : '方向未知';
  const reason = value => ({target:'到达目标',breakeven:'入场价止损',failed_reclaim:'确认未收回',manual:'手动平仓'}[value] || '未知退出原因');
  function stage(row, now) {
    if (row.stage === 'closed') return reason(row.reason);
    if (row.stage === 'breakeven') return '入场价止损已启用';
    if (row.stage === 'pending_confirm') return finite(row.decision_ms) && row.decision_ms <= now ? '等待确认结算' : '等待收盘确认';
    return '状态待核对';
  }
  function signalStatus(row) {
    if (row.status === 'excluded') return '排除币种';
    if (row.status === 'feature_error') return '特征读取失败';
    if (row.status === 'scored') return row.taken === 1 ? '通过评分' : '未通过评分';
    return '状态待核对';
  }
  function allocation(row) {
    return funded(row) ? '计入资金' : row.note === 'no_capacity' ? '影子 · 额度不足' : '影子 · 不计入资金';
  }
  function route(params={}) {
    return {track:params.track === '5' ? '5' : '60',tab:['open','closed','events'].includes(params.tab) ? params.tab : 'open',
      scope:['funded','shadow','all'].includes(params.scope) ? params.scope : 'funded',q:String(params.q || '').slice(0,80),period:['5m','15m','1H','4H'].includes(params.period)?params.period:'5m',trade:/^\d+$/.test(String(params.trade||''))?String(params.trade):'',symbol:validSymbol(params.symbol)?params.symbol:''};
  }
  function chartLink(symbol, track) {
    return validSymbol(symbol) ? '#monday-fib?' + new URLSearchParams({symbol,timeframe:track === '5' ? '5m' : '1H'}) : '#monday-fib';
  }
  function validate(data) {
    const records=value=>Array.isArray(value) && value.every(row=>row && typeof row==='object' && !Array.isArray(row) && typeof row.symbol==='string');
    if (data?.paper_only !== true || data?.strategy !== 'monday_1618_limit_reclaim_v2' || !finite(data.now_ms) || !records(data.signals)
      || !['60','5'].every(key => data.tracks?.[key] && records(data.tracks[key].open) && records(data.tracks[key].closed))) {
      throw Error('模拟盘返回格式不完整，请稍后重试');
    }
    return data;
  }
  function summary(track) {
    const stats=track?.stats || {}, count=stats.taken_closed;
    return {equity:track?.equity,start:track?.start_equity,
      pnl:finite(track?.equity) && finite(track?.start_equity) ? track.equity-track.start_equity : null,
      count,winRate:finite(count) && count>0 && finite(stats.wins) ? stats.wins/count*100 : null,
      wins:stats.wins,mean:stats.mean_net_bp};
  }
  function rows(data, state) {
    const source = state.tab === 'events' ? data.signals : data.tracks[state.track][state.tab];
    const query=state.q.trim().toUpperCase();
    return source.filter(row => (!query || String(row.symbol).toUpperCase().includes(query))
      && (state.tab === 'events' || state.scope === 'all' || (state.scope === 'funded' ? funded(row) : !funded(row))))
      .slice().sort((a,b) => (b[state.tab==='events'?'candle_ms':state.tab==='closed'?'exit_ms':'fill_ms']||0)-(a[state.tab==='events'?'candle_ms':state.tab==='closed'?'exit_ms':'fill_ms']||0));
  }
  // One visible page owns the GET, timer and cancellation generation. Failed
  // reads retain the last successful snapshot; late responses cannot revive it.
  function createFeed({read,cancel=()=>{},change=()=>{},schedule=setTimeout,unschedule=clearTimeout,interval=10000,validateData=validate}) {
    let active=false, epoch=0, pending=null, timer=null;
    let state={data:null,error:null,busy:false};
    const emit=next=>{state={...state,...next};change(state);};
    function refresh() {
      if (!active) return Promise.resolve();
      if (pending) return pending;
      unschedule(timer);timer=null;
      const revision=epoch;
      emit({busy:true});
      const task=Promise.resolve().then(()=>active && revision===epoch ? read() : null)
        .then(data=>{if(active && revision===epoch)emit({data:validateData(data),error:null});})
        .catch(error=>{if(active && revision===epoch)emit({error:error instanceof TypeError ? '网络暂时不可用' : error?.message || '读取失败，请稍后重试'});})
        .finally(()=>{if(pending!==task)return;pending=null;emit({busy:false});if(active)timer=schedule(refresh,interval);});
      pending=task;return task;
    }
    return {refresh,getState:()=>state,setActive(value){
      if(active===value)return;active=value;epoch++;unschedule(timer);timer=null;
      if(!active){pending=null;cancel();emit({busy:false});}else refresh();
    }};
  }
  function coinHTML(row, track) {
    const symbol=base(row.symbol), href=chartLink(row.symbol,track);
    const logo=validSymbol(row.symbol) ? `https://static.okx.com/cdn/oksupport/asset/currency/icon/${encodeURIComponent(symbol.toLowerCase())}.png` : '';
    return `<span class="rp-coin"><span class="rp-avatar" aria-hidden="true"><span>${esc(symbol.slice(0,1))}</span>${logo?`<img src="${esc(logo)}" alt="" loading="lazy" referrerpolicy="no-referrer">`:''}</span><span><a href="${esc(href)}" title="查看 ${esc(symbol)} 1.618 图表">${esc(symbol)} <span aria-hidden="true">↗</span></a><small class="${row.side===1?'rp-positive':row.side===-1?'rp-negative':''}">${sideLabel(row)} · USDT</small></span></span>`;
  }
  const cell=(label,value,cls='')=>`<span class="rp-cell ${cls}"><small>${esc(label)}</small><strong>${value}</strong></span>`;
  const detail=(label,value)=>`<div><dt>${esc(label)}</dt><dd>${value}</dd></div>`;
  function rowHTML(row, state, now) {
    const event=state.tab==='events', closed=state.tab==='closed', isFunded=funded(row);
    const key=`${state.tab}-${row.id}`;
    const value=closed ? (isFunded?`${signed(row.pnl)} <small>USDT</small>`:'不计入资金') : bp(row.unrealized_bp);
    const fields=event
      ? cell('触及 1.618',price(row.level))+cell(targetLabel(row),price(row.target))+cell('评分结果',esc(signalStatus(row)))+cell('入场分数',finite(row.score)?num(row.score,4):'—')+cell('触价时间 · 北京',date(row.candle_ms))
      : cell('1.618 入场价',price(row.entry))+cell(targetLabel(row),price(row.target))+cell('模拟状态',`${esc(stage(row,now))}<small class="rp-allocation">${esc(allocation(row))}</small>`)+cell(closed?'已实现盈亏':'参考浮动收益',value,closed?(isFunded?tone(row.pnl):''):tone(row.unrealized_bp))+cell(closed?'退出时间 · 北京':'确认时间 · 北京',date(closed?row.exit_ms:row.decision_ms));
    const more=event
      ? detail('观测时间',date(row.observed_ms))+detail('归属周一',esc(row.week))+detail('周一高点',price(row.high))+detail('周一低点',price(row.low))
        +detail('评分说明','通过评分表示满足入场门槛，是否计入资金以对应轨道的交易记录为准。')+(row.error?detail('读取错误',esc(row.error)):'')
      : detail('入场时间',date(row.fill_ms))+detail('确认时间',date(row.decision_ms))+detail('名义本金',isFunded?`${num(row.notional)} USDT`:'影子记录，不占用资金')
        +detail('保护止损',row.be_from_ms?`${price(row.entry)} · ${date(row.be_from_ms)} 起生效`:'未启用入场价止损')
        +(closed ? detail('退出价格',price(row.exit_price))+detail('毛收益',bp(row.gross_bp))+detail('手续费',finite(row.fee_bp)?`${num(row.fee_bp/100)}%`:'—')
          +detail('净收益',bp(row.net_bp))+detail('滑点情景净收益',bp(row.net_slip_bp))
          : detail('后台参考报价',price(row.mark))+detail('入场分数',finite(row.score)?num(row.score,4):'—'))
        +(row.note?detail('记录说明',esc(row.note==='no_capacity'?'通过评分，但轨道可用额度不足，按影子记录跟踪。':row.note)):'');
    const closeButton=!event&&!closed&&isFunded&&finite(row.remaining_notional)&&row.remaining_notional>0?`<div class="rp-position-actions"><span>剩余 ${price(row.remaining_notional/row.entry)} ${esc(base(row.symbol))}${row.remaining_notional<row.notional?` · 已减 ${num((1-row.remaining_notional/row.notional)*100,1)}%`:''}</span><button type="button" data-rp-close="${esc(row.id)}" aria-label="模拟平仓 ${esc(base(row.symbol))}">平仓</button></div>`:'';
    const exits=Array.isArray(row.exits)&&row.exits.length?`<div class="rp-exits"><strong>逐笔平仓</strong>${row.exits.map(x=>`<p>${date(x.exit_ms)} · ${esc(reason(x.reason))} · ${price(x.notional/row.entry)} ${esc(base(row.symbol))}<br>成交 ${price(x.price)} · 净盈亏 ${signed(x.pnl)} USDT</p>`).join('')}</div>`:'';
    const selectButton=event?'':`<button type="button" class="rp-select-trade" data-rp-trade="${esc(row.id)}" aria-pressed="${String(state.trade===String(row.id))}" aria-label="查看 ${esc(base(row.symbol))} ${sideLabel(row)} ${date(row.fill_ms)} 开仓图"><span><b>${esc(base(row.symbol))}</b><small class="${row.side===1?'rp-positive':'rp-negative'}">${sideLabel(row)} · ${esc(stage(row,now))}</small></span><span class="rp-row-value ${closed?tone(row.pnl):tone(row.unrealized_bp)}">${value}<small>${date(row.fill_ms)}</small></span></button>`;
    return `<article class="rp-record">${selectButton}${closeButton}<details class="rp-row" data-rp-row="${esc(key)}"><summary>${event?coinHTML(row,state.track):`<span>开仓 ${price(row.entry)}</span><span>${esc(allocation(row))}</span>`}<span class="rp-expand">${event?'详情':'明细'} ⌄</span></summary><div class="rp-row-detail"><div class="rp-detail-fields">${fields}</div><dl>${more}</dl>${exits}<a class="rp-chart-link" href="${esc(chartLink(row.symbol,state.track))}">查看 ${esc(base(row.symbol))} 的 1.618 图表 ↗</a></div></details></article>`;
  }
  const api={finite,esc,num,signed,bp,price,funded,targetLabel,stage,signalStatus,allocation,route,chartLink,validate,summary,rows,createFeed,rowHTML};
  if(typeof module!=='undefined' && module.exports){module.exports=api;return;}
  const root=document.getElementById('monday-reclaim-paper');
  if(!root)return;
  let state=route(), pageActive=false, mounted=false, shown=50, signature='', chart=null, pinned=null, ma=false, recordsOpen=false, sheet=null, watch=null, chartSymbol='', closer=null;
  const requests=window.SpikeRuntime.createRequests();
  const feed=createFeed({read:()=>requests.json('/api/monday-reclaim-paper'),cancel:()=>requests.cancel(),change:render});
  const el=id=>root.querySelector(`[data-rp="${id}"]`);
  function mount(){
    if(mounted)return;mounted=true;
    root.innerHTML=`<div class="rp-wallpaper" data-rp="chart"></div><div class="rp-watermark" aria-hidden="true" data-rp="watermark"></div><p class="rp-chart-notice rp-glass" data-rp="chart-notice" role="status" hidden></p>
      <header class="rp-heading rp-glass"><details class="rp-navigation"><summary aria-label="打开模拟盘导航"><b>ϟ SPIKE</b> <span class="rp-badge">模拟</span> ⌄</summary><nav class="rp-glass"><a href="#monday-fib">1.618 图表</a><a href="#signals?pane=records">信号记录</a><a href="#signals?pane=overview">表现分析</a><a href="#yolo-detect">形态巡检</a></nav></details><button type="button" class="rp-watch-trigger" data-rp-action="watch" aria-controls="rp-watch-panel" aria-expanded="false">监控币种 <span>—</span></button><button type="button" class="rp-refresh" data-rp-action="refresh" aria-label="刷新模拟盘" title="刷新模拟盘">↻</button></header>
      <section class="rp-context rp-glass"><div class="rp-context-line" data-rp="selection">等待模拟交易</div><div class="rp-context-tools"><div class="rp-tracks" role="group" aria-label="选择模拟轨道">${['60','5'].map(track=>`<button type="button" data-rp-track="${track}" aria-pressed="false">${track==='60'?'60m 主轨':'5m 研究轨'}</button>`).join('')}</div><button type="button" data-rp-action="records" aria-controls="rp-record-panel" aria-expanded="false">交易簿 <span data-rp="total">—</span></button></div></section>
      <details class="rp-account rp-glass"><summary><span>已实现净值 <b data-rp="equity">—</b><small> USDT</small></span><span data-rp="pnl"></span><span aria-hidden="true">⌄</span></summary><div class="rp-account-body"><section data-rp="metrics" class="rp-metrics"></section><p data-rp="status" role="status"></p><details class="rp-rules"><summary>规则与统计口径</summary><div data-rp="rules"></div></details></div></details>
      <div class="rp-alert rp-glass" data-rp="error" role="alert" hidden></div>
      <section id="rp-record-panel" class="rp-records rp-glass" aria-label="模拟交易簿" aria-hidden="true" inert><header class="rp-record-heading" data-yd-sheet-drag><strong>模拟交易簿</strong><button type="button" data-rp-action="close-records" aria-label="收起交易簿">×</button></header><div class="rp-toolbar"><div class="rp-tabs" role="group" aria-label="记录类型">${[['open','持仓'],['closed','已结束'],['events','触价']].map(([tab,label])=>`<button type="button" data-rp-tab="${tab}" aria-pressed="false">${label}<span data-rp="count-${tab}"></span></button>`).join('')}</div><div class="rp-filters"><label class="rp-search"><input type="search" placeholder="搜索币种" aria-label="搜索模拟盘币种" maxlength="80" autocomplete="off" data-rp="search"></label><label class="rp-scope" data-rp="scope-label"><select aria-label="记录资金范围" data-rp="scope"><option value="funded">计入资金</option><option value="shadow">影子记录</option><option value="all">全部记录</option></select></label></div></div><p class="rp-record-caption" data-rp="caption"></p><div class="rp-record-scroll"><div data-rp="rows" class="rp-rows"></div><button type="button" data-rp-action="more" class="rp-more" hidden>再显示 50 条</button><p class="rp-footnote" data-rp="record-note"></p></div></section>
      <nav class="rp-dock rp-glass" aria-label="模拟盘图表工具"><select data-rp="period" aria-label="K 线周期">${['5m','15m','1H','4H'].map(v=>`<option value="${v}">${v}</option>`).join('')}</select><button type="button" data-rp-action="ma" aria-pressed="false" title="SMA / EMA 20、60、120">六均线</button><button type="button" data-rp-action="fit" title="显示全部已加载 K 线">全图</button><button type="button" data-rp-action="latest" title="回到最新 K 线">最新 K</button><button type="button" data-rp-action="follow" aria-pressed="true">跟随最新单</button></nav><span class="rp-chart-foot">本周 1.618 区间 · 开平仓按实际成交标记</span>`;
    chart=window.SpikeReclaimChart.create(el('chart'),text=>{el('chart-notice').textContent=text;el('chart-notice').hidden=!text;});
    sheet=window.SpikeYoloPreviewSheet.bind({panel:root.querySelector('.rp-records'),isOpen:()=>recordsOpen,dismiss:()=>setRecords(false)});
    closer=window.SpikeReclaimClose.create({root,onChanged:()=>{feed.refresh();watch?.refresh();}});
    watch=window.SpikeReclaimWatchlist.create({root,createFeed,getContext:()=>({track:feed.getState().data?.tracks[state.track],symbol:chartSymbol}),onOpen:()=>{if(recordsOpen&&matchMedia('(max-width:700px)').matches)setRecords(false,false);},onSelect:symbol=>{state.symbol=symbol;state.trade='';pinned=null;chart.resetFocus();sync();render(feed.getState());}});
    el('period').addEventListener('change',event=>{state.period=event.target.value;sync();render(feed.getState());});
    root.addEventListener('keydown',event=>{if(event.key==='Escape'&&recordsOpen)setRecords(false);});
    root.addEventListener('click',event=>{
      const button=event.target.closest('button');if(!button)return;
      if(button.dataset.rpClose){const p=feed.getState().data?.tracks[state.track].open.find(p=>String(p.id)===button.dataset.rpClose);if(p)closer.open(p);}
      if(button.dataset.rpTrack){state.track=button.dataset.rpTrack;state.trade='';pinned=null;shown=50;sync();render(feed.getState());}
      if(button.dataset.rpTab){state.tab=button.dataset.rpTab;shown=50;sync();render(feed.getState());}
      if(button.dataset.rpTrade){state.symbol='';state.trade=button.dataset.rpTrade;pinned=window.SpikeReclaimChart.select(feed.getState().data?.tracks[state.track],state.trade);chart.resetFocus();sync();render(feed.getState());if(matchMedia('(max-width:700px)').matches)setRecords(false);}
      const action=button.dataset.rpAction;
      if(action==='watch')watch.toggle();
      if(action==='records')setRecords(!recordsOpen);
      if(action==='close-records')setRecords(false);
      if(action==='fit')chart.fit();if(action==='latest')chart.latest();
      if(action==='ma'){ma=!ma;render(feed.getState());}
      if(action==='follow'){state.symbol='';state.trade='';pinned=null;chart.resetFocus();sync();render(feed.getState());}
      if(action==='refresh'){chart.retry();feed.refresh();watch.refresh();}
      if(button.dataset.rpAction==='more'){shown+=50;render(feed.getState());}
    });
    el('scope').addEventListener('change',event=>{state.scope=event.target.value;shown=50;sync();render(feed.getState());});
    el('search').addEventListener('input',event=>{state.q=event.target.value;shown=50;sync();render(feed.getState());});
    root.addEventListener('error',event=>{if(event.target.tagName==='IMG')event.target.hidden=true;},true);
  }
  function setRecords(value,focus=true){if(value&&matchMedia('(max-width:700px)').matches)watch?.setOpen(false,false);sheet?.reset();recordsOpen=value;const panel=root.querySelector('.rp-records');panel.classList.toggle('is-open',value);panel.inert=!value;panel.setAttribute('aria-hidden',String(!value));root.querySelector('[data-rp-action="records"]').setAttribute('aria-expanded',String(value));if(!value&&focus)root.querySelector('[data-rp-action="records"]').focus({preventScroll:true});}
  function sync(){window.SpikeNavigation?.sync('signals',{pane:'reclaim',...state});}
  function replace(id,html){const node=el(id);if(node.innerHTML!==html)node.innerHTML=html;}
  function render(current){
    if(!pageActive)return;mount();
    const {data,error,busy}=current, track=data?.tracks?.[state.track], stats=summary(track);
    root.setAttribute('aria-busy',String(busy && !data));
    root.querySelector('[data-rp-action="refresh"]').disabled=busy;
    const worker=data?.worker, workerIssue=worker && (worker.alive!==true || worker.error);
    el('status').textContent=data ? `${error?'上次记录':'更新于'} ${date(data.now_ms)} · 北京时间 · ${workerIssue?'后台需要检查':busy?'同步中…':'每 10 秒同步'}${worker?.queue>0?` · 等待处理 ${worker.queue} 条`:''}` : busy?'正在读取模拟盘…':'尚未读取到模拟盘数据';
    el('error').hidden=!error && !workerIssue;
    el('error').textContent=error ? `同步失败：${error}。${data?'保留上次记录，请刷新重试。':'请刷新重试。'}` : workerIssue ? `后台状态异常：${worker.error || '处理线程未运行'}。当前展示已保存的记录。` : '';
    root.querySelectorAll('[data-rp-track]').forEach(button=>button.setAttribute('aria-pressed',String(button.dataset.rpTrack===state.track)));
    root.querySelectorAll('[data-rp-tab]').forEach(button=>button.setAttribute('aria-pressed',String(button.dataset.rpTab===state.tab)));
    if(el('search').value!==state.q)el('search').value=state.q;
    el('scope').value=state.scope;el('scope-label').hidden=state.tab==='events';
    const selected=state.symbol&&!state.trade?window.SpikeReclaimWatchlist.coinTrade(track,state.symbol):window.SpikeReclaimChart.select(track,state.trade,pinned);if(state.trade&&selected)pinned=selected;
    chartSymbol=state.symbol||selected?.symbol||'';
    chart.show(selected?{...selected,asof_ms:data.now_ms}:null,state.period,ma,chartSymbol);watch?.render();el('period').value=state.period;
    if(!selected&&state.trade){el('chart-notice').textContent='该交易不在当前返回记录中，请点击跟随最新单。';el('chart-notice').hidden=false;}
    root.querySelector('[data-rp-action="ma"]').setAttribute('aria-pressed',String(ma));
    root.querySelector('[data-rp-action="follow"]').setAttribute('aria-pressed',String(!state.trade&&!state.symbol));
    replace('selection',selected?`<strong>${esc(base(selected.symbol))}<small> / USDT</small></strong><span class="${selected.side===1?'rp-positive':'rp-negative'}">${sideLabel(selected)} · ${esc(stage(selected,data.now_ms))}</span><small>${state.trade?'已固定交易':state.symbol?'已固定币种':'跟随最新资金交易'} · ${date(selected.fill_ms)} 开仓</small>`:chartSymbol?`<strong>${esc(base(chartSymbol))}<small> / USDT</small></strong><small>已固定币种 · 最近记录无资金成交</small>`:'<strong>1.618 模拟盘</strong><small>等待资金交易 · 可从监控列表查看 K 线</small>');
    el('watermark').textContent=chartSymbol?base(chartSymbol)+' / USDT':'';
    el('total').textContent=track?String(track.open.filter(funded).length):'—';
    replace('equity',num(stats.equity));replace('pnl',`<b class="${tone(stats.pnl)}">${signed(stats.pnl)}</b>`);
    replace('metrics',cell('已实现盈亏',`${signed(stats.pnl)} USDT`,tone(stats.pnl))+cell('累计已平仓',`${num(stats.count,0)} 笔`)+cell('已平仓胜率',finite(stats.winRate)?`${num(stats.winRate,1)}%`:'—')+cell('平均单笔净收益',bp(stats.mean),tone(stats.mean))+cell('初始资金',`${num(stats.start)} USDT`));
    for(const tab of ['open','closed','events'])el(`count-${tab}`).textContent=data ? String(rows(data,{...state,tab}).length) : '—';
    const items=data?rows(data,state):[], visible=items.slice(0,shown);
    el('caption').textContent=!data ? (error?'记录暂时不可用':'正在读取记录…') : state.tab==='events' ? `两轨共用触价记录 · ${items.length} 条符合筛选 · 最近最多 100 条` : `${state.track==='60'?'60 分钟主轨':'5 分钟研究轨'} · ${{funded:'计入资金',shadow:'影子记录',all:'全部记录'}[state.scope]} · ${items.length} 条${state.tab==='closed'?'符合筛选 · 最近最多 100 条':'持仓'}`;
    const rowSignature=visible.length ? visible.map(row=>rowHTML(row,state,data.now_ms)).join('')
      : `<div class="rp-empty"><span aria-hidden="true">${data?'◌':'⋯'}</span><strong>${!data?(error?'暂时无法读取':'正在读取模拟盘'):state.q?'没有匹配的币种':state.tab==='open'?'当前没有持仓':state.tab==='closed'?'当前没有已结束记录':'还没有触价记录'}</strong><p>${data?(state.q?'换一个币种或清空搜索。':state.scope==='shadow'&&state.tab!=='events'?'未计入资金的模拟交易会出现在这里。':'后台产生新记录后会自动更新。'):'请稍候，也可以点击刷新重试。'}</p></div>`;
    if(rowSignature!==signature){
      const container=el('rows'),open=new Set(Array.from(container.querySelectorAll('details[open]'),node=>node.dataset.rpRow));
      const focused=document.activeElement, focusedRow=focused?.closest?.('[data-rp-row]')?.dataset.rpRow;
      const focusLink=focused?.tagName==='A',focusDetail=!!focused?.closest?.('.rp-row-detail');
      container.innerHTML=rowSignature;
      container.querySelectorAll('details').forEach(node=>{node.open=open.has(node.dataset.rpRow);if(node.dataset.rpRow===focusedRow)node.querySelector(focusLink?(focusDetail?'.rp-chart-link':'summary a'):'summary')?.focus({preventScroll:true});});
      signature=rowSignature;
    }
    root.querySelectorAll('[data-rp-trade]').forEach(button=>button.setAttribute('aria-pressed',String(button.dataset.rpTrade===String(selected?.id))));
    root.querySelector('[data-rp-action="more"]').hidden=items.length<=shown;
    el('record-note').textContent=state.tab==='events' ? '通过评分不等于已分配资金；分配结果请查看各轨道交易记录。' : state.tab==='open' ? '参考浮动收益未扣费；账户净值包含已减仓盈亏，不包含余仓浮盈。' : '净收益已扣手续费；滑点情景仅供对照，不重复扣入账户净值。';
    replace('rules',`<p>两轨分别记账，初始资金各 ${num(data?.sizing?.start_equity)} USDT；每笔最多按当时净值的 ${finite(data?.sizing?.fraction)?num(data.sizing.fraction*100,0):'—'}% 分配名义本金，总名义敞口上限 ${num(data?.sizing?.gross,1)} 倍，按剩余额度分配。影子记录不占用资金、不改变净值。</p><p>1.618 限价入场；做多目标为周一低点，做空目标为周一高点。确认 K 线收回后启用入场价止损，否则按确认收盘退出。入场价止损仍产生手续费。</p><p>Maker ${finite(data?.fees_bp?.maker)?num(data.fees_bp.maker/100):'—'}% / Taker ${finite(data?.fees_bp?.taker)?num(data.fees_bp.taker/100):'—'}%（单边）；退出滑点情景 ${finite(data?.fees_bp?.report_slippage)?num(data.fees_bp.report_slippage/100):'—'}%。成交费用以每条记录为准。</p><p>入场分数门槛 ${num(data?.model?.threshold,4)}，分数不代表胜率。模型训练截止 ${esc(data?.model?.trained_before || '—')}；训练行情与当前 OKX 行情存在来源差异。BTC、ETH、XAU、XAG、PAXG、XAUT 仅记录触价，不开模拟仓。</p><p>上方统计为所选轨道全部已结束资金交易；结束与触价列表各最多返回最近 100 条，列表筛选不改变累计统计。手动平仓仅影响模拟资金；部分平仓立即计入已实现盈亏，已平仓统计仅计完全结束的整笔交易。</p>`);
  }
  document.addEventListener('visibilitychange',()=>{feed.setActive(pageActive && !document.hidden);chart?.setActive(pageActive && !document.hidden);watch?.setActive(pageActive && !document.hidden);});
  window.SpikeReclaimPaper={
    setActive(value){pageActive=value;root.hidden=!value;if(value)mount();else{sheet?.reset();closer?.hide();}chart?.setActive(value && !document.hidden);if(value)render(feed.getState());feed.setActive(value && !document.hidden);watch?.setActive(value && !document.hidden);},
    refresh:()=>{watch?.refresh();return feed.refresh();},route:()=>({...state}),
    restore(params){const next=route(params);if(next.track!==state.track||next.trade!==state.trade||next.symbol!==state.symbol)pinned=null;state=next;shown=50;render(feed.getState());}
  };
})();
