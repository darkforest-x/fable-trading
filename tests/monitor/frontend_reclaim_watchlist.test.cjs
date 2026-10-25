'use strict';
const test=require('node:test'),assert=require('node:assert/strict');
const ui=require('../../yoyo/monitor/static/reclaim-watchlist.js');
const {createFeed,route}=require('../../yoyo/monitor/static/monday-reclaim-paper.js');
const coin=(symbol,x={})=>({symbol:symbol+'-USDT-SWAP',state:'ready',price:100,upper:110,lower:90,stale:false,touches:{},...x});
const position=(symbol,x={})=>({id:1,symbol:symbol+'-USDT-SWAP',taken:1,notional:100,entry:100,side:1,fill_ms:1000,...x});
test('universe includes untraded contracts while holding filter belongs to the selected funded track',()=>{
 const data={items:[coin('SOL'),coin('BTC'),coin('DOGE',{touches:{lower:{candle_ms:1}}})]},track={open:[position('SOL'),position('DOGE',{taken:0,notional:0})]};
 const all=ui.model(data,track);assert.equal(all.total,3);assert.equal(all.holding,1);assert.equal(all.touched,1);
 assert.deepEqual(ui.model(data,track,{filter:'holding'}).items.map(i=>i.symbol),['SOL-USDT-SWAP']);
 assert.deepEqual(ui.model(data,track,{filter:'touched'}).items.map(i=>i.symbol),['DOGE-USDT-SWAP']);
 assert.equal(ui.model(data,{open:[]},{filter:'holding'}).items.length,0);
 assert.equal(ui.model(data,track,{q:' btc '}).items[0].observe,true);
});
test('distance sorting preserves exact touches and moves stale/missing quotes last without inventing prices',()=>{
 const data={items:[coin('STALE',{stale:true,upper:100}),coin('FAR'),coin('TOUCH',{upper:100}),coin('NONE',{price:null}),coin('MISSING',{state:'missing_monday',upper:100}),coin('NEAR',{upper:101})]};
 const items=ui.model(data,{}).items;assert.deepEqual(items.slice(0,3).map(i=>i.symbol),['TOUCH-USDT-SWAP','NEAR-USDT-SWAP','FAR-USDT-SWAP']);assert.equal(items[0].nearest.distance,0);
 for(const value of [null,0,-1,NaN,Infinity,'100'])assert.equal(ui.nearest(coin('BAD',{price:value})),null);
 assert.equal(ui.nearest(coin('BAD',{upper:null,lower:null})),null);
 assert.match(ui.rowHTML(ui.model({items:[coin('NONE',{price:null})]},{}).items[0],''),/>—</);
 assert.match(ui.rowHTML(ui.model({items:[coin('BTC')]},{}).items[0],''),/仅观察/);
 assert.equal(data.items[0].symbol,'STALE-USDT-SWAP');
});
test('coin selection never borrows another symbol or a shadow fill and honors fill chronology',()=>{
 const old=position('SOL'),latest=position('SOL',{id:2,fill_ms:3000}),shadow=position('SOL',{id:3,fill_ms:5000,taken:0});
 assert.equal(ui.coinTrade({open:[old,shadow],closed:[latest]},'SOL-USDT-SWAP'),latest);
 assert.equal(ui.coinTrade({latest_funded:latest},'SOL-USDT-SWAP'),latest);
 assert.equal(ui.coinTrade({open:[old]},'BTC-USDT-SWAP'),null);
 assert.equal(ui.coinTrade({open:[position('SOL',{entry:null})]},'SOL-USDT-SWAP'),null);
 assert.equal(route({symbol:'BTC-USDT-SWAP'}).symbol,'BTC-USDT-SWAP');assert.equal(route({symbol:'<script>'}).symbol,'');
});
test('universe validates symbol identities and rejects duplicate or malformed records',()=>{
 const d={now_ms:1000,items:[coin('SOL')]};assert.equal(ui.validate(d),d);
 for(const bad of [{},{...d,items:[null]},{...d,items:[coin('SOL'),coin('SOL')]},{...d,items:[{symbol:'<img>',state:'ready'}]}])assert.throws(()=>ui.validate(bad));
});
test('universe feed retains its last good snapshot on schema failure and stops on leave',async()=>{
 const good={now_ms:1000,items:[coin('SOL')]};let next=good,reads=0,cancels=0;
 const feed=createFeed({read:async()=>{reads++;return next;},validateData:ui.validate,cancel:()=>cancels++,schedule:()=>1,unschedule(){}});
 feed.setActive(true);await feed.refresh();assert.equal(feed.getState().data,good);
 next={};await feed.refresh();assert.equal(feed.getState().data,good);assert.match(feed.getState().error,/格式/);
 feed.setActive(false);await feed.refresh();assert.equal(reads,2);assert.equal(cancels,1);
});
