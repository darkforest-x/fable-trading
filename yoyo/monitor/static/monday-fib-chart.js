/* Read-only row chart. Reuses the bounded OKX context cache and shared renderer.
 * Monday anchors come from the monitor; candles must match its symbol, NY week,
 * and closed-bar boundary. No trading, alert writes, or synthetic candles.
 */
(() => {
  'use strict';
  const finite=Number.isFinite,STEP=900000;
  const weekOf=ms=>new Intl.DateTimeFormat('sv-SE',{timeZone:'America/New_York',year:'numeric',month:'2-digit',day:'2-digit'}).format(new Date(ms));
  function levels(row){
    const h=row.high,l=row.low,r=h-l;
    if(!finite(h)||!finite(l)||r<=0)return [];
    return [
      {label:'上 1.618',price:row.upper,kind:'fib',color:'#c93248',lineWidth:2},
      {label:'周一高',price:h,kind:'range',color:'#334155'},
      {label:'上 0.705',price:l+.705*r,kind:'fib',color:'#408ddd',axisLabelVisible:false},
      {label:'上 0.618',price:l+.618*r,kind:'fib',color:'#408ddd',axisLabelVisible:false},
      {label:'中点 0.5',price:l+.5*r,kind:'range',color:'#718096',lineStyle:2},
      {label:'下 0.618',price:h-.618*r,kind:'fib',color:'#8a6cbd',axisLabelVisible:false},
      {label:'下 0.705',price:h-.705*r,kind:'fib',color:'#8a6cbd',axisLabelVisible:false},
      {label:'周一低',price:l,kind:'range',color:'#334155'},
      {label:'下 1.618',price:row.lower,kind:'fib',color:'#087f69',lineWidth:2},
    ].filter(x=>finite(x.price));
  }
  function prepare(context,row,week){
    if(context.symbol!==row.symbol||context.timezone!=='America/New_York')throw Error('图表币种或时区不匹配');
    if(!context.candles?.length)return {...context,levels:levels(row)};
    const start=context.monday?.start_ms;
    if(!finite(start)||weekOf(start)!==week)throw Error('周一区间正在切换，请稍后重试');
    const boundary=context.last_close_ms;
    if(!finite(boundary))throw Error('K 线收盘边界缺失');
    let previous=-Infinity;
    const candles=context.candles.filter(x=>x.time>=start);
    for(const b of candles){
      if(![b.time,b.open,b.high,b.low,b.close].every(finite)||b.time<=previous||b.time+STEP>boundary||b.high<Math.max(b.open,b.close,b.low)||b.low>Math.min(b.open,b.close))throw Error('K 线数据不完整，请稍后重试');
      previous=b.time;
    }
    return {...context,candles,levels:levels(row),monday:{...context.monday,high:row.high,low:row.low,complete:true}};
  }
  if(typeof module!=='undefined')module.exports={levels,prepare};
  if(typeof document==='undefined')return;
  const view={bars:672,range:true,fib:true,fitAll:true,showLevels:true,mondayBand:true,candleColors:{up:'#087f69',down:'#d7355d'}};
  let target=null,controller=null,timer=null,version=0;
  const cache=new Map();
  function stop(){version++;clearTimeout(timer);controller?.abort();controller=null;if(target)window.SpikeRangeChart?.unmount(target.mount);target=null;}
  function draw(context,error=''){
    if(!target)return;
    try{
      const value=prepare(context,target.row,target.week);
      window.SpikeRangeChart.mount(target.mount,value,view,error);
      target.state.textContent=error||(context.error?'数据更新失败：'+context.error:context.loading?'正在同步此合约 K 线…':!value.candles?.length?'暂无本周 K 线，请稍后重试':'15m 已收盘 K 线 · 从纽约周一开始；实时触价可能发生在尚未收盘的 K 线内。');
    }catch(e){target.state.textContent=e.message;window.SpikeRangeChart.unmount(target.mount);}
  }
  async function refresh(){
    if(!target||controller||document.hidden)return;
    clearTimeout(timer);const current=target,token=version,c=new AbortController();controller=c;
    const timeout=setTimeout(()=>c.abort(),12000);let delay=60000;
    try{
      const r=await fetch('/api/market-sync/context?zone=America%2FNew_York&symbol='+encodeURIComponent(current.row.symbol),{cache:'no-store',credentials:'same-origin',signal:c.signal});
      const value=await r.json();if(!r.ok)throw Error(typeof value.detail==='string'?value.detail:'图表读取失败');
      if(token!==version||target!==current)return;
      prepare(value,current.row,current.week);
      cache.delete(current.key);cache.set(current.key,{value,at:Date.now()});while(cache.size>6)cache.delete(cache.keys().next().value);
      draw(value);if(value.loading)delay=3000;else if(value.error||!value.candles?.length)delay=10000;
    }catch(e){if(token===version&&target===current){current.state.textContent=(e.name==='AbortError'?'图表读取超时':e.message)+' · 可点击重新加载';delay=10000;}}
    finally{clearTimeout(timeout);if(controller===c)controller=null;if(token===version&&target===current&&!document.hidden)timer=setTimeout(refresh,delay);}
  }
  function show(host,row,week){
    const key=row.symbol+'|'+week;
    if(target?.host===host&&target.key===key){target.row=row;return;}
    stop();if(!host)return;
    host.innerHTML='<div class="mf-chart-caption"><p role="status">正在加载区间 K 线…</p><button type="button" data-mf-chart-retry>重新加载</button></div><div class="mf-chart-mount"></div>';
    target={host,row,week,key,state:host.querySelector('[role=status]'),mount:host.querySelector('.mf-chart-mount')};
    host.querySelector('[data-mf-chart-retry]').addEventListener('click',()=>refresh());
    const previous=cache.get(key);if(previous)draw(previous.value);
    if(previous&&Date.now()-previous.at<60000&&!previous.value.loading)timer=setTimeout(refresh,60000-(Date.now()-previous.at));else refresh();
  }
  window.SpikeMondayFibChart={show,hide:stop};
})();
