'use strict';
// Deferred reads reproduce navigation races without touching the running ledger.
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
const source=fs.readFileSync(path.join(__dirname,'../../yoyo/monitor/static/research.js'),'utf8');
function harness(fetchImpl){
  const nodes=new Map(),events={};
  function element(id=''){
    const classes=new Set(['hidden']);
    return {id,value:'',dataset:{},innerHTML:'',textContent:'',children:[],
      classList:{add:k=>classes.add(k),remove:k=>classes.delete(k),contains:k=>classes.has(k),toggle(k,v){v?classes.add(k):classes.delete(k);}},
      addEventListener(){},setAttribute(){},after(node){nodes.set(node.id,node);},remove(){nodes.delete(id);},
      contains(node){return this.children.includes(node);},querySelectorAll(){return this.children;},
      focus(){document.activeElement=this;}};
  }
  const get=id=>{if(!nodes.has(id))nodes.set(id,element(id));return nodes.get(id);};
  const document={activeElement:null,hidden:false,getElementById:get,createElement:()=>element(),addEventListener:(name,fn)=>events[name]=fn};
  const window={};let poll;
  vm.runInNewContext(source,{window,document,fetch:fetchImpl,setInterval(fn){poll=fn;},URL});
  return {api:window.SpikeResearch,get,document,events,element,poll};
}
const response=(url)=>({ok:true,json:async()=>url.endsWith('/overview')?{objective:'Research',statuses:{},recent:[],sources:[]}:{items:[]}});
test('initial table reads show loading, coalesce concurrent refreshes and recover after failure',async()=>{
  let fail;let calls=0;
  const c=harness(()=>{calls++;return new Promise((_,reject)=>{fail=reject;});});
  c.api.setView('experiments');const first=c.api.refresh(),second=c.api.refresh();
  assert.equal(first,second);assert.equal(calls,1);
  assert.match(c.get('experiment-rows').innerHTML,/正在读取/);assert.equal(c.get('research-loading').classList.contains('hidden'),false);
  fail(new Error('offline'));await first;
  assert.match(c.get('experiment-rows').innerHTML,/刷新重试/);assert.equal(c.get('research-error').textContent,'offline');
  assert.equal(c.get('research-loading').classList.contains('hidden'),true);
});
test('late research failure stays off a different page and a later read can recover',async()=>{
  let reject,offline=true;
  const c=harness(url=>offline?new Promise((_,r)=>{reject=r;}):Promise.resolve(response(url)));
  c.api.setView('experiments');const request=c.api.refresh();c.api.setView('knowledge');reject(new Error('late'));await request;
  assert.equal(c.get('research-error').classList.contains('hidden'),true);
  assert.equal(c.get('research-loading').classList.contains('hidden'),true);
  offline=false;c.api.setView('experiments');await c.api.refresh();
  assert.equal(c.get('research-error').classList.contains('hidden'),true);
  assert.match(c.get('experiment-rows').innerHTML,/没有匹配的研究问题/);
});
test('table focus is recovered by record identity after replacement, never by row position',()=>{
  const start=source.indexOf('  function rememberTableFocus('),end=source.indexOf('  function renderFactors()',start);
  const old={dataset:{compare:'record-a'},tagName:'INPUT',className:''};
  const other={...old,dataset:{compare:'record-b'},focus(){this.focused=true;}};
  const replacement={...old,focus(){this.focused=true;}};
  let children=[old];const root={contains:node=>children.includes(node),querySelectorAll:()=>children};
  const context={document:{activeElement:old},$:()=>root};
  vm.runInNewContext(source.slice(start,end)+'\nthis.restore=rememberTableFocus("rows");',context);
  children=[other,replacement];context.restore();assert.equal(replacement.focused,true);assert.equal(other.focused,undefined);
  replacement.focused=false;children=[other];context.restore();assert.equal(other.focused,undefined);
});
test('background refresh keeps existing rows without inserting a layout-shifting loading notice',async()=>{
  let deferred=false;const pending=[];
  const c=harness(url=>deferred?new Promise(resolve=>pending.push(()=>resolve(response(url)))):Promise.resolve(response(url)));
  c.api.setView('experiments');await c.api.refresh();const rows=c.get('experiment-rows').innerHTML;
  deferred=true;const request=c.api.refresh();
  assert.equal(c.get('experiment-rows').innerHTML,rows);assert.equal(c.get('research-loading').classList.contains('hidden'),true);
  pending.forEach(finish=>finish());await request;
});
test('late job-poll failures cannot place a research alert on another page',async()=>{
  let deferred=false,reject;
  const c=harness(url=>deferred?new Promise((_,fail)=>{reject=fail;}):Promise.resolve(url.endsWith('/jobs')?{ok:true,json:async()=>({items:[{id:'job',status:'queued',spec:{symbols:[]}}]})}:response(url)));
  c.api.setView('backtests');await c.api.refresh();deferred=true;
  const poll=c.poll();c.api.setView('knowledge');reject(new Error('poll offline'));await poll;
  assert.equal(c.get('research-error').classList.contains('hidden'),true);
});

test('catalog polling reads only the visible resource and reuses fresh data',async()=>{
  const calls=[];const c=harness(async url=>{calls.push(url);return response(url);});
  c.api.setView('experiments');await c.api.refresh();
  assert.deepEqual(calls,['/api/research/experiments']);
  await c.api.refresh('periodic');assert.equal(calls.length,1);
  c.api.setView('backtests');await c.api.refresh();
  assert.deepEqual(calls.slice(1).sort(),['/api/research/jobs','/api/research/recipes']);
  await c.poll();assert.equal(calls.length,3,'idle queue must not poll every four seconds');
  c.document.hidden=true;await c.poll();assert.equal(calls.length,3);
  await c.api.refresh();assert.equal(calls.length,5,'explicit refresh bypasses freshness cache');
});
