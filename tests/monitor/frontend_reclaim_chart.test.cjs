'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const {latest,select,candleIndex,geometry}=require('../../yoyo/monitor/static/reclaim-chart.js');
const pos=(x={})=>({id:1,symbol:'SOL-USDT-SWAP',side:1,fill_ms:600001,entry:120,target:125,taken:1,notional:100,stage:'pending_confirm',...x});
const rows=[{time:300},{time:600},{time:900}],step=300000;
test('score annotation identifies the candle ending at the saved close, only on 1m',()=>{
 const {replayScore}=require('../../yoyo/monitor/static/reclaim-chart.js');
 const close=Date.parse('2026-06-26T17:55:00Z'),bars=Array.from({length:10},(_,i)=>({time:close/1000-300+i*60,low:10,high:12}));
 const trade={entry_ms:close+300000,entry_rules:{status:'saved',kind:'add5',score:.8345036568541999},fills:{score_close_ms:close}};
 const score=replayScore(trade,bars,60000);
 assert.equal(score.index,4);assert.equal(bars[score.index].time,close/1000-60);
 assert.equal(score.label,'01:55 收盘 · 0.8345分');
 assert.equal(score.close,close,'an add uses the parent score close, not its later entry');
 for(const period of [120000,180000,300000,900000,3600000])assert.equal(replayScore(trade,bars,period),null);
 assert.equal(replayScore(trade,bars.filter((_,i)=>i!==4),60000),null,'missing scored candle never snaps to a neighbor');
 assert.equal(replayScore({...trade,fills:{}},bars,60000),null);
 for(const score of [null,undefined,NaN,-.1,1.1])assert.equal(replayScore({...trade,entry_rules:{status:'saved',score}},bars,60000),null);
 assert.equal(replayScore({...trade,entry_rules:{status:'unavailable',score:.8}},bars,60000),null);
});
test('score label and hollow mark avoid candles, fill labels and volume across pan and zoom',()=>{
 const {ReplayFillDrawing,replayScore,replayFills}=require('../../yoyo/monitor/static/reclaim-chart.js');
 const close=Date.parse('2026-06-26T17:55:00Z');
 for(const width of [260,330,1200])for(const zoom of [.6,1,1.8]){
  const bars=Array.from({length:20},(_,i)=>({time:close/1000-300+i*60,high:12+(i%3)*.2,low:10-(i%3)*.2}));
  const at=i=>width/2+(i-4)*12*zoom,price=p=>450-p*20,texts=[],strokes=[];let path=[];
  const ctx={save(){},restore(){},beginPath(){path=[];},rect(){},clip(){},setLineDash(){},moveTo:(...p)=>path.push(p),lineTo:(...p)=>path.push(p),stroke:()=>strokes.push(path.slice()),closePath(){},fill(){},
   fillText:(...t)=>texts.push(t),measureText:t=>({width:t.length*6})};
  const owner={rows:bars,chart:{timeScale:()=>({logicalToCoordinate:at})},series:{priceToCoordinate:price}};
  const trade={side:-1,entry_rules:{status:'saved',score:.8345},fills:{score_close_ms:close,entry_start_ms:close-300000,entry_end_ms:close,entry_price:12,exit_start_ms:close+300000,exit_end_ms:close+600000,exit_price:10}};
  const draw=new ReplayFillDrawing(owner);draw.step=60000;draw.items=replayFills(trade);draw.score=replayScore(trade,bars,60000);
  const paint=()=>draw.paint({useMediaCoordinateSpace:f=>f({context:ctx,mediaSize:{width,height:400}})});paint();
  const score=draw.labels.find(p=>p.key==='score');assert.ok(score);assert.equal(score.anchor,at(4));assert.ok(score.bottom<=400*.84);
  for(const label of draw.labels)for(let i=0;i<bars.length;i++){
   const half=12*zoom*.4+1,left=at(i)-half,right=at(i)+half;
   assert.ok(label.right<=left||label.left>=right||label.bottom<price(bars[i].high)||label.top>price(bars[i].low));
  }
  for(const other of draw.labels.filter(p=>p!==score))assert.ok(score.right<=other.left||score.left>=other.right||score.bottom<=other.top||score.top>=other.bottom);
  assert.ok(strokes.some(p=>p.length===4&&p[0][0]===score.anchor),'score uses a small hollow diamond');
  draw.score=null;texts.length=0;paint();assert.ok(!texts.some(t=>t[0].includes('分')),'switching periods removes the score text');
 }
});
test('historical fills anchor to saved price and native interval on a finer chart',()=>{
 const api=require('../../yoyo/monitor/static/reclaim-chart.js'),start=Date.parse('2026-09-02T03:45:00Z');
 const bars=Array.from({length:50},(_,i)=>({time:(start-60000+i*60000)/1000,high:.014,low:.01}));
 const trade={entry_ms:start+300000,exit_ms:start+2100000,fills:{entry_start_ms:start,entry_end_ms:start+300000,entry_price:.01115564,exit_start_ms:start+1800000,exit_end_ms:start+2100000,exit_price:.01364,exit_kind:'interval'}};
 assert.equal(api.replayRange(trade,bars,60000).entry,1);assert.equal(api.logicalAt(bars,start+300000,60000),6);
 assert.equal(api.replayFills(trade)[0].price,.01115564);assert.deepEqual(api.replayFills({}),[]);
 const sandbox={window:{SpikeChartTools:{axisPrice:String}},module:{exports:{}}};vm.runInNewContext(fs.readFileSync('yoyo/monitor/static/reclaim-chart.js','utf8'),sandbox);
 const strokes=[],texts=[];let path=[];const ctx={save(){},restore(){},beginPath(){path=[];},rect(){},clip(){},setLineDash(){},moveTo:(...x)=>path.push(x),lineTo:(...x)=>path.push(x),stroke:()=>strokes.push(path.slice()),closePath(){},fill(){},fillRect(){},measureText:t=>({width:t.length*3}),fillText:t=>texts.push(t)};
 const owner={rows:bars,chart:{timeScale:()=>({logicalToCoordinate:i=>Number.isInteger(i)?i*10:0})},series:{priceToCoordinate:p=>p*1000000}};
 const draw=new sandbox.module.exports.ReplayFillDrawing(owner);draw.step=60000;draw.items=api.replayFills(trade);draw.paint({useMediaCoordinateSpace:fn=>fn({context:ctx,mediaSize:{width:500,height:20000}})});
 assert.deepEqual(strokes[0].slice(0,2),[[10,11155.64],[60,11155.64]]);
 assert.deepEqual(texts,['① 开多','② 平仓']);
 assert.equal(draw.labels[0].anchor,35,'native interval midpoint interpolates adjacent integer coordinates instead of falling on the left edge');
 assert.equal(draw.autoscaleInfo(0,49).priceRange.minValue,.01115564);
});
test('same-bar entry and exit labels stay outside candle wicks while partial fills retain exact ticks',()=>{
 const {ReplayFillDrawing,paperReplayFills}=require('../../yoyo/monitor/static/reclaim-chart.js');
 for(const width of [180,1200]){
  const strokes=[],texts=[],arrows=[];let path=[];
  const ctx={save(){},restore(){},beginPath(){path=[];},rect(){},clip(){},setLineDash(){},moveTo:(...p)=>path.push(p),lineTo:(...p)=>path.push(p),stroke:()=>strokes.push(path.slice()),closePath(){},fill:()=>arrows.push(path.slice()),
   fillRect(){assert.fail('labels have no background panels');},fillText:(...t)=>texts.push(t),measureText:t=>({width:t.length*10})};
  const owner={rows:rows.map(r=>({...r,high:125,low:115})),chart:{timeScale:()=>({logicalToCoordinate:i=>Number.isInteger(i)?width/2+(i-1)*16:0})},series:{priceToCoordinate:p=>200-p}};
  const p=pos({stage:'closed',exit_ms:600002,exit_price:120,exits:[{exit_ms:600001.5,price:121},{exit_ms:600002,price:120}]});
  const draw=new ReplayFillDrawing(owner);draw.step=step;draw.items=paperReplayFills(p);
  draw.paint({useMediaCoordinateSpace:fn=>fn({context:ctx,mediaSize:{width,height:200}})});
  assert.equal(strokes[0][0][1],80);assert.equal(strokes[1][0][1],80);assert.equal(strokes[2][0][1],79);
  assert.ok(Math.abs(strokes[0][0][0]-(width/2+16/step))<1e-9,'intrabar fill tick retains its timestamp fraction');
  assert.deepEqual(texts.map(t=>t[0]),['① 开多','② 平仓']);assert.equal(arrows.length,2);
  assert.ok(draw.labels[0].top>85);assert.ok(draw.labels[1].bottom<75);
  for(const label of draw.labels){assert.ok(label.left>=0&&label.right<=width&&label.top>=0&&label.bottom<=200);}
 }
});
test('label clearance includes neighboring long wicks, view edges, direction and side fallback',()=>{
 const {replayLabelLayout,replayFills}=require('../../yoyo/monitor/static/reclaim-chart.js');
 const candles=[{left:40,right:52,top:70,bottom:130},{left:59,right:71,top:55,bottom:180}];
 const mark={anchor:46,y:100,width:54,above:false};
 let labels=replayLabelLayout([mark],candles,180,240);
 assert.equal(labels.length,1);assert.ok(labels[0].top>=198,'neighbor wick is included across the label width');
 labels=replayLabelLayout([mark],candles,180,200);
 assert.equal(labels.length,1);assert.equal(labels[0].above,true,'try the other side when bottom space runs out');
 for(const anchor of [1,179]){
  const c=[{left:anchor-6,right:anchor+6,top:70,bottom:130}];
  const [p]=replayLabelLayout([{...mark,anchor}],c,180,240);
  assert.ok(p.left>=0&&p.right<=180);assert.ok(p.top>c[0].bottom);
 }
 const fills={entry_start_ms:1,entry_end_ms:1,entry_price:10,exit_start_ms:2,exit_end_ms:2,exit_price:11};
 for(const side of [1,-1]){
  const [entry,exit]=replayFills({side,fills});assert.equal(entry.above,side===-1);assert.equal(exit.above,side===1);
 }
 assert.deepEqual(replayLabelLayout([mark],[{left:0,right:180,top:0,bottom:200}],180,200),[],'never force a label over candles when manual zoom leaves no whitespace');
 assert.deepEqual(replayLabelLayout([{...mark,anchor:-10}],candles,180,240),[],'offscreen events do not leave false edge labels');
});
test('same-side labels stack without candle or label overlap after zoom and resize',()=>{
 const {replayLabelLayout}=require('../../yoyo/monitor/static/reclaim-chart.js');
 for(const width of [180,390,1200])for(const zoom of [0.6,1,1.6]){
  const candles=Array.from({length:9},(_,i)=>({left:width/2+(i-4)*10*zoom-4,right:width/2+(i-4)*10*zoom+4,top:8,bottom:85+(i%3)*16}));
  const marks=[0,1].map(i=>({anchor:width/2+i*5,y:70,width:54,above:true}));
  const labels=replayLabelLayout(marks,candles,width,240);assert.equal(labels.length,2);
  for(const p of labels)for(const c of candles)assert.ok(p.right<=c.left||p.left>=c.right||p.bottom<=c.top||p.top>=c.bottom);
  const [a,b]=labels;assert.ok(a.right<=b.left||a.left>=b.right||a.bottom<=b.top||a.top>=b.bottom);
 }
});
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
test('deep-history candle lookup keeps exact gap and endpoint semantics with bounded reads',()=>{
 let reads=0;
 const history=Array.from({length:100000},(_,i)=>({get time(){reads++;return i*300;}}));
 assert.equal(candleIndex(history,99999*300000+1,step),99999);
 assert.ok(reads<25,`lookup read ${reads} timestamps`);
 assert.equal(candleIndex(history,-1,step),-1);
 assert.equal(candleIndex(history,100000*300000,step),-1);
 assert.equal(candleIndex(history,NaN,step),-1);
});
test('confirmation cannot invent an initial stop or extend an ended position into the present',()=>{
 assert.equal(geometry(pos({be_from_ms:1200000}),rows,step,1000000).stop,null);
 assert.equal(geometry(pos({be_from_ms:300000}),rows,step,1000000).stop,null);
 assert.equal(geometry(pos({fill_ms:100000,stage:'closed',exit_ms:200000}),rows,step,1000000),null);
 assert.equal(geometry(pos({stage:'closed',exit_ms:500000}),rows,step,1000000),null);
});
test('trade drawings retain directional triangles without event badges and time protection correctly',()=>{
 const sandbox={window:{SpikeChartTools:{axisPrice:String}},module:{exports:{}}};
 vm.runInNewContext(fs.readFileSync('yoyo/monitor/static/reclaim-chart.js','utf8'),sandbox);
 const texts=[],arrows=[];let path=[];
 const ctx={save(){},restore(){},beginPath(){path=[];},rect(){},clip(){},setLineDash(){},moveTo:(...v)=>path.push(v),lineTo:(...v)=>path.push(v),stroke(){},closePath:()=>arrows.push(path),fill(){},roundRect(){},fillRect(){},measureText:t=>({width:t.length*4}),fillText:t=>texts.push(t)};
 const owner={rows,chart:{timeScale:()=>({logicalToCoordinate:i=>i*100,width:()=>500})},series:{priceToCoordinate:p=>p*2}};
 for(const side of [1,-1]){
  const p=pos({side}),draw=new sandbox.module.exports.TradeDrawing(owner);draw.trade=p;draw.step=step;draw.shape=geometry(p,rows,step,950000);
  texts.length=0;arrows.length=0;draw.paint({useMediaCoordinateSpace:fn=>fn({context:ctx,mediaSize:{width:500,height:500}})});
  assert.ok(!texts.some(x=>x.includes('开仓 ·')));assert.ok(!texts.some(x=>x.includes('止损')));
  assert.equal(Math.sign(arrows[0][1][1]-arrows[0][0][1]),side);
  draw.trade={...p,be_from_ms:900001};draw.shape=geometry(draw.trade,rows,step,950000);texts.length=0;
  draw.paint({useMediaCoordinateSpace:fn=>fn({context:ctx,mediaSize:{width:500,height:500}})});
  assert.ok(texts.some(x=>x.includes('入场价止损已启用')));
  draw.trade={...p,exits:[{reason:'manual',notional:25,price:122,exit_ms:900001}]};draw.shape=geometry(draw.trade,rows,step,950000);texts.length=0;
  draw.paint({useMediaCoordinateSpace:fn=>fn({context:ctx,mediaSize:{width:500,height:500}})});
  assert.ok(!texts.some(x=>x.includes('减仓')));
  assert.equal(arrows.length,4,'entry plus partial-exit triangles remain across the two paints');
  draw.trade={...p,stage:'closed',exit_ms:900001,exit_price:122,reason:'manual'};draw.shape=geometry(draw.trade,rows,step,950000);texts.length=0;arrows.length=0;
  draw.paint({useMediaCoordinateSpace:fn=>fn({context:ctx,mediaSize:{width:500,height:500}})});
  assert.equal(arrows.length,2);assert.ok(!texts.some(x=>/平仓|开仓 ·|减仓/.test(x)));
 }
});
test('Monday chart factories own separate requests, canvases and timers',()=>{
 const pending=[],unmounts=[];
 const host=()=>({innerHTML:'',replaceChildren(){},querySelector(){return {addEventListener(){}};}});
 const sandbox={document:{hidden:false},window:{SpikeRangeChart:{mount(){},unmount:h=>unmounts.push(h)}},Intl,Date,AbortController,setTimeout:()=>1,clearTimeout(){},fetch:(url,options)=>new Promise(resolve=>pending.push({url,options,resolve}))};
 vm.runInNewContext(fs.readFileSync('yoyo/monitor/static/monday-fib-chart.js','utf8'),sandbox);
 const a=sandbox.window.SpikeMondayFibChart,b=a.create();
 a.show(host(),{symbol:'SOL-USDT-SWAP'},'2026-10-19');b.show(host(),{symbol:'SUI-USDT-SWAP'},'2026-10-19');
 assert.equal(pending.length,5,'the canonical page also requests monthly rails while the isolated factory stays weekly-only');assert.ok(pending.every(p=>!p.options.signal.aborted));
 a.hide();assert.ok(pending.slice(0,3).every(p=>p.options.signal.aborted));assert.ok(pending.slice(3).every(p=>!p.options.signal.aborted));assert.equal(unmounts.length,1);
 b.pause();assert.ok(pending.every(p=>p.options.signal.aborted));
});

