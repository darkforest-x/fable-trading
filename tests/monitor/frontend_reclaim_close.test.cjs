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
test('spike close preview honors the configured margin cap for each closed tranche',()=>{
 const p=position({track:3060,margin_leverage:5,mark:50});
 assert.equal(estimate(p,100).pnl,-15);assert.equal(estimate(p,50).pnl,-7.5);
 assert.equal(estimate({...p,mark:110},50).pnl,3.72375);
 assert.equal(estimate({...p,margin_leverage:4},100).pnl,-18.75);
 assert.equal(estimate({...p,mark:null},100).pnl,null);
 assert.equal(estimate({...p,margin_leverage:null},100),null);
 assert.equal(estimate({...p,track:60},100).pnl,-37.5525);
});
test('allin3 close preview caps each tranche at its own configured 3x margin',()=>{
 const p=position({track:5060,margin_leverage:3,notional:3000,remaining_notional:1500,mark:50});
 assert.equal(estimate(p,100).pnl,-500);assert.equal(estimate(p,25).pnl,-125);
 assert.equal(estimate({...p,mark:110},50).pnl,74.475);
 assert.equal(estimate({...p,margin_leverage:5},100).pnl,-300);
 assert.equal(estimate({...p,side:-1,mark:150},50).pnl,-250);
 assert.equal(estimate({...p,mark:null},100).pnl,null);
 for(const margin_leverage of [null,undefined,0,-1])assert.equal(estimate({...p,margin_leverage},100),null);
 assert.equal(estimate({...p,track:4060},100).pnl,-751.05);
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
