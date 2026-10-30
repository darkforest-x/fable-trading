'use strict';
const test=require('node:test'),assert=require('node:assert/strict');
const ui=require('../../yoyo/monitor/static/monday-reclaim-paper.js');
const midnight=Date.parse('2026-10-27T00:00:00+08:00');
const event=(id,stamp)=>({id,symbol:'ABC-USDT-SWAP',candle_ms:stamp,score:.7});
const holding=(signal_id,extra={})=>({id:signal_id,signal_id,symbol:'ABC-USDT-SWAP',taken:1,notional:100,remaining_notional:30,entry:100,fill_ms:midnight-100,stage:'breakeven',...extra});
test('today uses Shanghai boundaries, exact funded holdings across accounts and final exit time',()=>{
 const d={now_ms:midnight+1,signals:[event(1,midnight),event(2,midnight-1),event(3,midnight-2),event(4,midnight-3),event(5,midnight+86400000)],tracks:{60:{open:[],closed:[{...holding(8),fill_ms:midnight-86400000,exit_ms:midnight},{...holding(9),fill_ms:midnight-2,exit_ms:midnight-1}]},spike:{open:[holding(2),holding(4,{taken:0,notional:0})],closed:[]}}};
 assert.equal(ui.dayStart(midnight-1),midnight-86400000);
 assert.deepEqual(ui.rows(d,ui.route({tab:'events'})).map(r=>r.id),[1,2]);
 assert.deepEqual(ui.rows(d,ui.route({track:'60',tab:'closed'})).map(r=>r.id),[8]);
 for(const extra of [{remaining_notional:0},{stage:'pending_fill'},{fill_ms:0},{entry:0},{taken:0}]){
  d.tracks.spike.open=[holding(2,extra)];assert.deepEqual(ui.rows(d,ui.route({tab:'events'})).map(r=>r.id),[1]);
 }
 assert.equal(ui.route({tab:'open',history:'1'}).history,'');
 assert.equal(ui.route({tab:'events',history:'1'}).history,'1');
 assert.match(ui.rowHTML(event(2,midnight-1),ui.route({tab:'events'}),midnight,{carried:true}),/rp-held-tag.*持仓/);
});
const flush=()=>new Promise(r=>setImmediate(r));
test('today projection is not truncated to recent chart records',()=>{
 const d={now_ms:midnight+10,signals:[],record_signals:[event(1,midnight)],tracks:{60:{open:[],closed:[],record_closed:[{...holding(2),exit_ms:midnight}]}}};
 assert.equal(ui.rows(d,ui.route({tab:'events'})).length,1);
 assert.equal(ui.rows(d,ui.route({tab:'closed',track:'60'})).length,1);
});
const page=(ids,total=ids.length)=>({rows:ids.map(id=>event(id,midnight-1)),total,has_more:total>ids.length});
test('history paging ignores old filter responses and preserves rows on retryable failures',async()=>{
 const pending=[],requests=[];let cancels=0;
 const h=ui.createHistory({read:args=>{requests.push(args);return new Promise((resolve,reject)=>pending.push({resolve,reject}));},cancel:()=>cancels++});
 h.select({track:'60',tab:'events',q:''});await flush();
 h.select({track:'spike',tab:'closed',q:''});await flush();
 pending[0].resolve(page([99]));await flush();assert.equal(h.getState().loaded,false);
 pending[1].resolve(page([1,2],3));await flush();assert.deepEqual(h.getState().rows.map(r=>r.id),[1,2]);
 const loading=h.load(true);assert.equal(h.load(true),loading);await flush();assert.equal(requests[2].offset,2);
 pending[2].reject(TypeError('offline'));await loading;assert.match(h.getState().error,/网络/);assert.equal(h.getState().rows.length,2);
 const retry=h.load(true);await flush();pending[3].resolve({rows:[event(3,midnight-1)],total:3,has_more:false});await retry;
 assert.deepEqual(h.getState().rows.map(r=>r.id),[1,2,3]);assert.equal(h.getState().has_more,false);
 h.select(null);assert.equal(h.getState().rows.length,0);assert.equal(cancels,3);
});
test('closing before the history request starts creates no read',async()=>{
 let reads=0;const h=ui.createHistory({read:async()=>{reads++;return page([]);}});
 h.select({tab:'events'});h.select(null);await flush();assert.equal(reads,0);
});
test('time sorting uses each tab clock, ignores scores and is stable without mutating inputs',()=>{
 const source=[
  {...event(1,midnight+3),score:.9,fill_ms:20,exit_ms:midnight+1,observed_ms:midnight+99},
  {...event(2,midnight+1),score:null,fill_ms:40,exit_ms:midnight+3},
  {...event(3,midnight+2),score:.1,fill_ms:10,exit_ms:midnight+2},
  {...event(4,midnight+2),score:.8,fill_ms:30,exit_ms:midnight+2}
 ].map(r=>({...r,taken:1,notional:100}));
 const d={now_ms:midnight+100,record_signals:source,tracks:{60:{open:source,record_closed:source}}};
 const latest={events:[1,4,3,2],closed:[2,4,3,1],open:[2,4,1,3]};
 for(const tab of ['events','closed','open']){
  const sorted=sort=>ui.rows(d,ui.route({tab,track:'60',sort})).map(r=>r.id);
  assert.deepEqual(sorted('time_desc'),latest[tab]);
  assert.deepEqual(sorted('time_asc'),latest[tab].slice().reverse());
  assert.deepEqual(sorted('score'),[1,4,3,2]);
 }
 assert.deepEqual(source.map(r=>r.id),[1,2,3,4]);
 for(const sort of ['__proto__','bad',undefined])assert.equal(ui.route({sort}).sort,'score');
});
test('time sorting retains cross-day held touches and puts unknown open times last',()=>{
 const d={now_ms:midnight+100,record_signals:[event(1,midnight+1),event(2,midnight-1)],
  tracks:{60:{open:[holding(2),holding(3,{fill_ms:null})],closed:[]}}};
 assert.deepEqual(ui.rows(d,ui.route({tab:'events',sort:'time_asc'})).map(r=>r.id),[2,1]);
 for(const sort of ['time_asc','time_desc'])
  assert.deepEqual(ui.rows(d,ui.route({track:'60',sort})).map(r=>r.id),[2,3]);
});
test('history sort changes clear old pages and reject their late responses',async()=>{
 const requests=[],pending=[];
 const h=ui.createHistory({read:args=>{requests.push(args);return new Promise(resolve=>pending.push(resolve));}});
 const context={track:'spike',tab:'events',q:''};
 h.select({...context,sort:'score'});await flush();pending[0](page([1,2],4));await flush();
 h.load(true);await flush();assert.equal(requests[1].offset,2);
 h.select({...context,sort:'time_desc'});await flush();
 assert.deepEqual(h.getState().rows,[]);assert.equal(requests[2].sort,'time_desc');assert.equal(requests[2].offset,0);
 pending[1](page([3,4]));await flush();assert.deepEqual(h.getState().rows,[]);
 pending[2](page([4,3],4));await flush();assert.deepEqual(h.getState().rows.map(r=>r.id),[4,3]);
 h.load(true);await flush();assert.equal(requests[3].sort,'time_desc');assert.equal(requests[3].offset,2);
 pending[3]({...page([2,1]),has_more:false});await flush();assert.deepEqual(h.getState().rows.map(r=>r.id),[4,3,2,1]);
});
