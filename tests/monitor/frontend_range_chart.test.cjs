'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),vm=require('node:vm'),fs=require('node:fs');
const sandbox={window:{},module:{exports:{}},Intl,Date};vm.runInNewContext(fs.readFileSync('yoyo/monitor/static/range-chart.js','utf8'),sandbox);
const {render,price}=sandbox.module.exports,view={bars:192,range:true,profile:true};
test('weekly rays start at the current NY Monday and disappear in a previous-week viewport',()=>{
 const {logicalAt,WeekLevels}=sandbox.module.exports,start=Date.parse('2026-10-19T04:00Z'),end=Date.parse('2026-10-26T04:00Z');
 const rows=Array.from({length:14},(_,i)=>({time:start/1000-7*86400+i*86400}));
 assert.equal(logicalAt(rows,start),7);assert.equal(logicalAt(rows,start+43200000),7.5);
 let offset=0,lines=[];const owner={rows,levelWindow:{start_ms:start,end_ms:end},activeLevels:[{price:100}],chart:{timeScale:()=>({logicalToCoordinate:x=>x*10-offset})},series:{priceToCoordinate:p=>p}};
 let visible={from:0,to:6};owner.chart.timeScale=()=>({logicalToCoordinate:x=>x*10-offset,getVisibleLogicalRange:()=>visible});
 assert.deepEqual(Array.from(sandbox.module.exports.Interactive.prototype.scaleLevels.call(owner)),[]);
 visible={from:7,to:10};assert.deepEqual(Array.from(sandbox.module.exports.Interactive.prototype.scaleLevels.call(owner)),[100]);
 owner.scaleLevels=()=>sandbox.module.exports.Interactive.prototype.scaleLevels.call(owner);
 const primitive=new WeekLevels(owner),ctx={save(){},restore(){},beginPath(){},rect(){},clip(){},setLineDash(){},moveTo:(x,y)=>lines.push([x,y]),lineTo:(x,y)=>lines.push([x,y]),stroke(){}};
 primitive.paint({useMediaCoordinateSpace:fn=>fn({context:ctx,mediaSize:{width:100,height:400}})});assert.deepEqual(lines,[[70,100],[100,100]]);
 lines=[];offset=-100;primitive.paint({useMediaCoordinateSpace:fn=>fn({context:ctx,mediaSize:{width:100,height:400}})});assert.deepEqual(lines,[]);
 lines=[];offset=150;primitive.paint({useMediaCoordinateSpace:fn=>fn({context:ctx,mediaSize:{width:100,height:400}})});assert.deepEqual(lines,[]);
 assert.equal(primitive.priceAxisViews()[0].visible(),true);
 visible={from:0,to:6};assert.equal(primitive.priceAxisViews()[0].visible(),false);
});
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
 sandbox.window.SpikeChartTools={...require('../../yoyo/vision_research/static/chart-tools.js'),syncer:()=>()=>{},library:()=>ready};
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

test('Monday band uses millisecond calendar boundaries and second chart times',()=>{
 sandbox.window.SpikeChartTools={...require('../../yoyo/vision_research/static/chart-tools.js'),syncer:()=>()=>{},library:()=>new Promise(()=>{})};
 const host={replaceChildren(){}};const instance=sandbox.window.SpikeRangeChart.mount(host,{},view);
 instance.rows=[{time:100},{time:200},{time:300}];instance.mondayWindow={start_ms:100000,end_ms:300000};instance.bandIndices=[0,1];
 instance.band={hidden:true,style:{}};instance.chart={timeScale:()=>({timeToCoordinate:t=>t/10-5,width:()=>100})};
 instance.paintMondayBand();assert.equal(instance.band.hidden,false);assert.equal(instance.band.style.width,'20px');
 instance.mondayWindow=null;instance.bandIndices=[];instance.paintMondayBand();assert.equal(instance.band.hidden,true);
 instance.chart={remove(){}};sandbox.window.SpikeRangeChart.unmount(host);
});

