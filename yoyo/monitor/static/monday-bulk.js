/* Paper campaign controls. Drafts survive polling; requests retain retry IDs. */
(() => {
  'use strict';
  let current = null;
  const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const labels = {eligible:'可挂单',pending:'挂单中',filled:'已成交',cancelled:'已撤销',rejected:'未通过',skipped:'已跳过'};
  const num = value => value == null ? '—' : Number(value).toLocaleString('zh-CN', {maximumFractionDigits:10});
  const key = id => 'spike-monday-bulk-request-' + id;
  async function request(s, suffix = '', payload) {
    const response = await fetch(`/api/research/paper-desk/accounts/${encodeURIComponent(s.account)}/monday-bulk${suffix}`, {
      ...(payload ? {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)} : {})
    });
    const data = await response.json();
    if (!response.ok) {
      const error = new Error(typeof data.detail === 'string' ? data.detail : '设置无效，请核对后重试');
      error.status = response.status;
      throw error;
    }
    return data;
  }
  function message(s, text, error = false) {
    if (current !== s) return;
    const node = s.dialog.querySelector('[data-bulk-message]');
    node.textContent = text;
    node.classList.toggle('desk-short', error);
  }
  function draft(s) {
    const data = new FormData(s.form);
    return {notional_usdt:data.get('amount').trim(), leverage:Number(data.get('leverage')),
      direction:data.get('direction'), mode:'weekly', take_profit:data.get('take_profit'),
      stop_loss_percent:data.get('stop_mode') === 'percent' ? data.get('stop_percent').trim() : null};
  }
  function controls(s) {
    if (current !== s) return;
    const locked = s.busy || !!s.pending;
    s.form.querySelectorAll('input,select').forEach(el => {el.disabled=locked || !!s.data?.enabled;});
    const distance = s.form.elements.stop_mode.value === 'percent';
    s.dialog.querySelector('[data-bulk-stop-field]').hidden = !distance;
    s.form.elements.stop_percent.required = distance;
    s.form.elements.stop_percent.disabled = locked || !!s.data?.enabled || !distance;
    s.dialog.querySelector('[data-bulk-preview]').disabled = locked || !s.data || !!s.data.enabled;
    s.dialog.querySelector('[data-bulk-enable]').disabled = locked || !s.data || !!s.data.enabled;
    s.dialog.querySelector('[data-bulk-enable]').hidden = !!s.data?.enabled;
    s.dialog.querySelector('[data-bulk-stop]').hidden = !s.data?.enabled;
    s.dialog.querySelector('[data-bulk-stop]').disabled = locked;
    s.dialog.querySelector('[data-bulk-retry]').hidden = !s.pending;
    s.dialog.querySelector('[data-bulk-retry]').disabled = s.busy;
  }
  function renderRows(s) {
    const data = s.preview || s.data;
    if (!data || current !== s) return;
    const counts = data.counts || {};
    s.dialog.querySelector('[data-bulk-funds]').textContent = s.preview ? `可用 ${num(data.available)} · 预计新增保证金 ${num(data.estimated_margin)} · 开仓手续费 ${num(data.estimated_entry_fees)} · 剩余 ${num(data.available_after)} USDT` : '';
    s.dialog.querySelector('[data-bulk-counts]').innerHTML = [['candidates','方向机会'],['eligible','可挂单'],['pending','挂单中'],['filled','已成交'],['rejected','未通过'],['skipped','跳过']].map(([k,label]) => `<div><strong>${counts[k]||0}</strong><span>${label}</span></div>`).join('');
    const query = s.dialog.querySelector('[data-bulk-search]').value.trim().toUpperCase();
    const rows = (data.items || []).filter(r => r.symbol.includes(query));
    s.dialog.querySelector('[data-bulk-rows]').innerHTML = rows.map(r => `<tr><td><b>${esc(r.symbol.replace('-USDT-SWAP',''))}</b><small class="desk-${r.side}">${r.side==='long'?'下沿做多':'上沿做空'}</small></td><td>${num(r.entry)}</td><td>${num(r.take_profit)}<small>SL ${num(r.stop_loss)}</small></td><td>${esc(labels[r.status]||r.status)}<small>${esc(r.reason)}</small></td></tr>`).join('') || '<tr><td colspan="4">暂无匹配记录。开启后自动等待当前周有效区间与新鲜行情。</td></tr>';
    s.dialog.querySelector('[data-bulk-source]').textContent = s.preview ? '预览 · 挂单时会重新核对行情与余额' : '本周执行记录 · 每 5 秒刷新';
  }
  function renderStatus(s) {
    if (current !== s || !s.data) return;
    s.dialog.querySelector('[data-bulk-state]').textContent = s.data.enabled ? '已开启 · 每周自动更新' : '未开启';
    s.dialog.querySelector('[data-bulk-week]').textContent = `纽约周 ${s.data.week} · ${s.name}`;
    s.dialog.querySelector('[data-bulk-error]').textContent = s.data.last_error ? '等待中：' + s.data.last_error : '';
    controls(s); renderRows(s);
  }
  async function refresh(s, initial = false) {
    if (s.loading || s.busy || current !== s) return;
    s.loading = true;
    try {
      const data = await request(s);
      if (current !== s) return;
      const changed = s.data?.version !== data.version;
      s.data = data;
      if ((initial || data.enabled && changed) && data.settings) {
        const values = data.settings;
        s.form.elements.amount.value = values.notional_usdt;
        s.form.elements.leverage.value = values.leverage;
        s.form.elements.direction.value = values.direction;
        s.form.elements.take_profit.value = values.take_profit;
        s.form.elements.stop_mode.value = values.stop_loss_percent == null ? 'none' : 'percent';
        s.form.elements.stop_percent.value = values.stop_loss_percent ?? '';
      }
      renderStatus(s);
    } catch (error) {message(s, '状态读取失败：' + error.message, true);}
    finally {s.loading=false;}
  }
  async function preview(s) {
    if (s.busy || s.pending || !s.form.reportValidity()) return;
    const settings = draft(s);
    s.busy=true;controls(s);message(s,'正在核对币种、行情与可用资金…');
    try {
      const data = await request(s, '/preview', settings);
      if (current !== s) return;
      s.preview=data;renderRows(s);message(s,'预览完成。资金不足或缺少行情的币种会逐项显示原因。');
    } catch(error) {message(s,error.message+'。也可以保存开关，待数据就绪后自动挂单。',true);}
    finally {s.busy=false;controls(s);}
  }
  async function configure(s, enabled, retry = false) {
    if (s.busy || !s.data || (!retry && s.pending)) return;
    if (!retry && enabled && !s.form.reportValidity()) return;
    const payload = retry ? s.pending : {enabled,settings:enabled?draft(s):null,version:s.data.version,request_id:crypto.randomUUID()};
    s.pending=payload;
    try {sessionStorage.setItem(key(s.account), JSON.stringify(payload));} catch (_) { /* Memory retry still works. */ }
    s.busy=true;controls(s);message(s,'正在保存开关…');
    try {
      await request(s, '/configure', payload);
      s.pending=null;sessionStorage.removeItem(key(s.account));s.preview=null;
      if (current !== s) return;
      message(s,payload.enabled?'已开启；服务器将持续检查并挂单。':'已停止，功能创建的未成交单已撤销，持仓继续保留。');
      s.onChange?.();
    } catch(error) {
      if (error.status && error.status < 500) {s.pending=null;sessionStorage.removeItem(key(s.account));}
      message(s,error.message+(s.pending?'；结果待核对，请重试原请求。':''),true);
    } finally {
      s.busy=false;
      if(current===s){await refresh(s);controls(s);}
    }
  }
  function close() {
    if (!current) return;
    const s=current;current=null;clearInterval(s.timer);s.dialog.close();s.dialog.remove();
  }
  function open(account, {name='模拟账户',onChange} = {}) {
    if (!account) return;
    close();
    const dialog=document.createElement('dialog');dialog.className='desk-confirm-dialog monday-bulk-dialog';
    dialog.setAttribute('aria-labelledby','monday-bulk-title');
    dialog.innerHTML=`<header class="desk-confirm-header"><div><div class="desk-confirm-eyebrow">模拟交易 · PAPER ONLY</div><h2 id="monday-bulk-title">全币种 1.618</h2></div><button type="button" class="desk-dialog-close" data-bulk-close aria-label="关闭全币种挂单">×</button></header>
      <div class="desk-confirm-body"><div class="bulk-status"><strong data-bulk-state>读取状态中…</strong><span data-bulk-week></span></div>
      <p class="desk-muted">上沿挂空、下沿挂多，每周每币每方向只挂一次。纽约周一结束后使用新区间；下周一撤销旧挂单，已成交仓位继续保留。</p>
      <form data-bulk-form class="bulk-fields"><label class="desk-field"><span>每笔持仓金额 · USDT</span><input name="amount" inputmode="decimal" type="number" min="0.00000001" step="any" required placeholder="填写金额，不是保证金"></label>
      <label class="desk-field"><span>合约杠杆</span><input name="leverage" inputmode="numeric" type="number" min="1" max="125" step="1" required placeholder="填写倍数，逐币核对上限"></label>
      <label class="desk-field"><span>挂单方向</span><select name="direction" aria-label="挂单方向"><option value="both">上沿空 + 下沿多</option><option value="long">仅下沿做多</option><option value="short">仅上沿做空</option></select></label>
      <label class="desk-field"><span>全仓止盈</span><select name="take_profit" aria-label="全仓止盈" required><option value="">请选择止盈规则</option><option value="near">近侧周一边界</option><option value="far">对侧周一边界</option><option value="none">不设自动止盈</option></select></label>
      <label class="desk-field"><span>止损</span><select name="stop_mode" aria-label="止损" required><option value="">请选择止损规则</option><option value="percent">距开仓价百分比</option><option value="none">不设价格止损</option></select></label>
      <label class="desk-field" data-bulk-stop-field hidden><span>止损距离 · %</span><input name="stop_percent" inputmode="decimal" type="number" min="0.000001" max="99.999999" step="any" placeholder="填写价格距离"></label></form>
      <p class="desk-muted bulk-note">按当前 OKX USDT 永续目录检查所有币种；已触价、已有同向持仓、资金或行情不足时跳过。入场 Maker 0.02%，自动止盈止损按后续盘口市价成交、Taker 0.05%。预览按币名排序逐笔预留保证金，未模拟排队和部分成交。修改设置需先停止；停止后重开不会补挂本周已有委托的方向。</p>
      <p data-bulk-error class="desk-short" role="status"></p><p data-bulk-message role="status"></p>
      <div class="desk-actions bulk-actions"><button type="button" class="research-button" data-bulk-preview disabled>预览全部币种</button><button type="button" class="research-button primary" data-bulk-enable disabled>开启每周自动挂单</button><button type="button" class="research-button" data-bulk-stop hidden>停止并撤销未成交单</button><button type="button" class="research-button primary" data-bulk-retry hidden>重试原请求</button></div>
      <section class="bulk-results" aria-label="全币种挂单明细"><p data-bulk-funds></p><div class="bulk-counts" data-bulk-counts></div><div class="bulk-table-tools"><small data-bulk-source>本周执行记录</small><input type="search" data-bulk-search aria-label="筛选挂单币种" placeholder="搜索币种"></div><div class="bulk-table-wrap"><table><thead><tr><th>币种 / 方向</th><th>挂单价</th><th>止盈 / 止损</th><th>状态 / 原因</th></tr></thead><tbody data-bulk-rows></tbody></table></div></section></div>`;
    const s={account,name,onChange,dialog,form:dialog.querySelector('form'),data:null,busy:false,pending:null,loading:false,preview:null};
    try {s.pending=JSON.parse(sessionStorage.getItem(key(account))||'null');} catch (_) { /* Start without corrupt client cache. */ }
    current=s;document.body.append(dialog);dialog.showModal();
    dialog.addEventListener('cancel',event=>{event.preventDefault();close();});
    dialog.querySelector('[data-bulk-close]').onclick=close;
    s.form.addEventListener('submit',event=>{event.preventDefault();preview(s);});
    s.form.addEventListener('input',()=>{s.preview=null;message(s,'设置已更改，可重新预览。');controls(s);renderRows(s);});
    dialog.querySelector('[data-bulk-search]').addEventListener('input',()=>renderRows(s));
    dialog.querySelector('[data-bulk-preview]').onclick=()=>preview(s);
    dialog.querySelector('[data-bulk-enable]').onclick=()=>configure(s,true);
    dialog.querySelector('[data-bulk-stop]').onclick=()=>configure(s,false);
    dialog.querySelector('[data-bulk-retry]').onclick=()=>configure(s,null,true);
    refresh(s,true);s.timer=setInterval(()=>{if(!document.hidden)refresh(s);},5000);
    if(s.pending)message(s,'有一个请求尚待核对，请重试原请求。');
  }
  window.SpikeMondayBulk={open,close};
})();
