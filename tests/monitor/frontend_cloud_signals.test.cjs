const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const {averages,indexAt,levels,STEPS}=require('../../yoyo/monitor/static/cloud-signal-chart.js');
const {select,tvURL}=require('../../yoyo/monitor/static/cloud-signals.js');
const colors=require('../../yoyo/monitor/static/cloud-candle-colors.js');
const stopSettings=require('../../yoyo/monitor/static/cloud-stop-settings.js');
const item={id:'one',symbol:'BTC-USDT-SWAP',side:1,signal_open_ms:43200000,confirmed_ms:86400000,ema20:108,ema50:103,entry:110,stop:99,target:135,planned_rr:25/11,range:{high:125,low:105,upper:135,lower:95,end_ms:0,expires_ms:604800000}};
function cloudChartHarness(fetch,onExcursion=()=>{},feedBehavior={},hooks={}){
 const mounts=[],drawings=[],excursions=[],feedCreates=[],span={textContent:''},retryButton={addEventListener(_,fn){this.retry=fn;}},historyStatus={addEventListener(){}};
 const notice={hidden:true,querySelector:q=>q==='span'?span:retryButton};
 const host={clientWidth:500,parentElement:{querySelector:()=>historyStatus}};
 const window={
  SpikeCloudCandleColors:{normalize:()=>({}),colorizer:()=>()=> '#000000'},
  SpikeCloudStopSettings:stopSettings,
  SpikeCloudHistoryStats:require('../../yoyo/monitor/static/cloud-history-stats.js'),
  SpikeCloudHistoryLoader:require('../../yoyo/monitor/static/cloud-history-loader.js'),
  SpikeMondayCandleFeed:{
   mergeDisplay:(context,live)=>{const rows=new Map((context.candles||[]).map(r=>[r.time,r]));for(const r of live.bars||[])if(r.time>=context.last_close_ms)rows.set(r.time,r);return {...context,candles:[...rows.values()].sort((a,b)=>a.time-b.time),display_live:true};},
   create:options=>{feedCreates.push(options);const count=feedCreates.filter(v=>v.period===options.period).length;return {start(){feedBehavior.start?.(options,count);},stop(){feedBehavior.stop?.(options,count);}};},
  },
  SpikeRangeChart:{mount:(_,data,options={})=>{
   mounts.push(data);const rows=(data.candles||[]).map(r=>({...r,time:r.time/1000}));
   const instance={rows,chart:{timeScale:()=>({setVisibleLogicalRange(){},getVisibleLogicalRange:()=>({from:0,to:Math.max(0,rows.length-1)}),logicalToCoordinate:i=>i*10}),priceScale:()=>({applyOptions(){}})},series:{attachPrimitive(){},priceToCoordinate:v=>150-v/1000}};
   options.decorate?.(instance);
   if(instance.cloudDrawing)drawings.push({signal:instance.cloudDrawing.signal,plans:instance.cloudDrawing.plans.map(p=>({...p,path:p.path&&{...p.path}})),primitive:instance.cloudDrawing});
   return instance;
  },unmount(){}},
  SpikeChartHistory:{create:()=>({merge:value=>value,observe(){},pause(){},resume(){},retry(){}})},
 };
 const document={documentElement:{dataset:{theme:'light'}}};
 const sandbox={window,document,fetch,AbortController,URLSearchParams,setTimeout,clearTimeout,MutationObserver:class{observe(){}}};
 vm.runInNewContext(fs.readFileSync(require.resolve('../../yoyo/monitor/static/cloud-signal-chart.js'),'utf8'),sandbox);
 return {chart:window.SpikeCloudChart.create(host,notice,path=>excursions.push(path&&{...path}),hooks),mounts,drawings,excursions,feedCreates,notice,span,retryButton};
}
function response(value,status=200){return {ok:status>=200&&status<300,status,json:async()=>value};}
function chartValue(symbol,period,signal,weeklyRanges=[]){
 const step=STEPS[period],end=Math.min(Math.floor((signal.confirmed_ms-1)/step)*step+81*step,Math.floor(Date.now()/step)*step);
 return {symbol,bar:period,native_bar:period==='12H'?'12Hutc':period==='1D'?'1Dutc':period,source:'OKX',now_ms:Date.now(),last_close_ms:end,weekly_ranges:weeklyRanges,candles:[{time:end-step,open:100,high:110,low:90,close:105,volume:1}]};
}
function stopSignal(id,confirmed_ms,week,symbol='BTC-USDT-SWAP'){
 const range={state:'ready',symbol,week,start_ms:0,end_ms:604800000,expires_ms:1209600000,high:120,low:104,upper:124,lower:100};
 return {...item,id,symbol,side:1,signal_open_ms:confirmed_ms-STEPS['12H'],confirmed_ms,week,
  entry:100,stop:98.5,stop_pct:.015,target:124,planned_rr:16,plan_state:'ready',plan_reason:'',
  ema20:120,ema50:110,range,signal_bar:{open:100,high:105,low:99,close:104},provenance:'backfill'};
}
function stopChartValue(signal,period='12H'){
 const step=STEPS[period],candles=period==='12H'?
  [
   {time:0,open:100,high:106,low:99,close:105,volume:1},
   {time:step,open:105,high:107,low:99,close:104,volume:1},
   {time:2*step,open:100,high:110,low:98.3,close:105,volume:1},
   {time:3*step,open:105,high:108,low:99,close:104,volume:1},
  ]:[
   {time:signal.confirmed_ms,open:100,high:110,low:98.3,close:105,volume:1},
   {time:signal.confirmed_ms+step,open:105,high:108,low:99,close:104,volume:1},
  ];
 return {symbol:signal.symbol,bar:period,native_bar:period==='12H'?'12Hutc':period,source:'OKX',now_ms:Date.now(),
  last_close_ms:candles.at(-1).time+step,weekly_ranges:[],candles};
}
const tick=()=>new Promise(resolve=>setImmediate(resolve));
test('chart preserves the authoritative 12H cloud at confirmation and continues its recurrence',()=>{
 const rows=[{time:0,close:100},{time:43200,close:110},{time:86400,close:120}];
 const p=averages(rows,STEPS['12H'],item,'12H');
 assert.equal(p[1].a,108);assert.equal(p[1].b,103);assert.equal(p[2].a,108+12*2/21);
 assert.deepEqual(averages(rows.slice(0,2),STEPS['12H'],item,'12H'),p.slice(0,2));
 assert.equal(averages([{time:0,close:100},{time:86400,close:120}],STEPS['12H'],item,'12H')[1].a,120);
 assert.notEqual(averages(rows,STEPS['1H'],item,'1H')[1].a,108);
});
test('fractional timestamp geometry survives lower timeframe changes and empty windows',()=>{
 const rows=[{time:0},{time:3600},{time:7200}];
 assert.equal(indexAt(rows,1800000,STEPS['1H']),.5);
 assert.equal(indexAt(rows,10800000,STEPS['1H']),3);
 assert.equal(indexAt(rows,-3600000,STEPS['1H']),-1);
 assert.equal(indexAt([],0,STEPS['1H']),null);
});
test('weekly reference lines stay frozen and start only after the anchor was available',()=>{
 const before=JSON.stringify(item);const lines=levels(item);
 assert.deepEqual(lines.map(x=>x.price),[125,105,135,95]);
 assert.ok(lines.every(x=>x.start_ms===0&&x.end_ms===604800000));
 assert.equal(JSON.stringify(item),before);
});
test('signal filters and R sort never mutate the source order or plan',()=>{
 const source=[{...item,confirmed_ms:1},{...item,id:'two',symbol:'ETH-USDT-SWAP',side:-1,confirmed_ms:2,planned_rr:5}];
 const before=JSON.stringify(source);
 assert.equal(select(source,'','', 'rr')[0].id,'two');
 assert.equal(select(source,'BTC/USDT','1','newest')[0].id,'one');
 assert.equal(select(source,'','-1','oldest').length,1);
 assert.equal(JSON.stringify(source),before);
});
test('TradingView uses 720 for 12H and an existing Windows-compatible interval otherwise',()=>{
 global.window={SpikeMondayList:{tradingViewURL:require('../../yoyo/monitor/static/monday-list-model.js').tradingViewURL}};
 assert.match(tvURL('BTC-USDT-SWAP','12H','Win32'),/https:.*interval=720$/);
 assert.match(tvURL('BTC-USDT-SWAP','4H','Win32'),/^spike-tradingview:.*timeframe=4H$/);
 assert.match(tvURL('BTC-USDT-SWAP','1D','Win32'),/timeframe=1Dutc$/);
 assert.match(tvURL('BTC-USDT-SWAP','1H','MacIntel'),/interval=60$/);
 delete global.window;
});
test('candle day overrides follow NY midnight through both DST transitions',()=>{
 const category=iso=>colors.category(Date.parse(iso)/1000);
 assert.equal(category('2026-03-09T03:59:59Z'),'weekend');
 assert.equal(category('2026-03-09T04:00:00Z'),'monday');
 assert.equal(category('2026-03-10T03:59:59Z'),'monday');
 assert.equal(category('2026-03-10T04:00:00Z'),null);
 assert.equal(category('2026-11-02T04:59:59Z'),'weekend');
 assert.equal(category('2026-11-02T05:00:00Z'),'monday');
 assert.equal(category('2026-11-07T04:59:59Z'),null);
 assert.equal(category('2026-11-07T05:00:00Z'),'weekend');
 const paint=colors.colorizer({up:'#112233',down:'#445566',monday:'#778899',weekend:'#aabbcc'});
 const row={time:Date.parse('2026-11-02T12:00Z')/1000,open:100,close:90};
 assert.equal(paint(row),'#778899');assert.equal(paint({...row,close:110}),'#778899');
 assert.equal(paint({...row,time:Date.parse('2026-11-03T12:00Z')/1000}),'#445566');
 assert.equal(paint({...row,time:Date.parse('2026-11-03T12:00Z')/1000,close:110}),'#112233');
});
test('saved palette survives serialization, invalid fields and unavailable storage',()=>{
 let value='corrupted';const storage={getItem:()=>value,setItem:(key,v)=>{assert.equal(key,colors.KEY);value=v;}};
 assert.deepEqual(colors.read(storage),colors.DEFAULTS);
 assert.equal(colors.write(storage,{up:'#abc',down:'url(secret)',monday:'#e29b40',weekend:'#56789a'}),true);
 assert.deepEqual(colors.read(storage),{up:'#AABBCC',down:'#8B9099',monday:'#E29B40',weekend:'#56789A'});
 assert.deepEqual(colors.read(null),colors.DEFAULTS);assert.equal(colors.write(null,{}),false);
 assert.deepEqual(colors.normalize({}),colors.DEFAULTS);
});
test('legacy all-gray palette adopts green Monday once and preserves custom or newly saved gray',()=>{
 const gray={up:'#8B9099',down:'#8B9099',monday:'#8B9099',weekend:'#8B9099'};
 let saved=JSON.stringify(gray);const storage={getItem:()=>saved,setItem:(_,v)=>{saved=v;}};
 assert.equal(colors.read(storage).monday,'#30D158');
 assert.equal(JSON.parse(saved).paletteVersion,2);
 colors.write(storage,gray);assert.deepEqual(colors.read(storage),gray);
 const custom={...gray,up:'#123456'};saved=JSON.stringify(custom);
 assert.deepEqual(colors.read(storage),custom);
 const row={time:Date.parse('2026-11-02T12:00Z')/1000,open:100,close:90};
 assert.equal(colors.colorizer()(row),'#30D158');
});

