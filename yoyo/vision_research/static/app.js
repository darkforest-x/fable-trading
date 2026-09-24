// Use the unified workspace module scope when mounted; keep standalone tests pure.
const document = typeof window !== "undefined" && window.SpikeVision ? window.SpikeVision.document : globalThis.document;
import { showChart, fitChart, captureChart, destroyChart } from './chart.js';
import { exchangeConversation } from './conversation.js';
import { initReplay, setReplayActive } from './replay.js';
const API_BASE = typeof window !== 'undefined' && window.SpikeVision ? '/api/vision' : '/api';
const DEFAULT_MODEL = 'glm-5.3-flash';
const DEFAULT_CRITERIA = '只判断图表最右端当前盘口；左侧旧形态只作背景。区分仍在密集、正在启动与已经远离，只有当前启动才可判符合，不能确定则拒判。';
const MAX_IMAGE_BYTES = 5_000_000;
const MAX_TOTAL_IMAGE_BYTES = 12 * 1024 * 1024;
const MAX_REFERENCES = 49; // The review endpoint accepts 50 total images including the candidate.
const ALLOWED_TYPES = new Set(['image/png', 'image/jpeg', 'image/webp']);
const LIVE_WINDOW_OPTIONS = [6, 12, 24, 48];
const LIVE_WINDOW_STORAGE_KEY = 'spike-vision-post-signal-bars';

function loadPostSignalBars() {
  try {
    const value = Number(localStorage.getItem(LIVE_WINDOW_STORAGE_KEY));
    return LIVE_WINDOW_OPTIONS.includes(value) ? value : 12;
  } catch {
    return 12;
  }
}

const state = {
  tab: 'workspace',
  status: null,
  statusError: '',
  statusErrorIsTransport: false,
  statusPolling: false,
  apiReachable: null,
  apiTransportError: '',
  signals: [],
  signalsLoading: true,
  signalsRequestToken: 0,
  signalsPolling: false,
  signalsError: '',
  signalsWarning: '',
  automatic: null,
  automaticError: '',
  automaticStale: false,
  automaticActionError: '',
  automaticBySignal: new Map(),
  automaticLoading: false,
  automaticPolling: false,
  automaticRequestToken: 0,
  automaticSaving: false,
  automaticRefreshRequestedRunIds: new Set(),
  automaticRefreshPendingRunIds: new Set(),
  automaticRefreshScheduled: false,
  runs: [],
  runsLoading: true,
  runsError: '',
  selectedSignal: null,
  selectedImage: null,
  imageToken: 0,
  imageAbort: null,
  chartAbort: null,
  chartRefreshToken: 0,
  captureInProgress: false,
  postSignalBars: loadPostSignalBars(),
  references: [],
  referenceRevision: null,
  referencesReady: false,
  referencesDirty: false,
  referencesSaving: false,
  referencesError: '',
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
  exchanges: [],
  exchangesLoading: true,
  exchangesError: '',
  selectedExchangeId: '',
  exchangeDetail: null,
  exchangeDetailLoading: false,
  exchangeDetailError: '',
  exchangeListToken: 0,
  exchangeDetailToken: 0,
  model: DEFAULT_MODEL,
  configDraft: null,
  configDraftInitialized: false,
  criteria: DEFAULT_CRITERIA,
  criteriaEdited: false,
};

const byId = (id) => document.getElementById(id);
const healthEl = byId('api-health');
const signalsEl = byId('signal-list');
const resultEl = byId('result-content');
const historyEl = byId('history-list');
const workspaceErrorEl = byId('workspace-error');
let renderedResultMarkup = '';
let renderedExchangeDetail = null;
let renderedExchangeTitle = '';

function updateResultMarkup(markup) {
  // Keep disclosure state and unsaved review text across unrelated UI renders.
  if (markup === renderedResultMarkup) return;
  resultEl.innerHTML = markup;
  renderedResultMarkup = markup;
}

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

const FALLBACK_PROVIDER_SPECS = [
  { id: 'zhipu', name: '智谱', default_model: DEFAULT_MODEL, models: [DEFAULT_MODEL], default_region: 'default', regions: { default: '默认' }, endpoints: { default: '' } },
  {
    id: 'qwen', name: '通义千问（Qwen）', default_model: 'qwen3-vl-plus',
    models: ['qwen3-vl-plus', 'qwen-vl-max'], default_region: 'beijing',
    regions: { beijing: '北京', singapore: '新加坡' },
    endpoints: {
      beijing: 'https://dashscope.aliyuncs.com/compatible-mode/v1',
      singapore: 'https://dashscope-intl.aliyuncs.com/compatible-mode/v1',
    }, key_help_url: 'https://help.aliyun.com/en/model-studio/get-api-key',
  },
];
const HISTORICAL_PROVIDER_NAMES = { gemini: 'Gemini', zhipu: '智谱', qwen: '通义千问（Qwen）' };

function providerSpecs(status = state.status) {
  const specs = Array.isArray(status?.providers) ? status.providers.filter((item) => item && item.id) : [];
  return specs.length ? specs : FALLBACK_PROVIDER_SPECS;
}

function providerSpec(providerId, status = state.status) {
  return providerSpecs(status).find((item) => item.id === providerId)
    || FALLBACK_PROVIDER_SPECS.find((item) => item.id === providerId)
    || { id: providerId || 'zhipu', name: providerId || '模型服务', default_model: DEFAULT_MODEL, models: [DEFAULT_MODEL], default_region: 'default', regions: {}, endpoints: {} };
}

function configFromStatus(status = state.status) {
  const provider = asText(status?.provider, 'zhipu');
  const spec = providerSpec(provider, status);
  return {
    provider,
    region: asText(status?.region, asText(spec.default_region, 'default')),
    model: asText(status?.model, asText(spec.default_model, DEFAULT_MODEL)),
  };
}

function configMatchesStatus(draft, status = state.status) {
  if (!draft || !status) return false;
  const saved = configFromStatus(status);
  return draft.provider === saved.provider && draft.region === saved.region && draft.model === saved.model;
}

function providerDisplayName(providerId, providerName = '', status = state.status) {
  if (providerName) return providerName;
  const id = asText(providerId, 'zhipu');
  const advertised = providerSpecs(status).find((item) => item.id === id);
  if (advertised?.name) return advertised.name;
  const fallback = FALLBACK_PROVIDER_SPECS.find((item) => item.id === id);
  return asText(fallback?.name, HISTORICAL_PROVIDER_NAMES[id] || id);
}

function providerRegions(spec) {
  const regions = spec?.regions;
  if (Array.isArray(regions)) {
    return regions.map((item) => typeof item === 'string' ? [item, item] : [asText(item?.id), asText(item?.name, asText(item?.id))])
      .filter(([id]) => id);
  }
  if (regions && typeof regions === 'object') return Object.entries(regions).map(([id, name]) => [id, asText(name, id)]);
  const fallbackRegion = asText(spec?.default_region, 'default');
  return [[fallbackRegion, fallbackRegion]];
}

function providerModels(spec) {
  const models = Array.isArray(spec?.models) ? spec.models.map((item) => typeof item === 'string' ? item : asText(item?.id)).filter(Boolean) : [];
  const defaultModel = asText(spec?.default_model, DEFAULT_MODEL);
  return models.includes(defaultModel) ? models : [defaultModel, ...models];
}

function providerEndpoint(spec, region) {
  const endpoints = spec?.endpoints;
  if (endpoints && typeof endpoints === 'object') return asText(endpoints[region]);
  return '';
}

function safeHelpUrl(value) {
  try {
    const url = new URL(String(value || ''));
    return url.protocol === 'https:' ? url.href : '';
  } catch {
    return '';
  }
}

function ensureConfigDraft(status = state.status) {
  if (!state.configDraftInitialized && status) {
    state.configDraft = configFromStatus(status);
    state.configDraftInitialized = true;
  }
  return state.configDraft || configFromStatus(status);
}

function providerNameForRecord(record) {
  // Records created before provider metadata was added are known Zhipu history.
  return providerDisplayName(record?.provider, record?.provider_name, state.status);
}

function syncProviderDisclosure() {
  const savedName = providerDisplayName(state.status?.provider, state.status?.provider_name);
  const disclosure = byId('active-provider-disclosure');
  if (disclosure) disclosure.textContent = `识别时发送当前图表、参考图与规则至已保存的 ${savedName} 服务`;
  const referencesDisclosure = byId('references-provider-disclosure');
  if (referencesDisclosure) referencesDisclosure.textContent = `已保存的 ${savedName} 服务`;
  if (document?.documentElement?.dataset) document.documentElement.dataset.activeProviderName = savedName;
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
  let bodyText;
  try {
    bodyText = await response.text();
  } catch (error) {
    if (error?.name === 'AbortError') throw error;
    if (isFetchTransportFailure(error)) throw localApiTransportError();
    throw error;
  }
  let payload = bodyText;
  if (contentType.includes('application/json')) {
    try { payload = bodyText ? JSON.parse(bodyText) : null; } catch { payload = null; }
  }
  if (!response.ok) {
    const detail = payload && typeof payload === 'object' ? payload.detail || payload.message : payload;
    const error = new Error(asText(detail, `请求失败（HTTP ${response.status}）`));
    error.apiExchangeId = typeof payload?.api_exchange_id === 'string' ? payload.api_exchange_id : '';
    throw error;
  }
  return payload;
}

function isFetchTransportFailure(error) {
  if (error?.name === 'NetworkError') return true;
  return error instanceof TypeError && /fetch|network|connection|load failed/i.test(error.message || '');
}

function localApiTransportError() {
  const unavailable = new Error('本机 API 暂不可达，正在恢复连接。');
  unavailable.name = 'LocalApiTransportError';
  unavailable.code = 'LOCAL_API_UNREACHABLE';
  markApiUnreachable(unavailable.message);
  return unavailable;
}

async function apiJson(path, options = {}) {
  const headers = new Headers(options.headers || {});
  if (options.body !== undefined && !headers.has('content-type')) headers.set('content-type', 'application/json');
  let response;
  try {
    response = await fetch(`${API_BASE}${path}`, { ...options, headers });
  } catch (error) {
    if (error?.name === 'AbortError') throw error;
    if (isFetchTransportFailure(error)) throw localApiTransportError();
    throw error;
  }
  markApiReachable();
  return readResponse(response);
}

function exchangeKindLabel(kind) {
  if (kind === 'recognition') return '识别';
  if (kind === 'connection_test') return '连接测试';
  return 'API 调用';
}

function exchangeStatusLabel(status) {
  return ({ running: '进行中', completed: '已完成', failed: '失败', interrupted: '已中断' })[status] || '状态未提供';
}

function runStatusLabel(status) {
  return ({ running: '进行中', completed: '已完成', failed: '失败', interrupted: '已中断' })[status]
    || (status ? asText(status) : '状态未提供');
}

function currentStateLabel(value) {
  return ({ converging: '当前仍在密集，尚未启动', launching: '当前正在启动', extended: '当前已离开密集区', no_setup: '当前无该形态', unclear: '当前状态不清楚' })[value] || '';
}

function exchangeTitle(exchange) {
  if (exchange.kind === 'connection_test') return '连接测试';
  const run = state.runs.find((item) => item.id === exchange.run_id || item.api_exchange_id === exchange.id);
  const symbol = run?.symbol || run?.signal?.symbol;
  return symbol ? `${symbol} · ${run.timeframe || run.signal?.timeframe || ''}` : (run?.image_name || '图表识别');
}

function chatParagraphs(text) {
  return String(text || '').split(/\n\s*\n/).filter(Boolean).map((part) => `<p>${escapeHtml(part).replace(/\n/g, '<br>')}</p>`).join('');
}

