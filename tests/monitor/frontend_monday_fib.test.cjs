'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const source=fs.readFileSync('yoyo/monitor/static/monday-fib.js','utf8');
const {price,selectRows,latestTouch,touchTime,recentTouch,RECENT_MS,selection,tradingViewURL}=require('../../yoyo/monitor/static/monday-fib.js');
const event=(o={})=>({source:'live',observed_ms:Date.now()-60000,candle_ms:Date.now()-60000,status:'muted',level:84375.7884,...o});
const item=(o={})=>({symbol:'BTC-USDT-SWAP',state:'ready',stale:false,price:82500,high:83149.8,low:81166,upper:84375.7884,lower:79940.0116,received_ms:Date.now(),distance_pct:1.2,nearest:'upper',touches:{upper:event()},...o});
const snap=(o={})=>({now_ms:Date.now(),week:'2026-10-19',monday_complete:true,stream:{connected:true,last_message_ms:Date.now()},telegram:{enabled:false,configured:true},counts:{total:1,touched:0},items:[item()],events:[],...o});
const response=(d,ok=true)=>({ok,json:async()=>d});
function harness(fetchImpl){
 const els=new Map(),timers=new Map();let id=0;
 function el(name){if(!els.has(name))els.set(name,{innerHTML:'',textContent:'',hidden:false,disabled:false,value:'',dataset:{},events:{},attrs:{},addEventListener(t,fn){this.events[t]=fn;},setAttribute(k,v){this.attrs[k]=v;},classList:{toggle(){}},contains(){return false;},focus(){},querySelectorAll(){return [];},querySelector(){return null;}});return els.get(name);}
 const charts=[];const window={SpikeMondayFibChart:{show:(host,row,week,options)=>charts.push({symbol:row.symbol,week,...options}),hide(){},fit(){},setInternal(){}}},document={hidden:false,activeElement:null,getElementById:el,events:{},addEventListener(t,fn){this.events[t]=fn;}};
 vm.runInNewContext(source,{window,document,fetch:fetchImpl,AbortController,Date,Intl,URLSearchParams,setTimeout(fn,ms){const n=++id;timers.set(n,{fn,ms});return n;},clearTimeout(n){timers.delete(n);}});
 return {api:window.SpikeMondayFib,el,timers,document,charts};
}
const flush=async()=>{for(let i=0;i<12;i++)await Promise.resolve();};
async function ready(c){c.api.setActive(true);await c.api.refresh();await flush();}
test('small coin prices retain precision; null is never presented as zero',()=>{assert.equal(price(null),'—');assert.equal(price(.00000123),'0.00000123');assert.equal(price(83149.8),'83,149.8');});
test('only touched contracts appear; real event time outranks distance and history insertion time',()=>{
 const now=2e12,a=[item({symbol:'UNTOUCHED-USDT-SWAP',touches:{},distance_pct:0}),item({symbol:'OLD-USDT-SWAP',touches:{upper:event({observed_ms:now-120000})}}),item({symbol:'NEW-USDT-SWAP',touches:{lower:event({observed_ms:now-60000})}}),item({symbol:'HIST-USDT-SWAP',touches:{upper:event({source:'history',observed_ms:now,candle_ms:now-86400000})}})];
 assert.deepEqual(selectRows(a,'','all',now).map(r=>r.symbol),['NEW-USDT-SWAP','OLD-USDT-SWAP','HIST-USDT-SWAP']);
 assert.equal(selectRows(a,'UNTOUCHED','all',now).length,0);assert.equal(selectRows(a,'','recent',now).length,2);assert.equal(selectRows(a,'','lower',now).length,1);
 assert.equal(touchTime(latestTouch(a[3])),now-86400000);assert.equal(a[0].symbol,'UNTOUCHED-USDT-SWAP');
});
test('recent highlight expires at 15 minutes and excludes recovery/future timestamps',()=>{
 const now=2e12;assert.equal(recentTouch(event({observed_ms:now-RECENT_MS+1}),now),true);assert.equal(recentTouch(event({observed_ms:now-RECENT_MS}),now),false);
 assert.equal(recentTouch(event({source:'history',observed_ms:now}),now),false);assert.equal(recentTouch(event({observed_ms:now+1}),now),false);
 const both=item({touches:{upper:event({observed_ms:now-10}),lower:event({observed_ms:now-5})}});assert.equal(latestTouch(both).side,'lower');
});
test('wallpaper opens the newest occurrence automatically and has no notification control or write',async()=>{
 const calls=[];const c=harness(async(u,o)=>{calls.push([u,o]);return response(snap());});await ready(c);
 assert.ok(calls.every(([u,o])=>u==='/api/monday-fib'&&!o.method));
 assert.equal(c.el('mf-symbol').textContent,'BTC');assert.equal(c.charts.at(-1).symbol,'BTC-USDT-SWAP');
 assert.match(c.el('mf-week').textContent,/2026-10-19/);assert.doesNotMatch(source,/mf-tg|method:'PUT'|Telegram/);c.api.setActive(false);assert.equal(c.timers.size,0);
});
test('latest follow advances, manual selection stays pinned, follow resumes and week rollover resets',async()=>{
 const a=item({symbol:'BTC-USDT-SWAP',touches:{upper:event({observed_ms:100})}}),b=item({symbol:'ETH-USDT-SWAP',touches:{lower:event({observed_ms:200})}}),d=item({symbol:'SOL-USDT-SWAP',touches:{upper:event({observed_ms:300})}});
 let value=snap({items:[a,b]});const c=harness(async()=>response(value));await ready(c);assert.equal(c.charts.at(-1).symbol,b.symbol);
 value=snap({items:[a,b,d]});await c.api.refresh();assert.equal(c.charts.at(-1).symbol,d.symbol);
 c.el('mf-rows').events.click({target:{closest:()=>({dataset:{previewSymbol:a.symbol}})}});assert.equal(c.charts.at(-1).symbol,a.symbol);
 await c.api.refresh();assert.equal(c.charts.at(-1).symbol,a.symbol);assert.equal(c.el('mf-follow').attrs['aria-pressed'],'false');
 c.el('mf-follow').events.click();assert.equal(c.charts.at(-1).symbol,d.symbol);
 c.el('mf-rows').events.click({target:{closest:()=>({dataset:{previewSymbol:a.symbol}})}});
 value=snap({week:'2026-10-26',items:[a,b]});await c.api.refresh();assert.equal(c.charts.at(-1).symbol,b.symbol);assert.equal(c.el('mf-follow').attrs['aria-pressed'],'true');c.api.setActive(false);
});
test('all matching contracts are scrollable; search resets position without changing wallpaper selection',async()=>{
 const c=harness(async()=>response(snap({items:Array.from({length:507},(_,i)=>item({symbol:`C${i}-USDT-SWAP`,touches:{upper:event({observed_ms:2e12-i})}}))})));await ready(c);
 assert.match(c.el('mf-rows').innerHTML,/C506/);assert.equal((c.el('mf-rows').innerHTML.match(/data-symbol=/g)||[]).length,507);
 c.el('mf-rows').scrollTop=620;await c.api.refresh();assert.equal(c.el('mf-rows').scrollTop,620);
 c.el('mf-search').events.input({target:{value:'C0-USDT-SWAP'}});assert.equal(c.el('mf-rows').scrollTop,0);assert.equal(c.el('mf-list-count').textContent,'1 个');assert.match(c.el('mf-rows').innerHTML,/>C0</);
 assert.equal(c.charts.at(-1).symbol,'C0-USDT-SWAP');c.el('mf-search').events.input({target:{value:'NONE'}});assert.match(c.el('mf-rows').innerHTML,/没有匹配/);assert.equal(c.charts.at(-1).symbol,'C0-USDT-SWAP');c.api.setActive(false);
});
test('late snapshot from a departed view is ignored',async()=>{
 const pending=[];const c=harness(()=>new Promise(resolve=>pending.push(resolve)));c.api.setActive(true);c.api.setActive(false);c.api.setActive(true);
 pending[1](response(snap({week:'2026-10-19'})));await flush();pending[0](response(snap({week:'1999-01-01'})));await flush();assert.doesNotMatch(c.el('mf-week').textContent,/1999/);c.api.setActive(false);
});
test('empty week has an honest waiting state and no invented symbol',async()=>{
 const c=harness(async()=>response(snap({items:[]})));await ready(c);assert.equal(c.charts.length,0);assert.equal(c.el('mf-selected').hidden,true);assert.match(c.el('mf-empty').textContent,/尚无/);assert.equal(selection([],null,true),null);c.api.setActive(false);
});
test('missing history and escaped symbols remain honest',async()=>{
 const c=harness(async()=>response(snap({items:[item({symbol:'<img onerror=x>-USDT-SWAP',state:'missing_monday',price:null,high:null,low:null,upper:null,lower:null})]})));await ready(c);
 assert.match(c.el('mf-rows').innerHTML,/缺少完整周一/);assert.match(c.el('mf-rows').innerHTML,/&lt;img/);assert.doesNotMatch(c.el('mf-rows').innerHTML,/<img onerror/);assert.equal(c.el('mf-price').textContent,'—');c.api.setActive(false);
});
test('failed refresh marks old quotes and hidden tabs stop polling',async()=>{
 let fail=false;const c=harness(async()=>{if(fail)throw Error('offline');return response(snap({items:[item({received_ms:Date.now()-100000})]}));});await ready(c);assert.match(c.el('mf-rows').innerHTML,/行情已滞后/);fail=true;await c.api.refresh();assert.match(c.el('mf-notice').textContent,/offline/);c.document.hidden=true;c.document.events.visibilitychange();assert.equal(c.timers.size,0);c.api.setActive(false);
});

