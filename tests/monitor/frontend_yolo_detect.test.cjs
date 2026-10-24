'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
const source=fs.readFileSync(path.join(__dirname,'../../yoyo/monitor/static/yolo-detect.js'),'utf8');
// Exercise the shipped primitive with the bundled 4.2 integer-only scale behavior.
const code=source.slice(source.indexOf('class DetectionPrimitive{'),source.indexOf('  function destroyChart'));
const Primitive=vm.runInNewContext(code+';DetectionPrimitive');
function primitive(){
 const p=new Primitive([],100000,1000);
 const scale={logicalToCoordinate:i=>Number.isInteger(i)?30+8*i:0,width:()=>1000};
 p.attached({chart:{timeScale:()=>scale},series:{priceToCoordinate:p=>500-p*10},requestUpdate:()=>{}});return p;
}
test('predicted sub-bar boundaries retain nonzero width instead of collapsing to x=0',()=>{
 const p=primitive(),r=p.rect({x_start_ms:102250,x_end_ms:106750,price_high:30,price_low:20});
 assert.deepEqual(Array.from(r),[48,200,84,300]);
});
test('sub-bar extrapolation preserves geometry outside the viewport',()=>{
 const p=primitive();assert.equal(p.x(99400),25.2);assert.equal(p.x(100000),30);
});

