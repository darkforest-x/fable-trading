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

const {filteredSymbols,initialSymbol,draftSpec,sameScan}=require('../../yoyo/monitor/static/yolo-detect.js');
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
