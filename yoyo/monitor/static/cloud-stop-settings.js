/* Local display preferences for cloud signal reference stops. */
(() => {
  'use strict';

  const KEY = 'spike.cloud.stop-settings.v1';
  const DEFAULTS = Object.freeze({ btcEth: 1.5, other: 3 });
  const V5_POLICY = 'touched-side-1618-entry-symbol-fixed-stop-v5';
  const PREFERENCE_POLICY = 'touched-side-1618-entry-configurable-fixed-stop-v1';
  const ENTRY_BASIS = '触及侧 1.618（12H 收盘确认后展示）';

  function numberValue(value) {
    if (typeof value === 'number') return Number.isFinite(value) ? value : null;
    if (typeof value !== 'string') return null;
    const text = value.trim();
    if (!text || !/^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:e[+-]?\d+)?$/i.test(text)) return null;
    const number = Number(text);
    return Number.isFinite(number) ? number : null;
  }

  function isValidPercentage(value) {
    const number = numberValue(value);
    return number !== null && number >= 0.1 && number <= 50;
  }

  function normalize(value) {
    const source = value && typeof value === 'object' && !Array.isArray(value) ? value : {};
    return {
      btcEth: isValidPercentage(source.btcEth) ? numberValue(source.btcEth) : DEFAULTS.btcEth,
      other: isValidPercentage(source.other) ? numberValue(source.other) : DEFAULTS.other,
    };
  }

  function implicitStorage(explicit) {
    if (explicit !== undefined) return explicit;
    try { return typeof window === 'undefined' ? null : window.localStorage; }
    catch { return null; }
  }

  function read(storage) {
    try {
      const target = implicitStorage(storage);
      const saved = target && target.getItem(KEY);
      return normalize(saved == null ? null : JSON.parse(saved));
    } catch {
      return normalize();
    }
  }

  function write(storage, value) {
    try {
      const target = implicitStorage(storage);
      if (!target || typeof target.setItem !== 'function') return false;
      target.setItem(KEY, JSON.stringify(normalize(value)));
      return true;
    } catch {
      return false;
    }
  }

  function baseSymbol(symbol) {
    return typeof symbol === 'string' ? symbol.split('-', 1)[0].toUpperCase() : '';
  }

  function percentageFor(symbol, settings) {
    const value = normalize(settings);
    return baseSymbol(symbol) === 'BTC' || baseSymbol(symbol) === 'ETH' ? value.btcEth : value.other;
  }

  function finitePositive(value) {
    return typeof value === 'number' && Number.isFinite(value) && value > 0;
  }

  function rangeIsValid(item) {
    const range = item.range;
    if (!range || typeof range !== 'object' || range.state !== 'ready' || range.symbol !== item.symbol) return false;
    return item.week == null || range.week === item.week;
  }

  function stopBasis(symbol, percent) {
    const tier = baseSymbol(symbol) === 'BTC' || baseSymbol(symbol) === 'ETH' ? 'BTC/ETH' : '其他币种';
    return `${tier} 以 1.618 参考入场为基准固定止损 ${percent}%（多单下方 / 空单上方）`;
  }

  function project(item, settings) {
    if (!item || typeof item !== 'object' || Array.isArray(item)) return item;

    const normalized = normalize(settings);
    const percent = percentageFor(item.symbol, normalized);
    const stopPct = percent / 100;
    const side = item.side;
    const entry = item.entry;
    const target = item.target;
    const stop = (side === 1 || side === -1) && finitePositive(entry)
      ? entry * (1 - side * stopPct)
      : null;
    const previousOriginal = item.original_reference;
    const original = previousOriginal && typeof previousOriginal === 'object' && !Array.isArray(previousOriginal)
      ? { ...previousOriginal }
      : {
          entry: item.entry,
          stop: item.stop,
          target: item.target,
          planned_rr: item.planned_rr,
          basis: 'stored-reference',
        };

    const numeric = finitePositive(entry) && finitePositive(target) && finitePositive(stop);
    const risk = numeric && (side === 1 || side === -1) ? (entry - stop) * side : null;
    const reward = numeric && (side === 1 || side === -1) ? (target - entry) * side : null;
    const sourceInvalid = item.plan_state === 'invalid';
    const valid = !sourceInvalid && rangeIsValid(item) && risk > 0 && reward > 0 &&
      Number.isFinite(risk) && Number.isFinite(reward);
    const reason = valid ? '' : (sourceInvalid && item.plan_reason
      ? item.plan_reason
      : !rangeIsValid(item)
        ? (item.plan_reason || '缺少有效的本周 1.618 区间')
        : '固定比例止损或目标位置无效');
    const isDefault = normalized.btcEth === DEFAULTS.btcEth && normalized.other === DEFAULTS.other;

    return {
      ...item,
      entry,
      stop,
      stop_pct: stopPct,
      planned_rr: valid ? reward / risk : null,
      plan_state: valid ? 'ready' : 'invalid',
      plan_reason: reason,
      original_reference: original,
      reference_policy: item.range_mode==='monday'?'monday-boundary-entry-configurable-fixed-stop-v1':isDefault ? V5_POLICY : PREFERENCE_POLICY,
      entry_basis: item.entry_basis || ENTRY_BASIS,
      stop_basis: item.range_mode==='monday'?stopBasis(item.symbol, percent).replace('1.618','周一边界'):stopBasis(item.symbol, percent),
      stop_settings: { ...normalized },
      stop_settings_source: 'browser-preference',
    };
  }

  const api = { KEY, DEFAULTS, isValidPercentage, normalize, read, write, percentageFor, project };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  if (typeof window !== 'undefined') window.SpikeCloudStopSettings = api;
})();
