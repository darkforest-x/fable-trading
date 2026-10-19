'use strict';
const test=require('node:test'),assert=require('node:assert/strict');
const T=require('../../yoyo/vision_research/static/chart-tools.js');
const ts=Array.from({length:120},(_,i)=>1000+i*60);
test('tail follow keeps zoom width and right padding as bars are appended or rolled',()=>{
 const saved=T.anchor({from:80,to:124},ts);
 assert.deepEqual(T.restore(saved,[...ts,8200,8260]),{from:82,to:126});
 assert.deepEqual(T.restore(saved,[...ts.slice(2),8200,8260]),{from:80,to:124});
});
test('historical view stays on timestamps, including missing bars and fractional edges',()=>{
 const saved=T.anchor({from:10.25,to:40.75},ts);
 const shifted=ts.slice(5);
 assert.deepEqual(T.restore(saved,shifted),{from:5.25,to:35.75});
 const missing=ts.filter((_,i)=>i!==20);
 assert.deepEqual(T.restore(saved,missing),{from:10.25,to:39.75});
});
test('invalid ranges and entirely evicted windows never restore a blank canvas',()=>{
 for(const range of [null,{from:4,to:3},{from:0,to:Infinity}])assert.equal(T.anchor(range,ts),null);
 assert.equal(T.restore(T.anchor({from:0,to:10},ts),ts.slice(50)),null);
 assert.equal(T.restore(null,ts),null);assert.equal(T.anchor({from:0,to:4},[]),null);
});
test('views are isolated by symbol/period and bounded in memory',()=>{
 const v=new T.Views(2);v.save('BTC/5m',{from:0,to:20},ts);v.save('BTC/1H',{from:30,to:50},ts);
 assert.deepEqual(v.get('BTC/5m',ts),{from:0,to:20});assert.equal(v.get('ETH/5m',ts),null);
 v.save('ETH/5m',{from:40,to:60},ts);assert.equal(v.get('BTC/5m',ts),null);assert.equal(v.values.size,2);
});
test('incremental refresh skips unchanged data, updates only the tail, replaces corrections and truncations',()=>{
 const calls=[],s={setData:r=>calls.push(['set',r]),update:r=>calls.push(['update',r])},sync=T.syncer();
 const first=[{time:1,value:10},{time:2,value:20}];sync(s,first);sync(s,first.map(r=>({...r})));
 assert.equal(calls.length,1);
 sync(s,[first[0],{time:2,value:21},{time:3,value:30}]);assert.deepEqual(calls.map(c=>c[0]),['set','update','update']);
 sync(s,[{time:1,value:9},{time:2,value:21},{time:3,value:30}]);assert.equal(calls.at(-1)[0],'set');
 sync(s,first);assert.equal(calls.at(-1)[0],'set');
});
test('readout identifies cursor versus latest and labels opening timestamp accurately',()=>{
 const row={o:.0000012,h:.0000014,l:.000001,c:.0000013};
 assert.match(T.readout(row,0,true),/最新 K 线.*01\/01 08:00 北京开盘/);
 assert.match(T.readout(row,0,false),/光标/);assert.match(T.readout(row,0,true),/0.0000013/);
 assert.equal(T.priceFormat([{low:.000001}]).precision,10);
});
function controls(){
 const attrs=new Map(),handlers={},buttons={};for(const name of ['latest','fit','log'])buttons[name]={dataset:{cv:name},setAttribute:(k,v)=>attrs.set(name+':'+k,v),getAttribute:k=>attrs.get(name+':'+k),click:()=>handlers.click({target:{closest:()=>buttons[name]}})};
 const keys={},canvas={setAttribute(){},addEventListener:(k,f)=>keys[k]=f,removeEventListener:k=>delete keys[k]};
 const root={contains:()=>true,querySelector:q=>buttons[q.match(/cv=(\w+)/)?.[1]],querySelectorAll:()=>Object.values(buttons),addEventListener:(k,f)=>handlers[k]=f,removeEventListener:k=>delete handlers[k]};
 let range={from:10,to:50},options={},fits=0;const scale={getVisibleLogicalRange:()=>range,setVisibleLogicalRange:r=>range=r,fitContent:()=>fits++};
 const chart={timeScale:()=>scale,priceScale:()=>({applyOptions:o=>options={...options,...o}})},rows=Array.from({length:120},()=>({low:1}));
 const binding=T.bind(root,{chart:()=>chart,rows:()=>rows,canvas});
 return {buttons,attrs,canvas,keys,rows,binding,handlers,range:()=>range,options:()=>options,fits:()=>fits};
}
test('latest keeps width; keyboard only handles chart focus; log rejects nonpositive data',()=>{
 const h=controls();h.buttons.latest.click();assert.deepEqual(h.range(),{from:84,to:124});
 h.keys.keydown({target:{},key:'ArrowLeft'});assert.equal(h.range().from,84);
 h.keys.keydown({target:h.canvas,key:'ArrowLeft',preventDefault(){}});assert.equal(h.range().from,78);
 h.buttons.log.click();assert.equal(h.options().mode,1);h.buttons.log.click();assert.equal(h.options().mode,0);
 h.rows[0].low=0;h.buttons.log.click();assert.equal(h.options().mode,0);assert.equal(h.attrs.get('log:aria-pressed'),'false');
 h.binding.destroy();assert.equal(h.handlers.click,undefined);assert.equal(h.keys.keydown,undefined);
});
test('resize is coalesced, ignores hidden zero sizes, retains viewport and cleans pending work',()=>{
 const old={raf:global.requestAnimationFrame,caf:global.cancelAnimationFrame,ro:global.ResizeObserver};
 let callback,tick,disconnected=false,range={from:20,to:70},calls=0;
 global.requestAnimationFrame=fn=>{tick=fn;return 1;};global.cancelAnimationFrame=()=>tick=null;
 global.ResizeObserver=class{constructor(fn){callback=fn;}observe(){}disconnect(){disconnected=true;}};
 try{
  const host={clientWidth:800,clientHeight:400},scale={getVisibleLogicalRange:()=>range,setVisibleLogicalRange:r=>range=r};
  const chart={timeScale:()=>scale,resize:()=>{calls++;range={from:0,to:200};}},binding=T.resize(chart,host);
  tick();assert.deepEqual(range,{from:20,to:70});
  host.clientWidth=1400;callback();callback();tick();assert.equal(calls,2);assert.deepEqual(range,{from:20,to:70});
  host.clientWidth=0;callback();tick();assert.equal(calls,2);
  callback();binding.disconnect();assert.equal(disconnected,true);assert.equal(tick,null);
 }finally{global.requestAnimationFrame=old.raf;global.cancelAnimationFrame=old.caf;global.ResizeObserver=old.ro;}
});
test('expanded dialog survives polled sibling replacement and restores inert, focus and scrolling',()=>{
 const original=global.MutationObserver;let changed,disconnected=false;
 global.MutationObserver=class{constructor(fn){changed=fn;}observe(){}disconnect(){disconnected=true;}};
 try{
  const doc={body:null,activeElement:null,addEventListener(){},removeEventListener(){}};
  const node=tag=>{const classes=new Set(),attrs=new Map();return {tagName:tag,inert:false,isConnected:true,children:[],parentElement:null,style:{},ownerDocument:doc,classList:{add:c=>classes.add(c),remove:c=>classes.delete(c),contains:c=>classes.has(c)},getAttribute:k=>attrs.get(k)??null,setAttribute:(k,v)=>attrs.set(k,v),removeAttribute:k=>attrs.delete(k),getRootNode:()=>doc,contains(n){return n===this||this.children.some(c=>c.contains(n));},focus(){doc.activeElement=this;}};};
  const body=doc.body=node('BODY'),dialog=node('DIALOG'),panel=node('SECTION'),background=node('ASIDE'),button=node('BUTTON'),originalSibling=node('DIV');
  body.style.overflow='auto';body.children=[dialog,background];dialog.parentElement=body;background.parentElement=body;
  dialog.children=[panel,originalSibling];panel.parentElement=dialog;originalSibling.parentElement=dialog;panel.children=[button];button.parentElement=panel;panel.closest=()=>dialog;
  doc.activeElement=originalSibling;const close=T.fullscreen(panel,button);
  assert.equal(background.inert,true);assert.equal(dialog.classList.contains('cv-dialog-host'),true);
  const replacement=node('DIV');replacement.parentElement=dialog;dialog.children=[panel,replacement];doc.activeElement=body;
  changed([{target:dialog}]);assert.equal(replacement.inert,true);assert.equal(doc.activeElement,button);
  close();close();assert.equal(disconnected,true);assert.equal(background.inert,false);assert.equal(replacement.inert,false);assert.equal(body.style.overflow,'auto');assert.equal(dialog.classList.contains('cv-dialog-host'),false);assert.equal(doc.activeElement,originalSibling);
 }finally{global.MutationObserver=original;}
});