const {filteredSymbols,initialSymbol,draftSpec,sameScan,normalizedSymbol,draftProblem,validView}=require('../../yoyo/monitor/static/yolo-detect.js');
const summaries=[{symbol:'BTC-USDT-SWAP',count:0},{symbol:'A-USDT-SWAP',count:1},{symbol:'ETH-USDT-SWAP',count:2},{symbol:'BAD-USDT-SWAP',error:'unavailable'}];
test('patrol symbol deep link resets filters and restores shareable explicit filters',()=>{
 const {patrolFilters}=require('../../yoyo/monitor/static/yolo-detect.js');
 assert.deepEqual(patrolFilters({symbol:'BTC-USDT-SWAP'}),{feedQuery:'BTC-USDT-SWAP',feedDays:7,feedPeriod:'',feedSide:'',feedOrder:'newest'});
 assert.deepEqual(patrolFilters({q:'ETH',days:'0',timeframe:'5m',side:'short',sort:'score'}),{feedQuery:'ETH',feedDays:0,feedPeriod:'5m',feedSide:'short',feedOrder:'score'});
 assert.equal(patrolFilters({symbol:'BTC<script>',days:'-1',timeframe:'3m'}).feedQuery,'');
});
test('opening a new patrol coin clears the previous cards, page and delayed search',()=>{
 const {patrolFilters}=require('../../yoyo/monitor/static/yolo-detect.js'),nodes=new Map();let cleared;
 const state={feedQuery:'ETH',feedDays:1,feedPeriod:'4H',feedSide:'short',feedOrder:'score',feedPage:4,feedBefore:123,feed:{items:['ETH']}};
 const el=id=>{if(!nodes.has(id))nodes.set(id,{});return nodes.get(id);};
 const context={state,patrolFilters,$:el,clearTimeout:id=>cleared=id,html:(id,value)=>el(id).innerHTML=value,error(){}};
 const code=source.slice(source.indexOf('  function restorePatrolFilters('),source.indexOf('  function setTab('));
 const restore=vm.runInNewContext('let feedTimer=123,feedVersion=0;'+code+';restorePatrolFilters',context);
 restore({symbol:'ZAMA-USDT-SWAP'});
 assert.equal(cleared,123);assert.equal(state.feedQuery,'ZAMA-USDT-SWAP');assert.equal(el('yd-feed-search').value,'ZAMA-USDT-SWAP');
 assert.equal(state.feedPage,0);assert.equal(state.feedBefore,null);assert.equal(state.feed,null);
 assert.equal(el('yd-feed-period').value,'');assert.equal(el('yd-feed-side').value,'');assert.equal(el('yd-feed-days').value,'7');
 assert.equal(el('yd-findings').innerHTML,'');assert.equal(el('yd-feed-more').innerHTML,'');
});
test('default browsing contains detected coins, search and failures remain accessible',()=>{
 assert.deepEqual(filteredSymbols(summaries).map(r=>r.symbol),['ETH-USDT-SWAP','A-USDT-SWAP']);
 assert.deepEqual(filteredSymbols(summaries,' btc ','all').map(r=>r.symbol),['BTC-USDT-SWAP']);
 assert.equal(filteredSymbols(summaries,'','failed')[0].symbol,'BAD-USDT-SWAP');
 assert.equal(summaries[0].symbol,'BTC-USDT-SWAP');
});
test('URL coin wins even without detections; fallback preserves error-only jobs',()=>{
 assert.equal(initialSymbol(summaries,'BTC-USDT-SWAP'),'BTC-USDT-SWAP');
 assert.equal(initialSymbol(summaries,'deleted'),'A-USDT-SWAP');
 assert.equal(initialSymbol([summaries[3]]),'BAD-USDT-SWAP');
 assert.equal(initialSymbol([]),'');
});
test('scan drafts normalize symbols and freeze only their own selection and Beijing range',()=>{
 const draft={model:'close',timeframe:'5m',scope:'selected',symbol:' ethusdt ',mode:'history',start:'2026-10-16T08:00',end:'2026-10-16T09:00'};
 const frozen=draftSpec(draft);draft.symbol='BTC';
 assert.deepEqual(frozen,{model_id:'close',timeframe:'5m',scope:'selected',symbols:['ETH-USDT-SWAP'],mode:'history',start_ms:Date.UTC(2026,9,16,0),end_ms:Date.UTC(2026,9,16,1)});
 const all=draftSpec({...draft,mode:'latest',scope:'all'});assert.deepEqual(all.symbols,[]);assert.equal(all.start_ms,undefined);
});
test('newer snapshot suggestions require the same model, timeframe and scope',()=>{
 const a={model_id:'a',mode:'latest',timeframe:'15m',scope:'all',symbols:[]};
 assert.equal(sameScan(a,{...a,symbols:['ETH-USDT-SWAP']}),true);
 for(const delta of [{model_id:'b'},{mode:'history'},{timeframe:'5m'},{scope:'selected'}])assert.equal(sameScan(a,{...a,...delta}),false);
 const one={...a,scope:'selected',symbols:['ETH-USDT-SWAP']};assert.equal(sameScan(one,{...one,symbols:['BTC-USDT-SWAP']}),false);
});
test('history suggestions do not mix different detection intervals',()=>{
 const a={model_id:'a',mode:'history',timeframe:'15m',scope:'all',symbols:[],start_ms:10,end_ms:20};
 assert.equal(sameScan(a,{...a}),true);assert.equal(sameScan(a,{...a,end_ms:30}),false);
});
test('favorites never imply a result exists outside the viewed job',()=>{
 const favorites=['BTC-USDT-SWAP','SOL-USDT-SWAP'];
 assert.deepEqual(filteredSymbols(summaries,'','favorites',favorites).map(r=>r.symbol),['BTC-USDT-SWAP']);
 assert.deepEqual(filteredSymbols(summaries,'sol','favorites',favorites),[]);
});
test('common symbol notation is normalized without turning empty input into a contract',()=>{
 for(const s of ['eth',' ethusdt ','ETH/USDT','ETH/USDT:USDT','ETH-USDT','ETH-USDT-SWAP'])assert.equal(normalizedSymbol(s),'ETH-USDT-SWAP');
 assert.equal(normalizedSymbol(' '),'');
 const d={model:'close',timeframe:'5m',scope:'selected',symbol:'NOTREAL',mode:'latest'};
 assert.equal(draftProblem(d,['ETH-USDT-SWAP']).field,'symbol');
 assert.equal(draftProblem({...d,scope:'all'},[]),null);
 assert.equal(draftProblem({...d,symbol:'ETH/USDT'},['ETH-USDT-SWAP']),null);
});
test('history validation catches future, reversed, unaligned and excessive ranges before submission',()=>{
 const now=Date.UTC(2026,9,16,1,3),d={model:'close',timeframe:'5m',scope:'selected',symbol:'ETH',mode:'history',start:'2026-10-16T08:00',end:'2026-10-16T09:00'};
 assert.equal(draftProblem(d,null,now),null);
 assert.equal(draftProblem({...d,end:'2026-10-16T09:05'},null,now).field,'end');
 assert.match(draftProblem({...d,start:'2026-10-16T08:01'},null,now).message,/不对齐/);
 assert.match(draftProblem({...d,start:d.end},null,now).message,/晚于/);
 assert.match(draftProblem({...d,start:'2026-08-16T08:00'},null,now).message,/10,000/);
 assert.equal(draftProblem({...d,timeframe:'1Dutc',start:'2026-10-15T08:00',end:'2026-10-16T08:00'},null,now),null);
});
test('invalid persisted chart ranges are rejected while a meaningful prior view is retained',()=>{
 assert.deepEqual(validView({from:-14.5,to:30.25}),{from:-14.5,to:30.25});
 for(const r of [null,{}, {from:3,to:2},{from:0,to:Infinity},{from:0,to:1000000}])assert.equal(validView(r),null);
});

