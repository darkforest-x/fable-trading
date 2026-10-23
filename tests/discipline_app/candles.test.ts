import { test } from 'node:test';
import assert from 'node:assert/strict';
import { CandleCache, fetchCandles, mergeCandles, parseCandles, type Candle } from '../../yoyo/discipline_app/mobile/src/candleData.ts';
import { CandleSession, type CandleState } from '../../yoyo/discipline_app/mobile/src/candleSession.ts';

const symbol='BTC-USDT-SWAP', time=1700000000;
const bar=(t=time,close=101,confirmed=false):Candle=>({time:t,open:100,high:Math.max(105,close),low:95,close,volume:10,confirmed});
const row=(b:Candle)=>[String(b.time*1000),String(b.open),String(b.high),String(b.low),String(b.close),'999',String(b.volume),'1000',b.confirmed?'1':'0'];
const flush=async()=>{await Promise.resolve();await Promise.resolve()};
class Disk {
 values=new Map<string,string>();get length(){return this.values.size}
 getItem(k:string){return this.values.get(k)??null} setItem(k:string,v:string){this.values.set(k,v)} removeItem(k:string){this.values.delete(k)} key(i:number){return [...this.values.keys()][i]??null}
}

test('deferred candle persistence reopens from memory without synchronous disk writes',t=>{
 t.mock.timers.enable({apis:['setTimeout']});
 const disk=new Disk(),cache=new CandleCache(disk,()=>time*1000,true);
 cache.write(symbol,'15m',[bar(time-900,100,true),bar()]);
 assert.equal(disk.length,0);assert.equal(cache.read(symbol,'15m').length,2);
 cache.write(symbol,'15m',[bar(time-900,100,true),bar(time,103)]);
 t.mock.timers.tick(300);assert.equal(disk.length,1);
 assert.equal(new CandleCache(disk,()=>time*1000).read(symbol,'15m').at(-1)?.close,103);
});
class Socket {
 readyState=0; sent:string[]=[]; closed=0;
 onopen:(()=>void)|null=null;onclose:(()=>void)|null=null;onerror:(()=>void)|null=null;onmessage:((e:{data:string})=>void)|null=null;
 open(){this.readyState=1;this.onopen?.()}
 send(data:string){this.sent.push(data)} close(){this.readyState=3;this.closed++;this.onclose?.()}
 bars(bars:Candle[],id=symbol){this.onmessage?.({data:JSON.stringify({arg:{instId:id,channel:'candle1m'},data:bars.map(row)})})}
}

