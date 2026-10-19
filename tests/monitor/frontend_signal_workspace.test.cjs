'use strict';
const test=require('node:test'),assert=require('node:assert/strict');
const {chartOptions}=require('../../yoyo/monitor/static/signal-workspace.js');
const colors={surface:'#fff',line:'#eee',text:'#111',muted:'#666',positive:'#059669',negative:'#dc2626'};
const stats={curve:{status:'ready',points:[{exit_ms:1000,cumulative_r:2,drawdown_r:0,net_r:2,closed:1},{exit_ms:2000,cumulative_r:-1,drawdown_r:3,net_r:-3,closed:2}]},by_timeframe:[{label:'15m',net_r:-1,total:3,measured:3,win_rate:1/3},{label:'4H',net_r:null,total:0,measured:0,win_rate:null}]};
test('reference curves use full exit chronology without smoothing or account conversion',()=>{
 const {line,bars}=chartOptions(stats,'cumulative',colors);
 assert.deepEqual(line.series[0].data,[[1000,2],[2000,-1]]);
 assert.equal(line.series[0].step,'end');assert.equal(line.animation,false);
 assert.equal(bars.series[0].data[1].value,null);
 assert.match(line.tooltip.formatter([{dataIndex:1}]),/同刻退出 2 笔/);
 assert.match(bars.tooltip.formatter([{dataIndex:1}]),/胜率  —/);
});
test('drawdown is below zero and incomplete samples never become a partial curve',()=>{
 const drawdown=chartOptions(stats,'drawdown',colors).line;
 assert.deepEqual(drawdown.series[0].data,[[1000,-0],[2000,-3]]);assert.equal(drawdown.yAxis.max,0);
 const missing=chartOptions({...stats,curve:{status:'incomplete',points:[]}},'cumulative',colors).line;
 assert.deepEqual(missing.series[0].data,[]);assert.match(missing.graphic[0].style.text,/缺失/);
});

test('filter summary names active constraints, including whitespace-only search and history reset',()=>{
 const {activeFilters}=require('../../yoyo/monitor/static/signal-workspace.js');
 assert.deepEqual(activeFilters({q:' ',timeframe:'all',side:'all',period:'all',outcome:'all'}),[]);
 assert.deepEqual(activeFilters({q:' btc ',timeframe:'60',side:'short',period:'week',outcome:'active'}),['BTC','1H','本周','空头','参考运行中']);
 assert.deepEqual(activeFilters({outcome:'all',side:'long'}),['多头']);
});

function workspaceHarness(){
 const fs=require('node:fs'),vm=require('node:vm'),nodes=new Map(),frames=new Map(),observers=[],charts=[];let sequence=0,release;
 const element=id=>{if(!nodes.has(id))nodes.set(id,{value:'',dataset:{},clientWidth:600,clientHeight:260,classList:{toggle(){}},setAttribute(){},dispatchEvent(){},addEventListener(){},getBoundingClientRect(){return{width:1000}},querySelectorAll(){return[]},querySelector:selector=>element(id+selector)});return nodes.get(id);};
 const document={hidden:false,documentElement:element('html'),body:element('body'),getElementById:element,addEventListener(){}};
 class Observer{constructor(fn){this.fn=fn;this.nodes=new Set();observers.push(this);}observe(node){this.nodes.add(node);}disconnect(){this.nodes.clear();}}
 const window={SpikeRuntime:{idleRelease(fn){release=fn;return {setActive(value){window.showing=value;}}}},echarts:{init(){const chart={writes:0,resizes:0,disposed:false,setOption(){this.writes++},resize(){this.resizes++},dispose(){this.disposed=true}};charts.push(chart);return chart;}}};
 vm.runInNewContext(fs.readFileSync(require('node:path').join(__dirname,'../../yoyo/monitor/static/signal-workspace.js'),'utf8'),{window,document,ResizeObserver:Observer,MutationObserver:Observer,Event:class{},getComputedStyle:()=>({getPropertyValue:()=> '#123456'}),requestAnimationFrame:fn=>{frames.set(++sequence,fn);return sequence},cancelAnimationFrame:id=>frames.delete(id)});
 const flush=()=>{for(const [id,fn] of [...frames]){frames.delete(id);fn();}};
 return {api:window.SpikeSignalWorkspace,element,charts,frames,observers,flush,release:()=>release()};
}
test('unchanged signal statistics do not resize or redraw charts, including coalesced observers',()=>{
 const h=workspaceHarness();h.api.setActive(true);h.api.render({closed_statistics:stats},'history','overview');h.flush();
 assert.equal(h.charts.length,2);assert.deepEqual(h.charts.map(c=>[c.writes,c.resizes]),[[1,1],[1,1]]);
 h.api.render({closed_statistics:structuredClone(stats)},'history','overview');h.observers[0].fn();h.observers[1].fn();assert.equal(h.frames.size,1);h.flush();
 assert.deepEqual(h.charts.map(c=>[c.writes,c.resizes]),[[1,1],[1,1]]);
 h.element('signal-curve-chart').clientWidth=700;h.observers[0].fn();h.flush();
 assert.deepEqual(h.charts.map(c=>[c.writes,c.resizes]),[[1,2],[1,1]]);
});
test('leaving the chart pane disconnects observers, cancels pending draw and releases idle instances',()=>{
 const h=workspaceHarness();h.api.setActive(true);h.api.render({closed_statistics:stats},'history','overview');h.flush();
 h.observers[0].fn();assert.equal(h.frames.size,1);h.api.render(null,'history','account');
 assert.equal(h.frames.size,0);assert.ok(h.observers.every(o=>o.nodes.size===0));h.release();assert.ok(h.charts.every(c=>c.disposed));
 h.api.setActive(false);h.observers[0].fn();assert.equal(h.frames.size,0);
 h.api.setActive(true);h.api.render({closed_statistics:stats},'history','overview');h.flush();assert.equal(h.charts.length,4);
});

test('loading charts are distinct from a successful empty cohort',()=>{
 const loading=chartOptions(null,'cumulative',colors,true),empty=chartOptions(null,'cumulative',colors,false);
 assert.match(loading.line.graphic[0].style.text,/正在读取/);assert.match(loading.bars.graphic[0].style.text,/正在读取/);
 assert.match(empty.line.graphic[0].style.text,/暂无已结束/);assert.deepEqual(empty.bars.graphic,[]);
 const h=workspaceHarness();h.api.setActive(true);h.api.render(null,'history','overview');h.flush();
 assert.equal(h.element('signal-curve-caption').textContent,'正在读取信号表现…');
});
