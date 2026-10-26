'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
const source=fs.readFileSync(path.join(__dirname,'../../yoyo/monitor/static/workspace-display.js'),'utf8');
const flush=()=>new Promise(resolve=>setImmediate(resolve));
function node(id,children=[]) {
  const classes=new Set();
  const n={id,tagName:'DIV',children,inert:false,attrs:{},parentElement:null,
    classList:{contains:k=>classes.has(k),add:k=>classes.add(k),remove:k=>classes.delete(k),toggle(k,on){on??=!classes.has(k);on?classes.add(k):classes.delete(k);return on;}},
    setAttribute(k,v){this.attrs[k]=v;},removeAttribute(k){delete this.attrs[k];},querySelector(){return null;},
    querySelectorAll(){return [];},getClientRects(){return [1];},closest(selector){
      if(selector==='[inert]')for(let node=this;node;node=node.parentElement)if(node.inert)return node;
      return null;
    },focus(){this.focused=true;}};
  children.forEach(c=>c.parentElement=n);return n;
}
function harness({mobile=false,stored=null,restricted=false,native='missing',design='',contextVisible=false}={}) {
  const sidebar=node('main-sidebar'),nav=node('nav-toggle'),backdrop=node('nav-backdrop'),root=node('paper-desk-workspace'),other=node('other'),close=node('nav-close'),skip=node('skip');
  const contextButton=node('context-button'),context=node('context',[contextButton]),rail=node('rail'),footer=node('footer');
  context.getClientRects=()=>context.visible?[1]:[];context.visible=contextVisible;
  sidebar.children=[close,rail,context,footer];sidebar.children.forEach(child=>child.parentElement=sidebar);
  sidebar.querySelector=selector=>selector==='.workspace-context'?context:null;
  const topbar=node('topbar',[nav]),main=node('main',[other,root]);
  const workspace=node('workspace',[topbar,main]),shell=node('shell',[sidebar,backdrop,workspace]),body=node('body',[shell]);
  body.dataset={design};
  const label={textContent:''},button=node('fullscreen');button.parentElement=root;
  button.querySelector=()=>label;button.closest=selector=>selector==='#paper-desk-workspace'?root:selector==='[data-workspace-fullscreen]'?button:null;
  root.querySelector=()=>button;
  const events={},windowEvents={},saved=new Map(stored===null?[]:[['spike-sidebar-collapsed-v1',stored]]);
  const media={matches:mobile,addEventListener(_,fn){this.change=fn;}};
  const ids=new Map([sidebar,nav,backdrop,root,close].map(n=>[n.id,n]));
  const document={body,fullscreenElement:null,fullscreenEnabled:true,addEventListener:(name,fn)=>events[name]=fn,
    getElementById:id=>ids.get(id),querySelectorAll:selector=>selector==='.workspace,.skip-link'?[workspace,skip]:selector==='[data-workspace-fullscreen]'?[button]:[],querySelector:()=>null,
    async exitFullscreen(){this.fullscreenElement=null;events.fullscreenchange();}};
  if(native==='allowed')root.requestFullscreen=async()=>{document.fullscreenElement=root;events.fullscreenchange();};
  if(native==='denied')root.requestFullscreen=async()=>{throw new Error('unavailable');};
  const window={matchMedia:()=>media,addEventListener:(name,fn)=>windowEvents[name]=fn};
  const localStorage={getItem:k=>{if(restricted)throw new Error('storage blocked');return saved.get(k);},setItem:(k,v)=>{if(restricted)throw new Error('storage blocked');saved.set(k,v);}};
  vm.runInNewContext(source,{window,document,localStorage});
  return {api:window.SpikeWorkspaceDisplay,document,media,sidebar,context,contextButton,rail,footer,nav,root,topbar,other,workspace,close,skip,events,windowEvents,saved,label,button,
    enter:()=>events.click({target:button})};
}
test('desktop collapse restores across reload and navigation without changing mobile drawer state',()=>{
  const c=harness();assert.equal(c.nav.attrs['aria-expanded'],'true');
  c.api.toggleNavigation();assert.equal(c.sidebar.inert,true);assert.equal(c.nav.attrs['aria-label'],'展开左侧菜单');
  c.api.setMobileNavigation(false);c.api.syncNavigation();assert.equal(c.sidebar.inert,true);
  assert.equal(c.saved.get('spike-sidebar-collapsed-v1'),'true');
  assert.equal(harness({stored:'true'}).sidebar.inert,true);
  c.api.toggleNavigation();assert.equal(c.sidebar.inert,false);
});
test('mobile open/close and breakpoint changes preserve the desktop preference',()=>{
  const c=harness({stored:'true'});c.media.matches=true;c.media.change();
  c.api.toggleNavigation();assert.equal(c.sidebar.inert,false);assert.equal(c.nav.attrs['aria-label'],'关闭导航菜单');
  c.api.setMobileNavigation(false);assert.equal(c.sidebar.inert,true);
  c.media.matches=false;c.media.change();assert.equal(c.sidebar.inert,true);
  assert.equal(c.saved.get('spike-sidebar-collapsed-v1'),'true');
});
test('storage restrictions and preference clearing never prevent navigation',()=>{
  const c=harness({restricted:true});c.api.toggleNavigation();assert.equal(c.sidebar.inert,true);
  c.windowEvents.storage({key:null,newValue:null});assert.equal(c.sidebar.inert,false);
});
test('page fullscreen preserves browser chrome and restores focus and surroundings',async()=>{
  const c=harness({native:'allowed'});let nativeCalls=0;c.root.requestFullscreen=()=>{nativeCalls++;};
  c.enter();await flush();
  assert.equal(nativeCalls,0);assert.equal(c.document.fullscreenElement,null);
  assert.equal(c.root.classList.contains('is-workspace-fullscreen'),true);
  assert.equal(c.sidebar.inert,true);assert.equal(c.topbar.inert,true);assert.equal(c.other.inert,true);
  c.enter();assert.equal(c.root.classList.contains('is-workspace-fullscreen'),false);
  assert.equal(c.topbar.inert,false);assert.equal(c.sidebar.inert,false);assert.equal(c.button.focused,true);
});
test('global alarm remains interactive above a fullscreen workspace',()=>{
  const c=harness();c.other.hasAttribute=name=>name==='data-global-alert';c.enter();
  assert.equal(c.other.inert,false);assert.equal(c.topbar.inert,true);c.enter();assert.equal(c.other.inert,false);
});
test('denied native fullscreen retains an escapable viewport layout and prior sidebar preference',async()=>{
  const c=harness({native:'denied',stored:'true'});c.enter();await flush();
  assert.equal(c.root.classList.contains('is-workspace-fullscreen'),true);assert.equal(c.document.fullscreenElement,null);
  c.events.keydown({key:'Escape'});assert.equal(c.root.classList.contains('is-workspace-fullscreen'),false);
  assert.equal(c.sidebar.inert,true);assert.equal(c.topbar.inert,false);
});
test('leaving page fullscreen never requests a browser transition',async()=>{
  const c=harness();c.root.requestFullscreen=()=>{throw new Error('must not be called');};
  c.enter();c.api.leaveFullscreen();await flush();
  assert.equal(c.document.fullscreenElement,null);assert.equal(c.root.classList.contains('is-workspace-fullscreen'),false);
  assert.equal(c.other.inert,false);
});

