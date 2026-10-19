/* The final confirmation is an explicit owner gesture, never an automatic retry. */
import {api,dialog,esc,num,time,text,$,route} from './core.js';
export async function previewOrder(detection,{stop,rr=3}={}){
 if(!detection?.job_id||!detection?.detection_id)throw Error('请先选择一条检测记录。');
 const payload={job_id:detection.job_id,detection_id:detection.detection_id,symbol:detection.symbol,rr,...(stop?{stop}:{})};
 const headers={'X-Fable-Manual':'owner-confirmation'};
 const view=dialog('核对实盘订单',`<p>${esc(detection.symbol)} · 正在计算数量、止损和保证金…</p>`);
 try{
  const p=await api('/api/manual-live/preview',{method:'POST',headers,body:JSON.stringify(payload),timeout:30000});if(!view.isConnected)return;view.close();
  let submitted=false;
  const review=dialog(`${p.side==='long'?'做多':'做空'} ${p.symbol}`,`<p>OKX · 均线密集专用账户 · 市价开仓</p><dl class="grid grid-cols-2 gap-space-md"><dt>数量 / 杠杆</dt><dd>${esc(p.size)} 张 / ${esc(p.leverage)}×</dd><dt>参考价</dt><dd>${num(p.price,8)}</dd><dt>止损 / 止盈</dt><dd>${num(p.initial_stop,8)} / ${num(p.take_profit,8)}</dd><dt>计划风险 / 保证金</dt><dd>${num(p.planned_risk,4)} / ${num(p.margin,4)} USDT</dd><dt>有效至</dt><dd>${esc(time(p.expires_ms))}</dd></dl><p data-order-status class="text-on-surface-variant">确认后将提交真实订单，成交与保护状态分别核对。</p>`,[
   {label:'返回调整',run:d=>d.close()},
   {label:'确认实盘开仓',primary:true,run:async d=>{
    if(submitted)return;if(Date.now()>=p.expires_ms)throw Error('预览已过期，请重新预览。');submitted=true;
    const status=$('[data-order-status]',d);text(status,'提交中，请勿重复操作…');
    try{const r=await api(`/api/manual-live/${encodeURIComponent(p.id)}/confirm`,{method:'POST',headers,body:JSON.stringify({acknowledgement:'CONFIRM-LIVE-ORDER'}),timeout:30000});text(status,`订单状态：${r.state||r.queue_state} · 等待成交与保护核验`);watchReceipt(p.id,d);}
    catch(e){text(status,'提交结果待核对：'+e.message+'。正在查询同一订单回执…');watchReceipt(p.id,d);}
    for(const b of d.querySelectorAll('button'))if(b.textContent==='确认实盘开仓')b.remove();
   }}]);
 }catch(e){if(view.isConnected){view.close();dialog('订单预览暂未就绪',`<p>${esc(e.message)}</p><p>尚未提交订单。可以保留当前检测，核对账户状态后重新预览。</p>`,[{label:'留在当前页面',run:d=>d.close()},{label:'查看账户与持仓',run:()=>route('account')}]);}}
}

async function watchReceipt(id,view){if(!view.isConnected)return;try{const r=await api('/api/manual-live/'+id);text($('[data-order-status]',view),`${r.state} · 成交 ${num(r.entry_price,8)} · ${r.protection_verified?'保护已核验':r.reason||'正在核对成交与保护'}`);if(!['active','closed','skipped','expired','rejected'].includes(r.state))setTimeout(()=>watchReceipt(id,view),2000);}catch(e){text($('[data-order-status]',view),'查询暂未返回，后台仍在核对：'+e.message);setTimeout(()=>watchReceipt(id,view),5000);}}
