import test from 'node:test';
import assert from 'node:assert/strict';
import {allocatedMargin,marginRoom,ticketEstimate} from '../../yoyo/discipline_app/mobile/src/ticketEstimate.ts';
import type {State} from '../../yoyo/discipline_app/mobile/src/api.ts';

test('100% spends only the remaining category allowance',()=>{
 const state={equity:'1000',policy:{major_margin_fraction:'.1',alt_margin_fraction:'.05'},
  positions:[{symbol:'BTC-USDT-SWAP',entry:'100',remaining:'2',leverage:10}],
  pending:[{symbol:'ETH-USDT-SWAP',plan:{margin:'30'}}],quotes:{}} as unknown as State;
 assert.equal(allocatedMargin(marginRoom(state,'BTC-USDT-SWAP'),100),.05);
 assert.equal(allocatedMargin(marginRoom(state,'ETH-USDT-SWAP'),50),.025);
 assert.equal(allocatedMargin(marginRoom(state,'SOL-USDT-SWAP'),100),.05);
 assert.equal(allocatedMargin(marginRoom(null,'BTC-USDT-SWAP'),100),0);
});

test('BTC and ETH each keep a full independent allowance while alts share theirs',()=>{
 const state={equity:'1000',policy:{major_allowance_scope:'independent',major_margin_fraction:'.1',alt_margin_fraction:'.05'},
  positions:[{symbol:'BTC-USDT-SWAP',entry:'100',remaining:'70',leverage:100}],
  pending:[{symbol:'BTC-USDT-SWAP',plan:{margin:'30'}},{symbol:'SOL-USDT-SWAP',plan:{margin:'50'}}],quotes:{}} as unknown as State;
 assert.equal(marginRoom(state,'BTC-USDT-SWAP'),0);
 assert.equal(marginRoom(state,'ETH-USDT-SWAP'),.1);
 assert.equal(marginRoom(state,'DOGE-USDT-SWAP'),0);
});

test('display estimate reserves both margin and notional within the selected pool',()=>{
 const state={equity:'1000',policy:{major_allowance_scope:'independent',major_margin_fraction:'.1',alt_margin_fraction:'.05',major_exposure_multiple:'10',alt_exposure_multiple:'5',risk_fraction:'.05',fee_rate:'.0005',slippage_rate:'.001'},
  positions:[],pending:[],quotes:{'ETH-USDT-SWAP':{last:'100',ask:'100',bid:'100'}},instruments:{'ETH-USDT-SWAP':{step:'.01'}}} as unknown as State;
 const ticket={symbol:'ETH-USDT-SWAP',side:'long' as const,kind:'market' as const,price:'',stop:'100',fraction:'.1',leverage:100};
 const empty=ticketEstimate(state,ticket);
 state.pending.push({symbol:'BTC-USDT-SWAP',side:'long',plan:{quantity:'100',entry:'100',margin:'100'}} as State['pending'][0]);
 assert.deepEqual(ticketEstimate(state,ticket),empty);
 state.pending[0].symbol='ETH-USDT-SWAP';
 assert.equal(ticketEstimate(state,ticket)?.quantity,0);
 state.pending[0].symbol='BTC-USDT-SWAP';
 state.policy!.major_allowance_scope='shared';
 assert.equal(ticketEstimate(state,ticket)?.quantity,0);
});

test('allocation clamps invalid inputs and never rounds above room',()=>{
 for(const room of [0,.000000019,.029999999,.1])for(const percent of [0,1,33,100,120]){
  const fraction=allocatedMargin(room,percent);
  assert.ok(fraction>=0&&fraction<=room);
 }
 assert.equal(allocatedMargin(.1,-10),0);
 assert.equal(allocatedMargin(.1,NaN),0);
 assert.equal(allocatedMargin(Infinity,100),0);
});


test('a fixed wick stop controls size without being reverse-solved from allocation',()=>{
 const state={equity:'1000',policy:{major_allowance_scope:'independent',major_margin_fraction:'.1',alt_margin_fraction:'.05',major_exposure_multiple:'10',alt_exposure_multiple:'5',risk_fraction:'.05',fee_rate:'.0005',slippage_rate:'.001'},
  positions:[],pending:[],quotes:{'BTC-USDT-SWAP':{last:'100',ask:'100',bid:'100'}},instruments:{'BTC-USDT-SWAP':{step:'.01'}}} as unknown as State;
 const ticket={symbol:'BTC-USDT-SWAP',side:'long' as const,kind:'market' as const,price:'',stop:'94',fraction:'.1',leverage:100};
 const near=ticketEstimate(state,ticket)!,far=ticketEstimate(state,{...ticket,stop:'90'})!;
 assert.ok(near.quantity>far.quantity);
 assert.ok(near.risk<=50&&far.risk<=50);
 assert.equal(ticket.stop,'94');
 const cap=ticketEstimate(state,{...ticket,stop:'100'})!;
 assert.ok(cap.notional<=10000&&cap.margin<=100);
 assert.equal(ticketEstimate(state,{...ticket,stop:'101'}),null);
});
