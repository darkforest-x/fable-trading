'use strict';
const test=require('node:test'),assert=require('node:assert/strict');
const {levels,validCandles,CandleRead}=require('../../yoyo/monitor/static/manual-position-chart.js');
const data=symbol=>({symbol,timeframe:'5m',cutoff_ms:900000,candles:[{time:300,open:100,high:102,low:98,close:101},{time:600,open:101,high:103,low:100,close:102}]});
test('long and short use actual entry and price directions, not fixed colors by height',()=>{
  assert.deepEqual(levels({side:'long',price:'100',initial_stop:'98',take_profit:'106',rr:3}),{side:'long',entry:100,stop:98,target:106,rr:3});
  assert.deepEqual(levels({side:'short',price:'100',entry_price:'99',initial_stop:'102',take_profit:'90',rr:3}),{side:'short',entry:99,stop:102,target:90,rr:3});
  for(const d of [{side:'long',price:100,initial_stop:102,take_profit:106},{side:'short',price:100,initial_stop:102,take_profit:106},{side:'long',price:'NaN',initial_stop:98,take_profit:106}])assert.equal(levels(d),null);
});
test('chart validates symbol, ascending real OHLC, gaps and cutoff',()=>{
  assert.equal(validCandles(data('A'),'A').length,2);
  assert.throws(()=>validCandles(data('B'),'A'));
  assert.throws(()=>validCandles({...data('A'),cutoff_ms:1200000},'A'));
  assert.throws(()=>validCandles({...data('A'),candles:data('A').candles.reverse()},'A'));
  const bad=data('A');bad.candles[0].high=99;assert.throws(()=>validCandles(bad,'A'));
});
test('switching symbol and closing abort obsolete requests and ignore late responses',async()=>{
  const calls=[],resolvers=[];const read=new CandleRead((symbol,signal)=>{calls.push({symbol,signal});return new Promise(resolve=>resolvers.push(resolve));});
  const a=read.select('A');assert.equal(read.select('A'),a);const b=read.select('B');
  assert.ok(calls[0].signal.aborted);resolvers[0](data('A'));assert.equal(await a,null);
  resolvers[1](data('B'));assert.equal((await b).symbol,'B');
  read.clear();assert.ok(calls[1].signal.aborted);assert.equal(read.promise,null);
});
const {stopBar}=require('../../yoyo/monitor/static/manual-position-chart.js');
test('stop marker anchors to candle opening time and verifies both extremes',()=>{
  const d={side:'short',stop_source:'latest_closed_5m',stop_candle:{open_ms:600000,close_ms:900000,high:'103',low:'100'}};
  const source=stopBar(d,data('A').candles);
  assert.equal(source.time,600);assert.equal(source.position,'aboveBar');assert.equal(source.text,'止损取高点');
  assert.equal(stopBar({...d,side:'long'},data('A').candles).position,'belowBar');
  assert.ok(stopBar({...d,stop_candle:{...d.stop_candle,high:'104'}},data('A').candles).error);
  assert.ok(stopBar({...d,stop_candle:{...d.stop_candle,open_ms:900000,close_ms:1200000}},data('A').candles).error);
  assert.equal(stopBar({...d,stop_source:'manual'},data('A').candles),null);
});
const {maBoundary}=require('../../yoyo/monitor/static/manual-position-chart.js');
test('six-MA marker checks all six values and matches the frozen closed bar',()=>{
  const ma={sma20:100,sma60:99,sma120:98,ema20:101,ema60:102,ema120:103},rows=[{time:600,...ma}];
  const d={stop_source:'ma_envelope_5m',stop_candle:{open_ms:600000,ma,tick:'.01',boundary_key:'sma120',boundary_price:'98'}};
  assert.equal(maBoundary(d,rows).key,'sma120');
  assert.ok(maBoundary(d,[{time:600,...ma,ema120:104}]).error);
  assert.ok(maBoundary(d,[{time:900,...ma}]).error);
});
