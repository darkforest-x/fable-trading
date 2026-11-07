/** Six moving averages for the 1.618 chart, as the workbench (chart-style.js: SMA / EMA 20 · 60 · 120).
 * Computed from closed + live candles already on screen; display only, never an order input.
 * Colours follow the app palette (blue / amber / grey); SMA solid, EMA dashed. */
export type Bar={time:number;close:number};
export const AVERAGES=[
 {key:'sma20',kind:'sma',period:20,color:'#0a84ff',dashed:false},
 {key:'sma60',kind:'sma',period:60,color:'#ff9f0a',dashed:false},
 {key:'sma120',kind:'sma',period:120,color:'#8e8e93',dashed:false},
 {key:'ema20',kind:'ema',period:20,color:'#0a84ff',dashed:true},
 {key:'ema60',kind:'ema',period:60,color:'#ff9f0a',dashed:true},
 {key:'ema120',kind:'ema',period:120,color:'#8e8e93',dashed:true},
] as const;
export function sma(bars:Bar[],period:number){
 const out:{time:number;value:number}[]=[];let sum=0;
 bars.forEach((bar,i)=>{sum+=bar.close;if(i>=period)sum-=bars[i-period].close;if(i>=period-1)out.push({time:bar.time,value:sum/period})});
 return out;
}
/** EMA seeded with the SMA of the first `period` closes, the usual TradingView convention. */
export function ema(bars:Bar[],period:number){
 const out:{time:number;value:number}[]=[];if(bars.length<period)return out;
 const k=2/(period+1);let value=bars.slice(0,period).reduce((s,b)=>s+b.close,0)/period;
 out.push({time:bars[period-1].time,value});
 for(let i=period;i<bars.length;i++){value=bars[i].close*k+value*(1-k);out.push({time:bars[i].time,value})}
 return out;
}
