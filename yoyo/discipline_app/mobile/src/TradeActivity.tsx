/** Compact activity details, based on Stitch flow-0.8.5/pending.html. */
import type {State} from './api';
import {activityTime,coinQuantity} from './activityFormat';
import {Spinner} from './Dialog';
import {Icon} from './Icon';
import {feedback} from './feedback';

const coin=(symbol:string)=>symbol.replace('-USDT-SWAP','');
export function TradeActivity({state,busy,cancelling,onCancel,price}:{state:State;busy:boolean;cancelling?:string;onCancel:(id:string)=>void;price:(value:string|undefined,symbol:string)=>string}){
 const recent=state.closed.slice().reverse().sort((a,b)=>(b.closed??0)-(a.closed??0)).slice(0,5);
 return <>
  {!!state.pending.length&&<section className="card list-card activity-list" aria-label="待成交委托">
   <div className="section-heading"><h2>待成交委托</h2><span>北京时间</span></div>
   {state.pending.map(order=><article className="activity-item" key={order.id}>
    <div className="activity-title"><b>{coin(order.symbol)}<small>/USDT</small></b><span className={order.side==='long'?'long':'short'}>{order.side==='long'?'开多':'开空'}</span><span className="activity-status">{Number(order.filled_quantity??0)>0?'部分成交':'挂单中'}</span></div>
    <dl className="activity-prices"><div><dt>委托价</dt><dd>{price(order.price,order.symbol)} <small>USDT</small></dd></div><div><dt>委托持仓金额</dt><dd>{Number(order.plan.notional).toLocaleString('en-US',{minimumFractionDigits:2,maximumFractionDigits:2})} <small>USDT</small></dd></div></dl>
    <div className="activity-pending-footer"><div className="activity-time"><span>委托时间</span><time>{activityTime(order.created)}</time></div><button disabled={busy} aria-busy={cancelling===order.id} onClick={()=>onCancel(order.id)} aria-label={`撤销 ${coin(order.symbol)} 委托`}>{cancelling===order.id?<><Spinner/>撤单中</>:'撤单'}</button></div>
   </article>)}
  </section>}
  {!!recent.length&&<section className="card list-card activity-list" aria-label="最近完成">
   <div className="section-heading"><h2>最近完成</h2><span>北京时间</span></div>
   {recent.map(pos=><details className="activity-item completed-trade" key={pos.id} onToggle={e=>{if(e.currentTarget.open)feedback('light')}}>
    <summary><div className="activity-title"><b>{coin(pos.symbol)}<small>/USDT</small></b><span className={pos.side==='long'?'long':'short'}>{pos.side==='long'?'多':'空'}</span><b className={`activity-pnl ${Number(pos.pnl)>=0?'long':'short'}`}>{Number(pos.pnl)>0?'+':''}{Number(pos.pnl).toLocaleString('en-US',{minimumFractionDigits:2,maximumFractionDigits:2})} <small>USDT</small></b><Icon name="chevron" size={14}/></div><time className="completed-time">{activityTime(pos.closed)}</time></summary>
    <div className="completed-details"><dl className="activity-prices"><div><dt>开仓价</dt><dd>{price(pos.entry,pos.symbol)} <small>USDT</small></dd></div><div><dt>最后止损价</dt><dd>{price(pos.stop,pos.symbol)} <small>USDT</small></dd></div></dl>
    <div className="activity-time"><span>开仓时间</span><time>{activityTime(pos.opened)}</time></div>
    <div className="activity-time"><span>平仓时间</span><time>{activityTime(pos.closed)}</time></div>
    <div className="activity-time"><span>{pos.total_fee!==undefined?'累计交易手续费':'开仓手续费'}</span><span>{coinQuantity(pos.total_fee??pos.entry_fee)} USDT</span></div>
    <div className="activity-time"><span>平仓原因</span><span>{({exchange:'交易所平仓',manual:'手动平仓',stop:'止损触发',take_profit:'止盈触发'} as Record<string,string>)[pos.close_reason??'']??(pos.close_reason?.includes('R')?pos.close_reason:'未记录')}</span></div></div>
   </details>)}
  </section>}
 </>;
}
