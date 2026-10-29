'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const {levels,prepare}=require('../../yoyo/monitor/static/monday-fib-chart.js');
const start=Date.parse('2026-10-19T04:00:00Z');
const row={symbol:'BTC-USDT-SWAP',high:83149.8,low:81166,upper:84375.7884,lower:79940.0116};
const anchorReply=url=>({...row,symbol:new URL(url,'http://test').searchParams.get('symbol'),timezone:'America/New_York',week:'2026-10-19',state:'ready',start_ms:start,end_ms:start+86400000,expires_ms:start+7*86400000,high_ms:start+3600000,low_ms:start+7200000});
const bar=time=>({time,open:82000,high:82100,low:81900,close:82050,volume:10});
const context=(symbol=row.symbol)=>({symbol,bar:'15m',timezone:'America/New_York',monday:{start_ms:start,end_ms:start+86400000},last_close_ms:start+1800000,candles:[bar(start-900000),bar(start),bar(start+900000)]});
test('chart uses monitor anchors and the exact mirrored nine price levels',()=>{
 const a=levels(row);assert.equal(a.length,9);assert.equal(a[0].price,row.upper);assert.equal(a[8].price,row.lower);
 for(const label of ['上 1.618','周一高','周一低','下 1.618'])assert.equal(a.find(x=>x.label===label).lineStyle,0);
 assert.ok(a.every(x=>x.axisLabelVisible===false),'reference levels must not widen the price axis');
 for(const label of ['上 1.618','周一高','周一低','下 1.618'])assert.equal(a.find(x=>x.label===label).inlinePriceVisible,true);
 for(const label of ['上 0.705','上 0.618','下 0.618','下 0.705'])assert.equal(a.find(x=>x.label===label).inlinePriceVisible,true,'optional Fibonacci lines retain their numeric prices');
 for(const [label,value] of [['中点 0.5',82157.9],['上 0.618',82391.9884],['下 0.618',81923.8116],['上 0.705',82564.579],['下 0.705',81751.221]])assert.ok(Math.abs(a.find(x=>x.label===label).price-value)<1e-7);
 assert.equal(levels({...row,high:null}).length,0);
});
test('chart retains native history, keeps closed candles, and rejects wrong identity or week',()=>{
 const c=context(),p=prepare(c,row,'2026-10-19');assert.equal(p.candles.length,3);assert.equal(c.candles.length,3);assert.equal(p.levels.length,9);
 assert.throws(()=>prepare(context('ETH-USDT-SWAP'),row,'2026-10-19'),/币种/);
 assert.throws(()=>prepare(c,row,'2026-10-12'),/周一区间/);
 assert.throws(()=>prepare({...c,last_close_ms:start+900000},row,'2026-10-19'),/K 线数据/);
 assert.throws(()=>prepare({...c,candles:[bar(start),bar(start)]},row,'2026-10-19'),/K 线数据/);
});
test('empty data never invents candles or reuses another symbol',()=>{
 const c={symbol:row.symbol,bar:'15m',timezone:'America/New_York',loading:true,candles:[]};assert.deepEqual(prepare(c,row,'2026-10-19').candles,[]);
 assert.throws(()=>prepare({...c,symbol:'ETH-USDT-SWAP'},row,'2026-10-19'),/币种/);
});
test('row chart makes only selected GETs, ignores retired replies and aborts on close',async()=>{
 const pending=[],draws=[],rendered=[],views=[],timers=new Map();let n=0;
 const makeHost=()=>{const state={textContent:''},mount={},button={addEventListener(){}};return {innerHTML:'',querySelector(s){return s==='[role=status]'?state:s==='.mf-chart-mount'?mount:button;}};};
 let range={from:10,to:50},autoScale=false,fits=0;
 const instance={rows:Array(400),chart:{priceScale:()=>({applyOptions:o=>autoScale=o.autoScale}),timeScale:()=>({getVisibleLogicalRange:()=>range,setVisibleLogicalRange:r=>range=r,fitContent:()=>fits++})}};
 const sandbox={document:{hidden:false},window:{SpikeRangeChart:{mount:(host,data,view)=>{views.push(view);draws.push(data.symbol);rendered.push(data);return instance;},unmount(){}}},Intl,Date,AbortController,fetch:(url,options)=>url.includes('/levels?')?Promise.resolve({ok:true,json:async()=>anchorReply(url)}):new Promise(resolve=>pending.push({url,options,resolve})),setTimeout(fn,ms){timers.set(++n,{fn,ms});return n;},clearTimeout(i){timers.delete(i);}};
 vm.runInNewContext(fs.readFileSync('yoyo/monitor/static/monday-fib-chart.js','utf8'),sandbox);
 const api=sandbox.window.SpikeMondayFibChart,first=makeHost(),second=makeHost();api.show(first,row,'2026-10-19');
 assert.equal(pending.length,1);assert.equal(pending[0].options.method,undefined);assert.match(pending[0].url,/symbol=BTC-USDT-SWAP/);
 api.show(second,{...row,symbol:'ETH-USDT-SWAP'},'2026-10-19');assert.equal(pending[0].options.signal.aborted,true);
 pending[0].resolve({ok:true,json:async()=>context()});pending[1].resolve({ok:true,json:async()=>context('ETH-USDT-SWAP')});
 for(let i=0;i<12;i++)await Promise.resolve();assert.ok(draws.length&&draws.every(s=>s==='ETH-USDT-SWAP'));assert.equal(rendered.at(-1).levels.length,4);
 assert.equal(views.at(-1).ma,false);api.setMa(true);assert.equal(views.at(-1).ma,true);assert.deepEqual(range,{from:10,to:50});api.setMa(false);assert.equal(views.at(-1).ma,false);
 api.setInternal(true);assert.equal(rendered.at(-1).levels.length,9);api.setInternal(false);assert.equal(rendered.at(-1).levels.length,4);
 api.show(second,{...row,symbol:'ETH-USDT-SWAP'},'2026-10-19');assert.equal(pending.length,2);
 api.latest();assert.equal(range.to-range.from,40);assert.equal(range.to,409);assert.equal(autoScale,true);
 api.zoom(.8);assert.equal(range.to-range.from,32);assert.equal(range.to,409);
 api.zoom(1.25);assert.equal(range.to-range.from,40);api.zoom(Infinity);assert.equal(range.to-range.from,40);
 api.fit();assert.equal(fits,1);assert.equal(pending.length,2);assert.ok(draws.every(s=>s==='ETH-USDT-SWAP'));
 api.pause();assert.equal(timers.size,0);
 api.show(second,{...row,symbol:'ETH-USDT-SWAP'},'2026-10-19');assert.equal(pending.length,2);assert.equal(timers.size,1);
 api.hide();assert.equal(timers.size,0);
});

