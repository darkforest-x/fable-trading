/** Comparable USDT activity; missing metrics follow valid zero, without coin pins. */
import type {Quote} from './api';
export type ContractSort='volume'|'gainers'|'losers';
export function dayChange(q:Quote,now:number){return q.today_change!=null&&Number.isFinite(Number(q.today_change))&&now-q.time>=0&&now-q.time<=15&&Math.floor((now+28800)/86400)===Math.floor((q.time+28800)/86400)?Number(q.today_change):null}
export function turnover(q:Quote|undefined):number|null{
 const value=q?.volume24h_usdt;
 if(value==null||value.trim()==='')return null;
 const number=Number(value);
 return Number.isFinite(number)&&number>=0?number:null;
}
export function rankingSnapshot(quotes:Record<string,Quote>,now:number,sort:ContractSort):Record<string,number|null>{
 return Object.fromEntries(Object.entries(quotes).map(([symbol,q])=>[symbol,sort==='volume'?turnover(q):dayChange(q,now)]));
}
export function compareContracts(a:string,b:string,values:Record<string,number|null>,sort:ContractSort){
 const x=values[a],y=values[b];
 if(x==null)return y==null?a.localeCompare(b):1;
 if(y==null)return -1;
 return (sort==='losers'?x-y:y-x)||a.localeCompare(b);
}
export function turnoverLabel(value:number|null){
 if(value===null)return '24h —';
 const unit=value>=1e8?'亿':value>=1e4?'万':'';
 const amount=value/(unit==='亿'?1e8:unit==='万'?1e4:1);
 return `24h ≈ ${amount.toLocaleString('en-US',{maximumFractionDigits:unit?1:0})}${unit} U`;
}
