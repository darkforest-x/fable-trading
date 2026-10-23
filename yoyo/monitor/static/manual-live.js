/* Explicit owner-confirmed OKX entry; quote drafts never submit themselves. */
(() => {
  'use strict';
  const terminal=new Set(['closed','skipped','rejected','expired','superseded']);
  const labels={preview:'待确认',queued:'已确认 · 等待执行',prepared:'准备下单',leverage_pending:'核对杠杆',entry_pending:'核对成交',cancel_entry_pending:'处理部分成交',protecting:'核对止损与止盈',manual_tp_pending:'按成交价校准止盈',active:'持仓中 · 止损止盈已核验',stop_executing:'止盈／止损成交核验中',exit_needed:'准备退出',cancel_stop_pending:'撤销本单保护后退出',exit_pending:'核对退出成交',closed:'已平仓',skipped:'未开仓',rejected:'交易所拒绝',expired:'预览已过期',superseded:'预览已更新'};
  class Flow {
    constructor(request,clock=()=>Date.now()){this.request=request;this.clock=clock;this.version=0;this.reset();}
    cancelReads(){this.previewController?.abort();this.referenceController?.abort();}
    reset(ref=null){this.cancelReads();this.version++;this.ref=ref?{...ref}:null;this.draft=null;this.reference=null;this.rejectionReference=null;this.referenceBusy=false;this.referenceError='';this.busy=false;this.sent=null;this.error='';this.blocker=null;this.recovered=false;}
    edit(){if(this.sent)return;this.cancelReads();this.version++;this.draft=null;this.reference=null;this.rejectionReference=null;this.referenceBusy=false;this.referenceError='';this.busy=false;this.error='';this.blocker=null;this.recovered=false;}
    canConfirm(){return !!this.draft&&!this.draft.display_only&&!this.busy&&!this.sent&&this.draft.queue_state==='preview'&&this.clock()<this.draft.expires_ms;}
    async preview(terms){if(this.sent||!this.ref)return;this.cancelReads();this.previewController=new AbortController();const v=++this.version;this.busy=true;this.draft=null;this.reference=null;this.rejectionReference=null;this.referenceError='';this.error='';this.blocker=null;this.recovered=false;
      try{const d=await this.request('/preview',{method:'POST',signal:this.previewController.signal,body:JSON.stringify({job_id:this.ref.job_id,symbol:this.ref.symbol,detection_id:this.ref.detection_id,...terms})});if(v===this.version)this.draft=d;}
      catch(e){if(v===this.version){this.error=e.message;this.blocker=e.detail?.status==='blocked'?e.detail:null;if(validReference(e.reference,this.ref,terms.rr))this.rejectionReference=e.reference;}}finally{if(v===this.version)this.busy=false;}}
    async readiness(){
      if(this.sent||!this.blocker)return false;const v=this.version,signature=d=>JSON.stringify([d.code,d.message,d.issues]),old=signature(this.blocker);
      try{const status=await this.request('/readiness');if(v!==this.version||this.sent)return false;
        if(status.status==='ready'){this.blocker=null;this.error='';this.recovered=true;return true;}
        if(status.status==='blocked'){this.blocker=status;this.error=status.message;return old!==signature(status);}}
      catch(e){/* Retain the known blocker when a status read fails. */}return false;
    }
    display(){return this.draft||this.rejectionReference||this.reference;}
    async referenceFor(terms){
      if(this.sent||!this.ref)return;this.referenceController?.abort();this.referenceController=new AbortController();const v=this.version;this.referenceBusy=true;this.referenceError='';
      try{const d=await this.request('/reference',{method:'POST',signal:this.referenceController.signal,body:JSON.stringify({job_id:this.ref.job_id,symbol:this.ref.symbol,detection_id:this.ref.detection_id,...terms})});
        if(v===this.version&&!this.sent){if(!validReference(d,this.ref,terms.rr))throw Error('参考点位与当前订单不一致');this.reference=d;}}
      catch(e){if(v===this.version&&!this.sent)this.referenceError=e.message;}
      finally{if(v===this.version)this.referenceBusy=false;}
    }
    async confirm(){if(!this.canConfirm())return;const id=this.draft.id,v=this.version;this.sent=id;this.busy=true;this.error='';
      try{const d=await this.request(`/${id}/confirm`,{method:'POST',body:JSON.stringify({acknowledgement:'CONFIRM-LIVE-ORDER'})});if(v===this.version)this.draft=d;}
      catch(e){if(v===this.version){
        if(e.detail?.status==='blocked'){this.sent=null;this.draft=null;this.blocker=e.detail;this.error=e.message;}
        else this.error='提交回执暂未收到，正在查询原订单；不会重复下单。';
      }}
      finally{if(v===this.version)this.busy=false;}}
    async status(){if(!this.sent)return;const id=this.sent,v=this.version;
      try{const d=await this.request('/'+id);if(v!==this.version)return;this.draft=d;this.error='';
        if(d.state==='preview'){this.sent=null;this.error='确认尚未送达，可在有效期内再次点击确认。';}}
      catch(e){if(v===this.version)this.error='订单状态暂不可用，系统会自动重试；不要重复开仓。';}}
  }

  function validReference(d,ref,rr){
    return !!d&&d.display_only===true&&!d.id&&!d.queue_state&&d.symbol===ref?.symbol&&d.rr===rr
      &&['long','short'].includes(d.side)&&(!ref.side||d.side===ref.side);
  }
  function quoteExpired(d,confirmed,now=Date.now()){
    // Preview TTL controls approval, never the life of an existing position.
    return !confirmed&&Number.isFinite(d?.expires_ms)&&now>=d.expires_ms;
  }
  function validateTerms(stop, rr, custom=false) {
    if (![3,5].includes(rr)) return '请选择 3R 或 5R 止盈。';
    if (custom && (!/^\d{1,12}(\.\d{1,18})?$/.test(stop) || !Number.isFinite(Number(stop)) || Number(stop)<=0)) return '请输入大于 0 的止损价格。';
    return '';
  }
  function limitMessage(detail){
    if(detail?.code!=='market_order_size_limit')return '';
    const fields=['requested_size','max_market_size','target_risk','price','stop','stop_distance'];
    if(fields.some(k=>!Number.isFinite(Number(detail[k]))||Number(detail[k])<=0))return '';
    const n=k=>Number(detail[k]).toLocaleString('en-US',{maximumFractionDigits:10});
    return `按 1R = ${n('target_risk')} USDT、止损距离 ${n('stop_distance')} 计算，需要 ${n('requested_size')} 张；交易所单笔市价上限为 ${n('max_market_size')} 张。参考市价 ${n('price')}，止损价 ${n('stop')}。止损越近，同样风险预算所需张数越多，请检查止损设置后重新预览。`;
  }
  function marginMessage(detail){
    if(detail?.code!=='insufficient_margin')return '';
    const fields=['required_margin','usable_margin','shortfall','leverage','requested_size','target_risk'];
    if(fields.some(k=>!Number.isFinite(Number(detail[k]))||Number(detail[k])<0))return '';
    const n=(k,d=2)=>Number(detail[k]).toLocaleString('en-US',{maximumFractionDigits:d});
    return `按 1R = ${n('target_risk',4)} USDT 计算，需要 ${n('requested_size',8)} 张；已采用该仓位允许的最高 ${n('leverage')} 倍杠杆，预计保证金 ${n('required_margin')} USDT，当前可用 ${n('usable_margin')} USDT，还差 ${n('shortfall')} USDT。3% 是止损风险，不是保证金比例；止损越近，需要的仓位越大。`;
  }
  if(typeof module!=='undefined'&&module.exports)module.exports={Flow,labels,validateTerms,limitMessage,marginMessage,validReference,quoteExpired};
  if(typeof window==='undefined'||typeof document==='undefined')return;
  const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const fmt=t=>t?new Intl.DateTimeFormat('zh-CN',{timeZone:'Asia/Shanghai',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false}).format(new Date(t)):'—';
  const num=(v,digits=4)=>v==null?'—':Number(v).toLocaleString('en-US',{maximumFractionDigits:digits});
  const price=v=>num(v,8), coin=s=>String(s||'').replace('-USDT-SWAP','');
  const messages={
    'execution worker is not ready for manual entry':'暂时不能开仓。账户执行服务尚未就绪，确认账户状态后再试。',
    'manual executor is stopped':'账户已连接，但尚未启用开仓。请先完成账户执行设置。',
    'manual executor reconciliation pending':'正在核对账户与原订单，暂不能开新仓。核对完成后可重新预览。',
    'symbol has owner-managed positions or orders':'这个币种已有你自行管理的仓位或挂单。为避免混仓，请选择其他币种，或在你自行处理后重新预览。',
    'owner order conflicts with a system-managed instrument':'系统持仓所在币种出现了其他挂单，请先核对该币种的订单。',
    'this detection already has a confirmed order':'这处形态已提交过订单，可在「实盘记录」查看进度。',
    'manual preview expired or superseded':'报价已过期，请更新报价后再次确认。',
    'latest closed 5m candle unavailable':'最近一根已收盘 5 分钟 K 线尚未到齐，请稍后更新报价。',
    'market has passed the initial stop':'当前价格已越过止损价。请改用自定义止损，再重新计算。',
    'insufficient available allocation for this signal':'可用保证金不足，无法按账户权益的 3% 计算本次仓位。',
    'risk-sized quantity exceeds exchange market-order limit':'本次仓位超过交易所单笔市价单上限。',
    'risk-sized quantity is below exchange minimum':'本次仓位小于交易所最小下单量。',
    'symbol excluded by owner entry universe':'该币种不在当前账户允许交易的范围内。',
    'exchange positions do not match owned signal lots':'系统仓位与交易所数量不一致，需先核对持仓再开新单。',
    'manual preview expired or policy invalid':'执行前报价已过期，本次未开仓；请重新预览并确认。',
    'signal expired before leverage setup':'执行前报价已过期，本次未开仓；请重新预览并确认。',
    'signal or quote expired before entry':'下单前报价已过期，本次未开仓；请重新预览并确认。',
    '5m moving averages unavailable':'5 分钟均线数据尚未到齐，请稍后重试或自行指定止损。',
    'manual preview or quote expired':'行情已变化，本次未下单，请更新报价。',
    'manual margin or leverage changed; preview again':'保证金或杠杆档位已变化，请更新报价后确认。',
  };
  const friendly=s=>messages[s]||s;
  async function request(path,options={}){
    const controller=new AbortController(),confirmation=path.endsWith('/confirm');let timeout;
    const abort=()=>controller.abort();
    if(!confirmation){options.signal?.addEventListener('abort',abort,{once:true});if(options.signal?.aborted)abort();}
    try {
      const operation=(async()=>{
        const r=await fetch('/api/manual-live'+path,{cache:'no-store',...options,...(!confirmation?{signal:controller.signal}:{}),headers:{'Content-Type':'application/json','X-Fable-Manual':'owner-confirmation',...options.headers}});
        const data=await r.json();if(!r.ok){const error=Error(limitMessage(data.detail)||marginMessage(data.detail)||(data.detail?.status==='blocked'?data.detail.message:'')||friendly(typeof data.detail==='string'?data.detail:'请求未通过，请稍后重试。'));error.reference=data.detail?.reference;error.detail=data.detail;throw error;}return data;
      })();
      return await Promise.race([operation,new Promise((_,reject)=>{timeout=setTimeout(()=>{
        reject(Error('交易所响应较慢，暂未取得可用结果，请稍后重试。'));
        if(!confirmation)controller.abort();
      },path==='/preview'?90000:20000);})]);
    } catch(e) {if(e instanceof TypeError)throw Error('网络连接暂不可用，请检查网络后重试。');throw e;}
    finally{clearTimeout(timeout);options.signal?.removeEventListener('abort',abort);}
  }
  const flow=new Flow(request),ticketChart=window.SpikeTicketChart?new window.SpikeTicketChart():null;
  let dialog,timer,returnFocus,mode='entry',revision=0,scrollLock='',customStop=false;
  const $=id=>dialog.querySelector('#'+id);
  function init(){
    if(dialog)return;
    dialog=document.createElement('dialog');dialog.className='ml-dialog';dialog.setAttribute('aria-labelledby','ml-title');
    dialog.innerHTML=`<header class="ml-header"><div><div class="ml-eyebrow">OKX <span>均线密集专用账户</span></div><h2 id="ml-title">手动开仓</h2><p id="ml-context"></p></div><button type="button" id="ml-close" aria-label="关闭开仓面板">×</button></header><ol class="ml-steps" aria-label="下单进度"><li>1 <span>设置订单</span></li><li>2 <span>核对并确认</span></li><li>3 <span>成交与保护</span></li></ol><div id="ml-body"></div><footer id="ml-footer"></footer>`;
    document.body.append(dialog);$('ml-close').onclick=()=>dialog.close();
    dialog.addEventListener('close',()=>{clearTimeout(timer);ticketChart?.close();revision++;flow.reset();document.body.style.overflow=scrollLock;returnFocus?.focus?.({preventScroll:true});});
    dialog.addEventListener('click',async e=>{
      const b=e.target.closest('button');if(!b||b.disabled)return;
      if(b.id==='ml-preview')await preview();
      if(b.id==='ml-check'){b.disabled=true;await checkReadiness();}
      if(b.id==='ml-confirm'){const p=flow.confirm();renderState();await p;renderState();window.dispatchEvent(new Event('spike:manual-order-update'));}
      if(b.id==='ml-account'){dialog.close();document.querySelector('#yd-manual-account')?.scrollIntoView({block:'start',behavior:'smooth'});}
      if(b.id==='ml-history-back')await history(false);
      if(b.dataset.mlOrder){const fromHistory=mode==='history';mode='status';flow.reset();flow.sent=b.dataset.mlOrder;statusShell(fromHistory);renderState();await flow.status();renderState();}
    });
    dialog.addEventListener('change',e=>{
      if(!['ml-stop-mode','ml-rr'].includes(e.target.name)||flow.sent)return;
      flow.edit();
      if(e.target.name==='ml-stop-mode'){
        customStop=e.target.value==='custom';$('ml-stop-field').hidden=!customStop;
        $('ml-stop-hint').textContent=customStop?'做多止损应低于市价，做空止损应高于市价。':'按最新已收盘 5 分钟 K 线，止损放在六条均线之外 1 个最小价格单位。';
        if(customStop)$('ml-stop').focus();
      }
      renderState();
      if(!customStop||$('ml-stop').value.trim())preview();
    });
    dialog.addEventListener('input',e=>{if(e.target.id==='ml-stop'&&!flow.sent){flow.edit();$('ml-stop').removeAttribute('aria-invalid');renderState();}});
    dialog.addEventListener('keydown',e=>{if(e.key==='Enter'&&e.target.id==='ml-stop'){e.preventDefault();preview();}});
  }
  function show(){if(!dialog.open){scrollLock=document.body.style.overflow;document.body.style.overflow='hidden';dialog.showModal();}}
  function context(d=flow.ref){
    $('ml-title').textContent=coin(d?.symbol)+' / USDT';
    $('ml-context').innerHTML=`<span class="ml-direction ${d?.side==='short'?'short':'long'}">${d?.side==='short'?'做空':d?.side==='long'?'做多':'核对方向'}</span><span>${esc(d?.timeframe||'')} · 市价开仓</span>`;
  }
  function shell(ref){
    mode='entry';customStop=false;flow.reset(ref);context();dialog.querySelector('.ml-steps').hidden=false;
    $('ml-body').innerHTML=`<div id="ml-notice"></div><div class="ml-layout"><section class="ml-settings"><h3>设置订单</h3><p class="ml-subtitle">按你的风险规则自动计算仓位</p><div class="ml-policy"><span>每笔风险 · 1R</span><strong>3<small>%</small></strong><p>交易账户总权益 · 最大可用杠杆 · 全仓</p></div><fieldset class="ml-fieldset"><legend>止损方式</legend><div class="ml-segments"><label><input type="radio" name="ml-stop-mode" value="auto" checked><span>5m 六均线之外</span></label><label><input type="radio" name="ml-stop-mode" value="custom"><span>自定义价格</span></label></div><p id="ml-stop-hint" class="ml-hint">SMA / EMA 20、60、120；取已收盘 5m 值，${ref.side==='short'?'最高均线上方':'最低均线下方'} 1 个最小价格单位。</p><div id="ml-stop-field" hidden><label for="ml-stop">止损价格 <span>USDT</span></label><input id="ml-stop" type="text" inputmode="decimal" autocomplete="off" placeholder="输入止损价格" aria-describedby="ml-stop-hint"></div></fieldset><fieldset class="ml-fieldset"><legend>止盈目标</legend><div class="ml-rr-options"><label><input type="radio" name="ml-rr" value="3" checked><span><strong>3R</strong><small>默认目标</small></span></label><label><input type="radio" name="ml-rr" value="5"><span><strong>5R</strong><small>延伸目标</small></span></label></div></fieldset><div id="ml-order-summary"></div></section><section class="ml-review" aria-label="订单金额与状态"><div id="ml-state" aria-live="polite"></div></section></div>`;
  }
  function statusShell(fromHistory=false){
    dialog.querySelector('.ml-steps').hidden=false;
    $('ml-body').innerHTML=(fromHistory?'<button id="ml-history-back" class="ml-back">返回实盘记录</button>':'')+'<section class="ml-review ml-status-review"><div id="ml-state" aria-live="polite"></div></section>';
  }
  async function preview(){
    if(flow.sent||!flow.ref)return;
    const rr=Number(dialog.querySelector('[name="ml-rr"]:checked').value),stop=$('ml-stop').value.trim().replace(/^\./,'0.').replace(/\.$/,'');
    const error=validateTerms(stop,rr,customStop);
    if(error){flow.edit();flow.error=error;$('ml-stop').setAttribute('aria-invalid','true');renderState();$('ml-stop').focus();return;}
    ticketChart?.read.clear();ticketChart?.select(flow.ref.symbol);
    const terms={rr,...(customStop?{stop}:{})},p=flow.preview(terms),reference=flow.referenceFor(terms);
    renderState();await Promise.all([p.then(renderState),reference.then(renderState)]);
  }
  function renderState(){
    if(!dialog.open||!$('ml-state'))return;
    const previousChart=$('ml-position-chart');
    const d=flow.draft,display=flow.display(),referenceExpired=!!(display?.display_only&&Date.now()>=display.expires_ms);let state=d?.state||d?.queue_state;
    if(state==='preview'&&Date.now()>=d.expires_ms)state='expired';
    const sent=!!flow.sent,active=state==='active'&&d?.protection_verified,expired=state==='expired';
    dialog.dataset.stage=sent?'status':'entry';
    dialog.querySelector('.ml-layout')?.classList.toggle('is-submitted',sent);
    if(d)context(d);
    dialog.querySelectorAll('.ml-steps li').forEach((el,i)=>{const current=sent?2:d&&!expired?1:0;el.classList.toggle('is-current',i===current);if(i===current)el.setAttribute('aria-current','step');else el.removeAttribute('aria-current');});
    dialog.querySelectorAll('.ml-settings input').forEach(el=>{el.disabled=sent;});
    if(display&&$('ml-stop-hint')&&!customStop)$('ml-stop-hint').textContent=`止损 ${price(display.initial_stop)} · ${fmt(display.stop_candle?.close_ms)} 收盘值 · ${String(display.stop_candle?.boundary_key||'').toUpperCase()} ${price(display.stop_candle?.boundary_price)} 外侧 1 tick`;
    const status=sent&&state==='preview'?'提交待核验':active?'持仓中 · 保护已核验':state==='active'?'持仓中 · 等待保护核验':labels[state]||'订单预览';
    const error=flow.error?`<div class="ml-error" role="alert"><strong>${esc(sent?'正在核对提交结果':flow.blocker?.title||'请调整订单设置')}</strong><p>${esc(flow.error)}</p>${flow.blocker?`<div class="ml-blocking-orders">${(flow.blocker.issues||[]).filter(i=>i.id).map(i=>`<button type="button" data-ml-order="${esc(i.id)}">${esc(coin(i.symbol))} · 查看原订单</button>`).join('')}<button type="button" id="ml-check">重新检查</button></div>`:''}</div>`:'';
    const notice=$('ml-notice'),noticeHtml=error+(flow.recovered?'<p class="ml-recovered" role="status">账户已恢复，可以重新计算订单预览。</p>':'');
    if(notice)notice.innerHTML=noticeHtml;
    if($('ml-order-summary')&&!d)$('ml-order-summary').innerHTML='';
    if(!d){
      const rr=Number(dialog.querySelector('[name="ml-rr"]:checked')?.value)||3;
      const prices=[['止损',display?.initial_stop,'loss'],['参考市价',display?.price,'entry'],['止盈 · '+rr+'R',display?.take_profit,'profit']];
      $('ml-state').innerHTML=(notice?'':noticeHtml)+`<div class="ml-review-heading"><h3>开仓点位</h3><span class="ml-status">${flow.busy?'正在核对账户':flow.error?'暂不可开仓':referenceExpired?'点位已过期':'参考点位'}</span></div><section id="ml-position-chart" class="ml-position-chart" aria-label="K 线与多空盈亏区域"></section><div class="ml-prices ml-reference-prices">${prices.map(([name,value,tone])=>`<div class="${tone}"><span>${name}</span><strong>${price(value)}</strong></div>`).join('')}</div><p class="ml-hint">${referenceExpired?'参考点位已过期，请重新预览获取最新价格。':display?.price_error?'参考价已越过止损或止盈无效，请修改止损后重新预览。':display?'仅供核对点位，尚不可提交订单。':flow.referenceBusy?'正在读取参考市价与止盈止损点位…':esc(flow.referenceError||'输入有效止损后，可查看参考点位。')}</p>${flow.busy?'<p class="ml-hint" role="status">正在核对账户、仓位与可用保证金…</p>':''}`;

    }else{
      const prices=[['止损',d.stop??d.initial_stop,'loss'],[d.entry_price?'成交均价':'参考市价',d.entry_price??d.price,'entry'],['止盈 · '+d.rr+'R',d.take_profit,'profit']];
      const rows=[['预览时账户权益',num(d.total_equity_usdt)+' USDT'],['下单数量',num(d.size,8)+' 张'],['杠杆',num(d.leverage,0)+'× · 全仓'],['预计止损风险',num(d.actual_risk??d.planned_risk)+' USDT']];
      const orderSummary=`<div class="ml-money"><div><span>1R 风险预算</span><strong>${num(d.target_risk)} <small>USDT</small></strong></div><div><span>预计保证金</span><strong>${num(d.margin)} <small>USDT</small></strong></div></div><dl class="ml-facts">${rows.map(([name,value])=>`<div><dt>${name}</dt><dd>${value}</dd></div>`).join('')}</dl>`;
      if($('ml-order-summary'))$('ml-order-summary').innerHTML=sent?'':orderSummary;
      $('ml-state').innerHTML=(notice?'':error)+`<div class="ml-review-heading"><h3>${sent?'订单进度':'核对订单'}</h3><span class="ml-status ${active?'is-good':expired?'is-warning':''}">${esc(status)}</span></div><section id="ml-position-chart" class="ml-position-chart" aria-label="K 线与多空盈亏区域"></section><div class="ml-prices">${prices.map(([name,value,tone])=>`<div class="${tone}"><span>${esc(name)}</span><strong>${price(value)}</strong></div>`).join('')}</div>${sent?orderSummary:''}${sent?`<div class="ml-protection ${active?'is-good':''}"><strong>${active?'止损与止盈已挂好':state==='closed'?'本单已结束':terminal.has(state)?'本次未开仓':'正在核对成交与保护'}</strong><p>${active?'按实际成交均价计算止盈，后续使用固定止盈／止损。':state==='closed'&&d.exit_reason==='external_flattened'?'交易所已无持仓，原保护单已结束。外部合并平仓的收益不归入本单。':state==='closed'?`退出价格 ${price(d.exit_price)} · ${d.exit_reason==='owner_confirmed_close'?'手动平仓':d.exit_reason==='exchange_take_profit'?'止盈':d.exit_reason==='exchange_stop'?'止损':esc(d.exit_reason||'已退出')}`:terminal.has(state)?'本次请求已结束。重新开仓需要新的预览和确认。':'以交易所回执为准。关闭面板后，可在实盘记录继续查看。'}</p></div>`:''}${d.reason&&!terminal.has(state)?`<div class="ml-error"><p>${esc(friendly(d.reason))}</p></div>`:''}<details class="ml-details"><summary>计算规则与形态来源</summary><p>止损参考：${fmt(d.stop_candle?.open_ms)} — ${fmt(d.stop_candle?.close_ms)} 北京${d.stop_source==='manual'?'，已手动指定价格':''}。</p><p>形态确认：${fmt(d.detection?.input_close_ms)} · ${esc(d.timeframe)}，按当前行情开仓。</p><p>下单前重新核价，数量和保证金不超过本次预览；止盈按实际成交均价的 ${d.rr}R 校准。</p></details><p class="ml-risk-note">风险估算不含费用与滑点；最大杠杆可能先于止损触发强平。</p>`;
    }
    if(sent){
      const chartHost=$('ml-position-chart'),workspace=document.createElement('div'),details=document.createElement('aside');
      workspace.className='ml-ticket-workspace';details.className='ml-ticket-details';details.setAttribute('aria-label','订单金额与保护详情');
      chartHost.before(workspace);
      while(chartHost.nextSibling)details.append(chartHost.nextSibling);
      workspace.append(chartHost,details);
    }
    const button=flow.blocker&&!sent?'<button id="ml-account" class="ml-primary ml-outline">返回检测 · 查看持仓</button>':sent?'<button id="ml-account" class="ml-primary ml-outline">返回检测 · 跟踪订单</button>':d&&!expired?`<button id="ml-confirm" class="ml-primary ${d.side==='short'?'is-short':''}" ${flow.canConfirm()?'':'disabled'}>确认实盘${d.side==='long'?'做多':'做空'} ${esc(coin(d.symbol))}</button>`:`<button id="ml-preview" class="ml-primary" ${flow.busy?'disabled':''}>${flow.busy?'正在计算…':expired?'报价已过期 · 更新报价':flow.error?'重新预览订单':'计算并预览订单'}</button>`;
    $('ml-footer').innerHTML=`<div class="ml-footer-note"><strong>${flow.blocker&&!sent?'等待账户核验':sent?(flow.busy?'正在提交确认':terminal.has(state)?'本次请求已结束':d?.queue_state==='confirmed'?'确认已提交':'正在核对提交结果'):expired?'报价已过期':d?`保证金 ${num(d.margin,2)} · 止损风险 ${num(d.actual_risk??d.planned_risk,2)} USDT`:'预览不会开仓'}</strong><span id="ml-countdown">${flow.blocker&&!sent?'恢复后自动更新预览，仍需你确认':sent?(terminal.has(state)?'已自动更新至最终状态':'每 3 秒自动核对成交与保护'):d&&!expired?'':'系统计算仓位，逐笔由你确认'}</span></div>${button}`;
    const chartReference=display||flow.ref;
    const chartExpired=quoteExpired(display,sent);
    const chartKey=JSON.stringify([chartReference?.symbol,chartReference?.side,chartReference?.display_only,chartReference?.entry_price??chartReference?.price,chartReference?.stop??chartReference?.initial_stop,chartReference?.take_profit,chartReference?.rr,chartReference?.stop_candle,chartExpired]);
    if(previousChart&&previousChart.dataset.chartKey===chartKey)$('ml-position-chart')?.replaceWith(previousChart);
    else if(chartReference){const host=$('ml-position-chart');host.dataset.chartKey=chartKey;ticketChart?.mount(host,chartReference,chartExpired);}
    updateCountdown();schedule();
  }
  function updateCountdown(){
    if(flow.sent||!flow.draft||!$('ml-countdown'))return;
    const remaining=Math.max(0,Math.ceil((flow.draft.expires_ms-Date.now())/1000));
    $('ml-countdown').textContent=remaining?`本次报价还剩 ${remaining} 秒`:'请更新报价后再次确认';
  }
  function schedule(){
    clearTimeout(timer);if(!dialog.open)return;
    if(flow.sent&&!terminal.has(flow.draft?.state))timer=setTimeout(async()=>{await flow.status();renderState();},3000);
    else if(flow.blocker&&!flow.sent)timer=setTimeout(checkReadiness,5000);
    else if(!flow.sent&&flow.draft?.queue_state==='preview'&&Date.now()<flow.draft.expires_ms)timer=setTimeout(()=>{if(Date.now()>=flow.draft.expires_ms)renderState();else{updateCountdown();schedule();}},1000);
    else if(!flow.sent&&!flow.draft&&flow.display()?.expires_ms>Date.now())timer=setTimeout(renderState,flow.display().expires_ms-Date.now()+1);
  }
  async function checkReadiness(){
    const changed=await flow.readiness();
    if(!dialog.open)return;
    if(changed&&flow.recovered&&!flow.sent){await preview();return;}
    if(changed)renderState();else{const check=$('ml-check');if(check)check.disabled=false;schedule();}
  }
  async function open(ref){init();clearTimeout(timer);revision++;returnFocus=document.activeElement;shell(ref);show();await preview();}
  async function history(captureFocus=true){
    init();clearTimeout(timer);ticketChart?.close();const v=++revision;flow.reset();mode='history';if(captureFocus)returnFocus=document.activeElement;
    $('ml-title').textContent='实盘记录';$('ml-context').textContent='仅显示由你确认提交的系统订单';dialog.querySelector('.ml-steps').hidden=true;
    $('ml-body').innerHTML='<div class="ml-placeholder"><h3>正在读取订单…</h3></div>';$('ml-footer').innerHTML='<p class="ml-hint">你自行管理的持仓和挂单不会被接管。</p>';show();
    try{
      const data=await request('/recent');if(v!==revision||!dialog.open)return;
      $('ml-body').innerHTML=data.items.length?`<div class="ml-history">${data.items.map(d=>`<button data-ml-order="${esc(d.id)}"><div><strong>${esc(coin(d.symbol))} <span class="ml-direction ${d.side}">${d.side==='long'?'做多':'做空'}</span></strong><small>${fmt(d.created_ms)} · ${d.rr}R</small></div><span>${esc(d.state==='active'&&!d.protection_verified?'等待保护核验':labels[d.state]||d.state)}</span></button>`).join('')}</div>`:'<div class="ml-placeholder"><h3>还没有实盘订单</h3><p>在形态旁点击「预览开仓」，确认后的订单会出现在这里。</p></div>';
    }catch(e){if(v===revision&&dialog.open)$('ml-body').innerHTML=`<div class="ml-error" role="alert"><p>${esc(e.message)}</p><button id="ml-history-back">重新读取</button></div>`;}
  }
  async function openStatus(identity){
    init();clearTimeout(timer);revision++;returnFocus=document.activeElement;mode='status';flow.reset();flow.sent=identity;$('ml-title').textContent='订单详情';$('ml-context').textContent='正在读取成交与保护状态';statusShell();show();renderState();await flow.status();renderState();
  }
  window.SpikeManualLive={open,history,openStatus,close:()=>dialog?.open&&dialog.close()};
  window.addEventListener('hashchange',()=>{if(!location.hash.startsWith('#yolo-detect'))window.SpikeManualLive.close();});
})();
