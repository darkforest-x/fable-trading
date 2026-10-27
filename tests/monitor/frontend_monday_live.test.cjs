'use strict';
const test=require('node:test'),assert=require('node:assert/strict');
const {Flow,paperAccount}=require('../../yoyo/monitor/static/monday-live.js');
test('home account preference resolves its identity even when another account is active or first',()=>{
 const data={active_account_id:'old',accounts:[{id:'old',name:'其他账户'},{id:'preferred',name:'梭哈账户'}]};
 assert.equal(paperAccount(data,'梭哈账户'),'preferred');
 assert.equal(paperAccount({...data,accounts:[...data.accounts].reverse()},'梭哈账户'),'preferred');
 assert.equal(paperAccount(data),'old');
});
test('missing or ambiguous preferred accounts require explicit choice instead of silent fallback',()=>{
 assert.equal(paperAccount({accounts:[{id:'old',name:'其他账户'}]},'梭哈账户'),'');
 assert.equal(paperAccount({accounts:[{id:'a',name:'梭哈账户'},{id:'b',name:'梭哈账户'}]},'梭哈账户'),'');
 assert.equal(paperAccount({accounts:[]},'梭哈账户'),'');
});
const draft=()=>({id:'one',state:'preview',expires_ms:Date.now()+60000});
test('preview and edits never confirm, and retired preview replies cannot become confirmable',async()=>{
 const calls=[];let release;const f=new Flow((p,o)=>{calls.push(p);return new Promise(r=>release=r);});
 const p=f.preview({symbol:'BTC'});f.reset();release(draft());await p;assert.equal(f.canConfirm(),false);assert.deepEqual(calls,['/free/preview']);
});
test('confirmation is single flight and ambiguous success is queried with the original ID',async()=>{
 const calls=[];const f=new Flow(async p=>{calls.push(p);if(p.endsWith('confirm'))throw Error('lost');return {...draft(),state:'confirmed'};});f.draft=draft();
 await Promise.all([f.confirm(),f.confirm()]);assert.equal(f.sent,'one');assert.equal(f.canConfirm(),false);
 await f.status();assert.equal(f.draft.state,'confirmed');assert.deepEqual(calls,['/free/one/confirm','/free/one']);
});
test('expired quotes cannot confirm and a known unsent confirmation can be retried',async()=>{
 let calls=0;const f=new Flow(async()=>{calls++;return draft();});f.draft={...draft(),expires_ms:0};await f.confirm();assert.equal(calls,0);
 f.draft=draft();await f.confirm();await f.status();assert.equal(f.sent,null);assert.equal(f.canConfirm(),true);
});
