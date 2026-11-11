'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const {
  CHART_PERIODS,CHART_COLORS,chartURL,symbolChartURL,validateChartData,
  candleIsFirstWeek,monthlyLevels,
} = require('../../yoyo/monitor/static/month-first-week.js');

const anchor = {start_ms:1_000,ready_ms:2_000,expires_ms:40_000,high:110,low:90,upper:122.36,lower:77.64};
const candle = (time, extra={}) => ({time,open:100,high:105,low:95,close:101,volume:12,...extra});
const event = {event_id:'touch/ETH?side=上&1',symbol:'ETH-USDT-SWAP',source:'history',side:'upper'};
const eventData = (overrides={}) => ({
  event_id:event.event_id,symbol:event.symbol,month:'2026-11',side:event.side,source:event.source,bar:'1H',level:122.36,
  anchor,candles:[candle(3_000),candle(6_600_000)],marker_ms:3_000,forming_time_ms:null,
  observation_start_ms:0,observation_end_ms:7_200_000,...overrides,
});

test('chart URLs keep event and symbol lookups mutually explicit and encode their key',()=>{
  assert.equal(chartURL(event.event_id,'4H'),`/api/month-first-week-1618/chart?event_id=${encodeURIComponent(event.event_id)}&period=4H`);
  assert.equal(chartURL('id','1H'),'/api/month-first-week-1618/chart?event_id=id&period=1H');
  assert.equal(symbolChartURL('ETH-USDT-SWAP','1H'),'/api/month-first-week-1618/chart?symbol=ETH-USDT-SWAP&period=1H');
  assert.deepEqual(CHART_PERIODS,['1m','5m','15m','1H','4H','12H','1D']);
});

test('event chart requires an exact marker candle and preserves historical precision when none is verifiable',()=>{
  assert.equal(validateChartData(eventData(),{event},'1H').marker_ms,3_000);
  const missing=eventData({marker_ms:null,candles:[candle(6_600_000)]});
  assert.equal(validateChartData(missing,{event},'1H').marker_ms,null);
  assert.throws(()=>validateChartData(eventData({marker_ms:4_000}),{event},'1H'),/标记/);
  assert.throws(()=>validateChartData(eventData({symbol:'BTC-USDT-SWAP'}),{event},'1H'),/不匹配/);
});

test('symbol chart allows unknown or flat first-week levels and never invents a marker',()=>{
  const data={...eventData(),event_id:null,symbol:'ETH-USDT-SWAP',source:null,side:null,level:null,marker_ms:null,
    anchor:{...anchor,high:100,low:100,upper:null,lower:-2},observation_start_ms:null,observation_end_ms:null,
    candles:[candle(3_000,{first_week:true})]};
  assert.equal(validateChartData(data,{symbol:data.symbol},'1H').marker_ms,null);
  assert.deepEqual(monthlyLevels(data),[]);
  assert.throws(()=>validateChartData({...data,marker_ms:3_000},{symbol:data.symbol},'1H'),/标记/);
});

test('all positive known levels stop at the month boundary; nonpositive levels do not affect price scale',()=>{
  const levels=monthlyLevels(eventData({anchor:{...anchor,lower:-1}}));
  assert.deepEqual(levels.map(level=>level.label),['首周高','上 1.618','首周低']);
  assert.ok(levels.every(level=>level.start_ms===anchor.ready_ms&&level.end_ms===anchor.expires_ms));
  assert.equal(CHART_COLORS.firstWeek,'#30D158');
  assert.equal(CHART_COLORS.rest,'#8B9099');
});

test('explicit first_week flags override the current anchor range for coarse and historical bars',()=>{
  assert.equal(candleIsFirstWeek({time:3_000,first_week:true},anchor),true);
  assert.equal(candleIsFirstWeek({time:1_500,first_week:false},anchor),false);
  assert.equal(candleIsFirstWeek({time:1_500},anchor),true);
  assert.equal(candleIsFirstWeek({time:2_000},anchor),false);
});
