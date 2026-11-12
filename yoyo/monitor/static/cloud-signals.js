/* Stitch 6759b56d047c40339658911d48455805 → live, read-only signal cards. */
(() => {
  'use strict';
  const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const dateFormat=new Intl.DateTimeFormat('zh-CN',{timeZone:'Asia/Shanghai',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false});
  const date=v=>dateFormat.format(new Date(v));
  const paths={search:'M9 16a7 7 0 1 0 0-14 7 7 0 0 0 0 14m5-2 6 6',sort:'M5 4v16m-3-3 3 3 3-3M11 5h10M11 11h7M11 17h4',close:'m6 6 12 12M18 6 6 18',expand:'M9 3H3v6m12-6h6v6M3 15v6h6m12-6v6h-6',focus:'M8 3H3v5m13-5h5v5M3 16v5h5m13-5v5h-5M8 12h8m-4-4v8',latest:'m5 5 8 7-8 7m14-14v14',layers:'m12 3 10 6-10 6L2 9l10-6M2 13l10 6 10-6M2 17l10 6 10-6',grip:'M8 6h.01M16 6h.01M8 12h.01M16 12h.01M8 18h.01M16 18h.01',arrow:'M7 17 17 7M7 7h10v10'};
  const icon=n=>`<svg viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><path d="${paths[n]}"></path></svg>`;
  function select(items,query,side,sort){return items.filter(x=>(!query||x.symbol.replaceAll('-','').includes(query.toUpperCase().replace(/[^A-Z0-9]/g,'')))&&(!side||x.side===Number(side))).sort((a,b)=>sort==='rr'?(b.planned_rr??-Infinity)-(a.planned_rr??-Infinity):sort==='oldest'?a.confirmed_ms-b.confirmed_ms:b.confirmed_ms-a.confirmed_ms);}
  function tvURL(symbol,period,platform=''){
    return window.SpikeMondayList.tradingViewURL(symbol,platform,period);
  }
  function lifecycle(x,now){
    const origin=x.provenance||(x.historical===false?'live':'backfill'),age=now-x.confirmed_ms;
    const live=origin==='live'&&age>=0&&age<43200000;
    return {origin,live,fresh:live&&age<1800000,label:live&&age<1800000?'新确认':origin==='live'?'实时捕获':origin==='delayed'?'延迟补录':'历史补录'};
  }
  function selectedSignal(value,id){
    const exact=value.items.find(s=>s.id===id)||(value.selected?.id===id?value.selected:null);
    if(exact)return exact;
    return value.selected&&value.selected_requested_id===id&&value.selection_redirect_id===value.selected.id?value.selected:null;
  }
  if(typeof module!=='undefined')module.exports={select,tvURL,lifecycle,selectedSignal};
  if(typeof document==='undefined')return;
  const root=document.getElementById('cloud-signal-root');if(!root)return;
  const price=v=>Number.isFinite(v)?window.SpikeMondayList.price(v):'—';
  let active=false,items=[],status=null,query='',side='',sort='newest',limit=300,controller=null,timer=null,selected=null,period='12H',opener=null,closeTimer=null,signature='',openedLink=null,linkedSymbol=null,missingSymbol=null;
  let scope=new URLSearchParams(location.hash.split('?')[1]||'').get('scope')==='history'?'history':'live',snapshot=null,clockOffset=0,loading=true,embedded=false,onReturn=null,reference=null,statsPanel=null;
  const now=()=>Date.now()+clockOffset;
  root.innerHTML=`<header class="cs-heading"><div class="cs-heading-start"><div class="cs-scopes" role="group" aria-label="信号时间范围"><button type="button" data-scope="live" aria-pressed="true">实时 <small data-count="live">—</small></button><button type="button" data-scope="history" aria-pressed="false">历史 <small data-count="history">—</small></button></div><details class="cs-info"><summary aria-label="监控状态与信号说明"><span data-cs-health role="status">同步中</span><span class="cs-info-mark">i</span></summary><div class="cs-info-panel cs-glass"><strong>12H · EMA 20/50 × 1.618</strong><p>云带定方向，刺穿 1.618 后收盘收回，才确认信号。每个币种每个纽约周一区间，仅保留首个合格信号。</p><dl><div><dt>实时</dt><dd>监控启动后及时捕获，最近 12 小时内的确认。</dd></div><div><dt>新确认</dt><dd>收盘后 30 分钟内，在列表中标为新确认。</dd></div><div><dt>历史</dt><dd>历史补录、延迟补录及更早的实时捕获。12H 信号仅记入列表。</dd></div></dl><p>实时仅表示来源与时间，不代表当前仍可入场。入场参考取触及侧 1.618，不是确认收盘价，也不代表已按该价成交。止损按 1.618 参考入场价固定计算：<span data-cs-stop-policy>BTC、ETH 为 1.5%，其他币种为 3%</span>；多单在入场下方，空单在入场上方。不限制整根 K 线相对云带的位置。R 为参考价差盈亏比，未计费用。入场仍等 12H 收盘确认；退出不等收盘，盘中 EMA20/50 一反向即结束，与固定止损取先触发者，转回原方向也不恢复持仓。最高 R 从确认后统计到首次退出；退出参考 R 单独显示，均不代表账户收益。历史只有 12H OHLC 时，同 K 先后未知保留上下界，退出时间只能定位到该 K 区间；开盘已反向则开盘即结束，不计后续高低点。参考框在退出 K 起点截断，区间内精确时刻未知。止损不在风险侧时不显示 R。</p><span data-cs-monitor-detail></span></div></details></div><div class="cs-filters"><label class="cs-search">${icon('search')}<input type="search" aria-label="搜索云带信号" placeholder="搜索合约" autocomplete="off" spellcheck="false"></label><div class="cs-directions" role="group" aria-label="云带信号方向"><button type="button" data-side="" aria-pressed="true">全部</button><button type="button" data-side="1" aria-pressed="false">做多</button><button type="button" data-side="-1" aria-pressed="false">做空</button></div><label class="cs-mobile-direction" title="方向">↕<select aria-label="筛选信号方向"><option value="">全部方向</option><option value="1">做多</option><option value="-1">做空</option></select></label><label class="cs-sort" title="排序">${icon('sort')}<select aria-label="云带信号排序"><option value="newest">最新确认</option><option value="oldest">最早确认</option><option value="rr">目标盈亏比</option></select></label></div></header><div class="cs-error" role="status" hidden></div><div class="cs-grid" aria-label="信号卡片"></div><div class="cs-empty cs-glass" role="status" hidden><span class="cs-empty-icon" aria-hidden="true">${icon('latest')}</span><div><h3>正在同步信号</h3><p></p></div><button type="button" data-cs-history>查看历史 ${icon('arrow')}</button></div><footer class="cs-foot"><span data-cs-count></span><span>参考计划 · 北京时间</span><button type="button" data-cs-more hidden>加载更多</button></footer>`;
  const dialog=document.createElement('dialog');dialog.className='cs-dialog';dialog.id='cloud-signal-dialog';dialog.setAttribute('aria-labelledby','cs-drawer-title');
  dialog.innerHTML=`<section class="cs-drawer"><header class="cs-drawer-head cs-glass"><div class="cs-symbol-heading"><strong id="cs-drawer-title"></strong><span data-cs-side class="cs-side"></span><span data-cs-origin class="cs-origin"></span></div><div class="cs-head-actions"><a data-cs-tv target="_blank" rel="noopener noreferrer" title="在 TradingView 打开">TV ${icon('arrow')}</a><button type="button" data-theme-toggle class="theme-toggle" aria-label="切换浅色或深色" title="切换浅色或深色"></button><button type="button" data-cs-full aria-label="展开全屏图表" title="展开全屏图表">${icon('expand')}</button><button type="button" data-cs-close aria-label="关闭信号图表" title="关闭 · Esc">${icon('close')}</button></div></header><div class="cs-chart-space"><div class="cs-chart"></div><div class="cs-chart-notice cs-glass" role="status" hidden><span></span><button type="button">重新加载</button></div><details class="cs-plan cs-glass" aria-label="信号参考计划"><summary aria-label="展开参考价格与最高 R"><b data-cs-rr></b><span aria-hidden="true">⌄</span></summary><div class="cs-plan-meta"><span data-cs-time></span></div><div class="cs-plan-prices"><div><span>1.618 入场</span><strong data-cs-entry></strong></div><div><span data-cs-stop-label>固定止损</span><strong data-cs-stop></strong></div><div><span>对侧 1.618</span><strong data-cs-target></strong></div></div><p data-cs-excursion></p><p>收盘后确认 · 触价参考，不代表成交</p></details><div class="cs-dock cs-glass"><button type="button" data-cs-grip aria-label="移动工具栏，方向键移动，Home 复位" title="拖动工具栏 · 双击复位">${icon('grip')}</button><div class="cs-periods" role="group" aria-label="图表周期">${['5m','15m','1H','4H','12H','1D'].map(p=>`<button type="button" data-period="${p}" aria-pressed="${p==='12H'}">${p}</button>`).join('')}</div><span class="cs-divider"></span><div class="cs-layer-anchor"><button type="button" data-cs-layers aria-label="显示图层" aria-expanded="false" title="图层">${icon('layers')}</button><div class="cs-layer-menu cs-glass" hidden><strong data-cs-cloud-label>EMA 20/50 · 12H</strong>${[['cloud','EMA 云带'],['range','周一区间'],['fib','1.618 水平'],['plan','交易参考框']].map(([key,label])=>`<label><input type="checkbox" data-layer="${key}" checked>${label}</label>`).join('')}</div></div><button type="button" data-cs-focus aria-label="定位信号" title="定位信号">${icon('focus')}</button><button type="button" data-cs-latest aria-label="最新 K 线" title="最新 K 线">${icon('latest')}</button></div><button type="button" class="cs-history-status cs-glass" data-cs-history-status hidden></button></div></section>`;
  document.body.append(dialog);
  // One chart subtree moves between the modal drawer and the page wallpaper.
  // Its listeners and data owner stay stable; the wallpaper never opens a dialog.
  const surface=dialog.firstElementChild;
  const wallpaper=document.createElement('div');wallpaper.id='cloud-signal-wallpaper';wallpaper.className='rp-wallpaper rp-cloud-wallpaper';wallpaper.setAttribute('aria-label','信号壁纸图表');
  const chart=window.SpikeCloudChart.create(surface.querySelector('.cs-chart'),surface.querySelector('.cs-chart-notice'),path=>{
    const r=path?(path.ambiguous?path.lowerR.toFixed(2)+'–'+path.upperR.toFixed(2):path.lowerR.toFixed(2))+'R':'待载入';
    const reason=path?.exit_state==='cloud_flip'?'盘中转向已结束':path?.state==='stopped'?'已触止损':path?.state==='gap'?'行情未连续':'截至已载入行情';
    const exit=Number.isFinite(path?.exit_r)?' · 退出参考 '+path.exit_r.toFixed(2)+'R':'';
    const timing=path?.exit_at_open?' · 开盘即结束':path?.exit_time_precision==='within_12H_bar'?' · 退出时刻仅定位到 12H 区间':'';
    surface.querySelector('[data-cs-excursion]').textContent='确认后最高 '+r+' · '+reason+exit+timing+(path?.ambiguous?' · 同 K 先后未知':'');
  },{stats:(summary,status)=>statsPanel?.update(summary,status),reference:value=>{reference=value;renderReference();}});
  const stops=window.SpikeCloudStopSettings;
  const settings=window.SpikeCloudChartSettings.create(surface,chart,value=>{chart.stops(value);renderReference();render();},value=>{reference=null;chart.rangeMode(value);renderReference();});
  statsPanel=window.SpikeCloudHistoryPanel.create(surface.querySelector('.cs-drawer-head'),{loadAll:()=>chart.loadAll(),cancel:()=>chart.cancelAll()});
  surface.querySelector('.cs-dock').insertAdjacentHTML('afterbegin','<div class="cs-layout-switch" role="group" aria-label="图表布局" hidden><button type="button" data-cs-range-layout aria-pressed="false">区间</button><button type="button" aria-pressed="true">信号</button></div>');
  chart.rangeMode(settings.rangeMode());
  chart.stops(settings.stops());
  const health=root.querySelector('[data-cs-health]'),grid=root.querySelector('.cs-grid'),empty=root.querySelector('.cs-empty'),error=root.querySelector('.cs-error');
  function card(x){
    const name=x.symbol.replace('-USDT-SWAP',''),long=x.side===1,state=lifecycle(x,now());
    return `<button type="button" class="cs-card${selected?.id===x.id?' is-selected':''}" data-cloud-id="${esc(x.id)}" aria-label="打开 ${esc(name)} ${long?'做多':'做空'} 12H 信号图表"><div class="cs-card-head"><strong>${esc(name)}<small>/ USDT</small></strong><span class="cs-side ${long?'is-long':'is-short'}">${long?'↗ 做多':'↘ 做空'}</span></div><div class="cs-card-meta"><span class="cs-origin${state.fresh?' is-fresh':''}">${state.label}</span><time datetime="${new Date(x.confirmed_ms).toISOString()}">${date(x.confirmed_ms)}</time></div><div class="cs-card-hero"><div><span>入场</span><strong>${price(x.entry)}</strong></div><div class="cs-card-rr" title="目标盈亏比"><span>目标</span><strong>${Number.isFinite(x.planned_rr)?x.planned_rr.toFixed(2)+'<small>R</small>':'—'}</strong></div></div><div class="cs-card-prices"><div><span>止损</span><b class="cs-loss">${price(x.stop)}</b></div><div><span>目标</span><b class="cs-profit">${price(x.target)}</b></div><span class="cs-card-open" aria-hidden="true">›</span></div></button>`;
  }
  function render(){
    const preferences=settings.stops();
    const rows=select(items.map(x=>stops.project(x,preferences)),query,side,sort).filter(x=>lifecycle(x,now()).live===(scope==='live')).slice(0,limit);
    root.querySelector('[data-cs-stop-policy]').textContent=`BTC、ETH 为 ${preferences.btcEth}%，其他币种为 ${preferences.other}%（本机参考设置）`;
    const sig=JSON.stringify(rows.map(x=>[x.id,x.entry,x.planned_rr,x.stop,x.plan_state,x.reference_policy,lifecycle(x,now()).label]));
    if(sig!==signature){const focused=document.activeElement?.dataset?.cloudId;grid.innerHTML=rows.map(card).join('');signature=sig;if(focused)Array.from(grid.children).find(x=>x.dataset.cloudId===focused)?.focus({preventScroll:true});}
    // Selection changes only two borders. Replacing300 glass cards here
    // blocks the drawer's first frame and invalidates every background layer.
    const selectedCard=grid.querySelector('.cs-card.is-selected');
    if(selectedCard?.dataset.cloudId!==selected?.id){
      selectedCard?.classList.remove('is-selected');
      if(selected)Array.from(grid.children).find(node=>node.dataset.cloudId===selected.id)?.classList.add('is-selected');
    }
    root.querySelectorAll('[data-scope]').forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.scope===scope)));
    root.querySelectorAll('[data-count]').forEach(b=>b.textContent=snapshot?.counts?.[b.dataset.count]?.toLocaleString('zh-CN')??'—');
    grid.setAttribute('aria-busy',String(loading));empty.hidden=rows.length>0;
    const filtered=!!query||!!side;
    empty.querySelector('h3').textContent=loading?'正在同步信号':filtered?(missingSymbol===query?query.replace('-USDT-SWAP','')+' 暂无已确认的 12H 信号':'没有匹配的信号'):scope==='live'?'等待下一次 12H 收盘':'暂无历史信号';
    empty.querySelector('p').textContent=loading?'':filtered?'试试其他合约或方向':scope==='live'?(snapshot?.next_close_ms?'下次确认 '+date(snapshot.next_close_ms)+' · 北京时间':'仅在收盘后显示确认信号'):'监控捕获的确认会保留在这里';
    root.querySelector('[data-cs-history]').hidden=scope!=='live'||loading;
    root.querySelector('[data-cs-count]').textContent=loading?'同步中…':snapshot?`${scope==='live'?'最近 12 小时':'历史记录'} · ${rows.length} / ${snapshot.counts?.[scope]??snapshot.total} 条`:'';
    if(dialog.open&&selected&&!embedded){surface.querySelector('[data-cs-origin]').textContent=lifecycle(selected,now()).label;}
    if(status){
      health.textContent=status.state==='error'?'连接异常':!status.worker_alive?'监控未启动':status.state==='scanning'?'同步中':'监控中';
      health.dataset.ready=String(status.worker_alive&&status.state!=='error');
      const detail=`${status.completed||0} / ${status.total||0} 合约就绪${status.waiting?' · '+status.waiting+' 个等待收盘数据':''}${status.errors?' · '+status.errors+' 个待重试':''}`;
      root.querySelector('[data-cs-monitor-detail]').textContent=detail;
      health.title=detail+'\n'+(status.error_samples||[]).map(x=>x.symbol+' · '+x.reason).join('\n');
    }
  }
  function revealPeriod(){
    window.SpikeChartFocus?.revealControl?.(surface.querySelector('.cs-periods [aria-pressed="true"]'));
  }
  function updateLink(){const a=surface.querySelector('[data-cs-tv]');a.href=tvURL(selected.symbol,period,navigator.platform);if(a.href.startsWith('spike-tradingview:'))a.removeAttribute('target');else a.target='_blank';a.setAttribute('aria-label','在 TradingView 打开 '+selected.symbol+' '+period);}
  function setHash(id){if(!location.hash.startsWith('#signals'))return;const p=new URLSearchParams(location.hash.split('?')[1]||'');if(p.get('pane')==='reclaim')return;if(id){p.set('cloud',id);p.delete('cloudSymbol');}else{p.delete('cloud');p.delete('cloudSymbol');}if(scope==='history')p.set('scope','history');else p.delete('scope');history.replaceState(null,'','#signals'+(p.size?'?'+p.toString():''));}
  function renderReference(){
    if(!selected)return;const item=stops.project(reference||selected,settings.stops());
    surface.querySelector('.cs-plan').hidden=!!item.context_only||(item.range_mode||'fib')!==settings.rangeMode();
    const badge=surface.querySelector('[data-cs-side]');badge.hidden=surface.querySelector('.cs-plan').hidden;
    if(!badge.hidden){badge.textContent=item.side===1?'↗ 做多':'↘ 做空';badge.className='cs-side '+(item.side===1?'is-long':'is-short');}
    if(surface.querySelector('.cs-plan').hidden)return;
    surface.querySelector('[data-cs-time]').textContent=date(item.confirmed_ms)+' 确认 · '+(settings.rangeMode()==='monday'?'周一高低点':'1.618');
    const labels=surface.querySelectorAll('.cs-plan-prices>div>span');labels[0].textContent=settings.rangeMode()==='monday'?'周一边界入场':'1.618 入场';labels[2].textContent=settings.rangeMode()==='monday'?'对侧周一边界':'对侧 1.618';
    surface.querySelector('[data-cs-rr]').textContent=Number.isFinite(item.planned_rr)?item.planned_rr.toFixed(2)+'R':item.plan_state==='invalid'?'止损位置无效':'止损计算中';
    surface.querySelector('.cs-plan').title=[item.entry_basis,item.stop_basis,item.plan_reason].filter(Boolean).join(' · ');
    surface.querySelector('[data-cs-stop-label]').textContent='固定止损'+(Number.isFinite(item.stop_pct)?' · '+Number((item.stop_pct*100).toFixed(2))+'%':'');
    for(const key of ['entry','stop','target'])surface.querySelector('[data-cs-'+key+']').textContent=price(item[key]);
  }
  function open(item,source){
    if(embedded)close(true);
    clearTimeout(closeTimer);selected=item;reference=item;period='12H';opener=source||document.activeElement;
    surface.querySelector('.cs-plan').open=false;dialog.classList.remove('is-closing','is-full');surface.querySelector('.cs-layer-menu').hidden=true;
    surface.querySelector('[data-cs-layers]').setAttribute('aria-expanded','false');
    surface.querySelector('[data-cs-full]').setAttribute('aria-label','展开全屏图表');
    surface.querySelector('#cs-drawer-title').textContent=item.symbol.replace('-USDT-SWAP','')+' / USDT';
    const badge=surface.querySelector('[data-cs-side]');badge.hidden=false;badge.textContent=item.side===1?'↗ 做多':'↘ 做空';badge.className='cs-side '+(item.side===1?'is-long':'is-short');
    surface.querySelector('[data-cs-time]').textContent=date(item.confirmed_ms)+' 确认 · '+lifecycle(item,now()).label;
    surface.querySelector('[data-cs-origin]').textContent=lifecycle(item,now()).label;
    surface.querySelector('[data-cs-origin]').title='确认 '+date(item.confirmed_ms)+' · 记录 '+date(item.detected_ms); 
    renderReference();
    surface.querySelectorAll('[data-period]').forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.period==='12H')));
    surface.querySelectorAll('[data-layer]').forEach(b=>b.checked=true);
    surface.querySelector('[data-cs-cloud-label]').textContent='EMA 20/50 · 12H';
    updateLink();if(!dialog.open)dialog.showModal();document.body.classList.add('cloud-drawer-open');resetDock();revealPeriod();chart.show(item);setHash(item.id);openedLink=item.id;render();
    surface.querySelector('[data-cs-close]').focus({preventScroll:true});
  }
  function close(immediate=false){
    if(!dialog.open&&!embedded)return;
    if(embedded){const callback=onReturn;onReturn=null;embedded=false;chart.close();settings.close();statsPanel.setOpen(false);dialog.append(surface);wallpaper.remove();surface.querySelector('.cs-layout-switch').hidden=true;surface.querySelector('[data-cs-full]').hidden=false;callback?.();return;}
    chart.pause();dialog.classList.add('is-closing');setHash(null);
    const finish=()=>{dialog.close();dialog.classList.remove('is-closing');chart.close();document.body.classList.remove('cloud-drawer-open');if(opener?.isConnected)opener.focus({preventScroll:true});};
    clearTimeout(closeTimer);if(immediate||matchMedia('(prefers-reduced-motion: reduce)').matches)finish();else closeTimer=setTimeout(finish,300);
  }
  async function refresh(){
    if(!active||document.hidden||controller)return;const c=new AbortController();controller=c;let timedOut=false;const timeout=setTimeout(()=>{timedOut=true;c.abort();},15000);
    try{const requestedId=new URLSearchParams(location.hash.split('?')[1]||'').get('cloud');const params=new URLSearchParams({limit:String(sort==='rr'?1000:limit),search:query,side:side||'0',sort:sort==='rr'?'newest':sort,scope});if(requestedId)params.set('signal_id',requestedId);const response=await fetch('/api/cloud-signals?'+params,{signal:c.signal,cache:'no-store'});if(!response.ok)throw Error('信号读取失败，请刷新重试');const value=await response.json();if(value.schema!==1||!Array.isArray(value.items))throw Error('信号数据格式不匹配');
      if(!active||controller!==c)return;items=value.items;status=value.status;snapshot=value;clockOffset=value.now_ms-Date.now();loading=false;error.hidden=true;root.querySelector('[data-cs-more]').hidden=Math.min(items.length,limit)>=value.total||limit>=1000;render();
      const linkSymbol=new URLSearchParams(location.hash.split('?')[1]||'').get('cloudSymbol');
      if(linkSymbol&&linkSymbol!==linkedSymbol&&/^[A-Z0-9]{1,32}-USDT-SWAP$/.test(linkSymbol)){
        const historyResponse=await fetch('/api/cloud-signals/history?'+new URLSearchParams({symbol:linkSymbol}),{signal:c.signal,cache:'no-store'});
        if(!historyResponse.ok)throw Error('该币信号读取失败，请重试');
        const historyValue=await historyResponse.json();if(!active||controller!==c)return;
        if(historyValue.symbol!==linkSymbol||!Array.isArray(historyValue.items))throw Error('信号币种不匹配');
        const target=historyValue.items.filter(x=>x.symbol===linkSymbol).sort((a,b)=>b.confirmed_ms-a.confirmed_ms)[0];
        linkedSymbol=linkSymbol;missingSymbol=target?null:linkSymbol;
        if(target)open(target);else render();
      }
      const id=new URLSearchParams(location.hash.split('?')[1]||'').get('cloud');if(id&&id!==openedLink){const found=selectedSignal(value,id);if(found)open(found);}else if(dialog.open)chart.refreshPlans();
    }catch(e){if(active&&(timedOut||e.name!=='AbortError')){error.textContent=timedOut?'信号读取超时，请刷新重试':e.message;error.hidden=false;loading=false;empty.hidden=true;}}
    finally{clearTimeout(timeout);if(controller===c){controller=null;clearTimeout(timer);if(active&&!document.hidden)timer=setTimeout(refresh,30000);}}
  }
  function chooseScope(value){if(scope===value)return;scope=value;items=[];limit=300;loading=true;setHash(selected?.id&&dialog.open?selected.id:null);refilter();}
  function refilter(){controller?.abort();controller=null;clearTimeout(timer);timer=setTimeout(refresh,220);render();}
  root.querySelector('input[type=search]').addEventListener('input',e=>{query=e.target.value;refilter();});
  root.querySelector('.cs-sort select').addEventListener('change',e=>{sort=e.target.value;refilter();});
  root.querySelector('.cs-mobile-direction select').addEventListener('change',e=>{side=e.target.value;root.querySelectorAll('[data-side]').forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.side===side)));refilter();});
  root.addEventListener('click',e=>{const tab=e.target.closest('[data-scope]');if(tab)chooseScope(tab.dataset.scope);if(e.target.closest('[data-cs-history]'))chooseScope('history');const b=e.target.closest('[data-cloud-id]');if(b){const item=items.find(x=>x.id===b.dataset.cloudId);if(item)open(item,b);}const direction=e.target.closest('[data-side]');if(direction){side=direction.dataset.side;root.querySelector('.cs-mobile-direction select').value=side;root.querySelectorAll('[data-side]').forEach(b=>b.setAttribute('aria-pressed',String(b===direction)));refilter();}if(e.target.closest('[data-cs-more]')){limit=1000;refresh();}});
  document.addEventListener('pointerdown',e=>{const info=root.querySelector('.cs-info');if(info.open&&!info.contains(e.target))info.open=false;});
  root.querySelector('.cs-info').addEventListener('keydown',e=>{if(e.key==='Escape'){e.preventDefault();e.currentTarget.open=false;e.currentTarget.querySelector('summary').focus();}});
  dialog.addEventListener('click',e=>{if(e.target===dialog)close();});
  dialog.addEventListener('cancel',e=>{e.preventDefault();if(settings.isOpen())settings.close(true);else close();});
  surface.addEventListener('click',e=>{
    if(e.target.closest('[data-cs-close],[data-cs-range-layout]'))close();
    const b=e.target.closest('[data-period]');if(b){period=b.dataset.period;surface.querySelectorAll('[data-period]').forEach(x=>x.setAttribute('aria-pressed',String(x===b)));surface.querySelector('[data-cs-cloud-label]').textContent='EMA 20/50 · '+period;revealPeriod();chart.period(period);updateLink();}
    if(e.target.closest('[data-cs-focus]'))chart.focus();if(e.target.closest('[data-cs-latest]'))chart.latest();
    if(e.target.closest('[data-cs-full]')){const full=dialog.classList.toggle('is-full');surface.querySelector('[data-cs-full]').setAttribute('aria-label',full?'恢复 85% 图表抽屉':'展开全屏图表');resetDock();}
  });
  surface.addEventListener('change',e=>{if(e.target.dataset.layer)chart.layer(e.target.dataset.layer,e.target.checked);});
  const dock=surface.querySelector('.cs-dock'),grip=dock.querySelector('[data-cs-grip]'),space=surface.querySelector('.cs-chart-space');let drag=null;
  function resetDock(){settings.close();for(const k of ['left','top','bottom','transform'])dock.style[k]='';}
  function moveDock(x,y){settings.close();const r=space.getBoundingClientRect();dock.style.left=Math.max(8,Math.min(r.width-dock.offsetWidth-8,x))+'px';dock.style.top=Math.max(8,Math.min(r.height-dock.offsetHeight-36,y))+'px';dock.style.bottom='auto';dock.style.transform='none';}
  grip.addEventListener('pointerdown',e=>{if(e.button!==0)return;const a=dock.getBoundingClientRect(),b=space.getBoundingClientRect();drag={id:e.pointerId,x:e.clientX,y:e.clientY,left:a.left-b.left,top:a.top-b.top};grip.setPointerCapture(e.pointerId);});
  grip.addEventListener('pointermove',e=>{if(drag?.id===e.pointerId)moveDock(drag.left+e.clientX-drag.x,drag.top+e.clientY-drag.y);});
  for(const key of ['pointerup','pointercancel','lostpointercapture'])grip.addEventListener(key,()=>drag=null);
  grip.addEventListener('dblclick',resetDock);grip.addEventListener('keydown',e=>{const d={ArrowLeft:[-12,0],ArrowRight:[12,0],ArrowUp:[0,-12],ArrowDown:[0,12]}[e.key];if(e.key==='Home'){e.preventDefault();resetDock();}if(d){e.preventDefault();const a=dock.getBoundingClientRect(),b=space.getBoundingClientRect();moveDock(a.left-b.left+d[0],a.top-b.top+d[1]);}});
  window.addEventListener('resize',()=>{if(dialog.open||embedded)resetDock();});
  document.addEventListener('visibilitychange',()=>{if(document.hidden){clearTimeout(timer);controller?.abort();if(dialog.open||embedded)chart.pause();}else if(active||embedded){if(active)refresh();if(dialog.open||embedded)chart.resume();}});
  function openEmbedded(symbol,back,mount,record=null){
    if(!mount||!/^[A-Z0-9]{1,32}-USDT-SWAP$/.test(symbol))return;
    if(dialog.open||embedded){if(embedded)onReturn=null;close(true);}clearTimeout(closeTimer);embedded=true;onReturn=back;selected=record||{symbol,context_only:true};reference=record;period='12H';
    dialog.classList.remove('is-closing','is-full');mount.append(wallpaper);wallpaper.append(surface);surface.querySelector('.cs-layout-switch').hidden=false;surface.querySelector('[data-cs-full]').hidden=true;
    surface.querySelector('#cs-drawer-title').textContent=symbol.replace('-USDT-SWAP','')+' / USDT';surface.querySelector('[data-cs-side]').textContent='';surface.querySelector('[data-cs-origin]').textContent='实时行情';
    surface.querySelector('.cs-plan').hidden=true;surface.querySelector('.cs-plan').open=false;surface.querySelectorAll('[data-period]').forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.period==='12H')));
    surface.querySelector('[data-cs-cloud-label]').textContent='EMA 20/50 · 12H';surface.querySelectorAll('[data-layer]').forEach(b=>b.checked=true);
    updateLink();resetDock();revealPeriod();
    if(record){renderReference();chart.show(record);}else chart.showSymbol(symbol);
  }
  window.SpikeCloudSignals={refresh,openEmbedded,closeEmbedded(){if(embedded)close(true);},setActive(value){if(active===value)return;active=value;root.hidden=!value;document.body.classList.toggle('cloud-signals-active',value);if(value){const p=new URLSearchParams(location.hash.split('?')[1]||'');scope=p.get('scope')==='history'?'history':'live';query=p.get('cloudSymbol')||'';root.querySelector('input[type=search]').value=query;linkedSymbol=null;openedLink=null;render();refresh();}else{clearTimeout(timer);controller?.abort();if(!embedded)close(true);}}};
})();