test('period choice reaches the chart without changing pinned symbol or NY anchors',async()=>{
 const c=harness(async()=>response(snap()));await ready(c);assert.equal(c.charts.at(-1).period,'15m');
 c.el('mf-period-menu').events.click({target:{closest:()=>({dataset:{period:'4H'}})}});
 assert.equal(c.charts.at(-1).period,'4H');assert.equal(c.charts.at(-1).symbol,'BTC-USDT-SWAP');assert.equal(c.charts.at(-1).week,'2026-10-19');
 assert.equal(c.el('mf-period-label').textContent,'4H');assert.equal(c.el('mf-period-menu').hidden,true);
 await c.api.refresh();assert.equal(c.charts.at(-1).period,'4H');
 c.el('mf-period-menu').events.click({target:{closest:()=>({dataset:{period:'invalid'}})}});assert.equal(c.charts.at(-1).period,'4H');c.api.setActive(false);
});

test('live list insertion preserves the same visible symbol and pixel offset',()=>{
 const {captureListScroll,restoreListScroll}=require('../../yoyo/monitor/static/monday-fib.js');
 let rows=[{dataset:{symbol:'A'},offsetTop:2,offsetHeight:58},{dataset:{symbol:'B'},offsetTop:62,offsetHeight:58},{dataset:{symbol:'C'},offsetTop:122,offsetHeight:58}];
 const container={scrollTop:75,querySelectorAll:()=>rows},position=captureListScroll(container);
 assert.equal(position.symbol,'B');assert.equal(position.offset,-13);
 rows=rows.map(r=>({...r,offsetTop:r.offsetTop+60}));restoreListScroll(container,position);assert.equal(container.scrollTop,135);
 rows=[];restoreListScroll(container,position);assert.equal(container.scrollTop,75);
 container.scrollTop=0;assert.equal(captureListScroll(container).symbol,undefined);
});

