/* Stitch b28188b2e438404593e684445325de54. The public catalog owns All;
 * Monday owns touch history. Account fills never determine list membership. */
(() => {
  'use strict';
  const list=typeof module!=='undefined'&&module.exports?require('./monday-list-model.js'):window.SpikeMondayList;
  const catalogModel=typeof module!=='undefined'&&module.exports?require('./symbol-list-model.js'):window.SpikeSymbolList;
  const {esc,price,latestTouch,touchTime,selectRows,createListPreferences,FILTERS,STATES,tradingViewURL,inspectionURL,captureListScroll,restoreListScroll}=list;
  const validSymbol=value=>/^[A-Z0-9]{1,32}-USDT-SWAP$/.test(String(value||''));
  const positive=value=>Number.isFinite(value)&&value>0,base=symbol=>symbol.slice(0,-10);
  const date=new Intl.DateTimeFormat('zh-CN',{timeZone:'Asia/Shanghai',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hourCycle:'h23'});
  function validate(data){
    if(!Number.isFinite(data?.now_ms)||!/^\d{4}-\d{2}-\d{2}$/.test(data.week)||!Array.isArray(data.items)||!data.items.every(i=>i&&validSymbol(i.symbol)&&typeof i.state==='string')||new Set(data.items.map(i=>i.symbol)).size!==data.items.length)throw Error('触价列表返回格式不完整');
    return data;
  }
  function model(data,preferences,{q='',filter='all',catalog=[],now=Date.now()}={}){
    const visible=preferences.visible(data?.items||[],data?.display_week||data?.week),favorites=preferences.favorites();
    if(filter==='all'||filter==='favorites'){
      const touches=new Map(visible.map(row=>[row.symbol,row])),quotes=new Map(catalog.map(row=>[row.symbol,row]));
      const symbols=catalog.filter(row=>filter!=='favorites'||favorites.has(row.symbol)).map(row=>row.symbol);
      return {total:catalog.length,items:catalogModel.rankedSymbols(symbols,quotes,'volume',q,now).map(symbol=>{
        const quote=quotes.get(symbol);
        return {...touches.get(symbol),symbol,catalog:true,price:catalogModel.number(quote,'last',now),volume:catalogModel.number(quote,'volume_usdt_24h',now),change:catalogModel.number(quote,'change_24h_percent',now),stale:!(quote.expires_ms>now)};
      })};
    }
    return {total:selectRows(visible).length,items:selectRows(visible,q,filter,data?.now_ms,favorites)};
  }
  const PINNED=Object.freeze(['BTC-USDT-SWAP','ETH-USDT-SWAP','XAU-USDT-SWAP']);
  const recentRows=(data,preferences)=>selectRows(preferences.visible(data?.items||[],data?.display_week||data?.week)).filter(item=>!PINNED.includes(item.symbol)).slice(0,20);
  const avatar=coin=>`<span class="rp-avatar" aria-hidden="true"><span>${esc(coin.slice(0,1))}</span><img src="https://static.okx.com/cdn/oksupport/asset/currency/icon/${encodeURIComponent(coin.toLowerCase())}.png" alt="" loading="lazy" referrerpolicy="no-referrer"></span>`;
  const touchLabel=side=>side==='upper'?'上沿':side==='lower'?'下沿':'尚未触价';
  const touchTone=side=>side==='upper'?'rp-negative':side==='lower'?'rp-positive':'';
  // The ribbon shows the model score of this exact NY-week/side/touch bar.
  // Never borrow an account fill or another occurrence of the same symbol.
  function touchScore(item,week,signals,now=Date.now()){
    const event=latestTouch(item),side=event?.side==='upper'?-1:event?.side==='lower'?1:0;
    const signal=positive(event?.candle_ms)?signals?.find(s=>s.week===week&&s.symbol===item.symbol&&s.side===side&&s.candle_ms===event.candle_ms):null;
    if(signal?.status==='scored'&&Number.isFinite(signal.score))return {text:signal.score.toFixed(4),title:`入场评分 ${signal.score} · 不是胜率`,high:signal.score>.5,scored:true};
    const text=signal?.status==='feature_error'?'评分失败':signal?.status==='excluded'?'不评分':event?.source==='live'&&now>=event.observed_ms&&now-event.observed_ms<90000?'待评分':'暂无评分';
    return {text,title:text,high:false};
  }
  function recentHTML(item,selected,score={text:'暂无评分',title:'暂无评分',high:false}){
    const event=latestTouch(item),stamp=touchTime(event),coin=base(item.symbol);
    return `<button type="button" class="rp-recent-chip" data-symbol="${esc(item.symbol)}" data-watch-select="${esc(item.symbol)}" aria-pressed="${item.symbol===selected}" title="${esc(coin)} · ${touchLabel(event?.side)} 1.618 · ${stamp?date.format(stamp)+' 北京':'时间待核对'} · ${esc(score.title)}">${avatar(coin)}<span><b>${esc(coin)}</b><span class="rp-recent-meta"><em data-scored="${!!score.scored}" class="rp-recent-score${score.high?' is-high':''}" aria-label="${esc(score.title)}">${score.scored?esc(score.text):'—'}</em></span></span></button>`;
  }
  function pinnedHTML(selected){
    return PINNED.map(symbol=>`<button type="button" class="rp-pinned-chip" data-symbol="${symbol}" data-watch-select="${symbol}" aria-pressed="${symbol===selected}" aria-label="查看 ${base(symbol)} K 线">${avatar(base(symbol))}<b>${base(symbol)}</b></button>`).join('');
  }
  // Native touch/trackpad scrolling; mouse dragging never becomes coin selection.
  function bindRecentDrag(lane,events,clock=Date.now){
    let drag=null,blockedUntil=0;
    const finish=event=>{
      if(!drag||event.pointerId!==drag.id)return;
      if(drag.moved||event.type==='pointercancel')blockedUntil=clock()+750;
      if(lane.hasPointerCapture?.(drag.id))lane.releasePointerCapture(drag.id);
      drag=null;lane.classList.remove('is-dragging');
    };
    lane.addEventListener('pointerdown',event=>{
      if(event.button!==0||event.isPrimary===false)return;
      blockedUntil=0;drag={id:event.pointerId,x:event.clientX,start:lane.scrollLeft,mouse:event.pointerType==='mouse',moved:false};
    });
    events.addEventListener('pointermove',event=>{
      if(!drag||event.pointerId!==drag.id)return;
      const dx=event.clientX-drag.x;
      if(Math.abs(dx)>6)drag.moved=true;
      if(drag.mouse&&drag.moved){
        if(!lane.hasPointerCapture?.(drag.id))lane.setPointerCapture?.(drag.id);
        lane.classList.add('is-dragging');lane.scrollLeft=drag.start-dx;event.preventDefault();
      }
    },{passive:false});
    events.addEventListener('pointerup',finish);events.addEventListener('pointercancel',finish);
    lane.addEventListener('click',event=>{if(event.detail!==0&&clock()<blockedUntil){event.preventDefault();event.stopImmediatePropagation();}},true);
    lane.addEventListener('dragstart',event=>event.preventDefault());
    return ()=>{drag=null;blockedUntil=0;lane.classList.remove('is-dragging');};
  }
  const fundedFill=(p,symbol)=>p.symbol===symbol&&p.taken===1&&positive(p.notional)&&[1,-1].includes(p.side)&&positive(p.fill_ms)&&positive(p.entry);
  const newestFill=(a,b)=>b.fill_ms-a.fill_ms||b.id-a.id;
  // Only remaining, filled exposure can link a coin to an account. Shadow
  // records and unfilled add orders are not holdings, even in the open feed.
  function openPosition(track,symbol){
    return (track?.open||[]).filter(p=>fundedFill(p,symbol)&&['pending_confirm','breakeven'].includes(p.stage)&&positive(p.remaining_notional??p.notional)).sort(newestFill)[0]||null;
  }
  function holdingTrack(tracks,symbol,current,order){
    const holders=order.filter(id=>openPosition(tracks?.[id],symbol));
    return holders.includes(current)?current:holders[0]||'spike';
  }
  function coinTrade(track,symbol){
    return openPosition(track,symbol)||[...(track?.open||[]),...(track?.closed||[]),...(track?.latest_funded?[track.latest_funded]:[])].filter(p=>fundedFill(p,symbol)).sort(newestFill)[0]||null;
  }
  function rowHTML(item,selected,{favorite=false,expanded=false,now=Date.now(),error=false,platform='',period='5m'}={}){
    const coin=base(item.symbol),event=latestTouch(item),stamp=touchTime(event),side=event?.side;
    const warning=item.catalog?(item.stale?'行情待更新':''):STATES[item.state]||((item.stale||!Number.isFinite(item.received_ms)||now-item.received_ms>=90000||error)?'行情已滞后':'');
    const meta=item.catalog?warning||`24h ${item.volume===null?'—':Number(item.volume).toLocaleString('en-US',{notation:'compact',maximumFractionDigits:2})} USDT`:warning||(stamp?date.format(stamp):'尚未触价');
    const change=Number.isFinite(item.change)?`${item.change>0?'+':''}${item.change.toFixed(2)}%`:'—';
    const icon=name=>`<span class="rp-watch-icon" aria-hidden="true" style="--watch-icon:url(/static/icons/${name}.svg)"></span>`;
    return `<article class="rp-watch-row" data-symbol="${esc(item.symbol)}" data-selected="${item.symbol===selected}"><div class="rp-watch-main"><button type="button" class="rp-watch-select" data-watch-select="${esc(item.symbol)}" aria-label="查看 ${esc(coin)} K 线" aria-pressed="${item.symbol===selected}">${avatar(coin)}<span class="rp-watch-identity"><b class="rp-watch-name">${esc(coin)}${side?`<em class="rp-touch-tag ${touchTone(side)}">${touchLabel(side)}</em>`:''}</b><small class="rp-watch-time" title="${esc(meta)}">${esc(meta)}</small></span><span class="rp-watch-quote"><b>${price(item.price)}</b><span class="${item.catalog?(item.change>0?'rp-positive':item.change<0?'rp-negative':''):touchTone(side)}">${item.catalog?change:touchLabel(side)}</span></span></button><button type="button" class="rp-watch-disclosure" data-watch-action="details" aria-expanded="${expanded}" aria-controls="rp-watch-actions-${esc(item.symbol)}" aria-label="${expanded?'收起':'展开'} ${esc(coin)} 操作"><span aria-hidden="true">⌄</span></button></div><div class="rp-watch-bottom" id="rp-watch-actions-${esc(item.symbol)}"${expanded?'':' hidden'}><a class="rp-watch-tv" href="${esc(tradingViewURL(item.symbol,platform))}" target="_blank" rel="noopener" aria-label="在 TradingView 打开 ${esc(coin)}" title="TradingView">TV ↗</a><a href="${esc(inspectionURL(item.symbol,period))}" aria-label="查看 ${esc(coin)} 形态巡检" title="形态巡检">${icon('activity')}</a><button data-watch-action="favorite" aria-pressed="${favorite}" aria-label="${favorite?'取消收藏':'收藏'} ${esc(coin)}">${icon('star')}</button>${!item.catalog&&side?`<button data-watch-action="remove" aria-label="从本周列表移除 ${esc(coin)}" title="从本周列表移除">${icon('trash')}</button>`:''}</div></article>`;
  }
  const api={validSymbol,validate,model,recentRows,touchScore,recentHTML,PINNED,pinnedHTML,bindRecentDrag,openPosition,holdingTrack,coinTrade,rowHTML};
  if(typeof module!=='undefined'&&module.exports){module.exports=api;return;}
  function reconcile(container,html){
    if(container._html===html)return;
    const template=document.createElement('template');template.innerHTML=html;
    const old=new Map(Array.from(container.children,node=>[node.dataset.symbol||'',node])),focused=document.activeElement;
    function patch(node,next){
      if(node.nodeType===3){if(node.nodeValue!==next.nodeValue)node.nodeValue=next.nodeValue;return;}
      for(const attr of Array.from(node.attributes))if(!next.hasAttribute(attr.name)&&!(node.tagName==='IMG'&&attr.name==='hidden'))node.removeAttribute(attr.name);
      for(const attr of next.attributes)if(node.getAttribute(attr.name)!==attr.value)node.setAttribute(attr.name,attr.value);
      for(let i=0;i<next.childNodes.length;i++){
        const a=node.childNodes[i],b=next.childNodes[i];
        if(!a)node.append(b.cloneNode(true));else if(a.nodeType!==b.nodeType||a.nodeName!==b.nodeName)a.replaceWith(b.cloneNode(true));else patch(a,b);
      }
      while(node.childNodes.length>next.childNodes.length)node.lastChild.remove();
    }
    Array.from(template.content.children).forEach((next,i)=>{
      const key=next.dataset.symbol||'',node=old.get(key);
      if(node&&node.tagName===next.tagName){old.delete(key);patch(node,next);if(container.children[i]!==node)container.insertBefore(node,container.children[i]||null);}
      else container.insertBefore(next,container.children[i]||null);
    });
    for(const node of old.values())node.remove();
    if(focused&&container.contains(focused)&&document.activeElement!==focused)focused.focus({preventScroll:true});
    container._html=html;
  }
  function create({root,createFeed,getContext,onSelect,onOpen}){
    const trigger=root.querySelector('[data-rp-action="watch"]'),panel=document.createElement('section');
    panel.id='rp-watch-panel';panel.className='rp-watch-panel rp-glass';panel.setAttribute('aria-label','币种列表');
    panel.innerHTML=`<header class="rp-record-heading rp-watch-heading" data-yd-sheet-drag><strong>币种 <span data-watch="total">—</span></strong><label class="rp-search" hidden><input type="search" placeholder="搜索币种" aria-label="搜索全部合约" maxlength="80" autocomplete="off"></label><button type="button" data-watch-action="search" aria-label="搜索币种" aria-expanded="false"><svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" aria-hidden="true"><circle cx="10.5" cy="10.5" r="6.5"/><path d="m16 16 5 5"/></svg></button><button type="button" data-watch-action="close" aria-label="收起币种列表">×</button></header><div class="rp-watch-tools"><div class="rp-tabs" role="group" aria-label="触价与收藏筛选">${Object.entries(FILTERS).map(([key,label])=>`<button type="button" data-watch-filter="${key}" aria-pressed="false">${label}</button>`).join('')}</div><div class="rp-watch-feedback" hidden><span role="status"></span><button data-watch-action="undo">撤销</button></div></div><div class="rp-watch-scroll"><div data-watch="rows"></div><button type="button" data-watch-action="more" hidden>再显示 50 个</button></div><p class="rp-watch-status" role="status"></p>`;
    const recent=document.createElement('nav');recent.className='rp-recent rp-glass';recent.setAttribute('aria-label','最近触价币种');
    recent.innerHTML='<span class="rp-recent-label">触价</span><div class="rp-recent-scroll" aria-label="滑动查看最近触价"></div><div class="rp-pinned" role="group" aria-label="固定快捷币种"></div>';
    root.append(panel,recent);
    const lane=recent.querySelector('.rp-recent-scroll'),resetDrag=bindRecentDrag(lane,document);
    let storage;try{storage=window.localStorage;}catch{}
    const preferences=createListPreferences(storage),platform=navigator.userAgentData?.platform||navigator.platform||'';
    let active=false,open=false,filter='all',q='',shown=50,lastRemoval=null,expandedSymbol='',searching=false;
    const pointers=new Set();
    const requests=window.SpikeRuntime.createRequests();
    const feed=createFeed({read:()=>requests.json('/api/monday-fib/display'),validateData:validate,cancel:()=>requests.cancel(),change:render});
    const catalogRequests=window.SpikeRuntime.createRequests();
    const catalogFeed=createFeed({read:()=>catalogRequests.json('/api/research/paper-desk/symbols'),validateData:catalogModel.validateCatalog,cancel:()=>catalogRequests.cancel(),change:render});
    const sheet=window.SpikeYoloPreviewSheet.bind({panel,isOpen:()=>open,dismiss:()=>setOpen(false)});
    const el=id=>panel.querySelector(`[data-watch="${id}"]`),scroll=panel.querySelector('.rp-watch-scroll');
    function setOpen(value,focus=true){sheet.reset();open=value;panel.classList.toggle('is-open',value);panel.inert=!value;panel.setAttribute('aria-hidden',String(!value));trigger.setAttribute('aria-expanded',String(value));catalogFeed.setActive(active&&open);if(value){preferences.read();onOpen?.();render();}else if(focus)trigger.focus({preventScroll:true});}
    function setSearch(value){
      searching=value;const heading=panel.querySelector('.rp-watch-heading'),button=heading.querySelector('[data-watch-action="search"]');
      heading.classList.toggle('is-searching',value);heading.querySelector('.rp-search').hidden=!value;
      button.setAttribute('aria-expanded',String(value));button.setAttribute('aria-label',value?'收起搜索':'搜索币种');
      (value?heading.querySelector('input'):button).focus({preventScroll:true});
    }
    function feedback(text,undo=false){const box=panel.querySelector('.rp-watch-feedback');box.hidden=!text;box.querySelector('span').textContent=text;box.querySelector('button').hidden=!undo;}
    function render(){
      const {data,error,busy}=feed.getState(),catalog=catalogFeed.getState(),catalogTab=filter==='all'||filter==='favorites',context=getContext(),view=model(data,preferences,{q,filter,catalog:catalog.data||[]});
      recent.dataset.previous=String(!!data?.display_previous);recent.querySelector('.rp-recent-label').textContent=data?.display_previous?'上周':'触价';recent.title=data?.display_previous?'本周周一区间形成中，保留上周触价记录':'';
      trigger.title='选择币种';el('total').textContent=(catalogTab?catalog.data:data)?view.items.length:'—';
      panel.querySelectorAll('[data-watch-filter]').forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.watchFilter===filter)));
      panel.querySelector('[data-watch-action="search"]').classList.toggle('has-query',!!q);
      const source=catalogTab?catalog:feed.getState();
      panel.querySelector('.rp-watch-status').textContent=source.error?`同步失败 · ${source.error}${source.data?' · 保留上次记录':''}`:!source.data?'正在读取币种…':catalogTab?`OKX USDT 永续 · 24h 成交额降序 · ${view.items.length} 个`:`${data?.display_previous?'上周触价':'本周触价'} · 最新触价优先 · ${view.items.length} 个`;
      // Keep live controls in place, including while a finger is held down.
      if(pointers.size)return;
      const chips=recentRows(data,preferences).map(item=>recentHTML(item,context.symbol,touchScore(item,data.display_week||data.week,context.signals))).join('')||`<span class="rp-recent-empty">${!data?(error?'触价读取失败':'触价同步中…'):'暂无触价'}</span>`;
      const offset=lane.scrollLeft;reconcile(lane,chips);lane.scrollLeft=offset;
      reconcile(recent.querySelector('.rp-pinned'),pinnedHTML(context.symbol));
      if(!open)return;
      const html=view.items.slice(0,shown).map(item=>rowHTML(item,context.symbol,{favorite:preferences.has(item.symbol),expanded:expandedSymbol===item.symbol,now:Date.now(),error:source.error,platform,period:context.period})).join('')||`<div class="rp-empty">${!source.data?(source.error?'读取失败，正在重试':'正在读取…'):q?'没有匹配的币种':filter==='favorites'?'尚未收藏币种':catalogTab?'暂无可用币种':'暂无符合筛选的触价币种'}</div>`;
      const position=captureListScroll(scroll);reconcile(el('rows'),html);restoreListScroll(scroll,position);
      panel.querySelector('[data-watch-action="more"]').hidden=view.items.length<=shown;
    }
    panel.addEventListener('input',event=>{if(event.target.matches('input')){q=event.target.value;shown=50;scroll.scrollTop=0;render();}});
    panel.addEventListener('click',event=>{
      const b=event.target.closest('button'),row=event.target.closest('[data-symbol]');
      if(b?.dataset.watchFilter){filter=b.dataset.watchFilter;shown=50;scroll.scrollTop=0;render();return;}
      const action=b?.dataset.watchAction;
      if(action==='search'){setSearch(!searching);return;}
      if(action==='details'){expandedSymbol=expandedSymbol===row.dataset.symbol?'':row.dataset.symbol;render();return;}
      if(action==='favorite'){preferences.toggle(row.dataset.symbol);feedback(preferences.persistent?'':'收藏暂未保存，仅本次有效');render();return;}
      if(action==='remove'){const data=feed.getState().data,item=data.items.find(i=>i.symbol===row.dataset.symbol);lastRemoval=preferences.remove(item,data.display_week||data.week);feedback(preferences.persistent?'已从本周列表移除':'暂未保存，仅本次移除',!!lastRemoval);render();return;}
      if(action==='undo'){preferences.undo(lastRemoval);lastRemoval=null;feedback('');render();return;}
      if(action==='close')setOpen(false);if(action==='more'){shown+=50;render();}
      if(row&&!event.target.closest('a,.rp-watch-bottom')&&!action){onSelect(row.dataset.symbol);setOpen(false);}
    });
    recent.addEventListener('click',event=>{const b=event.target.closest('[data-watch-select]');if(b){onSelect(b.dataset.watchSelect);setOpen(false,false);}});
    root.addEventListener('keydown',event=>{if(event.key==='Escape'&&open){event.preventDefault();event.stopImmediatePropagation();if(searching)setSearch(false);else setOpen(false);}});
    root.addEventListener('pointerdown',event=>{if(panel.contains(event.target)||recent.contains(event.target))pointers.add(event.pointerId);else if(open&&!trigger.contains(event.target))setOpen(false,false);});
    const release=event=>{if(pointers.delete(event.pointerId))setTimeout(render,0);};
    document.addEventListener('pointerup',release);document.addEventListener('pointercancel',release);
    window.addEventListener('blur',()=>{pointers.clear();resetDrag();render();});
    window.addEventListener('storage',event=>{if(event.key===preferences.key){preferences.read();render();}});
    setOpen(open,false);
    return {render,refreshPreferences(){preferences.read();render();},setOpen,toggle:()=>setOpen(!open),refresh(){catalogFeed.refresh();return feed.refresh();},setActive(value){active=value;catalogFeed.setActive(active&&open);if(value)preferences.read();else{pointers.clear();resetDrag();setOpen(false,false);}feed.setActive(value);render();}};
  }
  window.SpikeReclaimWatchlist={...api,create};
})();