test('compact axes bound long calculated prices while full readouts preserve prices',()=>{
 for(const value of [81741.96125,.006771234,1.23456789,1200000,-81741.96125,0,1.23e-10,1.23e12]){
  const label=T.compactAxisPrice(value);
  assert.ok(label.length<=9,`${value}: ${label}`);
  assert.ok(Math.abs(Number(label)-value)<=Math.max(Math.abs(value)*.005,1e-14));
 }
 assert.equal(T.compactAxisPrice(81741.96125),'81742');
 assert.equal(T.axisPrice(81741.96125),'81,741.96125');
 assert.match(T.readout({open:81741.96125},0),/81,741\.96125/);
 assert.equal(T.compactAxisPrice(null),'—');
});
test('compact axis changes at resize breakpoints without changing candle viewport',()=>{
 const old={raf:global.requestAnimationFrame,caf:global.cancelAnimationFrame,ro:global.ResizeObserver};
 let callback,tick,formatter,range={from:20.5,to:90.5};
 global.requestAnimationFrame=fn=>{tick=fn;return 1;};global.cancelAnimationFrame=()=>{};
 global.ResizeObserver=class{constructor(fn){callback=fn;}observe(){}disconnect(){}};
 try{
  const host={clientWidth:382,clientHeight:420};
  const chart={timeScale:()=>({getVisibleLogicalRange:()=>range,setVisibleLogicalRange:r=>range=r}),resize(){range={from:0,to:200};},applyOptions:o=>formatter=o.localization.priceFormatter};
  const binding=T.resize(chart,host,{compactAxis:true});tick();
  assert.equal(formatter(81741.96125),'81742');assert.deepEqual(range,{from:20.5,to:90.5});
  host.clientWidth=1000;callback();tick();
  assert.equal(formatter(81741.96125),'81,741.96125');assert.deepEqual(range,{from:20.5,to:90.5});
  binding.disconnect();
 }finally{global.requestAnimationFrame=old.raf;global.cancelAnimationFrame=old.caf;global.ResizeObserver=old.ro;}
});
