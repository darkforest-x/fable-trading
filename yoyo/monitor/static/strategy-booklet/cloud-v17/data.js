const historyRoot = new URL('../cloud-v17-history/', import.meta.url);
export const historyAsset = path => new URL(path, historyRoot).href;
export const amount = (value, digits = 2) => Number.isFinite(value)
  ? value.toLocaleString('en-US', {maximumFractionDigits: digits, minimumFractionDigits: digits}) : '—';
export const price = value => Number.isFinite(value)
  ? value.toLocaleString('en-US', {maximumSignificantDigits: 8, useGrouping: false}) : '—';
export const signed = value => Number.isFinite(value) ? (value > 0 ? '+' : '') + amount(value) : '—';
export const beijing = ms => Number.isFinite(ms) && ms > 0
  ? new Date(ms + 28800000).toISOString().slice(0, 16).replace('T', ' ') : '—';

export async function read(path, options = {}) {
  const response = await fetch(path, options);
  if (!response.ok) throw new Error('读取失败（HTTP ' + response.status + '）');
  return response.json();
}

export async function readTrade(row) {
  // StaticFiles may serve literal gzip bytes. Proxies can also decode them before delivery.
  const response = await fetch(historyAsset(row.dataUrl));
  if (!response.ok) throw new Error('未能读取逐笔流水');
  const bytes = new Uint8Array(await response.arrayBuffer());
  const stream = new Blob([bytes]).stream();
  const body = bytes[0] === 0x1f && bytes[1] === 0x8b ? stream.pipeThrough(new DecompressionStream('gzip')) : stream;
  const trade = await new Response(body).json();
  if (trade.id !== row.id || trade.eventId !== row.eventId || trade.fills.length !== row.nativeOrderCount)
    throw new Error('逐笔流水与交易编号不符');
  return trade;
}

export function filterTrades(rows, options) {
  const text = (options.query || '').trim().toUpperCase().replace(/[-/]?USDT(?:-SWAP)?$/, '');
  const from = options.from ? Date.parse(options.from + 'T00:00:00+08:00') : -Infinity;
  const until = options.until ? Date.parse(options.until + 'T00:00:00+08:00') + 86400000 : Infinity;
  const result = rows.filter(r => (!text || r.symbol.includes(text) || r.id.toUpperCase().includes(text))
    && (!options.side || r.side === Number(options.side))
    && (!options.outcome || (options.outcome === 'win' ? r.netPnl > 0 : r.netPnl < 0))
    && from <= r.entryMs && r.entryMs < until);
  const order = options.order || 'recent';
  return result.sort((a, b) => {
    if (order === 'profit') return b.netPnl - a.netPnl || a.id.localeCompare(b.id);
    if (order === 'loss') return a.netPnl - b.netPnl || a.id.localeCompare(b.id);
    return (order === 'early' ? a.entryMs - b.entryMs : b.entryMs - a.entryMs) || a.id.localeCompare(b.id);
  });
}

export const actionNames = {
  entry: '开仓', trim: '20% 敞口减仓', stop: '固定止损', stop_open: '开盘止损',
  ema_flip: '云带反转', ema_flip_open: '开盘云带反转', take_profit: '限价止盈',
  take_profit_open: '开盘止盈', data_gap_markout: '数据缺口退出',
  delisting_settlement: '退市结算', series_tail_close: '数据末端结算'
};
