const API_BASE = '/api';
const DEFAULT_MODEL = 'gemini-3.8-flash';
const DEFAULT_CRITERIA = '只判断当前可见的双均线密集启动形态，不能利用未来涨跌；先密集后启动，不能确定则拒判；说明对应可观察证据。';
const MAX_IMAGE_BYTES = 8 * 1024 * 1024;
const MAX_TOTAL_IMAGE_BYTES = 12 * 1024 * 1024;
const MAX_REFERENCES = 4;
const ALLOWED_TYPES = new Set(['image/png', 'image/jpeg', 'image/webp']);

const state = {
  tab: 'workspace',
  status: null,
  statusError: '',
  signals: [],
  signalsLoading: true,
  signalsError: '',
  signalsWarning: '',
  runs: [],
  runsLoading: true,
  runsError: '',
  selectedSignal: null,
  selectedImage: null,
  imageToken: 0,
  imageAbort: null,
  references: [],
  activeRun: null,
  activeRunInputKey: null,
  analysisLoading: false,
  analysisError: '',
  configLoading: false,
  connectionTesting: false,
  lastConnection: null,
  notice: '',
  reviewLoadingId: null,
  exportLoadingId: null,
  model: DEFAULT_MODEL,
  criteria: DEFAULT_CRITERIA,
  criteriaEdited: false,
};

const byId = (id) => document.getElementById(id);
const healthEl = byId('api-health');
const signalsEl = byId('signal-list');
const resultEl = byId('result-content');
const historyEl = byId('history-list');
const workspaceErrorEl = byId('workspace-error');

