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
  function paperSymbol(data,params){
    const pinned=params.get('symbol');if(pinned)return /^[A-Z0-9]{1,32}-USDT-SWAP$/.test(pinned)?pinned:null;
    const track=data?.tracks?.[params.get('track')==='5'?'5':'60'],id=params.get('trade');
    const rows=[...(track?.open||[]),...(track?.closed||[]),...(track?.latest_funded?[track.latest_funded]:[])].filter(p=>/^[A-Z0-9]{1,32}-USDT-SWAP$/.test(p.symbol)&&[1,-1].includes(p.side)&&Number.isFinite(p.fill_ms)&&p.fill_ms>0&&Number.isFinite(p.entry)&&p.entry>0);
    return (id?rows.find(p=>String(p.id)===id):rows.filter(p=>p.taken===1&&p.notional>0).sort((a,b)=>b.fill_ms-a.fill_ms||b.id-a.id)[0])?.symbol||null;
  }
  if(typeof module!=='undefined')module.exports={initialSymbol,paperSymbol};
  if(typeof document==='undefined')return;
  const route=window.location.hash.slice(1).split('?');
  const params=new URLSearchParams(route[1]||''),paper=!route[0]||route[0]==='market'||route[0]==='signals'&&params.get('pane')==='reclaim';
  if((route[0]!=='monday-fib'&&!paper)||document.hidden)return;
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
  window.addEventListener('hashchange',()=>{const [view,query]=window.location.hash.slice(1).split('?');const herePaper=!view||view==='market'||view==='signals'&&new URLSearchParams(query||'').get('pane')==='reclaim';if(paper?!herePaper:view!=='monday-fib')cancel();});
  document.addEventListener('visibilitychange',()=>{if(document.hidden)cancel();});
  setTimeout(cancel,15000);
  if(paper){
    start('paper-snapshot','/api/monday-reclaim-paper')?.then(packet=>{
      const symbol=paperSymbol(packet?.value,params);if(!symbol)return;
      const candidate=params.get('period'),period=['5m','15m','1H','4H'].includes(candidate)?candidate:'5m';
      start('levels:'+symbol,'/api/monday-fib/levels?symbol='+encodeURIComponent(symbol));
      start(symbol+'|'+period,'/api/monday-fib/candles?symbol='+encodeURIComponent(symbol)+'&period='+period+'&wait_ms=8000');
    });
  }else{
    start('monday-snapshot','/api/monday-fib/display')?.then(packet=>{
      const symbol=initialSymbol(packet?.value?.items,params.get('symbol')||'');
      if(!symbol||!/^[A-Z0-9]{1,32}-USDT-SWAP$/.test(symbol))return;
      let period='15m';try{period=window.localStorage.getItem('spike.monday.timeframe')||period;}catch{}
      if(!['1m','5m','15m','30m','1H','4H','1D'].includes(period))period='15m';
      start(symbol+'|'+period,'/api/monday-fib/candles?symbol='+encodeURIComponent(symbol)+'&period='+period+'&wait_ms=8000');
    });
  }
  const critical=['workspace-base.css','workspace-shell.css','workspace-polish.css','workspace-boot.js'];
  const page=paper?['monday-reclaim-paper.css','reclaim-chart.js','reclaim-watchlist.js','reclaim-analytics.js','reclaim-close.js','monday-reclaim-paper.js','signal-workspace.js']:['monday-fib.css','monday-quick.js','monday-fib.js'];
  const names=[...critical,...page,'public-chart-cache.js','range-chart.js','monday-candle-feed.js','monday-fib-chart.js','monday-list-model.js','/vision-assets/vendor/lightweight-charts.standalone.production.js'];
  for(const name of names){
    if(!versions[name])continue;
    const link=document.createElement('link');link.rel='preload';link.as=name.endsWith('.css')?'style':'script';
    if(critical.includes(name))link.fetchPriority='high';
    link.href=(name.startsWith('/')?name:'/static/'+name)+'?v='+versions[name];
    document.head.append(link);
  }
})();
