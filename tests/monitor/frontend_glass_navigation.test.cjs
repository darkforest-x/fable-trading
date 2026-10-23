'use strict';
// Exercise the real More controller: route dispatch, keyboard, focus and no
// business writes. DOM fixtures contain both route buttons and account links.
const test=require('node:test'),assert=require('node:assert/strict'),vm=require('node:vm'),fs=require('node:fs');
const source=fs.readFileSync('yoyo/monitor/static/workspace-command.js','utf8');
function harness(){
 const handlers={},windowHandlers={},nodes=new Map(),visits=[];
 let document;
 const element=(tag='DIV')=>({tagName:tag,children:[],events:{},textContent:'',value:'',open:false,
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
 vm.runInNewContext(source,{document,window:{addEventListener:(k,f)=>windowHandlers[k]=f}});
 const trigger=get('workspace-search'),dialog=get('workspace-command'),input=get('workspace-command-input'),results=get('workspace-command-results');
 const buttons=()=>results.querySelectorAll('button');
 const open=()=>{trigger.focus();trigger.click();};
 const search=value=>{input.value=value;input.events.input();};
 const key=(key,target=dialog)=>target.events.keydown({key,preventDefault(){}});
 return {get,document,trigger,dialog,input,results,buttons,open,search,key,visits,windowHandlers,handlers};
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
