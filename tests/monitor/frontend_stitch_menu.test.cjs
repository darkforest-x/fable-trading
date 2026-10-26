'use strict';
// Run the production dismissal controller through Safari's tap/focus sequence.
const test=require('node:test'),assert=require('node:assert/strict'),vm=require('node:vm'),fs=require('node:fs');
const source=fs.readFileSync('yoyo/monitor/static/stitch-workspace.js','utf8').split('/* Account navigation')[0];
function harness(){
 const handlers={},windowHandlers={},tasks=[];
 const summary={focus(){this.focused=true;}};
 const menu={open:true,contains:n=>n===link||n===summary,querySelector:()=>summary};
 const link={closest:selector=>selector==='a'?link:menu};
 const outside={closest:()=>null};
 const document={querySelector:()=>menu.open?menu:null,querySelectorAll:()=>menu.open?[menu]:[],addEventListener:(type,fn)=>(handlers[type]??=[]).push(fn)};
 vm.runInNewContext(source,{document,window:{addEventListener:(type,fn)=>windowHandlers[type]=fn},setTimeout:fn=>tasks.push(fn)});
 const dispatch=(type,target=outside,props={})=>{for(const fn of handlers[type]||[])fn({target,key:'',preventDefault(){},stopPropagation(){},...props});};
 return {menu,link,outside,summary,dispatch,windowHandlers,flush(){while(tasks.length)tasks.shift()();}};
}
test('Safari focus fallback during a link tap preserves the target until native activation',()=>{
 const h=harness();h.dispatch('pointerdown',h.link);
 // Safari may focus the containing main before synthesizing mouseup/click.
 h.dispatch('focusin',h.outside);
 assert.equal(h.menu.open,true,'focus must not hide the pending tap target');
 h.dispatch('click',h.link);
 assert.equal(h.menu.open,true,'native anchor activation runs after click listeners');
 h.flush();assert.equal(h.menu.open,false);
});
test('keyboard focus departure, outside pointer and Escape still dismiss menus',()=>{
 const h=harness();h.dispatch('pointerdown',h.link);h.dispatch('keydown',h.link,{key:'Tab'});h.dispatch('focusin');
 assert.equal(h.menu.open,false);
 h.menu.open=true;h.dispatch('pointerdown');assert.equal(h.menu.open,false);
 h.menu.open=true;h.dispatch('keydown',h.link,{key:'Escape'});
 assert.equal(h.menu.open,false);assert.equal(h.summary.focused,true);
});
test('cancelled or completed taps release the focus guard; same-route links close too',()=>{
 const h=harness();h.dispatch('pointerdown',h.link);h.dispatch('pointercancel',h.link);h.dispatch('focusin');
 assert.equal(h.menu.open,false);
 h.menu.open=true;h.dispatch('pointerdown',h.link);h.dispatch('click',h.link);h.flush();
 assert.equal(h.menu.open,false);
 h.menu.open=true;h.dispatch('focusin');assert.equal(h.menu.open,false);
 h.menu.open=true;h.windowHandlers.hashchange();assert.equal(h.menu.open,false);
});
