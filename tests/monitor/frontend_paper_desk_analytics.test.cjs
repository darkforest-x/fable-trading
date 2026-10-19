'use strict';
const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const context={window:{}};
vm.runInNewContext(fs.readFileSync('yoyo/monitor/static/paper-desk-analytics.js','utf8'),context);
const renderer=context.window.SpikePaperHistory;
const at=Date.parse('2024-02-29T12:00:00+08:00');
const data={account_id:'test',month:'2024-02',now_ms:at,month_net:'9.9',profitable_days:1,losing_days:1,
  days:[{date:'2024-02-28',net:'-.1',fees:'.1',closes:0,fills:1},{date:'2024-02-29',net:'10',fees:'.2',closes:1,fills:1}],
  balance_curve:[{at:at-600000,balance:'1000'},{at:at-120000,balance:'1009.9'},{at,balance:'1009.9'}],
  equity_curve:[{observed_ms:at-240000,equity:'1000'},{observed_ms:at-180000,equity:null},{observed_ms:at-60000,equity:'1009.9'},{observed_ms:at,equity:'1008'}]};
const view={kind:'balance',span:'all',day:'2024-02-29'};
test('calendar includes leap day and records fees separately from close counts',()=>{
  const html=renderer.render(data,view);
  assert.equal((html.match(/data-desk-day=/g)||[]).length,29);
  assert.match(html,/2024-02-29 净盈亏 \+10.00 USDT，1 笔成交/);
  assert.match(html,/1 笔平仓 · 费用 0.20 U/);
  assert.match(html,/余额包含已实现盈亏和全部手续费/);
});
test('equity gaps never become a connected invented history',()=>{
  const html=renderer.chart(data,{...view,kind:'equity'});
  const path=html.match(/<path d="([^"]+)"/)[1];
  assert.equal((path.match(/ M /g)||[]).length,2);
  assert.doesNotMatch(html,/NaN|Infinity/);
  assert.match(html,/断线留空/);
});
test('empty equity is explicit and selection values remain finite',()=>{
  assert.match(renderer.chart({...data,equity_curve:[]},{...view,kind:'equity'}),/权益正在采样/);
  assert.doesNotMatch(renderer.chart({...data,balance_curve:[{at,balance:'1000'}]},view),/NaN|Infinity/);
});
test('error text cannot inject markup',()=>{
  const html=renderer.render(null,view,'<script>alert(1)</script>');
  assert.doesNotMatch(html,/<script>/);
  assert.match(html,/&lt;script&gt;/);
});

