/* Result-first YOLO workbench. Browsing and new scan drafts have separate state.
 * Frozen model inference, observation books, and trading gates are unchanged.
 */
(() => {
  'use strict';
  const steps={'5m':300000,'15m':900000,'30m':1800000,'1H':3600000,'4H':14400000,'1Dutc':86400000};
  const short=s=>(s||'').replace('-USDT-SWAP','');
  function filteredSymbols(rows,query='',filter='detected') {
    return rows.filter(r=>r.symbol.toLowerCase().includes(query.trim().toLowerCase()) && (filter==='all'||(filter==='detected'?r.count>0:!!r.error)))
      .sort((a,b)=>(b.count||0)-(a.count||0)||a.symbol.localeCompare(b.symbol));
  }
  function initialSymbol(rows,preferred) {
    return (rows.find(r=>r.symbol===preferred)||rows.find(r=>!r.error&&r.count>0)||rows.find(r=>!r.error)||rows[0])?.symbol||'';
  }
  function draftSpec(draft) {
    let symbol=draft.symbol.trim().toUpperCase();
    if(!symbol.includes('-'))symbol=symbol.replace(/USDT$/,'')+'-USDT-SWAP';
    return {model_id:draft.model,timeframe:draft.timeframe,mode:draft.mode,scope:draft.scope,
      symbols:draft.scope==='all'?[]:[symbol],...(draft.mode==='history'?{start_ms:Date.parse(draft.start+'+08:00'),end_ms:Date.parse(draft.end+'+08:00')}:{})};
  }
  function sameScan(a,b) {
    return a.mode===b.mode && a.timeframe===b.timeframe && a.model_id===b.model_id && a.scope===b.scope &&
      (a.scope==='all'||JSON.stringify(a.symbols)===JSON.stringify(b.symbols)) &&
      (a.mode==='latest'||(a.start_ms===b.start_ms&&a.end_ms===b.end_ms));
  }
  if(typeof module!=='undefined')module.exports={filteredSymbols,initialSymbol,draftSpec,sameScan};
  const root=typeof document!=='undefined'&&document.getElementById('yolo-detect-workspace');if(!root)return;
  const $=id=>root.querySelector('#'+id), BASE='/api/yolo-detect';
  const terminal=new Set(['completed','partial','failed','cancelled','interrupted']);
  const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const fmt=(ms,full=false)=>ms?new Intl.DateTimeFormat('zh-CN',{timeZone:'Asia/Shanghai',...(full?{year:'numeric'}:{}),month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false}).format(new Date(ms)):'—';
  const names={queued:'排队中',running:'检测中',completed:'已完成',partial:'部分失败',failed:'失败',cancelled:'已取消',interrupted:'已中断'};
  const periods={'5m':'5 分钟','15m':'15 分钟','30m':'30 分钟','1H':'1 小时','4H':'4 小时','1Dutc':'日线 · UTC'};
  const colors={sma20:'#4389c7',sma60:'#71aaa6',sma120:'#7c7f9e',ema20:'#e57950',ema60:'#d4a74e',ema120:'#af7db6'};
  const state={active:false,loaded:false,catalog:null,jobs:[],schedules:[],job:null,result:null,symbol:'',selected:null,
    tab:'results',boxes:'structure',side:'all',symbolSearch:'',resultFilter:'detected',page:0,jobOffset:0,jobTotal:0,ma:true,volume:true,
    pendingRoute:null,attempted:false,loadingJob:false,loadingResult:false,draft:null,scheduleDraft:null,newest:null};
  let timer=null,refreshing=false,requestVersion=0,chart=null,series=null,primitive=null,resize=null,chartPromise=null,maSeries=[],volume=null;
  let submitKey=null,submitting=false,scheduleSubmitting=false,chartNeedsTheme=false;
  const resultCache=new Map();
  function html(id,markup){const el=$(id);if(el._markup!==markup){el.innerHTML=markup;el._markup=markup;}}
  function error(message,id='yd-error'){const el=$(id);if(el){el.hidden=!message;el.textContent=message||'';}else if(message)root.textContent=message;}
  async function api(path,options={}) {
    const response=await fetch(BASE+path,{...options,headers:{'Content-Type':'application/json',...options.headers}});
    const data=await response.json();if(!response.ok)throw Error(Array.isArray(data.detail)?data.detail.map(x=>x.msg).join('；'):data.detail||'请求失败，请重试');return data;
  }
  const localInput=ms=>new Date(ms+8*3600000).toISOString().slice(0,16);
  function rangeDays(draft,days=1){const end=Math.floor(Date.now()/steps[draft.timeframe])*steps[draft.timeframe];draft.end=localInput(end);draft.start=localInput(end-Math.max(steps[draft.timeframe],days*86400000));}
  function defaultDraft(){const d={model:'grade-a-close-1280',timeframe:'15m',mode:'latest',scope:'selected',symbol:'ETH',start:'',end:''};rangeDays(d);return d;}
  function modelOptions(){return state.catalog.items.map(m=>`<option value="${m.id}" ${!m.available?'disabled':''}>${esc(m.name)}${m.recommended?' · 推荐':''}</option>`).join('');}
  function selectionFields(prefix){return `<div class="yd-form-grid"><label>检测范围<select id="${prefix}-scope"><option value="selected">单个币种</option><option value="all">全市场加密币</option></select></label><label id="${prefix}-symbol-label">币种<input id="${prefix}-symbol" list="yd-symbols" autocomplete="off" placeholder="例如 ETH 或 BTC"></label><label>K 线周期<select id="${prefix}-timeframe">${Object.entries(periods).map(([v,n])=>`<option value="${v}">${n}</option>`).join('')}</select></label></div><details class="yd-advanced"><summary>模型选择 <span>默认使用推荐版本</span></summary><label>检测模型<select id="${prefix}-model">${modelOptions()}</select></label><p>15 分钟为原训练周期，其他周期可检测，效果尚需独立验证。</p></details>`;}
  function shell(){
    root.innerHTML=`<div class="yd-shell">
      <div class="yd-toolbar"><div class="yd-view-tabs" role="tablist" aria-label="检测工作台"><button role="tab" id="yd-tab-results" data-yd-tab="results" aria-controls="yd-panel-results">检测结果</button><button role="tab" id="yd-tab-history" data-yd-tab="history" aria-controls="yd-panel-history">检测记录</button><button role="tab" id="yd-tab-schedules" data-yd-tab="schedules" aria-controls="yd-panel-schedules">自动巡检 <span id="yd-plan-count"></span></button><button role="tab" id="yd-tab-model" data-yd-tab="model" aria-controls="yd-panel-model">模型说明</button></div><button class="yd-primary" data-yd-new>新建检测</button></div>
      <p id="yd-error" class="yd-error" role="alert" hidden></p>
      <section id="yd-panel-results" role="tabpanel" aria-labelledby="yd-tab-results">
        <div id="yd-summary" class="yd-summary"></div><div id="yd-run-progress" class="yd-run-progress" aria-live="polite" hidden></div>
        <div class="yd-browser"><aside id="yd-symbol-panel" class="yd-symbol-panel" aria-label="本次检测币种"><header><h2>币种列表</h2><span id="yd-symbol-total"></span></header>
          <input id="yd-result-search" aria-label="搜索本次检测币种" placeholder="搜索币种…"><div class="yd-symbol-filters" role="group" aria-label="筛选币种"><button data-yd-filter="detected">有形态 <span id="yd-hit-count">0</span></button><button data-yd-filter="all">全部</button><button data-yd-filter="failed" id="yd-failed-filter">失败</button></div>
          <div id="yd-symbol-list" class="yd-symbol-list"></div></aside>
          <div id="yd-stage-home"><section class="yd-chart-stage" id="yd-chart-stage">
            <header class="yd-chart-head"><div><h2 id="yd-chart-title">选择币种查看 K 线</h2><p id="yd-chart-sub">运行检测后，在这里查看形态。</p></div><div class="yd-switcher"><select id="yd-quick-symbol" aria-label="切换查看的币种"></select><button class="yd-mobile-filter" data-yd-symbols-toggle aria-expanded="false">筛选</button><button id="yd-prev" aria-label="上一个币种" title="上一个币种">上一个</button><span id="yd-symbol-index"></span><button id="yd-next" aria-label="下一个币种" title="下一个币种">下一个</button></div></header>
            <div class="yd-chart-tools"><div><button id="yd-focus">定位形态</button><button id="yd-fit">全局</button><button id="yd-latest">最近 K 线</button></div><div><label><input type="checkbox" id="yd-ma" checked>均线</label><label><input type="checkbox" id="yd-volume" checked>量</label><button id="yd-scan-symbol">检测此币</button><button id="yd-expand">放大图表</button><button id="yd-export-png">导出图</button></div></div>
            <div id="yd-chart-wrap"><div id="yd-chart-empty" class="yd-chart-empty" role="status">正在读取检测结果…</div><div id="yd-chart"></div></div>
            <footer class="yd-chart-foot"><span id="yd-crosshair">北京时间 · 滚轮缩放，拖拽平移</span><a href="https://www.tradingview.com/" target="_blank" rel="noopener">TradingView Lightweight Charts™</a></footer>
            <div class="yd-detections"><header><h3>密集形态 <span id="yd-box-count">0</span></h3><div><select id="yd-boxes" aria-label="结果口径"><option value="structure">已筛选形态</option><option value="raw">全部原始框</option></select><select id="yd-side" aria-label="检测方向"><option value="all">多空</option><option value="long">多头</option><option value="short">空头</option></select><button id="yd-export-json">导出数据</button></div></header><div id="yd-box-list" class="yd-box-list"></div></div>
          </section></div></div>
        <p class="yd-footnote">结果按已收盘 K 线生成。检测框包含启动后的确认过程，以“确认时间”为可知时点；模型分数不是胜率。</p>
      </section>
      <section id="yd-panel-history" class="yd-card" role="tabpanel" aria-labelledby="yd-tab-history" hidden><header><div><h2>检测记录</h2><p>手动检测和定时巡检都保留在这里。点击一条记录，直接回到看图。</p></div><span id="yd-worker"></span></header><div id="yd-jobs-list"></div><div id="yd-jobs-pages" class="yd-pagination"></div></section>
      <section id="yd-panel-schedules" role="tabpanel" aria-labelledby="yd-tab-schedules" hidden><div class="yd-section-heading"><div><h2>自动巡检</h2><p>关闭网页后继续扫描；本机电脑与服务需要保持运行。</p></div><button class="yd-primary" data-yd-plan-new>添加巡检</button></div><div id="yd-schedules"></div></section>
      <section id="yd-panel-model" role="tabpanel" aria-labelledby="yd-tab-model" hidden>${training()}</section>
      <datalist id="yd-symbols"></datalist>
      <dialog id="yd-scan-dialog" class="yd-dialog" aria-labelledby="yd-scan-heading"><header><h2 id="yd-scan-heading">新建检测</h2><button data-yd-close="yd-scan-dialog" aria-label="关闭新建检测">关闭</button></header><form id="yd-scan-form"><div class="yd-mode-options" role="group" aria-label="检测模式"><button type="button" data-yd-mode="latest">当前盘口<span>查看最新收盘附近的形态</span></button><button type="button" data-yd-mode="history">历史识别<span>检查指定时间段的形态</span></button></div>${selectionFields('yd')}
        <div id="yd-dates"><label>检测时间段 · 北京时间</label><div class="yd-presets"><button type="button" data-yd-days="1">最近 1 天</button><button type="button" data-yd-days="3">最近 3 天</button><button type="button" data-yd-days="7">最近 7 天</button></div><div class="yd-form-grid"><label>开始<input type="datetime-local" id="yd-start"></label><label>结束<input type="datetime-local" id="yd-end"></label></div><p class="yd-help">自定义时间需对齐 K 线边界；UTC 日线在北京时间 08:00 开始。</p></div>
        <p id="yd-scan-error" class="yd-error" role="alert" hidden></p><footer><span id="yd-submit-summary"></span><button type="submit" id="yd-submit" class="yd-primary">开始检测</button></footer></form></dialog>
      <dialog id="yd-plan-dialog" class="yd-dialog" aria-labelledby="yd-plan-heading"><header><h2 id="yd-plan-heading">添加自动巡检</h2><button data-yd-close="yd-plan-dialog" aria-label="关闭自动巡检设置">关闭</button></header><form id="yd-schedule-form">${selectionFields('ys')}<label>扫描频率<select id="yd-interval"><option value="5">每 5 分钟</option><option value="15">每 15 分钟</option><option value="30">每 30 分钟</option><option value="60" selected>每小时</option><option value="240">每 4 小时</option><option value="1440">每天</option></select></label><p class="yd-help">每次检查最新已收盘窗口。上次尚未结束时跳过重叠任务。</p><p id="yd-plan-error" class="yd-error" role="alert" hidden></p><footer><span id="yd-plan-summary"></span><button type="submit" id="yd-plan-submit" class="yd-primary">启用巡检</button></footer></form></dialog>
      <dialog id="yd-inspection" class="yd-dialog yd-inspection" aria-labelledby="yd-input-heading"><header><h2 id="yd-input-heading">模型原图</h2><button data-yd-close="yd-inspection" aria-label="关闭模型原图">关闭</button></header><div id="yd-input-content"></div></dialog>
      <dialog id="yd-enlarge-dialog" class="yd-enlarge-dialog" aria-label="放大的 K 线图"></dialog>
    </div>`;
    bind();setTab(state.tab,false);
  }
  function training(){const t=state.catalog.training;return `<details class="yd-training" open><summary><span><span class="yd-eyebrow">MODEL LINEAGE</span><strong>Grade-A 是怎么训练出来的？</strong></span><span>样本 → 训练 → 验证 → 迭代 ＋</span></summary><p>${esc(t.note)}</p><div class="yd-lineage"><article><b>01</b><h3>定义与筛选</h3><p>双组均线密集启动，多头 / 空头两类。SMA 与 EMA 各 20、60、120，共六条线。</p></article><article><b>02</b><h3>32,000 张图</h3><p>8,000 正图 + 24,000 负图。正图来自 1,043 个事件，重裁图不等于新事件。</p></article><article><b>03</b><h3>YOLO11s · 40 轮</h3><p>原生 1280 输入，AdamW，训练 27,200 图，时间验证 4,800 图；翻转、mosaic、mixup、HSV 关闭。</p></article><article><b>04</b><h3>按证据选择版本</h3><p>Close 基线 best.pt 为第 4 轮。更长训练和更高检出率都不自动意味着更好。</p></article></div>
      <div class="yd-table-wrap"><table><thead><tr><th>研究版本</th><th>最早事件检出 / 155</th><th>负图误触 / 3,600</th><th>结论</th></tr></thead><tbody>${state.catalog.items.map(m=>`<tr><td>${esc(m.name)}</td><td>${(m.earliest_recall*100).toFixed(2)}%</td><td>${m.negative_fires}</td><td>${esc(m.verdict)}</td></tr>`).join('')}</tbody></table></div>
      <p>这些数据来自已有开发集比较，不能作为独立市场精度或收益验收。模型训练周期是 15m；5m、1H、4H、日线均标记为跨周期研究。每次任务固定模型版本、参数和输入来源。</p>
      <p><a href="#yolo">训练与实验记录 ↗</a> · <a href="#models">模型中心 ↗</a> · 后续迭代：逐样本确认与难负例 → 时间分组验证 → 同输入版本对比 → 更新推荐。新训练仍需通过项目准入。</p></details>`;}
  function syncRoute(){window.SpikeNavigation?.sync('yolo-detect',{tab:state.tab,job:state.job?.id,symbol:state.symbol});}
  function setTab(tab,sync=true){
    if(!['results','history','schedules','model'].includes(tab))tab='results';state.tab=tab;
    root.querySelectorAll('[data-yd-tab]').forEach(b=>{const yes=b.dataset.ydTab===tab;b.setAttribute('aria-selected',String(yes));b.tabIndex=yes?0:-1;});
    for(const name of ['results','history','schedules','model'])$('yd-panel-'+name).hidden=name!==tab;
    if(tab==='results'&&state.result&&(!chart||chartNeedsTheme))renderChart(chart?.timeScale().getVisibleLogicalRange()).catch(e=>error(e.message));
    if(sync)syncRoute();
  }
  function draftFromJob(scope){const j=state.job,d=defaultDraft();if(j){Object.assign(d,{model:j.spec.model_id,timeframe:j.spec.timeframe,scope:scope||j.spec.scope,symbol:short(state.symbol||j.spec.symbols[0]||'ETH')});rangeDays(d);}return d;}
  function openScan(){
    if(!state.draft){try{state.draft=JSON.parse(localStorage.getItem('spike.yolo.scan-draft.v2'));}catch{}if(!state.draft||!steps[state.draft.timeframe])state.draft=draftFromJob();}
    fillDraft('yd',state.draft);error('','yd-scan-error');$('yd-scan-dialog').showModal();
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
    }else $('yd-plan-summary').textContent=summary+' · '+$('yd-interval').selectedOptions[0].textContent;
  }
  function openPlan(){state.scheduleDraft=state.scheduleDraft||draftFromJob();state.scheduleDraft.mode='latest';fillDraft('ys',state.scheduleDraft);error('','yd-plan-error');$('yd-plan-dialog').showModal();}
  async function submitScan(spec,button,errorId){
    if(submitting)return;submitting=true;button.disabled=true;const label=button.textContent;button.textContent='正在提交…';error('',errorId);
    const identity=JSON.stringify(spec);if(submitKey?.identity!==identity)submitKey={identity,key:crypto.randomUUID()};
    try{
      const job=await api('/jobs',{method:'POST',body:JSON.stringify({...spec,request_id:submitKey.key})});submitKey=null;
      if($('yd-scan-dialog').open)$('yd-scan-dialog').close();
      state.jobOffset=0;await chooseJob(job.id);await refresh();
    }catch(e){error(e.message,errorId);}finally{submitting=false;button.disabled=false;button.textContent=label;}
  }
  function bind(){
    const on=(id,event,fn)=>$(id).addEventListener(event,fn);
    root.addEventListener('click',delegate);
    root.querySelector('.yd-view-tabs').addEventListener('keydown',e=>{if(!['ArrowLeft','ArrowRight'].includes(e.key))return;e.preventDefault();const tabs=['results','history','schedules','model'];setTab(tabs[(tabs.indexOf(state.tab)+(e.key==='ArrowRight'?1:3))%4]);$('yd-tab-'+state.tab).focus();});
    for(const prefix of ['yd','ys'])for(const key of ['scope','symbol','timeframe','model'])on(prefix+'-'+key,key==='symbol'?'input':'change',e=>{
      const d=prefix==='yd'?state.draft:state.scheduleDraft;d[key]=e.target.value;if(key==='timeframe'&&prefix==='yd'){rangeDays(d);$('yd-start').value=d.start;$('yd-end').value=d.end;}draftContext(prefix);
    });
    for(const key of ['start','end'])on('yd-'+key,'input',e=>state.draft[key]=e.target.value);
    on('yd-interval','change',()=>draftContext('ys'));
    on('yd-scan-form','submit',async e=>{e.preventDefault();try{localStorage.setItem('spike.yolo.scan-draft.v2',JSON.stringify(state.draft));}catch{}await submitScan(draftSpec(state.draft),$('yd-submit'),'yd-scan-error');});
    on('yd-schedule-form','submit',async e=>{
      e.preventDefault();if(scheduleSubmitting)return;scheduleSubmitting=true;$('yd-plan-submit').disabled=true;error('','yd-plan-error');
      try{await api('/schedules',{method:'POST',body:JSON.stringify({spec:draftSpec(state.scheduleDraft),interval_minutes:Number($('yd-interval').value)})});$('yd-plan-dialog').close();setTab('schedules');await refresh();}
      catch(e){error(e.message,'yd-plan-error');}finally{scheduleSubmitting=false;$('yd-plan-submit').disabled=false;}
    });
    on('yd-result-search','input',e=>{state.symbolSearch=e.target.value;drawSymbols();});
    on('yd-symbol-list','keydown',e=>{if(!['ArrowDown','ArrowUp'].includes(e.key)||!e.target.closest('[data-yd-symbol]'))return;e.preventDefault();moveSymbol(e.key==='ArrowDown'?1:-1,true);});
    for(const [id,key] of [['yd-boxes','boxes'],['yd-side','side']])on(id,'change',e=>{state[key]=e.target.value;state.page=0;state.selected=null;drawBoxes();if(boxes().length)selectBox(boxes()[0].id);});
    on('yd-ma','change',e=>{state.ma=e.target.checked;maSeries.forEach(s=>s.applyOptions({visible:state.ma}));});
    on('yd-volume','change',e=>{state.volume=e.target.checked;volume?.applyOptions({visible:state.volume});});
    on('yd-fit','click',()=>chart?.timeScale().fitContent());on('yd-latest','click',recentRange);
    on('yd-focus','click',()=>{const b=boxes().find(b=>b.id===state.selected)||boxes()[0];if(b)selectBox(b.id);});
    on('yd-quick-symbol','change',e=>chooseSymbol(e.target.value));
    on('yd-prev','click',()=>moveSymbol(-1));on('yd-next','click',()=>moveSymbol(1));
    on('yd-scan-symbol','click',()=>{state.draft=draftFromJob('selected');openScan();});
    on('yd-expand','click',()=>{const dialog=$('yd-enlarge-dialog');if(dialog.open)dialog.close();else{dialog.append($('yd-chart-stage'));$('yd-chart-stage').classList.add('is-expanded');$('yd-expand').textContent='退出大图';dialog.showModal();}});
    on('yd-enlarge-dialog','close',()=>{$('yd-stage-home').append($('yd-chart-stage'));$('yd-chart-stage').classList.remove('is-expanded');$('yd-expand').textContent='放大图表';});
    on('yd-export-png','click',()=>{if(!chart)return;const a=document.createElement('a');a.download=`${short(state.symbol)}-${state.result.timeframe}-detections.png`;a.href=chart.takeScreenshot().toDataURL();a.click();});
    on('yd-export-json','click',()=>{if(state.result)download(JSON.stringify(state.result,null,2),`${short(state.symbol)}-${state.result.timeframe}-detections.json`,'application/json');});
  }
  async function delegate(e){
    const b=e.target.closest('button');if(!b)return;const d=b.dataset;
    try{
      if(d.ydTab)setTab(d.ydTab);
      if('ydSymbolsToggle'in d){const yes=root.querySelector('.yd-browser').classList.toggle('is-symbols-open');b.setAttribute('aria-expanded',String(yes));}
      if('ydNew'in d)openScan();if('ydPlanNew'in d)openPlan();if(d.ydClose)$(d.ydClose).close();
      if(d.ydMode){state.draft.mode=d.ydMode;draftContext('yd');}
      if(d.ydDays){rangeDays(state.draft,Number(d.ydDays));fillDraft('yd',state.draft);}
      if(d.ydJob)await chooseJob(d.ydJob);if(d.ydSymbol)await chooseSymbol(d.ydSymbol);
      if(d.ydFilter){state.resultFilter=d.ydFilter;drawSymbols();}
      if(d.ydBox)selectBox(d.ydBox);if(d.ydInput)openInspection(d.ydInput);
      if(d.ydPage){state.page+=Number(d.ydPage);listBoxes();}
      if(d.ydJobsPage){state.jobOffset+=Number(d.ydJobsPage)*20;await refresh();}
      if(d.ydCancel){b.disabled=true;await api(`/jobs/${d.ydCancel}/cancel`,{method:'POST'});await refresh();}
      if(d.ydStop){b.disabled=true;await api(`/schedules/${d.ydStop}`,{method:'DELETE'});await refresh();}
      if('ydRerun'in d&&state.job){const s=state.job.spec;await submitScan({model_id:s.model_id,timeframe:s.timeframe,mode:s.mode,scope:s.scope,symbols:s.scope==='all'?[]:s.symbols,...(s.mode==='history'?{start_ms:s.start_ms,end_ms:s.end_ms}:{})},b,'yd-error');}
      if('ydClearSearch'in d){if(state.resultFilter==='all'){state.symbolSearch='';$('yd-result-search').value='';}state.resultFilter='all';drawSymbols();}
    }catch(e){b.disabled=false;error(e.message);}
  }
  function download(content,name,type){const url=URL.createObjectURL(new Blob([content],{type}));const a=document.createElement('a');a.href=url;a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}
  function scopeName(spec){return spec.scope==='all'?'全市场':spec.symbols.map(short).join(' / ');}
  function summary(){
    const j=state.job;if(!j){html('yd-summary',state.loadingJob?'<strong>正在打开检测记录…</strong>':'<div><strong>从一次检测开始</strong><p>选择币种或扫描全市场，结果直接显示在下方。</p></div><button class="yd-primary" data-yd-new>新建检测</button>');return;}
    const rows=j.results||[],hits=rows.filter(r=>r.count>0).length,failed=rows.filter(r=>r.error).length;
    const latest=state.jobs.find(n=>n.id!==j.id&&n.created_ms>j.created_ms&&terminal.has(n.status)&&n.status!=='failed'&&sameScan(n.spec,j.spec));
    const age=j.spec.end_ms?Date.now()-j.spec.end_ms:0,isOld=j.spec.mode==='latest'&&age>steps[j.spec.timeframe];
    html('yd-summary',`<div><strong>${scopeName(j.spec)} · ${periods[j.spec.timeframe]} <span class="yd-status ${j.status}">${names[j.status]}</span></strong><p>${j.spec.mode==='history'?'历史识别':'盘口快照'} · ${fmt(j.spec.end_ms||j.created_ms)} 北京${isOld?' · <span class="yd-stale">非最新快照</span>':''} · 已检测 ${rows.length}/${j.progress.total||'—'} 币 · <b>${hits} 币有形态</b>${failed?' · '+failed+' 币失败':''}</p>${j.error?`<p class="yd-error-text">${esc(j.error)}</p>`:''}</div><div class="yd-summary-actions">${latest?`<button data-yd-job="${latest.id}">查看较新结果</button>`:''}<button data-yd-rerun ${!terminal.has(j.status)||submitting?'disabled':''}>${j.spec.mode==='latest'?'重新扫描':'重新检测'}</button></div>`);
    const p=$('yd-run-progress');p.hidden=terminal.has(j.status);
    html('yd-run-progress',`<span>${names[j.status]} · ${j.progress.completed||0}/${j.progress.total||'—'} 币${j.progress.symbol?' · '+esc(short(j.progress.symbol)):''}${j.progress.windows_total?' · '+j.progress.windows_done+'/'+j.progress.windows_total+' 窗口':''}</span><progress aria-label="本次检测进度" max="${j.progress.total||1}" value="${j.progress.completed||0}"></progress><button data-yd-cancel="${j.id}" ${j.cancel?'disabled':''}>${j.cancel?'正在取消…':'取消检测'}</button>`);
  }
  function symbolRows(){return filteredSymbols(state.job?.results||[],state.symbolSearch,state.resultFilter);}
  function drawSymbols(){
    const all=state.job?.results||[],rows=symbolRows(),hit=all.filter(r=>r.count>0).length,failed=all.filter(r=>r.error).length;
    $('yd-symbol-total').textContent=all.length+' 币';$('yd-hit-count').textContent=hit;$('yd-failed-filter').textContent='失败 '+failed;$('yd-failed-filter').hidden=!failed;
    root.querySelectorAll('[data-yd-filter]').forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.ydFilter===state.resultFilter)));
    const focus=root.contains(document.activeElement)&&document.activeElement?.matches('[data-yd-symbol]');
    html('yd-symbol-list',rows.length?rows.map(r=>`<button data-yd-symbol="${esc(r.symbol)}" class="yd-symbol ${r.symbol===state.symbol?'selected':''}" aria-pressed="${r.symbol===state.symbol}"><strong>${esc(short(r.symbol))}</strong><span>${r.error?'读取失败':r.count?`${r.count} 个形态`:'未检出'}${!r.error&&!r.count&&r.raw_count?' · 有原始框':''}</span></button>`).join(''):`<div class="yd-empty"><p>${state.loadingJob?'正在读取…':all.length?'没有符合条件的币种':terminal.has(state.job?.status)?'本次没有可浏览的币种':'结果会在检测完成后出现'}</p>${all.length?`<button data-yd-clear-search>${state.resultFilter==='all'?'清空搜索':state.symbolSearch?'在全部币种中搜索':'查看全部币种'}</button>`:''}</div>`);
    if(focus)root.querySelector(`[data-yd-symbol="${CSS.escape(state.symbol)}"]`)?.focus({preventScroll:true});
    html('yd-quick-symbol',(!rows.some(r=>r.symbol===state.symbol)&&state.symbol?`<option value="${esc(state.symbol)}">${esc(short(state.symbol))} · 当前查看</option>`:'')+rows.map(r=>`<option value="${esc(r.symbol)}">${esc(short(r.symbol))}${r.count?' · '+r.count+' 形态':''}</option>`).join(''));$('yd-quick-symbol').value=state.symbol;
    const index=rows.findIndex(r=>r.symbol===state.symbol);$('yd-symbol-index').textContent=index<0?'':`${index+1} / ${rows.length}`;
    $('yd-prev').disabled=index<=0;$('yd-next').disabled=index<0||index>=rows.length-1;
  }
  async function moveSymbol(offset,focus=false){const rows=symbolRows(),index=rows.findIndex(r=>r.symbol===state.symbol),next=rows[index+offset];if(next){await chooseSymbol(next.symbol);if(focus)root.querySelector(`[data-yd-symbol="${CSS.escape(next.symbol)}"]`)?.focus({preventScroll:true});}}
  function jobsView(worker,clock){
    $('yd-worker').textContent=worker&&clock-worker.heartbeat_ms<180000&&worker.state!=='stopped'?'检测服务在线':'提交后启动检测服务';
    html('yd-jobs-list',state.jobs.length?`<div class="yd-table-wrap"><table><thead><tr><th>创建时间 · 北京</th><th>检测内容</th><th>进度</th><th>状态</th><th></th></tr></thead><tbody>${state.jobs.map(j=>`<tr class="${state.job?.id===j.id?'yd-current':''}"><td><button data-yd-job="${j.id}">${fmt(j.created_ms)}</button></td><td>${scopeName(j.spec)} · ${periods[j.spec.timeframe]}<small>${j.spec.mode==='latest'?'当前盘口':'历史识别'}${j.schedule_id?' · 自动巡检':''}</small></td><td>${j.progress.completed||0} / ${j.progress.total||'—'}</td><td><span class="yd-status ${j.status}">${names[j.status]}</span>${j.error?`<small>${esc(j.error)}</small>`:''}</td><td><button data-yd-job="${j.id}">看结果</button>${!terminal.has(j.status)?` <button data-yd-cancel="${j.id}" ${j.cancel?'disabled':''}>取消</button>`:''}</td></tr>`).join('')}</tbody></table></div>`:'<p class="yd-empty">还没有检测记录。新建一次检测即可开始。</p>');
    html('yd-jobs-pages',state.jobTotal>20?`<button data-yd-jobs-page="-1" ${state.jobOffset===0?'disabled':''}>较新记录</button><span>${state.jobOffset+1}–${Math.min(state.jobOffset+20,state.jobTotal)} / ${state.jobTotal}</span><button data-yd-jobs-page="1" ${state.jobOffset+20>=state.jobTotal?'disabled':''}>更早记录</button>`:'');
    $('yd-plan-count').textContent=state.schedules.length||'';
    html('yd-schedules',state.schedules.length?state.schedules.map(s=>`<article class="yd-plan-card"><div><span class="yd-status completed">已启用</span><h3>${scopeName(s.spec)} · ${periods[s.spec.timeframe]}</h3><p>每 ${s.interval_minutes>=60?s.interval_minutes/60+' 小时':s.interval_minutes+' 分钟'}扫描 · 下次 ${fmt(s.next_due_ms)}</p><small>${esc(state.catalog.items.find(m=>m.id===s.spec.model_id)?.name)}${s.skipped_overlap?' · 已跳过 '+s.skipped_overlap+' 次重叠任务':''}</small>${s.error?`<p class="yd-error-text">${esc(s.error)}</p>`:''}</div><div>${s.last_job_id?`<button data-yd-job="${s.last_job_id}">查看最近结果</button>`:''}<button data-yd-stop="${s.id}">停止巡检</button></div></article>`).join(''):'<div class="yd-empty yd-card"><h3>让模型定时查看行情</h3><p>选择币种范围、K 线周期和扫描频率，结果自动保存。</p><button class="yd-primary" data-yd-plan-new>添加第一个巡检</button></div>');
  }
  async function chooseJob(id,preferred='',tab='results'){
    const previousTab=state.tab;const version=++requestVersion;state.attempted=true;state.loadingJob=true;state.loadingResult=false;state.job=null;state.result=null;state.symbol='';state.selected=null;state.symbolSearch='';$('yd-result-search').value='';
    if($('yd-inspection').open)$('yd-inspection').close();setTab(tab,false);if(previousTab!==tab)root.scrollIntoView({block:'start'});destroyChart();$('yd-chart-title').textContent='等待检测结果';$('yd-chart-sub').textContent='';emptyChart('正在打开检测记录…');summary();drawSymbols();drawBoxes();error('');
    try{
      const job=await api('/jobs/'+id);if(version!==requestVersion||!state.active)return;
      state.job=job;state.resultFilter=job.results.some(r=>r.count>0)?'detected':'all';
      if(preferred&&job.results.some(r=>r.symbol===preferred&&!r.count))state.resultFilter='all';
      summary();drawSymbols();const first=initialSymbol(job.results,preferred);
      if(first)await chooseSymbol(first);else{$('yd-chart-title').textContent=terminal.has(job.status)?'本次检测未生成图表':'等待检测结果';emptyChart(job.error||'检测结果将逐币出现，可先查看进度。');syncRoute();}
    }catch(e){if(version===requestVersion){error(e.message);emptyChart('无法打开这条记录，请从“检测记录”选择其他任务。');}}
    finally{if(version===requestVersion){state.loadingJob=false;drawSymbols();}}
  }
  async function chooseSymbol(symbol){
    const id=state.job?.id;if(!id)return;const version=++requestVersion;state.loadingJob=false;state.loadingResult=true;state.symbol=symbol;state.result=null;state.selected=null;state.page=0;
    root.querySelector('.yd-browser').classList.remove('is-symbols-open');root.querySelector('[data-yd-symbols-toggle]').setAttribute('aria-expanded','false');
    destroyChart();$('yd-chart-title').textContent=short(symbol)+' / USDT';$('yd-chart-sub').textContent='正在加载检测快照…';emptyChart('正在加载 '+short(symbol)+' 的 K 线…');drawBoxes();drawSymbols();syncRoute();error('');
    try{
      const key=id+':'+symbol;let result=resultCache.get(key);if(!result){result=await api(`/jobs/${id}/results/${encodeURIComponent(symbol)}`);resultCache.set(key,result);if(resultCache.size>32)resultCache.delete(resultCache.keys().next().value);}
      if(version!==requestVersion||!state.active)return;
      if(result.error){$('yd-chart-sub').textContent='该币种检测失败';emptyChart('该币种检测失败：'+result.error);return;}
      state.result=result;await renderChart();drawBoxes();
    }catch(e){if(version===requestVersion){$('yd-chart-sub').textContent='图表未能加载，请重新选择币种重试';emptyChart('图表加载失败：'+e.message);error(e.message);}}
    finally{if(version===requestVersion){state.loadingResult=false;drawSymbols();}}
  }
  function boxes(){const r=state.result;return(r?(state.boxes==='raw'?r.raw_detections:r.episodes):[]).filter(b=>state.side==='all'||b.side===state.side).sort((a,b)=>b.detected_at_ms-a.detected_at_ms);}
  function listBoxes(){
    const rows=boxes();$('yd-box-count').textContent=rows.length;$('yd-focus').disabled=!rows.length;
    const page=rows.slice(state.page*20,(state.page+1)*20);
    html('yd-box-list',page.length?page.map(b=>`<article class="yd-detection ${b.side} ${state.selected===b.id?'selected':''}"><button class="yd-box-focus" data-yd-box="${b.id}" aria-pressed="${state.selected===b.id}"><b>${b.side==='long'?'多头':'空头'}密集</b><span>确认 ${fmt(b.detected_at_ms)}</span><span class="yd-score" title="模型置信度，不是交易胜率">${(b.confidence*100).toFixed(1)}%</span></button><button class="yd-input-link" data-yd-input="${b.id}">查看原图</button>${!b.structural_pass?'<small>原始提案 · 未通过结构筛选</small>':''}</article>`).join('')+(rows.length>20?`<div class="yd-pagination"><button data-yd-page="-1" ${state.page===0?'disabled':''}>上一页</button><span>${state.page+1}/${Math.ceil(rows.length/20)}</span><button data-yd-page="1" ${(state.page+1)*20>=rows.length?'disabled':''}>下一页</button></div>`:''):`<p class="yd-empty">${state.result?'该币种暂无符合筛选条件的形态。可切换“全部原始框”查看模型提案。':'选择检测结果后，这里显示识别到的形态。'}</p>`);
    $('yd-export-json').disabled=!state.result;$('yd-export-png').disabled=!chart;
  }
  function drawBoxes(){listBoxes();if(primitive){primitive.boxes=boxes();primitive.request?.();}}
  function selectBox(id){
    const b=boxes().find(b=>b.id===id);if(!b)return;state.selected=id;drawBoxes();
    const first=state.result.candles[0].t,step=steps[state.result.timeframe];chart?.timeScale().setVisibleLogicalRange({from:(b.window_start_ms-first)/step-14,to:(b.window_end_ms-first)/step+8});
  }
  function openInspection(id){
    const b=boxes().find(b=>b.id===id);if(!b)return;selectBox(id);
    const url=`${BASE}/jobs/${state.job.id}/results/${encodeURIComponent(state.symbol)}/input/${b.id}`;
    $('yd-input-heading').textContent=`${short(state.symbol)} · ${b.side==='long'?'多头':'空头'}密集`;
    html('yd-input-content',`<p class="yd-input-summary">模型分数 ${(b.confidence*100).toFixed(1)}% · 确认时间 ${fmt(b.detected_at_ms,true)} 北京</p><div class="yd-inspect-grid"><figure><img src="${url}?annotated=true" alt="模型原始预测框"><figcaption>原始预测框</figcaption></figure><figure><img src="${url}" alt="模型实际看到的无标注输入"><figcaption>模型实际输入 · 1280 × 742</figcaption></figure></div><dl><div><dt>密集核心</dt><dd>${fmt(b.core_start_ms)} — ${fmt(b.core_end_ms)} · ${b.core_bars} 根</dd></div><div><dt>核心后的确认</dt><dd>${b.post_bars} 根 K 线</dd></div><div><dt>完整检测窗</dt><dd>${fmt(b.window_start_ms)} — ${fmt(b.detected_at_ms)}</dd></div><div><dt>首次确认</dt><dd>${fmt(b.first_detected_at_ms||b.detected_at_ms)}${b.observations>1?' · 合并 '+b.observations+' 次检出':''}</dd></div><div><dt>模型与输入</dt><dd>${esc(state.result.model_id)} · ${esc(state.result.ma_source)}</dd></div><div><dt>权重标识</dt><dd>${esc(state.result.model_sha256.slice(0,12))}</dd></div></dl><p class="yd-help">核心位置表示形态在哪里；确认时间表示模型在当时已有足够 K 线看见这个框。</p>`);
    $('yd-input-content').querySelectorAll('img').forEach(img=>img.onerror=()=>{const p=document.createElement('p');p.className='yd-error-text';p.textContent='原图读取失败，关闭后重试。';img.replaceWith(p);});
    $('yd-inspection').showModal();
  }
  function chartLibrary(){if(window.LightweightCharts)return Promise.resolve();if(!chartPromise)chartPromise=new Promise((resolve,reject)=>{const s=document.createElement('script');s.src='/vision-assets/vendor/lightweight-charts.standalone.production.js';s.onload=resolve;s.onerror=()=>{chartPromise=null;s.remove();reject(Error('图表组件加载失败，请刷新重试'));};document.head.append(s);});return chartPromise;}
  class DetectionPrimitive{
    constructor(rows,first,step){this.boxes=rows;this.first=first;this.step=step;this.view={zOrder:()=> 'top',renderer:()=>({draw:target=>target.useMediaCoordinateSpace(({context:ctx})=>this.draw(ctx))})};}
    attached({chart,series,requestUpdate}){this.chart=chart;this.series=series;this.request=requestUpdate;}
    detached(){this.request=null;}
    paneViews(){return [this.view];}
    // The bundled 4.2 build returns 0 for fractional logical indices. Interpolate
    // adjacent bar centers to preserve the model's exact sub-bar box boundaries.
    x(ms){const index=(ms-this.first)/this.step,left=Math.floor(index),scale=this.chart.timeScale();const a=scale.logicalToCoordinate(left),b=scale.logicalToCoordinate(left+1);return a==null||b==null?null:a+(b-a)*(index-left);}
    rect(b){return [this.x(b.x_start_ms),this.series.priceToCoordinate(b.price_high),this.x(b.x_end_ms),this.series.priceToCoordinate(b.price_low)];}
    draw(ctx){for(const b of this.boxes){const [x,y,x2,y2]=this.rect(b);if([x,y,x2,y2].some(v=>v==null)||x2<0||x>this.chart.timeScale().width())continue;const color=b.side==='long'?'#239f85':'#d96575';ctx.save();ctx.fillStyle=b.side==='long'?'rgba(35,159,133,.09)':'rgba(217,101,117,.09)';ctx.strokeStyle=color;ctx.lineWidth=state.selected===b.id?2.5:1.2;ctx.setLineDash(b.structural_pass?[]:[4,3]);ctx.fillRect(x,y,x2-x,y2-y);ctx.strokeRect(x,y,x2-x,y2-y);if(x2-x>35){ctx.fillStyle=color;ctx.font='11px system-ui';ctx.fillText(`${b.side==='long'?'L':'S'} ${(b.confidence*100).toFixed(0)}%`,Math.max(2,x+3),Math.max(12,y-4));}ctx.restore();}}
  }
  function destroyChart(){resize?.disconnect();resize=null;chart?.remove();chart=null;series=null;primitive=null;maSeries=[];volume=null;}
  function emptyChart(message){$('yd-chart-empty').hidden=false;$('yd-chart-empty').textContent=message;}
  function recentRange(){if(chart&&state.result)chart.timeScale().setVisibleLogicalRange({from:Math.max(0,state.result.candles.length-90),to:state.result.candles.length+3});}
  async function renderChart(savedRange=null){
    const result=state.result;await chartLibrary();if(result!==state.result||!state.active||state.tab!=='results')return;
    destroyChart();chartNeedsTheme=false;$('yd-chart-empty').hidden=true;
    const light=document.documentElement.dataset.theme==='light',bg=light?'#ffffff':'#151c20',text=light?'#465961':'#a6b4bb',grid=light?'#f0f3f4':'#222d33';
    const host=$('yd-chart');chart=window.LightweightCharts.createChart(host,{width:host.clientWidth,height:host.clientHeight,layout:{background:{color:bg},textColor:text,fontSize:12},grid:{vertLines:{color:grid},horzLines:{color:grid}},rightPriceScale:{borderColor:grid,scaleMargins:{top:.1,bottom:.2}},timeScale:{borderColor:grid,timeVisible:true,secondsVisible:false,tickMarkFormatter:(t,type)=>new Intl.DateTimeFormat('zh-CN',{timeZone:'Asia/Shanghai',...(type<3?{month:'2-digit',day:'2-digit'}:{hour:'2-digit',minute:'2-digit',hour12:false})}).format(new Date(t*1000))},localization:{locale:'zh-CN',timeFormatter:t=>fmt(t*1000,true)},crosshair:{mode:0}});
    const precision=Math.min(10,Math.max(2,Math.ceil(-Math.log10(Math.min(...result.candles.map(r=>r.close))))+4));
    series=chart.addCandlestickSeries({upColor:'#239f85',downColor:'#d96575',borderVisible:false,wickUpColor:'#239f85',wickDownColor:'#d96575',priceFormat:{type:'price',precision,minMove:10**-precision}});
    series.setData(result.candles.map(r=>({time:r.t/1000,open:r.open,high:r.high,low:r.low,close:r.close})));
    for(const [name,color] of Object.entries(colors)){const line=chart.addLineSeries({color,lineWidth:1,lastValueVisible:false,priceLineVisible:false,crosshairMarkerVisible:false,visible:state.ma});line.setData(result.candles.filter(r=>Number.isFinite(r[name])).map(r=>({time:r.t/1000,value:r[name]})));maSeries.push(line);}
    volume=chart.addHistogramSeries({priceScaleId:'volume',priceFormat:{type:'volume'},lastValueVisible:false,priceLineVisible:false,visible:state.volume});volume.priceScale().applyOptions({scaleMargins:{top:.85,bottom:0}});volume.setData(result.candles.map(r=>({time:r.t/1000,value:r.volume,color:r.close>=r.open?'rgba(35,159,133,.3)':'rgba(217,101,117,.3)'})));
    primitive=new DetectionPrimitive(boxes(),result.candles[0].t,steps[result.timeframe]);series.attachPrimitive(primitive);
    if(savedRange)chart.timeScale().setVisibleLogicalRange(savedRange);else if(boxes().length)selectBox(boxes()[0].id);else recentRange();
    chart.subscribeClick(p=>{if(!p.point||!primitive)return;const hit=boxes().find(b=>{const [x,y,x2,y2]=primitive.rect(b);return p.point.x>=x&&p.point.x<=x2&&p.point.y>=y&&p.point.y<=y2;});if(hit)openInspection(hit.id);});
    chart.subscribeCrosshairMove(p=>{const c=p.seriesData.get(series);$('yd-crosshair').textContent=c?`${fmt(p.time*1000)} · 开 ${c.open} 高 ${c.high} 低 ${c.low} 收 ${c.close}`:'北京时间 · 滚轮缩放，拖拽平移';});
    resize=new ResizeObserver(entries=>{const r=entries[0].contentRect;if(chart&&r.width>0&&r.height>0)chart.resize(r.width,r.height);});resize.observe(host);
    $('yd-chart-title').textContent=`${short(result.symbol)} / USDT · ${periods[result.timeframe]}`;
    $('yd-chart-sub').textContent=`${result.mode==='history'?'历史识别':'盘口快照'} · ${fmt(result.cutoff_ms)} 北京 · ${boxes().length?'已定位密集区':'未发现符合筛选条件的形态'}${result.timeframe==='15m'?'':' · 跨周期研究'}`;
  }
  async function refresh(){
    if(!state.active||refreshing)return;refreshing=true;
    try{
      if(!state.loaded){state.catalog=await api('/catalog');shell();state.loaded=true;
        api('/symbols').then(d=>{html('yd-symbols',d.items.map(r=>`<option value="${esc(short(r.symbol))}">${esc(r.name)}</option>`).join(''));}).catch(()=>{/* Manual symbols remain usable; task failures are explicit. */});}
      const [jobs,schedules]=await Promise.all([api('/jobs?limit=20&offset='+state.jobOffset),api('/schedules')]);
      if(!state.active)return;state.jobs=jobs.items;state.jobTotal=jobs.total||jobs.items.length;state.schedules=schedules.items;jobsView(jobs.worker,jobs.now_ms);
      if(state.pendingRoute){
        const p=state.pendingRoute;state.pendingRoute=null;setTab(p.tab||'results',false);
        if(p.job&&p.job!==state.job?.id)await chooseJob(p.job,p.symbol||'',state.tab);
        else if(p.symbol&&p.symbol!==state.symbol&&state.job)await chooseSymbol(p.symbol);
      }
      if(state.job&&!terminal.has(state.job.status)){
        const id=state.job.id,version=requestVersion,job=await api('/jobs/'+id);
        if(version===requestVersion&&state.job?.id===id){state.job=job;summary();drawSymbols();if(!state.symbol){const first=initialSymbol(job.results);if(first)await chooseSymbol(first);}}
      }else if(!state.job&&!state.attempted&&!state.loadingJob&&state.jobs.length)await chooseJob(state.jobs[0].id);
      summary();drawSymbols();
    }catch(e){error(e.message);}finally{refreshing=false;}
  }
  window.SpikeNavigation?.register('yolo-detect',p=>{state.pendingRoute=p;if(state.active)refresh();});
  document.addEventListener('visibilitychange',()=>{if(state.active&&!document.hidden)refresh();});
  new MutationObserver(()=>{chartNeedsTheme=true;if(state.active&&state.result&&state.tab==='results'){const range=chart?.timeScale().getVisibleLogicalRange();renderChart(range).then(drawBoxes).catch(e=>error(e.message));}}).observe(document.documentElement,{attributes:true,attributeFilter:['data-theme']});
  window.SpikeYoloDetect={refresh,setActive(value){
    state.active=value;clearInterval(timer);
    if(value){refresh();if(state.result&&!chart)renderChart().then(drawBoxes).catch(e=>error(e.message));timer=setInterval(()=>{if(!document.hidden)refresh();},5000);}
    else{requestVersion++;state.loadingJob=false;state.loadingResult=false;root.querySelectorAll('dialog[open]').forEach(d=>d.close());if(state.loaded)destroyChart();}
  }};
})();
