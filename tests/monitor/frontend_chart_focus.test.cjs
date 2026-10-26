'use strict';
const test=require('node:test'),assert=require('node:assert/strict');
const {createGesture,bind}=require('../../yoyo/monitor/static/chart-focus.js');
const event=(t=0,x=100,id=1)=>({pointerId:id,isPrimary:true,timeStamp:t,clientX:x,clientY:100});
function setup(){let count=0;const g=createGesture(()=>count++);return {g,count:()=>count,tap(t,x=100){g.down(event(t,x));return g.up(event(t+40,x));}};}
test('two nearby short taps toggle once; distant or slow taps do not',()=>{
 const s=setup();assert.equal(s.tap(0),false);assert.equal(s.tap(180),true);assert.equal(s.count(),1);
 s.tap(1000);s.tap(1500);assert.equal(s.count(),1);
 s.tap(2000);s.tap(2200,150);assert.equal(s.count(),1);
 s.tap(3000);s.tap(3200);assert.equal(s.count(),2);
});
test('dragging, long press, cancellation and a second finger invalidate the tap pair',()=>{
 for(const interrupt of [
  s=>{s.g.down(event(180));s.g.move(event(200,130));s.g.up(event(230));},
  s=>{s.g.down(event(180));s.g.up(event(600));},
  s=>{s.g.down(event(180));s.g.cancel(event(200));s.g.up(event(230));},
  s=>{s.g.down(event(180));s.g.down({...event(190,100,2),isPrimary:false});s.g.up(event(210,100,2));s.g.up(event(230));},
  s=>{s.g.down(event(180),false);s.g.up(event(230));}
 ]){const s=setup();s.tap(0);interrupt(s);s.tap(700);assert.equal(s.count(),0);s.tap(880);assert.equal(s.count(),1);}
});
test('reset drops an incomplete gesture across workspace activation',()=>{
 const s=setup();s.tap(0);s.g.reset();s.tap(180);assert.equal(s.count(),0);s.tap(350);assert.equal(s.count(),1);
});
test('focus suspends native modal without changing its input and restores or discards it on reset',()=>{
 const handlers={},classes=new Set(),dialog={open:true,isConnected:true,value:'25',matches:()=>true,close(){this.open=false},showModal(){this.open=true}};
 const root={classList:{toggle(k,v){v?classes.add(k):classes.delete(k)}},querySelectorAll:()=>dialog.open?[dialog]:[],focus(){},addEventListener(k,fn){handlers[k]=fn}};
 const api=bind(root),dbl=t=>handlers.dblclick({button:0,timeStamp:t,target:{closest:()=>null},preventDefault(){},stopPropagation(){}});
 dbl(1);assert.equal(api.hidden,true);assert.equal(dialog.open,false);assert.ok(classes.has('is-chart-focused'));
 dbl(2);assert.equal(api.hidden,false);assert.equal(dialog.open,true);assert.equal(dialog.value,'25');
 dbl(3);api.reset();assert.equal(api.hidden,false);assert.equal(dialog.open,false);
});
