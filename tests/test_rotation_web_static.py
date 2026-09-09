"""Verify local dashboard assets and client research-state transitions.

The Node harness exercises the shipped browser script against a minimal DOM and
fake HTTP responses; browser layout and accessibility still require manual QA.
No network, market data, installed JS package or training dependency is needed.
"""

from __future__ import annotations

from html.parser import HTMLParser
from pathlib import Path
import re
import shutil
import subprocess

import pytest


WEB = Path(__file__).resolve().parents[1] / "yoyo" / "rotation" / "web"


class _AssetParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.ids: list[str] = []
        self.assets: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if values.get("id"):
            self.ids.append(values["id"])
        if tag == "script" and values.get("src"):
            self.assets.append(values["src"])
        if tag == "link" and values.get("rel") == "stylesheet":
            self.assets.append(values["href"])


def test_dashboard_assets_are_local_and_dom_references_exist() -> None:
    parser = _AssetParser()
    parser.feed((WEB / "index.html").read_text())
    assert len(parser.ids) == len(set(parser.ids)), "Duplicate DOM ids are ambiguous"
    assert parser.assets == ["/static/theme.js", "/static/styles.css", "/static/app.js"]
    for asset in parser.assets:
        assert (WEB / Path(asset).name).is_file()
    referenced = set(re.findall(r'\$\("([\w-]+)"\)', (WEB / "app.js").read_text()))
    assert referenced <= set(parser.ids)


def test_rs_tooltip_describes_relative_return_ratio_not_percentage_points() -> None:
    html = (WEB / "index.html").read_text()
    assert "(1 + 币种收益) / (1 + BTC 收益) − 1" in html
    assert "以百分比（%）显示" in html
    assert "收益之差" not in html and "百分点" not in html
    assert 'suffix: "pp"' not in (WEB / "app.js").read_text()