for(const [period,step] of [['12H',43200000],['1D',86400000]])test(`${period} validates native UTC identity and retains context beyond NY Monday`,()=>{
 const day=Date.parse('2026-10-18T00:00:00Z');
 const d={...context(),bar:period,native_bar:period+'utc',last_close_ms:day+4*step,candles:[bar(day),bar(day+step)]};
 assert.equal(prepare(d,row,'2026-10-19',period).candles.length,2);
 assert.deepEqual(prepare(d,row,'2026-10-19',period).levels,levels(row));
 assert.throws(()=>prepare(d,row,'2026-10-19','5m'),/周期/);
 assert.throws(()=>prepare({...d,native_bar:period},row,'2026-10-19',period),/周期/);
 assert.throws(()=>prepare({...d,last_close_ms:day+step},row,'2026-10-19',period),/K 线数据/);
});
test('rapid timeframe switching aborts old loads and keeps responses and cache isolated',async()=>{
 const pending=[],draws=[];const state={textContent:''},mount={},button={addEventListener(){}};
 const host={innerHTML:'',replaceChildren(){},querySelector:s=>s==='[role=status]'?state:s==='.mf-chart-mount'?mount:button};
 const sandbox={document:{hidden:false},window:{SpikeRangeChart:{mount:(h,d)=>{if(d.candles.length)draws.push(d.bar);},unmount(){}}},Intl,Date,AbortController,fetch:(url,options)=>url.includes('/levels?')?Promise.resolve({ok:true,json:async()=>anchorReply(url)}):new Promise(resolve=>pending.push({url,options,resolve})),setTimeout:()=>1,clearTimeout(){}};
 vm.runInNewContext(fs.readFileSync('yoyo/monitor/static/monday-fib-chart.js','utf8'),sandbox);
 const api=sandbox.window.SpikeMondayFibChart;
 api.show(host,row,'2026-10-19',{period:'15m'});api.show(host,row,'2026-10-19',{period:'5m'});
 assert.equal(pending[0].options.signal.aborted,true);assert.match(pending[1].url,/period=5m/);
 pending[1].resolve({ok:true,json:async()=>({...context(),bar:'5m'})});
 pending[0].resolve({ok:true,json:async()=>context()});for(let i=0;i<12;i++)await Promise.resolve();
 assert.ok(draws.length&&draws.every(s=>s==='5m'));
 api.show(host,row,'2026-10-19',{period:'1H'});assert.match(pending[2].url,/period=1H/);
 api.show(host,row,'2026-10-19',{period:'5m'});assert.ok(draws.every(s=>s==='5m'));assert.equal(pending[2].options.signal.aborted,true);api.hide();
});

