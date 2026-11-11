/* Monthly first touches have their own source, calendar and subscription settings. */
(() => {
  'use strict';
  const CHART_PERIODS = Object.freeze(['1m', '5m', '15m', '1H', '4H', '12H', '1D']);
  const CHART_PATH = '/api/month-first-week-1618/chart';
  const CHART_COLORS = Object.freeze({firstWeek:'#30D158',rest:'#8B9099',upper:'#d7355d',lower:'#169873',range:'#4f8fc2'});
  const finite = Number.isFinite;
  const chartURL = (eventId, period='1H') => `${CHART_PATH}?event_id=${encodeURIComponent(eventId)}&period=${encodeURIComponent(period)}`;
  const symbolChartURL = (symbol, period='1H') => `${CHART_PATH}?symbol=${encodeURIComponent(symbol)}&period=${encodeURIComponent(period)}`;
  function validateChartData(data, selection, period) {
    const event = selection?.event || null, symbol = event?.symbol || selection?.symbol;
    if (!data || typeof data !== 'object' || data.event_id !== (event?.event_id ?? null) || data.symbol !== symbol ||
        data.bar !== period || !CHART_PERIODS.includes(period) ||
        (event ? data.source !== event.source : ![null,undefined,'live','history'].includes(data.source)))
      throw Error('图表记录与所选触价不匹配');
    const a = data.anchor;
    const datesValid = a && finite(a.start_ms) && finite(a.ready_ms) && finite(a.expires_ms) && a.start_ms < a.ready_ms && a.ready_ms < a.expires_ms;
    const levelsValid = a && [a.high,a.low,a.upper,a.lower].every(value => value == null || finite(value)) &&
      (a.high == null || a.low == null || a.high >= a.low);
    const observationValid = (data.observation_start_ms == null || finite(data.observation_start_ms)) &&
      (data.observation_end_ms == null || finite(data.observation_end_ms)) &&
      (data.observation_start_ms == null || data.observation_end_ms == null || data.observation_end_ms >= data.observation_start_ms);
    if (!datesValid || !levelsValid || (event ? !finite(data.level) : data.level != null) ||
        !Array.isArray(data.candles) || !observationValid ||
        (data.forming_time_ms != null && !finite(data.forming_time_ms)))
      throw Error('图表锚点或时间范围不完整');
    let previous = -Infinity;
    for (const candle of data.candles) {
      if (!candle || ![candle.time,candle.open,candle.high,candle.low,candle.close,candle.volume].every(finite) ||
          !Number.isInteger(candle.time) || candle.time <= previous || candle.high < Math.max(candle.open,candle.close,candle.low) ||
          candle.low > Math.min(candle.open,candle.close) || candle.volume < 0 ||
          (candle.first_week != null && typeof candle.first_week !== 'boolean'))
        throw Error('K线数据不完整或顺序错误');
      previous = candle.time;
    }
    if (data.marker_ms != null && (!event || !finite(data.marker_ms) || !data.candles.some(candle => candle.time === data.marker_ms)))
      throw Error('触价K线标记与原始行情不匹配');
    return data;
  }
  function firstWeekCandle(timeMs, anchor) {
    return finite(timeMs) && anchor && finite(anchor.start_ms) && finite(anchor.ready_ms) && timeMs >= anchor.start_ms && timeMs < anchor.ready_ms;
  }
  function candleIsFirstWeek(candle, anchor) {
    return typeof candle?.first_week === 'boolean' ? candle.first_week : firstWeekCandle(candle?.time,anchor);
  }
  function monthlyLevels(data) {
    const a = data.anchor, start = a.ready_ms, end = a.expires_ms;
    if (!finite(start) || !finite(end) || end <= start) return [];
    const hasRange = finite(a.high) && finite(a.low) && a.high > a.low;
    return [
      ...(hasRange ? [{label:'首周高',price:a.high,kind:'range',color:CHART_COLORS.range,lineWidth:1,lineStyle:0,start_ms:start,end_ms:end,inlinePriceVisible:true,inlineNameVisible:true,startMarkerVisible:false}] : []),
      {label:'上 1.618',price:a.upper,kind:'fib',color:CHART_COLORS.upper,lineWidth:2,lineStyle:2,start_ms:start,end_ms:end,inlinePriceVisible:true,inlineNameVisible:true,startMarkerVisible:false},
      {label:'下 1.618',price:a.lower,kind:'fib',color:CHART_COLORS.lower,lineWidth:2,lineStyle:2,start_ms:start,end_ms:end,inlinePriceVisible:true,inlineNameVisible:true,startMarkerVisible:false},
      ...(hasRange ? [{label:'首周低',price:a.low,kind:'range',color:CHART_COLORS.range,lineWidth:1,lineStyle:0,start_ms:start,end_ms:end,inlinePriceVisible:true,inlineNameVisible:true,startMarkerVisible:false}] : []),
    ].filter(level => finite(level.price) && level.price > 0);
  }
  if (typeof module !== 'undefined') module.exports = {CHART_PERIODS,CHART_PATH,CHART_COLORS,chartURL,symbolChartURL,validateChartData,firstWeekCandle,candleIsFirstWeek,monthlyLevels};
  if (typeof document === 'undefined') return;
  const root = document.getElementById('month-first-week-1618-view');
  if (!root) return;
  const get = key => root.querySelector('#month-' + key);
  const api = '/api/month-first-week-1618';
  const clock = new Intl.DateTimeFormat('zh-CN', {timeZone:'Asia/Shanghai', month:'2-digit', day:'2-digit', hour:'2-digit', minute:'2-digit', hour12:false});
  const nyDay = new Intl.DateTimeFormat('zh-CN', {timeZone:'America/New_York', month:'2-digit', day:'2-digit'});
  const number = new Intl.NumberFormat('en-US', {maximumSignificantDigits:8, useGrouping:false});
  let snapshot, active = false, timer, controller, saving = false, rowKey = '', channelKey = '';
  let chartDialog, chartRequest, chartEpoch = 0, chartSelection = null, chartRefreshTimer;

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
      row.append(main, quote, stamp, node('span','K 线 ›','month-row-open'));
      row.setAttribute('aria-label', `查看 ${coin} ${upper ? '上轨' : '下轨'} ${number.format(event.level)} 的月首周触价 K 线 · ${clock.format(when)} 北京时间`);
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

  function clearChart() {
    if (!chartDialog) return;
    const mount = chartDialog.querySelector('.month-chart-mount');
    window.SpikeRangeChart?.unmount(mount);
    mount?.replaceChildren();
    if (mount) mount.hidden = true;
  }

  function stopChartRequest() {
    chartEpoch++;
    chartRequest?.abort();
    chartRequest = null;
  }

  function closeChart() {
    clearTimeout(chartRefreshTimer); chartRefreshTimer = null;
    stopChartRequest();
    if (chartSelection?.mode === 'symbol') get('chart-symbol').value = '';
    chartSelection = null;
    if (chartDialog?.open) chartDialog.close();
    else clearChart();
  }

  function ensureChartDialog() {
    if (chartDialog) return chartDialog;
    chartDialog = node('dialog', null, 'month-chart-dialog');
    chartDialog.setAttribute('aria-labelledby', 'month-chart-title');
    const header = node('header', null, 'month-chart-header');
    const back = node('button', '← 返回触价列表', 'month-chart-back');
    back.type = 'button'; back.dataset.monthChartBack = '';
    const title = node('div', null, 'month-chart-title-wrap');
    const heading = node('h2', '触价 K 线', 'month-chart-title'); heading.id = 'month-chart-title';
    const subheading = node('p', '', 'month-chart-subtitle');
    title.append(heading, subheading);
    header.append(back, title);
    const periods = node('nav', null, 'month-chart-periods');
    periods.setAttribute('aria-label', 'K 线周期'); periods.setAttribute('role', 'group');
    for (const period of CHART_PERIODS) {
      const button = node('button', period, 'month-chart-period');
      button.type = 'button'; button.dataset.monthChartPeriod = period;
      button.setAttribute('aria-pressed', 'false');
      periods.append(button);
    }
    const feedback = node('p', '正在读取历史 K 线…', 'month-chart-feedback');
    feedback.setAttribute('role', 'status');
    const retry = node('button', '重新加载', 'month-chart-retry');
    retry.type = 'button'; retry.dataset.monthChartRetry = ''; retry.hidden = true;
    const precision = node('p', '', 'month-chart-precision');
    const mount = node('div', null, 'month-chart-mount'); mount.hidden = true;
    const legend = node('div', null, 'month-chart-legend');
    const note = node('p', '首周 K 线为绿色，其余为灰色；价位线只在本月有效期间显示。', 'month-chart-note');
    chartDialog.append(header, periods, feedback, retry, precision, mount, legend, note);
    (root.querySelector('.month-desk') || root).append(chartDialog);
    back.addEventListener('click', closeChart);
    retry.addEventListener('click', () => { if (chartSelection) loadChart(chartSelection, chartSelection.period); });
    periods.addEventListener('click', event => {
      const button = event.target.closest('[data-month-chart-period]');
      if (!button || !chartSelection || !chartDialog.open) return;
      const period = button.dataset.monthChartPeriod;
      if (period !== chartSelection.period) loadChart(chartSelection, period);
    });
    chartDialog.addEventListener('cancel', () => {
      clearTimeout(chartRefreshTimer); chartRefreshTimer = null; stopChartRequest(); clearChart();
      if (chartSelection?.mode === 'symbol') get('chart-symbol').value = '';
      chartSelection = null;
    });
    chartDialog.addEventListener('close', () => {
      if (chartDialog.open) return;
      stopChartRequest(); clearTimeout(chartRefreshTimer); chartRefreshTimer = null; clearChart();
      if (chartSelection?.mode === 'symbol') get('chart-symbol').value = '';
      chartSelection = null;
    });
    return chartDialog;
  }

  function setChartFeedback(message, {error=false,retry=false}={}) {
    const feedback = chartDialog.querySelector('.month-chart-feedback');
    feedback.textContent = message;
    feedback.classList.toggle('is-error', error);
    feedback.hidden = !message;
    chartDialog.querySelector('[data-month-chart-retry]').hidden = !retry;
  }

  function formatChartPrice(value) { return number.format(value); }

  function renderChart(selection, period, data, {preserve=false}={}) {
    const dialog = chartDialog, mount = dialog.querySelector('.month-chart-mount');
    const event = selection.event, coin = data.symbol.replace(/-USDT-SWAP$/, '');
    const isEvent = !!event, sideLabel = event?.side === 'upper' ? '上轨触价' : '下轨触价';
    const markerExists = data.marker_ms != null && data.candles.some(candle => candle.time === data.marker_ms);
    dialog.querySelector('.month-chart-title').textContent = isEvent ? `${coin} · ${sideLabel}` : `${coin} · 当月盘面`;
    dialog.querySelector('.month-chart-subtitle').textContent = isEvent
      ? `${data.month} · ${data.source === 'history' ? '历史回补' : '实时触及'} · 触价 ${formatChartPrice(data.level)}`
      : `${data.month} · 月首周 1.618 · 当月行情`;
    dialog.querySelectorAll('[data-month-chart-period]').forEach(button => button.setAttribute('aria-pressed', String(button.dataset.monthChartPeriod === period)));
    const precision = dialog.querySelector('.month-chart-precision');
    precision.textContent = !isEvent ? monthlyLevels(data).length ? '当月盘面 · 不显示触价标记。' : '首周基准暂不可用。'
      : markerExists ? '已标记可核实的触价 K 线。' : data.source === 'history' ? '触价分钟待确认；未标记具体 K 线。' : '触价 K 线暂缺；未添加标记。';
    const legend = dialog.querySelector('.month-chart-legend');
    legend.replaceChildren();
    for (const [label,value,color] of [
      ['首周高',data.anchor.high,CHART_COLORS.range],['上 1.618',data.anchor.upper,CHART_COLORS.upper],
      ['下 1.618',data.anchor.lower,CHART_COLORS.lower],['首周低',data.anchor.low,CHART_COLORS.range],
    ]) {
      if (!finite(value)) continue;
      const item = node('span', null, 'month-chart-legend-item');
      const swatch = node('i', null, 'month-chart-swatch'); swatch.style.setProperty('--month-level-color', color);
      item.append(swatch, node('span', label), node('b', formatChartPrice(value)));
      legend.append(item);
    }
    if (!legend.childElementCount) legend.append(node('span','暂无已知首周价位','month-chart-no-levels'));
    if (!preserve) clearChart();
    mount.hidden = false;
    setChartFeedback('');
    const marker = markerExists ? [{time:data.marker_ms/1000,
      position:event?.side === 'upper' ? 'aboveBar' : 'belowBar',
      shape:event?.side === 'upper' ? 'arrowDown' : 'arrowUp',
      color:event?.side === 'upper' ? CHART_COLORS.upper : CHART_COLORS.lower,
      text:'触价'}] : [];
    let positioned = preserve;
    const levels = monthlyLevels(data);
    window.SpikeRangeChart.mount(mount, {
      symbol:data.symbol, bar:data.bar, candles:data.candles, levels,
      ...(levels.length ? {level_window:{start_ms:data.anchor.ready_ms,end_ms:data.anchor.expires_ms}} : {}),
      observation_start_ms:data.observation_start_ms, observation_end_ms:data.observation_end_ms,
      display_live:data.forming_time_ms != null,
    }, {
      bars:Infinity, initialBars:160, range:true, fib:true, ma:false, wallpaper:false,
      mondayBand:false, showLevels:false, scaleReferenceLevels:false,
      candleColor:candle => candleIsFirstWeek({...candle,time:candle.time*1000},data.anchor) ? CHART_COLORS.firstWeek : CHART_COLORS.rest,
      decorate:instance => {
        if (instance.series && typeof instance.series.setMarkers === 'function') instance.series.setMarkers(marker);
        const status = instance.panel?.querySelector('.cv-range-status');
        if (status) {
          const text = data.forming_time_ms == null ? '' : `本根 ${period} K 线尚未收盘。`;
          status.textContent = text; status.classList.toggle('is-warning',!!text);
        }
        if (positioned || !marker.length || !instance.chart || !instance.rows.length) return;
        const index = instance.rows.findIndex(candle => candle.time === marker[0].time);
        if (index < 0) return;
        const span = Math.min(80, Math.max(36, instance.rows.length));
        instance.chart.timeScale().setVisibleLogicalRange({from:Math.max(0,index-span/2),to:Math.min(instance.rows.length-1,index+span/2)});
        positioned = true;
      },
    });
  }

  function scheduleSymbolRefresh() {
    clearTimeout(chartRefreshTimer); chartRefreshTimer = null;
    if (!active || document.hidden || !chartDialog?.open || chartSelection?.mode !== 'symbol') return;
    chartRefreshTimer = setTimeout(() => loadChart(chartSelection,chartSelection.period,{background:true}),60000);
  }

  async function loadChart(selection, period='1H', {background=false}={}) {
    if (!active || !selection || !CHART_PERIODS.includes(period)) return;
    const event = selection.event || null, symbol = event?.symbol || selection.symbol;
    if (!symbol || (event && !event.event_id)) return;
    const dialog = ensureChartDialog();
    chartSelection = {...selection,mode:event?'event':'symbol',symbol,period};
    clearTimeout(chartRefreshTimer); chartRefreshTimer = null;
    stopChartRequest();
    const token = chartEpoch, request = new AbortController(); chartRequest = request;
    if (!background) clearChart();
    dialog.querySelectorAll('[data-month-chart-period]').forEach(button => button.setAttribute('aria-pressed', String(button.dataset.monthChartPeriod === period)));
    if (!background) {
      dialog.querySelector('.month-chart-title').textContent = event ? `${symbol.replace(/-USDT-SWAP$/, '')} · ${event.side === 'upper' ? '上轨触价' : '下轨触价'}` : `${symbol.replace(/-USDT-SWAP$/, '')} · 当月盘面`;
      dialog.querySelector('.month-chart-subtitle').textContent = event
        ? `${event.month} · ${event.source === 'history' ? '历史回补' : '实时触及'} · 正在读取 ${period}`
        : `${snapshot?.month || ''} · 月首周 1.618 · 正在读取 ${period}`;
      dialog.querySelector('.month-chart-precision').textContent = '';
      dialog.querySelector('.month-chart-legend').replaceChildren();
      setChartFeedback(`正在读取 ${period} 历史 K 线…`);
    }
    if (!dialog.open) dialog.showModal();
    try {
      const url = event ? chartURL(event.event_id,period) : symbolChartURL(symbol,period);
      const response = await fetch(url, {signal:request.signal,cache:'no-store',credentials:'same-origin'});
      if (token !== chartEpoch || request.signal.aborted || !dialog.open) return;
      if (!response.ok) {
        const status = response.status;
        throw Object.assign(Error(status === 404 ? '没有找到这次触价或合约对应的行情数据。' : status === 503 ? '历史行情暂时不可用，请稍后重试。' : '暂时无法读取历史 K 线。'), {retry:true});
      }
      const data = validateChartData(await response.json(),chartSelection,period);
      if (token !== chartEpoch || request.signal.aborted || !dialog.open) return;
      if (!data.candles.length) {
        setChartFeedback(background ? '行情同步暂不可用，保留上次图表。' : '该周期没有可用的真实行情 K 线。', {error:true,retry:true});
        return;
      }
      renderChart(chartSelection,period,data,{preserve:background});
    } catch (error) {
      if (request.signal.aborted || token !== chartEpoch || !dialog.open) return;
      const message = error?.message && error?.retry ? error.message : error?.name === 'TypeError' ? '网络暂时不可用，请检查连接后重试。' : 'K 线数据暂时不可用，请重试。';
      if (!background || chartDialog.querySelector('.month-chart-mount')?.hidden) setChartFeedback(message,{error:true,retry:true});
      else setChartFeedback(`${message} · 保留上次图表`,{error:true,retry:true});
    } finally {
      if (chartRequest === request) chartRequest = null;
      scheduleSymbolRefresh();
    }
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
    else { controller?.abort(); controller = null; closeChart(); }
  }
  get('events').addEventListener('click', event => {
    const button = event.target.closest('[data-month-chart-event]');
    if (!button || !snapshot) return;
    const row = snapshot.touches.find(item => String(item.event_id) === button.dataset.monthChartEvent);
    if (row) loadChart({mode:'event',event:row,symbol:row.symbol},'1H');
  });
  get('chart-symbol').addEventListener('change', event => {
    const symbol = event.target.value;
    if (symbol) loadChart({mode:'symbol',symbol},'1H');
  });
  get('search').addEventListener('input', renderEvents);
  get('side').addEventListener('change', renderEvents);
  document.addEventListener('visibilitychange', () => {
    if (document.hidden) { clearTimeout(chartRefreshTimer); chartRefreshTimer = null; chartRequest?.abort(); }
    else {
      refresh();
      if (chartDialog?.open && chartSelection?.mode === 'symbol') loadChart(chartSelection,chartSelection.period,{background:true});
    }
  });
  window.SpikeMonthFirstWeek = {setActive, refresh};
})();
