'use strict';
const test=require('node:test'),assert=require('node:assert/strict');
const {PERIODS,nativeBar,parse,mergeDisplay,create}=require('../../yoyo/monitor/static/monday-candle-feed.js');
const NOW=Date.parse('2026-10-24T02:00:10Z');
const raw=(t,confirm='0',close='11')=>[String(t),'10','14','8',close,'4','40','400',confirm];
const obj=(t,confirmed=false,close=11)=>({time:t,open:10,high:14,low:8,close,volume:4,confirmed});
for(const [period,step] of Object.entries(PERIODS))test(`${period}: exact native live OHLC, confirmed-only history, boundary rollover`,()=>{
 const boundary=Math.floor(NOW/step)*step;
 assert.equal(nativeBar(period),({'2m':'1m','12H':'12Hutc','1D':'1Dutc'}[period]||period));
 const rows=parse([raw(boundary),raw(boundary-step,'1')],period,NOW);
 assert.equal(rows.length,2);assert.equal(rows[1].confirmed,false);
 assert.deepEqual(parse([raw(boundary,'1'),raw(boundary-step),raw(boundary+step),raw(boundary+1)],period,NOW),[]);
 const history={last_close_ms:boundary,candles:[obj(boundary-step,true,12)]};
 const result=mergeDisplay(history,{bars:rows,now_ms:NOW},period);
 assert.equal(result.candles.length,2);assert.equal(result.candles[0].close,12);assert.equal(history.candles.length,1);assert.equal(result.last_close_ms,boundary);
 const next=boundary+step;
 assert.equal(mergeDisplay(history,{bars:rows,now_ms:next+1},period).candles.length,1,'expired forming bar must never be promoted on the clock alone');
 const confirmed=parse([raw(boundary,'1'),raw(next)],period,next+1);
 assert.equal(mergeDisplay(history,{bars:confirmed,now_ms:next+1},period).candles.length,3);
});
test('malformed OHLC and invalid confirm/volume are rejected',()=>{
 const t=Math.floor(NOW/60000)*60000;
 for(const change of [r=>r[4]='NaN',r=>r[2]='9',r=>r[3]='12',r=>r[5]='-1',r=>r[1]='',r=>r[8]='true',r=>r.pop()]){
  const r=raw(t);change(r);assert.deepEqual(parse([r],'1m',NOW),[]);
 }
});
function fixture(period='1H'){
 let now=NOW,id=0;const timers=new Map(),intervals=new Map(),sockets=[],requests=[],states=[];let repairs=0;
 const feed=create({symbol:'BTC-USDT-SWAP',period,now:()=>now,
  later:(fn,ms)=>{timers.set(++id,{fn,ms});return id;},cancel:id=>timers.delete(id),repeat:(fn,ms)=>{intervals.set(++id,{fn,ms});return id;},clearRepeat:id=>intervals.delete(id),
  socket:()=>{const s={readyState:0,sent:[],send(v){this.sent.push(v);},close(){this.readyState=3;this.onclose?.();}};sockets.push(s);return s;},
  request:(url,options)=>new Promise(resolve=>requests.push({url,options,resolve})),onChange:s=>states.push(s),onReconnect:()=>repairs++});
 const boundary=Math.floor(NOW/PERIODS[period])*PERIODS[period];
 const value=(close=11)=>({symbol:'BTC-USDT-SWAP',bar:period,native_bar:nativeBar(period),now_ms:now,closed_candles:[obj(boundary-PERIODS[period],true)],forming_candle:obj(boundary,false,close)});
 const message=(rows,s=sockets.at(-1),symbol='BTC-USDT-SWAP',channel='candle'+nativeBar(period))=>s.onmessage?.({data:JSON.stringify({arg:{instId:symbol,channel},data:rows})});
 const run=(ms)=>{for(const [key,t] of [...timers])if(t.ms===ms){timers.delete(key);t.fn();}};
 const flush=async()=>{for(let i=0;i<12;i++)await Promise.resolve();};
 return {feed,sockets,requests,states,timers,intervals,value,message,run,flush,boundary,advance:ms=>now+=ms,repairs:()=>repairs};
}
test('12H subscribes to UTC candles and ignores the Hong Kong half-day channel',()=>{
 const f=fixture('12H');f.feed.start();const s=f.sockets[0];s.readyState=1;s.onopen();
 assert.equal(JSON.parse(s.sent[0]).args[0].channel,'candle12Hutc');
 f.message([raw(f.boundary,'0','12')]);f.run(100);
 f.message([raw(f.boundary,'0','9')],s,'BTC-USDT-SWAP','candle12H');f.run(100);
 assert.equal(f.states.at(-1).bars.at(-1).close,12);f.feed.stop();
});
test('socket supersedes slow REST, ignores other identities and cannot reopen a confirmed candle',async()=>{
 const f=fixture();f.feed.start();const s=f.sockets[0];s.readyState=1;s.onopen();
 assert.deepEqual(JSON.parse(s.sent[0]),{op:'subscribe',args:[{channel:'candle1H',instId:'BTC-USDT-SWAP'}]});
 f.message([raw(f.boundary,'0','13')]);f.run(100);
 f.requests[0].resolve({ok:true,json:async()=>({...f.value(10),now_ms:NOW-3600000})});await f.flush();f.run(100);
 assert.equal(f.states.at(-1).bars.at(-1).close,13);
 f.message([raw(f.boundary,'0','9')],s,'ETH-USDT-SWAP');f.run(100);assert.equal(f.states.at(-1).bars.at(-1).close,13);
 f.advance(3600000);f.message([raw(f.boundary,'1','12'),raw(f.boundary+3600000)]);f.run(100);
 f.message([raw(f.boundary,'0','9')]);f.run(100);assert.equal(f.states.at(-1).bars.find(b=>b.time===f.boundary).close,12);
 f.feed.stop();assert.equal(s.readyState,3);assert.equal(f.timers.size,0);assert.equal(f.intervals.size,0);
});
test('REST fallback, reconnect repair, daily UTC, cancellation and stale warning',async()=>{
 const f=fixture('1D');f.feed.start();const first=f.sockets[0];first.readyState=1;first.onopen();
 assert.equal(JSON.parse(first.sent[0]).args[0].channel,'candle1Dutc');
 assert.match(f.requests[0].url,/period=1D/);assert.equal(f.requests[0].options.method,undefined);
 f.requests[0].resolve({ok:true,json:async()=>f.value()});await f.flush();f.run(100);assert.equal(f.states.at(-1).bars.at(-1).confirmed,false);
 f.advance(6000);for(const t of f.intervals.values())t.fn();assert.equal(f.requests.length,2);
 first.close();f.run(1000);const second=f.sockets[1];second.readyState=1;second.onopen();assert.equal(f.repairs(),1);
 f.advance(16000);for(const t of f.intervals.values())t.fn();assert.match(f.states.at(-1).error,/延迟/);
 const late=second.onmessage;f.feed.stop();assert.equal(f.requests[1].options.signal.aborted,true);
 const n=f.states.length;late({data:JSON.stringify({arg:{instId:'BTC-USDT-SWAP',channel:'candle1Dutc'},data:[raw(f.boundary)]})});
 f.requests[1].resolve({ok:true,json:async()=>f.value()});await f.flush();assert.equal(f.states.length,n);assert.equal(f.timers.size,0);assert.equal(f.intervals.size,0);
});

test('identical snapshots and heartbeats skip redraws while price, volume and stale status still update',()=>{
 const f=fixture();f.feed.start();const s=f.sockets[0];s.readyState=1;s.onopen();f.message([raw(f.boundary)]);f.run(100);const count=f.states.length;
 f.message([raw(f.boundary)]);f.run(100);for(const t of f.intervals.values())t.fn();assert.equal(f.states.length,count);
 const changed=raw(f.boundary);changed[5]='5';f.message([changed]);f.run(100);assert.equal(f.states.length,count+1);
 f.advance(16000);for(const t of f.intervals.values())t.fn();assert.match(f.states.at(-1).error,/延迟/);f.feed.stop();
});

test('2m polls validated aggregated REST bars without requesting an unsupported exchange channel',async()=>{
 const f=fixture('2m');f.feed.start();assert.equal(f.sockets.length,0);
 f.requests[0].resolve({ok:true,json:async()=>f.value()});await f.flush();f.run(100);
 assert.equal(f.states.at(-1).bars.at(-1).time,f.boundary);
 for(const t of f.intervals.values())t.fn();assert.equal(f.requests.length,2);
 f.feed.stop();assert.equal(f.requests[1].options.signal.aborted,true);
});
