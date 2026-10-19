'use strict';
// Exercise the workspace controller's routing, focus and local presentation state.
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const source=fs.readFileSync('yoyo/monitor/static/workspace-layouts.js','utf8');
const html=fs.readFileSync('yoyo/monitor/static/index.html','utf8');
const {GROUPS}=require('../../yoyo/monitor/static/workspace-layouts.js');
function node(dataset={}) {
  const classes=new Set();
  return {dataset,hidden:false,value:'',textContent:'',attrs:{},events:{},children:[],
    classList:{toggle(k,on){if(on)classes.add(k);else classes.delete(k);},remove(k){classes.delete(k);},contains(k){return classes.has(k);}},removeAttribute(k){delete this.attrs[k];},setAttribute(k,v){this.attrs[k]=v;},addEventListener(k,fn){this.events[k]=fn;},
    getAttribute(k){return this.attrs[k]??null;},replaceChildren(...children){this.children=children;},append(child){this.children.push(child);},
    click(){this.events.click?.({target:this});},focus(){this.focused=true;},
    closest(){return this;},getClientRects(){return this.hidden?[]:[1];}};
}
function mount(saved=new Map()) {
  const rail=Object.keys(GROUPS).map(key=>node({workspaceGroup:key}));
  const groups=Object.keys(GROUPS).map(key=>node({navGroup:key}));
  const buttons=Object.values(GROUPS).flatMap(g=>g.views).map(view=>Object.assign(node({view}),{textContent:view}));
  const links=['#yolo-detect?tab=account','#signals?pane=account'].map(href=>{const el=node();el.setAttribute('href',href);return el;});
  const panes=['channels','routes'].map(key=>node({notificationPane:key}));
  const ids=Object.fromEntries(['main','main-sidebar','workspace-nav-filter','workspace-outline','workspace-group-title','workspace-nav-empty','workspace-density','notification-channel-cards','notification-routes-section','page-title','page-description'].map(id=>[id,node()]));
  ids.main.querySelector=()=>null;
  ids['main-sidebar'].querySelectorAll=selector=>selector==='.nav-item[href]'?links:selector==='[data-nav-group]'?groups:selector.includes('data-nav-group=')?buttons.filter(b=>GROUPS[selector.match(/data-nav-group="([^"]+)"/)[1]].views.includes(b.dataset.view)):buttons;
  ids['main-sidebar'].querySelector=selector=>buttons.find(b=>b.dataset.view===selector.match(/data-view="([^"]+)"/)[1]);
  const body={dataset:{}};
  const accountLink=node(),signalLink=node(),location={hash:'#market'};
  const document={body,querySelector:selector=>selector.includes('data-primary-accounts')?accountLink:selector.includes('stitch-primary-nav')?signalLink:null,getElementById:id=>ids[id],querySelectorAll:selector=>selector==='[data-workspace-group]'?rail:panes,createElement:()=>node()};
  const navigationStates=[];
  const window={addEventListener(){},SpikeWorkspaceDisplay:{syncNavigation(){navigationStates.push({group:body.dataset.workspaceGroup,accountRoute:body.dataset.accountRoute,visibleGroups:groups.filter(g=>!g.hidden).map(g=>g.dataset.navGroup)});}}};let routed=[];
  buttons.forEach(b=>b.events.click=()=>{routed.push(b.dataset.view);window.SpikeWorkbench.navigate(b.dataset.view);});
  vm.runInNewContext(source,{document,window,location,URLSearchParams,localStorage:{getItem:k=>saved.get(k),setItem:(k,v)=>saved.set(k,v)},setTimeout:()=>1,clearTimeout(){},MutationObserver:class{observe(){}disconnect(){}}});
  return {api:window.SpikeWorkbench,rail,groups,buttons,links,panes,ids,body,saved,routed,accountLink,signalLink,location,navigationStates};
}
test('every visible route belongs to exactly one workspace and each pane has a real target',()=>{
  const routes=[...html.matchAll(/class="nav-item[^\"]*"[^>]*data-view="([^"]+)"/g)].map(x=>x[1]);
  const registered=Object.values(GROUPS).flatMap(g=>g.views);
  assert.equal(registered.length,new Set(registered).size);
  assert.deepEqual([...registered].sort(),[...routes].sort());
  const targets={channels:'notification-channel-cards',routes:'notification-routes-section'};
  for(const [,pane] of html.matchAll(/data-notification-pane="([^"]+)"/g))assert.ok(html.includes(`id="${targets[pane]}"`),pane);
});
test('workspace rail resumes the last visited page and clears stale filter/outline on navigation',()=>{
  const c=mount();c.api.navigate('models');
  assert.equal(c.body.dataset.workspaceFamily,'registry');
  assert.equal(c.groups.find(g=>g.dataset.navGroup==='research').hidden,false);
  assert.equal(c.rail.find(r=>r.dataset.workspaceGroup==='research').attrs['aria-pressed'],'true');
  c.ids['workspace-nav-filter'].value='no-match';c.ids['workspace-nav-filter'].events.input();
  assert.equal(c.ids['workspace-nav-empty'].hidden,false);
  c.rail.find(r=>r.dataset.workspaceGroup==='learn').click();
  c.rail.find(r=>r.dataset.workspaceGroup==='research').click();
  assert.deepEqual(c.routed,['learning','models']);
  assert.equal(c.ids['workspace-nav-filter'].value,'');
  assert.equal(c.ids['workspace-outline'].hidden,true);
  assert.ok(c.buttons.every(b=>!b.hidden));
});
test('keyboard rail navigation changes focus only; menu Enter dispatches the existing route',()=>{
  const c=mount();let prevented=0;
  c.rail[0].events.keydown({key:'ArrowUp',preventDefault(){prevented++;}});
  assert.equal(c.rail.at(-1).focused,true);assert.equal(c.routed.length,0);
  const filter=c.ids['workspace-nav-filter'];filter.value='auto-leaders';filter.events.input();
  filter.events.keydown({key:'Enter',preventDefault(){prevented++;}});
  assert.deepEqual(c.routed,['auto-leaders']);assert.equal(prevented,2);
});
test('notification tabs change visibility only and density persists across a new mount',()=>{
  const c=mount();assert.equal(c.ids['notification-routes-section'].hidden,true);
  c.panes[1].click();assert.equal(c.ids['notification-routes-section'].hidden,false);
  assert.equal(c.ids['notification-channel-cards'].hidden,true);
  assert.equal(c.panes[1].attrs['aria-pressed'],'true');
  c.ids['workspace-density'].click();assert.equal(c.body.dataset.density,'comfortable');
  const again=mount(c.saved);assert.equal(again.body.dataset.density,'comfortable');
  again.ids['workspace-density'].click();assert.equal(again.body.dataset.density,'compact');
});

test('historical pages keep the operations context and the rail returns to their parent',()=>{
  const c=mount();for(const view of ['watch','warmup','shadow']){
    c.api.navigate(view);assert.equal(c.rail.find(r=>r.dataset.workspaceGroup==='manage').attrs['aria-pressed'],'true');
    c.rail.find(r=>r.dataset.workspaceGroup==='manage').click();assert.equal(c.routed.at(-1),'system');
  }
});

test('account pane owns account navigation and clears it when leaving the shared signal module',()=>{
 const c=mount();c.location.hash='#signals?pane=account';c.api.navigate('signals');
 assert.equal(c.body.dataset.accountRoute,'true');assert.equal(c.ids['page-title'].textContent,'交易与账户');
 assert.equal(c.accountLink.attrs['aria-current'],'page');assert.equal(c.groups.find(g=>g.dataset.navGroup==='accounts').hidden,false);
 assert.equal(c.links[1].attrs['aria-current'],'page');assert.equal(c.links[0].attrs['aria-current'],undefined);
 c.location.hash='#yolo-detect?tab=account';c.api.navigate('yolo-detect');
 assert.equal(c.body.dataset.workspaceGroup,'accounts');assert.equal(c.links[0].attrs['aria-current'],'page');assert.equal(c.links[1].attrs['aria-current'],undefined);
 c.location.hash='#research';c.api.navigate('research');assert.equal(c.body.dataset.accountRoute,'false');assert.equal(c.accountLink.attrs['aria-current'],undefined);assert.equal(c.accountLink.classList.contains('active'),false);
 assert.ok(c.links.every(link=>!link.classList.contains('active')));
});

test('mobile navigation is resynchronized after route-dependent visibility changes',()=>{
 const c=mount();
 for(const [hash,view,group,accountRoute] of [
  ['#research','research','research','false'],
  ['#signals?pane=account','signals','accounts','true'],
  ['#signals?pane=records','signals','signals','false'],
  ['#learning','learning','learn','false'],
  ['#system','system','manage','false'],
 ]){
  c.location.hash=hash;c.api.navigate(view);
  assert.deepEqual(c.navigationStates.at(-1),{group,accountRoute,visibleGroups:[group]});
 }
});

test('paper routes own the simulation context without highlighting real accounts',()=>{
 const c=mount();
 for(const view of ['paper-desk','paper']){
  c.location.hash='#'+view;c.api.navigate(view);
  assert.equal(c.body.dataset.workspaceGroup,'simulation');
  assert.equal(c.accountLink.attrs['aria-current'],undefined);
  assert.deepEqual(c.groups.filter(g=>!g.hidden).map(g=>g.dataset.navGroup),['simulation']);
  assert.equal(c.navigationStates.at(-1).group,'simulation');
 }
 c.location.hash='#yolo-detect?tab=account';c.api.navigate('yolo-detect');
 assert.equal(c.body.dataset.workspaceGroup,'accounts');
 assert.equal(c.groups.find(g=>g.dataset.navGroup==='simulation').hidden,true);
});
