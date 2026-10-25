'use strict';
const test=require('node:test'),assert=require('node:assert/strict');
const {bind}=require('../../yoyo/monitor/static/yolo-preview-sheet.js');
function fixture(){
 let id=0,captured=null,computed=null,closed=0;
 const frames=new Map(),timers=new Map(),classes=new Set(),values={};
 const target=()=>({events:{},addEventListener(k,fn){this.events[k]=fn;},removeEventListener(k){delete this.events[k];}});
 const mobile=Object.assign(target(),{matches:true}),reduced={matches:false};
 const panel=Object.assign(target(),{open:true,style:{setProperty:(k,v)=>values[k]=v},classList:{toggle:(k,v)=>v?classes.add(k):classes.delete(k)},getBoundingClientRect:()=>({height:633}),setPointerCapture:i=>captured=i,hasPointerCapture:i=>captured===i,releasePointerCapture:()=>captured=null});
 const env=Object.assign(target(),{matchMedia:q=>q.includes('700px')?mobile:reduced,requestAnimationFrame:fn=>(frames.set(++id,fn),id),cancelAnimationFrame:i=>frames.delete(i),setTimeout:fn=>(timers.set(++id,fn),id),clearTimeout:i=>timers.delete(i),getComputedStyle:()=>({transform:`matrix(1, 0, 0, 1, 0, ${computed??parseFloat(values['--yd-sheet-offset']||0)})`})});
 const motion=bind({panel,env,dismiss(){closed++;panel.open=false;motion.reset();}});
 const event=(y,at=0,{x=100,zone='header',...extra}={})=>({clientX:x,clientY:y,timeStamp:at,pointerId:1,isPrimary:true,button:0,target:{closest:s=>s==='[data-yd-sheet-drag]'?(zone==='canvas'?null:{}):(zone==='button'?{}:null)},preventDefault(){this.prevented=true;},stopImmediatePropagation(){this.stopped=true;},...extra});
 const send=(name,y,at,options)=>{const e=event(y,at,options);panel.events[name](e);return e;};
 const run=map=>{for(const [key,fn] of [...map]){map.delete(key);fn();}};
 return {panel,env,mobile,reduced,frames,timers,classes,motion,event,send,paint:()=>run(frames),settle:()=>run(timers),offset:()=>parseFloat(values['--yd-sheet-offset']||0),capture:()=>captured,closed:()=>closed,setComputed:v=>computed=v};
}
test('mobile panel follows latest finger position 1:1 and coalesces painting',()=>{
 const f=fixture();f.send('pointerdown',200);f.send('pointermove',230,20);f.send('pointermove',260,40);
 assert.equal(f.frames.size,1);f.paint();assert.equal(f.offset(),60);assert.equal(f.capture(),1);assert.equal(f.classes.has('yd-sheet-dragging'),true);f.motion.destroy();
});
test('long downward drag animates out then runs owner cleanup exactly once',()=>{
 const f=fixture();f.send('pointerdown',200);f.send('pointermove',320,200);f.paint();f.send('pointerup',320,400);
 assert.equal(f.closed(),0);assert.equal(f.offset(),665);assert.equal(f.capture(),null);
 f.panel.events.transitionend({target:f.panel,propertyName:'transform'});assert.equal(f.closed(),1);assert.equal(f.offset(),0);f.settle();assert.equal(f.closed(),1);f.motion.destroy();
});
test('short slow pull rebounds and regrab continues from the visual animation position',()=>{
 const f=fixture();f.send('pointerdown',200);f.send('pointermove',240,100);f.send('pointerup',240,300);
 assert.equal(f.closed(),0);assert.equal(f.offset(),0);assert.equal(f.timers.size,1);
 f.setComputed(18);f.send('pointerdown',218,320);f.setComputed(null);f.send('pointermove',238,350);f.paint();assert.equal(f.offset(),38);assert.equal(f.timers.size,0);
 f.motion.destroy();assert.equal(f.frames.size,0);assert.equal(f.capture(),null);
});
test('short fast flick closes, but a pause at the end removes flick velocity',()=>{
 for(const [release,expected] of [[40,1],[200,0]]){
  const f=fixture();f.send('pointerdown',200);f.send('pointermove',235,30);f.send('pointerup',235,release);f.settle();assert.equal(f.closed(),expected);f.motion.destroy();
 }
});
test('canvas and controls never enter the gesture, desktop stays unchanged',()=>{
 const f=fixture();for(const zone of ['canvas','button']){const e=f.send('pointerdown',200,0,{zone});assert.equal(e.prevented,undefined);assert.equal(f.capture(),null);}
 f.mobile.matches=false;f.send('pointerdown',200);assert.equal(f.capture(),null);assert.equal(f.closed(),0);f.motion.destroy();
});
test('horizontal/upward gestures and secondary fingers cannot dismiss',()=>{
 const f=fixture();f.send('pointerdown',200);f.send('pointermove',210,20,{x:180});f.send('pointerup',400,30,{x:180});assert.equal(f.closed(),0);
 f.send('pointerdown',200);f.send('pointermove',160,20);f.paint();assert.equal(f.offset(),0);f.send('pointerup',160,30);assert.equal(f.closed(),0);
 f.send('pointerdown',200);f.send('pointermove',220,20);f.send('pointerdown',220,25,{pointerId:2,isPrimary:false});assert.equal(f.capture(),null);f.send('pointerup',400,40);f.settle();assert.equal(f.closed(),0);f.motion.destroy();
});
test('pointer cancel and lost capture reset without dismissing',()=>{
 for(const name of ['pointercancel','lostpointercapture']){
  const f=fixture();f.send('pointerdown',200);f.send('pointermove',240,50);f.paint();f.send(name,240,60);f.settle();assert.equal(f.offset(),0);assert.equal(f.closed(),0);assert.equal(f.capture(),null);f.motion.destroy();
 }
});
test('close/reopen and viewport change cancel pending exit callbacks',()=>{
 for(const reset of [f=>f.motion.reset(),f=>f.panel.events.close(),f=>f.env.events.resize(),f=>f.mobile.events.change()]){
  const f=fixture();f.send('pointerdown',200);f.send('pointermove',350,100);f.send('pointerup',350,200);reset(f);f.panel.open=true;f.settle();assert.equal(f.closed(),0);assert.equal(f.offset(),0);f.motion.destroy();
 }
});
test('reduced motion tracks finger and closes immediately without animation timers',()=>{
 const f=fixture();f.reduced.matches=true;f.send('pointerdown',200);f.send('pointermove',310,200);f.paint();assert.equal(f.offset(),110);f.send('pointerup',310,400);assert.equal(f.closed(),1);assert.equal(f.timers.size,0);f.motion.destroy();
});
test('drag-generated click cannot become a backdrop close but next button tap remains usable',()=>{
 const f=fixture();f.send('pointerdown',200);f.send('pointermove',230,100);f.send('pointerup',230,300);
 const click=f.event(230,300,{detail:1});f.panel.events.click(click);assert.equal(click.prevented,true);assert.equal(click.stopped,true);
 f.send('pointerdown',230,400,{zone:'button'});const button=f.event(230,410,{detail:1,zone:'button'});f.panel.events.click(button);assert.equal(button.prevented,undefined);f.motion.destroy();
});
