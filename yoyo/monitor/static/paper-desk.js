/* Manual local paper trading. Fresh quote snapshots, durable retry IDs. */
(() => {
  'use strict';
  const endpoint = '/api/research/paper-desk';
  const entryLeverages = [1,2,5,10,15,20,30,50,100];
  const defaultEntryLeverage = 100;
  const defaultPositionMultiple = 10;
  const historyView = {kind:'balance',span:'all',month:'',day:'',cursor:null};
  let historyData=null, historyError='', historyLoading=null;
  const retryKey = 'spike-paper-desk-pending-v1';
  const state = {revision:0, active:false, activation:0, data:null, account:'', busy:false, refreshing:false, quote:null, estimateQuote:null, estimateLoading:null, estimateError:'', estimateAttempt:null, previewVisible:false, createVisible:false, draft:{}, closeId:null, timer:null, pending:null, symbols:null, symbolsError:'', pickerOpen:false, symbolSearch:'', symbolIndex:0, tab:'positions', tradeMode:'open', createOpen:false, stopsExpanded:false};
  let records={account:'',kind:'',offset:0,data:null,loading:null,error:'',revision:0};
  let journal=null, journalFocus=null;
  const journalDrafts=new Map();
  const journalFields=[['entry_reason','开单逻辑','为什么在这里开仓？记录信号、触发条件和判断依据。'],['analysis','行情分析','趋势、关键位置、量价表现，以及不同周期的判断。'],['plan','交易计划','预期走势、止盈止损依据、风险与退出条件。'],['review','复盘记录','执行与计划的差异、做对的地方、下次改进。']];
  const journalContent=value=>Object.fromEntries(journalFields.map(([key])=>[key,value?.[key]||'']));
  const journalDirty=j=>j?.data&&journalFields.some(([key])=>j.draft[key]!==j.data.journal[key]);
  function rememberJournal(j){
    if(!j?.data)return;
    if(journalDirty(j))journalDrafts.set(j.id,{draft:{...j.draft},version:j.data.journal.version});
    else journalDrafts.delete(j.id);
  }
  function redrawJournal(){root()?.querySelector('#desk-journal-dialog')?.remove();render();}
  async function openJournal(id){
    if(state.busy||journal?.saving||journal?.uploading)return;
    const j={id,data:null,draft:{},loading:true,saving:false,error:'',latest:null};
    journal=j;journalFocus=id;state.pickerOpen=false;state.protection=null;state.createOpen=false;
    clearFeedback();redrawJournal();
    try{
      const data=await request(`/positions/${encodeURIComponent(id)}/journal`);
      if(journal!==j)return;
      j.data=data;
      const saved=journalDrafts.get(id);
      j.draft=saved?{...saved.draft}:journalContent(data.journal);
      if(saved&&saved.version!==data.journal.version){
        j.latest=data;
        j.data={...data,journal:{...data.journal,version:saved.version}};
      }
    }catch(error){if(journal===j)j.error=error.message;}
    finally{if(journal===j){j.loading=false;redrawJournal();}}
  }
  function closeJournal(){
    if(!journal||journal.saving||journal.uploading)return;
    rememberJournal(journal);journal=null;render();
  }
  async function saveJournal(){
    const j=journal;if(!j?.data||j.saving||j.uploading||j.latest)return;
    j.saving=true;j.error='';clearFeedback('error');redrawJournal();
    try{
      const data=await request(`/positions/${encodeURIComponent(j.id)}/journal`,{version:j.data.journal.version,...j.draft});
      j.data=data;j.draft=journalContent(data.journal);rememberJournal(j);
      if(journal===j)notify('success','开单日志已保存。');
    }catch(error){
      rememberJournal(j);
      if(journal===j){
        if(error.status===409){
          try{j.latest=await request(`/positions/${encodeURIComponent(j.id)}/journal`);}catch(_){/* Keep the draft and allow retry. */}
        }
        if(journal===j)notify('error',error.message);
      }
    }finally{j.saving=false;if(journal===j&&state.active)redrawJournal();}
  }
  async function uploadJournalImages(files){
    const j=journal;if(!j?.data||j.saving||j.uploading)return;
    j.uploading=true;redrawJournal();
    try{
      for(const file of files){
        if(!['image/png','image/jpeg','image/webp'].includes(file.type)||file.size>6*1024*1024)throw new Error('请上传不超过 6 MB 的 PNG、JPEG 或 WebP 图片。');
        const response=await fetch(endpoint+`/positions/${encodeURIComponent(j.id)}/images`,{method:'POST',headers:{'Content-Type':file.type,'X-Filename':encodeURIComponent(file.name||'截图.png')},body:file});
        const data=await response.json();if(!response.ok)throw new Error(typeof data.detail==='string'?data.detail:'图片上传失败');
        j.data.images=data;
      }
      if(journal===j)notify('success','图片已保存到这一单的日志。');
    }catch(error){if(journal===j)notify('error',error.message);}
    finally{j.uploading=false;if(journal===j&&state.active)redrawJournal();}
  }
  async function setJournalImage(id,deleted){
    const j=journal;if(!j?.data||j.saving||j.uploading)return;
    j.uploading=true;redrawJournal();
    try{j.data.images=await request(`/positions/${encodeURIComponent(j.id)}/images/${encodeURIComponent(id)}/visibility`,{deleted});}
    catch(error){if(journal===j)notify('error',error.message);}
    finally{j.uploading=false;if(journal===j&&state.active)redrawJournal();}
  }
  function journalImages(j){
    const images=j.data?.images||[];
    return `<section class="desk-journal-images" aria-label="日志图片"><div class="desk-position-head"><strong>图片记录</strong><label class="research-button desk-image-upload">${j.uploading?'上传中…':'添加图片'}<input type="file" data-desk-journal-images multiple accept="image/png,image/jpeg,image/webp" ${j.saving||j.uploading?'disabled':''}></label></div><p class="desk-muted">截图、形态和复盘图，可直接粘贴。每张 6 MB，最多 12 张；图片上传后立即保存。</p><div class="desk-image-grid">${images.filter(i=>!i.deleted).map(i=>`<figure><a href="${endpoint}/positions/${encodeURIComponent(j.id)}/images/${encodeURIComponent(i.id)}" target="_blank" rel="noopener" aria-label="查看原图 ${esc(i.name)}"><img src="${endpoint}/positions/${encodeURIComponent(j.id)}/images/${encodeURIComponent(i.id)}" alt="${esc(i.name)}" loading="lazy"></a><figcaption><span>${esc(i.name)}</span><button type="button" data-desk-image-remove="${esc(i.id)}" ${j.uploading?'disabled':''}>移除</button></figcaption></figure>`).join('')}</div>${images.some(i=>i.deleted)?`<details class="desk-images-removed"><summary>已移除的图片</summary>${images.filter(i=>i.deleted).map(i=>`<p>${esc(i.name)} <button type="button" class="research-button" data-desk-image-restore="${esc(i.id)}" ${j.uploading?'disabled':''}>恢复</button></p>`).join('')}</details>`:''}</section>`;
  }
  function journalDialog(){
    if(!state.active||!journal)return '';
    const j=journal,p=j.data?.position;
    const facts=p?[['开仓时间',stamp(p.opened_ms)],['开仓均价',price(p.entry_price)+' USDT'],['累计开仓数量',qty(p.quantity)],['剩余数量',qty(p.remaining)],['手续费',money(p.fees)+' USDT'],['平仓时间',p.closed_ms?stamp(p.closed_ms):'持仓中']]:[];
    return `<dialog id="desk-journal-dialog" class="desk-confirm-dialog desk-journal-dialog" aria-labelledby="desk-journal-title">
      <header class="desk-confirm-header"><div><div class="desk-confirm-eyebrow">交易笔记 · ${esc(j.data?.account_name||'模拟账户')}</div><h2 id="desk-journal-title">开单日志</h2></div><button type="button" class="desk-dialog-close" data-desk-journal-close aria-label="关闭开单日志" ${j.saving||j.uploading?'disabled':''}>×</button></header>
      ${j.loading?'<div class="desk-confirm-body" role="status">正在加载开单日志…</div>':j.error?`<div class="desk-confirm-body"><p role="alert">${esc(j.error)}</p><button type="button" class="research-button" data-desk-journal-retry>重新加载</button></div>`:`<form data-desk-journal-form>
        <div class="desk-confirm-body">
          <div class="desk-confirm-contract"><strong>${esc(p.symbol.replace('-USDT-SWAP',' / USDT'))}</strong><span class="desk-confirm-side desk-${p.side}">${direction(p.side)} · ${p.leverage}× · ${Number(p.remaining)>0?'持仓中':'已平仓'}</span></div>
          <details class="desk-journal-facts"><summary>交易详情 · ${stamp(p.opened_ms)}</summary><dl>${facts.map(([label,value])=>`<div><dt>${label}</dt><dd>${esc(value)}</dd></div>`).join('')}</dl></details>
          ${j.latest?`<section class="desk-journal-conflict" aria-label="日志版本冲突"><strong>另一页已更新这笔日志</strong><p>下方编辑区保留了你的草稿。对照最新内容后，选择要保留的版本。</p><details><summary>查看最新内容 · ${stamp(j.latest.journal.updated_ms)}</summary>${journalFields.map(([key,label])=>`<h3>${label}</h3><p class="desk-journal-latest">${esc(j.latest.journal[key]||'（空）')}</p>`).join('')}</details><div class="desk-actions"><button type="button" class="research-button" data-desk-journal-use-latest>采用最新内容</button><button type="button" class="research-button" data-desk-journal-keep-draft>保留我的内容并保存</button></div></section>`:''}
          <div class="desk-journal-fields">${journalFields.map(([key,label,hint],i)=>`<label class="desk-field"><span><b>${String(i+1).padStart(2,'0')}</b>${label}</span><textarea name="journal_${key}" rows="4" maxlength="10000" placeholder="${hint}" ${j.saving||j.uploading?'disabled':''}>${esc(j.draft[key])}</textarea></label>`).join('')}</div>${journalImages(j)}
        </div>
        <footer class="desk-confirm-footer desk-journal-footer"><span data-desk-journal-status role="status">${j.saving?'正在保存…':journalDirty(j)?'未保存 · 关闭后草稿在本页保留':j.data.journal.updated_ms?'已保存 · '+stamp(j.data.journal.updated_ms):'尚未保存'}</span><button type="submit" class="research-button primary" ${j.saving||j.uploading||j.latest?'disabled':''}>${j.saving?'保存中…':'保存日志'}</button></footer>
      </form>`}${feedbackMarkup()}</dialog>`;
  }

  const livePath=()=>'/live'+(state.account?'?account_id='+encodeURIComponent(state.account):'');
  function applySnapshot(data){state.data=data;state.account=data.active_account_id||state.account||data.accounts[0]?.id||'';}
  function resetRecords(){records={account:'',kind:'',offset:0,data:null,loading:null,error:'',revision:records.revision+1};}
  function recordRows(kind,fallback){return state.data?.view==='live'?(records.account===state.account&&records.kind===kind?records.data?.items||[]:[]):fallback;}
  function recordFooter(){
    if(state.data?.view!=='live'||!['closed','fills'].includes(state.tab))return '';
    const current=records.account===state.account&&records.kind===state.tab;
    if(!current||records.loading)return '<p class="desk-muted" role="status">正在加载记录…</p>';
    if(records.error)return `<p class="desk-short" role="alert">${esc(records.error)} <button class="research-button" data-desk-record-retry>重试</button></p>`;
    const data=records.data;if(!data)return '';
    return `<div class="desk-actions" aria-label="记录分页"><button class="research-button" data-desk-record-page="${Math.max(0,data.offset-data.limit)}" ${data.offset===0?'disabled':''}>上一页</button><span class="desk-muted">第 ${Math.floor(data.offset/data.limit)+1} 页 · 共 ${data.total} 条</span><button class="research-button" data-desk-record-page="${data.offset+data.limit}" ${data.has_more?'':'disabled'}>下一页</button></div>`;
  }
  async function loadRecords(force=false){
    if(!state.active||state.data?.view!=='live'||!state.account||!['closed','fills'].includes(state.tab))return;
    if(records.account!==state.account||records.kind!==state.tab){resetRecords();records.account=state.account;records.kind=state.tab;}
    const current=records;
    if(current.loading||(!force&&(current.error||current.data&&Date.now()-current.loadedAt<30000)))return;
    const token={};current.loading=token;current.error='';
    try{
      const data=await request(`/accounts/${encodeURIComponent(current.account)}/records?kind=${current.kind}&offset=${current.offset}&limit=50`);
      if(records!==current)return;
      current.data=data;current.loadedAt=Date.now();
    }catch(error){if(records===current)current.error=error.message;}
    finally{if(records===current){current.loading=null;if(state.active)render();}}
  }
  let quickMount=null;
  const root = () => document.getElementById('paper-desk-workspace');
  const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  let feedback=[], feedbackSequence=0, backgroundError='';
  const feedbackTimers=new Map();
  function feedbackItems() {
    return feedback.map(item=>`<div class="desk-toast desk-toast-${item.kind}" role="${item.kind==='error'?'alert':'status'}" aria-atomic="true"><span class="desk-toast-icon" aria-hidden="true">${item.kind==='error'?'!':'✓'}</span><p>${esc(item.message)}</p><button type="button" data-desk-dismiss-feedback="${item.id}" aria-label="关闭提示">×</button></div>`).join('');
  }
  function feedbackMarkup() {
    return `<div class="desk-feedback" data-desk-feedback popover="manual" aria-label="操作提示">${feedbackItems()}</div>`;
  }
  function updateFeedback() {
    const host=root()?.querySelector('[data-desk-feedback]');
    if(!host)return;
    const markup=feedbackItems();
    if(host.innerHTML!==markup)host.innerHTML=markup;
    if(feedback.length)host.showPopover?.();else host.hidePopover?.();
  }
  function dismissFeedback(id) {
    clearTimeout(feedbackTimers.get(id));feedbackTimers.delete(id);
    feedback=feedback.filter(item=>item.id!==id);updateFeedback();
  }
  function clearFeedback(kind) {
    for(const item of [...feedback])if(!kind||item.kind===kind)dismissFeedback(item.id);
  }
  function notify(kind,message) {
    if(!state.active)return;
    clearFeedback(kind);
    const id=++feedbackSequence;
    feedback.push({id,kind,message});updateFeedback();
    // Polling and other renders must not restart a success message's lifetime.
    // Errors remain available until dismissed or a new action replaces them.
    if(kind==='success')feedbackTimers.set(id,setTimeout(()=>dismissFeedback(id),5000));
  }
  const money = value => value == null ? '—' : Number(value).toLocaleString('zh-CN',{useGrouping:false,maximumFractionDigits:4});
  // Round position-card amounts for display only; sizing uses original values.
  const wholeMoney = value => value == null ? '—' : Number(value).toLocaleString('zh-CN',{useGrouping:false,maximumFractionDigits:0});
  const qty = value => value == null ? '—' : Number(value).toLocaleString('zh-CN',{useGrouping:false,maximumFractionDigits:12});
  const price = value => value == null ? '—' : Number(value).toLocaleString('zh-CN',{useGrouping:false,maximumFractionDigits:Number(value)>=100?2:Number(value)>=1?4:8});
  const stamp = value => value ? new Date(value).toLocaleString('zh-CN',{hour12:false}) : '—';
  const orderStatus = status => ({pending:'待成交',filled:'已成交',cancelled:'已撤销',rejected:'已拒绝'}[status]||status);
  const protectionText = value => `止盈 ${value.take_profit?qty(value.take_profit):'—'} / 止损 ${value.stop_loss?qty(value.stop_loss):'—'}`;
  const exitLabel = fill => fill.exit_reason==='liquidation'?'全仓强平':fill.exit_reason==='take_profit'?'止盈自动平仓':fill.exit_reason==='stop_loss'?'止损自动平仓':fill.order_id?'限价':'市价';
  const direction = side => side === 'long' ? '做多' : '做空';
  const account = () => state.data?.accounts.find(a => a.id === state.account);
  const positions = () => (state.data?.positions || []).filter(p => p.account_id === state.account);
  const closePercent = () => Number(state.draft.closePercent||100);
  const protectedPosition = () => positions().find(p=>p.id===state.protection?.id);
  const leverageOptions = () => entryLeverages.filter(n=>n<=((state.symbols||[]).find(s=>s.symbol===state.draft.symbol)?.max_leverage??(['BTC-USDT-SWAP','ETH-USDT-SWAP'].includes(state.draft.symbol||'BTC-USDT-SWAP')?100:20)));
  const activeLeverage = () => Math.min(Number(state.draft.leverage||defaultEntryLeverage),Math.max(...leverageOptions()));
  function feeEstimate() {
    const taker=Number(state.data?.taker_fee_rate??state.data?.fee_rate),maker=Number(state.data?.maker_fee_rate??state.data?.fee_rate);
    if(state.draft.orderType!=='limit')return {rate:taker,role:'Taker'};
    const q=state.estimateQuote,long=state.draft.side!=='short',price=Number(state.draft.limitPrice);
    if(!freshEstimateQuote(q)||!validPrice(price))return {rate:taker,role:'最高 Taker'};
    const crosses=long?price>=Number(q.ask):price<=Number(q.bid);
    return {rate:crosses?taker:maker,role:crosses?'Taker':'Maker'};
  }
  function historyMarkup() {
    return window.SpikePaperHistory?.render(historyData?.account_id===state.account?historyData:null,historyView,historyError)||'';
  }
  function updateHistory() {
    const el=root()?.querySelector('[data-desk-history]');
    if(el){
      const focused=el.querySelector('[data-desk-chart-plot]')===document.activeElement;
      el.innerHTML=historyMarkup();
      if(focused)el.querySelector('[data-desk-chart-plot]')?.focus({preventScroll:true});
    }
  }
  function positionMultipleLabel(notional) {
    const equity=Number(account()?.equity),amount=Number(notional);
    if(notional==null||!Number.isFinite(amount)||amount<0||!Number.isFinite(equity)||equity<=0)return '仓位 —';
    const multiple=amount/equity;
    return `仓位 ${multiple>0&&multiple<.01?'<0.01':multiple.toLocaleString('zh-CN',{useGrouping:false,maximumFractionDigits:2})}×`;
  }

  const workerNotice = () => {
    const worker=state.data?.limit_worker;
    return worker?.error?`<p role="alert" class="desk-worker-warning">${esc(worker.error)}</p>`:worker?.running===false?'<p role="alert" class="desk-worker-warning">自动检查未运行，挂单和止盈止损暂不触发。</p>':'';
  };
  function expandedRecords() {return [...(root()?.querySelectorAll?.('.desk-related-orders[open]')||[])].map(node=>node.dataset.position);}
  function restoreRecords(ids) {for(const node of root()?.querySelectorAll?.('.desk-related-orders')||[])if(ids.includes(node.dataset.position))node.open=true;}
  function updateLiveSnapshot() {
    const expanded=expandedRecords();
    const a=account();if(!a)return;
    const values=[a.equity,a.available,a.reserved,a.order_reserved||'0',a.unrealized,a.net_realized];
    for(const [i,node] of [...(root()?.querySelectorAll?.('.desk-metrics article strong')||[])].entries()){
      node.textContent=money(values[i]);
      if(i>=4)node.className=Number(values[i])<0?'desk-short':Number(values[i])>0?'desk-long':'';
    }
    const content=root()?.querySelector('.desk-ledger-content');
    if(content)content.innerHTML=(state.tab==='orders'?ordersPanel():state.tab==='fills'?fillsPanel():state.tab==='closed'?closedPanel():positionsPanel(positions().filter(p=>Number(p.remaining)>0)))+recordFooter();
    restoreRecords(expanded);
    const status=root()?.querySelector('[data-desk-worker-alert]');if(status)status.innerHTML=workerNotice();
    const risk=root()?.querySelector('.desk-risk-strip');
    if(risk)risk.innerHTML=`<span>全仓保证金 · ${a.risk_complete?'标记价格风控':'风险数据待更新，自动强平暂不可用'}</span><span>维持保证金 <strong>${money(a.maintenance)} USDT</strong> · 保证金率 <strong>${a.margin_ratio!=null?money(Number(a.margin_ratio)*100)+'%':'—'}</strong></span>`;
  }
  async function loadHistory(force=false) {
    if(!state.active||quickMount||!state.account||!window.SpikePaperHistory)return;
    const id=state.account,month=historyView.month,revision=state.revision,token=id+':'+month+':'+revision;
    if(historyLoading===token)return;
    if(!force&&historyData?.account_id===id&&Date.now()-historyData.loadedAt<30000&&(!month||historyData.month===month))return;
    historyLoading=token;
    try {
      const data=await request(`/accounts/${encodeURIComponent(id)}/history${month?'?month='+encodeURIComponent(month):''}`);
      if(id!==state.account||month!==historyView.month||revision!==state.revision)return;
      historyData={...data,loadedAt:Date.now()};historyView.month=data.month;historyError='';
    }catch(error){if(id===state.account&&revision===state.revision)historyError=error.message;}
    finally{if(historyLoading===token){historyLoading=null;updateHistory();}}
  }
  function closeSizingText(p) {
    const percentage=closePercent(), remaining=Number(p.remaining), available=Number(p.available_to_close??p.remaining);
    if(!Number.isInteger(percentage)||percentage<1||percentage>100)return '请输入 1–100 的平仓百分比';
    const amount=percentage===100?remaining:Math.floor(remaining*percentage/100*1e12)/1e12;
    return `预计平仓 ${qty(amount)} / 持仓 ${qty(p.remaining)} 个${amount>available?' · 超出可平数量，请先撤销平仓挂单':''}`;
  }
  function closeControls(p) {
    return `<section class="desk-close-sizing" aria-label="平仓比例">${field('closePercent','平仓比例 · %',String(closePercent()),'number','required min="1" max="100" step="1" inputmode="numeric"')}<div class="desk-percent-options">${[25,50,75,100].map(n=>`<button type="button" data-desk-percent="${n}" aria-pressed="${closePercent()===n}" ${state.busy?'disabled':''}>${n}%</button>`).join('')}</div><p data-desk-close-summary aria-live="polite">${closeSizingText(p)}</p><small>按当前持仓计算；已挂单占用 ${qty(p.pending_close_quantity||0)} 个。</small></section>`;
  }
  function heldProtectionEstimate(name) {
    const p=protectedPosition(), value=state.protection?.[name];
    if(!p||Number(p.remaining)<=0)return '持仓已平仓';
    if(!value)return '留空即取消此项';
    if(!validPrice(value))return '请输入有效价格';
    const current=Number(p.mark), long=p.side==='long', above=(name==='takeProfit')===long;
    if(!p.mark_ms||Date.now()-p.mark_ms>15000||!validPrice(current))return '等待新鲜行情，暂无法校验';
    if(above?Number(value)<=current:Number(value)>=current)return `须${above?'高':'低'}于当前价 ${qty(current)}`;
    const rate=Number(state.data?.fee_rate), entry=Number(p.entry_price), exit=Number(value), remaining=Number(p.remaining);
    if(!Number.isFinite(rate))return '等待有效费率';
    const net=(exit-entry)*remaining*(long?1:-1)-exit*remaining*rate-Number(p.entry_fees_remaining??entry*remaining*rate);
    return `预计净${net<0?'亏':'赚'} <strong class="${net<0?'desk-short':'desk-long'}">${net<0?'−':'+'}${Math.abs(net).toLocaleString('zh-CN',{useGrouping:false,minimumFractionDigits:2,maximumFractionDigits:2})} USDT</strong>`;
  }
  function updatePositionControls() {
    const p=positions().find(p=>p.id===state.closeId), summary=root()?.querySelector('[data-desk-close-summary]');
    if(p&&summary)summary.textContent=closeSizingText(p);
    for(const option of root()?.querySelectorAll?.('[data-desk-percent]')||[])option.setAttribute('aria-pressed',String(Number(option.dataset.deskPercent)===closePercent()));
    const available=root()?.querySelector('.desk-available strong');
    if(p&&available&&state.tradeMode==='close')available.textContent=qty(p.available_to_close??p.remaining);
    const current=root()?.querySelector('[data-desk-protection-current]'), held=protectedPosition();
    if(current)current.textContent=held&&Number(held.remaining)>0?`当前可平仓价 ${qty(held.mark)} USDT · 剩余 ${qty(held.remaining)} 个`:'持仓已平仓，请关闭弹窗';
    for(const name of ['takeProfit','stopLoss']){
      const output=root()?.querySelector(`[data-desk-held-estimate="${name}"]`);
      if(output)output.innerHTML=heldProtectionEstimate(name);
    }
    const save=root()?.querySelector('[data-desk-save-protection]');
    if(save)save.disabled=state.busy||Boolean(state.pending)||!held||Number(held.remaining)<=0;
  }
  function protectionDialog() {
    if(!state.active||!state.protection||(state.pending&&!state.busy))return '';
    const p=protectedPosition();
    if(!p)return '';
    return `<dialog id="desk-protection-dialog" class="desk-confirm-dialog" aria-labelledby="desk-protection-title"><form data-desk-protection-form><header class="desk-confirm-header"><div><span class="desk-confirm-eyebrow">持仓管理</span><h2 id="desk-protection-title">止盈 / 止损</h2></div><button type="button" class="desk-dialog-close" data-desk-dismiss-protection aria-label="关闭止盈止损" ${state.busy?'disabled':''}>×</button></header><div class="desk-confirm-body"><div class="desk-confirm-contract"><strong>${esc(p.symbol.replace('-USDT-SWAP',' / USDT'))}</strong><span class="desk-${p.side}">${direction(p.side)} · ${p.leverage}×</span></div><p class="desk-muted" data-desk-protection-current>当前可平仓价 ${qty(p.mark)} USDT · 剩余 ${qty(p.remaining)} 个</p><div class="desk-stops-grid">${['takeProfit','stopLoss'].map((name,i)=>field('held'+name,i?'止损价 · USDT':'止盈价 · USDT',state.protection[name]||'','number','min="0.000000000001" max="1000000000000" step="any" inputmode="decimal" placeholder="不设置"',`<output class="desk-stop-estimate" data-desk-held-estimate="${name}" aria-live="polite">${heldProtectionEstimate(name)}</output>`)).join('')}</div><p class="desk-stops-hint">保护全部剩余仓位，部分平仓后继续生效。留空可取消对应设置；止损可移至盈利区间。预计盈亏含剩余仓位对应的开平仓费。</p><p class="desk-stops-hint">触价后按当时报价自动平仓，并撤销该仓位的平仓挂单。</p></div><footer class="desk-confirm-footer"><button type="button" class="research-button" data-desk-clear-protection ${state.busy?'disabled':''}>清空设置</button><button class="desk-submit desk-long" data-desk-save-protection ${state.busy||Number(p.remaining)<=0?'disabled':''}>${state.busy?'保存中…':'保存设置'}</button></footer></form>${feedbackMarkup()}</dialog>`;
  }
  async function saveProtection(form) {
    if(state.busy||state.pending||!state.protection)return;
    const values=new FormData(form), id=state.protection.id, activation=state.activation;
    const takeProfit=String(values.get('heldtakeProfit')||'').trim(), stopLoss=String(values.get('heldstopLoss')||'').trim();
    state.protection={id,takeProfit,stopLoss};clearFeedback('error');
    if([takeProfit,stopLoss].some(v=>v&&!validPrice(v))){notify('error','请输入有效正数价格，或留空取消。');render();return;}
    const p=protectedPosition();
    if(!p||Number(p.remaining)<=0){notify('error','该持仓已平仓。');render();return;}
    state.busy=true;render();
    try {
      const q=takeProfit||stopLoss?await request('/quote?symbol='+encodeURIComponent(p.symbol)):null;
      if(!state.active||activation!==state.activation||state.protection?.id!==id)return;
      state.busy=false;
      await execute({path:`/positions/${encodeURIComponent(id)}/protection`,payload:{take_profit:takeProfit||null,stop_loss:stopLoss||null,quote_id:q?.id||null,request_id:crypto.randomUUID()}});
    }catch(error){notify('error',error.message);}
    finally{state.busy=false;render();}
  }
  function entryStopState(p) {
    const sign=p.side==='long'?1:-1,entry=Number(p.entry_price),stop=Number(p.stop_loss);
    if(p.stop_loss!=null&&(stop-entry)*sign>=0)return {disabled:true,label:stop===entry?'已到开仓价':'已有更优止损',hint:'现有止损已在开仓价或更有利的位置，保留原设置'};
    const fresh=p.mark_ms!=null&&Date.now()-Number(p.mark_ms)<=15000&&Date.now()-Number(p.mark_ms)>=-5000;
    if(!fresh||!validPrice(p.mark)||!validPrice(p.entry_price))return {disabled:true,label:'止损到开仓价',hint:'等待新鲜报价后可用'};
    if((Number(p.mark)-entry)*sign<=0)return {disabled:true,label:'止损到开仓价',hint:'当前可平仓价须越过开仓价，避免设置后立即触发'};
    return {disabled:false,label:'止损到开仓价',hint:'一键将止损设为当前开仓均价 '+qty(p.entry_price)+'，保留止盈；不含手续费'};
  }
  function entryStopButton(p) {
    const s=entryStopState(p);
    return `<button type="button" class="research-button desk-entry-stop" data-desk-entry-stop="${esc(p.id)}" title="${esc(s.hint)}" aria-label="${esc(p.symbol)}：${esc(s.label)}，${esc(s.hint)}" ${state.busy||state.pending||s.disabled?'disabled':''}><svg viewBox="0 0 20 20" aria-hidden="true"><path d="m10 2 6 3v5c0 4-6 7-6 7s-6-3-6-7V5Z"/><path d="m7 10 2 2 4-4"/></svg>${s.label}</button>`;
  }
  async function moveStopToEntry(id) {
    if(state.busy||state.pending)return;
    const p=positions().find(p=>p.id===id),activation=state.activation;
    if(!p||entryStopState(p).disabled)return;
    state.busy=true;clearFeedback('error');render();
    try{
      const q=await request('/quote?symbol='+encodeURIComponent(p.symbol));
      if(!state.active||activation!==state.activation)return;
      state.busy=false;
      await execute({path:`/positions/${encodeURIComponent(id)}/protection`,payload:{stop_to_entry:true,quote_id:q.id,request_id:crypto.randomUUID()}});
    }catch(error){notify('error',error.message);}
    finally{state.busy=false;render();}
  }
  const button = (label, action, cls='') => `<button type="button" class="research-button ${cls}" data-desk-${action} ${state.busy?'disabled':''}>${label}</button>`;
  function field(name,label,value='',type='text',extra='',suffix='') {
    return `<label class="desk-field"><span>${label}</span><input name="${name}" type="${type}" value="${esc(value)}" ${state.busy?'disabled':''} ${extra}>${suffix}</label>`;
  }
  const validPrice = value => Number.isFinite(Number(value))&&Number(value)>=1e-12&&Number(value)<=1e12;
  const freshEstimateQuote = q => q?.symbol===state.draft.symbol&&Number.isFinite(q.expires_ms)&&q.expires_ms>Date.now()&&validPrice(q.bid)&&validPrice(q.ask);
  const fixedNotional=()=>state.draft.sizingMode==='notional';
  const entrySizeLabel=()=>fixedNotional()?`仓位 ${money(state.draft.notionalUsdt||0)} USDT`:`仓位 ${Number(state.draft.positionMultiple||defaultPositionMultiple)}×`;
  function exposureSizing() {
    const a=account(), rate=feeEstimate().rate, leverage=activeLeverage(), multiple=Number(state.draft.positionMultiple||defaultPositionMultiple);
    if(!a||a.available==null||a.equity==null||!Number.isFinite(Number(a.available))||!Number.isFinite(Number(a.equity)))return {error:'账户权益或可用资金暂不可用'};
    if(!Number.isFinite(state.data?.now_ms)||Date.now()-state.data.now_ms>15000)return {error:'资金待刷新，暂无法估算'};
    if(state.data?.fee_rate==null||!Number.isFinite(rate)||rate<0||!entryLeverages.includes(leverage)||(!fixedNotional()&&!entryLeverages.includes(multiple)))return {error:'等待有效费率与倍数'};
    if(Number(a.equity)<=0)return {error:'账户权益不足'};
    const notional=fixedNotional()?Number(state.draft.notionalUsdt):Number(a.equity)*multiple;
    if(!validPrice(notional))return {error:'请输入有效仓位金额（USDT）'};
    const margin=notional/leverage, fee=notional*rate, remaining=Number(a.available)-margin-fee;
    return {notional,margin,fee,remaining,rate,leverage,multiple};
  }
  function sizingSummary() {
    const s=exposureSizing();
    if(s.error)return `<p class="desk-sizing-note">${esc(s.error)}</p>`;
    return `<dl>${fixedNotional()?`<div><dt>相当于账户仓位</dt><dd>${money(s.notional/Number(account()?.equity))}×</dd></div>`:''}<div><dt>仓位金额</dt><dd>${money(s.notional)} USDT</dd></div><div><dt>保证金 + ${feeEstimate().role} 费</dt><dd>${money(s.margin)} + ${money(s.fee)} USDT</dd></div><div><dt>${s.remaining<0?'资金缺口':'下单后可用'}</dt><dd class="${s.remaining<0?'desk-short':''}">${money(Math.abs(s.remaining))} USDT</dd></div></dl>${s.remaining<0?'<p class="desk-short">资金不足，请调低仓位金额或提高合约杠杆。</p>':''}`;
  }
  function protectionEstimate(name) {
    const value=state.draft[name], a=account();
    if(!value)return '<span>输入价格后计算</span>';
    if(!validPrice(value))return '<span>请输入有效价格</span>';
    if(!a||a.available==null||!Number.isFinite(Number(a.available)))return '<span>可用资金暂不可用</span>';
    if(Number(a.available)<=0)return '<span>暂无可用资金</span>';
    if(!Number.isFinite(state.data?.now_ms)||Date.now()-state.data.now_ms>15000)return '<span>资金待刷新，暂无法估算</span>';
    const sizing=exposureSizing();
    if(sizing.error)return `<span>${esc(sizing.error)}</span>`;
    if(sizing.remaining<0)return '<span>可用资金不足，请调整仓位</span>';
    const rate=sizing.rate;
    const long=state.draft.side!=='short', limit=state.draft.orderType==='limit', q=state.estimateQuote;
    if(!limit&&!freshEstimateQuote(q))return `<span>${state.estimateLoading?'更新行情…':state.estimateError?'行情不可用，暂无法估算':'等待最新行情'}</span>`;
    const crossing=limit&&freshEstimateQuote(q)&&feeEstimate().role==='Taker';
    const entry=Number(limit&&!crossing?state.draft.limitPrice:long?q?.ask:q?.bid), exit=Number(value);
    if(!validPrice(entry))return '<span>先填写有效委托价</span>';
    const above=(name==='takeProfit')===long;
    if(above?exit<=entry:exit>=entry)return `<span class="desk-estimate-invalid">须${above?'高':'低'}于开仓价 ${qty(entry)}</span>`;
    // Match size_by_equity: target exposure comes from equity, not margin leverage.
    // Contract leverage changes collateral only; never multiply P&L again.
    const raw=sizing.notional/entry;
    if(!Number.isFinite(raw)||raw>1e12)return '<span>仓位数量超出范围</span>';
    const quantity=Math.floor(raw*1e12)/1e12;
    if(quantity<1e-12)return '<span>可用资金不足</span>';
    const net=(exit-entry)*quantity*(long?1:-1)-quantity*(entry*rate+exit*Number(state.data?.taker_fee_rate??state.data?.fee_rate));
    if(!Number.isFinite(net))return '<span>暂无法估算</span>';
    const amount=Math.abs(net)<0.01&&net!==0?'<0.01':Math.abs(net).toLocaleString('zh-CN',{useGrouping:false,minimumFractionDigits:2,maximumFractionDigits:2});
    return `<span>预计${net<0?'净亏':net>0?'净赚':'净盈亏'}</span><strong class="${net<0?'desk-short':net>0?'desk-long':''}">${net<0?'−':net>0?'+':''}${esc(amount)} <small>USDT</small></strong>`;
  }
  function protectionField(name,label) {
    return field(name,label,state.draft[name]||'','number','min="0.000000000001" max="1000000000000" step="any" inputmode="decimal" placeholder="选填"',`<output class="desk-stop-estimate" data-desk-estimate="${name}" aria-live="polite">${protectionEstimate(name)}</output>`);
  }
  function updateProtectionEstimates() {
    const summary=root()?.querySelector('[data-desk-sizing-summary]');
    if(summary)summary.innerHTML=sizingSummary();
    const submit=root()?.querySelector('[data-desk-trade] .desk-submit');
    if(submit&&state.tradeMode==='open'&&!state.busy&&!state.pending){submit.textContent=quickEntryWaiting()?(state.estimateError?'行情暂不可用':'正在准备合约…'):`开${state.draft.side==='short'?'空':'多'} · ${entrySizeLabel()}`;submit.disabled=quickEntryWaiting();}
    for(const name of ['takeProfit','stopLoss']){
      const output=root()?.querySelector(`[data-desk-estimate="${name}"]`);
      if(output)output.innerHTML=protectionEstimate(name);
    }
  }
  function quickEntryWaiting(){
    return !!quickMount&&state.tradeMode==='open'&&(!freshEstimateQuote(state.estimateQuote)||state.estimateQuote.entry_ready!==true);
  }
  async function refreshEstimateQuote() {
    updateProtectionEstimates();
    if(!state.active||state.busy||state.pending||state.quote||state.tradeMode!=='open'||!(Number(account()?.available)>0)||(!quickMount&&state.draft.orderType!=='limit'&&!['takeProfit','stopLoss'].some(name=>validPrice(state.draft[name]))))return;
    const symbol=state.draft.symbol, activation=state.activation;
    if(!(state.symbols||[]).some(item=>item.symbol===symbol))return;
    if(freshEstimateQuote(state.estimateQuote)&&state.estimateQuote.expires_ms-Date.now()>5000&&(!quickMount||state.estimateQuote.entry_ready===true))return;
    if(state.estimateLoading?.symbol===symbol&&state.estimateLoading.activation===activation)return;
    if(state.estimateAttempt?.symbol===symbol&&state.estimateAttempt.activation===activation&&Date.now()-state.estimateAttempt.at<5000)return;
    const attempt={symbol,activation,at:Date.now()};
    state.estimateAttempt=attempt;state.estimateLoading=attempt;state.estimateError='';updateProtectionEstimates();
    try {
      const q=await request('/estimate?symbol='+encodeURIComponent(symbol));
      if(state.estimateLoading!==attempt||!state.active||activation!==state.activation||symbol!==state.draft.symbol)return;
      if(!freshEstimateQuote(q))throw new Error('行情已过期');
      state.estimateQuote=q;
    }catch(error){if(state.estimateLoading===attempt)state.estimateError=error.message;}
    finally{if(state.estimateLoading===attempt){state.estimateLoading=null;updateProtectionEstimates();}}
  }
  const symbolMatches = () => {
    const query=state.symbolSearch.toUpperCase().replace(/[^A-Z0-9]/g,'');
    const rank=item=>[item.base,item.base+'USDT',item.symbol.replace(/[^A-Z0-9]/g,'')].includes(query)?0:item.base.startsWith(query)?1:2;
    return (state.symbols||[]).filter(item=>item.symbol.replace(/[^A-Z0-9]/g,'').includes(query)).sort((a,b)=>query?rank(a)-rank(b):0);
  };
  function symbolOptions() {
    const matches=symbolMatches();
    return matches.map((item,index)=>`<button type="button" role="option" tabindex="-1" id="desk-symbol-option-${index}" aria-selected="${item.symbol===state.draft.symbol}" class="desk-symbol-option ${index===state.symbolIndex?'is-active':''}" data-desk-symbol="${esc(item.symbol)}"><strong>${esc(item.base)} / USDT</strong><small>${item.volume_usdt_24h!=null?'24h 约 '+money(Number(item.volume_usdt_24h)/1e6)+'M USDT':esc(item.symbol)}</small></button>`).join('')||'<p class="desk-muted desk-symbol-empty">没有匹配的币种，试试 BTC、ETH 或 SOL。</p>';
  }
  function symbolPicker() {
    const selected=(state.symbols||[]).find(item=>item.symbol===state.draft.symbol);
    return `<div class="desk-field desk-symbol-picker" data-desk-picker><span id="desk-symbol-label">币种（USDT 永续）</span>
      <input type="hidden" name="symbol" value="${esc(selected?.symbol||'')}">
      <button type="button" class="desk-symbol-trigger" data-desk-symbol-toggle aria-labelledby="desk-symbol-label desk-symbol-selected" aria-haspopup="listbox" aria-expanded="${state.pickerOpen}" aria-controls="desk-symbol-results" ${state.busy||!state.symbols?.length?'disabled':''}><span id="desk-symbol-selected">${selected?`${esc(selected.base)} / USDT`:'选择币种'}</span></button>
      ${state.symbolsError?`<span class="desk-symbol-error" role="status">${esc(state.symbolsError)} <button type="button" class="research-button" data-desk-symbol-retry ${state.busy?'disabled':''}>重试加载币种</button></span>`:''}
      ${state.pickerOpen?`<div class="desk-symbol-popover"><input id="desk-symbol-search" type="search" role="combobox" aria-label="搜索币种" aria-autocomplete="list" aria-expanded="true" aria-controls="desk-symbol-results" autocomplete="off" placeholder="搜索币种，如 BTC / ETH" value="${esc(state.symbolSearch)}"><div id="desk-symbol-results" role="listbox" aria-label="可选币种">${symbolOptions()}</div><small id="desk-symbol-count" class="desk-muted" aria-live="polite">${symbolMatches().length} 个合约 · 按 24h 成交额排序</small></div>`:''}</div>`;
  }
  function updateSymbolOptions() {
    const list=root()?.querySelector('#desk-symbol-results');
    if(!list)return;
    list.innerHTML=symbolOptions();
    const matches=symbolMatches(), input=root().querySelector('#desk-symbol-search');
    if(matches.length)input?.setAttribute('aria-activedescendant',`desk-symbol-option-${state.symbolIndex}`);
    else input?.removeAttribute('aria-activedescendant');
    const count=root().querySelector('#desk-symbol-count');
    if(count)count.textContent=`${matches.length} 个合约 · 按 24h 成交额排序`;
    list.querySelector('.is-active')?.scrollIntoView({block:'nearest'});
  }
  function toggleSymbols(open) {
    state.pickerOpen=open;state.symbolSearch='';state.symbolIndex=0;render();
    if(open){root()?.querySelector('#desk-symbol-search')?.focus();updateSymbolOptions();}
    else root()?.querySelector('[data-desk-symbol-toggle]')?.focus();
  }
  function chooseSymbol(symbol) {
    if(!(state.symbols||[]).some(item=>item.symbol===symbol))return;
    if(state.tradeMode==='close'){const p=positions().find(p=>p.symbol===symbol&&Number(p.remaining)>0);state.closeId=p?.id||null;if(!p)state.tradeMode='open';}
    state.draft.symbol=symbol;state.draft.takeProfit='';state.draft.stopLoss='';state.quote=null;clearFeedback('error');
    state.draft.limitPrice='';
    state.draft.leverage=String(activeLeverage());
    toggleSymbols(false);
  }
  async function loadSymbols() {
    try {
      const data=await request('/symbols');
      if(!Array.isArray(data.symbols)||!data.symbols.length)throw new Error('币种列表为空，请重试。');
      state.symbols=data.symbols;state.symbolsError='';
      if(!state.draft.symbol)state.draft.symbol=data.symbols.find(item=>item.symbol==='BTC-USDT-SWAP')?.symbol||data.symbols[0].symbol;
      else if(!data.symbols.some(item=>item.symbol===state.draft.symbol)){state.draft.symbol='';state.quote=null;}
      state.draft.leverage=String(activeLeverage());
    }catch(error){state.symbols=[];state.symbolsError=error.message;}
  }
  async function request(path, payload) {
    const response = await fetch(endpoint+path, payload ? {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)} : {});
    const data = await response.json();
    if (!response.ok) {const error=new Error(typeof data.detail === 'string' ? data.detail : '输入格式无效，请检查字段。');error.status=response.status;throw error;}
    return data;
  }
  function pending(value) {
    state.pending = value;
    if (value) sessionStorage.setItem(retryKey, JSON.stringify(value)); else sessionStorage.removeItem(retryKey);
  }
  async function execute(operation) {
    if (state.busy) return;
    state.busy=true; state.revision++; clearFeedback();
    let committed=false, needsRefresh=false;
    try { pending(operation); } catch (_) {state.busy=false;notify('error','浏览器无法保存请求回执，请允许此站点的会话存储后重试。');render();return;}
    render();
    try {
      const result = await request(operation.path+'?include_snapshot=true',operation.payload);
      committed=true;
      pending(null); state.quote=null; state.closeId=null;state.tradeMode='open';
      if(result.order)state.tab='orders';else if(result.fill)state.tab='positions';
      if(result.fill?.action==='open'||(result.order?.action==='open'&&result.order.status==='pending')){state.draft.takeProfit='';state.draft.stopLoss='';}
      let message=result.order ? `限价委托${result.order.status==='pending'?'已提交':orderStatus(result.order.status)}：${result.order.symbol.replace('-USDT-SWAP',' / USDT')} · ${direction(result.order.side)} · ${qty(result.order.limit_price)} USDT。` : result.fill ? `已模拟${result.fill.action==='open'?'开仓':'平仓'} ${qty(result.fill.quantity)}，成交价 ${qty(result.fill.price)}，手续费 ${money(result.fill.fee)} USDT。` : '模拟账户已创建。';
      if(result.protection){state.protection=null;state.tab='positions';message=result.protection.take_profit||result.protection.stop_loss?'持仓止盈止损已更新，保护全部剩余仓位。':'已取消该持仓的止盈止损。';}
      else if (!result.fill&&!result.order) {state.account=result.id;state.draft.accountName='';state.draft.initialCash='';state.createOpen=false;}
      if(result.stop_to_entry)message=result.unchanged?'已保留现有更有利的止损。':`止损已移到开仓均价 ${qty(result.protection.entry_price)} USDT，原止盈保留；未包含手续费。`;
      notify('success',message);
      resetRecords();
      if(result.snapshot)applySnapshot(result.snapshot);else needsRefresh=true;
    } catch(error) {
      if(error.status>=400&&error.status<500){pending(null);state.quote=null;notify('error',error.message);}
      else notify('error',error.message + ' 如未收到结果，可使用原请求重试；不会重复提交或成交。');
    }
    finally {
      state.busy=false;render();
      if(committed){loadHistory(true);if(needsRefresh)refreshAfterCommit();}
    }
  }
  async function refreshAfterCommit(){
    const revision=state.revision,activation=state.activation,id=state.account;
    try {const data=await request(livePath());if(revision===state.revision&&activation===state.activation&&id===state.account){applySnapshot(data);render();}}
    catch(error){if(revision===state.revision)notify('error','操作已记账，账户刷新失败，请点击刷新行情。');}
  }
  function closePreview() {
    if(state.busy)return;
    if(journal){closeJournal();return;}
    if(state.createOpen)discardAccountDraft();
    if(state.protection)state.protection=null;
    state.quote=null;render();
  }
  function discardAccountDraft() {
    state.createOpen=false;
    state.draft.accountName='';state.draft.initialCash='';
  }
  function syncPreviewDialog(restoreFocus=true) {
    const createDialog=root()?.querySelector('#desk-create-dialog');
    const dialog=root()?.querySelector('#desk-journal-dialog')||createDialog||root()?.querySelector('#desk-protection-dialog'), wasVisible=state.previewVisible, wasCreating=state.createVisible;
    state.previewVisible=Boolean(dialog);
    state.createVisible=Boolean(createDialog);
    document.body?.classList?.toggle('desk-confirm-open',state.previewVisible);
    if(!dialog){
      const returnTarget=journalFocus?`[data-desk-journal="${journalFocus}"]`:wasCreating?'[data-desk-create-toggle]':state.protectionFocus?`[data-desk-protection="${state.protectionFocus}"]`:state.pending?'[data-desk-retry]':'.desk-submit';
      state.protectionFocus=null;journalFocus=null;
      if(wasVisible&&restoreFocus&&state.active&&!state.busy)root()?.querySelector(returnTarget)?.focus({preventScroll:true});
      return;
    }
    // Native dialog keeps keyboard focus and pointer events inside the confirmation.
    dialog.addEventListener('cancel',event=>{event.preventDefault();closePreview();});
    dialog.addEventListener('keydown',event=>{
      if(event.key!=='Tab')return;
      const buttons=[...dialog.querySelectorAll('button:not([disabled]),input:not([disabled]),textarea:not([disabled]),summary')];
      if(!buttons.length){event.preventDefault();return;}
      const first=buttons[0], last=buttons[buttons.length-1];
      if(event.shiftKey&&document.activeElement===first){event.preventDefault();last.focus();}
      else if(!event.shiftKey&&document.activeElement===last){event.preventDefault();first.focus();}
    });
    dialog.addEventListener('click',event=>{
      if(event.target!==dialog)return;
      const rect=dialog.getBoundingClientRect();
      if(event.clientX<rect.left||event.clientX>rect.right||event.clientY<rect.top||event.clientY>rect.bottom)closePreview();
    });
    if(!dialog.open)dialog.showModal();
  }
  function accountDialog() {
    if(!state.active||!state.createOpen)return '';
    return `<dialog id="desk-create-dialog" class="desk-confirm-dialog desk-account-dialog" aria-labelledby="desk-create-title">
      <header class="desk-confirm-header"><h2 id="desk-create-title">新建模拟账户</h2><button type="button" class="desk-dialog-close" data-desk-create-cancel aria-label="关闭新建账户" ${state.busy?'disabled':''}>×</button></header>
      <form data-desk-create>
        <div class="desk-confirm-body desk-create">
          
          ${state.pending&&!state.busy?'<p class="desk-muted">请求结果待核对，请关闭弹窗后重试原请求。</p>':''}
          ${field('accountName','账户名称',state.draft.accountName||'','text','required autofocus maxlength="80" placeholder="例如：区间练习"')}
          ${field('initialCash','初始资金 · USDT',state.draft.initialCash||'','number','required min="0.01" max="1000000000000" step="any"')}
        </div>
        <footer class="desk-confirm-footer">${button('取消','create-cancel')}<button type="submit" class="research-button primary" ${state.busy||state.pending?'disabled':''}>${state.busy&&state.pending?'创建中…':'创建账户'}</button></footer>
      </form>
      ${feedbackMarkup()}
    </dialog>`;
  }
  function ordersPanel() {
    const orders=(state.data?.orders||[]).filter(o=>o.account_id===state.account);
    const waiting=orders.filter(o=>o.status==='pending');
    const row=o=>`<article class="desk-order"><div class="desk-position-head"><strong>${esc(o.symbol.replace('-USDT-SWAP',' / USDT'))} <small title="委托金额 ÷ 当前账户权益">${esc(positionMultipleLabel(Number(o.quantity)*Number(o.limit_price)))}</small></strong><div class="desk-order-actions"><span class="desk-order-status ${esc(o.status)}">${orderStatus(o.status)}</span>${o.status==='pending'?`<button type="button" class="research-button" data-desk-cancel-order="${esc(o.id)}" ${state.busy||state.pending?'disabled':''}>撤单</button>`:''}</div></div><div class="desk-order-line"><span class="desk-${o.side}">${direction(o.side)} · ${o.action==='open'?'开仓':'平仓'}</span><span>${money(Number(o.quantity)*Number(o.limit_price))} USDT</span><span><small>委托价</small> ${qty(o.limit_price)}</span></div>${o.action==='open'?`<p class="desk-order-protection">保证金 ${money(Number(o.quantity)*Number(o.limit_price)/o.leverage)} USDT · ${o.status==='pending'?'Maker '+money(Number(o.fee_rate??state.data?.maker_fee_rate)*100)+'%':'成交费用见记录'}</p>`:''}${o.action==='open'&&(o.take_profit||o.stop_loss)?`<p class="desk-order-protection">${protectionText(o)} · 成交后生效</p>`:''}${o.status==='rejected'&&o.reason?`<small class="desk-short">${esc(o.reason)}</small>`:''}</article>`;
    return `${waiting.map(row).join('')||'<div class="desk-empty"><strong>暂无待成交委托</strong><span>选择限价委托，设定价格后提交。</span><small>挂单成交后将自动转入持仓</small></div>'}`;
  }
  function positionReturnMarkup(position) {
    // Both fields describe the remaining position. Use raw margin, not its rounded display.
    // Unrealized P&L already includes direction and leverage; do not apply either again.
    const values=[position.unrealized,position.reserved_margin];
    const valid=values.every(value=>value!=null&&String(value).trim()!==''&&Number.isFinite(Number(value)));
    const rate=valid&&Number(position.reserved_margin)>0?Number(position.unrealized)/Number(position.reserved_margin)*100:null;
    const available=rate!==null&&Number.isFinite(rate);
    const tone=!available||rate===0?'desk-muted':rate<0?'desk-short':'desk-long';
    const label=available?rate.toLocaleString('zh-CN',{useGrouping:false,minimumFractionDigits:2,maximumFractionDigits:2,signDisplay:'exceptZero'})+'%':'—';
    return `<div class="desk-position-return ${tone}" title="浮动盈亏 ÷ 当前持仓保证金 × 100%，未扣手续费"><strong>${label}</strong><span>收益率</span></div>`;
  }
  function positionsPanel(open) {
    return open.map(p=>`<article class="desk-position" data-desk-journal-card="${esc(p.id)}" tabindex="0" aria-label="${esc(p.symbol)} 开单日志"><div class="desk-position-head"><button type="button" class="desk-position-symbol" data-desk-position-symbol="${esc(p.symbol)}" aria-label="查看 ${esc(p.symbol.replace('-USDT-SWAP',''))} 图表" title="切换到该币种图表"><strong>${esc(p.symbol.replace('-USDT-SWAP',' / USDT'))} <small>永续</small> <span class="desk-contract-leverage" title="合约杠杆">${p.leverage?esc(p.leverage)+'×':'—'}</span></strong></button><span class="desk-${p.side}" title="当前持仓金额 ÷ 当前账户权益">${direction(p.side)} · ${esc(positionMultipleLabel(p.notional_usdt))}</span></div><div class="desk-position-result"><div class="desk-position-pnl ${Number(p.unrealized)<0?'desk-short':'desk-long'}">${money(p.unrealized)} <small>USDT</small><span>浮动盈亏</span></div>${positionReturnMarkup(p)}</div><dl class="desk-position-metrics"><div><dt>持仓 · USDT</dt><dd>${wholeMoney(p.notional_usdt)}</dd></div><div><dt>预估爆仓价</dt><dd>${p.risk_complete?(p.liquidation_price?price(p.liquidation_price):'无正值触发价'):'待风险数据'}</dd></div><div><dt>开仓均价</dt><dd>${price(p.entry_price)}</dd></div><div><dt>手续费 · USDT</dt><dd>${money(p.fees)}</dd></div>${p.take_profit||p.stop_loss?`<div><dt>止盈价 / 止损价</dt><dd>${p.take_profit?qty(p.take_profit):'—'} / ${p.stop_loss?qty(p.stop_loss):'—'}</dd></div>`:''}</dl><div class="desk-position-head"><small class="desk-muted">${stamp(p.mark_ms)}</small><div class="desk-actions"><button type="button" class="research-button desk-journal-link" data-desk-journal="${esc(p.id)}" ${state.busy?'disabled':''}>开单日志 ↗</button>${entryStopButton(p)}<button type="button" class="research-button" data-desk-protection="${esc(p.id)}" ${state.busy||state.pending?'disabled':''}>止盈止损</button><button type="button" class="research-button" data-desk-close="${esc(p.id)}" ${state.busy||state.pending||Number(p.available_to_close??p.remaining)<=0?'disabled':''}>平仓</button></div></div></article>`).join('')||'<div class="desk-empty"><strong>暂无持仓</strong><span>选择币种、设置杠杆，开启一笔模拟交易。</span><small>持仓数量、盈亏和止盈止损将在这里显示</small></div>';
  }
  function fillCards(fills) {
    return fills.map(f=>`<article class="desk-fill"><div class="desk-position-head"><strong>${esc((f.symbol||'').replace('-USDT-SWAP',' / USDT'))} <small>${f.action==='open'?'开仓':'平仓'} · ${exitLabel(f)}</small></strong><span class="desk-${f.side||'long'}">${f.action==='open'?'开':'平'}${f.side==='short'?'空':'多'}</span></div><dl class="desk-record-grid"><div><dt>成交价格</dt><dd>${price(f.price)}</dd></div><div><dt>成交金额（USDT）</dt><dd>${money(Number(f.quantity)*Number(f.price))}</dd></div><div><dt>手续费（USDT）</dt><dd>${money(f.fee)}</dd></div><div><dt>数量（基础币）</dt><dd>${qty(f.quantity)}</dd></div><div><dt>毛盈亏（USDT）</dt><dd class="${Number(f.gross_pnl)<0?'desk-short':'desk-long'}">${f.action==='close'?money(f.gross_pnl):'—'}</dd></div><div><dt>费用类型</dt><dd>${f.fee_role?esc(f.fee_role.toUpperCase()):'历史费率'}</dd></div></dl><small class="desk-muted">${stamp(f.created_ms)}</small></article>`).join('');
  }
  function closedPanel() {
    const closed=recordRows('closed',positions().filter(p=>Number(p.remaining)===0).sort((a,b)=>b.closed_ms-a.closed_ms));
    return closed.map(p=>{const roi=Number(p.cumulative_entry_margin)>0?Number(p.net_realized)/Number(p.cumulative_entry_margin)*100:null;
      const label={liquidation:'强平',take_profit:'止盈平仓',stop_loss:'止损平仓'}[p.closing_reason]||'全部平仓';
      return `<article class="desk-position desk-closed-position" data-desk-journal-card="${esc(p.id)}" tabindex="0" aria-label="${esc(p.symbol)} 开单日志"><div class="desk-position-head"><strong>${esc(p.symbol.replace('-USDT-SWAP',' / USDT'))} <small>永续</small> <span class="desk-contract-leverage">${p.leverage}×</span></strong><span class="desk-${p.side}">${direction(p.side)} · ${label}</span></div><div class="desk-position-result"><div class="desk-position-pnl ${Number(p.net_realized)<0?'desk-short':'desk-long'}">${money(p.net_realized)} <small>USDT</small><span>已实现净收益</span></div><div class="desk-position-return ${roi<0?'desk-short':'desk-long'}"><strong>${roi==null?'—':(roi>0?'+':'')+roi.toFixed(2)+'%'}</strong><span>收益率 / 累计保证金</span></div></div><dl class="desk-position-metrics"><div><dt>累计开仓 · USDT</dt><dd>${wholeMoney(p.opened_notional)}</dd></div><div><dt>开仓均价</dt><dd>${price(p.average_open_price)}</dd></div><div><dt>平仓均价</dt><dd>${price(p.average_close_price)}</dd></div><div><dt>手续费 · USDT</dt><dd>${money(p.fees)}</dd></div></dl><div class="desk-position-head"><small class="desk-muted">${stamp(p.opened_ms)} → ${stamp(p.closed_ms)}</small><button type="button" class="research-button desk-journal-link" data-desk-journal="${esc(p.id)}" ${state.busy?'disabled':''}>开单日志 ↗</button></div><details class="desk-related-orders" data-position="${esc(p.id)}"><summary>关联成交 · ${(p.related_fills||[]).length}</summary>${fillCards((p.related_fills||[]).map(f=>({...f,symbol:p.symbol,side:p.side})))}</details></article>`;
    }).join('')||'<div class="desk-record-empty">暂无已平仓记录</div>';
  }
  function fillsPanel() {
    return fillCards(recordRows('fills',(state.data?.fills||[]).filter(f=>f.account_id===state.account)))||'<div class="desk-record-empty">暂无成交记录</div>';
  }
  function render() {
    const el=root(); if(!el) return;
    // An unrelated response must never replace an editor or move its caret.
    if(journal&&el.querySelector('#desk-journal-dialog')){updateFeedback();return;}
    const expanded=expandedRecords();
    const a=account(), rows=positions(), open=rows.filter(p=>Number(p.remaining)>0);
    const close=rows.find(p=>p.id===state.closeId), closing=state.tradeMode==='close';
    const side=close?.side||state.draft.side||'long';
    const stats=a ? [['账户权益',a.equity],['可用资金',a.available],['持仓保证金',a.reserved],['挂单冻结',a.order_reserved||'0'],['浮动盈亏',a.unrealized],['已结算盈亏',a.net_realized]] : [];
    const waiting=(state.data?.orders||[]).filter(o=>o.account_id===state.account&&o.status==='pending').length;
    const dialogs=journalDialog()+protectionDialog()+accountDialog();
    const markup=`<header class="desk-toolbar"><div class="desk-toolbar-main"><h1>模拟交易</h1><span class="desk-paper-badge">PAPER</span><select aria-label="模拟账户" data-desk-account ${state.busy?'disabled':''}><option value="">选择账户</option>${(state.data?.accounts||[]).map(x=>`<option value="${esc(x.id)}" ${x.id===state.account?'selected':''}>${esc(x.name)}</option>`).join('')}</select></div><div class="desk-toolbar-actions">${button('新建账户','create-toggle')}${button('刷新','refresh')}${a?button('全币种 1.618','monday-bulk'):''}<button type="button" class="research-button desk-fullscreen-button" data-workspace-fullscreen aria-label="交易页全屏" aria-pressed="false"><svg viewBox="0 0 20 20" aria-hidden="true"><path d="M7 3H3v4m10-4h4v4M3 13v4h4m10-4v4h-4"/></svg><span data-fullscreen-label>全屏</span></button></div></header>
      ${state.pending?`<div class="desk-preview"><strong>请求结果待核对</strong><p>重试原请求可核对成交结果。</p><div class="desk-actions">${button('重试原请求','retry','primary')}${button('已核对，结束此请求','clear-retry')}</div></div>`:''}
      ${!a&&state.data&&!state.busy?`<p class="desk-muted">${state.data?.accounts?.length?'请选择模拟账户。':'还没有模拟账户，点击“新建账户”开始。'}</p>`:''}
      ${a?`<section class="desk-metrics" aria-label="账户资金">${stats.map(([label,value],i)=>`<article><span>${label} <small>USDT</small></span><strong class="${i>=4?(Number(value)<0?'desk-short':Number(value)>0?'desk-long':''):''}">${money(value)}</strong></article>`).join('')}</section>
      ${!a.marks_complete?`<p class="research-error">${a.missing_mark_symbols?.length?esc(a.missing_mark_symbols.map(s=>s.replace('-USDT-SWAP','')).join('、')):'部分持仓'}缺少新鲜行情，暂不能核算权益和可用资金；行情恢复后自动更新。</p>`:''}
      <div class="desk-layout"><section class="desk-card desk-ticket" aria-label="下单面板">
      ${closing?`<div class="desk-close-heading"><strong>平仓</strong><button type="button" data-desk-trade-open ${state.busy?'disabled':''}>返回开仓</button></div>`:''}
      <form id="desk-trade-form" data-desk-trade>
      ${closing?`<label class="desk-field"><span>平仓合约</span><select data-desk-close-select ${state.busy?'disabled':''}><option value="">选择持仓</option>${open.map(p=>`<option value="${esc(p.id)}" ${p.id===state.closeId?'selected':''}>${esc(p.symbol.replace('-USDT-SWAP',' / USDT'))} · ${direction(p.side)} ${p.leverage}×</option>`).join('')}</select></label>`:symbolPicker()}
      ${closing?'':`<div class="desk-entry-settings"><label class="desk-leverage"><span>合约杠杆</span><select name="leverage" ${state.busy?'disabled':''}>${leverageOptions().map(value=>`<option value="${value}" ${activeLeverage()===value?'selected':''}>${value}×</option>`).join('')}</select></label><div class="desk-sizing-control"><select name="sizingMode" aria-label="仓位方式" ${state.busy?'disabled':''}><option value="multiple" ${fixedNotional()?'':'selected'}>仓位倍数</option><option value="notional" ${fixedNotional()?'selected':''}>USDT 金额</option></select>${fixedNotional()?`<input name="notionalUsdt" aria-label="仓位金额 USDT" type="number" min="0.000000000001" max="1000000000000" step="any" required placeholder="10000" value="${esc(state.draft.notionalUsdt||'')}" ${state.busy?'disabled':''}>`:`<select name="positionMultiple" aria-label="仓位倍数" ${state.busy?'disabled':''}>${entryLeverages.map(value=>`<option value="${value}" ${Number(state.draft.positionMultiple||defaultPositionMultiple)===value?'selected':''}>${value}×</option>`).join('')}</select>`}</div><button type="button" class="desk-direction desk-${side}" data-desk-flip aria-label="切换为${side==='long'?'做空':'做多'}" ${state.busy?'disabled':''}><span>${direction(side)}</span><i class="desk-swap-icon" aria-hidden="true"></i></button></div>`}
      <label class="desk-field desk-order-type"><span class="sr-only">下单方式</span><select name="orderType" ${state.busy?'disabled':''}><option value="market" ${state.draft.orderType==='limit'?'':'selected'}>市价委托</option><option value="limit" ${state.draft.orderType==='limit'?'selected':''}>限价委托</option></select></label>
      ${state.draft.orderType==='limit'?field('limitPrice','委托价格 · USDT',state.draft.limitPrice||'','number','required min="0.000000000001" max="1000000000000" step="any" placeholder="输入目标价格"'):''}
      ${closing&&close?closeControls(close):''}
      ${!closing?`<details class="desk-protection" data-desk-entry-stops ${state.stopsExpanded?'open':''}><summary class="desk-protection-heading"><strong>止盈 / 止损</strong><span>${state.draft.takeProfit||state.draft.stopLoss?'已设置':'选填'}</span></summary><div class="desk-stops-grid">${protectionField('takeProfit','止盈价 · USDT')}${protectionField('stopLoss','止损价 · USDT')}</div></details>`:''}
      <div class="desk-available"><span>${closing?'可平数量':'可用资金'}</span><strong>${closing?(close?qty(close.available_to_close??close.remaining):'—'):money(a.available)} ${closing?'':'USDT'}</strong></div>
      ${closing&&!close?'<p class="desk-muted">请选择持仓后平仓。</p>':''}
      ${!closing?`<div class="desk-sizing-summary" data-desk-sizing-summary aria-live="polite">${sizingSummary()}</div>`:''}
      <button class="desk-submit desk-${side}" ${state.busy||state.pending||(closing&&!close)?'disabled':''}>${state.busy?(state.pending?'提交中…':'读取中…'):`${closing?'平':'开'}${side==='long'?'多':'空'}${closing?'':` · ${entrySizeLabel()}`}`}</button></form></section>
      <div class="desk-book"><section class="desk-card desk-ledger" aria-label="持仓与委托"><div class="desk-ledger-header"><nav class="desk-ledger-tabs" aria-label="交易记录"><button type="button" data-desk-tab="positions" ${state.busy?'disabled':''} aria-pressed="${state.tab==='positions'}">持仓 <span>${open.length}</span></button><button type="button" data-desk-tab="orders" ${state.busy?'disabled':''} aria-pressed="${state.tab==='orders'}">委托 <span>${waiting}</span></button><button type="button" data-desk-tab="closed" ${state.busy?'disabled':''} aria-pressed="${state.tab==='closed'}">历史仓位</button><button type="button" data-desk-tab="fills" ${state.busy?'disabled':''} aria-pressed="${state.tab==='fills'}">成交记录</button></nav></div><div data-desk-worker-alert>${workerNotice()}</div><div class="desk-ledger-content">${state.tab==='orders'?ordersPanel():state.tab==='fills'?fillsPanel():state.tab==='closed'?closedPanel():positionsPanel(open)}${recordFooter()}</div></section></div></div>
      <div class="desk-risk-strip"><span>全仓保证金 · ${a.risk_complete?'标记价格风控':'风险数据待更新，自动强平暂不可用'}</span><span>维持保证金 <strong>${money(a.maintenance)} USDT</strong> · 保证金率 <strong>${a.margin_ratio!=null?money(Number(a.margin_ratio)*100)+'%':'—'}</strong></span></div>
      <div data-desk-history>${historyMarkup()}</div>
      <details class="desk-rules"><summary>模拟规则与费用</summary><p>仓位金额＝账户权益 × 仓位倍数；保证金＝仓位金额 ÷ 合约杠杆。默认仓位 10 倍，BTC / ETH 默认合约 100 倍，其余合约最高 20 倍，另受 OKX 当前持仓档位限制。也可直接输入 USDT 仓位金额；例如权益 1000 USDT 时，10000 USDT 仓位等于 10 倍。两种方式都按合约杠杆计算保证金与费用。同合约可继续挂单；同方向、同杠杆加仓合并均价，不同方向或杠杆分别显示。加仓不填止盈止损则沿用已有保护，填写的价格应用于合并后的全部仓位。</p><p>按 OKX 普通等级模拟：Maker 0.02%，Taker 0.05%；撤单不收手续费。限价单穿过盘口立即以 Taker 成交；其余挂单等待后续新鲜报价触价，以 Maker 按委托价全额成交。历史成交费不改写。止盈止损以可平仓买卖价触发，自动关闭剩余持仓并撤销其平仓挂单。</p><p>全仓风险以 OKX 标记价及对应持仓档位维持保证金率计算：权益不高于维持保证金与预估平仓费之和时，撤销账户全部挂单并模拟平掉所有持仓，记录 Taker 强平费及按档位计算的清算费。价格跳空可能导致负余额，保留实际结算结果。爆仓价是假定其他币价不变的估算，并非交易所保证成交价。服务每 5 秒检查；风险数据过期时暂停强平，服务停机不补历史触发。此简化模拟不包含交易所分档减仓、保险基金、ADL、资金费和盘口深度。</p><p>持仓 USDT 金额按当前可平仓价换算。浮盈未扣平仓费；日历按北京时间统计已实现盈亏及当日费用，权益从上线后采样。成交与历史仓位每页展示 50 条，导出和资金统计包含全部记录。</p></details>`:''}${dialogs||feedbackMarkup()}`;
    if(window.SpikePaperMarket&&state.active&&!quickMount)window.SpikePaperMarket.render(el,markup,{symbol:state.draft.symbol,selector:symbolPicker()});
    else el.innerHTML=markup;
    restoreRecords(expanded);
    window.SpikeWorkspaceDisplay?.sync();
    syncPreviewDialog();
    updateFeedback();
    refreshEstimateQuote();loadHistory();loadRecords();
  }
  async function refresh(reloadSymbols=false, background=false) {
    if(state.busy||journal)return;
    if(state.refreshing){if(!background){state.refreshAgain=true;state.busy=true;render();}return;}
    state.refreshing=true;
    if(!background){state.busy=true;render();}
    const revision=state.revision, activation=state.activation, requestedAccount=state.account;
    const canApply=()=>revision===state.revision&&activation===state.activation&&requestedAccount===state.account&&(!background||(!state.busy&&!journal&&!state.quote&&!state.pickerOpen&&!state.createOpen&&!state.pending));
    try {
      const symbolsTask=state.symbols===null||reloadSymbols||!state.symbols?.length?loadSymbols():Promise.resolve();
      const data=await request(livePath());
      if(!canApply())return;
      applySnapshot(data);
      for(const item of [...feedback])if(item.kind==='error'&&item.message===backgroundError)dismissFeedback(item.id);
      backgroundError='';loadHistory();
      if(!account())state.account=state.data.accounts[0]?.id||'';
      await symbolsTask;
      loadRecords();
      if(background){
        if(state.protection||root()?.contains(document.activeElement)){
          // Keep the caret while refreshing the funds used for live estimates.
          updateProtectionEstimates();updatePositionControls();updateLiveSnapshot();
          const available=root()?.querySelector('.desk-available strong');
          if(available&&state.tradeMode==='open')available.textContent=money(account()?.available)+' USDT';
        }else render();
      }
    } catch(error){if(canApply()){
      if(!background||backgroundError!==error.message)notify('error',error.message);
      backgroundError=error.message;
    }}
    finally{
      state.refreshing=false;
      if(state.refreshAgain){state.refreshAgain=false;state.busy=false;if(state.active)refresh();}
      else if(!background){state.busy=false;render();}
    }
  }
  async function getQuote(form) {
    if(state.busy||state.pending) return;
    if(quickEntryWaiting()){refreshEstimateQuote();return;}
    const values=new FormData(form), close=positions().find(p=>p.id===state.closeId);
    if(state.tradeMode==='close'&&!close)return;
    const symbol=close?.symbol||String(values.get('symbol')||'').trim().toUpperCase();
    if(!close&&!(state.symbols||[]).some(item=>item.symbol===symbol)){notify('error','请从下拉列表选择币种后再获取报价。');render();return;}
    const orderType=String(values.get('orderType')||'market'), limitPrice=String(values.get('limitPrice')||'');
    if(orderType==='limit'&&(!(Number(limitPrice)>0)||!Number.isFinite(Number(limitPrice)))){notify('error','请填写有效的委托价格。');render();return;}
    const takeProfit=close?'':String(values.get('takeProfit')||'').trim(), stopLoss=close?'':String(values.get('stopLoss')||'').trim();
    if([takeProfit,stopLoss].some(value=>value&&(!(Number(value)>0)||!Number.isFinite(Number(value))))){notify('error','止盈止损价格必须是有效正数，或留空不设置。');render();return;}
    const percentage=Number(values.get('closePercent')||state.draft.closePercent||100), leverage=close?.leverage||Number(values.get('leverage')||defaultEntryLeverage), positionMultiple=Number(values.get('positionMultiple')||defaultPositionMultiple), side=close?.side||state.draft.side||'long';
    if(!close&&!leverageOptions().includes(leverage)){notify('error','请选择有效的杠杆档位。');render();return;}
    if(!close&&!fixedNotional()&&!entryLeverages.includes(positionMultiple)){notify('error','请选择有效的仓位倍数。');render();return;}
    if(close&&(!Number.isInteger(percentage)||percentage<1||percentage>100)){notify('error','平仓比例须为 1 至 100 的整数。');render();return;}
    const notional=String(values.get('notionalUsdt')||state.draft.notionalUsdt||'');
    if(!close&&fixedNotional()&&!validPrice(notional)){notify('error','请输入有效仓位金额（USDT）');return;}
    const activation=state.activation;
    state.busy=true;clearFeedback();state.quote=null;render();
    const sizeQuery=fixedNotional()?'notional_usdt='+encodeURIComponent(notional):'position_multiple='+positionMultiple;
    const path=close?`/positions/${encodeURIComponent(close.id)}/close-preview?percentage=${percentage}${orderType==='limit'?'&limit_price='+encodeURIComponent(limitPrice):''}`:`/open-preview?account_id=${encodeURIComponent(state.account)}&symbol=${encodeURIComponent(symbol)}&side=${side}&leverage=${leverage}&${sizeQuery}${orderType==='limit'?'&limit_price='+encodeURIComponent(limitPrice):''}${takeProfit?'&take_profit='+encodeURIComponent(takeProfit):''}${stopLoss?'&stop_loss='+encodeURIComponent(stopLoss):''}`;
    try {const q=await request(path);if(!state.active||activation!==state.activation)return;
      if(!close&&(fixedNotional()?(q.sizing!=='fixed_notional'||Number(q.requested_notional)!==Number(notional)):(q.sizing!=='equity_multiple'||q.position_multiple!==positionMultiple)))throw new Error('服务尚未支持当前仓位设置，请稍后刷新重试。');
      if(close&&(q.sizing!=='position_percentage'||q.close_percent!==percentage))throw new Error('服务尚未支持百分比平仓，请刷新后重试。');
      state.quote={...q,fee_rate:q.fee_rate??state.data?.fee_rate??'0.0005',action:close?'close':'open',position_id:close?.id,account_id:state.account,side,quantity:q.quantity,leverage,order_type:orderType,limit_price:limitPrice,entry_price:close?.entry_price,note:'',request_id:crypto.randomUUID()};
      state.busy=false;await submitQuote();
    }
    catch(error){notify('error',error.message);}
    finally{state.busy=false;render();}
  }
  async function submitQuote() {
    if(state.busy||state.pending||!state.active||!state.quote)return;
    const q=state.quote;
    if(!Number.isFinite(q.expires_ms)||Date.now()>q.expires_ms){
      notify('error','报价已过期，请重新获取。');
      state.quote=null;
      render();return;
    }
    // Direct entries and closes share the frozen quote and durable idempotent request.
    const payload={quantity:q.quantity,quote_id:q.id,request_id:q.request_id};
    if(q.order_type==='limit')payload.limit_price=q.limit_price;
    if(q.action==='open'){payload.side=q.side;payload.note=q.note;payload.leverage=q.leverage;payload.take_profit=q.take_profit??null;payload.stop_loss=q.stop_loss??null;}
    await execute({path:q.action==='open'?`/accounts/${encodeURIComponent(q.account_id)}/${q.order_type==='limit'?'limit-open':'open'}`:`/positions/${encodeURIComponent(q.position_id)}/${q.order_type==='limit'?'limit-close':'close'}`,payload});
  }
  function bind() {
    const el=root();if(!el||el.dataset.bound)return;el.dataset.bound='true';
    window.SpikePaperHistory?.bind?.(el,()=>({data:historyData,view:historyView}));
    el.addEventListener('toggle',event=>{if(event.target.hasAttribute?.('data-desk-entry-stops')){state.stopsExpanded=event.target.open;const label=event.target.querySelector?.('summary>span');if(label)label.textContent=state.draft.takeProfit||state.draft.stopLoss?'已设置':'选填';}},true);
    try {state.pending=JSON.parse(sessionStorage.getItem(retryKey)||'null');}catch(_){notify('error','请求缓存不可读，请先核对成交记录。');}
    el.addEventListener('input',event=>{if(journal&&event.target.name?.startsWith('journal_')){const key=event.target.name.slice(8);if(journalFields.some(([field])=>field===key)){journal.draft[key]=event.target.value;rememberJournal(journal);const status=el.querySelector('[data-desk-journal-status]');if(status)status.textContent='未保存 · 关闭后草稿在本页保留';}return;}if(event.target.id==='desk-symbol-search'){state.symbolSearch=event.target.value;state.symbolIndex=0;updateSymbolOptions();return;}if(state.protection&&['heldtakeProfit','heldstopLoss'].includes(event.target.name)){state.protection[event.target.name.slice(4)]=event.target.value;updatePositionControls();return;}if(['accountName','initialCash'].includes(event.target.name)){state.draft[event.target.name]=event.target.value;return;}if(event.target.name){state.draft[event.target.name]=event.target.value;state.quote=null;syncPreviewDialog(false);refreshEstimateQuote();updatePositionControls();}});
    el.addEventListener('keydown',event=>{
      if(event.isComposing||state.busy)return;
      if(event.target.dataset?.deskJournalCard&&['Enter',' '].includes(event.key)){event.preventDefault();openJournal(event.target.dataset.deskJournalCard);return;}
      if(event.target.id==='desk-symbol-search'){
        if(event.key==='Escape'){event.preventDefault();toggleSymbols(false);return;}
        const matches=symbolMatches();
        if(event.key==='ArrowDown'||event.key==='ArrowUp'){
          event.preventDefault();if(matches.length)state.symbolIndex=(state.symbolIndex+(event.key==='ArrowDown'?1:-1)+matches.length)%matches.length;updateSymbolOptions();
        }
        if(event.key==='Enter'){event.preventDefault();if(matches[state.symbolIndex])chooseSymbol(matches[state.symbolIndex].symbol);}
      }else if(event.target.hasAttribute('data-desk-symbol-toggle')&&event.key==='ArrowDown'){event.preventDefault();toggleSymbols(true);}
    });
    el.addEventListener('paste',event=>{if(!journal?.data)return;const files=Array.from(event.clipboardData?.items||[]).filter(item=>item.kind==='file').map(item=>item.getAsFile()).filter(Boolean);if(files.length){event.preventDefault();uploadJournalImages(files);}});
    el.addEventListener('focusout',event=>{
      if(state.pickerOpen&&event.relatedTarget&&!el.querySelector('[data-desk-picker]')?.contains(event.relatedTarget)){
        state.pickerOpen=false;
        el.querySelector('.desk-symbol-popover')?.remove();
        el.querySelector('[data-desk-symbol-toggle]')?.setAttribute('aria-expanded','false');
      }
    });
    document.addEventListener('click',event=>{
      if(state.pickerOpen&&!event.target.closest('[data-desk-picker]')){
        state.pickerOpen=false;
        el.querySelector('.desk-symbol-popover')?.remove();
        el.querySelector('[data-desk-symbol-toggle]')?.setAttribute('aria-expanded','false');
      }
    });
    el.addEventListener('change',event=>{if(event.target.hasAttribute?.('data-desk-journal-images')){uploadJournalImages(Array.from(event.target.files||[]));return;}if(event.target.matches('[data-desk-close-select]')){state.closeId=event.target.value;const p=positions().find(p=>p.id===state.closeId);if(p)state.draft.symbol=p.symbol;state.draft.closePercent='100';state.quote=null;render();return;}if(['orderType','leverage','positionMultiple','sizingMode'].includes(event.target.name)){state.draft[event.target.name]=event.target.value;state.quote=null;render();return;}if(event.target.matches('[data-desk-account]')){window.SpikeMondayBulk?.close();state.account=event.target.value;state.revision++;resetRecords();historyData=null;historyError='';historyView.day='';historyView.cursor=null;state.closeId=null;state.tradeMode='open';state.quote=null;state.draft.closePercent='100';state.protection=null;clearFeedback('error');refresh();}});
    el.addEventListener('submit',event=>{event.preventDefault();if(event.target.matches('[data-desk-journal-form]')){saveJournal();return;}if(state.busy||state.pending)return;
      if(event.target.matches('[data-desk-create]')){const values=new FormData(event.target);execute({path:'/accounts',payload:{name:values.get('accountName'),initial_cash:values.get('initialCash'),request_id:crypto.randomUUID()}});}
      else if(event.target.matches('[data-desk-protection-form]'))saveProtection(event.target);
      else if(event.target.matches('[data-desk-trade]'))getQuote(event.target);
    });
    el.addEventListener('click',event=>{
      const target=event.target.closest('button');
      if(!target){
        const card=event.target.closest('[data-desk-journal-card]');
        if(card?.dataset?.deskJournalCard&&!event.target.closest('a,input,textarea,select,summary,details')&&!window.getSelection?.()?.toString())openJournal(card.dataset.deskJournalCard);
        return;
      }
      if(target.dataset.deskDismissFeedback){dismissFeedback(Number(target.dataset.deskDismissFeedback));return;}
      if(state.busy)return;
      if(target.dataset.deskImageRemove){setJournalImage(target.dataset.deskImageRemove,true);return;}
      if(target.dataset.deskImageRestore){setJournalImage(target.dataset.deskImageRestore,false);return;}
      if(target.dataset.deskPositionSymbol){
        if(state.draft.symbol!==target.dataset.deskPositionSymbol)chooseSymbol(target.dataset.deskPositionSymbol);
        root()?.querySelector('[data-desk-market]')?.scrollIntoView({behavior:'smooth',block:'start'});
        return;
      }
      if(target.dataset.deskJournal){openJournal(target.dataset.deskJournal);return;}
      if(target.hasAttribute('data-desk-journal-close')){closeJournal();return;}
      if(target.hasAttribute('data-desk-journal-retry')){if(journal)openJournal(journal.id);return;}
      if(target.hasAttribute('data-desk-journal-use-latest')&&journal?.latest){journal.data=journal.latest;journal.draft=journalContent(journal.latest.journal);journal.latest=null;rememberJournal(journal);clearFeedback('error');redrawJournal();return;}
      if(target.hasAttribute('data-desk-journal-keep-draft')&&journal?.latest){journal.data=journal.latest;journal.latest=null;saveJournal();return;}
      if(target.dataset.deskChartKind){historyView.kind=target.dataset.deskChartKind;historyView.cursor=null;updateHistory();return;}
      if(target.dataset.deskChartSpan){historyView.span=target.dataset.deskChartSpan;historyView.cursor=null;updateHistory();return;}
      if(target.dataset.deskDay){historyView.day=target.dataset.deskDay;updateHistory();return;}
      if(target.dataset.deskMonth){const [y,m]=historyView.month.split('-').map(Number),d=new Date(Date.UTC(y,m-1+Number(target.dataset.deskMonth),1));historyView.month=d.toISOString().slice(0,7);historyView.day='';historyError='';loadHistory(true);return;}
      if(target.hasAttribute('data-desk-history-refresh')){loadHistory(true);return;}
      if(target.dataset.deskEntryStop){moveStopToEntry(target.dataset.deskEntryStop);return;}
      if(target.dataset.deskProtection){if(state.pending)return;const p=positions().find(p=>p.id===target.dataset.deskProtection);if(!p)return;state.protectionFocus=p.id;state.protection={id:p.id,takeProfit:p.take_profit||'',stopLoss:p.stop_loss||''};state.quote=null;clearFeedback('error');render();return;}
      if(target.hasAttribute('data-desk-dismiss-protection')){closePreview();return;}
      if(target.hasAttribute('data-desk-clear-protection')){state.protection.takeProfit='';state.protection.stopLoss='';clearFeedback('error');render();return;}
      if(target.dataset.deskPercent){state.draft.closePercent=target.dataset.deskPercent;state.quote=null;render();return;}
      if(target.dataset.deskRecordPage!==undefined){records.offset=Number(target.dataset.deskRecordPage);records.data=null;records.error='';loadRecords(true);render();return;}
      if(target.hasAttribute('data-desk-record-retry')){loadRecords(true);render();return;}
      if(target.dataset.deskTab){state.tab=target.dataset.deskTab;render();return;}
      if(target.hasAttribute('data-desk-create-cancel')){closePreview();return;}
      if(target.hasAttribute('data-desk-create-toggle')){if(state.createOpen){closePreview();return;}state.createOpen=true;state.quote=null;state.protection=null;state.pickerOpen=false;clearFeedback('error');render();return;}
      if(target.hasAttribute('data-desk-flip')){state.draft.side=state.draft.side==='short'?'long':'short';state.draft.takeProfit='';state.draft.stopLoss='';state.quote=null;render();return;}
      if(target.hasAttribute('data-desk-trade-open')){state.tradeMode='open';state.closeId=null;state.quote=null;render();return;}
      if(target.hasAttribute('data-desk-trade-close')){state.tradeMode='close';const p=positions().find(p=>Number(p.available_to_close??p.remaining)>0);state.closeId=p?.id||null;if(p)state.draft.symbol=p.symbol;state.draft.closePercent='100';state.quote=null;render();return;}
      if(target.dataset.deskCancelOrder){execute({path:`/orders/${encodeURIComponent(target.dataset.deskCancelOrder)}/cancel`,payload:{request_id:crypto.randomUUID()}});return;}
      if(target.hasAttribute('data-desk-symbol-toggle')){toggleSymbols(!state.pickerOpen);return;}
      if(target.dataset.deskSymbol){chooseSymbol(target.dataset.deskSymbol);return;}
      if(target.hasAttribute('data-desk-symbol-retry')){refresh(true);return;}
      if(target.hasAttribute('data-desk-monday-bulk')){window.SpikeMondayBulk?.open(state.account,{name:account()?.name,onChange:()=>refresh(true)});return;}
      if(target.hasAttribute('data-desk-refresh'))refresh(true);
      if(target.hasAttribute('data-desk-cancel'))closePreview();
      if(target.hasAttribute('data-desk-retry')&&state.pending)execute(state.pending);
      if(target.hasAttribute('data-desk-clear-retry')){pending(null);state.quote=null;refresh();}
      if(target.dataset.deskClose){state.tradeMode='close';state.closeId=target.dataset.deskClose;const position=positions().find(p=>p.id===state.closeId);if(position)state.draft.symbol=position.symbol;state.draft.closePercent='100';state.quote=null;render();el.querySelector('[data-desk-trade]')?.scrollIntoView({block:'center',behavior:'smooth'});}
    });
  }
  function activate(active) {
    document.body?.classList?.toggle('paper-desk-active',active&&!quickMount);
    if(state.active!==active)state.activation++;
    state.active=active;clearInterval(state.timer);state.timer=null;
    if(!active){window.SpikeMondayBulk?.close();if(journal){rememberJournal(journal);journal=null;root()?.querySelector('#desk-journal-dialog')?.remove();}journalFocus=null;clearFeedback();backgroundError='';discardAccountDraft();root()?.querySelector('#desk-create-dialog')?.remove();window.SpikeWorkspaceDisplay?.leaveFullscreen();window.SpikePaperMarket?.stop();state.protection=null;root()?.querySelector('#desk-protection-dialog')?.remove();state.quote=null;state.estimateQuote=null;state.estimateLoading=null;state.estimateError='';syncPreviewDialog(false);}
    if(active){bind();refresh(true);state.timer=setInterval(()=>{
      if(!document.hidden&&!journal&&!state.pickerOpen&&!state.createOpen&&!state.quote&&!state.pending){refresh(false,true);refreshEstimateQuote();}
    },5000);}
  }
  function closeQuick() {
    if(!quickMount)return;
    activate(false);
    const el=root(),mount=quickMount;quickMount=null;
    mount.home.insertBefore(el,mount.next?.parentNode===mount.home?mount.next:null);
    el.classList.remove('desk-quick');
    el.innerHTML='';
  }
  function setActive(active) {
    // The page router and the floating host share one trading owner. Returning
    // to the full desk restores its original node before activating the page.
    if(active&&quickMount)closeQuick();
    if(!active&&quickMount)return;
    activate(active);
  }
  function openQuick(host,{symbol}={}) {
    if(quickMount)return;
    if(state.busy)throw Error('上一笔请求仍在处理中，请稍后再打开。');
    if(!host||!symbol||!/^[A-Z0-9]{1,24}-USDT-SWAP$/.test(symbol))throw Error('请选择有效合约。');
    const el=root();if(!el?.parentNode)throw Error('模拟交易模块尚未就绪。');
    activate(false);
    quickMount={home:el.parentNode,next:el.nextSibling};
    host.append(el);el.classList.add('desk-quick');
    // Never discard a durable, unresolved submission while opening a panel.
    if(!state.pending){state.draft.symbol=symbol;state.draft.takeProfit='';state.draft.stopLoss='';state.draft.limitPrice='';state.closeId=null;state.tradeMode='open';}
    state.pickerOpen=false;state.quote=null;state.tab='positions';
    activate(true);
  }
  window.SpikePaperDesk={setActive,refresh,openQuick,closeQuick};
})();
