/** Instant display estimates only; server recomputes every value before execution. */
import type {Policy,State,Ticket} from './api';
const major=(symbol:string)=>['BTC-USDT-SWAP','ETH-USDT-SWAP'].includes(symbol);
const pool=(policy:Policy|null|undefined,symbol:string,exposure=false)=>policy?.major_allowance_scope==='independent'?(major(symbol)?symbol:'alts'):(exposure?'portfolio':major(symbol)?'majors':'alts');
/** The slider spends a fraction of the remaining allowed margin, not equity. */
export function allocatedMargin(room:number,percent:number){
 if(!Number.isFinite(room)||!Number.isFinite(percent)||room<=0)return 0;
 return Math.floor(room*Math.min(100,Math.max(0,percent))/100*1e8)/1e8;
}
export function marginRoom(state:State|null,symbol:string){
 const cap=Number(major(symbol)?state?.policy?.major_margin_fraction:state?.policy?.alt_margin_fraction);
 const equity=Number(state?.equity);
 if(!(cap>0&&equity>0))return 0;
 let used=0;
 for(const p of state?.positions??[])if(pool(state?.policy,p.symbol)===pool(state?.policy,symbol))used+=Math.max(Number(p.entry),Number(state?.quotes[p.symbol]?.last??p.entry))*Number(p.remaining)/p.leverage;
 for(const p of state?.pending??[])if(pool(state?.policy,p.symbol)===pool(state?.policy,symbol))used+=Number(p.plan.margin);
 return Math.max(0,cap-used/equity);
}
export function ticketEstimate(state:State|null,ticket:Ticket){
 const policy=state?.policy,q=state?.quotes[ticket.symbol],instrument=state?.instruments[ticket.symbol];
 if(!policy||!q||!instrument)return null;
 const equity=Number(state!.equity),direction=ticket.side==='long'?1:-1;
 const price=ticket.kind==='limit'?Number(ticket.price):Number(direction===1?q.ask:q.bid)*(1+direction*Number(policy.slippage_rate));
 const stop=Number(ticket.stop),distance=(price-stop)*direction;
 if(!(equity>0&&price>0&&stop>0&&distance>0))return null;
 const cost=price*(2*Number(policy.fee_rate)+Number(policy.slippage_rate));
 const requested=equity*Math.min(Number(ticket.fraction),marginRoom(state,ticket.symbol))*ticket.leverage/price;
 let used=0;
 const cap=(s:string)=>Number(major(s)?policy.major_exposure_multiple:policy.alt_exposure_multiple);
 for(const p of state!.positions)if(pool(policy,p.symbol,true)===pool(policy,ticket.symbol,true))used+=Number(state!.quotes[p.symbol]?.last??p.entry)*Number(p.remaining)/cap(p.symbol);
 for(const p of state!.pending){if(pool(policy,p.symbol,true)!==pool(policy,ticket.symbol,true))continue;const entry=Number(p.plan.entry),mark=Number(state!.quotes[p.symbol]?.last??entry);used+=Number(p.plan.quantity)*(Math.max(mark,entry)/cap(p.symbol)+entry*Number(policy.fee_rate)+Math.max(0,(entry-mark)*(p.side==='long'?1:-1)))}
 const exposure=Math.max(0,equity-used)/(Math.max(price,Number(q.last))/cap(ticket.symbol)+price*Number(policy.fee_rate)+Math.max(0,(price-Number(q.last))*direction));
 const existing=state!.positions.find(p=>p.symbol===ticket.symbol&&p.side===ticket.side);
 const remainingRisk=existing?Number(existing.remaining)*((Number(existing.entry)-Number(existing.stop))*direction+Number(existing.entry)*(Number(policy.fee_rate)+Number(policy.slippage_rate)))+Number(existing.entry_fee)*Number(existing.remaining)/Number(existing.quantity):0;
 const riskLimit=Math.max(0,equity*Number(policy.risk_fraction)-Math.max(0,remainingRisk))/(distance+cost),step=Number(instrument.step);
 const quantity=Math.floor(Math.min(requested,riskLimit,exposure)/step)*step;
 if(!Number.isFinite(quantity)||quantity<0)return null;
 return {quantity,notional:quantity*price,margin:quantity*price/ticket.leverage,risk:quantity*(distance+cost)+remainingRisk,limited:quantity<requested-step/2};
}
