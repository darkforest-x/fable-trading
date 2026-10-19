/** Account/authorization-scoped local display data, refreshed without blanking. */
import {api,type Connection} from './api';
import {DisplayCache} from './readCache';
type Summary={count:number;wins:number;losses:number;flat:number;net_pnl:string;win_rate:string|null;payoff_ratio:string|null;average_net_r:string|null};
export type StatisticsData={account_id:string;mode:'paper'|'okx_live';server_time:number;account_updated_at?:number;period_days:number;market_fresh:boolean;account:{equity:string;initial_equity:string|null;realized_pnl:string;unrealized_pnl:string;net_pnl:string|null;return_fraction:string|null;open_positions:number;pending_orders:number};closed:Summary;fees:string;curve:{date:string;pnl:string}[];symbols:(Summary&{symbol:string})[];sides:(Summary&{side:string})[]};
let storage:Storage|undefined;try{storage=window.localStorage}catch{}
export const displayCache=new DisplayCache(storage);
export const statisticsKey=(c:Connection,days:number)=>`${c.cacheScope??'uncached'}:${c.mode}:${c.accountId??'primary'}:statistics:${days}`;
export function cachedStatistics(c:Connection|null,days:number){return c?.cacheScope?displayCache.peek<StatisticsData>(statisticsKey(c,days))?.data:undefined}
export function loadStatistics(c:Connection,days:number,force=false){
 const loader=async()=>{
  const next=await api<StatisticsData>(c,`/v1/statistics?days=${days}`);
  if(next.mode!==(c.mode??'paper')||next.period_days!==days||next.account_id!==(c.accountId??'primary'))throw Error('统计响应与当前账户不匹配');
  return next;
 };
 return c.cacheScope?displayCache.load(statisticsKey(c,days),loader,force?0:10000):loader();
}