test('key candle is the closing sub-bar, including 5m and daily boundaries',()=>{
 const {keyCandleTime}=require('../../yoyo/monitor/static/cloud-signal-chart.js');
 const signal={confirmed_ms:Date.parse('2026-11-02T00:00Z')};
 assert.equal(keyCandleTime(signal,'12H')*1000,Date.parse('2026-11-01T12:00Z'));
 assert.equal(keyCandleTime(signal,'5m')*1000,Date.parse('2026-11-01T23:55Z'));
 assert.equal(keyCandleTime(signal,'1D')*1000,Date.parse('2026-11-01T00:00Z'));
});
test('historical key candles are highlighted across periods, deduplicated and scoped to the symbol',()=>{
 const {keyCandleTimes,keyCandleTime}=require('../../yoyo/monitor/static/cloud-signal-chart.js');
 const selected={...item,confirmed_ms:Date.parse('2026-11-02T00:00Z')};
 const old={...item,id:'aug30',confirmed_ms:Date.parse('2026-08-31T00:00Z')};
 const rows=[old,old,{...old,symbol:'OTHER',confirmed_ms:old.confirmed_ms-43200000}];
 for(const period of ['12H','5m','1D']){
  const times=keyCandleTimes(selected,rows,period);
  assert.deepEqual([...times],[keyCandleTime(old,period),keyCandleTime(selected,period)]);
 }
 assert.deepEqual([...keyCandleTimes(selected,[],'12H')],[keyCandleTime(selected,'12H')]);
});
test('historical cloud weeks keep their expiry, saved anchor, neutral colors and no origin dot',()=>{
 const {historicalLevels}=require('../../yoyo/monitor/static/cloud-signal-chart.js');
 const current={...item,range:{...item.range,start_ms:1000,end_ms:2000,expires_ms:9000}};
 const weeks=[{...current.range,high:999},{...current.range,start_ms:-7000,end_ms:-6000,expires_ms:1000}];
 const before=JSON.stringify([current,weeks]),lines=historicalLevels(current,weeks);
 assert.equal(lines.length,8);assert.equal(lines[0].price,125);assert.equal(lines[0].historical,false);
 assert.ok(lines.slice(4).every(l=>l.historical&&l.end_ms===1000));
 assert.ok(lines.every(l=>l.startMarkerVisible===false));
 assert.ok(lines.filter(l=>l.kind==='range').every(l=>l.color==='#9399A2'));
 assert.ok(lines.filter(l=>l.kind==='fib').every(l=>l.color==='#17191D'));
 assert.ok(historicalLevels(current,weeks,true).filter(l=>l.kind==='fib').every(l=>l.color==='#E3E7EC'));
 assert.equal(JSON.stringify([current,weeks]),before);
});
test('cloud fills meet at exact crossings and never bridge missing candles',()=>{
 const {cloudSegments}=require('../../yoyo/monitor/static/cloud-signal-chart.js');
 const segments=cloudSegments([{time:0,a:12,b:10},{time:60,a:8,b:10},{time:120,a:9,b:10},{time:240,a:12,b:10},{time:300,a:13,b:10}],60000);
 assert.equal(segments.length,3);assert.deepEqual(segments[0].at(-1),{time:30,a:10,b:10});
 assert.deepEqual(segments[1][0],segments[0].at(-1));assert.equal(segments[1].at(-1).time,120);assert.equal(segments[2][0].time,240);
});
test('cloud paint input is bounded to the visible logical range plus crossing neighbors',()=>{
 const {visibleCloudPoints,cloudSegments}=require('../../yoyo/monitor/static/cloud-signal-chart.js');
 const points=Array.from({length:2000},(_,i)=>({time:i*60,a:100+Math.sin(i/7),b:100}));
 const visible=visibleCloudPoints(points,{from:900.25,to:960.5});
 assert.equal(visible.length,64);assert.equal(visible[0],points[899]);assert.equal(visible.at(-1),points[962]);
 assert.deepEqual(visibleCloudPoints(points,{from:0,to:1999}),points);
 assert.ok(cloudSegments(visible,60000).length>0);assert.ok(visible.length/points.length<.04);
});
test('signal chart cache freshness follows the confirmed period boundary and bounded horizon',()=>{
 const {signalChartWindowEnd,isFreshSignalChart}=require('../../yoyo/monitor/static/cloud-signal-chart.js');
 const now=Date.parse('2026-11-03T07:00:00Z'),step=STEPS['12H'],signal={...item,confirmed_ms:now-step};
 const currentEnd=signalChartWindowEnd(signal,'12H',now),value={symbol:signal.symbol,bar:'12H',last_close_ms:currentEnd};
 assert.equal(isFreshSignalChart(signal,'12H',value,now),true);assert.equal(isFreshSignalChart(signal,'12H',value,now+step),false);
 const old={...signal,confirmed_ms:Date.parse('2026-01-01T12:00:00Z')},oldEnd=signalChartWindowEnd(old,'12H',now);
 assert.equal(isFreshSignalChart(old,'12H',{symbol:old.symbol,bar:'12H',last_close_ms:oldEnd},now+365*STEPS['1D']),true);
 assert.equal(isFreshSignalChart(old,'12H',{symbol:'ETH-USDT-SWAP',bar:'12H',last_close_ms:oldEnd},now),false);
});
test('live cards expire without changing historical provenance; future and delayed records never become live',()=>{
 const {lifecycle}=require('../../yoyo/monitor/static/cloud-signals.js');
 const s={confirmed_ms:10000,provenance:'live',historical:false};
 assert.equal(lifecycle(s,10000).label,'新确认');
 assert.equal(lifecycle(s,1810000).label,'实时捕获');assert.equal(lifecycle(s,1810000).live,true);
 assert.equal(lifecycle(s,43210000).live,false);assert.equal(lifecycle(s,9999).live,false);
 for(const origin of ['backfill','delayed'])assert.equal(lifecycle({...s,provenance:origin},10000).live,false);
 assert.equal(lifecycle({confirmed_ms:10000},10000).label,'历史补录');
});
test('5m TV link matches the chart period',()=>{
 global.window={SpikeMondayList:{tradingViewURL:require('../../yoyo/monitor/static/monday-list-model.js').tradingViewURL}};
 assert.match(tvURL('BTC-USDT-SWAP','5m','MacIntel'),/interval=5$/);delete global.window;
});
test('a symbol-only chart has no fabricated key candle or reference and still draws historical levels',()=>{
 const {keyCandleTimes,planWindows,historicalLevels}=require('../../yoyo/monitor/static/cloud-signal-chart.js');
 const context={symbol:item.symbol,id:'context',context_only:true,confirmed_ms:item.confirmed_ms};
 assert.equal(keyCandleTimes(context,[],'12H').size,0);assert.deepEqual(planWindows(context),[]);
 assert.equal(historicalLevels(context,[item.range]).length,4);
 const cloud=averages([{time:0,close:100}],STEPS['12H'],context,'12H');assert.equal(cloud[0].a,100);
});
test('Monday and 1.618 reconstructed weeks cannot reuse one another’s cached references',async()=>{
 const {createPlanHistory}=require('../../yoyo/monitor/static/cloud-signal-chart.js');const cache=new Map(),calls=[],updates=[];
 for(const rangeMode of ['fib','monday']){
  const reader=createPlanHistory({symbol:item.symbol,rangeMode,completedCache:cache,changed:r=>updates.push(r),request:async url=>{
   const p=new URL(url,'http://local').searchParams;calls.push(p.get('range_mode'));return response({symbol:item.symbol,week:'2026-09-14',state:'ready',complete:true,items:[{...item,id:rangeMode,range_mode:rangeMode}]});
  }});await reader.observe([{symbol:item.symbol,week:'2026-09-14',state:'ready'}]);await reader.resume();
 }assert.deepEqual(calls,['fib','monday']);assert.equal(updates.at(-1)[0].id,'monday');
});
test('all symbol plans are bounded and latest is chronological, independent of selected card',()=>{
 const {planWindows}=require('../../yoyo/monitor/static/cloud-signal-chart.js');
 const old={...item,id:'old',confirmed_ms:0};
 const next={...item,id:'next',confirmed_ms:2*STEPS['12H']};
 const latest={...item,id:'latest',confirmed_ms:30*STEPS['12H']};
 const wrongSymbol={...latest,id:'other',symbol:'ETH-USDT-SWAP'};
 const source=[latest,wrongSymbol,next,old,next],before=JSON.stringify(source);
 const plans=planWindows(old,source,'latest');
 assert.deepEqual(plans.map(p=>p.id),['old','next','latest']);
 assert.equal(plans[0].end_ms,next.confirmed_ms);
 assert.equal(plans[1].end_ms,next.confirmed_ms+12*STEPS['12H']);
 assert.equal(plans[0].selected,true);assert.equal(plans[0].latest,false);
 assert.equal(plans[2].latest,true);assert.equal(JSON.stringify(source),before);
 assert.deepEqual(planWindows({...item,stop:120,planned_rr:null}),[]);
});
test('historical plans retain paired bright risk and reward fills, without entry marks',()=>{
 const {planPalette,planMarks}=require('../../yoyo/monitor/static/cloud-signal-chart.js');
 const opacity=c=>Number(c.split(',').at(-1).replace(')',''));
 for(const dark of [false,true]){
  const active=planPalette(true,dark),history=planPalette(false,dark);
  assert.equal(history.reward,active.reward);assert.ok(opacity(history.reward)>=.2);
  assert.equal(history.risk,active.risk);assert.ok(opacity(history.risk)>=.25);
  const path={lowerR:12.4,upperR:12.4,peak:246.4,ambiguous:false};
  const marks=planMarks(item,path,x=>x,active);
  assert.deepEqual(marks.map(m=>m.kind),['r','r','peak']);
  assert.deepEqual(marks.map(m=>m.label),['5R','10R','12.4R']);
  assert.deepEqual(marks.map(m=>m.p),[165,220,246.4]);
  assert.equal(planMarks(item,null,x=>x,history).length,0);
  const uncertain=planMarks(item,{...path,lowerR:0,upperR:4.65,ambiguous:true},x=>x,history);
  assert.deepEqual(uncertain.map(m=>m.label),['0–4.65R']);

 }
});
test('historical 12H cloud anchors stay exact while switching period leaves plan prices unchanged',()=>{
 const {planWindows}=require('../../yoyo/monitor/static/cloud-signal-chart.js');
 const old={...item,signal_open_ms:0,ema20:104,ema50:102};
 const rows=[{time:0,close:100},{time:43200,close:110},{time:86400,close:120}];
 const cloud=averages(rows,STEPS['12H'],item,'12H',[old,item]);
 assert.equal(cloud[0].a,104);assert.equal(cloud[1].a,108);
 const before=JSON.stringify(planWindows(item));
 averages(rows,STEPS['5m'],item,'5m',[old,item]);
 assert.equal(JSON.stringify(planWindows(item)),before);
});
test('reconstructed history merges without changing saved signal prices or latest identity',()=>{
 const {mergePlans,planWindows}=require('../../yoyo/monitor/static/cloud-signal-chart.js');
 const old={...item,id:'aug',confirmed_ms:0},saved={...item,id:'nov',confirmed_ms:90*STEPS['12H']};
 const plans=mergePlans([saved],[old,{...saved,stop:1}]);
 assert.deepEqual(plans.map(s=>s.id),['aug','nov']);assert.equal(plans[1].stop,saved.stop);
 assert.equal(planWindows(saved,plans,saved.id)[0].latest,false);
});
test('historical plans follow loaded weeks, cache finished weeks and retry partial weeks',async()=>{
 const {createPlanHistory}=require('../../yoyo/monitor/static/cloud-signal-chart.js');
 const requests=[],updates=[];let now=100;
 const reader=createPlanHistory({symbol:item.symbol,now:()=>now,changed:r=>updates.push(r),request:async url=>{
  const week=new URL(url,'https://local').searchParams.get('week');requests.push(week);
  return {ok:true,json:async()=>({symbol:item.symbol,week,state:'ready',complete:week==='2026-08-24',items:[{...item,id:week}]})};
 }});
 const weeks=['2026-08-24','2026-11-02'].map(week=>({symbol:item.symbol,week,state:'ready'}));
 await reader.observe(weeks);assert.equal(requests.length,0);
 await reader.resume();assert.deepEqual(requests,['2026-08-24','2026-11-02']);assert.equal(updates.at(-1).length,2);
 await reader.observe(weeks);assert.equal(requests.length,2);
 now+=30001;await reader.observe(weeks);assert.equal(requests.length,3);assert.equal(requests[2],'2026-11-02');
});
test('period or symbol cancellation discards late history and retries after resume',async()=>{
 const {createPlanHistory}=require('../../yoyo/monitor/static/cloud-signal-chart.js');
 let release,aborted,updates=0;
 const week={symbol:item.symbol,week:'2026-08-24',state:'ready'};
 const reader=createPlanHistory({symbol:item.symbol,changed:()=>updates++,request:async(url,options)=>{
  aborted=options.signal;await new Promise(resolve=>{release=resolve;});
  return {ok:true,json:async()=>({symbol:item.symbol,week:week.week,state:'ready',complete:true,items:[item]})};
 }});
 await reader.resume();const pending=reader.observe([week]);reader.pause();assert.equal(aborted.aborted,true);
 release();await pending;assert.equal(updates,0);
 const resumed=reader.resume();release();await resumed;assert.equal(updates,1);
});
test('failed historical week stays retryable and cannot publish wrong-symbol plans',async()=>{
 const {createPlanHistory}=require('../../yoyo/monitor/static/cloud-signal-chart.js');
 let wrong=true,updates=0;const errors=[];
 const week={symbol:item.symbol,week:'2026-08-24',state:'ready'};
 const reader=createPlanHistory({symbol:item.symbol,changed:()=>updates++,status:s=>errors.push(s),request:async()=>({ok:true,json:async()=>({symbol:item.symbol,week:week.week,state:'ready',complete:true,items:[{...item,symbol:wrong?'WRONG':item.symbol}]})})});
 await reader.resume();await reader.observe([week]);assert.equal(updates,0);assert.match(errors.at(-1),/不完整/);
 wrong=false;await reader.retry();assert.equal(updates,1);
});
test('completed weekly frames restore across readers without another request',async()=>{
 const {createPlanHistory}=require('../../yoyo/monitor/static/cloud-signal-chart.js');
 const cache=new Map(),week={symbol:item.symbol,week:'2026-08-24',state:'ready'};let requests=0;const updates=[];
 const make=changed=>createPlanHistory({symbol:item.symbol,completedCache:cache,now:()=>1000,changed:rows=>changed.push(rows),request:async url=>{
  requests++;const requested=new URL(url,'https://local').searchParams.get('week');
  return response({symbol:item.symbol,week:requested,state:'ready',complete:true,items:[{...item,id:requested}]});
 }});
 const first=make(updates);await first.observe([week]);await first.resume();assert.equal(requests,1);
 const reopenedUpdates=[],reopened=make(reopenedUpdates);await reopened.observe([week]);
 assert.equal(requests,1);assert.equal(reopenedUpdates.at(-1)[0].id,week.week);
 await reopened.resume();assert.equal(requests,1);
});
test('reopening a chart reuses completed weekly frames but revalidates current symbol plans',async()=>{
 const week={symbol:item.symbol,week:'2026-08-24',state:'ready'};let weekRequests=0,planRequests=0;
 const h=cloudChartHarness(url=>{
  if(url.includes('/trade-candles'))return Promise.resolve(response(chartValue(item.symbol,'12H',item,[week])));
  const params=new URL(url,'https://local').searchParams;
  if(params.has('week')){weekRequests++;return Promise.resolve(response({symbol:item.symbol,week:week.week,state:'ready',complete:true,items:[{...item,id:week.week}]}));}
  planRequests++;return Promise.resolve(response({symbol:item.symbol,items:[item],latest_id:item.id,reconstruction_supported:true}));
 });
 await h.chart.show(item);await tick();await tick();assert.equal(weekRequests,1);assert.equal(planRequests,1);
 h.chart.close();await h.chart.show(item);await tick();await tick();
 assert.equal(weekRequests,1);assert.equal(planRequests,2);
});
test('incomplete and failed weekly frames are never shared as completed cache entries',async()=>{
 const {createPlanHistory}=require('../../yoyo/monitor/static/cloud-signal-chart.js');
 const cache=new Map(),week={symbol:item.symbol,week:'2026-08-24',state:'ready'};let requests=0;
 const make=()=>createPlanHistory({symbol:item.symbol,completedCache:cache,now:()=>1000,request:async url=>{
  requests++;const requested=new URL(url,'https://local').searchParams.get('week');
  if(requests===1)return response({symbol:item.symbol,week:requested,state:'ready',complete:false,items:[item]});
  if(requests===2)return response({},503);
  return response({symbol:item.symbol,week:requested,state:'ready',complete:true,items:[item]});
 }});
 for(let i=0;i<3;i++){const reader=make();await reader.observe([week]);await reader.resume();}
 assert.equal(requests,3);assert.equal(cache.size,1);
 const restored=make();await restored.observe([week]);await restored.resume();assert.equal(requests,3);
});
test('completed weekly frame cache keys keep identical weeks separate by exact symbol',async()=>{
 const {createPlanHistory}=require('../../yoyo/monitor/static/cloud-signal-chart.js');
 const cache=new Map(),week='2026-08-24';let requests=0;
 for(const symbol of ['BTC-USDT-SWAP','ETH-USDT-SWAP']){
  const reader=createPlanHistory({symbol,completedCache:cache,now:()=>1000,request:async url=>{
   requests++;const requested=new URL(url,'https://local').searchParams.get('week');
   return response({symbol,week:requested,state:'ready',complete:true,items:[{...item,symbol,id:symbol}]});
  }});
  await reader.observe([{symbol,week,state:'ready'}]);await reader.resume();
 }
 assert.equal(requests,2);assert.equal(cache.size,2);
});
test('completed weekly frame cache expires after five minutes and evicts beyond 64 entries',async()=>{
 const {createPlanHistory}=require('../../yoyo/monitor/static/cloud-signal-chart.js');
 const cache=new Map();let now=1000,requests=0;
 const make=(symbol,week)=>createPlanHistory({symbol,completedCache:cache,now:()=>now,request:async url=>{
  requests++;const requested=new URL(url,'https://local').searchParams.get('week');
  return response({symbol,week:requested,state:'ready',complete:true,items:[{...item,symbol,id:requested}]});
 }});
 const week='2026-08-24',first=make(item.symbol,week);await first.observe([{symbol:item.symbol,week,state:'ready'}]);await first.resume();
 now+=300000;const expired=make(item.symbol,week);await expired.observe([{symbol:item.symbol,week,state:'ready'}]);await expired.resume();assert.equal(requests,2);
 cache.clear();requests=0;now=1000;
 const weeks=Array.from({length:65},(_,i)=>new Date(Date.UTC(2026,0,5+i*7)).toISOString().slice(0,10));
 for(const value of weeks){const reader=make(item.symbol,value);await reader.observe([{symbol:item.symbol,week:value,state:'ready'}]);await reader.resume();}
 assert.equal(cache.size,64);const oldest=make(item.symbol,weeks[0]);await oldest.observe([{symbol:item.symbol,week:weeks[0],state:'ready'}]);await oldest.resume();
 assert.equal(requests,66);assert.equal(cache.size,64);
});
test('signal candles render while symbol plan history is still deferred',async()=>{
 let release;const held=new Promise(resolve=>release=resolve);
 const h=cloudChartHarness(url=>url.includes('/trade-candles')?Promise.resolve(response(chartValue(item.symbol,'12H',item))):held);
 await h.chart.show(item);
 assert.equal(h.mounts.at(-1).bar,'12H');assert.equal(h.mounts.at(-1).candles.length,1);assert.equal(h.notice.hidden,true);
 const before=h.mounts.length;release(response({symbol:item.symbol,items:[item],latest_id:item.id,reconstruction_supported:true}));
 await tick();await tick();assert.ok(h.mounts.length>before);
});
test('failed symbol plan history stays retryable without hiding already loaded candles',async()=>{
 let planCalls=0;
 const h=cloudChartHarness(url=>{
  if(url.includes('/trade-candles'))return Promise.resolve(response(chartValue(item.symbol,'12H',item)));
  planCalls++;return Promise.resolve(planCalls===1?response({},503):response({symbol:item.symbol,items:[item],latest_id:item.id,reconstruction_supported:true}));
 });
 await h.chart.show(item);await tick();
 assert.equal(h.mounts.at(-1).candles.length,1);assert.equal(h.notice.hidden,false);assert.match(h.span.textContent,/读取失败/);
 await h.chart.refreshPlans();assert.equal(planCalls,2);assert.equal(h.notice.hidden,true);assert.equal(h.mounts.at(-1).candles.length,1);
});
test('period changes cancel stale chart requests and ignore late plan results',async()=>{
 let releaseFirst;const first=new Promise(resolve=>releaseFirst=resolve),signals=[];let planCalls=0;
 const h=cloudChartHarness((url,options)=>{
  if(url.includes('/trade-candles')){const period=new URL(url,'https://local').searchParams.get('period');return Promise.resolve(response(chartValue(item.symbol,period,item)));}
  signals.push(options.signal);planCalls++;return planCalls===1?first:Promise.resolve(response({symbol:item.symbol,items:[item],latest_id:'fresh',reconstruction_supported:true}));
 });
 await h.chart.show(item);await h.chart.period('5m');await tick();
 assert.equal(signals[0].aborted,true);assert.equal(h.mounts.at(-1).bar,'5m');
 const before=h.mounts.length;releaseFirst(response({symbol:item.symbol,items:[{...item,id:'stale'}],latest_id:'stale',reconstruction_supported:true}));
 await tick();await tick();assert.equal(h.mounts.length,before);assert.equal(planCalls,2);
});
test('fresh signal candle windows are reused across period switches and dialog reopen',async()=>{
 let candleCalls=0;
 const h=cloudChartHarness(url=>{
  if(url.includes('/trade-candles')){candleCalls++;const period=new URL(url,'https://local').searchParams.get('period');return Promise.resolve(response(chartValue(item.symbol,period,item)));}
  return Promise.resolve(response({symbol:item.symbol,items:[item],latest_id:item.id,reconstruction_supported:true}));
 });
 await h.chart.show(item);await h.chart.period('5m');await h.chart.period('12H');
 assert.equal(candleCalls,3);assert.equal(h.mounts.at(-1).bar,'12H');
 h.chart.close();await h.chart.show(item);assert.equal(candleCalls,3);
});

