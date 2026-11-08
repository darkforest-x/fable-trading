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

test('booklet examples open the actual fills and use the displayed cost scope', () => {
  const year=ui.rulesHTML(data,{scope:'2026_actual_funded'});
  const all=ui.rulesHTML(data,{scope:'all_history_fee_only'});
  for(const html of [year,all]){
    assert.match(html,/data-breadth-case="BTC\|2026-09-15T19:20:00Z"/);
    assert.match(html,/data-breadth-case="ALT\|2026-10-23T22:55:00Z" data-member="1000LUNCUSDT"/);
    assert.match(html,/86,325/);
  }
  assert.match(year,/10,005\.75/);
  assert.match(year,/篮子 \+2\.44%/);
  assert.match(all,/篮子 \+2\.45%/);
  assert.match(year,/单币 \+2\.80%/);
  assert.match(all,/单币 \+2\.81%/);
  assert.match(ui.originHTML(),/13 根/);
});

test('branch contributions reconcile within each rebased 1,000 USDT account', () => {
  const year=ui.statsHTML(data,{scope:'2026_actual_funded'});
  for(const pnl of ['+207.53','+107.30','+105.44'])assert.ok(year.includes(pnl));
  const all=ui.statsHTML(data,{scope:'all_history_fee_only'});
  for(const pnl of ['+2,193.33','+914.97','+1,557.67'])assert.ok(all.includes(pnl));
  assert.ok(all.includes('-22.27%'));
});

test('cadence comparison keeps settlement risk separate from five-minute marked risk', () => {
  const year=ui.statsHTML(data,{scope:'2026_actual_funded'});
  for(const label of ['+43.01%','+41.72%','3.54%','7.89%','7.93%','+40.75%','28 / 28','-0.70%','42 个山寨信号','含资金费盯市回撤'])assert.ok(year.includes(label),label);
  const all=ui.statsHTML(data,{scope:'all_history_fee_only'});
  for(const label of ['+377.67%','+377.43%','64 / 3','34 / 4','538 个山寨信号'])assert.ok(all.includes(label),label);
  assert.ok(!all.includes('含资金费盯市回撤'));
});

test('changing BTC admission and splitting capital preserve scope and actual case admission', () => {
  const rows=data.summaries.frequency_review.btc_variants;
  const check=(scope,names)=>assert.deepEqual(rows.filter(r=>r.scope===scope).map(r=>[
    r.variant,r.distinct_entries,Number(r.growth_pct.toFixed(2)),Number(r.marked_dd_pct.toFixed(2)),r.sep16_admitted,
  ]),names);
  check('2026',[
    ['current',48,42.03,6.59,true],['early_ema',54,17.33,13.10,false],['reserved_case',57,16.86,7.92,true],
  ]);
  check('all_history',[
    ['current',398,466.60,26.75,true],['early_ema',431,234.73,25.57,false],['reserved_case',469,232.14,19.13,true],
  ]);
  const html=ui.statsHTML(data,{scope:'2026_actual_funded'});
  for(const text of ['+30.86 USDT','+95.20 USDT','51 个北京时间交易日','32 个自然周','尚未回测'])assert.ok(html.includes(text),text);
});
