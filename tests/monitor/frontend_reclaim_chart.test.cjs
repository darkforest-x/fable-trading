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
  draw.trade={...p,exits:[{reason:'manual',notional:25,price:122,exit_ms:900001}]};draw.shape=geometry(draw.trade,rows,step,950000);texts.length=0;
  draw.paint({useMediaCoordinateSpace:fn=>fn({context:ctx,mediaSize:{width:500,height:500}})});
  assert.ok(texts.some(x=>x.includes('减仓 25.0%')));
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
test('untraded symbols load their own real chart without inheriting an execution overlay',async()=>{
 const calls=[],requests=[],notices=[];let decorate;
 const chart={show:(...args)=>calls.push(args),hide(){},pause(){}};
 const sandbox={window:{SpikeMondayFibChart:{create:options=>{decorate=options.decorate;return chart;}}},Date,AbortController,setTimeout,clearTimeout,
  fetch:async url=>{requests.push(url);return {ok:true,json:async()=>({symbol:decodeURIComponent(url.split('=')[1]),timezone:'America/New_York',week:'2026-10-19'})};}};
 vm.runInNewContext(fs.readFileSync('yoyo/monitor/static/reclaim-chart.js','utf8'),sandbox);
 const api=sandbox.window.SpikeReclaimChart.create({clientWidth:1200},s=>notices.push(s));
 await api.show(pos());assert.equal(calls.at(-1)[3].trade.symbol,'SOL-USDT-SWAP');
 await api.show(null,'1H',false,'BTC-USDT-SWAP');assert.match(requests.at(-1),/BTC-USDT-SWAP/);assert.equal(calls.at(-1)[3].trade,null);
 const instance={rows,series:{attachPrimitive(){}},paperDrawing:{shape:{old:true}}};
 decorate(instance,{paperTrade:null,bar:'1H',now_ms:1000000});assert.equal(instance.paperDrawing.shape,null);assert.equal(notices.at(-1),'');
 await api.show(pos(),'1H',false,'BTC-USDT-SWAP');assert.equal(calls.at(-1)[3].trade,null);
 api.setActive(false);const n=calls.length;await api.show(null,'5m',false,'DOGE-USDT-SWAP');assert.equal(calls.length,n);
 api.setActive(true);await new Promise(r=>setImmediate(r));assert.match(requests.at(-1),/DOGE-USDT-SWAP/);api.destroy();
});

test('paper indicator changes reuse the chart and anchors across polling, periods and resume',async()=>{
 const calls=[],requests=[];
 const chart={show:(...args)=>calls.push(args),hide(){},pause(){}};
 const sandbox={window:{SpikeMondayFibChart:{create:()=>chart}},Date,AbortController,setTimeout,clearTimeout,
  fetch:async url=>{requests.push(url);return {ok:true,json:async()=>({symbol:'SOL-USDT-SWAP',timezone:'America/New_York',week:'2026-10-19'})};}};
 vm.runInNewContext(fs.readFileSync('yoyo/monitor/static/reclaim-chart.js','utf8'),sandbox);
 const api=sandbox.window.SpikeReclaimChart.create({clientWidth:1200},()=>{});
 await api.show(pos());assert.equal(calls.at(-1)[3].internal,false);assert.equal(calls.at(-1)[3].ma,false);
 await api.show(pos(),'5m',true,'SOL-USDT-SWAP',true);
 assert.equal(calls.at(-1)[3].internal,true);assert.equal(calls.at(-1)[3].ma,true);
 assert.equal(requests.length,1,'display switches must not refetch anchors');
 await api.show(pos(),'1H',false,'SOL-USDT-SWAP',true);
 assert.equal(calls.at(-1)[3].internal,true);assert.equal(calls.at(-1)[3].period,'1H');assert.equal(calls.at(-1)[3].ma,false);
 api.setActive(false);api.setActive(true);await new Promise(setImmediate);
 assert.equal(calls.at(-1)[3].internal,true);assert.equal(requests.length,1);
 await api.show(pos(),'1H',false,'SOL-USDT-SWAP',false);assert.equal(calls.at(-1)[3].internal,false);api.destroy();
});

test('same-symbol historical positions persist independently of selected trade and detach on switch',()=>{
 let decorate;const attached=[],detached=[];
 const sandbox={window:{SpikeMondayFibChart:{create:o=>{decorate=o.decorate;return {};}}}};
 vm.runInNewContext(fs.readFileSync('yoyo/monitor/static/reclaim-chart.js','utf8'),sandbox);
 sandbox.window.SpikeReclaimChart.create({clientWidth:390},()=>{});
 const instance={rows,series:{attachPrimitive:p=>attached.push(p),detachPrimitive:p=>detached.push(p)},chart:{priceScale:()=>({applyOptions(){}}),timeScale:()=>({setVisibleLogicalRange(){}})}};
 const old=pos({id:2,stage:'closed',fill_ms:300001,exit_ms:600002}),current=pos();
 decorate(instance,{symbol:current.symbol,paperTrade:current,paperTrades:[old,current,old,pos({symbol:'BTC-USDT-SWAP',id:3})],bar:'5m',now_ms:1000000});
 assert.equal(instance.paperDrawings.size,2);assert.equal(attached.length,2);assert.equal(instance.paperDrawings.get('2').shape.to,old.exit_ms);
 decorate(instance,{symbol:current.symbol,paperTrade:old,paperTrades:[current,old],bar:'5m',now_ms:1100000});
 assert.equal(attached.length,2);assert.equal(instance.paperDrawings.get('2').shape.to,old.exit_ms);assert.equal(instance.paperDrawings.get('1').shape.to,1100000);
 decorate(instance,{symbol:'BTC-USDT-SWAP',paperTrade:null,paperTrades:[],bar:'5m',now_ms:1100000});assert.equal(instance.paperDrawings.size,0);assert.equal(detached.length,2);
});

test('chart uses recorded parent-based protection and ends the backstop at exact BE activation',()=>{
 const sandbox={window:{SpikeChartTools:{axisPrice:String}},module:{exports:{}}};
 vm.runInNewContext(fs.readFileSync('yoyo/monitor/static/reclaim-chart.js','utf8'),sandbox);
 const strokes=[],texts=[];let path=[];
 const ctx={save(){},restore(){},beginPath(){path=[];},rect(){},clip(){},setLineDash(){},moveTo:(...v)=>path.push(v),lineTo:(...v)=>path.push(v),stroke(){strokes.push(path.slice());},closePath(){},fill(){},roundRect(){},fillRect(){},measureText:t=>({width:t.length*2}),fillText:t=>texts.push(t)};
 const owner={rows,chart:{timeScale:()=>({logicalToCoordinate:i=>i*100,width:()=>600})},series:{priceToCoordinate:p=>p}};
 const draw=new sandbox.module.exports.TradeDrawing(owner),p=pos({entry:95,parent_id:9,reclaim_ref:100,backstop:70,be_price:100.17,be_from_ms:750000,stage:'breakeven'});
 draw.trade=p;draw.step=step;draw.shape=geometry(p,rows,step,950000);
 draw.paint({useMediaCoordinateSpace:fn=>fn({context:ctx,mediaSize:{width:600,height:600}})});
 assert.ok(texts.includes('保本保护止损 100.17'));
 assert.ok(strokes.some(path=>path[0][1]===70&&path[1][0]===150));
 assert.ok(strokes.some(path=>path[0][1]===100.17&&path[0][0]===150));
 assert.equal(draw.autoscaleInfo(0,3).priceRange.maxValue,125);
});
