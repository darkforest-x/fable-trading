/* Read-only wallpaper chart. Keeps bounded live snapshots and demand-only history.
 * Monday anchors come from the monitor; candles must match its symbol, NY week,
 * and closed-bar boundary. A separate display-only feed supplies the live tip.
 * No trading, alert writes, or synthetic candles.
 */
(() => {
  'use strict';
  const finite=Number.isFinite,PERIODS={'1m':60000,'5m':300000,'15m':900000,'30m':1800000,'1H':3600000,'4H':14400000,'12H':43200000,'1D':86400000};
  const weekOf=ms=>new Intl.DateTimeFormat('sv-SE',{timeZone:'America/New_York',year:'numeric',month:'2-digit',day:'2-digit'}).format(new Date(ms));
  function levels(row,anchors){
    const h=row.high,l=row.low,r=h-l;
    if(!finite(h)||!finite(l)||r<=0)return [];
    return [
      {label:'上 1.618',price:row.upper,kind:'fib',color:'#c93248',lineWidth:2,lineStyle:0},
      {label:'周一高',price:h,kind:'range',color:'#528cc1',lineStyle:0},
      {label:'上 0.705',price:l+.705*r,kind:'fib',color:'#408ddd',axisLabelVisible:false},
      {label:'上 0.618',price:l+.618*r,kind:'fib',color:'#408ddd',axisLabelVisible:false},
      {label:'中点 0.5',price:l+.5*r,kind:'range',color:'#718096',lineStyle:2},
      {label:'下 0.618',price:h-.618*r,kind:'fib',color:'#8a6cbd',axisLabelVisible:false},
      {label:'下 0.705',price:h-.705*r,kind:'fib',color:'#8a6cbd',axisLabelVisible:false},
      {label:'周一低',price:l,kind:'range',color:'#528cc1',lineStyle:0},
      {label:'下 1.618',price:row.lower,kind:'fib',color:'#087f69',lineWidth:2,lineStyle:0},
    ].filter(x=>finite(x.price)).map(level=>({...level,
      // Inline names and prices stay on the ray; the axis keeps room for OHLC.
      inlinePriceVisible:true,inlineNameVisible:true,axisLabelVisible:false,
      ...(anchors?{start_ms:['上 1.618','周一高'].includes(level.label)?anchors.high_ms:['周一低','下 1.618'].includes(level.label)?anchors.low_ms:Math.max(anchors.high_ms,anchors.low_ms)}:{}),
    }));
  }
  function prepare(context,row,week,period='15m',anchors){
    if(!PERIODS[period]||context.bar!==period||(['12H','1D'].includes(period)&&context.native_bar!==period+'utc'))throw Error('K 线周期不匹配');
    if(context.symbol!==row.symbol||context.timezone!=='America/New_York')throw Error('图表币种或时区不匹配');
    if(!context.candles?.length)return {...context,levels:levels(row)};
    const start=context.monday?.start_ms;
    if(!finite(start)||weekOf(start)!==week)throw Error('周一区间正在切换，请稍后重试');
    const boundary=context.last_close_ms;
    if(!finite(boundary))throw Error('K 线收盘边界缺失');
    let previous=-Infinity;
    const candles=context.candles;
    for(const b of candles){
      if(![b.time,b.open,b.high,b.low,b.close].every(finite)||b.time<=previous||b.time+PERIODS[period]>boundary||b.time%PERIODS[period]!==0||b.high<Math.max(b.open,b.close,b.low)||b.low>Math.min(b.open,b.close))throw Error('K 线数据不完整，请稍后重试');
      previous=b.time;
    }
    const prior=anchors?.display_previous===true;
    if(anchors&&(anchors.symbol!==row.symbol||anchors.state!=='ready'||(prior
      ? anchors.current_week!==week||anchors.expires_ms!==start||anchors.end_ms>start
      : anchors.week!==week)||![anchors.high_ms,anchors.low_ms].every(t=>finite(t)&&t>=anchors.start_ms&&t<anchors.end_ms)))throw Error('周一区间锚点不匹配');
    const range=anchors||row;
    return {...context,candles,levels:levels(range,anchors),level_window:{start_ms:anchors?.start_ms??start,end_ms:anchors?.expires_ms??context.monday.expires_ms},monday:{...context.monday,high:range.high,low:range.low,complete:!prior&&range.state!=='developing'}};
  }
  function withWeeks(value,anchors,week){
    const ranges=new Map((value.weekly_ranges||[]).filter(w=>w.symbol===value.symbol&&w.state==='ready').map(w=>[w.week,w]));
    if(anchors)ranges.set(anchors.week,anchors);
    if(!ranges.size)return {...value,levels:[]};
    // Stitch edc75895cf3640c090680fdeb1e1fd18: old weeks keep their lines only.
    // Label the API-selected display range, including Monday's prior-week fallback.
    const values=[...ranges.values()],labelWeek=anchors?.week||week;
    return {...value,levels:values.flatMap(w=>levels(w,w).map(l=>({...l,end_ms:w.expires_ms,historical:w.week!==week,week:w.week,
      inlinePriceVisible:w.week===labelWeek,inlineNameVisible:w.week===labelWeek}))),
      level_window:{start_ms:Math.min(...values.map(w=>w.start_ms)),end_ms:Math.max(...values.map(w=>w.expires_ms))}};
  }
  // The display contains closed candles. Refresh at their next close, using
  // server time so a user's clock cannot postpone updates. Lagging tails retry.
  function refreshDelay(context,period){
    if(context.loading)return 1500;
    if(context.error||!context.candles?.length)return 10000;
    const step=PERIODS[period],now=context.now_ms;
    if(!finite(now))return 15000;
    const boundary=Math.floor(now/step)*step;
    if(context.last_close_ms<boundary)return 3000;
    return Math.max(1000,boundary+step-now+1500);
  }
  if(typeof module!=='undefined')module.exports={levels,prepare,withWeeks,PERIODS,refreshDelay};
  if(typeof document==='undefined')return;
  const shared=window.SpikePublicChartCache,cache=shared?.candles||new Map(),levelCache=shared?.levels||new Map();
  function create({decorate}={}){
  const view={decorate,bars:720,initialBars:160,range:true,fib:true,fitAll:false,showLevels:false,mondayBand:true,wallpaper:true};
  let target=null,controller=null,timer=null,version=0,paused=false;
  function pause(){paused=true;version++;clearTimeout(timer);controller?.abort();controller=null;target?.feed?.stop();target?.history?.pause();if(target)target.feed=null;}
  function stop(){pause();if(target){window.SpikeRangeChart?.unmount(target.mount);target.host.replaceChildren?.();}target=null;}
  function draw(context,error=''){
    if(!target)return;
    try{
      let value=prepare(context,target.row,target.week,target.period,target.levels);
      value=withWeeks(value,target.levels,target.week);
      value=window.SpikeMondayCandleFeed?.mergeDisplay(value,target.live,target.period)||value;
      target.context=context;
      value.paperTrade=target.trade;value.paperTrades=target.trades;
      if(context.display_cached)value.stale=true;
      if(!target.internal)value.levels=value.levels.filter(l=>['上 1.618','周一高','周一低','下 1.618'].includes(l.label));
      target.instance=window.SpikeRangeChart.mount(target.mount,value,{...view,bars:window.SpikeChartHistory?.full(target.row.symbol)?Infinity:720,ma:target.ma,mondayBand:target.period!=='1D',onVisibleRange:instance=>target?.history?.observe(instance)},error);
      message(error||target.levelError||(context.error?'K 线更新失败：'+context.error:context.loading?'正在同步此合约 K 线…':!value.candles?.length?'暂无此周期 K 线，请稍后重试':target.live?.error||''));
    }catch(e){message(e.message);window.SpikeRangeChart.unmount(target.mount);}
  }
  function message(value){if(!target)return;target.state.textContent=value;target.caption.hidden=!value;}
  function startFeed(){
    if(!target||target.feed||document.hidden||paused||!window.SpikeMondayCandleFeed)return;
    const current=target;current.live=null;
    current.feed=window.SpikeMondayCandleFeed.create({symbol:current.row.symbol,period:current.period,
      onChange:value=>{if(target!==current||paused)return;current.live=value;if(current.context)draw(current.context);},
      onReconnect:()=>{if(target===current&&!paused)refresh();}});
    // A cached high-timeframe snapshot can be hours old. Never reset the live
    // clock to that historical receipt; fresh REST responses synchronize it.
    current.feed.start();
  }
  async function refresh(){
    if(!target||controller||document.hidden)return;
    clearTimeout(timer);const current=target,token=version,c=new AbortController();controller=c;
    const timeout=setTimeout(()=>c.abort(),15000);let delay=15000;
    const anchorRequest=current.levels&&!current.levels.display_previous?null:fetch('/api/monday-fib/levels?symbol='+encodeURIComponent(current.row.symbol),{signal:c.signal,cache:'no-store'}).then(async r=>{
      if(!r.ok)throw Error('周一锚点读取失败');const a=await r.json();
      if(a.symbol!==current.row.symbol||(a.current_week||a.week)!==current.week)throw Error('周一锚点待同步');
      if(a.state==='developing')return a;
      if(a.state!=='ready'||![a.high_ms,a.low_ms].every(t=>finite(t)&&t>=a.start_ms&&t<a.end_ms)||!['high','low','upper','lower'].every(k=>finite(a[k])))throw Error('周一锚点待同步');
      return a;
    }).catch(()=>null);
    try{
      const warming=window.SpikeStartup?.take(current.row.symbol+'|'+current.period,c.signal),warm=warming?await warming:null;
      let value=warm?.value;
      if(!warm){const r=await fetch('/api/monday-fib/candles?symbol='+encodeURIComponent(current.row.symbol)+'&period='+encodeURIComponent(current.period)+'&wait_ms=8000',{cache:'no-store',credentials:'same-origin',signal:c.signal});value=await r.json();if(!r.ok)throw Error(typeof value.detail==='string'?value.detail:'图表读取失败');}
      if(token!==version||target!==current)return;
      prepare(value,current.row,current.week,current.period,current.levels);
      current.feed?.setClock(value.now_ms+(warm?Date.now()-warm.receivedAt:0));
      delay=Math.max(0,refreshDelay(value,current.period)-(warm?Date.now()-warm.receivedAt:0));
      if(current.levels?.display_previous)delay=Math.min(delay,60000);
      if(!value.candles?.length&&current.context?.candles?.length)value={...current.context,display_cached:true,loading:false,error:value.error||null};
      value=current.history?.merge(value)||value;
      cache.delete(current.key);cache.set(current.key,{value,due:Date.now()+delay});while(cache.size>12)cache.delete(cache.keys().next().value);
      if(!value.display_cached)shared?.saveCandles({...value,candles:value.candles.slice(-720)});
      draw(value);
      if(anchorRequest){const anchors=await anchorRequest;if(token!==version||target!==current)return;
        current.levels=anchors?.state==='ready'?anchors:current.levels;current.levelError=anchors?'':'周一锚点暂不可用，稍后重试';
        if(anchors?.state==='ready'){shared?.saveLevels(anchors);levelCache.set(current.row.symbol+'|'+current.week,anchors);while(levelCache.size>32)levelCache.delete(levelCache.keys().next().value);}else delay=10000;
        value=current.history?.merge(value)||value;
        cache.set(current.key,{value,due:Date.now()+delay});
        draw(value);}
    }catch(e){if(token===version&&target===current){message((e.name==='AbortError'?'图表读取超时':e.message)+' · 可重新加载');delay=10000;}}
    finally{clearTimeout(timeout);if(controller===c)controller=null;if(token===version&&target===current&&!document.hidden)timer=setTimeout(refresh,delay);}
  }
  function show(host,row,week,{internal=false,ma=false,period='15m',trade=null,trades=[],anchors=null}={}){
    if(!PERIODS[period])period='15m';
    const key=row.symbol+'|'+week+'|'+period;
    if(anchors?.symbol===row.symbol&&(anchors.current_week||anchors.week)===week&&anchors.state==='ready'){levelCache.set(row.symbol+'|'+week,anchors);shared?.saveLevels(anchors);}
    if(target?.host===host&&target.key===key){
      const changed=target.internal!==internal||target.ma!==ma||JSON.stringify(target.trade)!==JSON.stringify(trade)||JSON.stringify(target.trades)!==JSON.stringify(trades)||['high','low','upper','lower'].some(k=>target.row[k]!==row[k]);
      target.row=row;target.levels=levelCache.get(row.symbol+'|'+week)||target.levels;target.trade=trade;target.trades=trades;target.internal=internal;target.ma=ma;if(changed&&target.context)draw(target.context);if(paused){paused=false;target.history?.resume();startFeed();const previous=cache.get(key);if(previous&&previous.due>Date.now())timer=setTimeout(refresh,previous.due-Date.now());else refresh();}return;
    }
    const reuse=target?.host===host?target:null;
    if(reuse){version++;clearTimeout(timer);controller?.abort();controller=null;target.feed?.stop();target.history?.pause();}else stop();
    if(!host)return;paused=false;
    if(!reuse)host.innerHTML='<div class="mf-chart-caption"><p role="status">正在加载区间 K 线…</p><button type="button" data-mf-chart-retry>重新加载</button></div><div class="mf-chart-mount"></div><div class="mf-history-status"><button type="button" data-mf-history-retry disabled aria-live="polite"></button><small></small></div>';
    target={host,row,week,key,internal,ma,period,trade,trades,levels:levelCache.get(row.symbol+'|'+week),state:host.querySelector('[role=status]'),caption:host.querySelector('.mf-chart-caption'),mount:host.querySelector('.mf-chart-mount')};
    if(!reuse)host.querySelector('[data-mf-chart-retry]').addEventListener('click',()=>refresh());
    const owner=target;
    target.history=window.SpikeChartHistory?.create({symbol:row.symbol,period,
      changed:value=>{if(target!==owner||paused)return;const old=cache.get(key);cache.set(key,{value,due:old?.due||0});draw(value);},
      status:value=>{if(target!==owner)return;const button=host.querySelector('[data-mf-history-retry]');if(!button)return;button.textContent=value.text;button.disabled=!value.retry;host.querySelector('.mf-history-status small').textContent=value.count?`${value.count.toLocaleString()} 根`:'';}});
    if(!reuse)host.querySelector('[data-mf-history-retry]')?.addEventListener('click',()=>target?.history?.retry());
    const previous=cache.get(key);
    if(previous){const value=target.history?.merge(previous.value)||previous.value;draw(previous.due>Date.now()?value:{...value,display_cached:true});}
    else if(reuse){
      // Clear the previous identity synchronously while keeping its canvas and
      // per-symbol viewport cache. Never label the old candles as a new symbol.
      target.instance=window.SpikeRangeChart.mount(target.mount,{symbol:row.symbol,bar:period,candles:[],levels:[]},view);
      message('正在加载区间 K 线…');
    }
    const current=target;
    if(!current.levels)shared?.restoreLevels?.(row.symbol+'|'+week).then(anchors=>{
      if(anchors&&target===current&&!paused&&!current.levels&&anchors.symbol===row.symbol&&anchors.week===week&&['high','low','upper','lower'].every(k=>finite(anchors[k])&&Math.abs(anchors[k]-row[k])<=Math.max(1e-12,Math.abs(anchors[k])*1e-8))){
        current.levels=anchors;levelCache.set(row.symbol+'|'+week,anchors);while(levelCache.size>32)levelCache.delete(levelCache.keys().next().value);if(current.context)draw(current.context);
      }
    });
    if(!previous)shared?.restoreCandles(key).then(value=>{
      if(value&&target===current&&!paused&&!current.context?.candles?.length){try{prepare(value,row,week,period,current.levels);draw(current.history?.merge({...value,display_cached:true})||{...value,display_cached:true})}catch{/* Invalid local history cannot paint. */}}
    });
    startFeed();
    if(target.levels&&previous&&Date.now()<previous.due&&!previous.value.loading&&!previous.value.error)timer=setTimeout(refresh,previous.due-Date.now());else refresh();
  }
  function setInternal(value){if(!target)return;target.internal=value;if(target.context)draw(target.context);}
  function setMa(value){if(!target)return;target.ma=!!value;if(target.context)draw(target.context);}
  function fit(){const chart=target?.instance?.chart;if(chart){chart.priceScale('right').applyOptions({autoScale:true});chart.timeScale().fitContent();}}
  function latest(){
    const instance=target?.instance,chart=instance?.chart,n=instance?.rows?.length;if(!chart||!n)return;
    const scale=chart.timeScale(),range=scale.getVisibleLogicalRange(),span=range?range.to-range.from:view.initialBars,to=n-1+10;
    chart.priceScale('right').applyOptions({autoScale:true});scale.setVisibleLogicalRange({from:to-span,to});
  }
  function zoom(factor){
    const scale=target?.instance?.chart?.timeScale(),range=scale?.getVisibleLogicalRange();if(!range||!finite(factor)||factor<=0)return;
    const span=Math.max(8,Math.min((target.instance?.rows?.length||view.bars)+20,(range.to-range.from)*factor));scale.setVisibleLogicalRange({from:range.to-span,to:range.to});
  }
  return {show,hide:stop,pause,setInternal,setMa,fit,latest,zoom};
  }
  window.SpikeMondayFibChart=Object.assign(create(),{create});
})();
