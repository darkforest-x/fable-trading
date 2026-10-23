import test from 'node:test';
import assert from 'node:assert/strict';
import {parseCloseAttempt,parseFavorites,countdown} from '../../yoyo/discipline_app/mobile/src/tradeExperience.ts';
import {fieldIssue} from '../../yoyo/discipline_app/mobile/src/ticketValidation.ts';
import {entryAvailability} from '../../yoyo/discipline_app/mobile/src/entryAvailability.ts';
import type {State,Ticket} from '../../yoyo/discipline_app/mobile/src/api.ts';

test('close recovery validates stored identity and exact percentage',()=>{
 const attempt={id:'a'.repeat(32),position:'immutable-position',symbol:'BTC-USDT-SWAP',percent:25,created:123};
 assert.deepEqual(parseCloseAttempt(JSON.stringify(attempt)),attempt);
 for(const patch of [{id:'bad'},{percent:101},{percent:.5},{created:null},{position:null}])assert.equal(parseCloseAttempt(JSON.stringify({...attempt,...patch})),null);
 assert.equal(parseCloseAttempt('{bad'),null);
});
test('favorites remain a bounded public-symbol list and countdown rounds up',()=>{
 assert.deepEqual(parseFavorites('["BTC-USDT-SWAP","BTC-USDT-SWAP","invalid",1]'),['BTC-USDT-SWAP']);
 assert.equal(countdown(3661.2),'01:01:02');assert.equal(countdown(-1),'00:00:00');
});
const state={policy:{},pending:[],blockers:[],cooldown_until:0,instruments:{'BTC-USDT-SWAP':{tick:'.01'}},quotes:{'BTC-USDT-SWAP':{ask:'100',bid:'100'}}} as unknown as State;
const ticket:Ticket={symbol:'BTC-USDT-SWAP',side:'long',kind:'limit',price:'100',stop:'99',fraction:'.1',leverage:10};
test('input issues identify price precision, wrong-side stop and empty allocation',()=>{
 assert.equal(fieldIssue({...ticket,price:'100.001'},state)?.field,'price');
 assert.equal(fieldIssue({...ticket,stop:'101'},state)?.message,'多单止损应低于入场价');
 assert.equal(fieldIssue({...ticket,side:'short',stop:'99'},state)?.message,'空单止损应高于入场价');
 assert.equal(fieldIssue({...ticket,fraction:'0'},state)?.field,'sizing');
 assert.equal(fieldIssue({...ticket,stop:''},state,true),null);
});
test('disabled entry always has a reason and a relevant destination',()=>{
 const args={state,symbol:ticket.symbol,connected:true,online:true,fresh:true,restoring:false,pending:false,room:0,now:100};
 assert.deepEqual(entryAvailability(args),{text:'BTC/ETH可用保证金额度已用完',tab:'持仓'});
 assert.equal(entryAvailability({...args,room:0,state:{...state,policy:{...state.policy!,major_allowance_scope:'independent'}}})?.text,'BTC可用保证金额度已用完');
 assert.equal(entryAvailability({...args,room:.1}),null);
 assert.equal(entryAvailability({...args,room:.1,state:{...state,pending:[{symbol:ticket.symbol}] as State['pending']}})?.tab,'持仓');
 assert.deepEqual(entryAvailability({...args,state:{...state,cooldown_until:161}}),{text:'冷静期剩余 00:01:01',tab:'纪律'});
});

test('external activity never blocks the matching App contract',()=>{
 const external={...state,external_activity:{current:[{symbol:'ETH-USDT-SWAP'}],recent:[],updated_at:100}} as State;
 const args={state:external,symbol:'BTC-USDT-SWAP',connected:true,online:true,fresh:true,restoring:false,pending:false,room:.1,now:100};
 assert.equal(entryAvailability(args),null);
 assert.equal(entryAvailability({...args,symbol:'ETH-USDT-SWAP'}),null);
 assert.equal(entryAvailability({...args,symbol:'ETH-USDT-SWAP',state:{...state,external_activity:{current:[],recent:external.external_activity!.current,updated_at:100}}}),null);
});

test('a rounded-to-zero order gets an inline minimum-size error',()=>{
 const tinyState={...state,equity:'1000',positions:[],policy:{major_margin_fraction:'.1',alt_margin_fraction:'.05',major_exposure_multiple:'10',alt_exposure_multiple:'5',risk_fraction:'.05',fee_rate:'.0005',slippage_rate:'.0005'},instruments:{'BTC-USDT-SWAP':{tick:'.01',step:'.001',minimum:'.001'}},quotes:{'BTC-USDT-SWAP':{last:'100000',ask:'100000',bid:'100000'}}} as unknown as State;
 assert.deepEqual(fieldIssue({...ticket,price:'100000',stop:'99000',fraction:'.000001'},tinyState),{field:'sizing',message:'低于合约最小委托量，请增加额度'});
});
