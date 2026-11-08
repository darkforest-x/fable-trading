// The chart payload and the booklet ledger are published separately. Guard the join,
// fee basis and partial fills that a successful fetch or a rendered canvas cannot verify.
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {gunzipSync} = require('node:zlib');
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
