import {
  read, readTrade, historyAsset, amount, price, signed, beijing,
  filterTrades, actionNames, escapeHTML as esc, tableHTML
} from './data.js';

const PAGE_SIZE = 20;
const FILTER_KEYS = ['query', 'from', 'until', 'side', 'outcome', 'order'];
let manifestPromise;

const sideName = row => row.side === 1 ? '多' : '空';
const pnl = value => '<b class="rs-cloud-pnl' + (value > 0 ? ' is-positive' : value < 0 ? ' is-negative' : '') + '">' + signed(value) + ' U</b>';
const safeFact = (label, value) => '<div><small>' + esc(label) + '</small><b>' + esc(value) + '</b></div>';

function table(head, rows, className = '') {
  return tableHTML(head, rows, className);
}

function optionsFrom(form) {
  return Object.fromEntries(new FormData(form));
}

function loadManifest() {
  if (!manifestPromise) {
    manifestPromise = read(historyAsset('manifest.json')).then(value => {
      if (value.records?.length !== value.tradeCount || value.tradeCount !== 1252)
        throw new Error('交易目录应包含 1,252 笔历史资金单');
      return value;
    }).catch(error => {
      manifestPromise = undefined;
      throw error;
    });
  }
  return manifestPromise;
}

function evidenceHTML(evidence) {
  const bar = evidence.signalOhlc || {};
  const eth = evidence.benchmark30d?.ETH;
  const btc = evidence.benchmark30d?.BTC;
  const relative = eth?.available && btc?.available
    ? 100 * ((eth.endClose / eth.startClose) / (btc.endClose / btc.startClose) - 1) : null;
  const values = [
    ['信号最低', bar.low], ['信号最高', bar.high], ['确认收盘', bar.close],
    ['EMA20', evidence.ema?.ema20], ['EMA50', evidence.ema?.ema50],
    ['下轨', evidence.rails?.lowerPrice], ['上轨', evidence.rails?.upperPrice],
    ['固定止损', evidence.fixedStopPrice], ['2R 目标', evidence.twoRPrice]
  ];
  return '<div class="rs-facts rs-cloud-evidence-facts">' +
    values.filter(([, value]) => Number.isFinite(value)).map(([label, value]) => safeFact(label, price(value))).join('') +
    (Number.isFinite(relative) ? safeFact('ETH/BTC 30 天', signed(relative) + '%') : '') + '</div>';
}

function tradeRows(trades) {
  return trades.map(row => [
    '<button type="button" class="rs-cloud-trade-link" data-trade="' + esc(row.id) + '">' +
      '<span class="rs-cloud-trade-id">' + esc(row.id.slice(-6)) + '</span>' +
      '<b>' + esc(row.symbol) + ' · ' + sideName(row) + '</b></button>',
    esc(beijing(row.entryMs)),
    esc(price(row.entryPrice)),
    pnl(row.netPnl),
    esc(row.exitReasonLabel)
  ]);
}

function renderShell(root) {
  root.innerHTML = '<div class="rs-cloud-gallery">' +
    '<div class="rs-cloud-gallery-head"><div><h2>历史开仓样本与回放</h2>' +
    '<p>1,252 笔资金单 · 12H 开仓图 · 日期按北京时间筛选</p></div>' +
    '<a class="rs-cloud-download" data-cloud-download target="_blank" rel="noopener" download>下载全部开仓图 ↗</a></div>' +
    '<form class="rs-cloud-filters" role="search" aria-label="筛选历史交易">' +
      '<label class="rs-cloud-search">搜索币种或编号<input name="query" type="search" placeholder="例如 ETH、CFX、001082" autocomplete="off"></label>' +
      '<label>方向<select name="side"><option value="">全部方向</option><option value="1">做多</option><option value="-1">做空</option></select></label>' +
      '<label>结果<select name="outcome"><option value="">全部结果</option><option value="win">盈利</option><option value="loss">亏损</option></select></label>' +
      '<label class="rs-cloud-date-filter">开仓起日<input name="from" type="date"></label>' +
      '<label class="rs-cloud-date-filter">开仓止日<input name="until" type="date"></label>' +
      '<label>排序<select name="order"><option value="recent">最新开仓</option><option value="early">最早开仓</option>' +
        '<option value="profit">盈利最多</option><option value="loss">亏损最多</option></select></label>' +
      '<button class="rs-cloud-reset" type="reset">清除条件</button>' +
    '</form>' +
    '<p class="rs-cloud-gallery-status" role="status" aria-live="polite">正在读取 1,252 笔历史交易…</p>' +
    '<article class="rs-cloud-selected panel" data-selected-trade hidden></article>' +
    '<section class="rs-cloud-list panel" data-list-panel hidden>' +
      '<div class="rs-cloud-list-head"><div><h3>交易目录</h3><p data-list-count></p></div>' +
      '<div class="rs-cloud-pagination" data-pagination aria-label="历史交易分页"></div></div>' +
      '<div data-trade-list></div>' +
    '</section>' +
  '</div>';
  const link = root.querySelector('[data-cloud-download]');
  link.href = new URL('cloud-v17-charts.zip', import.meta.url).href;
}

