/** Percentage of current remaining size, never of the original entry.
 * Design: Stitch af3da3a50d9440bf9ca16d3ebad36bd2.
 */
import type {CSSProperties} from 'react';
import {Dialog,Spinner} from './Dialog';
import {sliderFeedback,feedback} from './feedback';
import {positionAmount} from './positionAmount';
import type {Position,State} from './api';

export function ClosePosition({position,state,online,percent,onPercent,busy,locked,onClose,onDismiss}:{position:Position;state:State|null;online:boolean;percent:number;onPercent:(n:number)=>void;busy:boolean;locked:boolean;onClose:()=>void;onDismiss:()=>void}){
 const current=state?.positions.find(p=>p.id===position.id)??position;
 const amount=positionAmount(current,state?.quotes[current.symbol],online,state?.server_time??0).value;
 const step=Number(state?.instruments[current.symbol]?.step),minimum=Number(state?.instruments[current.symbol]?.minimum),remaining=Number(current.remaining);
 const quantity=percent===100?remaining:step>0?Math.floor((remaining*percent/100+step*1e-9)/step)*step:NaN;
 const valid=quantity>0&&(percent===100||quantity>=minimum&&remaining-quantity>=minimum-step*1e-8);
 const closingAmount=amount===undefined||!Number.isFinite(quantity)?undefined:amount*quantity/remaining;
 const fmt=(n:number|undefined)=>n===undefined?'—':n.toLocaleString('en-US',{minimumFractionDigits:2,maximumFractionDigits:2});
 return <Dialog label="选择平仓比例" busy={busy} onDismiss={onDismiss}>{dismiss=><>
  <h2>平仓</h2><div className="position-head"><b>{current.symbol.replace('-USDT-SWAP','')} / USDT</b><span className={current.side==='long'?'long':'short'}>{current.side==='long'?'多':'空'} · {current.leverage}x</span></div>
  <div className="equity"><span>当前剩余持仓</span><b>{fmt(amount)} USDT</b></div>
  <div className="budget close-budget"><div className="slider-heading"><label htmlFor="close-percent">平仓比例</label><output htmlFor="close-percent">{percent}<small>%</small></output></div>
   <input id="close-percent" aria-label="平仓比例" type="range" min="1" max="100" step="1" value={percent} disabled={busy||locked} onChange={e=>{sliderFeedback(percent,Number(e.target.value));onPercent(Number(e.target.value))}} style={{'--fill':`${percent}%`} as CSSProperties}/>
   <div className="slider-scale"><span>1%</span><span>25</span><span>50</span><span>75</span><span>100%</span></div>
   <div className="close-presets" aria-label="快捷平仓比例">{[25,50,75,100].map(value=><button key={value} type="button" aria-pressed={percent===value} disabled={busy||locked} onClick={()=>{onPercent(value);feedback(value===100?'heavy':'medium')}}>{value}%</button>)}</div>
  </div>
  <div className="ticket-estimate close-estimate"><div><span>本次平仓 · USDT</span><b>{fmt(closingAmount)}</b></div><div><span>平仓后剩余 · USDT</span><b>{fmt(amount===undefined||closingAmount===undefined?undefined:Math.max(0,amount-closingAmount))}</b></div></div>
  <p className="micro">{locked?'上一笔结果待核对，重试将使用原比例与编号。':!valid?'低于最小平仓数量，请调整比例或选择 100%。':percent===100?'平掉当前全部剩余仓位。':'剩余仓位继续执行保护规则。'} 按合约步长取整。</p>
  <button className="close-position" aria-busy={busy} disabled={busy||!online||!valid} onClick={()=>{feedback('light');onClose()}}>{busy?<><Spinner/>正在提交…</>:locked?'以原请求重试':percent===100?'确认全部平仓':`确认平仓 ${percent}%`}</button>
  <button className="plain" disabled={busy} onClick={dismiss}>返回</button>
 </>}</Dialog>;
}
