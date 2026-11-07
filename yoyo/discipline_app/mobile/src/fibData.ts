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
// Workbench replica (monday-fib-chart.js levels, owner 2026-11-06 “直接复刻”) in the app palette:
// upper 1.618 red / lower 1.618 green (2 px), Monday high/low blue; 内部线 adds 0.705 / 0.618 / 0.5.
// Inlined colours so node tests import this file directly.
export type FibLevel={price:number;title:string;color:string;width:number;dashed:boolean};
export function fibLevels(range:FibRange,internal=false):FibLevel[]{
 const h=range.high,l=range.low,r=h-l,inner='rgba(10,132,255,.5)';
 const rows:FibLevel[]=[
  {price:range.upper,title:'上 1.618',color:'#ff453a',width:2,dashed:false},
  {price:h,title:'周一高',color:'#0a84ff',width:1,dashed:false},
  ...(internal?[
   {price:l+.705*r,title:'上 0.705',color:inner,width:1,dashed:false},
   {price:l+.618*r,title:'上 0.618',color:inner,width:1,dashed:false},
   {price:l+.5*r,title:'中点 0.5',color:'#8e8e93',width:1,dashed:true},
   {price:h-.618*r,title:'下 0.618',color:inner,width:1,dashed:false},
   {price:h-.705*r,title:'下 0.705',color:inner,width:1,dashed:false},
  ]:[]),
  {price:l,title:'周一低',color:'#0a84ff',width:1,dashed:false},
  {price:range.lower,title:'下 1.618',color:'#30d158',width:2,dashed:false},
 ];
 return rows.filter(x=>x.price>0);
}
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
