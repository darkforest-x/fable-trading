/* Stitch 33be10a38bf34bf798c96a02cf5e4a83: compact manual ticket.
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
  // A preferred account is an identity constraint, never a positional fallback.
  function paperAccount(data,preferredName){
    const accounts=data.accounts||[];
    if(preferredName){const matches=accounts.filter(a=>a.name===preferredName);return matches.length===1?matches[0].id:'';}
    return accounts.find(a=>a.id===data.active_account_id)?.id||accounts[0]?.id||'';
  }
  if(typeof module!=='undefined')module.exports={Flow,terminal,paperAccount};
  if(typeof document==='undefined')return;
  const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const num=v=>v==null||v===''||!Number.isFinite(Number(v))?'—':Number(v).toLocaleString('en-US',{maximumFractionDigits:Math.abs(Number(v))<1?12:4});
  const labels={preview:'核对订单',confirmed:'已确认 · 等待执行',leverage_pending:'正在核对杠杆',sending:'正在核对回执',live:'限价挂单中',partially_filled:'部分成交',filled:'委托已成交',canceled:'委托已撤销',protection_pending:'成交保护待核对',rejected:'本次未下单',expired:'报价已过期',superseded:'报价已更新',unknown:'原订单需要核对'};
  let host=null,revision=0,timer,defaultTimer,ticketId,symbol,context,mode,accountId='',limits=null,defaultVersion=0,stopVersion=0,loading=false,autoAmount=true,autoLeverage=true,paperPlan=null,defaultRetries=0,ledger=null,targetPreset='',section='ticket';
  const controllers=new Set(),pendingKey='spike-monday-paper-pending-v1';
  const $=s=>host?.querySelector(s)||context?.accountHost?.querySelector(s),field=n=>$('form')?.elements[n];
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
  function terms(){const f=$('form');return {ticket_id:ticketId,symbol,side:f.elements.side.value,order_type:f.elements.order_type.value,allocation_percent:field('allocation').value,notional_usdt:autoAmount?null:field('notional_usdt').value.trim(),leverage:autoLeverage?null:field('leverage').value,limit_price:field('order_type').value==='limit'?field('limit_price').value.trim()||null:null,take_profit:field('take_profit').value.trim()||null,stop_loss:field('stop_loss').value.trim()||null,entry_policy:'monday_1618',range_week:limits?.range_week||null};}
  function invalidate(){if(flow.sent)return;flow.reset();render();}
  function summary(){if(!host)return;const d=flow.draft,n=Number(d?.notional??field('notional_usdt').value),lev=Number(d?.leverage??field('leverage').value),px=Number(d?.price??limits?.price),stop=Number(d?.stop_loss??field('stop_loss').value);
    $('[data-fl-notional]').textContent=num(n||null);$('[data-fl-margin]').textContent=num(n&&lev?n/lev:null);$('[data-fl-risk]').textContent=num(n&&px&&stop?n*Math.abs(px-stop)/px:null);
    $('[data-fl-allocation]').textContent=autoAmount?field('allocation').value+'%':'自定义';
    field('allocation').setAttribute('aria-valuetext',autoAmount?field('allocation').value+'%':'自定义金额；调整滑块恢复百分比');
    for(const b of host.querySelectorAll('[data-fl-side]'))b.setAttribute('aria-pressed',String(b.dataset.flSide===field('side').value));
    for(const b of host.querySelectorAll('[data-fl-target]')){b.disabled=!limits||flow.busy||!!flow.sent;b.setAttribute('aria-pressed',String(b.dataset.flTarget===targetPreset));}
    $('[data-fl-target=extreme]').textContent=field('side').value==='long'?'周一高点':'周一低点';
    $('[data-fl-settings]').textContent=[!autoAmount?'自定义金额':'',field('take_profit').value?'止盈':'',field('stop_loss').value?'止损':''].filter(Boolean).join(' · ');
    $('form').dataset.side=field('side').value;$('[data-fl-limit]').hidden=field('order_type').value!=='limit';
  }
  function render(){
    if(!host)return;clearTimeout(timer);const d=flow.draft,expired=d&&Date.now()>=d.expires_ms&&!flow.sent;
    $('fieldset').hidden=!!d&&!expired;$('[data-fl-summary]').hidden=!!d&&!expired;
    $('fieldset').disabled=flow.busy||!!flow.sent;$('[data-fl-preview]').disabled=flow.busy||!!flow.sent||loading||!limits;
    $('[data-fl-confirm]').hidden=!d||!!flow.sent||expired;$('[data-fl-confirm]').disabled=!flow.canConfirm();
    $('[data-fl-preview]').hidden=!!d&&!expired;
    $('[data-fl-preview]').textContent=flow.busy?'正在核对…':loading?'正在读取额度…':`预览${mode==='live'?'实盘':'模拟'}挂单`;
    $('[data-fl-confirm]').textContent=`确认${mode==='live'?'实盘':'模拟'}${d?.side==='long'?'做多':'做空'} · ${num(d?.notional)} USDT`;
    $('[data-fl-notice]').textContent=flow.error||d?.reason||'';
    $('[data-fl-review]').innerHTML=d?`<h3>${esc(expired?'报价已过期':labels[d.state]||d.state)}</h3><dl><div><dt>账户</dt><dd>${esc(mode==='paper'?'模拟 · '+($('[data-fl-accounts]')?.selectedOptions[0]?.textContent||d.account_id||'—'): '实盘 · 手动专用账户')}</dd></div><div><dt>标的</dt><dd>${esc((d.symbol||symbol).replace('-USDT-SWAP',' / USDT'))}</dd></div><div><dt>周一区间</dt><dd>${esc(d.range_week||limits?.range_week||'—')}</dd></div><div><dt>1.618 限价</dt><dd>${num(d.limit_price)}</dd></div><div><dt>数量 / 杠杆</dt><dd>${num(d.base_quantity)} ${esc((d.symbol||symbol).split('-')[0])} / ${esc(d.leverage)}×</dd></div><div><dt>仓位金额 / 保证金</dt><dd>${num(d.notional)} / ${num(d.margin)} USDT</dd></div><div><dt>止损 / 止盈</dt><dd>${num(d.stop_loss)} / ${num(d.take_profit)}</dd></div><div><dt>开仓费预留</dt><dd>${num(d.fee_reserve)} USDT</dd></div></dl><small>全仓 · 数量按交易所步长向下取整${mode==='live'&&d.order_type==='limit'&&(d.stop_loss||d.take_profit)?'。限价附带止盈止损等待整单成交或余单撤销后生效':''}</small>${!flow.sent?'<button type="button" data-fl-edit>返回编辑</button>':''}`:'';
    if($('[data-fl-accounts]'))$('[data-fl-accounts]').disabled=flow.busy||!!flow.sent;
    $('[data-fl-retry]').hidden=!flow.error||!!d||flow.busy||loading;
    $('[data-fl-edit]')?.addEventListener('click',invalidate);
    summary();
    if(flow.sent&&!document.hidden&&!terminal.has(d?.state))timer=setTimeout(async()=>{await flow.status();render();},2500);
    else if(d&&!expired&&!flow.sent)timer=setTimeout(render,Math.max(1,d.expires_ms-Date.now()+5));
  }
  async function defaults(){
    if(!host||flow.sent||flow.busy||(mode==='paper'&&!accountId))return;const r=revision,v=++defaultVersion;let retry=false;loading=true;limits=null;render();
    try{const t=terms();let d;if(mode==='live')d=await post('/api/manual-live/free/defaults',t);else {delete t.ticket_id;d=await post('/api/research/paper-desk/quick/defaults',{...t,account_id:accountId});}
      if(r!==revision||v!==defaultVersion)return;limits=d;flow.error='';
      field('limit_price').value=d.limit_price;
      if(targetPreset)field('take_profit').value=d[targetPreset==='0618'?'target_0618':'target_extreme'];
      if(autoAmount)field('notional_usdt').value=d.notional_usdt;
      if(autoLeverage)field('leverage').value=d.leverage;
      field('leverage').max=d.max_leverage;
      field('leverage').title=`交易所上限 ${d.max_leverage}×`;
      $('[data-fl-equity]').textContent=`总权益 ${num(d.equity)} USDT · 可用 ${num(d.available)}`;
      $('[data-fl-budget]').textContent=`${d.exposure_pool==='alts'?'山寨共享':symbol.split('-')[0]+' 独立'} ${d.exposure_multiple}× · 已占 ${num(d.exposure_used)} / 上限 ${num(d.exposure_cap)} USDT`;
    }catch(e){if(r===revision&&v===defaultVersion){flow.error=e.message;if(mode==='paper'&&e.status===503&&defaultRetries++<5){retry=true;defaultTimer=setTimeout(defaults,2000);}}}
    finally{if(r===revision&&v===defaultVersion){loading=retry;render();}}
  }
  function schedule(){defaultRetries=0;clearTimeout(defaultTimer);defaultVersion++;loading=true;limits=null;render();defaultTimer=setTimeout(defaults,350);}
  async function init(){const r=revision;
    if(mode==='paper'){
      try{const data=await http('/api/research/paper-desk/live');if(r!==revision)return;
        const select=$('[data-fl-accounts]');accountId=paperAccount(data,context.preferredPaperAccountName);
        select.innerHTML='<option value="" disabled>选择模拟账户</option>'+(data.accounts||[]).map(a=>`<option value="${esc(a.id)}">${esc(a.name)}</option>`).join('');select.value=accountId;
        let pending;try{pending=JSON.parse(sessionStorage.getItem(pendingKey)||'null');}catch{}
        if(pending){accountId=pending.draft.account_id||pending.account_id;
          if(!Array.from(select.options).some(o=>o.value===accountId))select.add(new Option('待核对原账户',accountId));select.value=accountId;
          paperPlan=pending.draft;flow.draft=paperPlan;flow.sent=paperPlan.id;flow.error='上一笔模拟委托回执待核对，请勿重复提交';await flow.status();render();return;}
        if(!accountId)throw Error(context.preferredPaperAccountName?'梭哈账户不可唯一识别，请选择模拟账户':'请先在模拟账户页创建账户');
      }catch(e){if(r===revision){flow.error=e.message;loading=false;render();}return;}
    }
    if(r===revision){ledger?.refresh();defaults();}
  }
  function setSection(value){if(!host)return;section=value;$('form').hidden=section!=='ticket';$('[data-fl-ledger]').hidden=section!=='holdings';for(const tab of host.querySelectorAll('[data-fl-section]'))tab.setAttribute('aria-pressed',String(tab.dataset.flSection===section));context.onSection?.(section);if(section==='holdings')ledger?.refresh();}
  const toggleHoldings=()=>setSection(section==='ticket'?'holdings':'ticket');
  function closeQuick(){ledger?.destroy();ledger=null;revision++;defaultVersion++;stopVersion++;clearTimeout(timer);clearTimeout(defaultTimer);for(const c of controllers)c.abort();controllers.clear();flow.reset();if(context?.accountHost)context.accountHost.textContent='';host=null;context=null;}
  function openQuick(target,ctx,environment){closeQuick();host=target;context=ctx;symbol=ctx.symbol;mode=environment;accountId='';limits=null;autoAmount=true;autoLeverage=true;loading=true;paperPlan=null;defaultRetries=0;targetPreset='';section='ticket';ticketId=crypto.randomUUID().replaceAll('-','');
    const accountMarkup=mode==='paper'?'<select data-fl-accounts aria-label="模拟账户"><option value="">读取账户…</option></select>':'<span class="mf-live-account-name">手动专用账户</span>';
    if(ctx.accountHost)ctx.accountHost.innerHTML=accountMarkup;
    host.innerHTML=`${ctx.compact?'':'<nav class="rp-ticket-tabs" aria-label="开单与持仓"><button type="button" data-fl-section="ticket" aria-pressed="true">开单</button><button type="button" data-fl-section="holdings" aria-pressed="false">持仓</button></nav>'}<div class="mf-ticket-scroll"><form class="mf-live-form">${ctx.accountHost?'':`<div class="mf-ticket-account">${accountMarkup}</div>`}<fieldset>
      <input type="hidden" name="side" value="${ctx.side==='short'?'short':'long'}"><input type="hidden" name="order_type" value="limit">
      <div class="mf-ticket-segment mf-ticket-sides"><button type="button" data-fl-side="long">做多 · 下 1.618</button><button type="button" data-fl-side="short">做空 · 上 1.618</button></div>
      <div class="mf-live-grid mf-ticket-order"><label data-fl-limit>1.618 挂单价<input name="limit_price" type="text" readonly aria-label="1.618 挂单价"></label><label>杠杆<span class="mf-ticket-leverage"><input name="leverage" type="number" inputmode="numeric" min="1" max="125" step="1" aria-label="合约杠杆"><span aria-hidden="true">×</span></span></label></div>
      <section class="mf-ticket-allocation"><div class="mf-ticket-between"><span>剩余额度</span><strong data-fl-allocation>100%</strong></div><input name="allocation" type="range" min="1" max="100" step="1" value="100" aria-label="剩余仓位额度百分比"></section>
      <details class="mf-ticket-more"><summary>更多设置 <span data-fl-settings></span></summary><div class="mf-ticket-settings">
        <label>自定义仓位金额 · USDT<input name="notional_usdt" type="text" inputmode="decimal" autocomplete="off" placeholder="按剩余额度计算"></label>
        <section class="rp-ticket-exits"><label>止盈 · 选填<input name="take_profit" type="text" inputmode="decimal" autocomplete="off" placeholder="未设置" aria-label="止盈价格"></label><div class="rp-target-presets"><button type="button" data-fl-target="0618">区间 0.618</button><button type="button" data-fl-target="extreme">周一高点</button></div><label>止损 · 选填<input name="stop_loss" type="text" inputmode="decimal" autocomplete="off" placeholder="未设置" aria-label="止损价格"></label><span data-fl-stop-note hidden></span></section>
        <p data-fl-budget class="mf-ticket-hint"></p><p data-fl-equity class="mf-ticket-hint"></p><p class="mf-ticket-hint">预计止损 <span data-fl-risk>—</span> USDT · 未计费用与滑点</p>
      </div></details></fieldset>
      <p data-fl-notice class="mf-live-notice" role="alert"></p><button type="button" data-fl-retry hidden>重试</button><section data-fl-review class="mf-live-review" aria-live="polite"></section>
      <footer class="mf-ticket-footer"><div class="mf-ticket-summary" data-fl-summary><div><span>仓位 · USDT</span><strong data-fl-notional>—</strong></div><div><span>保证金 · USDT</span><strong data-fl-margin>—</strong></div></div><button type="submit" data-fl-preview class="mf-live-primary">正在读取额度…</button><button type="button" data-fl-confirm class="mf-live-primary" hidden>确认委托</button></footer>
      </form><section class="mf-ledger" data-fl-ledger aria-label="持仓与委托" hidden></section></div>`;
    const form=$('form');
    form.addEventListener('input',e=>{if(flow.sent)return;const n=e.target.name;
      if(n==='stop_loss'){stopVersion++;$('[data-fl-stop-note]').textContent='手动止损';}
      if(n==='take_profit')targetPreset='';
      if(n==='leverage')autoLeverage=false;if(n==='notional_usdt')autoAmount=false;if(n==='allocation')autoAmount=true;
      invalidate();if(['allocation','notional_usdt','limit_price','leverage'].includes(n))schedule();});
    form.addEventListener('click',e=>{const b=e.target.closest('button');if(!b||flow.sent||flow.busy)return;
      if(b.dataset.flSide){field('side').value=b.dataset.flSide;field('take_profit').value='';field('stop_loss').value='';invalidate();schedule();}
      if(b.dataset.flTarget&&limits){targetPreset=b.dataset.flTarget;field('take_profit').value=limits[targetPreset==='0618'?'target_0618':'target_extreme'];invalidate();}
    });
    form.addEventListener('submit',async e=>{e.preventDefault();if(loading||!limits||flow.busy||flow.sent)return;stopVersion++;const r=revision,p=flow.preview(terms());render();await p;if(r===revision){render();$('[data-fl-confirm]').scrollIntoView({block:'nearest',behavior:'smooth'});}});
    $('[data-fl-confirm]').onclick=async()=>{const r=revision,p=flow.confirm();render();await p;if(r===revision)render();};
    $('[data-fl-retry]').onclick=()=>{if(flow.sent||flow.busy)return;defaultRetries=0;invalidate();if(mode==='paper'&&!accountId)init();else defaults();};
    for(const b of host.querySelectorAll('[data-fl-section]'))b.onclick=()=>setSection(b.dataset.flSection);
    $('[data-fl-accounts]')?.addEventListener('change',e=>{if(flow.sent||flow.busy)return;accountId=e.target.value;invalidate();ledger?.refresh();defaults();});
    ledger=window.SpikeMondayLedger.create({host:$('[data-fl-ledger]'),mode,symbol,account:()=>accountId,request:http,onChange:()=>{if(!flow.sent&&!flow.busy){invalidate();defaults();}}});
    render();init();
  }
  document.addEventListener('visibilitychange',()=>{if(document.hidden)clearTimeout(timer);else if(host){if(flow.sent)flow.status().then(render);else render();}});
  window.SpikeMondayLive={openQuick:(h,c)=>openQuick(h,c,'live'),closeQuick,toggleHoldings};
  window.SpikeMondayPaper={openQuick:(h,c)=>openQuick(h,c,'paper'),closeQuick,toggleHoldings};
})();
