'use strict';
const test=require('node:test'),assert=require('node:assert/strict');
const ui=require('../../yoyo/monitor/static/reclaim-analytics.js');
const data={paper_only:true,track:'60',days:0,now_ms:1000,summary:{},coverage:{},curve:[{time_ms:0,equity:1000,pnl:0},{time_ms:1000,equity:1010,pnl:10}],by_side:[],by_symbol:[],by_reason:[]};
test('analytics responses belong to one exact track and period and reject malformed curves',()=>{
 assert.equal(ui.validate(data,'60',0),data);
 for(const d of [{...data,track:'5'},{...data,days:7},{...data,paper_only:false},{...data,curve:[{time_ms:0,equity:null}]}])assert.throws(()=>ui.validate(d,'60',0));
});
test('missing or zero outcomes do not fabricate win rates or infinite profits',()=>{
 assert.equal(ui.pf({profit_factor:null,profit_factor_state:'empty'}),'—');assert.equal(ui.pf({profit_factor:null,profit_factor_state:'no_losses'}),'∞');assert.equal(ui.pf({profit_factor:0,profit_factor_state:'finite'}),'0.00');
 const html=ui.metricsHTML({realized_pnl:2,unrealized_pnl:null,missing_quotes:1,settled_fees:null,closed_count:0,wins:0,losses:0,breakeven:0,win_rate:null,profit_factor:null,profit_factor_state:'empty',open_count:1});
 assert.match(html,/含分批减仓/);assert.match(html,/1 笔行情待更新/);assert.doesNotMatch(html,/NaN|undefined|Infinity/);
});
test('curve uses saved realized equity in steps with truthful zero and hover values',()=>{
 const o=ui.chartOptions(data);assert.equal(o.series[0].step,'end');assert.deepEqual(o.series[0].data,[[0,1000],[1000,1010]]);assert.equal(o.animation,false);
 assert.match(o.tooltip.formatter([{dataIndex:1}]),/1,010.00 USDT/);assert.match(o.tooltip.formatter([{dataIndex:1}]),/\+10.00/);
 assert.match(o.tooltip.formatter([{dataIndex:0}]),/0.00 USDT/);assert.equal(o.tooltip.formatter([]),'');
});
test('appearance changes recolor analytics without changing money, timing or hover values',()=>{
 const light=ui.chartOptions(data),dark=ui.chartOptions(data,true);
 assert.deepEqual(dark.series[0].data,light.series[0].data);
 assert.equal(dark.series[0].step,light.series[0].step);
 assert.equal(dark.tooltip.formatter([{dataIndex:1}]),light.tooltip.formatter([{dataIndex:1}]));
 assert.notEqual(dark.tooltip.backgroundColor,light.tooltip.backgroundColor);
 assert.notEqual(dark.xAxis.axisLabel.color,light.xAxis.axisLabel.color);
});
test('breakdowns label tranche counts separately and escape unexpected symbols',()=>{
 const html=ui.breakdownHTML({...data,by_reason:[{reason:'manual',exit_count:2,realized_pnl:-3}],by_symbol:[{symbol:'<img>-USDT-SWAP',closed_count:0,win_rate:null,realized_pnl:2}]});
 assert.match(html,/成交批次/);assert.match(html,/结清 \/ 胜率/);assert.match(html,/&lt;img&gt;/);assert.doesNotMatch(html,/<img>/);
});

test('named accounts validate their exact internal analytics identity and reject another account',()=>{
 for(const [key,id] of [['v3','1060'],['v3add','2060'],['spike','3060'],['v3lock','4060'],['allin3','5060']]){
  assert.equal(ui.validate({...data,track:id},key,0).track,id);
  for(const wrong of ['60','5',key,...['1060','2060','3060','4060','5060'].filter(v=>v!==id)])assert.throws(()=>ui.validate({...data,track:wrong},key,0));
 }
 const html=ui.breakdownHTML({...data,by_reason:[{reason:'backstop',exit_count:1,realized_pnl:-30},{reason:'reclaimed_inside_be',exit_count:1,realized_pnl:-.07}]});
 assert.match(html,/兜底止损/);assert.match(html,/收回但未越过保本价/);
});

test('v3lock analytics identifies profit-lock exits without relabeling original v3 exits',()=>{
 const d={...data,by_reason:[{reason:'breakeven',exit_count:1,realized_pnl:4.3},{reason:'reclaimed_inside_be',exit_count:1,realized_pnl:1}]};
 assert.match(ui.breakdownHTML({...d,track:'4060'}),/利润锁止损/);
 assert.match(ui.breakdownHTML({...d,track:'4060'}),/收回但未越过利润锁价/);
 assert.match(ui.breakdownHTML({...d,track:'1060'}),/保本止损/);
 assert.doesNotMatch(ui.breakdownHTML({...d,track:'1060'}),/利润锁/);
});

test('allin3 analytics labels refill cash separately and keeps the drawdown before a refill',()=>{
 const d={...data,track:'5060',summary:{realized_pnl:-902.1,refilled:902.1,refill_count:1,exit_count:1},curve:[{time_ms:0,equity:1000,pnl:0},{time_ms:500,equity:97.9,pnl:-902.1},{time_ms:501,equity:1000,pnl:-902.1}]};
 const html=ui.metricsHTML(d.summary,d.track);
 assert.match(html,/补充本金/);assert.match(html,/补充次数/);assert.match(html,/本期净入账 USDT · 不计入交易盈亏/);
 assert.doesNotMatch(ui.metricsHTML(d.summary,'1060'),/补充本金/);
 assert.match(ui.curveNote(d),/补充本金 902.10 USDT（1 次）/);assert.match(ui.curveNote(d),/资金曲线包含补入资金/);
 const o=ui.chartOptions(d);assert.deepEqual(o.series[0].data,[[0,1000],[500,97.9],[501,1000]]);
 const tip=o.tooltip.formatter([{dataIndex:2}]);assert.match(tip,/1,000.00 USDT/);assert.match(tip,/-902.10 USDT/);
 assert.doesNotMatch(ui.curveNote({...d,summary:{...d.summary,exit_count:0}}),/曲线保持不变/);
});
