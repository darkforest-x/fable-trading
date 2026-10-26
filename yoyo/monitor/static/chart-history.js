/* Demand-only public chart history. Stitch b6adf7933bf9478c8113d8d2d9a20660.
 * Native OKX pages extend three pinned symbols; visible NY weeks load separately.
 * Requests stop with their owning chart. No scanner or order-context changes.
 */
(() => {
  'use strict';
  const full=symbol=>['BTC-USDT-SWAP','ETH-USDT-SWAP','XAU-USDT-SWAP'].includes(symbol);
  const steps={'1m':60000,'5m':300000,'15m':900000,'30m':1800000,'1H':3600000,'4H':14400000,'1D':86400000},DAY=86400000;
  const weekAt=typeof window==='undefined'?require('./public-chart-cache.js').weekAt:window.SpikePublicChartCache.weekAt;
  function mergeRows(oldRows,newRows,limit=Infinity){
    const rows=new Map(oldRows.map(r=>[r.time,r]));for(const row of newRows)rows.set(row.time,row);
    return [...rows.values()].sort((a,b)=>a.time-b.time).slice(-limit);
  }
  function validWeek(w,symbol){return w?.symbol===symbol&&w.state==='ready'&&w.timezone==='America/New_York'&&
    [w.start_ms,w.end_ms,w.expires_ms,w.high_ms,w.low_ms,w.high,w.low,w.upper,w.lower].every(Number.isFinite)&&
    w.start_ms<w.end_ms&&w.end_ms<w.expires_ms&&w.high>w.low&&weekAt(w.start_ms)===w.week&&
    [w.high_ms,w.low_ms].every(t=>t>=w.start_ms&&t<w.end_ms);}
  function weekKeys(from,to){
    const a=Date.parse(weekAt(from)+'T12:00:00Z'),b=Date.parse(weekAt(to)+'T12:00:00Z'),out=[];
    for(let t=b;t>=a;t-=7*DAY)out.push(new Date(t).toISOString().slice(0,10));return out;
  }
  function create({symbol,period,request=(url,options)=>fetch(url,options),changed=()=>{},status=()=>{},now=Date.now}={}){
    const weeks=new Map(),attempts=new Map(),active=new Map();let context=null,instance=null,paused=false,more=true,historyError='',weekError='',weekBlocked=false;
    const native=period==='1D'?'1Dutc':period,step=steps[period];
    function publish(){
      const detail=historyError||weekError,error=detail&&!detail.includes('重试')?detail+' · 点击重试':detail;
      status({text:error|| (active.has('history')?'正在加载更早 K 线…':active.has('weeks')?'正在补齐历史周线…':full(symbol)?more?'← 向左拖动加载历史':'已到此合约最早 K 线':'最近 720 根 · 逐周区间'),retry:!!error,count:context?.candles?.length||0});
    }
    function merge(value){
      if(value.symbol!==symbol||value.bar!==period)throw Error('历史 K 线身份不匹配');
      for(const w of value.weekly_ranges||[])if(validWeek(w,symbol))weeks.set(w.week,w);
      if(value.history_has_more===false)more=false;
      const candles=full(symbol)?mergeRows(context?.candles||[],value.candles||[]):(value.candles||[]).slice(-720);
      context={...value,candles,weekly_ranges:[...weeks.values()],history_has_more:more};publish();return context;
    }
    async function get(kind,url){
      const c=new AbortController();active.set(kind,c);publish();const timeout=setTimeout(()=>c.abort(),30000);
      try{const r=await request(url,{signal:c.signal,cache:'no-store',credentials:'same-origin'});if(!r.ok)throw Error('读取失败');const value=await r.json();if(paused||active.get(kind)!==c)return null;return value;}
      catch(e){if(active.get(kind)!==c)e.stale=true;throw e;}
      finally{clearTimeout(timeout);if(active.get(kind)===c)active.delete(kind);}
    }
    function update(){if(paused||!context)return;context={...context,weekly_ranges:[...weeks.values()],history_has_more:more};changed(context);publish();}
    async function older(){
      const before=context?.candles?.[0]?.time;
      if(paused||!full(symbol)||!before||!more||historyError||active.has('history'))return;
      try{
        const v=await get('history',`/api/monday-fib/history?symbol=${encodeURIComponent(symbol)}&period=${period}&before_ms=${before}`);if(!v)return;
        if(v.symbol!==symbol||v.bar!==period||v.native_bar!==native||!['ready','exhausted'].includes(v.state))throw Error(v.error||'历史 K 线待重试');
        const rows=v.candles;
        if(!Array.isArray(rows)||rows.some(r=>![r.time,r.open,r.high,r.low,r.close,r.volume].every(Number.isFinite)||r.time>=before||r.time%step||Math.min(r.open,r.low,r.close)<=0||r.volume<0||r.high<Math.max(r.open,r.low,r.close)||r.low>Math.min(r.open,r.close))||rows.some((r,i)=>i&&r.time<=rows[i-1].time)||v.has_more&&(!rows.length||v.next_before_ms!==rows[0].time))throw Error('历史 K 线返回不完整');
        more=v.has_more!==false;context={...context,candles:mergeRows(rows,context.candles)};update();
      }catch(e){if(!paused&&!e.stale){historyError=e.name==='AbortError'?'历史加载超时 · 点击重试':e.message;publish();}}
    }
    async function weekly(from,to){
      if(paused||active.has('weeks')||weekBlocked)return;
      const missing=weekKeys(from,to).filter(k=>!weeks.has(k)&&(attempts.get(k)||0)<=now()).slice(0,3);if(!missing.length)return;
      // UTC midnight starts before NY Monday even during DST. This also works
      // during Monday's first hours, before the UTC-noon selection marker.
      const a=Date.parse(missing.at(-1)+'T00:00:00Z'),b=Date.parse(missing[0]+'T12:00:00Z')+1;
      try{
        const v=await get('weeks',`/api/monday-fib/weekly-levels?symbol=${encodeURIComponent(symbol)}&from_ms=${a}&to_ms=${b}`);if(!v)return;
        if(v.symbol!==symbol||v.timezone!=='America/New_York'||v.error||!v.weeks?.length)throw Error(v.error||'历史周线待重试');
        let incomplete=false;
        for(const w of v.weeks){
          if(validWeek(w,symbol))weeks.set(w.week,w);
          else if(w.symbol===symbol&&w.state==='developing')attempts.set(w.week,now()+60000);
          else if(w.symbol===symbol&&['missing_monday','invalid_range'].includes(w.state)){attempts.set(w.week,now()+60000);incomplete=true;}
          else throw Error('历史周线返回不完整');
        }
        if(incomplete)weekError='部分周一数据不完整 · 点击重试';update();
      }catch(e){if(!paused&&!e.stale){weekBlocked=true;weekError=e.name==='AbortError'?'周线加载超时 · 点击重试':e.message;publish();}}
    }
    function observe(value){
      instance=value;if(paused||!context?.candles?.length)return;
      if(!instance?.key?.startsWith(symbol+'|')||!instance.key.includes('|'+period+'|'))return;
      const rows=instance?.rows,r=instance?.chart?.timeScale().getVisibleLogicalRange();if(!rows?.length||!r)return;
      const first=Math.max(0,Math.min(rows.length-1,Math.floor(r.from))),last=Math.max(first,Math.min(rows.length-1,Math.ceil(r.to)));
      if(r.from<80)older();weekly(rows[first].time*1000,rows[last].time*1000);
    }
    function pause(){paused=true;for(const c of active.values())c.abort();active.clear();}
    function resume(){paused=false;publish();observe(instance);}
    function retry(){historyError='';weekError='';weekBlocked=false;attempts.clear();publish();observe(instance);}
    return {merge,observe,pause,resume,retry,older,weekly};
  }
  const api={create,full,mergeRows,validWeek,weekKeys};
  if(typeof module!=='undefined')module.exports=api;
  if(typeof window!=='undefined')window.SpikeChartHistory=api;
})();
