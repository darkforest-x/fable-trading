'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),vm=require('node:vm'),fs=require('node:fs');
const sandbox={window:{},module:{exports:{}},Intl,Date};vm.runInNewContext(fs.readFileSync('yoyo/monitor/static/range-chart.js','utf8'),sandbox);
const {render,price}=sandbox.module.exports,view={bars:192,range:true,profile:true};
test('shared range renderer uses the selected contract identity and small coin precision',()=>{
 const c={symbol:'W-USDT-SWAP',candles:[{time:0,open:.0021,high:.0023,low:.002,close:.0022,volume:10}],levels:[{kind:'range',label:'周一高',price:.0023}]};
 const html=render(c,view);assert.match(html,/OKX WUSDT.P/);assert.match(html,/周一高 0.0023/);assert.doesNotMatch(html,/BTC/);
 assert.equal(price(.00001234),'0.00001234');assert.equal(price(null),'—');
});
test('range layer filters hide overlays and empty snapshots never invent candles',()=>{
 const c={symbol:'ETH-USDT-SWAP',candles:[{time:0,open:10,high:12,low:9,close:11,volume:10}],levels:[{kind:'fib',label:'Fib 0.618',price:10}]};
 assert.doesNotMatch(render(c,view),/Fib/);assert.match(render(c,{...view,fib:true}),/Fib/);assert.equal(render({candles:[]},view),'');
});
test('interactive loading coalesces to the newest snapshot and never paints after unmount',async()=>{
 let finish;const ready=new Promise(resolve=>finish=resolve),paint=[];
 sandbox.window.SpikeChartTools={syncer:()=>()=>{},library:()=>ready};
 const api=sandbox.window.SpikeRangeChart,host={replaceChildren(){}};
 const instance=api.mount(host,{symbol:'BTC'},view);
 instance.create=function(){this.chart={remove(){}};};instance.draw=(data)=>paint.push(data.symbol);
 assert.equal(api.mount(host,{symbol:'ETH'},view),instance);
 finish();await new Promise(setImmediate);assert.deepEqual(paint,['ETH']);
 api.unmount(host);
 let release;const pending=new Promise(resolve=>release=resolve);sandbox.window.SpikeChartTools.library=()=>pending;
 const retired=api.mount(host,{symbol:'SOL'},view);retired.create=()=>assert.fail('retired chart created');
 api.unmount(host);release();await new Promise(setImmediate);assert.deepEqual(paint,['ETH']);
});