function validationHarness(symbol='NOTREAL'){
 const fields=new Map(),messages=[];
 const element=id=>{if(!fields.has(id))fields.set(id,{open:true,textContent:'开始检测',disabled:false,setAttribute(){},removeAttribute(){},focus(){}});return fields.get(id);};
 let resolve;const pending=new Promise(r=>resolve=r);
 const state={draft:{model:'close',timeframe:'5m',scope:'selected',symbol,mode:'latest'},catalog:{items:[{id:'close',available:true}]},symbols:null};
 const context={state,validating:new Set(),$:element,draftProblem,loadSymbols:()=>pending.then(items=>state.symbols=items),error:m=>messages.push(m)};
 const fn=source.slice(source.indexOf('  async function validateDraft('),source.indexOf('  function openPlan('));
 const validate=vm.runInNewContext(fn+';validateDraft',context);
 return {fields,messages,element,resolve,validate};
}
test('submission waits for the symbol list and rejects an unknown symbol after loading',async()=>{
 const h=validationHarness(),result=h.validate('yd');
 assert.equal(h.element('yd-submit').disabled,true);
 assert.equal(h.element('yd-submit').textContent,'正在检查币种…');
 h.resolve([{symbol:'ETH-USDT-SWAP'}]);
 assert.equal(await result,false);assert.match(h.messages.at(-1),/币种列表/);
 assert.equal(h.element('yd-submit').disabled,false);
});
test('closing the draft while symbol validation is pending does not submit later',async()=>{
 const h=validationHarness('ETH'),result=h.validate('yd');h.element('yd-scan-dialog').open=false;
 h.resolve([{symbol:'ETH-USDT-SWAP'}]);assert.equal(await result,false);
});

function feedHarness(){
 const pending=[],messages=[],nodes=new Map();let draws=0;
 const state={active:true,tab:'schedules',feed:null,feedDays:7,feedQuery:'',feedPeriod:'',feedSide:'',feedLimit:30};
 const context={state,URLSearchParams,api:path=>new Promise((resolve,reject)=>pending.push({path,resolve,reject})),
  error:message=>messages.push(message),$:id=>{if(!nodes.has(id))nodes.set(id,{});return nodes.get(id);},drawFeed:()=>draws++};
 const fn=vm.runInNewContext('let feedVersion=0;'+source.slice(source.indexOf('  async function refreshFeed(){'),source.indexOf('  function drawFeed(){'))+';refreshFeed',context);
 return {state,pending,messages,nodes,fn,draws:()=>draws};
}
test('patrol filter replies cannot overwrite a newer selection',async()=>{
 const h=feedHarness();const old=h.fn();h.state.feedQuery='ETH';const latest=h.fn();
 assert.match(h.pending[1].path,/q=ETH/);
 h.pending[1].resolve({items:['ETH']});await latest;
 h.pending[0].resolve({items:['BTC']});await old;
 assert.deepEqual(h.state.feed.items,['ETH']);assert.equal(h.draws(),1);
});
test('patrol older pages retain the observation cutoff and reset it on the first page',async()=>{
 const h=feedHarness();h.state.feedLimit=12;h.state.feedPage=0;
 const first=h.fn();assert.match(h.pending[0].path,/offset=0/);assert.doesNotMatch(h.pending[0].path,/before_ms/);
 h.pending[0].resolve({items:['a'],total:35,before_ms:1000});await first;
 h.state.feedPage=1;const second=h.fn();assert.match(h.pending[1].path,/offset=12/);assert.match(h.pending[1].path,/before_ms=1000/);
 h.pending[1].resolve({items:['b'],total:35,before_ms:1000});await second;
 h.state.feedPage=0;const refreshed=h.fn();assert.doesNotMatch(h.pending[2].path,/before_ms/);
 h.pending[2].resolve({items:['new'],total:36,before_ms:2000});await refreshed;assert.equal(h.state.feedBefore,2000);
});
test('patrol pagination clamps a page emptied by the current filter',async()=>{
 const h=feedHarness();h.state.feedLimit=12;h.state.feedPage=4;h.state.feedBefore=1000;
 const result=h.fn();h.pending[0].resolve({items:[],total:13,before_ms:1000});await new Promise(setImmediate);
 assert.equal(h.state.feedPage,1);assert.match(h.pending[1].path,/offset=12/);
 h.pending[1].resolve({items:['last'],total:13,before_ms:1000});await result;assert.deepEqual(h.state.feed.items,['last']);
});