test('closed paper history shares backtest framing and resumes without live anchor requests',()=>{
 const calls=[];let decorate;
 const chart={showReplay:(...args)=>calls.push(args),pause(){},hide(){}};
 const sandbox={window:{SpikeMondayFibChart:{create:options=>{decorate=options.decorate;return chart;}}},Date,AbortController,setTimeout,clearTimeout,
  fetch(){throw Error('history must not request current-week anchors');}};
 vm.runInNewContext(fs.readFileSync('yoyo/monitor/static/reclaim-chart.js','utf8'),sandbox);
 const api=sandbox.window.SpikeReclaimChart.create({clientWidth:390},()=>{}),p=pos({stage:'closed',exit_ms:900002});
 api.showHistory('spike',p,'5m',false,false);assert.equal(calls.at(-1)[3].paper,true);
 let visible;
 const instance={rows,series:{attachPrimitive(){},setMarkers(){}},chart:{applyOptions(){},priceScale:()=>({applyOptions(){}}),timeScale:()=>({setVisibleLogicalRange:r=>visible=r})}};
 decorate(instance,{symbol:p.symbol,paperTrade:p,paperHistory:true,bar:'5m',now_ms:1200000});
 assert.equal(visible.from,-.5);assert.equal(visible.to,rows.length-.5);
 assert.equal(instance.paperDrawings.size,0);assert.equal(instance.backtestDrawing.items[0].start,p.fill_ms);assert.equal(instance.backtestDrawing.items[0].end,p.fill_ms);
 api.setActive(false);api.setActive(true);assert.equal(calls.at(-1)[3].paper,true);api.destroy();
});
test('paper replay keeps exact point fills, partial exits and saved net returns',()=>{
 const {paperReplayTrade,paperReplayFills}=require('../../yoyo/monitor/static/reclaim-chart.js');
 const ui=require('../../yoyo/monitor/static/strategy-backtest.js'),start=Date.parse('2026-10-28T20:07:07Z');
 const p=pos({fill_ms:start,entry:90.2005,stage:'closed',exit_ms:start+120000,exit_price:91.9,pnl:9.282902,net_bp:184.41359,
  exits:[{exit_ms:start+60000,price:91},{exit_ms:start+120000,price:91.9}]});
 const r=paperReplayTrade(p),fills=paperReplayFills(p);assert.equal(r.return_pct,p.net_bp/100);
 assert.equal(fills.length,3);assert.ok(fills.every(f=>f.start===f.end));assert.equal(fills[2].start,start+60000);assert.equal(fills[2].price,91);
 const html=ui.summaryHTML(r,{source:'OKX',mode:'历史交易',costNote:'已扣手续费'});
 assert.match(html,/SOL/);assert.match(html,/OKX · 历史交易/);assert.match(html,/90.2005/);assert.match(html,/2026\/10\/29 04:07:07/);assert.match(html,/\+9.28/);assert.doesNotMatch(html,/Binance|记账|成交区间/);
 const shadow=ui.summaryHTML({...r,pnl:undefined},{source:'OKX',mode:'历史交易',funded:false,costNote:'不计入净值'});
 assert.match(shadow,/影子记录/);assert.doesNotMatch(shadow,/NaN|undefined|\+9.28|净收益/);
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

test('monthly ribbon mode toggles the same live chart without selecting a replay route',async()=>{
 const calls=[],monthly=[],chart={show:(...args)=>calls.push(args),hide(){},pause(){},setMonthly:value=>monthly.push(value)};
 const sandbox={window:{SpikeMondayFibChart:{create:()=>chart}},Date,AbortController,setTimeout,clearTimeout,
  fetch:async url=>({ok:true,json:async()=>({symbol:decodeURIComponent(url.split('=')[1]),timezone:'America/New_York',week:'2026-10-19'})})};
 vm.runInNewContext(fs.readFileSync('yoyo/monitor/static/reclaim-chart.js','utf8'),sandbox);
 const api=sandbox.window.SpikeReclaimChart.create({clientWidth:390},()=>{});
 api.setMonthly(true);await api.show(null,'15m',false,'SOL-USDT-SWAP');
 assert.deepEqual(monthly,[true]);assert.equal(calls.at(-1)[1].symbol,'SOL-USDT-SWAP');assert.equal(calls.at(-1)[2],'2026-10-19');assert.equal(calls.at(-1)[3].trade,null);
 api.setMonthly(false);assert.deepEqual(monthly,[true,false]);api.destroy();
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

test('appearance setting is forwarded to the owned chart without changing anchor requests',async()=>{
 const appearances=[],requests=[];let chart;
 chart={show(){},hide(){},pause(){},setAppearance:value=>appearances.push(value)};
 const sandbox={window:{SpikeMondayFibChart:{create:()=>chart}},Date,AbortController,setTimeout,clearTimeout,
  fetch:async url=>{requests.push(url);return {ok:true,json:async()=>({symbol:'SOL-USDT-SWAP',timezone:'America/New_York',week:'2026-10-19'})};}};
 vm.runInNewContext(fs.readFileSync('yoyo/monitor/static/reclaim-chart.js','utf8'),sandbox);
 const api=sandbox.window.SpikeReclaimChart.create({clientWidth:320},()=>{}),appearance={candleEnabled:false,lines:{'上 1.618':{color:'#123456',width:2,style:'dash'}}};
 api.setAppearance(appearance);assert.equal(appearances.at(-1),appearance);
 await api.show(pos());const count=requests.length;api.setAppearance({...appearance,lines:{'上 1.618':{color:null,width:1,style:'solid'}}});
 assert.equal(requests.length,count,'appearance edits do not reload Monday anchors');
 assert.equal(appearances.length,2);api.destroy();
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
