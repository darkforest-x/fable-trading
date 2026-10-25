'use strict';
const test=require('node:test'),assert=require('node:assert/strict');
const {estimate,createSubmitter}=require('../../yoyo/monitor/static/reclaim-close.js');
const {rowHTML,route}=require('../../yoyo/monitor/static/monday-reclaim-paper.js');
const position=(x={})=>({id:1,track:60,symbol:'SOL-USDT-SWAP',side:1,stage:'pending_confirm',entry:100,mark:110,notional:100,remaining_notional:75,taken:1,close_revision:1,...x});
test('partial presets apply to remaining quantity, with allocated entry fee and taker exit cost',()=>{
 const e=estimate(position(),50);assert.equal(e.quantity,.375);assert.equal(e.remaining,.375);assert.ok(Math.abs(e.entryFee-.0075)<1e-12);assert.equal(e.exitFee,.01875);assert.equal(e.pnl,3.72375);
 assert.equal(estimate(position({side:-1}),100).pnl,-7.5525);
 assert.equal(estimate(position({mark:null}),25).pnl,null);
 for(const percentage of [0,-1,101,NaN,'50'])assert.equal(estimate(position(),percentage),null);
 for(const p of [position({stage:'closed'}),position({taken:0}),position({remaining_notional:0})])assert.equal(estimate(p,100),null);
});
test('only open funded records expose close actions and tranches remain visible in the ledger',()=>{
 const html=rowHTML(position({exits:[{reason:'manual',notional:25,price:105,pnl:1.2325,exit_ms:100000}]}),route(),200000);
 assert.match(html,/data-rp-close="1"/);assert.match(html,/已减 25.0%/);assert.match(html,/逐笔平仓/);assert.match(html,/手动平仓/);
 assert.doesNotMatch(rowHTML(position({stage:'closed'}),route({tab:'closed'}),200000),/data-rp-close/);
 assert.doesNotMatch(rowHTML(position({taken:0}),route(),200000),/data-rp-close/);
});
test('double-clicks share one POST and uncertain retries preserve request identity',async()=>{
 let resolve,seq=0;const calls=[];
 const client=createSubmitter({uuid:()=>String(++seq),send:(id,payload)=>{calls.push({id,payload});return new Promise(r=>resolve=r);}});
 const a=client.submit(position(),25),b=client.submit(position(),25);assert.equal(a,b);await Promise.resolve();assert.equal(calls.length,1);resolve({ok:true});await a;
 const retry=client.submit(position(),25);await Promise.resolve();assert.equal(calls[1].payload.request_id,calls[0].payload.request_id);resolve({ok:true});await retry;
 const next=client.submit(position({close_revision:2}),25);await Promise.resolve();assert.notEqual(calls[2].payload.request_id,calls[0].payload.request_id);resolve({ok:true});await next;
 let retries=0;const keys=[];const uncertain=createSubmitter({uuid:()=>String(++seq),send:async(id,p)=>{keys.push(p.request_id);if(!retries++)throw TypeError('network');return {ok:true};}});
 await assert.rejects(uncertain.submit(position(),25));await uncertain.submit(position(),25);assert.equal(keys[0],keys[1]);
 await assert.rejects(uncertain.submit(position(),0));assert.equal(keys.length,2);
});