test('mobile drawer isolates the background, restores prior inert state and returns focus',()=>{
  const c=harness({mobile:true});c.skip.inert=true;
  c.api.toggleNavigation();
  assert.equal(c.workspace.inert,true);assert.equal(c.sidebar.attrs['aria-modal'],'true');assert.equal(c.close.focused,true);
  c.api.setMobileNavigation(false);
  assert.equal(c.workspace.inert,false);assert.equal(c.skip.inert,true);assert.equal(c.sidebar.attrs.role,undefined);assert.equal(c.nav.focused,true);
});
test('drawer traps both Tab directions and route dismissal does not focus the old toggle',()=>{
  const c=harness({mobile:true}),first=node('first'),last=node('last');
  c.sidebar.querySelectorAll=()=>[first,last];c.api.toggleNavigation();let prevented=0;
  c.document.activeElement=last;c.events.keydown({key:'Tab',preventDefault(){prevented++;}});assert.equal(first.focused,true);
  c.document.activeElement=first;c.events.keydown({key:'Tab',shiftKey:true,preventDefault(){prevented++;}});assert.equal(last.focused,true);
  assert.equal(prevented,2);
  c.api.setMobileNavigation(false,{restoreFocus:false});assert.equal(c.nav.focused,undefined);assert.equal(c.workspace.inert,false);
});
test('search shortcut selects a visible current-page field and ignores text, composition and modals',()=>{
  const c=harness(),hidden=node('hidden'),search=node('search');hidden.getClientRects=()=>[];
  c.document.querySelectorAll=()=>[hidden,search];c.document.activeElement=node('main');let prevented=0;
  const event={key:'/',preventDefault(){prevented++;}};
  c.events.keydown(event);assert.equal(search.focused,true);assert.equal(hidden.focused,undefined);assert.equal(prevented,1);
  search.focused=false;
  for(const origin of [{tagName:'TEXTAREA'},{isContentEditable:true},{tagName:'SELECT'}]){
    c.document.activeElement=origin;c.events.keydown(event);
  }
  c.document.activeElement=node('main');c.events.keydown({...event,isComposing:true});
  c.document.querySelector=()=>({open:true});c.events.keydown(event);
  assert.equal(search.focused,false);assert.equal(prevented,1);
  c.document.querySelector=()=>null;c.document.querySelectorAll=()=>[];c.events.keydown(event);assert.equal(prevented,1);
});

