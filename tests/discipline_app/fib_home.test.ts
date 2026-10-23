import test from 'node:test';
import assert from 'node:assert/strict';
import {fibRangeFor,fibLevels,fibSelection,fibSide,latestFibTouch,parseFibSnapshot,touchLabel,type FibSnapshot,type FibContract} from '../../yoyo/discipline_app/mobile/src/fibData.ts';
const row=(symbol:string,side:'upper'|'lower',at:number,source:'live'|'history'='live'):FibContract=>({symbol,high:110,low:90,upper:122.36,lower:77.64,price:100,stale:false,touches:{[side]:{side,occurred_ms:at,source}}});
const snapshot=(items:FibContract[],week='2026-10-19'):FibSnapshot=>({week,now_ms:5000,timezone:'America/New_York',week_start_ms:1000,monday_end_ms:2000,next_week_ms:10000,monday_complete:true,connected:true,last_message_ms:5000,items});
test('five levels use monitor anchors and mirrored extension arithmetic',()=>{
 const levels=fibLevels({high:110,low:90,upper:122.36,lower:77.64,start:1,end:9});
 assert.deepEqual(levels.map(l=>l.price),[122.36,110,100,90,77.64]);
});
test('initial latest selection, stable manual selection and weekly reset',()=>{
 const next=parseFibSnapshot(snapshot([row('BTC-USDT-SWAP','upper',3000),row('ETH-USDT-SWAP','lower',4000)]));
 assert.equal(fibSelection('',undefined,next),'ETH-USDT-SWAP');
 assert.equal(fibSelection('BTC-USDT-SWAP',next.week,next),'BTC-USDT-SWAP');
 assert.equal(fibSelection('BTC-USDT-SWAP','2026-10-12',next),'ETH-USDT-SWAP');
 assert.equal(fibSelection('GONE-USDT-SWAP',next.week,next),'ETH-USDT-SWAP');
});
test('future, untouched and malformed rows never become an entry shortcut',()=>{
 const untouched={...row('BTC-USDT-SWAP','upper',3000),touches:{}};
 assert.deepEqual(parseFibSnapshot(snapshot([untouched,row('ETH-USDT-SWAP','upper',6000),row('invalid','lower',3000)])).items,[]);
 assert.throws(()=>parseFibSnapshot({...snapshot([]),timezone:'UTC'}));
});
test('direction uses newest side; history labels preserve hourly uncertainty',()=>{
 const two=row('BTC-USDT-SWAP','upper',3000);two.touches.lower={side:'lower',source:'live',occurred_ms:4000};
 assert.equal(fibSide(two),'long');assert.equal(fibSide(row('ETH-USDT-SWAP','upper',4000)),'short');
 assert.equal(latestFibTouch(two).side,'lower');
 assert.match(touchLabel({side:'upper',source:'history',occurred_ms:1000},5000),/时内/);
 assert.match(touchLabel({side:'lower',source:'live',occurred_ms:4999},5000),/刚刚/);
});


test('reference rays start at Monday close, not week open',()=>{
 const data=snapshot([row('BTC-USDT-SWAP','upper',3000)]);
 const range=fibRangeFor(data.items[0],data);
 assert.equal(range.start,data.monday_end_ms/1000);
 assert.notEqual(range.start,data.week_start_ms/1000);
 assert.equal(range.end,data.next_week_ms/1000);
});
