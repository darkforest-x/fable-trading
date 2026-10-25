'use strict';
const test=require('node:test'),assert=require('node:assert/strict');
const {averages,lines}=require('../../yoyo/monitor/static/chart-style.js');
const rows=Array.from({length:150},(_,i)=>({time:i*300000,close:i+1}));
test('six causal close averages warm up independently without changing input',()=>{
 const before=JSON.stringify(rows),out=averages(rows,300000);
 for(const line of lines){
  assert.equal(out[line.period-2][line.key],null);
  const i=line.period-1;
  if(line.key.startsWith('sma'))assert.equal(out[i][line.key],(line.period+1)/2);
  else {let expected=1;for(let j=1;j<=i;j++)expected=2/(line.period+1)*(j+1)+(1-2/(line.period+1))*expected;assert.ok(Math.abs(out[i][line.key]-expected)<1e-10);}
 }
 assert.equal(JSON.stringify(rows),before);
 assert.deepEqual(averages(rows.slice(0,121),300000),out.slice(0,121),'future bars cannot alter historical averages');
});
test('gaps and invalid closes restart warm-up instead of bridging missing history',()=>{
 for(const broken of [rows.map((r,i)=>({...r,time:r.time+(i>=20?300000:0)})),rows.map((r,i)=>({...r,close:i===19?null:r.close}))]){
  const out=averages(broken,300000);
  assert.equal(out[20].sma20,null);assert.equal(out[38].ema20,null);
  assert.equal(out[39].sma20,30.5);assert.equal(out[138].sma120,null);assert.equal(out[139].sma120,80.5);
 }
});