function escapeHtml(value) {
  return String(value ?? '').replace(/[&<>"']/g, (char) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  })[char]);
}

function asText(value, fallback = '') {
  if (value === null || value === undefined || value === '') return fallback;
  if (typeof value === 'string' || typeof value === 'number' || typeof value === 'boolean') return String(value);
  try { return JSON.stringify(value); } catch { return fallback; }
}

function setVisible(element, visible) {
  if (element) element.hidden = !visible;
}

function showMessage(element, message, kind = 'error') {
  if (!element) return;
  element.textContent = message;
  element.classList.toggle('notice-error', kind === 'error');
  element.classList.toggle('notice-info', kind === 'info');
  setVisible(element, Boolean(message));
}

function showInlineError(id, message) {
  const element = byId(id);
  if (!element) return;
  element.textContent = message || '';
  setVisible(element, Boolean(message));
}

function showInlineSuccess(id, message) {
  const element = byId(id);
  if (!element) return;
  element.textContent = message || '';
  setVisible(element, Boolean(message));
}

async function readResponse(response) {
  const contentType = response.headers.get('content-type') || '';
  let payload;
  if (contentType.includes('application/json')) {
    try { payload = await response.json(); } catch { payload = null; }
  } else {
    try { payload = await response.text(); } catch { payload = ''; }
  }
  if (!response.ok) {
    const detail = payload && typeof payload === 'object' ? payload.detail || payload.message : payload;
    throw new Error(asText(detail, `请求失败（HTTP ${response.status}）`));
  }
  return payload;
}

async function apiJson(path, options = {}) {
  const headers = new Headers(options.headers || {});
  if (options.body !== undefined && !headers.has('content-type')) headers.set('content-type', 'application/json');
  const response = await fetch(`${API_BASE}${path}`, { ...options, headers });
  return readResponse(response);
}

function formatDate(value) {
  if (!value) return '时间未提供';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return asText(value, '时间未提供');
  return new Intl.DateTimeFormat('zh-CN', { dateStyle: 'medium', timeStyle: 'short' }).format(date);
}

function sideLabel(side) {
  return ({ long: '做多方向', short: '做空方向', unknown: '方向未确定' })[side] || (side ? asText(side) : '方向未返回');
}

function verdictLabel(verdict) {
  return ({ match: '符合', no_match: '不符合', uncertain: '不确定', accepted: '接受', rejected: '拒绝' })[verdict] || '未返回判断';
}

function sourceLabel(source) {
  const labels = {
    local: '本地数据', spike: 'SPIKE', cache: '本机缓存', database: '本地数据库',
    environment: '后端环境变量', env: '后端环境变量', session: '本机进程会话',
    memory: '本机进程会话', process: '本机进程会话', upload: '本地上传图表', unset: '未配置', none: '未配置',
  };
  return labels[source] || asText(source, '来源未提供');
}

function safeList(items) {
  if (!Array.isArray(items)) return [];
  return items.map((item) => {
    if (typeof item === 'string' || typeof item === 'number') return String(item);
    if (item && typeof item === 'object') {
      const title = item.title || item.label || item.text || item.description;
      if (title) return String(title);
      return asText(item, '');
    }
    return asText(item, '');
  }).filter(Boolean);
}

function setHealth(ok, message) {
  healthEl.classList.toggle('is-online', ok);
  healthEl.classList.toggle('is-offline', !ok);
  const text = healthEl.querySelector('span:last-child');
  if (text) text.textContent = message;
}

function renderEligibility() {
  const target = byId('eligibility-badges');
  if (!target) return;
  const status = state.status;
  const label = (value) => value === false ? '否' : value === true ? '是 · 状态由后端返回' : '未读取';
  const className = (value) => value === false ? 'is-false' : value === true ? 'is-true' : '';
  target.innerHTML = `<span class="soft-badge ${className(status?.production_eligible)}">生产资格：${escapeHtml(label(status?.production_eligible))}</span>
    <span class="soft-badge ${className(status?.training_eligible)}">训练资格：${escapeHtml(label(status?.training_eligible))}</span>`;
}

function renderSignalSource() {
  const caption = byId('signal-source');
  const spike = state.status?.spike;
  caption.textContent = state.signalsLoading ? '正在读取候选…'
    : state.signalsError ? '读取失败'
      : spike?.available === false ? 'SPIKE 暂不可用'
        : `SPIKE · ${state.signals.length} 条信号`;
  caption.title = spike?.source ? `来源：${sourceLabel(spike.source)}` : '';

  const warning = state.signalsWarning || spike?.warning || '';
  if (warning) {
    byId('workspace-notice').textContent = `候选来源提示：${warning}`;
    setVisible(byId('workspace-notice'), true);
  } else if (!state.notice) {
    setVisible(byId('workspace-notice'), false);
  }
}

function getFilteredSignals() {
  const needle = (byId('signal-search')?.value || '').trim().toLowerCase();
  if (!needle) return state.signals;
  return state.signals.filter((item) => [item.symbol, item.timeframe, item.side, item.id, item.signal_at]
    .some((value) => asText(value).toLowerCase().includes(needle)));
}

function renderSignals() {
  renderSignalSource();
  showInlineError('signals-error', state.signalsError);
  if (state.signalsLoading && !state.signals.length) {
    signalsEl.innerHTML = '<div class="list-skeleton" aria-label="正在加载候选信号"><span></span><span></span><span></span></div>';
    return;
  }
  const items = getFilteredSignals();
  if (!items.length) {
    const text = state.signalsError ? '暂时无法读取候选。可以稍后重试，或上传本地图表继续。'
      : byId('signal-search').value.trim() ? '没有符合搜索条件的候选。'
        : '当前没有可用的 SPIKE 候选。可以上传本地图表进行研究。';
    signalsEl.innerHTML = `<div class="empty-list">${escapeHtml(text)}</div>`;
    return;
  }
  signalsEl.innerHTML = items.map((item) => {
    const id = asText(item.id);
    const selected = state.selectedSignal?.id === item.id;
    const sideClass = item.side === 'short' ? 'is-short' : '';
    const symbol = asText(item.symbol, '未知币种');
    const timeframe = asText(item.timeframe, '周期未提供');
    const side = sideLabel(item.side);
    const time = item.signal_at ? formatDate(item.signal_at) : '时间未提供';
    return `<button class="signal-item ${selected ? 'is-selected' : ''}" type="button" data-signal-id="${escapeHtml(id)}" aria-pressed="${selected}" aria-label="载入 ${escapeHtml(symbol)} ${escapeHtml(timeframe)} ${escapeHtml(side)} 候选" title="${escapeHtml(symbol)} · ${escapeHtml(id)}">
      <span class="signal-topline"><strong>${escapeHtml(symbol.replace(/-SWAP$/, ''))}</strong><span class="signal-timeframe">${escapeHtml(timeframe)}</span></span>
      <span class="signal-meta"><span class="signal-side ${sideClass}">${escapeHtml(side.replace('方向', ''))}</span><time>${escapeHtml(time)}</time></span>
    </button>`;
  }).join('');
}

function renderImage() {
  const image = state.selectedImage;
  const empty = byId('empty-stage');
  const loading = byId('image-loading');
  const error = byId('image-error');
  const display = byId('image-display');
  const preview = byId('chart-preview');
  const chip = byId('input-source-chip');
  const summary = byId('selection-summary');
  const box = byId('box-overlay');

  byId('clear-input').disabled = !image;
  setVisible(chip, Boolean(image));
  if (!image) {
    setVisible(empty, true);
    setVisible(loading, false);
    setVisible(error, false);
    setVisible(display, false);
    setVisible(summary, false);
    chip.textContent = '尚未选择图片';
    chip.classList.remove('is-upload');
    setVisible(box, false);
    preview.removeAttribute('src');
    return;
  }
  setVisible(empty, false);
  setVisible(loading, Boolean(image.loading));
  setVisible(error, Boolean(image.error));
  if (image.error) error.textContent = image.error;
  setVisible(display, Boolean(image.previewUrl) && !image.loading && !image.error);
  if (image.previewUrl && preview.src !== image.previewUrl) preview.src = image.previewUrl;
  preview.alt = image.name ? `当前图表：${image.name}` : '当前选择的图表';
  chip.textContent = image.source === 'signal' ? 'SPIKE 候选'
    : image.source === 'history' ? '历史记录' : image.name;
  chip.classList.toggle('is-upload', image.source === 'upload');
  if (image.source === 'signal' && image.signal) {
    const sig = image.signal;
    summary.textContent = `${asText(sig.symbol, '未知币种')} · ${asText(sig.timeframe, '周期未提供')} · ${sideLabel(sig.side)} · ${formatDate(sig.signal_at)}`;
    setVisible(summary, true);
  } else {
    setVisible(summary, false);
  }
  renderOverlay();
}

function normalizedBox(run) {
  const box = run?.decision?.box_2d;
  if (!Array.isArray(box) || box.length !== 4) return null;
  const values = box.map(Number);
  if (!values.every((v) => Number.isFinite(v) && v >= 0 && v <= 1000)) return null;
  const [yMin, xMin, yMax, xMax] = values;
  if (yMax <= yMin || xMax <= xMin) return null;
  return values;
}

function renderOverlay() {
  const boxElement = byId('box-overlay');
  const image = state.selectedImage;
  const box = normalizedBox(state.activeRun);
  const canShow = Boolean(image && box && state.activeRunInputKey === image.key && image.previewUrl && !image.loading && !image.error);
  if (!canShow) {
    setVisible(boxElement, false);
    return;
  }
  const [yMin, xMin, yMax, xMax] = box;
  boxElement.style.left = `${xMin / 10}%`;
  boxElement.style.top = `${yMin / 10}%`;
  boxElement.style.width = `${(xMax - xMin) / 10}%`;
  boxElement.style.height = `${(yMax - yMin) / 10}%`;
  boxElement.setAttribute('aria-label', `识别框：左 ${xMin / 10}%，上 ${yMin / 10}%，右 ${xMax / 10}%，下 ${yMax / 10}%`);
  setVisible(boxElement, true);
}

function renderReferences() {
  const list = byId('reference-list');
  if (!state.references.length) {
    list.innerHTML = '<span class="reference-empty">未添加参考图</span>';
    return;
  }
  list.innerHTML = state.references.map((reference, index) => `<div class="reference-thumb" title="${escapeHtml(reference.name)}">
    <img src="${escapeHtml(reference.previewUrl)}" alt="参考图 ${index + 1}：${escapeHtml(reference.name)}" />
    <span class="reference-label">${escapeHtml(reference.name)}</span>
    <button class="reference-remove" type="button" data-remove-reference="${index}" aria-label="移除参考图 ${escapeHtml(reference.name)}">×</button>
  </div>`).join('');
  byId('add-reference').disabled = state.references.length >= MAX_REFERENCES;
}

function renderRunStatus() {
  const status = byId('run-status');
  status.className = 'run-status';
  setVisible(status, Boolean(state.analysisLoading || state.analysisError || state.activeRun));
  if (state.analysisLoading) {
    status.textContent = '正在识别';
    status.classList.add('is-running');
  } else if (state.analysisError) {
    status.textContent = '请求失败';
    status.classList.add('is-failed');
  } else if (state.activeRun) {
    if (state.activeRun.status && state.activeRun.status !== 'completed') {
      status.textContent = '识别未完成';
      status.classList.add('is-failed');
    } else {
      status.textContent = '已返回结果';
      status.classList.add('is-done');
    }
  } else if (state.selectedImage?.loading) {
    status.textContent = '载入图片中';
  } else if (state.selectedImage) {
    status.textContent = '可以识别';
  } else {
    status.textContent = '等待输入';
  }
}

function renderResult() {
  renderRunStatus();
  const canAnalyze = Boolean(state.selectedImage && !state.selectedImage.loading && !state.selectedImage.error
    && (state.selectedSignal || (state.selectedImage.source === 'upload' && state.selectedImage.dataUrl))
    && !state.analysisLoading);
  byId('analyze-button').disabled = !canAnalyze;
  byId('analyze-button').querySelector('.button-label').textContent = state.analysisLoading ? '正在识别…' : '开始识别';
  setVisible(byId('analyze-button').querySelector('.button-spinner'), state.analysisLoading);
  showInlineError('analyze-error', state.analysisError);

  if (!state.activeRun) {
    resultEl.innerHTML = `<div class="result-empty">
      <h3>${state.analysisLoading ? '正在分析图表…' : '等待识别'}</h3>
      <p>${state.analysisLoading ? '通常需要 60–90 秒。' : state.selectedImage ? '点击「开始识别」查看分析。' : '选择图表后，分析结果会显示在这里。'}</p></div>`;
    return;
  }
  const run = state.activeRun;
  const decision = run.decision || {};
  const verdict = decision.verdict;
  const verdictClass = verdict === 'no_match' ? 'no-match' : verdict === 'uncertain' ? 'uncertain' : '';
  const incomplete = run.status && run.status !== 'completed';
  const mark = incomplete ? '!' : verdict === 'match' ? '✓' : verdict === 'no_match' ? '×' : '…';
  const headingLabel = incomplete ? '识别未完成' : verdictLabel(verdict);
  const evidence = safeList(decision.evidence);
  const risks = safeList(decision.risks);
  const human = run.review;
  const evidenceHtml = evidence.length ? `<ul class="result-list">${evidence.map((item) => `<li>${escapeHtml(item)}</li>`).join('')}</ul>` : '<span class="list-blank">模型未返回可观察证据。</span>';
  const risksHtml = risks.length ? `<ul class="result-list">${risks.map((item) => `<li>${escapeHtml(item)}</li>`).join('')}</ul>` : '<span class="list-blank">模型未列出风险说明。</span>';
  const model = asText(run.model, state.status?.model || state.model);
  const created = formatDate(run.created_at);
  const latency = run.latency_ms != null && Number.isFinite(Number(run.latency_ms)) ? `${Math.round(Number(run.latency_ms))} ms` : '';
  const usage = run.usage && typeof run.usage === 'object' ? Object.entries(run.usage).map(([key, value]) => `${key}: ${asText(value)}`).join(' · ') : '';
  const referenceNames = Array.isArray(run.references) ? run.references.map((reference) => asText(reference?.name, '参考图')).filter(Boolean) : [];
  const criteria = asText(run.criteria, '记录中没有保存形态规则。');
  const requestSource = sourceLabel(run.source || (run.symbol ? 'spike' : 'upload'));
  const requestContext = `<details class="request-context"><summary>识别详情</summary><dl>
    <dt>图片来源</dt><dd>${escapeHtml(requestSource)} · ${escapeHtml(asText(run.image_name, state.selectedImage?.name || '图片名称未返回'))}</dd>
    <dt>参考图</dt><dd>${referenceNames.length ? escapeHtml(referenceNames.join('、')) : '未使用参考图'}</dd>
    <dt>本次规则</dt><dd>${escapeHtml(criteria)}</dd>
    <dt>模型</dt><dd>${escapeHtml(model)}</dd>
    <dt>时间</dt><dd>${escapeHtml(created)}</dd>
    ${latency ? `<dt>耗时</dt><dd>${escapeHtml(latency)}</dd>` : ''}
    ${usage ? `<dt>用量</dt><dd>${escapeHtml(usage)}</dd>` : ''}
    ${run.image_sha256 ? `<dt>图片 SHA-256</dt><dd class="hash-value">${escapeHtml(run.image_sha256)}</dd>` : ''}
  </dl></details>`;
  const reviewVerdict = human ? `<div class="review-verdict">人工复核：${escapeHtml(verdictLabel(human.verdict))}${human.note ? ` · ${escapeHtml(human.note)}` : ''}<br><span>${escapeHtml(formatDate(human.reviewed_at))}</span></div>` : '';
  const runId = asText(run.id, '');
  const reviewControls = incomplete ? '<p class="review-locked">只有已完成的识别可以人工复核。</p>' : `<label class="sr-only" for="review-note">人工复核备注</label>
      <textarea id="review-note" class="review-note" maxlength="1200" placeholder="可选：记录边界或依据">${human?.note ? escapeHtml(human.note) : ''}</textarea>
      <div class="review-buttons" role="group" aria-label="记录人工复核结论">
        <button type="button" data-review-verdict="accepted" data-review-run="${escapeHtml(runId)}">接受</button>
        <button type="button" data-review-verdict="rejected" data-review-run="${escapeHtml(runId)}">拒绝</button>
        <button type="button" data-review-verdict="uncertain" data-review-run="${escapeHtml(runId)}">不确定</button>
      </div>`;
  resultEl.innerHTML = `<article class="decision-card">
    <div class="decision-topline"><span class="decision-label"><span class="decision-mark ${incomplete ? 'failed' : verdictClass}" aria-hidden="true">${mark}</span>${escapeHtml(headingLabel)}</span><span class="side-tag">${escapeHtml(sideLabel(decision.side))}</span></div>
    <p class="result-summary">${escapeHtml(asText(decision.summary, run.error || '模型未返回摘要。'))}</p>
    <section class="result-section" aria-label="可观察证据"><h4>可观察证据</h4>${evidenceHtml}</section>
    <section class="result-section risks" aria-label="不确定性与风险"><h4>不确定性与风险</h4>${risksHtml}</section>
    ${requestContext}
    <section class="human-review" aria-label="人工复核">
      <div class="human-review-heading"><strong>人工复核</strong><span>与模型结果分别记录</span></div>
      ${reviewVerdict}
      ${reviewControls}
    </section>
  </article>`;
  if (state.reviewLoadingId === runId) {
    resultEl.querySelectorAll('[data-review-verdict]').forEach((button) => { button.disabled = true; });
  }
  renderOverlay();
}

function renderHistory() {
  const count = byId('history-count');
  count.textContent = String(state.runs.length);
  setVisible(count, state.runs.length > 0);
  showInlineError('history-error', state.runsError);
  if (state.runsLoading && !state.runs.length) {
    historyEl.innerHTML = '<div class="history-empty"><span class="spinner" aria-hidden="true"></span>正在读取记录…</div>';
    return;
  }
  if (!state.runs.length) {
    historyEl.innerHTML = `<div class="history-empty is-empty">${state.runsError ? '暂时无法读取记录，请检查本地 API 后重试。' : '还没有识别记录。完成一次手动识别后，API 返回的记录会显示在这里。'}</div>`;
    return;
  }
  historyEl.innerHTML = state.runs.map((run) => {
    const decision = run.decision || {};
    const verdict = decision.verdict;
    const verdictClass = verdict === 'no_match' ? 'no-match' : verdict === 'uncertain' ? 'uncertain' : '';
    const model = asText(run.model, '模型未提供');
    const symbol = asText(run.symbol || run.signal?.symbol, '自定义图片');
    const timeframe = asText(run.timeframe || run.signal?.timeframe, '周期未提供');
    const review = run.review ? `<div class="history-review"><span>人工复核</span><strong>${escapeHtml(verdictLabel(run.review.verdict))}</strong>${run.review.note ? `<span>${escapeHtml(run.review.note)}</span>` : ''}<time>${escapeHtml(formatDate(run.review.reviewed_at))}</time></div>` : '';
    const runId = asText(run.id, '');
    const summary = asText(decision.summary, run.error || '无摘要');
    const isExporting = state.exportLoadingId === runId;
    return `<article class="history-card">
      <div class="history-main"><div class="history-head"><h2>${escapeHtml(symbol)} · ${escapeHtml(timeframe)}</h2><span class="verdict-pill ${verdictClass}">${escapeHtml(verdictLabel(verdict))}</span></div>
        <p class="history-summary">${escapeHtml(summary)}</p>
        <div class="history-meta"><time>${escapeHtml(formatDate(run.created_at))}</time><span>${escapeHtml(model)}</span><span>${escapeHtml(asText(run.status, '状态未提供'))}</span>${run.latency_ms ? `<span>${escapeHtml(Math.round(Number(run.latency_ms)))} ms</span>` : ''}</div>
      </div>
      <div class="history-actions"><button class="button button-secondary" type="button" data-open-run="${escapeHtml(runId)}">查看 / 复核</button><button class="button button-quiet" type="button" data-export-run="${escapeHtml(runId)}" ${isExporting ? 'disabled' : ''}>${isExporting ? '导出中…' : '导出 JSON'}</button></div>
      ${review}
    </article>`;
  }).join('');
}

function credentialSummary(source) {
  const known = sourceLabel(source);
  const lower = asText(source).toLowerCase();
  const configured = !['', 'unset', 'none', 'missing', 'not_configured', 'null'].includes(lower);
  return { label: known, configured };
}

function renderSettings() {
  const statusTarget = byId('settings-status');
  const modelInput = byId('model-input');
  if (state.status) {
    const key = credentialSummary(state.status.credential_source);
    statusTarget.innerHTML = `<div class="status-line"><strong>本地 API</strong><span>已连接</span></div>
      <div class="status-line"><strong>当前模型</strong><code>${escapeHtml(asText(state.status.model, state.model))}</code></div>
      <div class="status-line"><strong>凭据来源</strong><span class="status-key ${key.configured ? '' : 'is-missing'}">${escapeHtml(key.label)}</span></div>
      <div class="status-line"><strong>SPIKE 数据源</strong><span>${state.status.spike?.available === true ? `可用 · ${escapeHtml(asText(state.status.spike.count, ''))} 条 · ${escapeHtml(sourceLabel(state.status.spike.source))}` : state.status.spike?.available === false ? '当前不可用' : '状态未返回'}</span></div>`;
    if (!modelInput.matches(':focus')) modelInput.value = state.status.model || state.model;
  } else if (state.statusError) {
    statusTarget.innerHTML = `<div class="inline-error">无法读取本机 API 状态：${escapeHtml(state.statusError)}</div>`;
  } else {
    statusTarget.innerHTML = '<div class="status-loading"><span class="spinner" aria-hidden="true"></span>正在读取服务状态…</div>';
  }
  const label = byId('save-config').querySelector('.button-label');
  label.textContent = state.configLoading ? '正在保存…' : '保存设置';
  setVisible(byId('save-config').querySelector('.button-spinner'), state.configLoading);
  byId('save-config').disabled = state.configLoading;
  const testLabel = byId('connection-test').querySelector('.button-label');
  testLabel.textContent = state.connectionTesting ? '正在测试…' : '测试 Gemini 连接';
  setVisible(byId('connection-test').querySelector('.button-spinner'), state.connectionTesting);
  byId('connection-test').disabled = state.connectionTesting;
  if (state.lastConnection) {
    const output = byId('connection-result');
    output.className = `connection-result ${state.lastConnection.ok ? 'is-ok' : 'is-bad'}`;
    output.textContent = `${state.lastConnection.ok ? '连接成功' : '连接未通过'} · ${asText(state.lastConnection.model, state.model)} · ${asText(state.lastConnection.message, '')}`;
    setVisible(output, true);
  }
}

function renderNotice() {
  const element = byId('workspace-notice');
  if (state.notice) {
    element.textContent = state.notice;
    element.className = 'notice notice-info';
    setVisible(element, true);
  } else {
    renderSignalSource();
  }
}

function renderWorkspace() {
  renderEligibility();
  renderSignals();
  renderImage();
  renderReferences();
  renderResult();
  renderNotice();
}

function renderAll() {
  renderWorkspace();
  renderHistory();
  renderSettings();
  for (const button of document.querySelectorAll('[data-tab]')) {
    const active = button.dataset.tab === state.tab;
    button.classList.toggle('is-active', active);
    button.setAttribute('aria-selected', String(active));
    button.tabIndex = active ? 0 : -1;
  }
  for (const id of ['workspace', 'history', 'settings']) {
    const panel = byId(`tab-${id}`);
    setVisible(panel, state.tab === id);
  }
}

async function loadStatus() {
  try {
    const status = await apiJson('/status');
    if (!status || typeof status !== 'object') throw new Error('本地 API 状态格式无效。');
    state.status = status;
    state.statusError = '';
    state.model = asText(status.model, state.model || DEFAULT_MODEL);
    if (!state.criteriaEdited && typeof status.default_criteria === 'string' && status.default_criteria.trim()) {
      state.criteria = status.default_criteria;
      byId('criteria-input').value = status.default_criteria;
      byId('criteria-count').textContent = `${status.default_criteria.length} 字`;
    }
    setHealth(true, '本地 API 已连接');
  } catch (error) {
    state.statusError = error.message || '无法连接本地 API。';
    setHealth(false, '本地 API 未连接');
  }
  renderAll();
}

async function loadSignals() {
  state.signalsLoading = true;
  state.signalsError = '';
  renderSignals();
  try {
    const payload = await apiJson('/signals');
    state.signals = Array.isArray(payload?.items) ? payload.items.filter((item) => item && typeof item === 'object') : [];
    state.signalsWarning = asText(payload?.warning, '');
    state.signalsError = '';
  } catch (error) {
    state.signals = [];
    state.signalsError = error.message || '候选信号读取失败。';
  } finally {
    state.signalsLoading = false;
    renderSignals();
  }
}

async function loadRuns() {
  state.runsLoading = true;
  state.runsError = '';
  renderHistory();
  try {
    const payload = await apiJson('/runs');
    state.runs = Array.isArray(payload?.items) ? payload.items.filter((item) => item && typeof item === 'object') : [];
    state.runsError = '';
  } catch (error) {
    state.runsError = error.message || '记录读取失败。';
  } finally {
    state.runsLoading = false;
    renderHistory();
  }
}

function revokeImage(image) {
  if (image?.previewUrl?.startsWith('blob:')) URL.revokeObjectURL(image.previewUrl);
}

function clearActiveImage({ keepNotice = false } = {}) {
  state.imageToken += 1;
  state.imageAbort?.abort();
  state.imageAbort = null;
  revokeImage(state.selectedImage);
  state.selectedSignal = null;
  state.selectedImage = null;
  state.activeRun = null;
  state.activeRunInputKey = null;
  state.analysisError = '';
  if (!keepNotice) state.notice = '';
  renderWorkspace();
}

async function selectSignal(signal) {
  if (!signal?.id) return;
  const id = asText(signal.id);
  if (state.selectedSignal?.id === signal.id && state.selectedImage?.loading) return;
  state.imageToken += 1;
  const token = state.imageToken;
  state.imageAbort?.abort();
  state.imageAbort = new AbortController();
  revokeImage(state.selectedImage);
  state.selectedSignal = signal;
  state.selectedImage = { key: `signal:${id}`, name: `${asText(signal.symbol, '候选')} · ${asText(signal.timeframe, '')}`, source: 'signal', signal, loading: true, previewUrl: null, dataUrl: null };
  state.activeRun = null;
  state.activeRunInputKey = null;
  state.analysisError = '';
  state.notice = '';
  showMessage(workspaceErrorEl, '');
  renderWorkspace();
  try {
    const response = await fetch(`${API_BASE}/signals/${encodeURIComponent(id)}/image`, { signal: state.imageAbort.signal });
    const contentType = response.headers.get('content-type') || '';
    if (!response.ok || contentType.includes('application/json')) {
      let detail = `候选图表读取失败（HTTP ${response.status}）。`;
      if (contentType.includes('application/json')) {
        try {
          const payload = await response.json();
          detail = asText(payload?.detail, detail);
        } catch { /* Keep the HTTP description. */ }
      }
      throw new Error(detail);
    }
    const blob = await response.blob();
    if (!ALLOWED_TYPES.has(blob.type) && blob.type !== 'image/avif') {
      throw new Error(`候选图片格式无法显示${blob.type ? `（${blob.type}）` : ''}。`);
    }
    const previewUrl = URL.createObjectURL(blob);
    await verifyImageUrl(previewUrl);
    if (token !== state.imageToken || state.selectedImage?.key !== `signal:${id}`) {
      URL.revokeObjectURL(previewUrl);
      return;
    }
    state.selectedImage = {
      ...state.selectedImage,
      loading: false,
      previewUrl,
      byteSize: blob.size,
      sha256: response.headers.get('X-Image-SHA256') || null,
    };
    renderWorkspace();
  } catch (error) {
    if (error.name === 'AbortError') return;
    if (token !== state.imageToken || state.selectedImage?.key !== `signal:${id}`) return;
    state.selectedImage = { ...state.selectedImage, loading: false, error: error.message || '候选图表读取失败。' };
    renderWorkspace();
  }
}

function inferMime(file) {
  if (ALLOWED_TYPES.has(file.type)) return file.type;
  const extension = file.name.toLowerCase().split('.').pop();
  return ({ png: 'image/png', jpg: 'image/jpeg', jpeg: 'image/jpeg', webp: 'image/webp' })[extension] || '';
}

function validateFile(file) {
  if (!file) throw new Error('没有选择图片文件。');
  const mime = inferMime(file);
  if (!mime || !ALLOWED_TYPES.has(mime)) throw new Error('仅支持 PNG、JPEG 或 WEBP 图片。');
  if (file.size > MAX_IMAGE_BYTES) throw new Error('图片超过 8 MB，请选择较小的图片。');
  return mime;
}

function readFileDataUrl(file, mime) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onerror = () => reject(new Error('读取图片文件失败。'));
    reader.onabort = () => reject(new Error('图片读取已取消。'));
    reader.onload = () => {
      const result = asText(reader.result);
      const comma = result.indexOf(',');
      if (comma < 0) return reject(new Error('图片编码格式无效。'));
      resolve(`data:${mime};base64,${result.slice(comma + 1)}`);
    };
    reader.readAsDataURL(file);
  });
}

