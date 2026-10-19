'use strict';
// Behavioral invariants: performance shortcuts must preserve corrected history,
// keyboard reachability and resource ownership, rather than just reduce calls.
const test=require('node:test'),assert=require('node:assert/strict');
const {BoundedCache,createSeriesSync,frameTask,virtualWindow}=require('../../yoyo/monitor/static/workspace-runtime.js');
test('bounded caches evict least recently used display data, including replacements',()=>{
 const c=new BoundedCache(2);c.set('BTC',1).set('ETH',2);assert.equal(c.get('BTC'),1);c.set('SOL',3);
 assert.equal(c.has('ETH'),false);assert.equal(c.size,2);c.set('BTC',4).set('LINK',5);assert.equal(c.has('SOL'),false);assert.equal(c.get('BTC'),4);c.clear();assert.equal(c.size,0);
});
test('incremental series matches full replacement across append, correction, gaps, pruning and missing MA warmup',()=>{
 const sync=createSeriesSync();let current=[],sets=0,updates=0;
 const series={setData(rows){current=rows.slice();sets++;},update(row){updates++;if(current.at(-1)?.time===row.time)current[current.length-1]=row;else current.push(row);}};
 const apply=rows=>{sync(series,rows);assert.deepEqual(current,rows);};
 apply([{time:1,value:2},{time:2,value:3}]);assert.equal(sets,1);
 apply([{time:1,value:2},{time:2,value:3}]);assert.equal(sets,1);assert.equal(updates,0);
 apply([{time:1,value:2},{time:2,value:4},{time:3,value:8}]);assert.equal(updates,2);assert.equal(sets,1);
 apply([{time:1,value:9},{time:2,value:4},{time:3,value:8}]);assert.equal(sets,2);
 apply([{time:2,value:4},{time:3,value:8}]);assert.equal(sets,3);
 apply([]);apply([{time:120,value:10}]);apply([{time:120,value:10},{time:122,value:12}]);
 apply([{time:120,value:10},{time:121,value:11},{time:122,value:12}]);assert.equal(sets,6);
});
test('all six independent MA series retain numeric values and historical corrections',()=>{
 const sync=createSeriesSync();const lines=Array.from({length:6},()=>({rows:[],setData(r){this.rows=r;},update(p){this.rows=[...this.rows.filter(r=>r.time!==p.time),p];}}));
 for(let round=0;round<4;round++)for(let i=0;i<6;i++){const rows=Array.from({length:round+1},(_,j)=>({time:j+120,value:(i+1)*.00000013+j/100}));sync(lines[i],rows);assert.deepEqual(lines[i].rows,rows);}
 const corrected=[...lines[4].rows];corrected[0]={...corrected[0],value:.009123456789};sync(lines[4],corrected);assert.deepEqual(lines[4].rows,corrected);
});
test('crosshair work coalesces to the newest pointer position and cancels on unmount',()=>{
 const pending=new Map(),seen=[];let id=0;
 const task=frameTask(x=>seen.push(x),{request:fn=>{pending.set(++id,fn);return id;},cancel:id=>pending.delete(id)});
 for(let i=0;i<100;i++)task(i);assert.equal(pending.size,1);pending.get(1)();pending.delete(1);assert.deepEqual(seen,[99]);
 task(200);task.cancel();assert.equal(pending.size,0);assert.deepEqual(seen,[99]);
});
test('virtual rows retain total scroll height and every one of 494 keyboard targets is renderable',()=>{
 for(let i=0;i<494;i++){const w=virtualWindow(494,i*70,480);assert.ok(w.start<=i&&w.end>i);assert.ok(w.end-w.start<=17);assert.equal(w.before+(w.end-w.start)*70+w.after,494*70);}
 assert.deepEqual(virtualWindow(0,0,480),{start:0,end:0,before:0,after:0});assert.equal(virtualWindow(494,999999,480).end,494);
});
test('market region cache preserves the chart mount when a sibling tab changes',()=>{
 const fs=require('node:fs'),vm=require('node:vm');const source=fs.readFileSync('yoyo/monitor/static/market-sync.js','utf8');let parses=0;
 const host={children:[],insertBefore(node,before){if(node.parentNode===this)this.children.splice(this.children.indexOf(node),1);const index=before?this.children.indexOf(before):-1;this.children.splice(index<0?this.children.length:index,0,node);node.parentNode=this;}};
 const document={createElement(){return {content:{children:[]},set innerHTML(markup){parses++;this.content.children=[...markup.matchAll(/<(div|nav) id="([^"]+)">(.*?)<\/\1>/g)].map(([outerHTML,tagName,id])=>({outerHTML,tagName,id,remove(){host.children.splice(host.children.indexOf(this),1);this.parentNode=null;}}));}};}};
 const context={document,root:()=>host,regionTemplates:new Map(),renderedRegions:new Map()};
 const patch=vm.runInNewContext(source.slice(source.indexOf('  function patchRegions('),source.indexOf('\n  function render()'))+';patchRegions',context);
 patch(['<div id="ms-terminal-top"></div><nav id="tabs">overview</nav>']);const chart=host.children[0];chart.children=['live chart'];
 patch(['<div id="ms-terminal-top"></div><nav id="tabs">history</nav>']);assert.equal(host.children[0],chart);assert.deepEqual(chart.children,['live chart']);assert.equal(parses,2);
 patch(['<div id="ms-terminal-top"></div><nav id="tabs">history</nav>']);assert.equal(parses,2);assert.equal(host.children[0],chart);
});
