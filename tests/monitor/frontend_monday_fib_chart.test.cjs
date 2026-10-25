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
 for(const label of ['上 0.705','上 0.618','下 0.618','下 0.705'])assert.equal(a.find(x=>x.label===label).inlinePriceVisible,false,'dense optional lines keep their existing quiet presentation');
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

test('native timeframe is validated and daily candles retain context beyond NY Monday',()=>{
 const day=Date.parse('2026-10-18T00:00:00Z');
 const d={...context(),bar:'1D',native_bar:'1Dutc',last_close_ms:day+4*86400000,candles:[bar(day),bar(day+86400000)]};
 assert.equal(prepare(d,row,'2026-10-19','1D').candles.length,2);
 assert.deepEqual(prepare(d,row,'2026-10-19','1D').levels,levels(row));
 assert.throws(()=>prepare(d,row,'2026-10-19','5m'),/周期/);
 assert.throws(()=>prepare({...d,native_bar:'1D'},row,'2026-10-19','1D'),/周期/);
 assert.throws(()=>prepare({...d,last_close_ms:day+86400000},row,'2026-10-19','1D'),/K 线数据/);
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
