/** Inline, nonmodal execution states from Stitch flow-0.8.4. */
import {Spinner} from './Dialog';
import {Icon} from './Icon';
import type {State} from './api';
export type EntryProgress={id:string;symbol:string;status:string};
export function TradeProgress({entry,state,onOpen,onDismiss}:{entry:EntryProgress|null;state:State|null;onOpen:()=>void;onDismiss:()=>void}){
 if(!entry)return null;
 const pos=state?.positions.find(p=>p.id===entry.id||(entry.status==='filled'&&p.symbol===entry.symbol)),order=state?.pending.find(p=>p.id===entry.id),closed=state?.closed.some(p=>p.id===entry.id);
 const status=order?(Number(order.filled_quantity??0)>0?'partially_filled':'pending'):pos?'filled':closed?'closed':entry.status;
 const verified=!!pos&&pos.protection==='exchange-verified';
 const checking=!['pending','partially_filled','filled','closed','cancelled','rejected','interrupted'].includes(status);
 const text=checking?'正在核对成交结果':status==='pending'?'挂单中 · 等待成交':status==='partially_filled'?'部分成交 · 余量等待':status==='filled'?(verified?'已成交 · 止损已生效':'已成交 · 正在核对保护'):status==='closed'?'交易已完成':status==='cancelled'?'委托已取消':status==='interrupted'?'请求已中断 · 未生成委托':'委托未成交';
 return <section className="trade-progress" aria-label="订单进度"><div className="progress-heading">{checking?<Spinner/>:<Icon name={verified?'shield':'clock'} size={17}/>}<b>{entry.symbol.replace('-USDT-SWAP','')} <small>开仓</small></b><button aria-label="查看订单" onClick={onOpen}>查看<Icon name="chevron" size={13}/></button>{!checking&&<button className="progress-dismiss" aria-label="收起订单进度" onClick={onDismiss}><Icon name="close" size={14}/></button>}</div><p role="status">{text}</p><ol className="progress-steps"><li className="done">已提交</li><li className={!checking?'done':''}>成交状态</li><li className={verified||closed?'done':''}>保护核对</li></ol></section>;
}
