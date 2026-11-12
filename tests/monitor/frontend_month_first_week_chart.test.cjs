'use strict';
const test=require('node:test'), assert=require('node:assert/strict');
const ledger=require('../../yoyo/monitor/static/touch-ledger.js');
const paper=require('../../yoyo/monitor/static/monday-reclaim-paper.js');
const list=require('../../yoyo/monitor/static/monday-list-model.js');
const make=(coin,side,id,status)=>({symbol:coin+'-USDT-SWAP',side,occurred_ms:10000+id,candle_ms:9990,event_id:'2026-11|'+coin+'-USDT-SWAP|'+side,level:100,
  reclaims:Object.fromEntries(ledger.periods.map((p,i)=>[p,{status:status[i],reason:status[i]==='pending'?'candle_not_closed':null}]))});

test('real reclaim filters retain independently qualifying periods and both touches on one coin',()=>{
 const a=make('ABC','upper',1,['reclaimed','not_reclaimed','unavailable']);
 const b=make('ABC','lower',3,['pending','reclaimed','reclaimed']);
 const c=make('XYZ','lower',4,['unavailable','reclaimed','not_reclaimed']);
 const data=[a,b,c];
 assert.deepEqual(ledger.selectRows(data,'abc','all','time_desc'),[b,a]);
 assert.deepEqual(ledger.selectRows(data,'','4H','time_asc'),[b,c]);
 assert.deepEqual(ledger.selectRows(data,'','1H','time_desc'),[a]);
 assert.deepEqual(ledger.selectRows(data,'','12H','time_desc'),[b]);
 assert.deepEqual(data,[a,b,c]);
});
test('monthly event route retains exact symbol, month and side; Windows TV uses the selected interval',()=>{
 const a=make('ABC','upper',1,['reclaimed','not_reclaimed','unavailable']);
 const parsed=paper.route({ledger:'1',source:'month',reclaim:'12H',symbol:a.symbol,monthEvent:a.event_id});
 assert.equal(parsed.monthEvent,a.event_id);assert.equal(parsed.ledger,'1');
 assert.equal(paper.route({...parsed,symbol:'XYZ-USDT-SWAP'}).monthEvent,undefined);
 const html=ledger.rowHTML(a,{source:'month',period:'12H',selected:a.event_id,platform:'Win32',chartPeriod:'5m'});
 assert.match(html,/data-selected="true"/);assert.match(html,/spike-tradingview:.*timeframe=12H/);
 assert.match(html,/1H 已收回/);assert.match(html,/4H 未收回/);assert.match(html,/12H 缺数据/);
 for(const p of ['1m','2m','3m','5m','15m','1H','4H','12H'])assert.ok(list.tradingViewURL(a.symbol,'Windows',p).endsWith('timeframe='+p));
});

test('a local reclaim filter reuses the loaded month and closing cancels its scheduled poll',async()=>{
 const document={hidden:false,activeElement:null};
 const nodes=new Map();
 function element(selector){
   if(!nodes.has(selector))nodes.set(selector,{
     classList:{toggle(){}},setAttribute(){},children:[],scrollTop:0,innerHTML:'',getBoundingClientRect:()=>({top:0}),
     querySelector:element,querySelectorAll:()=>[],addEventListener(){},
   });
   return nodes.get(selector);
 }
 const timers=new Map(),calls=[];
 let id=0,total;
 const context={document,URLSearchParams,AbortController,module:{exports:{}},require:()=>list,
   setTimeout:(fn,delay)=>{timers.set(++id,{fn,delay});return id;},clearTimeout:key=>timers.delete(key),
   fetch:async(path,options)=>{
     calls.push([path,options.signal]);
     return {ok:true,json:async()=>({items:[make('ABC','lower',1,['reclaimed','not_reclaimed','pending'])],period:'2026-11',month:'2026-11'})};
   }};
 require('node:vm').runInNewContext(require('node:fs').readFileSync(require.resolve('../../yoyo/monitor/static/touch-ledger.js'),'utf8'),context);
 const book=context.module.exports.create({host:element('host'),controls:element('controls'),platform:'Win32',onCount:n=>total=n});
 const view={visible:true,events:true,source:'month',period:'all',q:'',sort:'time_desc',chartPeriod:'1H'};
 book.set(view);
 const [key,task]=[...timers][0];timers.delete(key);await task.fn();
 assert.equal(calls.length,1);assert.equal(total,1);
 book.set({...view,period:'4H'});assert.equal(total,0);
 book.set({...view,month:'2026-11',q:'ABC',sort:'time_asc'});assert.equal(total,1);
 assert.deepEqual([...timers.values()].map(t=>t.delay),[15000]);
 book.set(null);assert.equal(timers.size,0);
 await book.refresh();assert.equal(calls.length,1);
});
