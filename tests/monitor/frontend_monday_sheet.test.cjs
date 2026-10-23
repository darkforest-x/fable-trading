'use strict';
const test=require('node:test'),assert=require('node:assert/strict');
const {sheetMotion}=require('../../yoyo/monitor/static/monday-quick.js');
function fixture(){
 let id=0,capture=null,computed=null,reduced=false,active=true,dismissed=0,finished=0;
 const frames=new Map(),timers=new Map(),writes=[];
 const node=()=>{const classes=new Set();return {events:{},values:{},classList:{toggle:(n,on)=>on?classes.add(n):classes.delete(n),contains:n=>classes.has(n)},style:{setProperty(k,v){this.owner.values[k]=v;writes.push([k,v]);}},addEventListener(n,f){this.events[n]=f;}};};
 const panel=node(),backdrop=node();panel.style.owner=panel;backdrop.style.owner=backdrop;
 panel.getBoundingClientRect=()=>({height:633});panel.setPointerCapture=x=>capture=x;panel.hasPointerCapture=x=>capture===x;panel.releasePointerCapture=()=>capture=null;
 const env={requestAnimationFrame:f=>{frames.set(++id,f);return id;},cancelAnimationFrame:i=>frames.delete(i),setTimeout:f=>{timers.set(++id,f);return id;},clearTimeout:i=>timers.delete(i),
  matchMedia:()=>({matches:reduced}),getComputedStyle:()=>({transform:`matrix(1, 0, 0, 1, 0, ${computed??parseFloat(panel.values['--mf-sheet-offset']||0)})`})};
 const motion=sheetMotion({panel,backdrop,env,active:()=>active,dismiss:()=>{dismissed++;motion.exit(()=>finished++);}});
 const target=zone=>({closest:s=>s==='[data-quick-grabber]'?(zone==='grab'?{}:null):s==='.mf-quick-heading'?(['title','close'].includes(zone)?{}:null):zone==='close'?{}:null});
 const event=(y,at=0,x=100,zone='grab')=>({target:target(zone),pointerId:1,isPrimary:true,button:0,clientX:x,clientY:y,timeStamp:at,preventDefault(){}});
 const send=(name,...args)=>panel.events[name](event(...args));
 const run=map=>{for(const [i,f] of [...map]){map.delete(i);f();}};
 return {panel,backdrop,motion,frames,timers,writes,send,event,paint:()=>run(frames),settle:()=>run(timers),offset:()=>parseFloat(panel.values['--mf-sheet-offset']||0),capture:()=>capture,dismissed:()=>dismissed,finished:()=>finished,setComputed:v=>computed=v,setReduced:v=>reduced=v,setActive:v=>active=v};
}
test('drag coalesces to the latest 1:1 offset with proportional fade and no transition',()=>{
 const f=fixture();f.send('pointerdown',200);f.send('pointermove',220,16);f.send('pointermove',260,32);
 assert.equal(f.frames.size,1);f.paint();assert.equal(f.offset(),60);assert.equal(f.panel.classList.contains('mf-sheet-dragging'),true);assert.equal(f.panel.classList.contains('mf-sheet-settling'),false);
 assert.ok(Math.abs(Number(f.backdrop.values.opacity)-(1-60/633))<1e-9);
 f.motion.reset();assert.equal(f.offset(),0);assert.equal(f.capture(),null);assert.equal(f.frames.size,0);
});
test('long drag exits from current offset and releases exactly once on transition completion',()=>{
 const f=fixture();f.send('pointerdown',200);f.send('pointermove',300,100);f.paint();f.writes.length=0;f.send('pointerup',300,150);
 assert.equal(f.dismissed(),1);assert.equal(f.finished(),0);assert.equal(f.offset(),665);
 assert.deepEqual(f.writes.filter(([k])=>k==='--mf-sheet-offset').map(([,v])=>v),['100px','100px','100px','665px']);
 f.panel.events.transitionend({target:f.panel,propertyName:'transform'});assert.equal(f.finished(),1);assert.equal(f.timers.size,0);f.settle();assert.equal(f.finished(),1);
});
test('short slow drag rebounds, and regrabbing an in-flight rebound continues from its visual position',()=>{
 const f=fixture();f.send('pointerdown',200,0,100,'title');f.send('pointermove',240,100,100,'title');f.paint();f.send('pointerup',240,260,100,'title');
 assert.equal(f.dismissed(),0);assert.equal(f.offset(),0);assert.equal(f.timers.size,1);
 f.setComputed(18);f.send('pointerdown',218,280,100,'title');f.setComputed(null);f.send('pointermove',238,300,100,'title');f.paint();assert.equal(f.offset(),38);assert.equal(f.timers.size,0);
 f.send('pointercancel',238,320);assert.equal(f.offset(),0);assert.equal(f.capture(),null);f.settle();assert.equal(f.dismissed(),0);
});
test('fast short flick dismisses, while horizontal drags, controls and content do not',()=>{
 const f=fixture();for(const zone of ['content','close']){f.send('pointerdown',200,0,100,zone);assert.equal(f.capture(),null);}
 f.send('pointerdown',200);f.send('pointermove',220,16,180);f.send('pointerup',350,30,180);assert.equal(f.dismissed(),0);
 f.send('pointerdown',200);f.send('pointermove',235,30);f.send('pointerup',235,40);assert.equal(f.dismissed(),1);
 f.motion.reset();f.settle();assert.equal(f.finished(),0,'teardown cancels pending exit');
});
test('reduced motion retains direct tracking and skips only settling; inactive sheets never capture',()=>{
 const f=fixture();f.setActive(false);f.send('pointerdown',200);assert.equal(f.capture(),null);f.setActive(true);f.setReduced(true);
 f.send('pointerdown',200);f.send('pointermove',300,200);f.paint();assert.equal(f.offset(),100);f.send('pointerup',300,400);assert.equal(f.finished(),1);assert.equal(f.timers.size,0);
});
