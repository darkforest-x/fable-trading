'use strict';
const test=require('node:test'),assert=require('node:assert/strict');
const {createRequests,idleRelease}=require('../../yoyo/monitor/static/workspace-runtime.js');
test('concurrent reads coalesce; navigation abort releases the key for re-entry',async()=>{
 const original=global.fetch;let calls=0,release;
 global.fetch=(_url,{signal})=>{calls++;return new Promise((resolve,reject)=>{release=()=>resolve({ok:true,json:async()=>({ok:true})});signal.addEventListener('abort',()=>reject(Object.assign(Error('abort'),{name:'AbortError'})));});};
 try{
  const requests=createRequests();const first=requests.json('/read'),second=requests.json('/read');
  assert.equal(first,second);assert.equal(calls,1);requests.cancel();await assert.rejects(first,{name:'AbortError'});
  const next=requests.json('/read');release();assert.deepEqual(await next,{ok:true});assert.equal(calls,2);
 }finally{global.fetch=original;}
});
test('body stalls time out, failed reads back off, and writes are never combined or cancelled on leave',async()=>{
 const original=global.fetch;let calls=0,resolvers=[];
 global.fetch=(_url,{signal,method})=>{calls++;return Promise.resolve({ok:true,json:()=>new Promise((resolve,reject)=>{resolvers.push(resolve);signal.addEventListener('abort',()=>reject(Object.assign(Error('abort'),{name:'AbortError'})));})});};
 try{
  const requests=createRequests({timeoutMs:10,backoffMs:1000});await assert.rejects(requests.json('/slow'),/超时/);
  await assert.rejects(requests.json('/slow'),/超时/);assert.equal(calls,1);
  const a=requests.json('/write',{method:'POST'}),b=requests.json('/write',{method:'POST'});assert.notEqual(a,b);
  await Promise.resolve();requests.cancel();for(const resolve of resolvers)resolve({saved:true});
  assert.deepEqual(await a,{saved:true});assert.deepEqual(await b,{saved:true});assert.equal(calls,3);
 }finally{global.fetch=original;}
});
test('idle cleanup happens once after leaving; repeated inactive notifications do not postpone it',async()=>{
 let count=0;const idle=idleRelease(()=>count++,15);
 idle.setActive(false);await new Promise(r=>setTimeout(r,10));idle.setActive(false);
 await new Promise(r=>setTimeout(r,10));assert.equal(count,1);
 idle.setActive(true);idle.setActive(false);idle.setActive(true);await new Promise(r=>setTimeout(r,20));assert.equal(count,1);
});
test('a synchronous transport failure cannot strand the pending read lock',async()=>{
 const original=global.fetch;let calls=0;
 global.fetch=()=>{calls++;if(calls===1)throw Error('transport unavailable');return Promise.resolve({ok:true,json:async()=>({ok:true})});};
 try{const requests=createRequests({backoffMs:0});await assert.rejects(requests.json('/sync'),/transport/);assert.deepEqual(await requests.json('/sync'),{ok:true});assert.equal(calls,2);}finally{global.fetch=original;}
});