test('feed failure preserves the last successful discovery list and marks it stale',async()=>{
 const h=feedHarness();h.state.feed={items:['kept']};const request=h.fn();
 h.pending[0].reject(Error('offline'));await request;
 assert.deepEqual(h.state.feed.items,['kept']);assert.match(h.messages.at(-1),/更新失败/);
 assert.match(h.nodes.get('yd-feed-updated').textContent,/更新中断/);
});
test('leaving the workspace discards an outstanding discovery response',async()=>{
 const h=feedHarness(),request=h.fn();h.state.active=false;
 h.pending[0].resolve({items:['old']});await request;assert.equal(h.state.feed,null);
});

test('coin ordering supports recent shapes, score, alphabet and favorites without mutating rows',()=>{
 const rows=[{symbol:'Z-USDT-SWAP',count:1,latest_detection_ms:200,max_confidence:.5},{symbol:'A-USDT-SWAP',count:2,latest_detection_ms:100,max_confidence:.9},{symbol:'B-USDT-SWAP',count:0}];
 const order=(mode,favorites=[])=>filteredSymbols(rows,'','all',favorites,mode).map(r=>r.symbol[0]).join('');
 assert.equal(order('latest'),'ZAB');assert.equal(order('score'),'AZB');assert.equal(order('name'),'ABZ');assert.equal(order('name-desc'),'ZBA');assert.equal(order('favorites',['B-USDT-SWAP']),'BZA');assert.equal(rows[0].symbol,'Z-USDT-SWAP');
});
test('chart continuation never mutates the detection prefix or mixes coins and periods',()=>{
 const {chartRows}=require('../../yoyo/monitor/static/yolo-detect.js');
 const result={symbol:'ETH',timeframe:'5m',candles:[{t:0},{t:300000}]};
 const tail={symbol:'ETH',timeframe:'5m',detection_cutoff_ms:600000,candles:[{t:600000}]};
 assert.deepEqual(chartRows(result,tail).map(r=>r.t),[0,300000,600000]);assert.equal(result.candles.length,2);
 assert.equal(chartRows(result,{...tail,symbol:'BTC'}).length,2);
 assert.equal(chartRows(result,{...tail,timeframe:'15m'}).length,2);
 assert.equal(chartRows(result,{...tail,detection_cutoff_ms:900000}).length,2);
});

function tailHarness(){
 const pending=[];let draws=0,focuses=0;
 const state={active:true,tab:'results',result:{symbol:'ETH'},job:{id:'job'},follow:true,tailLoading:false,tailChecked:0,tail:null,viewTouched:false};
 const context={state,Date,encodeURIComponent,api:path=>new Promise((resolve,reject)=>pending.push({path,resolve,reject})),tailStatus(){},updateChartData(){draws++;},followupRange(){focuses++;}};
 const fn=vm.runInNewContext('let tailVersion=0;'+source.slice(source.indexOf('  async function refreshTail('),source.indexOf('  function emptyChart('))+';refreshTail',context);
 return {state,pending,fn,draws:()=>draws,focuses:()=>focuses};
}
test('pending chart continuation cannot replace a different coin or a disabled follow view',async()=>{
 for(const change of [s=>s.result={symbol:'BTC'},s=>s.follow=false,s=>s.active=false]){
  const h=tailHarness(),request=h.fn();change(h.state);h.pending[0].resolve({candles:[{t:1}]});await request;
  assert.equal(h.state.tail,null);assert.equal(h.draws(),0);
 }
});
test('chart updates preserve a user-adjusted view and avoid duplicate network requests',async()=>{
 const h=tailHarness(),request=h.fn();await h.fn(true);assert.equal(h.pending.length,1);
 h.state.viewTouched=true;h.pending[0].resolve({candles:[{t:1}]});await request;
 assert.equal(h.draws(),1);assert.equal(h.focuses(),0);await h.fn();assert.equal(h.pending.length,1);
 const fresh=tailHarness(),first=fresh.fn();fresh.pending[0].resolve({candles:[{t:1}]});await first;assert.equal(fresh.focuses(),1);
});
test('chart update failure retains the last successful candles and exposes the failure',async()=>{
 const h=tailHarness(),saved={candles:[{t:1}]};h.state.tail=saved;const request=h.fn();
 h.pending[0].reject(Error('offline'));await request;
 assert.equal(h.state.tail,saved);assert.match(h.state.tailError,/保留当前图表.*offline/);assert.equal(h.state.tailLoading,false);
});

