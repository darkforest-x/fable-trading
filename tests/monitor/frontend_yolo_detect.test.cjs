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
