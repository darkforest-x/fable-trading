/* Research workflow UI. Every count and result is loaded from local sources. */
(() => {
  'use strict';
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const statusNames = { planned:'待开始', negative:'负面结论', active:'研究中', accepted:'历史记录：通过', rejected:'已否定', inconclusive:'证据不足', completed:'已完成', superseded:'已替代', archived:'已归档', hypothesis:'待验证', implemented:'已实现', research:'研究因子', review:'待复核', queued:'排队中', running:'运行中', failed:'运行失败', interrupted:'已中断', cancelled:'已取消' };
  const stages = ['hypothesis','review','inconclusive','rejected','archived'];
  const badge = (s) => `<span class="research-badge status-${esc(s)}">${esc(statusNames[s] || s || '未记录')}</span>`;
  const state = { view:null, factors:[], experiments:[], jobs:[], recipes:[], selected:new Set(), loaded:false, loading:false, revision:0, detail:null, job:null };
  const date = (s) => s ? new Date(s).toLocaleString('zh-CN', {hour12:false}) : '—';
  const title = (e) => e.title || e.experiment_id || e.id;
  async function api(path, options={}) {
    const response = await fetch('/api/research'+path, { ...options, headers:{'Content-Type':'application/json', ...options.headers} });
    let body; try { body = await response.json(); } catch { throw new Error('服务未返回有效数据'); }
    if (!response.ok) throw new Error(typeof body.detail === 'string' ? body.detail : '保存失败，请检查必填字段和长度。');
    return body;
  }
  const options = (items, selected='') => items.map(([value,label]) => `<option value="${esc(value)}" ${value===selected?'selected':''}>${esc(label)}</option>`).join('');
  function showError(error) { const el=$('research-error'); el.textContent=error.message || error; el.classList.remove('hidden'); }
  function dialog(content) { $('research-dialog-content').innerHTML=content; if (!$('research-dialog').open) $('research-dialog').showModal(); }
  function dialogError(error) { const el=$('research-dialog-error'); el.textContent=error.message || error; el.hidden=false; }
  function heading(name,subtitle='') { return `<div class="research-dialog-heading"><div><h2>${esc(name)}</h2><p>${esc(subtitle)}</p></div><button class="research-button" data-close-dialog aria-label="关闭详情">关闭 ×</button></div><p id="research-dialog-error" class="research-error" role="alert" hidden></p>`; }
  function stageSelect(value='hypothesis') { return `<label>研究处理状态<select name="stage">${options(stages.map(x=>[x,statusNames[x]]),value)}</select></label>`; }
  async function refresh() {
    if (state.loading) return;
    state.loading=true; $('research-error').classList.add('hidden');
    try {
      const [overview,factors,experiments,jobs,recipes] = await Promise.all([api('/overview'),api('/factors'),api('/experiments'),api('/jobs'),api('/recipes')]);
      state.overview=overview; state.factors=factors.items; state.experiments=experiments.items; state.jobs=jobs.items; state.recipes=recipes.items; state.loaded=true;
      render();
    } catch(error) { showError(error); }
    finally { state.loading=false; }
  }
  function render() {
    if (!state.loaded) return;
    const o=state.overview;
    $('research-overview').innerHTML=`<div class="research-objective"><span class="research-kicker">研究目标</span><h2>${esc(o.objective)}</h2><p>均线密集 → 启动识别 → 因子筛选 → 回测对照 → 前向观察</p></div>
      <div class="research-metrics"><button data-research-nav="factors"><small>已整理因子</small><strong>${o.factor_count}</strong><span>代码特征与研究假设</span></button><button data-research-nav="experiments"><small>登记实验</small><strong>${o.experiment_count}</strong><span>原始结论与历史版本</span></button><button data-research-nav="experiments" data-filter-status="rejected"><small>否定 / 证据不足</small><strong>${(o.statuses.rejected||0)+(o.statuses.inconclusive||0)+(o.statuses.negative||0)}</strong><span>保留失败证据，避免重复试错</span></button><button data-research-nav="backtests"><small>待完成任务</small><strong>${state.jobs.filter(j=>['queued','running'].includes(j.status)).length}</strong><span>独立离线队列</span></button></div>
      <div class="research-flow">${[['01','定义因子','记录公式、窗口和可知时点','factors'],['02','登记实验','明确问题与唯一变化','experiments'],['03','运行回测','冻结成本、时间与原始数据','backtests'],['04','复核证据','核对前后段及随机对照','experiments'],['05','视觉验证','回放图表、参考图与人工判断','vision']].map(([n,t,d,v])=>`<button data-research-nav="${v}"><span>${n}</span><strong>${t}</strong><small>${d}</small><b>↗</b></button>`).join('')}</div>
      <div class="research-section-heading"><h2>近期研究</h2><button class="research-button" data-research-nav="experiments">查看全部 →</button></div><div class="research-recent">${o.recent.map(e=>`<button data-experiment="${esc(e.experiment_id)}"><div>${badge(e.status)}<span class="research-mono">${esc(e.experiment_id)}</span></div><h3>${esc(title(e))}</h3><p>${esc(e.question || e.result || '尚未记录研究问题')}</p><span class="research-open">查看问题、单变量与证据 →</span></button>`).join('')}</div>
      <div class="research-provenance"><span>工作流参考</span>${o.sources.map(s=>`<a href="${esc(s.url)}" target="_blank" rel="noopener noreferrer">${esc(s.name)} ↗</a>`).join('')}<span>研究完成不等于通过验证；实盘资格独立审批。</span></div>`;
    const categories=[...new Set(state.factors.map(f=>f.category||'未分类'))];
    const selected=$('factor-category').value;
    $('factor-category').innerHTML=options([['','全部类别'],...categories.map(x=>[x,x])],selected);
    renderFactors(); renderExperiments(); renderJobs();
  }
  function renderFactors() {
    const q=$('factor-search').value.trim().toLowerCase(),category=$('factor-category').value;
    const items=state.factors.filter(f=>(!category||f.category===category)&&(!q||JSON.stringify(f).toLowerCase().includes(q)));
    $('factor-count').textContent=`${items.length} / ${state.factors.length} 个因子`;
    $('factor-rows').innerHTML=items.map(f=>`<tr><td><button class="research-text-button" data-factor="${esc(f.id)}">${esc(f.name||f.id)}</button><small class="research-mono">${esc(f.id)}</small></td><td>${esc(f.category||'未分类')}</td><td class="research-description">${esc(f.definition)}</td><td>${badge(f.stage||f.status)}</td><td>${(f.experiment_ids||[]).length}</td><td><button class="research-button" data-factor="${esc(f.id)}">管理 →</button></td></tr>`).join('') || '<tr><td colspan="6" class="research-empty">没有匹配的因子。可以新建一个待验证假设。</td></tr>';
  }
  function renderExperiments() {
    const q=$('experiment-search').value.trim().toLowerCase(),status=$('experiment-status').value;
    const items=state.experiments.filter(e=>(!status||e.status===status)&&(!q||JSON.stringify(e).toLowerCase().includes(q)));
    $('experiment-count').textContent=`${items.length} / ${state.experiments.length} 个实验`;
    $('compare-experiments').disabled=state.selected.size<2;
    $('compare-experiments').textContent=`对照查看${state.selected.size?` (${state.selected.size})`:''}`;
    $('experiment-rows').innerHTML=items.slice(0,120).map(e=>`<tr><td><input type="checkbox" aria-label="对照 ${esc(title(e))}" data-compare="${esc(e.experiment_id)}" ${state.selected.has(e.experiment_id)?'checked':''}></td><td><button class="research-text-button" data-experiment="${esc(e.experiment_id)}">${esc(title(e))}</button><small class="research-mono">${esc(e.experiment_id)}</small><p>${esc(e.question||'未记录研究问题')}</p></td><td>${badge(e.status)}</td><td class="research-description">${esc(e.single_variable||'旧记录未注明')}</td><td>${e.artifacts?.length||0}</td><td><button class="research-button" data-experiment="${esc(e.experiment_id)}">证据 →</button></td></tr>`).join('')||'<tr><td colspan="6" class="research-empty">没有匹配的实验。</td></tr>';
    $('experiment-limit').textContent=items.length>120?'当前显示前 120 条，请用关键词缩小范围。':'';
  }
  function renderJobs() {
    $('job-rows').innerHTML=state.jobs.map(j=>`<tr><td><button class="research-text-button" data-job="${esc(j.id)}">${j.recipe==='spike-v128-frozen'?'V12.8 冻结规则重放':'现有证据核验'}</button><small class="research-mono">${esc(j.id.slice(0,12))}</small></td><td>${esc(j.experiment_id)}<small>${esc(j.recipe==='spike-v128-frozen'?j.spec.symbols.join('、'):'读取现有结果')}</small></td><td>${badge(j.status)}${j.cancel_requested&&j.status==='running'?'<small>正在取消…</small>':''}</td><td>${date(j.created_at)}</td><td><button class="research-button" data-job="${esc(j.id)}">日志与结果 →</button></td></tr>`).join('')||'<tr><td colspan="5" class="research-empty">尚无工作台任务。历史实验可在“实验登记”查看；新任务将独立保留参数、日志与结果。</td></tr>';
    const r=state.recipes.find(r=>r.id==='spike-v128-frozen');
    $('backtest-recipe-detail').textContent=r?.description||'';
    $('new-backtest').disabled=!r?.available;
    $('backtest-availability').textContent=r?.available?`冻结快照可用 · ${r.symbols.length} 个合约 · 2026-07-23 至 2026-09-23`:'冻结输入缺失，不能启动重放。';
  }
  function experimentOptions(selected) { return options(state.experiments.map(e=>[e.experiment_id,title(e)]),selected); }
  function factorDetail(id) {
    const f=state.factors.find(x=>x.id===id);if(!f)return;
    dialog(heading(f.name||f.id,f.id)+`<dl class="research-facts"><dt>类别</dt><dd>${esc(f.category)}</dd><dt>定义 / 公式</dt><dd>${esc(f.definition)}</dd><dt>可知时点与限制</dt><dd>${esc(f.causality||'待核对')}</dd><dt>实现来源</dt><dd class="research-mono">${esc(f.source_path||'手工登记的研究假设')}${f.source_line?':'+f.source_line:''}</dd></dl><form id="factor-note-form" data-id="${esc(id)}" data-revision="${f.revision||0}">${stageSelect(f.stage)}<label>关联实验<select name="experiment_ids" multiple size="5">${state.experiments.map(e=>`<option value="${esc(e.experiment_id)}" ${(f.experiment_ids||[]).includes(e.experiment_id)?'selected':''}>${esc(title(e))}</option>`).join('')}</select><small>按住 Command / Ctrl 可多选；已关联实验可直接在下方打开。</small></label><div class="research-links">${(f.experiment_ids||[]).map(x=>`<button type="button" class="research-button" data-experiment="${esc(x)}">${esc(title(state.experiments.find(e=>e.experiment_id===x)||{id:x}))} ↗</button>`).join('')}</div><label>研究笔记<textarea name="notes" rows="5" maxlength="12000">${esc(f.notes||'')}</textarea></label><div class="research-form-footer"><span>笔记按版本保存，不改变训练或实盘资格。</span><button class="research-button primary" type="submit">保存研究状态</button></div></form>`);
  }
  function newFactor() {
    dialog(heading('登记因子','先写清楚假设、输入列与可知时点。')+`<form id="new-factor-form"><div class="research-form-grid"><label>因子名称<input name="name" required minlength="2" maxlength="100" placeholder="例如：距均线边缘的 ATR 距离"></label><label>类别<input name="category" required maxlength="50" placeholder="价格位置 / 波动 / 形态"></label></div><label>定义与输入窗口<textarea name="definition" required minlength="5" maxlength="4000" rows="3" placeholder="写清使用的列、计算方法和回看窗口"></textarea></label><label>可知时点与适用边界<textarea name="causality" required minlength="5" maxlength="2000" rows="3" placeholder="在信号 bar 收盘可计算？是否有未来筛选、事后标签或镜像限制？"></textarea></label><button class="research-button primary" type="submit">保存为待验证假设</button></form>`);
  }
  function newExperiment() {
    dialog(heading('登记单变量实验','预登记写入现有 experiments/registry.yaml；结果默认未验证。')+`<form id="new-experiment-form"><label>实验名称<input name="title" required minlength="2" maxlength="150" placeholder="例如：密集启动的价格位置筛选"></label><label>要回答的问题<textarea name="question" required minlength="5" maxlength="4000" rows="3"></textarea></label><label>唯一变化<textarea name="single_variable" required minlength="3" maxlength="4000" rows="3" placeholder="相对基线只改变什么？其余成本、退出与数据范围保持什么？"></textarea></label><label>关联因子<select name="factor_ids" multiple size="5">${state.factors.map(f=>`<option value="${esc(f.id)}">${esc(f.name||f.id)}</option>`).join('')}</select></label><button class="research-button primary" type="submit">保存预登记</button></form>`);
  }
  const columnLabels={timeframe_min:'周期（分钟）',arm:'策略组',policy:'方案',period:'时间段',closed:'已平仓',n:'样本数',win_rate:'净胜率',mean_net_bp:'平均净 bp',mean_net_r:'平均净 R',mean_gross_bp:'平均毛 bp',net_gt5r:'净 >5R',net_gt10r:'净 >10R',control_net_bp:'随机对照净 bp',target_net_bp:'配对目标净 bp',mean_excess_bp:'配对超额 bp',p_holm_primary_four:'Holm p',p_holm:'Holm p',p_one_sided:'单侧 p',matched:'配对数',view:'统计视角',sum_net_r:'累计净 R（非账户）'};
  function cell(value,key) {
    if(value===null||value===undefined||value==='')return '<span class="research-missing">未记录</span>';
    const n=Number(value);
    if(Number.isFinite(n)&&typeof value!=='boolean'&&String(value).trim()!==''&& !['arm','policy','period','view'].includes(key)) {
      if(key==='win_rate'||key==='control_win_rate')return `${(n*100).toFixed(2)}%`;
      if(/bp|_r$/.test(key))return `<span class="${n<0?'research-negative':n>0?'research-positive':''}">${n.toFixed(2)}</span>`;
      if(/^p_/.test(key))return n.toFixed(4);
      return Number.isInteger(n)?n.toLocaleString('zh-CN'):n.toFixed(4);
    }
    return esc(value);
  }
  function tableMarkup(table) {
    if(!table?.rows?.length)return '<div class="research-empty">这张表没有可显示的记录。</div>';
    const preferred=Object.keys(columnLabels).filter(c=>table.columns.includes(c));
    const keys=[...preferred,...table.columns.filter(c=>!preferred.includes(c))];
    return `<div class="research-table-wrap"><table class="research-table evidence-table"><thead><tr>${keys.map(c=>`<th>${esc(columnLabels[c]||c)}</th>`).join('')}</tr></thead><tbody>${table.rows.map(r=>`<tr>${keys.map(k=>`<td>${cell(r[k],k)}</td>`).join('')}</tr>`).join('')}</tbody></table></div><p class="research-caption">原始记录 ${table.total_rows ?? table.rows.length} 行${table.truncated?' · 当前仅显示前 300 行':''} · 缺失值不作零处理</p>`;
  }
  function evidenceMarkup(data,prefix='evidence') {
    const tables=data.tables||[];
    const priority=(t)=> /summary\.csv$|^summary$/.test(t.name)?0:/period|by_period/.test(t.name)?1:/random|control/.test(t.name)?2:3;
    tables.sort((a,b)=>priority(a)-priority(b));
    return `<div class="research-section-heading"><h3>原始结果表</h3><span>${tables.length} 张可读表</span></div>${tables.length?`<select class="research-table-select" data-evidence-select="${prefix}" aria-label="选择结果表">${options(tables.map((t,i)=>[String(i),t.name]))}</select><div id="${prefix}-table">${tableMarkup(tables[0])}</div>`:'<p class="research-empty">尚无可直接读取的结构化结果；请查看原始登记和证据文件。</p>'}<p class="research-caption">比较收益时同时查看随机对照和前后段表；全期均值不代表稳定优势。</p>`;
  }
  async function experimentDetail(id) {
    dialog(heading('读取实验…')+'<p class="research-empty">正在加载登记与原始证据。</p>');
    try {
      const data=await api('/experiments/'+encodeURIComponent(id));state.detail=data;
      const e=data.experiment,a=data.annotation||{};
      dialog(heading(title(e),e.experiment_id)+`<div class="research-detail-status">${badge(e.status)}<span>历史研究结论 · 训练 / 实盘资格独立</span></div><dl class="research-facts"><dt>问题</dt><dd>${esc(e.question||'未记录')}</dd><dt>单变量</dt><dd>${esc(e.single_variable||'未记录')}</dd><dt>原始结论</dt><dd>${esc(e.result||'尚未记录结果')}</dd><dt>限制 / 来源</dt><dd>${esc(e.notes||'未记录')}</dd></dl>${Object.keys(data.config||{}).length?`<details class="research-json"><summary>冻结配置与时间范围</summary><pre>${esc(JSON.stringify(data.config,null,2))}</pre></details>`:''}${evidenceMarkup(data)}<details class="research-json"><summary>证据文件与身份</summary><div class="research-files">${(data.files||[]).filter(f=>f.downloadable!==false).map(f=>`<a href="/api/research/file?experiment_id=${encodeURIComponent(id)}&path=${encodeURIComponent(f.path)}" download>${esc(f.path)} ↓<small>${esc(f.sha256||'未记录哈希')}</small></a>`).join('')||'无可读文件'}</div></details><p class="research-caption">${(data.notes||[]).map(esc).join(' · ')}</p><form id="experiment-note-form" data-id="${esc(id)}" data-revision="${a.revision||0}"><h3>后续处理</h3>${stageSelect(a.stage)}<label>结论补充 / 下一步<textarea name="notes" rows="3" maxlength="12000">${esc(a.notes||'')}</textarea></label><div class="research-form-footer"><button class="research-button" type="button" data-verify="${esc(id)}">核验并保存证据快照</button><button class="research-button primary" type="submit">保存补充记录</button></div></form>${data.history?.length?`<details class="research-json"><summary>补充记录历史 (${data.history.length})</summary>${data.history.map(h=>`<p>${badge(h.stage)} v${h.revision} · ${date(h.created_at)}</p><p>${esc(h.notes)}</p>`).join('')}</details>`:''}`);
    } catch(error) { dialogError(error); }
  }
  function newBacktest() {
    const r=state.recipes.find(x=>x.id==='spike-v128-frozen');
    dialog(heading('新建回测运行','每次运行保留独立参数、原始逐笔、随机对照与失败日志。')+`<form id="new-job-form"><label>回测适配器<select name="recipe"><option value="spike-v128-frozen">SPIKE V12.8 冻结规则重放</option></select></label><div class="research-callout">${esc(r.description)}<br>2026-07-23 → 2026-09-23 · 时间切分 2026-08-23 · 成本 20bp<br>冻结数据为原研究 Binance 合约池，与实时 OKX 信号来源分别记录。</div><label>合约（空格或逗号分隔，最多 10 个）<input name="symbols" required value="ETHUSDT" list="research-symbol-options"><datalist id="research-symbol-options">${r.symbols.map(x=>`<option value="${esc(x)}">`).join('')}</datalist></label><p class="research-caption">当前提供冻结规则重放。新增因子的可执行适配器需在代码中实现并通过检查；登记假设不会自动修改策略。</p><button class="research-button primary" type="submit">加入离线回测队列</button></form>`);
  }
  async function jobDetail(id) {
    const j=await api('/jobs/'+encodeURIComponent(id));state.job=j;
    dialog(heading('回测任务',j.id)+`<div class="research-detail-status">${badge(j.status)}<span>${date(j.created_at)}</span></div><dl class="research-facts"><dt>实验</dt><dd>${esc(j.experiment_id)}</dd><dt>范围</dt><dd>${esc(j.recipe==='spike-v128-frozen'?j.spec.symbols.join('、'):'已有证据核验')}</dd><dt>输出目录</dt><dd class="research-mono">${esc(j.output)}</dd></dl>${j.error?`<p class="research-error">${esc(j.error)}</p>`:''}<div class="research-form-footer"><button class="research-button" data-job="${esc(id)}">刷新任务状态</button>${['queued','running'].includes(j.status)?`<button class="research-button" data-cancel-job="${esc(id)}" ${j.cancel_requested?'disabled':''}>${j.cancel_requested?'正在取消…':'取消此任务'}</button>`:''}</div>${j.result?evidenceMarkup(j.result,'job-evidence'):''}<p class="research-caption">${(j.result?.notes||[]).map(esc).join(' · ')}</p><details class="research-json" open><summary>运行日志</summary><pre>${esc(j.log||'等待工作进程输出…')}</pre></details><details class="research-json"><summary>本次冻结请求</summary><pre>${esc(JSON.stringify(j.spec,null,2))}</pre></details>`);
  }
  async function compare() {
    const data=await Promise.all([...state.selected].map(id=>api('/experiments/'+encodeURIComponent(id))));
    dialog(heading('实验对照','并排核对原始结论与统计口径；数据或成本不同不能直接排名。')+`<div class="research-comparison">${data.map(d=>`<article><h3>${esc(title(d.experiment))}</h3>${badge(d.experiment.status)}<p>${esc(d.experiment.single_variable||'未记录单变量')}</p><p>${esc(d.experiment.result||'无结论')}</p><details class="research-json"><summary>配置与口径</summary><pre>${esc(JSON.stringify(d.config,null,2))}</pre></details>${(d.tables||[]).length?tableMarkup(d.tables.find(t=>/summary\.csv$|^summary$/.test(t.name))||d.tables[0]):'<p>无结构化结果表</p>'}<button class="research-button" data-experiment="${esc(d.experiment.experiment_id)}">打开全部证据 →</button></article>`).join('')}</div>`);
  }
  async function submit(event) {
    const form=event.target;if(!form.id?.includes('form')||!$('research-dialog').contains(form))return;
    event.preventDefault();const button=form.querySelector('[type="submit"]');button.disabled=true;
    const values=Object.fromEntries(new FormData(form));
    try {
      if(form.id==='new-factor-form') { await api('/factors',{method:'POST',body:JSON.stringify(values)}); }
      else if(form.id==='new-experiment-form') { values.factor_ids=new FormData(form).getAll('factor_ids'); await api('/experiments',{method:'POST',body:JSON.stringify(values)}); }
      else if(form.id==='factor-note-form'||form.id==='experiment-note-form') {
        values.expected_revision=Number(form.dataset.revision);values.experiment_ids=new FormData(form).getAll('experiment_ids');
        await api(form.id==='factor-note-form'?`/factors/${form.dataset.id}`:`/experiments/${form.dataset.id}/note`,{method:'PUT',body:JSON.stringify(values)});
      } else if(form.id==='new-job-form') {
        const r=state.recipes.find(x=>x.id==='spike-v128-frozen');
        const j=await api('/jobs',{method:'POST',body:JSON.stringify({recipe:values.recipe,experiment_id:r.experiment_id,symbols:values.symbols.split(/[\s,，]+/).filter(Boolean)})});
        await refresh(); await jobDetail(j.id); return;
      } else return;
      $('research-dialog').close(); await refresh();
    } catch(error) { dialogError(error); }
    finally { button.disabled=false; }
  }
  document.addEventListener('submit',submit);
  document.addEventListener('click',async(event)=>{
    const b=event.target.closest('button');if(!b)return;
    try {
      if(b.dataset.closeDialog!==undefined)$('research-dialog').close();
      if(b.dataset.researchNav) { window.location.hash=b.dataset.researchNav; if(b.dataset.filterStatus){$('experiment-status').value=b.dataset.filterStatus;renderExperiments();} }
      if(b.dataset.factor)factorDetail(b.dataset.factor);
      if(b.dataset.experiment)await experimentDetail(b.dataset.experiment);
      if(b.dataset.job)await jobDetail(b.dataset.job);
      if(b.dataset.cancelJob){await api('/jobs/'+b.dataset.cancelJob+'/cancel',{method:'POST',body:'{}'});await jobDetail(b.dataset.cancelJob);await refresh();}
      if(b.dataset.verify){b.disabled=true;const j=await api('/jobs',{method:'POST',body:JSON.stringify({recipe:'verify-evidence',experiment_id:b.dataset.verify})});await refresh();await jobDetail(j.id);}
      if(b.id==='new-factor')newFactor();
      if(b.id==='new-experiment')newExperiment();
      if(b.id==='new-backtest')newBacktest();
      if(b.id==='compare-experiments')await compare();
    } catch(error) { if($('research-dialog').open)dialogError(error);else showError(error); }
  });
  document.addEventListener('change',(event)=>{
    const el=event.target;
    if(el.dataset.compare) { if(el.checked&&state.selected.size>=3){el.checked=false;showError('一次最多对照 3 个实验。');return;} if(el.checked)state.selected.add(el.dataset.compare);else state.selected.delete(el.dataset.compare);renderExperiments(); }
    if(el.dataset.evidenceSelect){const prefix=el.dataset.evidenceSelect;const data=prefix==='job-evidence'?state.job?.result:state.detail;$(`${prefix}-table`).innerHTML=tableMarkup(data?.tables[Number(el.value)]);}
  });
  ['factor-search','factor-category'].forEach(id=>$(id).addEventListener('input',renderFactors));
  ['experiment-search','experiment-status'].forEach(id=>$(id).addEventListener('input',renderExperiments));
  setInterval(async()=>{if(state.view==='backtests'&&!document.hidden){try{state.jobs=(await api('/jobs')).items;renderJobs();}catch(error){showError(error);}}},4000);
  window.SpikeResearch={setView(view){state.view=view;if(['research','factors','experiments','backtests'].includes(view))refresh();},refresh};
})();
