'use strict';
const test=require('node:test'),assert=require('node:assert/strict');
const {normalize,Action}=require('../../yoyo/monitor/static/monday-ledger.js');
const memory=()=>{const values=new Map();return {getItem:k=>values.get(k),setItem:(k,v)=>values.set(k,v),removeItem:k=>values.delete(k)};};
const operation=()=>({mode:'paper',action:'close',path:'/close',payload:{request_id:'same'},statusPath:'/receipt/same'});
test('account filtering keeps other accounts out of paper management',()=>{
 const d=normalize({positions:[{account_id:'a',id:'p',remaining:'1'},{account_id:'b',id:'other',remaining:'2'}],orders:[{account_id:'b',status:'pending'}]},'paper','a');
 assert.equal(d.positions.length,1);assert.equal(d.positions[0].id,'p');assert.equal(d.orders.length,0);
});
test('live controls need exact current ownership, connected account and management readiness',()=>{
 const d={account_scope:'yolo_manual',account:{status:'ready',data:{positions:[{instId:'BTC',posSide:'long',pos:'2'}]}},management_ready:true,free_tickets:[{id:'new',symbol:'BTC',side:'long',state:'filled',filled_size:'2'},{id:'old',symbol:'BTC',side:'long',state:'filled',filled_size:'2'}]};
 assert.equal(normalize(d,'live').positions[0].id,'new');assert.equal(normalize(d,'live').positions[0].canManage,true);
 d.free_tickets[0].filled_size='1';assert.equal(normalize(d,'live').positions[0].canManage,false);
 d.free_tickets[0].filled_size='2';d.account.status='stale';assert.equal(normalize(d,'live').positions[0].canManage,false);
 assert.throws(()=>normalize({...d,account_scope:'wrong'},'live'));
});
test('double confirmations send once and unknown writes survive reopen using the same receipt',async()=>{
 const store=memory(),calls=[];const request=async(path,o)=>{calls.push([path,o]);if(o)throw Error('lost');return {found:true};};
 const a=new Action(request,store,'paper:a');a.value=operation();await Promise.all([a.confirm(),a.confirm()]);assert.equal(calls.length,1);
 const reopened=new Action(request,store,'paper:a');await reopened.confirm();assert.equal(calls.length,1);await reopened.read();assert.equal(calls[1][0],'/receipt/same');assert.equal(reopened.value.finished,true);assert.equal(store.getItem('paper:a'),undefined);
});
test('unavailable session storage prevents the management write',async()=>{
 let writes=0;const a=new Action(async()=>writes++,{getItem(){},setItem(){throw Error('blocked')},removeItem(){}},'key');a.value=operation();await a.confirm();assert.equal(writes,0);assert.equal(a.value.sent,false);assert.match(a.error,/会话存储/);
});
test('live confirm ambiguity only reads the original management identity',async()=>{
 const calls=[],a=new Action(async(p,o)=>{calls.push(p);if(o)throw Error('lost');return {state:'done',reason:'止损已核对'};},memory(),'live');a.value={...operation(),mode:'live'};await a.confirm();await a.read();assert.deepEqual(calls,['/close','/receipt/same']);assert.equal(a.value.finished,true);
});
test('strategy lots keep their original ownership and management source',()=>{
 const d={account_scope:'yolo_manual',account:{status:'ready',data:{positions:[{instId:'BTC',posSide:'long',pos:'3',avgPx:'100'}]}},signal_management_ready:true,executions:{status:'ready',owned_lots:[{symbol:'BTC',side:'long',size:'3'}],active_items:[{id:'lot',symbol:'BTC',side:'long',size:'3',state:'active',protection_verified:true,stop:'95'}]}};
 const p=normalize(d,'live').positions[0];assert.equal(p.source,'signal');assert.equal(p.id,'lot');assert.equal(p.canManage,true);
 d.executions.owned_lots[0].size='2';assert.equal(normalize(d,'live').positions[0].canManage,false);
});
test('unknown entry receipts remain visible and cannot be canceled blindly',()=>{
 const d=normalize({account_scope:'yolo_manual',account:{status:'ready',data:{orders:[{ordId:'o',clOrdId:'c'}]}},management_ready:true,free_tickets:[{id:'t',client_order_id:'c',symbol:'BTC',state:'unknown'}]},'live');
 assert.equal(d.orders.length,1);assert.equal(d.orders[0].id,'t');assert.equal(d.orders[0].status,'unknown');assert.equal(d.orders[0].canManage,false);
});
test('partial fill retains free-ticket ownership only for the exact recorded remainder',()=>{
 const d={account_scope:'yolo_manual',account:{status:'ready',data:{positions:[{instId:'BTC',posSide:'long',pos:'75'}]}},management_ready:true,free_tickets:[{id:'part',symbol:'BTC',side:'long',state:'filled',filled_size:'100',management_closed_size:'25',take_profit:'120',stop_loss:'90'}]};
 const p=normalize(d,'live').positions[0];assert.equal(p.id,'part');assert.equal(p.canManage,true);assert.equal(p.takeProfit,'120');
 d.account.data.positions[0].pos='74';assert.equal(normalize(d,'live').positions[0].canManage,false);
});
