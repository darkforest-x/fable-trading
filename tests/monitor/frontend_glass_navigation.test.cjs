'use strict';
// Exercise the real More controller: route dispatch, keyboard, focus and no
// business writes. DOM fixtures contain both route buttons and account links.
const test=require('node:test'),assert=require('node:assert/strict'),vm=require('node:vm'),fs=require('node:fs');
const source=fs.readFileSync('yoyo/monitor/static/workspace-command.js','utf8');
function harness(mobile=false){
 const handlers={},windowHandlers={},viewportHandlers={},nodes=new Map(),visits=[];
 let document;
 const element=(tag='DIV')=>({tagName:tag,children:[],events:{},textContent:'',value:'',open:false,
  style:{setProperty(k,v){this[k]=v;}},
  append(...items){this.children.push(...items.filter(Boolean));},replaceChildren(){this.children=[];},
  addEventListener(k,f){this.events[k]=f;},focus(){document.activeElement=this;},
  click(){this.events.click?.({});},showModal(){this.open=true;},close(){this.open=false;this.events.close?.();},
  querySelectorAll(tag){return this.children.flatMap(c=>[...(c.tagName.toLowerCase()===tag?[c]:[]),...c.querySelectorAll(tag)]);}
 });
 const get=id=>{if(!nodes.has(id))nodes.set(id,element());return nodes.get(id);};
 const fixtures=[['market','市场','market'],['signals','信号','signals'],['yolo-detect','巡检','signals'],['research','实验室总览','research'],['datasets','数据与标注','research'],['trading-books','书架与研读','learn'],['system','服务与数据节点','manage'],['signals?pane=account','自动执行账户','accounts',true]];
 const items=fixtures.map(([view,title,group,link])=>Object.assign(element(),{
  dataset:link?{}:{view},textContent:title,getAttribute:k=>k==='aria-label'?title:k==='href'&&link?'#'+view:null,
  closest:()=>({dataset:{navGroup:group}}),click(){visits.push(view);}
 }));
 document={activeElement:null,body:{classList:{contains:()=>false}},getElementById:get,
  querySelector:()=>get('workspace-command').open?get('workspace-command'):null,
  querySelectorAll:()=>items,createElement:tag=>element(tag.toUpperCase()),addEventListener:(k,f)=>handlers[k]=f};
 const viewport={height:844,offsetTop:0,addEventListener:(k,f)=>viewportHandlers[k]=f};
 vm.runInNewContext(source,{document,window:{innerHeight:844,visualViewport:viewport,matchMedia:()=>({matches:mobile}),addEventListener:(k,f)=>windowHandlers[k]=f}});
 const trigger=get('workspace-search'),dialog=get('workspace-command'),input=get('workspace-command-input'),results=get('workspace-command-results');
 const buttons=()=>results.querySelectorAll('button');
 const open=()=>{trigger.focus();trigger.click();};
 const search=value=>{input.value=value;input.events.input();};
 const key=(key,target=dialog)=>target.events.keydown({key,preventDefault(){}});
 return {get,document,trigger,dialog,input,results,buttons,open,search,key,visits,windowHandlers,handlers,element,viewport,viewportHandlers};
}
test('More defaults to grouped secondary destinations; searching reaches primary routes too',()=>{
 const h=harness();h.open();
 assert.equal(h.dialog.open,true);assert.equal(h.document.activeElement,h.input);
 assert.equal(h.buttons().length,5);assert.equal(h.results.querySelectorAll('h3').length,4);
 h.search('信号');assert.equal(h.buttons().length,2);h.key('Enter');
 assert.deepEqual(h.visits,['signals']);assert.equal(h.dialog.open,false);
 assert.equal(h.document.activeElement,h.get('main'));
});
test('account links preserve their query parameter and do not fall through to records',()=>{
 const h=harness();h.open();h.search('自动执行账户');h.buttons()[0].click();
 assert.deepEqual(h.visits,['signals?pane=account']);
});
test('Escape restores trigger focus; browser navigation closes without stealing destination focus',()=>{
 const h=harness();h.open();h.search('不存在');assert.equal(h.buttons().length,0);
 h.key('Escape');assert.equal(h.dialog.open,false);assert.equal(h.document.activeElement,h.trigger);
 h.open();h.get('main').focus();h.windowHandlers.hashchange();
 assert.equal(h.dialog.open,false);assert.equal(h.document.activeElement,h.get('main'));
});
test('arrow keys move focus only, and density retains its original node',()=>{
 const h=harness();h.open();h.key('ArrowDown');assert.equal(h.document.activeElement,h.buttons()[0]);
 h.key('ArrowUp');assert.equal(h.document.activeElement,h.input);assert.deepEqual(h.visits,[]);
 assert.equal(h.get('glass-appearance').children[0],h.get('workspace-density'));
});
test('phone More opens for browsing without focusing search, but explicit keyboard search focuses it',()=>{
 const h=harness(true);h.open();
 assert.equal(h.document.activeElement,h.get('workspace-command-close'));
 h.key('Escape');
 h.handlers.keydown({key:'k',ctrlKey:true,preventDefault(){}});
 assert.equal(h.document.activeElement,h.input);
});
test('lazy chart More closes the short menu and returns focus to its visible summary',()=>{
 const h=harness(true),summary=h.element(),menu={open:true,querySelector:()=>summary};
 const entry={closest:()=>menu};
 h.handlers.click({target:{closest:()=>entry}});
 assert.equal(menu.open,false);assert.equal(h.dialog.open,true);
 assert.equal(h.document.activeElement,h.get('workspace-command-close'));
 h.key('Escape');assert.equal(h.document.activeElement,summary);
 h.handlers.click({target:{closest:()=>entry}});h.search('服务');h.buttons()[0].click();
 assert.deepEqual(h.visits,['system']);assert.equal(h.document.activeElement,h.get('main'));
});
test('global SPIKE More restores the visible logo rather than its collapsed menu item',()=>{
 const h=harness(),summary=h.element(),menu={open:true,querySelector:()=>summary};
 h.trigger.closest=()=>menu;
 h.open();assert.equal(menu.open,false);assert.equal(h.dialog.open,true);
 h.key('Escape');assert.equal(h.document.activeElement,summary);
});
test('visible viewport changes constrain the open phone menu and reopening resets list scroll',()=>{
 const h=harness(true);h.open();h.results.scrollTop=400;
 h.viewport.height=390;h.viewport.offsetTop=42;h.viewportHandlers.resize();
 assert.equal(h.dialog.style['--command-viewport-height'],'390px');
 assert.equal(h.dialog.style['--command-viewport-top'],'42px');
 h.viewport.offsetTop=52;h.viewportHandlers.scroll();
 assert.equal(h.dialog.style['--command-viewport-top'],'52px');
 h.key('Escape');h.open();assert.equal(h.results.scrollTop,0);
});