test('OKX parsing preserves base volume, sorts/deduplicates and rejects malformed OHLC',()=>{
 const bad=row(bar());bad[2]='90';
 const result=parseCandles([row(bar(time+60)),row(bar()),row(bar(time,102)),bad,[],row({...bar(),volume:-1})]);
 assert.deepEqual(result.map(b=>[b.time,b.close,b.volume]),[[time,102,10],[time+60,101,10]]);
 assert.deepEqual(parseCandles({data:[]}),[]);
});
test('merge never regresses a confirmed candle and retains a bounded chronological tail',()=>{
 const result=mergeCandles([bar(time,103,true)], [bar(time,99),bar(time+60),bar(time+120)],2);
 assert.deepEqual(result.map(b=>b.time),[time+60,time+120]);
 assert.equal(mergeCandles([bar(time,103,true)],[bar(time,99)])[0].close,103);
});
test('cache is isolated by contract and period, expires and prunes without touching other app data',()=>{
 const disk=new Disk();let now=time*1000;const cache=new CandleCache(disk,()=>now);
 disk.setItem('other-app-data','keep');cache.write(symbol,'1m',[bar()]);
 assert.equal(cache.read(symbol,'1m').length,1);assert.equal(cache.read(symbol,'5m').length,0);assert.equal(cache.read('ETH-USDT-SWAP','1m').length,0);
 for(let i=0;i<15;i++)cache.write(`COIN${i}-USDT-SWAP`,'1m',[bar()]);
 assert.equal(disk.length,13);assert.equal(disk.getItem('other-app-data'),'keep');
 now+=8*86400000;assert.equal(cache.read('COIN14-USDT-SWAP','1m').length,0);
 disk.setItem('spike-candles-v1:BTC-USDT-SWAP:1m','broken');assert.deepEqual(cache.read(symbol,'1m'),[]);
});
test('public REST uses millisecond older cursor, no account credentials and forwards abort',async t=>{
 let url='',init:RequestInit|undefined;
 t.mock.method(globalThis,'fetch',async(u:URL,i:RequestInit)=>{url=String(u);init=i;return new Response(JSON.stringify({code:'0',data:[row(bar())]}))});
 const abort=new AbortController();const result=await fetchCandles(symbol,'1m',{before:time,signal:abort.signal});
 assert.equal(result.length,1);assert.equal(new URL(url).pathname,'/api/v5/market/history-candles');assert.equal(new URL(url).searchParams.get('after'),String(time*1000));assert.equal(init?.credentials,'omit');assert.equal(init?.headers,undefined);
 t.mock.method(globalThis,'fetch',(_u:URL,i:RequestInit)=>new Promise((_resolve,reject)=>i.signal!.addEventListener('abort',()=>reject(Error('abort')))));
 const pending=fetchCandles(symbol,'1m',{signal:abort.signal});abort.abort();await assert.rejects(pending,/abort/);
});
test('cache appears immediately; websocket arriving during REST wins; stop fences late callbacks',async t=>{
 t.mock.timers.enable({apis:['setTimeout','setInterval','Date'],now:time*1000+5000});
 const disk=new Disk(),cache=new CandleCache(disk);cache.write(symbol,'1m',[bar(time-120),bar(time-60)]);
 let resolve!:(bars:Candle[])=>void;const socket=new Socket();const states:CandleState[]=[];let signal:AbortSignal|undefined;
 const session=new CandleSession({symbol,period:'1m',cache,socket:()=>socket as unknown as WebSocket,fetchPage:(_s,_p,o)=>{signal=o.signal;return new Promise(r=>resolve=r)},onChange:s=>states.push(s)});
 session.start();assert.equal(states[0].status,'cached');socket.open();socket.bars([bar(time,104)]);
 resolve([bar(time-60,100,true),bar(time,101)]);await flush();assert.equal(states.at(-1)!.bars.at(-1)!.close,104);assert.equal(states.at(-1)!.status,'live');
 const callback=socket.onmessage!;session.stop();const count=states.length;callback({data:JSON.stringify({arg:{instId:symbol,channel:'candle1m'},data:[row(bar(time+60))]})});
 assert.equal(states.length,count);assert.equal(socket.closed,1);assert.equal(cache.read(symbol,'1m').at(-1)!.close,104);assert.equal(signal?.aborted,false);
 t.mock.timers.tick(60000);assert.equal(states.length,count);
});
test('stop aborts in-flight history, and a late old-symbol result cannot publish',async t=>{
 t.mock.timers.enable({apis:['setTimeout','setInterval']});let resolve!:(bars:Candle[])=>void;let signal!:AbortSignal;let count=0;
 const session=new CandleSession({symbol,period:'1m',cache:new CandleCache(),socket:()=>new Socket() as unknown as WebSocket,fetchPage:(_s,_p,o)=>{signal=o.signal;return new Promise(r=>resolve=r)},onChange:()=>count++});
 session.start();session.stop();assert.equal(signal.aborted,true);const before=count;resolve([bar()]);await flush();assert.equal(count,before);
});
test('history errors survive live ticks; reconnect snapshot repairs gaps without inventing candles',async t=>{
 t.mock.timers.enable({apis:['setTimeout','setInterval','Date'],now:(time+1000)*1000});
 const cache=new CandleCache(new Disk());cache.write(symbol,'1m',[bar(time-1000)]);const socket=new Socket();let fail=true;let state!:CandleState;
 const session=new CandleSession({symbol,period:'1m',cache,socket:()=>socket as unknown as WebSocket,fetchPage:async()=>{if(fail)throw Error('offline');return [bar(time-60,100,true),bar(time)]},onChange:s=>state=s});
 session.start();await flush();socket.open();await flush();socket.bars([bar(time,104)]);assert.match(state.error,/历史/);assert.equal(state.status,'offline');assert.equal(state.historyReady,false);
 fail=false;await session.refresh();assert.deepEqual(state.bars.map(b=>b.time),[time-60,time]);assert.equal(state.error,'');session.stop();
});
test('heartbeat detects stale feed, repairs by REST and retries hung sockets; collapse clears retries',async t=>{
 t.mock.timers.enable({apis:['setTimeout','setInterval','Date'],now:(time+1)*1000});let state!:CandleState;let reads=0;const sockets:Socket[]=[];
 const session=new CandleSession({symbol,period:'1m',cache:new CandleCache(),socket:()=>{const s=new Socket();sockets.push(s);return s as unknown as WebSocket},fetchPage:async()=>{reads++;return [bar(time-60),bar()]},onChange:s=>state=s});
 session.start();await flush();sockets[0].open();await flush();sockets[0].bars([bar()]);assert.equal(state.status,'live');
 t.mock.timers.tick(15000);await flush();assert.equal(state.status,'polling');
 t.mock.timers.tick(10000);await flush();assert.ok(sockets[0].closed);assert.ok(reads>=2);
 t.mock.timers.tick(1000);assert.equal(sockets.length,2);
 t.mock.timers.tick(15000);assert.ok(sockets[1].closed);session.stop();const count=sockets.length;t.mock.timers.tick(60000);assert.equal(sockets.length,count);
});
test('older history is single-flight, cannot overwrite live bars, and stops at exhaustion',async t=>{
 t.mock.timers.enable({apis:['setTimeout','setInterval','Date'],now:(time+1)*1000});const socket=new Socket();let state!:CandleState;let resolve!:(bars:Candle[])=>void;let olderReads=0;
 const session=new CandleSession({symbol,period:'1m',cache:new CandleCache(),socket:()=>socket as unknown as WebSocket,fetchPage:async(_s,_p,o)=>{if(o.before){olderReads++;return new Promise(r=>resolve=r)}return [bar(time-60),bar()]},onChange:s=>state=s});
 session.start();await flush();socket.open();await flush();const first=session.loadOlder();void session.loadOlder();assert.equal(olderReads,1);
 socket.bars([bar(time,104)]);resolve([bar(time-120,99,true),bar(time,98)]);await first;assert.equal(state.bars.at(-1)!.close,104);assert.equal(state.bars.length,3);
 const last=session.loadOlder();resolve([]);await last;assert.equal(state.canLoadOlder,false);session.stop();
});

