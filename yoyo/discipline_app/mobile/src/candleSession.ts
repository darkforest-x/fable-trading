/** One visible chart owns one public candle stream. Stop aborts reads and fences late callbacks.
 * REST snapshots repair reconnect gaps; websocket bars received during a snapshot take precedence.
 */
import { CandleCache, MAX_CANDLES, PERIOD_SECONDS, mergeCandles, parseCandles, type Candle, type CandlePeriod, type FetchCandles } from './candleData.ts';
export type CandleStatus = 'loading' | 'cached' | 'connecting' | 'live' | 'delayed' | 'offline' | 'paused';
export type CandleState = { bars: Candle[]; status: CandleStatus; error: string; loadingOlder: boolean; canLoadOlder: boolean; reset: number };
type Options = { symbol: string; period: CandlePeriod; cache: CandleCache; fetchPage: FetchCandles; socket: () => WebSocket; onChange: (state: CandleState) => void; now?: () => number };
export class CandleSession {
 private options: Options;
 private state: CandleState;
 private active = false;
 private generation = 0;
 private socket: WebSocket | null = null;
 private reads = new Set<AbortController>();
 private retryTimer?: ReturnType<typeof setTimeout>;
 private heartbeat?: ReturnType<typeof setInterval>;
 private cacheTimer?: ReturnType<typeof setTimeout>;
 private attempts = 0;
 private lastMessage = 0;
 private connectedAt = 0;
 private lastData = 0;
 private lastSnapshot = 0;
 private syncing = false;
 private hasOpened = false;
 private duringSync: Candle[] = [];
 constructor(options: Options) {
  this.options = options;
  const bars = options.cache.read(options.symbol, options.period);
  this.state = { bars, status: bars.length ? 'cached' : 'loading', error: '', loadingOlder: false, canLoadOlder: true, reset: 0 };
 }
 private now() { return (this.options.now ?? Date.now)(); }
 private emit() { this.options.onChange({ ...this.state }); }
 private persist() { this.options.cache.write(this.options.symbol, this.options.period, this.state.bars); }
 private changed(bars: Candle[], reset = false) {
  this.state.bars = bars;
  if (reset) this.state.reset++;
  this.state.canLoadOlder = this.state.canLoadOlder && bars.length < MAX_CANDLES;
  if (!this.cacheTimer) this.cacheTimer = setTimeout(() => { this.cacheTimer = undefined; this.persist(); }, 5000);
  this.emit();
 }
 start() {
  if (this.active) return;
  this.active = true; this.generation++;
  this.state.status = this.state.bars.length ? 'cached' : 'loading'; this.emit();
  void this.refresh(); this.connect();
  this.heartbeat = setInterval(() => {
   if (!this.active) return;
   if (this.socket?.readyState === 0 && this.now() - this.connectedAt > 10000) this.socket.close();
   if (this.socket?.readyState === 1) {
    if (this.now() - this.lastMessage > 20000 || this.now() - Math.max(this.lastData, this.connectedAt) > 30000) this.socket.close();
    else this.socket.send('ping');
   }
   if (this.now() - this.lastData > 12000) {
    const status = this.state.bars.length ? 'delayed' : 'offline';
    if (this.state.status !== status) { this.state.status = status; this.emit(); }
    if (this.now() - this.lastSnapshot > 15000) void this.refresh();
   }
  }, 5000);
 }
 stop() {
  this.active = false; this.generation++;
  clearTimeout(this.retryTimer); clearTimeout(this.cacheTimer); clearInterval(this.heartbeat);
  this.retryTimer = this.cacheTimer = undefined;
  if (this.socket) { this.socket.onclose = this.socket.onmessage = this.socket.onopen = this.socket.onerror = null; this.socket.close(); this.socket = null; }
  for (const read of this.reads) read.abort(); this.reads.clear();
  this.syncing = false; this.duringSync = []; this.lastData = 0;
  this.state.loadingOlder = false; this.state.status = 'paused'; this.persist();
 }
 private connect() {
  if (!this.active || this.socket) return;
  const generation = this.generation;
  const retry = () => {
   if (!this.active || generation !== this.generation) return;
   this.socket = null;
   this.state.status = this.state.bars.length ? 'delayed' : 'offline'; this.emit();
   this.retryTimer = setTimeout(() => this.connect(), Math.min(15000, 1000 * 2 ** this.attempts++));
  };
  let socket: WebSocket;
  try { socket = this.options.socket(); } catch { retry(); return; }
  this.socket = socket; this.lastMessage = this.connectedAt = this.now();
  socket.onopen = () => {
   if (!this.active || generation !== this.generation || this.socket !== socket) return;
   socket.send(JSON.stringify({ op: 'subscribe', args: [{ channel: 'candle' + this.options.period, instId: this.options.symbol }] }));
   this.state.status = this.state.bars.length ? 'cached' : 'connecting'; this.emit();
   if (this.hasOpened) void this.refresh();
   this.hasOpened = true;
  };
  socket.onmessage = event => {
   if (!this.active || generation !== this.generation || this.socket !== socket) return;
   this.lastMessage = this.now();
   if (event.data === 'pong') return;
   try {
    const data = JSON.parse(String(event.data));
    if (data.event === 'error') { socket.close(); return; }
    if (data.arg?.instId !== this.options.symbol || data.arg?.channel !== 'candle' + this.options.period) return;
    const bars = parseCandles(data.data).filter(bar => bar.time <= this.now() / 1000 + 60);
    if (!bars.length) return;
    this.lastData = this.now(); this.attempts = 0;
    this.state.status = 'live';
    if (this.syncing) this.duringSync = mergeCandles(this.duringSync, bars, 300);
    const oldLast = this.state.bars.at(-1)?.time ?? 0;
    const merged = mergeCandles(this.state.bars, bars);
    const reset = bars.some(bar => bar.time < oldLast) || merged[0]?.time !== this.state.bars[0]?.time;
    this.changed(merged, reset);
   } catch { /* Malformed public data never replaces the chart. */ }
  };
  socket.onerror = () => socket.close();
  socket.onclose = retry;
 }
 async refresh() {
  if (!this.active || this.syncing) return;
  this.syncing = true; this.duringSync = []; this.lastSnapshot = this.now();
  const generation = this.generation, controller = new AbortController(); this.reads.add(controller);
  try {
   const fresh = await this.options.fetchPage(this.options.symbol, this.options.period, { signal: controller.signal });
   if (!this.active || generation !== this.generation) return;
   if (!fresh.length) throw Error('暂无K线数据');
   // A live tip may already have arrived; it cannot prove the old history is contiguous.
   const prefix = this.state.bars.filter(bar => bar.time < fresh[0].time);
   const gap = prefix.length && fresh[0].time - prefix.at(-1)!.time > PERIOD_SECONDS[this.options.period];
   const base = gap ? this.state.bars.filter(bar => bar.time >= fresh[0].time) : this.state.bars;
   const bars = mergeCandles(mergeCandles(base, fresh), this.duringSync);
   this.state.error = '';
   this.state.status = this.lastData && this.now() - this.lastData < 12000 ? 'live' : 'delayed';
   this.changed(bars, true);
  } catch {
   if (this.active && generation === this.generation) {
    this.state.error = this.state.bars.length ? '历史补齐失败，可重试' : 'K线暂时无法加载';
    this.state.status = this.state.bars.length ? 'delayed' : 'offline'; this.emit();
   }
  } finally {
   this.reads.delete(controller);
   if (generation === this.generation) { this.syncing = false; this.duringSync = []; }
  }
 }
 async loadOlder() {
  if (!this.active || this.state.loadingOlder || !this.state.canLoadOlder || !this.state.bars.length) return;
  const generation = this.generation, before = this.state.bars[0].time, controller = new AbortController();
  this.reads.add(controller); this.state.loadingOlder = true; this.emit();
  try {
   const older = (await this.options.fetchPage(this.options.symbol, this.options.period, { before, signal: controller.signal })).filter(bar => bar.time < before);
   if (!this.active || generation !== this.generation) return;
   const room = MAX_CANDLES - this.state.bars.length;
   this.state.canLoadOlder = older.length > 0 && older.length < room;
   this.state.error = '';
   this.changed(mergeCandles(older.slice(-room), this.state.bars), true);
  } catch {
   if (this.active && generation === this.generation) { this.state.error = '历史加载失败，点击重试'; this.emit(); }
  } finally {
   this.reads.delete(controller);
   if (this.active && generation === this.generation) { this.state.loadingOlder = false; this.emit(); }
  }
 }
}