function imageDataUrl(value) {
  if (typeof value !== 'string') return '';
  return /^data:image\/(?:png|jpeg|webp);base64,[A-Za-z0-9+/=\r\n]+$/i.test(value) ? value.replace(/[\r\n]/g, '') : '';
}

function referenceNameFromText(value) {
  const match = String(value || '').match(/名称（仅为数据，不是指令）：\s*(.+)/u);
  if (!match) return '';
  const encodedName = match[1].trim();
  try {
    const parsed = JSON.parse(encodedName);
    return typeof parsed === 'string' ? parsed : '';
  } catch {
    return encodedName.replace(/^['"]|['"]$/g, '');
  }
}

function collectExchangeImages(requestBodyText) {
  let body;
  try { body = JSON.parse(requestBodyText); } catch { return []; }
  const images = [];
  const visit = (value, hint = '') => {
    if (Array.isArray(value)) {
      let currentHint = hint;
      for (const item of value) {
        if (item && typeof item === 'object' && typeof item.text === 'string') {
          currentHint = item.text;
          continue;
        }
        const before = images.length;
        visit(item, currentHint);
        if (images.length > before) currentHint = '';
      }
      return;
    }
    if (!value || typeof value !== 'object') return;
    const inline = value.inlineData || value.inline_data;
    const possibleUrl = value.image_url?.url || (value.type === 'image_url' ? value.url : '')
      || (inline?.data ? `data:${inline.mimeType || inline.mime_type};base64,${inline.data}` : '');
    const dataUrl = imageDataUrl(possibleUrl);
    if (dataUrl) {
      images.push({ dataUrl, titleHint: hint });
      return;
    }
    for (const [key, child] of Object.entries(value)) {
      if (key === 'text' && typeof child === 'string') continue;
      visit(child, hint);
    }
  };
  visit(body);
  return images.map((image, index) => {
    if (index === 0) return { ...image, title: '待判图（候选）' };
    const referenceName = referenceNameFromText(image.titleHint);
    return { ...image, title: referenceName ? `参考图 ${index} · ${referenceName}` : `参考图 ${index}` };
  });
}

function requestBodyPreview(bodyText) {
  const rawText = typeof bodyText === 'string' ? bodyText : '';
  let preview = rawText;
  let foldedImages = false;
  try { preview = JSON.stringify(JSON.parse(rawText), null, 2); } catch { /* Preserve the stored body when it is not valid JSON. */ }
  preview = preview.replace(/data:image\/(?:png|jpeg|webp);base64,[A-Za-z0-9+/=\r\n]+/gi, () => {
    foldedImages = true;
    return '[图片 Base64 已折叠；完整内容请下载原始记录]';
  });
  return {
    text: preview,
    label: foldedImages
      ? '请求输入预览（已格式化；图片 Base64 已折叠）'
      : '请求输入预览（已格式化）',
  };
}

function formatDate(value) {
  if (!value) return '时间未提供';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return asText(value, '时间未提供');
  return new Intl.DateTimeFormat('zh-CN', { dateStyle: 'medium', timeStyle: 'short' }).format(date);
}

function formatMarketTime(value, withSeconds = false) {
  const stamp = Number(value);
  if (!Number.isFinite(stamp) || stamp <= 0) return '时间未提供';
  return new Intl.DateTimeFormat('zh-CN', {
    timeZone: 'Asia/Shanghai', month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit', ...(withSeconds ? { second: '2-digit' } : {}), hour12: false,
  }).format(new Date(stamp));
}

function formatMarketClock(value, withSeconds = false) {
  const stamp = Number(value);
  if (!Number.isFinite(stamp) || stamp <= 0) return '时间未提供';
  return new Intl.DateTimeFormat('zh-CN', {
    timeZone: 'Asia/Shanghai', hour: '2-digit', minute: '2-digit',
    ...(withSeconds ? { second: '2-digit' } : {}), hour12: false,
  }).format(new Date(stamp));
}

function liveSnapshotInfo(image, now = Date.now()) {
  const provenance = image?.chartData?.provenance;
  if (!provenance || provenance.time_boundary !== 'live_observation') return null;
  const observed = Number(provenance.observed_at_ms);
  const expires = Number(provenance.recognition_expires_ms);
  const signalClose = Number(provenance.signal_bar_close_ms ?? image?.signal?.bar_close_ms);
  const duration = Number(image?.signal?.timeframe_min) * 60_000;
  const rows = image?.chartData?.candles || [];
  const postBarsSeen = rows.filter((row) => Number(row.t) >= signalClose).length;
  const fresh = Number.isFinite(observed) && now >= observed && now - observed <= 90_000;
  const expired = Number.isFinite(expires) && now >= expires;
  const stale = provenance.source_stale === true || image?.liveRefreshError === true;
  const usable = provenance.recognition_eligible === true && !stale && fresh && !expired
    && /^[a-f0-9]{32}$/i.test(String(image?.chartData?.snapshot_id || ''))
    && /^[a-f0-9]{64}$/i.test(String(image?.chartData?.chart_sha256 || ''))
    && Number(provenance.post_signal_bars) === state.postSignalBars
    && !image?.frozen && !image?.error;
  return {
    provenance, observed, expires, signalClose, duration, postBarsSeen,
    windowBars: Number(provenance.post_signal_bars) || state.postSignalBars,
    fresh, expired, stale, usable,
  };
}

function sideLabel(side) {
  return ({ long: '做多方向', short: '做空方向', unknown: '方向未确定' })[side] || (side ? asText(side) : '方向未返回');
}

function verdictLabel(verdict) {
  return ({ match: '符合', no_match: '不符合', uncertain: '不确定', accepted: '接受', rejected: '拒绝' })[verdict] || '未返回判断';
}

function automaticStatusLabel(status) {
  return ({ queued: '排队中', running: '识别中', completed: '已完成', failed: '失败', skipped: '已跳过', interrupted: '已中断' })[status]
    || asText(status, '状态未提供');
}

function automaticVerdictClass(verdict) {
  return ({ match: 'is-match', no_match: 'is-no-match', uncertain: 'is-uncertain' })[verdict] || '';
}

function isAutomaticTerminal(status) {
  return ['completed', 'failed', 'skipped', 'interrupted'].includes(status);
}

function sourceLabel(source) {
  const labels = {
    historical_replay: '历史回放', historical_replay_comparison: '回放图文输入对照',
    local: '本地数据', spike: 'SPIKE', spike_automatic: 'SPIKE 自动识别', spike_capture: 'SPIKE 图表快照', cache: '本机缓存', database: '本地数据库',
    local_config: '本机已保存 · 重启保留', environment: '后端环境变量', env: '后端环境变量', session: '本机进程会话',
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

function markApiUnreachable(message) {
  const changed = state.apiReachable !== false;
  state.apiReachable = false;
  state.apiTransportError = message;
  if (typeof state.automatic?.enabled === 'boolean') state.automaticStale = true;
  setHealth(false, '本地 API 未连接 · 正在恢复连接');
  if (changed) {
    renderAutomaticControls();
    if (state.tab === 'settings') renderSettings();
  }
}

function markApiReachable() {
  const changed = state.apiReachable !== true;
  state.apiReachable = true;
  state.apiTransportError = '';
  if (state.statusErrorIsTransport) {
    state.statusError = '';
    state.statusErrorIsTransport = false;
  }
  setHealth(true, '本地服务已连接');
  if (changed && state.tab === 'settings') renderSettings();
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

function automaticJobTime(job) {
  let label = '';
  let value = null;
  if (Number(job.observed_at_ms) > 0) {
    label = '判断于';
    value = Number(job.observed_at_ms);
  } else if (job.status === 'queued') {
    label = '加入于';
    value = job.created_at;
  } else if (job.status === 'running' && job.started_at) {
    label = '开始于';
    value = job.started_at;
  }
  if (!value) return '';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '';
  const formatted = new Intl.DateTimeFormat('zh-CN', {
    month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false,
  }).format(date);
  return `${label} ${formatted}`;
}

function automaticJobSummary(job) {
  const error = asText(job.error, '');
  const summary = asText(job.decision?.summary, '');
  if (error) return error;
  if (summary) return summary;
  return ({
    queued: '已进入后端自动识别队列。',
    running: '正在按自动识别规则判断当前盘口。',
    failed: '自动识别失败，未返回错误详情。',
    skipped: '本次自动识别已跳过，未返回原因。',
    interrupted: '自动识别已中断，未返回原因。',
    completed: '没有可展示的识别摘要。',
  })[job.status] || '没有可展示的识别摘要。';
}

function rememberAutomaticJob(job) {
  if (!job || typeof job !== 'object') return;
  const signalId = asText(job.signal_id, '');
  if (!signalId) return;
  const previous = state.automaticBySignal.get(signalId);
  let next = { ...previous, ...job };
  if (isAutomaticTerminal(previous?.status) && !isAutomaticTerminal(job.status)) next = { ...job, ...previous };
  state.automaticBySignal.set(signalId, next);
  if (isAutomaticTerminal(next.status) && next.run_id) requestAutomaticRecordRefresh(next.run_id);
}

function automaticJobForRun(runId) {
  const id = asText(runId, '');
  if (!id) return null;
  for (const job of state.automaticBySignal.values()) {
    if (asText(job.run_id, '') === id) return job;
  }
  return null;
}

function runFailureReason(run) {
  const automaticError = automaticJobForRun(run?.id)?.error;
  return asText(run?.error, '') || asText(run?.error_details?.message, '')
    || asText(run?.error_details?.detail, '') || asText(automaticError, '');
}

function automaticReviewMarkup(signalId) {
  const job = state.automaticBySignal.get(signalId);
  if (!job) return '';
  const verdict = asText(job.decision?.verdict, '');
  const verdictClass = automaticVerdictClass(verdict);
  const statusClass = job.status === 'completed' && verdictClass ? verdictClass
    : ['queued', 'running', 'failed', 'skipped', 'interrupted'].includes(job.status) ? `is-${job.status}` : '';
  const statusText = job.status === 'completed' && verdict ? verdictLabel(verdict) : automaticStatusLabel(job.status);
  const runId = asText(job.run_id, '');
  const jobTime = automaticJobTime(job);
  const action = ['completed', 'failed'].includes(job.status) && runId
    ? `<button type="button" class="automatic-open-run" data-open-run="${escapeHtml(runId)}">查看结果</button>` : '';
  const footer = jobTime || action ? `<div class="automatic-review-footer">${jobTime ? `<time>${escapeHtml(jobTime)}</time>` : ''}${action}</div>` : '';
  return `<div class="automatic-review" aria-label="自动识别结果">
    <div class="automatic-review-heading"><span class="automatic-status ${statusClass}"${job.status === 'completed' && verdict ? ' title="自动识别已完成"' : ''}>${escapeHtml(statusText)}</span></div>
    <p class="automatic-review-summary">${escapeHtml(automaticJobSummary(job))}</p>
    ${footer}
  </div>`;
}

function renderAutomaticControls() {
  const automatic = state.automatic;
  const toggle = byId('automatic-toggle');
  const hasKnownState = typeof automatic?.enabled === 'boolean';
  const stale = state.automaticStale || state.apiReachable === false;
  const pending = state.automaticLoading || state.automaticSaving || !hasKnownState;
  const error = state.automaticActionError || state.automaticError || asText(automatic?.error, '')
    || (stale ? '自动识别状态显示为上次读取结果；连接恢复后会同步。' : '');
  toggle.disabled = pending || stale;
  toggle.classList.toggle('is-enabled', !stale && automatic?.enabled === true);
  toggle.classList.toggle('is-disabled', !stale && automatic?.enabled === false);
  toggle.setAttribute('aria-pressed', stale ? 'mixed' : String(automatic?.enabled === true));
  toggle.setAttribute('aria-label', stale ? '自动识别状态未同步，暂时无法切换'
    : automatic?.enabled === true ? '暂停自动识别' : '开启自动识别');
  byId('automatic-toggle-state').textContent = state.automaticSaving ? '保存中…'
    : stale && hasKnownState ? `状态未同步 · 上次${automatic.enabled ? '已开启' : '已暂停'}`
      : automatic?.enabled === true ? '已开启'
        : automatic?.enabled === false ? '已暂停'
          : state.automaticLoading ? '读取中…' : '不可用';
  const hasPolicy = Number.isFinite(Number(automatic?.post_signal_bars))
    && Number.isFinite(Number(automatic?.poll_interval_seconds));
  byId('automatic-policy').textContent = hasPolicy
    ? `信号后 ${Number(automatic.post_signal_bars)} 根 · 每 ${Number(automatic.poll_interval_seconds)} 秒扫描`
    : '';
  setVisible(byId('automatic-policy'), hasPolicy);
  for (const name of ['queued', 'running', 'completed', 'failed', 'skipped', 'interrupted']) {
    const count = automatic?.counts?.[name];
    byId(`automatic-count-${name}`).textContent = count !== null && count !== undefined && Number.isFinite(Number(count)) ? String(Number(count)) : '—';
  }
  showInlineError('automatic-error', error);
}

function renderSignals() {
  renderSignalSource();
  renderAutomaticControls();
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
    const time = item.signal_at ? new Intl.DateTimeFormat('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false }).format(new Date(item.signal_at)) : '时间未提供';
    return `<article class="signal-item ${selected ? 'is-selected' : ''}">
      <button class="signal-select" type="button" data-signal-id="${escapeHtml(id)}" aria-pressed="${selected}" aria-label="载入 ${escapeHtml(symbol)} ${escapeHtml(timeframe)} ${escapeHtml(side)} 候选" title="${escapeHtml(symbol)} · ${escapeHtml(timeframe)}">
        <span class="signal-topline"><strong class="signal-name">${escapeHtml(symbol.replace(/-SWAP$/, ''))}</strong><span class="signal-timeframe">${escapeHtml(timeframe)}</span></span>
        <span class="signal-meta"><span class="signal-side ${sideClass}">${escapeHtml(side.replace('方向', ''))}</span><time>${escapeHtml(time)}</time></span>
      </button>
      ${automaticReviewMarkup(id)}
    </article>`;
  }).join('');
}

async function loadAutomatic({ quiet = false } = {}) {
  if (quiet && state.automaticPolling) return;
  const token = ++state.automaticRequestToken;
  state.automaticPolling = true;
  if (!quiet) {
    state.automaticLoading = true;
    renderAutomaticControls();
  }
  try {
    const payload = await apiJson('/automatic', { cache: 'no-store' });
    if (token !== state.automaticRequestToken) return;
    if (!payload || typeof payload !== 'object' || typeof payload.enabled !== 'boolean') {
      throw new Error('自动识别状态格式无效。');
    }
    const items = Array.isArray(payload.items) ? payload.items.filter((item) => item && typeof item === 'object') : [];
    state.automatic = {
      ...payload,
      counts: payload.counts && typeof payload.counts === 'object' ? payload.counts : {},
      items,
      error: asText(payload.error, ''),
    };
    state.automaticStale = false;
    state.automaticError = '';
    state.automaticActionError = '';
    for (const job of items) rememberAutomaticJob(job);
  } catch (error) {
    if (token !== state.automaticRequestToken) return;
    state.automaticStale = true;
    state.automaticError = error.message || '自动识别状态读取失败。';
  } finally {
    if (token === state.automaticRequestToken) {
      state.automaticPolling = false;
      state.automaticLoading = false;
      renderSignals();
    }
  }
}

async function toggleAutomaticRecognition() {
  if (typeof state.automatic?.enabled !== 'boolean' || state.automaticSaving) return;
  state.automaticSaving = true;
  state.automaticActionError = '';
  renderAutomaticControls();
  try {
    await apiJson('/automatic', {
      method: 'POST',
      body: JSON.stringify({ enabled: !state.automatic.enabled }),
    });
    await loadAutomatic();
  } catch (error) {
    state.automaticActionError = error.message || '自动识别设置保存失败。';
  } finally {
    state.automaticSaving = false;
    renderSignals();
  }
}

function renderLiveControls(image) {
  const liveControls = byId('live-chart-controls');
  const liveStatus = byId('live-chart-status');
  const isSignal = image?.source === 'signal' && image.signal;
  setVisible(liveControls, Boolean(isSignal));
  if (!isSignal) {
    setVisible(liveStatus, false);
    return;
  }

  const windowSelect = byId('recognition-window');
  windowSelect.value = String(state.postSignalBars);
  windowSelect.disabled = Boolean(state.analysisLoading || state.captureInProgress || image.loading || image.frozen);
  const refreshButton = byId('refresh-live-chart');
  refreshButton.disabled = Boolean(state.analysisLoading || state.captureInProgress || image.loading || image.chartRefreshing || image.frozen);
  const refreshLabel = image.chartRefreshing ? '刷新中…' : '刷新';
  const refreshLabelElement = refreshButton.querySelector('.button-label');
  const refreshTextNode = Array.from(refreshButton.childNodes).find((node) => node.nodeType === Node.TEXT_NODE && node.textContent.trim());
  if (refreshLabelElement) refreshLabelElement.textContent = refreshLabel;
  else if (refreshTextNode) refreshTextNode.textContent = refreshLabel;
  else if (refreshButton.querySelector('.ui-icon')) refreshButton.append(document.createTextNode(refreshLabel));
  else refreshButton.textContent = refreshLabel;

  const info = liveSnapshotInfo(image);
  let message;
  let className = 'is-waiting';
  if (image.loading && !image.chartData) {
    message = '正在读取实时行情…';
  } else if (image.frozen) {
    message = `本次识别快照 · ${formatMarketClock(info?.observed, true)} · 返回实时图表后继续更新。`;
  } else if (image.chartRefreshing) {
    message = info?.usable ? `正在更新 · 当前快照 ${formatMarketClock(info.observed, true)} · 末根${info.provenance.last_bar_closed ? '已收盘' : '未收盘'}` : '正在读取新行情…';
  } else if (image.liveRefreshMessage) {
    message = `${image.liveRefreshMessage} 当前显示上次快照，不可识别。`;
    className = 'is-stale';
  } else if (!info) {
    message = image.error ? `实时图表不可用：${image.error}` : '等待实时行情快照。';
    className = image.error ? 'is-stale' : 'is-waiting';
  } else {
    const updated = formatMarketClock(info.provenance.observed_at_ms, true);
    if (info.expired) {
      message = `识别窗口已于 ${formatMarketTime(info.expires)} 结束；可扩大范围后刷新。`;
      className = 'is-stale';
    } else if (info.stale) {
      message = `行情滞后 · 显示上次快照（${updated}），已禁用识别。`;
      className = 'is-stale';
    } else if (!info.fresh) {
      message = `快照超过 90 秒未更新（${updated}），已禁用识别。`;
      className = 'is-stale';
    } else if (info.usable) {
      message = `每 10 秒更新 · 更新于 ${updated} · 末根 K 线${info.provenance.last_bar_closed === false ? '未收盘' : '已收盘'}`;
      className = 'is-ready';
    } else {
      message = `当前快照不可识别 · 更新于 ${updated}`;
      className = 'is-waiting';
    }
  }
  liveStatus.className = `live-chart-status ${className}`;
  liveStatus.textContent = message;
  setVisible(liveStatus, true);
}

function renderImage() {
  const image = state.selectedImage;
  const liveChart = Boolean(image?.chartData && !image.loading && !image.error && !image.frozen);
  setVisible(byId('empty-stage'), !image);
  setVisible(byId('image-loading'), Boolean(image?.loading));
  setVisible(byId('image-error'), Boolean(image?.error));
  byId('image-error').textContent = image?.error || '';
  setVisible(byId('tradingview-chart'), liveChart);
  setVisible(byId('chart-legend'), liveChart);
  setVisible(byId('fit-chart'), liveChart);
  setVisible(byId('resume-chart'), Boolean(image?.chartData && image.frozen));
  byId('resume-chart').disabled = state.analysisLoading;
  byId('resume-chart').textContent = '返回实时图表并刷新';
  setVisible(byId('image-display'), Boolean(image?.previewUrl && !image.loading && !image.error && !liveChart));
  byId('clear-input').disabled = !image;
  byId('image-heading').textContent = image?.signal ? `${image.signal.symbol.replace(/-SWAP$/, '')} · ${image.signal.timeframe}` : image?.name || '图表工作台';
  const chip = byId('input-source-chip');
  chip.textContent = image?.source === 'signal' ? 'SPIKE' : image?.source === 'history' ? '识别快照' : '上传图片';
  setVisible(chip, Boolean(image));
  const summary = byId('selection-summary');
  const liveInfo = liveSnapshotInfo(image);
  summary.textContent = image?.signal
    ? liveInfo
      ? `信号 ${formatMarketTime(liveInfo.signalClose)} · 后续 ${liveInfo.postBarsSeen}/${liveInfo.windowBars} 根 · 可识别至 ${formatMarketTime(liveInfo.expires)}`
      : `信号 ${formatDate(image.signal.signal_at)} · 正在加载行情`
    : '';
  setVisible(summary, Boolean(image?.signal));
  const preview = byId('chart-preview');
  if (image?.previewUrl && preview.getAttribute('src') !== image.previewUrl) preview.src = image.previewUrl;
  if (!image) preview.removeAttribute('src');
  preview.alt = image?.name ? `图表：${image.name}` : '当前图表';
  byId('chart-context').textContent = liveChart ? '拖动平移 · 滚轮缩放 · UTC+8 · 实时观察，含可能未收盘 K 线' : image?.frozen ? '本次发送的冻结图表快照' : image?.source === 'history' ? '当次发送的原始图片' : '支持拖放图片';
  renderLiveControls(image);
  if (liveChart) showChart(image.chartData, image.key);
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
  const canShow = Boolean(image && box && state.activeRunInputKey === image.key && image.previewUrl && !image.loading && !image.error && (!image.chartData || image.frozen));
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
  list.innerHTML = state.references.length ? state.references.map((reference, index) => `<div class="reference-thumb">
    <button class="reference-preview" type="button" data-preview-reference="${index}" aria-label="查看大图 ${escapeHtml(reference.name)}"><img src="${escapeHtml(reference.previewUrl)}" alt="参考图 ${index + 1}：${escapeHtml(reference.name)}" /></button>
    <span class="reference-label" title="${escapeHtml(reference.name)}">${escapeHtml(reference.name)}</span>
    <button class="reference-remove" type="button" data-remove-reference="${index}" aria-label="移除参考图 ${escapeHtml(reference.name)}" ${state.referencesSaving ? 'disabled' : ''}>移除</button>
  </div>`).join('') : '<span class="reference-empty">添加形态参考图，作为所有识别共用的对照。</span>';
  byId('reference-count').textContent = state.referencesReady ? String(state.references.length) : '—';
  byId('references-total').textContent = `${state.references.length} 张`;
  byId('reload-references').disabled = state.referencesSaving;
  byId('add-reference').disabled = !state.referencesReady || state.referencesSaving || state.references.length >= MAX_REFERENCES;
  byId('save-references').disabled = !state.referencesDirty || state.referencesSaving || !state.referencesReady;
  byId('save-references').textContent = state.referencesSaving ? '正在保存…' : '保存全局参考图';
  byId('references-status').textContent = !state.referencesReady ? '尚未读取全局参考图' : state.referencesSaving ? '正在保存到本机…' : state.referencesDirty ? '有未保存的更改 · 保存后应用于后续识别' : `已保存 · ${state.references.length} 张 · 应用于后续识别`;
  showInlineError('references-error', state.referencesError);
}

async function loadReferences() {
  try {
    const data = await apiJson('/references');
    state.references = data.items.map(item => ({ ...item, previewUrl: safeApiImageUrl(item.image_url) }));
    state.referenceRevision = data.revision;
    state.referencesReady = true;
    state.referencesDirty = false;
    state.referencesError = '';
  } catch (error) {
    state.referencesError = error.message || '全局参考图读取失败。';
    state.referencesReady = false;
  }
  renderReferences();
  renderResult();
}

async function saveReferences() {
  if (state.referencesSaving || !state.referencesReady) return;
  state.referencesSaving = true;
  state.referencesError = '';
  renderReferences();
  renderResult();
  try {
    const references = await Promise.all(state.references.map(async item => {
      if (item.dataUrl) return { name: item.name, data_url: item.dataUrl };
      const response = await fetch(safeApiImageUrl(item.image_url));
      if (!response.ok) throw new Error('已保存的参考图读取失败，请重新载入。');
      return { name: item.name, data_url: await readFileDataUrl(await response.blob(), 'image/png') };
    }));
    const data = await apiJson('/references', { method: 'PUT', body: JSON.stringify({ references, expected_revision: state.referenceRevision }) });
    state.references = data.items.map(item => ({ ...item, previewUrl: safeApiImageUrl(item.image_url) }));
    state.referenceRevision = data.revision;
    state.referencesDirty = false;
  } catch (error) {
    state.referencesError = error.message || '参考图未保存。';
  } finally {
    state.referencesSaving = false;
    renderReferences();
    renderResult();
  }
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

function apiExchangeActionMarkup(exchangeId) {
  const id = asText(exchangeId, '');
  if (id) {
    return `<button class="button button-secondary" type="button" data-open-exchange="${escapeHtml(id)}">查看模型对话</button>`;
  }
  return '<span class="exchange-unavailable">本条记录没有关联 API 原始记录；未保存的旧请求无法补取当时输入输出。</span>';
}

function renderResult() {
  renderRunStatus();
  const image = state.selectedImage;
  const liveInfo = image?.source === 'signal' ? liveSnapshotInfo(image) : null;
  const sourceReady = image?.source === 'signal' ? Boolean(liveInfo?.usable)
    : image?.source === 'upload' && Boolean(image.dataUrl);
  const canAnalyze = Boolean(image && !image.loading && !image.error && sourceReady
    && !state.captureInProgress
    && !state.analysisLoading && state.referencesReady && !state.referencesDirty && !state.referencesSaving);
  byId('analyze-button').disabled = !canAnalyze;
  const analyzeLabel = state.analysisLoading ? '正在识别…'
    : state.referencesDirty ? '请先保存参考图'
      : image?.frozen && image.source === 'signal' ? '返回实时图表后再次识别'
        : image?.chartRefreshing && !liveInfo?.usable ? '正在更新行情…'
          : image?.source === 'signal' && liveInfo?.expired ? '识别窗口已结束，可扩大范围'
            : image?.source === 'signal' && liveInfo?.stale ? '行情滞后，暂不可识别'
              : image?.source === 'signal' && !liveInfo?.usable ? '等待可识别的实时快照'
                : '识别当前盘口';
  byId('analyze-button').querySelector('.button-label').textContent = analyzeLabel;
  setVisible(byId('analyze-button').querySelector('.button-spinner'), state.analysisLoading);
  showInlineError('analyze-error', state.analysisError);

  if (!state.activeRun) {
    updateResultMarkup(`<div class="result-empty">
      <h3>${state.analysisLoading ? '正在等待模型响应…' : '等待识别'}</h3>
      <p>${state.analysisLoading ? '可能需要数分钟，请勿重复提交。结果对应本次发送时的图表快照。' : state.selectedImage ? '只判断最右端当前形态，左侧历史仅作背景。' : '选择图表，检查最右端当前形态。'}</p></div>`);
    return;
  }
  const run = state.activeRun;
  const decision = run.decision || {};
  const failureReason = runFailureReason(run);
  const verdict = decision.verdict;
  const verdictClass = verdict === 'no_match' ? 'no-match' : verdict === 'uncertain' ? 'uncertain' : '';
  const completed = run.status === 'completed';
  const incomplete = run.status && run.status !== 'completed';
  const currentScope = run.analysis_scope === 'current_right_edge';
  const headingLabel = incomplete ? '识别未完成' : `${currentScope ? '' : '旧版整图判断 · '}${verdictLabel(verdict)}`;
  const stateLabel = currentStateLabel(decision.current_state);
  const sideText = stateLabel
    ? `${stateLabel}${['long', 'short'].includes(decision.side) ? ` · ${sideLabel(decision.side)}` : ''}`
    : sideLabel(decision.side);
  const evidence = safeList(decision.evidence);
  const risks = safeList(decision.risks);
  const human = run.review;
  const evidenceHtml = evidence.length ? `<ul class="result-list">${evidence.map((item) => `<li>${escapeHtml(item)}</li>`).join('')}</ul>` : '<span class="list-blank">模型未返回可观察证据。</span>';
  const risksHtml = risks.length ? `<ul class="result-list">${risks.map((item) => `<li>${escapeHtml(item)}</li>`).join('')}</ul>` : '<span class="list-blank">模型未列出风险说明。</span>';
  const model = asText(run.model, state.status?.model || state.model);
  const providerName = providerNameForRecord(run);
  const created = formatDate(run.created_at);
  const latency = run.latency_ms != null && Number.isFinite(Number(run.latency_ms)) ? `${Math.round(Number(run.latency_ms))} ms` : '';
  const usage = run.usage && typeof run.usage === 'object' ? Object.entries(run.usage).map(([key, value]) => `${key}: ${asText(value)}`).join(' · ') : '';
  const referenceNames = Array.isArray(run.references) ? run.references.map((reference) => asText(reference?.name, '参考图')).filter(Boolean) : [];
  const criteria = asText(run.criteria, '记录中没有保存形态规则。');
  const requestSource = sourceLabel(run.source || (run.symbol ? 'spike' : 'upload'));
  const inputMode = { vision: '纯图像', text: '纯数值（未发送图片）', hybrid: '图像 + 数值' }[run.input_mode];
  const requestContext = `<details class="request-context"><summary>识别详情</summary><dl>
    <dt>输入来源</dt><dd>${escapeHtml(requestSource)} · ${escapeHtml(inputMode || asText(run.image_name, state.selectedImage?.name || '图片名称未返回'))}</dd>
    <dt>参考图</dt><dd>${referenceNames.length ? escapeHtml(referenceNames.join('、')) : '未使用参考图'}</dd>
    <dt>检测范围</dt><dd>${currentScope ? '图中最右端当前盘口；旧形态不代表当前符合' : '旧版整图判断，不代表当前盘口有效'}</dd>
    <dt>服务商</dt><dd>${escapeHtml(providerName)}</dd>
    <dt>本次规则</dt><dd>${escapeHtml(criteria)}</dd>
    <dt>模型</dt><dd>${escapeHtml(model)}</dd>
    <dt>时间</dt><dd>${escapeHtml(created)}</dd>
    ${run.provenance?.time_boundary === 'live_observation' ? `<dt>行情快照</dt><dd>信号 ${escapeHtml(formatMarketTime(run.provenance.signal_bar_close_ms))} · 观察 ${escapeHtml(formatMarketTime(run.provenance.observed_at_ms, true))} · 末根${run.provenance.last_bar_closed ? '已收盘' : '未收盘'}</dd>` : ''}
    ${latency ? `<dt>耗时</dt><dd>${escapeHtml(latency)}</dd>` : ''}
    ${usage ? `<dt>用量</dt><dd>${escapeHtml(usage)}</dd>` : ''}
    ${run.image_sha256 ? `<dt>图片 SHA-256</dt><dd class="hash-value">${escapeHtml(run.image_sha256)}</dd>` : ''}
  </dl></details>`;
  const reviewVerdict = human ? `<div class="review-verdict">人工复核：${escapeHtml(verdictLabel(human.verdict))}${human.note ? ` · ${escapeHtml(human.note)}` : ''}<br><span>${escapeHtml(formatDate(human.reviewed_at))}</span></div>` : '';
  const runId = asText(run.id, '');
  const reviewControls = !completed ? '<p class="review-locked">只有已完成的识别可以人工复核。</p>' : `<label class="sr-only" for="review-note">人工复核备注</label>
      <textarea id="review-note" class="review-note" maxlength="1200" placeholder="可选：记录边界或依据">${human?.note ? escapeHtml(human.note) : ''}</textarea>
      <div class="review-buttons" role="group" aria-label="记录人工复核结论">
        <button type="button" data-review-verdict="accepted" data-review-run="${escapeHtml(runId)}">接受</button>
        <button type="button" data-review-verdict="rejected" data-review-run="${escapeHtml(runId)}">拒绝</button>
        <button type="button" data-review-verdict="uncertain" data-review-run="${escapeHtml(runId)}">不确定</button>
      </div>`;
  updateResultMarkup(`<article class="decision-card" data-result-run="${escapeHtml(runId)}">
    <div class="decision-topline"><span class="decision-label"><span class="verdict-pill ${incomplete ? 'failed' : verdictClass}">${escapeHtml(headingLabel)}</span></span><span class="side-tag">${escapeHtml(sideText)}</span></div>
    <p class="result-summary">${escapeHtml(asText(decision.summary, failureReason || '模型未返回摘要。'))}</p>
    <section class="result-section" aria-label="可观察证据"><h4>可观察证据</h4>${evidenceHtml}</section>
    <section class="result-section risks" aria-label="不确定性与风险"><h4>不确定性与风险</h4>${risksHtml}</section>
    ${requestContext}
    <div class="exchange-result-action">${apiExchangeActionMarkup(run.api_exchange_id)}</div>
    <details class="human-review" aria-label="人工复核">
      <summary class="human-review-heading"><strong>人工复核</strong><span>与模型结果分别记录</span></summary>
      ${reviewVerdict}
      ${reviewControls}
    </details>
  </article>`);
  resultEl.querySelectorAll('[data-review-verdict]').forEach((button) => {
    button.disabled = state.reviewLoadingId === runId;
  });
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
    const providerName = providerNameForRecord(run);
    const symbol = asText(run.symbol || run.signal?.symbol, '');
    const timeframe = asText(run.timeframe || run.signal?.timeframe, '');
    const title = symbol ? `${symbol} · ${timeframe || '周期未提供'}` : asText(run.image_name, '自定义图片');
    const imageUrl = safeApiImageUrl(run.image_url);
    const thumbnail = imageUrl ? `<div class="history-thumbnail"><img src="${escapeHtml(imageUrl)}" alt="${escapeHtml(title)}" loading="lazy" /></div>` : '';
    const review = run.review ? `<div class="history-review"><span>人工复核</span><strong>${escapeHtml(verdictLabel(run.review.verdict))}</strong>${run.review.note ? `<span>${escapeHtml(run.review.note)}</span>` : ''}<time>${escapeHtml(formatDate(run.review.reviewed_at))}</time></div>` : '';
    const runId = asText(run.id, '');
    const summary = asText(decision.summary, run.error || '无摘要');
    const isExporting = state.exportLoadingId === runId;
    return `<article class="history-card">
      ${thumbnail}<div class="history-main"><div class="history-head"><h2>${escapeHtml(title)}</h2><span class="verdict-pill ${verdictClass}">${escapeHtml(verdictLabel(verdict))}</span><span class="source-chip">${run.analysis_scope === 'current_right_edge' ? '当前盘口' : '旧版整图'}</span></div>
        <p class="history-summary">${escapeHtml(summary)}</p>
        <div class="history-meta"><time>${escapeHtml(formatDate(run.created_at))}</time><span>${escapeHtml(providerName)} · ${escapeHtml(model)}</span><span>${escapeHtml(runStatusLabel(run.status))}</span>${run.latency_ms ? `<span>${escapeHtml(Math.round(Number(run.latency_ms)))} ms</span>` : ''}</div>
      </div>
      <div class="history-actions"><button class="button button-secondary" type="button" data-open-run="${escapeHtml(runId)}">查看 / 复核</button>${run.api_exchange_id ? `<button class="button button-quiet" type="button" data-open-exchange="${escapeHtml(run.api_exchange_id)}">查看模型对话</button>` : '<span class="exchange-unavailable">旧记录未保存原文</span>'}<button class="button button-quiet" type="button" data-export-run="${escapeHtml(runId)}" ${isExporting ? 'disabled' : ''}>${isExporting ? '导出中…' : '导出 JSON'}</button></div>
      ${review}
    </article>`;
  }).join('');
  historyEl.querySelectorAll('.history-thumbnail img').forEach((image) => {
    image.addEventListener('error', () => image.closest('.history-thumbnail')?.remove(), { once: true });
  });
}

function credentialSummary(source) {
  const known = sourceLabel(source);
  const lower = asText(source).toLowerCase();
  const configured = !['', 'unset', 'none', 'missing', 'not_configured', 'null'].includes(lower);
  return { label: known, configured };
}

function renderExchangeDetail(detail) {
  const target = byId('api-exchange-detail');
  const title = exchangeTitle(detail);
  // Background status refreshes must not collapse text or jump the conversation.
  if (renderedExchangeDetail === detail && renderedExchangeTitle === title && target.querySelector('.conversation-thread')) return;
  renderedExchangeDetail = detail;
  renderedExchangeTitle = title;
  const chat = exchangeConversation(detail);
  const compactRequest = chat.requestText.length > 140
    ? (chat.requestText.match(/^[\s\S]{1,140}?[。！？\n]/u)?.[0] || `${chat.requestText.slice(0,120)}…`).trim()
    : chat.requestText;
  const request = detail.request || {};
  const requestText = typeof request.body_text === 'string' ? request.body_text : '';
  const images = collectExchangeImages(requestText);
  const id = asText(detail.id || state.selectedExchangeId);
  const preview = requestBodyPreview(requestText);
  const decision = chat.decision;
  const completed = detail.status === 'completed';
  const status = exchangeStatusLabel(detail.status);
  const latency = detail.latency_ms == null ? '' : `用时 ${(Number(detail.latency_ms) / 1000).toFixed(1)} 秒`;
  const meta = [asText(detail.model), chat.effort ? `推理 ${chat.effort}` : '', latency, chat.usageText].filter(Boolean);
  const imageButton = (image, index) => `<button class="chat-image ${index === 0 ? 'chat-image-primary' : ''}" type="button" data-exchange-image="${index}" aria-label="放大${escapeHtml(image.title)}"><img src="${escapeHtml(image.dataUrl)}" alt="${escapeHtml(image.title)}" loading="lazy"><span>${escapeHtml(image.title)}</span></button>`;
  const list = (items) => `<ul>${safeList(items).map((item) => `<li>${escapeHtml(item)}</li>`).join('')}</ul>`;
  const answer = decision ? `
    <div class="chat-verdict"><strong>${escapeHtml(verdictLabel(decision.verdict))}</strong><span>${escapeHtml(currentStateLabel(decision.current_state) || sideLabel(decision.side))}</span></div>
    ${chatParagraphs(decision.summary)}
    ${safeList(decision.evidence).length ? `<section class="chat-evidence"><h4>判断依据</h4>${list(decision.evidence)}</section>` : ''}
    ${safeList(decision.risks).length ? `<section class="chat-risks"><h4>需要留意</h4>${list(decision.risks)}</section>` : ''}
  ` : chatParagraphs(chat.responseText);
  target.innerHTML = `<article class="conversation-thread">
    <header class="conversation-thread-heading"><div><h2>${escapeHtml(title)}</h2><time>${escapeHtml(formatDate(detail.created_at))}</time></div><span class="conversation-state ${completed ? '' : 'is-incomplete'}">${escapeHtml(status)}</span></header>
    <div class="conversation-messages">
      <section class="chat-turn chat-user" aria-label="发送给模型的内容">
        <div class="chat-speaker">你 <span>· ${escapeHtml(exchangeKindLabel(detail.kind))}</span></div>
        <div class="chat-user-bubble">
          ${images.length ? `<div class="chat-attachments">${imageButton(images[0], 0)}${images.length > 1 ? `<details class="chat-reference-images"><summary>随附 ${images.length - 1} 张参考图</summary><div class="chat-reference-gallery">${images.slice(1).map((image, index) => imageButton(image, index + 1)).join('')}</div></details>` : ''}</div>` : ''}
          <div class="chat-request">${chatParagraphs(compactRequest)}</div>
          ${compactRequest !== chat.requestText ? `<details class="chat-disclosure chat-prompt"><summary>完整识别要求</summary><div class="chat-plain-text">${escapeHtml(chat.requestText)}</div></details>` : ''}
        </div>
      </section>
      <section class="chat-turn chat-assistant" aria-label="模型回答">
        <div class="chat-speaker">${escapeHtml(providerNameForRecord(detail))} <span>· ${escapeHtml(asText(detail.model, '模型'))}</span></div>
        ${chat.reasoningText ? '<details class="chat-disclosure chat-thinking"><summary>查看思考过程</summary><div class="chat-plain-text" data-chat-reasoning></div></details>' : ''}
        ${chat.errorText ? `<div class="chat-error" role="status">${chatParagraphs(chat.errorText)}</div>` : ''}
        <div class="chat-answer">${answer || `<p class="chat-muted">${detail.status === 'running' ? '正在等待模型回答…' : '本次没有可展示的回答。'}</p>`}</div>
        <div class="chat-meta">${meta.map((item) => `<span>${escapeHtml(item)}</span>`).join('')}</div>
      </section>
      <details class="chat-disclosure chat-technical"><summary>技术详情与原始记录</summary>
        <div class="chat-technical-content">
          <p>HTTP ${escapeHtml(asText(detail.http_status, '未返回'))}${detail.redacted ? ' · 密钥回显已隐藏' : ''} · 原始输入输出保留如下</p>
          <a class="button button-secondary" href="${API_BASE}/exchanges/${encodeURIComponent(id)}/export" download="api-exchange-${escapeHtml(id)}.json"><span class="ui-icon icon-download" aria-hidden="true"></span>下载完整记录</a>
          <details class="chat-disclosure"><summary>发送的完整提示词</summary><div class="chat-plain-text" data-chat-prompt></div></details>
          <details class="exchange-body-block"><summary>请求 JSON（图片已折叠）</summary><pre data-exchange-request></pre></details>
          <details class="exchange-body-block"><summary>完整请求（含图片 Base64）</summary><pre data-exchange-request-original></pre></details>
          <details class="exchange-body-block"><summary>原始响应 JSON</summary><pre data-exchange-response></pre></details>
          <p class="chat-endpoint" data-chat-endpoint></p>
        </div>
      </details>
    </div>
  </article>`;
  target.querySelector('[data-chat-prompt]').textContent = chat.requestFullText || '本条记录没有保存完整提示词。';
  const reasoning = target.querySelector('[data-chat-reasoning]');
  if (reasoning) reasoning.textContent = chat.reasoningText;
  target.querySelector('[data-exchange-request]').textContent = requestText ? preview.text : '未保存请求正文。';
  target.querySelector('[data-exchange-request-original]').textContent = requestText || '未保存请求正文。';
  target.querySelector('[data-exchange-response]').textContent = detail.response?.body_text || '未收到响应正文。';
  target.querySelector('[data-chat-endpoint]').textContent = `${request.method || ''} ${request.url || ''}`;
}

function renderExchangeRecords() {
  const list = byId('api-exchange-list');
  const statusTarget = byId('api-exchanges-status');
  const detailTarget = byId('api-exchange-detail');
  const exchanges = state.exchanges.filter((item) => item && ['recognition', 'connection_test'].includes(item.kind));
  const options = [...exchanges];
  if (state.selectedExchangeId && !options.some((item) => asText(item.id) === state.selectedExchangeId)) {
    options.unshift({ id: state.selectedExchangeId, placeholder: true });
  }
  const markup = options.map((item) => `<button class="conversation-item ${asText(item.id) === state.selectedExchangeId ? 'is-selected' : ''}" type="button" data-select-exchange="${escapeHtml(asText(item.id))}" ${asText(item.id) === state.selectedExchangeId ? 'aria-current="true"' : ''}><strong>${escapeHtml(item.placeholder ? '正在读取对话…' : exchangeTitle(item))}</strong><span><time>${escapeHtml(formatDate(item.created_at))}</time><span>${escapeHtml(providerNameForRecord(item))}</span><span class="${item.status === 'failed' ? 'is-failed' : ''}">${escapeHtml(exchangeStatusLabel(item.status))}</span></span></button>`).join('');
  if (list.innerHTML !== markup) list.innerHTML = markup || '<p class="conversation-list-empty">还没有对话</p>';
  statusTarget.textContent = state.exchangesLoading ? '正在读取…' : `${exchanges.length} 段对话`;
  showInlineError('api-exchanges-error', state.exchangesError);
  byId('reload-api-exchanges').disabled = state.exchangesLoading;
  if (state.exchangeDetailLoading) {
    detailTarget.innerHTML = '<p class="exchange-empty"><span class="spinner" aria-hidden="true"></span>正在读取对话…</p>';
  } else if (state.exchangeDetailError) {
    detailTarget.innerHTML = `<div class="exchange-load-error"><p>${escapeHtml(state.exchangeDetailError)}</p><button class="button button-secondary" type="button" data-reload-exchange-detail>重试读取</button></div>`;
  } else if (state.exchangeDetail && asText(state.exchangeDetail.id || state.selectedExchangeId) === state.selectedExchangeId) {
    renderExchangeDetail(state.exchangeDetail);
  } else if (!state.exchangesLoading && !exchanges.length && !state.selectedExchangeId) {
    detailTarget.innerHTML = '<div class="conversation-empty"><h2>从一张图表开始</h2><p>在工作台完成识别后，这里会显示发送的图片和 AI 的回答。</p></div>';
  } else if (!state.exchangeDetailLoading) {
    detailTarget.innerHTML = '<p class="exchange-empty">选择一段对话，查看图片与回答。</p>';
  }
}

async function loadExchangeDetail(id) {
  const exchangeId = asText(id, '');
  if (!exchangeId) return;
  const token = ++state.exchangeDetailToken;
  state.selectedExchangeId = exchangeId;
  state.exchangeDetail = null;
  state.exchangeDetailError = '';
  state.exchangeDetailLoading = true;
  renderExchangeRecords();
  try {
    const detail = await apiJson(`/exchanges/${encodeURIComponent(exchangeId)}`);
    if (!detail || typeof detail !== 'object') throw new Error('API 原始记录格式无效。');
    if (token !== state.exchangeDetailToken || state.selectedExchangeId !== exchangeId) return;
    const normalized = { ...detail, id: asText(detail.id, exchangeId) };
    state.exchangeDetail = normalized;
    const existingIndex = state.exchanges.findIndex((item) => asText(item.id) === exchangeId);
    if (existingIndex >= 0) state.exchanges[existingIndex] = { ...state.exchanges[existingIndex], ...normalized };
    else state.exchanges.unshift(normalized);
  } catch (error) {
    if (token !== state.exchangeDetailToken || state.selectedExchangeId !== exchangeId) return;
    state.exchangeDetailError = error.message || 'API 原始记录读取失败。';
  } finally {
    if (token === state.exchangeDetailToken && state.selectedExchangeId === exchangeId) {
      state.exchangeDetailLoading = false;
      renderExchangeRecords();
    }
  }
}

function selectExchange(id) {
  const exchangeId = asText(id, '');
  state.exchangeDetailError = '';
  state.selectedExchangeId = exchangeId;
  if (exchangeId) loadExchangeDetail(exchangeId);
  else {
    state.exchangeDetailToken += 1;
    state.exchangeDetail = null;
    state.exchangeDetailLoading = false;
    renderExchangeRecords();
  }
}

async function loadExchangeRecords({ preferredId = '', selectNewest = false, reloadSelected = false } = {}) {
  const token = ++state.exchangeListToken;
  state.exchangesLoading = true;
  state.exchangesError = '';
  renderExchangeRecords();
  try {
    const payload = await apiJson('/exchanges', { cache: 'no-store' });
    if (token !== state.exchangeListToken) return;
    state.exchanges = Array.isArray(payload?.items)
      ? payload.items.filter((item) => item && typeof item === 'object' && ['recognition', 'connection_test'].includes(item.kind))
      : [];
    const desiredId = preferredId || (selectNewest ? '' : state.selectedExchangeId) || asText(state.exchanges[0]?.id, '');
    const detailMatches = state.exchangeDetail && asText(state.exchangeDetail.id) === desiredId;
    if (desiredId && (reloadSelected || !detailMatches || desiredId !== state.selectedExchangeId)) {
      state.selectedExchangeId = desiredId;
      loadExchangeDetail(desiredId);
    } else if (!desiredId) {
      state.selectedExchangeId = '';
      state.exchangeDetail = null;
      state.exchangeDetailLoading = false;
    }
  } catch (error) {
    if (token !== state.exchangeListToken) return;
    state.exchangesError = error.message || 'API 原始记录读取失败。';
  } finally {
    if (token === state.exchangeListToken) {
      state.exchangesLoading = false;
      renderExchangeRecords();
    }
  }
}

function openExchange(id) {
  const exchangeId = asText(id, '');
  if (!exchangeId) return;
  switchTab('settings');
  state.exchangeDetailError = '';
  selectExchange(exchangeId);
  renderExchangeRecords();
  byId('api-exchanges-heading').scrollIntoView({ block: 'start' });
}

function renderSettings() {
  const statusTarget = byId('settings-status');
  const providerSelect = byId('provider-select');
  const regionField = byId('qwen-region-field');
  const regionSelect = byId('region-select');
  const modelSelect = byId('model-input');
  const keyInput = byId('api-key-input');
  const draft = ensureConfigDraft();
  const selectedSpec = providerSpec(draft.provider);
  const savedConfig = configFromStatus(state.status);
  const savedSpec = providerSpec(savedConfig.provider);
  const hasKeyDraft = Boolean(keyInput.value.trim());
  const matchesSaved = Boolean(state.status) && configMatchesStatus(draft, state.status) && !hasKeyDraft;
  const selectedProviderName = providerDisplayName(draft.provider, selectedSpec.name);
  const savedProviderName = providerDisplayName(state.status?.provider, state.status?.provider_name);
  const selectedRegionName = providerRegions(selectedSpec).find(([id]) => id === draft.region)?.[1] || draft.region;
  const savedRegionName = providerRegions(savedSpec).find(([id]) => id === savedConfig.region)?.[1] || savedConfig.region;

  const providerOptions = [...providerSpecs()];
  if (!providerOptions.some((item) => item.id === selectedSpec.id)) providerOptions.push(selectedSpec);
  providerSelect.innerHTML = providerOptions.map((item) => `<option value="${escapeHtml(asText(item.id))}">${escapeHtml(providerDisplayName(item.id, item.name))}</option>`).join('');
  providerSelect.value = draft.provider;

  const regions = providerRegions(selectedSpec);
  regionSelect.innerHTML = regions.map(([id, name]) => `<option value="${escapeHtml(id)}">${escapeHtml(name)}</option>`).join('');
  if (!regions.some(([id]) => id === draft.region)) {
    regionSelect.insertAdjacentHTML('beforeend', `<option value="${escapeHtml(draft.region)}">${escapeHtml(draft.region)} · 当前</option>`);
  }
  regionSelect.value = draft.region;
  regionField.hidden = draft.provider !== 'qwen';

  const models = providerModels(selectedSpec);
  if (!models.includes(draft.model)) models.unshift(draft.model);
  modelSelect.innerHTML = models.map((model) => `<option value="${escapeHtml(model)}">${escapeHtml(model)}</option>`).join('');
  modelSelect.value = draft.model;
  for (const control of [providerSelect, regionSelect, modelSelect]) control.disabled = state.configLoading || !state.status;

  if (state.status) {
    const key = credentialSummary(state.status.credential_source);
    const keyConfigured = typeof state.status.api_key_configured === 'boolean' ? state.status.api_key_configured : key.configured;
    const savedKeyLabel = keyConfigured ? key.label : '待配置密钥';
    byId('model-config-summary').textContent = `已保存 · ${savedProviderName} · ${savedConfig.model}`;
    byId('config-selection-status').textContent = matchesSaved
      ? `所选设置与当前已保存配置一致：${selectedProviderName} · ${draft.model}`
      : `尚未保存：选择为 ${selectedProviderName} · ${selectedRegionName} · ${draft.model}。当前仍使用已保存的 ${savedProviderName} · ${savedConfig.model}。`;
    keyInput.placeholder = matchesSaved && keyConfigured ? '已保存在本机；留空会保留当前密钥' : `粘贴与所选服务商及地域匹配的 API Key 后保存`;
    const connectionLabel = state.apiReachable === false ? '未连接 · 正在恢复连接' : '已连接';
    const connectionError = state.apiReachable === false
      ? `<div class="inline-error">${escapeHtml(state.apiTransportError || '未收到本机 API 响应。')}</div>`
      : state.statusError ? `<div class="inline-error">${escapeHtml(state.statusError)}</div>` : '';
    statusTarget.innerHTML = `<div class="status-line"><strong>本地 API</strong><span>${connectionLabel}</span></div>${connectionError}
      <div class="status-line"><strong>当前服务商</strong><span>${escapeHtml(savedProviderName)}</span></div>
      ${state.status.region ? `<div class="status-line"><strong>当前地域</strong><span>${escapeHtml(savedRegionName)}</span></div>` : ''}
      <div class="status-line"><strong>当前接收端点</strong><code>${escapeHtml(asText(state.status.endpoint, providerEndpoint(savedSpec, savedConfig.region) || '由本机服务配置'))}</code></div>
      <div class="status-line"><strong>当前模型</strong><code>${escapeHtml(asText(state.status.model, state.model))}</code></div>
      <div class="status-line"><strong>凭据来源</strong><span class="status-key ${keyConfigured ? '' : 'is-missing'}">${escapeHtml(savedKeyLabel)}</span></div>
      <div class="status-line"><strong>SPIKE 数据源</strong><span>${state.status.spike?.available === true ? `可用 · ${escapeHtml(asText(state.status.spike.count, ''))} 条 · ${escapeHtml(sourceLabel(state.status.spike.source))}` : state.status.spike?.available === false ? '当前不可用' : '状态未返回'}</span></div>`;
  } else if (state.statusError) {
    const label = state.apiReachable === false ? '本机 API 暂不可达' : '无法读取本机 API 状态';
    statusTarget.innerHTML = `<div class="inline-error">${label}：${escapeHtml(state.statusError)}</div>`;
    byId('model-config-summary').textContent = '尚未读取已保存配置';
    byId('config-selection-status').textContent = '连接本机服务后才能读取或修改模型配置。';
  } else {
    statusTarget.innerHTML = '<div class="status-loading"><span class="spinner" aria-hidden="true"></span>正在读取服务状态…</div>';
    byId('model-config-summary').textContent = '正在读取配置…';
    byId('config-selection-status').textContent = '等待读取当前配置。';
  }

  const selectedEndpoint = providerEndpoint(selectedSpec, draft.region) || (draft.provider === savedConfig.provider && draft.region === savedConfig.region ? asText(state.status?.endpoint) : '');
  byId('recipient-provider-name').textContent = matchesSaved ? savedProviderName : `${selectedProviderName}（待保存）`;
  byId('recipient-endpoint').textContent = selectedEndpoint || (matchesSaved ? asText(state.status?.endpoint, '由本机服务配置') : '保存后由本机服务切换');
  byId('recipient-heading').textContent = matchesSaved ? '当前已保存接收方' : '保存后将使用的接收方';

  const regionKeyUrl = safeHelpUrl(selectedSpec.key_help_url);
  const keyHelp = byId('api-key-help');
  keyHelp.hidden = !regionKeyUrl;
  keyHelp.href = regionKeyUrl || '#';
  byId('api-key-help-note').textContent = draft.provider === 'qwen'
    ? draft.region === 'beijing'
      ? '请创建北京地域 DashScope 按量付费 API Key，并确保与端点地域一致；Coding Plan 密钥不能代替该 API Key。'
      : `请创建与${selectedRegionName}端点相同地域的 DashScope API Key。`
    : '请创建与所选服务商及地域匹配的 API Key。';

  const label = byId('save-config').querySelector('.button-label');
  label.textContent = state.configLoading ? '正在保存…' : '保存设置';
  setVisible(byId('save-config').querySelector('.button-spinner'), state.configLoading);
  byId('save-config').disabled = state.configLoading;
  const testLabel = byId('connection-test').querySelector('.button-label');
  testLabel.textContent = state.connectionTesting ? '正在测试…' : matchesSaved ? `测试${savedProviderName}连接` : '先保存所选设置后再测试';
  setVisible(byId('connection-test').querySelector('.button-spinner'), state.connectionTesting);
  byId('connection-test').disabled = state.connectionTesting || state.configLoading || !matchesSaved;
  byId('connection-test-help').textContent = matchesSaved
    ? '连接测试会发送一条简短请求到当前已保存的服务，产生少量模型用量。'
    : '测试连接只针对已保存配置；请先保存当前选择。测试请求会产生少量模型用量。';
  syncProviderDisclosure();
  if (state.lastConnection) {
    const output = byId('connection-result');
    output.className = `connection-result ${state.lastConnection.ok ? 'is-ok' : 'is-bad'}`;
    output.textContent = `${state.lastConnection.ok ? '连接成功' : '连接未通过'} · ${asText(state.lastConnection.model, state.model)} · ${asText(state.lastConnection.message, '')}`;
    setVisible(output, true);
    const exchangeButton = byId('open-last-connection-exchange');
    exchangeButton.dataset.openExchange = asText(state.lastConnection.apiExchangeId, '');
    setVisible(exchangeButton, Boolean(state.lastConnection.apiExchangeId));
  } else {
    setVisible(byId('open-last-connection-exchange'), false);
  }
  renderExchangeRecords();
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
  for (const button of document.querySelectorAll('.nav-tab[data-tab]')) {
    const active = button.dataset.tab === state.tab;
    button.classList.toggle('is-active', active);
    button.setAttribute('aria-selected', String(active));
    button.tabIndex = active ? 0 : -1;
  }
  for (const id of ['workspace', 'replay', 'references', 'history', 'settings']) {
    const panel = byId(`tab-${id}`);
    setVisible(panel, state.tab === id);
  }
  const pageHeadings = {
    workspace: ['VLM 工作流', '使用视觉语言模型研究图表形态，与 YOLO 并行验证。'],
    replay: ['验证／历史回放', '逐步揭示历史行情，保留当时的判断与后续观察。'],
    references: ['参考图库', '管理后续识别共用的形态参考图。'],
    history: ['识别记录', '查看历史结果、人工复核与 API 原始记录。'],
    settings: ['模型对话', '查看每一次识别的图片与回答。'],
  };
  const [title, description] = pageHeadings[state.tab] || pageHeadings.workspace;
  const pageTitle = byId('page-title');
  const pageDescription = byId('page-description');
  if (pageTitle) pageTitle.textContent = title;
  if (pageDescription) pageDescription.textContent = description;
}

async function loadStatus() {
  if (state.statusPolling) return;
  state.statusPolling = true;
  try {
    const status = await apiJson('/status');
    if (!status || typeof status !== 'object') throw new Error('本地 API 状态格式无效。');
    const previousSaved = state.status ? configFromStatus(state.status) : null;
    const preserveDraft = Boolean(previousSaved && state.configDraftInitialized && !configMatchesStatus(state.configDraft, state.status));
    const savedProfileChanged = Boolean(previousSaved && (previousSaved.provider !== asText(status.provider, 'zhipu')
      || previousSaved.region !== configFromStatus(status).region));
    state.status = status;
    state.statusError = '';
    state.statusErrorIsTransport = false;
    state.model = asText(status.model, state.model || DEFAULT_MODEL);
    if (!preserveDraft) {
      state.configDraft = configFromStatus(status);
      state.configDraftInitialized = true;
      if (savedProfileChanged) byId('api-key-input').value = '';
    }
    if (!state.criteriaEdited && typeof status.default_criteria === 'string' && status.default_criteria.trim()) {
      state.criteria = status.default_criteria;
      byId('criteria-input').value = status.default_criteria;
      byId('criteria-count').textContent = `${status.default_criteria.length} 字`;
    }
  } catch (error) {
    state.statusError = error.message || '无法连接本地 API。';
    state.statusErrorIsTransport = error.code === 'LOCAL_API_UNREACHABLE';
    if (state.apiReachable !== false) setHealth(true, '本地服务已连接');
  } finally {
    state.statusPolling = false;
    renderEligibility();
    renderSignalSource();
    renderSettings();
  }
}

async function loadSignals({ quiet = false } = {}) {
  if (quiet && state.signalsPolling) return;
  const token = ++state.signalsRequestToken;
  state.signalsPolling = true;
  if (!quiet) {
    state.signalsLoading = true;
    state.signalsError = '';
    renderSignals();
  }
  try {
    const payload = await apiJson('/signals', { cache: 'no-store' });
    if (token !== state.signalsRequestToken) return;
    const items = Array.isArray(payload?.items) ? payload.items.filter((item) => item && typeof item === 'object') : [];
    for (const signal of items) rememberAutomaticJob(signal.ai_review);
    state.signals = items;
    state.signalsWarning = asText(payload?.warning, '');
    state.signalsError = '';
    const selectedId = asText(state.selectedSignal?.id);
    const freshSelected = items.find((item) => asText(item.id) === selectedId);
    if (freshSelected) {
      state.selectedSignal = freshSelected;
      if (state.selectedImage?.key === `signal:${selectedId}`) state.selectedImage.signal = freshSelected;
    }
  } catch (error) {
    if (token !== state.signalsRequestToken) return;
    if (quiet) state.signalsWarning = error.message || '候选列表更新失败，保留上次数据。';
    else {
      state.signals = [];
      state.signalsError = error.message || '候选信号读取失败。';
    }
  } finally {
    if (token === state.signalsRequestToken) {
      state.signalsPolling = false;
      state.signalsLoading = false;
      renderSignals();
    }
  }
}

function requestAutomaticRecordRefresh(runId) {
  const id = asText(runId, '');
  if (!id || state.automaticRefreshRequestedRunIds.has(id)) return;
  state.automaticRefreshRequestedRunIds.add(id);
  state.automaticRefreshPendingRunIds.add(id);
  scheduleAutomaticRecordRefresh();
}

function scheduleAutomaticRecordRefresh() {
  if (state.automaticRefreshScheduled || !state.automaticRefreshPendingRunIds.size) return;
  state.automaticRefreshScheduled = true;
  window.setTimeout(() => {
    state.automaticRefreshScheduled = false;
    if (state.runsLoading || !state.automaticRefreshPendingRunIds.size) return;
    state.automaticRefreshPendingRunIds.clear();
    Promise.allSettled([loadRuns(), loadExchangeRecords()]);
  }, 0);
}

function cancelLiveChartRequest() {
  state.chartRefreshToken += 1;
  state.chartAbort?.abort();
  state.chartAbort = null;
}

async function refreshSelectedLiveChart({ manual = false, initial = false } = {}) {
  const image = state.selectedImage;
  const signal = state.selectedSignal;
  if (!signal || image?.source !== 'signal' || state.analysisLoading || state.captureInProgress || image?.frozen) return false;
  if (!initial && image.loading) return false;
  if (!manual && (image.chartRefreshing || state.chartAbort)) return false;
  if (!manual && (state.tab !== 'workspace' || document.hidden)) return false;

  cancelLiveChartRequest();
  const token = state.chartRefreshToken;
  const key = image.key;
  const id = asText(signal.id);
  const controller = new AbortController();
  state.chartAbort = controller;
  state.selectedImage = { ...image, loading: initial || image.loading, chartRefreshing: !initial, error: '', liveRefreshMessage: '' };
  renderWorkspace();

  const stillCurrent = () => token === state.chartRefreshToken
    && state.selectedImage?.key === key
    && state.selectedImage?.source === 'signal'
    && !state.selectedImage?.frozen
    && !state.analysisLoading
    && !state.captureInProgress && state.tab === 'workspace' && !document.hidden;
  try {
    const query = new URLSearchParams({ mode: 'live', post_signal_bars: String(state.postSignalBars) });
    const data = await apiJson(`/signals/${encodeURIComponent(id)}/chart?${query}`, {
      signal: controller.signal, cache: 'no-store',
    });
    if (!stillCurrent()) return false;
    if (!Array.isArray(data?.candles) || !data.candles.length
        || data?.provenance?.time_boundary !== 'live_observation'
        || !/^[a-f0-9]{32}$/i.test(String(data?.snapshot_id || ''))
        || !/^[a-f0-9]{64}$/i.test(String(data?.chart_sha256 || ''))) {
      throw new Error('实时图表快照格式无效，已暂停识别。');
    }
    const current = state.selectedImage;
    const changed = current.chartData?.chart_sha256 && current.chartData.chart_sha256 !== data.chart_sha256;
    if (changed) {
      state.activeRun = null;
      state.activeRunInputKey = null;
    }
    state.selectedImage = {
      ...current, loading: false, chartRefreshing: false, error: '',
      chartData: data, chartSha256: data.chart_sha256, frozen: false,
      previewUrl: null, dataUrl: null, liveRefreshMessage: '', liveRefreshError: false,
    };
    state.chartAbort = null;
    if (initial) {
      byId('candidates-panel').classList.remove('is-open');
      byId('toggle-candidates').setAttribute('aria-expanded', 'false');
    }
    renderWorkspace();
    return true;
  } catch (error) {
    if (error.name === 'AbortError' || token !== state.chartRefreshToken) return false;
    if (!stillCurrent()) return false;
    const current = state.selectedImage;
    if (current.chartData) {
      const staleData = {
        ...current.chartData,
        provenance: { ...current.chartData.provenance, source_stale: true, recognition_eligible: false },
      };
      state.selectedImage = {
        ...current, loading: false, chartRefreshing: false, chartData: staleData,
        chartSha256: staleData.chart_sha256, liveRefreshMessage: error.message || '实时行情读取失败',
        liveRefreshError: true,
      };
    } else {
      state.selectedImage = {
        ...current, loading: false, chartRefreshing: false,
        error: error.message || '实时图表读取失败。', liveRefreshMessage: '', liveRefreshError: true,
      };
    }
    state.chartAbort = null;
    renderWorkspace();
    return false;
  } finally {
    if (token === state.chartRefreshToken && state.selectedImage?.key === key
        && state.selectedImage.chartRefreshing) {
      state.selectedImage = { ...state.selectedImage, loading: false, chartRefreshing: false };
      renderWorkspace();
    }
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
    renderExchangeRecords();
    scheduleAutomaticRecordRefresh();
  }
}

function revokeImage(image) {
  if (image?.previewUrl?.startsWith('blob:')) URL.revokeObjectURL(image.previewUrl);
}

function clearActiveImage({ keepNotice = false } = {}) {
  state.imageToken += 1;
  state.imageAbort?.abort();
  state.imageAbort = null;
  cancelLiveChartRequest();
  destroyChart();
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
  if (state.selectedSignal?.id === signal.id && state.selectedImage?.chartData) return;
  state.imageToken += 1;
  state.imageAbort?.abort();
  state.imageAbort = null;
  cancelLiveChartRequest();
  destroyChart();
  revokeImage(state.selectedImage);
  state.selectedSignal = signal;
  state.selectedImage = { key: `signal:${id}`, name: `${asText(signal.symbol, '候选')} · ${asText(signal.timeframe, '')}`, source: 'signal', signal, loading: true, previewUrl: null, dataUrl: null };
  state.activeRun = null;
  state.activeRunInputKey = null;
  state.analysisError = '';
  state.notice = '';
  showMessage(workspaceErrorEl, '');
  renderWorkspace();
  await refreshSelectedLiveChart({ initial: true });
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
  if (file.size >= MAX_IMAGE_BYTES) throw new Error('图片须小于 5 MB，请选择较小的图片。');
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
  cancelLiveChartRequest();
  destroyChart();
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
  if (state.referencesSaving || !state.referencesReady) return;
  const selected = Array.from(files).slice(0, MAX_REFERENCES - state.references.length);
  const errors = [];
  for (const file of selected) {
    try {
      const dataUrl = await readFileDataUrl(file, validateFile(file));
      await verifyImageUrl(dataUrl);
      state.references.push({ name: file.name, dataUrl, previewUrl: dataUrl, byteSize: file.size });
      state.referencesDirty = true;
    } catch (error) { errors.push(`${file.name}: ${error.message}`); }
  }
  if (files.length > selected.length) errors.push('单次最多 50 张图（含待判图），请减少参考图数量。');
  state.referencesError = errors.join(' ');
  byId('reference-file').value = '';
  renderReferences(); renderResult();
}

function removeReference(index) {
  if (state.referencesSaving || !Number.isInteger(index) || index < 0 || index >= state.references.length) return;
  state.references.splice(index, 1);
  state.referencesDirty = true;
  renderReferences(); renderResult();
}

async function analyze() {
  if (state.analysisLoading || !state.selectedImage || !state.referencesReady || state.referencesDirty || state.referencesSaving) return;
  if (state.selectedImage.source === 'history') {
    state.notice = '历史记录只供查看和人工复核；选择新候选或上传图片后可开始新的识别。';
    renderNotice();
    return;
  }
  const image = state.selectedImage;
  const signal = state.selectedSignal;
  if (image.loading || image.error || state.captureInProgress || (!signal && !image.dataUrl)) return;
  let capture = null;
  let chartViewport = null;
  if (signal && image.chartData) {
    const liveInfo = liveSnapshotInfo(image);
    if (!liveInfo?.usable) {
      state.analysisError = image.frozen ? '当前显示的是已发送快照；返回实时图表并刷新后才能再次识别。'
        : liveInfo?.expired ? '已超出当前信号后识别窗口；可扩大范围并刷新。'
          : liveInfo?.stale ? '行情滞后，当前图表不可识别；请刷新实时图表。'
            : '实时快照已过期或无效；请刷新图表后再识别。';
      renderResult();
      return;
    }
    cancelLiveChartRequest();
    image.chartRefreshing = false;
    state.captureInProgress = true;
    let captureFailure = '';
    try {
      const captured = captureChart();
      capture = captured.dataUrl;
      chartViewport = captured.viewport;
      image.previewUrl = capture;
      image.dataUrl = capture;
      image.frozen = true;
    } catch (error) {
      captureFailure = error.message || '图表截图失败。';
    } finally {
      state.captureInProgress = false;
    }
    if (captureFailure) {
      state.analysisError = captureFailure;
      renderResult();
      return;
    }
  }
  const inputKey = image.key;
  state.analysisLoading = true;
  state.analysisError = '';
  state.activeRun = null;
  state.activeRunInputKey = null;
  state.notice = '';
  renderWorkspace();
  const payload = {
    signal_id: signal ? asText(signal.id) : null,
    image_data_url: signal ? null : image.dataUrl,
    image_name: signal ? null : image.name,
    chart_capture_data_url: capture,
    expected_chart_sha256: signal ? image.chartSha256 : null,
    chart_snapshot_id: signal ? image.chartData?.snapshot_id || null : null,
    chart_viewport: chartViewport,
    reference_revision: state.referenceRevision,
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
    if (state.selectedSignal && state.selectedImage?.source === 'signal' && state.selectedImage.loading) {
      refreshSelectedLiveChart({ initial: true });
    }
  }
}

function safeApiImageUrl(raw) {
  if (!raw || typeof raw !== 'string') return null;
  try {
    const parsed = new URL(raw, window.location.origin);
    if (parsed.origin !== window.location.origin || !parsed.pathname.startsWith(`${API_BASE}/`)) return null;
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
  cancelLiveChartRequest();
  destroyChart();
  revokeImage(state.selectedImage);
  state.selectedSignal = null;
  state.activeRun = run;
  state.activeRunInputKey = `run:${id}`;
  state.analysisError = '';
  state.notice = '';
  const imageUrl = safeApiImageUrl(run.image_url);
  const runSymbol = asText(run.symbol || run.signal?.symbol, '');
  const runTimeframe = asText(run.timeframe || run.signal?.timeframe, '');
  const imageName = runSymbol
    ? `${runSymbol.replace(/-SWAP$/, '')}${runTimeframe ? ` · ${runTimeframe}` : ''}`
    : asText(run.image_name, '历史图表');
  state.selectedImage = imageUrl ? { key: `run:${id}`, name: imageName, source: 'history', signal: null, loading: true, previewUrl: null, dataUrl: null } : null;
  renderAll();
  window.scrollTo(0, 0);
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

function onConfigProviderChange(event) {
  const provider = event.currentTarget.value;
  const spec = providerSpec(provider);
  const firstRegion = providerRegions(spec)[0]?.[0] || asText(spec.default_region, 'default');
  state.configDraft = {
    provider,
    region: asText(spec.default_region, firstRegion),
    model: asText(spec.default_model, providerModels(spec)[0] || DEFAULT_MODEL),
  };
  state.configDraftInitialized = true;
  byId('api-key-input').value = '';
  state.lastConnection = null;
  showInlineError('config-error', '');
  showInlineSuccess('config-success', '');
  renderSettings();
}

function onConfigRegionChange(event) {
  if (!state.configDraft) return;
  state.configDraft = { ...state.configDraft, region: event.currentTarget.value };
  byId('api-key-input').value = '';
  state.lastConnection = null;
  showInlineError('config-error', '');
  showInlineSuccess('config-success', '');
  renderSettings();
}

function onConfigModelChange(event) {
  if (!state.configDraft) return;
  state.configDraft = { ...state.configDraft, model: event.currentTarget.value };
  state.lastConnection = null;
  showInlineError('config-error', '');
  showInlineSuccess('config-success', '');
  renderSettings();
}

async function saveConfig(event) {
  event.preventDefault();
  if (state.configLoading) return;
  showInlineError('config-error', '');
  showInlineSuccess('config-success', '');
  const draft = ensureConfigDraft();
  const model = asText(draft.model).trim();
  if (!model) {
    showInlineError('config-error', '请选择视觉模型。');
    byId('model-input').focus();
    return;
  }
  const keyInput = byId('api-key-input');
  const apiKey = keyInput.value;
  const payload = { provider: draft.provider, region: draft.region, model };
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
    state.configDraft = configFromStatus(status);
    state.configDraftInitialized = true;
    setHealth(true, '本地服务已连接');
    showInlineSuccess('config-success', '模型与密钥已保存在本机，重启后自动读取。');
  } catch (error) {
    showInlineError('config-error', error.message || '保存设置失败。');
  } finally {
    state.configLoading = false;
    renderAll();
  }
}

async function testConnection() {
  if (state.connectionTesting || !state.status || !configMatchesStatus(state.configDraft, state.status) || byId('api-key-input').value.trim()) return;
  state.connectionTesting = true;
  state.lastConnection = null;
  renderSettings();
  try {
    const result = await apiJson('/connection-test', { method: 'POST', body: '{}' });
    state.lastConnection = {
      ok: result?.ok === true,
      model: result?.model || state.model,
      message: result?.message || '',
      apiExchangeId: asText(result?.api_exchange_id, ''),
    };
  } catch (error) {
    state.lastConnection = {
      ok: false, model: state.model, message: error.message || '连接测试失败。',
      apiExchangeId: error.apiExchangeId || '',
    };
  } finally {
    state.connectionTesting = false;
    renderSettings();
    await loadExchangeRecords({
      preferredId: state.lastConnection?.apiExchangeId || '',
      selectNewest: !state.lastConnection?.apiExchangeId,
      reloadSelected: true,
    });
  }
}

function switchTab(tab) {
  if (!['workspace', 'replay', 'references', 'history', 'settings'].includes(tab)) return;
  const changed = state.tab !== tab;
  state.tab = tab;
  if (tab !== 'workspace') pauseLivePolling();
  if (tab === 'workspace') pollLiveChart();
  if (tab === 'references' && !state.referencesDirty && !state.referencesSaving) loadReferences();
  renderAll();
  setReplayActive(tab === 'replay');
  if (changed) window.scrollTo(0, 0);
  byId(`tab-${tab}-button`).focus({ preventScroll: true });
}

document.addEventListener('click', (event) => {
  if (event.target.closest('.brand')) {
    event.preventDefault();
    switchTab('workspace');
    return;
  }
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
  const previewReferenceButton = event.target.closest('[data-preview-reference]');
  if (previewReferenceButton) {
    const reference = state.references[Number(previewReferenceButton.dataset.previewReference)];
    if (reference) {
      byId('reference-viewer-title').textContent = reference.name;
      byId('reference-viewer-image').src = reference.previewUrl;
      byId('reference-viewer-image').alt = reference.name;
      byId('reference-viewer').showModal();
    }
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
    const runId = openRunButton.dataset.openRun;
    if (state.runs.some((run) => asText(run.id) === runId)) openRun(runId);
    else loadRuns().then(() => openRun(runId));
    return;
  }
  const selectExchangeButton = event.target.closest('[data-select-exchange]');
  if (selectExchangeButton) {
    selectExchange(selectExchangeButton.dataset.selectExchange);
    return;
  }
  const exchangeImageButton = event.target.closest('[data-exchange-image]');
  if (exchangeImageButton) {
    const image = collectExchangeImages(state.exchangeDetail?.request?.body_text || '')[Number(exchangeImageButton.dataset.exchangeImage)];
    if (image) {
      byId('reference-viewer-title').textContent = image.title;
      byId('reference-viewer-image').src = image.dataUrl;
      byId('reference-viewer-image').alt = image.title;
      byId('reference-viewer').showModal();
    }
    return;
  }
  const openExchangeButton = event.target.closest('[data-open-exchange]');
  if (openExchangeButton) {
    openExchange(openExchangeButton.dataset.openExchange);
    return;
  }
  const reloadExchangeButton = event.target.closest('[data-reload-exchange-detail]');
  if (reloadExchangeButton) {
    loadExchangeDetail(state.selectedExchangeId);
    return;
  }
  const exportButton = event.target.closest('[data-export-run]');
  if (exportButton) exportRun(exportButton.dataset.exportRun);
});

document.addEventListener('keydown', (event) => {
  const tabButton = event.target.closest('.nav-tab[data-tab]');
  if (tabButton && ['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown', 'Home', 'End'].includes(event.key)) {
    event.preventDefault();
    const tabs = Array.from(document.querySelectorAll('.nav-tab[data-tab]'));
    let index = tabs.indexOf(tabButton);
    if (event.key === 'Home') index = 0;
    else if (event.key === 'End') index = tabs.length - 1;
    else index = (index + (['ArrowRight', 'ArrowDown'].includes(event.key) ? 1 : tabs.length - 1)) % tabs.length;
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
byId('automatic-toggle').addEventListener('click', toggleAutomaticRecognition);
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
byId('provider-select').addEventListener('change', onConfigProviderChange);
byId('region-select').addEventListener('change', onConfigRegionChange);
byId('model-input').addEventListener('change', onConfigModelChange);
byId('api-key-input').addEventListener('input', renderSettings);
byId('connection-test').addEventListener('click', testConnection);
byId('reload-api-exchanges').addEventListener('click', () => Promise.allSettled([loadRuns(), loadExchangeRecords({ reloadSelected: true })]));


const dropZone = byId('drop-zone');
const chartFileInput = byId('chart-file');
dropZone.addEventListener('click', (event) => {
  if (state.selectedImage || event.target === chartFileInput || event.target.closest('a,button,input,select,textarea')) return;
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
initReplay();
renderAll();
if (window.location.hash === '#replay') switchTab('replay');
Promise.allSettled([loadStatus(), loadSignals(), loadAutomatic(), loadRuns(), loadReferences(), loadExchangeRecords()]);

byId('save-references').addEventListener('click', saveReferences);
byId('reload-references').addEventListener('click', loadReferences);
byId('open-references').addEventListener('click', () => switchTab('references'));
byId('upload-chart').addEventListener('click', () => chartFileInput.click());
byId('empty-upload').addEventListener('click', () => chartFileInput.click());
byId('fit-chart').addEventListener('click', () => {
  fitChart();
  state.analysisError = '';
  renderResult();
});
byId('resume-chart').addEventListener('click', () => {
  if (state.analysisLoading || !state.selectedImage?.chartData) return;
  state.selectedImage.frozen = false;
  state.activeRunInputKey = null;
  state.activeRun = null;
  state.analysisError = '';
  refreshSelectedLiveChart({ manual: true });
});
byId('toggle-candidates').addEventListener('click', () => {
  const expanded = byId('candidates-panel').classList.toggle('is-open');
  byId('toggle-candidates').setAttribute('aria-expanded', String(expanded));
});

function pauseLivePolling() {
  cancelLiveChartRequest();
  if (state.selectedImage?.chartRefreshing) state.selectedImage.chartRefreshing = false;
}

function pollLiveChart() {
  if (document.hidden || state.tab !== 'workspace' || state.analysisLoading || state.captureInProgress
      || state.selectedImage?.frozen || state.chartAbort) return;
  refreshSelectedLiveChart({ initial: Boolean(state.selectedImage?.loading) });
}

byId('refresh-live-chart').addEventListener('click', () => refreshSelectedLiveChart({ manual: true }));
byId('recognition-window').addEventListener('change', (event) => {
  const value = Number(event.target.value);
  if (!LIVE_WINDOW_OPTIONS.includes(value) || state.analysisLoading || state.selectedImage?.frozen) return;
  state.postSignalBars = value;
  try { localStorage.setItem(LIVE_WINDOW_STORAGE_KEY, String(value)); } catch { /* Session preference still works. */ }
  state.analysisError = '';
  refreshSelectedLiveChart({ manual: true });
});
setInterval(pollLiveChart, 10_000);
setInterval(() => {
  if (!document.hidden) loadAutomatic({ quiet: true });
}, 5_000);
setInterval(() => {
  if (!document.hidden && state.tab === 'workspace' && !state.analysisLoading) loadSignals({ quiet: true });
  if (!document.hidden) loadStatus();
}, 30_000);
document.addEventListener('visibilitychange', () => {
  if (document.hidden) pauseLivePolling();
  else {
    loadStatus();
    loadAutomatic({ quiet: true });
    pollLiveChart();
    if (state.tab === 'workspace') loadSignals({ quiet: true });
  }
});
window.addEventListener('pagehide', pauseLivePolling);
