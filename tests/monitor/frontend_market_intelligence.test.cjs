'use strict';
const test=require('node:test'),assert=require('node:assert/strict');
const {metrics,panels,field}=require('../../yoyo/monitor/static/market-intelligence.js');
test('workstation source values require fresh fields and preserve valid zero funding',()=>{
 const data={symbol:'BTC-USDT-SWAP',fields:{funding:{stale:false,data:[{fundingRate:'0',fundingTime:1792252800000}]},interest:{stale:true,data:[{oiUsd:'100000'}]}}};
 assert.match(metrics(data,null),/0\.0000%/);assert.equal(field(data,'interest'),null);
 data.fields.funding.error='network';assert.equal(field(data,'funding'),null);
 assert.doesNotMatch(metrics(data,null),/0\.0000%/);
});
test('flow and liquidation views state actual sample coverage, never invent empty totals',()=>{
 const html=panels({fields:{trades:{error:'offline'},liquidations:{pending:true}}},80000);
 assert.match(html,/公开数据暂不可用/);assert.match(html,/正在同步/);assert.doesNotMatch(html,/\$0/);
 const ready=panels({flow:{available:true,count:2,delta_usdt:-200,buy_usdt:100,sell_usdt:300,buy_ratio:.25,start_ms:1792252800000,end_ms:1792252801000,curve:[{value:100},{value:-200}],items:[]},liquidations:{count:0,long_usdt:0,short_usdt:0,items:[]}},80000);
 assert.match(ready,/公开样本中暂无近期强平/);assert.match(ready,/不是完整时段 CVD/);assert.match(ready,/width:25%/);assert.match(ready,/\$-200/);
});
