/* Owner-selected terminal composition. Public display endpoints only.
 * Native 15m/1H/4H OHLC stays source-owned; 1D uses complete UTC groups of six
 * closed 4H bars. Moving averages use only current and preceding closes.
 * Chart API: TradingView Lightweight Charts 4.2, bundled by this repository.
 */
(() => {
  'use strict';
  const PERIODS={'15m':900000,'30m':1800000,'1H':3600000,'4H':14400000,'1D':86400000};
  const chartStyle=typeof module!=='undefined'?require('./chart-style.js'):window.SpikeChartStyle;
  const tickFormats=[{month:'2-digit',day:'2-digit'},{hour:'2-digit',minute:'2-digit',hour12:false}].map(options=>new Intl.DateTimeFormat('zh-CN',{timeZone:'Asia/Shanghai',...options}));
  const MA=chartStyle.lines;
  const perf=typeof module!=='undefined'?require('./workspace-runtime.js'):window.SpikeRuntime;
  const DEFAULTS=['BTC','ETH','SOL','BNB','LINK','AVAX'];
  const full=base=>base+'-USDT-SWAP',base=s=>s.replace('-USDT-SWAP','');
  const esc=x=>String(x??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const finite=x=>typeof x==='number'&&Number.isFinite(x);
  const price=x=>x!=null&&x!==''?chartStyle.price(Number(x)):'—';
  const signed=(x,suffix='')=>finite(x)?(x>0?'+':'')+x+suffix:'—';
  const percent=x=>finite(x)?(x>0?'+':'')+x.toFixed(2)+'%':'—';
  const tone=x=>x>0?'up':x<0?'down':'';
  const dateFormat=new Intl.DateTimeFormat('zh-CN',{timeZone:'Asia/Shanghai',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false});
  const clock=x=>x?dateFormat.format(new Date(x)):'—';
  function candles(raw){return (raw||[]).map(r=>({time:(r.time??r.t)/1000,open:r.open??r.o,high:r.high??r.h,low:r.low??r.l,close:r.close??r.c,volume:r.volume??r.v,...Object.fromEntries(MA.filter(m=>finite(r[m.key])).map(m=>[m.key,r[m.key]]))})).filter(r=>[r.time,r.open,r.high,r.low,r.close,r.volume].every(finite));}
  function daily(rows,now=Date.now()){
    const groups=new Map();for(const row of rows){const t=Math.floor(row.time/86400)*86400;if(!groups.has(t))groups.set(t,[]);groups.get(t).push(row);}
    return [...groups].sort((a,b)=>a[0]-b[0]).flatMap(([time,items])=>{
      const a=items.slice().sort((x,y)=>x.time-y.time);
      if((time+86400)*1000>now||a.length!==6||a.some((r,i)=>r.time!==time+i*14400))return [];
      return [{time,open:a[0].open,high:Math.max(...a.map(r=>r.high)),low:Math.min(...a.map(r=>r.low)),close:a[5].close,volume:a.reduce((n,r)=>n+r.volume,0)}];
    });
  }
  function average(rows,n,step){let sum=0;return rows.map((r,i)=>{sum+=r.close;if(i>=n)sum-=rows[i-n].close;return i>=n-1&&r.time-rows[i-n+1].time===(n-1)*step?{time:r.time,value:sum/n}:null;}).filter(Boolean);}
  function exponential(rows,n,step){let value,count=0,previous;const alpha=2/(n+1);return rows.flatMap(r=>{if(previous!==undefined&&r.time-previous!==step)count=0;value=count?alpha*r.close+(1-alpha)*value:r.close;count++;previous=r.time;return count>=n?[{time:r.time,value}]:[];});}
  const {rankedSymbols}=typeof module!=='undefined'?require('./symbol-list-model.js'):window.SpikeSymbolList;
  if(typeof module!=='undefined')module.exports={candles,daily,average,exponential,rankedSymbols};
  if(typeof document==='undefined')return;
  const chartTools=window.SpikeChartTools,chartViews=new chartTools.Views();
  let chartIdentity=null,chartControls=null,hoverTime=null;
  let active=false,view=null,side=null,chart=null,candleSeries=null,volumeSeries=null,maSeries=[],lines=[],resize=null,themeObserver=null;
  let selected=full('BTC'),period='15m',scope='all',order='volume',query='',catalog=[],catalogError='',snapshot={},contexts=new perf.BoundedCache(16),quotes=new Map(),chartCache=new perf.BoundedCache(16),chartError='',chartPending=false,chartToken=0,revision='',timer=null,quoteBusy=false,libraryPromise,lastChartAt=0;
  let showMA=true,showVolume=true,showLevels=true;
  let intelligence=null,intelligenceBusy=false,intelligenceToken=0;
  let maValues=new Map();
  let favorites=DEFAULTS.map(full),notes={},sparks=new perf.BoundedCache(32);
  try{const f=JSON.parse(localStorage.getItem('spike.market.watch.v1'));if(Array.isArray(f))favorites=f.filter(x=>/^[A-Z0-9]{1,24}-USDT-SWAP$/.test(x));notes=JSON.parse(localStorage.getItem('spike.market.notes.v1'))||{};}catch{}
  const syncSeries=perf.createSeriesSync(),requests=perf.createRequests({timeoutMs:12000});
  let listSymbols=[],listFrame=null,listResize=null,activation=0,lastLegend='',chartData=null;
  const rowNodes=new Map(),failedIcons=new Set(),ROW_HEIGHT=60;
  const crosshair=perf.frameTask((row,time,volume)=>{if(!active||!chart)return;paintLegend(row,time);if(showVolume)$('.mt-volume-label').textContent='成交量　'+Number(volume??row?.volume??0).toLocaleString('en-US',{notation:'compact',maximumFractionDigits:2});});
  const idle=perf.idleRelease(()=>{chartCache.clear();contexts.clear();sparks.clear();quotes.clear();catalog=[];listSymbols=[];rowNodes.clear();side?.querySelector('.mt-watch-list').replaceChildren();});
  const $=selector=>view?.querySelector(selector);
  const save=(key,value)=>{try{localStorage.setItem(key,JSON.stringify(value));return true;}catch{return false;}};
  const get=url=>requests.json(url);
  function library(){return chartTools.library();}
  function watchSymbols(){return rankedSymbols(scope==='watch'?favorites:catalog.map(x=>x.symbol),quotes,order,query);}
  function setHTML(node,html){if(node&&node.innerHTML!==html)node.innerHTML=html;}
  function initSidebar(){
    side=document.getElementById('market-watch-sidebar');if(side){view.append(side);return;}
    side=document.createElement('section');side.id='market-watch-sidebar';side.setAttribute('aria-label','市场行情');
    side.innerHTML=`<div class="stitch-market-controls"><div class="mt-watch-search"><span class="rail-icon" style="--icon:url(/static/icons/search.svg)" aria-hidden="true"></span><input type="search" aria-label="搜索交易对" placeholder="搜索交易对" autocomplete="off"><kbd>/</kbd><button type="button" data-watch-clear aria-label="清空交易对搜索" hidden>×</button></div><div class="mt-watch-controls"><div class="mt-watch-tabs" role="group" aria-label="行情范围"><button data-watch-scope="watch" aria-pressed="true">自选 <span data-watch-count></span></button><button data-watch-scope="all" aria-pressed="false">全市场 <span data-all-count></span></button></div><select class="mt-watch-order" aria-label="行情排序"><option value="default">默认排序</option><option value="volume">成交额</option><option value="gainers">涨幅优先</option><option value="losers">跌幅优先</option></select></div></div><div class="stitch-screener-panel"><header class="mt-watch-heading"><h2>行情列表</h2><details class="quiet-menu"><summary aria-label="市场页面">观察</summary><div class="quiet-menu-panel"><button type="button" data-market-leaders>龙头观察</button></div></details></header><div class="mt-watch-columns"><span data-watch-result></span><span>价格 / 24h 涨跌</span><span>24h 成交额</span><span>报价状态</span></div><div class="mt-watch-list" role="group" aria-label="交易对列表"></div><p class="mt-catalog-status" role="status" hidden></p><section class="mt-watch-status" aria-live="polite"></section></div>`;
    view.append(side);
    side.querySelector('.mt-watch-order').addEventListener('change',e=>{order=e.target.value;paintList(true);});
    side.querySelector('input').addEventListener('input',e=>{query=e.target.value;paintList(true);});
    side.addEventListener('click',e=>{const b=e.target.closest('button');if(!b)return;
      if(b.dataset.watchScope){scope=b.dataset.watchScope;if(scope==='all'&&order==='default')order='volume';paintList(true);loadQuotes();}
      if(b.dataset.watchSort){order=b.dataset.watchSort;paintList(true);}
      if(b.hasAttribute('data-watch-clear')){query='';side.querySelector('input').value='';paintList(true);side.querySelector('input').focus();}
      if(b.dataset.marketSymbol)select(b.dataset.marketSymbol);
      if(b.hasAttribute('data-market-leaders'))document.querySelector('.nav-item[data-view="auto-leaders"]').click();
      if(b.hasAttribute('data-market-search'))document.getElementById('workspace-search').click();
      if(b.hasAttribute('data-watch-sound'))snapshot.toggleSound?.();
    });
    document.addEventListener('keydown',e=>{if(!active||e.key!=='/'||e.defaultPrevented||e.isComposing||e.metaKey||e.ctrlKey||e.altKey||document.querySelector('dialog[open]')||e.target.closest('input,textarea,select,[contenteditable=true],[role=dialog]'))return;e.preventDefault();side.querySelector('input').focus();});

    side.querySelector('.mt-watch-list').addEventListener('focusin',e=>{if(e.target.matches('.mt-watch-row'))side.querySelectorAll('.mt-watch-row').forEach(row=>row.tabIndex=row===e.target?0:-1);});
    const list=side.querySelector('.mt-watch-list');
    list.addEventListener('scroll',()=>{if(listFrame===null)listFrame=requestAnimationFrame(()=>{listFrame=null;paintVisibleList();});},{passive:true});
    list.addEventListener('keydown',e=>{
      if(!['ArrowUp','ArrowDown','Home','End'].includes(e.key)||!listSymbols.length)return;
      e.preventDefault();const index=listSymbols.indexOf(e.target.dataset.marketSymbol);
      const next=e.key==='Home'?0:e.key==='End'?listSymbols.length-1:Math.max(0,Math.min(listSymbols.length-1,index+(e.key==='ArrowDown'?1:-1)));
      focusSymbol(next);
    });
    side.querySelector('input').addEventListener('keydown',e=>{if(e.key==='Escape'&&query){e.preventDefault();e.stopPropagation();e.target.value='';query='';paintList(true);}if(e.key==='ArrowDown'){e.preventDefault();focusSymbol(0);}if(e.key==='Enter'&&watchSymbols()[0])select(watchSymbols()[0]);});
  }
  function paintList(reset=false){
    if(!active||!side)return;
    side.querySelectorAll('[data-watch-scope]').forEach(b=>b.setAttribute('aria-pressed',String(scope===b.dataset.watchScope)));
    side.querySelector('.mt-watch-order').value=order;
    side.querySelector('[data-watch-clear]').hidden=!query;
    side.querySelector('.mt-watch-search kbd').hidden=!!query;
    side.querySelector('[data-watch-count]').textContent=favorites.length;
    side.querySelector('[data-all-count]').textContent=catalog.length||'—';
    const list=side.querySelector('.mt-watch-list'),oldTop=list.scrollTop,oldIndex=Math.floor(oldTop/ROW_HEIGHT),anchor=listSymbols[oldIndex];
    const focused=list.contains(document.activeElement)?document.activeElement.dataset.marketSymbol:null;
    listSymbols=watchSymbols();
    const index=listSymbols.indexOf(anchor);
    list.scrollTop=reset?0:index>=0?index*ROW_HEIGHT+(oldTop%ROW_HEIGHT):oldTop;
    side.querySelector('[data-watch-result]').textContent=(scope==='watch'?'自选':'交易对')+' · '+listSymbols.length;
    side.querySelector('.mt-catalog-status').hidden=!catalogError;side.querySelector('.mt-catalog-status').textContent=catalogError;
    list.classList.toggle('is-market-list',scope==='all');
    if(!reset&&focused&&listSymbols.includes(focused))focusSymbol(listSymbols.indexOf(focused));else paintVisibleList();
  }
  function focusSymbol(index){
    const list=side.querySelector('.mt-watch-list');
    if(index*ROW_HEIGHT<list.scrollTop)list.scrollTop=index*ROW_HEIGHT;
    else if((index+1)*ROW_HEIGHT>list.scrollTop+list.clientHeight)list.scrollTop=(index+1)*ROW_HEIGHT-list.clientHeight;
    paintVisibleList(listSymbols[index]);rowNodes.get(listSymbols[index])?.focus({preventScroll:true});
  }
  function paintVisibleList(focus){
    if(!active||!side)return;
    const list=side.querySelector('.mt-watch-list'),count=listSymbols.length;
    const range=perf.virtualWindow(count,list.scrollTop,list.clientHeight||420,ROW_HEIGHT);
    const symbols=listSymbols.slice(range.start,range.end),focused=focus||document.activeElement?.dataset?.marketSymbol;
    const tabSymbol=symbols.includes(focused)?focused:symbols.includes(selected)?selected:symbols[0];
    if(!count){rowNodes.clear();setHTML(list,'<p class="mt-empty">'+(catalogError?'行情暂不可用，可稍后重试':query?'没有匹配的交易对。'+(scope==='watch'?'试试全市场，或清空搜索。':'检查币种名称，或清空搜索。'):scope==='watch'?'还没有自选。可从全市场选择交易对，点击星标收藏。':'正在读取全市场行情…')+'</p>');return;}
    if(!list.querySelector('.mt-list-spacer')){list.replaceChildren();for(let i=0;i<2;i++){const spacer=document.createElement('div');spacer.className='mt-list-spacer';spacer.setAttribute('aria-hidden','true');list.append(spacer);}}
    const top=list.firstElementChild,bottom=list.lastElementChild;
    top.style.height=range.before+'px';bottom.style.height=range.after+'px';list.dataset.total=String(count);
    for(const [symbol,node] of rowNodes)if(!symbols.includes(symbol)){node.remove();rowNodes.delete(symbol);}
    let previous=top;
    for(const symbol of symbols){
      const coin=base(symbol),q=quotes.get(symbol),change=q?.change_24h_percent==null?null:Number(q.change_24h_percent),fresh=q&&q.expires_ms>Date.now();
      let row=rowNodes.get(symbol);
      if(!row){row=document.createElement('button');row.type='button';row.dataset.marketSymbol=symbol;row.setAttribute('aria-label','查看 '+coin+' USDT');rowNodes.set(symbol,row);}
      if(row.dataset.scope!==scope){const icon=failedIcons.has(coin)?null:DEFAULTS.includes(coin)?'/static/icons/coins/'+coin.toLowerCase()+'.png':'https://static.okx.com/cdn/oksupport/asset/currency/icon/'+encodeURIComponent(coin.toLowerCase())+'.png';
        row.innerHTML=`${icon?`<img src="${icon}" alt="" width="28" height="28" loading="lazy" decoding="async" referrerpolicy="no-referrer">`:`<span class="mt-coin-monogram" aria-hidden="true">${esc(coin.slice(0,2))}</span>`}<span class="mt-watch-coin"><strong>${esc(coin)}</strong>${scope==='watch'?`<canvas data-spark="${esc(symbol)}" width="120" height="28" aria-label="${esc(coin)} 最近收盘趋势"></canvas>`:'<small>USDT 永续</small>'}</span><span class="mt-watch-quote"><b></b><small></small></span><span class="mt-watch-volume"></span><span class="mt-watch-range"></span>`;row.dataset.scope=scope;const img=row.querySelector('img');if(img)img.addEventListener('error',()=>{failedIcons.add(coin);const fallback=document.createElement('span');fallback.className='mt-coin-monogram';fallback.setAttribute('aria-hidden','true');fallback.textContent=coin.slice(0,2);img.replaceWith(fallback);},{once:true});}
      row.className='mt-watch-row'+(symbol===selected?' selected':'');row.tabIndex=symbol===tabSymbol?0:-1;row.setAttribute('aria-pressed',String(symbol===selected));
      const value=row.querySelector('.mt-watch-quote b'),delta=row.querySelector('.mt-watch-quote small'),valueText=price(q?.last),deltaText=fresh&&finite(change)?percent(change):q?'报价过期':'暂无报价';
      if(value.textContent!==valueText)value.textContent=valueText;if(delta.textContent!==deltaText)delta.textContent=deltaText;delta.className=fresh?tone(change):'';
      row.querySelector('.mt-watch-volume').textContent=fresh&&finite(Number(q?.volume_usdt_24h))?Number(q.volume_usdt_24h).toLocaleString('en-US',{notation:'compact',maximumFractionDigits:2}):'—';
      row.querySelector('.mt-watch-range').textContent=fresh?'已同步':q?'已过期':'待同步';
      if(previous.nextElementSibling!==row)list.insertBefore(row,previous.nextElementSibling);previous=row;
      const canvas=row.querySelector('canvas'),rows=sparks.get(symbol);if(canvas&&canvas._rows!==rows){drawSpark(canvas,rows);canvas._rows=rows;}
    }
    if(focused&&document.activeElement!==rowNodes.get(focused)&&symbols.includes(focused))rowNodes.get(focused).focus({preventScroll:true});
  }
  function drawSpark(canvas,rows){if(!rows?.length)return;const a=rows.slice(-32).map(r=>r.close),ctx=canvas.getContext('2d'),lo=Math.min(...a),hi=Math.max(...a),pad=hi-lo||1;ctx.clearRect(0,0,120,28);ctx.beginPath();a.forEach((x,i)=>{const px=i*118/(a.length-1)+1,py=25-(x-lo)/pad*22;i?ctx.lineTo(px,py):ctx.moveTo(px,py);});ctx.strokeStyle=a.at(-1)>=a[0]?'#00e599':'#ff3366';ctx.lineWidth=1.4;ctx.stroke();}
  function create(){
    const mount=document.getElementById('ms-terminal-top');if(!mount)return;
    if(view===mount&&view.querySelector('.mt-topline'))return;
    destroyChart();view=mount;
    view.innerHTML=`<section class="mt-quote-panel" aria-label="当前交易对行情"><div class="mt-topline"><div class="mt-pair"><h2><button type="button" data-pair data-pair-switch aria-label="切换交易对" aria-haspopup="dialog"></button></h2><span>OKX 永续</span><button class="mt-star" data-market-star aria-label="加入自选"></button></div><div class="mt-quotes" aria-live="polite"></div><section class="stitch-market-pulse" aria-label="合约市场指标"></section></div></section><section class="mt-chart-panel" aria-label="行情 K 线图"><div class="mt-chart-toolbar"><div class="mt-expanded-heading"><strong data-expanded-pair></strong><span>OKX 永续</span><kbd>Esc 退出</kbd></div><div class="mt-chart-control-row"><div class="mt-periods" role="group" aria-label="图表周期">${Object.keys(PERIODS).map(p=>`<button type="button" data-period="${p}" aria-pressed="${period===p}">${p}</button>`).join('')}</div><div class="mt-chart-actions" role="group" aria-label="图表显示"><details class="quiet-menu mt-chart-settings"><summary>图表工具</summary><div class="quiet-menu-panel">${chartTools.toolbar()}<button type="button" data-chart-ma aria-pressed="${showMA}">六条均线</button><button type="button" data-chart-volume aria-pressed="${showVolume}">成交量</button><button type="button" data-chart-levels aria-pressed="${showLevels}">关键价位</button><button type="button" data-chart-reset>重置缩放</button></div></details><button type="button" data-chart-expand aria-pressed="false" aria-label="放大行情图表"><img src="/static/icons/maximize.svg" alt="" width="16" height="16"></button></div></div><div class="mt-ohlc cv-readout"></div></div><div class="mt-chart-surface"><div class="mt-ma-legend chart-ma-legend" aria-label="六条均线数值" ${showMA?'':'hidden'}></div><div class="mt-chart cv-canvas"></div></div><div class="mt-volume-label" ${showVolume?'':'hidden'}></div><div class="mt-chart-feedback" role="status"></div><div class="mt-chart-source"><span data-chart-source></span><a href="https://www.tradingview.com/" target="_blank" rel="noopener">TradingView Lightweight Charts™</a></div></section><aside class="mt-intelligence" aria-label="资金与强平信息"></aside><aside class="mt-evidence" aria-label="观察依据"></aside><dialog class="mt-chart-dialog" aria-label="放大行情图表"></dialog><dialog class="mt-symbol-dialog" aria-label="切换交易对"><header><strong>切换交易对</strong><button type="button" data-symbol-close aria-label="关闭交易对选择">×</button></header><input type="search" data-symbol-search placeholder="搜索币种，例如 BTC" aria-label="搜索并切换交易对" autocomplete="off"><div data-symbol-results></div></dialog>`;
    const picker=$('.mt-symbol-dialog');
    picker.querySelector('input').addEventListener('input',paintSymbolPicker);
    picker.querySelector('input').addEventListener('keydown',e=>{if(e.key==='Enter'){e.preventDefault();picker.querySelector('[data-pick-symbol]')?.click();}if(e.key==='ArrowDown'){e.preventDefault();picker.querySelector('[data-pick-symbol]')?.focus();}});
    picker.addEventListener('close',()=>{if(active)$('[data-pair-switch]')?.focus({preventScroll:true});});
    const dialog=$('.mt-chart-dialog');
    dialog.addEventListener('close',()=>{if(!dialog.open)restoreChartPanel();});
    dialog.addEventListener('click',e=>{if(e.target!==dialog)return;const r=dialog.getBoundingClientRect();if(e.clientX<r.left||e.clientX>r.right||e.clientY<r.top||e.clientY>r.bottom)setChartExpanded(false);});
    view.onclick=e=>{const b=e.target.closest('button');if(!b)return;
      if(b.hasAttribute('data-pair-switch')){$('.mt-symbol-dialog').showModal();$('[data-symbol-search]').value='';paintSymbolPicker();$('[data-symbol-search]').focus();}
      if(b.hasAttribute('data-symbol-close'))$('.mt-symbol-dialog').close();
      if(b.dataset.pickSymbol){$('.mt-symbol-dialog').close();select(b.dataset.pickSymbol);}
      if(b.dataset.period&&period!==b.dataset.period){period=b.dataset.period;revision='';paintQuote();loadChart();}
      if(b.hasAttribute('data-records')){document.querySelector('[data-ms-panel="overview"]')?.click();document.getElementById('ms-recent-events')?.scrollIntoView({block:'start',behavior:'smooth'});}
      if(b.hasAttribute('data-market-star')){favorites=favorites.includes(selected)?favorites.filter(x=>x!==selected):[...favorites,selected];save('spike.market.watch.v1',favorites);paintList();paintQuote();}
      if(b.hasAttribute('data-chart-retry'))loadChart();
      if(b.hasAttribute('data-chart-ma')){showMA=!showMA;maSeries.forEach(m=>m.series.applyOptions({visible:showMA}));$('.mt-ma-legend').hidden=!showMA;b.setAttribute('aria-pressed',String(showMA));}
      if(b.hasAttribute('data-chart-volume')){showVolume=!showVolume;volumeSeries?.applyOptions({visible:showVolume});$('.mt-volume-label').hidden=!showVolume;candleSeries?.priceScale().applyOptions({scaleMargins:{top:.08,bottom:showVolume?.24:.08}});b.setAttribute('aria-pressed',String(showVolume));}
      if(b.hasAttribute('data-chart-levels')){showLevels=!showLevels;b.setAttribute('aria-pressed',String(showLevels));lines.forEach(line=>line.applyOptions({lineVisible:showLevels}));}
      if(b.hasAttribute('data-chart-expand'))setChartExpanded(!$('.mt-chart-dialog').open);
      if(b.hasAttribute('data-chart-reset')){const rows=chartCache.get(selected+'|'+period)?.rows;if(rows&&chart){chart.priceScale('right').applyOptions({autoScale:true});chart.timeScale().setVisibleLogicalRange({from:Math.max(0,rows.length-(($('.mt-chart')?.clientWidth||800)<500?60:100)),to:rows.length+5});}}
    };
    view.onchange=e=>{if(e.target.matches('[data-terminal-zone]'))snapshot.setZone?.(e.target.value);};
    themeObserver=new MutationObserver(()=>{paintChart();});themeObserver.observe(document.documentElement,{attributes:true,attributeFilter:['data-theme']});
    initSidebar();if(!listResize){listResize=new ResizeObserver(()=>paintVisibleList());listResize.observe(side.querySelector('.mt-watch-list'));}paintQuote();paintList();paintEvidence();loadChart();loadIntelligence();
  }
  function paintSymbolPicker(){
    const input=$('[data-symbol-search]'),list=$('[data-symbol-results]');
    const symbols=rankedSymbols(catalog.length?catalog.map(x=>x.symbol):favorites,quotes,'volume',input.value).slice(0,60);
    setHTML(list,symbols.length?symbols.map(symbol=>`<button type="button" data-pick-symbol="${esc(symbol)}" aria-pressed="${symbol===selected}"><strong>${esc(base(symbol))} <small>/ USDT</small></strong><span>${price(quotes.get(symbol)?.last)}</span></button>`).join(''):'<p>没有匹配的交易对</p>');
  }
  function restoreChartPanel(){
    const panel=$('.mt-chart-panel');if(!panel?.classList.contains('is-expanded'))return;
    panel.classList.remove('is-expanded');view.insertBefore(panel,$('.mt-evidence'));
    document.body.classList.remove('market-chart-open');
    const button=$('[data-chart-expand]');button.setAttribute('aria-pressed','false');button.setAttribute('aria-label','放大行情图表');
    if(active)button.focus({preventScroll:true});
  }
  function setChartExpanded(expanded){
    const dialog=$('.mt-chart-dialog'),panel=$('.mt-chart-panel');if(!dialog||!panel)return;
    if(!expanded){if(dialog.open)dialog.close();restoreChartPanel();return;}
    dialog.append(panel);panel.classList.add('is-expanded');dialog.showModal();
    document.body.classList.add('market-chart-open');
    const button=$('[data-chart-expand]');button.setAttribute('aria-pressed','true');button.setAttribute('aria-label','收起行情图表');button.focus({preventScroll:true});
  }
  function paintQuote(){
    if(!view)return;const q=quotes.get(selected),fresh=q&&q.expires_ms>Date.now(),change=q?.change_24h_percent==null?null:Number(q.change_24h_percent);
    if($('[data-pair]').textContent!==base(selected)+' / USDT')$('[data-pair]').textContent=base(selected)+' / USDT';
    $('[data-expanded-pair]').textContent=base(selected)+' / USDT';
    const star=$('[data-market-star]'),saved=favorites.includes(selected);star.setAttribute('aria-label',saved?'移出自选':'加入自选');star.setAttribute('aria-pressed',String(saved));star.innerHTML=`<img src="/static/icons/${saved?'star-filled':'star'}.svg" alt="" width="15" height="15">`;
    view.querySelectorAll('[data-period]').forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.period===period)));
    setHTML($('.stitch-market-pulse'),window.SpikeMarketIntelligence.metrics(intelligence,intelligence?.quote?.expires_ms>Date.now()?intelligence.quote:fresh?q:null));
    setHTML($('.mt-quotes'),`<div><span>最新价格 <small>${q&&!fresh?'报价过期':'USDT'}</small></span><strong>${price(q?.last)}</strong>${q&&!fresh?'<em class="mt-quote-stale">报价已过期</em>':''}</div><div><span>24h 变化</span><strong class="${fresh?tone(change):''}">${fresh&&finite(change)?percent(change):'—'}</strong></div>`);
  }
  function paintEvidence(){
    if(!view)return;const c=snapshot.context,b=c?.bias,row=snapshot.live?.snapshot?.latest?.['60']?.at(-1),valid=!snapshot.error&&!c?.stale&&!c?.error&&finite(b?.score),components=valid?b.components:[],get=name=>components.find(x=>x.name===name)?.score;
    const breadth=get('市场广度'),momentum=get('BTC / ETH 动量'),structure=get('BTC 结构'),n=row?.n_alts;
    const current=contexts.get(selected)||(selected===full('BTC')?c:null),levels=current?.levels||[],prices=[levels.find(x=>x.label==='周一高'),levels.find(x=>x.label==='昨日 POC'),levels.find(x=>x.label==='周一低')].filter(Boolean);
    const blocks=[['市场广度',signed(breadth),finite(n)&&valid?`上涨 ${Math.round(row.breadth_up*n)}　下跌 ${Math.round(row.breadth_down*n)} · ${n} 个样本`:'等待完整新鲜行情',breadth],['BTC / ETH 动量',signed(momentum),valid&&finite(row?.btc_z)&&finite(row?.eth_z)?`BTC 波动 ${Math.abs(row.btc_z).toFixed(2)}×　ETH ${Math.abs(row.eth_z).toFixed(2)}×`:'等待完整新鲜行情',momentum],['BTC 结构',valid?c.structure.label:'—','15m 收盘与均线方向',structure]];
    const detailsOpen=$('.mt-evidence details')?.open;
    setHTML($('.mt-evidence'),`<h2>宏观共振矩阵</h2><div class="mt-evidence-blocks">${blocks.map(([label,value,caption,score],i)=>`<section><h3>${label} <strong class="${tone(score)}">${esc(value)}</strong></h3><p>${caption}</p><div class="mt-evidence-meter ${finite(score)?'':'unavailable'} ${i===1?'momentum':i===2?'structure':''}" aria-label="${label} ${value}"><span style="width:${finite(score)?(score+100)/2:0}%"></span></div></section>`).join('')}</div><section class="mt-key-levels"><h2>关键价位 <small>${esc(base(selected))}</small></h2>${prices.map((l,i)=>`<div><span>${esc(l.label)}</span><b>${price(l.price)}</b><em class="${i===0?'down':i===2?'up':''}">${i===0?'上沿':i===2?'下沿':'估算'}</em></div>`).join('')||'<p class="mt-empty">正在读取关键价位…</p>'}<details ${detailsOpen?'open':''}><summary>全部价位与口径</summary><label>区间日界<select data-terminal-zone aria-label="区间日界">${[['America/New_York','纽约日界'],['UTC','UTC 日界'],['Asia/Shanghai','北京时间日界']].map(([v,l])=>`<option value="${v}" ${snapshot.zone===v?'selected':''}>${l}</option>`).join('')}</select></label>${levels.map(l=>`<p>${esc(l.label)} <b>${price(l.price)}</b></p>`).join('')}<p>${esc(current?.profile_method||'成交量分布为估算值')}</p></details></section><p class="mt-evidence-note">条件观察指数，不代表胜率。${current?.error?'价位更新失败，仅显示已有缓存。':current?.stale?'价位缓存过期，仅供历史参考。':''}</p>`);
    const intel=$('.mt-intelligence');
    if(intel){const opened=intel.querySelector('details')?.open;setHTML(intel,window.SpikeMarketIntelligence.panels(intelligence,quotes.get(selected)?.last));if(opened)intel.querySelector('details').open=true;}
    if(side){
      const a=snapshot.alert||{},status=side.querySelector('.mt-watch-status'),opened=status.querySelector('details')?.open;
      setHTML(status,`<details ${opened?'open':''}><summary class="${a.kind==='event'?'up':a.kind==='unavailable'?'down':''}"><span class="mt-status-dot" aria-hidden="true"></span>${esc(a.title||'等待行情')}<small>${esc(snapshot.alertPeriod||'1H')}</small></summary><div><p>${esc(a.detail||'正在读取观察数据')}</p><p>数据更新 · ${clock(a.row?.close_ms)}</p><button data-watch-sound type="button" aria-pressed="${!!snapshot.soundEnabled}">${snapshot.soundEnabled?'关闭提示音':'开启提示音'}</button></div></details>`);
    }
  }
  async function loadChart(reset=true){
    if(!active||!view)return;const symbol=selected,tf=period,token=++chartToken;chartPending=reset||!chart;chartError='';if(reset){revision='';destroyChart();for(const selector of ['.mt-ohlc','.mt-ma-legend','.mt-volume-label','[data-chart-source]']){const el=$(selector);if(el)el.textContent='';}}lastChartAt=Date.now();feedback();
    try{const sourceTf=tf==='1D'?'4H':tf;const zone=snapshot.zone||'America/New_York';const data=await get('/api/chart?symbol='+encodeURIComponent(symbol)+'&timeframe='+sourceTf);if(!active||token!==chartToken)return;
      if(data.symbol!==symbol||(data.timeframe||data.bar)!==sourceTf)throw Error('图表来源与所选交易对不一致');
      if(data.loading&&!data.candles?.length){lastChartAt=Date.now()-55000;throw Error('行情缓存正在准备，稍后自动重试');}
      let rows=candles(data.candles);if(tf==='1D')rows=daily(rows);if(!rows.length)throw Error('暂无完整的 '+tf+' K 线');
      const closeMs=(rows.at(-1).time+PERIODS[tf]/1000)*1000;
      chartCache.set(symbol+'|'+tf,{rows,stale:!!(data.stale||data.state?.stale||Date.now()-closeMs>PERIODS[tf]+120000),error:data.error||data.state?.error});if(tf==='15m')sparks.set(symbol,rows);chartPending=false;await paintChart();paintList();
      loadSelectedContext(symbol);
    }catch(e){if(token===chartToken&&active){chartPending=false;chartError=e.name==='AbortError'?'图表读取超时':e.message;feedback();}}
  }
  async function loadSelectedContext(symbol){if(!active)return;const epoch=activation,zone=snapshot.zone||'America/New_York';try{const d=await get('/api/market-sync/context?symbol='+encodeURIComponent(symbol)+'&zone='+encodeURIComponent(zone));if(!active||epoch!==activation||d.symbol!==symbol||zone!==(snapshot.zone||'America/New_York')||d.timezone!==zone)return;contexts.set(symbol,d);if(selected===symbol){paintEvidence();paintChart();}}catch{}}
  function feedback(){const el=$('.mt-chart-feedback');if(!el)return;el.hidden=!chartPending&&!chartError;el.innerHTML=chartPending?'正在读取 '+esc(base(selected))+' '+period+' K 线…':esc(chartError)+' <button type="button" data-chart-retry>重试</button>';}
  function destroyChart(){if(chart&&chartData)chartViews.save(chartIdentity,chart.timeScale().getVisibleLogicalRange(),chartTools.times(chartData));chartControls?.destroy();chartControls=null;chartIdentity=null;hoverTime=null;crosshair.cancel();lastLegend='';chartData=null;revision='';maValues.clear();resize?.disconnect();resize=null;chart?.remove();chart=null;candleSeries=null;volumeSeries=null;maSeries=[];lines=[];}
  async function paintChart(){
    const data=chartCache.get(selected+'|'+period);if(!active||!view||!data||chartPending||chartError)return;const s=selected,p=period,token=chartToken;
    try{await library();if(!active||s!==selected||p!==period||token!==chartToken)return;
      const theme=getComputedStyle(view),color=theme.getPropertyValue('--chart-bg').trim(),text=theme.getPropertyValue('--chart-text').trim(),grid=theme.getPropertyValue('--chart-grid').trim(),rows=data.rows;
      const key=[s,p,color,text,grid,data.stale,data.error,JSON.stringify((contexts.get(s)||snapshot.context)?.levels||[])].join('|');const dataChanged=!chartData||chartData.length!==rows.length||rows.some((r,i)=>!perf.samePoint(r,chartData[i]));if(chart&&key===revision&&!dataChanged)return;
      const saved=chartData?chartTools.anchor(chart.timeScale().getVisibleLogicalRange(),chartTools.times(chartData)):null;
      if(!chart){chartIdentity=s+'|'+p;const host=$('.mt-chart');chart=window.LightweightCharts.createChart(host,{width:host.clientWidth,height:host.clientHeight,layout:{background:{color},textColor:text,fontFamily:'JetBrains Mono, monospace',fontSize:10},grid:{vertLines:{color:grid,style:1},horzLines:{color:grid,style:1}},rightPriceScale:{borderColor:grid,scaleMargins:{top:.12,bottom:.24}},timeScale:{borderColor:grid,timeVisible:true,secondsVisible:false,rightOffset:6,barSpacing:8,tickMarkFormatter:(t,type)=>tickFormats[type<3?0:1].format(new Date(t*1000))},localization:{locale:'zh-CN',priceFormatter:chartTools.axisPrice,timeFormatter:t=>clock(t*1000)},crosshair:{mode:0},handleScroll:{vertTouchDrag:false,horzTouchDrag:true}});
        candleSeries=chart.addCandlestickSeries({upColor:chartTools.colors.up,downColor:chartTools.colors.down,borderVisible:false,wickUpColor:chartTools.colors.up,wickDownColor:chartTools.colors.down});
        chart.applyOptions(chartTools.interaction);
        chartControls=chartTools.bind($('.mt-chart-panel'),{chart:()=>chart,rows:()=>chartData||[],canvas:host});
        volumeSeries=chart.addHistogramSeries({priceScaleId:'volume',priceFormat:{type:'volume'},lastValueVisible:false,priceLineVisible:false});volumeSeries.priceScale().applyOptions({scaleMargins:{top:.81,bottom:0}});
        maSeries=MA.map(m=>({...m,series:chart.addLineSeries({color:m.color,lineWidth:1,lastValueVisible:false,priceLineVisible:false,crosshairMarkerVisible:false})}));
        chart.subscribeCrosshairMove(e=>{const latest=chartData?.at(-1),bar=e.seriesData.get(candleSeries);hoverTime=bar?e.time:null;crosshair(bar||latest,bar?e.time:latest?.time,e.seriesData.get(volumeSeries)?.value);});
        resize=chartTools.resize(chart,host,{compactAxis:true});
      }
      chart.applyOptions({layout:{background:{color},textColor:text},grid:{vertLines:{color:grid,style:1},horzLines:{color:grid,style:1}}});
      maSeries.forEach(m=>m.series.applyOptions({visible:showMA}));volumeSeries.applyOptions({visible:showVolume});candleSeries.priceScale().applyOptions({scaleMargins:{top:.08,bottom:showVolume?.24:.08}});$('.mt-ma-legend').hidden=!showMA;$('.mt-volume-label').hidden=!showVolume;$('[data-chart-ma]').setAttribute('aria-pressed',String(showMA));$('[data-chart-volume]').setAttribute('aria-pressed',String(showVolume));
      const last=rows.at(-1),precision=Math.min(10,Math.max(2,Math.ceil(-Math.log10(Math.min(...rows.map(r=>r.close))))+4));candleSeries.applyOptions({priceFormat:{type:'price',precision,minMove:10**-precision}});maSeries.forEach(m=>m.series.applyOptions({priceFormat:{type:'price',precision,minMove:10**-precision}}));if(dataChanged)syncSeries(candleSeries,rows);
      if(dataChanged){
      syncSeries(volumeSeries,rows.map(r=>({time:r.time,value:r.volume,color:r.close>=r.open?chartTools.colors.volumeUp:chartTools.colors.volumeDown})));
      maValues=new Map(rows.map(r=>[r.time,{...r}]));
      for(const m of maSeries){const values=p==='1D'?(m.key.startsWith('sma')?average:exponential)(rows,m.period,PERIODS[p]/1000):rows.filter(r=>finite(r[m.key])).map(r=>({time:r.time,value:r[m.key]}));syncSeries(m.series,values);for(const v of values)maValues.get(v.time)[m.key]=v.value;}
      chartData=rows;
      }
      lines.forEach(l=>candleSeries.removePriceLine(l));lines=[];const context=contexts.get(s)||(s===full('BTC')?snapshot.context:null);
      for(const l of (context?.levels||[]).filter(x=>['周一高','周一低','昨日 POC'].includes(x.label)))lines.push(candleSeries.createPriceLine({price:l.price,color:text,lineWidth:1,lineStyle:2,lineVisible:showLevels,axisLabelVisible:false,title:l.label}));
      $('.mt-volume-label').textContent='成交量　'+Number(last.volume).toLocaleString('en-US',{notation:'compact',maximumFractionDigits:2});
      const retained=saved?chartTools.restore(saved,chartTools.times(rows)):chartViews.get(chartIdentity,chartTools.times(rows));
      if(retained)chart.timeScale().setVisibleLogicalRange(retained);else if(!revision)chart.timeScale().setVisibleLogicalRange({from:Math.max(0,rows.length-(($('.mt-chart')?.clientWidth||800)<500?60:100)),to:rows.length+5});
      revision=key;chartControls?.update();const inspected=hoverTime&&rows.find(r=>r.time===hoverTime);paintLegend(inspected||last,inspected?.time||last.time);
      $('[data-chart-source]').textContent=`${s.replace('-USDT-SWAP','USDT')} · ${p} · ${data.error?'更新失败，缓存收盘':data.stale?'缓存过期，仅供历史参考':'已收盘'} ${clock((last.time+PERIODS[p]/1000)*1000)}${p==='1D'?' · 完整 UTC 日聚合 · 长周期均线历史不足':''}`;
      feedback();
    }catch(e){chartError=e.message;feedback();}
  }
  function paintLegend(row,time){if(!row||!view)return;const key=[selected,period,hoverTime===time,time,row.open,row.high,row.low,row.close,...MA.map(m=>maValues.get(time)?.[m.key])].join('|');if(key===lastLegend)return;lastLegend=key;$('.mt-ohlc').innerHTML=`<span class="mt-chart-name">${esc(base(selected))}USDT <b>${period}</b> · ${hoverTime===time?'光标':'最新 K 线'} ${clock(time*1000)} 北京开盘</span><span>开 <b>${chartTools.axisPrice(row.open)}</b></span><span>高 <b>${chartTools.axisPrice(row.high)}</b></span><span>低 <b>${chartTools.axisPrice(row.low)}</b></span><span>收 <b class="${tone(row.close-row.open)}">${chartTools.axisPrice(row.close)}</b></span>`;$('.mt-ma-legend').innerHTML=chartStyle.legend(maValues.get(time)||{});}
  async function loadIntelligence(){
    if(!active||!view||document.hidden||intelligenceBusy)return;
    intelligenceBusy=true;const token=++intelligenceToken,symbol=selected,epoch=activation;
    try{const [data,ticker]=await Promise.all([get('/api/market-workstation?symbol='+encodeURIComponent(symbol)),get('/api/research/paper-desk/market?symbol='+encodeURIComponent(symbol)).catch(()=>null)]);if(ticker?.symbol===symbol)data.quote=ticker;if(!active||epoch!==activation||token!==intelligenceToken||symbol!==selected||data.symbol!==symbol)return;intelligence=data;paintQuote();paintEvidence();}
    catch{if(active&&epoch===activation&&token===intelligenceToken){intelligence={fields:Object.fromEntries(['funding','interest','mark','index','trades','liquidations'].map(k=>[k,{error:'unavailable',stale:true}]))};paintQuote();paintEvidence();}}
    finally{if(token===intelligenceToken)intelligenceBusy=false;}
  }
  async function loadQuotes(){
    if(!active||document.hidden||quoteBusy)return;quoteBusy=true;const epoch=activation;
    try{const data=await get('/api/research/paper-desk/symbols');if(!active||epoch!==activation)return;catalog=data.symbols||[];catalogError='';for(const row of catalog){if(row.last!=null&&row.expires_ms)quotes.set(row.symbol,row);}paintQuote();paintList();}
    catch{if(active&&epoch===activation){catalogError='行情更新失败，保留上次快照';paintList();}}
    finally{if(epoch===activation)quoteBusy=false;}
  }
  async function warm(){
    const epoch=activation;await loadQuotes();if(!active||epoch!==activation)return;
    await Promise.allSettled(DEFAULTS.map(async coin=>{const symbol=full(coin);if(sparks.has(symbol))return;const d=await get('/api/chart?symbol='+encodeURIComponent(symbol)+'&timeframe=15m');if(active&&epoch===activation&&d.symbol===symbol)sparks.set(symbol,candles(d.candles));}));if(active)paintList();
  }
  function select(symbol){if(!/^[A-Z0-9]{1,24}-USDT-SWAP$/.test(symbol))return;const fromDrawer=document.body.classList.contains('nav-open');window.SpikeWorkspaceDisplay?.setMobileNavigation(false,{restoreFocus:false});if(fromDrawer)$('[data-pair]')?.focus({preventScroll:true});if(symbol===selected)return;selected=symbol;intelligence=null;intelligenceBusy=false;intelligenceToken++;paintQuote();paintList();paintEvidence();loadChart();loadIntelligence();loadQuotes();}
  function update(value){const zoneChanged=snapshot.zone&&snapshot.zone!==value.zone;snapshot=value;if(zoneChanged){contexts.clear();loadSelectedContext(selected);}if(value.context)contexts.set(full('BTC'),value.context);create();paintQuote();paintEvidence();paintNotes();}
  function paintNotes(){const mount=document.getElementById('ms-terminal-notes');if(!mount||mount.querySelector('textarea'))return;mount.innerHTML=`<label for="market-observation-note">观察笔记 <small>保存在当前浏览器</small></label><textarea id="market-observation-note" rows="5" placeholder="记录观察、关键价位与需要等待的条件…"></textarea><div><button type="button" data-save-market-note>保存笔记</button><span role="status" data-note-status></span></div>`;mount.querySelector('textarea').value=notes.text||'';mount.querySelector('textarea').addEventListener('input',e=>{notes.text=e.target.value;mount.querySelector('[data-note-status]').textContent='尚未保存';});mount.querySelector('button').addEventListener('click',()=>{notes={text:mount.querySelector('textarea').value};mount.querySelector('[data-note-status]').textContent=save('spike.market.notes.v1',notes)?'已保存到当前浏览器':'保存失败，请先复制笔记';});}
  function setActive(value){
    if(active!==value)activation++;active=value;idle.setActive(value);
    if(value){if(!timer){warm();timer=setInterval(()=>{loadQuotes();loadIntelligence();if(active&&!document.hidden&&!chartPending&&Date.now()-lastChartAt>60000)loadChart(false);},5000);}}
    else{if($('.mt-symbol-dialog')?.open)$('.mt-symbol-dialog').close();setChartExpanded(false);chartToken++;intelligenceToken++;intelligenceBusy=false;requests.cancel();quoteBusy=false;clearInterval(timer);timer=null;listResize?.disconnect();listResize=null;if(listFrame!==null)cancelAnimationFrame(listFrame);listFrame=null;themeObserver?.disconnect();destroyChart();view=null;}
  }

  window.SpikeMarketTerminal={update,setActive};
})();
