/** The phone submits intent only. No locally calculated size is sent. */
import {Capacitor,CapacitorHttp} from '@capacitor/core';
export type Ticket={symbol:string;side:'long'|'short';kind:'market'|'limit';price:string;stop:string;fraction:string;leverage:number;sizing_mode?:'risk'|'margin'};
export type Plan={quantity:string;notional:string;margin:string;risk:string;budget:string;entry:string;stop:string;price_risk:string;costs:string;margin_mode:'cross'|'isolated';notional_equity_multiple:string;exposure_limited:boolean};
export type Quote={last:string;bid:string;ask:string;time:number;source:string;today_change?:string|null;volume24h_usdt?:string|null};
export type Position={id:string;symbol:string;side:string;quantity:string;remaining:string;entry:string;stop:string;original_stop:string;risk:string;margin:string;leverage:number;pnl:string;current_r?:string;breakeven:boolean;stages:number[];protection:string;policy:Policy;entry_fee:string;total_fee?:string;opened?:number;closed?:number;close_reason?:string};
export type Policy={major_allowance_scope?:'shared'|'independent';risk_fraction:string;major_margin_fraction?:string|null;alt_margin_fraction?:string|null;margin_mode?:'cross'|'isolated';major_exposure_multiple?:string|null;alt_exposure_multiple?:string|null;major_leverage:number;alt_leverage:number;max_positions:number;max_entries:number;profit_entry_bonus?:number;loss_streak:number;cooldown_after_losses?:number;daily_loss_fraction:string;loss_cooldown_seconds:number;breakeven_r:string;take_profits:[string,string][];fee_rate:string;slippage_rate:string;quote_max_age_seconds:number};
export type ExternalRecord={id:string;kind:"position"|"order"|"algo";symbol:string;side:string;mixed:boolean;value_currency?:string;price:string|null;notional:string|null;pnl:string|null;created:number|null;last_seen:number;status:"observed"|"absent"};
export type State={shared_leverage?:Record<string,number>;external_activity?:{current:ExternalRecord[];recent:ExternalRecord[];updated_at:number};discipline?:{entries_remaining:number;entries_limit?:number;entries_bonus?:number;losses_remaining:number;daily_loss_remaining:string;daily_loss_limit:string};private_at?:number;private_error?:string|null;account_synced?:boolean;account_id?:string;account_name?:string;mode:'paper'|'okx_live';version:number;policy:Policy|null;policy_revision:number;policy_proposal:{policy:Policy;effective_after:number}|null;equity:string;cash:string;positions:Position[];pending:(Ticket&{id:string;plan:Plan;created?:number;filled_quantity?:string})[];closed:Position[];quotes:Record<string,Quote>;instruments:Record<string,{symbol:string;max_leverage:number;tick:string;step:string;minimum?:string;tradable?:boolean;coin?:string;logo_url?:string}>;blockers:string[];daily:{key:string;entries:number;locked_reason:string|null}|null;loss_streak:number;cooldown_until:number;server_time:number;heartbeat:number;quote_error:string|null};
export type Connection={url:string;token:string;accountId?:string;mode?:'paper'|'okx_live';cacheScope?:string};
export async function authorizationScope(url:string,token:string){
 const hash=await crypto.subtle.digest('SHA-256',new TextEncoder().encode(url+'\n'+token));
 return Array.from(new Uint8Array(hash),v=>v.toString(16).padStart(2,'0')).join('');
}
export class ApiError extends Error {status:number;constructor(message:string,status:number){super(message);this.status=status}}

export function normalizeUrl(value:string){
 const url=new URL(value.trim());
 if(url.username||url.password||url.search||url.hash||url.pathname!=='/')throw new Error('请输入不含路径和凭据的服务地址');
 const local=['127.0.0.1','localhost'].includes(url.hostname);
 if(url.protocol!=='https:'&&!(local&&url.protocol==='http:'))throw new Error('手机连接必须使用 HTTPS 服务地址');
 return url.origin;
}

export async function api<T>(connection:Connection,path:string,body?:unknown,signal?:AbortSignal):Promise<T>{
 const headers={'X-Spike-Account':connection.accountId??'primary',...(connection.token?{Authorization:`Bearer ${connection.token}`} : {}),...(body===undefined?{}:{'Content-Type':'application/json'})};
 if(Capacitor.isNativePlatform()){
  signal?.throwIfAborted();
  const response=await CapacitorHttp.request({url:connection.url+path,method:body===undefined?'GET':'POST',headers,data:body,connectTimeout:10000,readTimeout:path==='/v1/preview'?45000:12000,disableRedirects:true,responseType:'json'});
  signal?.throwIfAborted();
  if(response.status<200||response.status>=300)throw new ApiError(typeof response.data?.detail==='string'?response.data.detail:'服务拒绝请求',response.status);
  return response.data as T;
 }
 const response=await fetch(connection.url+path,{method:body===undefined?'GET':'POST',headers,body:body===undefined?undefined:JSON.stringify(body),cache:'no-store',redirect:'error',signal:signal??AbortSignal.timeout(path==='/v1/preview'?45000:12000)});
 const data=await response.json().catch(()=>({detail:'服务返回格式异常'}));
 if(!response.ok)throw new ApiError(typeof data.detail==='string'?data.detail:'参数无效或服务拒绝请求',response.status);
 return data as T;
}

// A development fixture can replace this at build time; release builds use the fixed domain.
export const SERVICE_URL=normalizeUrl(import.meta.env.VITE_SPIKE_SERVICE_URL||'https://discipline.spikeok.com');
