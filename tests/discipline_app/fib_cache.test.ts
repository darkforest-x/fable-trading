import test from 'node:test';
import assert from 'node:assert/strict';
import {FibCache,currentNYWeek} from '../../yoyo/discipline_app/mobile/src/fibCache.ts';
import type {FibSnapshot} from '../../yoyo/discipline_app/mobile/src/fibData.ts';
const at=Date.parse('2026-10-23T16:00:00Z');
const data:FibSnapshot={week:'2026-10-19',now_ms:at,timezone:'America/New_York',week_start_ms:Date.parse('2026-10-19T04:00Z'),monday_end_ms:Date.parse('2026-10-20T04:00Z'),next_week_ms:Date.parse('2026-10-26T04:00Z'),monday_complete:true,connected:true,last_message_ms:at,items:[{symbol:'ETH-USDT-SWAP',high:110,low:90,upper:122.36,lower:77.64,price:100,stale:false,touches:{lower:{side:'lower',source:'live',occurred_ms:at-1000}}}]};
function disk(){const values=new Map<string,string>();return {getItem:(key:string)=>values.get(key)??null,setItem:(key:string,value:string)=>{values.set(key,value)},values}}
test('cached public chart restores symbol and period without claiming live connectivity',()=>{
 const storage=disk(),cache=new FibCache(storage,'test',()=>at);cache.write(data,'ETH-USDT-SWAP','4H');
 const saved=cache.read()!;assert.equal(saved.symbol,'ETH-USDT-SWAP');assert.equal(saved.period,'4H');assert.equal(saved.data.connected,false);assert.equal(saved.data.last_message_ms,null);
 assert.equal(new FibCache(storage,'other',()=>at).read(),null);
});
test('cache invalidates exactly at New York week rollover, including DST',()=>{
 const storage=disk();new FibCache(storage,'test',()=>at).write(data,'ETH-USDT-SWAP','15m');
 assert.ok(new FibCache(storage,'test',()=>data.next_week_ms-1).read());assert.equal(new FibCache(storage,'test',()=>data.next_week_ms).read(),null);
 assert.equal(currentNYWeek(Date.parse('2026-11-02T04:59:59Z')),'2026-10-26');
 assert.equal(currentNYWeek(Date.parse('2026-11-02T05:00:00Z')),'2026-11-02');
 assert.equal(currentNYWeek(Date.parse('2026-03-09T03:59:59Z')),'2026-03-02');
 assert.equal(currentNYWeek(Date.parse('2026-03-09T04:00:00Z')),'2026-03-09');
});
test('broken, future and denied storage never invent a usable cache',()=>{
 const storage=disk(),cache=new FibCache(storage,'test',()=>at);cache.write(data,'ETH-USDT-SWAP','15m');
 assert.equal(new FibCache(storage,'test',()=>at-1).read(),null);
 storage.setItem('spike-fib-display-v1:test','{"data":null}');assert.equal(cache.read(),null);
 const denied=new FibCache({getItem(){throw Error('denied')},setItem(){throw Error('full')}},'test',()=>at);
 assert.equal(denied.read(),null);assert.doesNotThrow(()=>denied.write(data,'','15m'));
});
