const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const root = path.join(__dirname, '../..');
const appPath = path.join(root, 'yoyo/vision_research/static/app.js');
const appSource = fs.readFileSync(appPath, 'utf8');
const html = fs.readFileSync(path.join(root, 'yoyo/vision_research/static/index.html'), 'utf8');

function declaration(startMarker, endMarker) {
  const start = appSource.indexOf(startMarker);
  const end = appSource.indexOf(endMarker, start);
  assert.ok(start >= 0 && end > start, `expected ${startMarker} before ${endMarker}`);
  return appSource.slice(start, end);
}

const providerHelpers = declaration('const FALLBACK_PROVIDER_SPECS', '\nfunction setVisible');
const failureHelpers = declaration('function runFailureReason', '\nfunction automaticReviewMarkup');
const dateHelpers = declaration('function formatDate', '\nfunction formatMarketTime');
const automaticTime = declaration('function automaticJobTime', '\nfunction automaticJobSummary');
const runStatus = declaration('function renderRunStatus()', '\nfunction apiExchangeActionMarkup');
const resultRenderer = declaration('function renderResult()', '\nfunction renderHistory()');
const automaticRenderer = declaration('function renderAutomaticControls()', '\nfunction renderSignals()');

function createHarness({ activeRun = null, status = {}, automatic = null } = {}) {
  const elements = new Map();
  const resultEl = { innerHTML: '', querySelectorAll: () => [] };
  const state = {
    activeRun,
    analysisLoading: false,
    analysisError: '',
    selectedImage: activeRun ? { source: 'history', loading: false, frozen: true } : null,
    captureInProgress: false,
    referencesReady: true,
    referencesDirty: false,
    referencesSaving: false,
    reviewLoadingId: null,
    status,
    model: status.model || 'current-model',
    automatic,
    automaticStale: false,
    automaticLoading: false,
    automaticSaving: false,
    automaticActionError: '',
    automaticError: '',
    apiReachable: true,
  };
  const makeElement = (id) => {
    if (elements.has(id)) return elements.get(id);
    const nested = new Map();
    const classes = new Set();
    const item = {
      id,
      hidden: false,
      disabled: false,
      textContent: '',
      className: '',
      attributes: {},
      classList: {
        add(name) { classes.add(name); },
        remove(name) { classes.delete(name); },
        toggle(name, force) {
          const enabled = force === undefined ? !classes.has(name) : Boolean(force);
          if (enabled) classes.add(name); else classes.delete(name);
          return enabled;
        },
        contains(name) { return classes.has(name); },
      },
      setAttribute(name, value) { this.attributes[name] = value; },
      querySelector(selector) {
        if (!nested.has(selector)) nested.set(selector, makeElement(`${id}:${selector}`));
        return nested.get(selector);
      },
      querySelectorAll: () => [],
    };
    elements.set(id, item);
    return item;
  };
  const context = vm.createContext({
    DEFAULT_MODEL: 'glm-5.3-flash',
    Date,
    Intl,
    state,
    resultEl,
    byId: makeElement,
    asText(value, fallback = '') {
      if (value === null || value === undefined || value === '') return fallback;
      if (typeof value === 'string' || typeof value === 'number' || typeof value === 'boolean') return String(value);
      try { return JSON.stringify(value); } catch { return fallback; }
    },
    escapeHtml(value) {
      return String(value ?? '').replace(/[&<>"']/g, (char) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[char]);
    },
    setVisible(element, visible) { if (element) element.hidden = !visible; },
    showInlineError() {},
    updateResultMarkup(markup) { resultEl.innerHTML = markup; },
    automaticJobForRun: () => null,
    currentStateLabel: () => '',
    sideLabel: () => '方向未返回',
    verdictLabel: (value) => ({ no_match: '不符合', match: '符合', uncertain: '不确定' })[value] || '未返回判断',
    safeList(value) { return Array.isArray(value) ? value.map(String) : []; },
    sourceLabel: (value) => value || '来源未提供',
    formatMarketTime: () => '时间未提供',
    apiExchangeActionMarkup: () => '<span>原始记录</span>',
    liveSnapshotInfo: () => null,
    renderOverlay() {},
  });
  const source = [
    providerHelpers,
    failureHelpers,
    dateHelpers,
    automaticTime,
    runStatus,
    resultRenderer,
    automaticRenderer,
    'globalThis.__api = { renderResult, renderAutomaticControls, automaticJobTime };',
  ].join('\n');
  vm.runInContext(source, context, { filename: appPath });
  return { api: context.__api, state, resultEl, elements, makeElement };
}

function qwenStatus() {
  return { provider: 'qwen', model: 'qwen3.8-max', api_key_configured: true };
}

test('failed timeout result shows the originating model and unknown outcome outside details', () => {
  const run = {
    id: 'run-timeout',
    status: 'failed',
    provider: 'zhipu',
    model: 'glm-5.3-flash',
    created_at: '2026-09-24T05:02:00Z',
    latency_ms: 300203,
    error_details: { code: 'read_timeout' },
    error: '等待智谱响应时连续300秒未收到数据，已停止等待；结果未知，没有自动重试。',
    decision: {},
  };
  const harness = createHarness({ activeRun: run, status: qwenStatus() });
  harness.api.renderResult();
  const markup = harness.resultEl.innerHTML;
  const visibleMeta = markup.match(/<p class="result-run-meta">([\s\S]*?)<\/p>/)?.[1] || '';

  assert.match(markup, /请求超时/);
  assert.match(visibleMeta, /智谱 · glm-5\.3-flash/);
  assert.match(visibleMeta, /5 分 00 秒/);
  assert.doesNotMatch(visibleMeta, /qwen3\.8-max/);
  assert.match(markup, /本次没有收到可采纳的模型判断，结果未知；不能据此判为符合或不符合/);
  assert.doesNotMatch(markup, /aria-label="可观察证据"|aria-label="不确定性与风险|class="side-tag"|verdict-pill no-match/);
});

test('failed non-timeout remains an incomplete request, and missing recorded model never borrows current config', () => {
  const run = {
    id: 'run-failed',
    status: 'failed',
    provider: 'zhipu',
    created_at: '2026-09-24T05:02:00Z',
    error: '模型返回内容格式无效。',
    decision: {},
  };
  const harness = createHarness({ activeRun: run, status: qwenStatus() });
  harness.api.renderResult();
  const markup = harness.resultEl.innerHTML;

  assert.match(markup, /识别未完成/);
  assert.match(markup, /模型未记录/);
  assert.doesNotMatch(markup, /qwen3\.8-max/);
  assert.match(markup, /本次没有收到可采纳的模型判断，结果未知/);
});

test('automatic mode with no current API key is pending configuration and links to settings', () => {
  const harness = createHarness({
    status: { ...qwenStatus(), api_key_configured: false },
    automatic: { enabled: true, counts: {} },
  });
  harness.api.renderAutomaticControls();

  assert.equal(harness.makeElement('automatic-toggle-state').textContent, '等待配置 API Key');
  assert.equal(harness.makeElement('automatic-settings-link').hidden, false);
  assert.equal(harness.makeElement('automatic-toggle').classList.contains('is-credential-missing'), true);
  assert.equal(harness.makeElement('automatic-toggle').classList.contains('is-enabled'), false);
  assert.match(html, /id="automatic-settings-link"[^>]*data-tab="settings"/);
});

test('failed and interrupted automatic records say observation or end time, while completed keeps judgment time', () => {
  const harness = createHarness();
  assert.match(harness.api.automaticJobTime({ status: 'failed', observed_at_ms: Date.parse('2026-09-24T05:02:00Z') }), /^观察于 /);
  assert.match(harness.api.automaticJobTime({ status: 'interrupted', completed_at: '2026-09-24T05:07:00Z' }), /^结束于 /);
  assert.match(harness.api.automaticJobTime({ status: 'completed', observed_at_ms: Date.parse('2026-09-24T05:02:00Z') }), /^判断于 /);
});
