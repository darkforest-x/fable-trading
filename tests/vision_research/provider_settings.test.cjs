const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const root = path.join(__dirname, '../..');
const appPath = path.join(root, 'yoyo/vision_research/static/app.js');
const htmlPath = path.join(root, 'yoyo/vision_research/static/index.html');
const replayPath = path.join(root, 'yoyo/vision_research/static/replay.js');
const appSource = fs.readFileSync(appPath, 'utf8');
const helperStart = appSource.indexOf('const FALLBACK_PROVIDER_SPECS');
const helperEnd = appSource.indexOf('\nfunction setVisible', helperStart);
assert.notEqual(helperStart, -1);
assert.notEqual(helperEnd, -1);

function loadProviderHelpers() {
  const context = vm.createContext({ URL });
  const helpers = appSource.slice(helperStart, helperEnd);
  vm.runInContext(`
    const DEFAULT_MODEL = 'glm-5.3-flash';
    const state = { status: null };
    const document = undefined;
    function byId() { return null; }
    function asText(value, fallback = '') {
      if (value === null || value === undefined || value === '') return fallback;
      if (typeof value === 'string' || typeof value === 'number' || typeof value === 'boolean') return String(value);
      try { return JSON.stringify(value); } catch { return fallback; }
    }
    ${helpers}
    globalThis.api = { FALLBACK_PROVIDER_SPECS, providerSpecs, providerSpec, configFromStatus,
      configMatchesStatus, providerDisplayName, providerRegions, providerModels, providerEndpoint };
  `, context);
  return context.api;
}

test('provider selectors expose the documented Qwen model IDs and regional endpoints', () => {
  const api = loadProviderHelpers();
  const qwen = api.providerSpec('qwen');
  assert.equal(qwen.default_model, 'qwen3-vl-plus');
  assert.deepEqual([...api.providerModels(qwen)], ['qwen3-vl-plus', 'qwen-vl-max']);
  assert.deepEqual(JSON.parse(JSON.stringify(api.providerRegions(qwen))), [['beijing', '北京'], ['singapore', '新加坡']]);
  assert.equal(api.providerEndpoint(qwen, 'beijing'), 'https://dashscope.aliyuncs.com/compatible-mode/v1');
  assert.equal(api.providerEndpoint(qwen, 'singapore'), 'https://dashscope-intl.aliyuncs.com/compatible-mode/v1');
  assert.equal(api.providerModels(qwen).includes('qwen3-vl-max'), false);
});

test('provider specs from status drive model choices while saved provider and region stay distinct', () => {
  const api = loadProviderHelpers();
  const status = {
    provider: 'qwen', region: 'singapore', model: 'qwen-vl-max',
    providers: [{
      id: 'qwen', name: 'Qwen', default_model: 'qwen3-vl-plus',
      models: ['qwen3-vl-plus', 'qwen-vl-max'], default_region: 'beijing',
      regions: { beijing: '北京', singapore: '新加坡' },
      endpoints: { singapore: 'https://dashscope-intl.aliyuncs.com/compatible-mode/v1' },
    }],
  };
  const selected = api.configFromStatus(status);
  assert.deepEqual({ ...selected }, { provider: 'qwen', region: 'singapore', model: 'qwen-vl-max' });
  assert.equal(api.configMatchesStatus(selected, status), true);
  assert.equal(api.configMatchesStatus({ ...selected, region: 'beijing' }, status), false);
  assert.equal(api.providerDisplayName('zhipu'), '智谱');
  assert.equal(api.providerDisplayName('qwen'), '通义千问（Qwen）');
  assert.equal(api.providerDisplayName('gemini'), 'Gemini');
});

test('settings controls and replay disclosure use active provider metadata', () => {
  const html = fs.readFileSync(htmlPath, 'utf8');
  const replay = fs.readFileSync(replayPath, 'utf8');
  for (const id of ['provider-select', 'qwen-region-field', 'region-select', 'model-input', 'config-selection-status', 'api-key-help', 'active-provider-disclosure']) {
    assert.match(html, new RegExp(`\\bid="${id}"`));
  }
  assert.match(html, /最多 49 张参考图；单张小于 5 MB，待判图与参考图合计不超过 12 MB/);
  assert.match(appSource, /const payload = \{ provider: draft\.provider, region: draft\.region, model \}/);
  assert.match(appSource, /state\.configDraft = configFromStatus\(status\)/);
  assert.match(appSource, /onConfigProviderChange[\s\S]*?byId\('api-key-input'\)\.value = ''/);
  assert.match(appSource, /onConfigRegionChange[\s\S]*?byId\('api-key-input'\)\.value = ''/);
  const analyzeStart = appSource.indexOf('async function analyze()');
  const analyzeEnd = appSource.indexOf('\nfunction ', analyzeStart);
  const analyzeSource = appSource.slice(analyzeStart, analyzeEnd);
  assert.match(analyzeSource, /model: state\.model \|\| null/);
  assert.doesNotMatch(analyzeSource, /configDraft/);
  assert.match(appSource, /!configMatchesStatus\(state\.configDraft, state\.status\)/);
  assert.match(replay, /dataset\?\.activeProviderName/);
});
