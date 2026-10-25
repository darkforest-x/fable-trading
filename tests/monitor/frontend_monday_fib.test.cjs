'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const source=fs.readFileSync('yoyo/monitor/static/monday-fib.js','utf8');
const {price,selectRows,latestTouch,touchTime,recentTouch,RECENT_MS,selection,tradingViewURL}=require('../../yoyo/monitor/static/monday-fib.js');
const event=(o={})=>({source:'live',observed_ms:Date.now()-60000,candle_ms:Date.now()-60000,status:'muted',level:84375.7884,...o});
const item=(o={})=>({symbol:'BTC-USDT-SWAP',state:'ready',stale:false,price:82500,high:83149.8,low:81166,upper:84375.7884,lower:79940.0116,received_ms:Date.now(),distance_pct:1.2,nearest:'upper',touches:{upper:event()},...o});
const snap=(o={})=>({now_ms:Date.now(),week:'2026-10-19',monday_complete:true,stream:{connected:true,last_message_ms:Date.now()},telegram:{enabled:false,configured:true},counts:{total:1,touched:0},items:[item()],events:[],...o});
const response=(d,ok=true)=>({ok,json:async()=>d});
function harness(fetchImpl,navigator={},localStorage){
 const els=new Map(),timers=new Map();let id=0;
 function el(name){if(!els.has(name))els.set(name,{innerHTML:'',textContent:'',hidden:false,disabled:false,value:'',dataset:{},style:{setProperty(){}},events:{},attrs:{},addEventListener(t,fn){this.events[t]=fn;},setAttribute(k,v){this.attrs[k]=v;},classList:{toggle(){}},contains(){return false;},focus(){},querySelectorAll(){return [];},querySelector(){return null;}});return els.get(name);}
 const routes={};const charts=[];const window={SpikeMondayList:require('../../yoyo/monitor/static/monday-list-model.js'),navigator,localStorage,SpikeNavigation:{register(name,fn){routes[name]=fn;},sync(){}},SpikeMondayFibChart:{show:(host,row,week,options)=>charts.push({symbol:row.symbol,week,...options}),hide(){},fit(){},setInternal(){}}},document={hidden:false,activeElement:null,getElementById:el,events:{},addEventListener(t,fn){this.events[t]=fn;}};
 vm.runInNewContext(source,{window,document,fetch:fetchImpl,AbortController,Date,Intl,URLSearchParams,setTimeout(fn,ms){const n=++id;timers.set(n,{fn,ms});return n;},clearTimeout(n){timers.delete(n);}});
 return {api:window.SpikeMondayFib,el,timers,document,charts,routes};
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
 assert.ok(calls.every(([u,o])=>u==='/api/monday-fib?compact=true'&&!o.method));
 assert.equal(c.el('mf-symbol').textContent,'BTC');assert.equal(c.charts.at(-1).symbol,'BTC-USDT-SWAP');
 assert.match(c.el('mf-week').textContent,/2026-10-19/);assert.doesNotMatch(source,/mf-tg|method:'PUT'|Telegram/);c.api.setActive(false);assert.equal(c.timers.size,0);
});
test('latest follow advances, manual selection stays pinned, follow resumes and week rollover resets',async()=>{
 const a=item({symbol:'BTC-USDT-SWAP',touches:{upper:event({observed_ms:100})}}),b=item({symbol:'ETH-USDT-SWAP',touches:{lower:event({observed_ms:200})}}),d=item({symbol:'SOL-USDT-SWAP',touches:{upper:event({observed_ms:300})}});
 let value=snap({items:[a,b]});const c=harness(async()=>response(value));await ready(c);assert.equal(c.charts.at(-1).symbol,b.symbol);
 value=snap({items:[a,b,d]});await c.api.refresh();assert.equal(c.charts.at(-1).symbol,d.symbol);
 c.el('mf-rows').events.click({target:{closest:s=>s==='[data-symbol]'?{dataset:{symbol:a.symbol}}:null}});assert.equal(c.charts.at(-1).symbol,a.symbol);
 await c.api.refresh();assert.equal(c.charts.at(-1).symbol,a.symbol);assert.equal(c.el('mf-follow').attrs['aria-pressed'],'false');
 assert.equal(c.el('mf-watermark-symbol').textContent,'BTCUSDT');assert.equal(c.el('mf-watermark-period').textContent,'15m · USDT 永续');
 c.el('mf-follow').events.click();assert.equal(c.charts.at(-1).symbol,d.symbol);
 c.el('mf-rows').events.click({target:{closest:s=>s==='[data-symbol]'?{dataset:{symbol:a.symbol}}:null}});
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
test('patrol route pins the requested untapped contract and timeframe across refreshes',async()=>{
 const requested=item({symbol:'USELESS-USDT-SWAP',touches:{}});
 let value=snap({items:[item(),requested]});const c=harness(async()=>response(value));
 c.routes['monday-fib']({symbol:requested.symbol,timeframe:'1H'});await ready(c);
 assert.equal(c.charts.at(-1).symbol,requested.symbol);assert.equal(c.charts.at(-1).period,'1H');
 value=snap({items:[item()]});await c.api.refresh();
 assert.equal(c.el('mf-empty').hidden,false);assert.match(c.el('mf-empty').textContent,/USELESS 本周数据暂不可用/);
 assert.equal(c.el('mf-follow').attrs['aria-pressed'],'false');
 value=snap({items:[item(),requested]});await c.api.refresh();assert.equal(c.charts.at(-1).symbol,requested.symbol);
 c.routes['monday-fib']({symbol:requested.symbol,timeframe:'1Dutc'});assert.equal(c.charts.at(-1).period,'1D');
 c.api.setActive(false);
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
test('only the inline coin link opens externally; the rest of each row pins the wallpaper',async()=>{
 const calls=[],a=item({symbol:'BTC-USDT-SWAP',touches:{upper:event({observed_ms:100})}}),b=item({symbol:'ETH-USDT-SWAP',touches:{upper:event({observed_ms:200})}});
 const c=harness(async(u,o)=>{calls.push([u,o]);return response(snap({items:[a,b]}));});await ready(c);
 const html=c.el('mf-rows').innerHTML;
 assert.match(html,/<a class="mf-row-symbol" data-tv-symbol="BTC-USDT-SWAP" href="https:\/\/www.tradingview.com\/chart\/\?symbol=OKX%3ABTCUSDT.P&amp;interval=15" target="_blank" rel="noopener noreferrer"/);
 assert.match(html,/<\/a><small>[^<]+<\/small><\/div><button type="button" class="mf-row-value" data-preview-symbol="BTC-USDT-SWAP"/);
 assert.ok([...html.matchAll(/<a class="mf-row-symbol"[^>]*>(.*?)<\/a>/g)].every(m=>!m[1].includes('<small>')));
 assert.doesNotMatch(html,/<button[^>]*class="mf-row[" ]/);
 const before=c.charts.length;
 let stopped=false;
 c.el('mf-rows').events.click({target:{closest:s=>s==='[data-tv-symbol]'?{dataset:{tvSymbol:a.symbol}}:null},stopPropagation(){stopped=true;}});
 assert.equal(stopped,true);
 assert.equal(c.charts.length,before);assert.equal(c.el('mf-follow').attrs['aria-pressed'],'true');
 c.el('mf-rows').events.click({target:{closest:s=>s==='[data-symbol]'?{dataset:{symbol:a.symbol}}:null}});
 assert.equal(c.charts.at(-1).symbol,a.symbol);assert.equal(c.el('mf-follow').attrs['aria-pressed'],'false');
 assert.equal(c.el('mf-watermark-symbol').textContent,'BTCUSDT');assert.equal(c.el('mf-watermark-period').textContent,'15m · USDT 永续');
 assert.ok(calls.every(([u,o])=>u==='/api/monday-fib?compact=true'&&!o.method));c.api.setActive(false);
});
test('inspection link targets exact coin K-line detail and updates when its period changes',async()=>{
 const c=harness(async()=>response(snap()));await ready(c);
 assert.match(c.el('mf-rows').innerHTML,/<a data-inspect-symbol="BTC-USDT-SWAP" href="#yolo-detect\?tab=results&amp;symbol=BTC-USDT-SWAP&amp;timeframe=15m" aria-label="查看 BTC 的巡检 K 线详情"/);
 const before=c.charts.length;let stopped=false;
 c.el('mf-rows').events.click({target:{closest:s=>s==='[data-inspect-symbol]'?{dataset:{inspectSymbol:'BTC-USDT-SWAP'}}:s==='[data-symbol]'?{dataset:{symbol:'BTC-USDT-SWAP'}}:null},stopPropagation(){stopped=true;}});
 assert.equal(stopped,true);assert.equal(c.charts.length,before);assert.equal(c.el('mf-follow').attrs['aria-pressed'],'true');
 c.el('mf-period-menu').events.click({target:{closest:()=>({dataset:{period:'4H'}})}});
 assert.match(c.el('mf-rows').innerHTML,/tab=results&amp;symbol=BTC-USDT-SWAP&amp;timeframe=4H/);
 c.el('mf-period-menu').events.click({target:{closest:()=>({dataset:{period:'1D'}})}});
 assert.match(c.el('mf-rows').innerHTML,/tab=results&amp;symbol=BTC-USDT-SWAP&amp;timeframe=1Dutc/);
 c.api.setActive(false);
});
test('Windows links use only the local app protocol and preserve independent wallpaper controls',async()=>{
 for(const navigator of [{platform:'Win32'},{platform:'Win64'},{platform:'Linux',userAgentData:{platform:'Windows'}}]){
  const calls=[],c=harness(async(u,o)=>{calls.push([u,o]);return response(snap());},navigator);await ready(c);
  const html=c.el('mf-rows').innerHTML;
  assert.match(html,/href="spike-tradingview:\/\/open\?symbol=BTC-USDT-SWAP&amp;timeframe=15m"/);
  assert.match(html,/在本机 TradingView 打开 BTC 15 分钟图/);
  assert.doesNotMatch(html,/target="_blank"|https:\/\/www.tradingview.com/);
  const before=c.charts.length;
  c.el('mf-rows').events.click({target:{closest:()=>null}});
  assert.equal(c.charts.length,before);
  assert.ok(calls.every(([u,o])=>u==='/api/monday-fib?compact=true'&&!o.method));
  c.api.setActive(false);
 }
 assert.equal(tradingViewURL('1000PEPE-USDT-SWAP','Windows'),'spike-tradingview://open?symbol=1000PEPE-USDT-SWAP&timeframe=15m');
 for(const platform of ['MacIntel','Linux','iPhone',''])assert.match(tradingViewURL('BTC-USDT-SWAP',platform),/^https:/);
 assert.equal(tradingViewURL('BTC-USDT-SWAP&timeframe=1H','Win32'),'');
 assert.equal(tradingViewURL('A'.repeat(31)+'-USDT-SWAP','Win32'),'');
});
test('quote-only refresh patches the price without replacing rows or moving focus/scroll',async()=>{
 let value=snap();const c=harness(async()=>response(value));await ready(c);
 const container=c.el('mf-rows'),originalHTML=container.innerHTML,cell={textContent:'82,500'};
 let writes=0;Object.defineProperty(container,'innerHTML',{get:()=>originalHTML,set(){writes++;}});
 const focused={dataset:{tvSymbol:'BTC-USDT-SWAP'}};c.document.activeElement=focused;
 container.scrollTop=620;
 container.querySelectorAll=selector=>selector==='[data-preview-symbol]'?[{dataset:{previewSymbol:'BTC-USDT-SWAP'},querySelector:()=>cell}]:[];
 value=snap({items:[{...value.items[0],price:82501}]});await c.api.refresh();
 assert.equal(cell.textContent,'82,501');assert.equal(writes,0);assert.equal(c.document.activeElement,focused);assert.equal(container.scrollTop,620);
 c.api.setActive(false);
});

const {createListPreferences}=require('../../yoyo/monitor/static/monday-fib.js');
const storage=()=>{const values=new Map();return {getItem:k=>values.get(k),setItem:(k,v)=>values.set(k,v)};};
test('deleted occurrences survive refresh/reload; a new side or NY week can appear',()=>{
 const disk=storage(),p=createListPreferences(disk),a=item(),week='2026-10-19';
 p.toggle(a.symbol);const undo=p.remove(a,week),again=createListPreferences(disk);
 assert.equal(again.has(a.symbol),true);assert.equal(again.visible([a],week).length,0);
 const recovered=item({touches:{upper:event({source:'history',observed_ms:123,candle_ms:100})}});
 assert.equal(again.visible([recovered],week).length,0);
 const both=item({touches:{upper:event(),lower:event({observed_ms:1})}});
 const shown=again.visible([both],week);assert.deepEqual(Object.keys(shown[0].touches),['lower']);
 assert.equal(latestTouch(shown[0]).side,'lower');assert.equal(again.visible([a],'2026-10-26').length,1);
 again.undo(undo);assert.equal(again.visible([a],week).length,1);assert.equal(createListPreferences(disk).visible([a],week).length,1);
 assert.equal(Object.keys(a.touches).length,1);
});
test('favorites remain searchable across weeks without inventing a touch',()=>{
 const p=createListPreferences(storage()),a=item({touches:{}});p.toggle(a.symbol);
 assert.deepEqual(selectRows([a],'','all'),[]);
 assert.deepEqual(selectRows([a],'BTC','favorites',Date.now(),p.favorites()),[a]);
 assert.deepEqual(selectRows([a],'ETH','favorites',Date.now(),p.favorites()),[]);
 assert.equal(selection([a],a.symbol,false),a);assert.equal(selection([a],a.symbol,true),null);
 const undo=p.remove(a,'2026-10-26');assert.equal(p.has(a.symbol),false);p.undo(undo);assert.equal(p.has(a.symbol),true);
});
test('malformed or blocked local storage does not break the list',()=>{
 for(const raw of ['{bad','null','{"version":1,"favorites":4,"hidden":"invalid"}']){
  const p=createListPreferences({getItem:()=>raw,setItem(){throw Error('quota');}});
  assert.equal(p.has('BTC-USDT-SWAP'),false);p.toggle('BTC-USDT-SWAP');assert.equal(p.has('BTC-USDT-SWAP'),true);assert.equal(p.persistent,false);
  p.remove(item(),'2026-10-19');assert.equal(p.visible([item()],'2026-10-19').length,0);
 }
});
test('favorite and delete controls keep list open, persist and do not select an unrelated chart',async()=>{
 const disk=storage(),a=item({symbol:'BTC-USDT-SWAP',touches:{upper:event({observed_ms:100})}}),b=item({symbol:'ETH-USDT-SWAP',touches:{lower:event({observed_ms:200})}});
 let value=snap({items:[a,b]});const calls=[],c=harness(async(u,o)=>{calls.push(o);return response(value);},{},disk);await ready(c);
 const click=(key,symbol)=>c.el('mf-rows').events.click({target:{closest:s=>s===`[data-${key}-symbol]`?{dataset:{[key+'Symbol']:symbol}}:null},stopPropagation(){}});
 const before=c.charts.length;click('favorite',a.symbol);assert.equal(c.charts.length,before);assert.equal(c.el('mf-symbol').textContent,'ETH');
 c.el('mf-filters').events.click({target:{closest:()=>({dataset:{filter:'favorites'}})}});assert.match(c.el('mf-rows').innerHTML,/>BTC</);assert.doesNotMatch(c.el('mf-rows').innerHTML,/>ETH</);
 click('delete',a.symbol);await c.api.refresh();assert.doesNotMatch(c.el('mf-rows').innerHTML,/>BTC</);assert.match(c.el('mf-rows').innerHTML,/收藏的合约/);
 c.el('mf-list-undo').events.click();assert.match(c.el('mf-rows').innerHTML,/>BTC</);
 c.api.setActive(false);const reopened=harness(async()=>response(value),{},disk);await ready(reopened);assert.match(reopened.el('mf-rows').innerHTML,/data-favorite-symbol="BTC-USDT-SWAP" aria-pressed="true"/);
 value=snap({week:'2026-10-26',items:[{...a,touches:{}}]});await reopened.api.refresh();reopened.el('mf-filters').events.click({target:{closest:()=>({dataset:{filter:'favorites'}})}});assert.match(reopened.el('mf-rows').innerHTML,/本周尚未触价/);
 assert.ok(calls.every(o=>!o.method));reopened.api.setActive(false);
});
