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
