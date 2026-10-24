/** Pure replay formatting and input parsing helpers. Timestamps from the replay
 * API are UTC seconds; user-entered replay starts are always Beijing time.
 */
export const REPLAY_PERIODS = ['1m', '5m', '15m', '1H', '4H'] as const;
export type ReplayPeriod = typeof REPLAY_PERIODS[number];
export type ReplaySourceId = 'okx_local' | 'okx_online';
export type ReplayBar = {time:number|string;open:number|string;high:number|string;low:number|string;close:number|string;volume:number|string};
export type ReplayRange = {state:'ready'|'developing'|'missing_monday';week:string;start:number;end:number;expires:number;high?:number|string;low?:number|string;upper?:number|string;lower?:number|string};
export type ReplayPosition = {id:string;side:'long'|'short';entry:number|string;quantity:number|string;remaining:number|string;notional:number|string;stop?:number|string;target?:number|string;opened:number;unrealized:number|string};
export type ReplayOrder = {id:string;side:'long'|'short';price:number|string;notional:number|string;status:string;tag?:string;expires?:number};
export type ReplayClosedTrade = {id:string;side:'long'|'short';entry:number|string;exit:number|string;opened:number;closed:number;net_pnl:number|string;fees:number|string};
export type ReplayFill = {id:string;position_id:string;side:'long'|'short';price:number|string;quantity:number|string;fee:number|string;role:string;time:number;reason:string};
export type ReplayStatistics = {trades:number;wins:number;losses:number;win_rate:number|null;payoff:number|null;profit_factor:number|null;net_pnl:number|string;return_pct:number|string;max_drawdown:number|string;max_drawdown_pct:number|string;longest_loss_streak:number};
export type ReplaySnapshot = {id:string;symbol:string;period:ReplayPeriod;source:ReplaySourceId;initial_cash:number|string;requested_start:number;clock:number;version:number;bars:ReplayBar[];range:ReplayRange;cash:number|string;equity:number|string;available:number|string;fees:number|string;price:number|string;positions:ReplayPosition[];orders:ReplayOrder[];closed:ReplayClosedTrade[];fills:ReplayFill[];statistics:ReplayStatistics;notice?:string|null};
export type ReplayCatalog = {periods:ReplayPeriod[];sources:{id:ReplaySourceId;label:string}[];local:{symbol:string;period:ReplayPeriod}[];warning?:string};
export type ReplaySessionRow = {id:string;symbol:string;period:ReplayPeriod;source:ReplaySourceId;clock:number;initial_cash:number|string;version:number};

const numericValue = (value:unknown):number|null => {
 if(typeof value==='number')return Number.isFinite(value)?value:null;
 if(typeof value==='string'&&value.trim()!==''){const number=Number(value);return Number.isFinite(number)?number:null}
 return null;
};

export function replayNumber(value:unknown,fallback=0):number {
 return numericValue(value)??fallback;
}

export function isPendingReplayOrder(order:ReplayOrder):boolean {
 return order.status==='open'||order.status==='submitted'||order.status==='pending';
}

export function isCancellableReplayOrder(order:ReplayOrder):boolean {
 // The native simulator accepts cancellation only while a passive entry is open.
 return order.status==='open';
}

export function replayOrderStatusLabel(status:string):string {
 const labels:Record<string,string>={open:'待成交',pending:'待成交',submitted:'处理中',filled:'已成交',canceled:'已撤销',cancelled:'已撤销',expired:'已过期',rejected:'已拒绝'};
 return labels[status]??'已结束';
}

export function replayFillRoleLabel(role:string):string {
 const labels:Record<string,string>={entry:'入场',manual_close:'手动平仓',stop:'止损',target:'止盈'};
 return labels[role]??'成交';
}

export function replayFillReasonLabel(reason:string):string {
 const labels:Record<string,string>={entry:'开仓',manual:'手动平仓',manual_close:'手动平仓',stop:'止损触发',target:'止盈触发'};
 return labels[reason]??'成交';
}

export function formatReplayValue(value:unknown,digits=2):string {
 const number=numericValue(value);
 return number===null?'—':number.toLocaleString('en-US',{maximumFractionDigits:digits,minimumFractionDigits:digits});
}

export function formatReplayPrice(value:unknown):string {
 const number=numericValue(value);
 if(number===null)return '—';
 const digits=Math.min(8,Math.max(2,6-Math.floor(Math.log10(Math.abs(number)||1))));
 return number.toLocaleString('en-US',{maximumFractionDigits:digits,minimumFractionDigits:digits});
}

export function formatReplayTime(seconds:unknown,options:Intl.DateTimeFormatOptions={}):string {
 const value=numericValue(seconds);
 if(value===null||value<=0)return '—';
 return new Intl.DateTimeFormat('zh-CN',{timeZone:'Asia/Shanghai',hour12:false,month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',...options}).format(value*1000);
}

/** Convert explicit Beijing wall-clock inputs to UTC seconds without consulting
 * the device's local timezone. Date and time values are HTML input strings.
 */
export function beijingStartToUtcSeconds(date:string,time:string):number {
 const dateMatch=/^(\d{4})-(\d{2})-(\d{2})$/.exec(date);
 const timeMatch=/^(\d{2}):(\d{2})$/.exec(time);
 if(!dateMatch||!timeMatch)throw new Error('请选择有效的北京时间');
 const [,yearText,monthText,dayText]=dateMatch;
 const [,hourText,minuteText]=timeMatch;
 const year=Number(yearText),month=Number(monthText),day=Number(dayText),hour=Number(hourText),minute=Number(minuteText);
 const check=new Date(Date.UTC(year,month-1,day));
 if(check.getUTCFullYear()!==year||check.getUTCMonth()!==month-1||check.getUTCDate()!==day||hour>23||minute>59)throw new Error('请选择有效的北京时间');
 return Date.UTC(year,month-1,day,hour-8,minute,0)/1000;
}

export function currentBeijingInputs(now=Date.now()):{date:string;time:string} {
 const shifted=new Date(now+8*60*60*1000).toISOString().slice(0,16);
 return {date:shifted.slice(0,10),time:shifted.slice(11,16)};
}

export function normalizeReplaySymbol(value:string):string {
 const input=value.trim().toUpperCase();
 if(!input)throw new Error('请输入合约代码');
 const base=input.endsWith('-USDT-SWAP')?input.slice(0,-10):input.endsWith('-USDT')?input.slice(0,-5):input;
 if(!/^[A-Z0-9]+$/.test(base))throw new Error('合约代码仅支持字母和数字，例如 BTC-USDT-SWAP');
 return `${base}-USDT-SWAP`;
}

export function validReplayCash(value:string):boolean {
 const amount=Number(value.trim());
 return value.trim()!==''&&Number.isFinite(amount)&&amount>0;
}

export function replayBarsForChart(bars:ReplayBar[],clock:number):{time:number;open:number;high:number;low:number;close:number;volume:number}[] {
 if(!Array.isArray(bars))return [];
 return bars.flatMap(bar=>{
  const row={time:numericValue(bar.time),open:numericValue(bar.open),high:numericValue(bar.high),low:numericValue(bar.low),close:numericValue(bar.close),volume:numericValue(bar.volume)};
  if(Object.values(row).some(value=>value===null)||row.time!>clock)return [];
  return [{time:row.time!,open:row.open!,high:row.high!,low:row.low!,close:row.close!,volume:row.volume!}];
 }).sort((left,right)=>left.time-right.time);
}
