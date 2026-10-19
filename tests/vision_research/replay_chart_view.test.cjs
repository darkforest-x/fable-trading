const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
const base=path.join(__dirname,'../../yoyo/vision_research/static');
function harness(){
 let range=null,fitCount=0,data=[];const scale={getVisibleLogicalRange:()=>range,setVisibleLogicalRange:r=>range=r,fitContent:()=>{fitCount++;range={from:0,to:data.length+4};}};
 const series={applyOptions(){},setData:r=>data=r,update:r=>{if(data.at(-1)?.time===r.time)data[data.length-1]=r;else data.push(r);},setMarkers(){}};
 const chart={timeScale:()=>scale,addCandlestickSeries:()=>series,addLineSeries:()=>({setData(){},update(){},applyOptions(){}}),applyOptions(){},remove(){}};
 const host={clientWidth:800,clientHeight:400,closest:()=>null};
 const context=vm.createContext({window:{LightweightCharts:{createChart:()=>chart}},document:{getElementById:()=>host,documentElement:{},body:{}},getComputedStyle:()=>({getPropertyValue:()=> '#123456'}),URLSearchParams});
 vm.runInContext(fs.readFileSync(path.join(base,'chart-tools.js'),'utf8'),context);
 context.window.SpikeChartTools.resize=()=>({disconnect(){}});context.window.SpikeChartTools.inspector=()=>({update(){},destroy(){}});
 vm.runInContext(fs.readFileSync(path.join(base,'replay.js'),'utf8').replace(/^export function /gm,'function ')+'\nglobalThis.chartTest={updateReplayChart,teardownReplayChart};',context);
 return {api:context.chartTest,range:()=>({...range}),setRange:r=>range=r,fitCount:()=>fitCount,data:()=>data};
}
const rows=Array.from({length:120},(_,i)=>({t:1000000+i*60000,o:10,h:12,l:9,c:11}));
const session=(candles,hash,id='one')=>({id,chart:{candles,chart_sha256:hash}});
test('replay advances disclosed prefixes without resetting a panned view or leaking future rows',()=>{
 const h=harness();h.api.updateReplayChart(session(rows.slice(0,80),'a'));h.setRange({from:10,to:40});
 h.api.updateReplayChart(session(rows.slice(0,81),'b'));assert.deepEqual(h.range(),{from:10,to:40});assert.equal(h.fitCount(),1);assert.equal(h.data().length,81);assert.equal(h.data().at(-1).time,rows[80].t/1000);
 h.api.updateReplayChart(session(rows.slice(0,70),'c'));assert.deepEqual(h.range(),{from:10,to:40});assert.equal(h.data().length,70);
});
test('tail-follow keeps zoom during replay and a different session gets its own initial fit',()=>{
 const h=harness();h.api.updateReplayChart(session(rows.slice(0,80),'a'));h.setRange({from:50,to:84});
 h.api.updateReplayChart(session(rows.slice(0,81),'b'));assert.deepEqual(h.range(),{from:51,to:85});assert.equal(h.fitCount(),1);
 h.api.updateReplayChart(session(rows.slice(0,81),'b','two'));assert.equal(h.fitCount(),2);
 h.api.teardownReplayChart();
});
