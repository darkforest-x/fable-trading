/* Stitch f6ca485dbc9440bcabc2a9058a9d4768. Monday owns list membership,
 * occurrence ordering and shared preferences; the paper ledger only owns fills. */
(() => {
  'use strict';
  const list=typeof module!=='undefined'&&module.exports?require('./monday-list-model.js'):window.SpikeMondayList;
  const {esc,price,latestTouch,touchTime,selectRows,createListPreferences,FILTERS,STATES,tradingViewURL,inspectionURL,captureListScroll,restoreListScroll}=list;
  const validSymbol=value=>/^[A-Z0-9]{1,32}-USDT-SWAP$/.test(String(value||''));
  const positive=value=>Number.isFinite(value)&&value>0,base=symbol=>symbol.slice(0,-10);
  const date=new Intl.DateTimeFormat('zh-CN',{timeZone:'Asia/Shanghai',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hourCycle:'h23'});
  function validate(data){
    if(!Number.isFinite(data?.now_ms)||!/^\d{4}-\d{2}-\d{2}$/.test(data.week)||!Array.isArray(data.items)||!data.items.every(i=>i&&validSymbol(i.symbol)&&typeof i.state==='string')||new Set(data.items.map(i=>i.symbol)).size!==data.items.length)throw Error('触价列表返回格式不完整');
    return data;
  }
  function model(data,preferences,{q='',filter='all'}={}){
    const visible=preferences.visible(data?.items||[],data?.week),favorites=preferences.favorites();
    return {total:selectRows(visible).length,items:selectRows(visible,q,filter,data?.now_ms,favorites)};
  }
  function coinTrade(track,symbol){
    return [...(track?.open||[]),...(track?.closed||[]),...(track?.latest_funded?[track.latest_funded]:[])].filter(p=>p.symbol===symbol&&p.taken===1&&positive(p.notional)&&[1,-1].includes(p.side)&&positive(p.fill_ms)&&positive(p.entry)).sort((a,b)=>b.fill_ms-a.fill_ms||b.id-a.id)[0]||null;
  }
  function rowHTML(item,selected,{favorite=false,now=Date.now(),error=false,platform='',period='5m'}={}){
    const coin=base(item.symbol),event=latestTouch(item),stamp=touchTime(event),side=event?.side;
    const warning=STATES[item.state]||((item.stale||!Number.isFinite(item.received_ms)||now-item.received_ms>=90000||error)?'行情已滞后':'');
    const icon=name=>`<span class="rp-watch-icon" aria-hidden="true" style="--watch-icon:url(/static/icons/${name}.svg)"></span>`;
    return `<article class="rp-watch-row" data-symbol="${esc(item.symbol)}" data-selected="${item.symbol===selected}"><div class="rp-watch-top"><span class="rp-avatar" aria-hidden="true"><span>${esc(coin.slice(0,1))}</span><img src="https://static.okx.com/cdn/oksupport/asset/currency/icon/${encodeURIComponent(coin.toLowerCase())}.png" alt="" loading="lazy" referrerpolicy="no-referrer"></span><a class="rp-watch-name" href="${esc(tradingViewURL(item.symbol,platform))}" target="_blank" rel="noopener" aria-label="在 TradingView 打开 ${esc(coin)}">${esc(coin)}<small>↗</small></a><button class="rp-watch-quote" data-watch-select="${esc(item.symbol)}" aria-label="查看 ${esc(coin)} K 线" aria-pressed="${item.symbol===selected}"><span class="${side==='upper'?'rp-negative':side==='lower'?'rp-positive':''}">${side==='upper'?'上沿 1.618':side==='lower'?'下沿 1.618':'尚未触价'}</span><b>${price(item.price)}</b></button></div><div class="rp-watch-bottom"><span>${warning|| (stamp?date.format(stamp)+' 北京':'收藏币种')}</span><a href="${esc(inspectionURL(item.symbol,period))}" aria-label="查看 ${esc(coin)} 形态巡检" title="形态巡检">${icon('activity')}</a><button data-watch-action="favorite" aria-pressed="${favorite}" aria-label="${favorite?'取消收藏':'收藏'} ${esc(coin)}">${icon('star')}</button><button data-watch-action="remove" aria-label="从本周列表移除 ${esc(coin)}" title="从本周列表移除">${icon('trash')}</button></div></article>`;
  }
  const api={validSymbol,validate,model,coinTrade,rowHTML};
  if(typeof module!=='undefined'&&module.exports){module.exports=api;return;}
  function create({root,createFeed,getContext,onSelect,onOpen}){
    const trigger=root.querySelector('[data-rp-action="watch"]'),panel=document.createElement('section');
    panel.id='rp-watch-panel';panel.className='rp-watch-panel rp-glass';panel.setAttribute('aria-label','最新触价币种列表');
    panel.innerHTML=`<header class="rp-record-heading" data-yd-sheet-drag><strong>最新触价 <span data-watch="total">—</span></strong><button type="button" data-watch-action="close" aria-label="收起触价列表">×</button></header><div class="rp-watch-tools"><div class="rp-tabs" role="group" aria-label="触价与收藏筛选">${Object.entries(FILTERS).map(([key,label])=>`<button type="button" data-watch-filter="${key}" aria-pressed="false">${label}</button>`).join('')}</div><input type="search" placeholder="搜索已触价合约" aria-label="搜索已触价合约" maxlength="80" autocomplete="off"><div class="rp-watch-feedback" hidden><span role="status"></span><button data-watch-action="undo">撤销</button></div></div><div class="rp-watch-scroll"><div data-watch="rows"></div><button type="button" data-watch-action="more" hidden>再显示 50 个</button></div><p class="rp-watch-status" role="status"></p>`;
    root.append(panel);
    let storage;try{storage=window.localStorage;}catch{}
    const preferences=createListPreferences(storage),platform=navigator.userAgentData?.platform||navigator.platform||'';
    let open=!matchMedia('(max-width:700px)').matches,filter='all',q='',shown=50,signature='',lastRemoval=null;
    const requests=window.SpikeRuntime.createRequests();
    const feed=createFeed({read:()=>requests.json('/api/monday-fib?compact=true'),validateData:validate,cancel:()=>requests.cancel(),change:render});
    const sheet=window.SpikeYoloPreviewSheet.bind({panel,isOpen:()=>open,dismiss:()=>setOpen(false)});
    const el=id=>panel.querySelector(`[data-watch="${id}"]`),scroll=panel.querySelector('.rp-watch-scroll');
    function setOpen(value,focus=true){sheet.reset();open=value;panel.classList.toggle('is-open',value);panel.inert=!value;panel.setAttribute('aria-hidden',String(!value));trigger.setAttribute('aria-expanded',String(value));if(value){preferences.read();onOpen?.();render();}else if(focus)trigger.focus({preventScroll:true});}
    function feedback(text,undo=false){const box=panel.querySelector('.rp-watch-feedback');box.hidden=!text;box.querySelector('span').textContent=text;box.querySelector('button').hidden=!undo;}
    function render(){
      const {data,error,busy}=feed.getState(),context=getContext(),view=model(data,preferences,{q,filter});
      trigger.querySelector('span').textContent=data?view.total:'—';el('total').textContent=data?view.items.length:'—';
      panel.querySelectorAll('[data-watch-filter]').forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.watchFilter===filter)));
      panel.querySelector('.rp-watch-status').textContent=error?`同步失败 · ${error}${data?' · 保留上次记录':''}`:!data?busy?'正在读取最新触价…':'等待触价数据':`${data.stream?.connected?'行情在线':'行情重连中'} · 最新触价优先 · ${view.items.length} 个`;
      if(!open)return;
      const html=view.items.slice(0,shown).map(item=>rowHTML(item,context.symbol,{favorite:preferences.has(item.symbol),now:data.now_ms,error,platform,period:context.period})).join('')||`<div class="rp-empty">${!data?(error?'读取失败，请刷新重试':'正在读取…'):q?'没有匹配的币种':filter==='favorites'?'尚未收藏币种':'本周暂无符合筛选的触价币种'}</div>`;
      if(html!==signature){
        const position=captureListScroll(scroll),focused=document.activeElement,focusRow=focused?.closest?.('[data-symbol]')?.dataset.symbol,action=focused?.dataset.watchAction,select=focused?.dataset.watchSelect;
        el('rows').innerHTML=html;signature=html;restoreListScroll(scroll,position);
        if(focusRow){const row=el('rows').querySelector(`[data-symbol="${focusRow}"]`);row?.querySelector(action?`[data-watch-action="${action}"]`:select?'[data-watch-select]':'.rp-watch-name')?.focus({preventScroll:true});}
      }
      panel.querySelector('[data-watch-action="more"]').hidden=view.items.length<=shown;
    }
    panel.addEventListener('input',event=>{if(event.target.matches('input')){q=event.target.value;shown=50;scroll.scrollTop=0;render();}});
    panel.addEventListener('click',event=>{
      const b=event.target.closest('button'),row=event.target.closest('[data-symbol]');
      if(b?.dataset.watchFilter){filter=b.dataset.watchFilter;shown=50;scroll.scrollTop=0;render();return;}
      const action=b?.dataset.watchAction;
      if(action==='favorite'){preferences.toggle(row.dataset.symbol);feedback(preferences.persistent?'':'收藏暂未保存，仅本次有效');render();return;}
      if(action==='remove'){const data=feed.getState().data,item=data.items.find(i=>i.symbol===row.dataset.symbol);lastRemoval=preferences.remove(item,data.week);feedback(preferences.persistent?'已从本周列表移除':'暂未保存，仅本次移除',!!lastRemoval);render();return;}
      if(action==='undo'){preferences.undo(lastRemoval);lastRemoval=null;feedback('');render();return;}
      if(action==='close')setOpen(false);if(action==='more'){shown+=50;render();}
      if(row&&!event.target.closest('a')&&!action){onSelect(row.dataset.symbol);render();if(matchMedia('(max-width:700px)').matches)setOpen(false);}
    });
    panel.addEventListener('keydown',event=>{if(event.key==='Escape'&&open){event.stopPropagation();setOpen(false);}});
    window.addEventListener('storage',event=>{if(event.key===preferences.key){preferences.read();render();}});
    setOpen(open,false);
    return {render,setOpen,toggle:()=>setOpen(!open),refresh:feed.refresh,setActive(value){if(value)preferences.read();else sheet.reset();feed.setActive(value);render();}};
  }
  window.SpikeReclaimWatchlist={...api,create};
})();