test('patrol progress distinguishes processed coins, unavailable history and pending coverage',()=>{
 const {patrolProgress}=require('../../yoyo/monitor/static/yolo-detect.js');
 const text=patrolProgress({last_run:{status:'partial',progress:{completed:286,total:286,unavailable:100,failed:1,remaining_endpoints:20,elapsed_seconds:180}}});
 assert.match(text,/已处理 286\/286/);assert.match(text,/100 币历史不足/);assert.match(text,/1 币待重试/);assert.match(text,/20 个收盘位置待补扫/);assert.match(text,/3.0 分钟/);
});

test('polling only reads visible panels and keeps completed job configuration',async()=>{
 const calls=[],state={active:true,loaded:true,tab:'model',jobs:[],schedules:[],jobOffset:0,job:{id:'done',status:'completed',spec:{model_id:'frozen'}},pendingRoute:null};
 const context={state,document:{hidden:false},refreshing:false,activationVersion:0,jobsRevision:'',jobsOffset:-1,jobsAt:0,schedulesAt:0,Date,encodeURIComponent,
  $:()=>({open:false}),terminal:new Set(['completed']),api:async path=>{calls.push(path);return path==='/schedules'?{items:[],worker:{}}:{items:[],total:0,revision:'r1',worker:{}};},
  jobsView(){},summary(){},drawSymbols(){},refreshTail(){},refreshFeed(){},error(e){if(e)throw Error(e);}};
 const fn=source.slice(source.indexOf('  async function refresh(){'),source.indexOf('  window.SpikeNavigation?.register'));
 const refresh=vm.runInNewContext(fn+';refresh',context);
 await refresh();assert.deepEqual(calls,['/schedules']);calls.length=0;
 await refresh();assert.deepEqual(calls,[]);
 state.tab='history';await refresh();assert.equal(calls.length,1);assert.match(calls[0],/jobs\?limit=20/);calls.length=0;
 await refresh();assert.match(calls[0],/if_revision=r1/);calls.length=0;
 state.tab='results';await refresh();assert.equal(calls.length,0);assert.equal(state.job.spec.model_id,'frozen');
});

test('coin overview retains 5m and 15m independently when selecting a period',()=>{
 const {aggregateSymbols,periodResult}=require('../../yoyo/monitor/static/yolo-detect.js');
 const items=[{symbol:'ETH-USDT-SWAP',timeframe:'5m',job_id:'five',count:0},{symbol:'ETH-USDT-SWAP',timeframe:'15m',job_id:'quarter',count:2,latest_detection_ms:100,max_confidence:.8},{symbol:'BTC-USDT-SWAP',timeframe:'1H',job_id:'hour',count:1}];
 const rows=aggregateSymbols(items);assert.equal(rows.length,2);assert.equal(rows[0].count,2);assert.equal(rows[0].periods.length,2);
 assert.equal(periodResult(items,'ETH-USDT-SWAP','15m').job_id,'quarter');assert.equal(periodResult(items,'ETH-USDT-SWAP','4H'),undefined);
 assert.equal(filteredSymbols(rows,'','detected').length,2);assert.equal(items[0].count,0);
});

