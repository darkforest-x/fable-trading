/* Shared display-only chart interaction, Lightweight Charts 4.2.
 * Stitch: 8978791379095391159 / 84121c9f6b5b461993837bca2b1ce811.
 * Time anchors preserve the viewed candles when rolling history changes.
 * No data fetching beyond the renderer library; no trading or indicator calculations.
 */
(() => {
  'use strict';
  const finite=Number.isFinite;
  const lines=Object.freeze([
    {key:'sma20',label:'SMA 20',color:'#c0c1ff',period:20},{key:'sma60',label:'SMA 60',color:'#00e599',period:60},
    {key:'sma120',label:'SMA 120',color:'#cbd5e1',period:120},{key:'ema20',label:'EMA 20',color:'#9d7cff',period:20},
    {key:'ema60',label:'EMA 60',color:'#f59e0b',period:60},{key:'ema120',label:'EMA 120',color:'#ff3366',period:120},
  ]);
  const colors={up:'#01e599',down:'#ff4f73'};
  const price=v=>finite(v)?v.toLocaleString('en-US',{maximumFractionDigits:Math.abs(v)>=100?2:Math.abs(v)>=1?4:10}):'—';
  const axisPrice=v=>finite(v)?v.toLocaleString('en-US',{maximumFractionDigits:10}):'—';
  // Compact axis labels are display-only. Full OHLC readouts use axisPrice.
  const compactAxisPrice=v=>{
    if(!finite(v))return '—';
    const abs=Math.abs(v);
    if(abs!==0&&(abs<.001||abs>=1e7))return v.toExponential(2).replace('e+','e');
    const digits=abs>0&&abs<1?Math.max(3,7+Math.floor(Math.log10(abs))):6;
    return v.toLocaleString('en-US',{useGrouping:false,maximumSignificantDigits:digits});
  };
  const date=new Intl.DateTimeFormat('zh-CN',{timeZone:'Asia/Shanghai',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false});
  const clock=t=>finite(t)?date.format(new Date(t*1000)):'—';
  const times=rows=>rows.map(r=>r.time??r.t/1000);
  const validRange=r=>r&&finite(r.from)&&finite(r.to)&&r.to>r.from&&r.to-r.from<100000;
  function at(index,ts){const last=ts.length-1,i=Math.max(0,Math.min(last-1,Math.floor(index))),step=ts.length>1?ts[i+1]-ts[i]:1;return ts[i]+(index-i)*step;}
  function indexAt(t,ts){if(ts.length===1)return t-ts[0];let lo=0,hi=ts.length-1;while(lo+1<hi){const m=(lo+hi)>>1;if(ts[m]<=t)lo=m;else hi=m;}return lo+(t-ts[lo])/(ts[hi]-ts[lo]);}
  function anchor(range,ts){if(!validRange(range)||!ts.length)return null;return {from:at(range.from,ts),to:at(range.to,ts),span:range.to-range.from,right:range.to-(ts.length-1),follow:range.to>=ts.length-1&&range.from<ts.length};}
  function restore(saved,ts){if(!saved||!ts.length)return null;const to=saved.follow?ts.length-1+saved.right:indexAt(saved.to,ts),from=saved.follow?to-saved.span:indexAt(saved.from,ts);return validRange({from,to})&&to>=0&&from<ts.length?{from,to}:null;}
  class Views {
    constructor(limit=40){this.values=new Map();this.limit=limit;}
    save(key,range,ts){const value=anchor(range,ts);if(!key||!value)return;this.values.delete(key);this.values.set(key,value);while(this.values.size>this.limit)this.values.delete(this.values.keys().next().value);}
    get(key,ts){return restore(this.values.get(key),ts);}
  }
  function priceFormat(rows,extra=[]){const prices=rows.flatMap(r=>[r.low??r.l,r.high??r.h,r.close??r.c]).concat(extra).filter(v=>finite(v)&&v>0),min=prices.length?Math.min(...prices):1,precision=Math.min(10,Math.max(2,4-Math.floor(Math.log10(min))));return {type:'price',precision,minMove:10**-precision};}
  function frame(fn){let id=null,args;const run=(...values)=>{args=values;if(id===null)id=requestAnimationFrame(()=>{id=null;fn(...args);});};run.cancel=()=>{if(id!==null)cancelAnimationFrame(id);id=null;};return run;}
  function resize(chart,host,{compactAxis=false}={}){
    let w=0,h=0,narrow;
    const paint=frame(()=>{
      const width=Math.round(host.clientWidth),height=Math.round(host.clientHeight);
      if(width<=0||height<=0||(width===w&&height===h))return;
      const range=chart.timeScale().getVisibleLogicalRange();w=width;h=height;
      if(compactAxis&&narrow!==(w<=700)){
        narrow=w<=700;
        chart.applyOptions({localization:{priceFormatter:narrow?compactAxisPrice:axisPrice}});
      }
      chart.resize(w,h);
      if(validRange(range))chart.timeScale().setVisibleLogicalRange(range);
    }),observer=new ResizeObserver(paint);
    observer.observe(host);paint();
    return {disconnect(){observer.disconnect();paint.cancel();}};
  }
  function syncer(){const snapshots=new WeakMap(),same=(a,b)=>a===b||!!a&&!!b&&Object.keys(a).length===Object.keys(b).length&&Object.keys(a).every(k=>Object.is(a[k],b[k]));return (series,rows)=>{const old=snapshots.get(series);if(old?.length===rows.length&&rows.every((r,i)=>same(r,old[i])))return;const append=old?.length&&rows.length>=old.length&&old.slice(0,-1).every((r,i)=>same(r,rows[i]))&&rows[old.length-1]?.time===old.at(-1).time;if(append&&series.update){for(let i=old.length-1;i<rows.length;i++)if(!same(rows[i],old[i]))series.update(rows[i]);}else series.setData(rows);snapshots.set(series,rows);};}
  const interaction={handleScroll:{vertTouchDrag:false,horzTouchDrag:true,mouseWheel:true,pressedMouseMove:true},handleScale:{axisPressedMouseMove:true,mouseWheel:true,pinch:true},kineticScroll:{mouse:false,touch:true}};
  function readout(row,t,latest=false){return `<span class="cv-time">${latest?'最新 K 线':'光标'} · ${clock(t)} 北京开盘</span>${[['开','open','o'],['高','high','h'],['低','low','l'],['收','close','c']].map(([label,key,short])=>`<span>${label} <b>${axisPrice(row?.[key]??row?.[short])}</b></span>`).join('')}`;}
  function toolbar({reset=false,expand=false,log=true}={}){return `<div class="cv-tools" role="group" aria-label="看图工具"><button type="button" data-cv="latest" title="定位最后一根已载入 K 线，保留缩放宽度">定位最新</button><button type="button" data-cv="fit">适应全部</button>${reset?'<button type="button" data-cv="reset">还原视图</button>':''}${log?'<button type="button" data-cv="log" aria-pressed="false" title="切换价格轴；不改变行情数据">对数</button>':''}${expand?'<button type="button" data-cv="expand" aria-pressed="false">放大</button>':''}</div>`;}
  // Expand in place: reparenting an embedded TradingView iframe reloads it.
  function fullscreen(panel,button){
    const doc=panel.ownerDocument,previous=doc.activeElement,oldOverflow=doc.body.style.overflow,isolated=new Map();
    const role=panel.getAttribute('role'),modal=panel.getAttribute('aria-modal');
    const dialog=panel.closest('dialog[open]');dialog?.classList.add('cv-dialog-host');
    const isolate=()=>{for(let node=panel;node;){const parent=node.parentElement;if(parent)for(const sibling of parent.children)if(sibling!==node&&!['SCRIPT','STYLE','LINK','HEAD'].includes(sibling.tagName)){if(!isolated.has(sibling))isolated.set(sibling,sibling.inert);sibling.inert=true;}node=parent||node.getRootNode()?.host;}};
    isolate();
    // Status polling may replace siblings and detach/reinsert the preserved chart.
    const mutations=new MutationObserver(records=>{if(!panel.isConnected||!records.some(r=>!panel.contains(r.target)))return;isolate();const active=panel.getRootNode().activeElement||doc.activeElement;if(!panel.contains(active))button.focus({preventScroll:true});});
    mutations.observe(dialog||panel.parentElement,{childList:true,subtree:true});
    panel.classList.add('cv-expanded');panel.setAttribute('role','dialog');panel.setAttribute('aria-modal','true');doc.body.style.overflow='hidden';button.setAttribute('aria-pressed','true');button.textContent='退出放大';
    let closed=false;const close=()=>{if(closed)return;closed=true;mutations.disconnect();panel.classList.remove('cv-expanded');dialog?.classList.remove('cv-dialog-host');for(const [node,inert] of isolated)node.inert=inert;for(const [key,value] of [['role',role],['aria-modal',modal]])value===null?panel.removeAttribute(key):panel.setAttribute(key,value);doc.body.style.overflow=oldOverflow;button.setAttribute('aria-pressed','false');button.textContent='放大';doc.removeEventListener('keydown',key,true);if(previous?.isConnected)previous.focus({preventScroll:true});};
    const key=e=>{if(e.key==='Escape'){e.preventDefault();e.stopPropagation();close();}if(e.key==='Tab'){const controls=[...panel.querySelectorAll('button:not(:disabled),a[href],input:not(:disabled),select:not(:disabled),[tabindex="0"]')].filter(n=>n.getClientRects().length),active=panel.getRootNode().activeElement||doc.activeElement,first=controls[0],last=controls.at(-1);if(e.shiftKey&&active===first){e.preventDefault();last?.focus();}else if(!e.shiftKey&&active===last){e.preventDefault();first?.focus();}}};
    doc.addEventListener('keydown',key,true);button.focus({preventScroll:true});return close;
  }
  function bind(root,{chart,rows,reset,panel,canvas,readoutNode}={}){
    let closeFullscreen=null;
    root.querySelector('[data-cv=log]')?.setAttribute('aria-pressed','false');
    const click=e=>{const b=e.target.closest?.('[data-cv]');if(!b||!root.contains(b))return;const c=chart();if(!c)return;const values=rows(),n=values.length,scale=c.timeScale(),action=b.dataset.cv;
      if(action==='latest'&&n){const range=scale.getVisibleLogicalRange(),width=validRange(range)?range.to-range.from:80;c.priceScale('right').applyOptions({autoScale:true});scale.setVisibleLogicalRange({from:n-1+5-width,to:n-1+5});}
      if(action==='fit'){c.priceScale('right').applyOptions({autoScale:true});scale.fitContent();}
      if(action==='reset'){c.priceScale('right').applyOptions({autoScale:true});reset?.();}
      if(action==='log'){const next=b.getAttribute('aria-pressed')!=='true';if(next&&values.some(r=>(r.low??r.l)<=0))return;c.priceScale('right').applyOptions({mode:next?1:0,autoScale:true});b.setAttribute('aria-pressed',String(next));}
      if(action==='expand'&&panel){if(panel.classList.contains('cv-expanded')){closeFullscreen?.();closeFullscreen=null;}else closeFullscreen=fullscreen(panel,b);}
    };
    root.addEventListener('click',click);
    const keyboard=e=>{if(e.target!==canvas||e.ctrlKey||e.metaKey||e.altKey||e.isComposing)return;const c=chart(),range=c?.timeScale().getVisibleLogicalRange();if(!validRange(range))return;const width=range.to-range.from;if(['ArrowLeft','ArrowRight','+','-','=','Home','End'].includes(e.key)){e.preventDefault();if(e.key==='Home'){c.priceScale('right').applyOptions({autoScale:true});c.timeScale().fitContent();}else if(e.key==='End')root.querySelector('[data-cv=latest]')?.click();else{const zoom=['+','-','='].includes(e.key),delta=width*(e.key==='ArrowLeft'?-.15:.15),next=width*(e.key==='-'?1.25:.8);c.timeScale().setVisibleLogicalRange(zoom?{from:range.to-next,to:range.to}:{from:range.from+delta,to:range.to+delta});}}};
    if(canvas){canvas.tabIndex=0;canvas.setAttribute('aria-label','K 线交互图；左右键平移，加减键缩放，End 定位最新，Home 适应全部');canvas.addEventListener('keydown',keyboard);}
    return {update(){root.querySelectorAll('[data-cv]').forEach(b=>b.disabled=!chart());},destroy(){root.removeEventListener('click',click);canvas?.removeEventListener('keydown',keyboard);closeFullscreen?.();closeFullscreen=null;}};
  }
  function inspector(root,chart,series,{rows,canvas,panel}={}){
    root.classList.add('cv-inspector');root.innerHTML=toolbar({expand:!!panel})+'<div class="cv-readout"></div><div class="cv-levels"></div>';
    const controls=bind(root,{chart:()=>chart,rows,canvas,panel});
    let hovered=null;
    const update=()=>{const values=rows(),row=values.find(r=>(r.time??r.t/1000)===hovered)||values.at(-1),time=row?.time??row?.t/1000;
      root.querySelector('.cv-readout').innerHTML=row?readout(row,time,hovered!==time):'';
      root.querySelector('.cv-levels').innerHTML=lines.map(l=>`<span style="color:${l.color}">${l.label} <b>${price(row?.[l.key])}</b></span>`).join('');controls.update();};
    const crosshair=frame(p=>{hovered=p?.time&&p.seriesData?.has(series)?p.time:null;update();});chart.subscribeCrosshairMove(crosshair);update();
    return {update,destroy(){crosshair.cancel();chart.unsubscribeCrosshairMove(crosshair);controls.destroy();root.replaceChildren();}};
  }
  let pending;
  const scriptBase=typeof document!=='undefined'&&document.currentScript?.src;
  let libraryVersion='';
  try{libraryVersion=JSON.parse(document.getElementById('asset-versions')?.textContent||'{}')['/vision-assets/vendor/lightweight-charts.standalone.production.js']||'';}catch{}
  function library(){if(window.LightweightCharts)return Promise.resolve();if(!pending)pending=new Promise((resolve,reject)=>{const el=document.createElement('script');el.src=libraryVersion?'/vision-assets/vendor/lightweight-charts.standalone.production.js?v='+libraryVersion:scriptBase?new URL('vendor/lightweight-charts.standalone.production.js',scriptBase).href:'/vision-assets/vendor/lightweight-charts.standalone.production.js';el.onload=()=>window.LightweightCharts?resolve():reject(Error('图表组件不可用'));el.onerror=()=>{pending=null;el.remove();reject(Error('图表组件加载失败，请重试'));};document.head.append(el);});return pending;}
  const api={lines,colors,price,axisPrice,compactAxisPrice,priceFormat,clock,times,anchor,restore,Views,validRange,resize,frame,syncer,interaction,readout,toolbar,bind,inspector,fullscreen,library};
  if(typeof window!=='undefined')window.SpikeChartTools=api;
  if(typeof module!=='undefined')module.exports=api;
})();
