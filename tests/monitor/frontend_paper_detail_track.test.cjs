'use strict';
// Owner 2026-11-06 performance pass: the paper snapshot carries closed/today
// records only for the requested account.
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs');
const {detailPending}=require('../../yoyo/monitor/static/monday-reclaim-paper.js');
const source=fs.readFileSync('yoyo/monitor/static/monday-reclaim-paper.js','utf8');

test('a snapshot for another account is pending, a full or matching snapshot is not',()=>{
  assert.equal(detailPending({detail_track:'spike'},'60'),true);
  assert.equal(detailPending({detail_track:'spike'},'spike'),false);
  assert.equal(detailPending({tracks:{}},'60'),false);
  assert.equal(detailPending(null,'60'),false);
});

test('the page requests its current account and shows reading instead of an empty closed list',()=>{
  assert.match(source,/\/api\/monday-reclaim-paper\?list_period=today&track='\+encodeURIComponent\(state\.track\)/);
  assert.match(source,/if\(pendingDetail&&!busy&&!error\)feed\.refresh\(\);/);
  assert.match(source,/state\.tab==='closed'&&pendingDetail\?'正在读取记录'/);
});