test('free chart pan unlocks only pane gestures before the native handler, without capturing input',()=>{
 const events=new Map(),globalEvents=new Map(),classes=new Set(),writes=[];
 const canvas={clientHeight:600,getBoundingClientRect:()=>({left:20,top:30}),classList:{add:x=>classes.add(x),remove:x=>classes.delete(x)},
  addEventListener:(name,fn,capture)=>events.set(name,{fn,capture}),removeEventListener:name=>events.delete(name)};
 const env={addEventListener:(name,fn)=>globalEvents.set(name,fn),removeEventListener:name=>globalEvents.delete(name)};
 const chart={timeScale:()=>({width:()=>900,height:()=>28}),priceScale:side=>({width:()=>side==='left'?40:100,applyOptions:o=>writes.push(o)})};
 const binding=sandbox.module.exports.bindFreePan(chart,canvas,env);
 const down=events.get('pointerdown'),event=(o={})=>({pointerId:1,button:0,isPrimary:true,clientX:400,clientY:300,target:{closest:()=>null},...o});
 assert.equal(down.capture,true);
 for(const o of [{button:2},{isPrimary:false},{clientX:30},{clientX:1000},{clientY:610},{target:{closest:()=>({})}}])down.fn(event(o));
 assert.equal(writes.length,0,'axes, secondary pointers and links remain native');
 down.fn(event());assert.equal(writes.at(-1).autoScale,false);
 events.get('pointermove').fn(event({clientY:302}));assert.equal(classes.size,0);
 events.get('pointermove').fn(event({clientY:350}));assert.equal(classes.has('cv-panning'),true);
 globalEvents.get('pointerup')(event());assert.equal(classes.size,0);
 down.fn(event({pointerType:'touch'}));events.get('pointermove').fn(event({clientX:450}));globalEvents.get('blur')();assert.equal(classes.size,0);
 binding.destroy();assert.equal(events.size,0);assert.equal(globalEvents.size,0);
});

test('wallpaper starts with readable recent candles and refresh retains a manually panned viewport',()=>{
 const T=require('../../yoyo/vision_research/static/chart-tools.js');
 sandbox.window.SpikeChartTools={...T,library:()=>new Promise(()=>{})};
 const host={replaceChildren(){}},instance=sandbox.window.SpikeRangeChart.mount(host,{},view),nodes=new Map();
 const node=key=>{if(!nodes.has(key))nodes.set(key,{innerHTML:'',textContent:'',classList:{toggle(){}}});return nodes.get(key);};
 let visible=null,autoScale=true,rangeWrites=0,seriesWrites=0,lineWrites=0,optionWrites=0;const scale={getVisibleLogicalRange:()=>visible,setVisibleLogicalRange:r=>{visible=r;rangeWrites++;},fitContent:()=>visible={from:0,to:399}};
 instance.chart={priceScale:()=>({applyOptions:o=>autoScale=o.autoScale}),timeScale:()=>scale,applyOptions:o=>{if(o.rightPriceScale?.autoScale!==undefined)autoScale=o.rightPriceScale.autoScale;},remove(){}};
 instance.series={setData(){seriesWrites++;},update(){seriesWrites++;},applyOptions(){optionWrites++;},createPriceLine(){lineWrites++;},removePriceLine(){lineWrites++;}};instance.volume={setData(){seriesWrites++;},update(){seriesWrites++;}};
 instance.canvas={clientWidth:1200};instance.panel={querySelector:node};instance.controls={update(){},destroy(){}};instance.paintMondayBand=()=>{};
 const data={symbol:'BTC',timezone:'America/New_York',levels:[],candles:Array.from({length:400},(_,i)=>({time:1e12+i*900000,open:100,high:102,low:99,close:101,volume:1}))};
 const config={...view,bars:672,wallpaper:true,initialBars:160};instance.draw(data,config,'');
 assert.equal(visible.to-visible.from,160);assert.ok(visible.to>399);
 const counters=[rangeWrites,seriesWrites,lineWrites,optionWrites];
 instance.draw(JSON.parse(JSON.stringify(data)),config,'');
 assert.deepEqual([rangeWrites,seriesWrites,lineWrites,optionWrites],counters,'identical refresh must not redraw series, recreate lines, or touch viewport');
 visible={from:100.25,to:180.25};autoScale=false;
 instance.draw({...data,candles:[...data.candles,{...data.candles.at(-1),time:data.candles.at(-1).time+900000}]},config,'');
 assert.equal(visible.from,100.25);assert.equal(visible.to,180.25);assert.equal(autoScale,false);
 instance.draw({...data,symbol:'NEW',candles:[data.candles[0]]},config,'');
 assert.ok(visible.from<=0&&visible.to>=0,'right padding must not push short histories offscreen');
 instance.draw(data,config,'');
 assert.equal(visible.from,100.25);assert.equal(visible.to,180.25,'returning to a symbol restores its horizontal view');
 const older=Array.from({length:240},(_,i)=>({...data.candles[0],time:data.candles[0].time-(240-i)*900000}));
 instance.draw({...data,candles:[...older,...data.candles]},config,'');
 assert.equal(visible.from,340.25);assert.equal(visible.to,420.25,'background history prepends must keep the same candle timestamps in view');
 sandbox.window.SpikeRangeChart.unmount(host);
});
