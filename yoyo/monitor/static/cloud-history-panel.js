/* Compact per-symbol reference statistics for the SPIKE cloud chart. */
(() => {
  'use strict';

  const PHASE_LABELS = Object.freeze({
    idle: '未载入', candles: '读取行情', references: '载入参考信号',
    done: '本次读取完成', error: '读取失败', paused: '已暂停'
  });

  function finite(value) {
    return typeof value === 'number' && Number.isFinite(value);
  }

  function signedR(value) {
    if (!finite(value)) return '—';
    return `${value > 0 ? '+' : ''}${value.toFixed(2)}R`;
  }

  function percent(value) {
    return finite(value) ? `${(value * 100).toFixed(1)}%` : '—';
  }

  function count(value) {
    return Number.isSafeInteger(value) && value >= 0 ? String(value) : '—';
  }

  function shortDate(value) {
    if (!finite(value)) return null;
    const date = new Date(value);
    if (!Number.isFinite(date.getTime())) return null;
    return new Intl.DateTimeFormat('zh-CN', {
      timeZone: 'America/New_York', year:'numeric', month: '2-digit', day: '2-digit'
    }).format(date);
  }

  function coverageText(status, summary) {
    const done = Number.isSafeInteger(status.weeksDone) && status.weeksDone >= 0 ? status.weeksDone : null;
    const total = Number.isSafeInteger(status.weeksTotal) && status.weeksTotal >= 0 ? status.weeksTotal : null;
    const from = shortDate(status.from_ms ?? summary?.firstConfirmationMs);
    const to = shortDate(status.to_ms ?? summary?.lastConfirmationMs);
    const range = from && to ? ` · ${from}–${to} NY` : '';
    if (done !== null && total !== null) return `已载入 ${done}/${total} 周${range}`;
    if (done !== null) return `已载入 ${done} 周${range}`;
    if (from || to) return `行情范围${range}`;
    return summary ? '已载入范围未提供日期' : '尚无已载入范围';
  }

  function selectedSummary(summary) {
    if (!summary || typeof summary !== 'object') return null;
    if (summary.bySymbol && typeof summary.bySymbol === 'object') {
      const values = Object.values(summary.bySymbol);
      if (values.length === 1) return values[0];
    }
    return summary;
  }

  function create(header, {loadAll, cancel} = {}) {
    if (!header?.append) throw new TypeError('header must be a chart header element');
    const panel = document.createElement('details');
    panel.className = 'cs-history-panel';
    panel.innerHTML = `<summary aria-label="历史参考统计">
      <span class="cs-history-dot" aria-hidden="true"></span>
      <span class="cs-history-desktop"><span data-history-scope>已载</span> <strong data-history-net>—</strong> · 胜率 <span data-history-win>—</span> · <span data-history-closed>—</span> 笔</span>
      <strong class="cs-history-mobile" data-history-net>—</strong>
    </summary>
    <section class="cs-history-popover cs-glass" role="region" aria-label="历史参考统计详情">
      <header class="cs-history-popover-head"><strong>统计范围</strong><span data-history-state role="status">未载入</span></header>
      <div class="cs-history-row"><span>覆盖</span><strong data-history-coverage>尚无已载入范围</strong></div>
      <div class="cs-history-row"><span>笔数状态</span><strong data-history-counts>已结束 — · 运行 — · 待确认 —</strong></div>
      <div class="cs-history-row"><span>净统计</span><strong data-history-core>— · — · —</strong></div>
      <div class="cs-history-row"><span>排除状态</span><strong data-history-excluded>缺口 — · 无效 — · 不可用 — · 不支持 —</strong></div>
      <div class="cs-history-row" data-history-progress-row hidden><span>读取进度</span><strong data-history-progress></strong></div>
      <p class="cs-history-message" data-history-message hidden></p>
      <p class="cs-history-fees" data-history-fees>Maker 0.02% 入场 · Taker 0.05% 退出</p>
      <p class="cs-history-disclosure">仅汇总已载入的参考信号，行情范围不代表信号已全部重建。退出与手续费按参考边界估算，不代表实际成交或账户盈亏。</p>
      <button type="button" class="cs-history-action" data-history-action>加载全部历史</button>
    </section>`;

    const actionSlot = header.matches?.('.cs-head-actions') ? header : header.querySelector?.('.cs-head-actions');
    if (header.matches?.('.cs-drawer-head') && actionSlot) header.insertBefore(panel, actionSlot);
    else if (actionSlot) actionSlot.prepend(panel);
    else header.append(panel);

    const $ = selector => panel.querySelector(selector);
    const netNodes = panel.querySelectorAll('[data-history-net]');
    const action = $('[data-history-action]');
    let currentSummary = null;
    let currentStatus = {running: false, stage: 'idle'};
    let updateVersion = 0;
    let actionVersion = 0;

    function renderSummary() {
      const summary = selectedSummary(currentSummary);
      const closed = summary?.closed;
      const net = signedR(summary?.netR);
      panel.dataset.negative=String(finite(summary?.netR)&&summary.netR<0);
      panel.querySelector('summary').title=(currentStatus.complete?'可读取历史累计':'已加载范围')+' · '+net+' · 胜率 '+percent(summary?.winRate);
      $('[data-history-scope]').textContent=currentStatus.complete?'累计':'已载';
      for (const node of netNodes) node.textContent = net;
      $('[data-history-win]').textContent = percent(summary?.winRate);
      $('[data-history-closed]').textContent = count(closed);
      $('[data-history-coverage]').textContent = coverageText(currentStatus, summary);
      $('[data-history-counts]').textContent = `已结束 ${count(closed)} · 运行 ${count(summary?.open)} · 待确认 ${count(summary?.pending)}`;
      $('[data-history-core]').textContent = `${net} · 胜率 ${percent(summary?.winRate)} · 平均 ${signedR(summary?.averageR)}/笔`;
      $('[data-history-excluded]').textContent = `缺口 ${count(summary?.gap)} · 无效 ${count(summary?.invalid)} · 不可用 ${count(summary?.unavailable)} · 不支持 ${count(summary?.unsupported)}`;

      const fee = currentStatus.fees ?? currentSummary?.fees;
      if (fee?.entry && fee?.exit && finite(fee.entry.rate) && finite(fee.exit.rate)) {
        $('[data-history-fees]').textContent = `Maker ${(fee.entry.rate * 100).toFixed(2)}% 入场 · Taker ${(fee.exit.rate * 100).toFixed(2)}% 退出`;
      }
    }

    function renderStatus() {
      const stage = String(currentStatus.stage || 'idle');
      const running = currentStatus.running === true || ['candles', 'references'].includes(stage);
      $('[data-history-state]').textContent = running ? (stage === 'candles' ? '读取行情' : '载入参考信号') :
        (stage === 'idle' && currentSummary ? '局部已载入' : PHASE_LABELS[stage] || '读取状态');
      const progressParts = [];
      if (Number.isSafeInteger(currentStatus.weeksDone) && currentStatus.weeksDone >= 0) progressParts.push(`${currentStatus.weeksDone} 周`);
      if (Number.isSafeInteger(currentStatus.weeksTotal) && currentStatus.weeksTotal >= 0) progressParts.push(`/ ${currentStatus.weeksTotal} 周`);
      if (Number.isSafeInteger(currentStatus.failedWeeks) && currentStatus.failedWeeks > 0) progressParts.push(`失败 ${currentStatus.failedWeeks} 周`);
      const progress = $('[data-history-progress-row]');
      progress.hidden = progressParts.length === 0;
      $('[data-history-progress]').textContent = progressParts.join(' ');

      const message = currentStatus.error || currentStatus.message || '';
      const messageNode = $('[data-history-message]');
      messageNode.textContent = String(message);
      messageNode.hidden = !message;

      const needsRetry = stage === 'error' || (Number.isSafeInteger(currentStatus.failedWeeks) && currentStatus.failedWeeks > 0);
      const complete = currentStatus.complete === true;
      action.hidden = complete && !running;
      action.disabled = running ? typeof cancel !== 'function' : typeof loadAll !== 'function';
      action.textContent = running ? '取消加载' : needsRetry ? '重试' : stage === 'paused' ? '继续加载' : '加载全部历史';
      action.dataset.action = running ? 'cancel' : 'load';
    }

    function positionPopover() {
      if (!panel.open) return;
      const popover = $('.cs-history-popover');
      const anchor = panel.getBoundingClientRect();
      const width = popover.getBoundingClientRect().width;
      const viewportWidth = document.documentElement.clientWidth || window.innerWidth;
      const left = Math.max(8, Math.min(anchor.right - width, viewportWidth - width - 8));
      popover.style.left = `${left - anchor.left}px`;
      popover.style.right = 'auto';
    }

    function update(summary, status = {}) {
      updateVersion++;
      currentSummary = summary ?? null;
      currentStatus = status && typeof status === 'object'
        ? {...currentStatus, ...status}
        : {...currentStatus, stage: typeof status === 'string' ? status : 'idle'};
      renderSummary();
      renderStatus();
      return api;
    }

    async function runAction(kind) {
      const callback = kind === 'cancel' ? cancel : loadAll;
      if (typeof callback !== 'function' || action.disabled) return;
      const version = ++actionVersion;
      const startUpdateVersion = updateVersion;
      if (kind === 'cancel') currentStatus = {...currentStatus, running: false, stage: 'paused'};
      else currentStatus = {...currentStatus, running: true, stage: 'references', error: ''};
      renderStatus();
      try {
        const result = callback();
        if (!result || typeof result.then !== 'function') return;
        await result;
        if (version === actionVersion && startUpdateVersion === updateVersion) {
          currentStatus = {...currentStatus, running: false, stage: kind === 'cancel' ? 'paused' : 'done'};
          renderStatus();
        }
      } catch (error) {
        if (version === actionVersion && startUpdateVersion === updateVersion) {
          currentStatus = {...currentStatus, running: false, stage: 'error', error: error?.message || '读取失败'};
          renderStatus();
        }
      }
    }

    function onClick(event) {
      if (event.target.closest('[data-history-action]')) runAction(action.dataset.action);
    }
    function onKeydown(event) {
      if (event.key === 'Escape' && panel.open) {
        event.preventDefault();
        panel.open = false;
        panel.querySelector('summary').focus({preventScroll: true});
      }
    }
    function onOutside(event) {
      if (panel.open && !panel.contains(event.target)) panel.open = false;
    }
    panel.addEventListener('click', onClick);
    panel.addEventListener('keydown', onKeydown);
    panel.addEventListener('toggle', positionPopover);
    document.addEventListener('pointerdown', onOutside);
    window.addEventListener('resize', positionPopover);

    const api = {
      update,
      setOpen(value) { panel.open = value === true; positionPopover(); return panel.open; },
      destroy() {
        panel.removeEventListener('click', onClick);
        panel.removeEventListener('keydown', onKeydown);
        panel.removeEventListener('toggle', positionPopover);
        document.removeEventListener('pointerdown', onOutside);
        window.removeEventListener('resize', positionPopover);
        panel.remove();
      }
    };
    update(null, currentStatus);
    return api;
  }

  const api = Object.freeze({create});
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  if (typeof window !== 'undefined') window.SpikeCloudHistoryPanel = api;
})();
