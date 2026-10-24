/* Result-first YOLO workbench. Browsing and new scan drafts have separate state.
 * Frozen model inference, observation books, and trading gates are unchanged.
 * Read-only patrol cleanup: Stitch screen d43ec70ea6664a098900cff4ce2af956.
 */
(() => {
  'use strict';
  const steps={'5m':300000,'15m':900000,'30m':1800000,'1H':3600000,'4H':14400000,'1Dutc':86400000};
  const short=s=>(s||'').replace('-USDT-SWAP','');
  const exactSymbol=value=>/^[A-Z0-9]{1,32}-USDT-SWAP$/.test(String(value||''));
  // A patrol link owns its filters; stale UI state must not hide another coin.
  function patrolFilters(p){
    return {feedQuery:exactSymbol(p.symbol)?p.symbol:String(p.q||'').slice(0,80),
      feedDays:[0,1,7,30].includes(Number(p.days))?Number(p.days):7,
      feedPeriod:steps[p.timeframe]?p.timeframe:'',feedSide:['long','short'].includes(p.side)?p.side:'',
      feedOrder:['newest','score','symbol'].includes(p.sort)?p.sort:'newest'};
  }
  function filteredSymbols(rows,query='',filter='detected',favorites=[],order='count') {
    return rows.filter(r=>r.symbol.toLowerCase().includes(query.trim().toLowerCase()) && (filter==='all'||(filter==='favorites'?favorites.includes(r.symbol):filter==='detected'?r.count>0:!!r.error)))
      .sort((a,b)=>{
        const tie=a.symbol.localeCompare(b.symbol),rank=r=>Number.isFinite(r)?r:-1;
        if(order==='name')return tie;if(order==='name-desc')return -tie;
        if(order==='latest')return rank(b.latest_detection_ms)-rank(a.latest_detection_ms)||tie;
        if(order==='score')return rank(b.max_confidence)-rank(a.max_confidence)||tie;
        if(order==='favorites')return Number(favorites.includes(b.symbol))-Number(favorites.includes(a.symbol))||rank(b.latest_detection_ms)-rank(a.latest_detection_ms)||tie;
        return (b.count||0)-(a.count||0)||tie;
      });
  }
  function aggregateSymbols(items){
    const groups=new Map();
    for(const r of items){
      if(!groups.has(r.symbol))groups.set(r.symbol,{symbol:r.symbol,count:0,raw_count:0,latest_detection_ms:0,max_confidence:0,periods:[]});
      const g=groups.get(r.symbol);g.periods.push(r);g.count+=r.count||0;g.raw_count+=r.raw_count||0;
      g.latest_detection_ms=Math.max(g.latest_detection_ms,r.latest_detection_ms||0);g.max_confidence=Math.max(g.max_confidence,r.max_confidence||0);
    }
    for(const g of groups.values()){g.periods.sort((a,b)=>steps[a.timeframe]-steps[b.timeframe]);g.error=g.periods.every(r=>r.error)?g.periods[0].error:null;}
    return [...groups.values()];
  }
  function periodResult(items,symbol,timeframe){return items.find(r=>r.symbol===symbol&&r.timeframe===timeframe);}
  function initialSymbol(rows,preferred) {
    return (rows.find(r=>r.symbol===preferred)||rows.find(r=>!r.error&&r.count>0)||rows.find(r=>!r.error)||rows[0])?.symbol||'';
  }
  function normalizedSymbol(value) {
    const symbol=String(value||'').trim().toUpperCase().replace(/:USDT$/,'').replace(/-SWAP$/,'').replace(/[-/]?USDT$/,'');
    return symbol?symbol+'-USDT-SWAP':'';
  }
  function draftSpec(draft) {
    const symbol=normalizedSymbol(draft.symbol);
    return {model_id:draft.model,timeframe:draft.timeframe,mode:draft.mode,scope:draft.scope,
      symbols:draft.scope==='all'?[]:[symbol],...(draft.mode==='history'?{start_ms:Date.parse(draft.start+'+08:00'),end_ms:Date.parse(draft.end+'+08:00')}:{})};
  }
  function sameScan(a,b) {
    return a.mode===b.mode && a.timeframe===b.timeframe && a.model_id===b.model_id && a.scope===b.scope &&
      (a.scope==='all'||JSON.stringify(a.symbols)===JSON.stringify(b.symbols)) &&
      (a.mode==='latest'||(a.start_ms===b.start_ms&&a.end_ms===b.end_ms));
  }
  function draftProblem(draft,symbols=null,now=Date.now()) {
    if(!steps[draft.timeframe])return {field:'timeframe',message:'请选择 K 线周期。'};
    const spec=draftSpec(draft),symbol=spec.symbols[0];
    if(draft.scope!=='all'&&(!/^[A-Z0-9]{1,24}-USDT-SWAP$/.test(symbol)||symbols&&!symbols.includes(symbol)))return {field:'symbol',message:'请从币种列表选择，例如 ETH、BTC 或 SOL。这里只检测 OKX 的加密 USDT 永续。'};
    if(draft.mode==='history'){
      if(!Number.isFinite(spec.start_ms)||!Number.isFinite(spec.end_ms)||spec.start_ms<=0||spec.start_ms>=spec.end_ms)return {field:'start',message:'结束时间要晚于开始时间，请检查这段历史范围。'};
      if(spec.end_ms>Math.floor(now/steps[draft.timeframe])*steps[draft.timeframe])return {field:'end',message:'结束时间包含尚未收盘的 K 线，可点击“对齐收盘时间”修正。'};
      if(spec.start_ms%steps[draft.timeframe]||spec.end_ms%steps[draft.timeframe])return {field:'start',message:'时间与所选 K 线周期不对齐，可点击“对齐收盘时间”修正。'};
      if((spec.end_ms-spec.start_ms)/steps[draft.timeframe]>10000)return {field:'start',message:'这段历史超过 10,000 根 K 线，请缩短时间范围或分段检测。'};
    }
    return null;
  }
  function chartRows(result,tail){return result?result.candles.concat(tail?.symbol===result.symbol&&tail?.timeframe===result.timeframe&&tail?.detection_cutoff_ms===result.candles.at(-1).t+steps[result.timeframe]?tail.candles:[]):[];}
  function shapeRange(box,first,step){const to=(box.window_end_ms-first)/step+8,from=(box.window_start_ms-first)/step-14;return {from:Math.max(0,Math.min(from,to-64)),to};}
  function patrolProgress(s){const j=s.last_run;if(!j)return '等待首轮检测';const p=j.progress||{},done=p.completed||0;return `${j.status==='running'||j.status==='queued'?'本轮进行中':'最近一轮'} · 已处理 ${done}/${p.total||'—'} 币${p.unavailable?' · '+p.unavailable+' 币历史不足':''}${p.failed?' · '+p.failed+' 币待重试':''}${p.recovered?' · '+p.recovered+' 币已补齐':''}${p.remaining_endpoints?' · '+p.remaining_endpoints+' 个收盘位置待补扫':''}${p.elapsed_seconds?' · '+(p.elapsed_seconds/60).toFixed(1)+' 分钟':''}${s.enabled&&j.cutoff_ms&&Date.now()-j.cutoff_ms>s.interval_minutes*60000?' · 有新收盘待追赶，自动补扫':''}`;}
  function validView(value){return value&&Number.isFinite(value.from)&&Number.isFinite(value.to)&&value.to>value.from&&value.to-value.from<100000?{from:value.from,to:value.to}:null;}
  if(typeof module!=='undefined')module.exports={aggregateSymbols,periodResult,filteredSymbols,initialSymbol,draftSpec,sameScan,normalizedSymbol,draftProblem,validView,chartRows,patrolProgress,shapeRange,patrolFilters};
  const root=typeof document!=='undefined'&&document.getElementById('yolo-detect-workspace');if(!root)return;
  const $=id=>root.querySelector('#'+id), BASE='/api/yolo-detect';
  const terminal=new Set(['completed','partial','failed','cancelled','interrupted']);
  const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const dateFormats=[false,true].map(full=>new Intl.DateTimeFormat('zh-CN',{timeZone:'Asia/Shanghai',...(full?{year:'numeric'}:{}),month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false}));
  const fmt=(ms,full=false)=>ms?dateFormats[Number(full)].format(new Date(ms)):'—';
  const names={queued:'排队中',running:'检测中',completed:'已完成',partial:'部分可用',failed:'失败',cancelled:'已取消',interrupted:'已中断'};
  const tickFormats=[{month:'2-digit',day:'2-digit'},{hour:'2-digit',minute:'2-digit',hour12:false}].map(options=>new Intl.DateTimeFormat('zh-CN',{timeZone:'Asia/Shanghai',...options}));
  const periods={'5m':'5 分钟','15m':'15 分钟','30m':'30 分钟','1H':'1 小时','4H':'4 小时','1Dutc':'日线 · UTC'};
  const chartStyle=window.SpikeChartStyle,chartTools=window.SpikeChartTools;
  let chartControls=null,hoverTime=null;
  const colors=Object.fromEntries(chartStyle.lines.map(line=>[line.key,line.color]));
  const state={overview:true,latest:[],latestRevision:"",timeframe:"",active:false,loaded:false,catalog:null,jobs:[],schedules:[],job:null,result:null,symbol:'',selected:null,
    tab:'schedules',boxes:'structure',side:'all',symbolSearch:'',resultFilter:'detected',page:0,jobOffset:0,jobTotal:0,ma:true,volume:true,
    tail:null,viewTouched:false,follow:true,tailLoading:false,tailChecked:0,tailError:'',symbolOrder:'latest',feedOrder:'newest',
    feed:null,feedQuery:'',feedDays:7,feedPeriod:'',feedSide:'',feedLimit:12,feedPage:0,feedBefore:null,fromPatrol:false,focusBox:null,
    pendingRoute:null,attempted:false,loadingJob:false,loadingResult:false,draft:null,scheduleDraft:null,symbols:null};
  let jobsRevision='',jobsOffset=-1,schedulesAt=0,jobsAt=0,activationVersion=0;
  let timer=null,refreshing=false,requestVersion=0,chart=null,series=null,primitive=null,resize=null,chartPromise=null,maSeries=[],volume=null;
  let submitKey=null,submitting=false,scheduleSubmitting=false,chartNeedsTheme=false,symbolsPromise=null;
  let feedVersion=0,feedTimer=null,tailVersion=0,dialogOrigin=null;
  const validating=new Set();
  const resultCache=new Map();
  let findingPreview=null,previewVersion=0,previewRequest=null,previewChartInstance=null,previewResize=null;
  const readSaved=(key,fallback,session=false)=>{try{return JSON.parse((session?sessionStorage:localStorage).getItem(key))??fallback;}catch{return fallback;}};
  const save=(key,value,session=false)=>{try{(session?sessionStorage:localStorage).setItem(key,JSON.stringify(value));}catch{/* Storage may be unavailable; the current session remains usable. */}};
  state.symbolOrder=readSaved('spike.yolo.symbol-sort.v1','latest');if(!['latest','score','count','name','name-desc','favorites'].includes(state.symbolOrder))state.symbolOrder='latest';
  const savedFavorites=readSaved('spike.yolo.favorites.v1',[]);
  let favorites=Array.isArray(savedFavorites)?savedFavorites.filter(s=>typeof s==='string'&&/^[A-Z0-9]{1,24}-USDT-SWAP$/.test(s)).slice(0,300):[];
  const savedViews=readSaved('spike.yolo.views.v1',[],true);
  const chartViews=new Map(Array.isArray(savedViews)?savedViews.filter(r=>Array.isArray(r)&&typeof r[0]==='string'&&validView(r[1]?.range)).slice(-40):[]);
  const perf=window.SpikeRuntime,syncSeries=perf.createSeriesSync();
  let chartLookup=new Map(),displayRows=[],lastReadout='',lastMarkers='',symbolRenderKey='',aggregateSource=null,aggregateRows=[];
  const crosshair=perf.frameTask(time=>{if(chart&&state.active&&state.tab==='results')paintChartReadout(chartLookup.get(time)||displayRows.at(-1));});
  let chartIdentity=null,chartSelection=null,viewTimer=null;
  function rememberChart(){
    if(!chart||!chartIdentity)return;const range=validView(chart.timeScale().getVisibleLogicalRange());if(!range)return;
    chartViews.delete(chartIdentity);chartViews.set(chartIdentity,{range,anchor:chartTools.anchor(range,chartTools.times(displayRows)),selected:chartSelection});if(chartViews.size>40)chartViews.delete(chartViews.keys().next().value);
    save('spike.yolo.views.v1',[...chartViews],true);
  }
  function saveDraft(){if(state.draft)save('spike.yolo.scan-draft.v2',state.draft);}
  const symbolPanelMedia=window.matchMedia('(max-width:700px)');
  let symbolPanelCollapsed=false;
  function syncSymbolPanel(){
    const panel=$('yd-symbol-home'),button=root.querySelector('[data-yd-symbols-toggle]');if(!panel||!button)return;
    const compact=symbolPanelMedia.matches;
    panel.hidden=!compact&&symbolPanelCollapsed;
    panel.closest('.yd-browser').classList.toggle('is-list-collapsed',!compact&&symbolPanelCollapsed);
    button.setAttribute('aria-controls',compact?'yd-symbol-dialog':'yd-symbol-home');
    button.setAttribute('aria-expanded',String(compact?$('yd-symbol-dialog').open:!symbolPanelCollapsed));
    button.textContent=compact?'选择币种':symbolPanelCollapsed?'展开币种':'收起币种';
  }
  symbolPanelMedia.addEventListener('change',()=>{if($('yd-symbol-dialog')?.open)closeDialog('yd-symbol-dialog');syncSymbolPanel();});
  function dialogViewport(){const v=window.visualViewport;root.style.setProperty('--yd-dialog-height',Math.max(140,(v?.height||innerHeight)-24)+'px');root.style.setProperty('--yd-dialog-top',(v?.offsetTop||0)+12+'px');}
  function showDialog(id){
    if(!dialogOrigin){const b=document.body;dialogOrigin={x:scrollX,y:scrollY,position:b.style.position,top:b.style.top,width:b.style.width,overflow:b.style.overflow};b.style.position='fixed';b.style.top=-dialogOrigin.y+'px';b.style.width='100%';b.style.overflow='hidden';}
    dialogViewport();$(id).showModal();
  }
  function unlockDialogs(){if(root.querySelector('dialog[open]')||!dialogOrigin)return;const o=dialogOrigin;dialogOrigin=null;Object.assign(document.body.style,{position:o.position,top:o.top,width:o.width,overflow:o.overflow});window.scrollTo({top:o.y,left:o.x,behavior:'instant'});}
  function restoreDialog(id){
    if(id==='yd-preview-dialog'){
      if($(id).open)return;
      previewVersion++;previewRequest?.abort();previewRequest=null;previewResize?.disconnect();previewResize=null;previewChartInstance?.remove();previewChartInstance=null;
      if(findingPreview&&state.active){const id=findingPreview.id;findingPreview=null;root.querySelector(`[data-yd-finding="${CSS.escape(id)}"]`)?.focus({preventScroll:true});}
    }
    if(id==='yd-enlarge-dialog'&&$('yd-chart-stage').parentElement!==$('yd-stage-home')){$('yd-stage-home').append($('yd-chart-stage'));$('yd-chart-stage').classList.remove('is-expanded');$('yd-expand').innerHTML='<span class="ui-icon ui-maximize" aria-hidden="true"></span>';}
    if(id==='yd-symbol-dialog'&&$('yd-symbol-panel').parentElement!==$('yd-symbol-home')){$('yd-symbol-home').append($('yd-symbol-panel'));root.querySelector('[data-yd-symbols-toggle]').setAttribute('aria-expanded','false');}
  }
  function closeDialog(id){$(id).close();restoreDialog(id);unlockDialogs();}
  window.visualViewport?.addEventListener('resize',dialogViewport);
  window.visualViewport?.addEventListener('scroll',dialogViewport);
  function html(id,markup){const el=$(id);if(el._markup!==markup){el.innerHTML=markup;el._markup=markup;}}
  function error(message,id='yd-error'){const el=$(id);if(el){el.hidden=!message;el.textContent=message||'';}else if(message&&state.active)root.textContent=message;}
  const requests=window.SpikeRuntime?.createRequests();
  async function api(path,options={}) {
    if(requests)return requests.json(BASE+path,{...options,headers:{'Content-Type':'application/json',...options.headers}});
    const response=await fetch(BASE+path,{...options,headers:{'Content-Type':'application/json',...options.headers}});
    const data=await response.json();if(!response.ok)throw Error(Array.isArray(data.detail)?data.detail.map(x=>x.msg).join('；'):data.detail||'请求失败，请重试');return data;
  }
  function loadSymbols(){
    if(state.symbols)return Promise.resolve(state.symbols);
    if(!symbolsPromise)symbolsPromise=(window.SpikeSymbols?.load()||api('/symbols')).then(d=>{state.symbols=d.items;html('yd-symbols',d.items.map(r=>`<option value="${esc(short(r.symbol))}">${esc(r.name)}</option>`).join(''));return d.items;}).finally(()=>{symbolsPromise=null;});
    return symbolsPromise;
  }
  const localInput=ms=>new Date(ms+8*3600000).toISOString().slice(0,16);
  function rangeDays(draft,days=1){const end=Math.floor(Date.now()/steps[draft.timeframe])*steps[draft.timeframe];draft.end=localInput(end);draft.start=localInput(end-Math.max(steps[draft.timeframe],days*86400000));}
  function defaultDraft(){const d={model:'grade-a-close-1280',timeframe:'15m',mode:'latest',scope:'selected',symbol:'ETH',start:'',end:''};rangeDays(d);return d;}
  function modelOptions(){return state.catalog.items.map(m=>`<option value="${m.id}" ${!m.available?'disabled':''}>${esc(m.name)}${m.recommended?' · 推荐':''}</option>`).join('');}
  function selectionFields(prefix){return `<div class="yd-form-grid"><label>检测范围<select id="${prefix}-scope"><option value="selected">单个币种</option><option value="all">全市场加密币</option></select></label><label id="${prefix}-symbol-label">币种<input id="${prefix}-symbol" list="yd-symbols" aria-label="币种" autocomplete="off" placeholder="例如 ETH 或 BTC" aria-describedby="${prefix}-symbol-help"><small id="${prefix}-symbol-help">支持 ETH、ETHUSDT、ETH/USDT</small></label><label>K 线周期<select id="${prefix}-timeframe">${Object.entries(periods).map(([v,n])=>`<option value="${v}">${n}</option>`).join('')}</select></label></div><details class="yd-advanced"><summary>模型选择 <span>默认使用推荐版本</span></summary><label>检测模型<select id="${prefix}-model">${modelOptions()}</select></label><p>15 分钟为原训练周期，其他周期可检测，效果尚需独立验证。</p></details>`;}
  function shell(){
    root.innerHTML=`<div class="yd-shell spike-ui">
      <div class="yd-toolbar"><h2 class="yd-workspace-title">形态巡检</h2><div class="yd-view-tabs nav nav-underline" role="tablist" aria-label="检测工作台"><button class="nav-link" role="tab" id="yd-tab-results" data-yd-tab="results" aria-controls="yd-panel-results">K 线详情</button><button class="nav-link" role="tab" id="yd-tab-history" data-yd-tab="history" aria-controls="yd-panel-history">检测记录</button><button class="nav-link" role="tab" id="yd-tab-schedules" data-yd-tab="schedules" aria-controls="yd-panel-schedules">形态巡检池 <span id="yd-plan-count"></span></button><button class="nav-link" role="tab" id="yd-tab-model" data-yd-tab="model" aria-controls="yd-panel-model">模型说明</button></div><button class="yd-primary btn btn-sm btn-primary" data-yd-new>新建检测</button></div>
      <p id="yd-error" class="yd-error" role="alert" hidden></p>
      <section id="yd-panel-results" role="tabpanel" aria-labelledby="yd-tab-results">
        <div id="yd-patrol-return" class="yd-patrol-return" hidden><button data-yd-patrol-back>← 返回最新发现</button><span>查看巡检保存的 K 线与识别框</span></div><div id="yd-run-progress" class="yd-run-progress" aria-live="polite" hidden></div>
        <div class="yd-results-layout"><div class="yd-browser"><div id="yd-symbol-home"><aside id="yd-symbol-panel" class="yd-symbol-panel" aria-label="各周期检测币种"><header><h2>多周期检测结果</h2><span id="yd-symbol-total"></span></header>
          <input class="form-control form-control-sm" id="yd-result-search" aria-label="搜索检测币种" placeholder="搜索币种…"><div class="yd-symbol-filters" role="group" aria-label="筛选币种"><button data-yd-filter="detected">有形态 <span id="yd-hit-count">0</span></button><button data-yd-filter="all">全部</button><button data-yd-filter="favorites">关注 <span id="yd-favorite-count">0</span></button><button data-yd-filter="failed" id="yd-failed-filter">失败</button></div>
          <label class="yd-symbol-sort"><span class="visually-hidden">币种排序</span><select class="form-select form-select-sm" id="yd-symbol-sort" aria-label="币种排序"><option value="latest">最新形态</option><option value="score">模型分数</option><option value="count">形态数量</option><option value="name">名称 A–Z</option><option value="name-desc">名称 Z–A</option><option value="favorites">关注币种优先</option></select></label><div id="yd-summary" class="yd-summary"></div><div id="yd-symbol-list" class="yd-symbol-list"></div></aside></div>
          <div id="yd-stage-home"><section class="yd-chart-stage" id="yd-chart-stage">
            <header class="yd-chart-head"><div><div class="yd-title-line"><h2 id="yd-chart-title" tabindex="-1">选择币种查看 K 线</h2><button id="yd-favorite" aria-pressed="false" title="保存在当前浏览器">☆ 关注</button></div><p id="yd-chart-sub">运行检测后，在这里查看形态。</p></div><div class="yd-switcher"><select id="yd-quick-symbol" aria-label="切换查看的币种"></select><button class="yd-mobile-filter" data-yd-symbols-toggle aria-expanded="false" aria-controls="yd-symbol-dialog">币种列表</button><button id="yd-prev" aria-label="上一个币种" title="上一个币种"><span class="ui-icon ui-chevron-left" aria-hidden="true"></span></button><span id="yd-symbol-index"></span><button id="yd-next" aria-label="下一个币种" title="下一个币种"><span class="ui-icon ui-chevron-right" aria-hidden="true"></span></button></div></header>
            <div class="yd-chart-commandbar"><div class="yd-context-actions"><div id="yd-periods" class="yd-periods" role="group" aria-label="K 线周期"></div><select id="yd-change-period" aria-label="K 线周期" hidden>${Object.entries(periods).map(([v,n])=>`<option value="${v}">${n}</option>`).join('')}</select></div>
            <div class="yd-chart-tools">
              <div class="yd-display-tools">${chartTools.toolbar()}<div class="yd-live-chart"><button id="yd-tail-refresh" class="btn btn-sm btn-outline-secondary yd-icon" title="更新 K 线" aria-label="更新 K 线"><span class="ui-icon ui-refresh" aria-hidden="true"></span></button></div><button id="yd-expand" class="btn btn-sm btn-outline-secondary yd-icon" title="放大图表" aria-label="放大图表"><span class="ui-icon ui-maximize" aria-hidden="true"></span></button>
              <div class="dropdown"><button class="btn btn-sm btn-outline-secondary yd-options-button" id="yd-options-toggle" aria-label="图表设置" data-bs-toggle="dropdown" data-bs-auto-close="outside" aria-expanded="false" aria-controls="yd-chart-options"><span class="ui-icon ui-adjustments" aria-hidden="true"></span><span>图表设置</span></button>
                <div id="yd-chart-options" class="dropdown-menu dropdown-menu-end yd-chart-options"><div class="yd-options-label">图表视角</div><div class="yd-view-modes btn-group" role="group" aria-label="图表视角"><button class="btn btn-sm btn-outline-secondary" id="yd-focus" title="聚焦当前形态">形态</button><button class="btn btn-sm btn-outline-secondary" id="yd-after" title="查看形态与后续行情">含后续</button></div><div class="dropdown-divider"></div><div class="yd-options-checks"><label class="form-check"><input class="form-check-input" type="checkbox" id="yd-follow" checked><span>跟随最新行情</span></label><label class="form-check"><input class="form-check-input" type="checkbox" id="yd-ma" checked><span>六条均线</span></label><label class="form-check"><input class="form-check-input" type="checkbox" id="yd-volume" checked><span>成交量</span></label></div><div class="yd-ma-legend">${Object.entries(colors).map(([name,color])=>`<span><i style="background:${color}"></i>${name.toUpperCase()}</span>`).join('')}</div><div class="dropdown-divider"></div><button class="dropdown-item" id="yd-scan-symbol">重新检测当前币种</button><div class="dropdown-divider"></div><button class="dropdown-item" id="yd-export-png">保存图片</button><button class="dropdown-item" id="yd-export-json">导出数据</button></div>
              </div></div></div></div><div id="yd-insight" class="yd-insight" role="status" aria-live="polite"></div>
            <div class="yd-chart-readout"><div id="yd-ohlc" class="yd-ohlc cv-readout"></div><div id="yd-six-ma" class="chart-ma-legend" aria-label="六条均线数值"></div></div>
            <div id="yd-chart-wrap"><div id="yd-chart-empty" class="yd-chart-empty" role="status">正在读取检测结果…</div><div id="yd-chart"></div></div>
            <footer class="yd-chart-foot"><span id="yd-crosshair">北京时间 · 滚轮缩放，拖拽平移</span><span id="yd-tail-status" role="status">检测时快照</span><a href="https://www.tradingview.com/" target="_blank" rel="noopener">TradingView Lightweight Charts™</a></footer>
            <div class="yd-detections"><header><h3>密集形态 <span id="yd-box-count">0</span></h3><div><select id="yd-boxes" aria-label="结果口径"><option value="structure">已筛选形态</option><option value="raw">全部原始框</option></select><select id="yd-side" aria-label="检测方向"><option value="all">多空</option><option value="long">多头</option><option value="short">空头</option></select></div></header><div id="yd-box-list" class="yd-box-list"></div></div>
          </section></div></div></div>
        <details class="yd-footnote yd-reading-guide"><summary>数据与操作说明</summary><p>滚轮缩放，拖拽平移。更新 K 线保留原识别框；重新检测才会生成新结果。仅使用已收盘 K 线，模型分数不代表胜率。</p></details>
      </section>
      <section id="yd-panel-history" class="yd-card" role="tabpanel" aria-labelledby="yd-tab-history" hidden><header><div><h2>检测记录</h2><p>手动检测和定时巡检都保留在这里。点击一条记录，直接回到看图。</p></div><span id="yd-worker"></span></header><div id="yd-jobs-list"></div><div id="yd-jobs-pages" class="yd-pagination"></div></section>
      <section id="yd-panel-schedules" role="tabpanel" aria-labelledby="yd-tab-schedules" hidden>
        <div class="yd-overview-actions"><button data-yd-plan-new>＋ 添加巡检</button><a href="#yolo-detect?tab=history">检测记录 ↗</a></div>
        <div id="yd-patrol-status" class="yd-patrol-status" role="status"></div>
        <div class="yd-feed-filters"><input id="yd-feed-search" aria-label="搜索巡检形态币种" placeholder="搜索币种，如 BTC"><select id="yd-feed-days" aria-label="发现时间范围"><option value="1">最近 1 天</option><option value="7" selected>最近 7 天</option><option value="30">最近 30 天</option><option value="0">全部发现</option></select><select id="yd-feed-period" aria-label="巡检形态周期"><option value="">全部周期</option>${Object.entries(periods).map(([v,n])=>`<option value="${v}">${n}</option>`).join('')}</select><select id="yd-feed-order" aria-label="巡检发现排序"><option value="newest">最新发现优先</option><option value="score">模型分数从高到低</option><option value="symbol">币种 A → Z</option></select><select id="yd-feed-side" aria-label="巡检形态方向"><option value="">多空方向</option><option value="long">多头</option><option value="short">空头</option></select><button data-yd-feed-refresh class="yd-icon" title="刷新发现" aria-label="刷新发现">↻</button></div>
        <div class="yd-feed-meta"><span id="yd-feed-count">正在读取形态…</span><span id="yd-feed-updated"></span></div>
        <p id="yd-feed-error" class="yd-error" role="alert" hidden></p><header class="yd-radar-heading"><h2>形态雷达</h2><span>点击卡片查看 K 线与均线形态</span></header><div id="yd-findings" class="yd-findings" aria-label="巡检发现的形态"></div><div id="yd-feed-more" class="yd-pagination"></div>
        <p class="yd-footnote">按首次发现排序，重复扫描合并显示。发现时间是巡检保存结果的时间，确认时间是模型看见形态的 K 线收盘时间；列表包含过去发现，不代表形态仍在持续。</p>
        <details class="yd-patrol-plans"><summary>管理巡检计划 <span id="yd-plan-summary-count"></span></summary><p class="yd-help">关闭网页后继续扫描；本机电脑与服务需要保持运行。停止计划后，已有发现仍然保留。</p><div id="yd-schedules"></div></details>
      </section>
      <section id="yd-panel-account" role="tabpanel" aria-label="实盘账户" hidden><div id="yd-account-detail"></div></section>
      <section id="yd-panel-model" role="tabpanel" aria-labelledby="yd-tab-model" hidden>${training()}</section>
      <datalist id="yd-symbols"></datalist>
      <dialog id="yd-preview-dialog" class="yd-preview-dialog" aria-labelledby="yd-preview-title"><header><div><h2 id="yd-preview-title">K 线预览</h2><p id="yd-preview-context"></p></div><button type="button" data-yd-close="yd-preview-dialog" aria-label="关闭 K 线预览">× <small>Esc</small></button></header><p id="yd-preview-confirmation"></p><div id="yd-preview-legend" class="yd-six-ma"></div><div class="yd-preview-stage"><div id="yd-preview-chart"></div><p id="yd-preview-message" role="status">正在读取检测快照…</p></div><footer><span>检测时快照 · 可拖动、缩放</span><button type="button" data-yd-preview-retry hidden>重新加载</button><button type="button" data-yd-preview-full>查看完整图表 ↗</button></footer></dialog>
      <dialog id="yd-scan-dialog" class="yd-dialog" aria-labelledby="yd-scan-heading"><header><h2 id="yd-scan-heading">新建检测</h2><button data-yd-close="yd-scan-dialog" aria-label="关闭新建检测">×</button></header><form id="yd-scan-form" novalidate><div class="yd-form-body"><div class="yd-mode-options" role="group" aria-label="检测模式"><button type="button" data-yd-mode="latest">当前盘口<span>查看最新收盘附近的形态</span></button><button type="button" data-yd-mode="history">历史识别<span>检查指定时间段的形态</span></button></div>${selectionFields('yd')}
        <div id="yd-dates"><label>检测时间段 · 北京时间</label><div class="yd-presets"><button type="button" data-yd-days="1">最近 1 天</button><button type="button" data-yd-days="3">最近 3 天</button><button type="button" data-yd-days="7">最近 7 天</button></div><div class="yd-form-grid"><label>开始<input type="datetime-local" id="yd-start"></label><label>结束<input type="datetime-local" id="yd-end"></label></div><div class="yd-date-help"><p class="yd-help">均为北京时间；日线按每天 08:00 划分。</p><button type="button" id="yd-align-dates">对齐收盘时间</button></div></div>
        <p id="yd-scan-estimate" class="yd-help"></p><p id="yd-scan-error" class="yd-error" role="alert" hidden></p></div><footer><span id="yd-submit-summary"></span><button type="submit" id="yd-submit" class="yd-primary">开始检测</button></footer></form></dialog>
      <dialog id="yd-plan-dialog" class="yd-dialog" aria-labelledby="yd-plan-heading"><header><h2 id="yd-plan-heading">添加自动巡检</h2><button data-yd-close="yd-plan-dialog" aria-label="关闭自动巡检设置">×</button></header><form id="yd-schedule-form" novalidate><div class="yd-form-body">${selectionFields('ys')}<label>扫描频率<select id="yd-interval"><option value="5">每 5 分钟</option><option value="15">每 15 分钟</option><option value="30">每 30 分钟</option><option value="60" selected>每小时</option><option value="240">每 4 小时</option><option value="1440">每天</option></select></label><p class="yd-help">每次覆盖全部所选币种。只检测新增的收盘位置；忙碌时延后补扫，断点自动保存。</p><p id="yd-plan-error" class="yd-error" role="alert" hidden></p></div><footer><span id="yd-plan-summary"></span><button type="submit" id="yd-plan-submit" class="yd-primary">启用巡检</button></footer></form></dialog>
      <dialog id="yd-inspection" class="yd-dialog yd-inspection" aria-labelledby="yd-input-heading"><header><h2 id="yd-input-heading">模型原图</h2><button data-yd-close="yd-inspection" aria-label="关闭模型原图">×</button></header><div id="yd-input-content"></div></dialog>
      <dialog id="yd-symbol-dialog" class="yd-dialog yd-symbol-dialog" aria-labelledby="yd-picker-heading"><header><h2 id="yd-picker-heading">选择币种</h2><button data-yd-close="yd-symbol-dialog" aria-label="关闭币种列表">×</button></header><div id="yd-picker-body"></div></dialog><dialog id="yd-enlarge-dialog" class="yd-enlarge-dialog" aria-label="放大的 K 线图"><header class="yd-enlarge-header"><strong>形态 K 线</strong><button data-yd-close="yd-enlarge-dialog">关闭大图</button></header></dialog>
    </div>`;
    symbolRenderKey='';bind();$('yd-symbol-sort').value=state.symbolOrder;setTab(state.tab,false);drawSymbols();
  }
  function training(){const t=state.catalog.training;return `<details class="yd-training" open><summary><span><span class="yd-eyebrow">MODEL LINEAGE</span><strong>Grade-A 是怎么训练出来的？</strong></span><span>样本 → 训练 → 验证 → 迭代 ＋</span></summary><p>${esc(t.note)}</p><div class="yd-lineage"><article><b>01</b><h3>定义与筛选</h3><p>双组均线密集启动，多头 / 空头两类。SMA 与 EMA 各 20、60、120，共六条线。</p></article><article><b>02</b><h3>32,000 张图</h3><p>8,000 正图 + 24,000 负图。正图来自 1,043 个事件，重裁图不等于新事件。</p></article><article><b>03</b><h3>YOLO11s · 40 轮</h3><p>原生 1280 输入，AdamW，训练 27,200 图，时间验证 4,800 图；翻转、mosaic、mixup、HSV 关闭。</p></article><article><b>04</b><h3>按证据选择版本</h3><p>Close 基线 best.pt 为第 4 轮。更长训练和更高检出率都不自动意味着更好。</p></article></div>
      <div class="yd-table-wrap"><table><thead><tr><th>研究版本</th><th>最早事件检出 / 155</th><th>负图误触 / 3,600</th><th>结论</th></tr></thead><tbody>${state.catalog.items.map(m=>`<tr><td>${esc(m.name)}</td><td>${(m.earliest_recall*100).toFixed(2)}%</td><td>${m.negative_fires}</td><td>${esc(m.verdict)}</td></tr>`).join('')}</tbody></table></div>
      <p>这些数据来自已有开发集比较，不能作为独立市场精度或收益验收。模型训练周期是 15m；5m、1H、4H、日线均标记为跨周期研究。每次任务固定模型版本、参数和输入来源。</p>
      <p><a href="#yolo">训练与实验记录 ↗</a> · <a href="#models">模型中心 ↗</a> · 后续迭代：逐样本确认与难负例 → 时间分组验证 → 同输入版本对比 → 更新推荐。新训练仍需通过项目准入。</p></details>`;}
  function syncRoute(){window.SpikeNavigation?.sync('yolo-detect',state.tab==='schedules'
    ?{tab:state.tab,symbol:exactSymbol(state.feedQuery)?state.feedQuery:null,q:exactSymbol(state.feedQuery)?null:state.feedQuery,days:state.feedDays,timeframe:state.feedPeriod,side:state.feedSide,sort:state.feedOrder}
    :{tab:state.tab,job:state.overview&&!state.fromPatrol?null:state.job?.id,symbol:state.symbol,timeframe:state.overview?state.timeframe:null,box:state.fromPatrol?(state.selected||state.focusBox):null,from:state.fromPatrol?'patrol':null});}
  function restorePatrolFilters(p){
    clearTimeout(feedTimer);feedVersion++;
    Object.assign(state,patrolFilters(p),{feedPage:0,feedBefore:null,feed:null});
    for(const [id,key] of [['yd-feed-search','feedQuery'],['yd-feed-days','feedDays'],['yd-feed-period','feedPeriod'],['yd-feed-side','feedSide'],['yd-feed-order','feedOrder']])$(id).value=String(state[key]);
    $('yd-feed-count').textContent='正在读取巡检形态…';$('yd-feed-updated').textContent='';
    html('yd-findings','');html('yd-feed-more','');error('','yd-feed-error');
  }
  function setTab(tab,sync=true){
    if(!['results','account','history','schedules','model'].includes(tab))tab='schedules';if(sync&&tab==='results'&&state.tab!=='results'&&state.overview)state.latestRevision='';state.tab=tab;
    root.querySelectorAll('[data-yd-tab]').forEach(b=>{const yes=b.dataset.ydTab===tab;b.setAttribute('aria-selected',String(yes));b.tabIndex=yes?0:-1;});
    const title=root.querySelector('.yd-workspace-title');if(title)title.textContent=tab==='account'?'实盘与风控':'形态巡检';
    for(const name of ['results','account','history','schedules','model'])$('yd-panel-'+name).hidden=name!==tab;
    if(tab==='results'&&state.result&&(!chart||chartNeedsTheme))renderChart(chart?.timeScale().getVisibleLogicalRange()).catch(e=>error(e.message));
    root.dataset.detectionPane=tab;
    window.SpikeManualAccount?.setActive(state.active,tab);
    if(tab!=='results')destroyChart();
    if(sync)syncRoute();
    if(sync&&state.loaded)refresh();
  }
  function draftFromJob(scope){const j=state.job,d=defaultDraft();d.symbol=short(state.symbol||'ETH');d.timeframe=state.timeframe||d.timeframe;if(j){Object.assign(d,{model:j.spec.model_id,timeframe:j.spec.timeframe,scope:scope||j.spec.scope,symbol:short(state.symbol||j.spec.symbols[0]||'ETH')});rangeDays(d);}return d;}
  function openScan(){
    if(!state.draft){const saved=readSaved('spike.yolo.scan-draft.v2',null);state.draft=saved&&typeof saved.symbol==='string'&&steps[saved.timeframe]?{...defaultDraft(),...saved}:draftFromJob();}
    if(!state.catalog.items.some(m=>m.id===state.draft.model&&m.available))state.draft.model=state.catalog.items.find(m=>m.available&&m.recommended)?.id||state.catalog.items.find(m=>m.available)?.id||'';
    saveDraft();fillDraft('yd',state.draft);error('','yd-scan-error');showDialog('yd-scan-dialog');
    const focus=state.draft.scope==='all'?$('yd-scope'):$('yd-symbol');focus.focus();focus.select?.();
  }
  function fillDraft(prefix,draft){
    for(const key of ['scope','symbol','timeframe','model'])$(prefix+'-'+key).value=draft[key];
    if(prefix==='yd'){for(const key of ['start','end'])$('yd-'+key).value=draft[key];}
    draftContext(prefix);
  }
  function draftContext(prefix){
    const d=prefix==='yd'?state.draft:state.scheduleDraft;if(!d)return;
    $(prefix+'-symbol-label').hidden=d.scope==='all';$(prefix+'-symbol').required=d.scope==='selected';
    const summary=`${d.scope==='all'?'全市场加密币':d.symbol.trim().toUpperCase()||'请选择币种'} · ${periods[d.timeframe]}`;
    if(prefix==='yd'){
      $('yd-dates').hidden=d.mode!=='history';$('yd-start').required=$('yd-end').required=d.mode==='history';
      root.querySelectorAll('[data-yd-mode]').forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.ydMode===d.mode)));
      $('yd-submit-summary').textContent=summary+' · '+(d.mode==='latest'?'当前盘口':'历史识别');
      const spec=draftSpec(d),bars=Math.round((spec.end_ms-spec.start_ms)/steps[d.timeframe]);
      $('yd-scan-estimate').textContent=(d.mode==='history'&&Number.isFinite(bars)&&bars>0?`每币检查 ${bars.toLocaleString()} 根历史 K 线。`:'检查最近 3 个已收盘位置。')+(d.scope==='all'?'全市场任务较慢，提交后可离开页面，稍后到“检测记录”查看。':'检测期间可继续浏览其他结果。');
    }else $('yd-plan-summary').textContent=summary+' · '+$('yd-interval').selectedOptions[0].textContent;
  }
  async function validateDraft(prefix){
    const d=prefix==='yd'?state.draft:state.scheduleDraft,errorId=prefix==='yd'?'yd-scan-error':'yd-plan-error';
    if(validating.has(prefix))return false;
    const button=$(prefix==='yd'?'yd-submit':'yd-plan-submit'),label=button.textContent;validating.add(prefix);button.disabled=true;
    try{
    if(d.scope==='selected'&&!state.symbols){button.textContent='正在检查币种…';await loadSymbols();}
    if(d!==(prefix==='yd'?state.draft:state.scheduleDraft)||!$(prefix==='yd'?'yd-scan-dialog':'yd-plan-dialog').open)return false;
    let problem=draftProblem(d,state.symbols?.map(r=>r.symbol));
    if(!problem&&!state.catalog.items.some(m=>m.id===d.model&&m.available))problem={field:'model',message:'当前模型不可用，请展开“模型选择”选择可用版本。'};
    for(const key of ['symbol','timeframe','start','end','model'])$(prefix+'-'+key)?.removeAttribute('aria-invalid');
    error(problem?.message||'',errorId);
    if(problem){const field=$(prefix+'-'+problem.field);field?.setAttribute('aria-invalid','true');if(problem.field==='model')field.closest('details').open=true;field?.focus();}
    return !problem;
    }catch(e){error('暂时无法检查币种列表，请稍后重试。'+e.message,errorId);return false;}
    finally{validating.delete(prefix);button.disabled=false;button.textContent=label;}
  }
  function openPlan(){state.scheduleDraft=state.scheduleDraft||draftFromJob();state.scheduleDraft.mode='latest';fillDraft('ys',state.scheduleDraft);error('','yd-plan-error');showDialog('yd-plan-dialog');}
  async function submitScan(spec,button,errorId){
    if(submitting)return;submitting=true;button.disabled=true;const label=button.textContent;button.textContent='正在提交…';error('',errorId);
    const identity=JSON.stringify(spec);if(submitKey?.identity!==identity)submitKey={identity,key:crypto.randomUUID()};
    try{
      const job=await api('/jobs',{method:'POST',body:JSON.stringify({...spec,request_id:submitKey.key})});submitKey=null;
      if($('yd-scan-dialog').open)closeDialog('yd-scan-dialog');
      state.jobOffset=0;await chooseJob(job.id);await refresh();
    }catch(e){error(e.message,errorId);}finally{submitting=false;button.disabled=false;button.textContent=label;}
  }
  function bind(){
    const on=(id,event,fn)=>$(id).addEventListener(event,fn);
    on('yd-feed-search','input',e=>{state.feedQuery=e.target.value;state.feedPage=0;state.feedBefore=null;clearTimeout(feedTimer);feedVersion++;syncRoute();feedTimer=setTimeout(refreshFeed,250);});
    for(const [id,key] of [['yd-feed-days','feedDays'],['yd-feed-period','feedPeriod'],['yd-feed-side','feedSide'],['yd-feed-order','feedOrder']])on(id,'change',e=>{state[key]=key==='feedDays'?Number(e.target.value):e.target.value;state.feedPage=0;state.feedBefore=null;syncRoute();refreshFeed();});
    root.addEventListener('click',delegate);
    root.addEventListener('change',e=>{if(e.target.matches('[data-yd-feed-size]')){state.feedLimit=Number(e.target.value);state.feedPage=0;state.feedBefore=null;refreshFeed();}});
    root.querySelectorAll('dialog').forEach(d=>d.addEventListener('close',()=>{restoreDialog(d.id);unlockDialogs();}));
    on('yd-preview-dialog','click',e=>{if(e.target!==$('yd-preview-dialog'))return;const r=e.target.getBoundingClientRect();if(e.clientX<r.left||e.clientX>r.right||e.clientY<r.top||e.clientY>r.bottom)closeDialog('yd-preview-dialog');});
    on('yd-symbol-sort','change',e=>{state.symbolOrder=e.target.value;save('spike.yolo.symbol-sort.v1',state.symbolOrder);drawSymbols();});
    on('yd-follow','change',e=>{state.follow=e.target.checked;tailVersion++;state.tailLoading=false;state.tailError='';if(state.follow)refreshTail(true);else{state.tail=null;updateChartData();if(state.selected)selectBox(state.selected);else recentRange();tailStatus();}});
    on('yd-tail-refresh','click',()=>{state.follow=true;$('yd-follow').checked=true;refreshTail(true);});
    root.querySelector('.yd-view-tabs').addEventListener('keydown',e=>{if(!['ArrowLeft','ArrowRight'].includes(e.key))return;e.preventDefault();const tabs=['schedules','results','history','model'];setTab(tabs[(tabs.indexOf(state.tab)+(e.key==='ArrowRight'?1:tabs.length-1))%tabs.length]);$('yd-tab-'+state.tab).focus();});
    for(const prefix of ['yd','ys'])for(const key of ['scope','symbol','timeframe','model'])on(prefix+'-'+key,key==='symbol'?'input':'change',e=>{
      const d=prefix==='yd'?state.draft:state.scheduleDraft;d[key]=e.target.value;draftContext(prefix);e.target.removeAttribute('aria-invalid');error('',prefix==='yd'?'yd-scan-error':'yd-plan-error');if(prefix==='yd')saveDraft();
    });
    for(const key of ['start','end'])on('yd-'+key,'input',e=>{state.draft[key]=e.target.value;draftContext('yd');saveDraft();e.target.removeAttribute('aria-invalid');error('','yd-scan-error');});
    on('yd-align-dates','click',()=>{const spec=draftSpec(state.draft),step=steps[state.draft.timeframe],end=Math.floor(Date.now()/step)*step;state.draft.start=localInput(Math.floor((Number.isFinite(spec.start_ms)?spec.start_ms:end-86400000)/step)*step);state.draft.end=localInput(Math.floor(Math.min(Number.isFinite(spec.end_ms)?spec.end_ms:end,end)/step)*step);fillDraft('yd',state.draft);saveDraft();validateDraft('yd');});
    on('yd-interval','change',()=>draftContext('ys'));
    on('yd-scan-form','submit',async e=>{e.preventDefault();if(!await validateDraft('yd'))return;saveDraft();await submitScan(draftSpec(state.draft),$('yd-submit'),'yd-scan-error');});
    on('yd-schedule-form','submit',async e=>{
      e.preventDefault();if(scheduleSubmitting||!await validateDraft('ys'))return;scheduleSubmitting=true;$('yd-plan-submit').disabled=true;error('','yd-plan-error');
      try{await api('/schedules',{method:'POST',body:JSON.stringify({spec:draftSpec(state.scheduleDraft),interval_minutes:Number($('yd-interval').value)})});closeDialog('yd-plan-dialog');setTab('schedules');await refresh();}
      catch(e){error(e.message,'yd-plan-error');}finally{scheduleSubmitting=false;$('yd-plan-submit').disabled=false;}
    });
    on('yd-result-search','symbolselect',e=>{const symbol=e.detail.symbol;if(allSymbolRows().some(r=>r.symbol===symbol)){if(!symbolRows().some(r=>r.symbol===symbol))state.resultFilter='all';chooseSymbol(symbol);}});
    window.SpikeManualAccount?.bind($('yd-account-detail'));
    on('yd-result-search','input',e=>{state.symbolSearch=e.target.value;drawSymbols();});
    on('yd-symbol-list','keydown',e=>{if(!['ArrowDown','ArrowUp'].includes(e.key)||!e.target.closest('[data-yd-symbol]'))return;e.preventDefault();moveSymbol(e.key==='ArrowDown'?1:-1,true);});
    for(const [id,key] of [['yd-boxes','boxes'],['yd-side','side']])on(id,'change',e=>{state[key]=e.target.value;state.page=0;state.selected=null;drawBoxes();if(boxes().length)selectBox(boxes()[0].id);});
    on('yd-ma','change',e=>{state.ma=e.target.checked;maSeries.forEach(s=>s.applyOptions({visible:state.ma}));$('yd-six-ma').hidden=!state.ma;});
    on('yd-volume','change',e=>{state.volume=e.target.checked;volume?.applyOptions({visible:state.volume});series?.priceScale().applyOptions({scaleMargins:{top:.1,bottom:state.volume?.2:.08}});});
    $('yd-chart-stage').addEventListener('click',e=>{if(e.target.closest('[data-cv]'))state.viewTouched=true;});on('yd-after','click',()=>{state.viewTouched=true;followupRange();});
    for(const event of ['pointerdown','wheel','touchstart','keydown'])on('yd-chart',event,()=>{state.viewTouched=true;});
    on('yd-focus','click',()=>{state.viewTouched=true;const b=boxes().find(b=>b.id===state.selected)||boxes()[0];if(b)selectBox(b.id);});
    on('yd-quick-symbol','change',e=>chooseSymbol(e.target.value));
    on('yd-prev','click',()=>moveSymbol(-1));on('yd-next','click',()=>moveSymbol(1));
    on('yd-scan-symbol','click',()=>{state.draft=draftFromJob('selected');openScan();});
    on('yd-change-period','change',e=>switchPeriod(e.target.value));
    syncSymbolPanel();
    window.SpikeUI.Dropdown.getOrCreateInstance($('yd-options-toggle'),{autoClose:'outside'});
    on('yd-favorite','click',()=>{if(!state.symbol)return;favorites=favorites.includes(state.symbol)?favorites.filter(s=>s!==state.symbol):[...favorites,state.symbol].slice(-300);save('spike.yolo.favorites.v1',favorites);drawSymbols();});
    on('yd-expand','click',()=>{const dialog=$('yd-enlarge-dialog');if(dialog.open)closeDialog('yd-enlarge-dialog');else{showDialog('yd-enlarge-dialog');dialog.append($('yd-chart-stage'));$('yd-chart-stage').classList.add('is-expanded');$('yd-expand').innerHTML='<span class="ui-icon ui-minimize" aria-hidden="true"></span>';}});
    on('yd-export-png','click',()=>{if(!chart)return;const name=`${short(state.symbol)}-${state.result.timeframe}-detections.png`;chart.takeScreenshot().toBlob(blob=>{if(blob)download(blob,name,'image/png');});});
    on('yd-export-json','click',()=>{if(state.result)download(JSON.stringify(state.result,null,2),`${short(state.symbol)}-${state.result.timeframe}-detections.json`,'application/json');});
  }
  async function delegate(e){
    const b=e.target.closest('button');if(!b)return;const d=b.dataset;
    try{
      if(d.ydTab)setTab(d.ydTab);
      if(d.ydPosition){if(state.tab!=='results')setTab('results');state.overview=true;state.symbol=d.ydPosition;state.resultFilter='all';await switchPeriod(state.timeframe||'15m');if(state.active&&state.tab==='results'&&state.symbol===d.ydPosition){$('yd-chart-title').focus({preventScroll:true});$('yd-chart-stage').scrollIntoView({block:'start',behavior:'smooth'});}return;}
      if(d.ydPairPeriod){await chooseSymbolPeriod(d.ydPairSymbol,d.ydPairPeriod);return;}
      if(d.ydPeriod)await switchPeriod(d.ydPeriod);
      if('ydLatest'in d){state.timeframe=state.job?.spec.timeframe||state.timeframe;state.fromPatrol=false;$('yd-patrol-return').hidden=true;state.overview=true;state.viewTouched=false;await refreshLatest(true);}
      if('ydFeedRefresh'in d){state.feedPage=0;state.feedBefore=null;await refreshFeed();}
      if('ydFeedPage'in d){state.feedPage=Number(d.ydFeedPage);await refreshFeed();$('yd-findings').scrollIntoView({block:'nearest'});}
      if('ydFeedClear'in d){state.feedQuery='';state.feedDays=0;state.feedPeriod='';state.feedSide='';state.feedPage=0;state.feedBefore=null;$('yd-feed-search').value='';$('yd-feed-days').value='0';$('yd-feed-period').value=$('yd-feed-side').value='';syncRoute();await refreshFeed();}
      if(d.ydFinding){const item=state.feed?.items.find(r=>r.id===d.ydFinding);if(item)await openFindingPreview(item);}
      if('ydPreviewRetry'in d&&findingPreview)await openFindingPreview(findingPreview);
      if('ydPreviewFull'in d&&findingPreview){const item=findingPreview;closeDialog('yd-preview-dialog');await chooseJob(item.job_id,item.symbol,'results',item.detection_id,true);}
      if('ydPatrolBack'in d){setTab('schedules');root.scrollIntoView({block:'start'});}
      if('ydSymbolsToggle'in d&&!symbolPanelMedia.matches){symbolPanelCollapsed=!symbolPanelCollapsed;save('spike.yolo.symbol-panel.collapsed',symbolPanelCollapsed);syncSymbolPanel();return;}
      if('ydSymbolsToggle'in d){showDialog('yd-symbol-dialog');$('yd-picker-body').append($('yd-symbol-panel'));b.setAttribute('aria-expanded','true');$('yd-symbol-dialog').querySelector('[data-yd-close]').focus({preventScroll:true});}
      if('ydNew'in d)openScan();if('ydPlanNew'in d)openPlan();if(d.ydClose)closeDialog(d.ydClose);
      if(d.ydMode){state.draft.mode=d.ydMode;draftContext('yd');saveDraft();error('','yd-scan-error');}
      if(d.ydDays){rangeDays(state.draft,Number(d.ydDays));fillDraft('yd',state.draft);saveDraft();error('','yd-scan-error');}
      if(d.ydJob)await chooseJob(d.ydJob);if(d.ydSymbol)await chooseSymbol(d.ydSymbol);
      if(d.ydFilter){state.resultFilter=d.ydFilter;drawSymbols();}
      if(d.ydBox)selectBox(d.ydBox);if(d.ydInput)openInspection(d.ydInput);
      if(d.ydPage){state.page+=Number(d.ydPage);listBoxes();}
      if(d.ydJobsPage){state.jobOffset+=Number(d.ydJobsPage)*20;await refresh();}
      if(d.ydCancel){b.disabled=true;await api(`/jobs/${d.ydCancel}/cancel`,{method:'POST'});await refresh();}
      if(d.ydStop){b.disabled=true;await api(`/schedules/${d.ydStop}`,{method:'DELETE'});await refresh();}
      if('ydRerun'in d&&state.job){const s=state.job.spec;await submitScan({model_id:s.model_id,timeframe:s.timeframe,mode:s.mode,scope:s.scope,symbols:s.scope==='all'?[]:s.symbols,...(s.mode==='history'?{start_ms:s.start_ms,end_ms:s.end_ms}:{})},b,'yd-error');}
      if('ydClearSearch'in d){if(state.resultFilter==='all'){state.symbolSearch='';$('yd-result-search').value='';}state.resultFilter='all';drawSymbols();}
      if('ydShowRaw'in d){state.boxes='raw';state.side='all';$('yd-boxes').value='raw';$('yd-side').value='all';state.page=0;drawBoxes();if(boxes().length)selectBox(boxes()[0].id);}
    }catch(e){b.disabled=false;error(e.message);}
  }
  function download(content,name,type){const url=URL.createObjectURL(new Blob([content],{type}));const a=document.createElement('a');a.href=url;a.download=name;root.append(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(url),1000);}
  function scopeName(spec){return spec.scope==='all'?'全市场':spec.symbols.map(short).join(' / ');}
  function summary(){
    if(state.overview){
      const rows=allSymbolRows(),hits=rows.filter(r=>r.count>0).length,tfs=[...new Set(state.latest.map(r=>r.timeframe))].sort((a,b)=>steps[a]-steps[b]);
      const newer=periodResult(state.latest,state.symbol,state.timeframe);
      html('yd-summary',`<div class="yd-summary-inline"><strong>${hits} 币有形态</strong><span>${rows.length} 币 · ${tfs.map(p=>p==='1Dutc'?'日线':p).join(' / ')}</span></div><div class="yd-summary-actions">${newer&&newer.job_id!==state.job?.id?'<button data-yd-latest>加载新结果</button>':''}</div>`);
      $('yd-run-progress').hidden=true;return;
    }
    const j=state.job;if(!j){html('yd-summary',state.loadingJob?'<strong>正在打开检测记录…</strong>':'<div><strong>从一次检测开始</strong><p>选择币种或扫描全市场，结果直接显示在下方。</p></div><button class="yd-primary btn btn-sm btn-primary" data-yd-new>新建检测</button>');return;}
    const rows=j.results||[],hits=rows.filter(r=>r.count>0).length,unavailable=rows.filter(r=>r.availability==='insufficient_history').length,failed=rows.filter(r=>r.error&&r.availability!=='insufficient_history').length;
    const latest=state.jobs.find(n=>n.id!==j.id&&n.created_ms>j.created_ms&&terminal.has(n.status)&&n.status!=='failed'&&sameScan(n.spec,j.spec));
    const age=j.spec.end_ms?Date.now()-j.spec.end_ms:0,isOld=j.spec.mode==='latest'&&age>steps[j.spec.timeframe];
    html('yd-summary',`<div><strong>${scopeName(j.spec)} · ${periods[j.spec.timeframe]} <span class="yd-status ${j.status}">${names[j.status]}</span></strong><p>${j.spec.mode==='history'?'历史识别':'形态识别'} · ${fmt(j.spec.end_ms||j.created_ms)} 北京${isOld?' · <span class="yd-stale">旧识别记录</span>':''} · 已处理 ${rows.length}/${j.progress.total||'—'} 币 · <b>${hits} 币有形态</b>${unavailable?' · '+unavailable+' 币历史不足':''}${failed?' · '+failed+' 币失败':''}</p>${j.error?`<p class="yd-error-text">${esc(j.error)}</p>`:''}</div><div class="yd-summary-actions"><button data-yd-latest>回到最新汇总</button>${latest?`<button data-yd-job="${latest.id}">查看较新结果</button>`:''}<button data-yd-rerun ${!terminal.has(j.status)||submitting?'disabled':''}>${j.spec.mode==='latest'?'重新扫描':'重新检测'}</button></div>`);
    const p=$('yd-run-progress');p.hidden=terminal.has(j.status);
    html('yd-run-progress',`<span>${names[j.status]} · ${j.progress.completed||0}/${j.progress.total||'—'} 币${j.progress.symbol?' · '+esc(short(j.progress.symbol)):''}${j.progress.windows_total?' · '+j.progress.windows_done+'/'+j.progress.windows_total+' 窗口':''}</span><progress aria-label="本次检测进度" max="${j.progress.total||1}" value="${j.progress.completed||0}"></progress><button data-yd-cancel="${j.id}" ${j.cancel?'disabled':''}>${j.cancel?'正在取消…':'取消检测'}</button>`);
  }
  function allSymbolRows(){if(!state.overview)return state.job?.results||[];if(aggregateSource!==state.latest){aggregateSource=state.latest;aggregateRows=aggregateSymbols(state.latest);}return aggregateRows;}
  function symbolRows(){return filteredSymbols(allSymbolRows(),state.symbolSearch,state.resultFilter,favorites,state.symbolOrder);}
  function drawSymbols(){
    const all=allSymbolRows(),key=[state.overview?state.latestRevision:JSON.stringify(state.job?.results),state.symbol,state.timeframe,state.symbolSearch,state.resultFilter,state.symbolOrder,favorites.join(','),state.loadingJob,state.job?.status].join('|');
    if(key===symbolRenderKey)return;symbolRenderKey=key;
    const rows=filteredSymbols(all,state.symbolSearch,state.resultFilter,favorites,state.symbolOrder),hit=all.filter(r=>r.count>0).length,failed=all.filter(r=>r.error).length;
    window.SpikeSymbols?.setOptions($('yd-result-search'),all.map(r=>r.symbol));
    drawPeriods();$('yd-symbol-total').textContent=all.length+' 币';$('yd-hit-count').textContent=hit;$('yd-failed-filter').textContent='异常 '+failed;$('yd-failed-filter').hidden=!failed;
    $('yd-favorite-count').textContent=all.filter(r=>favorites.includes(r.symbol)).length;
    const favorite=favorites.includes(state.symbol);$('yd-favorite').disabled=!state.symbol;$('yd-favorite').innerHTML='<span class="ui-icon '+(favorite?'ui-star-filled':'ui-star')+'" aria-hidden="true"></span>';$('yd-favorite').title=favorite?'取消关注':'关注币种';$('yd-favorite').setAttribute('aria-label',favorite?'取消关注':'关注币种');$('yd-favorite').setAttribute('aria-pressed',String(favorite));
    $('yd-scan-symbol').disabled=$('yd-change-period').disabled=!state.symbol;
    root.querySelectorAll('[data-yd-filter]').forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.ydFilter===state.resultFilter)));
    const focused=document.activeElement,focus=focused?.closest('#yd-symbol-list')?focused.dataset:null;
    html('yd-symbol-list',rows.length?rows.map(r=>{
      const current=r.symbol===state.symbol;
      const badges=r.periods?`<div class="yd-period-badges" role="group" aria-label="${esc(short(r.symbol))} 的检测周期">${r.periods.map(p=>{
        const status=p.availability==='insufficient_history'?'历史不足':p.error?'未能检测':p.count+' 个形态';
        return `<button type="button" data-yd-pair-symbol="${esc(r.symbol)}" data-yd-pair-period="${esc(p.timeframe)}" class="${p.count?'has-shape':''}" aria-pressed="${current&&p.timeframe===state.timeframe}" aria-label="查看 ${esc(short(r.symbol))} ${esc(periods[p.timeframe])} K 线，${status}" title="${esc(periods[p.timeframe])} · ${status}">${p.timeframe==='1Dutc'?'日线':esc(p.timeframe)}${p.error||p.count?`<b aria-hidden="true">${p.error?'!':p.count}</b>`:''}</button>`;
      }).join('')}</div>`:'';
      return `<div class="yd-symbol ${current?'selected':''}"><button type="button" data-yd-symbol="${esc(r.symbol)}" class="yd-symbol-main" aria-pressed="${current}"><strong>${favorites.includes(r.symbol)?'★ ':''}${esc(short(r.symbol))}</strong><span class="yd-symbol-meta" title="${r.count?'确认 '+fmt(r.latest_detection_ms):''}">${r.availability==='insufficient_history'?'历史不足':r.error?'读取失败':r.count?(state.symbolOrder==='score'?((r.max_confidence||0)*100).toFixed(1)+'%':fmt(r.latest_detection_ms).slice(6)):'未检出'}${!r.error&&!r.count&&r.raw_count?' · 原始框':''}</span></button>${badges}<div class="stitch-detection-meta"><span>${r.count?esc(r.count)+" 个形态":"等待形态"}</span><span>${r.count&&Number.isFinite(r.max_confidence)?"模型分数 "+(r.max_confidence*100).toFixed(1)+"%":""}</span></div></div>`;
    }).join(''):`<div class="yd-empty"><p>${state.loadingJob?'正在读取…':state.resultFilter==='favorites'?'本次结果中还没有符合搜索的关注币种。点图表标题旁的“关注”，以后就更好找。':all.length?'没有符合条件的币种':terminal.has(state.job?.status)?'本次没有可浏览的币种':'结果会在检测完成后出现'}</p>${all.length?`<button data-yd-clear-search>${state.resultFilter==='all'?'清空搜索':state.symbolSearch?'在全部币种中搜索':'查看全部币种'}</button>`:''}</div>`);
    if(focus){const selector=focus.ydPairPeriod?`[data-yd-pair-symbol="${CSS.escape(focus.ydPairSymbol)}"][data-yd-pair-period="${CSS.escape(focus.ydPairPeriod)}"]`:focus.ydSymbol?`[data-yd-symbol="${CSS.escape(focus.ydSymbol)}"]`:null;if(selector)root.querySelector(selector)?.focus({preventScroll:true});}
    html('yd-quick-symbol',(!rows.some(r=>r.symbol===state.symbol)&&state.symbol?`<option value="${esc(state.symbol)}">${esc(short(state.symbol))} · 当前查看</option>`:'')+rows.map(r=>`<option value="${esc(r.symbol)}">${esc(short(r.symbol))}${r.count?' · '+r.count+' 形态':''}</option>`).join(''));$('yd-quick-symbol').value=state.symbol;
    const index=rows.findIndex(r=>r.symbol===state.symbol);$('yd-symbol-index').textContent=index<0?'':`${index+1} / ${rows.length}`;
    $('yd-prev').disabled=index<=0;$('yd-next').disabled=index<0||index>=rows.length-1;
  }
  async function moveSymbol(offset,focus=false){const rows=symbolRows(),index=rows.findIndex(r=>r.symbol===state.symbol),next=rows[index+offset];if(next){await chooseSymbol(next.symbol);if(focus&&state.active&&state.tab==='results'&&state.symbol===next.symbol)root.querySelector(`[data-yd-symbol="${CSS.escape(next.symbol)}"]`)?.focus({preventScroll:true});}}
  async function chooseSymbolPeriod(symbol,timeframe){
    // Resolve the tuple once; selecting the coin first could load its previous period.
    if(!periodResult(state.latest,symbol,timeframe))return;
    const mobile=$('yd-symbol-dialog').open;
    state.overview=true;state.timeframe=timeframe;state.boxes='structure';state.side='all';
    $('yd-boxes').value='structure';$('yd-side').value='all';
    const loading=chooseSymbol(symbol,'',true);
    if(mobile){$('yd-chart-title').focus({preventScroll:true});root.querySelector('.yd-chart-stage').scrollIntoView({block:'start'});}
    summary();await loading;
  }
  function drawPeriods(){
    const focused=document.activeElement?.dataset.ydPeriod;
    const tf=state.overview?state.timeframe:state.job?.spec.timeframe;
    html('yd-periods',Object.keys(periods).map(p=>{const row=periodResult(state.latest,state.symbol,p),current=p===tf;return `<button data-yd-period="${p}" aria-pressed="${current}" title="${periods[p]}${row?' · '+(row.error?'未能检测':row.count+' 个形态'):' · 尚无检测结果'}" ${!state.symbol?'disabled':''}>${p==='1Dutc'?'日线':p}${row?.count?'<i></i>':''}</button>`;}).join(''));
    if(focused)$('yd-periods').querySelector(`[data-yd-period="${CSS.escape(focused)}"]`)?.focus({preventScroll:true});
  }
  async function switchPeriod(timeframe){
    const version=++requestVersion,symbol=state.symbol;
    state.overview=true;state.timeframe=timeframe;state.fromPatrol=false;$('yd-patrol-return').hidden=true;state.viewTouched=false;
    drawPeriods();
    if(!state.latest.length)await refreshLatest(false);
    // The catalog can resolve after a later period, symbol, tab, or route selection.
    if(version!==requestVersion||!state.active||state.tab!=='results'||state.timeframe!==timeframe||state.symbol!==symbol)return;
    if(periodResult(state.latest,state.symbol,timeframe))await chooseSymbol(state.symbol);
    else{tailVersion++;state.loadingJob=false;state.loadingResult=false;state.selected=null;state.page=0;state.focusBox=null;state.result=null;state.tail=null;state.job=null;destroyChart();emptyChart('这个周期还没有检测结果。可点击“检测最新行情”创建检测。');$('yd-chart-title').textContent=short(state.symbol)+' / USDT';$('yd-chart-sub').textContent='尚未检测';state.tailError='';state.tailLoading=false;tailStatus();drawBoxes();drawSymbols();syncRoute();if(['15m','30m','1H','4H'].includes(timeframe))await previewChart(state.symbol,timeframe);}
    summary();
  }
  async function previewChart(symbol,timeframe,keep=false){
    const version=requestVersion;state.tailLoading=true;state.tailChecked=Date.now();tailStatus();
    try{
      const path='/api/chart?'+new URLSearchParams({symbol,timeframe});
      const data=requests?await requests.json(path):await fetch(path).then(r=>{if(!r.ok)throw Error('该周期行情尚未就绪');return r.json();});
      if(version!==requestVersion||!state.active||!state.overview||state.symbol!==symbol||state.timeframe!==timeframe)return;
      if(data.symbol!==symbol||data.timeframe!==timeframe||!data.candles?.length)throw Error('该周期尚无可用行情');
      const candles=data.candles.map(r=>({...r,open:r.o,high:r.h,low:r.l,close:r.c,volume:r.v}));
      state.result={symbol,timeframe,mode:'preview',preview:true,stale:data.state?.stale,candles,episodes:[],raw_detections:[],cutoff_ms:candles.at(-1).t+steps[timeframe]};
      state.follow=true;$('yd-follow').checked=true;state.tailError='';
      await renderChart(keep&&chart?chart.timeScale().getVisibleLogicalRange():null);drawBoxes();
    }catch(e){if(version===requestVersion){state.tailError='行情预览暂不可用：'+e.message;if(!state.result)emptyChart(state.tailError);}}
    finally{if(version===requestVersion){state.tailLoading=false;tailStatus();}}
  }
  async function refreshLatest(select=true){
    const epoch=activationVersion,version=requestVersion,data=await api('/latest?if_revision='+encodeURIComponent(state.latestRevision||''));
    if(!state.active||epoch!==activationVersion)return;
    if(!data.unchanged){const merged=new Map((data.partial?state.latest:[]).map(r=>[r.symbol+':'+r.timeframe,r]));for(const r of data.items)merged.set(r.symbol+':'+r.timeframe,r);state.latest=[...merged.values()];state.latestRevision=data.revision;}
    if(!state.overview||!select||version!==requestVersion||state.tab!=='results')return;
    const rows=allSymbolRows();if(!rows.some(r=>r.count>0))state.resultFilter='all';
    const symbol=initialSymbol(filteredSymbols(rows,'','all',favorites,state.symbolOrder),state.symbol);
    const row=periodResult(state.latest,symbol,state.timeframe);
    if(symbol&&(!state.timeframe||row)&&(!state.job||state.symbol!==symbol||row&&row.job_id!==state.job.id&&!state.viewTouched)&&!state.loadingResult)await chooseSymbol(symbol);
    if(symbol&&state.timeframe&&!row&&!state.result&&!state.tailLoading&&Date.now()-state.tailChecked>=30000&&['15m','30m','1H','4H'].includes(state.timeframe))await previewChart(symbol,state.timeframe);
    if(!rows.length){emptyChart('还没有检测结果，可新建检测或等待巡检完成。');}
  }
  async function refreshFeed(){
    if(!state.active||state.tab!=='schedules')return;
    const version=++feedVersion,params=new URLSearchParams({days:state.feedDays,q:state.feedQuery,timeframe:state.feedPeriod,side:state.feedSide,sort:state.feedOrder,limit:state.feedLimit,offset:(state.feedPage||0)*state.feedLimit});
    if(state.feedPage>0&&state.feedBefore)params.set('before_ms',state.feedBefore);
    try{
      const data=await api('/discoveries?'+params);if(version!==feedVersion||!state.active)return;
      if(state.feedPage>0&&state.feedPage*state.feedLimit>=data.total){state.feedPage=Math.max(0,Math.ceil(data.total/state.feedLimit)-1);return refreshFeed();}
      state.feed=data;state.feedBefore=data.before_ms;error('','yd-feed-error');drawFeed();
    }catch(e){if(version===feedVersion){error('发现列表更新失败，请点击“刷新发现”重试。'+e.message,'yd-feed-error');$('yd-feed-updated').textContent=state.feed?'更新中断 · 下方保留上次成功读取的列表':'';if(!state.feed)$('yd-feed-count').textContent='暂时无法读取发现';}}
  }
  function drawFeed(){
    const data=state.feed;if(!data)return;const j=data.latest_job,progress=j?.progress||{};
    const activePlans=state.schedules.filter(s=>s.enabled).length,online=activePlans>0;
    html('yd-patrol-status',`<div><strong>${online?activePlans+' 个巡检计划已启用':state.schedules.some(s=>s.validation)?'正在验证巡检配置，通过后自动启用':'当前没有启用的巡检计划'}</strong><span>${j?`最近一轮 ${fmt(j.created_ms)} · <span class="yd-status ${j.status}">${names[j.status]}</span> · 已处理 ${progress.completed||0}/${progress.total||'—'} 币${progress.unavailable?' · '+progress.unavailable+' 币历史不足':''}${progress.failed?' · '+progress.failed+' 币失败':''}`:'启用巡检后，发现的形态会自动出现在这里。'}</span>${j?.error?`<span class="yd-error-text">${esc(j.error)}</span>`:''}${state.worker?.state==='cooling'?`<span class="yd-error-text">${esc(state.worker.reason)}</span>`:''}</div>${j?`<button data-yd-job="${j.id}">${['failed','partial','interrupted'].includes(j.status)?'查看本轮异常':'查看本轮进度'}</button>`:''}`);
    $('yd-feed-count').textContent=`${data.total} 处形态 · ${state.feedDays?'最近 '+state.feedDays+' 天':'全部发现'} · ${{newest:'首次发现从新到旧',score:'模型分数从高到低',symbol:'币种 A → Z'}[state.feedOrder]}`;
    $('yd-feed-updated').textContent=`${fmt(data.now_ms)} 更新 · 每 5 秒刷新`;
    const focused=document.activeElement?.dataset?.ydFinding;
    html('yd-findings',data.items.length?data.items.map(r=>`<button class="yd-finding ${r.side}" data-yd-finding="${esc(r.id)}" aria-label="查看 ${esc(short(r.symbol))} ${periods[r.timeframe]} ${r.side==='long'?'多头':'空头'}形态，发现于 ${fmt(r.first_observed_ms)}"><span class="yd-finding-head"><strong>${esc(short(r.symbol))}<small> / USDT</small></strong><span>${periods[r.timeframe]}</span></span><span class="yd-finding-direction">${r.side==='long'?'↗ 多头密集':'↘ 空头密集'}</span><span class="yd-confidence-label">模型分数 <b>${(r.confidence*100).toFixed(1)}%</b></span><span class="yd-confidence-track" aria-hidden="true"><i style="width:${Math.max(0,Math.min(100,r.confidence*100))}%"></i></span><span class="yd-finding-time">首次发现 <b>${fmt(r.first_observed_ms)}</b></span><span class="yd-finding-details" title="${esc(state.catalog.items.find(m=>m.id===r.model_id)?.name||r.model_id)} · 最近检出 ${fmt(r.last_observed_ms)}">确认 ${fmt(r.detected_at_ms)} · ${r.core_bars} 根${r.scan_count>1?' · '+r.scan_count+' 轮':''}<b aria-hidden="true">↗</b></span></button>`).join(''):`<div class="yd-empty yd-card"><h3>${!j?'还没有巡检发现':'这个范围内暂未发现形态'}</h3><p>${!j?'添加巡检后，结果会自动汇集到这里。':!terminal.has(j.status)?'本轮还在检测，已完成币种的形态会陆续出现。':'可以扩大时间范围或清除筛选，查看之前的发现。'}</p>${j?'<button data-yd-feed-clear>查看全部发现</button>':'<button class="yd-primary" data-yd-plan-new>添加巡检</button>'}</div>`);
    if(focused)root.querySelector(`[data-yd-finding="${CSS.escape(focused)}"]`)?.focus({preventScroll:true});
    const pages=Math.max(1,Math.ceil(data.total/state.feedLimit)),page=state.feedPage||0;
    const focusPage=document.activeElement?.dataset?.ydFeedPage,focusSize=document.activeElement?.hasAttribute('data-yd-feed-size');
    const numbers=[...new Set([0,...Array.from({length:5},(_,i)=>page+i-2).filter(i=>i>=0&&i<pages),pages-1])].sort((a,b)=>a-b);
    html('yd-feed-more',`<nav aria-label="巡检形态分页"><button data-yd-feed-page="${page-1}" ${page===0?'disabled':''}>上一页</button>${numbers.map((n,i)=>(i&&n>numbers[i-1]+1?'<span aria-hidden="true">…</span>':'')+`<button data-yd-feed-page="${n}" ${n===page?'aria-current="page"':''}>${n+1}</button>`).join('')}<button data-yd-feed-page="${page+1}" ${page+1>=pages?'disabled':''}>下一页</button></nav><label>每页 <select data-yd-feed-size aria-label="每页形态数">${[12,24,48].map(n=>`<option value="${n}" ${n===state.feedLimit?'selected':''}>${n}</option>`).join('')}</select></label><span>${data.total?page*state.feedLimit+1:0}–${page*state.feedLimit+data.items.length} / ${data.total}</span>`);
    if(focusSize)root.querySelector('[data-yd-feed-size]')?.focus({preventScroll:true});
    else if(focusPage!==undefined)root.querySelector(`[data-yd-feed-page="${focusPage}"]:not(:disabled)`)?.focus({preventScroll:true});
  }
  function jobsView(worker,clock){
    state.worker=worker;
    $('yd-worker').textContent=worker&&clock-worker.heartbeat_ms<180000&&worker.state!=='stopped'?'检测服务在线':'提交后启动检测服务';
    html('yd-jobs-list',state.jobs.length?`<div class="yd-table-wrap"><table><thead><tr><th>创建时间 · 北京</th><th>检测内容</th><th>进度</th><th>状态</th><th></th></tr></thead><tbody>${state.jobs.map(j=>`<tr class="${state.job?.id===j.id?'yd-current':''}"><td><button data-yd-job="${j.id}">${fmt(j.created_ms)}</button></td><td>${scopeName(j.spec)} · ${periods[j.spec.timeframe]}<small>${j.spec.mode==='latest'?'当前盘口':'历史识别'}${j.schedule_id?' · 自动巡检':''}</small></td><td>${j.progress.completed||0} / ${j.progress.total||'—'}</td><td><span class="yd-status ${j.status}">${names[j.status]}</span>${j.error?`<small>${esc(j.error)}</small>`:''}</td><td><button data-yd-job="${j.id}">看结果</button>${!terminal.has(j.status)?` <button data-yd-cancel="${j.id}" ${j.cancel?'disabled':''}>取消</button>`:''}</td></tr>`).join('')}</tbody></table></div>`:'<p class="yd-empty">还没有检测记录。新建一次检测即可开始。</p>');
    html('yd-jobs-pages',state.jobTotal>20?`<button data-yd-jobs-page="-1" ${state.jobOffset===0?'disabled':''}>较新记录</button><span>${state.jobOffset+1}–${Math.min(state.jobOffset+20,state.jobTotal)} / ${state.jobTotal}</span><button data-yd-jobs-page="1" ${state.jobOffset+20>=state.jobTotal?'disabled':''}>更早记录</button>`:'');
    $('yd-plan-count').textContent='';$('yd-plan-summary-count').textContent=state.schedules.length+' 个';
    html('yd-schedules',state.schedules.length?state.schedules.map(s=>`<article class="yd-plan-card"><div><span class="yd-status ${s.enabled?'completed':'queued'}">${s.enabled?'已启用':s.validation?'验证中 · 通过后启用':'已暂停'}</span><h3>${scopeName(s.spec)} · ${periods[s.spec.timeframe]}</h3><p>每 ${s.interval_minutes>=1440?s.interval_minutes/1440+' 天':s.interval_minutes>=60?s.interval_minutes/60+' 小时':s.interval_minutes+' 分钟'}扫描${s.enabled?' · 下次 '+fmt(s.next_due_ms):''}</p><p>${esc(patrolProgress(s))}</p><small>${s.incremental?'增量补扫 · 自动续接 · 短周期优先':'常规扫描'} · ${esc(state.catalog.items.find(m=>m.id===s.spec.model_id)?.name)}</small>${s.error?`<p class="yd-error-text">${esc(s.error)}</p>`:''}</div><div>${s.last_job_id?`<button data-yd-job="${s.last_job_id}">查看最近结果</button>`:''}<button data-yd-stop="${s.id}">${s.enabled?'停止巡检':'移除计划'}</button></div></article>`).join(''):'<div class="yd-empty yd-card"><h3>让模型定时查看行情</h3><p>选择币种范围、K 线周期和扫描频率，结果自动保存。</p><button class="yd-primary" data-yd-plan-new>添加第一个巡检</button></div>');
  }
  async function chooseJob(id,preferred='',tab='results',focusBox='',fromPatrol=false){
    state.overview=false;state.fromPatrol=fromPatrol;state.focusBox=focusBox;$('yd-patrol-return').hidden=!fromPatrol;
    const previousTab=state.tab;const version=++requestVersion;state.attempted=true;state.loadingJob=true;state.loadingResult=false;state.job=null;state.result=null;state.tail=null;tailVersion++;state.tailLoading=false;state.symbol='';state.selected=null;state.symbolSearch='';$('yd-result-search').value='';
    if($('yd-inspection').open)closeDialog('yd-inspection');setTab(tab,false);if(previousTab!==tab)root.scrollIntoView({block:'start'});destroyChart();$('yd-chart-title').textContent='等待检测结果';$('yd-chart-sub').textContent='';emptyChart('正在打开检测记录…');summary();drawSymbols();drawBoxes();error('');
    try{
      const job=await api('/jobs/'+id);if(version!==requestVersion||!state.active)return;
      state.job=job;state.resultFilter=job.results.some(r=>r.count>0)?'detected':'all';
      if(preferred&&job.results.some(r=>r.symbol===preferred&&!r.count))state.resultFilter='all';
      summary();drawSymbols();const first=initialSymbol(filteredSymbols(job.results,'','all',favorites,state.symbolOrder),preferred);
      if(first){await chooseSymbol(first,focusBox);if(fromPatrol&&state.job?.id===id){state.overview=true;state.viewTouched=true;await refreshLatest(false);summary();drawSymbols();syncRoute();}}else{$('yd-chart-title').textContent=terminal.has(job.status)?'本次检测未生成图表':'等待检测结果';emptyChart(job.error||'检测结果将逐币出现，可先查看进度。');syncRoute();}
    }catch(e){if(version===requestVersion){error(e.message);emptyChart('无法打开这条记录，请从“检测记录”选择其他任务。');}}
    finally{if(version===requestVersion){state.loadingJob=false;drawSymbols();}}
  }
  async function chooseSymbol(symbol,focusBox='',focusLatest=false){
    if(state.overview){
      const available=state.latest.filter(r=>r.symbol===symbol),same=periodResult(available,symbol,state.timeframe);
      const row=same||available.find(r=>r.count>0)||available[0];
      if(!row)return;state.timeframe=row.timeframe;
      state.job={id:row.job_id,status:'completed',created_ms:row.created_ms,progress:{},spec:{mode:'latest',scope:'selected',symbols:[symbol],timeframe:row.timeframe,model_id:row.model_id,end_ms:row.cutoff_ms},results:[]};
      state.fromPatrol=false;$('yd-patrol-return').hidden=true;
    }
    state.tail=null;state.viewTouched=false;state.tailChecked=0;state.tailError='';state.tailLoading=false;tailVersion++;state.follow=state.job?.spec.mode==='latest';$('yd-follow').checked=state.follow;if($('yd-symbol-dialog').open)closeDialog('yd-symbol-dialog');
    state.focusBox=focusBox;if(focusBox){state.boxes='structure';state.side='all';$('yd-boxes').value='structure';$('yd-side').value='all';}
    const id=state.job?.id;if(!id)return;const version=++requestVersion;state.loadingJob=false;state.loadingResult=true;state.symbol=symbol;state.result=null;state.selected=null;state.page=0;
    if(!symbolRows().some(r=>r.symbol===symbol)&&allSymbolRows().some(r=>r.symbol===symbol))state.resultFilter='all';
    root.querySelector('.yd-browser').classList.remove('is-symbols-open');syncSymbolPanel();
    destroyChart();$('yd-chart-title').textContent=short(symbol)+' / USDT';$('yd-chart-sub').textContent='正在加载检测快照…';emptyChart('正在加载 '+short(symbol)+' 的 K 线…');tailStatus();drawBoxes();drawSymbols();syncRoute();error('');
    try{
      const key=id+':'+symbol;let result=resultCache.get(key);if(!result){result=await api(`/jobs/${id}/results/${encodeURIComponent(symbol)}`);resultCache.set(key,result);if(resultCache.size>32)resultCache.delete(resultCache.keys().next().value);}
      if(version!==requestVersion||!state.active)return;
      if(result.error){const unavailable=result.availability==='insufficient_history';$('yd-chart-sub').textContent=unavailable?'上市历史不足，暂不能检测':'该币种检测失败';emptyChart(result.error+(unavailable?'。达到所需历史后，将在后续巡检自动检测。':''));return;}
      state.result=result;tailStatus();if(focusLatest)state.focusBox=boxes()[0]?.id||null;if(focusBox&&!result.episodes.some(b=>b.id===focusBox)&&result.raw_detections.some(b=>b.id===focusBox)){state.boxes='raw';$('yd-boxes').value='raw';}await renderChart();if(version!==requestVersion)return;if(focusBox&&!boxes().some(b=>b.id===focusBox))error('这处形态已无法定位，当前展示该币种的其他结果。');drawBoxes();syncRoute();refreshTail();
    }catch(e){if(version===requestVersion){$('yd-chart-sub').textContent='图表未能加载，请重新选择币种重试';emptyChart('图表加载失败：'+e.message);error(e.message);}}
    finally{if(version===requestVersion){state.loadingResult=false;drawSymbols();}}
  }
  function boxes(){const r=state.result;return(r?(state.boxes==='raw'?r.raw_detections:r.episodes):[]).filter(b=>state.side==='all'||b.side===state.side).sort((a,b)=>b.detected_at_ms-a.detected_at_ms);}
  function listBoxes(){
    const rows=boxes();$('yd-box-count').textContent=rows.length;$('yd-focus').disabled=!rows.length;
    const longs=rows.filter(b=>b.side==='long').length,shorts=rows.length-longs;
    html('yd-insight',state.result?`<div><strong>${rows.length?`找到 ${rows.length} 处${state.boxes==='raw'?'模型提案':'密集形态'}`:'这段行情暂无符合筛选的形态'}</strong><span>${rows.length?[longs?'多头 '+longs+' 处':'',shorts?'空头 '+shorts+' 处':''].filter(Boolean).join(' · '):'可切换周期或币种'}</span></div><p>${rows.length?'绿色框为多头，红色框为空头。点击下方形态可定位，查看原图可核对。':''}${!rows.length&&state.result.raw_detections.length?` <button data-yd-show-raw>查看 ${state.result.raw_detections.length} 个原始提案</button>`:''}</p>`:'');
    const page=rows.slice(state.page*20,(state.page+1)*20);
    const selected=rows.find(b=>b.id===state.selected)||rows[0];
    $('yd-insight').hidden=!!selected&&!state.result?.preview;
    html('yd-box-list',page.length?page.map(b=>`<article class="yd-detection ${b.side} ${state.selected===b.id?'selected':''}"><button class="yd-box-focus" data-yd-box="${b.id}" aria-pressed="${state.selected===b.id}"><b>${b.side==='long'?'多头':'空头'}密集</b><span>确认 ${fmt(b.detected_at_ms)}</span><span class="yd-score" title="模型置信度，不是交易胜率"><small>模型分数</small>${(b.confidence*100).toFixed(1)}%</span></button><button class="yd-input-link" data-yd-input="${b.id}">查看原图</button><small>密集 ${b.core_bars} 根 · 之后 ${b.post_bars} 根确认${!b.structural_pass?' · 未通过结构筛选':''}</small></article>`).join('')+(rows.length>20?`<div class="yd-pagination"><button data-yd-page="-1" ${state.page===0?'disabled':''}>上一页</button><span>${state.page+1}/${Math.ceil(rows.length/20)}</span><button data-yd-page="1" ${(state.page+1)*20>=rows.length?'disabled':''}>下一页</button></div>`:''):`<p class="yd-empty">${state.result?(state.result.raw_detections.length?'暂无符合筛选条件的形态，可切换“全部原始框”查看提案。':'这段行情未检出形态。可以换周期检测，或切换其他币种。'):'选择检测结果后，这里显示识别到的形态。'}</p>`);
    if(state.result?.preview){$('yd-box-count').textContent='—';html('yd-insight','<strong>行情预览 · 这个周期尚未检测</strong><p>复用监控已保存的 K 线与均线；检测后才会显示识别框。</p>');html('yd-box-list','<p class="yd-empty">尚未运行 YOLO 检测，不能据此判断有无形态。</p>');}
    $('yd-export-json').disabled=!state.result||state.result.preview;$('yd-export-png').disabled=!chart;
  }
  function drawBoxes(){listBoxes();if(primitive){primitive.boxes=boxes();primitive.request?.();}}
  function selectBox(id,focus=true){
    const b=boxes().find(b=>b.id===id);if(!b)return;state.selected=id;state.focusBox=null;chartSelection=id;state.page=Math.floor(boxes().findIndex(b=>b.id===id)/20);drawBoxes();syncRoute();
    const first=state.result.candles[0].t,step=steps[state.result.timeframe];if(focus)chart?.timeScale().setVisibleLogicalRange(shapeRange(b,first,step));
  }
  function openInspection(id){
    const b=boxes().find(b=>b.id===id);if(!b)return;selectBox(id);
    const url=`${BASE}/jobs/${state.job.id}/results/${encodeURIComponent(state.symbol)}/input/${b.id}`;
    $('yd-input-heading').textContent=`${short(state.symbol)} · ${b.side==='long'?'多头':'空头'}密集`;
    html('yd-input-content',`<p class="yd-input-summary">模型分数 ${(b.confidence*100).toFixed(1)}% · 确认时间 ${fmt(b.detected_at_ms,true)} 北京</p><div class="yd-inspect-grid"><figure><img src="${url}?annotated=true" alt="模型原始预测框"><figcaption>原始预测框</figcaption></figure><figure><img src="${url}" alt="模型实际看到的无标注输入"><figcaption>模型实际输入 · 1280 × 742</figcaption></figure></div><dl><div><dt>密集核心</dt><dd>${fmt(b.core_start_ms)} — ${fmt(b.core_end_ms)} · ${b.core_bars} 根</dd></div><div><dt>核心后的确认</dt><dd>${b.post_bars} 根 K 线</dd></div><div><dt>完整检测窗</dt><dd>${fmt(b.window_start_ms)} — ${fmt(b.detected_at_ms)}</dd></div><div><dt>首次确认</dt><dd>${fmt(b.first_detected_at_ms||b.detected_at_ms)}${b.observations>1?' · 合并 '+b.observations+' 次检出':''}</dd></div><div><dt>模型与输入</dt><dd>${esc(state.result.model_id)} · ${esc(state.result.ma_source)}</dd></div><div><dt>权重标识</dt><dd>${esc(state.result.model_sha256.slice(0,12))}</dd></div></dl><p class="yd-help">核心位置表示形态在哪里；确认时间表示模型在当时已有足够 K 线看见这个框。</p>`);
    $('yd-input-content').querySelectorAll('img').forEach(img=>img.onerror=()=>{const p=document.createElement('p');p.className='yd-error-text';p.textContent='原图读取失败，关闭后重试。';img.replaceWith(p);});
    showDialog('yd-inspection');
  }
  function chartLibrary(){return chartTools.library();}
  async function openFindingPreview(item){
    const dialog=$('yd-preview-dialog');
    previewRequest?.abort();previewResize?.disconnect();previewChartInstance?.remove();previewResize=previewChartInstance=null;
    findingPreview=item;const version=++previewVersion,controller=previewRequest=new AbortController();
    const current=()=>version===previewVersion&&dialog.open&&state.active;
    $('yd-preview-title').textContent=`${short(item.symbol)} / USDT · ${periods[item.timeframe]}`;
    $('yd-preview-context').textContent=`${item.side==='long'?'多头':'空头'}密集 · 模型分数 ${(item.confidence*100).toFixed(1)}%`;
    $('yd-preview-confirmation').textContent=`形态确认 ${fmt(item.detected_at_ms,true)} 北京`;
    $('yd-preview-legend').innerHTML='';
    const message=$('yd-preview-message');message.hidden=false;message.textContent='正在读取检测快照…';
    dialog.querySelector('[data-yd-preview-retry]').hidden=true;
    if(!dialog.open)showDialog('yd-preview-dialog');
    const timeout=setTimeout(()=>controller.abort(),20000);
    try{
      const key=item.job_id+':'+item.symbol;
      let result=resultCache.get(key);
      if(!result){const r=await fetch(`${BASE}/jobs/${item.job_id}/results/${encodeURIComponent(item.symbol)}`,{signal:controller.signal,cache:'no-store'});if(!r.ok)throw Error('检测快照暂不可用');result=await r.json();}
      if(!current())return;
      if(result.error||result.symbol!==item.symbol||result.timeframe!==item.timeframe||!result.candles?.length)throw Error('检测快照不完整，请重新加载');
      const box=[...(result.episodes||[]),...(result.raw_detections||[])].find(b=>b.id===item.detection_id);
      if(!box)throw Error('该形态已无法定位，请在完整图表中核对');
      resultCache.set(key,result);if(resultCache.size>32)resultCache.delete(resultCache.keys().next().value);
      await chartLibrary();if(!current())return;
      const rows=result.candles,host=$('yd-preview-chart'),style=getComputedStyle(document.documentElement),step=steps[result.timeframe];
      const c=previewChartInstance=window.LightweightCharts.createChart(host,{...chartTools.interaction,width:host.clientWidth,height:host.clientHeight,layout:{background:{color:style.getPropertyValue('--chart-bg').trim()},textColor:style.getPropertyValue('--chart-text').trim(),fontSize:10},grid:{vertLines:{visible:false},horzLines:{color:style.getPropertyValue('--chart-grid').trim()}},rightPriceScale:{scaleMargins:{top:.12,bottom:.2}},timeScale:{timeVisible:true,secondsVisible:false},localization:{locale:'zh-CN',priceFormatter:chartTools.axisPrice,timeFormatter:t=>fmt(t*1000,true)}});
      const precision=chartTools.priceFormat(rows).precision;
      const candles=c.addCandlestickSeries({upColor:'#01e599',downColor:'#ff4f73',borderVisible:false,wickUpColor:'#01e599',wickDownColor:'#ff4f73',priceFormat:{type:'price',precision,minMove:10**-precision}});
      candles.setData(rows.map(r=>({time:r.t/1000,open:r.open,high:r.high,low:r.low,close:r.close})));
      for(const [key,color] of Object.entries(colors)){const line=c.addLineSeries({color,lineWidth:1,lastValueVisible:false,priceLineVisible:false,crosshairMarkerVisible:false});line.setData(rows.filter(r=>Number.isFinite(r[key])).map(r=>({time:r.t/1000,value:r[key]})));}
      const vol=c.addHistogramSeries({priceScaleId:'volume',priceFormat:{type:'volume'},lastValueVisible:false,priceLineVisible:false});vol.priceScale().applyOptions({scaleMargins:{top:.85,bottom:0}});vol.setData(rows.map(r=>({time:r.t/1000,value:r.volume,color:r.close>=r.open?'#239f854d':'#d965754d'})));
      candles.attachPrimitive(new DetectionPrimitive([box],rows[0].t,step));
      const confirm=box.detected_at_ms-step;
      if(rows.some(r=>r.t===confirm))candles.setMarkers([{time:confirm/1000,position:'aboveBar',color:box.side==='long'?'#01e599':'#ff4f73',shape:'arrowDown',text:'确认'}]);
      c.timeScale().setVisibleLogicalRange(shapeRange(box,rows[0].t,step));
      const lookup=new Map(rows.map(r=>[r.t/1000,r]));
      const legend=row=>{$('yd-preview-legend').innerHTML=chartStyle.legend(row);};legend(rows.at(-1));
      c.subscribeCrosshairMove(p=>{if(current())legend(lookup.get(p.time)||rows.at(-1));});
      previewResize=chartTools.resize(c,host);message.hidden=true;
    }catch(e){if(current()){message.textContent=e.name==='AbortError'?'图表读取超时，请重新加载':e.message;dialog.querySelector('[data-yd-preview-retry]').hidden=false;}}
    finally{clearTimeout(timeout);}
  }
  class DetectionPrimitive{
    constructor(rows,first,step){this.boxes=rows;this.first=first;this.step=step;this.view={zOrder:()=> 'top',renderer:()=>({draw:target=>target.useMediaCoordinateSpace(({context:ctx})=>this.draw(ctx))})};}
    attached({chart,series,requestUpdate}){this.chart=chart;this.series=series;this.request=requestUpdate;}
    detached(){this.request=null;}
    paneViews(){return [this.view];}
    // The bundled 4.2 build returns 0 for fractional logical indices. Interpolate
    // adjacent bar centers to preserve the model's exact sub-bar box boundaries.
    x(ms){const index=(ms-this.first)/this.step,left=Math.floor(index),scale=this.chart.timeScale();const a=scale.logicalToCoordinate(left),b=scale.logicalToCoordinate(left+1);return a==null||b==null?null:a+(b-a)*(index-left);}
    rect(b){return [this.x(b.x_start_ms),this.series.priceToCoordinate(b.price_high),this.x(b.x_end_ms),this.series.priceToCoordinate(b.price_low)];}
    draw(ctx){for(const b of this.boxes){const [x,y,x2,y2]=this.rect(b);if([x,y,x2,y2].some(v=>v==null)||x2<0||x>this.chart.timeScale().width())continue;const color=b.side==='long'?'#01e599':'#ff4f73';ctx.save();ctx.fillStyle=b.side==='long'?'rgba(35,159,133,.09)':'rgba(217,101,117,.09)';ctx.strokeStyle=color;ctx.lineWidth=state.selected===b.id?2.5:1.2;ctx.setLineDash(b.structural_pass?[]:[4,3]);ctx.fillRect(x,y,x2-x,y2-y);ctx.strokeRect(x,y,x2-x,y2-y);if(x2-x>35){ctx.fillStyle=color;ctx.font='11px system-ui';ctx.fillText(`${b.side==='long'?'L':'S'} ${(b.confidence*100).toFixed(0)}%`,Math.max(2,x+3),Math.max(12,y-4));}ctx.restore();}}
  }
  function destroyChart(){rememberChart();chartControls?.destroy();chartControls=null;hoverTime=null;crosshair.cancel();chartLookup.clear();displayRows=[];lastReadout='';lastMarkers='';clearTimeout(viewTimer);resize?.disconnect();resize=null;chart?.remove();chart=null;chartIdentity=null;chartSelection=null;series=null;primitive=null;maSeries=[];volume=null;}
  function tailStatus(){
    const tail=state.tail,original=state.result?.cutoff_ms;
    $('yd-tail-refresh').disabled=!state.result||state.tailLoading;$('yd-after').disabled=!state.tail?.candles.length;$('yd-follow').disabled=!state.result;
    $('yd-tail-status').classList.toggle('yd-error-text',!!state.tailError);
    $('yd-tail-status').textContent=!state.result?'尚无可更新的 K 线':state.tailError|| (state.result.preview?`监控行情至 ${fmt(original)} 收盘 · 尚未检测${state.result.stale?' · 等待更新':''}`:state.tailLoading?'正在更新行情…':tail?`行情至 ${fmt(tail.chart_cutoff_ms)} 收盘 · 增加 ${tail.candles.length} 根 · 识别框来自 ${fmt(original)}`:state.follow?'等待更新行情':'检测时快照 · '+fmt(original));
  }
  function paintChartReadout(row){
    if(!row||!$('yd-six-ma'))return;const key=[hoverTime===row.t,row.t,row.open,row.high,row.low,row.close,state.ma,...Object.keys(colors).map(k=>row[k])].join('|');if(key===lastReadout)return;lastReadout=key;
    $('yd-six-ma').innerHTML=chartStyle.legend(row);$('yd-six-ma').hidden=!state.ma;
    $('yd-ohlc').innerHTML=chartTools.readout(row,row.t/1000,hoverTime!==row.t);
  }
  function updateChartData(){
    if(!chart||!state.result)return;
    const rows=chartRows(state.result,state.tail);
    if(rows.length===displayRows.length&&rows.every((r,i)=>perf.samePoint(r,displayRows[i])))return;
    const range=chartTools.anchor(chart.timeScale().getVisibleLogicalRange(),chartTools.times(displayRows));displayRows=rows;chartLookup=new Map(rows.map(r=>[r.t,r]));
    syncSeries(series,rows.map(r=>({time:r.t/1000,open:r.open,high:r.high,low:r.low,close:r.close})));
    Object.keys(colors).forEach((name,i)=>maSeries[i]&&syncSeries(maSeries[i],rows.filter(r=>Number.isFinite(r[name])).map(r=>({time:r.t/1000,value:r[name]}))));
    volume&&syncSeries(volume,rows.map(r=>({time:r.t/1000,value:r.volume,color:r.close>=r.open?'rgba(35,159,133,.3)':'rgba(217,101,117,.3)'})));
    const markers=state.tail?.candles.length?[{time:state.result.candles.at(-1).t/1000,position:'aboveBar',color:'#a18c57',shape:'circle',text:'原检测截止'}]:[],markerKey=JSON.stringify(markers);if(markerKey!==lastMarkers){series.setMarkers(markers);lastMarkers=markerKey;}
    paintChartReadout(chartLookup.get(hoverTime)||rows.at(-1));const retained=chartTools.restore(range,chartTools.times(rows));if(retained)chart.timeScale().setVisibleLogicalRange(retained);
  }
  async function refreshTail(force=false){
    if(!state.active||state.tab!=='results'||!state.result||!state.follow||state.tailLoading||!force&&Date.now()-state.tailChecked<30000)return;
    if(state.result.preview){await previewChart(state.symbol,state.timeframe,true);return;}
    const version=++tailVersion,result=state.result,id=state.job.id;state.tailChecked=Date.now();state.tailLoading=true;state.tailError='';tailStatus();
    try{
      const tail=await api(`/jobs/${id}/results/${encodeURIComponent(result.symbol)}/chart`);
      if(version!==tailVersion||result!==state.result||!state.active||!state.follow)return;
      const first=!state.tail;state.tail=tail;updateChartData();if(first&&!state.viewTouched&&tail.candles.length)followupRange();
    }catch(e){if(version===tailVersion&&result===state.result)state.tailError='未能更新，保留当前图表。'+e.message;}
    finally{if(version===tailVersion){state.tailLoading=false;tailStatus();}}
  }
  function emptyChart(message){$('yd-chart-empty').hidden=false;$('yd-chart-empty').textContent=message;}
  function followupRange(){const rows=chartRows(state.result,state.tail),b=boxes().find(b=>b.id===state.selected)||boxes()[0];if(chart&&rows.length)chart.timeScale().setVisibleLogicalRange({from:b?Math.max(0,(b.core_start_ms-rows[0].t)/steps[state.result.timeframe]-14):Math.max(0,rows.length-90),to:rows.length+3});}
  function recentRange(){const rows=chartRows(state.result,state.tail);if(chart&&rows.length)chart.timeScale().setVisibleLogicalRange({from:Math.max(0,rows.length-90),to:rows.length+3});}
  async function renderChart(savedRange=null){
    const result=state.result;await chartLibrary();if(result!==state.result||!state.active||state.tab!=='results')return;
    const rows=chartRows(result,state.tail);destroyChart();chartLookup=new Map(rows.map(r=>[r.t,r]));chartNeedsTheme=false;$('yd-chart-empty').hidden=true;
    chartIdentity=(result.preview?'preview:'+result.timeframe:state.job.id)+':'+result.symbol;const saved=chartViews.get(chartIdentity);savedRange=validView(savedRange)||chartTools.restore(saved?.anchor,chartTools.times(rows))||validView(saved?.range);
    const style=getComputedStyle(document.documentElement),bg=style.getPropertyValue('--chart-bg').trim(),text=style.getPropertyValue('--chart-text').trim(),grid=style.getPropertyValue('--chart-grid').trim();
    const host=$('yd-chart');chart=window.LightweightCharts.createChart(host,{width:host.clientWidth,height:host.clientHeight,layout:{background:{color:bg},textColor:text,fontSize:10,fontFamily:'JetBrains Mono, monospace'},grid:{vertLines:{color:grid,visible:false},horzLines:{color:grid,style:1}},rightPriceScale:{borderColor:grid,scaleMargins:{top:.1,bottom:.2}},timeScale:{borderColor:grid,timeVisible:true,secondsVisible:false,tickMarkFormatter:(t,type)=>tickFormats[type<3?0:1].format(new Date(t*1000))},localization:{locale:'zh-CN',priceFormatter:chartTools.axisPrice,timeFormatter:t=>fmt(t*1000,true)},crosshair:{mode:0},handleScroll:{vertTouchDrag:false,horzTouchDrag:true}});
    chart.applyOptions(chartTools.interaction);host.classList.add('cv-canvas');
    chartControls=chartTools.bind($('yd-chart-stage'),{chart:()=>chart,rows:()=>displayRows,canvas:host});
    const precision=chartTools.priceFormat(rows).precision;
    series=chart.addCandlestickSeries({upColor:'#01e599',downColor:'#ff4f73',borderVisible:false,wickUpColor:'#01e599',wickDownColor:'#ff4f73',priceFormat:{type:'price',precision,minMove:10**-precision}});
    syncSeries(series,rows.map(r=>({time:r.t/1000,open:r.open,high:r.high,low:r.low,close:r.close})));
    for(const [name,color] of Object.entries(colors)){const line=chart.addLineSeries({color,lineWidth:1,lastValueVisible:false,priceLineVisible:false,crosshairMarkerVisible:false,visible:state.ma});syncSeries(line,rows.filter(r=>Number.isFinite(r[name])).map(r=>({time:r.t/1000,value:r[name]})));maSeries.push(line);}
    volume=chart.addHistogramSeries({priceScaleId:'volume',priceFormat:{type:'volume'},lastValueVisible:false,priceLineVisible:false,visible:state.volume});volume.priceScale().applyOptions({scaleMargins:{top:.85,bottom:0}});syncSeries(volume,rows.map(r=>({time:r.t/1000,value:r.volume,color:r.close>=r.open?'rgba(35,159,133,.3)':'rgba(217,101,117,.3)'})));
    primitive=new DetectionPrimitive(boxes(),result.candles[0].t,steps[result.timeframe]);series.attachPrimitive(primitive);
    const target=boxes().find(b=>b.id===state.focusBox);if(target)savedRange=null;
    const selected=target||boxes().find(b=>b.id===(state.selected||saved?.selected))||boxes()[0];if(selected)selectBox(selected.id,!savedRange);else if(!savedRange)recentRange();
    if(savedRange)chart.timeScale().setVisibleLogicalRange(savedRange);
    chart.timeScale().subscribeVisibleLogicalRangeChange(()=>{clearTimeout(viewTimer);viewTimer=setTimeout(rememberChart,200);});
    chart.subscribeClick(p=>{if(!p.point||!primitive)return;const hit=boxes().find(b=>{const [x,y,x2,y2]=primitive.rect(b);return p.point.x>=x&&p.point.x<=x2&&p.point.y>=y&&p.point.y<=y2;});if(hit)openInspection(hit.id);});
    chart.subscribeCrosshairMove(p=>{hoverTime=p.seriesData.get(series)?p.time*1000:null;crosshair(hoverTime);});
    paintChartReadout(rows.at(-1));
    resize=chartTools.resize(chart,host);chartControls.update();
    $('yd-chart-title').textContent=`${short(result.symbol)} / USDT`;
    $('yd-chart-sub').textContent=result.preview?`行情预览 · ${fmt(result.cutoff_ms)} 北京收盘 · 尚未检测${result.stale?' · 等待监控更新':''}`:`${result.mode==='history'?'历史识别':'形态识别'} · 原检测截止 ${fmt(result.cutoff_ms)} 北京 · ${savedRange?'已恢复上次视角':boxes().length?'已定位密集区':'未发现符合筛选条件的形态'}${result.timeframe==='15m'?'':' · 跨周期研究'}`;
    updateChartData();tailStatus();
  }
  async function refresh(){
    if(!state.active||document.hidden||refreshing)return;refreshing=true;const epoch=activationVersion,routeAtStart=state.pendingRoute;
    try{
      if(!state.loaded){state.catalog=await api('/catalog');shell();const pool=$('yd-tab-schedules');pool.parentElement.prepend(pool);state.loaded=true;
        loadSymbols().catch(()=>{/* Submission retries and shows source failure explicitly. */});}
      const tab=state.pendingRoute?.tab||state.tab,stamp=Date.now();
      const needJobs=tab==='history'||(tab==='results'&&!state.overview&&(!state.job||stamp-jobsAt>=30000));
      const needSchedules=tab==='schedules'||!schedulesAt||$('yd-plan-dialog')?.open;
      const [jobs,schedules]=await Promise.all([
        needJobs?api('/jobs?limit=20&offset='+state.jobOffset+'&if_revision='+encodeURIComponent(jobsOffset===state.jobOffset?jobsRevision:'')):null,
        needSchedules?api('/schedules'):null]);
      if(!state.active||epoch!==activationVersion)return;
      if(jobs){jobsAt=stamp;jobsOffset=state.jobOffset;jobsRevision=jobs.revision||'';if(!jobs.unchanged){state.jobs=jobs.items;state.jobTotal=jobs.total??jobs.items.length;}state.worker=jobs.worker;}
      if(schedules){schedulesAt=stamp;state.schedules=schedules.items;state.worker=schedules.worker;}
      if(jobs||schedules)jobsView(state.worker,jobs?.now_ms||stamp);if(state.worker?.state==='cooling')$('yd-worker').textContent=state.worker.reason;
      if(state.pendingRoute){
        const p=state.pendingRoute;state.pendingRoute=null;setTab(p.tab||((p.job||p.symbol)?'results':'schedules'),false);
        if(state.tab==='schedules')restorePatrolFilters(p);
        if(!p.job){state.overview=true;state.timeframe=steps[p.timeframe]?p.timeframe:'';state.symbol=p.symbol||'';if(!p.symbol){state.symbolSearch='';$('yd-result-search').value='';state.resultFilter='detected';}state.job=null;state.viewTouched=false;}
        if(p.job&&(state.overview||p.job!==state.job?.id||p.box!==state.selected))await chooseJob(p.job,p.symbol||'',state.tab,p.box||'',p.from==='patrol');
        else if(p.symbol&&p.symbol!==state.symbol&&state.job)await chooseSymbol(p.symbol,p.box||'');
      }
      if(state.tab==='results'&&state.overview)await refreshLatest();
      if(state.tab==='results'&&!state.overview&&state.job&&!terminal.has(state.job.status)){
        const id=state.job.id,version=requestVersion,job=await api('/jobs/'+id+'?view=progress&if_revision='+encodeURIComponent(state.job.revision||''));
        if(!job.unchanged&&version===requestVersion&&state.job?.id===id){state.job={...state.job,...job};summary();drawSymbols();if(!state.symbol){const first=initialSymbol(filteredSymbols(job.results,'','all',favorites,state.symbolOrder));if(first)await chooseSymbol(first);else if(terminal.has(job.status)){$('yd-chart-title').textContent='本次检测未生成图表';emptyChart(job.error||'本次任务没有可显示的结果。');}}}
      }else if(!state.overview&&!state.job&&!state.attempted&&!state.loadingJob&&state.jobs.length&&state.tab==='results')await chooseJob(state.jobs[0].id);
      if(state.tab==='results'){summary();drawSymbols();}if(state.tab==='schedules')await refreshFeed();else if(state.tab==='results')refreshTail();
      error('');
    }catch(e){if(epoch===activationVersion&&e.name!=='AbortError')error(/Failed to fetch|NetworkError|Load failed/i.test(e.message)?'连接暂时中断，已保留上次数据；正在自动重试。':e.message);}finally{refreshing=false;if((epoch!==activationVersion||(state.pendingRoute&&state.pendingRoute!==routeAtStart))&&state.active)Promise.resolve().then(refresh);}
  }
  window.SpikeNavigation?.register('yolo-detect',p=>{state.pendingRoute=p;clearTimeout(feedTimer);feedVersion++;if(state.active)refresh();});
  document.addEventListener('visibilitychange',()=>{if(state.active&&!document.hidden)refresh();});
  window.addEventListener('pagehide',rememberChart);
  new MutationObserver(()=>{chartNeedsTheme=true;if(chart&&state.active){const style=getComputedStyle(document.documentElement);chart.applyOptions({layout:{background:{color:style.getPropertyValue('--chart-bg').trim()},textColor:style.getPropertyValue('--chart-text').trim()},grid:{vertLines:{color:style.getPropertyValue('--chart-grid').trim()},horzLines:{color:style.getPropertyValue('--chart-grid').trim()}}});chartNeedsTheme=false;}}).observe(document.documentElement,{attributes:true,attributeFilter:['data-theme']});
  window.SpikeYoloDetect={refresh,setActive(value){
    if(state.active!==value)activationVersion++;state.active=value;window.SpikeManualAccount?.setActive(value,state.tab);clearInterval(timer);
    if(value){refresh();if(state.tab==='results'&&state.result&&!chart)renderChart().then(drawBoxes).catch(e=>error(e.message));timer=setInterval(()=>{if(!document.hidden)refresh();},5000);}
    else{requests?.cancel();feedVersion++;requestVersion++;tailVersion++;state.tailLoading=false;state.loadingJob=false;state.loadingResult=false;root.querySelectorAll('dialog[open]').forEach(d=>closeDialog(d.id));if(state.loaded)destroyChart();}
  }};
})();