function previewHarness(){
 const pending=[],state={active:true,overview:true,symbol:'ETH-USDT-SWAP',timeframe:'30m',result:null};let draws=0;
 const context={state,requestVersion:1,Date,URLSearchParams,steps:{'30m':1800000},requests:{json:path=>new Promise((resolve,reject)=>pending.push({path,resolve,reject}))},chart:null,
  $:()=>({checked:false}),tailStatus(){},renderChart:async()=>{draws++;},drawBoxes(){},emptyChart(){}};
 const fn=vm.runInNewContext(source.slice(source.indexOf('  async function previewChart('),source.indexOf('  async function refreshLatest('))+';previewChart',context);
 return {state,pending,fn,draws:()=>draws};
}
test('unscanned preview uses read-only monitor candles and explicitly has no detection verdict',async()=>{
 const h=previewHarness(),request=h.fn('ETH-USDT-SWAP','30m');assert.match(h.pending[0].path,/^\/api\/chart\?/);
 h.pending[0].resolve({symbol:'ETH-USDT-SWAP',timeframe:'30m',state:{stale:true},candles:[{t:0,o:1,h:2,l:1,c:2,v:3}]});await request;
 assert.equal(h.state.result.preview,true);assert.equal(h.state.result.stale,true);assert.equal(h.state.result.candles[0].open,1);assert.equal(h.state.result.episodes.length,0);assert.equal(h.draws(),1);
});
test('preview replies cannot overwrite another selected period or symbol',async()=>{
 for(const change of [s=>s.timeframe='4H',s=>s.symbol='BTC-USDT-SWAP',s=>s.active=false]){
  const h=previewHarness(),request=h.fn('ETH-USDT-SWAP','30m');change(h.state);
  h.pending[0].resolve({symbol:'ETH-USDT-SWAP',timeframe:'30m',candles:[{t:0,o:1,h:2,l:1,c:2,v:3}]});await request;assert.equal(h.state.result,null);assert.equal(h.draws(),0);
 }
});

function pairHarness(mobile=false){
 const pending=[],draws=[],events=[],elements=new Map(),latest=[
  {symbol:'ETH-USDT-SWAP',timeframe:'5m',job_id:'five',count:1},
  {symbol:'ETH-USDT-SWAP',timeframe:'15m',job_id:'quarter',count:1},
  {symbol:'BTC-USDT-SWAP',timeframe:'1H',job_id:'hour',count:0},
 ];
 const state={active:true,overview:true,latest,symbol:'BTC-USDT-SWAP',timeframe:'1H',boxes:'raw',side:'short',resultFilter:'detected'};
 const el=id=>{if(!elements.has(id))elements.set(id,{open:id==='yd-symbol-dialog'&&mobile,focus(){events.push('focus:'+id);}});return elements.get(id);};
 const context={state,requestVersion:0,tailVersion:0,resultCache:new Map(),encodeURIComponent,
  symbolRows:()=>latest.filter(r=>state.resultFilter==='all'||r.count>0),allSymbolRows:()=>latest,
  periodResult:require('../../yoyo/monitor/static/yolo-detect.js').periodResult,$:el,
  root:{querySelector:()=>({classList:{remove(){}},setAttribute(){},scrollIntoView(){events.push('scroll');}})},
  syncSymbolPanel(){},closeDialog(id){el(id).open=false;events.push('close');},short:s=>s.split('-')[0],destroyChart(){},emptyChart(){},tailStatus(){},drawBoxes(){},drawSymbols(){},syncRoute(){},summary(){},error(){},refreshTail(){},
  api:path=>new Promise((resolve,reject)=>pending.push({path,resolve,reject})),
  renderChart:async()=>draws.push({symbol:state.result.symbol,timeframe:state.result.timeframe,focus:state.focusBox})};
 const body=source.slice(source.indexOf('  async function chooseSymbolPeriod('),source.indexOf('  function drawPeriods('))+
  source.slice(source.indexOf('  async function chooseSymbol('),source.indexOf('  function listBoxes('));
 const fn=vm.runInNewContext(body+';chooseSymbolPeriod',context);
 const result=(symbol,timeframe,episodes=[])=>({symbol,timeframe,episodes,raw_detections:[]});
 return{state,pending,draws,events,el,fn,result};
}
test('period action reads the exact coin/period once and focuses its newest shape',async()=>{
 const h=pairHarness(),request=h.fn('ETH-USDT-SWAP','15m');
 assert.equal(h.pending.length,1);assert.equal(h.pending[0].path,'/jobs/quarter/results/ETH-USDT-SWAP');
 assert.equal(h.state.timeframe,'15m');assert.equal(h.state.boxes,'structure');assert.equal(h.state.side,'all');
 h.pending[0].resolve(h.result('ETH-USDT-SWAP','15m',[{id:'older',detected_at_ms:1,side:'short'},{id:'newest',detected_at_ms:2,side:'long'}]));await request;
 assert.deepEqual(h.draws,[{symbol:'ETH-USDT-SWAP',timeframe:'15m',focus:'newest'}]);
});
test('rapid period clicks cannot let an older response replace the final selection',async()=>{
 const h=pairHarness(),first=h.fn('ETH-USDT-SWAP','5m'),second=h.fn('ETH-USDT-SWAP','15m');
 h.pending[1].resolve(h.result('ETH-USDT-SWAP','15m',[{id:'final',detected_at_ms:2}]));await second;
 h.pending[0].resolve(h.result('ETH-USDT-SWAP','5m',[{id:'stale',detected_at_ms:1}]));await first;
 assert.equal(h.state.result.timeframe,'15m');assert.equal(h.draws.length,1);assert.equal(h.draws[0].focus,'final');
});
test('mobile period selection closes the picker and moves focus before the read finishes',async()=>{
 const h=pairHarness(true),request=h.fn('BTC-USDT-SWAP','1H');
 assert.equal(h.el('yd-symbol-dialog').open,false);assert.deepEqual(h.events,['close','focus:yd-chart-title','scroll']);
 h.pending[0].resolve(h.result('BTC-USDT-SWAP','1H'));await request;
 assert.equal(h.state.focusBox,null);assert.equal(h.state.result.episodes.length,0);assert.equal(h.draws.length,1);
 assert.equal(h.state.resultFilter,'all','the selected position remains reachable in the symbol list');
});
test('missing period never falls back to a different result and errors clear prior boxes',async()=>{
 const h=pairHarness();await h.fn('ETH-USDT-SWAP','4H');assert.equal(h.pending.length,0);assert.equal(h.state.symbol,'BTC-USDT-SWAP');
 const request=h.fn('ETH-USDT-SWAP','5m');h.pending[0].resolve({error:'History too short',availability:'insufficient_history'});await request;
 assert.equal(h.state.result,null);assert.equal(h.draws.length,0);assert.equal(h.state.loadingResult,false);
});

