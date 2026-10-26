'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const {initialSymbol,paperSymbol}=require('../../yoyo/monitor/static/startup-hints.js');
const {selection}=require('../../yoyo/monitor/static/monday-fib.js');
const source=fs.readFileSync('yoyo/monitor/static/startup-hints.js','utf8');
const items=[
 {symbol:'BTC-USDT-SWAP',touches:{upper:{source:'history',candle_ms:10,observed_ms:1000}}},
 {symbol:'ETH-USDT-SWAP',touches:{lower:{source:'live',observed_ms:20}}},
 {symbol:'ADA-USDT-SWAP',touches:{upper:{source:'live',observed_ms:20}}},
 {symbol:'SOL-USDT-SWAP',touches:{}},
];
const flush=async()=>{for(let i=0;i<20;i++)await Promise.resolve();};
function harness({hash='#monday-fib',hidden=false,period='1H'}={}){
 const calls=[],links=[],timers=new Map(),events={};let sequence=0;
 const window={location:{hash},localStorage:{getItem:()=>period},addEventListener:(k,fn)=>events[k]=fn};
 const document={hidden,addEventListener:(k,fn)=>events[k]=fn,createElement:()=>({}),head:{append:el=>links.push(el)},getElementById:()=>({textContent:JSON.stringify({'monday-fib.css':'css-v','monday-fib.js':'js-v','monday-list-model.js':'list-v','/vision-assets/vendor/lightweight-charts.standalone.production.js':'lib-v'})})};
 const sandbox={window,document,URLSearchParams,AbortController,Date,Intl,fetch:(url,options)=>new Promise(resolve=>calls.push({url,options,resolve})),setTimeout(fn,ms){timers.set(++sequence,{fn,ms});return sequence;},clearTimeout:id=>timers.delete(id)};
 vm.runInNewContext(source,sandbox);
 return {window,document,sandbox,calls,links,timers,events};
}
test('startup chooses the same event and pinned symbol as the monitor',()=>{
 for(const pinned of ['',...items.map(r=>r.symbol),'MISSING-USDT-SWAP'])
  assert.equal(initialSymbol(items,pinned),selection(items,pinned,!pinned)?.symbol);
 assert.equal(initialSymbol([]),null);
});
test('other pages and hidden documents make no speculative market request',()=>{
 for(const options of [{hash:'#signals'},{hidden:true}]){const h=harness(options);assert.equal(h.calls.length,0);assert.equal(h.links.length,0);}
});
test('home and retired market bookmarks warm only the paper snapshot before choosing its actual coin',()=>{
 for(const hash of ['','#market']){const h=harness({hash});assert.equal(h.calls.length,1);assert.equal(h.calls[0].url,'/api/monday-reclaim-paper');}
});
test('startup overlaps code and data, preserves receipt time, and consumes each response once',async()=>{
 const h=harness(),signal=new AbortController().signal;
 assert.equal(h.calls.length,1);assert.equal(h.calls[0].url,'/api/monday-fib/display');
 assert.equal(h.calls[0].options.cache,'no-store');assert.equal(h.calls[0].options.credentials,'same-origin');
 assert.equal(h.links.length,4);assert.ok(h.links.some(l=>/monday-list-model.js\?v=list-v/.test(l.href)));assert.match(h.links[3].href,/production.js\?v=lib-v/);
 h.calls[0].resolve({ok:true,json:async()=>({items})});await flush();
 assert.equal(h.calls.length,2);assert.match(h.calls[1].url,/symbol=ADA-USDT-SWAP&period=1H&wait_ms=8000$/);
 const packet=await h.window.SpikeStartup.take('monday-snapshot',signal);
 assert.deepEqual(packet.value.items,items);assert.ok(packet.receivedAt<=Date.now());
 assert.equal(h.window.SpikeStartup.take('monday-snapshot',signal),null);
 assert.equal(h.window.SpikeStartup.take('ADA-USDT-SWAP|5m',signal),null);
 const c=new AbortController();h.window.SpikeStartup.take('ADA-USDT-SWAP|1H',c.signal);c.abort();
 assert.equal(h.calls[1].options.signal.aborted,true);
});
test('leaving before snapshot completion cancels early work and cannot start a candle request',async()=>{
 const h=harness();h.window.location.hash='#market';h.events.hashchange();
 assert.equal(h.calls[0].options.signal.aborted,true);
 h.calls[0].resolve({ok:true,json:async()=>({items})});await flush();
 assert.equal(h.calls.length,1);assert.equal(h.window.SpikeStartup.take('monday-snapshot',new AbortController().signal),null);
});
test('chart reuses warmed candles while fetching the required Monday anchors',async()=>{
 const h=harness({period:'15m'}),draws=[];
 const now=Date.parse('2026-10-23T04:00:00Z'),start=Date.parse('2026-10-19T04:00:00Z');
 h.calls[0].resolve({ok:true,json:async()=>({items})});await flush();
 const value={symbol:'ADA-USDT-SWAP',bar:'15m',timezone:'America/New_York',monday:{start_ms:start},now_ms:now,last_close_ms:now,candles:[{time:now-900000,open:10,high:12,low:9,close:11}]};
 h.calls[1].resolve({ok:true,json:async()=>value});await flush();
 h.window.SpikeRangeChart={mount:(host,data)=>draws.push(data),unmount(){}};
 vm.runInNewContext(fs.readFileSync('yoyo/monitor/static/monday-fib-chart.js','utf8'),h.sandbox);
 const nodes={};const host={querySelector:s=>nodes[s]||(nodes[s]={addEventListener(){}})};
 h.window.SpikeMondayFibChart.show(host,{symbol:'ADA-USDT-SWAP',high:12,low:9},'2026-10-19');await flush();
 assert.equal(h.calls.filter(c=>c.url.includes('/candles?')).length,1);
 assert.equal(h.calls.length,3);assert.match(h.calls[2].url,/\/levels\?symbol=ADA-USDT-SWAP$/);
 assert.equal(draws.length,1);assert.equal(draws[0].symbol,'ADA-USDT-SWAP');
 h.window.SpikeMondayFibChart.hide();
});

