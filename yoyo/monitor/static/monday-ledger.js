/* Stitch b8b04caa2ba8475a9efab100e295dff9: scrollable holdings beneath the ticket.
 * Actions bind a specific account/position and require a separate confirmation.
 * Uncertain mutations retain the original request identity across reopening.
 */
(() => {
 'use strict';
 const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
 const num=v=>v==null||v===''||!Number.isFinite(Number(v))?'—':Number(v).toLocaleString('en-US',{maximumFractionDigits:Math.abs(Number(v))<1?10:4});
 const terminal=new Set(['done','rejected','expired','superseded']);
 const orderState=s=>({pending:'待成交',live:'待成交',partially_filled:'部分成交',confirmed:'已确认',leverage_pending:'准备委托',sending:'提交核对中',protection_pending:'保护核对中',unknown:'回执待核对'}[s]||'回执待核对');
 function normalize(data,mode,account){
   if(mode==='paper')return {ready:true,positions:(data.positions||[]).filter(p=>p.account_id===account&&Number(p.remaining)>0).map(p=>({...p,size:p.remaining,entry:p.entry_price,pnl:p.unrealized,stop:p.stop_loss,takeProfit:p.take_profit,canManage:true})),orders:(data.orders||[]).filter(o=>o.account_id===account&&o.status==='pending').map(o=>({...o,size:o.quantity,price:o.limit_price,canManage:true})),pending:[]};
   if(data.account_scope!=='yolo_manual')throw Error('账户来源不匹配');
   const ready=data.account?.status==='ready',a=data.account?.data||{},tickets=data.free_tickets||[],seen=new Set();
   const latest=tickets.filter(t=>{if(seen.has(t.symbol))return false;seen.add(t.symbol);return true;});
   const activity=data.executions||{};
   const positions=(a.positions||[]).flatMap(p=>{
     const t=latest.find(t=>t.symbol===p.instId&&t.side===p.posSide&&!t.management_closed_ms&&['filled','canceled'].includes(t.state)&&Number(t.filled_size)>0&&Math.abs(Number(t.filled_size)-Number(t.management_closed_size||0)-Number(p.pos))<1e-8);
     const lots=(activity.active_items||[]).filter(r=>r.symbol===p.instId&&r.side===p.posSide);
     const owned=(activity.owned_lots||[]).filter(r=>r.symbol===p.instId&&r.side===p.posSide);
     const signalOwned=!t&&activity.status==='ready'&&lots.length&&Math.abs(owned.reduce((n,r)=>n+Number(r.size||0),0)-Number(p.pos))<1e-8;
     if(signalOwned)return lots.map(r=>({id:r.id,source:'signal',symbol:p.instId,side:p.posSide,size:r.size,entry:r.entry_price??p.avgPx,pnl:lots.length===1?p.upl:null,stop:r.stop??r.initial_stop,canManage:ready&&data.signal_management_ready&&r.state==='active'&&r.protection_verified,reason:'持仓或保护仍在核对'}));
     return [{id:t?.id||p.instId+':'+p.posSide,source:'free',symbol:p.instId,side:p.posSide,size:p.pos,entry:p.avgPx,pnl:p.upl,stop:t?.stop_loss,takeProfit:t?.take_profit,canManage:!!t&&ready&&data.management_ready,reason:!t?'持仓归属待核对':!data.management_ready?'管理服务正在更新':'更新持仓后可操作'}];
   });
   const orders=latest.filter(t=>['confirmed','leverage_pending','sending','live','partially_filled','protection_pending','unknown'].includes(t.state)).map(t=>({id:t.id,symbol:t.symbol,side:t.side,size:t.size,price:t.limit_price,action:'open',status:t.state,canManage:ready&&data.management_ready&&['live','partially_filled'].includes(t.state),reason:t.reason||'正在核对委托回执'}));
   for(const o of a.orders||[]){
     if(tickets.some(t=>t.client_order_id&&t.client_order_id===o.clOrdId||t.order_id&&t.order_id===o.ordId))continue;
     orders.push({id:o.ordId||o.instId+':'+o.posSide,symbol:o.instId,side:o.posSide,size:o.sz,price:o.px,status:o.state,canManage:false,reason:'请从原委托入口管理'});
   }
   return {ready,positions,orders,pending:data.management_pending||[]};
 }
 class Action {
   constructor(request,storage,key){Object.assign(this,{request,storage,key});this.busy=false;this.value=null;this.error='';try{this.value=JSON.parse(storage.getItem(key)||'null');}catch{}}
   save(){if(this.value?.sent)this.storage.setItem(this.key,JSON.stringify(this.value));else this.storage.removeItem(this.key);}
   async confirm(){const v=this.value;if(!v||v.sent||this.busy)return;this.busy=true;this.error='';
     try{v.sent=true;try{this.save();}catch{v.sent=false;throw Error('无法保存操作回执，请允许会话存储后重试');}const d=await this.request(v.path,{method:'POST',body:JSON.stringify(v.payload)});v.result=d;v.finished=v.mode==='paper'||terminal.has(d.state);if(v.finished)this.storage.removeItem(this.key);else this.save();}
     catch(e){if(e.status>=400&&e.status<500){v.finished=true;this.storage.removeItem(this.key);this.error=e.message;}else this.error=v.sent?'回执待核对，请勿重复提交':e.message;}
     finally{this.busy=false;}}
   async read(){const v=this.value;if(!v?.sent||v.finished||this.busy)return;this.busy=true;
     try{const d=await this.request(v.statusPath);v.result=d;if(v.mode==='live'&&d.state==='preview'){v.sent=false;this.error='确认未送达，可重新确认原操作';this.save();return;}v.finished=v.mode==='paper'?!!d.found:terminal.has(d.state);this.error='';if(v.finished)this.storage.removeItem(this.key);else this.save();}
     catch{this.error='回执暂不可用，正在核对原请求';}finally{this.busy=false;}}
 }
 function create({host,mode,symbol,account,request,onChange}){
   let alive=true,version=0,timer=null,snapshot=null,tab='positions',editor=null,error='',loading=false,action=null,scope='';
   const $=s=>host.querySelector(s),coin=s=>String(s).replace('-USDT-SWAP',' / USDT');
   function resetScope(){const next=mode+':'+account();if(scope===next)return;scope=next;version++;loading=false;clearTimeout(timer);snapshot=null;editor=null;action=new Action(request,sessionStorage,'spike:quick-management:'+scope);}
   const actionName=a=>({stop:'修改止损',protection:'止盈止损',close:'市价平仓',cancel:'撤销挂单'}[a]||a);
   function render(){if(!alive)return;
     const data=snapshot||{positions:[],orders:[]},v=action?.value,locked=action?.busy||v?.sent&&!v.finished;
     const active=document.activeElement,focus=host.contains(active)?active.dataset.editor:null,caret=focus?{start:active.selectionStart,end:active.selectionEnd}:null;
     for(const input of host.querySelectorAll('[data-editor]'))if(editor)editor[input.dataset.editor]=input.value;
     const row=p=>`<article class="mf-holding"><header><strong>${esc(coin(p.symbol))} <em class="${p.side==='short'?'is-short':''}">${p.side==='short'?'空':'多'}</em></strong>${tab==='positions'?`<b class="${Number(p.pnl)<0?'is-short':'is-long'}">${num(p.pnl)}</b>`:`<small>${orderState(p.status)}</small>`}</header><dl><div><dt>${tab==='positions'?'开仓均价':'挂单价'}</dt><dd>${num(p.entry??p.price)}</dd></div><div><dt>数量${mode==='live'?' · 张':''}</dt><dd>${num(p.size)}</dd></div>${tab==='positions'?`<div><dt>止盈 / 止损</dt><dd>${num(p.takeProfit)} / ${num(p.stop)}</dd></div>`:''}</dl><div class="mf-holding-actions">${tab==='positions'?`<button type="button" data-ledger-action="${p.source==='signal'?'stop':'protection'}" data-id="${esc(p.id)}" ${!p.canManage||locked?'disabled':''}>${p.source==='signal'?'修改止损':'止盈止损'}</button><button type="button" data-ledger-action="close" data-id="${esc(p.id)}" ${!p.canManage||locked?'disabled':''}>平仓</button>`:`<button type="button" data-ledger-action="cancel" data-id="${esc(p.id)}" ${!p.canManage||locked?'disabled':''}>撤单</button>`}</div>${p.reason&&!p.canManage?`<small>${esc(p.reason)}</small>`:''}</article>`;
     let fields='';
     if(editor&&!v){
       if(['stop','protection'].includes(editor.action))fields=`${editor.action==='protection'?`<label>止盈 · 留空取消<input data-editor="takeProfit" inputmode="decimal" value="${esc(editor.takeProfit||'')}" aria-label="持仓止盈价格"></label>`:''}<label>止损${editor.action==='protection'?' · 留空取消':''}<input data-editor="stop" data-ledger-stop inputmode="decimal" value="${esc(editor.stop||'')}" aria-label="持仓止损价格"></label>`;
       else if(editor.action==='close'&&editor.row.source!=='signal')fields=`<label>平仓比例 %<input data-editor="percentage" inputmode="decimal" value="${esc(editor.percentage)}" aria-label="平仓比例"></label><div class="mf-ticket-presets">${[25,50,75,100].map(n=>`<button type="button" data-ledger-percent="${n}" aria-pressed="${Number(editor.percentage)===n}">${n}%</button>`).join('')}</div>`;
       else fields='<p>核对本笔数量后确认。</p>';
     }
     host.innerHTML=`<header class="mf-ledger-heading"><nav aria-label="持仓和委托"><button type="button" data-ledger-tab="positions" aria-pressed="${tab==='positions'}">持仓 ${data.positions.length}</button><button type="button" data-ledger-tab="orders" aria-pressed="${tab==='orders'}">挂单 ${data.orders.length}</button></nav><button type="button" data-ledger-refresh aria-label="刷新持仓和委托" ${loading?'disabled':''}>刷新</button></header><p class="mf-ledger-notice" role="status">${esc(error||action?.error||(loading&&!snapshot?'正在读取账户…':''))}</p><div class="mf-holdings">${data[tab].map(row).join('')||`<p class="mf-ledger-empty">${!snapshot?'账户读取中':tab==='positions'?'暂无持仓':'暂无未成交挂单'}</p>`}</div><section class="mf-ledger-editor" ${!editor&&!v?'hidden':''}>${editor&&!v?`<strong>${actionName(editor.action)} · ${esc(coin(editor.row.symbol))}</strong>${fields}<div class="mf-holding-actions"><button type="button" data-ledger-dismiss>取消</button><button type="button" data-ledger-preview ${loading?'disabled':''}>预览</button></div>`:v?`<strong>${actionName(v.action)} · ${esc(coin(v.symbol))}</strong><p>${v.action==='protection'?'止盈 '+num(v.takeProfit)+' / 止损 '+num(v.stop):v.action==='stop'?'新止损 '+num(v.stop):'数量 '+num(v.size)}</p><p role="status">${esc(v.finished?(v.result?.reason||(!action.error?'操作已完成':'')):v.sent?(v.result?.reason||'正在核对原请求…'):'请确认本笔操作')}</p><div class="mf-holding-actions"><button type="button" data-ledger-dismiss ${locked?'disabled':''}>${v.finished?'完成':'取消'}</button>${!v.sent?`<button type="button" data-ledger-confirm>确认${mode==='live'?'实盘':'模拟'}${v.action==='close'?'平仓':v.action==='cancel'?'撤单':'修改'}</button>`:''}</div>`:''}</section>`;
     const next=focus&&host.querySelector('[data-editor="'+focus+'"]');if(next){next.focus({preventScroll:true});next.setSelectionRange(caret.start,caret.end);}
   }
   async function refresh(){resetScope();const r=version,a=account();if(!alive||loading||mode==='paper'&&!a)return;loading=true;
     try{const data=await request(mode==='paper'?'/api/research/paper-desk/live?account_id='+encodeURIComponent(a):'/api/manual-live/free/ledger');if(!alive||r!==version)return;snapshot=normalize(data,mode,a);error='';
       if(!action.value?.sent&&snapshot.pending.length){const p=snapshot.pending[0];action.value={mode,action:p.action,symbol:p.symbol,size:p.size,stop:p.stop,sent:true,statusPath:'/api/manual-live/free/manage/'+p.id+'?source='+(p.source||'free'),result:p};}
       await action.read();if(!alive||r!==version)return;
     }catch(e){if(alive&&r===version){error=e.message;if(snapshot)snapshot={...snapshot,positions:snapshot.positions.map(p=>({...p,canManage:false})),orders:snapshot.orders.map(o=>({...o,canManage:false}))};}}
     finally{if(alive&&r===version){loading=false;render();clearTimeout(timer);if(!document.hidden)timer=setTimeout(refresh,5000);}}
   }
   async function prepare(){if(!editor||loading||action.busy)return;const r=version,e=editor;error='';
     for(const input of host.querySelectorAll('[data-editor]'))e[input.dataset.editor]=input.value.trim();
     loading=true;render();
     try{
       if(['stop','protection'].includes(e.action))for(const [value,required] of [[e.stop,e.action==='stop'],[e.takeProfit,false]])if((required||value)&&(!/^\d+(\.\d+)?$/.test(value)||Number(value)<=0))throw Error('请输入有效的止盈止损价格');
       if(e.action==='close'&&(!/^\d+(\.\d+)?$/.test(e.percentage)||Number(e.percentage)<=0||Number(e.percentage)>100))throw Error('平仓比例须大于 0 且不超过 100%');
       const base={mode,action:e.action,symbol:e.row.symbol,size:e.row.size,stop:e.stop,takeProfit:e.takeProfit};
       if(mode==='live'){const d=await request('/api/manual-live/free/manage/preview',{method:'POST',body:JSON.stringify({ticket_id:e.row.id,source:e.row.source||'free',action:e.action,stop:['stop','protection'].includes(e.action)?e.stop||null:null,take_profit:e.action==='protection'?e.takeProfit||null:null,percentage:e.action==='close'?e.percentage:'100'})});if(!alive||r!==version)return;action.value={...base,size:d.size,stop:d.stop,takeProfit:d.take_profit,expires:d.expires_ms,path:'/api/manual-live/free/manage/'+d.id+'/confirm?source='+(e.row.source||'free'),payload:{acknowledgement:'CONFIRM-FREE-MANAGEMENT'},statusPath:'/api/manual-live/free/manage/'+d.id+'?source='+(e.row.source||'free')};}
       else {const id=crypto.randomUUID();let path,payload={request_id:id};if(e.action==='close'){const q=await request('/api/research/paper-desk/positions/'+encodeURIComponent(e.row.id)+'/close-preview?percentage='+encodeURIComponent(e.percentage));path='/positions/'+encodeURIComponent(e.row.id)+'/close';payload={...payload,quote_id:q.id,quantity:q.quantity};base.size=q.quantity;}
         else if(['stop','protection'].includes(e.action)){const q=await request('/api/research/paper-desk/quote?symbol='+encodeURIComponent(e.row.symbol));path='/positions/'+encodeURIComponent(e.row.id)+'/protection';payload={...payload,quote_id:q.id,stop_loss:e.stop||null,...(e.action==='stop'?{preserve_take_profit:true}:{take_profit:e.takeProfit||null})};}
         else path='/orders/'+encodeURIComponent(e.row.id)+'/cancel';
         if(!alive||r!==version)return;action.value={...base,path:'/api/research/paper-desk'+path,payload,statusPath:'/api/research/paper-desk/quick/receipt/'+id};}
     }catch(e){if(alive&&r===version)error=e.message;}finally{if(alive&&r===version){loading=false;render();$('[data-ledger-confirm]')?.scrollIntoView({block:'nearest'});}}
   }
   host.addEventListener('click',async event=>{const b=event.target.closest('button');if(!b||b.disabled)return;
     if(b.dataset.ledgerTab){tab=b.dataset.ledgerTab;render();}
     if(b.hasAttribute('data-ledger-refresh'))refresh();
     if(b.dataset.ledgerAction){const row=[...(snapshot?.positions||[]),...(snapshot?.orders||[])].find(p=>p.id===b.dataset.id);if(!row?.canManage)return;action.value=null;action.error='';editor={row,action:b.dataset.ledgerAction,stop:row.stop,takeProfit:row.takeProfit,percentage:'100'};render();$('[data-ledger-preview]')?.scrollIntoView({block:'nearest'});}
     if(b.dataset.ledgerPercent&&editor){editor.percentage=b.dataset.ledgerPercent;const input=$('[data-editor=percentage]');if(input)input.value=editor.percentage;render();}
     if(b.hasAttribute('data-ledger-dismiss')){editor=null;action.value=null;action.error='';render();}
     if(b.hasAttribute('data-ledger-preview'))await prepare();
     if(b.hasAttribute('data-ledger-confirm')){if(action.value?.expires&&Date.now()>=action.value.expires){error='预览已过期，请取消后重新核对';render();return;}const r=version,p=action.confirm();render();await p;if(alive&&r===version){render();onChange?.();refresh();}}
   });
   const visibility=()=>{clearTimeout(timer);if(!document.hidden)refresh();};document.addEventListener('visibilitychange',visibility);
   resetScope();render();refresh();
   return {refresh,destroy(){alive=false;version++;clearTimeout(timer);document.removeEventListener('visibilitychange',visibility);}};
 }
 if(typeof module!=='undefined')module.exports={normalize,Action};
 if(typeof window!=='undefined')window.SpikeMondayLedger={create};
})();
