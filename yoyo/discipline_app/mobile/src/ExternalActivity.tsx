/** Read-only exchange observations; Stitch 96e7fb4c06144641b5c26cd5f8dc2525. */
import type {ExternalRecord,State} from './api';
import {activityTime} from './activityFormat';
const amount=(value:string|null)=>value===null?'—':Number(value).toLocaleString('en-US',{minimumFractionDigits:2,maximumFractionDigits:2});
const side=(value:string)=>({long:'多',short:'空',buy:'买',sell:'卖'}[value]??value);
export function ExternalActivity({state,online}:{state:State;online:boolean}){
 const activity=state.external_activity;
 if(!activity||(!activity.current.length&&!activity.recent.length))return null;
 const fresh=online&&!state.private_error&&state.server_time-activity.updated_at<=15;
 const row=(record:ExternalRecord,history=false)=><article className="external-record" key={record.id}>
  <div className="activity-title"><b>{record.symbol.replace('-USDT-SWAP','/USDT')}</b><span className={record.side==='short'||record.side==='sell'?'short':'long'}>{side(record.side)}</span><span className="activity-status">{history?'已离开当前列表':record.mixed?'归属待核对':record.kind==='position'?'外部持仓':record.kind==='algo'?'策略委托':'外部挂单'}</span></div>
  {!history&&<dl className="external-values"><div><dt>{record.mixed?'交易所总持仓':record.kind==='position'?'持仓金额':'委托金额'} · {record.value_currency==='USD'?'USD':'U'}</dt><dd>{amount(record.notional)}</dd></div><div><dt>{record.kind==='position'?'开仓均价':'委托价'}</dt><dd>{record.price===null?'—':Number(record.price).toLocaleString('en-US',{maximumFractionDigits:8})}</dd></div>{record.kind==='position'&&<div><dt>浮动盈亏 · U</dt><dd className={record.pnl!==null&&Number(record.pnl)<0?'short':'long'}>{amount(record.pnl)}</dd></div>}</dl>}
  <div className="activity-time"><span>{history?'最后观测':'委托 / 开仓时间'}</span><time>{activityTime(history?record.last_seen:record.created??undefined)}</time></div>
 </article>;
 return <section className="card list-card external-activity" aria-label="外部交易记录">
  <div className="section-heading"><h2>外部交易</h2><span className="badge">仅记录</span></div>
  <p className="micro">不计入 App 纪律{!fresh?' · 等待同步':''}</p>
  {activity.current.map(record=>row(record))}
  {!!activity.recent.length&&<details className="external-history"><summary>历史观测 <span>{activity.recent.length}</span></summary><p className="micro">仅保留观测记录，最终成交与盈亏请在 OKX 查看。</p>{activity.recent.slice(0,5).map(record=>row(record,true))}</details>}
 </section>;
}
