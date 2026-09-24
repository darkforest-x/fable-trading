/* Read-only crypto dataset catalog and backtest reuse entry point. */
(() => {
  'use strict';

  const root = document.getElementById('datasets-workspace');
  const FILE_PAGE_SIZE = 50;
  const DATASET_PAGE_SIZE = 12;
  const state = {
    active:false, loading:false, loaded:false, data:null, error:'', notice:'',
    query:'', category:'', missingOnly:false, page:1, detail:null, detailRevision:0, reindexing:false, copyFor:'', copyMessage:'',
  };
  const esc = (value) => String(value ?? '').replace(/[&<>"']/g, (char) => ({
    '&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;',
  }[char]));
  const arr = (value) => Array.isArray(value) ? value : [];
  const text = (value, fallback='未知') => {
    if (value === null || value === undefined || value === '') return fallback;
    if (typeof value === 'string' || typeof value === 'number' || typeof value === 'boolean') return String(value);
    if (Array.isArray(value)) return value.map((part)=>text(part,'')).filter(Boolean).join(' · ') || fallback;
    try { return JSON.stringify(value); } catch { return fallback; }
  };
  const number = (value, fallback='—') => {
    if (value === null || value === undefined || value === '') return fallback;
    const parsed=Number(value);
    return Number.isFinite(parsed)&&parsed>=0 ? Math.floor(parsed).toLocaleString('zh-CN') : fallback;
  };
  function bytes(value) {
    if (value === null || value === undefined || value === '') return '未知';
    const size=Number(value);
    if (!Number.isFinite(size)||size<0) return '未知';
    if (size<1024) return `${Math.floor(size)} B`;
    const units=['KiB','MiB','GiB','TiB'];
    let scaled=size/1024, unit=0;
    while (scaled>=1024&&unit<units.length-1) { scaled/=1024; unit++; }
    return `${scaled.toLocaleString('zh-CN',{maximumFractionDigits:1})} ${units[unit]}`;
  }
  function date(value) {
    if (!value) return '未知';
    const parsed=new Date(value);
    return Number.isNaN(parsed.getTime()) ? text(value) : parsed.toLocaleString('zh-CN',{hour12:false,timeZone:'Asia/Shanghai'});
  }
  function categoryName(id) {
    return state.data?.categories?.find((category)=>category.id===id)?.label || id || '未分类';
  }
  function itemMissingCount(item) {
    const value=item?.missing_count;
    if (value===null||value===undefined||value==='') return null;
    const parsed=Number(value);
    return Number.isFinite(parsed)&&parsed>=0 ? Math.floor(parsed) : null;
  }
  function perItemMissingCountsAvailable() {
    const items=arr(state.data?.items);
    return items.length>0&&items.every((item)=>itemMissingCount(item)!==null);
  }
  function affectedDatasets() {
    return arr(state.data?.items).filter((item)=>itemMissingCount(item)>0);
  }
  function categoryFiltersHTML() {
    const categories=arr(state.data?.categories), items=arr(state.data?.items);
    return `<button type="button" class="dataset-category-filter ${state.category?'':'selected'}" data-dataset-category="" aria-pressed="${state.category?'false':'true'}"><span>全部分类</span><strong>${esc(number(items.length,'0'))}</strong></button>${categories.map((category)=>`<button type="button" class="dataset-category-filter ${state.category===category.id?'selected':''}" data-dataset-category="${esc(category.id)}" aria-pressed="${state.category===category.id?'true':'false'}"><span>${esc(category.label||category.id)}</span><strong>${esc(number(category.count))}</strong></button>`).join('')}`;
  }
  function renderCategoryFilters() {
    const grid=root?.querySelector('.dataset-category-grid');
    if (grid) grid.innerHTML=categoryFiltersHTML();
  }
  function storageName(storage) {
    return ({managed:'集中管理',in_place:'原位保留',external:'外部引用'})[storage] || '未登记';
  }
  function listText(values, fallback='未知') {
    return arr(values).map((value)=>text(value,'')).filter(Boolean).join('、') || fallback;
  }
  async function api(path, options={}) {
    const response=await fetch(`/api/research${path}`,{
      ...options,
      headers:{...(options.body?{'Content-Type':'application/json'}:{}),...options.headers},
    });
    let body=null;
    try { body=await response.json(); } catch { /* A 202 reindex acknowledgement may have no body. */ }
    if (!response.ok) throw new Error(typeof body?.detail==='string'?body.detail:`请求失败（HTTP ${response.status}）`);
    return {body,status:response.status};
  }
  function visibleItems() {
    const items=arr(state.data?.items), query=state.query.trim().toLowerCase();
    return items.filter((item)=>{
      const categoryMatch=!state.category||item.category===state.category;
      const queryMatch=!query||JSON.stringify(item).toLowerCase().includes(query);
      const missingMatch=!state.missingOnly||itemMissingCount(item)>0;
      return categoryMatch&&queryMatch&&missingMatch;
    });
  }
  function sampleCode(item) {
    const py=(value, fallback) => `'${String(value??fallback).replace(/\\/g,'\\\\').replace(/'/g,"\\'").replace(/[\r\n]/g,' ')}'`;
    const example=item.example&&typeof item.example==='object'?item.example:{};
    return `from yoyo.data.dataset_catalog import read_market_data\n\n# 示例流：symbol=${py(example.symbol,'替换为币种')}，exchange=${py(example.exchange,'替换为市场')}，timeframe=${py(example.timeframe,'替换为周期')}\nframe = read_market_data(\n    dataset_id=${py(item.id,'替换为数据集 ID')},\n    symbol=${py(example.symbol,'替换为币种')},\n    exchange=${py(example.exchange,'替换为市场')},\n    timeframe=${py(example.timeframe,'替换为周期')},\n    start=${py(example.start,'替换为开始时间')},\n    end=${py(example.end,'替换为结束时间')},\n)`;
  }
  function provenance(item) {
    const notes=arr(item.notes).map((note)=>text(note,'')).filter(Boolean);
    const originals=arr(item.original_paths);
    return `<details class="dataset-provenance"><summary>用途、来源与保留版本</summary>
      ${notes.length?`<ul>${notes.map((note)=>`<li>${esc(note)}</li>`).join('')}</ul>`:'<p>未登记额外用途、来源或保留版本说明。</p>'}
      ${originals.length?`<div class="dataset-original-paths"><strong>原始路径</strong><ul>${originals.map((path)=>`<li><code>${esc(text(path))}</code></li>`).join('')}</ul></div>`:''}
    </details>`;
  }
  function fileDetails(item) {
    const detail=state.detail;
    if (!detail||detail.id!==item.id) return '';
    if (detail.loading) return '<section class="dataset-file-detail"><h4>文件明细</h4><p role="status">正在读取文件列表…</p></section>';
    if (detail.error) return `<section class="dataset-file-detail"><h4>文件明细</h4><p class="dataset-inline-error" role="alert">${esc(detail.error)}</p><button type="button" class="research-button" data-dataset-retry-details="${esc(item.id)}">重试</button></section>`;
    const payload=detail.data||{}, files=arr(payload.files), total=Number(payload.total)||0, limit=Number(payload.limit)||FILE_PAGE_SIZE, offset=Number(payload.offset)||0;
    if (!files.length) return '<section class="dataset-file-detail"><h4>文件明细</h4><p class="research-caption">此数据集没有可列出的文件。</p></section>';
    return `<section class="dataset-file-detail"><div class="dataset-detail-heading"><h4>文件明细</h4><span>${esc(number(total,'0'))} 个文件</span></div>
      <div class="dataset-file-list">${files.map((file)=>{
        const href=`/api/research/datasets/${encodeURIComponent(item.id)}/file?path=${encodeURIComponent(file.path||'')}`;
        return `<article class="dataset-file-row"><div class="dataset-file-name"><code>${esc(file.path||'未记录')}</code><span>${esc(text(file.format))} · ${esc(bytes(file.size_bytes))}</span></div><dl>
          <dt>市场 / 周期</dt><dd>${esc(text(file.exchange))} · ${esc(text(file.timeframe))}</dd>
          <dt>币种</dt><dd>${esc(text(file.symbol))}</dd>
          <dt>覆盖</dt><dd>${esc(date(file.start))} – ${esc(date(file.end))}</dd>
          <dt>行数</dt><dd>${esc(number(file.row_count))}</dd>
        </dl><a class="research-button" href="${esc(href)}" download>只读下载</a></article>`;
      }).join('')}</div>
      <div class="dataset-pagination"><span>显示 ${esc(number(offset+1,'0'))}–${esc(number(Math.min(offset+files.length,total),'0'))} / ${esc(number(total,'0'))}</span><div>
        <button type="button" class="research-button" data-dataset-page="prev" data-dataset-id="${esc(item.id)}" ${offset<=0?'disabled':''}>上一页</button>
        <button type="button" class="research-button" data-dataset-page="next" data-dataset-id="${esc(item.id)}" ${offset+limit>=total?'disabled':''}>下一页</button>
      </div></div></section>`;
  }
  function itemCard(item) {
    const selected=state.detail?.id===item.id;
    const category=item.category_label||categoryName(item.category);
    const coverage=`开始 ${date(item.start)} · 结束 ${date(item.end)}`;
    const ready=item.backtest_ready===true;
    const example=ready?sampleCode(item):'';
    const missing=itemMissingCount(item), hasMissing=missing!==null&&missing>0;
    return `<article class="dataset-card${hasMissing?' has-missing-files':''}">
      <div class="dataset-card-top"><span class="dataset-category-tag">${esc(category)}</span><span class="dataset-storage-tag">${esc(storageName(item.storage))}</span>${hasMissing?`<span class="dataset-missing-tag">${esc(number(missing,'0'))} 个缺失文件</span>`:''}</div>
      <h3>${esc(item.name||item.id||'未命名数据集')}</h3><p class="dataset-id">${esc(item.id||'')}</p>
      <p class="dataset-path"><span>目录位置</span><code>${esc(item.path||'未知')}</code></p>
      <div class="dataset-metrics"><span>文件 <strong>${esc(number(item.file_count))}</strong></span><span>逻辑大小 <strong>${esc(bytes(item.total_bytes))}</strong></span><span>格式 <strong>${esc(listText(item.formats))}</strong></span></div>
      <dl class="dataset-facts"><dt>市场</dt><dd>${esc(listText(item.exchanges))}</dd><dt>周期</dt><dd>${esc(listText(item.timeframes))}</dd><dt>币种数</dt><dd>${esc(number(item.symbols_count))}</dd><dt>覆盖时间</dt><dd>${esc(coverage)}</dd></dl>
      ${provenance(item)}
      <div class="dataset-readiness ${ready?'ready':'not-ready'}"><strong>${ready?'回测读取：已登记':'回测读取：未登记'}</strong><span>已登记只说明此数据集可被回测读取，不代表 YOLO 训练资格或生产准入。</span></div>
      ${ready?`<div class="dataset-example"><div><strong>读取示例</strong><button type="button" class="research-button" data-dataset-copy="${esc(item.id)}">复制代码</button></div><pre><code>${esc(example)}</code></pre><p>${state.copyFor===item.id?esc(state.copyMessage):''}</p></div>`:''}
      <div class="dataset-card-footer"><span>索引记录 ${esc(number(item.file_count))} 个文件 · 逻辑大小 ${esc(bytes(item.total_bytes))}</span><button type="button" class="research-button" data-dataset-details="${esc(item.id)}">${selected?'收起文件明细':'查看文件明细'} →</button></div>
      ${selected?fileDetails(item):''}
    </article>`;
  }
  function renderItems() {
    if (!root||!state.data) return;
    const list=root.querySelector('#dataset-records');
    if (!list) return;
    const items=visibleItems();
    const countNode=root.querySelector('#dataset-result-count');
    if (countNode) countNode.textContent=`筛选结果 ${number(items.length,'0')} / 全部 ${number(arr(state.data.items).length,'0')} 个数据集`;
    const pageCount=Math.max(1,Math.ceil(items.length/DATASET_PAGE_SIZE));
    state.page=Math.min(Math.max(1,state.page),pageCount);
    const pageStart=(state.page-1)*DATASET_PAGE_SIZE;
    const visiblePage=items.slice(pageStart,pageStart+DATASET_PAGE_SIZE);
    const pagination=root.querySelector('#dataset-list-pagination');
    if (pagination) pagination.innerHTML=items.length>DATASET_PAGE_SIZE?`<span>显示 ${esc(number(pageStart+1,'0'))}–${esc(number(Math.min(pageStart+visiblePage.length,items.length),'0'))} / ${esc(number(items.length,'0'))} 个数据集 · 第 ${esc(number(state.page,'0'))} / ${esc(number(pageCount,'0'))} 页</span><div><button type="button" class="research-button" data-dataset-list-page="prev" ${state.page<=1?'disabled':''}>上一页</button><button type="button" class="research-button" data-dataset-list-page="next" ${state.page>=pageCount?'disabled':''}>下一页</button></div>`:'';
    if (!items.length) {
      const hasItems=arr(state.data.items).length>0;
      const hasFilters=Boolean(state.query.trim()||state.category||state.missingOnly);
      const copy=!state.data.indexed?'数据目录尚未建立索引；建立索引不会移动或删除数据。':hasItems?'当前筛选条件下没有匹配的数据集。':'当前已索引目录没有数据集。';
      list.innerHTML=`<div class="dataset-empty"><strong>${esc(!state.data.indexed?'等待数据目录索引':hasItems?'没有匹配的数据集':'目录索引为空')}</strong><p>${esc(copy)}</p>${hasItems&&hasFilters?'<button type="button" class="research-button" data-dataset-reset>清除筛选，显示全部数据集</button>':''}</div>`;
      return;
    }
    list.innerHTML=visiblePage.map(itemCard).join('');
  }
  function render() {
    if (!root) return;
    if (state.loading&&!state.data) {
      root.innerHTML='<div class="dataset-state" role="status"><span class="dataset-spinner" aria-hidden="true"></span><p>正在读取数据集目录…</p></div>';
      return;
    }
    if (!state.data) {
      root.innerHTML=`<div class="dataset-state"><strong>币圈数据集目录</strong>${state.error?`<p class="dataset-inline-error" role="alert">${esc(state.error)}</p>`:'<p>选择此菜单后读取本机已索引的数据集。</p>'}<button type="button" class="research-button primary" data-dataset-refresh>重试读取</button></div>`;
      return;
    }
    const data=state.data, summary=data.summary||{}, categories=arr(data.categories), maintenance=data.maintenance||{};
    const status=maintenance.status==='running'?'正在重新索引':maintenance.status==='failed'?'索引失败':data.indexed?'目录已索引':'尚未建立索引';
    const missingTotal=Number(summary.missing_count), missingAvailable=perItemMissingCountsAvailable(), affectedCount=affectedDatasets().length;
    const missingMismatch=missingAvailable&&Number.isFinite(missingTotal)&&affectedDatasets().reduce((sum,item)=>sum+itemMissingCount(item),0)!==missingTotal;
    const missingAction=missingAvailable&&affectedCount>0?`<button type="button" class="research-button" data-dataset-missing-filter aria-pressed="${state.missingOnly?'true':'false'}">${state.missingOnly?'取消缺失筛选':`查看受影响数据集（${esc(number(affectedCount,'0'))}）`}</button>`:'';
    const missingNote=missingTotal>0&&!missingAvailable?'<small>当前目录没有逐数据集缺失数，无法定位受影响条目。</small>':missingMismatch?'<small>汇总与逐数据集缺失数不一致，请刷新目录或重新索引核对。</small>':'';
    root.innerHTML=`<div class="datasets-workspace">
      <header class="datasets-hero"><div><span class="datasets-kicker">研究基础设施 / 数据目录</span><h2>币圈数据集</h2><p>统一查看已整理、原位保留与外部引用的数据目录，核对文件清单、覆盖范围和回测读取入口。</p><p class="datasets-generated">目录状态：<strong>${esc(status)}</strong> · 生成时间：${esc(date(data.generated_at))}</p></div><div class="datasets-actions"><button type="button" class="research-button" data-dataset-refresh ${state.loading?'disabled':''}>刷新目录</button><button type="button" class="research-button primary" data-dataset-reindex ${state.reindexing||maintenance.status==='running'?'disabled':''}>${state.reindexing?'正在提交…':'重新索引'}</button></div></header>
      ${state.error?`<p class="dataset-inline-error" role="alert">目录刷新失败：${esc(state.error)}</p>`:''}
      ${maintenance.status==='running'?'<p class="dataset-indexing" role="status">索引任务正在运行；点击“刷新目录”查看最新状态。</p>':''}
      ${maintenance.status==='failed'?`<p class="dataset-inline-error" role="alert">索引任务失败：${esc(text(maintenance.error,'未返回错误详情'))}</p>`:''}
      ${state.notice?`<p class="dataset-notice" role="status">${esc(state.notice)}</p>`:''}
      <section class="dataset-summary" aria-label="数据集目录统计"><article><span>数据集</span><strong>${esc(number(summary.dataset_count))}</strong></article><article><span>索引文件</span><strong>${esc(number(summary.file_count))}</strong></article><article><span>文件逻辑大小</span><strong>${esc(bytes(summary.total_bytes))}</strong></article><article><span>集中行情大小</span><strong>${esc(bytes(summary.managed_bytes))}</strong></article><article><span>已处理重复块</span><strong>${esc(bytes(summary.duplicate_bytes))}</strong></article><article class="dataset-summary-missing"><div><span>缺失文件</span><strong>${esc(number(summary.missing_count))}</strong></div>${missingAction}${missingNote}</article></section>
      <p class="dataset-storage-note">文件逻辑大小按目录文件统计；APFS 的 COW 共享会使物理占用不同。集中行情大小是集中管理目录的逻辑统计。已处理重复块表示已完成 COW 去重的字节数，反映整理状态。</p>
      <section class="dataset-category-section"><div class="dataset-section-heading"><div><h2>数据分类</h2><p>按索引登记的分类浏览；没有分类的条目仍可通过“全部”查看。</p></div></div>
        <div class="dataset-category-grid">${categoryFiltersHTML()}</div>
      </section>
      <section class="dataset-list-section"><div class="dataset-section-heading"><div><h2>数据集目录</h2><p id="dataset-result-count"></p></div><div class="dataset-filters"><label><span class="sr-only">搜索数据集</span><input id="dataset-search" type="search" value="${esc(state.query)}" placeholder="搜索名称、币种、路径或备注…" autocomplete="off"></label><label><span class="sr-only">筛选分类</span><select id="dataset-category"><option value="">全部分类</option>${categories.map((category)=>`<option value="${esc(category.id)}" ${state.category===category.id?'selected':''}>${esc(category.label||category.id)}</option>`).join('')}</select></label></div></div><div id="dataset-records" class="dataset-record-grid"></div><div id="dataset-list-pagination" class="dataset-list-pagination" aria-label="数据集目录分页"></div></section>
      <p class="dataset-footer-note">数据集目录为只读浏览；回测读取入口按目录登记状态展示。整理、去重与保留情况以索引事实为准。</p>
    </div>`;
    renderItems();
  }
  async function refresh() {
    if (!root||state.loading) return;
    clearDetail();
    state.loading=true; state.error=''; render();
    try {
      const {body}=await api('/datasets');
      if (!body||typeof body!=='object') throw new Error('服务未返回有效的数据集目录。');
      state.data=body; state.loaded=true; state.notice='';
    } catch (error) {
      state.error=error.message||'读取失败，请稍后重试。';
    } finally {
      state.loading=false; render();
    }
  }
  async function loadDetails(id, offset=0) {
    const revision=++state.detailRevision;
    state.detail={id,loading:true,error:'',data:null};
    renderItems();
    try {
      const {body}=await api(`/datasets/${encodeURIComponent(id)}?limit=${FILE_PAGE_SIZE}&offset=${Math.max(0,offset)}`);
      if (revision!==state.detailRevision) return;
      state.detail={id,loading:false,error:'',data:body};
    } catch (error) {
      if (revision!==state.detailRevision) return;
      state.detail={id,loading:false,error:error.message||'读取文件清单失败。',data:null};
    }
    renderItems();
  }
  async function reindex() {
    if (state.reindexing||state.data?.maintenance?.status==='running') return;
    state.reindexing=true; state.error=''; state.notice=''; render();
    try {
      const {status}=await api('/datasets/reindex',{method:'POST'});
      const acceptedNotice=status===202?'重新索引请求已接受；索引进度由后台维护，点击“刷新目录”查询。':'重新索引请求已提交。';
      await refresh();
      state.notice=state.data?.maintenance?.status==='running'?'索引任务正在后台运行；点击“刷新目录”查询最新状态。':acceptedNotice;
    } catch (error) {
      state.error=error.message||'提交索引请求失败。';
    } finally {
      state.reindexing=false; render();
    }
  }
  async function copyExample(id) {
    const item=arr(state.data?.items).find((entry)=>entry.id===id);
    if (!item||item.backtest_ready!==true) return;
    state.copyFor=id; state.copyMessage='';
    try {
      if (!navigator.clipboard?.writeText) throw new Error('浏览器未提供剪贴板权限');
      await navigator.clipboard.writeText(sampleCode(item));
      state.copyMessage='已复制';
    } catch (error) {
      state.copyMessage=`无法自动复制：${error.message||'请手动选择代码'}`;
    }
    renderItems();
  }
  function clearDetail() {
    state.detailRevision++;
    state.detail=null;
    state.copyFor=''; state.copyMessage='';
  }

  if (root) {
    root.innerHTML='<div class="dataset-state"><strong>币圈数据集目录</strong><p>选择此菜单后读取本机已索引的数据集。</p></div>';
    root.addEventListener('input',(event)=>{
      if (event.target.id==='dataset-search') { state.query=event.target.value; state.page=1; clearDetail(); renderItems(); }
    });
    root.addEventListener('change',(event)=>{
      if (event.target.id==='dataset-category') { state.category=event.target.value; state.page=1; clearDetail(); renderCategoryFilters(); renderItems(); }
    });
    root.addEventListener('click',async(event)=>{
      const button=event.target.closest('button');
      if (!button) return;
      if (button.matches('[data-dataset-refresh]')) { await refresh(); return; }
      if (button.matches('[data-dataset-reindex]')) { await reindex(); return; }
      if (button.matches('[data-dataset-category]')) {
        state.category=button.dataset.datasetCategory||''; state.page=1; clearDetail();
        const select=root.querySelector('#dataset-category'); if (select) select.value=state.category;
        renderCategoryFilters();
        renderItems(); return;
      }
      if (button.matches('[data-dataset-missing-filter]')) {
        state.missingOnly=!state.missingOnly; state.page=1; clearDetail(); render(); return;
      }
      if (button.matches('[data-dataset-reset]')) {
        state.query=''; state.category=''; state.missingOnly=false; state.page=1; clearDetail();
        const search=root.querySelector('#dataset-search'); if (search) search.value='';
        const select=root.querySelector('#dataset-category'); if (select) select.value='';
        render(); return;
      }
      if (button.matches('[data-dataset-list-page]')) {
        state.page=Math.max(1,state.page+(button.dataset.datasetListPage==='next'?1:-1));
        clearDetail(); renderItems(); return;
      }
      if (button.matches('[data-dataset-details]')) {
        const id=button.dataset.datasetDetails;
        if (state.detail?.id===id) { clearDetail(); renderItems(); }
        else await loadDetails(id,0);
        return;
      }
      if (button.matches('[data-dataset-retry-details]')) { await loadDetails(button.dataset.datasetRetryDetails,0); return; }
      if (button.matches('[data-dataset-page]')) {
        const current=state.detail?.data||{}, offset=Number(current.offset)||0, limit=Number(current.limit)||FILE_PAGE_SIZE;
        await loadDetails(button.dataset.datasetId,Math.max(0,offset+(button.dataset.datasetPage==='next'?limit:-limit)));
        return;
      }
      if (button.matches('[data-dataset-copy]')) await copyExample(button.dataset.datasetCopy);
    });
  }
  window.SpikeDatasets={
    setActive(active) { state.active=Boolean(active); if (state.active&&!state.loaded) return refresh(); return Promise.resolve(); },
    refresh,
  };
})();