export async function mountGallery(container, params = new URLSearchParams(), onChange = () => {}) {
  if (!container?.replaceChildren) throw new TypeError('mountGallery 需要有效的容器元素');
  const root = document.createElement('div');
  root.className = 'rs-cloud-gallery-mount';
  container.replaceChildren(root);
  renderShell(root);

  const form = root.querySelector('.rs-cloud-filters');
  const status = root.querySelector('.rs-cloud-gallery-status');
  const controller = new AbortController();
  const {signal} = controller;
  let manifest = null;
  let matches = [];
  let current = null;
  let page = 0;
  let selection = 0;
  let disposed = false;
  let externalParams = params instanceof URLSearchParams ? new URLSearchParams(params) : new URLSearchParams(params || '');

  const currentOptions = () => optionsFrom(form);
  const activeIndex = () => current ? matches.findIndex(row => row.id === current.id) : -1;
  const notifyChange = () => {
    if (disposed) return;
    const next = new URLSearchParams(externalParams);
    for (const key of [...FILTER_KEYS, 'trade']) next.delete(key);
    const options = currentOptions();
    for (const key of FILTER_KEYS) {
      const value = String(options[key] || '');
      if (value && !(key === 'order' && value === 'recent')) next.set(key, value);
    }
    if (current) next.set('trade', current.id);
    externalParams = new URLSearchParams(next);
    try {
      const pending = onChange(next);
      if (pending && typeof pending.catch === 'function') pending.catch(() => {});
    } catch { /* Host route callbacks must not break local gallery interactions. */ }
  };

  function renderList() {
    const panel = root.querySelector('[data-list-panel]');
    const listing = root.querySelector('[data-trade-list]');
    const count = root.querySelector('[data-list-count]');
    const pagination = root.querySelector('[data-pagination]');
    const pages = Math.ceil(matches.length / PAGE_SIZE);
    panel.hidden = matches.length === 0;
    count.textContent = matches.length ? '第 ' + (page + 1) + ' / ' + pages + ' 页 · 每页 20 笔' : '';
    pagination.innerHTML = '<button type="button" data-page="-1" aria-label="上一页"' + (!page ? ' disabled' : '') + '>‹</button>' +
      '<span>' + (matches.length ? (page + 1) + ' / ' + pages : '0 / 0') + '</span>' +
      '<button type="button" data-page="1" aria-label="下一页"' + (page + 1 >= pages ? ' disabled' : '') + '>›</button>';
    listing.innerHTML = matches.length
      ? table(['交易编号 / 合约','开仓（北京）','入场价','净盈亏','退出原因'], tradeRows(matches.slice(page * PAGE_SIZE, (page + 1) * PAGE_SIZE)), 'rs-cloud-trades')
      : '';
    listing.querySelectorAll('[data-trade]').forEach(button => {
      button.closest('tr')?.classList.toggle('is-selected', button.dataset.trade === current?.id);
    });
  }

  function renderTrade(index) {
    const detail = root.querySelector('[data-selected-trade]');
    if (index < 0 || !matches[index]) {
      current = null;
      selection++;
      detail.hidden = true;
      detail.replaceChildren();
      renderList();
      return;
    }

    current = matches[index];
    page = Math.floor(index / PAGE_SIZE);
    const row = current;
    const revision = ++selection;
    const chart = historyAsset(row.chartUrl);
    detail.hidden = false;
    detail.innerHTML = '<div class="rs-cloud-selected-head"><div><span class="rs-badge">' + esc(row.id) + '</span>' +
      '<h3>' + esc(row.symbol) + ' · ' + sideName(row) + '单</h3></div>' +
      '<div class="rs-cloud-trade-nav"><button type="button" data-step="-1" aria-label="上一笔交易"' + (index === 0 ? ' disabled' : '') + '>上一笔</button>' +
      '<span>' + (index + 1).toLocaleString('en-US') + ' / ' + matches.length.toLocaleString('en-US') + '</span>' +
      '<button type="button" data-step="1" aria-label="下一笔交易"' + (index + 1 === matches.length ? ' disabled' : '') + '>下一笔</button></div></div>' +
      '<div class="rs-facts rs-cloud-trade-facts">' +
      safeFact('开仓时间（北京）', beijing(row.entryMs)) + safeFact('回测入场价', price(row.entryPrice)) +
      safeFact('名义金额', amount(row.notional) + ' U') + safeFact('手续费', amount(row.fees) + ' U') +
      '<div><small>净盈亏</small>' + pnl(row.netPnl) + '</div></div>' +
      '<figure class="rs-cloud-chart-figure"><div class="rs-cloud-chart-scroll" tabindex="0" role="region" aria-label="K线图，可在较窄屏幕上滚动查看">' +
      '<a class="rs-cloud-chart-original" href="' + esc(chart) + '" target="_blank" rel="noopener" aria-label="在新标签页查看原尺寸K线图">' +
      '<img class="rs-cloud-chart" src="' + esc(chart) + '" width="1280" height="560" alt="' + esc(row.symbol) + ' 12H 开仓图，含云带、周轨和退出标记" decoding="async"></a></div>' +
      '<figcaption><span>' + row.chartAudit.availableBars + ' 根 12H K 线 · 北京时间 · 蓝线 EMA20，金线 EMA50，紫色虚线为当周外轨，紫点为敞口减仓' +
      (row.chartAudit.missingBars ? ' · 图窗缺 ' + row.chartAudit.missingBars + ' 根，缺口留白' : '') + '</span>' +
      '<a href="' + esc(chart) + '" target="_blank" rel="noopener">查看原图 ↗</a></figcaption></figure>' +
      '<div class="rs-facts rs-cloud-exit-facts">' + safeFact('跟随仓退出所在小时', beijing(row.exitMs)) +
      safeFact('退出价', price(row.exitPrice)) + safeFact('退出原因', row.exitReasonLabel) + '</div>' +
      '<details class="rs-cloud-evidence" data-evidence><summary>开仓依据与全部 ' + row.nativeOrderCount + ' 条子仓成交</summary>' +
      '<div class="rs-cloud-evidence-body" data-evidence-body><p class="rs-muted">展开后读取逐笔证据…</p></div></details>';

    const evidenceDetails = detail.querySelector('[data-evidence]');
    evidenceDetails.addEventListener('toggle', async event => {
      if (!event.target.open || event.target.dataset.loaded) return;
      const box = event.target.querySelector('[data-evidence-body]');
      event.target.dataset.loaded = '1';
      box.innerHTML = '<p class="rs-muted">正在读取成交证据…</p>';
      try {
        const trade = await readTrade(row);
        if (disposed || revision !== selection) return;
        const evidence = trade.entryEvidence;
        const audit = row.chartAudit;
        box.innerHTML = (evidence
          ? evidenceHTML(evidence)
          : '<div class="rs-facts rs-cloud-evidence-facts">' + safeFact('触及侧 1.618', price(trade.mondayRail?.level)) + '</div>') +
          '<p class="rs-cloud-receipt-note">盘中成交只保留所属 1H 区间。流水现金变动含开平本金，逐行求和为整笔扣费净盈亏。' +
          (audit.missingBars ? ' 图窗缺 ' + audit.missingBars + ' 根 12H K 线，按原数据留白。' : '') + '</p>' +
          table(['小时起点（北京）','子仓','动作','成交价','手续费 U','现金变动 U'], trade.fills.map(fill => [
            esc(beijing(fill.hourMs)) + (fill.phase === 'intrabar' ? ' · 盘中' : ' · 开盘'),
            esc(String(fill.childSlot % 4 + 1)), esc(actionNames[fill.action] || fill.action),
            esc(price(fill.fillPrice)), esc(amount(fill.fees, 4)), esc(signed(fill.cashPnl))
          ]), 'rs-cloud-fills');
      } catch (error) {
        if (disposed || revision !== selection) return;
        box.innerHTML = '<p class="rs-warning">读取成交证据失败：' + esc(error.message) + '。收起后重新展开可重试。</p>';
        delete event.target.dataset.loaded;
      }
    }, {signal});

    renderList();
  }

  function applyFilters(tradeId = '', deep = false) {
    if (!manifest || disposed) return;
    matches = filterTrades(manifest.records, currentOptions());
    const selected = tradeId ? matches.findIndex(row => row.id === tradeId) : 0;
    const mismatch = Boolean(deep && tradeId && selected < 0);
    status.textContent = '共 ' + manifest.records.length.toLocaleString('en-US') + ' 笔，当前匹配 ' +
      matches.length.toLocaleString('en-US') + ' 笔。' + (mismatch ? '直达编号与当前筛选条件不匹配。' : '') +
      (!matches.length ? '可以清除条件或调整日期。' : '');
    if (!matches.length) {
      root.querySelector('[data-selected-trade]').hidden = true;
      root.querySelector('[data-list-panel]').hidden = true;
      current = null;
      selection++;
      return;
    }
    renderTrade(Math.max(0, selected));
  }

  const onFilterInput = event => {
    if (!event.target.matches('[name="query"]')) return;
    applyFilters();
    notifyChange();
  };
  const onFilterChange = event => {
    if (event.target.closest('.rs-cloud-filters') && !event.target.matches('[name="query"]')) {
      applyFilters();
      notifyChange();
    }
  };
  form.addEventListener('submit', event => event.preventDefault(), {signal});
  form.addEventListener('input', onFilterInput, {signal});
  form.addEventListener('change', onFilterChange, {signal});
  form.addEventListener('reset', () => {
    setTimeout(() => {
      if (!disposed) {
        applyFilters();
        notifyChange();
      }
    }, 0);
  }, {signal});

  root.addEventListener('click', event => {
    const button = event.target.closest('[data-step],[data-page],[data-trade],[data-retry]');
    if (!button || !root.contains(button) || button.disabled) return;
    if (button.hasAttribute('data-retry')) {
      void initialize();
      return;
    }
    let target = -1;
    if (button.hasAttribute('data-step')) target = activeIndex() + Number(button.dataset.step);
    else if (button.hasAttribute('data-page')) target = (page + Number(button.dataset.page)) * PAGE_SIZE;
    else target = matches.findIndex(row => row.id === button.dataset.trade);
    if (!matches[target]) return;
    renderTrade(target);
    notifyChange();
    if (button.hasAttribute('data-trade'))
      root.querySelector('[data-selected-trade]').scrollIntoView({block: 'start', behavior: 'smooth'});
  }, {signal});

  async function initialize() {
    status.textContent = '正在读取 1,252 笔历史交易…';
    try {
      const data = await loadManifest();
      if (disposed) return;
      manifest = data;
      root.querySelector('[data-cloud-download]').href = new URL('cloud-v17-charts.zip', import.meta.url).href;
      for (const key of FILTER_KEYS) {
        const control = form.elements.namedItem(key);
        if (control && externalParams.has(key)) control.value = externalParams.get(key);
      }
      const requestedTrade = externalParams.get('trade') || '';
      applyFilters(requestedTrade, true);
    } catch (error) {
      if (disposed) return;
      status.innerHTML = '<span class="rs-warning">历史目录暂不可用：' + esc(error.message) + '</span> ' +
        '<button type="button" class="rs-cloud-retry" data-retry>重试</button>';
    }
  }

  await initialize();
  return () => {
    if (disposed) return;
    disposed = true;
    selection++;
    controller.abort();
    root.remove();
  };
}