function verifyImageUrl(url) {
  return new Promise((resolve, reject) => {
    const image = new Image();
    image.onload = () => image.naturalWidth > 0 && image.naturalHeight > 0 ? resolve() : reject(new Error('图片内容无法解码。'));
    image.onerror = () => reject(new Error('图片内容无法解码，请确认文件完整。'));
    image.src = url;
  });
}

async function addPrimaryImage(file) {
  state.imageToken += 1;
  const token = state.imageToken;
  state.imageAbort?.abort();
  state.imageAbort = null;
  revokeImage(state.selectedImage);
  const key = `upload:${crypto.randomUUID ? crypto.randomUUID() : `${Date.now()}-${Math.random().toString(36).slice(2)}`}`;
  state.selectedSignal = null;
  state.selectedImage = { key, name: file?.name || '本地图表', source: 'upload', previewUrl: null, dataUrl: null, loading: true, error: '' };
  state.activeRun = null;
  state.activeRunInputKey = null;
  state.analysisError = '';
  state.notice = '';
  showMessage(workspaceErrorEl, '');
  renderWorkspace();
  try {
    const mime = validateFile(file);
    const dataUrl = await readFileDataUrl(file, mime);
    await verifyImageUrl(dataUrl);
    if (token !== state.imageToken || state.selectedImage?.key !== key) return;
    state.selectedImage = { ...state.selectedImage, previewUrl: dataUrl, dataUrl, byteSize: file.size, loading: false, error: '' };
    renderWorkspace();
  } catch (error) {
    if (token !== state.imageToken || state.selectedImage?.key !== key) return;
    state.selectedImage = { ...state.selectedImage, loading: false, error: error.message || '无法读取这张图片。' };
    renderWorkspace();
  } finally {
    byId('chart-file').value = '';
  }
}

