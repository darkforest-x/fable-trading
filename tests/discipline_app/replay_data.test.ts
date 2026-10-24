import {test} from 'node:test';
import assert from 'node:assert/strict';
import {beijingStartToUtcSeconds,formatReplayTime,formatReplayPrice,isCancellableReplayOrder,isPendingReplayOrder,normalizeReplaySymbol,replayBarsForChart,replayOrderStatusLabel,validReplayCash} from '../../yoyo/discipline_app/mobile/src/replayData.ts';

test('Beijing wall-clock inputs convert to UTC independently of device timezone',()=>{
 assert.equal(beijingStartToUtcSeconds('2026-10-24','20:30'),1792845000);
 assert.equal(formatReplayTime(1792845000,{year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit'}),'2026/10/24 20:30');
});

test('invalid replay times and symbols fail before creating a session',()=>{
 for(const [date,time] of [['2026-02-30','12:00'],['2026-10-24','24:00'],['24/10/2026','12:00']])assert.throws(()=>beijingStartToUtcSeconds(date,time));
 assert.equal(normalizeReplaySymbol(' btc '),'BTC-USDT-SWAP');
 assert.equal(normalizeReplaySymbol('ETH-USDT'),'ETH-USDT-SWAP');
 assert.throws(()=>normalizeReplaySymbol('BTC/USDT'));
 assert.equal(validReplayCash('2500'),true);assert.equal(validReplayCash('0'),false);assert.equal(validReplayCash('nope'),false);
});

test('chart projection accepts only finite server bars through the snapshot clock',()=>{
 const bars=replayBarsForChart([
  {time:300,open:3,high:4,low:2,close:3,volume:8},
  {time:200,open:'2',high:'3',low:'1',close:'2',volume:'5'},
  {time:500,open:5,high:6,low:4,close:5,volume:10},
  {time:250,open:'invalid',high:3,low:1,close:2,volume:1},
 ],400);
 assert.deepEqual(bars,[
  {time:200,open:2,high:3,low:1,close:2,volume:5},
  {time:300,open:3,high:4,low:2,close:3,volume:8},
 ]);
});

test('only pending native orders can be canceled and historical states stay readable',()=>{
 const order=(status:string)=>({id:'x',side:'long' as const,price:'100',notional:'10',status});
 assert.equal(isPendingReplayOrder(order('open')),true);
 assert.equal(isCancellableReplayOrder(order('open')),true);
 assert.equal(isPendingReplayOrder(order('submitted')),true);
 assert.equal(isCancellableReplayOrder(order('submitted')),false);
 assert.equal(isPendingReplayOrder(order('filled')),false);
 assert.equal(isCancellableReplayOrder(order('filled')),false);
 assert.equal(replayOrderStatusLabel('canceled'),'已撤销');
 assert.equal(replayOrderStatusLabel('expired'),'已过期');
 assert.equal(formatReplayPrice('0.00012345'),'0.00012345');
});
