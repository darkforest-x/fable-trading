const {test}=require('node:test');const assert=require('node:assert/strict');
const {base,matches,selection}=require('../../yoyo/monitor/static/symbol-picker.js');
test('symbol search understands supported pair formats and ranks exact match first',()=>{
 const rows=['ETHW-USDT-SWAP','ETH-USDT-SWAP','BETH-USDT-SWAP'].map(symbol=>({symbol}));
 for(const query of ['eth','ETH/USDT','ETH-USDT-SWAP'])assert.equal(matches(rows,query)[0].symbol,'ETH-USDT-SWAP');
 assert.equal(base('BTC/USDT:USDT'),'BTC');assert.equal(rows[0].symbol,'ETHW-USDT-SWAP');
});
test('symbol selection preserves previous multi-symbol tokens and caller format',()=>{
 assert.equal(selection('BTC-USDT-SWAP, et','ETH-USDT-SWAP',true,'swap'),'BTC-USDT-SWAP, ETH-USDT-SWAP');
 assert.equal(selection('BTCUSDT, ','ETH-USDT-SWAP',true,'pair'),'BTCUSDT, ETHUSDT');
 assert.equal(selection('et','ETH-USDT-SWAP'),'ETH');
});

test('candidate arrows enter at the correct edge and wrap without skipping an item',()=>{
 const {nextIndex}=require('../../yoyo/monitor/static/symbol-picker.js');
 assert.equal(nextIndex(-1,53,'ArrowUp'),52);assert.equal(nextIndex(-1,53,'ArrowDown'),0);
 assert.equal(nextIndex(0,53,'ArrowUp'),52);assert.equal(nextIndex(52,53,'ArrowDown'),0);
 assert.equal(nextIndex(-1,0,'ArrowUp'),-1);assert.equal(nextIndex(-1,1,'ArrowUp'),0);
});
