/** Pure monitor helpers (no imports, unit-tested with node --test): fixed read-only paths and display text. */

export const MONITOR_PATHS={paper:'/v1/monitor/paper',alerts:'/v1/monitor/alerts',cloud:'/v1/monitor/cloud'} as const;
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
 const allowed:Record<MonitorSegment,Record<string,RegExp>>={paper:{track:TRACK},alerts:{},cloud:{side:/^(all|long|short)$/,scope:/^(live|all)$/}};
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
