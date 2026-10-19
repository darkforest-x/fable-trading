'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');

function menus(){
  const handlers={},windowHandlers={};
  const menu={open:true,contains:n=>n===inside||n===summary,querySelector:()=>summary};
  const summary={focus(){this.focused=true;}};
  const inside={closest:()=>menu},outside={closest:()=>null};
  const document={querySelector:()=>null,querySelectorAll:()=>menu.open?[menu]:[],addEventListener:(k,f)=>handlers[k]=f};
  vm.runInNewContext(fs.readFileSync('yoyo/monitor/static/workspace-minimal.js','utf8'),{document,window:{addEventListener:(k,f)=>windowHandlers[k]=f}});
  return {handlers,windowHandlers,menu,summary,inside,outside};
}
test('Escape closes the active menu and returns focus without bubbling into a parent drawer',()=>{
  const c=menus();let prevented=0,stopped=0;
  c.handlers.keydown({key:'Escape',target:c.inside,preventDefault(){prevented++;},stopPropagation(){stopped++;}});
  assert.equal(c.menu.open,false);assert.equal(c.summary.focused,true);assert.equal(prevented,1);assert.equal(stopped,1);
});
test('menu interactions keep it open; outside click and keyboard departure close without stealing focus',()=>{
  const c=menus();c.handlers.click({target:c.inside});assert.equal(c.menu.open,true);
  c.handlers.focusin({target:c.inside});assert.equal(c.menu.open,true);
  c.handlers.focusin({target:c.outside});assert.equal(c.menu.open,false);assert.equal(c.summary.focused,undefined);
  c.menu.open=true;c.handlers.click({target:c.outside});assert.equal(c.menu.open,false);
  c.menu.open=true;c.windowHandlers.hashchange();assert.equal(c.menu.open,false);
});

function inspectorHarness(){
  const nodes=[];
  function el(tag='DIV',className=''){
    const n={tagName:tag,className,dataset:{},children:[],events:{},attributes:{},hidden:false,value:'',
      get firstChild(){return this.children[0];},
      classList:{contains:name=>n.className.split(' ').includes(name)},
      hasAttribute:()=>false,setAttribute(k,v){this.attributes[k]=v;},
      addEventListener(k,fn){this.events[k]=fn;},
      append(...items){for(const item of items){if(item.parent)item.parent.children=item.parent.children.filter(x=>x!==item);item.parent=this;this.children.push(item);}},
      prepend(item){this.append(item);this.children=[item,...this.children.filter(x=>x!==item)];},
      closest:()=>({id:'signals-view'}),
      matches:()=>false,
      querySelector(selector){return this.querySelectorAll(selector)[0]||null;},
      querySelectorAll(selector){const descendants=this.children.flatMap(c=>[c,...c.querySelectorAll('*')]);return descendants.filter(c=>selector==='*'||selector==='h2'&&c.tagName==='H2'||selector==='.search-field'&&c.className==='search-field'||selector==='.signal-result-summary'&&c.className==='signal-result-summary'||selector==='input[type=search],select'&&['INPUT','SELECT'].includes(c.tagName));}
    };nodes.push(n);return n;
  }
  const root=el('ASIDE','signal-query-inspector work-inspector'),heading=el('H2'),search=el('DIV','search-field'),input=el('INPUT'),summary=el('DIV','signal-result-summary'),select=el('SELECT');
  heading.textContent='筛选信号';input.value='BTC';search.append(input);select.selectedIndex=0;select.options=[{text:'全部方向'},{text:'多头'}];root.append(heading,search,select,summary);
  const document={createElement:tag=>el(tag.toUpperCase()),getElementById:()=>el(),querySelectorAll:s=>s==='.work-inspector'?[root]:[]};
  vm.runInNewContext(fs.readFileSync('yoyo/monitor/static/workspace-panels.js','utf8'),{document,window:{},MutationObserver:class{observe(){}},Event:class{}});
  return {root,search,input,select,summary,button:nodes.find(n=>n.className==='work-inspector-toggle'),content:nodes.find(n=>n.className==='work-inspector-content')};
}
test('collapsing filters retains the original controls, search and summary while removing hidden inputs from tab flow',()=>{
  const c=inspectorHarness();assert.equal(c.content.hidden,true);
  assert.equal(c.search.parent,c.root);assert.equal(c.summary.parent,c.root);assert.equal(c.input.parent,c.search);
  c.button.events.click();assert.equal(c.content.hidden,false);assert.equal(c.button.attributes['aria-expanded'],'true');
  c.select.selectedIndex=1;c.content.events.change();assert.match(c.button.textContent,/多头/);
  c.button.events.click();assert.equal(c.content.hidden,true);assert.equal(c.select.selectedIndex,1);assert.equal(c.input.value,'BTC');
  c.button.events.click();assert.equal(c.select.parent,c.content);assert.equal(c.select.selectedIndex,1);
});
