/** Local field guidance only; the server still owns every trading decision. */
import type {Ticket,State} from './api';
import {ticketEstimate} from './ticketEstimate.ts';

export function ticketError(ticket:Ticket):string|null {
 for(const [value,label] of [[ticket.stop,'初始止损价格'],...(ticket.kind==='limit'?[[ticket.price,'委托价格']]:[])]){
  if(!value.trim())return `请填写${label}`;
  if(!Number.isFinite(Number(value))||Number(value)<=0)return `${label}必须是大于零的有效数字`;
 }
 if(!(Number(ticket.fraction)>0))return '请拖动滑条选择保证金比例';
 if(!Number.isInteger(ticket.leverage)||ticket.leverage<1)return '请选择有效的交易所杠杆';
 return null;
}

export function errorCopy(message:string){
 if(message.includes('请填写初始止损'))return {title:'请填写初始止损',detail:'先填写止损价格，服务端才能根据止损距离计算仓位与计划风险。',field:'初始止损',rules:false};
 if(message.includes('杠杆过高'))return {title:'杠杆与止损不匹配',detail:message,field:'',rules:true};
 if(message.includes('止损'))return {title:'请检查止损价格',detail:message,field:'初始止损',rules:false};
 if(message.includes('委托价格'))return {title:'请检查委托价格',detail:message,field:'委托价格',rules:false};
 if(message.includes('令牌'))return {title:'请检查访问令牌',detail:message,field:'服务访问令牌',rules:false};
 if(/certificate|证书/.test(message))return {title:'无法验证服务证书',detail:'请使用已配置的 HTTPS 服务地址，并检查证书是否有效。',field:'服务地址',rules:false};
 return {title:'本次操作未完成',detail:message||'服务暂时未响应，请稍后重试。',field:'',rules:/规则|上限|冷静|停手/.test(message)};
}

export type FieldIssue={field:'price'|'stop'|'sizing'|'leverage';message:string};
export function issueForMessage(message:string):FieldIssue|null{
 if(/止损/.test(message))return {field:'stop',message};
 if(/委托价格|限价/.test(message))return {field:'price',message};
 if(/杠杆/.test(message))return {field:'leverage',message};
 if(/数量|仓位额度|保证金比例/.test(message))return {field:'sizing',message};
 return null;
}
export function fieldIssue(ticket:Ticket,state:State|null,skipStop=false):FieldIssue|null{
 const instrument=state?.instruments[ticket.symbol],tick=Number(instrument?.tick);
 const precision=(value:string)=>tick>0&&Math.abs(Number(value)/tick-Math.round(Number(value)/tick))>1e-6;
 if(ticket.kind==='limit'){
  if(!ticket.price.trim()||!Number.isFinite(Number(ticket.price))||Number(ticket.price)<=0)return {field:'price',message:'请输入有效的委托价格'};
  if(precision(ticket.price))return {field:'price',message:`价格需为 ${instrument?.tick} 的整数倍`};
 }
 if(!skipStop){
  if(!ticket.stop.trim()||!Number.isFinite(Number(ticket.stop))||Number(ticket.stop)<=0)return {field:'stop',message:'请输入有效的止损价格'};
  if(precision(ticket.stop))return {field:'stop',message:`止损需为 ${instrument?.tick} 的整数倍`};
  const q=state?.quotes[ticket.symbol],entry=Number(ticket.kind==='limit'?ticket.price:ticket.side==='long'?q?.ask:q?.bid);
  if(entry>0&&(entry-Number(ticket.stop))*(ticket.side==='long'?1:-1)<=0)return {field:'stop',message:ticket.side==='long'?'多单止损应低于入场价':'空单止损应高于入场价'};
 }
 if(!(Number(ticket.fraction)>0))return {field:'sizing',message:'请选择大于 0 的仓位额度'};
 if(!Number.isInteger(ticket.leverage)||ticket.leverage<1)return {field:'leverage',message:'请选择有效杠杆'};
 const estimate=skipStop?null:ticketEstimate(state,ticket);
 if(estimate&&estimate.quantity<Number(instrument?.minimum??instrument?.step))return {field:'sizing',message:'低于合约最小委托量，请增加额度'};
 return null;
}
