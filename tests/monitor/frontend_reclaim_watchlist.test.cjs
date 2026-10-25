'use strict';
const test=require('node:test'),assert=require('node:assert/strict');
const ui=require('../../yoyo/monitor/static/reclaim-watchlist.js');
const {createFeed,route}=require('../../yoyo/monitor/static/monday-reclaim-paper.js');
const coin=(symbol,x={})=>({symbol:symbol+'-USDT-SWAP',state:'ready',price:100,upper:110,lower:90,stale:false,touches:{},...x});
const position=(symbol,x={})=>({id:1,symbol:symbol+'-USDT-SWAP',taken:1,notional:100,entry:100,side:1,fill_ms:1000,...x});
const list=require('../../yoyo/monitor/static/monday-list-model.js');
const disk=()=>{const store=new Map();return {getItem:k=>store.get(k),setItem:(k,v)=>store.set(k,v)};};
const snapshot=items=>({items,now_ms:10000,week:'2026-10-19'});
test('paper list is identical to Monday policy across every filter, history and favorites',()=>{
 const data=snapshot([coin('SOL',{touches:{upper:{source:'live',observed_ms:9000}}}),coin('DOGE',{touches:{lower:{source:'history',observed_ms:10000,candle_ms:1000}}}),coin('BTC')]);
 const prefs=list.createListPreferences(disk());prefs.toggle('BTC-USDT-SWAP');
 for(const filter of Object.keys(list.FILTERS))for(const q of ['', 'btc', 'USDT'])assert.deepEqual(ui.model(data,prefs,{q,filter}).items,list.selectRows(data.items,q,filter,data.now_ms,prefs.favorites()));
 assert.equal(ui.model(data,prefs).total,2);assert.deepEqual(ui.model(data,prefs).items.map(r=>r.symbol),['SOL-USDT-SWAP','DOGE-USDT-SWAP']);
 assert.equal(ui.model(data,prefs,{filter:'favorites'}).items[0].symbol,'BTC-USDT-SWAP');
});
test('shared storage removes the same weekly touch on both pages and supports undo',()=>{
 const storage=disk(),p=list.createListPreferences(storage),other=list.createListPreferences(storage),item=coin('SOL',{touches:{upper:{source:'live',observed_ms:9000}}}),data=snapshot([item]);
 p.toggle(item.symbol);other.read();assert.equal(other.has(item.symbol),true);
 const undo=p.remove(item,data.week);other.read();assert.equal(ui.model(data,other).total,0);
 other.undo(undo);p.read();assert.equal(ui.model(data,p).total,1);
});
test('row exposes separate chart, TradingView, exact inspection, favorite and removal actions',()=>{
 const item=coin('SOL',{price:null,touches:{lower:{source:'live',observed_ms:1000}}});
 const html=ui.rowHTML(item,'',{period:'1H',favorite:true,now:5000});
 assert.match(html,/data-watch-select="SOL-USDT-SWAP"/);assert.match(html,/tradingview.com/);assert.match(html,/#yolo-detect\?tab=results&amp;symbol=SOL-USDT-SWAP&amp;timeframe=1H/);
 assert.match(html,/data-watch-action="favorite" aria-pressed="true"/);assert.match(html,/data-watch-action="remove"/);assert.match(html,/>—</);
});
test('coin identity and quote share a local button; external navigation is an explicit TV action',()=>{
 const html=ui.rowHTML(coin('SOL'), 'SOL-USDT-SWAP');
 const local=html.match(/<button[^>]*data-watch-select="SOL-USDT-SWAP"[^>]*>([\s\S]*?)<\/button>/);
 assert.ok(local);assert.match(local[1],/rp-watch-name/);assert.match(local[1],/rp-watch-quote/);
 assert.doesNotMatch(local[1],/<a\b|<button\b/);
 const links=[...html.matchAll(/<a\b([^>]*)>([\s\S]*?)<\/a>/g)];
 assert.equal(links.length,2);
 const external=links.filter(link=>/tradingview/.test(link[1]));
 assert.equal(external.length,1);assert.match(external[0][1],/class="rp-watch-tv"/);assert.equal(external[0][2],'TV ↗');
 assert.doesNotMatch(external[0][1],/rp-watch-name|data-watch-select/);
});
test('recent strip preserves visible occurrence order independently of pinned shortcuts',()=>{
 const data=snapshot(Array.from({length:8},(_,i)=>coin('C'+i,{touches:{lower:{source:'history',candle_ms:1000+i*1000,observed_ms:20000-i}}})).concat(coin('BTC')));
 const prefs=list.createListPreferences(disk());prefs.toggle('BTC-USDT-SWAP');
 assert.deepEqual(ui.recentRows(data,prefs).map(x=>x.symbol),['C7','C6','C5','C4','C3','C2','C1','C0'].map(x=>x+'-USDT-SWAP'));
 const undo=prefs.remove(data.items[7],data.week);
 assert.deepEqual(ui.recentRows(data,prefs).map(x=>x.symbol),['C6','C5','C4','C3','C2','C1','C0'].map(x=>x+'-USDT-SWAP'));
 prefs.undo(undo);assert.equal(ui.recentRows(data,prefs)[0].symbol,'C7-USDT-SWAP');
 assert.deepEqual(ui.recentRows(null,prefs),[]);
});
test('coin selection never borrows another symbol or a shadow fill and honors fill chronology',()=>{
 const old=position('SOL'),latest=position('SOL',{id:2,fill_ms:3000}),shadow=position('SOL',{id:3,fill_ms:5000,taken:0});
 assert.equal(ui.coinTrade({open:[old,shadow],closed:[latest]},'SOL-USDT-SWAP'),latest);
 assert.equal(ui.coinTrade({latest_funded:latest},'SOL-USDT-SWAP'),latest);
 assert.equal(ui.coinTrade({open:[old]},'BTC-USDT-SWAP'),null);
 assert.equal(ui.coinTrade({open:[position('SOL',{entry:null})]},'SOL-USDT-SWAP'),null);
 assert.equal(route({symbol:'BTC-USDT-SWAP'}).symbol,'BTC-USDT-SWAP');assert.equal(route({symbol:'<script>'}).symbol,'');
});
test('touch list validates symbol identities and rejects duplicate or malformed records',()=>{
 const d=snapshot([coin('SOL')]);assert.equal(ui.validate(d),d);
 for(const bad of [{},{...d,items:[null]},{...d,items:[coin('SOL'),coin('SOL')]},{...d,items:[{symbol:'<img>',state:'ready'}]}])assert.throws(()=>ui.validate(bad));
});
test('touch list feed retains its last good snapshot on schema failure and stops on leave',async()=>{
 const good=snapshot([coin('SOL')]);let next=good,reads=0,cancels=0;
 const feed=createFeed({read:async()=>{reads++;return next;},validateData:ui.validate,cancel:()=>cancels++,schedule:()=>1,unschedule(){}});
 feed.setActive(true);await feed.refresh();assert.equal(feed.getState().data,good);
 next={};await feed.refresh();assert.equal(feed.getState().data,good);assert.match(feed.getState().error,/格式/);
 feed.setActive(false);await feed.refresh();assert.equal(reads,2);assert.equal(cancels,1);
});

test('fixed shortcuts exist before data and remain unique even when touched or hidden',()=>{
 assert.deepEqual(ui.PINNED,['BTC-USDT-SWAP','ETH-USDT-SWAP','XAU-USDT-SWAP']);
 const html=ui.pinnedHTML('ETH-USDT-SWAP');
 assert.equal((html.match(/data-watch-select=/g)||[]).length,3);
 assert.match(html,/data-watch-select="ETH-USDT-SWAP" aria-pressed="true"/);
 assert.equal((html.match(/<img /g)||[]).length,3);assert.doesNotMatch(html,/<a |tradingview|XAUT|PAXG/);
 const data=snapshot(['BTC','ETH','XAU','SOL'].map(symbol=>coin(symbol,{touches:{upper:{source:'live',observed_ms:9000}}})));
 const prefs=list.createListPreferences(disk());
 assert.deepEqual(ui.recentRows(data,prefs).map(i=>i.symbol),['SOL-USDT-SWAP']);
 prefs.remove(data.items[0],data.week);assert.match(ui.pinnedHTML(''),/BTC-USDT-SWAP/);
 assert.equal(ui.recentRows(snapshot(Array.from({length:30},(_,i)=>coin('C'+i,{touches:{upper:{source:'live',observed_ms:i+1}}}))),prefs).length,20);
});
test('horizontal dragging suppresses pointer clicks but keeps taps and keyboard selection',()=>{
 const target=()=>{const handlers={};return {handlers,addEventListener:(name,fn)=>(handlers[name]??=[]).push(fn),fire(name,x={}){let stopped=false;const e={type:name,preventDefault(){this.prevented=true;},stopImmediatePropagation(){stopped=true;},...x};for(const fn of handlers[name]||[])fn(e);return {stopped,e};}};};
 const lane=target(),events=target();let time=100;
 Object.assign(lane,{scrollLeft:60,classList:{add(){},remove(){}}});
 const reset=ui.bindRecentDrag(lane,events,()=>time);
 const down={pointerId:1,pointerType:'mouse',button:0,clientX:100};
 lane.fire('pointerdown',down);events.fire('pointermove',{pointerId:1,clientX:50});
 assert.equal(lane.scrollLeft,110);events.fire('pointerup',{pointerId:1});
 assert.equal(lane.fire('click',{detail:1}).stopped,true);
 assert.equal(lane.fire('click',{detail:0}).stopped,false);
 time=900;assert.equal(lane.fire('click',{detail:1}).stopped,false);
 lane.fire('pointerdown',down);events.fire('pointermove',{pointerId:1,clientX:98});events.fire('pointerup',{pointerId:1});
 assert.equal(lane.fire('click',{detail:1}).stopped,false);
 lane.fire('pointerdown',{...down,pointerType:'touch'});events.fire('pointermove',{pointerId:1,clientX:40});
 assert.equal(lane.scrollLeft,110);events.fire('pointercancel',{pointerId:1});
 assert.equal(lane.fire('click',{detail:1}).stopped,true);reset();assert.equal(lane.fire('click',{detail:1}).stopped,false);
});
