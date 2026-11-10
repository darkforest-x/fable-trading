import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {monitorPath,signedPercent,priceText,tone,groupByDay,dayLabel,signalStatus,reasonText,MONITOR_PATHS,freshAlerts,parseAlertPrefs,pushStatusText,FIB_ALERT_WINDOW_MS,notificationDestination} from '../../yoyo/discipline_app/mobile/src/monitorFormat.ts';

const source=(name:string)=>readFileSync(new URL(`../../yoyo/discipline_app/mobile/src/${name}`,import.meta.url),'utf8');

test('monitor client only builds fixed read-only /v1/monitor paths with whitelisted queries',()=>{
 assert.equal(monitorPath('paper',{track:'live-wick6x3'}),'/v1/monitor/paper?track=live-wick6x3');
 assert.equal(monitorPath('alerts'),'/v1/monitor/alerts');
 assert.equal(monitorPath('cloud',{side:'short',scope:'all'}),'/v1/monitor/cloud?side=short&scope=all');
 assert.equal(monitorPath('monthFirstWeek'),'/v1/monitor/month-first-week-1618');
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

test('in-app alerts: first snapshot only seeds, then one new alarm, monthly touch or fresh 1.618 push',()=>{
 const prefs=parseAlertPrefs(null),now=1_000_000_000;
 const fib=(id:string,at:number,pushed=true)=>({id,kind:'fib' as const,side:'lower' as const,at_ms:at,pushed,symbol:'SUI-USDT-SWAP',status:pushed?'pushed' as const:'below_threshold' as const});
 const alarm=(id:string,extra={})=>({id,kind:'alarm' as const,side:'upper' as const,at_ms:now,pushed:true,n:29,symbols:[],active:true,acknowledged:false,test:false,...extra});
 const month=(id:string,extra={})=>({id,kind:'month_first_week_touch' as const,type:'month_first_week_touch' as const,side:'upper' as const,at_ms:now,occurred_ms:now,fresh_until_ms:now+90_000,pushed:false,source:'live' as const,touch_confirmed:true,month:'2026-11',event_id:'2026-11|BTC-USDT-SWAP|upper',symbol:'BTC-USDT-SWAP',...extra});
 const seeded=freshAlerts([alarm('alarm:1'),fib('fib:a',now),month('month:a')],null,now,prefs);
 assert.deepEqual(seeded.alerts,[]);assert.ok(seeded.seen.has('alarm:1')&&seeded.seen.has('fib:a')&&seeded.seen.has('month:a'));
 const next=freshAlerts([fib('fib:b',now),alarm('alarm:2'),fib('fib:c',now-60_000),alarm('alarm:1')],seeded.seen,now,prefs);
 assert.deepEqual(next.alerts.map(e=>e.id),['alarm:2']);
 assert.deepEqual(freshAlerts([fib('fib:d',now)],next.seen,now,prefs).alerts.map(e=>e.id),['fib:d']);
 const monthNext=freshAlerts([month('month:b')],next.seen,now,prefs);
 assert.deepEqual(monthNext.alerts.map(e=>e.id),['month:b']);
 assert.deepEqual(freshAlerts([month('month:history',{source:'history'})],next.seen,now,prefs).alerts,[]);
 assert.deepEqual(freshAlerts([month('month:unconfirmed',{touch_confirmed:false})],next.seen,now,prefs).alerts,[]);
 assert.deepEqual(freshAlerts([month('month:expired',{fresh_until_ms:now-1})],next.seen,now,prefs).alerts,[]);
 assert.deepEqual(freshAlerts([fib('fib:e',now-FIB_ALERT_WINDOW_MS-1)],next.seen,now,prefs).alerts,[]);
 assert.deepEqual(freshAlerts([fib('fib:f',now,false)],next.seen,now,prefs).alerts,[]);
 assert.deepEqual(freshAlerts([alarm('alarm:3',{acknowledged:true}),alarm('alarm:4',{active:false})],next.seen,now,prefs).alerts,[]);
 const off={...prefs,popup:false,fib:false,monthFirstWeekTouch:false};
 assert.deepEqual(freshAlerts([alarm('alarm:5'),fib('fib:g',now),month('month:off')],next.seen,now,off).alerts,[]);
 assert.deepEqual(parseAlertPrefs('{"sound":false}'),{popup:true,sound:false,fib:true,monthFirstWeekTouch:true});
 assert.deepEqual(parseAlertPrefs('not json'),{popup:true,sound:true,fib:true,monthFirstWeekTouch:true});
 assert.deepEqual(notificationDestination(month('month:route')),{tab:'monitor',segment:'touches',source:'month',month:'2026-11',eventId:'2026-11|BTC-USDT-SWAP|upper',symbol:'BTC-USDT-SWAP'});
 assert.equal(pushStatusText(fib('fib:h',now,false)),'未达推送阈值');
});

test('the only monitor write is the 1.618 Bark switch, isolated from the read-only client',()=>{
 const text=source('notifySettings.ts');
 assert.deepEqual([...text.matchAll(/'\/v1\/[^']+'/g)].map(m=>m[0]),["'/v1/monitor/fib-bark'"]);
 assert.match(text,/return api<NotificationSnapshot>\(connection,FIB_BARK_PATH,\{enabled\}\)/);
 for(const file of ['Monitor.tsx','monitorData.ts','monitorFormat.ts','useNotifications.ts','NotificationCenter.tsx','AlarmBanner.tsx'])
  assert.ok(!source(file).includes('fib-bark')&&!source(file).includes('/v1/orders')&&!source(file).includes('/v1/positions'),file);
});