test('stop preference changes reproject selected and historical plans and recalculate a stopped peak without refetching',async()=>{
 const current=stopSignal('current',2*STEPS['12H'],'2026-11-02');
 const historical=stopSignal('historical',0,'2026-10-26');
 const before=JSON.stringify([current,historical]);let candleCalls=0,planCalls=0;
 const h=cloudChartHarness(url=>{
  if(url.includes('/trade-candles')){candleCalls++;return Promise.resolve(response(stopChartValue(current)));}
  planCalls++;return Promise.resolve(response({symbol:current.symbol,items:[historical,current],latest_id:current.id,reconstruction_supported:true}));
 });
 await h.chart.show(current);await tick();await tick();
 const originalDraw=h.drawings.at(-1),originalCurrent=originalDraw.plans.find(p=>p.id==='current');
 const originalHistorical=originalDraw.plans.find(p=>p.id==='historical');
 assert.ok(originalCurrent&&originalHistorical);
 assert.ok(Math.abs(originalCurrent.stop-98.5)<1e-10);
 assert.equal(originalCurrent.path.state,'stopped');
 assert.equal(h.excursions.at(-1).state,'stopped');
 const requests=[candleCalls,planCalls];

 h.chart.stops({btcEth:4,other:5});

 const changed=h.drawings.at(-1),changedCurrent=changed.plans.find(p=>p.id==='current');
 const changedHistorical=changed.plans.find(p=>p.id==='historical');
 assert.ok(Math.abs(changedCurrent.stop-96)<1e-10);
 assert.ok(Math.abs(changedHistorical.stop-96)<1e-10);
 assert.equal(changedCurrent.path.state,'open');
 assert.ok(Math.abs(changedCurrent.path.lowerR-2.5)<1e-10);
 assert.equal(h.excursions.at(-1).state,'open');
 assert.ok(Math.abs(h.excursions.at(-1).lowerR-2.5)<1e-10);
 assert.deepEqual([candleCalls,planCalls],requests);
 assert.equal(JSON.stringify([current,historical]),before);
});

