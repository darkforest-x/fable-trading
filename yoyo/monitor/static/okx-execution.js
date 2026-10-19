/* Read-only OKX account panel. Signal reference returns never enter this view. */
(() => {
  'use strict';
  // Keep the table's reading position when values change; identical snapshots do no DOM work.
  function updateDetail(panel, markup, focused) {
    if (panel._markup === markup) return;
    const old = panel.querySelector('.okx-table-scroll');
    const left = old?.scrollLeft || 0, top = old?.scrollTop || 0, restore = old && focused === old;
    panel.innerHTML = markup; panel._markup = markup;
    const next = panel.querySelector('.okx-table-scroll');
    if (next) { next.scrollLeft = left; next.scrollTop = top; }
    if (restore) (next || panel).focus({preventScroll:true});
  }
  if (typeof module !== 'undefined') module.exports = {updateDetail};
  if (typeof document === 'undefined') return;
  const root = document.getElementById('okx-execution-panel');
  if (!root) return;
  let active = false, snapshot = null, selected = 'positions', timer = null, flight = null, controller = null, revision = 0, failure = false, restricted = false, mounted = false;
  const esc = v => String(v ?? '—').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const finite = v => v !== null && v !== undefined && v !== '' && Number.isFinite(Number(v));
  const numberFormats = new Map();
  const num = (v, digits = 2) => { if (!finite(v)) return '—'; if (!numberFormats.has(digits)) numberFormats.set(digits,new Intl.NumberFormat('zh-CN',{maximumFractionDigits:digits,minimumFractionDigits:digits})); return numberFormats.get(digits).format(Number(v)); };
  const price = v => num(v, 6).replace(/(\.\d*?[1-9])0+$|\.0+$/, '$1');
  const when = v => finite(v) && Number(v) > 0 ? new Date(Number(v)).toLocaleString('zh-CN', {timeZone:'Asia/Shanghai', hour12:false}) : '尚未取得快照';
  const side = v => ({long:'多',short:'空',net:'单向',buy:'买入',sell:'卖出'}[v] || v || '—');
  const states = {prepared:'待处理',retry_pending:'等待重试',leverage_pending:'杠杆待核验',entry_pending:'入场待核验',cancel_entry_pending:'入场撤单待核验',protecting:'保护单待核验',active:'持仓中',exit_needed:'等待退出',cancel_stop_pending:'保护单撤销待核验',exit_pending:'退出待核验',stop_executing:'止损成交待核验',manual_tp_pending:'止盈校准待核验',amend_pending:'止损修改待核验',closed:'已平仓',rejected:'拒绝',skipped:'跳过',unknown:'状态未知'};
  const reasons = {quote_stale:'报价时间未通过校验',checkpoint_unavailable:'信号行情暂不可用',read_unavailable:'交易所读取暂时失败',tier_changed:'最新张数对应的杠杆档位已变化',signal_expired:'信号已超过 30 分钟有效期',preparation_rejected:'未通过入场准备校验',insufficient_margin:'可用保证金不足',minimum_size:'风险定仓小于交易所最小张数',stop_crossed:'当前价格已越过初始止损',symbol_excluded:'该标的已被排除',interrupted_before_entry:'入场提交前中断，未下单'};
  const workerLabels = {not_started:'执行器未启动',stopped:'执行器已停止',running:'执行器心跳正常',unresponsive:'执行器心跳待核验',unknown:'执行器状态未知'};
  const pnl = value => `<span class="${!finite(value) ? '' : Number(value) > 0 ? 'okx-positive' : Number(value) < 0 ? 'okx-negative' : ''}">${esc(num(value))}</span>`;
  const empty = text => `<p class="okx-empty">${esc(text)}</p>`;
  function table(headers, rows) {
    return `<div class="okx-table-scroll" tabindex="0" role="region" aria-label="账户明细，可横向滚动"><table><thead><tr>${headers.map(h => `<th scope="col">${esc(h)}</th>`).join('')}</tr></thead><tbody>${rows.join('')}</tbody></table></div>`;
  }
  const row = cells => `<tr>${cells.map(cell => `<td>${cell}</td>`).join('')}</tr>`;
  function positions(data) {
    if (!data) return empty('尚未取得账户持仓，不能据此判断为空仓。');
    if (!data.positions.length) return empty('该账户暂无真实持仓。');
    return table(['合约 / 方向','持仓量（张 / 币）','开仓均价','标记价格','浮动盈亏 / 币种','杠杆 / 保证金模式'], data.positions.map(p => row([
      `<strong>${esc(p.instId)}</strong><small>${esc(side(p.posSide))}</small>`, esc(price(p.pos)), esc(price(p.avgPx)), esc(price(p.markPx)), `${pnl(p.upl)} <small>${esc(p.ccy)}</small>`, `${esc(p.lever)}× <small>${esc({isolated:'逐仓',cross:'全仓'}[p.mgnMode] || p.mgnMode)}</small>`
    ]))) + (data.position_count > data.positions.length ? `<p class="okx-footnote">共 ${esc(data.position_count)} 条持仓，展示前 ${data.positions.length} 条。</p>` : '');
  }
  function orders(data) {
    if (!data) return empty('尚未取得挂单与保护单。');
    const items = [...data.orders, ...data.protection_orders];
    if (!items.length) return empty('该账户暂无普通挂单或已查询类型的策略委托。');
    return table(['合约','订单类型','方向','数量','委托价 / 触发价','状态'], items.map(o => row([
      esc(o.instId), `${o.kind === 'algo' ? '策略委托' : '普通挂单'}<small>${esc(o.ordType)}</small>`, `${esc(side(o.side))} / ${esc(side(o.posSide))}`, esc(price(o.sz)), o.kind === 'algo' ? `止损 ${esc(price(o.slTriggerPx))}<small>止盈 ${esc(price(o.tpTriggerPx))} · 触发 ${esc(price(o.triggerPx))}</small>` : esc(price(o.px)), esc(o.state)
    ]))) + '<p class="okx-footnote">普通挂单与条件、OCO、触发、移动止损委托；全账户数据，每类最多展示 50 条。</p>';
  }
  function executions(ledger) {
    if (!ledger || ledger.status !== 'ready') return empty('本地执行记录暂不可读，不能据此判断没有交易。');
    if (!ledger.items.length) return empty('暂无执行器记录。信号记录页展示参考走势，账户成交以此处回执为准。');
    return table(['信号 / 方向','执行状态 / 原因','实际入场 / 退出','止损','初始计划风险','已实现净额（USDT）','入场时间'], ledger.items.map(t => row([
      `<strong>${esc(t.symbol)}</strong><small>${esc(t.timeframe)} · ${esc(side(t.side))}</small>${t.signal_ms ? `<small>信号 ${esc(when(t.signal_ms))}</small>` : ''}`, `${esc(states[t.state] || '状态未知')}${t.reason_code ? `<small>${esc(reasons[t.reason_code] || '原因待核验')}</small>` : ''}${t.state === 'retry_pending' ? `<small>仅在信号有效期内重试${t.next_retry_ms ? ` · ${esc(when(t.next_retry_ms))}` : ''}</small>` : ''}`, `${esc(price(t.entry_price))}<small>${esc(price(t.exit_price))}</small>`, esc(price(t.stop)), `${esc(num(t.planned_risk))} U`, pnl(t.realized_net_usdt), esc(t.entry_ms ? when(t.entry_ms) : '尚无成交回执')
    ]))) + `<p class="okx-footnote">最近 ${ledger.items.length} / 共 ${esc(ledger.total)} 条执行记录。净额仅在平仓盈亏与入场、退出手续费回执齐全时计算，不含资金费；缺失显示 —。</p>`;
  }
  const tabs = [['positions','账户持仓'],['orders','挂单与保护'],['executions','执行记录']];
  const part = name => root.querySelector(`[data-okx-part="${name}"]`);
  function patch(name, markup) { const node=part(name); if(node._markup!==markup){node.innerHTML=markup;node._markup=markup;} }
  function render() {
    if (!mounted) {
      root.innerHTML = `<div class="okx-heading"><div><div class="okx-eyebrow">LIVE ACCOUNT <span>只读</span></div><h2>信号中心 · 自动账户</h2></div><button type="button" data-okx-refresh>刷新账户</button></div><div data-okx-part="status" class="okx-status" role="status"></div><div data-okx-part="notice" class="okx-notice"></div><div data-okx-part="metrics" class="okx-metrics"></div><div data-okx-part="brief" class="okx-brief"></div><div data-okx-part="expanded" class="okx-expanded"><div class="okx-tabs" role="tablist" aria-label="实盘账户详情">${tabs.map(([id,title])=>`<button type="button" id="okx-tab-${id}" data-okx-tab="${id}" role="tab" aria-controls="okx-account-detail">${title}</button>`).join('')}</div><div id="okx-account-detail" data-okx-part="detail" role="tabpanel" tabindex="-1"></div><details class="okx-settings"><summary data-okx-part="settings-title"></summary><div data-okx-part="config"></div></details><p class="okx-separation">本账户仅用于信号中心，与均线密集手动账户独立。账户持仓与挂单包含人工及其他来源订单；执行记录仅来自本执行器。信号参考表现位于“表现分析”页。</p></div>`;
      mounted = true;
    }
    const refreshButton=root.querySelector('[data-okx-refresh]');
    refreshButton.disabled=!!flight;refreshButton.textContent=flight?'正在读取…':'刷新账户';
    for(const name of ['status','metrics','brief','expanded'])part(name).hidden=restricted;
    if (restricted) {
      patch('notice','当前请求未获准读取账户。请从信号中心页面刷新重试。');part('notice').hidden=false;
      // Permission loss clears even hidden values, while retaining the stable controls.
      for(const name of ['status','metrics','brief','detail','config','settings-title'])patch(name,'');
      return;
    }
    const a = snapshot?.account, data = a?.data, ledger = snapshot?.ledger, cfg = snapshot?.config;
    const stale = failure || (a && !['ready','loading'].includes(a.status)) || (data && Date.now() - Number(a.updated_ms) > 60000);
    let label = '正在读取账户', tone = 'neutral';
    if (failure) { label = '连接失败'; tone = 'warn'; }
    else if (a?.status === 'unconfigured') { label = '账户未配置'; tone = 'warn'; }
    else if (a?.status === 'error' || a?.status === 'stale') { label = '账户数据待刷新'; tone = 'warn'; }
    else if (stale && data) { label = '快照已过时'; tone = 'warn'; }
    else if (data && snapshot.blockers.length) { label = '账户已连接 · 待调整'; tone = 'warn'; }
    else if (a?.status === 'ready') { label = '账户已连接'; tone = 'good'; }
    const worker = ledger?.worker;
    const warnings = [];
    if (stale) warnings.push(data ? '下列账户数值为上次成功快照，当前状态尚未核验。' : '未取得有效账户快照，未知数值显示 —。');
    if (snapshot?.blockers.includes('position_mode')) warnings.push('当前为单向持仓模式；执行器要求双向持仓模式，尚不满足启动条件。');
    if (snapshot?.blockers.includes('account_level')) warnings.push('当前账户模式与执行器要求的合约模式不一致。');
    if (snapshot?.blockers.includes('trade_permission')) warnings.push('当前 API 未确认交易权限。');
    if (worker?.error_present) warnings.push('执行器记录了异常，需核验执行状态。');
    if (data?.orders_truncated) warnings.push('挂单数量超出本次查询范围，所示订单数量为下限。');
    const metric = (title, value, note) => `<article><span>${title}</span><strong>${value}</strong><small>${note}</small></article>`;
    const cards = metric('账户权益 · USDT',esc(num(data?.balance.eq)),`账户现金 ${esc(num(data?.balance.cashBal))} U`)
      + metric('可用保证金 · USDT',esc(num(data?.balance.availEq)),`冻结 ${esc(num(data?.balance.frozenBal))} U`)
      + metric('账户浮动盈亏 · USDT',pnl(data?.balance.upl),'来自 OKX 账户，不含信号模拟收益')
      + metric('真实持仓',esc(data?.position_count ?? '—'),`普通挂单 ${esc(data?.order_count ?? '—')}${data?.orders_truncated ? '+' : ''} · 策略委托 ${esc(data?.protection_count ?? '—')}${data?.orders_truncated ? '+' : ''}`);
    const configLine = cfg ? `<div class="okx-config"><span>配置资金 <b>${esc(num(cfg.capital_usdt,0))} U</b></span><span>1R <b>${esc(num(Number(cfg.risk_fraction)*100,0))}% · ${esc(num(cfg.nominal_risk_usdt))} U</b></span><span>${esc({cross:"全仓",isolated:"逐仓"}[cfg.margin_mode] || cfg.margin_mode)} · 交易所允许的最高杠杆</span><span>${cfg.timeframes.map(esc).join(' / ')} · ${esc(cfg.entry_universe?.scope || '跟随信号中心范围')}</span><span>无额外持仓数 / 合计风险比例上限</span></div><p class="okx-footnote">1R 按配置资金与当前 USDT 权益较小值计算；${esc(num(cfg.nominal_risk_usdt))} U 为权益达到 ${esc(num(cfg.capital_usdt,0))} U 时的初始止损计划风险，实际损益以成交为准。${cfg.demo ? '当前连接模拟环境。' : '当前连接实盘环境。全仓共享账户保证金，125 U 定仓额度与计划 1R 均不隔离账户其他资金。'}</p>` : '';
    patch('status',`<span class="okx-badge ${tone}">${esc(label)}</span><span>${esc(workerLabels[worker?.status] || '执行器状态待核验')}</span><small>${esc(a?.updated_ms ? `快照 ${when(a.updated_ms)} · 北京时间` : '等待首次账户快照')}${a?.refreshing ? ' · 更新中' : ''}</small>`);
    patch('notice',warnings.map(esc).join('<br>'));part('notice').hidden=!warnings.length;
    patch('metrics',cards);part('metrics').classList.toggle('is-stale',!!stale);
    patch('brief',`${cfg ? `<span>定仓额度 <b>${esc(num(cfg.capital_usdt,0))} U</b></span><span>1R <b>${esc(num(Number(cfg.risk_fraction)*100,0))}%</b></span><span><b>${esc({cross:'全仓',isolated:'逐仓'}[cfg.margin_mode] || cfg.margin_mode)}</b> · 全周期</span>` : ''}<button type="button" data-signal-pane="account">查看账户详情 →</button>`);
    for(const [id] of tabs){const button=root.querySelector(`[data-okx-tab="${id}"]`);button.setAttribute('aria-selected',String(id===selected));button.tabIndex=id===selected?0:-1;}
    const detail=part('detail');detail.setAttribute('aria-labelledby','okx-tab-'+selected);
    updateDetail(detail,selected==='positions'?positions(data):selected==='orders'?orders(data):executions(ledger),document.activeElement);
    patch('settings-title',esc(`执行设置${cfg ? ` · ${num(cfg.capital_usdt,0)} U / 1R ${num(Number(cfg.risk_fraction)*100,0)}% / ${{cross:'全仓',isolated:'逐仓'}[cfg.margin_mode]||cfg.margin_mode}` : ''}`));
    patch('config',configLine);
  }

  async function refresh(force = true) {
    if (!active || document.hidden) return;
    if (flight) return flight;
    const id = revision;
    const readController = new AbortController();controller=readController;
    let expired=false;const timeout=setTimeout(()=>{expired=true;readController.abort();},15000);
    flight = (async () => {
      try {
        const response = await fetch(`/api/okx-execution/status${force ? '?refresh=true' : ''}`, {cache:'no-store', signal:readController.signal});
        if (id !== revision) return;
        if (response.status === 403) { restricted=true; snapshot=null; failure=false; return; }
        if (!response.ok) throw new Error('snapshot unavailable');
        const value = await response.json();
        if (id !== revision) return;
        if (!value.account || !value.ledger || !Array.isArray(value.blockers)) throw new Error('invalid snapshot');
        snapshot=value; failure=false; restricted=false;
      } catch (error) {
        if (id === revision && (expired || error.name !== 'AbortError')) failure=true;
      } finally {
        clearTimeout(timeout);
        if (id === revision) { flight=null; controller=null; render(); }
      }
    })();
    render();
    return flight;
  }
  root.addEventListener('click', event => {
    if (event.target.closest('[data-okx-refresh]')) refresh(true);
    const tab = event.target.closest('[data-okx-tab]');
    if (tab) { selected=tab.dataset.okxTab; render(); root.querySelector(`[data-okx-tab="${selected}"]`)?.focus(); }
  });
  root.addEventListener('keydown', event => {
    const tab = event.target.closest('[data-okx-tab]');
    if (!tab || !['ArrowLeft','ArrowRight','Home','End'].includes(event.key)) return;
    event.preventDefault();
    const ids=['positions','orders','executions'];
    selected=event.key==='Home' ? ids[0] : event.key==='End' ? ids[2] : ids[(ids.indexOf(selected)+(event.key==='ArrowRight'?1:2))%3];
    render(); root.querySelector(`[data-okx-tab="${selected}"]`)?.focus();
  });
  function pauseReads() {
    revision++;clearInterval(timer);timer=null;controller?.abort();controller=null;flight=null;
  }
  function resumeReads() {
    if(!active||document.hidden)return;
    refresh(false);timer=setInterval(()=>refresh(false),10000);
  }
  function setActive(value) {
    root.hidden=!value;
    if(active===value)return;
    active=value;pauseReads();if(active)resumeReads();
  }
  document.addEventListener('visibilitychange',()=>{pauseReads();resumeReads();});
  window.SpikeOKXExecution={setActive,refresh};
})();
