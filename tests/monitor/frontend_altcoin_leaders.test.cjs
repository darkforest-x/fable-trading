'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
function harness(){const window={};vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../../yoyo/monitor/static/altcoin-leaders.js'),'utf8'),{window});return window.SpikeAutoLeaders;}
test('unknown values are not zeros and a stale snapshot cannot claim a live rank',()=>{
 const html=harness().html({items:[{symbol:'SOL-USDT-SWAP',quality:'ok',leadership:'candidate',action:'wait_recovery',tier:1}],coverage:{total:5,comparable:1,candidates:1},episode:{phase:'pullback'},ranking_active:false});
 assert.match(html,/当前判断暂停/);assert.match(html,/当时第 1 层/);assert.match(html,/数据覆盖不全/);assert.match(html,/资料不足/);assert.ok(!html.includes('0.00%'));
});
test('untrusted symbols and errors are escaped, and zero evidence stays visible',()=>{
 const html=harness().html({items:[{symbol:'<img>-USDT-SWAP',quality:'ok',leadership:'candidate',action:'wait_recovery',tier:1,wave_return:0,wave_rs:0,giveback:0,drawdown:0}],episode:{phase:'pullback'},ranking_active:true});
 assert.ok(html.includes('&lt;img&gt;'));assert.ok(!html.includes('<img>'));assert.match(html,/0.00%/);
});
test('pre-wave discovery and immutable historical review are explicitly labelled',()=>{
 const html=harness().html({items:[],episode:null,historical_snapshot:true,ranking_active:false});
 assert.match(html,/当前为发现池/);assert.match(html,/历史观察快照/);assert.match(html,/尚未验证收益/);assert.match(html,/盘口深度和订单流尚未接入/);
});
