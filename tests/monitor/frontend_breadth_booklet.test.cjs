// The chart payload and the booklet ledger are published separately. Guard the join,
// fee basis and partial fills that a successful fetch or a rendered canvas cannot verify.
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {gunzipSync} = require('node:zlib');
const ui = require('../../yoyo/monitor/static/breadth-booklet.js');
const css = fs.readFileSync(path.resolve(__dirname,'../../yoyo/monitor/static/breadth-booklet.css'),'utf8');
const root = path.resolve(__dirname, '../../yoyo/monitor/static/strategy-booklet');
const data = JSON.parse(fs.readFileSync(path.join(root,'breadth-ema20-data.json')));
const chartRoot = path.join(root,'breadth-charts');
const readJSON = name => JSON.parse(fs.readFileSync(path.join(chartRoot,name)));
const readBlock = item => JSON.parse(gunzipSync(fs.readFileSync(path.join(chartRoot,item.path))));
const close = (actual, expected) => assert.ok(Math.abs(actual-expected)<1e-6, actual+' != '+expected);
test('both cost scopes have a reconciled endpoint and distinct event counts', () => {
  for (const [scope,field,rows] of [
    ['all_history_fee_only','net_pct',data.events],
    ['2026_actual_funded','funded_pct',data.events.filter(e=>e.epoch===3)],
  ]) {
    const summary=data.summaries[scope];
    assert.equal(summary.event_count,rows.length);
    assert.equal(summary.btc_event_count,rows.filter(r=>r.asset==='BTC').length);
    assert.ok(rows.every(e=>Number.isFinite(e[field])));
    close((data.equity_curves[scope+'_settlement'].points.at(-1).combined_equity-1)*100,summary.growth_pct);
  }
  const funded=data.summaries['2026_actual_funded'];
  assert.equal(funded.event_count,48);assert.equal(funded.wins,31);
  assert.equal(data.summaries.all_history_fee_only.event_count,398);
});

test('the shared drawer receives content fragments and complete selectable replay data', () => {
  assert.equal(ui.validateData(data),data);
  assert.equal(ui.mountReplay,undefined);
  const rules=ui.rulesHTML(data);
  assert.match(rules,/全市场下轨怎么数/);
  assert.match(rules,/普通 BTC/);
  assert.match(rules,/急跌 BTC/);
  assert.match(rules,/山寨篮子/);
  assert.match(rules,/仓位与成本/);
  const funded=ui.statsHTML(data,{scope:'2026_actual_funded'});
  assert.match(funded,/data-summary-scope="2026_actual_funded"/);
  assert.match(funded,/2026 · 含资金费/);
  assert.match(funded,/2026 匹配随机入场对照/);
  assert.match(funded,/加 EMA20 条件前后/);
  const history=ui.statsHTML(data,{scope:'all_history_fee_only'});
  assert.match(history,/data-summary-scope="all_history_fee_only"/);
  assert.match(history,/2020–2026 · 仅手续费/);
  assert.doesNotMatch(history,/data-field="scope"/);
  for(const html of [funded,history])assert.doesNotMatch(html,/bb-tiles|data-equity|结算净值 · 初始/);
  assert.match(funded,/data-period="2025"/);
  assert.match(history,/data-period="2025-01"/);
  for(const html of [rules,funded,history])assert.doesNotMatch(html,/<dialog|<nav|bb-tabs|bb-top|bb-close/);
  assert.ok(rules.startsWith('<div class="bb-fragment">'));
  assert.ok(funded.startsWith('<div class="bb-fragment"'));
  assert.match(css,/\.bb-fragment \{--bb-paper:/);
  assert.doesNotMatch(css,/\.bb-embedded|\.bb-sheet|dialog\.bb-sheet|\.bb-tabs/);
  assert.throws(()=>ui.statsHTML(data,{scope:'unsupported'}),/区间不受支持/);
});

test('each entry links its own candles, and the September replay labels the two fill prices', () => {
  const index=readJSON('manifest.json');
  const items=new Map(index.events.map(e=>[e.event_id,e]));
  assert.equal(items.size,data.events.length);
  for(const e of data.events){
    const item=items.get(e.event_id);
    assert.ok(item && fs.existsSync(path.join(chartRoot,item.path)),e.event_id);
  }
  const entry=data.events.find(e=>e.event_id==='BTC|2026-09-15T19:20:00Z');
  assert.equal(entry.signal_snapshot.initial_touch_count,68);assert.equal(entry.signal_snapshot.new_coins,0);
  const btc=readBlock(items.get(entry.event_id));
  assert.deepEqual(btc.chart.markers.map(m=>m.price),[76127.8,86325,86280]);
  assert.deepEqual(btc.chart.markers.map(m=>m.time_ms),[entry.entry_time,...entry.exit_legs_detail.map(x=>x.time)].map(Date.parse));
  const alt=data.events.find(e=>e.asset==='ALT' && e.epoch===3);
  const basket=readBlock(items.get(alt.event_id));
  assert.equal(basket.chart,null);
  assert.deepEqual(basket.members.map(m=>m.symbol).sort(),alt.members.map(m=>m.symbol).sort());
  for(const m of basket.members)assert.equal(m.chart.symbol,m.symbol);
});
