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
  assert.deepEqual(ui.rows(d,ui.route()).map(x=>x.id),[3,1]);
  assert.deepEqual(ui.rows(d,ui.route({scope:'shadow'})).map(x=>x.id),[2]);
  assert.deepEqual(ui.rows(d,ui.route({track:'5'})).map(x=>x.id),[4]);
  assert.deepEqual(ui.rows(d,ui.route({scope:'all',q:' sui '})).map(x=>x.id),[2]);
  assert.equal(ui.rows(d,ui.route({tab:'events',scope:'funded'})).length,1);
  assert.equal(d.tracks[60].open[0].id,1);assert.equal(d.tracks[60].stats.taken_closed,42);
});
test('route values are bounded and chart links preserve exact contract and selected track period',()=>{
  assert.deepEqual(ui.route({track:'bad',tab:'bad',scope:'bad'}),{track:'60',tab:'open',scope:'funded',q:'',period:'5m',trade:'',symbol:''});
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
    assert.deepEqual(ui.rows(data,ui.route({tab})).map(r=>r.id),[7,3,5,1,4,6,2]);
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
 for(const pattern of [/独立模拟账户/,/区间 0.618/,/对侧 1.618/,/30% 兜底/,/0.17%/,/确认周期 60 分钟/,/主单与已成交加仓分别计笔/])assert.match(text,pattern);
 assert.doesNotMatch(text,/彻底覆盖|保证|NaN|undefined/);
});
