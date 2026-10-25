/* Shared Monday list policy: actual touch chronology, favorites and weekly dismissals. */
(() => {
  'use strict';
  const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const finite=v=>typeof v==='number'&&Number.isFinite(v);
  function inspectionURL(symbol,period){
    if(!/^[A-Z0-9]{1,32}-USDT-SWAP$/.test(symbol||''))return '#yolo-detect?tab=results';
    const timeframe=period==='1D'?'1Dutc':['5m','15m','30m','1H','4H','1Dutc'].includes(period)?period:'5m';
    return '#yolo-detect?'+new URLSearchParams({tab:'results',symbol,timeframe});
  }
  const priceFormats=new Map();
  const price=v=>{if(!finite(v))return '—';const digits=Math.min(12,Math.max(2,6-Math.floor(Math.log10(Math.abs(v)||1))));if(!priceFormats.has(digits))priceFormats.set(digits,new Intl.NumberFormat('en-US',{maximumFractionDigits:digits}));return priceFormats.get(digits).format(v);};
  const STATES={loading:'正在同步',developing:'周一尚未结束',missing_monday:'缺少完整周一',missing_history:'历史触价待核对',recovering:'正在核对断线区间',fetch_error:'数据暂不可用',invalid_range:'周一区间无效'};
  const FILTERS={all:'全部',upper:'上沿',lower:'下沿',favorites:'收藏'},RECENT_MS=15*60000;
  const touchTime=e=>e?(e.source==='history'?e.candle_ms:e.observed_ms)||0:0;
  const latestTouch=r=>{let result=null;for(const [side,e] of Object.entries(r.touches||{}))if(!result||touchTime(e)>touchTime(result))result={...e,side};return result;};
  const recentTouch=(e,now)=>!!e&&e.source==='live'&&finite(e.observed_ms)&&now>=e.observed_ms&&now-e.observed_ms<RECENT_MS;
  function selectRows(items,q='',filter='all',now=Date.now(),favorites=new Set()) {
    const query=q.trim().toUpperCase().replace(/[^A-Z0-9]/g,'');
    return items.map(r=>({row:r,event:latestTouch(r)})).filter(({row:r,event:e})=>(filter==='favorites'?favorites.has(r.symbol):!!e)&&(!query||r.symbol.replace(/[^A-Z0-9]/g,'').includes(query))&&(
      filter==='recent'?recentTouch(e,now):filter==='upper'||filter==='lower'?!!r.touches?.[filter]:true))
      .sort((a,b)=>touchTime(b.event)-touchTime(a.event)||a.row.symbol.localeCompare(b.row.symbol)).map(x=>x.row);
  }
  function selection(items,symbol,follow){return !follow&&symbol?items.find(r=>r.symbol===symbol)||null:selectRows(items)[0]||null;}
  // A server touch is unique by NY week/symbol/side. Quote refreshes and history
  // reconciliation must not resurrect a locally dismissed occurrence.
  function createListPreferences(storage){
    const key='spike:monday-fib:list:v1',valid=s=>typeof s==='string'&&/^[A-Z0-9]{1,30}-USDT-SWAP$/.test(s);
    let favorites=new Set(),hidden={},persistent=true;
    function read(){try{
      const data=JSON.parse(storage?.getItem(key)||'null');if(!data||data.version!==1){favorites=new Set();hidden={};return;}
      favorites=new Set((Array.isArray(data.favorites)?data.favorites:[]).filter(valid).slice(0,5000));hidden={};
      for(const week of Object.keys(data.hidden||{}).filter(w=>/^\d{4}-\d{2}-\d{2}$/.test(w)).sort().slice(-4)){
        hidden[week]=new Set((Array.isArray(data.hidden[week])?data.hidden[week]:[]).filter(s=>typeof s==='string'&&/^[A-Z0-9]{1,30}-USDT-SWAP:(upper|lower)$/.test(s)).slice(0,10000));
      }
    }catch{persistent=false;}}
    function write(){try{
      if(!storage)throw Error('storage unavailable');
      hidden=Object.fromEntries(Object.entries(hidden).sort(([a],[b])=>a.localeCompare(b)).slice(-4));
      storage.setItem(key,JSON.stringify({version:1,favorites:[...favorites],hidden:Object.fromEntries(Object.entries(hidden).map(([w,s])=>[w,[...s]]))}));persistent=true;
    }catch{persistent=false;}return persistent;}
    read();
    return {key,read,get persistent(){return persistent;},favorites:()=>new Set(favorites),has:s=>favorites.has(s),
      toggle(symbol){if(!valid(symbol))return false;if(favorites.has(symbol))favorites.delete(symbol);else favorites.add(symbol);return write();},
      visible(items,week){return items.flatMap(row=>{
        const original=Object.entries(row.touches||{}),touches=Object.fromEntries(original.filter(([side])=>!hidden[week]?.has(row.symbol+':'+side)));
        return original.length&&!Object.keys(touches).length?[]:[{...row,touches}];
      });},
      remove(row,week){if(!valid(row.symbol)||!/^\d{4}-\d{2}-\d{2}$/.test(week))return null;
        const keys=Object.keys(row.touches||{}).filter(s=>['upper','lower'].includes(s)).map(s=>row.symbol+':'+s);
        const undo={symbol:row.symbol,week,keys,favorite:!keys.length&&favorites.has(row.symbol)};
        if(keys.length){hidden[week]??=new Set();undo.keys=keys.filter(k=>!hidden[week].has(k));for(const k of keys)hidden[week].add(k);}
        else favorites.delete(row.symbol);
        write();return undo;
      },
      undo(value){if(!value)return;for(const k of value.keys)hidden[value.week]?.delete(k);if(value.favorite)favorites.add(value.symbol);write();}
    };
  }
  // Stitch 2e8ce287d9fa44cdbf3036bdf6000937: Windows dispatches locally.
  // Ordinary HTTPS links stay in the browser even with Windows app associations.
  function tradingViewURL(symbol,platform=''){
    if(typeof symbol!=='string'||!/^[A-Z0-9]{1,30}-USDT-SWAP$/.test(symbol))return '';
    if(/^Win/i.test(platform))return 'spike-tradingview://open?symbol='+encodeURIComponent(symbol)+'&timeframe=15m';
    const ticker='OKX:'+symbol.replace('-SWAP','').replaceAll('-','')+'.P';
    return 'https://www.tradingview.com/chart/?symbol='+encodeURIComponent(ticker)+'&interval=15';
  }
  // Anchor the first visible row so live reordering does not move the reader.
  function captureListScroll(container){
    const top=container.scrollTop||0;
    const first=top>0?Array.from(container.querySelectorAll('[data-symbol]')).find(b=>b.offsetTop+b.offsetHeight>top):null;
    return {top,symbol:first?.dataset.symbol,offset:first?first.offsetTop-top:0};
  }
  function restoreListScroll(container,position){
    const row=position.symbol?Array.from(container.querySelectorAll('[data-symbol]')).find(b=>b.dataset.symbol===position.symbol):null;
    container.scrollTop=Math.max(0,row?row.offsetTop-position.offset:position.top);
  }
  const api={esc,finite,price,selectRows,selection,latestTouch,touchTime,recentTouch,RECENT_MS,STATES,FILTERS,captureListScroll,restoreListScroll,tradingViewURL,createListPreferences,inspectionURL};
  if(typeof module!=='undefined'&&module.exports)module.exports=api;
  else window.SpikeMondayList=api;
})();