test('shape focus keeps at least 64 context bars without moving the detection box',()=>{
 const {shapeRange}=require('../../yoyo/monitor/static/yolo-detect.js');
 const box={window_start_ms:100000,window_end_ms:104000};
 assert.deepEqual(shapeRange(box,0,1000),{from:48,to:112});
 assert.deepEqual(box,{window_start_ms:100000,window_end_ms:104000});
});

test('period controls preserve keyboard focus when their selected markup changes',()=>{
 const focused={dataset:{ydPeriod:'1H'}},document={activeElement:focused};let restored=false;
 const state={overview:true,timeframe:'1H',symbol:'BTC-USDT-SWAP',latest:[]};
 const context={state,document,periods:{'5m':'5 分钟','1H':'1 小时'},periodResult:()=>null,CSS:{escape:x=>x},
 html(){document.activeElement={dataset:{}};},$:()=>({querySelector:selector=>selector==='[data-yd-period="1H"]'?{focus(){restored=true;}}:null})};
 vm.runInNewContext(source.slice(source.indexOf('  function drawPeriods(){'),source.indexOf('  async function switchPeriod('))+';drawPeriods()',context);
 assert.equal(restored,true);
});
test('desktop symbol expansion state stays true after changing a coin; mobile follows its dialog',()=>{
 const attrs={},panel={hidden:false,closest:()=>({classList:{toggle(){}}})},button={setAttribute:(k,v)=>attrs[k]=v};
 const context={symbolPanelMedia:{matches:false},symbolPanelCollapsed:false,root:{querySelector:()=>button},$:id=>id==='yd-symbol-home'?panel:{open:false}};
 const sync=vm.runInNewContext(source.slice(source.indexOf('  function syncSymbolPanel(){'),source.indexOf('  symbolPanelMedia.addEventListener'))+';syncSymbolPanel',context);
 sync();assert.equal(attrs['aria-expanded'],'true');assert.equal(panel.hidden,false);
 context.symbolPanelMedia.matches=true;sync();assert.equal(attrs['aria-expanded'],'false');assert.equal(attrs['aria-controls'],'yd-symbol-dialog');
});

