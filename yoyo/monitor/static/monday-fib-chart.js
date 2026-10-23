/* Read-only wallpaper chart. Reuses the bounded OKX cache and shared renderer.
 * Monday anchors come from the monitor; candles must match its symbol, NY week,
 * and closed-bar boundary. No trading, alert writes, or synthetic candles.
 */
(() => {
  'use strict';
  const finite=Number.isFinite,PERIODS={'1m':60000,'5m':300000,'15m':900000,'30m':1800000,'1H':3600000,'4H':14400000,'1D':86400000};
  const weekOf=ms=>new Intl.DateTimeFormat('sv-SE',{timeZone:'America/New_York',year:'numeric',month:'2-digit',day:'2-digit'}).format(new Date(ms));
  function levels(row){
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
    ].filter(x=>finite(x.price));
  }
  function prepare(context,row,week,period='15m'){
    if(!PERIODS[period]||context.bar!==period||(period==='1D'&&context.native_bar!=='1Dutc'))throw Error('K 线周期不匹配');
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
    return {...context,candles,levels:levels(row),monday:{...context.monday,high:row.high,low:row.low,complete:true}};
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
  if(typeof module!=='undefined')module.exports={levels,prepare,PERIODS,refreshDelay};
  if(typeof document==='undefined')return;
  const view={bars:720,initialBars:160,range:true,fib:true,fitAll:false,showLevels:false,mondayBand:true,wallpaper:true,candleColors:{up:'#169873',down:'#d7355d'}};
  let target=null,controller=null,timer=null,version=0,paused=false;
  const cache=new Map();
  function pause(){paused=true;version++;clearTimeout(timer);controller?.abort();controller=null;}
  function stop(){pause();if(target){window.SpikeRangeChart?.unmount(target.mount);target.host.replaceChildren?.();}target=null;}
  function draw(context,error=''){
    if(!target)return;
    try{
      const value=prepare(context,target.row,target.week,target.period);
      target.context=context;
      if(!target.internal)value.levels=value.levels.filter(l=>['上 1.618','周一高','周一低','下 1.618'].includes(l.label));
      target.instance=window.SpikeRangeChart.mount(target.mount,value,{...view,mondayBand:target.period!=='1D'},error);
      message(error||(context.error?'K 线更新失败：'+context.error:context.loading?'正在同步此合约 K 线…':!value.candles?.length?'暂无此周期 K 线，请稍后重试':''));
    }catch(e){message(e.message);window.SpikeRangeChart.unmount(target.mount);}
  }
  function message(value){if(!target)return;target.state.textContent=value;target.caption.hidden=!value;}
  async function refresh(){
    if(!target||controller||document.hidden)return;
    clearTimeout(timer);const current=target,token=version,c=new AbortController();controller=c;
    const timeout=setTimeout(()=>c.abort(),12000);let delay=15000;
    try{
      const warming=window.SpikeStartup?.take(current.row.symbol+'|'+current.period,c.signal),warm=warming?await warming:null;
      let value=warm?.value;
      if(!warm){const r=await fetch('/api/monday-fib/candles?symbol='+encodeURIComponent(current.row.symbol)+'&period='+encodeURIComponent(current.period)+'&wait_ms=8000',{cache:'no-store',credentials:'same-origin',signal:c.signal});value=await r.json();if(!r.ok)throw Error(typeof value.detail==='string'?value.detail:'图表读取失败');}
      if(token!==version||target!==current)return;
      prepare(value,current.row,current.week,current.period);
      delay=Math.max(0,refreshDelay(value,current.period)-(warm?Date.now()-warm.receivedAt:0));
      cache.delete(current.key);cache.set(current.key,{value,due:Date.now()+delay});while(cache.size>6)cache.delete(cache.keys().next().value);
      draw(value);
    }catch(e){if(token===version&&target===current){message((e.name==='AbortError'?'图表读取超时':e.message)+' · 可重新加载');delay=10000;}}
    finally{clearTimeout(timeout);if(controller===c)controller=null;if(token===version&&target===current&&!document.hidden)timer=setTimeout(refresh,delay);}
  }
  function show(host,row,week,{internal=false,period='15m'}={}){
    if(!PERIODS[period])period='15m';
    const key=row.symbol+'|'+week+'|'+period;
    if(target?.host===host&&target.key===key){
      const changed=target.internal!==internal||['high','low','upper','lower'].some(k=>target.row[k]!==row[k]);
      target.row=row;target.internal=internal;if(changed&&target.context)draw(target.context);if(paused){paused=false;const previous=cache.get(key);if(previous&&previous.due>Date.now())timer=setTimeout(refresh,previous.due-Date.now());else refresh();}return;
    }
    const reuse=target?.host===host?target:null;
    if(reuse){version++;clearTimeout(timer);controller?.abort();controller=null;}else stop();
    if(!host)return;paused=false;
    if(!reuse)host.innerHTML='<div class="mf-chart-caption"><p role="status">正在加载区间 K 线…</p><button type="button" data-mf-chart-retry>重新加载</button></div><div class="mf-chart-mount"></div>';
    target={host,row,week,key,internal,period,state:host.querySelector('[role=status]'),caption:host.querySelector('.mf-chart-caption'),mount:host.querySelector('.mf-chart-mount')};
    if(!reuse)host.querySelector('[data-mf-chart-retry]').addEventListener('click',()=>refresh());
    const previous=cache.get(key);
    if(previous)draw(previous.value);
    else if(reuse){
      // Clear the previous identity synchronously while keeping its canvas and
      // per-symbol viewport cache. Never label the old candles as a new symbol.
      target.instance=window.SpikeRangeChart.mount(target.mount,{symbol:row.symbol,bar:period,candles:[],levels:[]},view);
      message('正在加载区间 K 线…');
    }
    if(previous&&Date.now()<previous.due&&!previous.value.loading&&!previous.value.error)timer=setTimeout(refresh,previous.due-Date.now());else refresh();
  }
  function setInternal(value){if(!target)return;target.internal=value;if(target.context)draw(target.context);}
  function fit(){const chart=target?.instance?.chart;if(chart){chart.priceScale('right').applyOptions({autoScale:true});chart.timeScale().fitContent();}}
  function latest(){
    const instance=target?.instance,chart=instance?.chart,n=instance?.rows?.length;if(!chart||!n)return;
    const scale=chart.timeScale(),range=scale.getVisibleLogicalRange(),span=range?range.to-range.from:view.initialBars,to=n-1+10;
    chart.priceScale('right').applyOptions({autoScale:true});scale.setVisibleLogicalRange({from:to-span,to});
  }
  function zoom(factor){
    const scale=target?.instance?.chart?.timeScale(),range=scale?.getVisibleLogicalRange();if(!range||!finite(factor)||factor<=0)return;
    const span=Math.max(8,Math.min(view.bars+20,(range.to-range.from)*factor));scale.setVisibleLogicalRange({from:range.to-span,to:range.to});
  }
  window.SpikeMondayFibChart={show,hide:stop,pause,setInternal,fit,latest,zoom};
})();
