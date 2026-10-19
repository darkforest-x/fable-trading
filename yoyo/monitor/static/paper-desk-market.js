/* OKX price display + official TradingView Advanced Chart embed.
 * https://www.tradingview.com/widget-docs/widgets/charts/advanced-chart/
 * Keep the chart DOM connected across ledger polling and form updates: moving
 * an iframe reloads it and loses the user's zoom, interval and studies.
 */
(() => {
  'use strict';
  const esc=x=>String(x??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const validSymbol=s=>/^[A-Z0-9]{1,24}-USDT-SWAP$/.test(s||'');
  const tvSymbol=s=>validSymbol(s)?'OKX:'+s.replace('-USDT-SWAP','USDT.P'):null;
  const px=x=>x==null?'—':Number(x).toLocaleString('zh-CN',{useGrouping:false,maximumFractionDigits:Number(x)>=100?2:Number(x)>=1?4:8});
  const compact=x=>x==null?'—':Number(x).toLocaleString('zh-CN',{useGrouping:false,notation:'compact',maximumFractionDigits:2});
  const layoutKey='spike-paper-panel-layout-v2';
  const layouts=[['chart-top','图表在上 · 持仓在右'],['ledger-left','图表在上 · 持仓在左'],['chart-left','图表在左 · 持仓在下'],['chart-right','图表在右 · 持仓在下']];
  const validLayout=value=>layouts.some(([key])=>key===value)?value:'chart-left';
  function readLayout(){try{return validLayout(localStorage.getItem(layoutKey));}catch{return 'chart-left';}}
  function applyLayout(root,value){
    const layout=validLayout(value);root.dataset.panelLayout=layout;
    try{localStorage.setItem(layoutKey,layout);}catch{/* Layout still works when storage is unavailable. */}
    return layout;
  }
  let closeFocus=null;
  let host=null,symbol='',interval='15',chartKey='',timer=null,controller=null,generation=0,quote=null,quoteError='',themeObserver=null;
  const theme=()=>document.documentElement.dataset.theme==='dark'?'dark':'light';
  const config=(s,i,t)=>({autosize:true,symbol:tvSymbol(s),interval:i,timezone:'Asia/Shanghai',theme:t,style:'1',locale:'zh_CN',
    allow_symbol_change:false,hide_side_toolbar:true,hide_top_toolbar:false,hide_legend:false,hide_volume:false,calendar:false,
    details:false,hotlist:false,save_image:false,withdateranges:false,watchlist:[],compareSymbols:[],studies:[],
    backgroundColor:t==='dark'?'#16171b':'#ffffff',gridColor:t==='dark'?'rgba(180,196,207,0.07)':'rgba(46,46,46,0.045)'});
  function ticker(q,error='') {
    const fresh=q&&q.expires_ms>Date.now(),change=q?.change_24h_percent==null?null:Number(q.change_24h_percent);
    const tone=change==null?'':change<0?'desk-short':'desk-long';
    return `<div class="desk-market-last"><span>最新成交价 <small>USDT</small></span><strong class="${fresh?tone:'desk-muted'}">${px(q?.last)}</strong><div><b class="${tone}">${change==null?'—':(change>0?'+':'')+change.toFixed(2)+'%'}</b><small>24h</small><small class="desk-market-status ${fresh?'':'is-stale'}">${fresh?'OKX · '+new Date(q.source_ms).toLocaleTimeString('zh-CN',{hour12:false}):q?'行情过期':error?'行情暂不可用':'连接行情中…'}</small></div></div>
    <dl class="desk-market-stats"><div><dt>24h 最高</dt><dd>${px(q?.high_24h)}</dd></div><div><dt>24h 最低</dt><dd>${px(q?.low_24h)}</dd></div><div><dt>24h 成交额（约）</dt><dd>${compact(q?.volume_usdt_24h)} USDT</dd></div></dl>`;
  }
  function updateTicker(){const node=host?.querySelector('[data-market-ticker]');if(node)node.innerHTML=ticker(quote,quoteError);}
  async function refreshPrice(force=false){
    if(!host||!symbol||document.hidden||controller&&!force)return;
    if(force)controller?.abort();
    const request=new AbortController(),token=generation,requested=symbol;controller=request;
    const timeout=setTimeout(()=>request.abort(),12000);
    try{
      const response=await fetch('/api/research/paper-desk/market?symbol='+encodeURIComponent(requested),{cache:'no-store',signal:request.signal});
      const data=await response.json();if(!response.ok)throw new Error(data.detail||'行情暂不可用');
      if(data.symbol!==requested||!Number.isFinite(Number(data.last))||Number(data.last)<=0||data.expires_ms<=Date.now())throw new Error('行情过期');
      if(token!==generation||requested!==symbol||controller!==request)return;
      quote=data;quoteError='';updateTicker();
    }catch(error){if(token===generation&&controller===request){quoteError=error.message;updateTicker();}}
    finally{clearTimeout(timeout);if(controller===request)controller=null;}
  }
  function drawChart(force=false){
    if(!host||!validSymbol(symbol)||chartTab!=='tradingview')return;
    const key=symbol+':'+interval+':'+theme();if(!force&&key===chartKey)return;
    chartKey=key;
    const mount=host.querySelector('[data-market-widget]');
    mount.innerHTML='<div class="tradingview-widget-container"><div class="tradingview-widget-container__widget"></div></div>';
    const container=mount.firstElementChild;
    const script=document.createElement('script');script.type='text/javascript';script.async=true;
    script.src='https://s3.tradingview.com/external-embedding/embed-widget-advanced-chart.js';
    script.textContent=JSON.stringify(config(symbol,interval,theme()));
    script.onerror=()=>{if(key===chartKey&&container.isConnected)mount.innerHTML='<div class="desk-chart-unavailable">TradingView 加载失败，请检查网络后重试。<button type="button" data-market-retry>重新加载图表</button></div>';};
    container.appendChild(script);
    const url='https://www.tradingview.com/chart/?symbol='+encodeURIComponent(tvSymbol(symbol))+'&interval='+encodeURIComponent(interval);
    host.querySelector('[data-market-external]').href=url;
  }
  const chartTabKey='spike-paper-chart-tab-v1';
  let chartTab='tradingview',rangeData=null,rangeError='',rangeController=null,rangeNext=0;
  const rangeView={zone:'America/New_York',bars:192,range:true,fib:false,profile:true,npoc:false,prior:false};
  try{if(localStorage.getItem(chartTabKey)==='range')chartTab='range';}catch(_){}
  function drawRange(){
    const mount=host?.querySelector('[data-range-content]');if(!mount)return;
    const c=rangeData,coin=(symbol||'').replace('-USDT-SWAP','');
    const heading=host.querySelector('[data-range-title]');heading.textContent=coin+' 区间与关键价位 · 15min';
    window.SpikeRangeChart.mount(mount,c,rangeView,rangeError);
    host.querySelectorAll('[data-range-layer]').forEach(b=>b.setAttribute('aria-pressed',String(rangeView[b.dataset.rangeLayer])));
    host.querySelector('[data-range-bars]').textContent=rangeView.bars===192?'近 2 天':'近 7 天';
  }
  async function refreshRange(force=false){
    if(!host||!validSymbol(symbol)||chartTab!=='range'||document.hidden||rangeController&&!force||!force&&Date.now()<rangeNext)return;
    if(force)rangeController?.abort();
    const request=new AbortController(),token=generation,requested=symbol,zone=rangeView.zone;
    rangeController=request;const timeout=setTimeout(()=>request.abort(),12000);
    try{
      const response=await fetch('/api/market-sync/context?symbol='+encodeURIComponent(requested)+'&zone='+encodeURIComponent(zone),{cache:'no-store',signal:request.signal});
      const data=await response.json();if(!response.ok)throw new Error(data.detail||'区间图读取失败');
      if(data.symbol!==requested||data.timezone!==zone)throw new Error('图表币种或日界不匹配');
      if(token!==generation||requested!==symbol||zone!==rangeView.zone||rangeController!==request)return;
      rangeData=data;rangeError='';rangeNext=Date.now()+(data.loading?3000:60000);drawRange();
    }catch(error){if(token===generation&&rangeController===request){rangeError=error.name==='AbortError'?'读取超时':error.message;rangeNext=Date.now()+10000;drawRange();}}
    finally{clearTimeout(timeout);if(rangeController===request)rangeController=null;}
  }
  function selectChartTab(value){
    chartTab=value==='range'?'range':'tradingview';
    try{localStorage.setItem(chartTabKey,chartTab);}catch(_){}
    for(const button of host.querySelectorAll('[data-chart-tab]')){
      const active=button.dataset.chartTab===chartTab;button.setAttribute('aria-selected',String(active));button.tabIndex=active?0:-1;
    }
    // Hide stable panels in place; never detach the TradingView iframe on tab changes.
    host.querySelector('[data-market-widget]').hidden=chartTab!=='tradingview';
    host.querySelector('[data-market-help]').hidden=chartTab!=='tradingview';
    host.querySelector('[data-market-range]').hidden=chartTab!=='range';
    const credit=host.querySelector('[data-chart-credit]');
    if(credit)credit.innerHTML=chartTab==='range'?'区间图 · OKX 已收盘 15min · 每分钟检查':'图表由 <a href="https://www.tradingview.com/" target="_blank" rel="noopener noreferrer">TradingView</a> 提供 · OKX 永续';
    if(chartTab==='range'){drawRange();refreshRange();}else drawChart();
  }
  function build(root){
    root.dataset.panelLayout=readLayout();
    root.innerHTML='<div data-desk-toolbar-slot></div><div data-desk-notices></div><div class="desk-market-layout"><section class="desk-market desk-card" aria-label="行情与TradingView图表" data-desk-market></section><div data-desk-ticket-slot></div><div data-desk-ledger-slot></div></div><div data-desk-body></div>';
    host=root.querySelector('[data-desk-market]');
    host.innerHTML=`<div class="desk-market-header"><div data-market-tools></div><div class="desk-market-overview"><div data-market-selector></div><div class="desk-funds"><button type="button" data-market-funds-toggle aria-expanded="false" aria-controls="desk-market-funds">模拟资金 ⌄</button><div id="desk-market-funds" data-market-balances></div></div><div data-market-ticker></div></div></div><section class="desk-chart-focus" data-chart-focus><div class="desk-chart-controls"><div class="desk-chart-tabs" role="tablist" aria-label="图表类型"><button type="button" id="desk-chart-tv-tab" role="tab" data-chart-tab="tradingview" aria-controls="desk-chart-tv-panel" aria-selected="true">TradingView</button><button type="button" id="desk-chart-range-tab" role="tab" data-chart-tab="range" aria-controls="desk-chart-range-panel" aria-selected="false" tabindex="-1">区间图表</button></div><div><a data-market-external target="_blank" rel="noopener noreferrer" aria-label="在 TradingView 打开所选币种">独立打开 ↗</a><button type="button" data-market-expand aria-pressed="false">放大</button><button type="button" data-market-collapse aria-expanded="true">收起图表</button></div></div><div class="desk-chart-help" data-market-help><span>图表由 TradingView 加载；若未显示，可重新加载或切换区间图。</span><button type="button" data-market-retry>重新加载图表</button></div><div id="desk-chart-tv-panel" role="tabpanel" aria-labelledby="desk-chart-tv-tab" data-market-widget class="desk-market-widget"></div><div id="desk-chart-range-panel" role="tabpanel" aria-labelledby="desk-chart-range-tab" data-market-range class="desk-range-panel" hidden><div class="desk-range-heading"><strong data-range-title></strong></div><details class="desk-range-settings quiet-menu"><summary>图层 ⌄</summary><div class="quiet-menu-panel"><label>区间日界 <select data-range-zone aria-label="区间图日界"><option value="America/New_York">纽约日界</option><option value="UTC">UTC 日界</option><option value="Asia/Shanghai">北京时间日界</option></select></label><div class="desk-range-controls">${[['range','周一区间'],['fib','双向 Fib'],['profile','昨日 POC / VA'],['npoc','nPOC'],['prior','上周高低']].map(([key,label])=>`<button type="button" data-range-layer="${key}" aria-pressed="${rangeView[key]}">${label}</button>`).join('')}<button type="button" data-range-bars>近 2 天</button></div></div></details><div data-range-content></div><p class="desk-range-note">POC / VAH / VAL 以 15min K 线成交量估算；nPOC 检查近 14 个完整日。与市场共振观察使用同一计算口径。</p></div><div class="desk-chart-credit"><span data-chart-credit>图表由 <a href="https://www.tradingview.com/" target="_blank" rel="noopener noreferrer">TradingView</a> 提供 · OKX 永续</span><span>成交按 OKX 买卖价</span></div></section>`;
    host.addEventListener('click',event=>{
      const button=event.target.closest('button');if(!button)return;
      if(button.hasAttribute('data-market-funds-toggle')){const open=button.getAttribute('aria-expanded')!=='true';button.setAttribute('aria-expanded',String(open));button.closest('.desk-funds').classList.toggle('is-expanded',open);}
      if(button.dataset.chartTab)selectChartTab(button.dataset.chartTab);
      if(button.dataset.rangeLayer){rangeView[button.dataset.rangeLayer]=!rangeView[button.dataset.rangeLayer];drawRange();}
      if(button.hasAttribute('data-range-bars')){rangeView.bars=rangeView.bars===192?672:192;drawRange();}
      if(button.hasAttribute('data-market-retry'))drawChart(true);
      if(button.hasAttribute('data-market-expand')){const panel=host.querySelector('[data-chart-focus]');if(panel.classList.contains('cv-expanded')){closeFocus?.();closeFocus=null;}else closeFocus=window.SpikeChartTools.fullscreen(panel,button);}
      if(button.hasAttribute('data-market-collapse')){
        const collapsed=host.classList.toggle('is-chart-collapsed');button.setAttribute('aria-expanded',String(!collapsed));button.textContent=collapsed?'展开图表':'收起图表';
      }
    });
    host.addEventListener('change',event=>{
      if(event.target.hasAttribute('data-range-zone')){rangeView.zone=event.target.value;rangeData=null;rangeError='';rangeNext=0;drawRange();refreshRange(true);}
    });
    host.addEventListener('keydown',event=>{
      if(!event.target.dataset.chartTab||!['ArrowLeft','ArrowRight','Home','End'].includes(event.key))return;
      event.preventDefault();selectChartTab(event.key==='Home'?'tradingview':event.key==='End'?'range':chartTab==='range'?'tradingview':'range');
      host.querySelector(`[data-chart-tab="${chartTab}"]`).focus();
    });
    host.querySelector('[data-range-zone]').value=rangeView.zone;
    selectChartTab(chartTab);
    themeObserver=new MutationObserver(()=>drawChart());themeObserver.observe(document.documentElement,{attributes:true,attributeFilter:['data-theme']});
    timer=setInterval(()=>{updateTicker();refreshPrice();refreshRange();},3000);
  }
  function render(root,html,options){
    if(!host||!root.querySelector('[data-desk-body]')){stop();build(root);}
    const body=root.querySelector('[data-desk-body]');body.innerHTML=html;
    const toolbar=body.querySelector('.desk-toolbar'),fallback=root.querySelector('[data-desk-toolbar-slot]');
    fallback.replaceChildren();
    (options.symbol?host.querySelector('[data-market-tools]'):fallback).replaceChildren(toolbar);
    const picker=body.querySelector('[data-desk-picker]');
    const selector=host.querySelector('[data-market-selector]');
    if(picker){picker.querySelector('input[name="symbol"]').setAttribute('form','desk-trade-form');selector.replaceChildren(picker);}
    else selector.innerHTML=options.selector;
    const ticket=body.querySelector('.desk-ticket'),slot=root.querySelector('[data-desk-ticket-slot]');
    slot.replaceChildren(...(ticket?[ticket]:[]));slot.hidden=!ticket;
    const ledger=body.querySelector('.desk-ledger'),ledgerSlot=root.querySelector('[data-desk-ledger-slot]');
    const balances=body.querySelector('.desk-metrics'),risk=body.querySelector('.desk-risk-strip');
    host.querySelector('[data-market-balances]').replaceChildren(...(balances?[balances]:[]));
    ledgerSlot.replaceChildren(...(ledger?[ledger,risk].filter(Boolean):[]));ledgerSlot.hidden=!ledger;
    // CSS grid reorders these stable siblings without reconnecting the chart iframe.
    body.querySelector('.desk-layout')?.remove();
    const notices=[...body.children].filter(node=>node.matches('.research-error,.desk-success,.desk-account-create'));
    root.querySelector('[data-desk-notices]').replaceChildren(...notices);
    host.hidden=!options.symbol;
    if((options.symbol||'')!==symbol){symbol=options.symbol||'';generation++;rangeController?.abort();rangeController=null;rangeData=null;rangeError='';rangeNext=0;drawRange();refreshRange(true);quote=null;quoteError='';controller?.abort();controller=null;updateTicker();refreshPrice(true);}
    drawChart();
  }
  function stop(){
    closeFocus?.();closeFocus=null;
    generation++;controller?.abort();controller=null;rangeController?.abort();rangeController=null;rangeData=null;rangeError='';rangeNext=0;clearInterval(timer);timer=null;themeObserver?.disconnect();themeObserver=null;
    window.SpikeRangeChart.unmount(host?.querySelector('[data-range-content]'));host?.querySelector('[data-market-widget]')?.replaceChildren();host=null;symbol='';quote=null;chartKey='';
  }
  window.SpikePaperMarket={render,stop};
  if(typeof module!=='undefined')module.exports={config,tvSymbol,ticker,readLayout,applyLayout};
})();