function periodSwitchHarness(){
 const pending=[],actions=[],elements=new Map(),state={active:true,tab:'results',latest:[],symbol:'ETH-USDT-SWAP',timeframe:'5m',loadingResult:true,loadingJob:true,selected:'old',page:2};
 const context={state,requestVersion:0,tailVersion:0,$:id=>{if(!elements.has(id))elements.set(id,{});return elements.get(id);},short:s=>s.split('-')[0],
  refreshLatest:()=>new Promise(resolve=>pending.push(resolve)),periodResult:(rows,symbol,tf)=>rows.find(r=>r.symbol===symbol&&r.timeframe===tf),
  chooseSymbol:async symbol=>actions.push(['select',symbol,state.timeframe]),previewChart:async (symbol,tf)=>actions.push(['preview',symbol,tf]),
  drawPeriods(){},destroyChart(){actions.push(['destroy']);},emptyChart(){},tailStatus(){},drawBoxes(){},drawSymbols(){},syncRoute(){},summary(){}};
 const fn=vm.runInNewContext(source.slice(source.indexOf('  async function switchPeriod('),source.indexOf('  async function previewChart('))+';switchPeriod',context);
 return {fn,state,pending,actions,context};
}
test('initial catalog lookup cannot clear or reload a subsequently selected period',async()=>{
 const h=periodSwitchHarness(),old=h.fn('15m'),latest=h.fn('1H');
 h.state.latest=[{symbol:h.state.symbol,timeframe:'1H'}];h.pending[1]();await latest;
 h.pending[0]();await old;
 assert.deepEqual(h.actions,[['select','ETH-USDT-SWAP','1H']]);assert.equal(h.state.timeframe,'1H');
});
test('unscanned period resets prior loading and shape state before its preview',async()=>{
 const h=periodSwitchHarness();h.state.latest=[{symbol:h.state.symbol,timeframe:'5m'}];await h.fn('30m');
 assert.equal(h.state.loadingResult,false);assert.equal(h.state.loadingJob,false);assert.equal(h.state.selected,null);assert.equal(h.state.page,0);
 assert.deepEqual(h.actions,[['destroy'],['preview','ETH-USDT-SWAP','30m']]);
});
test('catalog response after leaving results does not start hidden chart work',async()=>{
 const h=periodSwitchHarness(),read=h.fn('15m');h.state.tab='account';h.pending[0]();await read;assert.deepEqual(h.actions,[]);
});

test('returning from account preserves historical job and selected symbol instead of becoming latest overview',()=>{
 const state={tab:'account',overview:false,active:true,loaded:false,result:null,job:{id:'historical'},symbol:'CT-USDT-SWAP',timeframe:'5m'},elements=new Map();
 const context={state,root:{dataset:{},querySelector:()=>null,querySelectorAll:()=>[]},$:id=>{if(!elements.has(id))elements.set(id,{querySelector:()=>null,insertBefore(){},append(){}});return elements.get(id)},chart:null,window:{SpikeManualAccount:{setActive(){}}},destroyChart(){},syncRoute(){},refresh(){}};
 vm.runInNewContext(source.slice(source.indexOf('  function setTab('),source.indexOf('  function draftFromJob('))+';setTab("results")',context);
 assert.equal(state.overview,false);assert.equal(state.job.id,'historical');assert.equal(state.symbol,'CT-USDT-SWAP');assert.equal(state.timeframe,'5m');
});

test('discovery overview keeps a single account panel and uses the lightweight read mode',()=>{
 const moves=[],state={tab:'results',overview:true,active:true,loaded:false,result:null},elements=new Map();
 const context={state,root:{dataset:{},querySelector:()=>null,querySelectorAll:()=>[]},$:id=>{if(!elements.has(id))elements.set(id,{querySelector:()=>null,insertBefore:el=>moves.push([id,el]),append:el=>moves.push([id,el])});return elements.get(id)},chart:null,window:{SpikeManualAccount:{setActive:(active,tab)=>moves.push(['poll',active,tab])}},destroyChart(){},syncRoute(){},refresh(){}};
 const fn=vm.runInNewContext(source.slice(source.indexOf('  function setTab('),source.indexOf('  function draftFromJob('))+';setTab',context);
 fn('schedules');assert.equal(context.root.dataset.detectionPane,'schedules');assert.equal(elements.get('yd-manual-account').hidden,false);assert.equal(moves[0][0],'yd-overview-account');
 fn('results');assert.equal(moves[2][0],'yd-panel-results');assert.equal(moves[0][1],moves[2][1]);
 fn('model');assert.equal(elements.get('yd-manual-account').hidden,true);
});