async function addReferenceFiles(files) {
  const remaining = MAX_REFERENCES - state.references.length;
  if (remaining <= 0) {
    state.notice = '参考图片最多添加 4 张。';
    renderNotice();
    byId('reference-file').value = '';
    return;
  }
  const selected = Array.from(files).slice(0, remaining);
  const rejected = Math.max(0, files.length - selected.length);
  const added = [];
  const errors = [];
  let pendingBytes = Number(state.selectedImage?.byteSize || 0)
    + state.references.reduce((total, reference) => total + Number(reference.byteSize || 0), 0);
  for (const file of selected) {
    try {
      const mime = validateFile(file);
      if (pendingBytes + file.size > MAX_TOTAL_IMAGE_BYTES) {
        throw new Error('待判图与参考图合计不能超过 12 MB。');
      }
      const dataUrl = await readFileDataUrl(file, mime);
      await verifyImageUrl(dataUrl);
      added.push({ id: `${Date.now()}-${Math.random().toString(36).slice(2)}`, name: file.name, dataUrl, previewUrl: dataUrl, byteSize: file.size });
      pendingBytes += file.size;
    } catch (error) {
      errors.push(`${file.name}: ${error.message}`);
    }
  }
  state.references.push(...added);
  if (rejected) errors.push(`超过最多 4 张的限制，忽略 ${rejected} 张。`);
  state.notice = errors.length ? errors.join(' ') : added.length ? `已添加 ${added.length} 张参考图；识别时会与主图一并发送。` : '';
  byId('reference-file').value = '';
  renderWorkspace();
}

