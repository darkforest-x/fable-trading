const test=require('node:test'),assert=require('node:assert/strict');
const {read,legacyTarget}=require('../../yoyo/monitor/static/breadth-booklet-link.js');
const {accountLayout,pickerHTML}=require('../../yoyo/monitor/static/reclaim-strategy-book.js');
test('saved booklet links open the matching section and keep the exact trade, without changing the simulation account',()=>{
  const event='BTC|2026-09-15T19:20:00Z';
  const old=new URL(legacyTarget('?tab=history&event='+encodeURIComponent(event)),'https://spikeok.com');
  assert.deepEqual(read(old.search),{chapter:'replay',eventId:event,scope:'2026_actual_funded'});
  assert.equal(old.hash,'#signals?pane=reclaim');
  assert.equal(read('?book=spike'),null);
  assert.equal(read(new URL(legacyTarget('?tab=rules'),'https://spikeok.com').search).chapter,'rules');
  assert.equal(read(new URL(legacyTarget('?tab=stats'),'https://spikeok.com').search).chapter,'stats');
});
test('new research entry joins existing saved accounts with standard booklet/replay buttons and no simulated P&L',()=>{
  const id='breadth-ema20',tracks=['live-wick6x3','spike',id],layout=accountLayout({main:['spike'],more:['live-wick6x3']},tracks);
  assert.deepEqual(layout.main,['spike',id]);assert.deepEqual(layout.more,['live-wick6x3']);
  const row=pickerHTML([id]);assert.match(row,/data-strategy-book="breadth-ema20"/);
  assert.match(row,/data-strategy-backtest="breadth-ema20"/);assert.doesNotMatch(row,/data-account-pnl/);
});
