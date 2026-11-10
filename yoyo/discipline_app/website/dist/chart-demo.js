/*
 * Small offline canvas chart used by the public Spike website.
 * OHLC timestamps are UTC candle-open milliseconds. The educational trade
 * markers use the same source 5m candles but expose their close timestamps.
 */
(() => {
  'use strict';

  const PERIOD_MS = Object.freeze({ '5m': 300_000, '15m': 900_000, '1H': 3_600_000 });
  const FIVE_MINUTES_MS = PERIOD_MS['5m'];
  const VALID_PERIODS = Object.keys(PERIOD_MS);
  const VALID_PALETTES = ['workbench', 'classic'];
  const SCRIPT = document.currentScript || [...document.scripts].reverse().find(node => /(?:^|\/)chart-demo\.js(?:$|\?)/.test(node.src));
  const DATA_URL = new URL('./assets/market-btc-usdt-swap.json', SCRIPT?.src || location.href);
  const readyListeners = new Set();
  const errorListeners = new Set();
  let loaded = null;
  let loadFailure = null;

  function copyBar(bar, timestamp = bar.t) {
    return { t: timestamp, o: bar.o, h: bar.h, l: bar.l, c: bar.c };
  }

  function normalizePayload(payload) {
    if (!payload || typeof payload !== 'object' || !payload.metadata || !payload.bars) {
      throw new Error('行情样例结构异常');
    }
    const bars = {};
    for (const period of VALID_PERIODS) {
      const tuples = payload.bars[period];
      if (!Array.isArray(tuples) || tuples.length < 2) throw new Error(`缺少 ${period} 样例 K 线`);
      const step = PERIOD_MS[period];
      let previous = null;
      bars[period] = tuples.map(tuple => {
        if (!Array.isArray(tuple) || tuple.length < 6) throw new Error(`${period} K 线格式异常`);
        const [t, o, h, l, c, v] = tuple.map(Number);
        if (![t, o, h, l, c, v].every(Number.isFinite) || !Number.isInteger(t) || t % step !== 0) {
          throw new Error(`${period} K 线时间或数值异常`);
        }
        if (o <= 0 || h < Math.max(o, c) || l > Math.min(o, c) || l <= 0 || v < 0) {
          throw new Error(`${period} K 线 OHLC 边界异常`);
        }
        if (previous !== null && t - previous !== step) throw new Error(`${period} 样例包含缺失时间段`);
        previous = t;
        return { t, o, h, l, c, v };
      });
    }
    const metadata = payload.metadata;
    if (metadata.exchange !== 'OKX' || metadata.instrument !== 'BTC-USDT-SWAP' || metadata.timeZone !== 'UTC') {
      throw new Error('行情样例元信息与预期不符');
    }
    const raw = bars['5m'];
    const monday = metadata.monday;
    if (!monday || !Number.isFinite(monday.startMs) || !Number.isFinite(monday.endMs)) {
      throw new Error('缺少纽约周一范围信息');
    }
    const mondayBars = raw.filter(bar => bar.t >= monday.startMs && bar.t < monday.endMs);
    if (mondayBars.length !== 288 || !raw.some(bar => bar.t >= monday.endMs)) {
      throw new Error('纽约周一样例未完整收盘');
    }
    const high = Math.max(...mondayBars.map(bar => bar.h));
    const low = Math.min(...mondayBars.map(bar => bar.l));
    const upper = high + 0.618 * (high - low);
    const lower = low - 0.618 * (high - low);
    if (Math.abs(high - monday.high) > 1e-8 || Math.abs(low - monday.low) > 1e-8 ||
        Math.abs(upper - monday.upper1618) > 1e-7 || Math.abs(lower - monday.lower1618) > 1e-7) {
      throw new Error('纽约周一关键价位与 5m 样本不一致');
    }
    return { metadata, bars };
  }

  function loadData() {
    if (DATA_URL.origin !== location.origin) return Promise.reject(new Error('行情样例必须来自本站静态文件'));
    return fetch(DATA_URL.href, { credentials: 'omit', cache: 'force-cache', redirect: 'error' })
      .then(response => {
        if (!response.ok) throw new Error('离线行情样例暂时无法读取');
        return response.json();
      })
      .then(normalizePayload)
      .then(data => {
        loaded = data;
        for (const listener of [...readyListeners]) {
          try { listener(cloneMetadata(data.metadata)); } catch (error) { console.error(error); }
        }
        readyListeners.clear();
        return cloneMetadata(data.metadata);
      })
      .catch(error => {
        loadFailure = error;
        for (const listener of [...errorListeners]) {
          try { listener(error); } catch (listenerError) { console.error(listenerError); }
        }
        throw error;
      });
  }

  function cloneMetadata(metadata) {
    return JSON.parse(JSON.stringify(metadata));
  }

  const ready = loadData();
  ready.catch(() => {});

  function onReady(listener) {
    if (typeof listener !== 'function') throw new TypeError('onReady expects a function');
    let active = true;
    if (loaded) queueMicrotask(() => { if (active) listener(cloneMetadata(loaded.metadata)); });
    else if (loadFailure) queueMicrotask(() => { if (active) console.error('SpikeChart data is unavailable', loadFailure); });
    else readyListeners.add(listener);
    return () => { active = false; readyListeners.delete(listener); };
  }

  function onError(listener) {
    if (typeof listener !== 'function') throw new TypeError('onError expects a function');
    if (loadFailure) queueMicrotask(() => listener(loadFailure));
    else errorListeners.add(listener);
    return () => errorListeners.delete(listener);
  }

  function getExampleTrade() {
    if (!loaded) return null;
    const meta = loaded.metadata.exampleTrade;
    const raw = loaded.bars['5m'];
    const entry = raw.find(bar => bar.t === meta.entrySourceOpenMs);
    const exit = raw.find(bar => bar.t === meta.exitSourceOpenMs);
    if (!entry || !exit) return null;
    return {
      entry: copyBar(entry, meta.entryMs),
      exit: copyBar(exit, meta.exitMs),
    };
  }

  function fmtPrice(value) {
    return Number.isFinite(value) ? value.toLocaleString('en-US', { minimumFractionDigits: 0, maximumFractionDigits: value >= 100 ? 2 : 5 }) : '—';
  }

  function fmtTime(value, timeZone, options = {}) {
    const date = new Date(value);
    const parts = new Intl.DateTimeFormat('en-US', {
      timeZone,
      month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hourCycle: 'h23',
      ...(options.year ? { year: 'numeric' } : {}),
    }).formatToParts(date);
    const get = type => parts.find(part => part.type === type)?.value || '';
    const base = `${get('month')}/${get('day')} ${get('hour')}:${get('minute')}`;
    return options.year ? `${get('year')}-${base}` : base;
  }

  function niceStep(span, targetTicks) {
    const raw = Math.max(span / targetTicks, Number.EPSILON);
    const power = Math.pow(10, Math.floor(Math.log10(raw)));
    const normalized = raw / power;
    const multiple = normalized <= 1 ? 1 : normalized <= 2 ? 2 : normalized <= 2.5 ? 2.5 : normalized <= 5 ? 5 : 10;
    return multiple * power;
  }

  function ema(rows, period, step) {
    const output = new Array(rows.length).fill(null);
    const alpha = 2 / (period + 1);
    let value = null;
    let count = 0;
    let previous = null;
    for (let i = 0; i < rows.length; i += 1) {
      const bar = rows[i];
      if (previous !== null && bar.t - previous !== step) { value = null; count = 0; }
      value = value === null ? bar.c : alpha * bar.c + (1 - alpha) * value;
      count += 1;
      if (count >= period) output[i] = value;
      previous = bar.t;
    }
    return output;
  }

  class Chart {
    constructor(host, options) {
      if (!host || typeof host.appendChild !== 'function') throw new TypeError('SpikeChart.mount requires a DOM element');
      this.host = host;
      this.compact = Boolean(options.compact);
      this.onRead = typeof options.onRead === 'function' ? options.onRead : null;
      this.initialScene = ['touch', 'chart', 'review'].includes(options.scene) ? options.scene : 'chart';
      this.scene = this.initialScene;
      this.period = '15m';
      this.palette = 'workbench';
      this.fullFib = this.initialScene === 'touch';
      this.selected = null;
      this.alive = true;
      this.rows = null;
      this.view = { from: 0, to: 0 };
      this.layout = null;
      this.frame = 0;
      this.originalPosition = host.style.position;
      this.originalOverflow = host.style.overflow;
      this.originalMinHeight = host.style.minHeight;
      if (getComputedStyle(host).position === 'static') host.style.position = 'relative';
      host.style.overflow = 'hidden';
      if (host.clientHeight < 80) host.style.minHeight = this.compact ? '220px' : '360px';

      this.canvas = document.createElement('canvas');
      this.canvas.setAttribute('role', 'img');
      this.canvas.setAttribute('aria-label', 'BTC-USDT-SWAP 历史 K 线图，可点选蜡烛读取 OHLC');
      this.canvas.tabIndex = 0;
      Object.assign(this.canvas.style, {
        position: 'absolute', inset: '0', width: '100%', height: '100%', display: 'block',
        touchAction: 'pan-y', cursor: 'crosshair', zIndex: '1', outline: 'none',
      });
      this.host.appendChild(this.canvas);
      this.ctx = this.canvas.getContext('2d', { alpha: false });

      this.tooltip = document.createElement('div');
      Object.assign(this.tooltip.style, {
        position: 'absolute', zIndex: '2', top: '8px', left: '9px', maxWidth: 'calc(100% - 18px)',
        padding: '5px 7px', borderRadius: '6px', border: '1px solid rgba(255,255,255,.12)',
        background: 'rgba(4,5,8,.90)', color: '#cbd0d9', font: '10px/1.5 ui-monospace,SFMono-Regular,Menlo,monospace',
        pointerEvents: 'none', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis', display: 'none',
      });
      this.host.appendChild(this.tooltip);
      this.live = document.createElement('span');
      this.live.setAttribute('aria-live', 'polite');
      this.live.setAttribute('aria-atomic', 'true');
      Object.assign(this.live.style, {
        position: 'absolute', width: '1px', height: '1px', padding: '0', margin: '-1px',
        overflow: 'hidden', clip: 'rect(0,0,0,0)', whiteSpace: 'nowrap', border: '0',
      });
      this.host.appendChild(this.live);
      this.status = document.createElement('div');
      Object.assign(this.status.style, {
        position: 'absolute', inset: '0', zIndex: '3', display: 'grid', placeItems: 'center',
        background: '#050508', color: '#9aa1ae', font: '12px/1.5 -apple-system,BlinkMacSystemFont,sans-serif',
        textAlign: 'center', padding: '16px', pointerEvents: 'none',
      });
      this.status.textContent = '正在读取本地历史行情…';
      this.host.appendChild(this.status);

      this.onPointerDown = event => this._pointerDown(event);
      this.onPointerMove = event => this._pointerMove(event);
      this.onPointerUp = () => { this.pointerId = null; };
      this.onPointerLeave = event => { if (event.pointerType === 'mouse') this._clearSelection(); };
      this.onKeyDown = event => this._key(event);
      this.canvas.addEventListener('pointerdown', this.onPointerDown);
      this.canvas.addEventListener('pointermove', this.onPointerMove);
      this.canvas.addEventListener('pointerup', this.onPointerUp);
      this.canvas.addEventListener('pointercancel', this.onPointerUp);
      this.canvas.addEventListener('pointerleave', this.onPointerLeave);
      this.canvas.addEventListener('keydown', this.onKeyDown);

      this.resizeObserver = typeof ResizeObserver === 'function' ? new ResizeObserver(() => this._scheduleDraw()) : null;
      this.resizeObserver?.observe(host);
      this.onWindowResize = () => this._scheduleDraw();
      window.addEventListener('resize', this.onWindowResize, { passive: true });
      if (loaded) this._attachData();
      else {
        this.unsubscribeReady = onReady(() => this._attachData());
        this.unsubscribeError = onError(error => this._showError(error));
      }
      this._scheduleDraw();
    }

    _attachData() {
      if (!this.alive || !loaded) return;
      this.rows = loaded.bars[this.period];
      this.ema20 = ema(this.rows, 20, PERIOD_MS[this.period]);
      this.ema50 = ema(this.rows, 50, PERIOD_MS[this.period]);
      this.status.style.display = 'none';
      this._setFocusWindow();
      const latest = this.rows[Math.max(0, this.view.to - 1)];
      if (latest && this.onRead) this._emit(latest);
      this._scheduleDraw();
    }

    _showError(error) {
      if (!this.alive) return;
      this.status.style.display = 'grid';
      this.status.textContent = `本地行情暂时无法读取：${error?.message || '请刷新页面重试'}`;
    }

    _setFocusWindow() {
      if (!this.rows?.length) return;
      const step = PERIOD_MS[this.period];
      const focusEnd = Number(loaded.metadata.focusEndMs);
      let to = this.rows.findIndex(bar => bar.t >= focusEnd);
      if (to < 0) to = this.rows.length;
      to = Math.max(1, Math.min(to, this.rows.length));
      const width = this.host.clientWidth;
      const height = this.host.clientHeight;
      const inPhone = this.compact && height > 0 && height < 400;
      const desired = inPhone ? 27 : (this.compact || width < 520) ? 40 : 88;
      this.view = { from: Math.max(0, to - desired), to };
      if (this.scene === 'review') this._setReviewWindow();
      this.selected = null;
      this.tooltip.style.display = 'none';
      this._scheduleDraw();
    }

    _setReviewWindow() {
      if (!this.rows?.length) return;
      const trade = loaded.metadata.exampleTrade;
      const step = PERIOD_MS[this.period];
      const entryBucket = Math.floor(trade.entrySourceOpenMs / step) * step;
      const exitBucket = Math.floor(trade.exitSourceOpenMs / step) * step;
      const entryIndex = this.rows.findIndex(bar => bar.t === entryBucket);
      const exitIndex = this.rows.findIndex(bar => bar.t === exitBucket);
      if (entryIndex < 0 || exitIndex < 0) return;
      const padding = 2;
      this.view = {
        from: Math.max(0, entryIndex - padding),
        to: Math.min(this.rows.length, exitIndex + padding + 1),
      };
    }

    _emit(bar) {
      const copy = copyBar(bar);
      this.live.textContent = `${fmtTime(bar.t, loaded.metadata.displayTimeZone, { year: true })} NY，开 ${fmtPrice(bar.o)}，高 ${fmtPrice(bar.h)}，低 ${fmtPrice(bar.l)}，收 ${fmtPrice(bar.c)}`;
      try { this.onRead?.(copy); } catch (error) { console.error('SpikeChart onRead failed', error); }
    }

    _pointerDown(event) {
      if (event.pointerType === 'mouse' && event.button !== 0) return;
      this.pointerId = event.pointerId;
      try { this.canvas.setPointerCapture(event.pointerId); } catch { /* Pointer capture is optional. */ }
      this._selectAt(event.clientX, event.clientY);
    }

    _pointerMove(event) {
      if (event.pointerType === 'mouse' || this.pointerId === event.pointerId) {
        this._selectAt(event.clientX, event.clientY);
      }
    }

    _pointerUp() { this.pointerId = null; }

    _selectAt(clientX, clientY) {
      if (!this.layout || !this.rows) return;
      const rect = this.canvas.getBoundingClientRect();
      const x = (clientX - rect.left) * (this.host.clientWidth / Math.max(1, rect.width));
      const y = (clientY - rect.top) * (this.host.clientHeight / Math.max(1, rect.height));
      const { plotLeft, plotRight, plotTop, plotBottom, spacing, from, to } = this.layout;
      if (x < plotLeft || x > plotRight || y < plotTop || y > plotBottom) return;
      const index = Math.min(to - 1, from + Math.floor((x - plotLeft) / spacing));
      if (index === this.selected) return;
      this.selected = index;
      this._emit(this.rows[index]);
      this._scheduleDraw();
    }

    _clearSelection() {
      if (this.selected === null) return;
      this.selected = null;
      this.tooltip.style.display = 'none';
      this._scheduleDraw();
    }

    _key(event) {
      if (!this.rows || !['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
      event.preventDefault();
      if (event.key === 'Home') this.selected = this.view.from;
      else if (event.key === 'End') this.selected = this.view.to - 1;
      else {
        const start = this.selected === null ? this.view.from : this.selected;
        this.selected = Math.max(this.view.from, Math.min(this.view.to - 1, start + (event.key === 'ArrowLeft' ? -1 : 1)));
      }
      const bar = this.rows[this.selected];
      if (bar) { this._emit(bar); this._scheduleDraw(); }
    }

    _scheduleDraw() {
      if (!this.alive || this.frame) return;
      this.frame = requestAnimationFrame(() => { this.frame = 0; this._draw(); });
    }

    _draw() {
      if (!this.alive || !this.ctx) return;
      // The phone mockup is perspective transformed; keep the drawing buffer in
      // its untransformed layout size rather than the projected bounding box.
      const width = this.host.clientWidth;
      const height = this.host.clientHeight;
      if (width < 2 || height < 80) return;
      const dpr = Math.max(1, Math.min(3, window.devicePixelRatio || 1));
      const pixelWidth = Math.round(width * dpr);
      const pixelHeight = Math.round(height * dpr);
      if (this.canvas.width !== pixelWidth || this.canvas.height !== pixelHeight) {
        this.canvas.width = pixelWidth;
        this.canvas.height = pixelHeight;
      }
      const ctx = this.ctx;
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.clearRect(0, 0, width, height);
      ctx.fillStyle = '#040407';
      ctx.fillRect(0, 0, width, height);
      if (!this.rows) return;

      const rows = this.rows;
      const { from, to } = this.view;
      const visible = rows.slice(from, to);
      if (!visible.length) return;
      const step = PERIOD_MS[this.period];
      const compact = this.compact || width < 520;
      const left = compact ? 10 : 14;
      const axis = compact ? 66 : 78;
      const top = compact ? 18 : 20;
      const bottom = compact ? 28 : 31;
      const plotLeft = left;
      const plotRight = Math.max(plotLeft + 40, width - axis);
      const plotTop = top;
      const plotBottom = Math.max(plotTop + 50, height - bottom);
      const volumeHeight = Math.min(compact ? 30 : 42, Math.max(20, (plotBottom - plotTop) * 0.16));
      const mainBottom = plotBottom - volumeHeight - 6;
      const mainHeight = mainBottom - plotTop;
      const plotWidth = plotRight - plotLeft;
      const spacing = plotWidth / visible.length;
      const monday = loaded.metadata.monday;
      const visibleEnd = visible[visible.length - 1].t + step;
      const rangeReady = visibleEnd > monday.endMs;
      const fitLevels = rangeReady && this.fullFib;
      const trade = this.scene === 'review' ? getExampleTrade() : null;
      const tradeMarks = trade ? [
        { bar: trade.entry, openMs: loaded.metadata.exampleTrade.entrySourceOpenMs, label: '示意进场', color: '#67c8ff' },
        { bar: trade.exit, openMs: loaded.metadata.exampleTrade.exitSourceOpenMs, label: '示意离场', color: '#f3bf63' },
      ] : [];

      let min = Math.min(...visible.map(bar => bar.l));
      let max = Math.max(...visible.map(bar => bar.h));
      if (fitLevels) {
        min = Math.min(min, monday.low, monday.lower1618);
        max = Math.max(max, monday.high, monday.upper1618);
      }
      const rawSpan = Math.max(max - min, Math.abs(max) * 0.0005, 0.01);
      const padding = rawSpan * 0.065;
      min -= padding;
      max += padding;
      const priceSpan = max - min;
      const yPrice = value => mainBottom - ((value - min) / priceSpan) * mainHeight;
      const xBoundary = time => plotLeft + ((time - visible[0].t) / step) * spacing;
      const xCenter = index => plotLeft + (index - from + 0.5) * spacing;
      const tickStep = niceStep(priceSpan, compact ? 4 : 5);

      // Soft New York Monday session shading; reference prices stay hidden until its close.
      const bandLeft = Math.max(plotLeft, xBoundary(monday.startMs));
      const bandRight = Math.min(plotRight, xBoundary(monday.endMs));
      if (bandRight > bandLeft) {
        ctx.fillStyle = 'rgba(48,209,88,.045)';
        ctx.fillRect(bandLeft, plotTop, bandRight - bandLeft, plotBottom - plotTop);
      }

      // EMA20/50 causal cloud, painted beneath the candles with subdued native-inspired colours.
      ctx.save();
      ctx.beginPath();
      ctx.rect(plotLeft, plotTop, plotWidth, mainHeight);
      ctx.clip();
      for (let i = from; i < to - 1; i += 1) {
        const fast0 = this.ema20[i], fast1 = this.ema20[i + 1];
        const slow0 = this.ema50[i], slow1 = this.ema50[i + 1];
        if (![fast0, fast1, slow0, slow1].every(Number.isFinite)) continue;
        const x0 = xCenter(i), x1 = xCenter(i + 1);
        ctx.beginPath();
        ctx.moveTo(x0, yPrice(fast0));
        ctx.lineTo(x1, yPrice(fast1));
        ctx.lineTo(x1, yPrice(slow1));
        ctx.lineTo(x0, yPrice(slow0));
        ctx.closePath();
        ctx.fillStyle = fast0 >= slow0 && fast1 >= slow1 ? 'rgba(48,209,158,.105)' : 'rgba(255,99,125,.085)';
        ctx.fill();
      }
      ctx.lineWidth = 0.85;
      for (const [values, color] of [[this.ema20, 'rgba(115,220,185,.36)'], [this.ema50, 'rgba(234,130,151,.30)']]) {
        ctx.beginPath();
        let started = false;
        for (let i = from; i < to; i += 1) {
          if (!Number.isFinite(values[i])) { started = false; continue; }
          if (!started) { ctx.moveTo(xCenter(i), yPrice(values[i])); started = true; }
          else ctx.lineTo(xCenter(i), yPrice(values[i]));
        }
        ctx.strokeStyle = color;
        ctx.stroke();
      }
      ctx.restore();

      // Thin grid and a dedicated right price axis keep numbers off the candle field.
      ctx.save();
      ctx.font = compact ? '10px ui-monospace,SFMono-Regular,Menlo,monospace' : '11px ui-monospace,SFMono-Regular,Menlo,monospace';
      ctx.textBaseline = 'middle';
      ctx.textAlign = 'left';
      ctx.lineWidth = 1;
      ctx.strokeStyle = 'rgba(255,255,255,.065)';
      const firstTick = Math.ceil(min / tickStep) * tickStep;
      for (let value = firstTick, guard = 0; value <= max + tickStep * 0.001 && guard < 20; value += tickStep, guard += 1) {
        const y = yPrice(value);
        ctx.beginPath(); ctx.moveTo(plotLeft, y); ctx.lineTo(plotRight, y); ctx.stroke();
        ctx.fillStyle = 'rgba(222,228,237,.82)';
        ctx.fillText(fmtPrice(value), plotRight + 7, y);
      }
      ctx.strokeStyle = 'rgba(255,255,255,.035)';
      ctx.fillStyle = 'rgba(211,219,230,.72)';
      // Phone charts keep only the two date-bearing endpoints; four groups of
      // date/time labels collide at 320–390px and add no useful precision.
      const verticalTicks = compact ? 1 : 4;
      ctx.textBaseline = 'top';
      for (let j = 0; j <= verticalTicks; j += 1) {
        const x = plotLeft + plotWidth * j / verticalTicks;
        ctx.beginPath(); ctx.moveTo(x, plotTop); ctx.lineTo(x, plotBottom); ctx.stroke();
        const index = Math.min(to - 1, from + Math.round((visible.length - 1) * j / verticalTicks));
        const label = fmtTime(rows[index].t, loaded.metadata.displayTimeZone);
        if (j === 0) ctx.textAlign = 'left';
        else if (j === verticalTicks) ctx.textAlign = 'right';
        else ctx.textAlign = 'center';
        ctx.fillText(label, x, plotBottom + 7);
      }
      ctx.restore();

      // Relative volume strip from the same OKX candles; the source unit is not relabeled as BTC.
      const maxVolume = Math.max(1, ...visible.map(bar => bar.v));
      for (let i = from; i < to; i += 1) {
        const bar = rows[i];
        const index = i - from;
        const cx = xCenter(i);
        const barWidth = Math.max(1, Math.min(12, spacing * 0.68));
        const h = Math.max(1, (bar.v / maxVolume) * Math.max(6, volumeHeight - 2));
        const isMonday = bar.t >= monday.startMs && bar.t < monday.endMs;
        const up = bar.c >= bar.o;
        let color = this.palette === 'classic' ? (up ? '#169873' : '#D7355D') : '#8B9099';
        if (isMonday && this.palette === 'workbench') color = '#30D158';
        ctx.fillStyle = color;
        ctx.globalAlpha = 0.28;
        ctx.fillRect(cx - barWidth / 2, plotBottom - h, barWidth, h);
        ctx.globalAlpha = 1;
      }

      // Monday reference levels are unavailable until a closed post-Monday candle is in view.
      if (rangeReady) {
        const candidates = [
          { value: monday.upper1618, label: '↑ 1.618', color: '#E6BD5E', start: monday.endMs, dash: [4, 3], strong: true },
          { value: monday.high, label: '周一高', color: '#D5AF55', start: monday.endMs, dash: [2, 3] },
          { value: monday.low, label: '周一低', color: '#69BDE6', start: monday.endMs, dash: [2, 3] },
          { value: monday.lower1618, label: '↓ 1.618', color: '#69BDE6', start: monday.endMs, dash: [4, 3], strong: true },
        ].filter(level => level.value >= min && level.value <= max && xBoundary(level.start) < plotRight);
        const placements = candidates.map(level => ({ ...level, y: yPrice(level.value) })).sort((a, b) => a.y - b.y);
        let priorY = -Infinity;
        for (const level of placements) {
          const labelY = Math.max(plotTop + 8, Math.min(mainBottom - 8, Math.max(level.y, priorY + 14)));
          priorY = labelY;
          ctx.save();
          ctx.strokeStyle = level.color;
          ctx.globalAlpha = level.strong ? 0.76 : 0.44;
          ctx.lineWidth = level.strong ? 1.1 : 0.8;
          ctx.setLineDash(level.dash);
          ctx.beginPath();
          ctx.moveTo(Math.max(plotLeft, xBoundary(level.start)), level.y);
          ctx.lineTo(plotRight, level.y);
          ctx.stroke();
          ctx.restore();
          const shortPrice = level.value >= 10_000 ? `${(level.value / 1000).toFixed(1)}k` : fmtPrice(level.value);
          const label = `${level.label} ${shortPrice}`;
          ctx.font = compact ? '7px ui-monospace,SFMono-Regular,Menlo,monospace' : '8px ui-monospace,SFMono-Regular,Menlo,monospace';
          const labelWidth = Math.min(axis - 5, ctx.measureText(label).width + 8);
          const labelX = Math.max(plotRight + 3, width - axis + 3);
          ctx.fillStyle = 'rgba(4,5,8,.94)';
          ctx.fillRect(labelX, labelY - 7, labelWidth, 14);
          ctx.strokeStyle = `${level.color}66`;
          ctx.strokeRect(labelX, labelY - 7, labelWidth, 14);
          ctx.fillStyle = level.color;
          ctx.textAlign = 'left'; ctx.textBaseline = 'middle';
          ctx.fillText(label, labelX + 4, labelY, Math.max(10, labelWidth - 8));
        }
      }

      // Candles are painted last among the static layers; wicks and bodies remain distinct at phone width.
      ctx.save();
      ctx.beginPath(); ctx.rect(plotLeft, plotTop, plotWidth, mainHeight); ctx.clip();
      for (let i = from; i < to; i += 1) {
        const bar = rows[i];
        const cx = xCenter(i);
        const candleWidth = Math.max(1.15, Math.min(12, spacing * 0.64));
        const isMonday = bar.t >= monday.startMs && bar.t < monday.endMs;
        const up = bar.c >= bar.o;
        let color = this.palette === 'classic' ? (up ? '#169873' : '#D7355D') : '#8B9099';
        if (isMonday && this.palette === 'workbench') color = '#30D158';
        const wickColor = color;
        const yHigh = yPrice(bar.h), yLow = yPrice(bar.l);
        const yOpen = yPrice(bar.o), yClose = yPrice(bar.c);
        ctx.strokeStyle = wickColor;
        ctx.globalAlpha = this.palette === 'workbench' && !isMonday ? 0.86 : 0.96;
        ctx.lineWidth = spacing < 3 ? 0.75 : 1;
        ctx.beginPath(); ctx.moveTo(cx, yHigh); ctx.lineTo(cx, yLow); ctx.stroke();
        const topBody = Math.min(yOpen, yClose);
        const bodyHeight = Math.max(1.35, Math.abs(yClose - yOpen));
        ctx.globalAlpha = this.palette === 'workbench' && !isMonday && !up ? 0.38 : 0.88;
        ctx.fillStyle = color;
        ctx.fillRect(cx - candleWidth / 2, topBody, candleWidth, bodyHeight);
        ctx.globalAlpha = this.palette === 'workbench' && !isMonday ? 0.95 : 1;
        ctx.strokeStyle = color;
        ctx.lineWidth = 0.8;
        ctx.strokeRect(cx - candleWidth / 2, topBody, candleWidth, bodyHeight);
        ctx.globalAlpha = 1;
      }
      ctx.restore();

      // The review markers remain attached to the containing aggregate candle and use the fixed raw 5m close.
      const markerIndices = [];
      for (const marker of tradeMarks) {
        const bucket = Math.floor(marker.openMs / step) * step;
        const index = rows.findIndex(bar => bar.t === bucket);
        if (index < from || index >= to) continue;
        markerIndices.push({ ...marker, index });
      }
      for (const marker of markerIndices) {
        const x = xCenter(marker.index);
        const y = yPrice(marker.bar.c);
        ctx.save();
        ctx.setLineDash([2, 3]); ctx.strokeStyle = `${marker.color}88`; ctx.lineWidth = 1;
        ctx.beginPath(); ctx.moveTo(x, plotTop); ctx.lineTo(x, plotBottom); ctx.stroke();
        ctx.setLineDash([]); ctx.fillStyle = marker.color;
        ctx.beginPath(); ctx.arc(x, y, compact ? 3 : 4, 0, Math.PI * 2); ctx.fill();
        const label = marker.label;
        ctx.font = compact ? '8px -apple-system,BlinkMacSystemFont,sans-serif' : '9px -apple-system,BlinkMacSystemFont,sans-serif';
        const widthLabel = ctx.measureText(label).width + 12;
        const labelX = Math.max(plotLeft + 2, Math.min(plotRight - widthLabel - 2, x + 6));
        const labelY = Math.max(plotTop + 10, Math.min(mainBottom - 9, y - 13));
        ctx.fillStyle = 'rgba(4,5,8,.94)'; ctx.fillRect(labelX, labelY - 8, widthLabel, 16);
        ctx.strokeStyle = `${marker.color}88`; ctx.strokeRect(labelX, labelY - 8, widthLabel, 16);
        ctx.fillStyle = marker.color; ctx.textAlign = 'left'; ctx.textBaseline = 'middle'; ctx.fillText(label, labelX + 6, labelY);
        ctx.restore();
      }

      // Crosshair and selected OHLC overlay; time axes and price labels remain in their own gutters.
      if (this.selected !== null && this.selected >= from && this.selected < to) {
        const bar = rows[this.selected];
        const x = xCenter(this.selected);
        const y = yPrice(bar.c);
        ctx.save();
        ctx.strokeStyle = 'rgba(226,232,240,.5)'; ctx.lineWidth = 0.8; ctx.setLineDash([3, 3]);
        ctx.beginPath(); ctx.moveTo(x, plotTop); ctx.lineTo(x, plotBottom); ctx.stroke();
        ctx.beginPath(); ctx.moveTo(plotLeft, y); ctx.lineTo(plotRight, y); ctx.stroke();
        ctx.setLineDash([]); ctx.fillStyle = '#dfe5ed'; ctx.beginPath(); ctx.arc(x, y, 2.2, 0, Math.PI * 2); ctx.fill();
        ctx.restore();
        this.tooltip.textContent = `${fmtTime(bar.t, loaded.metadata.displayTimeZone, { year: true })} NY  O ${fmtPrice(bar.o)}  H ${fmtPrice(bar.h)}  L ${fmtPrice(bar.l)}  C ${fmtPrice(bar.c)}`;
        this.tooltip.style.display = 'block';
      } else if (this.scene !== 'review') {
        this.tooltip.style.display = 'none';
      }

      if (this.scene === 'review') {
        const note = this.compact ? '教学示意 · 非实际执行' : '教学示意位置 · 非实际执行';
        ctx.font = compact ? '8px -apple-system,BlinkMacSystemFont,sans-serif' : '9px -apple-system,BlinkMacSystemFont,sans-serif';
        const noteWidth = ctx.measureText(note).width + 12;
        const noteX = Math.max(plotLeft + 2, plotRight - noteWidth);
        ctx.fillStyle = 'rgba(226,184,96,.10)'; ctx.fillRect(noteX, 3, noteWidth, 13);
        ctx.strokeStyle = 'rgba(226,184,96,.32)'; ctx.strokeRect(noteX, 3, noteWidth, 13);
        ctx.fillStyle = 'rgba(242,200,112,.88)'; ctx.textAlign = 'center'; ctx.textBaseline = 'middle'; ctx.fillText(note, noteX + noteWidth / 2, 9.5);
      }

      this.layout = { plotLeft, plotRight, plotTop, plotBottom, spacing, from, to };
    }

    setPeriod(period) {
      if (!VALID_PERIODS.includes(period)) throw new RangeError(`Unsupported chart period: ${period}`);
      if (period === this.period) return this;
      this.period = period;
      if (loaded) this._attachData();
      return this;
    }

    setPalette(palette) {
      if (!VALID_PALETTES.includes(palette)) throw new RangeError(`Unsupported candle palette: ${palette}`);
      this.palette = palette;
      this._scheduleDraw();
      return this;
    }

    setScene(scene) {
      if (!['touch', 'chart', 'review'].includes(scene)) throw new RangeError(`Unsupported chart scene: ${scene}`);
      this.scene = scene;
      this.fullFib = scene === 'touch';
      if (this.rows) {
        if (scene === 'review') this._setReviewWindow();
        else this._setFocusWindow();
      }
      this.selected = null;
      this._scheduleDraw();
      return this;
    }

    reset() {
      this.period = '15m';
      this.palette = 'workbench';
      this.scene = this.initialScene;
      this.fullFib = this.initialScene === 'touch';
      this.selected = null;
      if (loaded) this._attachData();
      return this;
    }

    getExampleTrade() { return getExampleTrade(); }

    setFullFib(enabled) {
      this.fullFib = Boolean(enabled);
      this._scheduleDraw();
      return this;
    }

    destroy() {
      if (!this.alive) return;
      this.alive = false;
      if (this.frame) cancelAnimationFrame(this.frame);
      this.resizeObserver?.disconnect();
      this.unsubscribeReady?.(); this.unsubscribeError?.();
      window.removeEventListener('resize', this.onWindowResize);
      this.canvas.removeEventListener('pointerdown', this.onPointerDown);
      this.canvas.removeEventListener('pointermove', this.onPointerMove);
      this.canvas.removeEventListener('pointerup', this.onPointerUp);
      this.canvas.removeEventListener('pointercancel', this.onPointerUp);
      this.canvas.removeEventListener('pointerleave', this.onPointerLeave);
      this.canvas.removeEventListener('keydown', this.onKeyDown);
      this.canvas.remove(); this.tooltip.remove(); this.live.remove(); this.status.remove();
      this.host.style.position = this.originalPosition;
      this.host.style.overflow = this.originalOverflow;
      this.host.style.minHeight = this.originalMinHeight;
    }
  }

  const api = {
    ready,
    onReady,
    onError,
    mount(host, options = {}) { return new Chart(host, options); },
    getExampleTrade,
  };
  Object.defineProperty(api, 'metadata', { enumerable: true, get: () => loaded ? cloneMetadata(loaded.metadata) : null });
  window.SpikeChart = api;
})();
