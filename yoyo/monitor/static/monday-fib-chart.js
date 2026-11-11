/* Read-only wallpaper chart. Keeps bounded live snapshots and demand-only history.
 * Monday anchors come from the monitor; candles must match its symbol, NY week,
 * and closed-bar boundary. A separate display-only feed supplies the live tip.
 * No trading, alert writes, or synthetic candles.
 */
(() => {
  'use strict';
  const finite=Number.isFinite,PERIODS={'1m':60000,'2m':120000,'3m':180000,'5m':300000,'15m':900000,'30m':1800000,'1H':3600000,'4H':14400000,'12H':43200000,'1D':86400000};
  const MONTH_COLORS={upper:'#b86542',lower:'#367e88'};
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
    return {...value,levels:values.flatMap(w=>levels(w,w).map(l=>({...l,start_ms:finite(l.start_ms)?l.start_ms:w.end_ms,end_ms:w.expires_ms,historical:w.week!==week,week:w.week,
      inlinePriceVisible:w.week===labelWeek,inlineNameVisible:w.week===labelWeek}))),
      level_window:{start_ms:Math.min(...values.map(w=>w.start_ms)),end_ms:Math.max(...values.map(w=>w.expires_ms))}};
  }
  function validateMonthlyRanges(data,symbol){
    if(!data||data.symbol!==symbol||!Array.isArray(data.ranges))throw Error('月首周锚点与所选合约不匹配');
    let previous=-Infinity;
    for(const range of data.ranges){
      if(!range||typeof range.month!=='string'||!/^\d{4}-(0[1-9]|1[0-2])$/.test(range.month)||
          ![range.start_ms,range.ready_ms,range.expires_ms,range.high,range.low,range.upper,range.lower].every(finite)||
          !(range.start_ms<range.ready_ms&&range.ready_ms<range.expires_ms)||range.start_ms<previous||
          range.high<=range.low||range.upper<=range.high||range.lower>=range.low||range.lower<=0||range.upper<=0)
        throw Error('月首周锚点数据不完整或顺序错误');
      previous=range.start_ms;
    }
    return data.ranges;
  }
  function monthlyLines(ranges,fromMs=-Infinity,toMs=Infinity){
    const visible=(Array.isArray(ranges)?ranges:[]).filter(r=>finite(r?.ready_ms)&&finite(r?.expires_ms)&&r.ready_ms<toMs&&r.expires_ms>fromMs);
    const focus=visible.at(-1);
    return visible.flatMap(range=>[
      {label:'月首周高',price:range.high,kind:'range',color:'#738294',lineWidth:1,lineStyle:2,start_ms:range.ready_ms,end_ms:range.expires_ms,inlinePriceVisible:range===focus,inlineNameVisible:range===focus,axisLabelVisible:false,startMarkerVisible:false,monthly:true,historical:range!==focus,month:range.month},
      {label:'月首周上 1.618',price:range.upper,kind:'fib',color:MONTH_COLORS.upper,lineWidth:1,lineStyle:2,start_ms:range.ready_ms,end_ms:range.expires_ms,inlinePriceVisible:range===focus,inlineNameVisible:range===focus,axisLabelVisible:false,startMarkerVisible:false,monthly:true,historical:range!==focus,month:range.month},
      {label:'月首周下 1.618',price:range.lower,kind:'fib',color:MONTH_COLORS.lower,lineWidth:1,lineStyle:2,start_ms:range.ready_ms,end_ms:range.expires_ms,inlinePriceVisible:range===focus,inlineNameVisible:range===focus,axisLabelVisible:false,startMarkerVisible:false,monthly:true,historical:range!==focus,month:range.month},
      {label:'月首周低',price:range.low,kind:'range',color:'#738294',lineWidth:1,lineStyle:2,start_ms:range.ready_ms,end_ms:range.expires_ms,inlinePriceVisible:range===focus,inlineNameVisible:range===focus,axisLabelVisible:false,startMarkerVisible:false,monthly:true,historical:range!==focus,month:range.month},
    ].filter(level=>finite(level.price)&&level.price>0));
  }
  function withMonthlyLevels(value,ranges){
    const step=PERIODS[value.bar]||PERIODS['15m'],first=value.candles?.[0]?.time,last=value.candles?.at(-1)?.time;
    const fromMs=finite(first)?first:-Infinity,toMs=finite(last)?last+step:Infinity;
    const monthly=monthlyLines(ranges,fromMs,toMs),levels=[...(value.levels||[]),...monthly];
    const windows=levels.filter(l=>finite(l.start_ms)&&finite(l.end_ms));
    return {...value,levels,...(windows.length?{level_window:{start_ms:Math.min(...windows.map(l=>l.start_ms)),end_ms:Math.max(...windows.map(l=>l.end_ms))}}:{})};
  }
  function validateMonthlyEvent(data,{symbol,eventId,period}){
    const step=PERIODS[period];
    if(!data||data.event_id!==eventId||data.symbol!==symbol||data.bar!==period||!step||!/^\d{4}-(0[1-9]|1[0-2])$/.test(data.month||'')||
        !['upper','lower'].includes(data.side)||!finite(data.level)||!finite(data.observation_start_ms)||!data.anchor||!Array.isArray(data.candles))
      throw Error('历史触价 K 线与所选事件不匹配');
    const a=data.anchor;
    if(![a.start_ms,a.ready_ms,a.expires_ms,a.high,a.low,a.upper,a.lower].every(finite)||!(a.start_ms<a.ready_ms&&a.ready_ms<a.expires_ms)||a.high<=a.low||a.upper<=a.high||a.lower>=a.low||a.lower<=0||a.upper<=0)
      throw Error('月首周锚点数据不完整');
    let previous=-Infinity;
    for(const candle of data.candles){
      if(!candle||![candle.time,candle.open,candle.high,candle.low,candle.close].every(finite)||!Number.isInteger(candle.time)||
          candle.time<=previous||candle.time%step!==0||candle.high<Math.max(candle.open,candle.close,candle.low)||candle.low>Math.min(candle.open,candle.close))
        throw Error('历史 K 线数据不完整或顺序错误');
      previous=candle.time;
    }
    if(data.marker_ms!=null&&(!finite(data.marker_ms)||!data.candles.some(candle=>candle.time===data.marker_ms)))throw Error('触价标记与原始 K 线不匹配');
    if(data.observation_start_ms!=null&&!finite(data.observation_start_ms))throw Error('触价观测时间无效');
    if(data.forming_time_ms!=null&&!finite(data.forming_time_ms))throw Error('当前 K 线边界无效');
    if(!Array.isArray(data.weekly_ranges))throw Error('缺少触价对应的周一锚点');
    return data;
  }
  function monthlyEventFrame(data,{symbol,eventId,period}){
    validateMonthlyEvent(data,{symbol,eventId,period});
    const markerMs=data.marker_ms??data.observation_start_ms;
    const eventWeek=data.weekly_ranges.filter(w=>w?.symbol===symbol&&w.state==='ready'&&finite(w.start_ms)&&finite(w.expires_ms)&&
      [w.high,w.low,w.upper,w.lower,w.high_ms,w.low_ms].every(finite)&&w.start_ms<=markerMs&&markerMs<w.expires_ms);
    const weekly=eventWeek.length?eventWeek.at(-1):null;
    const bars=data.candles,first=bars[0]?.time,last=bars.at(-1)?.time;
    const fromMs=finite(first)?first:-Infinity,toMs=finite(last)?last+PERIODS[period]:Infinity;
    const monthly=monthlyLines([{month:data.month,...data.anchor}],fromMs,toMs);
    const weeklyLines=weekly?levels(weekly,weekly).map(level=>({...level,start_ms:finite(level.start_ms)?level.start_ms:weekly.end_ms,end_ms:weekly.expires_ms,week:weekly.week,historical:false})):[];
    const allLevels=[...weeklyLines,...monthly];
    const windows=allLevels.filter(l=>finite(l.start_ms)&&finite(l.end_ms));
    return {...data,source:'OKX',timezone:'America/New_York',levels:allLevels,
      ...(windows.length?{level_window:{start_ms:Math.min(...windows.map(l=>l.start_ms)),end_ms:Math.max(...windows.map(l=>l.end_ms))}}:{}),
      ...(weekly?{monday:{start_ms:weekly.start_ms,end_ms:weekly.end_ms,complete:true}}:{}),
      monthlyEvent:{event_id:eventId,marker_ms:data.marker_ms,weekly_anchor:weekly||null}};
  }
  function cloudReplayFrame(value){
    const step=PERIODS['12H'],w=value.window,byTime=new Map(value.candles.map(b=>[b.time,b]));
    if(value.bar!=='12H'||!w||!finite(w.startMs)||!finite(w.endMs)||
        w.endMs<w.startMs||(w.endMs-w.startMs)/step+1!==w.expectedBars||
        w.expectedBars>5000||byTime.size!==w.availableBars||value.candles.length!==w.availableBars)
      throw Error('云带 12H 窗口与原始行情不符');
    const candles=[];
    for(let t=w.startMs;t<=w.endMs;t+=step) candles.push(byTime.get(t)||{time:t,gap:true});
    if(candles.filter(b=>!b.gap).length!==w.availableBars)throw Error('云带 K 线超出冻结窗口');
    return {...value,candles,backtestSource:'live-cloud-v17'};
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
  if(typeof module!=='undefined')module.exports={levels,prepare,withWeeks,validateMonthlyRanges,monthlyLines,withMonthlyLevels,validateMonthlyEvent,monthlyEventFrame,PERIODS,refreshDelay};
  if(typeof document==='undefined')return;
  const shared=window.SpikePublicChartCache,cache=shared?.candles||new Map(),levelCache=shared?.levels||new Map(),monthLevelCache=new Map();
  const eventChartCache=new Map();
  function requestMonthlyRanges(symbol,signal){
    const cached=monthLevelCache.get(symbol);
    if(cached&&cached.due>Date.now())return Promise.resolve(cached.ranges);
    const url='/api/month-first-week-1618/levels?symbol='+encodeURIComponent(symbol);
    return fetch(url,{signal,cache:'no-store',credentials:'same-origin'}).then(async response=>{
      if(!response.ok)throw Error('月首周锚点暂不可用');
      const ranges=validateMonthlyRanges(await response.json(),symbol);
      monthLevelCache.set(symbol,{ranges,due:Date.now()+(ranges.length?300000:30000)});
      return ranges;
    }).catch(()=>cached?.ranges||[]);
  }
  function create({decorate,monthly=false}={}){
  const view={decorate,bars:720,initialBars:160,range:true,fib:true,fitAll:false,showLevels:false,mondayBand:true,wallpaper:true};
  let target=null,controller=null,timer=null,version=0,paused=false,appearance=null;
  const replayCache=new Map();
  function pause(){paused=true;version++;clearTimeout(timer);controller?.abort();controller=null;target?.feed?.stop();target?.history?.pause();if(target)target.feed=null;}
  function stop(){pause();if(target){window.SpikeRangeChart?.unmount(target.mount);target.host.replaceChildren?.();}target=null;}
  function clearMonthlyEventMarkers(host){
    if(target?.host!==host||!target.monthEvent)return false;
    target.instance?.series?.setMarkers?.([]);
    target.eventPositioned=false;
    return true;
  }
  function draw(context,error=''){
    if(!target)return;
    try{
      let value=target.monthEvent?monthlyEventFrame(context,{symbol:target.row.symbol,eventId:target.monthEvent,period:target.period})
        :target.replay?context:prepare(context,target.row,target.week,target.period,target.levels);
      if(!target.monthEvent)value=withWeeks(value,target.levels,target.week);
      if(monthly&&!target.monthEvent&&!target.replay)value=withMonthlyLevels(value,target.monthRanges);
      if(!target.monthEvent&&!target.replay)value=window.SpikeMondayCandleFeed?.mergeDisplay(value,target.live,target.period)||value;
      target.context=context;
      value.paperTrade=target.trade;value.paperTrades=target.trades;
      if(context.display_cached)value.stale=true;
      if(!target.internal)value.levels=value.levels.filter(l=>l.monthly===true||['上 1.618','周一高','周一低','下 1.618'].includes(l.label));
      const appearanceModel=window.SpikeMondayChartAppearance,theme=document.documentElement?.dataset?.theme==='dark'?'dark':'light';
      if(appearance&&appearanceModel?.applyLevels){
        value={...value,levels:appearanceModel.applyLevels(value.levels,appearance,theme)};
      }
      const compactReplay=!!value.backtestTrade||!!value.paperHistory,step=PERIODS[target.period];
      const displayWindow=value.paperHistory?{from:Math.floor(target.trade.fill_ms/step)*step-12*step,to:Math.floor(target.trade.exit_ms/step)*step+13*step}:null;
      if(compactReplay)value.levels=value.levels.map(l=>({...l,lineWidth:1,color:l.kind==='fib'?'#8196ac70':'#8196ac55',inlinePriceVisible:false}));
      const candleColor=appearance&&appearanceModel?.candleColorizer?appearanceModel.candleColorizer(appearance,window.SpikeCloudCandleColors):null;
      const eventData=target.monthEvent?context:null,marker=eventData?.marker_ms!=null?[{time:eventData.marker_ms/1000,
        position:eventData.side==='upper'?'aboveBar':'belowBar',shape:eventData.side==='upper'?'arrowDown':'arrowUp',
        color:eventData.side==='upper'?MONTH_COLORS.upper:MONTH_COLORS.lower,text:'首周触价'}]:[];
      const eventCandleColor=eventData?candle=>candle.first_week===true?'#30D158':'#8B9099':null;
      let positioned=!!target.eventPositioned;
      const eventDecorate=eventData?instance=>{
        if(instance.series?.setMarkers)instance.series.setMarkers(marker);
        const status=instance.panel?.querySelector('.cv-range-status');
        if(status){const text=eventData.forming_time_ms!=null?`本根 ${target.period} K 线尚未收盘。`:marker.length?'已标记可核实的月首周触价 K 线。':'触价 K 线暂不可核实，未添加标记。';status.textContent=text;status.classList.toggle('is-warning',!marker.length||eventData.forming_time_ms!=null);}
        if(positioned||!instance.chart||!instance.rows.length)return;
        const markerTime=(eventData.marker_ms??eventData.observation_start_ms)/1000;
        let index=instance.rows.findIndex(candle=>candle.time===markerTime);
        if(index<0)index=instance.rows.reduce((best,candle,i)=>Math.abs(candle.time-markerTime)<Math.abs(instance.rows[best]?.time-markerTime)?i:best,0);
        const span=Math.min(90,Math.max(36,instance.rows.length));
        instance.chart.timeScale().setVisibleLogicalRange({from:Math.max(0,index-span/2),to:Math.min(instance.rows.length-1,index+span/2)});
        target.eventPositioned=true;positioned=true;
      }:null;
      target.instance=window.SpikeRangeChart.mount(target.mount,value,{...view,...(eventCandleColor?{candleColor:eventCandleColor}:candleColor?{candleColor}:{}),compactReplay,displayWindow,scaleReferenceLevels:target.monthEvent?false:!compactReplay,bars:target.monthEvent||target.replay||window.SpikeChartHistory?.full(target.row.symbol)?Infinity:720,ma:target.monthEvent?false:target.ma,mondayBand:target.period!=='1D',...(target.monthEvent?{decorate:eventDecorate,onVisibleRange:undefined}:{onVisibleRange:instance=>target?.history?.observe(instance)})},error);
      const status=error||target.levelError||(context.error?'K 线更新失败：'+context.error:context.loading?'正在同步此合约 K 线…':!value.candles?.length?'暂无此周期 K 线，请稍后重试':target.monthEvent&&value.monthlyEvent&&!value.monthlyEvent.weekly_anchor?'对应周一锚点暂不可用':target.live?.error||'');
      message(status,{busy:!error&&!target.levelError&&!context.error&&!!context.loading});
    }catch(e){message(e.message);window.SpikeRangeChart.unmount(target.mount);}
  }
  function message(value,{busy=false}={}){
    if(!target)return;target.state.textContent=value;target.caption.hidden=!value;
    target.caption.setAttribute?.('aria-busy',String(!!busy));
    const retry=target.host.querySelector('[data-mf-chart-retry]');if(retry)retry.hidden=!!busy;
  }
  function startFeed(){
    if(!target||target.replay||target.monthEvent||target.feed||document.hidden||paused||!window.SpikeMondayCandleFeed)return;
    const current=target;current.live=null;
    current.feed=window.SpikeMondayCandleFeed.create({symbol:current.row.symbol,period:current.period,
      onChange:value=>{if(target!==current||paused)return;current.live=value;if(current.context)draw(current.context);},
      onReconnect:()=>{if(target===current&&!paused)refresh();}});
    // A cached high-timeframe snapshot can be hours old. Never reset the live
    // clock to that historical receipt; fresh REST responses synchronize it.
    current.feed.start();
  }
  async function refresh(){
    if(target?.replay)return refreshReplay();
    if(target?.monthEvent)return refreshMonthlyEvent();
    if(!target||controller||document.hidden)return;
    clearTimeout(timer);const current=target,token=version,c=new AbortController();controller=c;
    const timeout=setTimeout(()=>c.abort(),15000);let delay=15000;
    if(!current.context?.candles?.length&&!current.context?.error)message('正在加载区间 K 线…',{busy:true});
    const anchorRequest=current.levels&&!current.levels.display_previous?null:fetch('/api/monday-fib/levels?symbol='+encodeURIComponent(current.row.symbol),{signal:c.signal,cache:'no-store'}).then(async r=>{
      if(!r.ok)throw Error('周一锚点读取失败');const a=await r.json();
      if(a.symbol!==current.row.symbol||(a.current_week||a.week)!==current.week)throw Error('周一锚点待同步');
      if(a.state==='developing')return a;
      if(a.state!=='ready'||![a.high_ms,a.low_ms].every(t=>finite(t)&&t>=a.start_ms&&t<a.end_ms)||!['high','low','upper','lower'].every(k=>finite(a[k])))throw Error('周一锚点待同步');
      return a;
    }).catch(()=>null);
    const monthRequest=monthly?requestMonthlyRanges(current.row.symbol,c.signal):Promise.resolve(null);
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
      const [anchors,monthlyRanges]=await Promise.all([anchorRequest,monthRequest]);
      if(token!==version||target!==current)return;
      if(monthly)current.monthRanges=monthlyRanges;
      if(anchorRequest){
        current.levels=anchors?.state==='ready'?anchors:current.levels;current.levelError=anchors?'':'周一锚点暂不可用，稍后重试';
        if(anchors?.state==='ready'){shared?.saveLevels(anchors);levelCache.set(current.row.symbol+'|'+current.week,anchors);while(levelCache.size>32)levelCache.delete(levelCache.keys().next().value);}else delay=10000;
      }
      value=current.history?.merge(value)||value;
      cache.set(current.key,{value,due:Date.now()+delay});
      draw(value);
    }catch(e){if(token===version&&target===current){message((e.name==='AbortError'?'图表读取超时':e.message)+' · 可重新加载');delay=10000;}}
    finally{clearTimeout(timeout);if(controller===c)controller=null;if(token===version&&target===current&&!document.hidden)timer=setTimeout(refresh,delay);}
  }
  async function refreshMonthlyEvent(){
    if(!target?.monthEvent||controller||document.hidden||paused)return;
    const current=target,token=version,c=new AbortController();controller=c;
    const timeout=setTimeout(()=>c.abort(),30000),query=new URLSearchParams({event_id:current.monthEvent,period:current.period});
    message('正在读取月首周触价时段 K 线…',{busy:true});
    try{
      const response=await fetch('/api/month-first-week-1618/chart?'+query,{signal:c.signal,cache:'no-store',credentials:'same-origin'});
      const data=await response.json();
      if(!response.ok)throw Error(data.detail||'历史触价 K 线暂不可用');
      validateMonthlyEvent(data,{symbol:current.row.symbol,eventId:current.monthEvent,period:current.period});
      if(token!==version||target!==current||c.signal.aborted)return;
      const frame=monthlyEventFrame(data,{symbol:current.row.symbol,eventId:current.monthEvent,period:current.period});
      current.levelError=frame.monthlyEvent.weekly_anchor?'':'对应周一锚点暂不可用';
      current.onData?.(data);
      if(data.marker_ms!=null&&data.forming_time_ms==null&&frame.monthlyEvent.weekly_anchor){eventChartCache.set(current.key,data);while(eventChartCache.size>24)eventChartCache.delete(eventChartCache.keys().next().value);}
      draw(data);
    }catch(error){if(token===version&&target===current&&!c.signal.aborted)message((error?.name==='AbortError'?'历史 K 线读取超时':error?.message||'历史 K 线暂不可用')+' · 可重新加载');}
    finally{clearTimeout(timeout);if(controller===c)controller=null;}
  }
  async function refreshReplay(){
    if(!target?.replay||controller||paused)return;
    const current=target,token=version,c=new AbortController();controller=c;
    const timeout=setTimeout(()=>c.abort(),30000);message('正在读取原始回测 K 线…',{busy:true});
    const paper=current.paper,trade=current.replay,breadth=current.replaySource==='breadth-ema20',cloud=!paper&&current.track==='live-cloud-v17';
    const eventId=current.replayOptions?.event_id||trade.event_id;
    const url=breadth?'/api/breadth-replay/candles?'+new URLSearchParams({event_id:eventId,symbol:trade.symbol,period:current.period})
      :paper?'/api/monday-fib/trade-candles?'+new URLSearchParams({symbol:trade.symbol,entry_ms:trade.fill_ms,exit_ms:trade.exit_ms,period:current.period})
      :'/api/strategy-backtest/candles?'+new URLSearchParams({track:current.track,trade:trade.id,period:current.period});
    if(paper)message('正在读取开仓时段 K 线…',{busy:true});
    else if(breadth)message('正在读取全市场下轨原始 Binance K 线…',{busy:true});
    try{
      if(breadth&&(!eventId||typeof eventId!=='string'||typeof trade.symbol!=='string'||!trade.symbol))throw Error('回放事件或成分币标识缺失');
      const r=await fetch(url,{signal:c.signal,cache:'no-store'});
      let value=await r.json();
      if(!r.ok)throw Error(value.detail||'回测 K 线读取失败');
      if(token!==version||target!==current)return;
      if(value.schema!==1||value.symbol!==current.row.symbol||value.bar!==current.period||!value.candles?.length||(breadth
        ?value.source!=='Binance'||value.event_id!==eventId||value.entry_ms!==trade.entry_ms||value.exit_ms!==trade.exit_ms
        :paper?value.source!=='OKX'||value.entry_ms!==trade.fill_ms||value.exit_ms!==trade.exit_ms||value.paperHistory!==true
        :value.track!==current.track||value.trade_id!==trade.id||value.source!==(cloud?'OKX':'Binance')))throw Error('历史 K 线与所选交易不匹配');
      let last=0;for(const b of value.candles){const volumeOK=breadth||cloud?b.volume===null||finite(b.volume):finite(b.volume);
        if(![b.time,b.open,b.high,b.low,b.close].every(finite)||!volumeOK||b.time<=last||b.time%PERIODS[current.period]
          ||b.high<Math.max(b.open,b.close,b.low)||b.low>Math.min(b.open,b.close))throw Error('回测 K 线数据不完整');last=b.time;}
      if(cloud){
        if(value.backtestTrade?.id!==trade.id||value.backtestTrade?.entry_ms!==trade.entry_ms||
           value.backtestTrade?.exit_ms!==trade.exit_ms)throw Error('云带成交记录不匹配');
        value=cloudReplayFrame(value);
        current.week=value.weekly_ranges?.[0]?.week;
      }else if(breadth){
        if(!finite(value.now_ms)||!finite(value.last_close_ms))throw Error('历史 K 线收盘边界缺失');
        value={...value,track:current.track,backtestSource:'breadth-ema20',
          backtestScope:current.replayOptions?.scope||trade.scope||'',backtestEventId:eventId,backtestTrade:trade};
      }
      replayCache.set(current.key,value);while(replayCache.size>12)replayCache.delete(replayCache.keys().next().value);draw(value);
    }catch(e){if(token===version&&target===current)message((e.name==='AbortError'?'回测 K 线读取超时':e.message)+' · 可重新加载');}
    finally{clearTimeout(timeout);if(controller===c)controller=null;}
  }
  function showReplay(host,track,trade,{period='15m',ma=false,internal=false,refocus=false,paper=false,replayOptions={}}={}){
    clearMonthlyEventMarkers(host);
    const options=replayOptions&&typeof replayOptions==='object'?replayOptions:{};
    const breadth=!paper&&(options.replay_source==='breadth-ema20'||options.source==='breadth-ema20'),eventId=options.event_id||trade.event_id;
    const scope=options.scope||trade.scope||'';
    const key=breadth?`breadth|${scope}|${eventId}|${trade.symbol}|${period}`
      :(paper?'paper-history|':'backtest|')+track+'|'+trade.id+'|'+period+(paper?'|'+trade.symbol+'|'+trade.fill_ms+'|'+trade.exit_ms:'');
    if(target?.key===key&&target.host===host){
      const changed=refocus||target.ma!==ma||target.internal!==internal||target.replay!==trade||JSON.stringify(target.replayOptions)!==JSON.stringify(options);
      target.ma=ma;target.internal=internal;target.replay=trade;target.replayOptions=options;
      if(changed&&target.context)draw(target.context);if(paused){paused=false;if(!target.context)refreshReplay();}return;
    }
    const reuse=target?.host===host;pause();if(!reuse)stop();paused=false;
    if(!reuse)host.innerHTML='<div class="mf-chart-caption"><p role="status">正在读取原始回测 K 线…</p><button type="button" data-mf-chart-retry>重新加载</button></div><div class="mf-chart-mount"></div>';
    target={host,key,track,replay:trade,paper,replaySource:breadth?'breadth-ema20':'',replayOptions:options,trade:paper?trade:null,row:{symbol:trade.symbol},period,ma,internal,week:paper?trade.week:trade.event?.split(':')[1],state:host.querySelector('[role=status]'),caption:host.querySelector('.mf-chart-caption'),mount:host.querySelector('.mf-chart-mount')};
    if(!reuse)host.querySelector('[data-mf-chart-retry]').addEventListener('click',()=>{target?.history?.retry();refresh();});
    const cached=replayCache.get(key);if(cached)draw(cached);else{target.instance=window.SpikeRangeChart.mount(target.mount,{symbol:trade.symbol,source:paper?'OKX':'Binance',bar:period,candles:[],levels:[]},view);refreshReplay();}
  }
  function showMonthlyEvent(host,symbol,eventId,{period='15m',internal=false,refocus=false,onData=null}={}){
    if(!monthly||!host||!PERIODS[period]||typeof symbol!=='string'||! /^[A-Z0-9]{1,30}-USDT-SWAP$/.test(symbol)||
       typeof eventId!=='string'||!eventId||eventId.length>96)return;
    const key=`month-event|${eventId}|${symbol}|${period}`;
    if(target?.host===host&&target.key===key){
      target.internal=internal;
      target.onData=typeof onData==='function'?onData:target.onData;
      if(refocus)target.eventPositioned=false;
      if(target.context)draw(target.context);
      if(target.context?.event_id===eventId)target.onData?.(target.context);
      if(paused){paused=false;if(target.context)draw(target.context);else refreshMonthlyEvent();}
      return;
    }
    pause();
    if(target){window.SpikeRangeChart?.unmount(target.mount);target.host.replaceChildren?.();}
    target=null;paused=false;
    host.innerHTML='<div class="mf-chart-caption"><p role="status">正在读取月首周触价时段 K 线…</p><button type="button" data-mf-chart-retry>重新加载</button></div><div class="mf-chart-mount"></div>';
    target={host,key,row:{symbol},period,internal,ma:false,monthEvent:eventId,eventPositioned:false,onData,
      state:host.querySelector('[role=status]'),caption:host.querySelector('.mf-chart-caption'),mount:host.querySelector('.mf-chart-mount')};
    host.querySelector('[data-mf-chart-retry]').addEventListener('click',()=>refresh());
    const cached=eventChartCache.get(key);
    if(cached){draw(cached);target.onData?.(cached);}
    else{target.instance=window.SpikeRangeChart.mount(target.mount,{symbol,source:'OKX',timezone:'America/New_York',bar:period,candles:[],levels:[]},view);refreshMonthlyEvent();}
  }
  function show(host,row,week,{internal=false,ma=false,period='15m',trade=null,trades=[],anchors=null}={}){
    clearMonthlyEventMarkers(host);
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
    if(!reuse)host.innerHTML='<div class="mf-chart-caption"><p role="status">正在加载区间 K 线…</p><button type="button" data-mf-chart-retry>重新加载</button></div><div class="mf-chart-mount"></div>';
    target={host,row,week,key,internal,ma,period,trade,trades,levels:levelCache.get(row.symbol+'|'+week),monthRanges:monthly?monthLevelCache.get(row.symbol)?.ranges||[]:[],state:host.querySelector('[role=status]'),caption:host.querySelector('.mf-chart-caption'),mount:host.querySelector('.mf-chart-mount')};
    if(!reuse)host.querySelector('[data-mf-chart-retry]').addEventListener('click',()=>{target?.history?.retry();refresh();});
    const owner=target;
    target.history=window.SpikeChartHistory?.create({symbol:row.symbol,period,
      changed:value=>{if(target!==owner||paused)return;const old=cache.get(key);cache.set(key,{value,due:old?.due||0});draw(value);}});
    const previous=cache.get(key);
    if(previous){const value=target.history?.merge(previous.value)||previous.value;draw(previous.due>Date.now()?value:{...value,display_cached:true});}
    else if(reuse){
      // Clear the previous identity synchronously while keeping its canvas and
      // per-symbol viewport cache. Never label the old candles as a new symbol.
      target.instance=window.SpikeRangeChart.mount(target.mount,{symbol:row.symbol,bar:period,candles:[],levels:[]},view);
      message('正在加载区间 K 线…',{busy:true});
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
  function setAppearance(value){
    appearance=window.SpikeMondayChartAppearance?.normalize?window.SpikeMondayChartAppearance.normalize(value):value;
    if(target?.context)draw(target.context);
  }
  return {show,showReplay,showMonthlyEvent,retry:refresh,hide:stop,pause,setInternal,setMa,setAppearance,fit,latest,zoom};
  }
  window.SpikeMondayFibChart=Object.assign(create({monthly:true}),{create});
})();
