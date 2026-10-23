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
  const num=v=>v==null||v===''||!Number.isFinite(Number(v))?'—':Number(v).toLocaleString('en-US',{maximumFractionDigits:Math.abs(Number(v))<1?12:4});
  const labels={preview:'核对订单',confirmed:'已确认 · 等待执行',leverage_pending:'正在核对杠杆',sending:'正在核对回执',live:'限价挂单中',partially_filled:'部分成交',filled:'委托已成交',canceled:'委托已撤销',protection_pending:'成交保护待核对',rejected:'本次未下单',expired:'报价已过期',superseded:'报价已更新',unknown:'原订单需要核对'};
  let host=null,revision=0,timer,defaultTimer,ticketId,symbol,context,mode,accountId='',limits=null,defaultVersion=0,stopVersion=0,loading=false,autoAmount=true,autoLeverage=true,paperPlan=null,defaultRetries=0,ledger=null;
  const controllers=new Set(),pendingKey='spike-monday-paper-pending-v1';
  const $=s=>host?.querySelector(s),field=n=>$('form')?.elements[n];
  async function http(path,options={}){
    const mutation=options.method==='POST'&&!/\/(defaults|preview)$/.test(path),c=new AbortController();if(!mutation)controllers.add(c);
    const timeout=setTimeout(()=>c.abort(),90000);
    try{const r=await fetch(path,{cache:'no-store',credentials:'same-origin',...options,signal:c.signal,headers:{'Content-Type':'application/json','X-Fable-Manual':'owner-confirmation'}});
      const d=await r.json();if(!r.ok){const e=Error(typeof d.detail==='string'?d.detail:d.detail?.message||'账户或订单暂不可用，请重试');e.status=r.status;throw e;}return d;
    }catch(e){if(e.name==='AbortError')throw Error('请求超时，请重试');throw e;}finally{clearTimeout(timeout);controllers.delete(c);}
  }
  const post=(path,body)=>http(path,{method:'POST',body:JSON.stringify(body)});
  function normalizePaper(q,t){return {...t,...q,state:'preview',base_quantity:q.quantity,size:q.quantity,price:q.execution_price,fee_reserve:q.fee,required:String(Number(q.margin)+Number(q.fee)),maker_fee:'0.0002',taker_fee:'0.0005',protection:t.stop_loss||t.take_profit?'pending':'none',request_id:crypto.randomUUID()};}
  async function transport(path,options={}){
    if(mode==='live')return http('/api/manual-live'+path,options);
    if(path==='/free/preview'){
      const t=JSON.parse(options.body);delete t.ticket_id;
      const q=await post('/api/research/paper-desk/quick/preview',{...t,account_id:accountId});
      return normalizePaper(q,t);
    }
    if(path.endsWith('/confirm')){
      const d={...flow.draft},payload={quantity:d.quantity,quote_id:d.id,request_id:d.request_id,side:d.side,leverage:Number(d.leverage),take_profit:d.take_profit,stop_loss:d.stop_loss,note:'Monday 1.618 manual'};
      if(d.order_type==='limit')payload.limit_price=d.limit_price;
      const saved={draft:d,symbol,account_id:d.account_id};sessionStorage.setItem(pendingKey,JSON.stringify(saved));paperPlan=d;
      const value=await post(`/api/research/paper-desk/accounts/${encodeURIComponent(d.account_id)}/${d.order_type==='limit'?'limit-open':'open'}`,payload);
      sessionStorage.removeItem(pendingKey);
      return {...d,state:value.order?.status==='pending'?'live':'filled',protection:d.stop_loss||d.take_profit?'verified':'none'};
    }
    const d=paperPlan||flow.draft;
    const value=await http('/api/research/paper-desk/quick/receipt/'+encodeURIComponent(d.request_id));
    if(!value.found)return {...d,state:'sending',reason:'正在核对原模拟委托，请勿重复提交'};
    sessionStorage.removeItem(pendingKey);
    return {...d,state:value.order?.status==='pending'?'live':value.order?.status==='rejected'?'rejected':'filled',protection:d.stop_loss||d.take_profit?'verified':'none'};
  }
  const flow=new Flow(transport);
  function terms(){const f=$('form');return {ticket_id:ticketId,symbol,side:f.elements.side.value,order_type:f.elements.order_type.value,allocation_percent:field('allocation').value,notional_usdt:autoAmount?null:field('notional_usdt').value.trim(),leverage:autoLeverage?null:field('leverage').value,limit_price:field('order_type').value==='limit'?field('limit_price').value.trim()||null:null,take_profit:field('take_profit').value.trim()||null,stop_loss:field('stop_loss').value.trim()||null};}
  function invalidate(){if(flow.sent)return;flow.reset();render();}
  function summary(){if(!host)return;const d=flow.draft,n=Number(d?.notional??field('notional_usdt').value),lev=Number(d?.leverage??field('leverage').value),px=Number(d?.price??limits?.price),stop=Number(d?.stop_loss??field('stop_loss').value);
    $('[data-fl-notional]').textContent=num(n||null);$('[data-fl-margin]').textContent=num(n&&lev?n/lev:null);$('[data-fl-risk]').textContent=num(n&&px&&stop?n*Math.abs(px-stop)/px:null);
    $('[data-fl-allocation]').textContent=field('allocation').value+'%';
    for(const b of host.querySelectorAll('[data-fl-percent]'))b.setAttribute('aria-pressed',String(Number(b.dataset.flPercent)===Number(field('allocation').value)&&autoAmount));
    for(const b of host.querySelectorAll('[data-fl-side]'))b.setAttribute('aria-pressed',String(b.dataset.flSide===field('side').value));
    for(const b of host.querySelectorAll('[data-fl-type]'))b.setAttribute('aria-pressed',String(b.dataset.flType===field('order_type').value));
    $('form').dataset.side=field('side').value;$('[data-fl-limit]').hidden=field('order_type').value!=='limit';
  }
  function render(){
    if(!host)return;clearTimeout(timer);const d=flow.draft,expired=d&&Date.now()>=d.expires_ms&&!flow.sent;
    $('fieldset').disabled=flow.busy||!!flow.sent;$('[data-fl-preview]').disabled=flow.busy||!!flow.sent||loading||!limits;
    $('[data-fl-confirm]').hidden=!d||!!flow.sent||expired;$('[data-fl-confirm]').disabled=!flow.canConfirm();
    $('[data-fl-preview]').hidden=!!d&&!expired;
    $('[data-fl-preview]').textContent=flow.busy?'正在核对…':loading?'正在读取额度…':`${mode==='live'?'预览实盘':'预览模拟'}${field('side').value==='long'?'做多':'做空'}`;
    $('[data-fl-confirm]').textContent=`确认${mode==='live'?'实盘':'模拟'}${d?.side==='long'?'做多':'做空'} · ${num(d?.notional)} USDT`;
    $('[data-fl-notice]').textContent=flow.error||d?.reason||'';
    $('[data-fl-review]').innerHTML=d?`<h3>${esc(expired?'报价已过期':labels[d.state]||d.state)}</h3><dl><div><dt>数量 / 杠杆</dt><dd>${num(d.base_quantity)} ${esc(symbol.split('-')[0])} / ${esc(d.leverage)}×</dd></div><div><dt>仓位金额 / 保证金</dt><dd>${num(d.notional)} / ${num(d.margin)} USDT</dd></div><div><dt>止损 / 止盈</dt><dd>${num(d.stop_loss)} / ${num(d.take_profit)}</dd></div><div><dt>开仓费预留</dt><dd>${num(d.fee_reserve)} USDT</dd></div></dl><small>全仓 · 数量按交易所步长向下取整${mode==='live'&&d.order_type==='limit'&&(d.stop_loss||d.take_profit)?'。限价附带止盈止损等待整单成交或余单撤销后生效':''}</small>${!flow.sent?'<button type="button" data-fl-edit>返回编辑</button>':''}`:'';
    if($('[data-fl-accounts]'))$('[data-fl-accounts]').disabled=flow.busy||!!flow.sent;
    $('[data-fl-refresh]').disabled=flow.busy||!!flow.sent;
    $('[data-fl-edit]')?.addEventListener('click',invalidate);
    summary();
    if(flow.sent&&!document.hidden&&!terminal.has(d?.state))timer=setTimeout(async()=>{await flow.status();render();},2500);
    else if(d&&!expired&&!flow.sent)timer=setTimeout(render,Math.max(1,d.expires_ms-Date.now()+5));
  }
  async function defaults(){
    if(!host||flow.sent||flow.busy)return;const r=revision,v=++defaultVersion;let retry=false;loading=true;limits=null;render();
    try{const t=terms();let d;if(mode==='live')d=await post('/api/manual-live/free/defaults',t);else {delete t.ticket_id;d=await post('/api/research/paper-desk/quick/defaults',{...t,account_id:accountId});}
      if(r!==revision||v!==defaultVersion)return;limits=d;flow.error='';
      if(autoAmount)field('notional_usdt').value=d.notional_usdt;
      if(autoLeverage)field('leverage').value=d.leverage;
      field('leverage').max=d.max_leverage;
      $('[data-fl-max]').textContent=`交易所上限 ${d.max_leverage}×`;
      $('[data-fl-equity]').textContent=`总权益 ${num(d.equity)} USDT · 可用 ${num(d.available)}`;
      $('[data-fl-budget]').textContent=`${d.exposure_pool==='alts'?'山寨共享':symbol.split('-')[0]+' 独立'} ${d.exposure_multiple}× · 已占 ${num(d.exposure_used)} / 上限 ${num(d.exposure_cap)} USDT`;
    }catch(e){if(r===revision&&v===defaultVersion){flow.error=e.message;if(mode==='paper'&&e.status===503&&defaultRetries++<5){retry=true;defaultTimer=setTimeout(defaults,2000);}}}
    finally{if(r===revision&&v===defaultVersion){loading=retry;render();}}
  }
  function schedule(){defaultRetries=0;clearTimeout(defaultTimer);defaultVersion++;loading=true;limits=null;render();defaultTimer=setTimeout(defaults,350);}
  async function stop(){const r=revision,v=++stopVersion,side=field('side').value;$('[data-fl-stop-note]').textContent='正在查找本周最近的插针…';
    try{const d=await http(`/api/monday-fib/order-context?symbol=${encodeURIComponent(symbol)}&period=${encodeURIComponent(context.timeframe)}&side=${side}`);
      if(r!==revision||v!==stopVersion||flow.sent||flow.busy)return;
      field('stop_loss').value=d.stop_loss||'';
      $('[data-fl-stop-note]').textContent=d.stop_loss?`${context.timeframe} · ${new Date(d.candle_ms).toLocaleString('zh-CN',{month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit'})} 已收盘影线`:(d.reason||'未找到有效插针，请手动填写');invalidate();
    }catch(e){if(r===revision&&v===stopVersion)$('[data-fl-stop-note]').textContent=e.message;}}
  async function recent(){const r=revision;try{const d=await http('/api/manual-live/free/recent');if(r!==revision)return;
    $('[data-fl-recent]').innerHTML=(d.items||[]).filter(x=>x.symbol===symbol).slice(0,4).map(x=>`<button type="button" data-fl-order="${esc(x.id)}">${esc(labels[x.state]||x.state)} · ${x.side==='long'?'多':'空'} ${num(x.notional)} USDT</button>`).join('');
    }catch(e){if(r===revision)$('[data-fl-recent]').textContent='历史委托暂不可用，请在账户页核对';}}
  async function init(){const r=revision;
    if(mode==='live')recent();
    if(mode==='paper'){
      try{const data=await http('/api/research/paper-desk/live');if(r!==revision)return;
        const select=$('[data-fl-accounts]');select.innerHTML=(data.accounts||[]).map(a=>`<option value="${esc(a.id)}">${esc(a.name)}</option>`).join('');accountId=data.active_account_id||data.accounts?.[0]?.id||'';select.value=accountId;
        if(!accountId)throw Error('请先在模拟账户页创建账户');
        let pending;try{pending=JSON.parse(sessionStorage.getItem(pendingKey)||'null');}catch{}
        if(pending){paperPlan=pending.draft;flow.draft=paperPlan;flow.sent=paperPlan.id;flow.error='上一笔模拟委托回执待核对，请勿重复提交';await flow.status();render();return;}
      }catch(e){if(r===revision){flow.error=e.message;loading=false;render();}return;}
    }
    if(r===revision){ledger?.refresh();defaults();stop();}
  }
  function closeQuick(){ledger?.destroy();ledger=null;revision++;defaultVersion++;stopVersion++;clearTimeout(timer);clearTimeout(defaultTimer);for(const c of controllers)c.abort();controllers.clear();flow.reset();host=null;}
  function openQuick(target,ctx,environment){closeQuick();host=target;context=ctx;symbol=ctx.symbol;mode=environment;accountId='';limits=null;autoAmount=true;autoLeverage=true;loading=true;paperPlan=null;defaultRetries=0;ticketId=crypto.randomUUID().replaceAll('-','');
    host.innerHTML=`<div class="mf-ticket-scroll"><form class="mf-live-form"><div class="mf-ticket-account">${mode==='paper'?'<label><span>账户</span><select data-fl-accounts aria-label="模拟账户"></select></label>':'<span>实盘 · 手动专用账户</span>'}<button type="button" data-fl-refresh>刷新</button></div><fieldset><input type="hidden" name="side" value="${ctx.side==='short'?'short':'long'}"><input type="hidden" name="order_type" value="market"><div class="mf-ticket-segment mf-ticket-sides"><button type="button" data-fl-side="long">做多</button><button type="button" data-fl-side="short">做空</button></div><div class="mf-live-grid mf-ticket-order"><div class="mf-ticket-segment"><button type="button" data-fl-type="market">市价</button><button type="button" data-fl-type="limit">限价</button></div><label>合约杠杆<input name="leverage" type="number" inputmode="numeric" min="1" max="125" step="1" aria-label="合约杠杆"><button type="button" data-fl-maximum data-fl-max>读取交易所上限…</button></label></div><label data-fl-limit hidden>限价 · USDT<input name="limit_price" type="text" inputmode="decimal" autocomplete="off"></label><div class="mf-ticket-stop"><label>初始止损 · USDT<input name="stop_loss" type="text" inputmode="decimal" autocomplete="off" placeholder="止损价格" aria-label="初始止损"></label><button type="button" data-fl-auto-stop>插针自动</button></div><p data-fl-stop-note class="mf-ticket-hint"></p><section class="mf-ticket-allocation"><div class="mf-ticket-between"><span>剩余额度</span><strong data-fl-allocation>100%</strong></div><input name="allocation" type="range" min="1" max="100" step="1" value="100" aria-label="剩余仓位额度百分比"><div class="mf-ticket-presets">${[25,50,75,100].map(x=>`<button type="button" data-fl-percent="${x}">${x}%</button>`).join('')}</div><details><summary>自定义仓位金额</summary><label>仓位金额 · USDT<input name="notional_usdt" type="text" inputmode="decimal" autocomplete="off" placeholder="按总权益自动计算"></label></details><p data-fl-budget class="mf-ticket-hint">BTC / ETH 各 10× · 山寨共享 5×</p></section><details><summary>止盈 · 选填</summary><label>止盈价格 · USDT<input name="take_profit" type="text" inputmode="decimal" autocomplete="off"></label></details></fieldset><p data-fl-notice class="mf-live-notice" role="alert"></p><section data-fl-review class="mf-live-review" aria-live="polite"></section><div data-fl-recent class="mf-live-recent"></div><a class="mf-ticket-link" href="${mode==='paper'?'#paper-desk':'#yolo-detect?tab=account'}">查看账户与持仓 ↗</a><footer class="mf-ticket-footer"><div class="mf-ticket-summary"><div><span>持仓金额</span><strong data-fl-notional>—</strong></div><div><span>保证金</span><strong data-fl-margin>—</strong></div><div><span>预计止损</span><strong data-fl-risk>—</strong></div></div><p data-fl-equity class="mf-ticket-hint">正在读取总权益…</p><button type="submit" data-fl-preview class="mf-live-primary">正在读取额度…</button><button type="button" data-fl-confirm class="mf-live-primary" hidden>确认委托</button><small>金额单位 USDT · 止损预估未计费用与滑点</small></footer></form><section class="mf-ledger" data-fl-ledger aria-label="持仓与委托"></section></div>`;
    const form=$('form');
    form.addEventListener('input',e=>{if(flow.sent)return;const n=e.target.name;
      if(n==='stop_loss'){stopVersion++;$('[data-fl-stop-note]').textContent='手动止损';}
      if(n==='leverage')autoLeverage=false;if(n==='notional_usdt')autoAmount=false;if(n==='allocation')autoAmount=true;
      invalidate();if(['allocation','notional_usdt','limit_price','leverage'].includes(n))schedule();});
    form.addEventListener('click',e=>{const b=e.target.closest('button');if(!b||flow.sent||flow.busy)return;
      if(b.dataset.flSide){field('side').value=b.dataset.flSide;invalidate();schedule();stop();}
      if(b.dataset.flType){field('order_type').value=b.dataset.flType;invalidate();schedule();}
      if(b.dataset.flPercent){field('allocation').value=b.dataset.flPercent;autoAmount=true;invalidate();schedule();}
    });
    form.addEventListener('submit',async e=>{e.preventDefault();if(loading||!limits||flow.busy||flow.sent)return;stopVersion++;const r=revision,p=flow.preview(terms());render();await p;if(r===revision){render();$('[data-fl-review]').scrollIntoView({block:'nearest',behavior:'smooth'});}});
    $('[data-fl-confirm]').onclick=async()=>{const r=revision,p=flow.confirm();render();await p;if(r===revision)render();};
    $('[data-fl-refresh]').onclick=()=>{if(flow.sent||flow.busy)return;defaultRetries=0;invalidate();defaults();};
    $('[data-fl-maximum]').onclick=()=>{autoLeverage=true;invalidate();schedule();};
    $('[data-fl-auto-stop]').onclick=()=>{if(!flow.sent&&!flow.busy)stop();};
    $('[data-fl-accounts]')?.addEventListener('change',e=>{if(flow.sent||flow.busy)return;accountId=e.target.value;invalidate();ledger?.refresh();defaults();});
    $('[data-fl-recent]').onclick=async e=>{const b=e.target.closest('[data-fl-order]');if(!b||flow.busy)return;const r=revision;flow.reset();flow.sent=b.dataset.flOrder;await flow.status();if(r===revision)render();};
    ledger=window.SpikeMondayLedger.create({host:$('[data-fl-ledger]'),mode,symbol,account:()=>accountId,request:http,onChange:()=>{if(!flow.sent&&!flow.busy){invalidate();defaults();}}});
    render();init();
  }
  document.addEventListener('visibilitychange',()=>{if(document.hidden)clearTimeout(timer);else if(host){if(flow.sent)flow.status().then(render);else render();}});
  window.SpikeMondayLive={openQuick:(h,c)=>openQuick(h,c,'live'),closeQuick};
  window.SpikeMondayPaper={openQuick:(h,c)=>openQuick(h,c,'paper'),closeQuick};
})();
