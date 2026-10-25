/* Stitch 0d0dbd6c39834be2b78d6f44a5ec0f9d: the monitored universe is
 * independent of paper executions. Public Monday quotes own list membership;
 * only funded positions in the selected track own holding/trade annotations.
 */
(() => {
  'use strict';
  const validSymbol=value=>/^[A-Z0-9]{1,32}-USDT-SWAP$/.test(String(value||''));
  const positive=value=>Number.isFinite(value)&&value>0;
  const funded=p=>p?.taken===1&&positive(p.notional);
  const excluded=new Set(['BTC','ETH','XAU','XAG','PAXG','XAUT']);
  const base=symbol=>symbol.slice(0,-10);
  const esc=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const price=value=>positive(value)?value.toLocaleString('en-US',{maximumSignificantDigits:7}):'—';
  function validate(data){
    if(!Number.isFinite(data?.now_ms)||!Array.isArray(data.items)||!data.items.every(i=>i&&validSymbol(i.symbol)&&typeof i.state==='string')||new Set(data.items.map(i=>i.symbol)).size!==data.items.length)throw Error('监控币种返回格式不完整');
    return data;
  }
  function nearest(item){
    if(item.state!=='ready'||item.stale||!positive(item.price))return null;
    return [['上沿',item.upper],['下沿',item.lower]].filter(([,v])=>positive(v)).map(([edge,v])=>({edge,distance:Math.abs(v/item.price-1)*100})).sort((a,b)=>a.distance-b.distance)[0]||null;
  }
  function model(data,track,{q='',filter='all'}={}){
    const holdings=new Set((track?.open||[]).filter(funded).map(p=>p.symbol));
    const all=(data?.items||[]).map(item=>({...item,nearest:nearest(item),holding:holdings.has(item.symbol),touched:!!(item.touches?.upper||item.touches?.lower),observe:excluded.has(base(item.symbol))}));
    const query=q.trim().toUpperCase();
    return {total:all.length,touched:all.filter(i=>i.touched).length,holding:all.filter(i=>i.holding).length,
      items:all.filter(i=>(!query||i.symbol.includes(query))&&(filter==='holding'?i.holding:filter==='touched'?i.touched:true)).sort((a,b)=>(a.nearest?.distance??Infinity)-(b.nearest?.distance??Infinity)||a.symbol.localeCompare(b.symbol))};
  }
  function coinTrade(track,symbol){
    return [...(track?.open||[]),...(track?.closed||[]),...(track?.latest_funded?[track.latest_funded]:[])].filter(p=>p.symbol===symbol&&funded(p)&&[1,-1].includes(p.side)&&positive(p.fill_ms)&&positive(p.entry)).sort((a,b)=>b.fill_ms-a.fill_ms||b.id-a.id)[0]||null;
  }
  function rowHTML(item,selected){
    const coin=base(item.symbol),status=item.holding?'持仓':item.observe?'仅观察':item.touched?'已触价':'待触价';
    const distance=item.nearest?`距${item.nearest.edge} ${item.nearest.distance.toFixed(2)}%`:item.stale?'报价待更新':item.state==='ready'?'暂无报价':item.state==='missing_monday'?'周一数据不足':'区间准备中';
    return `<button type="button" class="rp-watch-row" data-watch-symbol="${esc(item.symbol)}" aria-pressed="${item.symbol===selected}" aria-label="查看 ${esc(coin)} K 线"><span class="rp-avatar" aria-hidden="true"><span>${esc(coin.slice(0,1))}</span><img src="https://static.okx.com/cdn/oksupport/asset/currency/icon/${encodeURIComponent(coin.toLowerCase())}.png" alt="" loading="lazy" referrerpolicy="no-referrer"></span><span class="rp-watch-name"><b>${esc(coin)}</b><small>${distance}</small></span><span class="rp-watch-quote"><b>${price(item.price)}</b><small class="${item.holding?'rp-positive':''}">${status}${item.stale&&positive(item.price)?' · 上次报价':''}</small></span></button>`;
  }
  const api={validSymbol,validate,nearest,model,coinTrade,rowHTML};
  if(typeof module!=='undefined'&&module.exports){module.exports=api;return;}
  function create({root,createFeed,getContext,onSelect,onOpen}){
    const trigger=root.querySelector('[data-rp-action="watch"]');
    const panel=document.createElement('section');panel.id='rp-watch-panel';panel.className='rp-watch-panel rp-glass';panel.setAttribute('aria-label','监控币种列表');
    panel.innerHTML=`<header class="rp-record-heading" data-yd-sheet-drag><strong>监控币种 <span data-watch="total">—</span></strong><button type="button" data-watch-action="close" aria-label="收起监控币种">×</button></header><div class="rp-watch-tools"><div class="rp-tabs" role="group" aria-label="监控币种筛选">${[['all','全部'],['touched','已触价'],['holding','持仓']].map(([key,label])=>`<button type="button" data-watch-filter="${key}" aria-pressed="false">${label} <span data-watch="${key}-count">—</span></button>`).join('')}</div><input type="search" placeholder="搜索监控币种" aria-label="搜索监控币种" maxlength="80" autocomplete="off"><p class="rp-watch-sort">按距最近 1.618 排序 · 当前轨道持仓</p></div><div class="rp-watch-scroll"><div data-watch="rows"></div><button type="button" data-watch-action="more" hidden>再显示 50 个</button></div><p class="rp-watch-status" role="status"></p>`;
    root.append(panel);
    let open=!matchMedia('(max-width:700px)').matches,filter='all',q='',shown=50,signature='';
    const requests=window.SpikeRuntime.createRequests();
    const feed=createFeed({read:()=>requests.json('/api/monday-fib?compact=true'),validateData:validate,cancel:()=>requests.cancel(),change:render});
    const sheet=window.SpikeYoloPreviewSheet.bind({panel,isOpen:()=>open,dismiss:()=>setOpen(false)});
    const el=id=>panel.querySelector(`[data-watch="${id}"]`);
    function setOpen(value,focus=true){sheet.reset();open=value;panel.classList.toggle('is-open',value);panel.inert=!value;panel.setAttribute('aria-hidden',String(!value));trigger.setAttribute('aria-expanded',String(value));if(value){onOpen?.();render();}else if(focus)trigger.focus({preventScroll:true});}
    function render(){
      const {data,error,busy}=feed.getState(),context=getContext(),view=model(data,context.track,{q,filter});
      trigger.querySelector('span').textContent=data?view.total:'—';el('total').textContent=data?view.total:'—';
      for(const [key,count] of [['all',view.total],['touched',view.touched],['holding',view.holding]])el(key+'-count').textContent=data?count:'—';
      panel.querySelectorAll('[data-watch-filter]').forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.watchFilter===filter)));
      panel.querySelector('.rp-watch-status').textContent=error?`同步失败 · ${error}${data?' · 保留上次记录':''}`:!data?busy?'正在读取监控币种…':'等待监控数据':`${data.stream?.connected?'行情在线':'行情重连中'} · ${new Date(data.now_ms).toLocaleTimeString('zh-CN',{hour12:false})} 更新${q||filter!=='all'?` · ${view.items.length} 个符合筛选`:''}`;
      if(!open)return;
      const html=view.items.slice(0,shown).map(item=>rowHTML(item,context.symbol)).join('')||`<div class="rp-empty">${!data?(error?'读取失败，请刷新重试':'正在读取…'):q?'没有匹配的币种':filter==='holding'?'当前轨道暂无资金持仓':filter==='touched'?'本周暂无触价币种':'监控池暂无币种'}</div>`;
      if(html!==signature){const focus=document.activeElement?.closest?.('[data-watch-symbol]')?.dataset.watchSymbol;el('rows').innerHTML=html;signature=html;if(focus)el('rows').querySelector(`[data-watch-symbol="${focus}"]`)?.focus({preventScroll:true});}
      panel.querySelector('[data-watch-action="more"]').hidden=view.items.length<=shown;
    }
    panel.addEventListener('input',event=>{if(event.target.matches('input')){q=event.target.value;shown=50;panel.querySelector('.rp-watch-scroll').scrollTop=0;render();}});
    panel.addEventListener('click',event=>{const b=event.target.closest('button');if(!b)return;if(b.dataset.watchFilter){filter=b.dataset.watchFilter;shown=50;panel.querySelector('.rp-watch-scroll').scrollTop=0;render();}if(b.dataset.watchSymbol){onSelect(b.dataset.watchSymbol);render();if(matchMedia('(max-width:700px)').matches)setOpen(false);}if(b.dataset.watchAction==='close')setOpen(false);if(b.dataset.watchAction==='more'){shown+=50;render();}});
    panel.addEventListener('keydown',event=>{if(event.key==='Escape'&&open){event.stopPropagation();setOpen(false);}});
    setOpen(open,false);
    return {render,setOpen,toggle:()=>setOpen(!open),refresh:feed.refresh,setActive(value){if(!value)sheet.reset();feed.setActive(value);}};
  }
  window.SpikeReclaimWatchlist={...api,create};
})();