test('TradingView URLs match Monday alerts and reject malformed contract names',()=>{
 assert.equal(tradingViewURL('BTC-USDT-SWAP'),'https://www.tradingview.com/chart/?symbol=OKX%3ABTCUSDT.P&interval=15');
 assert.equal(tradingViewURL('1000PEPE-USDT-SWAP'),'https://www.tradingview.com/chart/?symbol=OKX%3A1000PEPEUSDT.P&interval=15');
 for(const symbol of [null,'BTC-USDT','BTC-USDT-SWAP&interval=1','<img>-USDT-SWAP','javascript:alert(1)'])assert.equal(tradingViewURL(symbol),'');
});
test('coin links open externally while their sibling preview button alone pins the wallpaper',async()=>{
 const calls=[],a=item({symbol:'BTC-USDT-SWAP',touches:{upper:event({observed_ms:100})}}),b=item({symbol:'ETH-USDT-SWAP',touches:{upper:event({observed_ms:200})}});
 const c=harness(async(u,o)=>{calls.push([u,o]);return response(snap({items:[a,b]}));});await ready(c);
 const html=c.el('mf-rows').innerHTML;
 assert.match(html,/<a class="mf-row-symbol" data-tv-symbol="BTC-USDT-SWAP" href="https:\/\/www.tradingview.com\/chart\/\?symbol=OKX%3ABTCUSDT.P&amp;interval=15" target="_blank" rel="noopener noreferrer"/);
 assert.match(html,/<\/a><button type="button" class="mf-row-value" data-preview-symbol="BTC-USDT-SWAP"/);
 assert.doesNotMatch(html,/<button[^>]*class="mf-row[" ]/);
 const before=c.charts.length;
 c.el('mf-rows').events.click({target:{closest:selector=>{assert.equal(selector,'[data-preview-symbol]');return null;}}});
 assert.equal(c.charts.length,before);assert.equal(c.el('mf-follow').attrs['aria-pressed'],'true');
 c.el('mf-rows').events.click({target:{closest:()=>({dataset:{previewSymbol:a.symbol}})}});
 assert.equal(c.charts.at(-1).symbol,a.symbol);assert.equal(c.el('mf-follow').attrs['aria-pressed'],'false');
 assert.ok(calls.every(([u,o])=>u==='/api/monday-fib'&&!o.method));c.api.setActive(false);
});
test('a quote refresh retains focus on the same link or preview button',async()=>{
 let value=snap();const c=harness(async()=>response(value));await ready(c);
 for(const key of ['tvSymbol','previewSymbol']){
  const focused={dataset:{[key]:'BTC-USDT-SWAP'}},otherKey=key==='tvSymbol'?'previewSymbol':'tvSymbol';let restored=false;
  c.document.activeElement=focused;c.el('mf-rows').contains=el=>el===focused;
  c.el('mf-rows').querySelectorAll=selector=>selector==='[data-symbol]'?[]:[{dataset:{[otherKey]:'BTC-USDT-SWAP'},focus(){assert.fail('wrong sibling received focus');}},{dataset:{[key]:'BTC-USDT-SWAP'},focus(options){assert.equal(options.preventScroll,true);restored=true;}}];
  value=snap({items:[item({price:value.items[0].price+1})]});await c.api.refresh();assert.equal(restored,true);
 }
 c.api.setActive(false);
});