test('delayed plan history and a period switch both apply the latest stop preference',async()=>{
 let releaseFirst;const held=new Promise(resolve=>releaseFirst=resolve);
 const current=stopSignal('current',2*STEPS['12H'],'2026-11-02');
 const historical=stopSignal('history-new',0,'2026-10-26');
 const stale=stopSignal('history-stale',0,'2026-10-19');let historyCalls=0,candleCalls=0;
 const h=cloudChartHarness(url=>{
  if(url.includes('/trade-candles')){candleCalls++;const period=new URL(url,'https://local').searchParams.get('period');return Promise.resolve(response(stopChartValue(current,period)));}
  historyCalls++;
  return historyCalls===1?held:Promise.resolve(response({symbol:current.symbol,items:[historical,current],latest_id:current.id,reconstruction_supported:true}));
 });

 await h.chart.show(current);
 h.chart.stops({btcEth:4,other:5});
 await h.chart.period('5m');await tick();await tick();
 const afterSwitch=h.drawings.at(-1);
 assert.ok(afterSwitch.plans.some(p=>p.id==='history-new'&&Math.abs(p.stop-96)<1e-10));
 assert.ok(afterSwitch.plans.some(p=>p.id==='current'&&Math.abs(p.stop-96)<1e-10));
 assert.equal(historyCalls,2);assert.equal(candleCalls,3);

 releaseFirst(response({symbol:current.symbol,items:[stale,current],latest_id:current.id,reconstruction_supported:true}));
 await tick();await tick();
 const afterLateResponse=h.drawings.at(-1);
 assert.ok(afterLateResponse.plans.some(p=>p.id==='history-new'&&Math.abs(p.stop-96)<1e-10));
 assert.ok(!afterLateResponse.plans.some(p=>p.id==='history-stale'));
 assert.equal(h.excursions.at(-1).state,'open');
});

