/** Public monitor facts. Wick defaults are separately read from closed server candles. */
export type FibTouch={side:'upper'|'lower';source:'live'|'history';occurred_ms:number};
export type FibContract={symbol:string;high:number;low:number;upper:number;lower:number;price:number|null;stale:boolean;touches:Partial<Record<'upper'|'lower',FibTouch>>};
export type FibSnapshot={now_ms:number;week:string;timezone:string;week_start_ms:number;monday_end_ms:number;next_week_ms:number;monday_complete:boolean;connected:boolean;last_message_ms:number|null;items:FibContract[]};
export type FibRange={high:number;low:number;upper:number;lower:number;start:number;end:number};
/** Levels only become drawable after the NY Monday range has closed. */
export function fibRangeFor(row:FibContract,data:FibSnapshot):FibRange{
 return {high:row.high,low:row.low,upper:row.upper,lower:row.lower,start:data.monday_end_ms/1000,end:data.next_week_ms/1000};
}
export function latestFibTouch(row:FibContract):FibTouch {
 return Object.values(row.touches).sort((a,b)=>b.occurred_ms-a.occurred_ms||a.side.localeCompare(b.side))[0];
}
export function fibSide(row:FibContract){return latestFibTouch(row).side==='upper'?'short':'long'}
export function fibLevels(range:FibRange){return [
 {price:range.upper,title:'1.618',color:'#087f69'},
 {price:range.high,title:'高点',color:'#627d93'},
 {price:(range.high+range.low)/2,title:'',color:'#c5cdd0'},
 {price:range.low,title:'低点',color:'#627d93'},
 {price:range.lower,title:'1.618',color:'#e14f70'},
].filter(x=>x.price>0)}
export function fibSelection(previous:string,previousWeek:string|undefined,next:FibSnapshot){
 return previousWeek===next.week&&next.items.some(r=>r.symbol===previous)?previous:next.items[0]?.symbol??'';
}
export function touchLabel(touch:FibTouch,now:number){
 const direction=touch.side==='upper'?'上触':'下触';
 if(touch.source==='history')return `${direction} · ${new Intl.DateTimeFormat('zh-CN',{timeZone:'Asia/Shanghai',month:'2-digit',day:'2-digit',hour:'2-digit',hour12:false}).format(touch.occurred_ms)}时内`;
 const minutes=Math.max(0,Math.floor((now-touch.occurred_ms)/60000));
 return `${direction} · ${minutes<1?'刚刚':minutes<60?minutes+'分钟前':minutes<1440?Math.floor(minutes/60)+'小时前':Math.floor(minutes/1440)+'天前'}`;
}
export function parseFibSnapshot(raw:FibSnapshot):FibSnapshot{
 if(!raw||raw.timezone!=='America/New_York'||!/^\d{4}-\d{2}-\d{2}$/.test(raw.week)||!Number.isFinite(raw.now_ms)||!Array.isArray(raw.items)||!(raw.week_start_ms<raw.monday_end_ms&&raw.monday_end_ms<raw.next_week_ms))throw Error('监控数据格式异常');
 const items=raw.items.filter(row=>row&&/^[A-Z0-9]{1,40}-USDT-SWAP$/.test(row.symbol)&&[row.high,row.low,row.upper,row.lower].every(Number.isFinite)&&row.high>row.low&&row.low>0&&row.touches&&Object.values(row.touches).length>0&&Object.entries(row.touches).every(([side,touch])=>['upper','lower'].includes(side)&&touch.side===side&&['live','history'].includes(touch.source)&&Number.isFinite(touch.occurred_ms)&&touch.occurred_ms>=raw.week_start_ms&&touch.occurred_ms<=raw.now_ms));
 return {...raw,items:items.sort((a,b)=>latestFibTouch(b).occurred_ms-latestFibTouch(a).occurred_ms||a.symbol.localeCompare(b.symbol))};
}
