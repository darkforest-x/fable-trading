/* Start the selected read-only workspace while its code downloads. Responses
 * are single-use, short-lived and never persisted or shared between accounts.
 */
(() => {
  'use strict';
  function initialSymbol(items,pinned=''){
    // A pinned symbol may be an untouched favorite; never warm another coin.
    if(pinned)return (items||[]).find(row=>row.symbol===pinned)?.symbol;
    let best=null,latest=-Infinity;
    for(const row of items||[]){
      const events=Object.values(row.touches||{});if(!events.length)continue;
      const time=Math.max(...events.map(e=>(e?.source==='history'?e.candle_ms:e?.observed_ms)||0));
      if(time>latest||(time===latest&&row.symbol.localeCompare(best)<0)){best=row.symbol;latest=time;}
    }
    return best;
  }
  if(typeof module!=='undefined')module.exports={initialSymbol};
  if(typeof document==='undefined')return;
  const route=window.location.hash.slice(1).split('?');
  if(route[0]!=='monday-fib'||document.hidden)return;
  let versions={};try{versions=JSON.parse(document.getElementById('asset-versions')?.textContent||'{}');}catch{}
  const slots=new Map();let cancelled=false;
  function start(key,url){
    if(cancelled)return;
    const controller=new AbortController(),timeout=setTimeout(()=>controller.abort(),12000);
    const pending=fetch(url,{priority:'high',cache:'no-store',credentials:'same-origin',signal:controller.signal})
      .then(async response=>{if(!response.ok)return null;return {value:await response.json(),receivedAt:Date.now()};})
      .catch(()=>null).finally(()=>clearTimeout(timeout));
    slots.set(key,{pending,controller,startedAt:Date.now()});return pending;
  }
  function cancel(){cancelled=true;for(const slot of slots.values())slot.controller.abort();slots.clear();}
  function take(key,signal){
    const slot=slots.get(key);if(!slot)return null;slots.delete(key);
    if(Date.now()-slot.startedAt>15000||signal.aborted){slot.controller.abort();return null;}
    const abort=()=>slot.controller.abort();signal.addEventListener('abort',abort,{once:true});
    return slot.pending.finally(()=>signal.removeEventListener('abort',abort));
  }
  window.SpikeStartup={take};
  window.addEventListener('pagehide',cancel,{once:true});
  window.addEventListener('hashchange',()=>{if(window.location.hash.slice(1).split('?')[0]!=='monday-fib')cancel();});
  document.addEventListener('visibilitychange',()=>{if(document.hidden)cancel();});
  setTimeout(cancel,15000);
  start('monday-snapshot','/api/monday-fib?compact=true')?.then(packet=>{
    const symbol=initialSymbol(packet?.value?.items,new URLSearchParams(route[1]||'').get('symbol')||'');
    if(!symbol||!/^[A-Z0-9]{1,32}-USDT-SWAP$/.test(symbol))return;
    let period='15m';try{period=window.localStorage.getItem('spike.monday.timeframe')||period;}catch{}
    if(!['1m','5m','15m','30m','1H','4H','1D'].includes(period))period='15m';
    start(symbol+'|'+period,'/api/monday-fib/candles?symbol='+encodeURIComponent(symbol)+'&period='+period+'&wait_ms=8000');
  });
  const critical=['workspace-base.css','workspace-shell.css','workspace-polish.css','workspace-boot.js'];
  const names=[...critical,'monday-fib.css','range-chart.js','monday-candle-feed.js','monday-fib-chart.js','monday-quick.js','monday-list-model.js','monday-fib.js','/vision-assets/vendor/lightweight-charts.standalone.production.js'];
  for(const name of names){
    if(!versions[name])continue;
    const link=document.createElement('link');link.rel='preload';link.as=name.endsWith('.css')?'style':'script';
    if(critical.includes(name))link.fetchPriority='high';
    link.href=(name.startsWith('/')?name:'/static/'+name)+'?v='+versions[name];
    document.head.append(link);
  }
})();
