'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const source=fs.readFileSync(require('node:path').join(__dirname,'../../yoyo/monitor/static/module-loader.js'),'utf8');
function harness(){
 const calls=[],nodes=[],preloads=[];let fail='';
 const mount=el=>{if(el.rel==='preload'){preloads.push(el.href);return;}calls.push(el.href||el.src);nodes.push(el);queueMicrotask(()=>((el.src||el.href).includes(fail)&&fail?el.onerror:el.onload)());};
 const window={},document={getElementById:id=>id==='asset-versions'?{textContent:'{"langlang.js":"hash123"}'}:{},head:{append:mount,insertBefore:mount},createElement:()=>({remove(){this.removed=true;}})};
 vm.runInNewContext(source,{window,document});return {api:window.SpikeModules,calls,nodes,preloads,fail(name){fail=name;}};
}
test('signals defer ECharts until analysis and shared dependencies execute once',async()=>{
 const c=harness();await c.api.ensure('signals');assert.deepEqual(c.calls,['/static/signal-history.css','/static/signal-components.css','/static/okx-execution.css','/static/signal-workspace.css','/static/monday-reclaim-paper.css','/static/okx-execution.js','/static/public-chart-cache.js','/static/range-chart.js','/static/monday-candle-feed.js','/static/monday-fib-chart.js','/static/yolo-preview-sheet.js','/static/reclaim-chart.js','/static/monday-list-model.js','/static/reclaim-watchlist.js','/static/reclaim-analytics.js','/static/reclaim-close.js','/static/monday-reclaim-paper.js','/static/signal-workspace.js']);
 await Promise.all([c.api.ensure('signal-charts'),c.api.ensure('signal-charts')]);
 assert.equal(c.calls.filter(x=>x.includes('echarts')).length,1);
 await Promise.all([c.api.ensure('langlang'),c.api.ensure('rektproof')]);
 assert.equal(c.calls.filter(x=>x.includes('reading-tools.js')).length,1);
 assert.equal(c.calls.filter(x=>x.includes('trading-playbooks.js')).length,1);
 assert.ok(c.calls.includes('/static/langlang.js?v=hash123'));
 assert.ok(c.calls.indexOf('/static/rektproof-core.js')<c.calls.indexOf('/static/rektproof.js'));
 assert.ok(!c.calls.some(x=>x.includes('paper-desk')));
});
test('failed assets retry without executing successful dependencies again',async()=>{
 const c=harness();c.fail('platform.js');await assert.rejects(c.api.ensure('models'),/重试/);
 c.fail('');await c.api.ensure('platform');
 assert.equal(c.calls.filter(x=>x.includes('platform.css')).length,1);
 assert.equal(c.calls.filter(x=>x.includes('platform.js')).length,2);
});
test('trading study renderer loads before the library without eagerly loading other features',async()=>{
 const c=harness();await c.api.ensure('trading-books');
 assert.ok(c.calls.indexOf('/static/trading-books-study.js')>=0);
 assert.ok(c.calls.indexOf('/static/trading-books-study.js')<c.calls.indexOf('/static/trading-books.js'));
});

test('all dependency downloads are preloaded before the first stylesheet resolves',async()=>{
 const c=harness(),pending=c.api.ensure('langlang');
 assert.equal(c.preloads.length,5);
 assert.ok(c.preloads.includes('/static/langlang.js?v=hash123'));
 assert.equal(c.calls.filter(x=>x.includes('.js')).length,0);
 await pending;assert.equal(c.calls.filter(x=>x.includes('.js')).length,5);
});

test('Monday Fib loads shared chart before its scoped row controller',async()=>{
 const c=harness();await c.api.ensure('monday-fib');
 assert.deepEqual(c.calls,['/static/monday-fib.css','/static/public-chart-cache.js','/static/range-chart.js','/static/monday-candle-feed.js','/static/monday-fib-chart.js','/static/monday-quick.js','/static/monday-list-model.js','/static/monday-fib.js']);
});
test('patrol loads reference overlay before charts can mount',async()=>{
 const c=harness();await c.api.ensure('yolo-detect');
 assert.ok(c.calls.indexOf('/static/yolo-chart-levels.js')>=0);
 assert.ok(c.calls.indexOf('/static/yolo-chart-levels.js')<c.calls.indexOf('/static/yolo-detect.js'));
 assert.ok(c.calls.indexOf('/static/yolo-preview-sheet.js')>=0);
 assert.ok(c.calls.indexOf('/static/yolo-preview-sheet.js')<c.calls.indexOf('/static/yolo-detect.js'));
});
