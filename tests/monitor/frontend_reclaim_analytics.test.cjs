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
