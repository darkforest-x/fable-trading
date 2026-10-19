/** Human-readable reasons follow server gates, without changing those gates. */
import type {State} from './api';
import {countdown} from './tradeExperience.ts';
export function entryAvailability({state,symbol,connected,online,fresh,restoring,pending,room,now}:{state:State|null;symbol:string;connected:boolean;online:boolean;fresh:boolean;restoring:boolean;pending:boolean;room:number;now:number}){
 if(restoring)return {text:'正在恢复本机授权',tab:'设置' as const};
 if(!connected)return {text:'请先连接账户',tab:'设置' as const};
 if(!online)return {text:'连接中断 · 正在重连',tab:'设置' as const};
 if(pending)return {text:'正在核对上一笔请求',tab:'持仓' as const};
 if(state?.cooldown_until&&state.cooldown_until>now)return {text:`冷静期剩余 ${countdown(state.cooldown_until-now)}`,tab:'纪律' as const};
 const block=state?.blockers[0];
 if(block)return {text:block,tab:/委托|持仓|保护|交易所请求/.test(block)?'持仓' as const:'纪律' as const};
 if(!state?.policy)return {text:'等待账户规则',tab:'纪律' as const};
 if(!fresh)return {text:'行情更新中 · 暂停开仓',tab:'设置' as const};
 if(state.pending.some(p=>p.symbol===symbol))return {text:'该币种已有挂单，成交或撤单后可继续',tab:'持仓' as const};
 if(state.external_activity?.current.some(row=>row.symbol===symbol))return {text:'该合约有外部交易 · 仅记录，其他合约可正常开仓',tab:'持仓' as const};
 if(room<=0)return {text:`${['BTC-USDT-SWAP','ETH-USDT-SWAP'].includes(symbol)?(state.policy.major_allowance_scope==='independent'?symbol.replace('-USDT-SWAP',''):'BTC/ETH'):'山寨币'}可用保证金额度已用完`,tab:'持仓' as const};
 return null;
}
