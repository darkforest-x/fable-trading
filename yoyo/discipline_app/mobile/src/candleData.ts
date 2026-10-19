/** Public OKX display candles, never an execution price or risk input.
 * Source: OKX v5 market/candles, history-candles and business candle channels.
 * Times are UTC epoch seconds; daily bars use OKX's UTC+8 session boundary.
 */
export const PERIODS = { '1m': '1分', '5m': '5分', '15m': '15分', '1H': '1时', '4H': '4时', '1D': '日' } as const;
export type CandlePeriod = keyof typeof PERIODS;
export const PERIOD_SECONDS: Record<CandlePeriod, number> = { '1m': 60, '5m': 300, '15m': 900, '1H': 3600, '4H': 14400, '1D': 86400 };
export type Candle = { time: number; open: number; high: number; low: number; close: number; volume: number; confirmed: boolean };
export const MAX_CANDLES = 2000;
const PREFIX = 'spike-candles-v1:';
type Disk = Pick<Storage, 'getItem' | 'setItem' | 'removeItem' | 'key' | 'length'>;
const validSymbol = (symbol: string) => /^[A-Z0-9]+-USDT-SWAP$/.test(symbol);

export function validCandle(bar: Candle): boolean {
 return !!bar && [bar.time, bar.open, bar.high, bar.low, bar.close, bar.volume].every(Number.isFinite)
  && Number.isInteger(bar.time) && bar.time > 0 && Math.min(bar.open, bar.high, bar.low, bar.close) > 0
  && bar.volume >= 0 && bar.high >= Math.max(bar.open, bar.close) && bar.low <= Math.min(bar.open, bar.close)
  && typeof bar.confirmed === 'boolean';
}
export function parseCandles(rows: unknown): Candle[] {
 if (!Array.isArray(rows)) return [];
 const bars: Candle[] = [];
 for (const row of rows) {
  if (!Array.isArray(row) || row.length < 9 || ![0,1,2,3,4,6].every(i => typeof row[i] === 'string' && row[i].trim() !== '') || !['0','1'].includes(row[8])) continue;
  const bar = { time: Number(row[0]) / 1000, open: Number(row[1]), high: Number(row[2]), low: Number(row[3]), close: Number(row[4]), volume: Number(row[6]), confirmed: row[8] === '1' };
  if (validCandle(bar)) bars.push(bar);
 }
 return mergeCandles([], bars);
}
export function mergeCandles(base: Candle[], incoming: Candle[], limit = MAX_CANDLES): Candle[] {
 const byTime = new Map<number, Candle>();
 for (const bar of [...base, ...incoming]) {
  if (!validCandle(bar)) continue;
  if (byTime.get(bar.time)?.confirmed && !bar.confirmed) continue;
  byTime.set(bar.time, bar);
 }
 return [...byTime.values()].sort((a,b) => a.time - b.time).slice(-Math.max(1,limit));
}
export class CandleCache {
 private disk?: Disk;
 private now: () => number;
 constructor(disk?: Disk, now = Date.now) { this.disk = disk; this.now = now; }
 read(symbol: string, period: CandlePeriod): Candle[] {
  if (!validSymbol(symbol) || !(period in PERIODS)) return [];
  try {
   const raw = this.disk?.getItem(PREFIX + symbol + ':' + period);
   if (!raw || raw.length > 200000) return [];
   const value = JSON.parse(raw);
   if (!Number.isFinite(value.at) || value.at > this.now() || this.now() - value.at > 7 * 86400000 || !Array.isArray(value.bars)) return [];
   return mergeCandles([], value.bars, 600);
  } catch { return []; }
 }
 write(symbol: string, period: CandlePeriod, bars: Candle[]) {
  if (!this.disk || !validSymbol(symbol) || !(period in PERIODS) || !bars.length) return;
  try {
   const key = PREFIX + symbol + ':' + period;
   const entries: { key: string; at: number }[] = [];
   for (let i = 0; i < this.disk.length; i++) {
    const name = this.disk.key(i);
    if (!name?.startsWith(PREFIX) || name === key) continue;
    try { entries.push({ key: name, at: Number(JSON.parse(this.disk.getItem(name)!).at) || 0 }); }
    catch { entries.push({ key: name, at: 0 }); }
   }
   entries.sort((a,b) => b.at - a.at).forEach((e,i) => { if (i >= 11 || this.now() - e.at > 7 * 86400000) this.disk!.removeItem(e.key); });
   this.disk.setItem(key, JSON.stringify({ at: this.now(), bars: bars.slice(-600) }));
  } catch { /* Storage disabled/full must not interrupt live display. */ }
 }
}
export type CandleRequest = { before?: number; signal: AbortSignal };
export type FetchCandles = (symbol: string, period: CandlePeriod, options: CandleRequest) => Promise<Candle[]>;
export async function fetchCandles(symbol: string, period: CandlePeriod, options: CandleRequest, origin = 'https://www.okx.com'): Promise<Candle[]> {
 if (!validSymbol(symbol) || !(period in PERIODS)) throw Error('不支持的合约或周期');
 const controller = new AbortController();
 const abort = () => controller.abort();
 options.signal.throwIfAborted();
 options.signal.addEventListener('abort', abort, { once: true });
 const timer = setTimeout(abort, 8000);
 try {
  const url = new URL(options.before ? '/api/v5/market/history-candles' : '/api/v5/market/candles', origin);
  url.search = new URLSearchParams({ instId: symbol, bar: period, limit: '300', ...(options.before ? { after: String(options.before * 1000) } : {}) }).toString();
  const response = await fetch(url, { signal: controller.signal, credentials: 'omit', cache: 'no-store' });
  if (!response.ok) throw Error('K线暂时无法加载');
  const data = await response.json();
  if (data.code !== '0' || !Array.isArray(data.data)) throw Error('行情服务返回异常');
  const bars = parseCandles(data.data);
  if (data.data.length && !bars.length) throw Error('K线数据格式异常');
  return bars;
 } finally { clearTimeout(timer); options.signal.removeEventListener('abort', abort); }
}