test('paper warmup chooses exact funded latest fill, preserves pins and overlaps anchors with candles',async()=>{
 const p=(id,symbol,fill_ms,taken=1)=>({id,symbol,fill_ms,taken,notional:taken?100:0,side:1,entry:10});
 const data={tracks:{60:{open:[p(1,'BTC-USDT-SWAP',100),p(3,'ETH-USDT-SWAP',300,0)],closed:[p(2,'SOL-USDT-SWAP',200)]},5:{open:[p(4,'ADA-USDT-SWAP',400)]}}};
 assert.equal(paperSymbol(data,new URLSearchParams()),'SOL-USDT-SWAP');
 assert.equal(paperSymbol(data,new URLSearchParams('track=5')),'ADA-USDT-SWAP');
 assert.equal(paperSymbol(data,new URLSearchParams('trade=1')),'BTC-USDT-SWAP');
 assert.equal(paperSymbol(data,new URLSearchParams('trade=999')),null);
 assert.equal(paperSymbol(data,new URLSearchParams('symbol=DOGE-USDT-SWAP')),'DOGE-USDT-SWAP');
 const h=harness({hash:'#signals?pane=reclaim&period=1H'});h.calls[0].resolve({ok:true,json:async()=>data});await flush();
 assert.deepEqual(h.calls.map(c=>c.url),['/api/monday-reclaim-paper','/api/monday-fib/levels?symbol=SOL-USDT-SWAP','/api/monday-fib/candles?symbol=SOL-USDT-SWAP&period=1H&wait_ms=8000']);
 const warm=await h.window.SpikeStartup.take('paper-snapshot',new AbortController().signal);assert.equal(warm.value,data);
 h.window.location.hash='#signals?pane=records';h.events.hashchange();assert.ok(h.calls.slice(1).every(c=>c.options.signal.aborted));
});

test('cold named-account bookmarks warm only that account actual fill, not 60m or pending adds',()=>{
 const p=(id,symbol)=>({id,symbol,fill_ms:100,entry:10,side:1,taken:1,notional:100});
 const d={tracks:{60:{open:[p(1,'BTC-USDT-SWAP')]},v3:{open:[p(2,'SOL-USDT-SWAP')]},v3add:{open:[p(3,'SUI-USDT-SWAP')],pending_adds:[p(4,'DOGE-USDT-SWAP')]}}};
 assert.equal(paperSymbol(d,new URLSearchParams('track=v3')),'SOL-USDT-SWAP');
 assert.equal(paperSymbol(d,new URLSearchParams('track=v3add')),'SUI-USDT-SWAP');
 assert.equal(paperSymbol(d,new URLSearchParams('track=v3add&trade=4')),null);
 delete d.tracks.v3;assert.equal(paperSymbol(d,new URLSearchParams('track=v3')),null);
});


test('spike cold links warm only its funded row and never fall back when absent',()=>{
 const p={id:3001,symbol:'SOL-USDT-SWAP',fill_ms:100,entry:10,side:1,taken:1,notional:500};
 const d={tracks:{60:{open:[{...p,symbol:'BTC-USDT-SWAP'}]},spike:{open:[p,{...p,id:3002,symbol:'ETH-USDT-SWAP',taken:0,notional:0,fill_ms:200}]}}};
 assert.equal(paperSymbol(d,new URLSearchParams('track=spike')),'SOL-USDT-SWAP');
 assert.equal(paperSymbol(d,new URLSearchParams('track=spike&trade=3002')),'ETH-USDT-SWAP');
 delete d.tracks.spike;assert.equal(paperSymbol(d,new URLSearchParams('track=spike')),null);
});
