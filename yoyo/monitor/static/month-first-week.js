/* Monthly first touches have their own monitor and subscription settings.
 * Chart navigation always returns to the canonical Monday 1.618 workspace.
 */
(() => {
  'use strict';
  const SYMBOL_RE = /^[A-Z0-9]{1,30}-USDT-SWAP$/;
  function mondayFibSymbolHash(symbol) {
    if (!SYMBOL_RE.test(symbol || '')) return '#monday-fib';
    return '#monday-fib?' + new URLSearchParams({symbol}).toString();
  }
  function mondayFibEventHash(event) {
    if (!SYMBOL_RE.test(event?.symbol || '') || typeof event?.event_id !== 'string' || !event.event_id || event.event_id.length > 96) return '#monday-fib';
    return '#monday-fib?' + new URLSearchParams({symbol:event.symbol,monthEvent:event.event_id}).toString();
  }
  if (typeof module !== 'undefined') module.exports = {mondayFibSymbolHash,mondayFibEventHash};
  if (typeof document === 'undefined') return;
  const root = document.getElementById('month-first-week-1618-view');
  if (!root) return;
  const get = key => root.querySelector('#month-' + key);
  const api = '/api/month-first-week-1618';
  const clock = new Intl.DateTimeFormat('zh-CN', {timeZone:'Asia/Shanghai', month:'2-digit', day:'2-digit', hour:'2-digit', minute:'2-digit', hour12:false});
  const nyDay = new Intl.DateTimeFormat('zh-CN', {timeZone:'America/New_York', month:'2-digit', day:'2-digit'});
  const number = new Intl.NumberFormat('en-US', {maximumSignificantDigits:8, useGrouping:false});
  let snapshot, active = false, timer, controller, saving = false, rowKey = '', channelKey = '';

  function node(tag, text, cls) {
    const element = document.createElement(tag);
    if (text != null) element.textContent = text;
    if (cls) element.className = cls;
    return element;
  }

  function renderEvents() {
    if (!snapshot) return;
    const search = get('search').value.trim().toUpperCase();
    const side = get('side').value;
    const rows = snapshot.touches.filter(row => row.symbol.includes(search) && (!side || row.side === side));
    const key = JSON.stringify([rows.map(row => [row.event_id, row.occurred_ms, row.level, row.source]), snapshot.window.complete, search, side]);
    if (key === rowKey) return;
    rowKey = key;
    get('count').textContent = rows.length.toLocaleString();
    const content = document.createDocumentFragment();
    for (const event of rows) {
      const row = node('button', null, 'month-row month-row-button');
      row.type = 'button';
      row.dataset.monthChartEvent = event.event_id;
      const upper = event.side === 'upper';
      const coin = event.symbol.replace(/-USDT-SWAP$/, '');
      const main = node('div', null, 'month-coin');
      main.append(node('strong', coin), node('small', event.source === 'history' ? '历史回补' : '实时触及'));
      const quote = node('div', null, 'month-quote');
      quote.append(node('span', upper ? '↗ 上轨' : '↘ 下轨', 'month-edge'), node('b', number.format(event.level)));
      const when = event.source === 'history' ? event.candle_ms : event.occurred_ms;
      const stamp = node('time', clock.format(when));
      stamp.dateTime = new Date(when).toISOString();
      stamp.title = (event.source === 'history' ? '回补 K 线起点' : '首次观测') + ' · 北京时间';
      row.append(main, quote, stamp, node('span','看图 ›','month-row-open'));
      row.setAttribute('aria-label', `跳转到 1.618 图表查看 ${coin} ${upper ? '上轨' : '下轨'} ${number.format(event.level)} 的月首周触价 · ${clock.format(when)} 北京时间`);
      content.append(row);
    }
    if (!rows.length) content.append(node('p', search || side ? '没有符合筛选条件的触价' :
      snapshot.window.complete ? '本月尚未记录首次触价' : '首个完整周结束后开始监控', 'month-empty'));
    get('events').replaceChildren(content);
  }

  function renderChartSymbols() {
    const select = get('chart-symbol');
    if (!select || !snapshot) return;
    const items = Array.isArray(snapshot.items) ? snapshot.items.filter(item => typeof item?.symbol === 'string' && item.symbol) : [];
    const current = select.value;
    const key = JSON.stringify(items.map(item => [item.symbol,item.levels]));
    if (select.dataset.optionsKey === key) return;
    select.dataset.optionsKey = key;
    const ordered = [...items].sort((a,b) => Number(/(^|-)ETH(?:-|$)/.test(b.symbol))-Number(/(^|-)ETH(?:-|$)/.test(a.symbol)));
    select.replaceChildren(node('option','看 K 线',''));
    select.firstElementChild.value = '';
    for (const item of ordered) {
      const symbol = node('option',item.symbol.replace(/-USDT-SWAP$/,''));
      symbol.value = item.symbol;
      select.append(symbol);
    }
    select.value = items.some(item => item.symbol === current) ? current : '';
    select.disabled = !items.length;
  }

  function renderChannels() {
    const key = JSON.stringify(['telegram','bark'].map(k => [snapshot.channels[k].configured,snapshot.channels[k].enabled]));
    if (key === channelKey) return;
    channelKey = key;
    const host = get('channels');
    for (const [key, name] of [['telegram','Telegram'], ['bark','Bark 锁屏提醒']]) {
      let input = host.querySelector('input[name="' + key + '"]');
      if (!input) {
        const label = node('label');
        input = node('input');
        input.type = 'checkbox';
        input.name = key;
        input.setAttribute('role', 'switch');
        label.append(node('span', name), node('small'), input);
        host.append(label);
        input.addEventListener('change', () => saveChannel(key, input.checked));
      }
      const status = snapshot.channels[key];
      input.checked = status.enabled;
      input.disabled = saving || (!status.configured && !status.enabled);
      input.previousElementSibling.textContent = !status.configured ? '未配置' : status.enabled ? '已开启' : '已关闭';
    }
  }

  async function saveChannel(key, enabled) {
    if (saving) return;
    saving = true;
    get('channels').querySelectorAll('input').forEach(input => { input.disabled = true; });
    try {
      const response = await fetch(api + '/' + key, {method:'PUT', credentials:'same-origin',
        headers:{'Content-Type':'application/json','X-Spike-Action':'update-month-first-week-1618-' + key},
        body:JSON.stringify({enabled})});
      if (!response.ok) throw Error('未能保存渠道设置');
      snapshot.channels[key] = await response.json();
      get('channel-feedback').textContent = '已保存';
    } catch {
      get('channel-feedback').textContent = '设置保存失败，请重试';
    } finally {
      saving = false;
      channelKey = '';
      renderChannels();
      refresh();
    }
  }

  function render() {
    const {window: period, coverage, stream, worker, month} = snapshot;
    get('period').textContent = month + ' · 纽约首周 ' + nyDay.format(period.start_ms) + ' – ' + nyDay.format(period.ready_ms - 1);
    get('state').textContent = !period.complete ? '等待首周结束' : !worker.alive || !stream.connected ?
      '行情连接中断' : !coverage.ready_symbols ? '正在核对区间' : '监控中';
    get('coverage').textContent = '已就绪 ' + coverage.ready_symbols + ' / ' + coverage.universe_size + ' 个合约';
    renderChartSymbols();
    renderEvents();
    if (!saving) renderChannels();
  }

  async function refresh() {
    if (!active || controller || saving || document.hidden) return;
    const request = new AbortController();
    controller = request;
    try {
      const response = await fetch(api, {signal:request.signal, cache:'no-store', credentials:'same-origin'});
      if (!response.ok) throw Error();
      const data = await response.json();
      if (data.schema !== 'month_first_week_1618.v1' || !Array.isArray(data.touches)) throw Error();
      if (active && !request.signal.aborted) { snapshot = data; render(); }
    } catch {
      if (!request.signal.aborted) get('state').textContent = snapshot ? '连接中断 · 显示上次数据' : '暂时无法读取监控，正在重试';
    } finally {
      if (controller === request) controller = null;
    }
  }
  function setActive(value) {
    if (active === value) return;
    active = value;
    clearInterval(timer);
    if (active) { refresh(); timer = setInterval(refresh, 10000); }
    else { controller?.abort(); controller = null; }
  }
  get('events').addEventListener('click', event => {
    const button = event.target.closest('[data-month-chart-event]');
    if (!button || !snapshot) return;
    const row = snapshot.touches.find(item => String(item.event_id) === button.dataset.monthChartEvent);
    if (row) location.hash = mondayFibEventHash(row);
  });
  get('chart-symbol').addEventListener('change', event => {
    const symbol = event.target.value;
    if (symbol) location.hash = mondayFibSymbolHash(symbol);
  });
  get('search').addEventListener('input', renderEvents);
  get('side').addEventListener('change', renderEvents);
  document.addEventListener('visibilitychange', () => { if (!document.hidden) refresh(); });
  window.SpikeMonthFirstWeek = {setActive, refresh};
})();
