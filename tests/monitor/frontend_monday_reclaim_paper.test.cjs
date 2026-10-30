'use strict';
const test=require('node:test'),assert=require('node:assert/strict');
const ui=require('../../yoyo/monitor/static/monday-reclaim-paper.js');
const position=(overrides={})=>({id:1,symbol:'SOL-USDT-SWAP',side:1,taken:1,notional:100,entry:120,target:125,stage:'pending_confirm',fill_ms:1000,decision_ms:100000,mark:122,unrealized_bp:166.666,score:.43,...overrides});
const track=(overrides={})=>({equity:1002,start_equity:1000,open:[],closed:[],stats:{taken_closed:42,wins:21,mean_net_bp:20,shadow_closed:97,shadow_mean_net_bp:-5},...overrides});
const snapshot=()=>({paper_only:true,strategy:'monday_1618_limit_reclaim_v2',now_ms:10000,tracks:{60:track(),5:track({equity:998})},signals:[]});
test('account metrics use all-time funded stats and exclude shadow and floating returns',()=>{
  const t=track({open:[position({unrealized_bp:8000})],closed:[position()]});
  assert.deepEqual(ui.summary(t),{equity:1002,start:1000,pnl:2,count:42,winRate:50,wins:21,mean:20});
  assert.equal(ui.summary(track({stats:{taken_closed:0,wins:0,mean_net_bp:null}})).winRate,null);
  assert.equal(ui.summary({equity:null,start_equity:1000}).pnl,null);
  assert.equal(ui.summary(snapshot().tracks[5]).pnl,-2);
});
test('capacity shadows cannot masquerade as funded positions or executed signals',()=>{
  const p=position({taken:0,notional:0,note:'no_capacity',pnl:0});
  assert.equal(ui.funded(p),false);assert.equal(ui.funded(position({notional:0})),false);
  assert.equal(ui.signalStatus({status:'scored',taken:1}),'通过评分');
  assert.match(ui.allocation(p),/额度不足/);
  const html=ui.rowHTML({...p,stage:'closed',net_bp:100},ui.route({tab:'closed'}),10000);
  assert.match(html,/不计入资金/);assert.doesNotMatch(html,/0\.00 <small>USDT/);
});
test('near Monday edge, confirmation time, entry stop and fees retain backend semantics',()=>{
  assert.equal(ui.targetLabel(position()),'周一低点');assert.equal(ui.targetLabel(position({side:-1})),'周一高点');
  assert.equal(ui.stage(position(),10000),'等待收盘确认');assert.equal(ui.stage(position(),100001),'等待确认结算');
  assert.equal(ui.stage(position({stage:'breakeven'}),10000),'入场价止损已启用');
  const html=ui.rowHTML(position({stage:'closed',reason:'breakeven',pnl:-.07,gross_bp:0,fee_bp:7,net_bp:-7,net_slip_bp:-17}),ui.route({tab:'closed'}),10000);
  assert.match(html,/入场价止损/);assert.match(html,/-0\.07%/);assert.match(html,/-0\.17%/);assert.match(html,/>0\.07%/);
});
test('missing scores and quotes stay missing while real zeroes survive formatting',()=>{
  for(const value of [null,undefined,NaN,Infinity,'0']){assert.equal(ui.bp(value),'—');assert.equal(ui.price(value),'—');}
  assert.equal(ui.bp(0),'0.00%');assert.equal(ui.price(.00000042),'0.00000042');
  const html=ui.rowHTML({id:3,symbol:'ALT-USDT-SWAP',status:'feature_error',score:null,error:'unavailable'},ui.route({tab:'events'}),10000);
  assert.match(html,/特征读取失败/);assert.match(html,/入场分数<\/small><strong>—/);assert.doesNotMatch(html,/0\.0000/);
});
test('collapsed touch rows expose the raw score without opening details',()=>{
  const collapsed=score=>ui.rowHTML({id:9,symbol:'SOL-USDT-SWAP',side:1,status:'scored',score},ui.route({tab:'events'}),10000).split('</summary>')[0];
  assert.match(collapsed(.8234),/评分<\/small><strong>0\.8234<\/strong>/);
  assert.match(collapsed(0),/评分<\/small><strong>0\.0000<\/strong>/);
  for(const score of [null,undefined,NaN,Infinity,'0.9']) {
    assert.match(collapsed(score),/评分<\/small><strong>—<\/strong>/);
    assert.doesNotMatch(collapsed(score),/rp-score is-high/);
  }
});
test('score accent uses a strict raw >0.5 boundary, independent of the entry gate',()=>{
  const collapsed=(score,taken)=>ui.rowHTML({id:9,symbol:'SOL-USDT-SWAP',side:1,status:'scored',score,taken},ui.route({tab:'events'}),10000).split('</summary>')[0];
  for(const score of [.5,.5-Number.EPSILON,.2356,0])assert.doesNotMatch(collapsed(score,1),/rp-score is-high/);
  for(const score of [.5+Number.EPSILON,.50001,.9356])assert.match(collapsed(score,0),/rp-score is-high/);
  // A rounded boundary value must not visually contradict its accent.
  assert.match(collapsed(.50001,0),/<strong>0\.50001<\/strong>/);
  assert.match(collapsed(.5,1),/<strong>0\.5000<\/strong>/);
});
test('lists filter independently without mutating or mixing track totals and shared events',()=>{
  const d=snapshot();d.tracks[60].open=[position(),position({id:2,symbol:'SUI-USDT-SWAP',taken:0,notional:0}),position({id:3,fill_ms:3000})];
  d.tracks[5].open=[position({id:4,symbol:'DOGE-USDT-SWAP'})];d.signals=[{id:5,symbol:'SOL-USDT-SWAP',status:'excluded',candle_ms:1}];
  assert.deepEqual(ui.rows(d,ui.route({track:'60'})).map(x=>x.id),[3,1]);
  assert.deepEqual(ui.rows(d,ui.route({track:'60',scope:'shadow'})).map(x=>x.id),[2]);
  assert.deepEqual(ui.rows(d,ui.route({track:'5'})).map(x=>x.id),[4]);
  assert.deepEqual(ui.rows(d,ui.route({track:'60',scope:'all',q:' sui '})).map(x=>x.id),[2]);
  assert.equal(ui.rows(d,ui.route({tab:'events',scope:'funded'})).length,1);
  assert.equal(d.tracks[60].open[0].id,1);assert.equal(d.tracks[60].stats.taken_closed,42);
});
test('route values are bounded and chart links preserve exact contract and selected track period',()=>{
  assert.deepEqual(ui.route({track:'bad',tab:'bad',scope:'bad'}),{track:'spike',tab:'open',sort:'score',history:'',scope:'funded',q:'',period:'15m',trade:'',symbol:''});
  assert.equal(ui.route({q:'x'.repeat(1000)}).q.length,80);
  assert.equal(ui.chartLink('SOL-USDT-SWAP','5'),'#monday-fib?symbol=SOL-USDT-SWAP&timeframe=5m');
  assert.equal(ui.chartLink('SOL-USDT-SWAP','60'),'#monday-fib?symbol=SOL-USDT-SWAP&timeframe=1H');
  assert.equal(ui.chartLink('javascript:alert(1)','60'),'#monday-fib');
  assert.match(ui.rowHTML(position({symbol:'<img onerror="x">',note:'<script>unsafe</script>'}),ui.route(),10000),/&lt;script&gt;/);
  assert.doesNotMatch(ui.rowHTML(position({symbol:'<img onerror="x">'}),ui.route(),10000),/<img onerror/);
});
test('malformed or non-paper snapshots are errors rather than zero accounts',()=>{
  assert.equal(ui.validate(snapshot()).tracks[60].equity,1002);
  assert.throws(()=>ui.validate({...snapshot(),paper_only:false}));
  assert.throws(()=>ui.validate({...snapshot(),tracks:{60:track()}}));
  assert.throws(()=>ui.validate({...snapshot(),signals:null}));
  assert.throws(()=>ui.validate({...snapshot(),signals:[null]}));
  assert.throws(()=>ui.validate({...snapshot(),tracks:{60:track({open:[{}]}),5:track()}}));
});
const flush=()=>new Promise(resolve=>setImmediate(resolve));
function feedHarness(read){
  const timers=new Map(),changes=[];let seq=0,cancels=0;
  const feed=ui.createFeed({read,change:s=>changes.push(s),cancel:()=>cancels++,schedule:fn=>{timers.set(++seq,fn);return seq;},unschedule:id=>timers.delete(id)});
  return {feed,timers,changes,cancels:()=>cancels};
}
test('visible feed deduplicates refreshes, polls once and retains data after errors',async()=>{
  let resolve,reads=0;const c=feedHarness(()=>{reads++;return new Promise(r=>resolve=r);});
  c.feed.setActive(true);await flush();const a=c.feed.refresh(),b=c.feed.refresh();assert.equal(a,b);assert.equal(reads,1);
  resolve(snapshot());await a;assert.equal(c.timers.size,1);assert.equal(c.feed.getState().data.tracks[60].equity,1002);
  const second=c.feed.refresh();await flush();resolve({});await second;
  assert.match(c.feed.getState().error,/格式/);assert.equal(c.feed.getState().data.tracks[60].equity,1002);
  const recovery=c.feed.refresh();await flush();resolve(snapshot());await recovery;assert.equal(c.feed.getState().error,null);
  c.feed.setActive(false);assert.equal(c.timers.size,0);await c.feed.refresh();assert.equal(reads,3);assert.equal(c.cancels(),1);
});
test('leaving and returning discards late results without cancelling the new request',async()=>{
  const completions=[];const c=feedHarness(()=>new Promise(resolve=>completions.push(resolve)));
  c.feed.setActive(true);await flush();c.feed.setActive(false);c.feed.setActive(true);await flush();
  completions[0]({...snapshot(),now_ms:999});await flush();assert.equal(c.feed.getState().data,null);assert.equal(c.feed.getState().busy,true);
  completions[1](snapshot());await flush();assert.equal(c.feed.getState().data.now_ms,10000);assert.equal(c.timers.size,1);
  c.feed.setActive(false);
});
test('failed initial read shows an error and successful empty data remains distinguishable',async()=>{
  let fail=true;const c=feedHarness(()=>fail?Promise.reject(TypeError('Failed to fetch')):Promise.resolve(snapshot()));
  c.feed.setActive(true);await flush();assert.equal(c.feed.getState().data,null);assert.equal(c.feed.getState().error,'网络暂时不可用');
  fail=false;await c.feed.refresh();assert.ok(c.feed.getState().data);assert.equal(c.feed.getState().error,null);assert.equal(c.feed.getState().data.tracks[60].open.length,0);c.feed.setActive(false);
});