function removeReference(index) {
  if (!Number.isInteger(index) || index < 0 || index >= state.references.length) return;
  state.references.splice(index, 1);
  renderReferences();
}

async function analyze() {
  if (state.analysisLoading || !state.selectedImage) return;
  if (state.selectedImage.source === 'history') {
    state.notice = '历史记录只供查看和人工复核；选择新候选或上传图片后可开始新的识别。';
    renderNotice();
    return;
  }
  const image = state.selectedImage;
  const signal = state.selectedSignal;
  if (image.loading || image.error || (!signal && !image.dataUrl)) return;
  const totalImageBytes = Number(image.byteSize || 0)
    + state.references.reduce((total, reference) => total + Number(reference.byteSize || 0), 0);
  if (totalImageBytes > MAX_TOTAL_IMAGE_BYTES) {
    state.analysisError = '待判图与参考图合计超过 12 MB，请移除部分参考图后重试。';
    renderResult();
    return;
  }
  const inputKey = image.key;
  state.analysisLoading = true;
  state.analysisError = '';
  state.activeRun = null;
  state.activeRunInputKey = null;
  state.notice = '';
  renderResult();
  const payload = {
    signal_id: signal ? asText(signal.id) : null,
    image_data_url: signal ? null : image.dataUrl,
    image_name: signal ? null : image.name,
    expected_image_sha256: signal ? image.sha256 || null : null,
    references: state.references.map(({ name, dataUrl }) => ({ name, data_url: dataUrl })),
    criteria: byId('criteria-input').value.trim() || DEFAULT_CRITERIA,
    model: state.model || null,
  };
  try {
    const run = await apiJson('/analyze', { method: 'POST', body: JSON.stringify(payload) });
    if (!run || typeof run !== 'object') throw new Error('识别接口未返回记录对象。');
    if (run.id && !state.runs.some((item) => item.id === run.id)) state.runs.unshift(run);
    else if (run.id) state.runs = state.runs.map((item) => item.id === run.id ? run : item);
    if (state.selectedImage?.key === inputKey) {
      state.activeRun = run;
      state.activeRunInputKey = inputKey;
    } else {
      state.notice = '识别已完成；期间当前图表发生变化，结果已保存在记录中。';
    }
  } catch (error) {
    if (state.selectedImage?.key === inputKey) state.analysisError = error.message || '识别请求失败。';
  } finally {
    state.analysisLoading = false;
    renderWorkspace();
    renderHistory();
  }
}

