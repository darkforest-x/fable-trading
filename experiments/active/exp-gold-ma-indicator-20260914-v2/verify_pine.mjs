// Auxiliary PineTS execution of the shipped Pine text, independent of Python.
// Synthetic OHLC only; native TradingView compilation is a separate check.
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import crypto from 'node:crypto';
import { spawnSync } from 'node:child_process';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';

const here = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(here, '../../..');
const require = createRequire('/tmp/spike-ma-drift-runtime-20260914/package.json');
const { PineTS } = require('pinets');
globalThis.fetch = async () => { throw new Error('No network in parity test'); };
const sourcePath = path.join(root, 'yoyo/evaluation/pine/spike_gold_shape_v2.pine');
const source = await fs.readFile(sourcePath, 'utf8');
const python = `
import json, numpy as np, pandas as pd
from yoyo.evaluation.gold_ma_candidate import replay
c=100+np.sin(np.arange(500)/13)*.5
o=np.r_[c[0],c[:-1]]
f=pd.DataFrame({'open_time':pd.date_range('2025-01-01',periods=500,freq='min',tz='UTC'),'open':o,'high':np.maximum(o,c)+.1,'low':np.minimum(o,c)-.1,'close':c})
r=replay(f,bar_minutes=1)
print(json.dumps({col:np.flatnonzero(r[col]).tolist() for col in ['a_long_marker','a_short_marker','a_long_confirmation','a_short_confirmation']}))
`;
const result = spawnSync('python3', ['-c', python], {cwd: root, encoding: 'utf8'});
assert.equal(result.status, 0, result.stderr);
const expected = JSON.parse(result.stdout);
const names = {'V2多头识别':'a_long_marker', 'V2空头识别':'a_short_marker',
  'V2多头确认':'a_long_confirmation', 'V2空头确认':'a_short_confirmation'};
function fixture(minutes=1, flat=false) {
  const closes=Array.from({length:500}, (_, i) => flat ? 100 : 100+Math.sin(i/13)*.5);
  return closes.map((close, i) => { const open=closes[Math.max(0,i-1)];return {
    openTime:Date.UTC(2025,0,1)+i*minutes*60000,
    closeTime:Date.UTC(2025,0,1)+(i+1)*minutes*60000,
    open, high:Math.max(open,close)+.1, low:Math.min(open,close)-.1, close,volume:100};});
}
function events(ctx, name) {
  assert.ok(ctx.plots[name], 'Missing '+name);
  return ctx.plots[name].data.flatMap((r,i)=>r.value===1?[i]:[]);
}
const checks=[];
for(const minutes of [1,3,15]) {
  const ctx=await new PineTS(fixture(minutes),'SYNTHETIC',String(minutes)).run(source);
  for(const [name,col] of Object.entries(names)) assert.deepEqual(events(ctx,name),expected[col],name+' '+minutes+'m');
  checks.push('Pine/Python four-event parity '+minutes+'m');
}
const flat=await new PineTS(fixture(1,true),'SYNTHETIC','1').run(source);
for(const name of Object.keys(names)) assert.deepEqual(events(flat,name),[]);
checks.push('flat negative control');
const prefix=await new PineTS(fixture().slice(0,410),'SYNTHETIC','1').run(source);
for(const [name,col] of Object.entries(names)) assert.deepEqual(events(prefix,name),expected[col].filter(i=>i<410));
checks.push('truncation after nonempty signals');
const changed=fixture().map((r,i)=>i<410?r:{...r,open:r.open*10,high:r.high*10,low:r.low*10,close:r.close*10,openTime:r.openTime+3*86400000,closeTime:r.closeTime+3*86400000});
const mutated=await new PineTS(changed,'SYNTHETIC','1').run(source);
for(const [name,col] of Object.entries(names)) assert.deepEqual(events(mutated,name).filter(i=>i<410),expected[col].filter(i=>i<410));
checks.push('future price/time mutation');
const output={created_at:new Date().toISOString(),pine_sha256:crypto.createHash('sha256').update(source).digest('hex'),
  runtime:'pinets@0.9.33',source:'synthetic only',checks,expected,native_compile:false,market_performance_verified:false};
await fs.mkdir(path.join(here,'results'),{recursive:true});
await fs.writeFile(path.join(here,'results/pine_parity.json'),JSON.stringify(output,null,2)+'\n');
process.stdout.write(JSON.stringify(output,null,2)+'\n');
