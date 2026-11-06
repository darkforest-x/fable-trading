import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {monitorPath,signedPercent,priceText,tone,groupByDay,dayLabel,signalStatus,reasonText,MONITOR_PATHS} from '../../yoyo/discipline_app/mobile/src/monitorFormat.ts';

const source=(name:string)=>readFileSync(new URL(`../../yoyo/discipline_app/mobile/src/${name}`,import.meta.url),'utf8');

test('monitor client only builds fixed read-only /v1/monitor paths with whitelisted queries',()=>{
 assert.equal(monitorPath('paper',{track:'live-wick6x3'}),'/v1/monitor/paper?track=live-wick6x3');
 assert.equal(monitorPath('alerts'),'/v1/monitor/alerts');
 assert.equal(monitorPath('cloud',{side:'short',scope:'all'}),'/v1/monitor/cloud?side=short&scope=all');
 assert.ok(Object.values(MONITOR_PATHS).every(p=>p.startsWith('/v1/monitor/')));
 for(const [segment,query] of [['paper',{track:'../orders'}],['paper',{symbol:'BTC'}],['alerts',{ack:'1'}],['cloud',{side:'up'}],['cloud',{scope:'history'}]] as const)
  assert.throws(()=>monitorPath(segment as never,query as never));
 assert.throws(()=>monitorPath('orders' as never));
});

test('monitor UI cannot reach order, close, stop, account or alarm-ack endpoints',()=>{
 for(const file of ['Monitor.tsx','monitorData.ts','monitorFormat.ts']){
  const text=source(file);
  for(const forbidden of ['/v1/orders','/v1/order-requests','/v1/positions','/v1/preview','/v1/auto-stop','/v1/accounts','confirmed_live','/ack','/api/','fetch(','CapacitorHttp',"method:'POST'"])
   assert.ok(!text.includes(forbidden),`${file} must not contain ${forbidden}`);
 }
 // Monitor.tsx talks to the service only through monitorGet, which never passes a body (GET).
 assert.ok(!/import\s*\{[^}]*\bapi\b[^}]*\}\s*from '\.\/api'/.test(source('Monitor.tsx')));
 assert.match(source('monitorData.ts'),/return api<T>\(connection,monitorPath\(segment,query\),undefined,signal\)/);
});

test('display helpers keep sign, precision and Beijing day grouping honest',()=>{
 assert.equal(signedPercent(1.603),'+1.60%');
 assert.equal(signedPercent(-3.98),'−3.98%');
 assert.equal(signedPercent(0.001),'0.00%');
 assert.equal(signedPercent(null),'—');
 assert.equal(tone(-1),'down');assert.equal(tone(0),'flat');assert.equal(tone(2),'up');assert.equal(tone(null),'flat');
 assert.equal(priceText(104253.44),'104,253.4');
 assert.equal(priceText(0.0012076838),'0.001208');
 assert.equal(priceText(1.2),'1.2000');assert.equal(priceText(50.5),'50.500');
 const now=Date.UTC(2026,10,6,9,0); // 17:00 Beijing
 assert.equal(dayLabel(now-3600_000,now),'今天');
 assert.equal(dayLabel(now-24*3600_000,now),'昨天');
 const groups=groupByDay([{t:now},{t:now-60_000},{t:now-24*3600_000}],r=>r.t,now);
 assert.deepEqual(groups.map(g=>[g.label,g.rows.length]),[['今天',2],['昨天',1]]);
 assert.equal(signalStatus({id:1,symbol:'A-USDT-SWAP',side:'long',level:1,score:.6,status:'scored',taken:true,observed_ms:1}),'通过评分');
 assert.equal(reasonText('failed_reclaim'),'确认未收回');
 assert.equal(reasonText('unexpected'),'已平仓');
});