test('closed candle scheduling follows server boundaries and retries incomplete tails',()=>{
 const {refreshDelay,PERIODS}=require('../../yoyo/monitor/static/monday-fib-chart.js');
 for(const [period,step] of Object.entries(PERIODS)){
  const boundary=Math.floor(start/step)*step,now=boundary+5000;
  const ready={...context(),now_ms:now,last_close_ms:boundary};
  assert.equal(refreshDelay(ready,period),step-5000+1500);
  assert.equal(refreshDelay({...ready,last_close_ms:boundary-step},period),3000);
  assert.equal(refreshDelay({...ready,loading:true},period),1500);
  assert.equal(refreshDelay({...ready,error:'offline'},period),10000);
 }
});

test('late anchor replies cannot paint a retired symbol and pending anchors draw no rays',async()=>{
 const pending=[],rendered=[];const state={textContent:''},mount={},button={addEventListener(){}};
 const host={innerHTML:'',replaceChildren(){},querySelector:s=>s==='[role=status]'?state:s==='.mf-chart-mount'?mount:button};
 const sandbox={document:{hidden:false},window:{SpikeRangeChart:{mount:(h,d)=>{rendered.push(d);},unmount(){}}},Intl,Date,AbortController,fetch:(url,options)=>new Promise(resolve=>pending.push({url,options,resolve})),setTimeout:()=>1,clearTimeout(){}};
 vm.runInNewContext(fs.readFileSync('yoyo/monitor/static/monday-fib-chart.js','utf8'),sandbox);
 const api=sandbox.window.SpikeMondayFibChart,settle=async()=>{for(let i=0;i<20;i++)await Promise.resolve();};
 api.show(host,row,'2026-10-19');
 const first=[...pending];first.find(p=>p.url.includes('/candles?')).resolve({ok:true,json:async()=>context()});await settle();
 assert.equal(rendered.at(-1).levels.length,0,'waiting for anchors must not draw chart-wide placeholder lines');
 api.show(host,{...row,symbol:'ETH-USDT-SWAP'},'2026-10-19');
 const second=pending.slice(2);
 second.find(p=>p.url.includes('/candles?')).resolve({ok:true,json:async()=>context('ETH-USDT-SWAP')});
 second.find(p=>p.url.includes('/levels?')).resolve({ok:true,json:async()=>anchorReply('/?symbol=ETH-USDT-SWAP')});await settle();
 const count=rendered.length;
 first.find(p=>p.url.includes('/levels?')).resolve({ok:true,json:async()=>anchorReply('/?symbol=BTC-USDT-SWAP')});await settle();
 assert.equal(rendered.length,count);assert.equal(rendered.at(-1).symbol,'ETH-USDT-SWAP');
 assert.equal(rendered.at(-1).levels.length,4);assert.equal(rendered.at(-1).levels[0].start_ms,start+3600000);api.hide();
});

