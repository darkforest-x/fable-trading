import {test} from 'node:test';
import assert from 'node:assert/strict';
import {createQuoteFeed} from '../../yoyo/discipline_app/mobile/src/quoteFeed.ts';
const symbol='BTC-USDT-SWAP';
function harness(t:any){
 t.mock.timers.enable({apis:['setTimeout','setInterval','Date'],now:1700000000000});
 const sockets:any[]=[],published:any[]=[];
 const connect=()=>{const socket={onopen:null,onmessage:null,onerror:null,onclose:null,readyState:1,sent:[] as string[],closed:0,send(s:string){this.sent.push(s)},close(){this.closed++}};sockets.push(socket);return socket};
 const feed=createQuoteFeed([symbol],q=>published.push(q),connect);
 const row=(offset=0)=>({instId:symbol,last:'100',bidPx:'99',askPx:'101',ts:String(Date.now()+offset),sodUtc8:'98'});
 const send=(data:any,socket=sockets.at(-1))=>socket.onmessage?.({data:JSON.stringify({data})});
 t.after(()=>feed.stop());return {feed,sockets,published,row,send};
}
test('ticker bursts batch to one render and discard unrelated, invalid or out-of-order rows',t=>{
 const h=harness(t);h.feed.start();h.sockets[0].onopen();
 for(let i=0;i<100;i++)h.send([h.row(i)]);
 h.send([{...h.row(200),instId:'ETH-USDT-SWAP'},{...h.row(200),askPx:'1'},h.row(-1)]);
 assert.equal(h.published.length,0);t.mock.timers.tick(100);
 assert.equal(h.published.length,1);assert.equal(h.published[0][symbol].time,1700000000.099);
 h.send([h.row(-1)]);t.mock.timers.tick(100);assert.equal(h.published.length,1);
});
test('hidden/unmounted feed clears timers and fences queued callbacks from old socket',t=>{
 const h=harness(t);h.feed.start();const old=h.sockets[0];old.onopen();h.send([h.row()]);
 const lateMessage=old.onmessage,lateOpen=old.onopen,lateClose=old.onclose;
 h.feed.stop();lateMessage({data:JSON.stringify({data:[h.row()]})});lateOpen();lateClose();t.mock.timers.tick(60000);
 assert.equal(old.closed,1);assert.equal(h.sockets.length,1);assert.equal(h.published.length,0);assert.equal(old.onmessage,null);
 h.feed.start();assert.equal(h.sockets.length,2);h.sockets[1].onopen();h.send([h.row()]);t.mock.timers.tick(100);assert.equal(h.published.length,1);
});
test('pongs do not keep old prices fresh or trigger renders',t=>{
 const h=harness(t);h.feed.start();h.sockets[0].onopen();h.send([h.row()]);t.mock.timers.tick(100);
 for(let i=0;i<6;i++){h.sockets[0].onmessage({data:'pong'});t.mock.timers.tick(2000)}
 assert.equal(h.published.length,2);assert.deepEqual(h.published[1],{});
});
test('a socket stuck connecting is retried with a bounded delay',t=>{
 const h=harness(t);h.feed.start();t.mock.timers.tick(8000);assert.equal(h.sockets[0].closed,1);
 t.mock.timers.tick(1000);assert.equal(h.sockets.length,2);
});
test('construction failures back off and stop does not reconnect',t=>{
 t.mock.timers.enable({apis:['setTimeout','setInterval']});let attempts=0;
 const feed=createQuoteFeed([symbol],()=>{},()=>{attempts++;throw Error('offline')});
 feed.start();assert.equal(attempts,1);t.mock.timers.tick(1000);assert.equal(attempts,2);t.mock.timers.tick(1999);assert.equal(attempts,2);
 feed.stop();t.mock.timers.tick(60000);assert.equal(attempts,2);
});
