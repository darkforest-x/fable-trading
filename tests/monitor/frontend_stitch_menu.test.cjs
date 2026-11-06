'use strict';
// Run the production dismissal controller through Safari's tap/focus sequence.
const test=require('node:test'),assert=require('node:assert/strict'),vm=require('node:vm'),fs=require('node:fs');
const source=fs.readFileSync('yoyo/monitor/static/stitch-workspace.js','utf8').split('/* Account navigation')[0];
function harness(){
 const handlers={},windowHandlers={},tasks=[];let topLayer=null;
 const summary={focus(){this.focused=true;}};
 const menu={open:true,contains:n=>n===link||n===summary,querySelector:()=>summary};
 const link={closest:selector=>selector==='a'?link:menu};
 const outside={closest:()=>null};
 const document={querySelector:selector=>selector.startsWith('dialog')?topLayer:(menu.open?menu:null),querySelectorAll:()=>menu.open?[menu]:[],addEventListener:(type,fn)=>(handlers[type]??=[]).push(fn)};
 vm.runInNewContext(source,{document,window:{addEventListener:(type,fn)=>windowHandlers[type]=fn},setTimeout:fn=>tasks.push(fn)});
 const dispatch=(type,target=outside,props={})=>{for(const fn of handlers[type]||[])fn({target,key:'',preventDefault(){},stopPropagation(){},...props});};
 return {menu,link,outside,summary,dispatch,windowHandlers,layer(value){topLayer=value;},flush(){while(tasks.length)tasks.shift()();}};
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
test('native child layers and already handled Escape do not close the parent menu',()=>{
 const h=harness();
 h.layer({open:true});h.dispatch('keydown',h.link,{key:'Escape'});assert.equal(h.menu.open,true);
 h.layer(null);h.dispatch('keydown',h.link,{key:'Escape',defaultPrevented:true});assert.equal(h.menu.open,true);
 h.dispatch('keydown',h.link,{key:'Escape'});assert.equal(h.menu.open,false);assert.equal(h.summary.focused,true);
});
test('committed pushState routes update menu highlights without navigating again',()=>{
 const code=fs.readFileSync('yoyo/monitor/static/stitch-workspace.js','utf8').split('/* Account navigation')[1];
 const node=route=>({dataset:{terminalRoute:route},attrs:{},active:false,classList:{toggle(k,v){this[k]=v;}},setAttribute(k,v){this.attrs[k]=v;},removeAttribute(k){delete this.attrs[k];}});
 const links=['reclaim','signals','yolo-detect','paper-desk'].map(node),more=node('more');
 const location={hash:'#paper-desk'},visits=[];let changed;
 const document={body:{},querySelectorAll:()=>links,querySelector:()=>null,getElementById:()=>more};
 vm.runInNewContext('/* Account navigation'+code,{document,location,URLSearchParams,window:{addEventListener(){},SpikeWorkbench:{navigate:x=>visits.push(x)}},MutationObserver:class{constructor(fn){changed=fn;}observe(){}}});
 assert.equal(links[3].attrs['aria-current'],'page');
 location.hash='#research';changed();
 assert.equal(links.some(x=>x.attrs['aria-current']),false);assert.equal(more.classList.active,true);
 assert.deepEqual(visits,['paper-desk'],'highlight refresh must not re-enter route lifecycle');
 location.hash='#signals?pane=records';changed();assert.equal(links[1].attrs['aria-current'],'page');assert.equal(more.classList.active,false);
});
