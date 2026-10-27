'use strict';
const test=require('node:test'),assert=require('node:assert/strict');
const market=require('../../yoyo/monitor/static/yolo-market.js');
const {filteredSymbols}=require('../../yoyo/monitor/static/yolo-detect.js');

test('public quote metadata joins by contract without mutating detection records',()=>{
  const rows=[{symbol:'BTC-USDT-SWAP',count:2},{symbol:'ETH-USDT-SWAP',count:1}];
  const joined=market.join(rows,new Map([['BTC-USDT-SWAP',{volume_usdt_24h:'1200000000'}]]));
  assert.equal(joined[0].volume_usdt_24h,'1200000000');assert.equal(joined[1].volume_usdt_24h,null);
  assert.equal(rows[0].volume_usdt_24h,undefined);assert.equal(joined[0].count,2);
});
test('volume order is numeric, missing values are last, and favorites remain an independent filter',()=>{
  const rows=[['A',null],['B','9'],['C','100'],['D',0],['E','bad']].map(([symbol,volume_usdt_24h])=>({symbol:symbol+'-USDT-SWAP',volume_usdt_24h,count:1}));
  assert.deepEqual(filteredSymbols(rows,'','all',[],'volume').map(r=>r.symbol[0]),['C','B','D','A','E']);
  assert.deepEqual(filteredSymbols(rows,'','favorites',['B-USDT-SWAP','C-USDT-SWAP'],'volume').map(r=>r.symbol[0]),['C','B']);
  assert.deepEqual(rows.map(r=>r.symbol[0]),['A','B','C','D','E']);
  for(const value of [null,undefined,'',NaN,Infinity,-1,'bad'])assert.equal(market.amount(value),'—');
  assert.equal(market.amount(0),'0.00');assert.equal(market.amount('1200000000'),'1.20B');
});
test('Monday deep links retain exact contract and period, reject injection and normalize the daily period',()=>{
  assert.equal(market.mondayLink('USELESS-USDT-SWAP','1H'),'#monday-fib?symbol=USELESS-USDT-SWAP&timeframe=1H');
  assert.match(market.mondayLink('BTC-USDT-SWAP','1Dutc'),/timeframe=1D$/);
  assert.equal(market.mondayLink('BTC-USDT-SWAP&symbol=ETH-USDT-SWAP','1H'),'#monday-fib');
  assert.match(market.logo('BTC-USDT-SWAP'),/^\/coin-assets\/btc\.png\?v=[a-f0-9]{20}$/);
  assert.equal(market.logo('../secret'),'');
});
