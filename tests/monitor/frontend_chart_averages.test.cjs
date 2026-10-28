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
test('cached averages equal full causal recalculation after every display history change',()=>{
 const sync=require('../../yoyo/monitor/static/chart-style.js').createAverages();
 const check=(input,step=300000)=>{
  const before=JSON.stringify(input),actual=sync(input,step);
  assert.deepEqual(actual,averages(input,step));assert.equal(JSON.stringify(input),before);
  return actual;
 };
 const data=rows.map((r,i)=>({...r,volume:i,confirmed:true}));
 check(data);check(data.map(r=>({...r})));
 for(let i=0;i<10;i++){data.at(-1).close+=.13;check(data);}
 data.at(-2).volume=1234;assert.equal(check(data).at(-2).volume,1234);
 data.push({time:150*300000,close:180,volume:5,confirmed:false});check(data);
 data[5].close+=10;check(data); // Corrected old history invalidates recursive EMA state.
 check(data.slice(20));check(data); // Pruning and prepending retain their own warm-up.
 check(data.filter((_,i)=>i!==140));check(data.map((r,i)=>i===80?{...r,close:null}:r));
 check(data.filter((_,i)=>i%2===0),600000);check([]);check(data.slice(0,1));check(data);
 const first=check(data);data.at(-1).close+=1;check(data);
 assert.notEqual(first.at(-1).close,data.at(-1).close,'later updates cannot mutate previous outputs');
});