test('healthy live tips do not suppress failed initial history retry or expose a one-bar chart',async t=>{
 t.mock.timers.enable({apis:['setTimeout','setInterval','Date'],now:(time+1)*1000});
 const socket=new Socket();let state!:CandleState,reads=0,fail=true;
 const session=new CandleSession({symbol,period:'1m',cache:new CandleCache(),socket:()=>socket as unknown as WebSocket,fetchPage:async()=>{reads++;if(fail)throw Error('history blocked');return [bar(time-60),bar()]},onChange:s=>state=s});
 session.start();socket.open();await flush();socket.bars([bar()]);
 assert.equal(state.historyReady,false);assert.equal(state.bars.length,1);assert.equal(reads,1);
 await session.loadOlder();assert.equal(reads,1);
 fail=false;
 for(let i=0;i<5;i++){t.mock.timers.tick(1000);socket.bars([bar(time,104)]);await flush()}
 assert.equal(reads,2);assert.equal(state.historyReady,true);assert.equal(state.status,'live');assert.equal(state.error,'');assert.equal(state.bars.at(-1)!.close,104);
 session.stop();
});
test('repeated history failure backs off, retains real cache, and stops all background retry',async t=>{
 t.mock.timers.enable({apis:['setTimeout','setInterval','Date'],now:(time+1)*1000});
 const cache=new CandleCache(new Disk());cache.write(symbol,'1m',[bar(time-60),bar()]);let state!:CandleState,reads=0;
 const socket=new Socket();const session=new CandleSession({symbol,period:'1m',cache,socket:()=>socket as unknown as WebSocket,fetchPage:async()=>{reads++;throw Error('offline')},onChange:s=>state=s});
 session.start();socket.open();await flush();
 for(let i=0;i<14;i++){t.mock.timers.tick(1000);socket.bars([bar()]);await flush()}
 assert.equal(reads,2);assert.equal(state.historyReady,true);assert.equal(state.status,'delayed');assert.equal(state.errorKind,'snapshot');
 t.mock.timers.tick(1000);await flush();assert.equal(reads,3);
 session.stop();t.mock.timers.tick(120000);await flush();assert.equal(reads,3);
});
