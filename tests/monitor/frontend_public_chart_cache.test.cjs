'use strict';
const test=require('node:test'),assert=require('node:assert/strict');
const {create,clean,weekAt}=require('../../yoyo/monitor/static/public-chart-cache.js');
const now=Date.parse('2026-10-23T12:00:00Z'),start=Date.parse('2026-10-19T04:00:00Z');
const candles=(symbol='BTC-USDT-SWAP')=>({symbol,bar:'5m',native_bar:'5m',timezone:'America/New_York',source:'OKX',week:'2026-10-19',monday:{start_ms:start,end_ms:start+86400000,expires_ms:start+7*86400000},now_ms:now,last_close_ms:now,history_complete:true,candles:[{time:now-300000,open:10,high:12,low:9,close:11,volume:4}],paperTrade:{secret:'must-not-persist'}});
const levels=()=>({symbol:'BTC-USDT-SWAP',timezone:'America/New_York',week:'2026-10-19',state:'ready',start_ms:start,end_ms:start+86400000,expires_ms:start+7*86400000,high_ms:start,low_ms:start+300000,high:12,low:9,upper:13.854,lower:7.146});
const flush=async()=>{for(let i=0;i<30;i++)await new Promise(r=>setImmediate(r));};
function disk({fail=false}={}){const rows=new Map();return {rows,open(){const req={};queueMicrotask(()=>{if(fail){req.onerror();return}req.result={objectStoreNames:{contains:()=>true},close(){},transaction(){const tx={};const request=fn=>{const r={};queueMicrotask(()=>{r.result=structuredClone(fn());r.onsuccess?.()});return r;};tx.objectStore=()=>({put:r=>request(()=>{rows.set(r.id,structuredClone(r));return r.id}),get:k=>request(()=>rows.get(k)),getAll:()=>request(()=>[...rows.values()]),delete:k=>rows.delete(k)});setImmediate(()=>tx.oncomplete?.());return tx;}};req.onsuccess()});return req;}};}
test('NY week keys respect Monday midnight and daylight-saving offsets',()=>{
 assert.equal(weekAt(Date.parse('2026-10-26T03:59:59Z')),'2026-10-19');assert.equal(weekAt(Date.parse('2026-10-26T04:00:00Z')),'2026-10-26');assert.equal(weekAt(Date.parse('2026-11-02T04:59:59Z')),'2026-10-26');assert.equal(weekAt(Date.parse('2026-11-02T05:00:00Z')),'2026-11-02');
});
test('only validated closed OHLC is persisted, never live bars, trades or account fields',()=>{
 const c=clean('candles',candles());assert.equal(c.paperTrade,undefined);assert.equal(c.candles.length,1);
 for(const extra of [{last_close_ms:now+300000},{bar:'1D',native_bar:'1D'},{error:'bad'},{candles:[{...c.candles[0],time:now}]},{candles:[{...c.candles[0],volume:-1}]},{week:'2026-10-12'},{monday:{start_ms:1e100,end_ms:1e101,expires_ms:1e102}}])assert.equal(clean('candles',{...candles(),...extra}),null);
 assert.ok(clean('levels',levels()));assert.equal(clean('levels',{...levels(),upper:100}),null);
});
test('public history survives a new page instance while mismatched, old and corrupt entries are rejected',async()=>{
 const db=disk(),a=create({indexedDB:db,now:()=>now});a.saveCandles(candles());a.saveLevels(levels());await flush();
 const b=create({indexedDB:db,now:()=>now+1000}),key='BTC-USDT-SWAP|2026-10-19|5m';
 assert.equal((await b.restoreCandles(key)).candles[0].close,11);assert.equal((await b.restoreLevels('BTC-USDT-SWAP|2026-10-19')).high,12);
 assert.equal(await b.restoreCandles('ETH-USDT-SWAP|2026-10-19|5m'),null);
 const stale=create({indexedDB:db,now:()=>now+86400001});assert.equal(await stale.restoreCandles(key),null);
 const r=db.rows.get('candles|'+key);r.value.symbol='ETH-USDT-SWAP';assert.equal(await b.restoreCandles(key),null);
 r.value.symbol='BTC-USDT-SWAP';r.value.candles[0].close=Infinity;assert.equal(await b.restoreCandles(key),null);
});
test('disk storage is bounded and blocked storage falls back without blocking the page',async()=>{
 const db=disk();let time=now;const a=create({indexedDB:db,now:()=>time++});
 for(let i=0;i<14;i++)a.saveCandles(candles('C'+i+'-USDT-SWAP'));await flush();
 assert.equal([...db.rows.keys()].filter(k=>k.startsWith('candles|')).length,12);
 assert.equal(await a.restoreCandles('C0-USDT-SWAP|2026-10-19|5m'),null);
 for(const indexedDB of [null,disk({fail:true}),{open(){throw Error('denied')}}]){const b=create({indexedDB,now:()=>now});b.saveCandles(candles());assert.equal(await b.restoreCandles('BTC-USDT-SWAP|2026-10-19|5m'),null);}
});

test('12H cache restores UTC bars without mixing periods or accepting local boundaries',async()=>{
 const db=disk(),value={...candles(),bar:'12H',native_bar:'12Hutc'};
 value.candles[0].time=now-43200000;
 const a=create({indexedDB:db,now:()=>now});a.saveCandles(value);await flush();
 const b=create({indexedDB:db,now:()=>now});
 assert.equal((await b.restoreCandles('BTC-USDT-SWAP|2026-10-19|12H')).candles[0].time,now-43200000);
 assert.equal(await b.restoreCandles('BTC-USDT-SWAP|2026-10-19|1D'),null);
 assert.equal(clean('candles',{...value,native_bar:'12H'}),null);
 value.candles[0].time-=8*3600000;assert.equal(clean('candles',value),null);
});
