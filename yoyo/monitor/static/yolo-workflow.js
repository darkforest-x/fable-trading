/* YOLO experiment workflow catalog. This view classifies research records only. */
(() => {
  'use strict';

  const root = document.getElementById('yolo-workspace');
  const esc = (value) => String(value ?? '').replace(/[&<>"']/g, (char) => ({
    '&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;',
  }[char]));
  const statusNames = {
    planned:'待开始', active:'研究中', completed:'已完成', negative:'负面结论',
    rejected:'已否定', inconclusive:'证据不足', accepted:'历史记录：通过',
    superseded:'已替代', archived:'已归档', hypothesis:'待验证',
  };
  const PAGE_SIZE = 12;
  const state = { active:false, loading:false, loaded:false, data:null, error:'', actionError:'', query:'', status:'', stage:'', page:1 };

  function textValue(value, fallback='') {
    if (value === null || value === undefined || value === '') return fallback;
    if (typeof value === 'string' || typeof value === 'number' || typeof value === 'boolean') return String(value);
    if (Array.isArray(value)) return value.map((entry) => textValue(entry)).filter(Boolean).join(' · ') || fallback;
    for (const key of ['text','note','name','label','path','id']) {
      if (value[key] !== undefined && value[key] !== null && value[key] !== '') return textValue(value[key], fallback);
    }
    try { return JSON.stringify(value, null, 2); } catch { return fallback; }
  }
  function count(value, fallback='—') {
    const number=Number(value);
    return Number.isFinite(number)&&number>=0 ? Math.floor(number).toLocaleString('zh-CN') : fallback;
  }
  function date(value) {
    if (!value) return '—';
    const parsed=new Date(value);
    return Number.isNaN(parsed.getTime()) ? textValue(value) : parsed.toLocaleString('zh-CN',{hour12:false,timeZone:'Asia/Shanghai'});
  }
  function stageLabel(id) {
    return state.data?.stages?.find((stage)=>stage.id===id)?.label || id || '未分类';
  }
  function statusLabel(status) {
    return statusNames[status] || status || '未记录';
  }
  function eligibilityLabel(value) {
    if (value === true) return '登记为是（未复核）';
    if (value === false) return '登记为否';
    return '未记录';
  }
  async function api(path, options={}) {
    const response=await fetch(`/api/research${path}`,{
      ...options,
      headers:{'Content-Type':'application/json',...options.headers},
    });
    let body;
    try { body=await response.json(); } catch { throw new Error('服务未返回有效数据'); }
    if (!response.ok) throw new Error(typeof body.detail==='string'?body.detail:`读取失败（HTTP ${response.status}）`);
    return body;
  }
  function visibleItems() {
    const data=state.data||{}, query=state.query.trim().toLowerCase();
    return (Array.isArray(data.items)?data.items:[]).filter((item)=>{
      const stageMatch=!state.stage || (Array.isArray(item.stages)&&item.stages.includes(state.stage));
      const statusMatch=!state.status || item.status===state.status;
      const queryMatch=!query || JSON.stringify(item).toLowerCase().includes(query);
      return stageMatch&&statusMatch&&queryMatch;
    });
  }
  function stageFilters() {
    const stages=Array.isArray(state.data?.stages)?state.data.stages:[];
    if (!stages.length) return '<div class="yolo-stage-empty research-caption">尚未登记阶段分类。</div>';
    return stages.map((stage)=>`<button type="button" class="yolo-stage-filter ${state.stage===stage.id?'selected':''}" data-yolo-stage="${esc(stage.id)}" aria-pressed="${state.stage===stage.id}">
      <span class="yolo-stage-count">${esc(count(stage.count))}</span><span class="yolo-stage-copy"><strong>${esc(stage.label||stage.id)}</strong><small>${esc(stage.description||'')}</small></span><span class="yolo-stage-arrow" aria-hidden="true">↗</span>
    </button>`).join('');
  }
  function artifactLabel(artifact) {
    if (typeof artifact==='string') return artifact;
    return textValue(artifact,'未命名产物');
  }
  function itemCard(item) {
    const stages=Array.isArray(item.stages)?item.stages:[];
    const artifacts=Array.isArray(item.artifacts)?item.artifacts:[];
    const notes=textValue(item.notes,'');
    const experimentId=item.experiment_id||item.id||'';
    const classification=item.workflow_classification==='explicit'?'显式登记分类':item.workflow_classification==='inferred'?'按登记文本归类':'分类来源未记录';
    const result=textValue(item.result,'尚未记录结果');
    const receipt=item.receipt_status&&typeof item.receipt_status==='object'?item.receipt_status:null;
    return `<article class="yolo-record-card">
      <div class="yolo-record-top"><span class="yolo-record-state"><small>登记状态</small><strong class="yolo-record-status">${esc(statusLabel(item.status))}</strong></span><span class="yolo-record-classification">${esc(classification)}</span></div>
      <h3>${esc(item.title||experimentId||'未命名实验')}</h3>
      <p class="yolo-record-id">${esc(experimentId||item.id||'')}</p>
      <div class="yolo-receipt-state"><span>落盘回执</span><strong>${esc(receipt?statusLabel(receipt.status):'未记录')}</strong>${receipt?.path?`<code>${esc(receipt.path)}</code>`:''}</div>
      <div class="yolo-record-stages">${stages.map((id)=>`<span class="yolo-stage-tag">${esc(stageLabel(id))}</span>`).join('')||'<span class="research-missing">未分配阶段分类</span>'}</div>
      <section class="yolo-record-field"><h4>研究问题</h4><p>${esc(textValue(item.question,'尚未记录研究问题'))}</p></section>
      <section class="yolo-record-field yolo-result-field"><h4>原始结果</h4><pre>${esc(result)}</pre></section>
      ${notes?`<section class="yolo-record-field"><h4>研究备注</h4><pre>${esc(notes)}</pre></section>`:''}
      <div class="yolo-record-footer"><span>${artifacts.length?`${count(artifacts.length)} 个登记产物`:'暂无登记产物'}</span><button type="button" class="research-button" data-yolo-experiment="${esc(experimentId)}" ${experimentId?'':'disabled'}>打开实验详情 →</button></div>
      ${artifacts.length?`<details class="yolo-artifacts"><summary>登记产物</summary><ul>${artifacts.map((artifact)=>`<li><code>${esc(artifactLabel(artifact))}</code></li>`).join('')}</ul></details>`:''}
      <p class="yolo-eligibility">训练资格：${eligibilityLabel(item.training_eligible)} · 生产资格：${eligibilityLabel(item.production_eligible)}</p>
    </article>`;
  }
  function renderItems() {
    if (!root||!state.data) return;
    const items=visibleItems(), list=root.querySelector('#yolo-records');
    if (!list) return;
    const pageCount=Math.max(1,Math.ceil(items.length/PAGE_SIZE));
    state.page=Math.min(Math.max(1,state.page),pageCount);
    const start=(state.page-1)*PAGE_SIZE;
    const pageItems=items.slice(start,start+PAGE_SIZE);
    root.querySelector('#yolo-result-count').textContent=`筛选结果 ${count(items.length,'0')} / 全部 ${count(state.data.items?.length,'0')} 条研究记录`;
    const pagination=root.querySelector('#yolo-pagination');
    if (!items.length) {
      const any=Array.isArray(state.data.items)&&state.data.items.length>0;
      list.innerHTML=`<div class="yolo-empty"><strong>${any?'没有匹配的研究记录':'暂无 YOLO 实验记录'}</strong><p>${any?'调整搜索词、状态或阶段分类。':'登记一项 YOLO 研究后，实验问题和原始结论会保留在这里。'}</p></div>`;
      if (pagination) pagination.innerHTML='';
      return;
    }
    list.innerHTML=pageItems.map(itemCard).join('');
    if (pagination) pagination.innerHTML=`<span>第 ${count(state.page,'0')} / ${count(pageCount,'0')} 页 · 显示 ${count(start+1,'0')}–${count(Math.min(start+PAGE_SIZE,items.length),'0')} / ${count(items.length,'0')} 条</span><div><button type="button" class="research-button" data-yolo-page="prev" ${state.page<=1?'disabled':''} aria-label="上一页">上一页</button><button type="button" class="research-button" data-yolo-page="next" ${state.page>=pageCount?'disabled':''} aria-label="下一页">下一页</button></div>`;
  }
  function isServiceSnapshot(pointer) {
    return pointer.path==='/api/status'||pointer.name==='当前通知检测器快照';
  }
  function pointerState(pointer) {
    if (isServiceSnapshot(pointer)) return '服务快照';
    if (pointer.exists===true) return '路径存在';
    if (pointer.exists===false) return '路径缺失';
    return '路径未核对';
  }
  function pointerCard(pointer, generatedAt) {
    const snapshot=isServiceSnapshot(pointer);
    return `<article class="yolo-pointer-card ${snapshot?'yolo-service-snapshot':''}">
      <div><h3>${esc(pointer.name||'未命名指针')}</h3><span class="yolo-pointer-exists">${esc(pointerState(pointer))}</span></div>
      <dl><dt>${snapshot?'接口路径':'历史路径'}</dt><dd><code>${esc(pointer.path||'未记录')}</code></dd>
        ${snapshot?`<dt>来源时间</dt><dd>${esc(date(pointer.source_time))}</dd>`:''}
        <dt>目标</dt><dd><code>${esc(textValue(pointer.target,'未记录'))}</code></dd>
        <dt>记录状态</dt><dd>${esc(textValue(pointer.status,'未记录'))}</dd>
        <dt>说明</dt><dd>${esc(textValue(pointer.note,'未记录'))}</dd></dl>
      <p class="yolo-snapshot-caveat">${snapshot?'快照仅反映采集时的通知检测状态；训练进度、交易准入需各自证据。':'历史文件路径只表示目录记录的路径状态，不证明当前运行时使用该文件。'}</p>
    </article>`;
  }
  function render() {
    if (!root) return;
    if (state.loading) {
      root.innerHTML='<div class="yolo-load-state" role="status"><span class="yolo-spinner" aria-hidden="true"></span><p>正在读取 YOLO 研究记录…</p></div>';
      return;
    }
    if (state.error) {
      root.innerHTML=`<div class="yolo-error" role="alert"><div><strong>YOLO 工作流暂不可用</strong><p>${esc(state.error)}</p></div><button type="button" class="research-button primary" data-yolo-refresh>重试</button></div>`;
      return;
    }
    if (!state.loaded||!state.data) {
      root.innerHTML='<div class="yolo-empty"><strong>YOLO 工作流</strong><p>选择此菜单后读取本机研究记录。</p></div>';
      return;
    }
    const data=state.data, items=Array.isArray(data.items)?data.items:[], stages=Array.isArray(data.stages)?data.stages:[];
    const statuses=[...new Set(items.map((item)=>item.status).filter(Boolean))].sort((a,b)=>statusLabel(a).localeCompare(statusLabel(b),'zh-CN'));
    const notes=Array.isArray(data.notes)?data.notes.map((note)=>textValue(note)).filter(Boolean):[];
    const pointers=Array.isArray(data.pointers)?data.pointers:[];
    const models=Array.isArray(data.models)?data.models:[];
    const summary=data.summary||{};
    root.innerHTML=`<div class="yolo-workflow">
      <header class="yolo-hero">
        <div><span class="yolo-kicker">研究工作流 / YOLO</span><h2>YOLO 实验工作台</h2><p>按数据、训练、识别和经济验证分类浏览实验。分类支持一项实验跨多个阶段检索，不表示阶段已经完成。</p><p class="yolo-generated">目录生成时间：${esc(date(data.generated_at))}</p></div>
        <div class="yolo-hero-actions"><button type="button" class="research-button primary" data-yolo-register>＋ 登记 YOLO 实验</button><a class="research-button" href="#vision">VLM 工作流 →</a><a class="research-button" href="#paper">模拟实盘 →</a></div>
      </header>
      ${state.actionError?`<p class="yolo-inline-error" role="alert">${esc(state.actionError)}</p>`:''}
      <section class="yolo-summary" aria-label="YOLO 研究目录数量"><article><span>实验记录</span><strong>${esc(count(summary.experiments,items.length))}</strong></article><article><span>登记产物</span><strong>${esc(count(summary.artifacts))}</strong></article><article><span>工作流阶段</span><strong>${esc(count(stages.length,'0'))}</strong></article></section>
      ${notes.length?`<section class="yolo-notes"><h2>目录说明</h2><ul>${notes.map((note)=>`<li>${esc(note)}</li>`).join('')}</ul></section>`:''}
      <section class="yolo-stage-section"><div class="yolo-section-heading"><div><h2>阶段分类</h2><p>点击阶段卡片筛选；同一实验可以同时出现在多个阶段。</p></div><button type="button" class="research-button ${state.stage?'':'yolo-clear-active'}" data-yolo-stage="" aria-pressed="${!state.stage}">全部阶段</button></div>
        <div class="yolo-stage-grid">${stageFilters()}</div><p class="yolo-classification-note">分类用于查找研究记录，不是完成度或训练进度。</p>
      </section>
      <section class="yolo-record-section"><div class="yolo-section-heading"><div><h2>YOLO 实验记录</h2><p id="yolo-result-count"></p></div><div class="yolo-filters"><label class="yolo-search-label"><span class="sr-only">搜索 YOLO 实验</span><input id="yolo-search" type="search" value="${esc(state.query)}" placeholder="搜索问题、结论、备注或编号…" autocomplete="off"></label><label><span class="sr-only">研究状态筛选</span><select id="yolo-status"><option value="">全部研究状态</option>${statuses.map((status)=>`<option value="${esc(status)}" ${state.status===status?'selected':''}>${esc(statusLabel(status))}</option>`).join('')}</select></label><button type="button" class="research-button" data-yolo-refresh>刷新目录</button></div></div>
        <div id="yolo-records" class="yolo-record-grid"></div><nav id="yolo-pagination" class="yolo-pagination" aria-label="YOLO 实验记录分页"></nav>
      </section>
      <section class="yolo-pointer-section"><div class="yolo-section-heading"><div><h2>服务快照、模型登记与历史路径</h2><p>服务快照保留接口来源时间；历史文件路径说明记录时的路径状态。两者均不能等价为交易准入。</p></div></div>
        <div class="yolo-pointer-notice"><strong>快照、登记记录与运行时身份分开理解</strong><span>/api/status 是带来源时间的服务快照，不表示交易准入。模型版本来自登记信息或已记录的 summary/checkpoint 路径；页面不加载权重、不探测 GPU，实时训练进度未知。</span></div>
        ${models.length?`<details class="yolo-models"><summary>已记录模型版本（${count(models.length,'0')}）</summary><div class="yolo-model-grid">${models.map((model)=>`<article class="yolo-model-card"><div class="yolo-model-heading"><h3>${esc(model.name||'未命名模型')}</h3><span>${model.registered===true?'已登记':model.source_receipt?'回执有记录 · 待登记':'未登记'}</span></div><dl><dt>所属实验</dt><dd>${esc(model.experiment_id||'未记录')}</dd><dt>来源路径</dt><dd><code>${esc(model.source_path||'未记录')}</code></dd><dt>SHA-256</dt><dd><code>${esc(model.sha256||'未记录')}</code></dd><dt>本地文件</dt><dd>${model.local_exists===true?'存在':model.local_exists===false?'缺失':'未核对'}</dd>${model.source_receipt?`<dt>来源回执</dt><dd><code>${esc(model.source_receipt)}</code></dd>`:''}</dl></article>`).join('')}</div></details>`:'<p class="yolo-models-empty">暂无已记录模型版本。</p>'}
        ${pointers.length?`<div class="yolo-pointer-grid">${pointers.map((pointer)=>pointerCard(pointer,data.generated_at)).join('')}</div>`:'<div class="yolo-empty"><p>暂无模型登记或路径指针。</p></div>'}
      </section>
      <p class="yolo-footer-note">登记实验只保存研究问题和单变量计划，不启动训练、不提升模型，也不改变生产资格。</p>
    </div>`;
    renderItems();
  }
  async function refresh() {
    if (!root||state.loading) return;
    state.loading=true; state.error=''; render();
    try {
      state.data=await api('/yolo');
      state.loaded=true;
    } catch (error) {
      state.error=error.message||'请求失败，请稍后重试。';
    } finally {
      state.loading=false;
      render();
    }
  }
  function ensureDialog() {
    let dialog=document.getElementById('yolo-workflow-dialog');
    if (dialog) return dialog;
    dialog=document.createElement('dialog');
    dialog.id='yolo-workflow-dialog';
    dialog.className='yolo-workflow-dialog';
    dialog.setAttribute('aria-label','登记 YOLO 实验');
    dialog.innerHTML=`<div class="yolo-dialog-heading"><div><span class="yolo-kicker">研究登记</span><h2>登记 YOLO 实验</h2><p>登记假设与唯一变化；此操作不会启动训练或模型提升。</p></div><button type="button" class="research-button" data-yolo-close aria-label="关闭登记表单">关闭 ×</button></div>
      <p id="yolo-form-error" class="yolo-form-error" role="alert" hidden></p>
      <form id="yolo-register-form"><label>实验名称<input name="title" required minlength="2" maxlength="150" placeholder="例如：训练集因果窗复核"></label><label>要回答的问题<textarea name="question" required minlength="5" maxlength="4000" rows="3"></textarea></label><label>单变量计划<textarea name="single_variable" required minlength="3" maxlength="4000" rows="3" placeholder="相对当前基线，本次只改变什么？"></textarea></label><div class="yolo-register-notice">实验将标记为 YOLO 工作流记录。登记只保存研究计划，不运行训练、不 promote。</div><div class="yolo-dialog-actions"><button type="button" class="research-button" data-yolo-close>取消</button><button type="submit" class="research-button primary">保存实验登记</button></div></form>`;
    document.body.append(dialog);
    return dialog;
  }
  function openRegisterDialog() {
    const dialog=ensureDialog();
    dialog.querySelector('#yolo-form-error').hidden=true;
    if (!dialog.open) dialog.showModal();
  }
  async function submitRegister(event) {
    const form=event.target;
    if (form?.id!=='yolo-register-form') return;
    event.preventDefault();
    const submit=form.querySelector('[type="submit"]'), error=document.getElementById('yolo-form-error');
    submit.disabled=true; error.hidden=true;
    const values=Object.fromEntries(new FormData(form));
    try {
      await api('/experiments',{method:'POST',body:JSON.stringify({
        title:values.title, question:values.question, single_variable:values.single_variable,
        factor_ids:[], workflow:'yolo',
      })});
      document.getElementById('yolo-workflow-dialog').close();
      form.reset();
      await refresh();
    } catch (failure) {
      error.textContent=failure.message||'保存失败，请检查登记信息。';
      error.hidden=false;
    } finally {
      submit.disabled=false;
    }
  }

  if (root) {
    root.innerHTML='<div class="yolo-empty"><strong>YOLO 工作流</strong><p>选择此菜单后读取本机研究记录。</p></div>';
    root.addEventListener('input',(event)=>{
      if (event.target.id==='yolo-search') { state.query=event.target.value; state.page=1; renderItems(); }
    });
    root.addEventListener('change',(event)=>{
      if (event.target.id==='yolo-status') { state.status=event.target.value; state.page=1; renderItems(); }
    });
    root.addEventListener('click',async(event)=>{
      const button=event.target.closest('button');
      if (!button) return;
      if (button.matches('[data-yolo-refresh]')) { await refresh(); return; }
      if (button.matches('[data-yolo-register]')) { openRegisterDialog(); return; }
      if (button.matches('[data-yolo-page]')) {
        state.page+=button.dataset.yoloPage==='next'?1:-1;
        renderItems();
        return;
      }
      if (button.matches('[data-yolo-stage]')) {
        state.stage=button.dataset.yoloStage||'';
        state.page=1;
        const stageGrid=root.querySelector('.yolo-stage-grid');
        if (stageGrid) stageGrid.innerHTML=stageFilters();
        root.querySelectorAll('.yolo-section-heading [data-yolo-stage=""]').forEach((control)=>{
          control.setAttribute('aria-pressed',String(!state.stage));
          control.classList.toggle('yolo-clear-active',!state.stage);
        });
        renderItems();
        return;
      }
      if (button.matches('[data-yolo-experiment]')) {
        const experimentId=button.dataset.yoloExperiment;
        try {
          if (typeof window.SpikeResearch?.openExperiment!=='function') throw new Error('共享实验详情暂不可用，请稍后重试。');
          await window.SpikeResearch.openExperiment(experimentId);
        } catch (error) {
          state.actionError=error.message||'打开实验详情失败。';
          render();
        }
      }
    });
    document.addEventListener('submit',submitRegister);
    document.addEventListener('click',(event)=>{
      if (event.target.closest('[data-yolo-close]')) document.getElementById('yolo-workflow-dialog')?.close();
    });
  }
  window.SpikeYolo={
    setActive(active) { const entering=Boolean(active)&&!state.active; state.active=Boolean(active); if (entering) return refresh(); return Promise.resolve(); },
    refresh,
  };
})();
