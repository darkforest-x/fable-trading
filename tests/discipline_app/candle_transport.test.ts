import {test} from 'node:test';
import assert from 'node:assert/strict';
import {candleFetcher} from '../../yoyo/discipline_app/mobile/src/candleTransport.ts';
const payload={code:'0',data:[['1700000000000','100','105','95','101','2','20','2000','0']]};
const symbol='BTC-USDT-SWAP';
test('native history uses the service transport and converts older cursor in seconds',async()=>{
 const calls:any[]=[];
 const read=candleFetcher({connection:{url:'https://service.example',token:'test-token',accountId:'primary'},native:async options=>{calls.push(options);return {status:200,data:payload}}});
 const data=await read(symbol,'1m',{before:1700000000,signal:new AbortController().signal});
 assert.equal(data.length,1);assert.equal(calls.length,1);
 const url=new URL(calls[0].url);assert.equal(url.pathname,'/v1/market/candles');assert.equal(url.searchParams.get('before'),'1700000000');
 assert.equal(calls[0].headers.Authorization,'Bearer test-token');assert.equal(calls[0].disableRedirects,true);assert.equal(calls[0].readTimeout,9000);
});
test('public OKX fallback uses native HTTP without leaking service headers',async()=>{
 const calls:any[]=[];const read=candleFetcher({connection:{url:'https://service.example',token:'test-token'},native:async options=>{calls.push(options);return calls.length===1?{status:503,data:{}}:{status:200,data:JSON.stringify(payload)}}});
 assert.equal((await read(symbol,'5m',{before:1700000000,signal:new AbortController().signal})).length,1);
 assert.equal(new URL(calls[1].url).hostname,'www.okx.com');assert.equal(new URL(calls[1].url).searchParams.get('after'),'1700000000000');assert.deepEqual(calls[1].headers,{});
});
test('abort fences late native results and never starts fallback after collapse',async()=>{
 let finish!:(v:any)=>void,calls=0;const controller=new AbortController();
 const read=candleFetcher({connection:{url:'https://service.example',token:'test-token'},native:()=>{calls++;return new Promise(r=>finish=r)}});
 const pending=read(symbol,'1m',{signal:controller.signal});controller.abort();await assert.rejects(pending,{name:'AbortError'});
 finish({status:200,data:payload});await Promise.resolve();assert.equal(calls,1);
});
test('native timeout cannot hang the chart indefinitely',async t=>{
 t.mock.timers.enable({apis:['setTimeout']});let calls=0;
 const read=candleFetcher({native:()=>{calls++;return new Promise(()=>{})}});
 const pending=read(symbol,'1m',{signal:new AbortController().signal});const rejected=assert.rejects(pending,{name:'AbortError'});
 t.mock.timers.tick(10000);await rejected;assert.equal(calls,1);
});
test('primary plus fallback share one finite deadline',async t=>{
 t.mock.timers.enable({apis:['setTimeout']});let calls=0;
 const read=candleFetcher({connection:{url:'https://service.example',token:'test-token'},native:()=>{calls++;return new Promise(()=>{})}});
 const pending=read(symbol,'1m',{signal:new AbortController().signal});const rejected=assert.rejects(pending,{name:'AbortError'});
 t.mock.timers.tick(10000);for(let i=0;i<6;i++)await Promise.resolve();assert.equal(calls,2);
 t.mock.timers.tick(8000);await rejected;
});
