import test from 'node:test';
import assert from 'node:assert/strict';
import {sma,ema,AVERAGES} from '../../yoyo/discipline_app/mobile/src/indicators.ts';
const bars=[1,2,3,4,5,6].map((close,i)=>({time:i,close}));
test('six averages match the workbench set: SMA / EMA 20 · 60 · 120',()=>{
 assert.deepEqual(AVERAGES.map(a=>`${a.kind}${a.period}`),['sma20','sma60','sma120','ema20','ema60','ema120']);
});
test('SMA uses a rolling window; EMA seeds with the first SMA',()=>{
 assert.deepEqual(sma(bars,3),[{time:2,value:2},{time:3,value:3},{time:4,value:4},{time:5,value:5}]);
 const e=ema(bars,3);assert.equal(e[0].value,2);assert.equal(e[1].value,3);assert.equal(e.at(-1)!.time,5);
 assert.deepEqual(ema(bars.slice(0,2),3),[]);assert.deepEqual(sma(bars.slice(0,2),3),[]);
});
