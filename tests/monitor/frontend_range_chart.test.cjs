'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),vm=require('node:vm'),fs=require('node:fs');
const sandbox={window:{SpikeChartTools:require('../../yoyo/vision_research/static/chart-tools.js')},module:{exports:{}},Intl,Date};vm.runInNewContext(fs.readFileSync('yoyo/monitor/static/range-chart.js','utf8'),sandbox);
const {render,price}=sandbox.module.exports,view={bars:192,range:true,profile:true};
test('multiple weekly lines stop and autoscale at their own expiry',()=>{
 const lines=[],owner={rows:[{time:0},{time:100},{time:200},{time:300}],levelWindow:{start_ms:0,end_ms:300000},
  activeLevels:[{price:10,start_ms:0,end_ms:100000,historical:true},{price:20,start_ms:100000,end_ms:300000}],
  chart:{timeScale:()=>({logicalToCoordinate:x=>x*100,getVisibleLogicalRange:()=>({from:1.2,to:2.5})})},series:{priceToCoordinate:p=>p}};
 assert.deepEqual(Array.from(sandbox.module.exports.Interactive.prototype.scaleLevels.call(owner)),[20]);
 owner.referenceScale=false;assert.deepEqual(Array.from(sandbox.module.exports.Interactive.prototype.scaleLevels.call(owner)),[]);
 owner.referenceScale=true;
 const ctx={save(){},restore(){},beginPath(){},rect(){},clip(){},setLineDash(){},moveTo:(x,y)=>lines.push([x,y]),lineTo:(x,y)=>lines.push([x,y]),stroke(){},arc(){},fill(){}};
 new sandbox.module.exports.WeekLevels(owner).paint({useMediaCoordinateSpace:fn=>fn({context:ctx,mediaSize:{width:400,height:100}})});
 assert.deepEqual(lines,[[0,10],[100,10],[100,20],[300,20]]);
});
test('range fallback and interactive defaults share the Monday candle palette',()=>{
 const colors=sandbox.window.SpikeChartTools.colors;
 assert.equal(colors.up,'#169873');assert.equal(colors.down,'#d7355d');
 const svg=render({symbol:'BTC',candles:[{time:0,open:10,high:12,low:9,close:11},{time:900000,open:11,high:12,low:9,close:10}]},view);
 assert.match(svg,/fill="#169873"/);assert.match(svg,/fill="#d7355d"/);
 assert.equal(colors.volumeUp,colors.up+'38');assert.equal(colors.volumeDown,colors.down+'38');
});
test('weekly rays start at the current NY Monday and disappear in a previous-week viewport',()=>{
 const {logicalAt,WeekLevels}=sandbox.module.exports,start=Date.parse('2026-10-19T04:00Z'),end=Date.parse('2026-10-26T04:00Z');
 const rows=Array.from({length:14},(_,i)=>({time:start/1000-7*86400+i*86400}));
 assert.equal(logicalAt(rows,start),7);assert.equal(logicalAt(rows,start+43200000),7.5);
 let offset=0,lines=[],rectangles=[];const owner={rows,levelWindow:{start_ms:start,end_ms:end},activeLevels:[{price:100}],chart:{timeScale:()=>({logicalToCoordinate:x=>x*10-offset})},series:{priceToCoordinate:p=>p}};
 let visible={from:0,to:6};owner.chart.timeScale=()=>({logicalToCoordinate:x=>x*10-offset,getVisibleLogicalRange:()=>visible});
 assert.deepEqual(Array.from(sandbox.module.exports.Interactive.prototype.scaleLevels.call(owner)),[]);
 visible={from:7,to:10};assert.deepEqual(Array.from(sandbox.module.exports.Interactive.prototype.scaleLevels.call(owner)),[100]);
 owner.scaleLevels=()=>sandbox.module.exports.Interactive.prototype.scaleLevels.call(owner);
 const primitive=new WeekLevels(owner),ctx={save(){},restore(){},beginPath(){},rect(){},clip(){},setLineDash(){},moveTo:(x,y)=>lines.push([x,y]),lineTo:(x,y)=>lines.push([x,y]),stroke(){},arc(){},fill(){},roundRect(...args){rectangles?.push(args);}};
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
test('range fallback prices and Beijing clocks preserve output without per-call Intl allocation',()=>{
 let numbers=0,dates=0;
 const intl={
  NumberFormat:class extends Intl.NumberFormat{constructor(...args){numbers++;super(...args);}},
  DateTimeFormat:class extends Intl.DateTimeFormat{constructor(...args){dates++;super(...args);}},
 };
 const context={window:{SpikeChartTools:require('../../yoyo/vision_research/static/chart-tools.js')},module:{exports:{}},Intl:intl,Date};
 vm.runInNewContext(fs.readFileSync('yoyo/monitor/static/range-chart.js','utf8'),context);
 const api=context.module.exports,chart=context.window.SpikeRangeChart,initialNumbers=numbers,initialDates=dates;
 for(const value of [81741.96125,.006771234,1.23456789,1200000,-81741.96125,0,1.23e-10]){
  const digits=Math.abs(value)>=100?2:Math.abs(value)>=1?4:8;
  assert.equal(api.price(value),value.toLocaleString('zh-CN',{useGrouping:false,maximumFractionDigits:digits}));
 }
 const stamp=Date.parse('2026-11-09T04:05:00Z');
 for(const year of [false,true]){
  const expected=new Intl.DateTimeFormat('zh-CN',{timeZone:'Asia/Shanghai',...(year?{year:'numeric'}:{}),month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false}).format(new Date(stamp));
  assert.equal(chart.clock(stamp,year),expected);
 }
 assert.equal(chart.clock(null),'—');assert.equal(numbers,initialNumbers);assert.equal(dates,initialDates);
});
test('readout caching preserves DOM and updates corrected OHLC and MA independently',()=>{
 const style=sandbox.window.SpikeChartStyle=require('../../yoyo/monitor/static/chart-style.js');
 let writes=0;
 const textNode=()=>{let value='';return {get textContent(){return value;},set textContent(v){writes++;value=v;}};};
 const values=Array.from({length:5},textNode),numbers=style.lines.map(textNode);
 const legend=style.lines.map((_,i)=>({title:'',querySelector:()=>numbers[i]}));
 const row={time:900,open:.000001,high:.000004,low:.0000008,close:.000002,...Object.fromEntries(style.lines.map((l,i)=>[l.key,(i+1)/10000]))};
 const host={innerHTML:''},owner={rows:[row],rowByTime:new Map([[row.time,row]]),hover:row.time,maSeries:[{}],readoutNodes:values,legendNodes:legend,panel:{querySelector:()=>host}};
 const paint=()=>sandbox.module.exports.Interactive.prototype.readout.call(owner);
 paint();const first=writes;for(let i=0;i<180;i++)paint();assert.equal(writes,first);
 row.close=.000003;paint();assert.equal(values[4].textContent,'0.000003');assert.equal(writes,first+1);
 row.ema120=null;paint();assert.equal(numbers[5].textContent,'缺样');assert.equal(writes,first+2);
 owner.hover=null;paint();assert.match(values[0].textContent,/最新 K 线/);
 owner.compactReplay=true;paint();assert.equal(host.hidden,true);
 owner.hover=row.time;paint();assert.equal(host.hidden,false);
 owner.hover=null;owner.compactReplay=false;paint();assert.equal(host.hidden,false);
 owner.rows=[];owner.rowByTime.clear();paint();assert.equal(host.innerHTML,'');
});
test('inline names and prices stay inside their anchored ray, without axis badges',()=>{
 const {WeekLevels}=sandbox.module.exports,lines=[],texts=[],rectangles=[];
 let offset=0,y=40;
 const owner={rows:[{time:0},{time:100}],levelWindow:{start_ms:0,end_ms:100000},
  activeLevels:[{label:'上 1.618',price:.13668908,axisLabelVisible:false,inlinePriceVisible:true,inlineNameVisible:true,color:'#c93248'}],
  chart:{timeScale:()=>({logicalToCoordinate:x=>x*200-offset})},series:{priceToCoordinate:()=>y}};
 const primitive=new WeekLevels(owner),ctx={save(){},restore(){},beginPath(){},rect(){},clip(){},setLineDash(){},moveTo:(x,y)=>lines.push([x,y]),lineTo:(x,y)=>lines.push([x,y]),stroke(){},arc(){},fill(){},roundRect(...args){rectangles?.push(args);},measureText:()=>({width:65}),fillText:(...args)=>texts.push(args),fillRect:(...args)=>rectangles.push(args)};
 const paint=()=>primitive.paint({useMediaCoordinateSpace:fn=>fn({context:ctx,mediaSize:{width:180,height:100}})});
 assert.equal(primitive.priceAxisViews().length,0);paint();
 assert.deepEqual(lines,[[0,40],[180,40]]);assert.deepEqual(texts,[['上 1.618 (0.13668908)',174,36]]);assert.equal(rectangles.length,0,'plain Fibonacci text must never paint a filled label');
 texts.length=0;owner.activeLevels[0].inlinePriceVisible=false;paint();assert.deepEqual(texts,[['上 1.618',174,36]]);owner.activeLevels[0].inlinePriceVisible=true;
 texts.length=0;offset=160;paint();assert.deepEqual(texts,[],'a short visible ray must not crop its number');
 offset=0;y=16;paint();assert.deepEqual(texts,[],'top clipping must not leave a partial label');
 y=120;paint();assert.deepEqual(texts,[],'offscreen levels must not acquire floating labels');
 y=40;offset=-200;paint();assert.deepEqual(texts,[],'a future interval does not draw prices over history');
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
 sandbox.window.SpikeChartStyle=require('../../yoyo/monitor/static/chart-style.js');instance.syncAverages=()=>{};
 const from=data.candles[240].time,to=data.candles[267].time;
 instance.draw(data,{...config,ma:true,displayWindow:{from,to}},'');
 assert.equal(instance.rows.length,27);assert.equal(instance.rows[0].time,from/1000);assert.equal(instance.rows.at(-1).time,to/1000-900);
 assert.equal(instance.rows[0].sma120,101,'the hidden 240-bar prefix must still warm up the first displayed SMA');
 instance.draw(data,config,'');assert.equal(instance.rows.length,400,'leaving the trade window restores the full live history');
 sandbox.window.SpikeRangeChart.unmount(host);
});

test('720-bar live revisions update only the tip and still replace historical corrections',()=>{
 const T=require('../../yoyo/vision_research/static/chart-tools.js'),style=require('../../yoyo/monitor/static/chart-style.js');
 sandbox.window.SpikeChartTools={...T};sandbox.window.SpikeChartStyle=style;
 const host={replaceChildren(){}},instance=sandbox.window.SpikeRangeChart.mount(host,{},view),nodes=new Map();
 const node=key=>{if(!nodes.has(key))nodes.set(key,{innerHTML:'',textContent:'',hidden:false,open:false,classList:{toggle(){}},querySelectorAll:()=>[]});return nodes.get(key);};
 let visible={from:500.25,to:650.25},setCalls=0,updates=0,points=0;
 const makeSeries=()=>({rows:[],setData(rows){setCalls++;points+=rows.length;this.rows=rows.slice();},update(row){updates++;points++;if(this.rows.at(-1)?.time===row.time)this.rows[this.rows.length-1]=row;else this.rows.push(row);},applyOptions(){},createPriceLine(){return {};},removePriceLine(){}});
 const scale={getVisibleLogicalRange:()=>visible,setVisibleLogicalRange:r=>{visible=r;},fitContent(){}};
 instance.chart={priceScale:()=>({applyOptions(){}}),timeScale:()=>scale,applyOptions(){},removeSeries(){},remove(){}};
 instance.series=makeSeries();instance.volume=makeSeries();instance.chart.addLineSeries=()=>makeSeries();
 instance.canvas={clientWidth:1200};instance.panel={querySelector:node};instance.controls={update(){},destroy(){}};
 instance.paintMondayBand=()=>{};
 const start=Date.parse('2026-11-01T00:00Z'),candles=Array.from({length:720},(_,i)=>({time:start+i*900000,open:100,high:102,low:99,close:101,volume:20}));
 const config={...view,bars:900,wallpaper:true,initialBars:150,ma:true,mondayBand:true};
 const data={symbol:'BTC',timezone:'America/New_York',bar:'15m',levels:[],monday:{start_ms:start,end_ms:start+800*900000}};
 instance.draw({...data,candles},config,'');assert.equal(instance.rowDelta,null,'full initial data does not claim a certified tail');
 assert.equal(setCalls,8);assert.equal(points,720*8,'initial load sets the eight candle, volume and MA series');
 visible={from:500.25,to:650.25}; // Simulate a user pan after initial centering.
 const prefix=instance.rows.slice(0,-1),initialWrites=points;
 let current=candles,mapVisits=0;const originalMap=Array.prototype.map;
 Array.prototype.map=function(...args){if(this.length===720)mapVisits+=this.length;return originalMap.apply(this,args);};
 try{
  for(let tick=0;tick<60;tick++){
   current=current.slice();const old=current.at(-1),close=old.close+(tick%2?-.01:.02);
   current[719]={...old,high:Math.max(old.high,close),close,volume:old.volume+1};
   instance.draw({...data,candles:current},config,'');
   if(tick===0)assert.deepEqual(Array.from(instance.rowDelta.changedRows),[719],'drawing overlays receive the precise revised tail index');
  }
 }finally{Array.prototype.map=originalMap;}
 assert.equal(mapVisits,0,'same-K ticks allocate no full 720-row mapped series snapshots');
 assert.equal(updates,60*8,'each same-bar OHLC/volume revision updates one point in each active series');
 assert.equal(setCalls,8,'live revisions never send a 720-row setData snapshot');
 assert.equal(points-initialWrites,60*8);assert.ok(prefix.every((row,i)=>instance.rows[i]===row),'unchanged candles retain their chart row objects');
 assert.deepEqual(visible,{from:500.25,to:650.25},'same-K revisions preserve the historical viewport');
 current=[...current,{time:start+720*900000,open:current.at(-1).close,high:103,low:current.at(-1).close,close:102,volume:5}];
 instance.draw({...data,candles:current},config,'');
 assert.equal(instance.rows.length,721);assert.equal(setCalls,8,'an in-window append extends all active series incrementally');
 assert.deepEqual(Array.from(instance.rowDelta.changedRows),[720]);assert.equal(instance.bandIndices.length,721,'Monday overlay includes the appended bar');
 assert.equal(updates,61*8,'the new bar advances candle, volume and all six MA series once');
 assert.equal(instance.rows.at(-1).sma120,style.averages(current,900000).at(-1).sma120,'appended MA values match a full causal calculation');
 const corrected=current.slice();corrected[100]={...corrected[100],close:corrected[100].close+.5,high:corrected[100].high+.5};
 instance.draw({...data,candles:corrected},config,'');assert.equal(instance.rowDelta,null,'history corrections invalidate the tail certificate');
 assert.equal(setCalls,15,'a historical close correction replaces affected series from the first changed candle');
 assert.equal(instance.rows[100].close,corrected[100].close);assert.equal(instance.rowByTime.get(corrected[100].time/1000).close,corrected[100].close);
 assert.deepEqual(visible,{from:500.25,to:650.25},'historical OHLC corrections do not move the time viewport');
 sandbox.window.SpikeRangeChart.unmount(host);
});

test('each Monday ray starts at its actual extreme even between display bars',()=>{
 const {coordinateAt,WeekLevels}=sandbox.module.exports,lines=[];
 const scale={logicalToCoordinate:i=>Math.trunc(i)*100,getVisibleLogicalRange:()=>({from:0,to:1})};
 assert.equal(coordinateAt(scale,.25),25);
 const owner={rows:[{time:0},{time:100}],levelWindow:{start_ms:0,end_ms:100000},activeLevels:[{price:30,start_ms:25000},{price:60,start_ms:75000}],chart:{timeScale:()=>scale},series:{priceToCoordinate:p=>p}};
 const ctx={save(){},restore(){},beginPath(){},rect(){},clip(){},setLineDash(){},moveTo:(x,y)=>lines.push([x,y]),lineTo:(x,y)=>lines.push([x,y]),stroke(){},arc(){},fill(){}};
 new WeekLevels(owner).paint({useMediaCoordinateSpace:fn=>fn({context:ctx,mediaSize:{width:100,height:100}})});
 assert.deepEqual(lines,[[25,30],[100,30],[75,60],[100,60]]);
 scale.getVisibleLogicalRange=()=>({from:0,to:.5});
 assert.deepEqual(Array.from(sandbox.module.exports.Interactive.prototype.scaleLevels.call(owner)),[30]);
});

test('per-candle colors repaint identical OHLC, survive live appends and preserve pan',()=>{
 const T=require('../../yoyo/vision_research/static/chart-tools.js'),C=require('../../yoyo/monitor/static/cloud-candle-colors.js');
 sandbox.window.SpikeChartTools={...T,library:()=>new Promise(()=>{})};
 const host={replaceChildren(){}},instance=sandbox.window.SpikeRangeChart.mount(host,{},view);
 let visible=null,writes=0,rangeWrites=0,candles=[],volume=[];
 const series=kind=>({setData(rows){writes++;if(kind==='candle')candles=rows;else volume=rows;},update(row){writes++;const rows=kind==='candle'?candles:volume;if(rows.at(-1)?.time===row.time)rows[rows.length-1]=row;else rows.push(row);},applyOptions(){},removePriceLine(){},createPriceLine(){}});
 instance.series=series('candle');instance.volume=series('volume');
 const scale={getVisibleLogicalRange:()=>visible,setVisibleLogicalRange:r=>{visible=r;rangeWrites++;},fitContent(){}};
 instance.chart={priceScale:()=>({applyOptions(){}}),timeScale:()=>scale,applyOptions(){},remove(){}};
 instance.canvas={clientWidth:1200};instance.panel={querySelector:()=>({classList:{toggle(){}},innerHTML:'',textContent:''})};instance.controls={update(){},destroy(){}};instance.paintMondayBand=()=>{};
 const start=Date.parse('2026-11-01T00:00Z'),data={symbol:'BTC',bar:'12H',levels:[],candles:Array.from({length:8},(_,i)=>({time:start+i*43200000,open:100,high:110,low:90,close:105,volume:12}))};
 const before=JSON.stringify(data);let config={...view,bars:Infinity,wallpaper:true,initialBars:8,candleColor:C.colorizer()};
 instance.draw(data,config,'');const expected=['#8B9099','#8B9099','#8B9099','#30D158','#30D158','#8B9099','#8B9099','#8B9099'];
 assert.ok(candles.every((r,i)=>r.color===expected[i]&&r.wickColor===r.color&&r.borderColor===r.color));
 assert.ok(volume.every((r,i)=>r.color===expected[i]+'38'));visible={from:1,to:6};const oldRanges=rangeWrites;
 config={...config,candleColor:C.colorizer({monday:'#FFAA00',weekend:'#7766DD'})};instance.draw(data,config,'');
 assert.equal(rangeWrites,oldRanges);assert.deepEqual(visible,{from:1,to:6});
 assert.equal(candles[0].color,'#7766DD');assert.equal(candles[3].color,'#FFAA00');
 const oldWrites=writes;instance.draw(JSON.parse(before),config,'');assert.equal(writes,oldWrites);
 // An older signal arrives asynchronously with identical OHLC and callback.
 const historicalKeys=new Set(),ordinary=config.candleColor;
 config={...config,candleColor:r=>historicalKeys.has(r.time)?'#0A84FF':ordinary(r)};
 instance.draw(data,config,'');const beforeHistoryWrites=writes;
 historicalKeys.add(data.candles[3].time/1000);instance.draw(data,config,'');
 assert.ok(writes>beforeHistoryWrites);assert.equal(rangeWrites,oldRanges);
 for(const field of ['color','wickColor','borderColor'])assert.equal(candles[3][field],'#0A84FF');
 assert.equal(volume[3].color,'#0A84FF38');assert.deepEqual(visible,{from:1,to:6});
 instance.draw({...data,candles:[...data.candles,{...data.candles.at(-1),time:start+8*43200000}]},config,'');
 assert.equal(candles[3].color,'#0A84FF');assert.equal(candles.at(-1).color,'#8B9099');assert.equal(JSON.stringify(data),before);
 instance.draw(data,{...config,candleColor:undefined},'');assert.ok(candles.every(r=>!('color' in r)));assert.ok(volume.every(r=>r.color===T.colors.volumeUp));
 sandbox.window.SpikeRangeChart.unmount(host);
});

test('weekly rays support dotted lineStyle without changing solid or dashed defaults',()=>{
 const dashes=[],arcs=[],owner={rows:[{time:0},{time:100}],levelWindow:{start_ms:0,end_ms:100000},
  activeLevels:[{price:10,start_ms:0,lineStyle:0,startMarkerVisible:false},{price:20,start_ms:0,lineStyle:1,startMarkerVisible:false},{price:30,start_ms:0,lineStyle:2,startMarkerVisible:false}],
  chart:{timeScale:()=>({logicalToCoordinate:i=>i*50})},series:{priceToCoordinate:p=>p}};
 const ctx={save(){},restore(){},beginPath(){},rect(){},clip(){},setLineDash:value=>dashes.push(value),moveTo(){},lineTo(){},stroke(){},arc:(...args)=>arcs.push(args),fill(){}};
 new sandbox.module.exports.WeekLevels(owner).paint({useMediaCoordinateSpace:fn=>fn({context:ctx,mediaSize:{width:120,height:60}})});
 assert.deepEqual(dashes.map(value=>Array.from(value)),[[],[1,3],[4,4]]);assert.equal(arcs.length,0,'appearance lines do not draw origin dots');
});
