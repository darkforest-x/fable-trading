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
  assert.deepEqual(ui.route({track:'bad',tab:'bad',scope:'bad'}),{track:'60',tab:'open',scope:'funded',q:''});
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