// Ordering applies only to the displayed page; account totals and fills are untouched.
test('every ledger tab ranks raw scores descending, missing last, then newest on ties',()=>{
  const data=snapshot(),input=[.5,null,.9,.4,.50001,0,.9].map((score,i)=>position({id:i+1,score,fill_ms:i,exit_ms:i,candle_ms:i}));
  data.signals=input;data.tracks[60].open=input;data.tracks[60].closed=input;
  for(const tab of ['events','open','closed']){
    assert.deepEqual(ui.rows(data,ui.route({track:'60',tab})).map(r=>r.id),[7,3,5,1,4,6,2]);
    const html=ui.rowHTML(input[2],ui.route({tab,period:'15m'}),10000);
    assert.match(html,/rp-score is-high/);
    assert.match(html,/https:\/\/www.tradingview.com\/chart\/\?symbol=OKX%3ASOLUSDT.P/);
    assert.match(html,/#yolo-detect\?tab=results&amp;symbol=SOL-USDT-SWAP&amp;timeframe=15m/);
    assert.match(html,tab==='events'?/data-rp-symbol="SOL-USDT-SWAP"/:/data-rp-trade="3"/);
    assert.match(html,/data-rp-detail=/);
    // Links must be sibling actions, never descendants of the background button.
    assert.doesNotMatch(html,/<button[^>]*class="rp-row-select"[^>]*>[^<]*<a/);
  }
  assert.deepEqual(input.map(r=>r.id),[1,2,3,4,5,6,7]);
});

test('position amount is USDT and loss color never inherits the long direction',()=>{
 const api=require('../../yoyo/monitor/static/monday-reclaim-paper.js');
 const p={id:71,symbol:'SOL-USDT-SWAP',side:1,taken:1,notional:100,remaining_notional:75,entry:5,pnl:-3.25};
 const closed=api.rowHTML(p,{tab:'closed',track:'60',period:'5m'},1);
 assert.match(closed,/<span class="rp-positive">做多<\/span> · <span class="rp-negative">-3.25/);
 const open=api.rowHTML(p,{tab:'open',track:'60',period:'5m'},1);
 assert.match(open,/持仓 75.00 USDT/);assert.doesNotMatch(open,/剩余 15 SOL/);assert.match(open,/data-rp-close="71"/);
});

test('four account routes isolate positions and unavailable accounts never fall back to v2',()=>{
 const d=snapshot();d.tracks.v3=track({open:[position({id:1060})]});d.tracks.v3add=track({open:[position({id:2060})],pending_adds:[]});
 for(const key of ['v3','v3add']){assert.equal(ui.route({track:key}).track,key);assert.equal(ui.rows(d,ui.route({track:key}))[0].id,key==='v3'?1060:2060);}
 delete d.tracks.v3;assert.deepEqual(ui.rows(d,ui.route({track:'v3'})),[]);assert.equal(ui.summary(d.tracks.v3).equity,undefined);
 assert.equal(ui.validate(d),d);assert.throws(()=>ui.validate({...d,tracks:{...d.tracks,v3:track({open:null})}}));
 assert.throws(()=>ui.validate({...d,tracks:{...d.tracks,v3add:track({pending_adds:[null]})}}));
});
test('v3 targets respect score bands while each stop uses its stored price, including linked adds',()=>{
 for(const [score,label] of [[.49,'周一低点'],[.5,'区间 0.618'],[.59999,'区间 0.618'],[.6,'对侧 1.618'],[null,'止盈目标']])assert.equal(ui.targetLabel(position({score}), 'v3'),label);
 const p=position({id:9,track:2061,parent_id:8,entry:95,reclaim_ref:100,backstop:70,be_price:100.17,be_from_ms:10000,stage:'breakeven'});
 assert.equal(ui.stopPrice(p),100.17);assert.equal(ui.stopPrice({...p,be_price:null}),95);assert.equal(ui.stopPrice({...p,be_from_ms:null}),70);
 const html=ui.rowHTML(p,ui.route({track:'v3add'}),12000);
 assert.match(html,/加仓 · 主单 #8/);assert.match(html,/加仓入场价/);assert.match(html,/100.17/);assert.match(html,/主单确认参考价/);
 assert.equal(ui.stage({...p,stage:'closed',reason:'backstop'},12000),'兜底止损');
 assert.equal(ui.stage({...p,stage:'closed',reason:'reclaimed_inside_be'},12000),'收回但未越过保本价');
});
test('pending adds use parent original notional as a ceiling and stay out of fills, shadows and PnL',()=>{
 const parent=position({id:8,notional:200,remaining_notional:50}),pending=position({id:9,parent_id:8,entry:114,stage:'pending_fill',taken:0,notional:0});
 const t=track({open:[parent],pending_adds:[pending]}),state=ui.route({track:'v3add'}),d=snapshot();d.tracks.v3add=t;
 assert.deepEqual(ui.rows(d,state).map(p=>p.id),[8]);
 const html=ui.pendingHTML(t,state,10000);
 assert.match(html,/主单 #8/);assert.match(html,/200.00 USDT/);assert.match(html,/待成交加仓/);assert.doesNotMatch(html,/data-rp-close|data-rp-trade|参考浮动收益/);
 for(const change of [{track:'v3'},{tab:'closed'},{scope:'shadow'}])assert.equal(ui.pendingHTML(t,{...state,...change},10000),'');
 assert.doesNotMatch(ui.pendingHTML(t,{...state,q:'BTC'},10000),/rp-pending-order/);
 assert.match(ui.pendingHTML({...t,open:[]},state,10000),/计划本金上限<\/dt><dd>—/);
});
test('account rule copy reads frozen configuration and does not promise net breakeven',()=>{
 const d=snapshot();d.tracks.v3add=track({confirm_minutes:60});d.v3={min_score:.5,far_score:.6,backstop_pct:30,sizing:{fraction:.05,cut:.2262,mean_excess:.1088,cap:.5}};d.v3add={from_score:.6,drawdown_pct:5};d.breakeven_offset_bp=17;
 const text=ui.rulesHTML(d,'v3add');
 for(const pattern of [/独立模拟策略/,/区间 0.618/,/对侧 1.618/,/30% 兜底/,/0.17%/,/确认周期 60 分钟/,/主单与已成交加仓分别计笔/])assert.match(text,pattern);
 assert.doesNotMatch(text,/彻底覆盖|保证|NaN|undefined/);
});

test('spike routes and scopes preserve account allocation and explicit shadow reasons',()=>{
 const d=snapshot(),state=ui.route({track:'spike'});
 const p=position({track:3060,ema_order:4,score:.7});
 d.tracks.spike=track({open:[p,...['no_slot','order_0','order_missing'].map((note,i)=>({...p,id:i+2,taken:0,notional:0,note,ema_order:i===1?0:null}))]});
 assert.equal(state.track,'spike');assert.equal(ui.trackLabel(state.track),'spike');assert.equal(ui.validate(d),d);
 assert.deepEqual(ui.rows(d,state).map(p=>p.id),[1]);
 const shadow=ui.rows(d,{...state,scope:'shadow'});
 assert.equal(shadow.length,3);
 for(const [note,label] of [['no_slot','仓位已满'],['order_0','排列分不足'],['order_missing','排列分缺失']]){
  const row=shadow.find(p=>p.note===note),html=ui.rowHTML(row,state,10000);
  assert.match(ui.allocation(row),new RegExp(label));
  assert.match(html,new RegExp(label));assert.doesNotMatch(html,/data-rp-close=/);
 }
 assert.match(ui.rowHTML(p,state,10000),/排列 4\/4/);
 assert.equal(ui.targetLabel(p,'spike'),'周一低点');
 delete d.tracks.spike;assert.deepEqual(ui.rows(d,state),[]);
 assert.throws(()=>ui.validate({...d,tracks:{...d.tracks,spike:track({open:null})}}));
});

test('recorded order zero differs from missing values without inferring a current score',()=>{
 assert.equal(ui.orderLabel(0),'排列 0/4');
 for(const value of [null,undefined,NaN,-1,5,'3'])assert.equal(ui.orderLabel(value),'排列 —');
 for(const tab of ['open','closed','events']){
  const html=ui.rowHTML(position({ema_order:0}),ui.route({track:'spike',tab}),10000);
  assert.match(html,/排列 0\/4/);assert.match(html,/OKX · 15m/);
 }
});

test('spike rules use its own sizing, threshold and target without v2 gross cap',()=>{
 const d=snapshot();d.tracks.spike=track({confirm_minutes:60});
 d.spike={min_score:.6,min_order:3,slots:10,leverage:5,backstop_pct:18};
 const text=ui.rulesHTML(d,'spike');
 for(const pattern of [/最多 10 个资金仓位/,/5 倍杠杆/,/净值 ÷ 10/,/入场分 ≥ 0.60/,/排列分 ≥ 3/,/18% 兜底止损/,/确认周期 60 分钟/,/OKX · 15m/,/币安排列线尚未接入/])assert.match(text,pattern);
 assert.doesNotMatch(text,/总名义敞口上限|按评分线性分配|NaN|undefined/);
});

test('paper 12H bookmark survives route parsing and invalid periods keep the default',()=>{
 assert.equal(ui.route({track:'spike',period:'12H'}).period,'12H');
 assert.equal(ui.route({period:'12Hutc'}).period,'15m');
});

test('v3lock is an independent optional account with its own funded and shadow ledger',()=>{
 const d=snapshot(),state=ui.route({track:'v3lock'});
 d.tracks.v3=track({open:[position({id:31,track:1060})]});
 d.tracks.v3lock=track({equity:1000,open:[position({id:41,track:4060}),position({id:42,track:4060,taken:0,notional:0})]});
 assert.equal(state.track,'v3lock');assert.equal(ui.trackLabel(state.track),'v3lock');assert.equal(ui.validate(d),d);
 assert.deepEqual(ui.rows(d,state).map(p=>p.id),[41]);
 assert.deepEqual(ui.rows(d,{...state,scope:'shadow'}).map(p=>p.id),[42]);
 assert.deepEqual(ui.rows(d,ui.route({track:'v3'})).map(p=>p.id),[31]);
 assert.equal(ui.summary(d.tracks.v3lock).equity,1000);
 delete d.tracks.v3lock;
 assert.equal(ui.validate(d),d);assert.deepEqual(ui.rows(d,state),[]);assert.equal(ui.summary(d.tracks.v3lock).equity,undefined);
 assert.throws(()=>ui.validate({...d,tracks:{...d.tracks,v3lock:track({open:null})}}));
});

test('v3lock retains v3 score targets and displays the actual long or short protection price',()=>{
 for(const [score,label] of [[.49,'周一低点'],[.5,'区间 0.618'],[.59999,'区间 0.618'],[.6,'对侧 1.618']]){
  assert.equal(ui.targetLabel(position({score}),'v3lock'),label);
  assert.equal(ui.targetLabel(position({score,track:4060})),label);
 }
 for(const [side,be_price] of [[1,100.5],[-1,99.5]]){
  const p=position({track:4060,side,entry:100,score:.6,be_price,be_from_ms:10000,stage:'breakeven'});
  assert.equal(ui.stopPrice(p),be_price);assert.equal(ui.stage(p,12000),'利润锁保护已启用');
  const html=ui.rowHTML(p,ui.route({track:'v3lock'}),12000);
  assert.match(html,new RegExp(String(be_price).replace('.','\\.')));assert.match(html,/对侧 1.618/);
  assert.equal(ui.stage({...p,stage:'closed',reason:'breakeven'},12000),'利润锁止损');
  assert.equal(ui.stage({...p,stage:'closed',reason:'reclaimed_inside_be'},12000),'收回但未越过利润锁价');
 }
});

test('v3lock rules bind its own offset while inheriting v3 entry, target and sizing configuration',()=>{
 const d=snapshot();d.tracks.v3lock=track({confirm_minutes:60});d.tracks.v3=track({confirm_minutes:60});
 d.v3={min_score:.5,far_score:.6,backstop_pct:30,sizing:{fraction:.05,cut:.2262,mean_excess:.1088,cap:.5}};
 d.breakeven_offset_bp=17;d.v3lock={breakeven_offset_bp:50};
 const html=ui.rulesHTML(d,'v3lock');
 for(const p of [/v3lock · 独立模拟策略/,/按评分线性分配/,/0.50%（多加、空减）/,/区间 0.618/,/对侧 1.618/,/30% 兜底/,/确认周期 60 分钟/,/未越过利润锁价，也在该收盘退出/])assert.match(html,p);
 assert.doesNotMatch(html,/0.17%|NaN|undefined/);assert.match(ui.rulesHTML(d,'v3'),/0.17%/);
 d.v3lock.breakeven_offset_bp=75;assert.match(ui.rulesHTML(d,'v3lock'),/0.75%/);
 delete d.v3lock;assert.doesNotMatch(ui.rulesHTML(d,'v3lock'),/0.17%|0.50%|NaN|undefined/);
});

test('allin3 owns its ledger, far targets and busy shadows without v3 sizing or spike filters',()=>{
 const d=snapshot(),state=ui.route({track:'allin3'});
 const p=position({id:51,track:5060,score:.6,remaining_notional:3000,notional:3000});
 d.tracks.allin3=track({rule:'allin3',confirm_minutes:60,open:[p,{...p,id:52,taken:0,notional:0,remaining_notional:0,note:'busy'}],refills:[]});
 assert.equal(state.track,'allin3');assert.equal(ui.trackLabel(state.track),'allin3');assert.equal(ui.validate(d),d);
 assert.deepEqual(ui.rows(d,state).map(p=>p.id),[51]);assert.deepEqual(ui.rows(d,{...state,scope:'shadow'}).map(p=>p.id),[52]);
 for(const side of [1,-1])assert.equal(ui.targetLabel({...p,side},state.track),'对侧 1.618');
 assert.equal(ui.targetLabel(p),'对侧 1.618');
 const html=ui.rowHTML(d.tracks.allin3.open[1],state,10000);
 assert.match(html,/已有资金仓位/);assert.doesNotMatch(html,/data-rp-close=/);
 d.allin3={min_score:.6,backstop_pct:30,leverage:3,blowup_below:100};d.breakeven_offset_bp=17;
 const rules=ui.rulesHTML(d,'allin3');
 for(const r of [/仅持有 1 笔资金仓位/,/净值 × 3/,/分批平仓按对应份额封顶/,/入场分 ≥ 0.60/,/对侧 1.618/,/30% 价格兜底/,/确认周期 60 分钟/,/0.17%/,/低于 100 USDT/,/本金份数加 1/,/盈亏剔除补充本金/])assert.match(rules,r);
 assert.doesNotMatch(rules,/总名义敞口上限|按评分线性分配|均线排列分 ≥|0.50%|NaN|undefined/);
 assert.throws(()=>ui.validate({...d,tracks:{...d.tracks,allin3:{...d.tracks.allin3,refills:[null]}}}));
 delete d.tracks.allin3;assert.equal(ui.validate(d),d);assert.deepEqual(ui.rows(d,state),[]);
});

test('capital refills stay outside realized trading PnL and distinct from stake-budget multiples',()=>{
 const refill={id:1,ms:1000,position_id:51,equity_before:97.9,equity_after:1000};
 const t=track({rule:'allin3',equity:1000,stakes:2,injected:2000,multiple:.5,refills:[refill]});
 assert.ok(Math.abs(ui.summary(t).pnl+902.1)<1e-8);
 assert.equal(ui.capitalLabel(t),'第 2 份本金 · 0.50×');
 assert.match(ui.capitalHTML(t),/2,000.00 USDT/);assert.match(ui.capitalHTML(t),/本金份数/);
 const html=ui.refillsHTML(t);
 for(const r of [/97.90 USDT/,/1,000.00 USDT/,/\+902.10 USDT/,/交易 #51/,/不计入交易盈亏/])assert.match(html,r);
 assert.ok(Math.abs(ui.summary({...t,equity:1300}).pnl+602.1)<1e-8);
 assert.equal(ui.summary({...t,rule:'v3'}).pnl,0);
 assert.equal(ui.summary({...t,refills:[]}).pnl,0);assert.match(ui.refillsHTML({...t,refills:[]}),/尚无补充本金记录/);
 for(const refills of [undefined,null,[null],[{...refill,equity_before:null}]]){
  assert.equal(ui.summary({...t,refills}).pnl,null);assert.match(ui.refillsHTML({...t,refills}),/暂不可用/);
 }
 assert.equal(ui.summary(undefined,'allin3').pnl,null);
 assert.equal(ui.capitalLabel(undefined),'第 — 份本金 · —×');
 assert.equal(ui.refillTotal([{...refill,equity_before:0},refill]),1902.1);
});

test('v3add20 preserves its route, both legs, targets and capacity shadows',()=>{
 const d=snapshot(),state=ui.route({track:'v3add20'}),parent=position({id:6060,track:6060,score:.7}),child=position({id:6061,track:6061,parent_id:6060,score:.55});
 d.tracks.v3add20=track({open:[parent,child],pending_adds:[]});assert.equal(ui.validate(d),d);assert.equal(state.track,'v3add20');assert.equal(ui.trackLabel(state.track),'v3add20');assert.equal(ui.rows(d,state).length,2);
 assert.equal(ui.targetLabel(parent,state.track),'对侧 1.618');assert.equal(ui.targetLabel(child,state.track),'区间 0.618');assert.equal(ui.targetLabel({...child,score:.45},state.track),'周一低点');
 const html=ui.rowHTML(child,state,12000);assert.match(html,/加仓 · 主单 #6060/);assert.match(html,/交易关联/);
 for(const [note,label] of [['no_slot','仓位已满'],['no_capacity','额度不足']])assert.match(ui.rowHTML({...parent,taken:0,notional:0,note},state,12000),new RegExp(label));
 assert.equal(ui.rows(snapshot(),state).length,0);assert.throws(()=>ui.validate({...d,tracks:{...d.tracks,v3add20:track({pending_adds:[null]})}}));
});
test('v3add20 pending size is dynamic and current rules never inherit score-linear sizing',()=>{
 const parent=position({id:10,notional:777}),pending=position({id:11,parent_id:10,stage:'pending_fill',notional:0,taken:0});
 const d=snapshot();d.tracks.v3add20=track({confirm_minutes:60,open:[parent],pending_adds:[pending]});d.v3={min_score:.5,far_score:.6,backstop_pct:30};d.v3add20={from_score:.6,drawdown_pct:5,slots:20,leverage:3};d.breakeven_offset_bp=17;
 const html=ui.pendingHTML(d.tracks.v3add20,ui.route({track:'v3add20'}),12000,d.v3add20);
 assert.match(html,/成交时净值 ÷ 20 × 3/);assert.doesNotMatch(html,/777|计划本金上限/);assert.match(html,/满仓或保证金不足则取消/);
 assert.equal(ui.pendingHTML(d.tracks.v3add20,ui.route({track:'v3add20',scope:'shadow'}),12000,d.v3add20),'');
 const rules=ui.rulesHTML(d,'v3add20');for(const t of ['最多 20','3 倍','主单与已成交加仓合计','总保证金不超过净值','0.17%','主单入场价','成交时','30%'])assert.ok(rules.includes(t),t);
 assert.doesNotMatch(rules,/按评分线性分配|总名义敞口上限|等名义本金加仓/);
 assert.equal(ui.marginLeverage(d,'v3add20'),3);assert.equal(ui.marginLeverage({},'v3add20'),undefined);assert.equal(ui.marginLeverage(d,'v3add'),null);
});

test('home defaults to the owner-selected historical winner and 15-minute candles',()=>{assert.equal(ui.route().track,'spike');assert.equal(ui.route().period,'15m');assert.equal(ui.route({track:'v3add20',period:'4H'}).track,'v3add20');assert.equal(ui.route({track:'v3add20',period:'4H'}).period,'4H');});

test('each completed feed read emits one settled snapshot without a duplicate repaint',async()=>{
  const states=[],data=snapshot();
  const feed=ui.createFeed({read:async()=>data,change:s=>states.push(s),schedule:()=>1,unschedule(){}});
  feed.setActive(true);await feed.refresh();
  assert.equal(states.length,2);assert.equal(states[0].busy,true);
  assert.equal(states[1].busy,false);assert.equal(states[1].data,data);
  feed.setActive(false);
});