function safeApiImageUrl(raw) {
  if (!raw || typeof raw !== 'string') return null;
  try {
    const parsed = new URL(raw, window.location.origin);
    if (parsed.origin !== window.location.origin || !parsed.pathname.startsWith('/api/')) return null;
    return `${parsed.pathname}${parsed.search}`;
  } catch {
    return null;
  }
}

async function openRun(id) {
  const run = state.runs.find((item) => asText(item.id) === id);
  if (!run) return;
  state.tab = 'workspace';
  state.imageToken += 1;
  const token = state.imageToken;
  state.imageAbort?.abort();
  state.imageAbort = new AbortController();
  revokeImage(state.selectedImage);
  state.selectedSignal = null;
  state.activeRun = run;
  state.activeRunInputKey = `run:${id}`;
  state.analysisError = '';
  state.notice = '';
  const imageUrl = safeApiImageUrl(run.image_url);
  state.selectedImage = imageUrl ? { key: `run:${id}`, name: asText(run.symbol || run.id, '历史图表'), source: 'history', signal: null, loading: true, previewUrl: null, dataUrl: null } : null;
  renderAll();
  if (!imageUrl) {
    state.notice = '这条识别记录没有同源图表预览；结果仍可查看和人工复核。';
    renderNotice();
    return;
  }
  try {
    const response = await fetch(imageUrl, { signal: state.imageAbort.signal });
    const contentType = response.headers.get('content-type') || '';
    if (!response.ok || contentType.includes('application/json')) {
      let detail = `历史图表无法载入（HTTP ${response.status}）。`;
      if (contentType.includes('application/json')) {
        try { detail = asText((await response.json())?.detail, detail); } catch { /* Keep HTTP description. */ }
      }
      throw new Error(detail);
    }
    const blob = await response.blob();
    if (!blob.type.startsWith('image/')) throw new Error('历史记录资源不是图片。');
    const previewUrl = URL.createObjectURL(blob);
    await verifyImageUrl(previewUrl);
    if (token !== state.imageToken || state.selectedImage?.key !== `run:${id}`) {
      URL.revokeObjectURL(previewUrl);
      return;
    }
    state.selectedImage = { ...state.selectedImage, loading: false, previewUrl };
    renderWorkspace();
  } catch (error) {
    if (error.name === 'AbortError' || token !== state.imageToken || state.selectedImage?.key !== `run:${id}`) return;
    state.selectedImage = null;
    state.notice = `历史结果已载入，但图表预览不可用：${error.message || '读取失败。'}`;
    renderWorkspace();
  }
}

