/* Observation workbench. Model input is rendered by the frozen Python adapter.
 * Chart primitives: https://tradingview.github.io/lightweight-charts/docs/4.2/plugins/series-primitives
 */
(() => {
  'use strict';
  const root=document.getElementById('yolo-detect-workspace');if(!root)return;
  const BASE='/api/yolo-detect', terminal=new Set(['completed','partial','failed','cancelled','interrupted']);
  const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const fmt=(ms,full=false)=>ms?new Intl.DateTimeFormat('zh-CN',{timeZone:'Asia/Shanghai',...(full?{year:'numeric'}:{}),month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false}).format(new Date(ms)):'—';
  const short=s=>(s||'').replace('-USDT-SWAP','');
  const names={queued:'排队中',running:'检测中',completed:'已完成',partial:'部分失败',failed:'失败',cancelled:'已取消',interrupted:'已中断'};
  const steps={'5m':300000,'15m':900000,'30m':1800000,'1H':3600000,'4H':14400000,'1Dutc':86400000};
  const periods={'5m':'5 分钟','15m':'15 分钟','30m':'30 分钟','1H':'1 小时','4H':'4 小时','1Dutc':'1 日 · UTC'};
  const colors={sma20:'#4389c7',sma60:'#71aaa6',sma120:'#7c7f9e',ema20:'#e57950',ema60:'#d4a74e',ema120:'#af7db6'};
  const state={active:false,loaded:false,catalog:null,jobs:[],schedules:[],job:null,result:null,symbol:'',selected:null,
    boxes:'structure',side:'all',symbolSearch:'',resultFilter:'all',page:0,jobOffset:0,jobTotal:0,model:'grade-a-close-1280',timeframe:'15m',mode:'latest',scope:'selected',symbolInput:'ETH-USDT-SWAP',start:'',end:'',ma:true,volume:true};
  let timer=null,refreshing=false,requestVersion=0,chart=null,series=null,primitive=null,resize=null,chartPromise=null,maSeries=[],volume=null;
  function error(message){const n=root.querySelector('#yd-error');if(n){n.hidden=!message;n.textContent=message||'';}else if(message){root.innerHTML=`<p id="yd-error" class="yd-error" role="alert">${esc(message)} · 请点击页面右上角刷新重试。</p>`;}}
  async function api(path,options={}){
    const r=await fetch(BASE+path,{...options,headers:{'Content-Type':'application/json',...options.headers}});
    const d=await r.json();if(!r.ok){const msg=Array.isArray(d.detail)?d.detail.map(x=>x.msg).join('；'):d.detail;throw Error(msg||'请求失败');}return d;
  }
  const localInput=ms=>new Date(ms+8*3600000).toISOString().slice(0,16);
  function dates(){const end=Math.floor(Date.now()/steps[state.timeframe])*steps[state.timeframe];state.end=localInput(end);state.start=localInput(end-96*steps[state.timeframe]);}
  dates();
  function controls(){
    const models=state.catalog.items;
    return `<div class="yd-command"><div class="yd-command-top"><div class="yd-tabs" role="group" aria-label="检测模式"><button type="button" data-yd-mode="latest" aria-pressed="${state.mode==='latest'}">当前盘口</button><button type="button" data-yd-mode="history" aria-pressed="${state.mode==='history'}">历史识别</button></div><span class="yd-chip">OKX · 加密永续</span></div>
    <form id="yd-scan-form"><div class="yd-fields"><label>检测范围<select id="yd-scope"><option value="selected">单个币种</option><option value="all">全部加密币种</option></select></label><label id="yd-symbol-label">币种<input id="yd-symbol" list="yd-symbols" value="${esc(state.symbolInput)}" autocomplete="off" placeholder="搜索 ETH、BTC…"><datalist id="yd-symbols"></datalist></label><label>周期<select id="yd-timeframe">${Object.entries(periods).map(([v,n])=>`<option value="${v}">${n}</option>`).join('')}</select></label><label class="yd-model-field">检测模型<select id="yd-model">${models.map(m=>`<option value="${m.id}" ${!m.available?'disabled':''}>${esc(m.name)}${m.recommended?' · 默认':''}${!m.available?' · 文件缺失':''}</option>`).join('')}</select></label><button type="submit" id="yd-submit" class="yd-primary">开始检测 <span aria-hidden="true">↗</span></button></div>
    <div id="yd-dates" class="yd-dates" ${state.mode==='latest'?'hidden':''}><label>开始 · 北京时间<input type="datetime-local" id="yd-start" value="${state.start}" required></label><label>结束 · 北京时间<input type="datetime-local" id="yd-end" value="${state.end}" required></label><span>逐根扫描 · 只使用各检测时点之前的数据</span></div></form>
    <div id="yd-run-progress" class="yd-run-progress" aria-live="polite" hidden></div><div class="yd-context"><span id="yd-mode-note"></span><span id="yd-transfer"></span></div></div>`;
  }
  function shell(){
    root.innerHTML=`<div class="yd-shell"><p id="yd-error" class="yd-error" role="alert" hidden></p>${controls()}
      <div class="yd-layout"><section class="yd-chart-card"><header class="yd-chart-head"><div><span class="yd-eyebrow">PATTERN EXPLORER</span><h2 id="yd-chart-title">先选择行情，再让模型看图</h2><p id="yd-chart-sub">检测完成后，在这里缩放、拖动并查看密集区。</p></div><div class="yd-chart-actions"><button type="button" id="yd-fit" title="显示完整区间">全局</button><button type="button" id="yd-latest">末端</button><button type="button" id="yd-export-png">导出图</button></div></header>
      <div class="yd-chart-options"><label><input type="checkbox" id="yd-ma" checked> 六均线</label><label><input type="checkbox" id="yd-volume" checked> 成交量</label><span class="yd-legend"><i style="background:#4389c7"></i>SMA 20 / 60 / 120 <i style="background:#e57950"></i>EMA 20 / 60 / 120</span></div>
      <div id="yd-chart-wrap"><div id="yd-chart-empty"><svg viewBox="0 0 240 90" aria-hidden="true"><path d="M8 70l22-8 15 4 28-30 18 5 16-4 21 2 20-3 18 4 27-22 40-7"/><path d="M8 60l35 1 27-17 30 1 40-1 35-5 35-8 23-4"/><rect x="80" y="29" width="90" height="24" rx="3"/></svg><strong>每个检测框，都能回到模型原图</strong><span>选择币种和周期，运行一次检测。没有检出也会保留完整 K 线。</span></div><div id="yd-chart"></div></div>
      <footer class="yd-chart-foot"><span id="yd-crosshair">十字光标查看 OHLC · 滚轮缩放 · 拖拽平移</span><a href="https://www.tradingview.com/" target="_blank" rel="noopener">TradingView Lightweight Charts™</a></footer></section>
      <aside class="yd-detections"><header><div><span class="yd-eyebrow">DETECTIONS</span><h2>识别结果 <span id="yd-box-count">0</span></h2></div><button type="button" id="yd-export-json" title="导出当前币种的原始结果">JSON ↗</button></header><div class="yd-filters"><select id="yd-boxes" aria-label="结果口径"><option value="structure">结构符合 · 合并重叠</option><option value="raw">全部原始框</option></select><select id="yd-side" aria-label="检测方向"><option value="all">多空</option><option value="long">多头</option><option value="short">空头</option></select></div><div id="yd-box-list" class="yd-box-list"><p class="yd-empty">等待第一次检测</p></div></aside></div>
      <div id="yd-inspection" class="yd-inspection" hidden></div>
      <section class="yd-jobs"><header><div><span class="yd-eyebrow">SCAN HISTORY</span><h2>检测任务</h2></div><span id="yd-worker" class="yd-muted"></span></header><div id="yd-jobs-list"></div><div id="yd-jobs-pages" class="yd-pagination"></div><div id="yd-result-tools" class="yd-result-tools" hidden><input id="yd-result-search" aria-label="搜索检测结果币种" placeholder="搜索本次结果…"><select id="yd-result-filter" aria-label="筛选检测结果币种"><option value="all">全部币种</option><option value="detected">有结构检出</option><option value="failed">检测失败</option></select></div><div id="yd-job-detail"></div></section>
      <section class="yd-scheduler"><div><span class="yd-eyebrow">AUTOMATIC SCAN</span><h2>定时巡检</h2><p>按当前选择的币种范围、周期和模型，持续检测最新 3 个收盘端点。计划保存在本机，关闭页面后继续；电脑与服务需要运行。</p></div><form id="yd-schedule-form"><label>每隔<select id="yd-interval"><option value="5">5 分钟</option><option value="15" selected>15 分钟</option><option value="30">30 分钟</option><option value="60">1 小时</option><option value="240">4 小时</option><option value="1440">1 天</option></select></label><button type="submit">创建巡检计划</button></form><div id="yd-schedules"></div></section>
      ${training()}
      <p class="yd-footnote">当前盘口 = 最新已收盘窗口的形态观察。Grade-A 识别包含核心后的确认 K 线；可知时间以完整检测窗收盘为准。结果不会触发下单或模型晋级。</p>
    </div>`;
    root.querySelector('#yd-timeframe').value=state.timeframe;root.querySelector('#yd-model').value=state.model;root.querySelector('#yd-scope').value=state.scope;
    updateContext();bind();
  }
  function training(){const t=state.catalog.training;return `<details class="yd-training"><summary><span><span class="yd-eyebrow">MODEL LINEAGE</span><strong>Grade-A 是怎么训练出来的？</strong></span><span>样本 → 训练 → 验证 → 迭代 ＋</span></summary><p>${esc(t.note)}</p><div class="yd-lineage"><article><b>01</b><h3>定义与筛选</h3><p>双组均线密集启动，多头 / 空头两类。SMA 与 EMA 各 20、60、120，共六条线。</p></article><article><b>02</b><h3>32,000 张图</h3><p>8,000 正图 + 24,000 负图。正图来自 1,043 个事件，重裁图不等于新事件。</p></article><article><b>03</b><h3>YOLO11s · 40 轮</h3><p>原生 1280 输入，AdamW，训练 27,200 图，时间验证 4,800 图；翻转、mosaic、mixup、HSV 关闭。</p></article><article><b>04</b><h3>按证据选择版本</h3><p>Close 基线 best.pt 为第 4 轮。更长训练和更高检出率都不自动意味着更好。</p></article></div>
      <div class="yd-table-wrap"><table><thead><tr><th>研究版本</th><th>最早事件检出 / 155</th><th>负图误触 / 3,600</th><th>结论</th></tr></thead><tbody>${state.catalog.items.map(m=>`<tr><td>${esc(m.name)}</td><td>${(m.earliest_recall*100).toFixed(2)}%</td><td>${m.negative_fires}</td><td>${esc(m.verdict)}</td></tr>`).join('')}</tbody></table></div>
      <p>这些数据来自已有开发集比较，不能作为独立市场精度或收益验收。模型训练周期是 15m；5m、1H、4H、日线均标记为跨周期研究。每次任务固定模型版本、参数和输入来源。</p>
      <p><a href="#yolo">训练与实验记录 ↗</a> · <a href="#models">模型中心 ↗</a> · 后续迭代：逐样本确认与难负例 → 时间分组验证 → 同输入版本对比 → 更新推荐。新训练仍需通过项目准入。</p></details>`;}
  function updateContext(){
    root.querySelector('#yd-dates').hidden=state.mode!=='history';root.querySelector('#yd-symbol-label').hidden=state.scope==='all';
    root.querySelector('#yd-mode-note').textContent=state.mode==='latest'?'检测 tip / tip-1 / tip-2 · 已收盘 K 线 · 置信度固定 0.25':'历史逐窗检测 · W18 + W19 · 重叠结果保留原始记录';
    root.querySelector('#yd-transfer').textContent=state.timeframe==='15m'?'15m · 原训练周期':'跨周期研究 · 尚未独立验收';
    root.querySelectorAll('[data-yd-mode]').forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.ydMode===state.mode)));
  }
  function selection(mode=state.mode){
    let symbol=state.symbolInput.trim().toUpperCase();if(!symbol.includes('-'))symbol=symbol.replace(/USDT$/,'')+'-USDT-SWAP';
    return {model_id:state.model,timeframe:state.timeframe,mode,scope:state.scope,symbols:state.scope==='all'?[]:[symbol],
      ...(mode==='history'?{start_ms:Date.parse(state.start+'+08:00'),end_ms:Date.parse(state.end+'+08:00')}:{})};
  }
  function bind(){
    const on=(id,event,fn)=>root.querySelector('#'+id)?.addEventListener(event,fn);
    root.querySelectorAll('[data-yd-mode]').forEach(b=>b.onclick=()=>{state.mode=b.dataset.ydMode;updateContext();});
    on('yd-scope','change',e=>{state.scope=e.target.value;updateContext();});
    on('yd-timeframe','change',e=>{state.timeframe=e.target.value;dates();root.querySelector('#yd-start').value=state.start;root.querySelector('#yd-end').value=state.end;updateContext();});
    on('yd-model','change',e=>state.model=e.target.value);on('yd-symbol','input',e=>state.symbolInput=e.target.value);
    on('yd-start','input',e=>state.start=e.target.value);on('yd-end','input',e=>state.end=e.target.value);
    on('yd-scan-form','submit',async e=>{e.preventDefault();const b=root.querySelector('#yd-submit');b.disabled=true;error('');try{const d=await api('/jobs',{method:'POST',body:JSON.stringify({...selection(),request_id:crypto.randomUUID()})});await chooseJob(d.id);await refresh();}catch(e){error(e.message);}finally{b.disabled=false;}});
    on('yd-schedule-form','submit',async e=>{e.preventDefault();const b=e.target.querySelector('button');b.disabled=true;error('');try{await api('/schedules',{method:'POST',body:JSON.stringify({spec:selection('latest'),interval_minutes:Number(root.querySelector('#yd-interval').value)})});await refresh();}catch(e){error(e.message);}finally{b.disabled=false;}});
    on('yd-result-search','input',e=>{state.symbolSearch=e.target.value;jobDetail();});on('yd-result-filter','change',e=>{state.resultFilter=e.target.value;jobDetail();});
    on('yd-boxes','change',e=>{state.boxes=e.target.value;state.page=0;state.selected=null;drawBoxes();});on('yd-side','change',e=>{state.side=e.target.value;state.page=0;state.selected=null;drawBoxes();});
    on('yd-ma','change',e=>{state.ma=e.target.checked;maSeries.forEach(s=>s.applyOptions({visible:state.ma}));});
    on('yd-volume','change',e=>{state.volume=e.target.checked;volume?.applyOptions({visible:state.volume});});
    on('yd-fit','click',()=>chart?.timeScale().fitContent());on('yd-latest','click',()=>{if(chart&&state.result)chart.timeScale().setVisibleLogicalRange({from:Math.max(0,state.result.candles.length-100),to:state.result.candles.length+4});});
    on('yd-export-png','click',()=>{if(!chart)return;const a=document.createElement('a');a.download=`${short(state.symbol)}-${state.result.timeframe}-detections.png`;a.href=chart.takeScreenshot().toDataURL();a.click();});
    on('yd-export-json','click',()=>{if(state.result)download(JSON.stringify(state.result,null,2),`${short(state.symbol)}-${state.result.timeframe}-detections.json`,'application/json');});
    root.removeEventListener('click',delegate);root.addEventListener('click',delegate);
  }
  function download(content,name,type){const url=URL.createObjectURL(new Blob([content],{type}));const a=document.createElement('a');a.href=url;a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}
  async function delegate(e){
    const b=e.target.closest('[data-yd-job],[data-yd-symbol],[data-yd-cancel],[data-yd-stop],[data-yd-box],[data-yd-page],[data-yd-jobs-page]');if(!b)return;
    try{error('');if(b.dataset.ydJob)await chooseJob(b.dataset.ydJob);if(b.dataset.ydSymbol)await chooseSymbol(b.dataset.ydSymbol);if(b.dataset.ydCancel){await api(`/jobs/${b.dataset.ydCancel}/cancel`,{method:'POST'});await refresh();}if(b.dataset.ydStop){await api(`/schedules/${b.dataset.ydStop}`,{method:'DELETE'});await refresh();}if(b.dataset.ydBox)selectBox(b.dataset.ydBox);if(b.dataset.ydPage){state.page+=Number(b.dataset.ydPage);listBoxes();}if(b.dataset.ydJobsPage){state.jobOffset+=Number(b.dataset.ydJobsPage)*20;await refresh();}}catch(e){error(e.message);}
  }
  function jobsView(worker,clock){
    root.querySelector('#yd-worker').textContent=worker&&clock-worker.heartbeat_ms<180000&&worker.state!=='stopped'?'独立检测进程在线':'等待任务 · 提交后启动检测进程';
    root.querySelector('#yd-jobs-list').innerHTML=state.jobs.length?`<div class="yd-table-wrap"><table><thead><tr><th>创建时间 · 北京</th><th>范围 / 周期</th><th>模式</th><th>进度</th><th>状态</th><th></th></tr></thead><tbody>${state.jobs.map(j=>`<tr class="${state.job?.id===j.id?'yd-current':''}"><td><button type="button" data-yd-job="${j.id}">${fmt(j.created_ms)}</button></td><td>${j.spec.scope==='all'?'全部加密币':j.spec.symbols.map(short).join(', ')} · ${esc(j.spec.timeframe)}</td><td>${j.spec.mode==='latest'?'当前盘口':'历史'}</td><td>${j.progress.completed||0} / ${j.progress.total||'—'}${j.progress.symbol&&j.status==='running'?`<small>${esc(short(j.progress.symbol))} · ${j.progress.windows_done||0}/${j.progress.windows_total||'—'} 窗口</small>`:''}</td><td><span class="yd-status ${j.status}">${names[j.status]||j.status}</span></td><td>${!terminal.has(j.status)?`<button type="button" data-yd-cancel="${j.id}" ${j.cancel?'disabled':''}>${j.cancel?'取消中…':'取消'}</button>`:`<button type="button" data-yd-job="${j.id}">查看 ↗</button>`}</td></tr>`).join('')}</tbody></table></div>`:'<p class="yd-empty">每次手动检测和定时巡检都会保存在这里。</p>';
    root.querySelector('#yd-jobs-pages').innerHTML=state.jobTotal>20?`<button data-yd-jobs-page="-1" ${state.jobOffset===0?'disabled':''}>较新任务</button><span>${state.jobOffset+1}–${Math.min(state.jobOffset+20,state.jobTotal)} / ${state.jobTotal}</span><button data-yd-jobs-page="1" ${state.jobOffset+20>=state.jobTotal?'disabled':''}>更早任务</button>`:'';
    root.querySelector('#yd-schedules').innerHTML=state.schedules.length?state.schedules.map(s=>`<div class="yd-schedule-row"><div><strong>${s.spec.scope==='all'?'全部加密币':s.spec.symbols.map(short).join(', ')} · ${s.spec.timeframe}</strong><span>每 ${s.interval_minutes} 分钟 · ${esc(state.catalog.items.find(m=>m.id===s.spec.model_id)?.name)} · 下次 ${fmt(s.next_due_ms)}${s.skipped_overlap?` · 忙碌跳过 ${s.skipped_overlap} 次`:''}${s.error?' · '+esc(s.error):''}</span></div><button type="button" data-yd-stop="${s.id}">停止计划</button></div>`).join(''):'<p class="yd-muted">尚未启用定时计划。选择“全部加密币种”后创建，即可巡检该周期全市场。</p>';
  }
  function jobDetail(){
    const j=state.job;if(!j){root.querySelector('#yd-job-detail').innerHTML='';return;}
    const progress=root.querySelector('#yd-run-progress');progress.hidden=terminal.has(j.status);progress.innerHTML=`<span>${names[j.status]} · ${j.progress.completed||0} / ${j.progress.total||'—'} 个币种${j.progress.symbol?' · '+esc(short(j.progress.symbol)):''}${j.progress.windows_total?' · '+j.progress.windows_done+'/'+j.progress.windows_total+' 窗口':' · 正在准备行情'}</span><progress aria-label="检测进度" max="${j.progress.total||1}" value="${j.progress.completed||0}"></progress><button type="button" data-yd-cancel="${j.id}" ${j.cancel?'disabled':''}>${j.cancel?'取消中…':'取消任务'}</button>`;
    const all=j.results||[],rows=all.filter(r=>r.symbol.toLowerCase().includes(state.symbolSearch.toLowerCase())&&(state.resultFilter==='all'||(state.resultFilter==='detected'?r.count>0:!!r.error))).sort((a,b)=>(b.count||0)-(a.count||0)||a.symbol.localeCompare(b.symbol));root.querySelector('#yd-result-tools').hidden=!all.length;root.querySelector('#yd-job-detail').innerHTML=`<div class="yd-job-detail-head"><strong>本次结果 · ${all.length} 个币种 · ${all.filter(r=>r.count>0).length} 个有结构${rows.length!==all.length?` · 筛选 ${rows.length} 个`:""}</strong><span>${j.error?esc(j.error):`${esc(state.catalog.items.find(m=>m.id===j.spec.model_id)?.name)} · ${j.spec.end_ms?'截止 '+fmt(j.spec.end_ms,true):'等待冻结行情时点'}`}</span></div>${rows.length?`<div class="yd-result-symbols">${rows.map(r=>`<button type="button" data-yd-symbol="${esc(r.symbol)}" class="${r.symbol===state.symbol?'selected':''}" title="${esc(r.error||'查看该币种图表')}"><strong>${esc(short(r.symbol))}</strong><span>${r.error?'失败':r.count+' 个结构'}${!r.error&&r.raw_count!==r.count?' / '+r.raw_count+' 原始框':''}</span></button>`).join('')}</div>`:`<p class="yd-empty">${all.length?'没有符合筛选条件的币种。':'检测结果会逐币出现；取消任务会保留已完成部分。'}</p>`}`;
  }
  function restoreSelection(s){
    state.model=s.model_id;state.timeframe=s.timeframe;state.mode=s.mode;state.scope=s.scope;state.symbolInput=s.symbols[0]||'ETH-USDT-SWAP';
    if(s.mode==='history'){state.start=localInput(s.start_ms);state.end=localInput(s.end_ms);}
    for(const [id,value] of Object.entries({'yd-model':state.model,'yd-timeframe':state.timeframe,'yd-scope':state.scope,'yd-symbol':state.symbolInput,'yd-start':state.start,'yd-end':state.end}))root.querySelector('#'+id).value=value;
    updateContext();
  }
  async function chooseJob(id){
    const version=++requestVersion;state.resultFilter='all';state.symbolSearch='';root.querySelector('#yd-result-search').value='';root.querySelector('#yd-result-filter').value='all';state.job=null;state.result=null;state.symbol='';state.selected=null;destroyChart();drawBoxes();
    const j=await api('/jobs/'+id);if(version!==requestVersion)return;state.job=j;restoreSelection(j.spec);jobDetail();
    window.SpikeNavigation?.sync('yolo-detect',{job:id});
    const first=j.results.find(r=>!r.error&&r.count>0)||j.results.find(r=>!r.error)||j.results[0];if(first)await chooseSymbol(first.symbol);
  }
  async function chooseSymbol(symbol){
    const id=state.job?.id;if(!id)return;const version=++requestVersion;state.symbol=symbol;state.result=null;state.selected=null;state.page=0;destroyChart();drawBoxes();jobDetail();
    const r=await api(`/jobs/${id}/results/${encodeURIComponent(symbol)}`);if(version!==requestVersion)return;
    if(r.error){error(`${short(symbol)}：${r.error}`);return;}state.result=r;await renderChart();drawBoxes();
  }
  function boxes(){const r=state.result;return (r?(state.boxes==='raw'?r.raw_detections:r.episodes):[]).filter(b=>state.side==='all'||b.side===state.side).sort((a,b)=>b.detected_at_ms-a.detected_at_ms);}
  function listBoxes(){
    const rows=boxes();root.querySelector('#yd-box-count').textContent=rows.length;
    const page=rows.slice(state.page*30,(state.page+1)*30);
    root.querySelector('#yd-box-list').innerHTML=page.length?page.map(b=>`<button type="button" class="yd-detection ${b.side} ${state.selected===b.id?'selected':''}" data-yd-box="${b.id}"><span><b>${b.side==='long'?'↗ 多头':'↘ 空头'}密集</b><strong>${(b.confidence*100).toFixed(1)}<small>%</small></strong></span><span>核心 ${fmt(b.core_start_ms)}<small>${b.core_bars} 根</small></span><span>检测窗收盘 ${fmt(b.detected_at_ms)}<small>后 ${b.post_bars} 根</small></span>${b.observations>1?`<small>合并 ${b.observations} 次检出 · 首见 ${fmt(b.first_detected_at_ms)}</small>`:''}${!b.structural_pass?'<small class="yd-raw-note">原始提案 · 未满足 4/5 根核心 + 2–9 根确认</small>':''}</button>`).join('')+(rows.length>30?`<div class="yd-pagination"><button data-yd-page="-1" ${state.page===0?'disabled':''}>上一页</button><span>${state.page+1}/${Math.ceil(rows.length/30)}</span><button data-yd-page="1" ${(state.page+1)*30>=rows.length?'disabled':''}>下一页</button></div>`:''):`<p class="yd-empty">${state.result?'本次没有符合当前筛选条件的检测框。可切换“全部原始框”查看模型提案。':'选择检测任务或运行新检测。'}</p>`;
  }
  function drawBoxes(){listBoxes();if(primitive){primitive.boxes=boxes();primitive.request?.();}if(!state.selected)root.querySelector('#yd-inspection').hidden=true;}
  function selectBox(id){
    const b=boxes().find(b=>b.id===id);if(!b)return;state.selected=id;drawBoxes();
    const first=state.result.candles[0].t,step=steps[state.result.timeframe];chart?.timeScale().setVisibleLogicalRange({from:(b.window_start_ms-first)/step-20,to:(b.window_end_ms-first)/step+20});
    const inspection=root.querySelector('#yd-inspection');inspection.hidden=false;
    const url=`${BASE}/jobs/${state.job.id}/results/${encodeURIComponent(state.symbol)}/input/${b.id}`;
    inspection.innerHTML=`<header><div><span class="yd-eyebrow">EXACT MODEL INPUT</span><h2>${b.side==='long'?'多头':'空头'}密集 · 原始检测依据</h2></div><span>${b.window_bars} 根 K 线 · ${(b.confidence*100).toFixed(1)}%</span></header><div class="yd-inspect-grid"><figure><img src="${url}" alt="用于本次推理的裸输入，未绘制检测框" loading="lazy"><figcaption>模型实际看到的输入 · 1280 × 742</figcaption></figure><figure><img src="${url}?annotated=true" alt="原始模型框，保留预测的横向和纵向坐标" loading="lazy"><figcaption>原始预测框 · 不是重新手画的框</figcaption></figure></div><dl><div><dt>完整输入区间 · 北京</dt><dd>${fmt(b.window_start_ms,true)} — ${fmt(b.detected_at_ms,true)}</dd></div><div><dt>核心后确认</dt><dd>${b.post_bars} 根；该框到检测窗收盘才可知</dd></div><div><dt>均线来源</dt><dd>${esc(state.result.ma_source)} · SMA / EMA 20、60、120</dd></div><div><dt>模型版本</dt><dd>${esc(state.result.model_id)} · ${esc(state.result.model_sha256.slice(0,12))}</dd></div></dl>`;
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
  function destroyChart(){resize?.disconnect();resize=null;chart?.remove();chart=null;series=null;primitive=null;maSeries=[];volume=null;root.querySelector('#yd-chart-empty').hidden=false;root.querySelector('#yd-chart-title').textContent='选择结果查看 K 线';root.querySelector('#yd-chart-sub').textContent='原始框与全局行情使用同一时间、价格坐标';}
  async function renderChart(){
    const result=state.result;await chartLibrary();if(result!==state.result||!state.active)return;
    destroyChart();root.querySelector('#yd-chart-empty').hidden=true;
    const light=document.documentElement.dataset.theme==='light',bg=light?'#ffffff':'#151c20',text=light?'#465961':'#a6b4bb',grid=light?'#f0f3f4':'#222d33';
    const host=root.querySelector('#yd-chart');chart=window.LightweightCharts.createChart(host,{width:host.clientWidth,height:440,layout:{background:{color:bg},textColor:text,fontSize:11},grid:{vertLines:{color:grid},horzLines:{color:grid}},rightPriceScale:{borderColor:grid,scaleMargins:{top:.1,bottom:.22}},timeScale:{borderColor:grid,timeVisible:true,secondsVisible:false,tickMarkFormatter:(t,type)=>new Intl.DateTimeFormat('zh-CN',{timeZone:'Asia/Shanghai',...(type<3?{month:'2-digit',day:'2-digit'}:{hour:'2-digit',minute:'2-digit',hour12:false})}).format(new Date(t*1000))},localization:{locale:'zh-CN',timeFormatter:t=>fmt(t*1000,true)},crosshair:{mode:0}});
    const precision=Math.min(10,Math.max(2,Math.ceil(-Math.log10(Math.min(...result.candles.map(r=>r.close)))*1)+4));
    series=chart.addCandlestickSeries({upColor:'#239f85',downColor:'#d96575',borderVisible:false,wickUpColor:'#239f85',wickDownColor:'#d96575',priceFormat:{type:'price',precision,minMove:10**-precision}});
    series.setData(result.candles.map(r=>({time:r.t/1000,open:r.open,high:r.high,low:r.low,close:r.close})));
    for(const [name,color] of Object.entries(colors)){const line=chart.addLineSeries({color,lineWidth:1,lastValueVisible:false,priceLineVisible:false,crosshairMarkerVisible:false,visible:state.ma});line.setData(result.candles.filter(r=>Number.isFinite(r[name])).map(r=>({time:r.t/1000,value:r[name]})));maSeries.push(line);}
    volume=chart.addHistogramSeries({priceScaleId:'volume',priceFormat:{type:'volume'},lastValueVisible:false,priceLineVisible:false,visible:state.volume});volume.priceScale().applyOptions({scaleMargins:{top:.84,bottom:0}});volume.setData(result.candles.map(r=>({time:r.t/1000,value:r.volume,color:r.close>=r.open?'rgba(35,159,133,.3)':'rgba(217,101,117,.3)'})));
    primitive=new DetectionPrimitive(boxes(),result.candles[0].t,steps[result.timeframe]);series.attachPrimitive(primitive);chart.timeScale().fitContent();
    chart.subscribeClick(p=>{if(!p.point||!primitive)return;const hit=boxes().find(b=>{const [x,y,x2,y2]=primitive.rect(b);return p.point.x>=x&&p.point.x<=x2&&p.point.y>=y&&p.point.y<=y2;});if(hit)selectBox(hit.id);});
    chart.subscribeCrosshairMove(p=>{const c=p.seriesData.get(series);root.querySelector('#yd-crosshair').textContent=c?`${fmt(p.time*1000)} 北京 · 开 ${c.open} 高 ${c.high} 低 ${c.low} 收 ${c.close}`:'十字光标查看 OHLC · 滚轮缩放 · 拖拽平移';});
    resize=new ResizeObserver(entries=>{if(chart&&entries[0].contentRect.width>0)chart.resize(entries[0].contentRect.width,440);});resize.observe(host);
    root.querySelector('#yd-chart-title').textContent=`${short(result.symbol)} / USDT · ${periods[result.timeframe]}`;
    const age=Date.now()-result.cutoff_ms;root.querySelector('#yd-chart-sub').textContent=`${result.mode==='latest'?'盘口观察快照':'历史识别'} · ${result.candles.length} 根展示 · ${result.windows_scored} 个模型窗口 · 截止 ${fmt(result.cutoff_ms,true)} 北京${result.mode==='latest'&&age>steps[result.timeframe]?' · 已过期，请重新检测':''}`;
  }
  async function refresh(){
    if(!state.active||refreshing)return;refreshing=true;
    try{
      if(!state.loaded){state.catalog=await api('/catalog');shell();state.loaded=true;
        api('/symbols').then(d=>{root.querySelector('#yd-symbols').innerHTML=d.items.map(r=>`<option value="${esc(r.symbol)}">${esc(r.name)}</option>`).join('');}).catch(e=>error(e.message));}
      const [jobs,schedules]=await Promise.all([api('/jobs?limit=20&offset='+state.jobOffset),api('/schedules')]);state.jobs=jobs.items;state.jobTotal=jobs.total||jobs.items.length;state.schedules=schedules.items;jobsView(jobs.worker,jobs.now_ms);
      if(state.pendingJob){const id=state.pendingJob;state.pendingJob=null;await chooseJob(id);}
      else if(state.job){const id=state.job.id,version=requestVersion;const j=await api('/jobs/'+id);if(version===requestVersion&&state.job?.id===id){state.job=j;jobDetail();if(!state.result&&!state.symbol){const first=j.results.find(r=>!r.error&&r.count>0)||j.results.find(r=>!r.error);if(first)await chooseSymbol(first.symbol);}}}
      else if(state.jobs.length&&!state.symbol)await chooseJob(state.jobs[0].id);
    }catch(e){error(e.message);}finally{refreshing=false;}
  }
  window.SpikeNavigation?.register('yolo-detect',p=>{if(p.job&&p.job!==state.job?.id)state.pendingJob=p.job;});
  document.addEventListener('visibilitychange',()=>{if(state.active&&!document.hidden)refresh();});
  new MutationObserver(()=>{if(state.active&&state.result)renderChart().then(drawBoxes).catch(e=>error(e.message));}).observe(document.documentElement,{attributes:true,attributeFilter:['data-theme']});
  window.SpikeYoloDetect={refresh,setActive(value){state.active=value;clearInterval(timer);if(value){refresh();if(state.result&&!chart)renderChart().then(drawBoxes).catch(e=>error(e.message));timer=setInterval(()=>{if(!document.hidden)refresh();},4000);}else{requestVersion++;if(state.loaded)destroyChart();}}};
})();
