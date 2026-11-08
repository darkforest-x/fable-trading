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
 const c=harness();await c.api.ensure('signals');
 assert.ok(!c.calls.some(x=>x.includes('echarts')));
 for(const [dependency,consumer] of [['symbol-list-model.js','reclaim-watchlist.js'],['monday-list-model.js','reclaim-watchlist.js'],['reclaim-analytics.js','reclaim-strategy-book.js'],['reclaim-strategy-book.js','monday-reclaim-paper.js'],['monday-reclaim-paper.js','signal-workspace.js']]){assert.ok(c.calls.includes('/static/'+dependency));assert.ok(c.calls.indexOf('/static/'+dependency)<c.calls.indexOf('/static/'+consumer));}
 await c.api.ensure('market');assert.equal(c.calls.filter(x=>x==='/static/symbol-list-model.js').length,1);
 await Promise.all([c.api.ensure('signal-charts'),c.api.ensure('signal-charts')]);
 assert.equal(c.calls.filter(x=>x.includes('echarts')).length,1);
 await Promise.all([c.api.ensure('langlang'),c.api.ensure('rektproof')]);
 assert.equal(c.calls.filter(x=>x.includes('reading-tools.js')).length,1);
 assert.equal(c.calls.filter(x=>x.includes('trading-playbooks.js')).length,1);
 assert.ok(c.calls.includes('/static/langlang.js?v=hash123'));
 assert.ok(c.calls.indexOf('/static/rektproof-core.js')<c.calls.indexOf('/static/rektproof.js'));
 assert.ok(!c.calls.some(x=>x.includes('paper-desk')));
});
test('cloud stop settings load before chart, settings, and signal consumers',async()=>{
 const c=harness();await c.api.ensure('signals');
 const helper=c.calls.indexOf('/static/cloud-stop-settings.js');
 for(const consumer of ['cloud-signal-chart.js','cloud-chart-settings.js','cloud-signals.js']){
  const index=c.calls.indexOf('/static/'+consumer);
  assert.ok(helper>=0, 'cloud stop settings helper loaded');
  assert.ok(index>helper, `${consumer} loads after the stop settings helper`);
 }
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
 for(const name of ['range-chart.js','monday-candle-feed.js','monday-fib-chart.js','monday-list-model.js']){assert.ok(c.calls.includes('/static/'+name));assert.ok(c.calls.indexOf('/static/'+name)<c.calls.indexOf('/static/monday-fib.js'));}
});
test('patrol loads reference overlay before charts can mount',async()=>{
 const c=harness();await c.api.ensure('yolo-detect');
 assert.ok(c.calls.indexOf('/static/yolo-chart-levels.js')>=0);
 assert.ok(c.calls.indexOf('/static/yolo-chart-levels.js')<c.calls.indexOf('/static/yolo-detect.js'));
 assert.ok(c.calls.indexOf('/static/yolo-preview-sheet.js')>=0);
 assert.ok(c.calls.indexOf('/static/yolo-preview-sheet.js')<c.calls.indexOf('/static/yolo-detect.js'));
});

test('alarm animation library loads once on demand outside the startup bundle',async()=>{
 const c=harness();await c.api.ensure('signals');
 assert.ok(!c.calls.some(url=>url.includes('motion-14.6.0')));
 await Promise.all([c.api.ensure('alarm-motion'),c.api.ensure('alarm-motion')]);
 assert.equal(c.calls.filter(url=>url.includes('motion-14.6.0')).length,1);
 const shell=fs.readFileSync(require('node:path').join(__dirname,'../../yoyo/monitor/static/index.html'),'utf8');
 assert.ok(!shell.includes('vendor/motion-14.6.0.js'));
});
