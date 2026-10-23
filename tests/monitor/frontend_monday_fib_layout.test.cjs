'use strict';
const test=require('node:test'),assert=require('node:assert/strict');
const {clampPanel,mountLayout}=require('../../yoyo/monitor/static/monday-fib.js');
function harness(storage=new Map()){
 const events={},classes=new Set();let captures=0;
 const handle={events:{},addEventListener(k,fn){this.events[k]=fn;},removeEventListener(k){delete this.events[k];},setPointerCapture(){captures++;},releasePointerCapture(){captures--;}};
 const block={dataset:{mfMovable:'symbol'},style:{left:'',top:'',right:'',bottom:'',transform:''},classList:{add:k=>classes.add(k),remove:k=>classes.delete(k)},querySelector:()=>handle,getClientRects:()=>[{}],getBoundingClientRect(){return {x:parseFloat(this.style.left)||20,y:parseFloat(this.style.top)||80,width:240,height:130};}};
 const env={innerWidth:1280,innerHeight:900,localStorage:{getItem:k=>storage.get(k),setItem:(k,v)=>storage.set(k,v)},addEventListener:(k,fn)=>events[k]=fn,removeEventListener:k=>delete events[k]};
 const api=mountLayout({querySelectorAll:()=>[block]},env);api.setActive(true);
 const e=(x,y,extra={})=>({pointerId:1,clientX:x,clientY:y,button:0,preventDefault(){},stopPropagation(){},...extra});
 return {api,block,handle,env,storage,classes,events,e,captures:()=>captures};
}
test('floating bounds reject offscreen and non-finite coordinates',()=>{
 assert.deepEqual(clampPanel(-50,9999,240,130,390,844),{x:8,y:706});
 assert.deepEqual(clampPanel(NaN,Infinity,600,900,320,740),{x:8,y:8});
});
test('only grip movement beyond threshold writes a bounded persistent position',()=>{
 const c=harness();assert.equal(c.block.events,undefined);c.handle.events.pointerdown(c.e(25,90));c.handle.events.pointermove(c.e(27,91));c.handle.events.pointerup(c.e(27,91));assert.equal(c.storage.size,0);
 c.handle.events.pointerdown(c.e(25,90));c.handle.events.pointermove(c.e(2000,2000));assert.equal(c.block.style.left,'1032px');assert.equal(c.block.style.top,'762px');assert.equal(c.classes.has('mf-dragging'),true);
 c.handle.events.pointerup(c.e(2000,2000));assert.equal(c.captures(),0);assert.equal(c.classes.size,0);assert.equal(c.storage.size,1);
 const restored=harness(c.storage);assert.equal(restored.block.style.left,'1032px');assert.equal(restored.block.style.top,'762px');
 restored.handle.events.dblclick();assert.equal(restored.block.style.left,'');assert.equal(restored.block.style.top,'');
});
test('touch pointers work, secondary pointers are ignored and cancellation restores the old position',()=>{
 const c=harness();c.handle.events.pointerdown(c.e(25,90,{pointerType:'touch'}));c.handle.events.pointermove(c.e(200,200,{pointerId:2}));assert.equal(c.block.style.left,'');
 c.handle.events.pointermove(c.e(200,200,{pointerType:'touch'}));assert.equal(c.block.style.left,'195px');c.handle.events.pointercancel();assert.equal(c.block.style.left,'');assert.equal(c.storage.size,0);
 c.handle.events.pointerdown(c.e(1,1,{isPrimary:false}));assert.equal(c.captures(),0);
});
test('desktop and mobile positions are separate; keyboard reset and deactivation are reversible',()=>{
 const c=harness();c.handle.events.keydown(c.e(0,0,{key:'ArrowRight'}));assert.equal(c.block.style.left,'32px');
 c.env.innerWidth=390;c.api.refresh();assert.equal(c.block.style.left,'');
 c.handle.events.keydown(c.e(0,0,{key:'ArrowRight',shiftKey:true}));assert.equal(c.block.style.left,'60px');
 c.env.innerWidth=1280;c.api.refresh();assert.equal(c.block.style.left,'32px');
 c.handle.events.keydown(c.e(0,0,{key:'Home'}));assert.equal(c.block.style.left,'');
 c.handle.events.pointerdown(c.e(20,80));c.handle.events.pointermove(c.e(250,250));c.api.setActive(false);assert.equal(c.block.style.left,'');assert.equal(c.captures(),0);assert.deepEqual(c.events,{});
 c.api.destroy();assert.deepEqual(c.handle.events,{});
});