function cachedChart(){
 const pending=[],restores=[],levelRestores=[],rendered=[],saved=[];
 const shared={candles:new Map(),levels:new Map(),saveCandles:v=>saved.push(v),saveLevels(){},restoreCandles:key=>new Promise(resolve=>restores.push({key,resolve})),restoreLevels:key=>new Promise(resolve=>levelRestores.push({key,resolve}))};
 const host=()=>{const state={textContent:''},caption={},mount={};return {innerHTML:'',replaceChildren(){},querySelector:s=>s==='[role=status]'?state:s==='.mf-chart-caption'?caption:s==='.mf-chart-mount'?mount:{addEventListener(){}}};};
 const sandbox={document:{hidden:false},window:{SpikePublicChartCache:shared,SpikeRangeChart:{mount:(h,d)=>rendered.push(d),unmount(){}}},Intl,Date,AbortController,setTimeout:()=>1,clearTimeout(){},fetch:(url,options)=>new Promise(resolve=>pending.push({url,options,resolve}))};
 vm.runInNewContext(fs.readFileSync('yoyo/monitor/static/monday-fib-chart.js','utf8'),sandbox);
 return {api:sandbox.window.SpikeMondayFibChart,pending,restores,levelRestores,rendered,saved,host,settle:async()=>{for(let i=0;i<20;i++)await Promise.resolve();}};
}
test('disk history paints before HTTP, remains marked stale through empty loading replies, and never enters persistence again',async()=>{
 const h=cachedChart(),anchors=anchorReply('/?symbol='+row.symbol);
 h.api.show(h.host(),row,'2026-10-19',{anchors});
 assert.equal(h.pending.length,1,'provided Monday anchors avoid a duplicate levels GET');
 h.restores[0].resolve(context());await h.settle();
 assert.equal(h.rendered.at(-1).candles.length,3);assert.equal(h.rendered.at(-1).stale,true);
 h.pending[0].resolve({ok:true,json:async()=>({...context(),candles:[],loading:true})});await h.settle();
 assert.equal(h.rendered.at(-1).candles.length,3);assert.equal(h.rendered.at(-1).stale,true);assert.equal(h.saved.length,0);h.api.hide();
});
test('late disk reads cannot overwrite fresh HTTP candles or a different selected coin',async()=>{
 const h=cachedChart(),host=h.host();h.api.show(host,row,'2026-10-19',{anchors:anchorReply('/?symbol='+row.symbol)});
 const fresh={...context(),candles:[{...bar(start),close:82080}]};
 h.pending[0].resolve({ok:true,json:async()=>fresh});await h.settle();const n=h.rendered.length;
 h.restores[0].resolve(context());await h.settle();assert.equal(h.rendered.length,n);assert.equal(h.rendered.at(-1).candles[0].close,82080);assert.ok(!h.rendered.at(-1).stale);
 h.api.show(host,{...row,symbol:'ETH-USDT-SWAP'},'2026-10-19');h.api.show(host,{...row,symbol:'SOL-USDT-SWAP'},'2026-10-19');const m=h.rendered.length;
 h.restores[1].resolve(context('ETH-USDT-SWAP'));await h.settle();assert.equal(h.rendered.length,m);assert.equal(h.rendered.at(-1).symbol,'SOL-USDT-SWAP');h.api.hide();
});
test('independent page charts reuse fresh public history without re-fetching it or sharing paper overlays',async()=>{
 const h=cachedChart(),a=h.api,b=a.create(),anchors=anchorReply('/?symbol='+row.symbol);
 a.show(h.host(),row,'2026-10-19',{anchors,trade:{id:5}});
 h.pending[0].resolve({ok:true,json:async()=>({...context(),now_ms:start+1800000})});await h.settle();
 b.show(h.host(),row,'2026-10-19',{anchors});
 assert.equal(h.pending.length,1);assert.equal(h.rendered.at(-1).candles.length,3);assert.equal(h.rendered.at(-1).paperTrade,null);
 a.hide();b.hide();
});
test('Monday restores exact cached anchor rays and retains them when their revalidation fails',async()=>{
 const h=cachedChart();h.api.show(h.host(),row,'2026-10-19');
 h.restores[0].resolve(context());h.levelRestores[0].resolve(anchorReply('/?symbol='+row.symbol));await h.settle();
 assert.equal(h.rendered.at(-1).levels.length,4);assert.equal(h.rendered.at(-1).levels[0].start_ms,start+3600000);
 h.pending.find(p=>p.url.includes('/levels?')).resolve({ok:false});
 h.pending.find(p=>p.url.includes('/candles?')).resolve({ok:true,json:async()=>context()});await h.settle();
 assert.equal(h.rendered.at(-1).levels.length,4);h.api.hide();
});

test('prior completed rays stop exactly at the new Monday while candles remain current',()=>{
 const previous=anchorReply('/levels?symbol=BTC-USDT-SWAP'),next=start+7*86400000;
 const c={...context(),monday:{start_ms:next,end_ms:next+86400000,expires_ms:next+7*86400000},last_close_ms:next+1800000,candles:[bar(next-900000),bar(next)]};
 const a={...previous,display_previous:true,current_week:'2026-10-26'};
 const result=prepare(c,{symbol:row.symbol,state:'developing'},'2026-10-26','15m',a);
 assert.equal(result.levels.length,9);assert.equal(result.level_window.start_ms,start);assert.equal(result.level_window.end_ms,next);
 assert.equal(result.monday.start_ms,next);assert.equal(result.monday.complete,false);assert.equal(result.candles.length,2);
 assert.throws(()=>prepare(c,row,'2026-10-26','15m',previous),/锚点/);
 assert.throws(()=>prepare(c,row,'2026-10-26','15m',{...a,expires_ms:next+1}),/锚点/);
});

