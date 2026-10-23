'use strict';
const test=require('node:test'),assert=require('node:assert/strict');
const {CloseFlow}=require('../../yoyo/monitor/static/manual-close.js');
const {closeActions,updateTable}=require('../../yoyo/monitor/static/manual-account.js');
const draft={account_scope:'yolo_manual',action:'close',id:'command',lot_id:'lot',state:'preview',size:'2',expires_ms:1000};
test('opening and canceling a close preview never confirms; duplicate clicks submit once',async()=>{
 const calls=[];let finish;
 const flow=new CloseFlow(async(path,options)=>{calls.push({path,options});if(path.endsWith('/confirm'))return new Promise(r=>finish=r);return draft;});
 await flow.open('lot');flow.reset();assert.equal(calls.length,1);
 await flow.open('lot');const pending=flow.confirm(500);flow.confirm(500);assert.equal(calls.filter(c=>c.path.endsWith('/confirm')).length,1);
 assert.deepEqual(JSON.parse(calls.at(-1).options.body),{acknowledgement:'CONFIRM-LIVE-CLOSE'});
 finish({...draft,state:'confirmed'});await pending;assert.equal(flow.phase,'tracking');
});
test('lost confirmation is GET-reconciled and reopening the lot retains the same command',async()=>{
 const calls=[];const flow=new CloseFlow(async(path,options)=>{calls.push({path,options});if(path.endsWith('/confirm'))throw Error('lost');return {...draft,state:path==='/preview'?'preview':'processing'};});
 await flow.open('lot');await flow.confirm(500);await flow.confirm(500);await flow.read();flow.reset();await flow.open('lot');
 assert.equal(calls.filter(c=>c.options?.method==='POST').length,2);
 assert.equal(calls.at(-1).path,'/command');assert.equal(calls.at(-1).options,undefined);assert.equal(flow.phase,'tracking');
});
test('stale preview and mismatched receipt cannot authorize a close',async()=>{
 const calls=[];const flow=new CloseFlow(async(path,options)=>{calls.push(path);return draft;});
 await flow.open('lot');await flow.confirm(1000);assert.equal(flow.phase,'expired');assert.deepEqual(calls,['/preview']);
 await flow.open('different-lot');assert.equal(flow.phase,'error');await flow.confirm(1);assert.equal(calls.length,2);
});
test('a preview response after cancellation cannot replace another lot',async()=>{
 const resolvers=[];const flow=new CloseFlow(()=>new Promise(r=>resolvers.push(r)));
 const old=flow.open('old'),current=flow.open('lot');resolvers[0]({...draft,lot_id:'old'});await old;assert.equal(flow.value,null);
 resolvers[1](draft);await current;assert.equal(flow.value.lot_id,'lot');
});
test('close actions enumerate owned lots only and disable stale or unresolved positions',()=>{
 const p={instId:'ETH-USDT-SWAP',posSide:'long',pos:'3'},lots=[{id:'one',symbol:p.instId,side:'long',size:'1',state:'active',protection_verified:true},{id:'two',symbol:p.instId,side:'long',size:'2',state:'active',protection_verified:true}];
 const d={account:{status:'ready'},executions:{status:'ready',owned_lots:lots,active_items:lots}};
 assert.match(closeActions(p,d),/data-ma-close="one"/);assert.match(closeActions(p,d),/平仓 · 2 张/);
 assert.doesNotMatch(closeActions(p,d,false),/data-ma-close/);assert.doesNotMatch(closeActions({...p,pos:'4'},d),/data-ma-close/);
 lots[0].protection_verified=false;assert.match(closeActions(p,d),/data-ma-close="one" disabled/);
});
test('account polling restores the same close button focus',()=>{
 const focused={dataset:{maClose:'two'}},target={dataset:{maClose:'two'},focus(){this.called=true;}};
 updateTable({querySelector:()=>null,querySelectorAll:()=>[target],contains:n=>n===focused},'changed',focused);assert.equal(target.called,true);
});
