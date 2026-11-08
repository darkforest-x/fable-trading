/* Read-only adapter for the frozen breadth-EMA20 event ledger. */
(function attach(root, factory) {
  const api = factory();
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  if (root) root.SpikeBreadthReplayData = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, function createApi() {
  'use strict';

  const DATA_URL = '/static/strategy-booklet/breadth-ema20-data.json';
  const TRACK = 'breadth-ema20';
  const SCOPES = ['all_history_fee_only', '2026_actual_funded'];
  const KINDS = ['', 'medium', 'case', 'BTC', 'ALT'];
  const finite = Number.isFinite;
  const cacheByData = new WeakMap();
  let loadedData = null;
  let loadState = null;

  function utcMillis(value, field) {
    if (typeof value !== 'string' || !value.endsWith('Z')) throw Error(`无效 UTC 时间：${field}`);
    const time = Date.parse(value);
    if (!finite(time)) throw Error(`无效 UTC 时间：${field}`);
    return time;
  }

  function closeEnough(a, b) {
    return finite(a) && finite(b) && Math.abs(a - b) <= Math.max(1e-9, Math.abs(b) * 1e-9);
  }

  function safeFrozenSymbol(value) {
    return typeof value === 'string' && /^[\p{L}0-9]{1,32}USDT$/u.test(value);
  }

  function validateHeader(data) {
    if (data?.schema_version !== 'breadth-ema20.v1' || data.strategy_id !== TRACK ||
        data.provenance?.experiment_id !== 'exp-monday-breadth-extension-20261108-v1' || !Array.isArray(data.events)) {
      throw Error('全市场下轨冻结账本版本或来源不匹配');
    }
  }

  function validateEvent(event, entryMs, exitMs, funded) {
    if (!event || !['BTC', 'ALT'].includes(event.asset) || event.event_id !== `${event.asset}|${event.entry_time}` ||
        exitMs < entryMs || !finite(event[funded ? 'funded_pct' : 'net_pct'])) {
      throw Error('全市场下轨账本含无效事件');
    }
    if (event.asset === 'BTC') {
      if (!['medium', 'case'].includes(event.source) || !finite(event.entry_price) || event.entry_price <= 0 ||
          !finite(event.exit_price) || event.exit_price <= 0 || !Array.isArray(event.exit_legs_detail) ||
          !Number.isInteger(event.exit_legs) || event.exit_legs_detail.length !== event.exit_legs || !event.exit_legs_detail.length) {
        throw Error(`BTC 事件 ${event.event_id} 缺少真实成交标记`);
      }
      let fraction = 0, previousTime = entryMs;
      for (const leg of event.exit_legs_detail) {
        const time = utcMillis(leg?.time, 'exit_legs_detail.time');
        if (time < previousTime || time > exitMs || !finite(leg.price) || leg.price <= 0 ||
            !finite(leg.size_fraction) || leg.size_fraction <= 0 || !['limit_maker', 'market_taker'].includes(leg.fill_type)) {
          throw Error(`BTC 事件 ${event.event_id} 的退出成交无效`);
        }
        fraction += leg.size_fraction;
        previousTime = time;
      }
      const finalLeg = event.exit_legs_detail.at(-1);
      if (!closeEnough(fraction, 1) || previousTime !== exitMs || !closeEnough(finalLeg.price, event.exit_price)) {
        throw Error(`BTC 事件 ${event.event_id} 的退出几何与冻结记录不一致`);
      }
      return;
    }

    if (event.source !== 'baseline_6h' || !Array.isArray(event.members) || !Number.isInteger(event.expected_members) ||
        event.expected_members !== event.observed_members || event.members.length !== event.observed_members) {
      throw Error(`ALT 事件 ${event.event_id} 的冻结成员覆盖不完整`);
    }
    const symbols = new Set();
    for (const member of event.members) {
      const memberEntry = utcMillis(member?.entry_time, 'members.entry_time');
      const memberExit = utcMillis(member?.selected_exit_time, 'members.selected_exit_time');
      if (member.event_id !== event.event_id || !safeFrozenSymbol(member.symbol) || symbols.has(member.symbol) ||
          memberEntry !== entryMs || memberExit !== exitMs || !finite(member.entry_price) || member.entry_price <= 0 ||
          !finite(member.selected_exit_open) || member.selected_exit_open <= 0 || !finite(member.net_pct) ||
          (member.selected_net_pct != null && !finite(member.selected_net_pct)) ||
          (member.gross_pct != null && !finite(member.gross_pct))) {
        throw Error(`ALT 事件 ${event.event_id} 含无效或重复成员`);
      }
      if (funded && !finite(member.selected_funded_net_pct ?? member.funded_pct)) {
        throw Error(`ALT 成员 ${member.symbol} 缺少 2026 资金费收益`);
      }
      symbols.add(member.symbol);
    }
  }

  function historyApi() {
    let api = globalThis.SpikeBreadthHistory;
    if (!api && typeof require === 'function') api = require('./breadth-book-history.js');
    if (typeof api?.createBreadthBookHistory !== 'function') throw Error('全市场下轨结算历史模块尚未加载');
    return api;
  }

  function addIndex(index, key, item) {
    let rows = index.get(key);
    if (!rows) index.set(key, rows = []);
    rows.push(item);
  }

  function scopeCache(data, scope) {
    if (!SCOPES.includes(scope)) throw Error('未知的全市场下轨账本区间');
    let byScope = cacheByData.get(data);
    if (!byScope) cacheByData.set(data, byScope = new Map());
    if (byScope.has(scope)) return byScope.get(scope);

    validateHeader(data);
    const funded = scope === '2026_actual_funded';
    const events = data.events.filter(event => !funded || event.entry_time?.slice(0, 4) === '2026');
    const seen = new Set();
    const ordered = events.map(source => {
      const entryMs = utcMillis(source?.entry_time, 'entry_time');
      const exitMs = utcMillis(source?.exit_time, 'exit_time');
      validateEvent(source, entryMs, exitMs, funded);
      if (seen.has(source.event_id)) throw Error(`重复的冻结事件 ${source.event_id}`);
      seen.add(source.event_id);
      return { source, entryMs, exitMs, returnPct: source[funded ? 'funded_pct' : 'net_pct'] };
    }).sort((a, b) => a.exitMs - b.exitMs || a.source.event_id.localeCompare(b.source.event_id));

    // dayRows is the account settlement ledger; this adapter does not rebuild its equity or PnL.
    const history = historyApi().createBreadthBookHistory(data, { track: TRACK, scope });
    const settledRows = Object.values(history.dayRows || {}).flat();
    if (settledRows.length !== ordered.length || settledRows.some(row => typeof row.eventId !== 'string' || !finite(row.pnl))) {
      throw Error('全市场下轨结算 dayRows 与冻结事件不一致');
    }
    const pnlByEvent = new Map(settledRows.map(row => [row.eventId, row.pnl]));
    if (pnlByEvent.size !== ordered.length || ordered.some(item => !pnlByEvent.has(item.source.event_id))) {
      throw Error('全市场下轨结算 dayRows 含重复或缺失事件');
    }

    const numericIdByEvent = new Map(data.events.map((event, index) => [event.event_id, index + 1]));
    const byKind = new Map(), byPeriod = new Map(), bySymbol = new Map();
    for (const item of ordered) {
      const event = item.source;
      addIndex(byKind, '', item);
      addIndex(byKind, event.asset, item);
      if (event.asset === 'BTC') {
        addIndex(byKind, event.source, item);
        addIndex(bySymbol, 'BTCUSDT', item);
      } else {
        for (const member of event.members) addIndex(bySymbol, member.symbol, item);
      }
      addIndex(byPeriod, new Date(item.entryMs).toISOString().slice(0, 4), item);
      addIndex(byPeriod, new Date(item.entryMs).toISOString().slice(0, 7), item);
    }
    const cache = { ordered, pnlByEvent, numericIdByEvent, byKind, byPeriod, bySymbol };
    byScope.set(scope, cache);
    return cache;
  }

  function abortError() {
    const error = Error('读取已取消');
    error.name = 'AbortError';
    return error;
  }

  function load({ signal, fetcher } = {}) {
    if (loadedData) return Promise.resolve(loadedData);
    if (signal?.aborted) return Promise.reject(abortError());
    if (loadState && !loadState.signal?.aborted) return loadState.promise;
    const request = fetcher || (typeof fetch === 'function' ? fetch.bind(globalThis) : null);
    if (!request) return Promise.reject(Error('当前环境无法读取全市场下轨冻结账本'));

    const state = { signal, promise: null };
    state.promise = Promise.resolve().then(async () => {
      const response = await request(DATA_URL, { cache: 'force-cache', signal });
      if (signal?.aborted) throw abortError();
      if (!response?.ok) throw Error('全市场下轨冻结账本暂不可读，请重试');
      const data = await response.json();
      if (signal?.aborted) throw abortError();
      validateHeader(data);
      loadedData = data;
      return data;
    }).finally(() => {
      if (loadState === state) loadState = null;
    });
    loadState = state;
    return state.promise;
  }

  function utcFilter(period, entryMs) {
    if (!period) return true;
    if (!/^\d{4}(?:-(?:0[1-9]|1[0-2]))?$/.test(period)) throw Error('时间筛选请使用 YYYY 或 YYYY-MM');
    return new Date(entryMs).toISOString().startsWith(`${period}-`);
  }

  function branchOf(event) {
    if (event.asset === 'ALT') return { branch: 'ALT', branch_label: '山寨篮子' };
    return event.source === 'case'
      ? { branch: 'case', branch_label: 'BTC 急跌' }
      : { branch: 'medium', branch_label: 'BTC 普通' };
  }

  function legLabel(event, leg, entryMs, exitMs, rules) {
    const share = Math.round(leg.size_fraction * 100);
    if (leg.fill_type === 'limit_maker') return leg.size_fraction < 1
      ? `部分止盈 · 限价 · ${share}%` : `限价退出 · ${share}%`;
    const hours = (event.source === 'case' ? rules?.btc_case?.max_hold_hours : rules?.btc_medium?.max_hold_hours);
    const deadline = finite(hours) && exitMs - entryMs === hours * 60 * 60_000;
    if (event.exit_policy === 'half_then_6d') return deadline
      ? `6天到期 · 市价 · ${share}%` : `止损/到期退出 · 市价 · ${share}%`;
    if (event.exit_policy === 'fixed_6d_market' && deadline) return `6天到期 · 市价 · ${share}%`;
    return `策略退出 · 市价 · ${share}%`;
  }

  function eventRow(source, scope, pnl, memberSymbol, rules, id) {
    const member = source.asset === 'ALT'
      ? source.members.find(item => item.symbol === memberSymbol) || source.members[0]
      : null;
    const memberSymbols = member ? source.members.map(item => item.symbol) : [];
    const branch = branchOf(source);
    const entryMs = utcMillis(source.entry_time, 'entry_time');
    const basketExitMs = utcMillis(source.exit_time, 'exit_time');
    const entryPrice = member ? member.entry_price : source.entry_price;
    const exitMs = member ? utcMillis(member.selected_exit_time, 'members.selected_exit_time') : basketExitMs;
    const exitPrice = member ? member.selected_exit_open : source.exit_price;
    const memberNet = member ? (member.selected_net_pct ?? member.net_pct) : null;
    const memberFunded = member ? (member.selected_funded_net_pct ?? member.funded_pct) : null;
    const memberReturn = member ? (scope === '2026_actual_funded' ? memberFunded : memberNet) : null;
    const basketReturn = source[scope === '2026_actual_funded' ? 'funded_pct' : 'net_pct'];
    const exits = source.asset === 'ALT'
      ? [{ time_ms: exitMs, price: exitPrice, label: '6小时成员退出' }]
      : source.exit_legs_detail.map(leg => {
        const timeMs = utcMillis(leg.time, 'exit_legs_detail.time');
        const normalized = { time_ms: timeMs, price: leg.price, fill_type: leg.fill_type, size_fraction: leg.size_fraction };
        return { ...normalized, label: legLabel(source, normalized, entryMs, basketExitMs, rules) };
      });
    return {
      id,
      event_id: source.event_id,
      symbol: source.asset === 'BTC' ? 'BTCUSDT' : member.symbol,
      asset: source.asset,
      side: 1,
      ...branch,
      source: source.source,
      venue: 'Binance USD-M',
      scope,
      period: source.entry_time.slice(0, 7),
      expected_members: source.expected_members,
      observed_members: source.observed_members,
      member_coverage_pct: source.member_coverage_pct,
      entry_ms: entryMs,
      exit_ms: exitMs,
      entry_price: entryPrice,
      exit_price: exitPrice,
      pnl,
      return_pct: basketReturn,
      basket_return_pct: basketReturn,
      member_symbols: memberSymbols,
      selected_member_symbol: member?.symbol || null,
      member_return_pct: memberReturn,
      member_net_pct: memberNet,
      member_funded_pct: memberFunded,
      member_gross_pct: member?.gross_pct ?? null,
      funding_included: scope === '2026_actual_funded',
      exit_policy: source.exit_policy,
      exit_reason_code: source.exit_reason_code,
      signal_snapshot: source.signal_snapshot,
      fills: {
        entry_start_ms: entryMs,
        entry_end_ms: entryMs,
        entry_price: entryPrice,
        exit_start_ms: exits.at(-1).time_ms,
        exit_end_ms: exits.at(-1).time_ms,
        exit_price: exits.at(-1).price,
        exit_kind: 'point',
        exit_legs: exits,
      },
      point_times: true,
    };
  }

  function create(data, { scope = 'all_history_fee_only', period = '', kind = '' } = {}) {
    const cache = scopeCache(data, scope);
    if (!KINDS.includes(kind)) throw Error('未知的全市场下轨事件筛选');
    if (period && !/^\d{4}(?:-(?:0[1-9]|1[0-2]))?$/.test(period)) throw Error('时间筛选请使用 YYYY 或 YYYY-MM');
    const eventById = new Map(cache.ordered.map(item => [item.source.event_id, item]));

    function chooseMember(source, memberSymbol) {
      if (source.asset !== 'ALT') {
        if (memberSymbol && memberSymbol !== 'BTCUSDT') throw Error('BTC 事件没有可切换的篮子成员');
        return null;
      }
      const member = memberSymbol ? source.members.find(item => item.symbol === memberSymbol) : source.members[0];
      if (!member) throw Error(`币种 ${memberSymbol} 不属于这次冻结的 ALT 事件`);
      return member;
    }

    function rowFor(item, memberSymbol = '') {
      return eventRow(item.source, scope, cache.pnlByEvent.get(item.source.event_id), memberSymbol, data.rules,
        cache.numericIdByEvent.get(item.source.event_id));
    }

    function page(params = {}) {
      const offset = params.offset ?? 0, limit = params.limit ?? 50;
      const sort = params.sort ?? 'time', direction = params.direction ?? 'desc';
      const selectedKind = params.kind ?? kind, selectedPeriod = params.period ?? period, symbol = params.symbol || '';
      if (!Number.isInteger(offset) || offset < 0 || !Number.isInteger(limit) || limit < 1 || limit > 100) throw Error('无效的事件分页范围');
      if (!KINDS.includes(selectedKind)) throw Error('未知的全市场下轨事件筛选');
      if (!['time', 'pnl', 'return'].includes(sort) || !['asc', 'desc'].includes(direction)) throw Error('无效的事件排序');
      if (symbol && !cache.bySymbol.has(symbol)) throw Error('币种不在全市场下轨冻结成员名单中');
      if (selectedPeriod && !/^\d{4}(?:-(?:0[1-9]|1[0-2]))?$/.test(selectedPeriod)) throw Error('时间筛选请使用 YYYY 或 YYYY-MM');

      const candidates = [
        selectedKind ? cache.byKind.get(selectedKind) || [] : cache.ordered,
        selectedPeriod ? cache.byPeriod.get(selectedPeriod) || [] : cache.ordered,
        symbol ? cache.bySymbol.get(symbol) || [] : cache.ordered,
      ].sort((a, b) => a.length - b.length)[0];
      const filtered = candidates.filter(item => {
        const event = item.source;
        if (selectedKind === 'BTC' && event.asset !== 'BTC' || selectedKind === 'ALT' && event.asset !== 'ALT' ||
            selectedKind === 'medium' && !(event.asset === 'BTC' && event.source === 'medium') ||
            selectedKind === 'case' && !(event.asset === 'BTC' && event.source === 'case')) return false;
        if (selectedPeriod && !utcFilter(selectedPeriod, item.entryMs)) return false;
        if (symbol && (event.asset === 'BTC' ? symbol !== 'BTCUSDT' : !event.members.some(member => member.symbol === symbol))) return false;
        return true;
      }).sort((a, b) => {
        let primary = sort === 'time' ? a.entryMs - b.entryMs
          : sort === 'pnl' ? cache.pnlByEvent.get(a.source.event_id) - cache.pnlByEvent.get(b.source.event_id)
            : a.returnPct - b.returnPct;
        if (direction === 'desc') primary = -primary;
        return primary || a.source.event_id.localeCompare(b.source.event_id);
      });
      const rows = filtered.slice(offset, offset + limit).map(item =>
        rowFor(item, symbol && item.source.asset === 'ALT' ? symbol : ''));
      const focusItem = filtered[0] || null;
      const focus = focusItem ? rowFor(focusItem, symbol && focusItem.source.asset === 'ALT' ? symbol : '') : null;
      return {
        schema: 1,
        track: TRACK,
        sort,
        direction,
        symbol,
        offset,
        total: filtered.length,
        has_more: offset + rows.length < filtered.length,
        rows,
        ranking: [],
        focus,
        meta: {
          track: TRACK,
          trades: cache.ordered.length,
          funding_included: scope === '2026_actual_funded',
          basis: '净盈亏按历史结算账本；收益率按 BTC 袖套或 ALT 篮子结果。',
          note: scope === '2026_actual_funded'
            ? '只统计 2026 年入场，计入已记录资金费；ALT 每个篮子事件计一笔。'
            : '全历史扣除手续费，不含资金费；ALT 每个篮子事件计一笔。',
          limitation: 'ALT 图只显示所选成员的单币 K 线，篮子收益仍按组合记录；K 线来自 Binance USD-M 历史数据，不是 OKX 实时行情。',
        },
      };
    }

    function resolve(eventId, memberSymbol = '') {
      const item = eventById.get(eventId);
      if (!item) throw Error(`找不到冻结事件 ${eventId}`);
      chooseMember(item.source, memberSymbol);
      return rowFor(item, memberSymbol);
    }

    return { page, resolve };
  }

  return { load, create };
});
