'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const source=fs.readFileSync('yoyo/monitor/static/monday-fib.js','utf8');
const {price,selectRows}=require('../../yoyo/monitor/static/monday-fib.js');
const item=(o={})=>({symbol:'BTC-USDT-SWAP',state:'ready',stale:false,price:82500,high:83149.8,low:81166,upper:84375.7884,lower:79940.0116,received_ms:Date.now(),distance_pct:1.2,nearest:'upper',touches:{},...o});
const snap=(o={})=>({now_ms:Date.now(),week:'2026-10-19',monday_complete:true,stream:{connected:true,last_message_ms:Date.now()},telegram:{enabled:false,configured:true},counts:{total:1,touched:0},items:[item()],events:[],...o});
const response=(d,ok=true)=>({ok,json:async()=>d});
function harness(fetchImpl){
 const els=new Map(),timers=new Map();let id=0;
 function el(name){if(!els.has(name))els.set(name,{innerHTML:'',textContent:'',hidden:false,disabled:false,value:'',dataset:{},events:{},attrs:{},addEventListener(t,fn){this.events[t]=fn;},setAttribute(k,v){this.attrs[k]=v;},classList:{toggle(){}},querySelectorAll(){return [];},querySelector(){return null;}});return els.get(name);}
 const window={},document={hidden:false,activeElement:null,getElementById:el,events:{},addEventListener(t,fn){this.events[t]=fn;}};
 vm.runInNewContext(source,{window,document,fetch:fetchImpl,AbortController,Date,Intl,URLSearchParams,setTimeout(fn,ms){const n=++id;timers.set(n,{fn,ms});return n;},clearTimeout(n){timers.delete(n);}});
 return {api:window.SpikeMondayFib,el,timers,document};
}
const flush=async()=>{for(let i=0;i<12;i++)await Promise.resolve();};
async function ready(c){c.api.setActive(true);await c.api.refresh();await flush();}
test('small coin prices retain precision; null is never presented as zero',()=>{assert.equal(price(null),'—');assert.equal(price(.00000123),'0.00000123');assert.equal(price(83149.8),'83,149.8');});
test('filters search all contracts, preserve side touches and exclude stale near levels',()=>{
 const a=[item(),item({symbol:'ETH-USDT-SWAP',stale:true,touches:{upper:{}}}),item({symbol:'SOL-USDT-SWAP',state:'missing_monday',distance_pct:null})];
 assert.equal(selectRows(a,'BTC/USDT').length,1);assert.equal(selectRows(a,'','near').length,1);assert.equal(selectRows(a,'','upper')[0].symbol,'ETH-USDT-SWAP');assert.equal(selectRows(a,'','unavailable').length,2);
});
test('opening is GET-only, renders real data and leaves TG off',async()=>{
 const calls=[];const c=harness(async(u,o)=>{calls.push([u,o]);return response(snap());});await ready(c);
 assert.ok(calls.every(([u,o])=>u==='/api/monday-fib'&&!o.method));assert.equal(c.el('mf-tg-toggle').attrs['aria-checked'],'false');assert.match(c.el('mf-rows').innerHTML,/84,375.79/);assert.match(c.el('mf-week').textContent,/2026-10-19/);c.api.setActive(false);assert.equal(c.timers.size,0);
});
test('pagination reaches the final contract and filters reset the page',async()=>{
 const c=harness(async()=>response(snap({items:Array.from({length:507},(_,i)=>item({symbol:`C${i}-USDT-SWAP`}))})));await ready(c);
 for(let i=0;i<101;i++)c.el('mf-next').events.click();assert.match(c.el('mf-rows').innerHTML,/C506/);assert.equal(c.el('mf-next').disabled,true);
 c.el('mf-search').events.input({target:{value:'C0-USDT-SWAP'}});assert.equal(c.el('mf-page-number').textContent,'1 / 1');assert.match(c.el('mf-rows').innerHTML,/>C0</);c.api.setActive(false);
});
test('late snapshot from a departed view is ignored',async()=>{
 const pending=[];const c=harness(()=>new Promise(resolve=>pending.push(resolve)));c.api.setActive(true);c.api.setActive(false);c.api.setActive(true);
 pending[1](response(snap({week:'2026-10-19'})));await flush();pending[0](response(snap({week:'1999-01-01'})));await flush();assert.doesNotMatch(c.el('mf-week').textContent,/1999/);c.api.setActive(false);
});
test('TG saves are explicit, single-flight and fenced against a stale GET',async()=>{
 let current=snap(),deferGet=null,deferPut=null,reads=0;const calls=[];
 const c=harness((u,o)=>{calls.push([u,o]);if(o.method==='PUT')return new Promise(r=>deferPut=r);if(++reads===2)return new Promise(r=>deferGet=r);return Promise.resolve(response(current));});
 await ready(c);c.api.refresh();const save=c.el('mf-tg-toggle').events.click();c.el('mf-tg-toggle').events.click();
 assert.equal(calls.filter(x=>x[1].method==='PUT').length,1);assert.equal(c.el('mf-tg-toggle').disabled,true);assert.equal(c.el('mf-tg-toggle').attrs['aria-checked'],'false');
 const put=calls.find(x=>x[1].method==='PUT')[1];assert.equal(put.headers['X-Spike-Action'],'update-monday-fib-telegram');assert.deepEqual(JSON.parse(put.body),{enabled:true});
 current=snap({telegram:{enabled:true,configured:true}});deferPut(response(current));await save;deferGet(response(snap()));await flush();assert.equal(c.el('mf-tg-toggle').attrs['aria-checked'],'true');c.api.setActive(false);
});
test('failed subscription write reconciles actual state without claiming success',async()=>{
 const c=harness(async(u,o)=>o.method==='PUT'?response({detail:'server unavailable'},false):response(snap()));await ready(c);await c.el('mf-tg-toggle').events.click();assert.equal(c.el('mf-tg-toggle').attrs['aria-checked'],'false');assert.match(c.el('mf-save-message').textContent,/未确认/);assert.equal(c.el('mf-tg-toggle').disabled,false);c.api.setActive(false);
});
test('missing history, escaped symbols and uncertain delivery remain honest',async()=>{
 const c=harness(async()=>response(snap({telegram:{enabled:false,configured:false},items:[item({symbol:'<img onerror=x>-USDT-SWAP',state:'missing_monday',price:null,high:null,low:null,upper:null,lower:null})],events:[{symbol:'<script>-USDT-SWAP',side:'upper',source:'history',status:'unknown',candle_ms:Date.now(),level:2}]})));await ready(c);
 assert.match(c.el('mf-rows').innerHTML,/缺少完整周一/);assert.match(c.el('mf-rows').innerHTML,/&lt;img/);assert.doesNotMatch(c.el('mf-rows').innerHTML,/<img onerror/);assert.match(c.el('mf-events-list').innerHTML,/结果未知/);assert.doesNotMatch(c.el('mf-events-list').innerHTML,/时时/);assert.equal(c.el('mf-tg-toggle').disabled,true);c.api.setActive(false);
});
test('failed refresh ages quote eligibility and hidden tabs stop polling',async()=>{
 let fail=false;const c=harness(async()=>{if(fail)throw Error('offline');return response(snap({items:[item({received_ms:Date.now()-100000})]}));});await ready(c);assert.match(c.el('mf-rows').innerHTML,/行情已滞后/);assert.doesNotMatch(c.el('mf-rows').innerHTML,/1.20%/);fail=true;await c.api.refresh();assert.match(c.el('mf-notice').textContent,/offline/);assert.equal(c.el('mf-tg-toggle').disabled,true);c.document.hidden=true;c.document.events.visibilitychange();assert.equal(c.timers.size,0);c.api.setActive(false);
});