test('sidebar edge control collapses left and returns focus to the reopen control',()=>{
 const c=harness();c.events.click({target:{closest:selector=>selector==='[data-sidebar-fold]'?{}:null}});
 assert.equal(c.sidebar.inert,true);assert.equal(c.nav.attrs['aria-label'],'展开左侧菜单');assert.equal(c.nav.focused,true);
});

test('drawer search inputs participate in focus order rather than wrapping Shift+Tab early',()=>{
 const c=harness({mobile:true}),first=node('first'),search=node('search'),last=node('last');
 search.tagName='INPUT';
 c.sidebar.querySelectorAll=selector=>selector.includes('input:not([disabled])')?[first,search,last]:[first,last];
 c.api.toggleNavigation();let prevented=0;
 c.document.activeElement=search;c.events.keydown({key:'Tab',shiftKey:true,preventDefault(){prevented++;}});
 assert.equal(prevented,0);assert.notEqual(last.focused,true);
 c.document.activeElement=first;c.events.keydown({key:'Tab',shiftKey:true,preventDefault(){prevented++;}});
 assert.equal(prevented,1);assert.equal(last.focused,true);
});

test('visible mobile context tabs remain interactive while closed drawer controls stay inert',()=>{
 const c=harness({mobile:true,design:'stitch-obsidian',contextVisible:true});
 assert.equal(c.contextButton.closest('[inert]'),null);
 for(const control of [c.close,c.rail,c.footer])assert.equal(control.inert,true);
 assert.equal(c.workspace.inert,false);assert.equal(c.nav.attrs['aria-expanded'],'false');
 c.api.toggleNavigation();
 assert.equal(c.contextButton.closest('[inert]'),null);assert.equal(c.rail.inert,false);
 assert.equal(c.workspace.inert,true);assert.equal(c.sidebar.attrs['aria-modal'],'true');
 c.api.setMobileNavigation(false);
 assert.equal(c.contextButton.closest('[inert]'),null);assert.equal(c.workspace.inert,false);
 assert.equal(c.rail.inert,true);assert.equal(c.nav.focused,true);
});

test('route and breakpoint changes update context interaction without exposing hidden navigation',()=>{
 const c=harness({mobile:true,design:'stitch-obsidian'});
 assert.equal(c.sidebar.inert,true);
 c.footer.inert=true;c.context.visible=true;c.api.syncNavigation();
 assert.equal(c.contextButton.closest('[inert]'),null);
 c.context.visible=false;c.api.syncNavigation();
 assert.equal(c.sidebar.inert,true);assert.equal(c.rail.inert,false);assert.equal(c.footer.inert,true);
 c.context.visible=true;c.api.syncNavigation();
 assert.equal(c.contextButton.closest('[inert]'),null);
 c.media.matches=false;c.media.change();
 assert.equal(c.sidebar.inert,false);assert.equal(c.rail.inert,false);assert.equal(c.footer.inert,true);
 c.media.matches=true;c.media.change();
 assert.equal(c.contextButton.closest('[inert]'),null);assert.equal(c.rail.inert,true);
});

test('fullscreen blocks visible mobile context tabs and restores them on exit',()=>{
 const c=harness({mobile:true,design:'stitch-obsidian',contextVisible:true});
 c.enter();assert.equal(c.contextButton.closest('[inert]'),c.sidebar);
 c.api.leaveFullscreen();
 assert.equal(c.contextButton.closest('[inert]'),null);assert.equal(c.rail.inert,true);
 assert.equal(c.workspace.inert,false);assert.equal(c.topbar.inert,false);
});
