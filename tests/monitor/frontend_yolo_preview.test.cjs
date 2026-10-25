'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const source=fs.readFileSync(require('node:path').join(__dirname,'../../yoyo/monitor/static/yolo-detect.js'),'utf8');
const code=source.slice(source.indexOf('  async function openFindingPreview('),source.indexOf('  class DetectionPrimitive'));
function harness(){
 const nodes=new Map(),retry={hidden:true},requests=[];let ready;
 const node=id=>{if(!nodes.has(id))nodes.set(id,{open:false,hidden:false,textContent:'',innerHTML:'',replaceChildren(){},querySelector:()=>retry});return nodes.get(id);};
 const c=vm.createContext({AbortController,BASE:'/api/yolo-detect',state:{active:true},$:node,short:s=>s,periods:{'15m':'15 分钟'},fmt:String,showDialog:id=>node(id).open=true,setTimeout:()=>1,clearTimeout:()=>{},resultCache:new Map(),chartLibrary:()=>new Promise(r=>ready=r),fetch:(url,options)=>new Promise(resolve=>requests.push({url,options,resolve}))});
 vm.runInContext('let findingPreview=null,previewVersion=0,previewRequest=null,previewResize=null,previewChartInstance=null,previewControls=null,previewCrosshair=null,previewLevels=null;'+code,c);
 const item={id:'finding',job_id:'job',symbol:'ETH-USDT-SWAP',timeframe:'15m',side:'long',confidence:.8,detected_at_ms:900000,detection_id:'box'};
 return {c,node,retry,requests,item,open:i=>c.openFindingPreview(i||item),ready:()=>ready?.(),close:()=>{node('yd-preview-dialog').open=false;vm.runInContext('previewVersion++;previewRequest.abort()',c);}};
}
function result(item){return {symbol:item.symbol,timeframe:item.timeframe,candles:[{t:0}],episodes:[{id:'box'}]};}
test('closing preview during read discards response and aborts its request',async()=>{
 const h=harness(),p=h.open();h.close();h.requests[0].resolve({ok:true,json:async()=>result(h.item)});await p;
 assert.ok(h.requests[0].options.signal.aborted);assert.equal(h.c.resultCache.size,0);
});
test('switching cards discards the first card response without overwriting title',async()=>{
 const h=harness(),first=h.open(),next=h.open({...h.item,id:'second',symbol:'BTC-USDT-SWAP'});
 h.requests[0].resolve({ok:true,json:async()=>result(h.item)});await first;assert.equal(h.c.resultCache.size,0);
 h.requests[1].resolve({ok:false});await next;assert.match(h.node('yd-preview-title').textContent,/BTC/);assert.match(h.node('yd-preview-message').textContent,/暂不可用/);assert.equal(h.retry.hidden,false);
});
test('preview cannot substitute a different period or a missing detection',async()=>{
 for(const change of [{timeframe:'1H'},{episodes:[]}]){
  const h=harness(),p=h.open();h.requests[0].resolve({ok:true,json:async()=>({...result(h.item),...change})});await p;
  assert.equal(h.retry.hidden,false);assert.match(h.node('yd-preview-message').textContent,/不完整|无法定位/);
 }
});
test('closing while chart library loads cannot create a hidden chart',async()=>{
 const h=harness(),p=h.open();h.requests[0].resolve({ok:true,json:async()=>result(h.item)});await new Promise(setImmediate);
 h.close();h.ready();await p;assert.equal(vm.runInContext('previewChartInstance',h.c),null);
});
