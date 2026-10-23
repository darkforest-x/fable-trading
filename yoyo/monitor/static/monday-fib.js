/* Stitch wallpaper fd410c9bd37443dd8759a713c57e2d47, adapted to the read-only
 * Monday monitor. Latest means occurrence time, never history insertion time.
 * Manual selection pins the chart until follow is resumed; no subscription writes.
 */
(() => {
  'use strict';
  const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const finite=v=>typeof v==='number'&&Number.isFinite(v);
  const price=v=>finite(v)?v.toLocaleString('en-US',{maximumFractionDigits:Math.min(12,Math.max(2,6-Math.floor(Math.log10(Math.abs(v)||1))))}):'—';
  const STATES={loading:'正在同步',developing:'周一尚未结束',missing_monday:'缺少完整周一',missing_history:'历史触价待核对',recovering:'正在核对断线区间',fetch_error:'数据暂不可用',invalid_range:'周一区间无效'};
  const FILTERS={all:'全部',upper:'上沿',lower:'下沿'},RECENT_MS=15*60000,PAGE_SIZE=5;
  const touchTime=e=>e?(e.source==='history'?e.candle_ms:e.observed_ms)||0:0;
  const latestTouch=r=>Object.entries(r.touches||{}).map(([side,e])=>({...e,side})).sort((a,b)=>touchTime(b)-touchTime(a))[0]||null;
  const recentTouch=(e,now)=>!!e&&e.source==='live'&&finite(e.observed_ms)&&now>=e.observed_ms&&now-e.observed_ms<RECENT_MS;
  function selectRows(items,q='',filter='all',now=Date.now()) {
    const query=q.trim().toUpperCase().replace(/[^A-Z0-9]/g,'');
    return items.filter(r=>!!latestTouch(r)&&(!query||r.symbol.replace(/[^A-Z0-9]/g,'').includes(query))&&(
      filter==='recent'?recentTouch(latestTouch(r),now):filter==='upper'||filter==='lower'?!!r.touches?.[filter]:true))
      .sort((a,b)=>touchTime(latestTouch(b))-touchTime(latestTouch(a))||a.symbol.localeCompare(b.symbol));
  }
  function selection(items,symbol,follow){const rows=selectRows(items);return (!follow&&rows.find(r=>r.symbol===symbol))||rows[0]||null;}
  // Display coordinates are isolated from chart/data state. Only grip gestures move UI.
  function clampPanel(x,y,w,h,vw,vh){const gap=8;return {x:Math.max(gap,Math.min(Number.isFinite(x)?x:gap,Math.max(gap,vw-w-gap))),y:Math.max(gap,Math.min(Number.isFinite(y)?y:gap,Math.max(gap,vh-h-gap)))};}
  function mountLayout(root,env=window){
    const blocks=Array.from(root.querySelectorAll('[data-mf-movable]')),key='spike:monday-fib:layout:v1';
    let saved={},drag=null,active=false,frame=null;
    try{saved=JSON.parse(env.localStorage?.getItem(key)||'{}')||{};if(typeof saved!=='object'||Array.isArray(saved))saved={};}catch{saved={};}
    const surface=()=>env.innerWidth<=760?'mobile':'desktop';
    const write=()=>{try{env.localStorage?.setItem(key,JSON.stringify(saved));}catch{/* Storage can be unavailable in private mode. */}};
    const resetStyle=b=>{for(const k of ['left','top','right','bottom','transform'])b.style[k]='';};
    const rect=b=>b.getBoundingClientRect();
    function move(b,x,y){const r=rect(b),p=clampPanel(x,y,r.width,r.height,env.innerWidth,env.innerHeight);Object.assign(b.style,{left:p.x+'px',top:p.y+'px',right:'auto',bottom:'auto',transform:'none'});return p;}
    function remember(b){const r=rect(b),s=surface();if(!saved[s]||typeof saved[s]!=='object')saved[s]={};saved[s][b.dataset.mfMovable]={x:r.x/Math.max(1,env.innerWidth-r.width),y:r.y/Math.max(1,env.innerHeight-r.height)};write();}
    function restore(){
      if(!active||drag)return;
      for(const b of blocks){
        resetStyle(b);if(!b.getClientRects().length)continue;
        const p=saved[surface()]?.[b.dataset.mfMovable],r=rect(b);
        if(p&&Number.isFinite(p.x)&&Number.isFinite(p.y))move(b,p.x*(env.innerWidth-r.width),p.y*(env.innerHeight-r.height));
      }
    }
    function reset(b){if(saved[surface()])delete saved[surface()][b.dataset.mfMovable];resetStyle(b);write();}
    function finish(commit=true){
      if(!drag)return;const d=drag;drag=null;d.block.classList.remove('mf-dragging');
      d.handle.releasePointerCapture?.(d.id);
      if(commit&&d.moved)remember(d.block);else if(d.moved){Object.assign(d.block.style,d.before);}
    }
    const resize=()=>{finish(false);if(frame)env.cancelAnimationFrame?.(frame);frame=env.requestAnimationFrame?.(()=>{frame=null;restore();});};
    const bindings=[];
    for(const block of blocks){
      const handle=block.querySelector('[data-mf-grip]');if(!handle)continue;
      const down=e=>{if(!active||e.isPrimary===false||(e.button!=null&&e.button!==0))return;e.preventDefault();handle.focus?.({preventScroll:true});const r=rect(block);drag={block,handle,id:e.pointerId,startX:e.clientX,startY:e.clientY,x:r.x,y:r.y,moved:false,before:Object.fromEntries(['left','top','right','bottom','transform'].map(k=>[k,block.style[k]]))};handle.setPointerCapture?.(e.pointerId);};
      const pointer=e=>{if(!drag||drag.handle!==handle||drag.id!==e.pointerId)return;const dx=e.clientX-drag.startX,dy=e.clientY-drag.startY;if(!drag.moved&&Math.hypot(dx,dy)<5)return;drag.moved=true;block.classList.add('mf-dragging');move(block,drag.x+dx,drag.y+dy);};
      const up=e=>{if(drag?.handle===handle&&drag.id===e.pointerId)finish();};
      const cancel=()=>finish(false);
      const keyboard=e=>{
        if(e.key==='Escape'&&drag){e.preventDefault();finish(false);return;}
        if(e.key==='Home'){e.preventDefault();reset(block);return;}
        const deltas={ArrowLeft:[-1,0],ArrowRight:[1,0],ArrowUp:[0,-1],ArrowDown:[0,1]},d=deltas[e.key];if(!d)return;
        e.preventDefault();e.stopPropagation();const r=rect(block),step=e.shiftKey?40:12;move(block,r.x+d[0]*step,r.y+d[1]*step);remember(block);
      };
      const dbl=()=>reset(block);
      for(const [event,fn] of [['pointerdown',down],['pointermove',pointer],['pointerup',up],['pointercancel',cancel],['lostpointercapture',cancel],['keydown',keyboard],['dblclick',dbl]]){handle.addEventListener(event,fn);bindings.push([handle,event,fn]);}
    }
    const blur=()=>finish(false);
    return {refresh:restore,setActive(value){active=value;finish(false);if(value){env.addEventListener?.('resize',resize);env.addEventListener?.('blur',blur);restore();}else{env.removeEventListener?.('resize',resize);env.removeEventListener?.('blur',blur);if(frame)env.cancelAnimationFrame?.(frame);frame=null;}},destroy(){this.setActive(false);for(const [h,e,f] of bindings)h.removeEventListener(e,f);}};
  }
  if(typeof module!=='undefined')module.exports={esc,price,selectRows,selection,latestTouch,touchTime,recentTouch,RECENT_MS,STATES,clampPanel,mountLayout};
  if(typeof document==='undefined')return;
  const root=document.getElementById('monday-fib-root');if(!root)return;
  const $=id=>document.getElementById(id);
  const icon=name=>`<span class="mf-icon" aria-hidden="true" style="--mf-icon:url(/static/icons/${name}.svg)"></span>`;
  const grip=label=>`<button class="mf-grip" data-mf-grip type="button" aria-label="移动${label}；方向键移动，Home 复位" title="拖动换位置 · 双击还原"><span aria-hidden="true"></span></button>`;
  root.innerHTML=`
    <div id="mf-wallpaper" class="mf-wallpaper" aria-label="所选合约的周一区间 K 线"></div>
    <div id="mf-empty" class="mf-empty-chart" role="status">正在读取最新触价…</div>
    <header class="mf-nav mf-glass" data-mf-movable="nav">${grip('导航')}<a href="#market" aria-label="返回市场观察">${icon('chevron-left')}<b>SPIKE</b></a><span class="mf-divider"></span><h1>周一 1.618</h1><span id="mf-stream" class="mf-stream" title="行情连接中"><i></i><span>连接中</span></span></header>
    <section id="mf-selected" class="mf-selected mf-glass" data-mf-movable="symbol" aria-label="当前合约" hidden>${grip('币种卡片')}
      <div class="mf-symbol-line"><h2 id="mf-symbol">—</h2><span class="mf-perp">USDT 永续</span></div>
      <div id="mf-price" class="mf-price">—</div>
      <div class="mf-selected-meta"><span id="mf-touch"></span><span id="mf-mode">跟随最新</span></div>
      <p id="mf-quote-state" class="mf-quote-state" hidden></p>
    </section>
    <div class="mf-list-launch mf-glass" data-mf-movable="launcher">${grip('列表入口')}<button id="mf-list-toggle" class="mf-list-toggle" aria-label="打开或收起触价列表" aria-controls="mf-list-panel" aria-expanded="false">${icon('activity')}<span class="mf-list-label">触价列表</span><span id="mf-count">—</span></button></div>
    <aside id="mf-list-panel" class="mf-list-panel mf-glass" data-mf-movable="list" aria-label="已触价合约" hidden>
      <div class="mf-list-heading">${grip('触价列表')}<div><h2>最新触价</h2><span id="mf-list-count"></span></div><button id="mf-list-close" aria-label="收起触价列表">${icon('x')}</button></div>
      <label class="mf-search">${icon('search')}<input id="mf-search" type="search" placeholder="搜索已触价合约" aria-label="搜索已触价合约" autocomplete="off"></label>
      <div id="mf-filters" class="mf-filters" role="group" aria-label="触价方向">${Object.entries(FILTERS).map(([key,label])=>`<button data-filter="${key}" aria-pressed="${key==='all'}">${label}</button>`).join('')}</div>
      <div id="mf-rows" class="mf-rows"></div>
      <div class="mf-pagination"><button id="mf-prev" aria-label="上一页">‹</button><span id="mf-page-number"></span><button id="mf-next" aria-label="下一页">›</button></div>
      <p class="mf-list-note">按触价时间排序 · 点击切换图表</p>
    </aside>
    <div id="mf-notice" class="mf-notice mf-glass" role="status" hidden></div>
    <div class="mf-dock mf-glass" data-mf-movable="dock" role="group" aria-label="图表控制">${grip('图表工具')}<span class="mf-timeframe" title="15 分钟已收盘 K 线">15m</span><span class="mf-divider"></span><button id="mf-fit" title="恢复价格缩放并显示本周全部 K 线">${icon('maximize')}<span>全周</span></button><button id="mf-fib" aria-pressed="false" title="显示区间内部 0.5 / 0.618 / 0.705">${icon('adjustments-horizontal')}<span>内部线</span></button><span class="mf-divider"></span><button id="mf-follow" aria-pressed="true">${icon('activity')}<span>跟随最新</span></button></div>
    <footer class="mf-footer"><span id="mf-week">纽约周一 · 读取中</span><span id="mf-updated">15m 已收盘 K 线</span></footer>`;
  const layout=mountLayout(root);
  let active=false,data=null,receivedAt=0,error='',timer=null,controller=null,pending=null,revision=0;
  let query='',filter='all',page=0,selected='',selectedWeek='',follow=true,internal=false,lastRows='';
  let listOpen=!!window.matchMedia?.('(min-width: 761px)').matches;
  const nowMs=()=>data?.now_ms?data.now_ms+Math.max(0,Date.now()-receivedAt):Date.now();
  const time=ms=>ms?new Intl.DateTimeFormat('zh-CN',{timeZone:'Asia/Shanghai',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hourCycle:'h23'}).format(new Date(ms)):'时间待核对';
  const short=s=>s.replace('-USDT-SWAP','');
  function quoteState(r){return STATES[r.state]||((r.stale||!finite(r.received_ms)||nowMs()-r.received_ms>=90000||error)?'行情已滞后':'');}
  function panel(open,focus=false){listOpen=open;$('mf-list-panel').hidden=!open;$('mf-list-toggle').setAttribute('aria-expanded',String(open));root.classList.toggle('mf-list-open',open);layout.refresh();if(focus)(open?$('mf-search'):$('mf-list-toggle')).focus({preventScroll:true});}
  function sync(){window.SpikeNavigation?.sync('monday-fib',{q:query||null,filter:filter==='all'?null:filter,page:page?String(page+1):null,symbol:follow?null:selected||null});}
  function renderRows(){
    const items=data?.items||[],rows=selectRows(items,query,filter,nowMs()),pages=Math.max(1,Math.ceil(rows.length/PAGE_SIZE));page=Math.min(Math.max(0,page),pages-1);
    const html=rows.slice(page*PAGE_SIZE,(page+1)*PAGE_SIZE).map(r=>{
      const e=latestTouch(r),fresh=recentTouch(e,nowMs()),state=quoteState(r),side=e.side==='upper'?'上':'下';
      return `<button class="mf-row${fresh?' mf-recent':''}" data-symbol="${esc(r.symbol)}" aria-pressed="${r.symbol===selected}" title="${esc(r.symbol)} · ${time(touchTime(e))} 北京时间"><span class="mf-row-symbol"><b>${esc(short(r.symbol))}</b><small>${e.source==='history'?'历史 · ':''}${time(touchTime(e))}${fresh?' · 刚触及':''}</small></span><span class="mf-row-value"><strong class="mf-${e.side}">${side}沿 1.618</strong><small>${state||price(r.price)}</small></span></button>`;
    }).join('')||`<p class="mf-no-rows">${data?'没有匹配的已触价合约':'正在读取…'}</p>`;
    if(html!==lastRows){
      const focused=$('mf-rows').contains?.(document.activeElement)?document.activeElement?.dataset?.symbol:null;
      $('mf-rows').innerHTML=html;lastRows=html;
      if(focused)Array.from($('mf-rows').querySelectorAll('[data-symbol]')).find(b=>b.dataset.symbol===focused)?.focus({preventScroll:true});
    }
    $('mf-page-number').textContent=`${page+1} / ${pages}`;$('mf-prev').disabled=page===0;$('mf-next').disabled=page>=pages-1;
    $('mf-list-count').textContent=`${rows.length} 个`;
    for(const b of $('mf-filters').querySelectorAll('[data-filter]'))b.setAttribute('aria-pressed',String(b.dataset.filter===filter));
  }
  function render(){
    const now=nowMs(),connected=data?.stream.connected&&finite(data.stream.last_message_ms)&&now-data.stream.last_message_ms<90000&&!error;
    $('mf-stream').innerHTML=`<i></i><span>${connected?'行情在线':'连接待恢复'}</span>`;$('mf-stream').classList.toggle('mf-online',!!connected);$('mf-stream').title=connected?'OKX 行情在线':'行情连接待恢复';
    const notice=error||data?.stream.catalog_error||data?.stream.error||'';$('mf-notice').hidden=!notice;$('mf-notice').textContent=notice;
    if(data){
      if(selectedWeek&&selectedWeek!==data.week){selected='';follow=true;}
      selectedWeek=data.week;
      const row=selection(data.items,selected,follow);
      if(!follow&&selected&&!data.items.some(r=>r.symbol===selected&&latestTouch(r)))follow=true;
      const key=row?.symbol||'',changed=key!==selected;selected=key;
      $('mf-selected').hidden=!row;$('mf-empty').hidden=!!row;
      if(row){
        const e=latestTouch(row),state=quoteState(row);
        $('mf-symbol').textContent=short(row.symbol);$('mf-price').textContent=price(row.price);
        $('mf-touch').textContent=`${e.side==='upper'?'上':'下'}沿 1.618 · ${time(touchTime(e))}${e.source==='history'?' 历史':''}`;
        $('mf-touch').className='mf-'+e.side;
        $('mf-quote-state').hidden=!state;$('mf-quote-state').textContent=state;
        $('mf-mode').textContent=follow?'跟随最新':'已固定';
        if(changed&&root.animate&&!window.matchMedia?.('(prefers-reduced-motion: reduce)').matches)$('mf-selected').animate([{opacity:.55,transform:'translateY(4px)'},{opacity:1,transform:'translateY(0)'}],{duration:220,easing:'ease-out'});
        if(active&&!document.hidden)window.SpikeMondayFibChart?.show($('mf-wallpaper'),row,data.week,{internal});
      }else{
        $('mf-empty').textContent=data.monday_complete?'本周尚无 1.618 触价，等待最新行情':'纽约周一区间正在形成，完成后开始监控';
        window.SpikeMondayFibChart?.hide();
      }
      $('mf-count').textContent=String(selectRows(data.items).length);
      $('mf-week').textContent=`纽约周一 ${data.week}${data.monday_complete?'':' · 形成中'}`;
    }
    $('mf-follow').setAttribute('aria-pressed',String(follow));$('mf-follow').title=follow?'自动显示最近触价的合约':'回到最新触价，并自动跟随';
    $('mf-fit').disabled=!selected;$('mf-fib').disabled=!selected;
    renderRows();layout.refresh();
  }
  function schedule(){clearTimeout(timer);if(active&&!document.hidden)timer=setTimeout(refresh,5000);}
  function refresh(){
    if(!active||document.hidden)return Promise.resolve();if(pending)return pending;
    clearTimeout(timer);const rev=revision,c=new AbortController();controller=c;const timeout=setTimeout(()=>c.abort(),12000);
    const task=(async()=>{try{
      const res=await fetch('/api/monday-fib',{cache:'no-store',credentials:'same-origin',signal:c.signal}),d=await res.json();
      if(!res.ok)throw Error(typeof d.detail==='string'?d.detail:'监控暂不可用，正在重试');
      if(!Array.isArray(d.items)||!d.stream||!d.week)throw Error('监控数据格式异常');
      if(rev!==revision||!active)return;data=d;receivedAt=Date.now();error='';render();
    }catch(e){if(rev===revision&&active){error=e.name==='AbortError'?'数据请求超时，正在重试':e.message;render();}}
    finally{clearTimeout(timeout);if(controller===c)controller=null;if(pending===task)pending=null;if(rev===revision)schedule();}})();pending=task;return task;
  }
  function cancel(){revision++;controller?.abort();controller=null;pending=null;clearTimeout(timer);window.SpikeMondayFibChart?.hide();}
  function setActive(value){if(active===value)return;active=value;cancel();layout.setActive(value);if(active){panel(listOpen);render();refresh();}}
  $('mf-search').addEventListener('input',e=>{query=e.target.value;page=0;renderRows();sync();});
  $('mf-filters').addEventListener('click',e=>{const b=e.target.closest('[data-filter]');if(!b)return;filter=b.dataset.filter;page=0;renderRows();sync();});
  for(const [id,delta] of [['mf-prev',-1],['mf-next',1]])$(id).addEventListener('click',()=>{page+=delta;renderRows();sync();});
  $('mf-rows').addEventListener('click',e=>{const b=e.target.closest('[data-symbol]');if(!b)return;selected=b.dataset.symbol;follow=false;render();sync();if(window.matchMedia?.('(max-width: 760px)').matches)panel(false,true);});
  $('mf-list-toggle').addEventListener('click',()=>panel(!listOpen,true));$('mf-list-close').addEventListener('click',()=>panel(false,true));
  $('mf-follow').addEventListener('click',()=>{follow=true;render();sync();});
  $('mf-fit').addEventListener('click',()=>window.SpikeMondayFibChart?.fit());
  $('mf-fib').addEventListener('click',()=>{internal=!internal;$('mf-fib').setAttribute('aria-pressed',String(internal));window.SpikeMondayFibChart?.setInternal(internal);});
  root.addEventListener('keydown',e=>{if(e.key==='Escape'&&listOpen){e.stopPropagation();panel(false,true);}});
  document.addEventListener('visibilitychange',()=>{if(document.hidden){cancel();layout.setActive(false);}else if(active){layout.setActive(true);render();refresh();}});
  window.SpikeNavigation?.register('monday-fib',p=>{query=p.q||'';filter=Object.hasOwn(FILTERS,p.filter)?p.filter:'all';page=Math.max(0,(Number(p.page)||1)-1);selected=p.symbol||'';follow=!selected;$('mf-search').value=query;if(data)render();});
  panel(listOpen);window.SpikeMondayFib={setActive,refresh};
})();
