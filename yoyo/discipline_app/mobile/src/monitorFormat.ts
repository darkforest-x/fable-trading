/** Pure monitor helpers (no imports, unit-tested with node --test): fixed read-only paths and display text. */

export const MONITOR_PATHS={paper:'/v1/monitor/paper',alerts:'/v1/monitor/alerts',cloud:'/v1/monitor/cloud',notifications:'/v1/monitor/notifications',monthFirstWeek:'/v1/monitor/month-first-week-1618'} as const;
export type MonitorSegment=keyof typeof MONITOR_PATHS;
export type Side='long'|'short';
export type PaperAccount={key:string;label:string;group:'live'|'replica';equity:number|null;start_equity:number|null;return_pct:number|null;started_ms:number|null;stats:{closed:number|null;wins:number|null;mean_net_bp:number|null}};
export type PaperPosition={id:number|null;symbol:string;side:Side;entry:number|null;target:number|null;stage:string;funded:boolean;notional:number|null;fill_ms:number|null;score:number|null;mark?:number|null;unrealized_bp?:number|null;remaining_notional?:number|null;stop?:number|null;exit_price?:number|null;exit_ms?:number|null;reason?:string;net_bp?:number|null;pnl?:number|null};
export type PaperSignal={id:number|null;symbol:string;side:Side;level:number|null;score:number|null;status:'scored'|'excluded'|'feature_error';taken:boolean;observed_ms:number|null};
export type PaperSnapshot={now_ms:number;paper_only:true;equity_basis:'realized';accounts:PaperAccount[];detail:{key:string;open:PaperPosition[];closed:PaperPosition[]};signals:PaperSignal[]};
export type Alert={id:number|null;side:'upper'|'lower';first_ms:number|null;last_ms:number|null;n:number;symbols:string[];active:boolean;acknowledged:boolean;test:boolean};
export type AlertSnapshot={now_ms:number;threshold:number|null;window_minutes:number|null;worker_alive:boolean;alerts:Alert[]};
export type CloudSignal={id:string;symbol:string;side:Side;timeframe:string;confirmed_ms:number|null;entry:number|null;stop:number|null;stop_pct:number|null;target:number|null;planned_rr:number|null;freshness:string;historical:boolean;week:string;range:{high:number|null;low:number|null;upper:number|null;lower:number|null}};
export type CloudSnapshot={now_ms:number;version:string;reference_only:true;next_close_ms:number|null;total:number;status:{state:string;total:number|null;completed:number|null;last_scan_ms:number|null;worker_alive:boolean};items:CloudSignal[]};
export type CloudFilter={side:'all'|Side;scope:'live'|'all'};

