'use strict';
const test=require('node:test'),assert=require('node:assert/strict');
const {levels,fitRange,mount}=require('../../yoyo/monitor/static/yolo-chart-levels.js');
const identity={symbol:'BAT-USDT-SWAP',detection:'box',at:3000};
const value={symbol:identity.symbol,detection_id:'box',as_of_ms:3000,timezone:'America/New_York',week:'2026-10-19',start_ms:1000,end_ms:2000,expires_ms:4000,state:'ready',high_ms:1500,low_ms:1700,high:110,low:100,upper:116.18,lower:93.82};
global.window={SpikeRangeChart:{WeekLevels:class{constructor(owner){this.owner=owner;}}}};
const flush=()=>new Promise(setImmediate);
test('four reference lines retain canonical prices and colors',()=>{
 assert.deepEqual(levels(value,identity).map(r=>[r.label,r.price,r.color]),[['上 1.618',116.18,'#c93248'],['周一高',110,'#528cc1'],['周一低',100,'#528cc1'],['下 1.618',93.82,'#087f69']]);
 assert.deepEqual(levels({...value,state:'developing'},identity),[]);
});
test('another symbol, detection, time or incomplete future week cannot be displayed',()=>{
 for(const delta of [{symbol:'ETH-USDT-SWAP'},{detection_id:'other'},{as_of_ms:3001},{timezone:'UTC'},{end_ms:3500},{expires_ms:3000},{low:NaN},{high:99},{high_ms:999},{low_ms:2000}])assert.throws(()=>levels({...value,...delta},identity));
});
test('range fit includes all four levels and detail mode preserves candle scale',()=>{
 const info={priceRange:{minValue:101,maxValue:105},margins:{above:8,below:8}},rows=levels(value,identity);
 assert.equal(fitRange(info,rows,false),info);
 assert.deepEqual(fitRange(info,rows,true),{priceRange:{minValue:93.82,maxValue:116.18},margins:info.margins});
 assert.deepEqual(info.priceRange,{minValue:101,maxValue:105});assert.equal(fitRange(null,rows,true),null);
});
function harness(){
 const requests=[],added=[],removed=[],options=[],scale=[];
 const host={innerHTML:'',addEventListener(_,cb){this.click=cb;},removeEventListener(){this.click=null;},replaceChildren(){this.innerHTML='';}};
 const series={attachPrimitive:p=>added.push(...p.owner.activeLevels),detachPrimitive:p=>removed.push(...p.owner.activeLevels),applyOptions:o=>options.push(o),createPriceLine:o=>(added.push(o),o),removePriceLine:o=>removed.push(o),priceScale:()=>({applyOptions:o=>scale.push(o)})};
 const fetcher=(url,init)=>new Promise((resolve,reject)=>requests.push({url,init,resolve:v=>resolve({ok:true,json:async()=>v}),reject}));
 const instance=mount({chart:{},getRows:()=>[],...identity,url:'/levels',format:String,series,host,fetcher});
 return {host,series,instance,requests,added,removed,options,scale};
}
test('successful response draws once, fit toggles scale, destroy removes lines and listener',async()=>{
 const h=harness();h.requests[0].resolve(value);await flush();
 assert.equal(h.added.length,4);assert.match(h.host.innerHTML,/周一高/);
 assert.equal(h.options.at(-1).autoscaleInfoProvider(()=>({priceRange:{minValue:101,maxValue:105}})).priceRange.maxValue,116.18);
 const button={setAttribute(k,v){this[k]=v;}};
 h.host.click({target:{closest:s=>s==='[data-range-fit]'?button:null}});
 assert.equal(button['aria-pressed'],'false');assert.equal(h.scale.length,2);
 h.host.click({target:{closest:s=>s==='[data-range-fit]'?button:null}});assert.equal(button['aria-pressed'],'true');
 assert.equal(h.options.at(-1).autoscaleInfoProvider(()=>({priceRange:{minValue:101,maxValue:105}})).priceRange.maxValue,116.18);
 h.instance.destroy();assert.equal(h.removed.length,4);assert.equal(h.host.innerHTML,'');assert.equal(h.host.click,null);assert.equal(h.requests[0].init.signal.aborted,true);
});
test('response after close cannot repaint a disposed or reused host',async()=>{
 const h=harness();h.instance.destroy();h.host.innerHTML='new symbol';
 h.requests[0].resolve(value);await flush();assert.equal(h.host.innerHTML,'new symbol');assert.equal(h.added.length,0);
});
test('failed range fetch leaves chart intact and retry discards a delayed older reply',async()=>{
 const h=harness();h.requests[0].reject(Error('offline'));await flush();assert.match(h.host.innerHTML,/重试区间/);assert.equal(h.added.length,0);
 const click={target:{closest:s=>s==='[data-range-retry]'?{}:null}};
 h.host.click(click);h.host.click(click);
 h.requests[2].resolve(value);await flush();
 h.requests[1].resolve({...value,high:999});await flush();
 assert.equal(h.added.length,4);assert.doesNotMatch(h.host.innerHTML,/999/);h.instance.destroy();
});
test('forming Monday shows a nonblocking explanation with no fabricated lines',async()=>{
 const h=harness();h.requests[0].resolve({...value,state:'developing'});await flush();
 assert.equal(h.added.length,0);assert.match(h.host.innerHTML,/尚未形成/);h.instance.destroy();
});

test('each ray begins at its own Monday extreme',()=>{assert.deepEqual(levels(value,identity).map(l=>l.start_ms),[1500,1500,1700,1700]);});