CLIENT_HARNESS = r"""
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const html = fs.readFileSync(process.argv[1] + '/index.html', 'utf8');
const source = fs.readFileSync(process.argv[1] + '/app.js', 'utf8');
class Element {
  constructor(tag = 'div') {
    this.tagName = tag; this.children = []; this.events = {}; this.attributes = {};
    this.className = ''; this.hidden = false; this.disabled = false; this.dataset = {};
    this._text = ''; this.value = ''; this.style = {};
    this.classList = {
      toggle: (name, on) => {
        const list = new Set(this.className.split(' ').filter(Boolean));
        if (on === undefined) on = !list.has(name);
        on ? list.add(name) : list.delete(name); this.className = [...list].join(' ');
      },
      remove: (name) => this.classList.toggle(name, false),
    };
  }
  set innerHTML(_) { throw new Error('Untrusted text reached an HTML sink'); }
  set textContent(value) { this._text = String(value); this.children = []; }
  get textContent() { return this._text + this.children.map(child => child.textContent).join(''); }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this.children = []; this._text = ''; this.append(...children); }
  setAttribute(name, value) { this.attributes[name] = String(value); }
  addEventListener(type, fn) { this.events[type] = fn; }
  showModal() { this.open = true; }
  close() { this.open = false; }
  remove() {}
  click() { return this.events.click?.({ target: this }); }
}
const elements = new Map([...html.matchAll(/id="([\w-]+)"/g)].map(match => [match[1], new Element()]));
const tabs = ['all','watch','breakout','pullback','risk'].map(filter => {
  const element = new Element('button'); element.dataset.filter = filter; return element;
});
const closes = ['candidate-dialog','config-dialog'].map(id => {
  const element = new Element('button'); element.dataset.close = id; return element;
});
const document = {
  getElementById: id => { assert(elements.has(id), 'Unknown DOM id: ' + id); return elements.get(id); },
  createElement: tag => new Element(tag), createElementNS: (_, tag) => new Element(tag),
  createDocumentFragment: () => new Element('fragment'), body: new Element('body'),
  querySelectorAll: query => query === '[data-filter]' ? tabs : query === '[data-close]' ? closes : query === 'dialog' ? [elements.get('candidate-dialog'), elements.get('config-dialog')] : [],
};
const config = { mode: 'historical_research', as_of: '2026-04-01T00:00:00Z' };
const policy = { execution_eligible: false, training_eligible: false, model_scored: false, holdout_consumed: false };
const calls = [];
let scanResponse;
const snapshot = {
  schema_version: 'altcoin-rotation-v1', scan_id: 'test-001', as_of: config.as_of,
  generated_at: '2026-09-09T00:00:00Z', mode: config.mode, status: 'partial', policy,
  universe: { requested: 2, observed: 1, eligible: 1, exclusions: [], selection_basis: 'test fixture' },
  regime: { state: 'mixed', btc_return_30d: 0, eth_btc_return_30d: null, breadth_above_sma20: 0.5, coverage_count: 1,
    reasons: ['complete_inputs_with_mixed_trend_and_breadth'] },
  sectors: [{ sector: 'Research', count: 1, strong_count: 1, median_rs_30d: 0.1 }],
  candidates: [{ symbol: 'TEST-USDT', sector: 'Research', status: 'watch', score: 0.72, rank: 1,
    rs_7d: null, rs_30d: 0.1, review_status: 'review', reasons: ['trigger_15m_missing', 'heuristic_score_not_win_probability'],
    daily: { close: 60, return_7d: 0, return_30d: 0.1, sparkline: [{ time: config.as_of, value: 100 }, { time: config.as_of, value: 120 }] },
    setup: { state: 'watch', range_high: 58, range_low: 48, relative_volume: null,
      reasons: ['<img src=x onerror=alert(1)>', 'need_45_completed_candles_for_origin_invariance:have_42'] },
    trigger: {}, risk: { status: 'review' }, derivatives: {
      source: 'Binance USD-M, not spot flow', status: 'partial', funding_rate: -0.0001, interval_hours: null,
      open_interest: 100.5, open_interest_unit: 'base_asset_units_as_reported', base_asset: 'BTC',
      open_interest_quote: 5025000, quote_currency: 'USDT', observed_at: config.as_of,
      sources: [
        { kind: 'premium_index', status: 'ok', url: 'https://fapi.binance.com/fapi/v1/premiumIndex?symbol=BTCUSDT', event_time: '2026-03-31T23:59:00Z', observed_at: config.as_of },
        { kind: 'open_interest', status: 'ok', url: 'https://fapi.binance.com/fapi/v1/openInterest?symbol=BTCUSDT', event_time: '2026-03-31T23:59:30Z', observed_at: config.as_of },
      ],
      reasons: ['funding_info:interval_not_reported_no_eight_hour_assumption', 'single_open_interest_snapshot_cannot_infer_change_direction_or_spot_flow'],
    },
    events: [{ title: '<script>alert(1)</script>', source_url: 'javascript:alert(1)' }],
  }],
  errors: [{ symbol: 'MISSING', stage: 'fetch', message: 'source unavailable' }], sources: [], duration_seconds: 0,
};
const response = (status, body) => ({ ok: status >= 200 && status < 300, status, json: async () => body });
const fetch = async (path, options) => {
  calls.push({ path, options });
  if (path === '/api/status') return response(200, { config, policy, busy: false });
  if (path === '/api/config') return response(200, config);
  if (path === '/api/snapshot') return response(404, { detail: 'no scans' });
  if (path === '/api/journal') return response(200, { events: [], total: 0 });
  if (path === '/api/scan') return scanResponse;
  throw new Error('Unexpected request ' + path);
};
const context = { document, fetch, AbortController, URL, Intl, Date, console, setTimeout, clearTimeout,
  Option: function(text, value) { const element = new Element('option'); element.textContent = text; element.value = value; return element; },
};
const $ = id => elements.get(id);
const walk = root => [root, ...root.children.flatMap(walk)];
vm.runInNewContext(source, context);
(async () => {
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(calls.length, 4, 'Initialization only reads the four APIs');
  assert(calls.every(call => call.options.method === 'GET'), 'No automatic scan');
  assert.equal($('candidate-rows').children.length, 0);
  assert.match($('mode-title').textContent, /历史研究/);
  assert.match($('asof-time').textContent, /2026-04-01/);
  assert.equal($('scan-button').disabled, false);

  scanResponse = response(200, snapshot);
  await $('scan-button').click();
  const scanCall = calls.find(call => call.path === '/api/scan');
  assert.equal(scanCall.options.body, '{}', 'Browser cannot override source/as-of/policy');
  assert.equal(scanCall.options.method, 'POST');
  assert.equal($('candidate-rows').children.length, 1);
  assert.equal($('btc-return').textContent, '0.0%');
  assert.equal($('eth-btc-return').textContent, '—', 'Missing is not zero');
  assert.equal($('partial-notice').hidden, false);
  assert.match($('candidate-rows').textContent, /72/);
  assert.equal($('candidate-rows').children[0].children[4].textContent, '+10.0%');
  assert.match($('candidate-rows').children[0].children[4].title, /\(1 \+ 币种收益\) \/ \(1 \+ BTC 收益\) − 1/);
  assert.match($('sector-strip').textContent, /\+10.0%/);
  assert.match($('sector-strip').children[0].title, /以百分比/);
  assert.equal($('regime-reasons').textContent, '趋势与市场广度仍有分化');
  assert.match($('mode-title').textContent, /历史研究/);
  assert.equal($('export-button').disabled, false);

  tabs.find(tab => tab.dataset.filter === 'breakout').click();
  assert.equal($('candidate-rows').children.length, 0);
  assert.equal($('empty-state').hidden, false);
  tabs.find(tab => tab.dataset.filter === 'all').click();
  $('search').events.input({ target: { value: 'not-present' } });
  assert.equal($('candidate-rows').children.length, 0);
  $('search').events.input({ target: { value: 'TEST' } });
  const candidateButton = walk($('candidate-rows')).find(element => element.className === 'symbol-button');
  candidateButton.click();
  assert.equal($('candidate-dialog').open, true);
  assert.match($('candidate-detail').textContent, /非可执行买点/);
  assert.match($('candidate-detail').textContent, /<img src=x onerror=alert\(1\)>/);
  assert.match($('candidate-detail').textContent, /<script>alert\(1\)<\/script>/);
  assert(walk($('candidate-detail')).every(element => !String(element.href || '').startsWith('javascript:')));
  assert.match($('candidate-detail').textContent, /缺少 15 分钟辅助观察/);
  assert.match($('candidate-detail').textContent, /结构判断需 45 根完整 K 线，当前 42 根/);
  assert.doesNotMatch($('candidate-detail').textContent, /trigger_15m_missing|need_45_completed|heuristic_score_not_win_probability|\d+(?:\.\d+)?pp/);
  assert.match($('candidate-detail').textContent, /辅助背景，不纳入观察评分/);
  assert.match($('candidate-detail').textContent, /未平仓量（基础资产 BTC）/);
  assert.match($('candidate-detail').textContent, /估算持仓名义额（USDT）5,025,000/);
  assert.match($('candidate-detail').textContent, /Binance USD-M 衍生品接口；不是现货资金流/);
  assert.match($('candidate-detail').textContent, /交易所事件：2026-03-31 23:59:00 UTC/);
  assert.match($('candidate-detail').textContent, /交易所事件：2026-03-31 23:59:30 UTC/);
  assert.match($('candidate-detail').textContent, /费率间隔：未上报费率间隔，保持未知/);
  assert.match($('candidate-detail').textContent, /单次持仓量不能说明增减、方向或现货资金流入/);

  const referenceLines = () => walk($('candidate-detail')).filter(element => element.attributes['data-normalized-level']);
  const chartPath = () => walk($('candidate-detail')).find(element => element.tagName === 'path' && element.attributes.fill === 'none').attributes.d;
  assert.equal(referenceLines().length, 2);
  assert(Math.abs(Number(referenceLines()[0].attributes['data-normalized-level']) - 116) < 1e-9,
    'First actual close 50, final close 60, normalized 100→120 and level 58 must map to 116');
  const firstPath = chartPath();
  const ys = [...firstPath.matchAll(/[ML][\d.]+,([\d.]+)/g)].map(match => Number(match[1]));
  assert(Math.max(...ys) - Math.min(...ys) > 80, 'Price path retains readable vertical movement');
  const currentCandidate = snapshot.candidates[0];
  currentCandidate.daily.close = 120000;
  currentCandidate.setup.range_high = 116000;
  currentCandidate.setup.range_low = 96000;
  await $('scan-button').click();
  walk($('candidate-rows')).find(element => element.className === 'symbol-button').click();
  assert.equal(chartPath(), firstPath, '100,000-scale prices must preserve the same normalized geometry');
  assert(Math.abs(Number(referenceLines()[0].attributes['data-normalized-level']) - 116) < 1e-9);
  currentCandidate.daily.close = null;
  await $('scan-button').click();
  walk($('candidate-rows')).find(element => element.className === 'symbol-button').click();
  assert.equal(referenceLines().length, 0, 'Missing actual last close must omit reference price lines');
  assert.match($('candidate-detail').textContent, /缺少真实价格时不绘参考线/);

  const previousRows = $('candidate-rows').textContent;
  scanResponse = response(502, { detail: 'provider unavailable' });
  await $('scan-button').click();
  assert.equal($('candidate-rows').textContent, previousRows, 'Failure retains prior snapshot');
  assert.equal($('request-error').hidden, false);
  assert.match($('request-error').textContent, /保留上次快照/);
  assert.equal($('scan-button').disabled, false, 'Retry remains available');

  scanResponse = response(200, { ...snapshot, mode: 'synthetic_demo', status: 'ok', errors: [] });
  await $('scan-button').click();
  assert.match($('mode-title').textContent, /合成场景演示.*非真实行情/);
  assert.equal($('request-error').hidden, true);
  assert.equal($('partial-notice').hidden, true);
  console.log('client state transitions verified');
})().catch(error => { console.error(error); process.exitCode = 1; });
"""


def test_client_preserves_research_semantics_and_handles_refresh_errors() -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node is optional; browser client behavior requires Node or manual browser QA")
    result = subprocess.run(
        [node, "-e", CLIENT_HARNESS, str(WEB)],
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "client state transitions verified" in result.stdout