const TRACK=/^[a-z0-9-]{1,20}$/;
/** Builds the only URLs this module may request. Throws instead of reaching any other path. */
export function monitorPath(segment:MonitorSegment,query:Record<string,string>={}){
 const path=MONITOR_PATHS[segment];
 if(!path)throw Error('未知监控模块');
 const allowed:Record<MonitorSegment,Record<string,RegExp>>={paper:{track:TRACK},alerts:{},cloud:{side:/^(all|long|short)$/,scope:/^(live|all)$/},notifications:{},monthFirstWeek:{}};
 const params=new URLSearchParams();
 for(const [key,value] of Object.entries(query)){
  const rule=allowed[segment][key];
  if(!rule||!rule.test(value))throw Error('监控查询参数无效');
  params.set(key,value);
 }
 const text=params.toString();
 return text?`${path}?${text}`:path;
}
export const coin=(symbol:string)=>symbol.replace('-USDT-SWAP','');
export function signedPercent(value:number|null|undefined,digits=2){
 if(value===null||value===undefined||!Number.isFinite(value))return '—';
 const fixed=Math.abs(value).toFixed(digits);
 return `${value>0&&Number(fixed)!==0?'+':value<0&&Number(fixed)!==0?'−':''}${fixed}%`;
}
export function tone(value:number|null|undefined){return value===null||value===undefined||!Number.isFinite(value)||value===0?'flat':value>0?'up':'down'}
/** Price text with enough significant digits for low-priced contracts. */
export function priceText(value:number|null|undefined){
 if(value===null||value===undefined||!Number.isFinite(value))return '—';
 const abs=Math.abs(value);
 const digits=abs>=1000?1:abs>=1?Math.min(4,Math.max(2,4-Math.floor(Math.log10(abs)))):Math.min(10,Math.max(4,3-Math.floor(Math.log10(abs))));
 return value.toLocaleString('en-US',{minimumFractionDigits:digits,maximumFractionDigits:digits});
}
const beijing=(ms:number,options:Intl.DateTimeFormatOptions)=>new Date(ms).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai',hour12:false,...options});
export function clockText(ms:number|null|undefined,now=Date.now()){
 if(!ms)return '—';
 const sameDay=beijing(ms,{year:'numeric',month:'2-digit',day:'2-digit'})===beijing(now,{year:'numeric',month:'2-digit',day:'2-digit'});
 return sameDay?beijing(ms,{hour:'2-digit',minute:'2-digit'}):beijing(ms,{month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit'}).replace('/','-');
}
export function dayLabel(ms:number|null|undefined,now=Date.now()){
 if(!ms)return '更早';
 const key=(t:number)=>beijing(t,{year:'numeric',month:'2-digit',day:'2-digit'});
 if(key(ms)===key(now))return '今天';
 if(key(ms)===key(now-86400000))return '昨天';
 return beijing(ms,{month:'long',day:'numeric'});
}
export function signalStatus(signal:PaperSignal){
 if(signal.status==='excluded')return '已排除';
 if(signal.status==='feature_error')return '评分失败';
 return signal.taken?'通过评分':'未通过评分';
}
// Same wording as the workbench (monday-reclaim-paper.js `reason`).
export const reasonText=(reason?:string)=>({target:'到达目标',breakeven:'保本止损',backstop:'兜底止损',reclaimed_inside_be:'收回未越过保本价',failed_reclaim:'确认未收回',manual:'手动平仓'} as Record<string,string>)[reason??'']??'已平仓';
export const stageText=(stage:string)=>({pending_confirm:'等待确认',breakeven:'已保本',open:'持仓中'} as Record<string,string>)[stage]??'持仓中';
export function groupByDay<T>(rows:T[],time:(row:T)=>number|null,now=Date.now()){
 const groups:{label:string;rows:T[]}[]=[];
 for(const row of rows){const label=dayLabel(time(row),now);const last=groups.at(-1);if(last?.label===label)last.rows.push(row);else groups.push({label,rows:[row]})}
 return groups;
}

/** In-app notifications (owner 2026-11-06: 全市场插针报警 + 1.618 触价信号; background push stays Bark). */
export type FibPushStatus='pushed'|'pushing'|'below_threshold'|'score_unavailable'|'not_sent'|'history';
export type NotificationEvent={id:string;kind:'fib'|'alarm'|'month_first_week_touch';type?:'fib'|'alarm'|'month_first_week_touch';side:'upper'|'lower';at_ms:number|null;pushed:boolean;
 symbol?:string;level?:number|null;price?:number|null;status?:FibPushStatus|string;event_id?:string;month?:string;occurred_ms?:number;candle_ms?:number;created_ms?:number;expires_ms?:number;fresh_until_ms?:number;source?:'live'|'history';touch_confirmed?:boolean;candle_confirmed?:boolean;apns_status?:'not_subscribed'|'pending'|'sent'|'failed'|'unknown'|'skipped';
 n?:number;symbols?:string[];first_ms?:number|null;active?:boolean;test?:boolean;acknowledged?:boolean};
export type NotificationSnapshot={now_ms:number;events:NotificationEvent[];channels:{
 fib:{bark:boolean;bark_configured:boolean;bark_sent:number;telegram:boolean;min_score:number|null};
 alarm:{bark:boolean;telegram:boolean;threshold:number|null;window_minutes:number|null};
 month_first_week_touch?:{bark?:boolean;bark_configured?:boolean;bark_sent?:number;apns_configured:boolean;apns_status?:{counts?:Record<string,number>;cursor:number|null}}}};
export type AlertPrefs={popup:boolean;sound:boolean;fib:boolean;monthFirstWeekTouch:boolean};
export const ALERT_PREFS_KEY='spike-alert-prefs-v1';
export function parseAlertPrefs(raw:string|null):AlertPrefs{
 const base={popup:true,sound:true,fib:true,monthFirstWeekTouch:true};
 try{const v=JSON.parse(raw??'{}');return {popup:v.popup!==false,sound:v.sound!==false,fib:v.fib!==false,monthFirstWeekTouch:v.monthFirstWeekTouch!==false}}catch{return base}
}
/** A 1.618 push older than this when first seen is history, not something to ring for. */
export const FIB_ALERT_WINDOW_MS=20*60*1000;
export const MONTH_FIRST_WEEK_TOUCH_WINDOW_MS=90*1000;
export type MonthFirstWeekTouch={id:number;event_id:string;type:'month_first_week_touch';month:string;symbol:string;side:'upper'|'lower';level:number;price:number;candle_extreme:number;occurred_ms:number;candle_ms:number;created_ms:number;expires_ms:number;fresh_until_ms:number;source:'live'|'history';touch_confirmed:boolean;candle_confirmed:boolean};
export type MonthFirstWeekSnapshot={schema:'month_first_week_1618.v1';now_ms:number;timezone:string;month:string;window:{month:string;start_ms:number;ready_ms:number|null;expires_ms:number;complete:boolean};coverage:{universe_size:number;ready_symbols:number;incomplete_symbols:number;pending_symbols:number;expected_1h_bars:number|null;source:string|null};stream:{connected:boolean;last_message_ms:number|null;error:string|null};worker:{alive:boolean;last_cycle_ms:number|null;error:string|null;symbol_count:number;ready_count:number;incomplete_count:number;pending_count:number};items:{symbol:string;state:string;high:number|null;low:number|null;upper:number|null;lower:number|null;updated_ms:number|null;history_gaps?:unknown}[];touches:MonthFirstWeekTouch[]};
/** Which events should raise an in-app alert. The first snapshot after connecting only seeds `seen`. */
export function freshAlerts(events:NotificationEvent[],seen:Set<string>|null,now:number,prefs:AlertPrefs){
 const next=new Set(seen??[]);const alerts:NotificationEvent[]=[];
 for(const e of events){
  if(next.has(e.id))continue;next.add(e.id);
  if(!seen)continue;
  if(e.kind==='alarm'&&prefs.popup&&e.active&&!e.acknowledged)alerts.push(e);
  if(e.kind==='fib'&&prefs.fib&&e.pushed&&e.at_ms!==null&&now-e.at_ms<=FIB_ALERT_WINDOW_MS)alerts.push(e);
  if(e.kind==='month_first_week_touch'&&prefs.monthFirstWeekTouch&&e.source==='live'&&e.touch_confirmed===true&&e.occurred_ms!==undefined&&e.fresh_until_ms!==undefined&&now>=e.occurred_ms&&now<=e.fresh_until_ms&&e.fresh_until_ms-e.occurred_ms<=MONTH_FIRST_WEEK_TOUCH_WINDOW_MS)alerts.push(e);
 }
 // Never stack a burst: the newest alarm first, otherwise the newest 1.618 push.
 const alarm=alerts.find(e=>e.kind==='alarm'),monthTouch=alerts.find(e=>e.kind==='month_first_week_touch'),fib=alerts.find(e=>e.kind==='fib');
 return {alerts:alarm?[alarm]:monthTouch?[monthTouch]:fib?[fib]:[],seen:next};
}
export function pushStatusText(e:NotificationEvent){
 if(e.kind==='alarm')return e.test?'测试':'已推送';
 if(e.kind==='month_first_week_touch')return ({sent:'APNs 已接受',pending:'APNs 排队中',failed:'APNs 拒绝',unknown:'APNs 结果未知',skipped:'APNs 未发送',not_subscribed:'未订阅'} as Record<string,string>)[e.apns_status??'not_subscribed']??'APNs 未发送';
 return ({pushed:'已推送',pushing:'推送中',below_threshold:'未达推送阈值',score_unavailable:'评分不可用',not_sent:'未推送',history:'历史回补'} as Record<string,string>)[e.status??'not_sent']??'未推送';
}
export const edgeText=(side:'upper'|'lower')=>side==='upper'?'上沿 1.618':'下沿 1.618';
export function notificationDestination(event:NotificationEvent){
 if(event.kind==='month_first_week_touch')return {tab:'monitor' as const,segment:'touches' as const,source:'month' as const,month:event.month??'',eventId:event.event_id??event.id.replace(/^month_first_week_touch:/,''),symbol:event.symbol};
 if(event.kind==='alarm')return {tab:'monitor' as const,segment:'alerts' as const};
 return {tab:'fib' as const,symbol:event.symbol};
}
