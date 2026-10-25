'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const {latest,select,candleIndex,geometry}=require('../../yoyo/monitor/static/reclaim-chart.js');
const pos=(x={})=>({id:1,symbol:'SOL-USDT-SWAP',side:1,fill_ms:600001,entry:120,target:125,taken:1,notional:100,stage:'pending_confirm',...x});
const rows=[{time:300},{time:600},{time:900}],step=300000;
test('latest wallpaper follows fill time across open/closed funded trades, never exit order or shadows',()=>{
 const track={open:[pos(),pos({id:4,fill_ms:1200000,taken:0,notional:0})],closed:[pos({id:2,fill_ms:900001,exit_ms:1000000}),pos({id:3,fill_ms:300001,exit_ms:2000000})]};
 assert.equal(latest(track).id,2);assert.equal(latest({open:[pos({notional:0})]}),null);
 assert.equal(select(track,'1').id,1);const pinned=select(track,'1');assert.equal(select({open:[]},'1',pinned),pinned);
 assert.equal(select(track,'999'),null);assert.equal(latest({open:[pos({side:0})]}),null);
 const outsidePage=pos({id:8,fill_ms:2000000});
 assert.equal(latest({...track,latest_funded:outsidePage}),outsidePage);
 assert.equal(select({...track,latest_funded:outsidePage},'8'),outsidePage);
});
test('fill and exit markers belong to their actual containing candle; gaps never snap to a nearby candle',()=>{
 assert.equal(candleIndex(rows,899999,step),1);assert.equal(candleIndex(rows,900000,step),2);
 assert.equal(candleIndex(rows,1200000,step),-1);assert.equal(candleIndex([{time:300},{time:900}],600001,step),-1);
 for(const side of [1,-1])assert.deepEqual(geometry(pos({side}),rows,step,950000),{entry:1,exit:-1,from:600001,to:950000,stop:null});
 const closed=geometry(pos({stage:'closed',exit_ms:900002,be_from_ms:900000}),rows,step,1000000);
 assert.equal(closed.exit,2);assert.equal(closed.stop,900000);assert.equal(closed.to,900002);
});
test('confirmation cannot invent an initial stop or extend an ended position into the present',()=>{
 assert.equal(geometry(pos({be_from_ms:1200000}),rows,step,1000000).stop,null);
 assert.equal(geometry(pos({be_from_ms:300000}),rows,step,1000000).stop,null);
 assert.equal(geometry(pos({fill_ms:100000,stage:'closed',exit_ms:200000}),rows,step,1000000),null);
 assert.equal(geometry(pos({stage:'closed',exit_ms:500000}),rows,step,1000000),null);
});
test('trade drawings label both directions and activate the entry stop only at its backend timestamp',()=>{
 const sandbox={window:{SpikeChartTools:{axisPrice:String}},module:{exports:{}}};
 vm.runInNewContext(fs.readFileSync('yoyo/monitor/static/reclaim-chart.js','utf8'),sandbox);
 const texts=[],arrows=[];let path=[];
 const ctx={save(){},restore(){},beginPath(){path=[];},rect(){},clip(){},setLineDash(){},moveTo:(...v)=>path.push(v),lineTo:(...v)=>path.push(v),stroke(){},closePath:()=>arrows.push(path),fill(){},roundRect(){},fillRect(){},measureText:t=>({width:t.length*4}),fillText:t=>texts.push(t)};
 const owner={rows,chart:{timeScale:()=>({logicalToCoordinate:i=>i*100,width:()=>500})},series:{priceToCoordinate:p=>p*2}};
 for(const side of [1,-1]){
  const p=pos({side}),draw=new sandbox.module.exports.TradeDrawing(owner);draw.trade=p;draw.step=step;draw.shape=geometry(p,rows,step,950000);
  texts.length=0;arrows.length=0;draw.paint({useMediaCoordinateSpace:fn=>fn({context:ctx,mediaSize:{width:500,height:500}})});
  assert.ok(texts.includes(`${side===1?'多头':'空头'}开仓 · 120`));assert.ok(!texts.some(x=>x.includes('止损')));
  assert.equal(Math.sign(arrows[0][1][1]-arrows[0][0][1]),side);
  draw.trade={...p,be_from_ms:900001};draw.shape=geometry(draw.trade,rows,step,950000);texts.length=0;
  draw.paint({useMediaCoordinateSpace:fn=>fn({context:ctx,mediaSize:{width:500,height:500}})});
  assert.ok(texts.some(x=>x.includes('入场价止损已启用')));
 }
});
test('Monday chart factories own separate requests, canvases and timers',()=>{
 const pending=[],unmounts=[];
 const host=()=>({innerHTML:'',replaceChildren(){},querySelector(){return {addEventListener(){}};}});
 const sandbox={document:{hidden:false},window:{SpikeRangeChart:{mount(){},unmount:h=>unmounts.push(h)}},Intl,Date,AbortController,setTimeout:()=>1,clearTimeout(){},fetch:(url,options)=>new Promise(resolve=>pending.push({url,options,resolve}))};
 vm.runInNewContext(fs.readFileSync('yoyo/monitor/static/monday-fib-chart.js','utf8'),sandbox);
 const a=sandbox.window.SpikeMondayFibChart,b=a.create();
 a.show(host(),{symbol:'SOL-USDT-SWAP'},'2026-10-19');b.show(host(),{symbol:'SUI-USDT-SWAP'},'2026-10-19');
 assert.equal(pending.length,4);assert.ok(pending.every(p=>!p.options.signal.aborted));
 a.hide();assert.ok(pending.slice(0,2).every(p=>p.options.signal.aborted));assert.ok(pending.slice(2).every(p=>!p.options.signal.aborted));assert.equal(unmounts.length,1);
 b.pause();assert.ok(pending.every(p=>p.options.signal.aborted));
});
