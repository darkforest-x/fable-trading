'use strict';
// Financial summary boundaries: account identity, unknowns, stale reads and lifecycle.
const test=require('node:test'),assert=require('node:assert/strict');
const {ACCOUNTS,Feed,project,valid}=require('../../yoyo/monitor/static/market-accounts.js');
const now=200000;
const manual=()=>({account_scope:'yolo_manual',account:{status:'ready',updated_ms:now,data:{total_equity_usdt:'120.12',balance:{eq:'90',availEq:'100',upl:'0'},position_count:0}},executions:{status:'ready',statistics:{filled:10,closed:8,unattributed:4,realized_net_usdt:'-13.34'}}});
const signals=()=>({read_only:true,account:{status:'ready',updated_ms:now,data:{balance:{eq:'75',availEq:'60',upl:'-2'},position_count:2}},ledger:{worker:{status:'stopped'},items:[{realized_net_usdt:'9999'}]}});
const response=(value,status=200)=>({ok:status===200,status,json:async()=>value});
function scheduler(){let serial=0;const pending=new Map();return {pending,set(fn,ms){const id=++serial;pending.set(id,{fn,ms});return id;},clear(id){pending.delete(id);}};}
test('manual total equity and automatic USDT equity remain source-specific; real zero is retained',()=>{
 assert.deepEqual(project('manual',manual(),null,now).metrics,['120.12','100','0',0]);
 const auto=project('signals',signals(),null,now);
 assert.deepEqual(auto.metrics,['75','60','-2',2]);assert.match(auto.activity,/已停止/);
 assert.doesNotMatch(auto.note,/9999/);assert.match(project('manual',manual(),null,now).note,/4 笔收益待归属/);
});
test('missing balances and positions are unknown, not zero or an inferred empty account',()=>{
 const v=project('manual',{account_scope:'yolo_manual',account:{status:'loading',data:null}},null,now);
 assert.ok(v.metrics.every(x=>x==null));assert.match(v.status,/读取/);
 const denied=project('manual',manual(),'restricted',now);assert.ok(denied.metrics.every(x=>x==null));assert.doesNotMatch(denied.note,/-13/);
});
test('settlement strip is manual-only, preserves zero and clears on permission loss',()=>{
 const m=manual();m.executions.statistics={filled:0,closed:0,realized_net_usdt:0,unattributed:null};
 assert.deepEqual(project('manual',m,null,now).settlement,{net:0,filled:0,closed:0,unattributed:null});
 assert.equal(project('manual',m,'restricted',now).settlement,null);
 const a=signals();a.executions=m.executions;
 assert.equal(project('signals',a,null,now).settlement,null);
});
test('fresh account data can coexist with stopped execution; loading is never marked synced',()=>{
 const a=project('signals',signals(),null,now);
 assert.equal(a.synced,true);assert.equal(a.executionWarning,true);
 assert.equal(project('signals',signals(),null,now+60001).synced,false);
 assert.equal(project('manual',{account:{status:'loading'}},null,now).synced,false);
 assert.equal(project('manual',manual(),'restricted',now).synced,false);
});
test('freshness follows source time and read failure independently of HTTP success',()=>{
 assert.equal(project('signals',signals(),null,now+60001).stale,true);
 assert.equal(project('signals',signals(),'unavailable',now).stale,true);
 assert.equal(project('signals',signals(),null,now).stale,false);
 const v=signals();v.account.updated_ms=null;assert.equal(project('signals',v,null,now).stale,true);
 v.account.status='unconfigured';assert.ok(project('signals',v,null,now).metrics.every(x=>x==null));
});
test('account identity mismatch is rejected instead of falling back to the other account',()=>{
 assert.equal(valid('manual',signals()),false);assert.equal(valid('signals',manual()),false);
 assert.equal(valid('manual',manual()),true);assert.equal(valid('signals',signals()),true);
});
test('permission loss clears a previously loaded snapshot and stops exposing its values',async()=>{
 const timers=scheduler();let denied=false;
 const feed=new Feed(ACCOUNTS[0],async()=>response(manual(),denied?403:200),()=>{},timers);
 feed.setActive(true);await feed.flight;assert.ok(feed.snapshot);
 denied=true;await feed.refresh();assert.equal(feed.snapshot,null);assert.equal(feed.error,'restricted');
 feed.setActive(false);assert.equal(timers.pending.size,0);
});
test('one failing account leaves the other account available and marks its own previous values stale',async()=>{
 const timers=scheduler();let fail=false;
 const a=new Feed(ACCOUNTS[0],async()=>{if(fail)throw Error('offline');return response(manual());},()=>{},timers);
 const b=new Feed(ACCOUNTS[1],async()=>response(signals()),()=>{},timers);
 a.setActive(true);b.setActive(true);await Promise.all([a.flight,b.flight]);
 fail=true;await a.refresh();assert.ok(a.snapshot);assert.equal(a.error,'unavailable');assert.equal(b.error,null);assert.equal(b.snapshot.account.data.balance.eq,'75');
 a.setActive(false);b.setActive(false);
});
test('navigation aborts reads and late responses cannot overwrite a reactivated page',async()=>{
 const timers=scheduler(),requests=[];let paints=0;
 const feed=new Feed(ACCOUNTS[0],(url,options)=>new Promise(resolve=>requests.push({url,options,resolve})),()=>paints++,timers);
 feed.setActive(true);const old=feed.flight;await Promise.resolve();assert.equal(feed.refresh(),old);
 feed.setActive(false);assert.equal(requests[0].options.signal.aborted,true);
 feed.setActive(true);const next=feed.flight;await Promise.resolve();
 requests[1].resolve(response(manual()));await next;
 const stale=manual();stale.account.data.total_equity_usdt='9999';requests[0].resolve(response(stale));await old;
 assert.equal(feed.snapshot.account.data.total_equity_usdt,'120.12');assert.equal(paints,1);
 assert.equal(requests[0].url,'/api/manual-live/dashboard?view=workspace');assert.equal(requests[0].options.method,undefined);
 feed.setActive(false);assert.equal(timers.pending.size,0);
});
test('an invalid response clears account values instead of publishing a wrong-scope balance',async()=>{
 const timers=scheduler();const feed=new Feed(ACCOUNTS[0],async()=>response(signals()),()=>{},timers);
 feed.snapshot=manual();feed.setActive(true);await feed.flight;
 assert.equal(feed.snapshot,null);assert.equal(feed.error,'unavailable');feed.setActive(false);
});
test('browser timer functions are called without the feed as their receiver',async()=>{
 const fs=require('node:fs'),vm=require('node:vm'),timers=scheduler();
 const context={module:{exports:{}},AbortController,
  setTimeout:function(fn,ms){assert.equal(this,undefined);return timers.set(fn,ms);},
  clearTimeout:function(id){assert.equal(this,undefined);timers.clear(id);}};
 vm.runInNewContext(fs.readFileSync(require.resolve('../../yoyo/monitor/static/market-accounts.js'),'utf8'),context);
 const BrowserFeed=context.module.exports.Feed;
 const feed=new BrowserFeed(ACCOUNTS[0],async()=>response(manual()),()=>{});
 feed.setActive(true);await feed.flight;assert.ok(feed.snapshot);
 feed.setActive(false);assert.equal(timers.pending.size,0);
});
