/* Stitch 919dd584e7254fcc894df9399f23861e: owner-chosen free manual ticket.
 * Preview is read-only at the exchange. Only an explicit second click confirms
 * the exact server quote; ambiguous confirmations query the same identity.
 */
(() => {
  'use strict';
  const terminal=new Set(['filled','canceled','rejected','expired','superseded','unknown']);
  class Flow {
    constructor(request){this.request=request;this.revision=0;this.reset();}
    reset(){this.revision++;this.draft=null;this.sent=null;this.busy=false;this.error='';}
    async preview(terms){if(this.sent||this.busy)return;const r=++this.revision;this.busy=true;this.draft=null;this.error='';
      try{const d=await this.request('/free/preview',{method:'POST',body:JSON.stringify(terms)});if(r===this.revision)this.draft=d;}
      catch(e){if(r===this.revision)this.error=e.message;}finally{if(r===this.revision)this.busy=false;}}
    canConfirm(){return this.draft?.state==='preview'&&!this.sent&&!this.busy&&Date.now()<this.draft.expires_ms;}
    async confirm(){if(!this.canConfirm())return;const r=this.revision,id=this.draft.id;this.sent=id;this.busy=true;this.error='';
      try{const d=await this.request('/free/'+id+'/confirm',{method:'POST',body:JSON.stringify({acknowledgement:'CONFIRM-FREE-LIVE-ORDER'})});if(r===this.revision)this.draft=d;}
      catch(e){if(r===this.revision)this.error='确认回执暂未收到，正在查询原订单，请勿重复下单。';}
      finally{if(r===this.revision)this.busy=false;}}
    async status(){if(!this.sent)return;const r=this.revision;
      try{const d=await this.request('/free/'+this.sent);if(r!==this.revision)return;this.draft=d;this.error='';if(d.state==='preview'){this.sent=null;this.error='确认未送达，可在报价有效期内重新确认。';}}
      catch(e){if(r===this.revision)this.error='原订单状态暂不可用，正在自动核对；不会重复提交。';}}
  }
  if(typeof module!=='undefined')module.exports={Flow,terminal};
  if(typeof document==='undefined')return;
  const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const num=v=>v==null?'—':Number(v).toLocaleString('en-US',{maximumFractionDigits:8});
  const labels={preview:'核对订单',confirmed:'已确认 · 等待执行',leverage_pending:'正在核对杠杆',sending:'正在核对交易所回执',live:'限价挂单中',partially_filled:'部分成交',filled:'委托已成交',canceled:'委托已撤销',protection_pending:'成交保护待核对',rejected:'本次未下单',expired:'报价已过期',superseded:'报价已更新',unknown:'原订单需要核对'};
  let host=null,revision=0,timer,ticketId,symbol;
  const controllers=new Set();
  async function request(path,options={}){
    const confirmation=path.endsWith('/confirm'),c=new AbortController();if(!confirmation)controllers.add(c);
    const timeout=setTimeout(()=>c.abort(),90000);
    try{const r=await fetch('/api/manual-live'+path,{cache:'no-store',credentials:'same-origin',...options,signal:c.signal,headers:{'Content-Type':'application/json','X-Fable-Manual':'owner-confirmation'}});
      const d=await r.json();if(!r.ok)throw Error(typeof d.detail==='string'?d.detail:d.detail?.message||'账户或订单暂不可用，请重试');return d;
    }catch(e){if(e.name==='AbortError')throw Error('请求超时，请重试');throw e;}finally{clearTimeout(timeout);controllers.delete(c);}
  }
  const flow=new Flow(request),$=s=>host?.querySelector(s);
  function render(){
    if(!host)return;clearTimeout(timer);
    const d=flow.draft,expired=d&&Date.now()>=d.expires_ms&&!flow.sent;
    $('fieldset').disabled=flow.busy||!!flow.sent;
    $('[data-fl-notice]').textContent=flow.error||d?.reason||'';
    $('[data-fl-preview]').disabled=flow.busy||!!flow.sent;
    $('[data-fl-preview]').textContent=flow.busy?'正在核对…':expired?'更新报价':d?'重新预览':'计算并预览订单';
    $('[data-fl-confirm]').hidden=!d||!!flow.sent||expired;
    $('[data-fl-confirm]').disabled=!flow.canConfirm();
    $('[data-fl-confirm]').textContent=d?`确认实盘${d.side==='long'?'做多':'做空'} · ${num(d.size)} 张`:'确认实盘委托';
    $('[data-fl-review]').innerHTML=d?`<h3>${esc(expired?'报价已过期':labels[d.state]||d.state)}</h3><dl><div><dt>方向 / 类型</dt><dd>${d.side==='long'?'做多':'做空'} · ${d.order_type==='limit'?'限价':'市价'}</dd></div><div><dt>数量</dt><dd>${num(d.size)} 张 · ${num(d.base_quantity)} ${esc(symbol.split('-')[0])}</dd></div><div><dt>${d.order_type==='limit'?'委托价':'参考价'}</dt><dd>${num(d.price)}</dd></div><div><dt>仓位 / 杠杆</dt><dd>${num(d.notional)} USDT / ${esc(d.leverage)}×</dd></div><div><dt>保证金 + 开仓费预留</dt><dd>${num(d.margin)} + ${num(d.fee_reserve)} USDT</dd></div><div><dt>止盈 / 止损</dt><dd>${num(d.take_profit)} / ${num(d.stop_loss)}</dd></div>${d.filled_size?`<div><dt>已成交 / 均价</dt><dd>${num(d.filled_size)} 张 / ${num(d.average_price)}</dd></div>`:''}</dl><p>费率：maker ${num(Number(d.maker_fee)*100)}% / taker ${num(Number(d.taker_fee)*100)}% · 限价也可能按 taker 成交。</p>${d.order_type==='limit'&&(d.take_profit||d.stop_loss)?'<p>限价部分成交时，附带止盈止损等待整单成交或余单撤销后生效。</p>':''}${d.protection==='verified'?'<p>成交时附带止盈止损已核验。</p>':d.protection==='none'?'<p>本单未设置止盈止损。</p>':flow.sent?'<p>请留意附带止盈止损的交易所状态。</p>':''}<small>固定价格止盈止损 · 全仓 · 自行管理</small>`:'';
    if(flow.sent&&!document.hidden&&!terminal.has(d?.state))timer=setTimeout(async()=>{await flow.status();render();},2500);
    else if(d&&!expired&&!flow.sent)timer=setTimeout(render,Math.max(1,d.expires_ms-Date.now()+5));
  }
  async function account(){const r=revision;const n=$('[data-fl-account]');if(!n)return;n.textContent='正在读取实盘账户…';
    try{const d=await request('/account');if(r!==revision)return;if(d.account_scope!=='yolo_manual')throw Error('账户来源不匹配');
      const a=d.account;if(a?.status!=='ready')throw Error('实盘账户暂不可用，请重试');
      n.textContent=`手动专用账户 · 可用 ${num(a.data?.balance?.availEq)} USDT · 自由手动模式`;
    }catch(e){if(r===revision)n.textContent=e.message;}}
  async function recent(){const r=revision;try{const d=await request('/free/recent');if(r!==revision)return;
    $('[data-fl-recent]').innerHTML=(d.items||[]).filter(x=>x.symbol===symbol).slice(0,4).map(x=>`<button type="button" data-fl-order="${esc(x.id)}">${esc(labels[x.state]||x.state)} · ${x.side==='long'?'多':'空'} ${num(x.size)} 张</button>`).join('');
    }catch(e){if(r===revision)$('[data-fl-recent]').textContent='历史委托暂不可用';}}
  function closeQuick(){revision++;clearTimeout(timer);for(const c of controllers)c.abort();controllers.clear();flow.reset();host=null;}
  function openQuick(target,context){closeQuick();host=target;symbol=context.symbol;ticketId=crypto.randomUUID().replaceAll('-','');
    host.innerHTML=`<form class="mf-live-form"><h3>${esc(symbol.replace('-USDT-SWAP',' / USDT'))}</h3><div class="mf-live-account"><p data-fl-account role="status"></p><button type="button" data-fl-refresh>刷新账户</button></div><fieldset><div class="mf-live-grid"><label>方向<select name="side"><option value="long">做多</option><option value="short">做空</option></select></label><label>合约杠杆<input name="leverage" type="number" min="1" max="999" step="1" value="20" required></label></div><label>仓位金额（USDT）<input name="notional_usdt" type="text" inputmode="decimal" autocomplete="off" placeholder="输入委托金额" required></label><label>下单方式<select name="order_type"><option value="market">市价委托</option><option value="limit">限价委托</option></select></label><label data-fl-limit hidden>委托价格<input name="limit_price" type="text" inputmode="decimal" autocomplete="off"></label><details><summary>止盈 / 止损 · 选填</summary><div class="mf-live-grid"><label>止盈价格<input name="take_profit" type="text" inputmode="decimal" autocomplete="off"></label><label>止损价格<input name="stop_loss" type="text" inputmode="decimal" autocomplete="off"></label></div></details></fieldset><p data-fl-notice class="mf-live-notice" role="alert"></p><section data-fl-review class="mf-live-review" aria-live="polite"></section><button type="submit" data-fl-preview class="mf-live-primary">计算并预览订单</button><button type="button" data-fl-confirm class="mf-live-primary" hidden>确认实盘委托</button><p class="mf-live-footnote">使用现有手动账户 · 自选金额与固定止盈止损 · 每笔需确认</p><div data-fl-recent class="mf-live-recent"></div><a href="#yolo-detect?tab=account">查看实盘账户与持仓 ↗</a></form>`;
    const form=$('form');form.addEventListener('input',()=>{if(flow.sent)return;flow.reset();render();});
    form.addEventListener('change',()=>{$('[data-fl-limit]').hidden=form.elements.order_type.value!=='limit';});
    form.addEventListener('submit',async e=>{e.preventDefault();if(flow.busy||flow.sent)return;const terms=Object.fromEntries(new FormData(form));
      terms.ticket_id=ticketId;terms.symbol=symbol;for(const k of ['take_profit','stop_loss','limit_price'])terms[k]=terms[k]?.trim()||null;if(terms.order_type==='market')terms.limit_price=null;
      const r=revision,p=flow.preview(terms);render();await p;if(r===revision)render();});
    $('[data-fl-confirm]').onclick=async()=>{const r=revision,p=flow.confirm();render();await p;if(r===revision){render();recent();}};
    $('[data-fl-refresh]').onclick=()=>account();
    $('[data-fl-recent]').onclick=async e=>{const b=e.target.closest('[data-fl-order]');if(!b)return;flow.reset();flow.sent=b.dataset.flOrder;await flow.status();render();};
    account().then(()=>{if(host===target)recent();});render();
  }
  document.addEventListener('visibilitychange',()=>{if(document.hidden){clearTimeout(timer);for(const c of controllers)c.abort();}else if(host){if(flow.sent)flow.status().then(render);else render();}});
  window.SpikeMondayLive={openQuick,closeQuick};
})();
