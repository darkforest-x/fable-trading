'use strict';
const test=require('node:test'),assert=require('node:assert/strict');
const {Poller,body,ownership}=require('../../yoyo/monitor/static/manual-account.js');
test('automatic GET poll schedules next read and ignores response after leaving',async()=>{
  const jobs=new Map(),renders=[],calls=[];let seq=0,resolve;
  const p=new Poller(signal=>{calls.push(signal);return new Promise(r=>resolve=r)},(...args)=>renders.push(args),{set:(fn,ms)=>{const id=++seq;jobs.set(id,{fn,ms});return id},clear:id=>jobs.delete(id)});
  p.activate(true);resolve({account_scope:'yolo_manual'});await new Promise(setImmediate);
  assert.equal(renders.length,1);assert.equal([...jobs.values()][0].ms,5000);
  [...jobs.values()][0].fn();p.activate(false);resolve({account_scope:'yolo_manual'});await new Promise(setImmediate);
  assert.equal(renders.length,1);assert.ok(calls[1].aborted);
});
test('wrong account is never rendered',async()=>{
  const seen=[];const p=new Poller(async()=>({account_scope:'signals'}),(...a)=>seen.push(a),{set:()=>1,clear:()=>{}});
  p.activate(true);await new Promise(setImmediate);assert.equal(seen[0][0],null);assert.ok(seen[0][1]);p.activate(false);
});
test('ownership is not guessed from an instrument alone',()=>{
  const p={instId:'A',posSide:'long',pos:'2'};
  assert.equal(ownership(p,[],true),'自行管理');assert.equal(ownership(p,[],false),'待核对');
  assert.equal(ownership(p,[{symbol:'A',side:'long',size:'1',state:'active'}],true),'数量待核对');
  assert.equal(ownership(p,[{symbol:'A',side:'long',size:'2',state:'active'}],true),'系统管理');
});
test('history failure is distinct from empty history and untrusted strings are escaped',()=>{
  assert.match(body({account:{data:{history:{status:'unavailable'}}}},'history'),/暂不可用/);
  const d={executions:{status:'ready',items:[{id:'1',symbol:'<script>',side:'long',state:'skipped',reason_code:'manual_expired'}]}};
  const html=body(d,'executions');assert.ok(!html.includes('<script>'));assert.match(html,/报价已过期/);assert.match(html,/查看/);
});

test('changing account values preserves table focus and both scroll offsets',()=>{
 const {updateTable}=require('../../yoyo/monitor/static/manual-account.js');
 const old={scrollLeft:230,scrollTop:90},next={scrollLeft:0,scrollTop:0,focus(){this.focused=true;}};let replaced=false;
 const panel={_markup:'old',querySelector:()=>replaced?next:old,contains:node=>node===old,querySelectorAll:()=>[],set innerHTML(value){replaced=true;}};
 updateTable(panel,'new',old);assert.equal(next.scrollLeft,230);assert.equal(next.scrollTop,90);assert.equal(next.focused,true);
 next.focused=false;updateTable(panel,'new',old);assert.equal(next.focused,false);
});
test('order row refresh restores the same order action instead of stealing outside focus',()=>{
 const {updateTable}=require('../../yoyo/monitor/static/manual-account.js');
 const focused={dataset:{maOrder:'same'}},same={dataset:{maOrder:'same'},focus(){this.focused=true;}},other={dataset:{maOrder:'other'},focus(){this.focused=true;}};
 const panel={querySelector:()=>null,contains:n=>n===focused,querySelectorAll:()=>[other,same]};
 updateTable(panel,'one',focused);assert.equal(same.focused,true);assert.equal(other.focused,undefined);
 same.focused=false;updateTable(panel,'two',{dataset:{}});assert.equal(same.focused,false);
});
test('only visible discovery, results and account views own account reads',()=>{
 const {pollMode}=require('../../yoyo/monitor/static/manual-account.js');
 assert.equal(pollMode(true,'account',false),'full');assert.equal(pollMode(true,'results',false),'workspace');assert.equal(pollMode(true,'schedules',false),'workspace');
 for(const tab of ['model','history'])assert.equal(pollMode(true,tab,false),null);
 for(const tab of ['account','results','schedules']){assert.equal(pollMode(false,tab,false),null);assert.equal(pollMode(true,tab,true),null);}
});

test('results desk shows positions and pending execution without fabricated PnL',()=>{
 const {deskMetrics,deskPositions,deskActivity,deskBlocker}=require('../../yoyo/monitor/static/manual-account.js');
 const d={account:{data:{total_equity_usdt:'100',balance:{availEq:'90',upl:'-1.2'},positions:[{instId:'ETH-USDT-SWAP',posSide:'long',pos:'2',upl:'-1.2',avgPx:'100',markPx:'99'}]}},executions:{status:'ready',statistics:{realized_net_usdt:null},owned_lots:[{symbol:'ETH-USDT-SWAP',side:'long',state:'active',size:'2'}],active_items:[{id:'owned',symbol:'ETH-USDT-SWAP',side:'long',state:'active',size:'2',stop:'98',take_profit:'106',protection_verified:true},{id:'pending',symbol:'BTC-USDT-SWAP',side:'long',state:'entry_pending'}],items:[]}};
 assert.match(deskMetrics(d),/已结算净收益<\/span><strong class="">—/);
 assert.match(deskPositions(d),/止盈止损已核验/);assert.match(deskPositions(d),/止损 98/);
 assert.match(deskActivity(d),/核对市价成交/);assert.match(deskActivity(d),/data-ma-order="pending"/);
 d.executions.owned_lots[0].size='1';assert.match(deskPositions(d),/数量待核对/);assert.doesNotMatch(deskPositions(d),/止盈止损已核验/);
 assert.match(deskBlocker({status:'blocked',title:'核对',message:'<script>',issues:[]}),/&lt;script&gt;/);
});

test('refresh retains open position protection details',()=>{
 const {updateTable}=require('../../yoyo/monitor/static/manual-account.js');
 let replaced=false;const old={dataset:{maDisclosure:'ETH:long'},open:true},next={dataset:{maDisclosure:'ETH:long'},open:false};
 const panel={querySelector:()=>null,contains:()=>false,querySelectorAll:()=>replaced?[next]:[old],set innerHTML(v){replaced=true;}};
 updateTable(panel,'changed',null);assert.equal(next.open,true);
});

test('obsolete poll timeout cannot abort a restarted account request',async()=>{
 const timers=[],signals=[],resolvers=[];
 const p=new Poller(signal=>{signals.push(signal);return new Promise(r=>resolvers.push(r));},()=>{}, {set:fn=>(timers.push(fn),timers.length),clear:()=>{}});
 p.activate(true);p.restart();timers[0]();
 assert.equal(signals[0].aborted,true);assert.equal(signals[1].aborted,false);
 resolvers.forEach(r=>r({account_scope:'yolo_manual'}));await new Promise(setImmediate);p.activate(false);
});

test('all in-flight execution intents remain visible even beyond recent history limit',()=>{
 const {deskActivity}=require('../../yoyo/monitor/static/manual-account.js');
 const html=deskActivity({executions:{status:'ready',active_items:Array.from({length:8},(_,i)=>({id:'pending'+i,state:'entry_pending',symbol:'ETH-USDT-SWAP'})),items:[{id:'old',state:'closed'}]}});
 for(let i=0;i<8;i++)assert.ok(html.includes('data-ma-order="pending'+i+'"'));
 assert.ok(!html.includes('data-ma-order="old"'));
});