test('switching signals clears the prior excursion while the next candle request is pending',async()=>{
 let holdNext=false,releaseNext;const held=new Promise(resolve=>releaseNext=resolve);
 const first=stopSignal('first',2*STEPS['12H'],'2026-11-02');
 const next=stopSignal('next',2*STEPS['12H'],'2026-11-02','ETH-USDT-SWAP');
 const h=cloudChartHarness(url=>{
  if(url.includes('/trade-candles')){
   if(holdNext)return held;
   return Promise.resolve(response(stopChartValue(first)));
  }
  const symbol=new URL(url,'https://local').searchParams.get('symbol');
  return Promise.resolve(response({symbol,items:[],latest_id:null,reconstruction_supported:true}));
 });
 await h.chart.show(first);assert.ok(h.excursions.at(-1));
 holdNext=true;const pending=h.chart.show(next);
 assert.equal(h.excursions.at(-1),null);
 releaseNext(response(stopChartValue(next)));await pending;await tick();
 assert.notEqual(h.excursions.at(-1),null);
});

const {planExcursion,excursionLabel,rLadder}=require('../../yoyo/monitor/static/cloud-signal-chart.js');
const twelve=STEPS['12H'];
const plan={...item,entry:100,stop:98,target:124,planned_rr:12,confirmed_ms:twelve};
const bar=(i,o,h,l,c,extra={})=>({time:i*twelve,open:o,high:h,low:l,close:c,...extra});
test('R ladder follows the certain pre-stop excursion while the exit bar keeps ambiguity bounds',()=>{
 const rows=[bar(0,100,999,1,100),bar(1,100,130,99,129),bar(2,129,142,125,140),bar(3,140,141,97,99),bar(4,99,999,1,500)];
 const before=JSON.stringify([plan,rows]);const path=planExcursion(plan,rows,5*twelve);
 assert.equal(path.lowerR,21);assert.equal(path.upperR,21);assert.equal(path.peak,142);
 assert.equal(path.stop_ms,3*twelve);assert.equal(path.state,'stopped');assert.equal(path.ambiguous,false);
 assert.equal(path.exit_price,98);assert.equal(path.exit_r,-1);assert.equal(path.exit_time_precision,'within_12H_bar');
 assert.equal(path.through_ms,3*twelve);assert.equal(path.exit_bar_end_ms,4*twelve);
 assert.deepEqual(rLadder(plan,path),[{r:5,price:110},{r:10,price:120}]);
 assert.equal(excursionLabel(path),'21.00R');assert.equal(JSON.stringify([plan,rows]),before);
});
test('BTC Apr 8 reference exits at the first post-confirmation open EMA cross',()=>{
 const confirmed=Date.parse('2026-04-09T00:00:00Z');
 const btc={...plan,side:-1,entry:71613.9874,stop:72688.19721,target:66942.3126,planned_rr:4.34894073035,
  confirmed_ms:confirmed,confirmation_close:71041.7,ema20:68913.11019594768,ema50:68946.30656311511,
  signal_bar:{open:71661.4,high:72876.5,low:70670,close:71041.7}};
 const first={time:confirmed,open:71041.8,high:71548,low:70422,close:71124.5};
 const later={time:confirmed+twelve,open:71124.5,high:99999,low:1,close:50000};
 const path=planExcursion(btc,[first,later],confirmed+2*twelve);
 const threshold=((1-2/51)*btc.ema50-(1-2/21)*btc.ema20)/((2/21)-(2/51));
 assert.ok(Math.abs(threshold-69482.42789286927)<1e-8);
 assert.equal(path.state,'cloud_flipped');assert.equal(path.exit_state,'cloud_flip');
 assert.equal(path.exit_bar_ms,confirmed);assert.equal(path.exit_bar_end_ms,confirmed+twelve);
 assert.equal(path.exit_time_ms,confirmed);assert.equal(path.exit_time_precision,'bar_open');
 assert.equal(path.exit_at_open,true);assert.equal(path.exit_price,71041.8);assert.equal(path.trigger_level,threshold);
 assert.ok(Math.abs(path.exit_r-.5326588848293425)<1e-8);
 assert.ok(Math.abs(path.lowerR-path.exit_r)<1e-10);assert.equal(path.upperR,path.lowerR);
 assert.equal(path.peak,71041.8);assert.equal(path.through_ms,confirmed);
});
test('intrabar EMA cross exits even when the candle closes back in the original cloud color',()=>{
 const long={...plan,side:1,entry:90,stop:70,target:120,ema20:100,ema50:99,signal_bar:{close:90}};
 const row=bar(1,90,95,80,90);
 const threshold=((1-2/51)*long.ema50-(1-2/21)*long.ema20)/((2/21)-(2/51));
 const path=planExcursion(long,[row],2*twelve);
 assert.equal(path.state,'cloud_flipped');assert.equal(path.exit_state,'cloud_flip');
 assert.equal(path.exit_at_open,false);assert.equal(path.exit_time_ms,null);assert.equal(path.exit_time_precision,'within_12H_bar');
 assert.equal(path.exit_bar_ms,twelve);assert.equal(path.exit_bar_end_ms,2*twelve);assert.equal(path.through_ms,twelve);
 assert.ok(Math.abs(path.exit_price-threshold)<1e-10);assert.equal(path.trigger_level,threshold);
 assert.equal(path.lowerR,0);assert.equal(path.upperR,.25);assert.equal(path.peak,90);assert.equal(path.ambiguous,true);
 const short={...plan,side:-1,entry:110,stop:130,target:80,ema20:99,ema50:100};
 const shortPath=planExcursion(short,[bar(1,110,125,105,110)],2*twelve);
 assert.equal(shortPath.exit_state,'cloud_flip');assert.equal(shortPath.exit_at_open,false);
 assert.equal(shortPath.lowerR,0);assert.equal(shortPath.upperR,.25);assert.equal(shortPath.peak,110);
});
test('stop and EMA crossing use the first adverse price boundary inside the 12H range',()=>{
 const long={...plan,side:1,entry:90,stop:85,target:120,ema20:100,ema50:99};
 const row=bar(1,90,95,80,90);
 const stopped=planExcursion(long,[row],2*twelve);
 assert.equal(stopped.exit_state,'stop');assert.equal(stopped.exit_price,85);assert.equal(stopped.trigger_level,85);
 assert.equal(stopped.exit_at_open,false);assert.equal(stopped.lowerR,0);assert.equal(stopped.upperR,1);assert.equal(stopped.ambiguous,true);
 const crossed=planExcursion({...long,stop:75},[row],2*twelve);
 assert.equal(crossed.exit_state,'cloud_flip');assert.ok(crossed.exit_price>75);assert.equal(crossed.trigger_level,crossed.exit_price);
});
test('an opening gap beyond both stop and EMA threshold exits at the open with stop priority',()=>{
 const long={...plan,side:1,entry:90,stop:85,target:120,ema20:100,ema50:99};
 const path=planExcursion(long,[bar(1,80,90,75,80)],2*twelve);
 assert.equal(path.state,'stopped');assert.equal(path.exit_state,'stop');assert.equal(path.exit_at_open,true);
 assert.equal(path.exit_time_ms,twelve);assert.equal(path.exit_price,80);assert.equal(path.trigger_level,85);
 assert.equal(path.exit_r,-2);assert.equal(path.lowerR,0);assert.equal(path.upperR,0);assert.equal(path.through_ms,twelve);
});
test('EMA equality is not an opposite cross, but a later strict intrabar crossing is',()=>{
 const exact={...plan,entry:100,stop:1,ema20:101,ema50:100};
 const close=((1-2/51)*exact.ema50-(1-2/21)*exact.ema20)/((2/21)-(2/51));
 const equalBar=bar(1,100,100,close,close),crossBar=bar(2,close,close,75,75);
 const equal=planExcursion(exact,[equalBar],2*twelve);
 assert.equal(equal.state,'open');assert.equal(equal.exit_state,null);assert.equal(equal.cloud_flip_ms,null);
 const crossed=planExcursion(exact,[equalBar,crossBar],3*twelve);
 assert.equal(crossed.state,'cloud_flipped');assert.equal(crossed.exit_state,'cloud_flip');
 assert.equal(crossed.cloud_flip_ms,2*twelve);assert.equal(crossed.exit_bar_end_ms,3*twelve);
});
test('a current forming 12H row can trigger an exit before it is confirmed',()=>{
 const long={...plan,side:1,entry:90,stop:70,target:120,ema20:100,ema50:99};
 const current=bar(1,90,95,80,90,{confirmed:false});
 const path=planExcursion(long,[current],twelve);
 assert.equal(path.exit_state,'cloud_flip');assert.equal(path.exit_bar_confirmed,false);
 assert.equal(path.exit_bar_ms,twelve);assert.equal(path.exit_time_ms,null);
 assert.equal(path.exit_time_precision,'within_12H_bar');assert.equal(path.through_ms,twelve);
 assert.equal(path.lowerR,0);assert.equal(path.upperR,.25);
});
test('a dedicated 12H forming candle updates the exit while the visible chart is 5m',async()=>{
 const boundary=Math.floor(Date.now()/twelve)*twelve;
 const current={...plan,id:'live-5m-exit',symbol:'BTC-USDT-SWAP',week:'2026-11-02',signal_open_ms:boundary-twelve,confirmed_ms:boundary,
  side:1,entry:90,stop:70,target:120,planned_rr:1,ema20:100,ema50:99.38,confirmation_close:90,
  signal_bar:{open:91,high:95,low:88,close:90},plan_state:'ready',plan_reason:'',
  range:{...item.range,state:'ready',symbol:'BTC-USDT-SWAP',week:'2026-11-02'}};
 const forming={time:boundary,open:90,high:95,low:80,close:90,volume:1,confirmed:false};
 const older={...current,id:'other-visible-plan',week:'2026-10-26',range:{...current.range,week:'2026-10-26'},confirmed_ms:boundary-twelve,signal_open_ms:boundary-2*twelve};
 let pathFeedOptions=null;
 const summaries=[];
 const h=cloudChartHarness(url=>{
  if(url.includes('/trade-candles')){const period=new URL(url,'https://local').searchParams.get('period');return Promise.resolve(response(chartValue(current.symbol,period,current)));}
  return Promise.resolve(response({symbol:current.symbol,items:[older],latest_id:current.id,reconstruction_supported:false}));
 },()=>{},{start:(options,count)=>{
  if(options.period==='12H'&&count===2){pathFeedOptions=options;options.onChange({bars:[forming],now_ms:Date.now(),error:''});}
 }},{stats:value=>summaries.push(value)});
 await h.chart.show(current);await h.chart.period('5m');
 await tick();await tick();await tick();
 const path=h.excursions.filter(Boolean).at(-1),firstUpper=path?.upperR;
 const historicalPath=()=>h.drawings.at(-1).primitive.plans.find(p=>p.id===older.id)?.path;
 const firstHistoricalUpper=historicalPath()?.upperR;
 assert.ok(historicalPath()?.exit_state);assert.equal(historicalPath().exit_bar_confirmed,false);
 assert.equal(h.mounts.at(-1).bar,'5m');assert.equal(h.feedCreates.filter(f=>f.period==='12H').length,2);
 assert.ok(path);assert.equal(path.exit_state,'cloud_flip');assert.equal(path.exit_bar_ms,boundary);
 assert.equal(path.exit_bar_confirmed,false);assert.equal(path.exit_at_open,false);assert.equal(path.exit_time_ms,null);
 assert.ok(firstUpper>0);
 const beforeLayers=summaries.length;
 h.chart.layer('cloud',false);
 assert.equal(summaries.length,beforeLayers);
 const unlatched=planExcursion({...current,stop:current.entry*.985,stop_pct:.015},[{...forming,high:110}],boundary);
 assert.ok(unlatched.upperR>firstUpper);
 pathFeedOptions.onChange({bars:[{...forming,high:110}],now_ms:Date.now(),error:''});
 assert.ok(summaries.length>beforeLayers);
 const improved=h.excursions.filter(Boolean).at(-1);
 assert.equal(improved.upperR,firstUpper);assert.equal(improved.exit_state,'cloud_flip');
 pathFeedOptions.onChange({bars:[{...forming,high:130,confirmed:true}],now_ms:boundary+twelve+1000,error:''});
 const closed=h.excursions.filter(Boolean).at(-1);
 assert.equal(closed.exit_bar_confirmed,true);assert.equal(closed.exit_state,'cloud_flip');assert.equal(closed.upperR,firstUpper);
 assert.equal(historicalPath().upperR,firstHistoricalUpper);assert.equal(historicalPath().exit_bar_confirmed,true);
 await h.chart.period('12H');await tick();await tick();
 const visible12h=h.feedCreates.filter(f=>f.period==='12H').at(-1);
 visible12h.onChange({bars:[{...forming,high:150,confirmed:true}],now_ms:boundary+twelve+1000,error:''});
 const switched=h.excursions.filter(Boolean).at(-1);
 assert.equal(h.mounts.at(-1).bar,'12H');assert.equal(switched.exit_bar_confirmed,true);assert.equal(switched.upperR,firstUpper);
 h.chart.stops({btcEth:10,other:3});
 const changedStop=h.excursions.filter(Boolean).at(-1);
 assert.equal(changedStop.exit_state,'cloud_flip');assert.ok(changedStop.upperR>firstUpper);
});
test('the actual zero-width reference exit keeps its peak label without a profit box',async()=>{
 const confirmed=Date.parse('2026-04-09T00:00:00Z');
 const btc={...plan,id:'btc-open-cross',side:-1,entry:71613.9874,stop:72688.19721,target:66942.3126,planned_rr:4.34894,
  symbol:'BTC-USDT-SWAP',week:'2026-04-06',signal_open_ms:confirmed-twelve,confirmed_ms:confirmed,ema20:68913.11019594768,ema50:68946.30656311511,
  confirmation_close:71041.7,signal_bar:{open:71661.4,high:72876.5,low:70670,close:71041.7},plan_state:'ready',plan_reason:'',range:{...item.range,state:'ready',symbol:'BTC-USDT-SWAP',week:'2026-04-06'}};
 const previous={time:confirmed-twelve,open:71650,high:72800,low:71000,close:71661.4,volume:1};
 const candle={time:confirmed,open:71041.8,high:71548,low:70422,close:71124.5,volume:1};
 const h=cloudChartHarness(url=>{
  if(url.includes('/trade-candles'))return Promise.resolve(response({symbol:btc.symbol,bar:'12H',native_bar:'12Hutc',source:'OKX',last_close_ms:confirmed+twelve,candles:[previous,candle],weekly_ranges:[]}));
  return Promise.resolve(response({symbol:btc.symbol,items:[],latest_id:null,reconstruction_supported:false}));
 });
 await h.chart.show(btc);
 const drawing=h.drawings.at(-1).primitive;drawing.layers.cloud=false;
 const makeTarget=()=>{const calls={texts:[],rects:[]};const context={save(){},beginPath(){},rect(){},clip(){},restore(){},fillRect(...args){calls.rects.push(args);},setLineDash(){},moveTo(){},lineTo(){},stroke(){},closePath(){},fill(){},measureText(value){return {width:value.length*5};},fillText(value){calls.texts.push(value);}};return {calls,useMediaCoordinateSpace(fn){fn({context,mediaSize:{width:500,height:300}});}};};
 const labels=makeTarget();drawing.paint(labels,true);assert.ok(labels.calls.texts.includes('0.53R'));
 const fills=makeTarget();drawing.paint(fills,false);assert.ok(fills.calls.rects.length>=2);assert.ok(fills.calls.rects.every(r=>r[2]===0));
 drawing.layers.range=false;drawing.layers.fib=false;
 drawing.owner.chart.timeScale=()=>({getVisibleLogicalRange:()=>({from:10000,to:10100}),logicalToCoordinate(){throw Error('offscreen plan requested a pixel coordinate');}});
 const offscreen=makeTarget();drawing.paint(offscreen,false);
 assert.equal(offscreen.calls.rects.length,0);assert.equal(drawing.autoscaleInfo(10000,10100),null);
});
test('incomplete coverage and later candle gaps cannot inflate maximum R',()=>{
 assert.equal(planExcursion(plan,[bar(2,100,999,99,500)],3*twelve),null);
 assert.equal(planExcursion(plan,[bar(1,100,110,99,108)],2*twelve).state,'open');
 const path=planExcursion(plan,[bar(1,100,110,99,108),bar(3,108,999,99,500)],4*twelve);
 assert.equal(path.lowerR,5);assert.equal(path.state,'gap');assert.match(excursionLabel(path),/^5.00R/);
 const open=planExcursion(plan,[bar(1,100,110,99,108)],2*twelve);
 assert.match(excursionLabel(open),/^5.00R/);
 assert.equal(planExcursion({...plan,stop:101},[],0),null);
 assert.equal(planExcursion({...plan,ema20:undefined},[bar(1,100,110,99,108)],2*twelve),null);
});
test('ladder size remains bounded for narrow risk, keeping 5R and 10R explicit',()=>{
 const levels=rLadder({...plan,planned_rr:1000000},{lowerR:2000000});
 assert.equal(levels.length,2);assert.deepEqual(levels,[{r:5,price:110},{r:10,price:120}]);
});
test('one first symbol/week survives mixed history, opposite sides and stale selection',()=>{
 const {mergePlans,planWindows,keyCandleTimes,keyCandleTime}=require('../../yoyo/monitor/static/cloud-signal-chart.js');
 const first={...item,id:'first',week:'2026-10-26',confirmed_ms:2*twelve};
 const later={...first,id:'later',confirmed_ms:4*twelve};
 const opposite={...later,id:'opposite',confirmed_ms:5*twelve,side:-1,entry:110,stop:120,target:100,planned_rr:1};
 const next={...first,id:'next',week:'2026-11-02',confirmed_ms:16*twelve};
 const other={...later,id:'other',symbol:'OTHER-USDT-SWAP'};
 const source=[next,later,opposite,other],before=JSON.stringify(source);
 const merged=mergePlans(source,[first,{...later,stop:1}]);
 assert.deepEqual(merged.map(s=>s.id),['first','other','next']);
 assert.equal(merged[0].stop,first.stop);
 const windows=planWindows(later,merged,later.id);
 assert.deepEqual(windows.map(s=>s.id),['first','next']);
 assert.equal(windows[1].latest,true);
 assert.deepEqual([...keyCandleTimes(later,merged,'12H')],[keyCandleTime(first,'12H'),keyCandleTime(next,'12H')]);
 assert.equal(JSON.stringify(source),before);
});
test('a suppressed deep link resolves only through the matching canonical alias',()=>{
 const {selectedSignal}=require('../../yoyo/monitor/static/cloud-signals.js');
 const value={items:[],selected:item,selected_requested_id:'duplicate',selection_redirect_id:item.id};
 assert.equal(selectedSignal(value,'duplicate'),item);
 assert.equal(selectedSignal(value,'unrelated'),null);
 assert.equal(selectedSignal({...value,selection_redirect_id:'wrong'},'duplicate'),null);
 assert.equal(selectedSignal({items:[item]},item.id),item);
 assert.equal(selectedSignal({items:[],selected:item},item.id),item);
});

// A reused wallpaper/chart surface must never show the previous contract's totals.
test('switching chart context clears reference statistics before replacement candles arrive',async()=>{
 const updates=[],releases=[];
 const h=cloudChartHarness(url=>new Promise(resolve=>{releases.push(()=>resolve(response(chartValue('ETH-USDT-SWAP','12H',item))));}),undefined,{}, {stats:(summary,status)=>updates.push({summary,status})});
 const pending=h.chart.showSymbol('ETH-USDT-SWAP');
 assert.equal(updates[0].summary,null);
 assert.equal(updates[0].status.stage,'candles');
 assert.equal(updates[0].status.running,true);
 h.chart.close();releases.forEach(release=>release());await pending;
 assert.equal(updates.length,1,'late response must not restore statistics after leaving the wallpaper');
});