test('balance keeps exact step changes while area never joins equity gaps',()=>{
  const balance=renderer.chartModel(data,view),equity=renderer.chartModel(data,{...view,kind:'equity'});
  assert.match(balance.path,/ H /);assert.match(balance.path,/ V /);assert.doesNotMatch(balance.path,/ L /);
  assert.equal((equity.area.match(/ M /g)||[]).length,2);assert.equal((equity.area.match(/ Z/g)||[]).length,2);
});
test('pointer selection follows time distance rather than the ordinal data index',()=>{
  const m=renderer.chartModel(data,view);
  assert.equal(renderer.pointIndex(m,.1),0);assert.equal(renderer.pointIndex(m,.75),1);
  assert.equal(renderer.pointIndex(m,-1),0);assert.equal(renderer.pointIndex(m,4),2);
});
test('range selection carries prior balance but does not invent equity',()=>{
  const series={...data,now_ms:at+8*86400000};
  const balance=renderer.chartModel(series,{...view,span:'7'});
  assert.equal(balance.valid[0].at,series.now_ms-7*86400000);assert.equal(balance.valid[0].value,1009.9);
  assert.equal(renderer.chartModel(series,{...view,span:'7',kind:'equity'}),null);
});
test('empty, zero, single-point and invalid cursor values remain finite',()=>{
  for(const cursor of [-9,900,null]){
    const html=renderer.chart({...data,balance_curve:[{at,balance:'0'}]},{...view,cursor});
    assert.doesNotMatch(html,/NaN|Infinity|type="range"/);assert.match(html,/aria-valuenow="0"/);
  }
});
test('chart inspection supports pointer, touch tap and keyboard without a scrubber',()=>{
  const handlers={},nodes={},attrs={},classes=new Set(),selection={...view,cursor:null};
  for(const selector of ['.desk-chart-cursor','[data-chart-point]','[data-chart-tooltip]','[data-chart-date]','[data-chart-amount]']){
    nodes[selector]={style:{setProperty(){}},setAttribute(k,v){this[k]=v;}};
  }
  const plot={closest:()=>plot,contains:node=>node===plot,getBoundingClientRect:()=>({left:0,width:1000}),
    querySelector:selector=>nodes[selector],setAttribute:(k,v)=>attrs[k]=v,classList:{toggle:(name,on)=>on?classes.add(name):classes.delete(name)}};
  renderer.bind({addEventListener:(name,fn)=>handlers[name]=fn},()=>({data,view:selection}));
  handlers.pointerdown({type:'pointerdown',pointerType:'touch',button:0,target:plot,clientX:8});
  assert.equal(selection.cursor,0);assert.match(attrs['aria-valuetext'],/1000\.00/);
  handlers.pointermove({type:'pointermove',pointerType:'touch',target:plot,clientX:992});assert.equal(selection.cursor,0);
  handlers.keydown({target:plot,key:'End',preventDefault(){}});assert.equal(selection.cursor,2);
  handlers.keydown({target:plot,key:'ArrowLeft',preventDefault(){}});assert.equal(selection.cursor,1);
  handlers.keydown({target:plot,key:'Home',preventDefault(){}});assert.equal(selection.cursor,0);
  handlers.pointermove({type:'pointermove',pointerType:'mouse',target:plot,clientX:992});assert.equal(selection.cursor,2);
  handlers.pointerout({pointerType:'mouse',target:plot,relatedTarget:null});assert.equal(selection.cursor,null);assert.equal(classes.has('is-inspecting'),false);
});

test('account statistics render net performance and closed-trade definitions',()=>{
  const html=renderer.statistics({statistics:{total_net:'49.315',return_percent:'4.9315',closed_positions:3,wins:1,losses:1,breakeven:1,
    win_rate:'33.3333',payoff_ratio:'3.4547',profit_factor:'3.4547',total_fees:'.685',turnover:'1800',max_balance_drawdown_percent:'8.2',
    average_win:'69.405',average_loss:'-20.09',best_trade:'69.405',worst_trade:'-20.09',average_holding_ms:3600000}});
  assert.match(html,/账户交易统计/);assert.match(html,/\+49.32/);assert.match(html,/33.33%/);
  assert.match(html,/−20.09 USDT/);assert.match(html,/1.00 小时/);assert.match(html,/8.20%/);
  assert.match(html,/加仓和分批平仓合并为一笔/);assert.match(html,/持平计入交易总数/);
  assert.doesNotMatch(html,/NaN|Infinity/);
});
test('empty and winning-only statistics never present invented win rate or infinite ratios',()=>{
  const empty={total_net:'0',return_percent:'0',closed_positions:0,wins:0,losses:0,breakeven:0,win_rate:null,
    payoff_ratio:null,profit_factor:null,total_fees:'0',turnover:'0',max_balance_drawdown_percent:'0',
    average_win:null,average_loss:null,best_trade:null,worst_trade:null,average_holding_ms:null};
  const html=renderer.statistics({statistics:empty});assert.match(html,/完成首笔交易后统计/);
  assert.match(html,/胜率<\/span><strong>—/);assert.doesNotMatch(html,/NaN|Infinity|无亏损/);
  assert.match(renderer.statistics({statistics:{...empty,closed_positions:1,wins:1,win_rate:'100'}}),/无亏损/);
  assert.match(renderer.statistics({}),/暂不可用/);
});