test('a developing anchor response keeps live candles visible without a false error',async()=>{
 const messages={textContent:''},draws=[];const host={querySelector:s=>s==='[role=status]'?messages:{addEventListener(){}},replaceChildren(){}};
 const sandbox={document:{hidden:false},window:{SpikeRangeChart:{mount:(h,d)=>{draws.push(d);},unmount(){}}},Intl,Date,AbortController,setTimeout:()=>1,clearTimeout(){},fetch:async url=>({ok:true,json:async()=>url.includes('/levels?')?{symbol:row.symbol,week:'2026-10-19',state:'developing'}:context()})};
 vm.runInNewContext(fs.readFileSync('yoyo/monitor/static/monday-fib-chart.js','utf8'),sandbox);
 sandbox.window.SpikeMondayFibChart.show(host,{symbol:row.symbol,state:'developing'},'2026-10-19');
 for(let i=0;i<25;i++)await Promise.resolve();
 assert.ok(draws.length>0);assert.equal(draws.at(-1).levels.length,0);assert.equal(messages.textContent,'');sandbox.window.SpikeMondayFibChart.hide();
});

test('historical replay isolates source, trade, timeframe, cancellation and cache from live feeds',async()=>{
 const pending=[],drawn=[],timers=new Map();let sequence=0,feeds=0;
 const status={textContent:''},caption={},mount={};const host={innerHTML:'',replaceChildren(){},querySelector:s=>s==='[role=status]'?status:s==='.mf-chart-caption'?caption:s==='.mf-chart-mount'?mount:{addEventListener(){}}};
 const sandbox={document:{hidden:false},window:{SpikeMondayCandleFeed:{create(){feeds++;}},SpikeRangeChart:{mount:(h,d)=>drawn.push(d),unmount(){}}},Intl,Date,AbortController,URLSearchParams,
  setTimeout(fn,ms){timers.set(++sequence,{fn,ms});return sequence;},clearTimeout:i=>timers.delete(i),fetch:(url,options)=>new Promise(resolve=>pending.push({url,options,resolve}))};
 vm.runInNewContext(fs.readFileSync('yoyo/monitor/static/monday-fib-chart.js','utf8'),sandbox);
 const api=sandbox.window.SpikeMondayFibChart,settle=async()=>{for(let n=0;n<20;n++)await Promise.resolve();};
 const trade={id:1,symbol:'AAAUSDT',entry_ms:start,exit_ms:start+900000};
 const reply=(id,period='15m')=>({schema:1,track:'allin3',trade_id:id,symbol:'AAAUSDT',source:'Binance',bar:period,backtestTrade:{...trade,id},candles:[bar(start)],weekly_ranges:[]});
 api.showReplay(host,'allin3',trade);api.showReplay(host,'allin3',{...trade,id:2});assert.equal(pending[0].options.signal.aborted,true);
 pending[1].resolve({ok:true,json:async()=>reply(2)});pending[0].resolve({ok:true,json:async()=>reply(1)});await settle();
 assert.equal(drawn.at(-1).trade_id,2);assert.equal(feeds,0);assert.equal(timers.size,0);
 api.showReplay(host,'allin3',{...trade,id:2},{period:'5m'});api.pause();assert.equal(pending[2].options.signal.aborted,true);
 pending[2].resolve({ok:true,json:async()=>reply(2,'5m')});await settle();assert.equal(drawn.at(-1).candles.length,0);
 api.showReplay(host,'allin3',{...trade,id:2},{period:'5m'});assert.equal(pending.length,4);
 pending[3].resolve({ok:true,json:async()=>({...reply(2,'5m'),source:'OKX'})});await settle();assert.match(status.textContent,/不匹配/);
 api.retry();pending[4].resolve({ok:true,json:async()=>reply(2,'5m')});await settle();assert.equal(drawn.at(-1).bar,'5m');
 api.showReplay(host,'allin3',{...trade,id:2},{period:'15m'});assert.equal(pending.length,5);assert.equal(drawn.at(-1).bar,'15m');
 api.hide();assert.equal(timers.size,0);
});