async function submitReview(runId, verdict, note) {
  if (!runId || state.reviewLoadingId) return;
  state.reviewLoadingId = runId;
  renderResult();
  try {
    const updated = await apiJson(`/runs/${encodeURIComponent(runId)}/review`, {
      method: 'POST', body: JSON.stringify({ verdict, note: note || '' }),
    });
    if (!updated || typeof updated !== 'object' || asText(updated.id) !== runId || !updated.review) {
      throw new Error('服务器未返回可确认的人工复核记录。');
    }
    state.runs = state.runs.map((run) => run.id === updated.id ? updated : run);
    if (state.activeRun?.id === updated.id) state.activeRun = updated;
    state.notice = `人工复核已记录为“${verdictLabel(verdict)}”。`;
  } catch (error) {
    state.notice = `人工复核未保存：${error.message || '请求失败。'}`;
  } finally {
    state.reviewLoadingId = null;
    renderWorkspace();
    renderHistory();
  }
}

function downloadJson(filename, data) {
  const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json;charset=utf-8' });
  const objectUrl = URL.createObjectURL(blob);
  const anchor = document.createElement('a');
  anchor.href = objectUrl;
  anchor.download = filename;
  document.body.append(anchor);
  anchor.click();
  anchor.remove();
  window.setTimeout(() => URL.revokeObjectURL(objectUrl), 1500);
}

async function exportRun(runId) {
  if (!runId || state.exportLoadingId) return;
  state.exportLoadingId = runId;
  renderHistory();
  const run = state.runs.find((item) => asText(item.id) === runId);
  try {
    const response = await fetch(`${API_BASE}/runs/${encodeURIComponent(runId)}/export`);
    const payload = await readResponse(response);
    downloadJson(`spike-vision-run-${runId}.json`, payload);
  } catch (error) {
    if (run) {
      downloadJson(`spike-vision-run-${runId}.json`, run);
      state.runsError = `服务器导出失败，已下载当前页面已返回的记录副本：${error.message || '请求失败。'}`;
    } else {
      state.runsError = error.message || '导出失败。';
    }
  } finally {
    state.exportLoadingId = null;
    renderHistory();
  }
}

