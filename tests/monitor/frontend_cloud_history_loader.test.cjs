const test=require('node:test');
const assert=require('node:assert/strict');
const {create}=require('../../yoyo/monitor/static/cloud-history-loader.js');
const symbol='NEIRO-USDT-SWAP',step=43200000,t=Date.parse('2026-09-16T00:00:00Z');
const candle=time=>({time,open:10,high:12,low:9,close:11,volume:5,confirmed:true});
const value=rows=>({symbol,bar:'12H',native_bar:'12Hutc',candles:rows,last_close_ms:t+step});
const response=v=>({ok:true,json:async()=>v});
test('all-history discovery reaches the API boundary, reconstructs Monday mode and reports missing weeks',async()=>{
 const calls=[],states=[],updates=[];
 const loader=create({symbol,rangeMode:'monday',changed:v=>updates.push(v),status:v=>states.push(v),request:async url=>{
  calls.push(url);const u=new URL(url,'http://local');
  if(u.pathname.endsWith('/candles'))return response(value([candle(t)]));
  if(u.pathname.endsWith('/history')&&u.searchParams.has('before_ms')){
   const before=Number(u.searchParams.get('before_ms'));
   return response({...value(before===t?[candle(t-7*86400000)]:[]),state:before===t?'ready':'exhausted',has_more:before===t,next_before_ms:t-7*86400000,end_reason:before===t?null:'exchange_api_boundary'});
  }
  const week=u.searchParams.get('week');assert.equal(u.searchParams.get('range_mode'),'monday');
  return response({symbol,week,state:week==='2026-09-07'?'missing_monday':'ready',complete:true,items:[{id:week,symbol,range_mode:'monday'}]});
 }});
 await loader.start();assert.equal(calls.filter(u=>u.includes('before_ms')).length,2);
 assert.equal(states.at(-1).complete,false);assert.equal(states.at(-1).failedWeeks,1);assert.equal(updates.at(-1).items.length,1);
 assert.equal(states.at(-1).from_ms,t-7*86400000);
});
test('cancelling ignores late data and does not publish another symbol or falsely complete',async()=>{
 let release,signal;const updates=[],states=[];
 const loader=create({symbol,status:s=>states.push(s),changed:v=>updates.push(v),request:async(_,options)=>{signal=options.signal;await new Promise(r=>release=r);return response(value([candle(t)]));}});
 const pending=loader.start();loader.cancel();release();await pending;
 assert.equal(signal.aborted,true);assert.equal(updates.length,0);assert.equal(states.at(-1).stage,'paused');assert.equal(states.at(-1).complete,false);
});
test('complete empty reference history is distinct from failed history',async()=>{
 const loader=create({symbol,request:async url=>{
  const u=new URL(url,'http://local');
  if(u.pathname.endsWith('/candles'))return response(value([candle(t)]));
  if(u.searchParams.has('before_ms'))return response({...value([]),state:'exhausted',has_more:false,end_reason:'exchange_api_boundary'});
  return response({symbol,week:u.searchParams.get('week'),state:'ready',complete:true,items:[]});
 }});await loader.start();assert.equal(loader.getState().complete,true);assert.equal(loader.getState().weeksDone,1);
});
