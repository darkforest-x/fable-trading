/** Real persistence/race/TTL behavior, with a local in-memory Storage adapter. */
import {test} from 'node:test';
import assert from 'node:assert/strict';
import {DisplayCache} from '../../yoyo/discipline_app/mobile/src/readCache.ts';
class Disk {
 values=new Map<string,string>();
 get length(){return this.values.size}
 key(i:number){return [...this.values.keys()][i]??null}
 getItem(k:string){return this.values.get(k)??null}
 setItem(k:string,v:string){this.values.set(k,v)}
 removeItem(k:string){this.values.delete(k)}
}
test('warm cache survives app restart, is account-scoped and refreshes after TTL',async()=>{
 const disk=new Disk();let now=100;let calls=0;
 const first=new DisplayCache(disk,()=>now);
 const load=async()=>{calls++;return {equity:'123'}};
 await first.load('auth-a:live:primary:30',load);
 const reopened=new DisplayCache(disk,()=>now);
 assert.equal(reopened.peek<{equity:string}>('auth-a:live:primary:30')?.data.equity,'123');
 assert.equal(reopened.peek('auth-a:live:second:30'),undefined);
 assert.equal(reopened.peek('auth-b:live:primary:30'),undefined);
 assert.equal(reopened.peek('auth-a:paper:primary:30'),undefined);
 await reopened.load('auth-a:live:primary:30',load);assert.equal(calls,1);
 now+=10001;await reopened.load('auth-a:live:primary:30',load);assert.equal(calls,2);
 now+=86400001;assert.equal(reopened.peek('auth-a:live:primary:30'),undefined);
});
test('deduplicates in-flight GETs and logout prevents a late result repopulating disk',async()=>{
 const disk=new Disk(),cache=new DisplayCache(disk);let calls=0;let complete!:(value:string)=>void;
 const loader=()=>{calls++;return new Promise<string>(resolve=>complete=resolve)};
 const a=cache.load('primary',loader),b=cache.load('primary',loader);
 assert.equal(calls,1);cache.clear();complete('late private value');await Promise.all([a,b]);
 assert.equal(cache.peek('primary'),undefined);assert.equal(disk.length,0);
});
test('failure preserves last good value, corrupt/full storage is nonfatal',async()=>{
 const disk=new Disk();let now=100;const cache=new DisplayCache(disk,()=>now);
 await cache.load('primary',async()=>7);now+=10001;
 await assert.rejects(cache.load('primary',async()=>{throw Error('offline')}));
 assert.equal(cache.peek('primary')?.data,7);
 disk.setItem('spike-display-v1:bad','not JSON');assert.equal(cache.peek('bad'),undefined);
 const blocked=new DisplayCache({length:0,key:()=>null,getItem:()=>{throw Error()},setItem:()=>{throw Error()},removeItem:()=>{throw Error()}});
 assert.equal(await blocked.load('first',async()=>9),9);assert.equal(blocked.peek('first')?.data,9);
});