async function saveConfig(event) {
  event.preventDefault();
  if (state.configLoading) return;
  showInlineError('config-error', '');
  showInlineSuccess('config-success', '');
  const model = byId('model-input').value.trim();
  if (!model) {
    showInlineError('config-error', '请填写 Gemini 模型 ID。');
    byId('model-input').focus();
    return;
  }
  const keyInput = byId('api-key-input');
  const apiKey = keyInput.value;
  const payload = { model };
  if (apiKey.trim()) payload.api_key = apiKey;
  const body = JSON.stringify(payload);
  keyInput.value = '';
  state.configLoading = true;
  state.lastConnection = null;
  renderSettings();
  try {
    const response = await fetch(`${API_BASE}/config`, { method: 'POST', headers: { 'content-type': 'application/json' }, body });
    const status = await readResponse(response);
    if (!status || typeof status !== 'object') throw new Error('设置接口未返回状态对象。');
    state.status = status;
    state.statusError = '';
    state.model = asText(status.model, model);
    setHealth(true, '本地 API 已连接');
    showInlineSuccess('config-success', '设置已发送至本机 API。密钥输入框已清空；密钥不会保存在此页面。');
  } catch (error) {
    showInlineError('config-error', error.message || '保存设置失败。');
  } finally {
    state.configLoading = false;
    renderAll();
  }
}

async function testConnection() {
  if (state.connectionTesting) return;
  state.connectionTesting = true;
  state.lastConnection = null;
  renderSettings();
  try {
    const result = await apiJson('/connection-test', { method: 'POST', body: '{}' });
    state.lastConnection = { ok: result?.ok === true, model: result?.model || state.model, message: result?.message || '' };
  } catch (error) {
    state.lastConnection = { ok: false, model: state.model, message: error.message || '连接测试失败。' };
  } finally {
    state.connectionTesting = false;
    renderSettings();
  }
}

function switchTab(tab) {
  if (!['workspace', 'history', 'settings'].includes(tab)) return;
  state.tab = tab;
  renderAll();
  byId(`tab-${tab}-button`).focus({ preventScroll: true });
}

document.addEventListener('click', (event) => {
  const tabButton = event.target.closest('[data-tab]');
  if (tabButton) {
    switchTab(tabButton.dataset.tab);
    return;
  }
  const signalButton = event.target.closest('[data-signal-id]');
  if (signalButton) {
    const item = state.signals.find((signal) => asText(signal.id) === signalButton.dataset.signalId);
    if (item) selectSignal(item);
    return;
  }
  const removeReferenceButton = event.target.closest('[data-remove-reference]');
  if (removeReferenceButton) {
    removeReference(Number(removeReferenceButton.dataset.removeReference));
    return;
  }
  const reviewButton = event.target.closest('[data-review-verdict]');
  if (reviewButton) {
    const note = byId('review-note')?.value.trim() || '';
    submitReview(reviewButton.dataset.reviewRun, reviewButton.dataset.reviewVerdict, note);
    return;
  }
  const openRunButton = event.target.closest('[data-open-run]');
  if (openRunButton) {
    openRun(openRunButton.dataset.openRun);
    return;
  }
  const exportButton = event.target.closest('[data-export-run]');
  if (exportButton) exportRun(exportButton.dataset.exportRun);
});

document.addEventListener('keydown', (event) => {
  const tabButton = event.target.closest('[data-tab]');
  if (tabButton && ['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) {
    event.preventDefault();
    const tabs = Array.from(document.querySelectorAll('[data-tab]'));
    let index = tabs.indexOf(tabButton);
    if (event.key === 'Home') index = 0;
    else if (event.key === 'End') index = tabs.length - 1;
    else index = (index + (event.key === 'ArrowRight' ? 1 : tabs.length - 1)) % tabs.length;
    switchTab(tabs[index].dataset.tab);
  }
  if (event.key === '/' && !event.ctrlKey && !event.metaKey && !event.altKey) {
    const target = event.target;
    const typing = target instanceof HTMLElement && (target.isContentEditable || ['INPUT', 'TEXTAREA', 'SELECT'].includes(target.tagName));
    if (!typing && state.tab === 'workspace') {
      event.preventDefault();
      byId('signal-search').focus();
    }
  }
});

byId('signal-search').addEventListener('input', renderSignals);
byId('refresh-signals').addEventListener('click', loadSignals);
byId('refresh-runs').addEventListener('click', loadRuns);
byId('analyze-button').addEventListener('click', analyze);
byId('clear-input').addEventListener('click', () => clearActiveImage());
byId('chart-file').addEventListener('change', (event) => {
  const file = event.target.files?.[0];
  if (file) addPrimaryImage(file);
});
byId('add-reference').addEventListener('click', () => byId('reference-file').click());
byId('reference-file').addEventListener('change', (event) => {
  if (event.target.files?.length) addReferenceFiles(event.target.files);
});
byId('criteria-input').addEventListener('input', (event) => {
  state.criteriaEdited = true;
  state.criteria = event.target.value;
  byId('criteria-count').textContent = `${event.target.value.length} 字`;
});
byId('config-form').addEventListener('submit', saveConfig);
byId('connection-test').addEventListener('click', testConnection);

const dropZone = byId('drop-zone');
const chartFileInput = byId('chart-file');
dropZone.addEventListener('click', (event) => {
  if (event.target === chartFileInput || event.target.closest('a,button,input,select,textarea')) return;
  chartFileInput.click();
});
dropZone.addEventListener('keydown', (event) => {
  if (event.target !== dropZone || !['Enter', ' '].includes(event.key)) return;
  event.preventDefault();
  chartFileInput.click();
});
chartFileInput.addEventListener('click', (event) => event.stopPropagation());
dropZone.addEventListener('dragenter', (event) => { event.preventDefault(); dropZone.classList.add('is-dragover'); });
dropZone.addEventListener('dragover', (event) => { event.preventDefault(); dropZone.classList.add('is-dragover'); });
dropZone.addEventListener('dragleave', (event) => {
  if (!dropZone.contains(event.relatedTarget)) dropZone.classList.remove('is-dragover');
});
dropZone.addEventListener('drop', (event) => {
  event.preventDefault();
  dropZone.classList.remove('is-dragover');
  const files = event.dataTransfer?.files;
  if (files?.length) addPrimaryImage(files[0]);
});

byId('criteria-count').textContent = `${byId('criteria-input').value.length} 字`;
renderAll();
Promise.allSettled([loadStatus(), loadSignals(), loadRuns()]);
