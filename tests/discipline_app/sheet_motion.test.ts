import test from 'node:test';
import assert from 'node:assert/strict';
import {createSheetMotion} from '../../yoyo/discipline_app/mobile/src/sheetMotion.ts';
function fixture(){
 let id=0,capture:number|null=null,visual:number|null=null,reduced=false,active=true,closed=0,dismissals=0;
 const frames=new Map<number,()=>void>(),timers=new Map<number,()=>void>();
 function node(){const classes=new Set<string>(),values:Record<string,string>={},events:Record<string,(e:any)=>void>={};return {dataset:{},values,events,classList:{toggle:(n:string,on:boolean)=>on?classes.add(n):classes.delete(n),contains:(n:string)=>classes.has(n)},style:{setProperty:(n:string,v:string)=>values[n]=v},addEventListener:(n:string,f:any)=>events[n]=f,removeEventListener:(n:string)=>delete events[n],getBoundingClientRect:()=>({height:600}),hasPointerCapture:(x:number)=>x===capture,setPointerCapture:(x:number)=>capture=x,releasePointerCapture:()=>capture=null};}
 const panel=node(),backdrop=node();
 const env={matchMedia:()=>({matches:reduced}),requestAnimationFrame:(f:()=>void)=>{frames.set(++id,f);return id},cancelAnimationFrame:(n:number)=>frames.delete(n),setTimeout:(f:()=>void)=>{timers.set(++id,f);return id},clearTimeout:(n:number)=>timers.delete(n),getComputedStyle:()=>({transform:'none',translate:`0px ${visual??parseFloat(panel.values['--sheet-drag']||'0')}px`}),addEventListener(){},removeEventListener(){}};
 const api=createSheetMotion(panel as unknown as HTMLElement,backdrop as unknown as HTMLElement,()=>active,()=>{dismissals++;api.exit(()=>closed++)},env as unknown as Window);
 function send(name:string,y:number,at=0,x=100,zone='handle',pointerId=1){panel.events[name]({isPrimary:true,button:0,pointerId,clientX:x,clientY:y,timeStamp:at,target:{closest:(s:string)=>s==='.sheet-handle,h2,.fib-ticket-title'?(zone==='handle'?{}:null):zone==='input'?{}:null},preventDefault(){}})}
 const run=(map:Map<number,()=>void>)=>{for(const [k,f] of [...map]){map.delete(k);f()}};
 return {panel,backdrop,api,frames,timers,send,paint:()=>run(frames),finish:()=>run(timers),offset:()=>parseFloat(panel.values['--sheet-drag']||'0'),capture:()=>capture,closed:()=>closed,dismissals:()=>dismissals,reduced:(v:boolean)=>reduced=v,active:(v:boolean)=>active=v,visual:(v:number|null)=>visual=v};
}
test('reduced motion still follows every drag pixel and closes only on release',()=>{
 const f=fixture();f.reduced(true);f.send('pointerdown',100);f.send('pointermove',120,16);f.send('pointermove',200,32);assert.equal(f.frames.size,1);f.paint();assert.equal(f.offset(),100);assert.equal(f.closed(),0);assert.equal(f.panel.classList.contains('sheet-settling'),false);assert.equal(Number(f.backdrop.values['--sheet-dim']),1-100/600);
 f.send('pointerup',200,100);assert.equal(f.closed(),1);assert.equal(f.capture(),null);
});
test('rebound can be regrabbed at the current visual position, then closes once',()=>{
 const f=fixture();f.send('pointerdown',100);f.send('pointermove',140,200);f.paint();f.send('pointerup',140,400);assert.equal(f.dismissals(),0);assert.equal(f.timers.size,1);
 f.visual(18);f.send('pointerdown',118,410);f.visual(null);f.send('pointermove',168,450);f.paint();assert.equal(f.offset(),68);assert.equal(f.timers.size,0);f.send('pointerup',168,460);assert.equal(f.closed(),0);
 f.panel.events.transitionend({target:f.panel,propertyName:'translate'});assert.equal(f.closed(),1);f.finish();assert.equal(f.closed(),1);
});
test('busy, body/input scrolling, horizontal movement, cancellation and teardown cannot dismiss',()=>{
 const f=fixture();for(const zone of ['body','input']){f.send('pointerdown',100,0,100,zone);assert.equal(f.capture(),null)}
 f.active(false);f.send('pointerdown',100);assert.equal(f.capture(),null);f.active(true);
 f.send('pointerdown',100);f.send('pointermove',120,16,160);assert.equal(f.capture(),null);f.send('pointerup',200,32);assert.equal(f.dismissals(),0);
 f.send('pointerdown',100);f.send('pointermove',130,100);f.paint();f.send('pointercancel',130,120,100,'handle',2);assert.equal(f.capture(),1);f.send('pointercancel',130,130);f.finish();assert.equal(f.offset(),0);
 f.send('pointerdown',100);f.send('pointermove',200,200);f.active(false);f.send('pointerup',200,240);f.finish();assert.equal(f.dismissals(),0);
 f.active(true);f.send('pointerdown',100);f.send('pointermove',200,100);f.send('pointerup',200,200);f.api.destroy();f.finish();assert.equal(f.closed(),0);assert.equal(f.frames.size,0);assert.equal(f.capture(),null);assert.deepEqual(f.panel.events,{});
});
test('regrabbing a rebound does not count its inherited offset as a new short flick',()=>{
 const f=fixture();f.visual(20);f.send('pointerdown',120);f.visual(null);f.send('pointermove',140,10);f.paint();assert.equal(f.offset(),40);f.send('pointerup',140,15);f.finish();assert.equal(f.dismissals(),0);assert.equal(f.offset(),0);
});
