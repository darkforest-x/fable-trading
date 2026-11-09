'use strict';
const test=require('node:test'),assert=require('node:assert/strict');
const ui=require('../../yoyo/monitor/static/strategy-backtest.js');
const chart=require('../../yoyo/monitor/static/reclaim-chart.js');
const flush=()=>new Promise(r=>setImmediate(r));
const row=(id,symbol='AAAUSDT')=>({id,symbol,side:1,entry_ms:1700000000000+id*300000,exit_ms:1700000000000+id*300000+900000,pnl:id-3,return_pct:(id-3)/100});
const page=(q,ids,total=ids.length)=>({schema:1,...q,meta:{track:q.track,trades:total},rows:ids.map(i=>row(i)),ranking:[{symbol:'AAAUSDT',pnl:3,trades:total}],focus:row(2),total,has_more:q.offset+ids.length<total});
test('midpoint replay labels the actual branch and the entry-close instant',()=>{
 const r=row(1);r.entry_start_ms=r.entry_ms;
 r.entry_rules={kind:'touch_close',status:'saved',score:.6,minimum:.5,beyond_pct:1.125,target_rule:'shallow_ny_monday_midpoint'};
 r.target='range_0.5';assert.doesNotMatch(ui.entryLabel(r),/–/);
 assert.match(ui.rowHTML(r,1),/穿幅 1.125% · 目标 周一中点/);
 assert.match(ui.summaryHTML(r),/穿幅 1.125% · 目标 周一中点/);
 r.target='near_monday_edge';r.entry_rules.target_rule='near_monday_edge';r.entry_rules.beyond_pct=1.5;
 assert.match(ui.summaryHTML(r),/穿幅 1.5% · 目标 周一近侧/);
});
test('trade summary preserves source fill prices and native intervals, including a midnight boundary',()=>{
 const start=Date.parse('2026-09-17T17:25:00Z');
 const r={...row(166,'SIRENUSDT'),side:-1,entry_ms:start+300000,exit_ms:start+300000,pnl:62.84005123804659,return_pct:2.529151912694961,
  fills:{entry_start_ms:start,entry_end_ms:start+300000,entry_price:.02670598,exit_start_ms:start,exit_end_ms:start+300000,exit_price:.02602}};
 const html=ui.summaryHTML(r);
 assert.match(html,/SIREN/);assert.match(html,/做空/);assert.match(html,/0.02670598/);assert.match(html,/0.02602/);assert.match(html,/\+62.84/);
 assert.equal((html.match(/2026\/09\/18 01:25–01:30/g)||[]).length,2);
 const midnight=Date.parse('2026-09-17T15:55:00Z');
 assert.equal(ui.fillLabel({...r,fills:{entry_start_ms:midnight,entry_end_ms:midnight+300000}},'entry'),'2026/09/17 23:55–2026/09/18 00:00');
 assert.doesNotMatch(ui.summaryHTML({...r,fills:undefined}),/undefined|NaN|0.02670598|0.02602/);
 assert.match(ui.summaryHTML({...r,fills:undefined}),/记账/);
});
test('each saved row shows its own score and qualified gates without inventing missing values',()=>{
 const r={...row(1),entry_start_ms:row(1).entry_ms-300000,entry_rules:{status:'saved',score:.6592223040584619,minimum:.6,order:3,minimum_order:3,kind:'first_touch'}};
 const html=ui.rowHTML(r,1);assert.match(html,/1.618首触/);assert.match(html,/0.6592≥0.6/);assert.match(html,/排列 3≥3/);
 assert.ok(html.indexOf('bt-entry')<html.indexOf('bt-times'));assert.match(ui.entryLabel(r),/–/);
 assert.match(ui.rulesHTML({...r,entry_rules:{...r.entry_rules,kind:'add5',minimum_order:null}}),/逆向5%加仓.*主单评分/);
 assert.doesNotMatch(ui.rulesHTML({...r,entry_rules:{status:'unavailable'}}),/首触|≥|0.6592/);
 assert.match(ui.rulesHTML({...r,entry_rules:{...r.entry_rules,score:.2262167,minimum:.22621664169752762}}),/0.2262 · 前40%/);
});
test('rapid strategy changes discard stale data and focus only the correct Top 1',async()=>{
 const requests=[],chosen=[];const m=ui.createModel({read:q=>new Promise((resolve,reject)=>requests.push({q,resolve,reject})),focus:(r,t)=>chosen.push([r.id,t])});
 m.select('allin3');await flush();m.select('spike');await flush();
 requests[0].resolve(page(requests[0].q,[1]));await flush();assert.equal(m.getState().data,null);
 requests[1].resolve(page(requests[1].q,[2]));await flush();assert.deepEqual(chosen,[[2,'spike']]);assert.equal(m.getState().rows[0].id,2);
});
test('a calendar-selected trade keeps its focus through list reads and resume',async()=>{
 const requests=[],chosen=[];const m=ui.createModel({read:q=>new Promise(resolve=>requests.push({q,resolve})),focus:(r,t)=>chosen.push([r.id,t])});
 m.select('allin3');await flush();m.select('spike',row(351));await flush();assert.deepEqual(chosen,[[351,'spike']]);
 requests[0].resolve(page(requests[0].q,[1]));requests[1].resolve(page(requests[1].q,[2]));await flush();assert.deepEqual(chosen,[[351,'spike']]);
 m.pause();m.load();await flush();requests[2].resolve(page(requests[2].q,[2]));await flush();assert.deepEqual(chosen,[[351,'spike']]);
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

test('narrow charts show fewer context bars while retaining both endpoints of long trades',()=>{
 const bars=Array.from({length:100},(_,i)=>({time:1700000040+i*60}));
 const short={entry_ms:bars[20].time*1000,exit_ms:bars[24].time*1000};
 const desktop=chart.replayRange(short,bars,60000),mobile=chart.replayRange(short,bars,60000,25);
 assert.ok(mobile.to-mobile.from<desktop.to-desktop.from);
 assert.ok(mobile.from<mobile.entry&&mobile.to>mobile.exit);
 const long={...short,exit_ms:bars[70].time*1000};
 const focus=chart.replayRange(long,bars,60000,25);
 assert.ok(focus.from<focus.entry&&focus.to>focus.exit);
 for(const period of ['1m','2m','3m'])assert.equal(ui.periodFor(long,period),period);
});
