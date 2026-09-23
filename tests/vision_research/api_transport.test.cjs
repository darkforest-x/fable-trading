const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const appPath = path.join(__dirname, '../../yoyo/vision_research/static/app.js');
const appSource = fs.readFileSync(appPath, 'utf8');
const apiStart = appSource.indexOf('async function readResponse');
const apiEnd = appSource.indexOf('\nfunction exchangeKindLabel', apiStart);
const healthStart = appSource.indexOf('function setHealth');
const healthEnd = appSource.indexOf('\nfunction renderEligibility', healthStart);
assert.ok(apiStart >= 0 && apiEnd > apiStart, 'API response helpers are present');
assert.ok(healthStart >= 0 && healthEnd > healthStart, 'API reachability helpers are present');
const apiSource = `${appSource.slice(apiStart, apiEnd)}\n${appSource.slice(healthStart, healthEnd)}\n`
  + '\nglobalThis.__api = { apiJson };';

function createHarness(fetchImpl) {
  const healthText = { textContent: '' };
  const healthClasses = { toggle() {} };
  const state = {
    apiReachable: null,
    apiTransportError: '',
    automatic: { enabled: true },
    automaticStale: false,
    statusError: '',
    statusErrorIsTransport: false,
    tab: 'workspace',
  };
  const context = vm.createContext({
    API_BASE: '/api',
    Headers,
    TypeError,
    asText: (value, fallback = '') => value === null || value === undefined || value === '' ? fallback : String(value),
    fetch: fetchImpl,
    state,
    healthEl: {
      classList: healthClasses,
      querySelector: () => healthText,
    },
    renderAutomaticControls() {},
    renderSettings() {},
  });
  vm.runInContext(apiSource, context, { filename: appPath });
  return { api: context.__api, state, healthText, healthClasses };
}

test('fetch transport failures mark local API unavailable with a localized message', async () => {
  const harness = createHarness(async () => { throw new TypeError('Failed to fetch'); });

  await assert.rejects(harness.api.apiJson('/signals'), (error) => {
    assert.equal(error.code, 'LOCAL_API_UNREACHABLE');
    assert.match(error.message, /本机 API 暂不可达/);
    return true;
  });
  assert.equal(harness.state.apiReachable, false);
  assert.equal(harness.state.automaticStale, true);
  assert.match(harness.healthText.textContent, /正在恢复连接/);
});

test('an HTTP provider or validation error keeps local API marked reachable', async () => {
  const harness = createHarness(async () => new Response(
    JSON.stringify({ detail: '模型返回内容格式无效。' }),
    { status: 502, headers: { 'content-type': 'application/json' } },
  ));

  await assert.rejects(harness.api.apiJson('/analyze'), /模型返回内容格式无效/);
  assert.equal(harness.state.apiReachable, true);
  assert.equal(harness.state.automaticStale, false);
  assert.equal(harness.healthText.textContent, '本地服务已连接');
});

test('malformed JSON after an HTTP response remains a format result, not offline', async () => {
  const harness = createHarness(async () => new Response(
    '{broken json',
    { status: 200, headers: { 'content-type': 'application/json' } },
  ));

  assert.equal(await harness.api.apiJson('/status'), null);
  assert.equal(harness.state.apiReachable, true);
  assert.equal(harness.healthText.textContent, '本地服务已连接');
});

test('transport failure while reading a response body marks API unavailable', async () => {
  const response = {
    ok: true,
    status: 200,
    headers: { get: () => 'application/json' },
    text: async () => { throw new TypeError('Failed to fetch'); },
  };
  const harness = createHarness(async () => response);

  await assert.rejects(harness.api.apiJson('/status'), { code: 'LOCAL_API_UNREACHABLE' });
  assert.equal(harness.state.apiReachable, false);
});

test('aborted requests do not mark the local API unavailable', async () => {
  const abort = Object.assign(new Error('Request aborted'), { name: 'AbortError' });
  const harness = createHarness(async () => { throw abort; });

  await assert.rejects(harness.api.apiJson('/signals/signal-1/chart'), (error) => error === abort);
  assert.equal(harness.state.apiReachable, null);
  assert.equal(harness.state.automaticStale, false);
  assert.equal(harness.healthText.textContent, '');
});
