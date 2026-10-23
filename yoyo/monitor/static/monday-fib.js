/* Stitch navigation 2ef0866509164e07a536813ee3944d51, adapted to the read-only
 * Monday monitor. Latest means occurrence time, never history insertion time.
 * Manual selection pins the chart until follow is resumed; no subscription writes.
 */
(() => {
  'use strict';
  const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const finite=v=>typeof v==='number'&&Number.isFinite(v);
  const priceFormats=new Map();
  const price=v=>{if(!finite(v))return '—';const digits=Math.min(12,Math.max(2,6-Math.floor(Math.log10(Math.abs(v)||1))));if(!priceFormats.has(digits))priceFormats.set(digits,new Intl.NumberFormat('en-US',{maximumFractionDigits:digits}));return priceFormats.get(digits).format(v);};
  const STATES={loading:'正在同步',developing:'周一尚未结束',missing_monday:'缺少完整周一',missing_history:'历史触价待核对',recovering:'正在核对断线区间',fetch_error:'数据暂不可用',invalid_range:'周一区间无效'};
  const FILTERS={all:'全部',upper:'上沿',lower:'下沿'},RECENT_MS=15*60000;
  const touchTime=e=>e?(e.source==='history'?e.candle_ms:e.observed_ms)||0:0;
  const latestTouch=r=>{let result=null;for(const [side,e] of Object.entries(r.touches||{}))if(!result||touchTime(e)>touchTime(result))result={...e,side};return result;};
  const recentTouch=(e,now)=>!!e&&e.source==='live'&&finite(e.observed_ms)&&now>=e.observed_ms&&now-e.observed_ms<RECENT_MS;
  function selectRows(items,q='',filter='all',now=Date.now()) {
    const query=q.trim().toUpperCase().replace(/[^A-Z0-9]/g,'');
    return items.map(r=>({row:r,event:latestTouch(r)})).filter(({row:r,event:e})=>e&&(!query||r.symbol.replace(/[^A-Z0-9]/g,'').includes(query))&&(
      filter==='recent'?recentTouch(e,now):filter==='upper'||filter==='lower'?!!r.touches?.[filter]:true))
      .sort((a,b)=>touchTime(b.event)-touchTime(a.event)||a.row.symbol.localeCompare(b.row.symbol)).map(x=>x.row);
  }
  function selection(items,symbol,follow){const rows=selectRows(items);return (!follow&&rows.find(r=>r.symbol===symbol))||rows[0]||null;}
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
  // Display coordinates are isolated from chart/data state. Only grip gestures move UI.
  function clampPanel(x,y,w,h,vw,vh){const gap=8;return {x:Math.max(gap,Math.min(Number.isFinite(x)?x:gap,Math.max(gap,vw-w-gap))),y:Math.max(gap,Math.min(Number.isFinite(y)?y:gap,Math.max(gap,vh-h-gap)))};}
  function mountLayout(root,env=window){
    const blocks=Array.from(root.querySelectorAll('[data-mf-movable]')),key='spike:monday-fib:layout:v1';
    let saved={},drag=null,active=false,frame=null,dragFrame=null;
    try{saved=JSON.parse(env.localStorage?.getItem(key)||'{}')||{};if(typeof saved!=='object'||Array.isArray(saved))saved={};}catch{saved={};}
    const surface=()=>env.innerWidth<=760?'mobile':'desktop';
    const write=()=>{try{env.localStorage?.setItem(key,JSON.stringify(saved));}catch{/* Storage can be unavailable in private mode. */}};
    const resetStyle=b=>{for(const k of ['left','top','right','bottom','transform'])b.style[k]='';};
    const rect=b=>b.getBoundingClientRect();
    function move(b,x,y,r=rect(b)){const p=clampPanel(x,y,r.width,r.height,env.innerWidth,env.innerHeight);Object.assign(b.style,{left:p.x+'px',top:p.y+'px',right:'auto',bottom:'auto',transform:'none'});return p;}
    function remember(b){const r=rect(b),s=surface();if(!saved[s]||typeof saved[s]!=='object')saved[s]={};saved[s][b.dataset.mfMovable]={x:r.x/Math.max(1,env.innerWidth-r.width),y:r.y/Math.max(1,env.innerHeight-r.height)};write();}
    function restore(){
      if(!active||drag)return;
      for(const b of blocks){
        resetStyle(b);if((surface()==='mobile'&&b.dataset.mfMovable==='quick')||!b.getClientRects().length)continue;
        const p=saved[surface()]?.[b.dataset.mfMovable],r=rect(b);
        if(p&&Number.isFinite(p.x)&&Number.isFinite(p.y))move(b,p.x*(env.innerWidth-r.width),p.y*(env.innerHeight-r.height));
      }
    }
    function reset(b){if(saved[surface()])delete saved[surface()][b.dataset.mfMovable];resetStyle(b);write();}
    function paintDrag(){dragFrame=null;if(drag?.next)move(drag.block,drag.next.x,drag.next.y,drag);}
    function finish(commit=true){
      if(dragFrame!==null){env.cancelAnimationFrame?.(dragFrame);dragFrame=null;if(commit)paintDrag();}
      if(!drag)return;const d=drag;drag=null;d.block.classList.remove('mf-dragging');
      d.handle.releasePointerCapture?.(d.id);
      if(commit&&d.moved)remember(d.block);else if(d.moved){Object.assign(d.block.style,d.before);}
    }
    const resize=()=>{finish(false);if(frame)env.cancelAnimationFrame?.(frame);frame=env.requestAnimationFrame?.(()=>{frame=null;restore();});};
    const bindings=[];
    for(const block of blocks){
      const handle=block.querySelector('[data-mf-grip]');if(!handle)continue;
      const down=e=>{if((surface()==='mobile'&&block.dataset.mfMovable==='quick')||!active||e.isPrimary===false||(e.button!=null&&e.button!==0))return;e.preventDefault();handle.focus?.({preventScroll:true});const r=rect(block);drag={block,handle,id:e.pointerId,startX:e.clientX,startY:e.clientY,x:r.x,y:r.y,width:r.width,height:r.height,moved:false,before:Object.fromEntries(['left','top','right','bottom','transform'].map(k=>[k,block.style[k]]))};handle.setPointerCapture?.(e.pointerId);};
      const pointer=e=>{if(!drag||drag.handle!==handle||drag.id!==e.pointerId)return;const dx=e.clientX-drag.startX,dy=e.clientY-drag.startY;if(!drag.moved&&Math.hypot(dx,dy)<5)return;drag.moved=true;block.classList.add('mf-dragging');drag.next={x:drag.x+dx,y:drag.y+dy};if(env.requestAnimationFrame){if(dragFrame===null)dragFrame=env.requestAnimationFrame(paintDrag);}else paintDrag();};
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
  if(typeof module!=='undefined')module.exports={esc,price,selectRows,selection,latestTouch,touchTime,recentTouch,RECENT_MS,STATES,clampPanel,mountLayout,captureListScroll,restoreListScroll,tradingViewURL};
  if(typeof document==='undefined')return;
  const root=document.getElementById('monday-fib-root');if(!root)return;
  const platform=window.navigator?.userAgentData?.platform||window.navigator?.platform||'';
  const windowsDesktop=/^Win/i.test(platform);
  const $=id=>document.getElementById(id);
  const icon=name=>`<span class="mf-icon" aria-hidden="true" style="--mf-icon:url(/static/icons/${name}.svg)"></span>`;
  const grip=label=>`<button class="mf-grip" data-mf-grip type="button" aria-label="移动${label}；方向键移动，Home 复位" title="拖动换位置 · 双击还原"><span aria-hidden="true"></span></button>`;
  root.innerHTML=`
    <div id="mf-wallpaper" class="mf-wallpaper" aria-label="所选合约的周一区间 K 线"></div>
    <div id="mf-watermark" class="mf-watermark" aria-hidden="true" hidden><b id="mf-watermark-symbol"></b><span id="mf-watermark-period"></span></div>
    <div id="mf-empty" class="mf-empty-chart" role="status">正在读取最新触价…</div>
    <header class="mf-nav mf-glass" data-mf-movable="nav">${grip('导航')}<details id="mf-home-menu" class="mf-home-menu"><summary aria-label="打开工作区菜单">☰ <b>SPIKE</b></summary><nav class="mf-glass" aria-label="工作区"><a href="#monday-fib" aria-current="page">1.618 · 首页</a><a href="#signals">信号</a><a href="#yolo-detect">巡检</a><a href="#paper-desk">模拟</a><a href="#yolo-detect?tab=account">实盘账户</a><button id="mf-more" type="button">更多工作区…</button></nav></details><span class="mf-divider"></span><h1>周一 1.618</h1><span id="mf-stream" class="mf-stream" title="行情连接中"><i></i><span>连接中</span></span></header>
    <section id="mf-selected" class="mf-selected mf-glass" data-mf-movable="symbol" aria-label="当前合约" hidden>${grip('币种卡片')}
      <div class="mf-symbol-line"><h2 id="mf-symbol">—</h2><span class="mf-perp">USDT 永续</span></div>
      <div id="mf-price" class="mf-price">—</div>
      <div class="mf-selected-meta"><span id="mf-touch"></span><span id="mf-mode">跟随最新</span></div>
      <p id="mf-quote-state" class="mf-quote-state" hidden></p><button id="mf-quick-toggle" class="mf-quick-toggle" aria-expanded="false" aria-controls="mf-quick-panel" disabled>快捷开单 ↗</button>
    </section>
    <div class="mf-list-launch mf-glass" data-mf-movable="launcher">${grip('列表入口')}<button id="mf-list-toggle" class="mf-list-toggle" aria-label="打开或收起触价列表" aria-controls="mf-list-panel" aria-expanded="false">${icon('activity')}<span class="mf-list-label">触价列表</span><span id="mf-count">—</span></button></div>
    <aside id="mf-list-panel" class="mf-list-panel mf-glass" data-mf-movable="list" aria-label="已触价合约" hidden>
      <div class="mf-list-heading">${grip('触价列表')}<div><h2>最新触价</h2><span id="mf-list-count"></span></div><button id="mf-list-close" aria-label="收起触价列表">${icon('x')}</button></div>
      <label class="mf-search">${icon('search')}<input id="mf-search" type="search" placeholder="搜索已触价合约" aria-label="搜索已触价合约" autocomplete="off"></label>
      <div id="mf-filters" class="mf-filters" role="group" aria-label="触价方向">${Object.entries(FILTERS).map(([key,label])=>`<button data-filter="${key}" aria-pressed="${key==='all'}">${label}</button>`).join('')}</div>
      <div id="mf-rows" class="mf-rows" role="region" aria-label="已触价币种滚动列表" tabindex="0"></div>
      <p class="mf-list-note">点击列表切换图表 · 币种名称 ↗ ${windowsDesktop?'本机 ':''}TradingView</p>
    </aside>
    <button id="mf-quick-backdrop" class="mf-quick-backdrop" tabindex="-1" aria-label="关闭快捷开单" hidden></button><aside id="mf-quick-panel" class="mf-quick-panel mf-glass" data-mf-movable="quick" aria-label="快捷开单" hidden>
      <button type="button" class="mf-sheet-grabber" data-quick-grabber aria-label="向下滑动关闭快捷开单"><span></span></button><header class="mf-quick-heading">${grip('快捷开单')}<h2>快捷开单</h2><button type="button" data-quick-close aria-label="关闭快捷开单">${icon('x')}</button></header>
      <div class="mf-quick-modes" role="group" aria-label="交易环境"><button type="button" data-quick-mode="paper" aria-pressed="true">模拟</button><button type="button" data-quick-mode="live" aria-pressed="false">实盘 · 手动</button></div>
      <p data-quick-status class="mf-quick-status"></p><div id="mf-quick-content" class="mf-quick-content"></div>
    </aside>
    <div id="mf-notice" class="mf-notice mf-glass" role="status" hidden></div>
    <div class="mf-dock mf-glass" data-mf-movable="dock" role="group" aria-label="图表控制">${grip('图表工具')}<button id="mf-timeframe" class="mf-timeframe" type="button" aria-label="选择 K 线周期" aria-haspopup="menu" aria-controls="mf-period-menu" aria-expanded="false"><span id="mf-period-label">15m</span><span aria-hidden="true">⌄</span></button><span class="mf-divider"></span><button id="mf-zoom-out" class="mf-zoom" aria-label="缩小 K 线" title="缩小 K 线（−）"><span aria-hidden="true">−</span></button><button id="mf-zoom-in" class="mf-zoom" aria-label="放大 K 线" title="放大 K 线（+）"><span aria-hidden="true">+</span></button><button id="mf-latest" aria-label="回到当前合约最新 K 线" title="回到当前合约最新 K 线，保留缩放宽度">${icon('chevron-right')}<span>最新 K 线</span></button><button id="mf-fit" aria-label="显示全部已加载 K 线并复位" title="恢复价格缩放并显示全部已加载 K 线">${icon('maximize')}<span>全图</span></button><button id="mf-fib" aria-pressed="false" title="显示区间内部 0.5 / 0.618 / 0.705">${icon('adjustments-horizontal')}<span>内部线</span></button><span class="mf-divider"></span><button id="mf-follow" aria-pressed="true">${icon('activity')}<span>跟随最新</span></button></div>
    <div id="mf-period-menu" class="mf-period-menu mf-glass" role="menu" aria-label="K 线周期" hidden>${['1m','5m','15m','30m','1H','4H','1D'].map(p=>`<button type="button" role="menuitemradio" aria-checked="${p==='15m'}" data-period="${p}" tabindex="-1">${p}<span aria-hidden="true">✓</span></button>`).join('')}</div>
    <footer class="mf-footer"><span id="mf-week">纽约周一 · 读取中</span><span id="mf-updated">15m 已收盘 K 线</span><span class="mf-gesture-hint">拖动平移 · 滚轮缩放 · 双击价格轴复位</span></footer>`;
  const layout=mountLayout(root);
  $('mf-more').addEventListener('click',()=>{$('mf-home-menu').open=false;$('workspace-search')?.click();});
  $('mf-home-menu').addEventListener('click',e=>{if(e.target.closest('a'))$('mf-home-menu').open=false;});
  const periods=['1m','5m','15m','30m','1H','4H','1D'];
  let period='15m';try{const saved=window.localStorage?.getItem('spike.monday.timeframe');if(periods.includes(saved))period=saved;}catch{}
  let active=false,data=null,receivedAt=0,error='',timer=null,controller=null,pending=null,revision=0;
  let query='',filter='all',selected='',selectedWeek='',follow=true,internal=false,lastRows='';
  let listOpen=!!window.matchMedia?.('(min-width: 761px)').matches;
  const quick=window.SpikeMondayQuick?.create({
    panel:$('mf-quick-panel'),host:$('mf-quick-content'),trigger:$('mf-quick-toggle'),
    context:()=>({symbol:selected,timeframe:period,side:latestTouch(data?.items?.find(r=>r.symbol===selected)||{})?.side==='upper'?'short':'long'}),
    visibility:open=>{root.classList.toggle('mf-quick-open',open);layout.refresh();},
    load:()=>window.SpikeModules.ensure('monday-live'),
    paper:()=>window.SpikeMondayPaper,live:()=>window.SpikeMondayLive,
  });
  const nowMs=()=>data?.now_ms?data.now_ms+Math.max(0,Date.now()-receivedAt):Date.now();
  const timeFormat=new Intl.DateTimeFormat('zh-CN',{timeZone:'Asia/Shanghai',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hourCycle:'h23'});
  const time=ms=>ms?timeFormat.format(new Date(ms)):'时间待核对';
  const short=s=>s.replace('-USDT-SWAP','');
  function quoteState(r){return STATES[r.state]||((r.stale||!finite(r.received_ms)||nowMs()-r.received_ms>=90000||error)?'行情已滞后':'');}
  // Opening the list is browsing intent; focus a button so mobile keyboards stay closed.
  function panel(open,focus=false){listOpen=open;$('mf-list-panel').hidden=!open;$('mf-list-toggle').setAttribute('aria-expanded',String(open));root.classList.toggle('mf-list-open',open);layout.refresh();if(focus)(open?$('mf-list-close'):$('mf-list-toggle')).focus({preventScroll:true});}
  function sync(){window.SpikeNavigation?.sync('monday-fib',{q:query||null,filter:filter==='all'?null:filter,page:null,symbol:follow?null:selected||null});}
  function renderRows(resetScroll=false){
    const container=$('mf-rows');
    const rows=selectRows(data?.items||[],query,filter,nowMs());
    // Prices change frequently; row identity, links and focus usually do not.
    const structure=JSON.stringify([!!data,selected,rows.map(r=>{const e=latestTouch(r);return [r.symbol,e.side,e.source,touchTime(e),recentTouch(e,nowMs())];})]);
    if(structure!==lastRows){
    const position=captureListScroll(container);
    const html=rows.map(r=>{
      const e=latestTouch(r),fresh=recentTouch(e,nowMs()),state=quoteState(r),side=e.side==='upper'?'上':'下';
      const url=tradingViewURL(r.symbol,platform),label=esc(short(r.symbol));
      const name=`<b>${label}</b>${url?'<span class="mf-external" aria-hidden="true">↗</span>':''}`;
      const stamp=`<small>${e.source==='history'?'历史 · ':''}${time(touchTime(e))}${fresh?' · 刚触及':''}</small>`;
      const destination=windowsDesktop?'在本机 TradingView 打开':'在 TradingView 查看';
      const link=url?`<a class="mf-row-symbol" data-tv-symbol="${esc(r.symbol)}" href="${esc(url)}"${windowsDesktop?'':' target="_blank" rel="noopener noreferrer"'} aria-label="${destination} ${label} 15 分钟图" title="${destination} ${label} 15 分钟图">${name}</a>`:`<span class="mf-row-symbol">${name}</span>`;
      return `<div class="mf-row${fresh?' mf-recent':''}" data-symbol="${esc(r.symbol)}" data-selected="${r.symbol===selected}"><div class="mf-row-info">${link}${stamp}</div><button type="button" class="mf-row-value" data-preview-symbol="${esc(r.symbol)}" aria-pressed="${r.symbol===selected}" aria-label="切换背景 K 线至 ${label}" title="切换背景 K 线 · ${label}"><strong class="mf-${e.side}">${side}沿 1.618</strong><small>${state||price(r.price)}</small></button></div>`;
    }).join('')||`<p class="mf-no-rows">${data?'没有匹配的已触价合约':'正在读取…'}</p>`;

      const focused=container.contains?.(document.activeElement)?document.activeElement?.dataset:null;
      const focusKey=focused?.tvSymbol?'tvSymbol':'previewSymbol',focusSymbol=focused?.[focusKey];
      $('mf-rows').innerHTML=html;lastRows=structure;
      if(focusSymbol)Array.from(container.querySelectorAll('[data-tv-symbol],[data-preview-symbol]')).find(b=>b.dataset[focusKey]===focusSymbol)?.focus({preventScroll:true});
    restoreListScroll(container,resetScroll?{top:0}:position);
    }else{
      const values=new Map(rows.map(r=>[r.symbol,quoteState(r)||price(r.price)]));
      for(const button of container.querySelectorAll('[data-preview-symbol]')){
        const cell=button.querySelector('small'),value=values.get(button.dataset.previewSymbol);
        if(cell&&cell.textContent!==value)cell.textContent=value;
      }
      if(resetScroll)container.scrollTop=0;
    }
    $('mf-list-count').textContent=`${rows.length} 个`;
    for(const b of $('mf-filters').querySelectorAll('[data-filter]'))b.setAttribute('aria-pressed',String(b.dataset.filter===filter));
  }
  function periodMenu(open,focus=false){
    const menu=$('mf-period-menu'),button=$('mf-timeframe');menu.hidden=!open;button.setAttribute('aria-expanded',String(open));
    if(open){
      const rect=button.getBoundingClientRect(),box=menu.getBoundingClientRect(),bounds=root.getBoundingClientRect();
      menu.style.left=Math.max(8,Math.min(bounds.width-box.width-8,rect.left-bounds.left))+'px';
      const above=rect.top-bounds.top-box.height-8;
      menu.style.top=Math.max(8,Math.min(bounds.height-box.height-8,above>=8?above:rect.bottom-bounds.top+8))+'px';
      if(focus)menu.querySelector('[aria-checked="true"]')?.focus({preventScroll:true});
    }else if(focus)button.focus({preventScroll:true});
  }
  function render(){
    $('mf-period-label').textContent=period;
    $('mf-updated').textContent=`${period} 已收盘 K 线${period==='1D'?' · UTC 日线':''}`;
    for(const b of $('mf-period-menu').querySelectorAll('[data-period]'))b.setAttribute('aria-checked',String(b.dataset.period===period));
    const now=nowMs(),connected=data?.stream.connected&&finite(data.stream.last_message_ms)&&now-data.stream.last_message_ms<90000&&!error;
    const streamHTML=`<i></i><span>${connected?'行情在线':'连接待恢复'}</span>`;if($('mf-stream').innerHTML!==streamHTML)$('mf-stream').innerHTML=streamHTML;$('mf-stream').classList.toggle('mf-online',!!connected);$('mf-stream').title=connected?'OKX 行情在线':'行情连接待恢复';
    const notice=error||data?.stream.catalog_error||data?.stream.error||'';$('mf-notice').hidden=!notice;$('mf-notice').textContent=notice;
    if(data){
      if(selectedWeek&&selectedWeek!==data.week){selected='';follow=true;}
      selectedWeek=data.week;
      const row=selection(data.items,selected,follow);
      if(!follow&&selected&&!data.items.some(r=>r.symbol===selected&&latestTouch(r)))follow=true;
      const key=row?.symbol||'',changed=key!==selected;selected=key;
      $('mf-selected').hidden=!row;$('mf-empty').hidden=!!row;$('mf-watermark').hidden=!row;
      if(row){
        const e=latestTouch(row),state=quoteState(row);
        const ticker=short(row.symbol)+'USDT';
        $('mf-watermark-symbol').textContent=ticker;
        $('mf-watermark').style.setProperty('--mf-watermark-chars',String(Math.max(7,ticker.length)));
        $('mf-watermark-period').textContent=`${period} · USDT 永续`;
        $('mf-symbol').textContent=short(row.symbol);$('mf-price').textContent=price(row.price);
        $('mf-touch').textContent=`${e.side==='upper'?'上':'下'}沿 1.618 · ${time(touchTime(e))}${e.source==='history'?' 历史':''}`;
        $('mf-touch').className='mf-'+e.side;
        $('mf-quote-state').hidden=!state;$('mf-quote-state').textContent=state;
        $('mf-mode').textContent=follow?'跟随最新':'已固定';
        if(changed&&root.animate&&!window.matchMedia?.('(prefers-reduced-motion: reduce)').matches)$('mf-selected').animate([{opacity:.55,transform:'translateY(4px)'},{opacity:1,transform:'translateY(0)'}],{duration:220,easing:'ease-out'});
        if(active&&!document.hidden)window.SpikeMondayFibChart?.show($('mf-wallpaper'),row,data.week,{internal,period});
      }else{
        $('mf-empty').textContent=data.monday_complete?'本周尚无 1.618 触价，等待最新行情':'纽约周一区间正在形成，完成后开始监控';
        window.SpikeMondayFibChart?.hide();
      }
      $('mf-count').textContent=String(selectRows(data.items).length);
      $('mf-week').textContent=`本周 · ${data.week} 纽约周一${data.monday_complete?'':' · 形成中'}`;
    }
    $('mf-follow').setAttribute('aria-pressed',String(follow));$('mf-follow').title=follow?'自动显示最近触价的合约':'回到最新触价，并自动跟随';
    for(const id of ['mf-fit','mf-fib','mf-latest','mf-zoom-in','mf-zoom-out','mf-quick-toggle'])$(id).disabled=!selected;
    renderRows();
  }
  function schedule(){clearTimeout(timer);if(active&&!document.hidden)timer=setTimeout(refresh,5000);}
  function refresh(){
    if(!active||document.hidden)return Promise.resolve();if(pending)return pending;
    clearTimeout(timer);const rev=revision,c=new AbortController();controller=c;const timeout=setTimeout(()=>c.abort(),12000);
    const task=(async()=>{try{
      const warming=window.SpikeStartup?.take('monday-snapshot',c.signal),warm=warming?await warming:null;
      let d=warm?.value;
      if(!warm){const res=await fetch('/api/monday-fib?compact=true',{cache:'no-store',credentials:'same-origin',signal:c.signal});d=await res.json();if(!res.ok)throw Error(typeof d.detail==='string'?d.detail:'监控暂不可用，正在重试');}
      if(!Array.isArray(d.items)||!d.stream||!d.week)throw Error('监控数据格式异常');
      if(rev!==revision||!active)return;data=d;receivedAt=warm?.receivedAt||Date.now();error='';render();
    }catch(e){if(rev===revision&&active){error=e.name==='AbortError'?'数据请求超时，正在重试':e.message;render();}}
    finally{clearTimeout(timeout);if(controller===c)controller=null;if(pending===task)pending=null;if(rev===revision)schedule();}})();pending=task;return task;
  }
  function cancel(preserveChart=false){periodMenu(false);revision++;controller?.abort();controller=null;pending=null;clearTimeout(timer);if(preserveChart)window.SpikeMondayFibChart?.pause?.();else window.SpikeMondayFibChart?.hide();}
  function setActive(value){if(active===value)return;active=value;if(!value)quick?.close(false);cancel();layout.setActive(value);if(active){panel(listOpen);render();refresh();}}
  $('mf-search').addEventListener('input',e=>{query=e.target.value;renderRows(true);sync();});
  $('mf-filters').addEventListener('click',e=>{const b=e.target.closest('[data-filter]');if(!b)return;filter=b.dataset.filter;renderRows(true);sync();});
  $('mf-rows').addEventListener('click',e=>{
    // The external link owns only its text; dates, gaps and values select locally.
    if(e.target.closest('[data-tv-symbol]')){e.stopPropagation();return;}
    const b=e.target.closest('[data-symbol]');if(!b)return;
    selected=b.dataset.symbol;follow=false;render();sync();
    if(window.matchMedia?.('(max-width: 760px)').matches)panel(false,true);
  });
  $('mf-list-toggle').addEventListener('click',()=>panel(!listOpen,true));$('mf-list-close').addEventListener('click',()=>panel(false,true));
  $('mf-follow').addEventListener('click',()=>{follow=true;render();sync();});
  $('mf-fit').addEventListener('click',()=>window.SpikeMondayFibChart?.fit());
  $('mf-latest').addEventListener('click',()=>window.SpikeMondayFibChart?.latest());
  $('mf-zoom-in').addEventListener('click',()=>window.SpikeMondayFibChart?.zoom(.8));
  $('mf-zoom-out').addEventListener('click',()=>window.SpikeMondayFibChart?.zoom(1.25));
  $('mf-fib').addEventListener('click',()=>{internal=!internal;$('mf-fib').setAttribute('aria-pressed',String(internal));window.SpikeMondayFibChart?.setInternal(internal);});
  $('mf-timeframe').addEventListener('click',()=>periodMenu($('mf-period-menu').hidden,true));
  $('mf-timeframe').addEventListener('keydown',e=>{if(['ArrowDown','ArrowUp'].includes(e.key)){e.preventDefault();periodMenu(true,true);}});
  $('mf-period-menu').addEventListener('click',e=>{
    const value=e.target.closest('[data-period]')?.dataset.period;if(!periods.includes(value))return;
    period=value;try{window.localStorage?.setItem('spike.monday.timeframe',period);}catch{}
    periodMenu(false,true);render();
  });
  $('mf-period-menu').addEventListener('keydown',e=>{
    const buttons=Array.from($('mf-period-menu').querySelectorAll('[data-period]')),i=buttons.indexOf(document.activeElement);
    if(['ArrowDown','ArrowUp','Home','End'].includes(e.key)){e.preventDefault();e.stopPropagation();const n=e.key==='Home'?0:e.key==='End'?buttons.length-1:(i+(e.key==='ArrowDown'?1:-1)+buttons.length)%buttons.length;buttons[n]?.focus();}
    if(e.key==='Tab')periodMenu(false);
  });
  document.addEventListener('pointerdown',e=>{if(!$('mf-period-menu').hidden&&!$('mf-period-menu').contains(e.target)&&!$('mf-timeframe').contains(e.target))periodMenu(false);});
  window.addEventListener?.('resize',()=>periodMenu(false));
  root.addEventListener('keydown',e=>{if(e.key==='Escape'&&!$('mf-period-menu').hidden){e.stopPropagation();periodMenu(false,true);}else if(e.key==='Escape'&&listOpen){e.stopPropagation();panel(false,true);}});
  document.addEventListener('visibilitychange',()=>{if(document.hidden){cancel(true);layout.setActive(false);}else if(active){layout.setActive(true);render();refresh();}});
  window.SpikeNavigation?.register('monday-fib',p=>{query=p.q||'';filter=Object.hasOwn(FILTERS,p.filter)?p.filter:'all';$('mf-rows').scrollTop=0;selected=p.symbol||'';follow=!selected;$('mf-search').value=query;if(data)render();});
  panel(listOpen);window.SpikeMondayFib={setActive,refresh};
})();
