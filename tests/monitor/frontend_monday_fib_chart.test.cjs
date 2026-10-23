'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const {levels,prepare}=require('../../yoyo/monitor/static/monday-fib-chart.js');
const start=Date.parse('2026-10-19T04:00:00Z');
const row={symbol:'BTC-USDT-SWAP',high:83149.8,low:81166,upper:84375.7884,lower:79940.0116};
const bar=time=>({time,open:82000,high:82100,low:81900,close:82050,volume:10});
const context=(symbol=row.symbol)=>({symbol,timezone:'America/New_York',monday:{start_ms:start,end_ms:start+86400000},last_close_ms:start+1800000,candles:[bar(start-900000),bar(start),bar(start+900000)]});
test('chart uses monitor anchors and the exact mirrored nine price levels',()=>{
 const a=levels(row);assert.equal(a.length,9);assert.equal(a[0].price,row.upper);assert.equal(a[8].price,row.lower);
 for(const label of ['上 1.618','周一高','周一低','下 1.618'])assert.equal(a.find(x=>x.label===label).lineStyle,0);
 for(const [label,value] of [['中点 0.5',82157.9],['上 0.618',82391.9884],['下 0.618',81923.8116],['上 0.705',82564.579],['下 0.705',81751.221]])assert.ok(Math.abs(a.find(x=>x.label===label).price-value)<1e-7);
 assert.equal(levels({...row,high:null}).length,0);
});
test('chart crops to this NY Monday, keeps closed candles, and rejects wrong identity or week',()=>{
 const c=context(),p=prepare(c,row,'2026-10-19');assert.equal(p.candles.length,2);assert.equal(c.candles.length,3);assert.equal(p.levels.length,9);
 assert.throws(()=>prepare(context('ETH-USDT-SWAP'),row,'2026-10-19'),/币种/);
 assert.throws(()=>prepare(c,row,'2026-10-12'),/周一区间/);
 assert.throws(()=>prepare({...c,last_close_ms:start+900000},row,'2026-10-19'),/K 线数据/);
 assert.throws(()=>prepare({...c,candles:[bar(start),bar(start)]},row,'2026-10-19'),/K 线数据/);
});
test('empty data never invents candles or reuses another symbol',()=>{
 const c={symbol:row.symbol,timezone:'America/New_York',loading:true,candles:[]};assert.deepEqual(prepare(c,row,'2026-10-19').candles,[]);
 assert.throws(()=>prepare({...c,symbol:'ETH-USDT-SWAP'},row,'2026-10-19'),/币种/);
});
test('row chart makes only selected GETs, ignores retired replies and aborts on close',async()=>{
 const pending=[],draws=[],rendered=[],timers=new Map();let n=0;
 const makeHost=()=>{const state={textContent:''},mount={},button={addEventListener(){}};return {innerHTML:'',querySelector(s){return s==='[role=status]'?state:s==='.mf-chart-mount'?mount:button;}};};
 const sandbox={document:{hidden:false},window:{SpikeRangeChart:{mount:(host,data)=>{draws.push(data.symbol);rendered.push(data);},unmount(){}}},Intl,Date,AbortController,fetch:(url,options)=>new Promise(resolve=>pending.push({url,options,resolve})),setTimeout(fn,ms){timers.set(++n,{fn,ms});return n;},clearTimeout(i){timers.delete(i);}};
 vm.runInNewContext(fs.readFileSync('yoyo/monitor/static/monday-fib-chart.js','utf8'),sandbox);
 const api=sandbox.window.SpikeMondayFibChart,first=makeHost(),second=makeHost();api.show(first,row,'2026-10-19');
 assert.equal(pending.length,1);assert.equal(pending[0].options.method,undefined);assert.match(pending[0].url,/symbol=BTC-USDT-SWAP/);
 api.show(second,{...row,symbol:'ETH-USDT-SWAP'},'2026-10-19');assert.equal(pending[0].options.signal.aborted,true);
 pending[0].resolve({ok:true,json:async()=>context()});pending[1].resolve({ok:true,json:async()=>context('ETH-USDT-SWAP')});
 for(let i=0;i<12;i++)await Promise.resolve();assert.deepEqual(draws,['ETH-USDT-SWAP']);assert.equal(rendered.at(-1).levels.length,4);
 api.setInternal(true);assert.equal(rendered.at(-1).levels.length,9);api.setInternal(false);assert.equal(rendered.at(-1).levels.length,4);
 api.show(second,{...row,symbol:'ETH-USDT-SWAP'},'2026-10-19');assert.equal(pending.length,2);
 api.hide();assert.equal(timers.size,0);
});
