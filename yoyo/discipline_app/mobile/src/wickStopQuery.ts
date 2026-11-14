export type WickStopSource='week'|'month';
export type WickStopResult={stop_loss:string;first_touch_ms?:number;timeframe?:string;week?:string;month?:string};

/** Mobile 1.618 quick entry uses the 1m first-touch-to-recovery extreme for its wick stop. */
export function wickStopQuery(symbol:string,side:'long'|'short',source:WickStopSource){
 return new URLSearchParams({symbol,period:'1m',side,source}).toString();
}

export function wickStopValue(result:WickStopResult,source:WickStopSource,periodKey:string){
 const returnedPeriod=source==='week'?result.week:result.month;
 if(typeof result.first_touch_ms!=='number'||!Number.isFinite(result.first_touch_ms)||result.first_touch_ms<=0||result.timeframe!=='1m'||returnedPeriod!==periodKey)
  throw Error('1.618 首触周期已变化，请重新打开当前信号后重试');
 if(typeof result.stop_loss!=='string'||!Number.isFinite(Number(result.stop_loss))||Number(result.stop_loss)<=0)
  throw Error('插针止损暂不可用，请重试或手动填写');
 return result.stop_loss;
}
