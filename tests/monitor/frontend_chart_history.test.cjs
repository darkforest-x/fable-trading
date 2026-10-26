'use strict';
const test=require('node:test'),assert=require('node:assert/strict');
const H=require('../../yoyo/monitor/static/chart-history.js');
const {withWeeks}=require('../../yoyo/monitor/static/monday-fib-chart.js');
const T=require('../../yoyo/vision_research/static/chart-tools.js');
const start=Date.parse('2026-10-19T04:00:00Z'),step=300000;
const candle=time=>({time,open:10,high:12,low:9,close:11,volume:2});
const snapshot=(symbol='BTC-USDT-SWAP')=>({symbol,bar:'5m',candles:Array.from({length:720},(_,i)=>candle(start+i*step))});
const week=(symbol='BTC-USDT-SWAP')=>({symbol,timezone:'America/New_York',week:'2026-10-19',state:'ready',start_ms:start,end_ms:start+86400000,expires_ms:start+7*86400000,high_ms:start+step,low_ms:start+2*step,high:12,low:9,upper:13.854,lower:7.146});
const reply=v=>({ok:true,json:async()=>v});
test('BTC ETH XAU retain prepended history through rolling snapshots; alts stay at 720',async()=>{
 for(const symbol of ['BTC-USDT-SWAP','ETH-USDT-SWAP','XAU-USDT-SWAP','SOL-USDT-SWAP']){
  let calls=0,value;
  const h=H.create({symbol,period:'5m',changed:v=>value=v,request:async()=>{calls++;return reply({symbol,bar:'5m',native_bar:'5m',state:'ready',has_more:true,next_before_ms:start-300*step,candles:Array.from({length:300},(_,i)=>candle(start+(i-300)*step))});}});
  h.merge(snapshot(symbol));await h.older();
  assert.equal(calls,H.full(symbol)?1:0);
  const fresh=snapshot(symbol);fresh.candles.push(candle(start+720*step));fresh.candles.shift();
  const merged=h.merge(fresh);assert.equal(merged.candles.length,H.full(symbol)?1021:720);
  if(H.full(symbol)){assert.equal(value.candles[0].time,start-300*step);assert.equal(merged.candles.at(-1).time,start+720*step);}
  h.pause();
 }
});
test('identity mismatch or an empty retriable page keeps existing candles and explicit retry',async()=>{
 let status;
 const h=H.create({symbol:'BTC-USDT-SWAP',period:'5m',status:v=>status=v,request:async()=>reply({symbol:'ETH-USDT-SWAP',bar:'5m',native_bar:'5m',state:'ready',has_more:true,candles:[]})});
 h.merge(snapshot());await h.older();assert.equal(status.retry,true);assert.equal(h.merge(snapshot()).candles.length,720);h.pause();
});
test('pause aborts pages and ignores retired responses even after resume',async()=>{
 let resolve,signal;const draws=[];
 const h=H.create({symbol:'BTC-USDT-SWAP',period:'5m',changed:v=>draws.push(v),request:(url,opts)=>{signal=opts.signal;return new Promise(r=>resolve=r);}});
 h.merge(snapshot());const request=h.older();h.pause();assert.equal(signal.aborted,true);h.resume();
 resolve(reply({symbol:'BTC-USDT-SWAP',bar:'5m',native_bar:'5m',state:'exhausted',has_more:false,candles:[candle(start-step)]}));await request;
 assert.equal(draws.length,0);h.pause();
});
test('all coins load weekly ranges independently of candle-history eligibility',async()=>{
 const symbol='SOL-USDT-SWAP',urls=[];let value;
 const h=H.create({symbol,period:'5m',changed:v=>value=v,request:async url=>{urls.push(url);return reply({symbol,timezone:'America/New_York',weeks:[week(symbol)]});}});
 h.merge(snapshot(symbol));await h.weekly(start,start+2*86400000);assert.equal(value.weekly_ranges.length,1);
 await h.weekly(start,start+2*86400000);assert.equal(urls.length,1);assert.match(urls[0],/weekly-levels/);h.pause();
});
test('each historical range has its own expiry and current anchors are deduplicated',()=>{
 const a=week(),b={...a,week:'2026-10-12',start_ms:start-7*86400000,end_ms:start-6*86400000,expires_ms:start,high_ms:start-7*86400000+step,low_ms:start-7*86400000+2*step};
 const value=withWeeks({symbol:a.symbol,weekly_ranges:[a,b]},a,a.week);
 assert.equal(value.levels.length,18);
 assert.ok(value.levels.filter(l=>l.week===b.week).every(l=>l.historical&&l.end_ms===start&&l.start_ms<start));
 assert.ok(value.levels.filter(l=>l.week===a.week).every(l=>!l.historical&&l.end_ms===a.expires_ms));
 assert.ok(value.levels.filter(l=>l.week===b.week).every(l=>!l.inlinePriceVisible&&!l.inlineNameVisible));
 assert.ok(value.levels.filter(l=>l.week===a.week).every(l=>l.inlinePriceVisible&&l.inlineNameVisible));
 // While Monday forms, the selected last-completed range retains its labels.
 const pending=withWeeks({symbol:a.symbol,weekly_ranges:[a,b]},a,'2026-10-26');
 assert.equal(pending.levels.filter(l=>l.inlinePriceVisible).length,9);
 assert.ok(pending.levels.filter(l=>l.inlinePriceVisible).every(l=>l.week===a.week));
 const next={...a,week:'2026-10-26',start_ms:start+7*86400000,end_ms:start+8*86400000,expires_ms:start+14*86400000};
 const rolled=withWeeks({symbol:a.symbol,weekly_ranges:[a,b]},next,next.week);
 assert.equal(rolled.levels.length,27);
 assert.ok(rolled.levels.filter(l=>l.week!==next.week).every(l=>!l.inlinePriceVisible&&!l.inlineNameVisible));
});
test('year-spanning ranges and million-bar precision avoid argument-stack limits',()=>{
 const rows=Array.from({length:150000},(_,i)=>({time:i*60,low:.00012,high:.00015,close:.00014}));
 assert.equal(T.priceFormat(rows).precision,8);
 const times=rows.map(r=>r.time),saved=T.anchor({from:100,to:149000},times);
 assert.deepEqual(T.restore(saved,[-120,-60,...times]),{from:102,to:149002});
 assert.deepEqual(H.weekKeys(Date.parse('2026-03-02T05:00Z'),Date.parse('2026-03-16T04:00Z')),['2026-03-16','2026-03-09','2026-03-02']);
});

test('12H historical pages require native UTC identity and half-day timestamps',async()=>{
 const symbol='BTC-USDT-SWAP',boundary=Date.parse('2026-10-19T00:00:00Z'),half=43200000;
 for(const native of ['12Hutc','12H']){
  let value,status;
  const h=H.create({symbol,period:'12H',changed:v=>value=v,status:v=>status=v,request:async url=>{
   assert.match(url,/period=12H/);
   return reply({symbol,bar:'12H',native_bar:native,state:'ready',has_more:true,next_before_ms:boundary-half,candles:[candle(boundary-half)]});
  }});
  h.merge({symbol,bar:'12H',candles:[candle(boundary)]});await h.older();
  if(native==='12Hutc'){assert.equal(value.candles.length,2);assert.equal(status.retry,false);}
  else{assert.equal(value,undefined);assert.equal(status.retry,true);}
  h.pause();
 }
});
