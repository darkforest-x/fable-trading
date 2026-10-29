'use strict';
const test=require('node:test'),assert=require('node:assert/strict');
const ui=require('../../yoyo/monitor/static/strategy-backtest.js');
const chart=require('../../yoyo/monitor/static/reclaim-chart.js');
const flush=()=>new Promise(r=>setImmediate(r));
const row=(id,symbol='AAAUSDT')=>({id,symbol,side:1,entry_ms:1700000000000+id*300000,exit_ms:1700000000000+id*300000+900000,pnl:id-3,return_pct:(id-3)/100});
const page=(q,ids,total=ids.length)=>({schema:1,...q,meta:{track:q.track,trades:total},rows:ids.map(i=>row(i)),ranking:[{symbol:'AAAUSDT',pnl:3,trades:total}],focus:row(2),total,has_more:q.offset+ids.length<total});
test('rapid strategy changes discard stale data and focus only the correct Top 1',async()=>{
 const requests=[],chosen=[];const m=ui.createModel({read:q=>new Promise((resolve,reject)=>requests.push({q,resolve,reject})),focus:(r,t)=>chosen.push([r.id,t])});
 m.select('allin3');await flush();m.select('spike');await flush();
 requests[0].resolve(page(requests[0].q,[1]));await flush();assert.equal(m.getState().data,null);
 requests[1].resolve(page(requests[1].q,[2]));await flush();assert.deepEqual(chosen,[[2,'spike']]);assert.equal(m.getState().rows[0].id,2);
});
test('whole-ledger sorting/filtering restarts paging and errors preserve loaded rows',async()=>{
 const req=[],chosen=[];const m=ui.createModel({read:q=>new Promise((resolve,reject)=>req.push({q,resolve,reject})),focus:r=>chosen.push(r.id)});
 m.select('allin3');await flush();req[0].resolve(page(req[0].q,[4,3],4));await flush();
 m.load(true);await flush();assert.equal(req[1].q.offset,2);req[1].reject(Error('offline'));await flush();assert.equal(m.getState().rows.length,2);
 m.load(true);await flush();req[2].resolve(page(req[2].q,[2,1],4));await flush();assert.equal(m.getState().rows.length,4);
 m.sort('pnl');await flush();assert.equal(req[3].q.sort,'pnl');assert.equal(req[3].q.direction,'desc');assert.equal(req[3].q.offset,0);
 req[3].resolve(page(req[3].q,[4,3],4));await flush();m.sort('pnl');await flush();assert.equal(req[4].q.direction,'asc');
 m.filter('BBBUSDT');await flush();req[4].resolve(page(req[4].q,[8]));await flush();assert.equal(m.getState().data,null);
 req[5].resolve({...page(req[5].q,[],0),focus:null});await flush();assert.equal(m.getState().total,0);assert.deepEqual(chosen,[2]);
});
test('closed or paused panels cannot auto-focus a late result and resume can retry',async()=>{
 let count=0,resolve;const m=ui.createModel({read:q=>new Promise(r=>resolve=()=>r(page(q,[1]))),focus:()=>count++});
 m.select('60');await flush();m.pause();resolve();await flush();assert.equal(count,0);
 const pending=m.load();await flush();resolve();await pending;assert.equal(count,1);
});
test('saved history validates identity and full timestamps without inventing prices',()=>{
 const q={track:'allin3',sort:'time',direction:'desc',symbol:'',offset:0},p=page(q,[1]);
 assert.equal(ui.validate(p,q,0),p);assert.throws(()=>ui.validate({...p,track:'spike'},q,0));assert.throws(()=>ui.validate({...p,rows:[row(1),row(1)]},q,0));
 const html=ui.rowHTML(row(1),1);assert.match(html,/2023/);assert.match(html,/aria-pressed="true"/);assert.doesNotMatch(html,/开仓价|undefined|NaN/);
 assert.equal(ui.periodFor({...row(1),exit_ms:row(1).entry_ms+365*86400000},'5m'),'4H');
 const bars=Array.from({length:60},(_,i)=>({time:1700000000+i*300}));
 const range=chart.replayRange({...row(1),entry_ms:bars[8].time*1000,exit_ms:bars[45].time*1000},bars,300000);
 assert.equal(range.entry,8);assert.equal(range.exit,45);assert.ok(range.from<8&&range.to>45);
});

test('desktop overlay reserves visible chart space for both trade endpoints',()=>{
 const bars=Array.from({length:100},(_,i)=>({time:1700000000+i*300}));
 const r={entry_ms:bars[20].time*1000,exit_ms:bars[70].time*1000};
 const full=chart.replayRange(r,bars,300000),partial=chart.replayRange(r,bars,300000,.6);
 assert.equal(partial.from,full.from);assert.ok(partial.to>full.to);
 assert.ok((partial.exit-partial.from)/(partial.to-partial.from)<.6);
});

test('left ledger keeps both endpoints in the uncovered right area',()=>{
 const bars=Array.from({length:100},(_,i)=>({time:1700000040+i*60}));
 const r={entry_ms:bars[20].time*1000,exit_ms:bars[70].time*1000};
 const focus=chart.replayRange(r,bars,60000,.6,'left');
 assert.ok((focus.entry-focus.from)/(focus.to-focus.from)>.4);
 assert.ok((focus.exit-focus.from)/(focus.to-focus.from)<1);
 for(const period of ['1m','2m','3m'])assert.equal(ui.periodFor(r,period),period);
});
