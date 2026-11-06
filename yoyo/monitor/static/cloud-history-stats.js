/* Descriptive totals for the signal chart's existing excursion paths.
 * These are hypothetical reference-boundary costs, not exchange fills.
 */
(() => {
  'use strict';

  const DEFAULT_FEES = Object.freeze({maker: 0.0002, taker: 0.0005});
  const EPSILON = 1e-9;
  const supportedExits = new Set(['stop', 'cloud_flip']);
  const nyDateFormatter = new Intl.DateTimeFormat('en-CA', {
    timeZone: 'America/New_York', year: 'numeric', month: '2-digit', day: '2-digit'
  });

  function finite(value) {
    return typeof value === 'number' && Number.isFinite(value);
  }

  function validWeek(value) {
    if (typeof value !== 'string' || !/^\d{4}-\d{2}-\d{2}$/.test(value)) return false;
    const day = new Date(`${value}T00:00:00Z`);
    return Number.isFinite(day.getTime()) && day.toISOString().slice(0, 10) === value && day.getUTCDay() === 1;
  }

  function nyWeekForTimestamp(timestamp) {
    if (!finite(timestamp)) return null;
    const date = new Date(timestamp);
    if (!Number.isFinite(date.getTime())) return null;
    const parts = Object.fromEntries(nyDateFormatter.formatToParts(date)
      .filter(part => part.type !== 'literal').map(part => [part.type, part.value]));
    const localDay = new Date(Date.UTC(Number(parts.year), Number(parts.month) - 1, Number(parts.day)));
    const daysSinceMonday = (localDay.getUTCDay() + 6) % 7;
    localDay.setUTCDate(localDay.getUTCDate() - daysSinceMonday);
    return localDay.toISOString().slice(0, 10);
  }

  function weekFor(plan) {
    const saved = plan?.week ?? plan?.range?.week;
    if (validWeek(saved)) return saved;
    return nyWeekForTimestamp(finite(plan?.signal_open_ms) ? plan.signal_open_ms : plan?.confirmed_ms);
  }

  // Clone JSON-like plan and coverage values so a callback or consumer cannot
  // mutate data owned by the caller through an output alias.
  function copyValue(value) {
    if (Array.isArray(value)) return value.map(copyValue);
    if (value && Object.getPrototypeOf(value) === Object.prototype) {
      return Object.fromEntries(Object.entries(value).map(([key, item]) => [key, copyValue(item)]));
    }
    return value;
  }

  function normalizeFees(fees = {}) {
    const maker = fees?.maker ?? DEFAULT_FEES.maker;
    const taker = fees?.taker ?? DEFAULT_FEES.taker;
    if (!finite(maker) || maker < 0 || !finite(taker) || taker < 0) {
      throw new TypeError('fees.maker and fees.taker must be finite non-negative rates');
    }
    return {maker, taker};
  }

  function deduplicate(plans) {
    const ordered = plans.map((plan, index) => ({plan, index})).sort((a, b) => {
      const at = finite(a.plan?.confirmed_ms) ? a.plan.confirmed_ms : Infinity;
      const bt = finite(b.plan?.confirmed_ms) ? b.plan.confirmed_ms : Infinity;
      return at - bt || String(a.plan?.id ?? '').localeCompare(String(b.plan?.id ?? '')) || a.index - b.index;
    });
    const selected = new Map();
    for (const item of ordered) {
      const symbol = typeof item.plan?.symbol === 'string' ? item.plan.symbol.trim() : '';
      const week = weekFor(item.plan);
      // Malformed identities cannot safely suppress another row.
      const key = symbol && week ? `${symbol}\u0000${week}` : `unkeyed\u0000${item.index}`;
      if (!selected.has(key)) selected.set(key, item);
    }
    return [...selected.values()].sort((a, b) => {
      const at = finite(a.plan?.confirmed_ms) ? a.plan.confirmed_ms : Infinity;
      const bt = finite(b.plan?.confirmed_ms) ? b.plan.confirmed_ms : Infinity;
      return at - bt || String(a.plan?.symbol ?? '').localeCompare(String(b.plan?.symbol ?? '')) ||
        String(a.plan?.id ?? '').localeCompare(String(b.plan?.id ?? '')) || a.index - b.index;
    }).map(({plan}) => plan);
  }

  function emptyTotals() {
    return {
      grossR: null, netR: null, feesR: null,
      closed: 0, wins: 0, losses: 0, breakeven: 0,
      winRate: null, averageR: null,
      open: 0, pending: 0, gap: 0, invalid: 0, unavailable: 0, unsupported: 0,
      firstConfirmationMs: null, lastConfirmationMs: null
    };
  }

  function finishTotals(totals, gross, fees, confirmations) {
    if (confirmations.length) {
      totals.firstConfirmationMs = Math.min(...confirmations);
      totals.lastConfirmationMs = Math.max(...confirmations);
    }
    if (totals.closed) {
      totals.grossR = gross;
      totals.feesR = fees;
      totals.netR = gross - fees;
      totals.winRate = totals.wins / totals.closed;
      totals.averageR = totals.netR / totals.closed;
    }
    return totals;
  }

  function summarize(plans, {pathFor, coverage = null, fees} = {}) {
    if (!Array.isArray(plans)) throw new TypeError('plans must be an array');
    const feeRates = normalizeFees(fees);
    const getPath = typeof pathFor === 'function' ? pathFor : plan => plan?.path ?? null;
    const references = [];
    const perSymbol = new Map();
    const aggregate = emptyTotals();
    let totalGross = 0, totalFees = 0;
    const allConfirmations = [];

    for (const original of deduplicate(plans)) {
      const plan = original && typeof original === 'object' ? original : {};
      const symbol = typeof plan.symbol === 'string' && plan.symbol.trim() ? plan.symbol.trim() : '<unknown>';
      if (!perSymbol.has(symbol)) perSymbol.set(symbol, {totals: emptyTotals(), gross: 0, fees: 0, confirmations: []});
      const bucket = perSymbol.get(symbol);
      const confirmation = finite(plan.confirmed_ms) ? plan.confirmed_ms : null;
      if (confirmation !== null) {
        allConfirmations.push(confirmation);
        bucket.confirmations.push(confirmation);
      }

      const side = plan.side;
      const entry = plan.entry;
      const stop = plan.stop;
      const risk = (entry - stop) * side;
      const validRisk = (side === 1 || side === -1) && finite(entry) && entry > 0 &&
        finite(stop) && stop > 0 && finite(risk) && risk > 0 && plan.plan_state !== 'invalid';
      if (!validRisk) {
        aggregate.invalid++;
        bucket.totals.invalid++;
        references.push({id: plan.id ?? null, symbol, week: weekFor(plan), confirmed_ms: confirmation,
          outcome: 'invalid', reason: 'invalid_stop_risk'});
        continue;
      }

      const path = getPath(copyValue(plan));
      if (path == null) {
        aggregate.unavailable++;
        bucket.totals.unavailable++;
        references.push({id: plan.id ?? null, symbol, week: weekFor(plan), confirmed_ms: confirmation,
          outcome: 'unavailable'});
        continue;
      }

      const exitState = path.exit_state;
      if (exitState != null && !supportedExits.has(exitState)) {
        aggregate.unsupported++;
        bucket.totals.unsupported++;
        references.push({id: plan.id ?? null, symbol, week: weekFor(plan), confirmed_ms: confirmation,
          outcome: 'unsupported', exit_state: exitState});
        continue;
      }
      if (supportedExits.has(exitState) && path.exit_bar_confirmed !== true) {
        aggregate.pending++;
        bucket.totals.pending++;
        references.push({id: plan.id ?? null, symbol, week: weekFor(plan), confirmed_ms: confirmation,
          outcome: 'pending', exit_state: exitState, exit_bar_confirmed: false});
        continue;
      }
      if (path.state === 'gap' && exitState == null) {
        aggregate.gap++;
        bucket.totals.gap++;
        references.push({id: plan.id ?? null, symbol, week: weekFor(plan), confirmed_ms: confirmation,
          outcome: 'gap'});
        continue;
      }
      if (exitState == null && path.state === 'open') {
        aggregate.open++;
        bucket.totals.open++;
        references.push({id: plan.id ?? null, symbol, week: weekFor(plan), confirmed_ms: confirmation,
          outcome: 'open'});
        continue;
      }
      if (exitState == null && path.state === 'unavailable') {
        aggregate.unavailable++;
        bucket.totals.unavailable++;
        references.push({id: plan.id ?? null, symbol, week: weekFor(plan), confirmed_ms: confirmation,
          outcome: 'unavailable'});
        continue;
      }
      if (!supportedExits.has(exitState) || path.exit_bar_confirmed !== true ||
          !finite(path.exit_r) || !finite(path.exit_price) || path.exit_price <= 0) {
        aggregate.invalid++;
        bucket.totals.invalid++;
        references.push({id: plan.id ?? null, symbol, week: weekFor(plan), confirmed_ms: confirmation,
          outcome: 'invalid', reason: 'malformed_confirmed_exit'});
        continue;
      }

      const grossR = path.exit_r;
      // Unit size cancels: divide each hypothetical price fee by the plan's
      // validated per-unit stop risk. The exit uses taker for stop/cloud flip.
      const entryFeeR = feeRates.maker * entry / risk;
      const exitFeeR = feeRates.taker * path.exit_price / risk;
      const feesR = entryFeeR + exitFeeR;
      const netR = grossR - feesR;
      aggregate.closed++;
      bucket.totals.closed++;
      if (netR > EPSILON) { aggregate.wins++; bucket.totals.wins++; }
      else if (netR < -EPSILON) { aggregate.losses++; bucket.totals.losses++; }
      else { aggregate.breakeven++; bucket.totals.breakeven++; }
      totalGross += grossR;
      totalFees += feesR;
      bucket.gross += grossR;
      bucket.fees += feesR;
      references.push({id: plan.id ?? null, symbol, week: weekFor(plan), confirmed_ms: confirmation,
        outcome: 'closed', exit_state: exitState, exit_bar_confirmed: true,
        grossR, feesR, netR, exit_price: path.exit_price});
    }

    finishTotals(aggregate, totalGross, totalFees, allConfirmations);
    const bySymbol = Object.fromEntries([...perSymbol.entries()].sort(([a], [b]) => a.localeCompare(b))
      .map(([symbol, bucket]) => [symbol, finishTotals(bucket.totals, bucket.gross, bucket.fees, bucket.confirmations)]));
    return {
      basis: 'confirmed_signal_chart_reference_paths',
      feeMeaning: 'hypothetical touched-boundary reference costs; not actual fills',
      coverage: copyValue(coverage),
      coverageScope: 'caller-supplied coverage only; not assumed to include all history',
      fees: {entry: {role: 'maker', rate: feeRates.maker}, exit: {role: 'taker', rate: feeRates.taker}},
      ...aggregate,
      bySymbol,
      references
    };
  }

  const api = Object.freeze({summarize, DEFAULT_FEES});
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  if (typeof window !== 'undefined') window.SpikeCloudHistoryStats = api;
})();
