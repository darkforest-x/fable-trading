/* SPIKE V1 OKX review viewer. Uses only local frozen JSON and Lightweight Charts v4.2.0. */
(() => {
  "use strict";
  const DATA_ROOT = "data/";
  const NOTES_KEY = "spike-v1-okx-133-review-notes-v1";
  const $ = (id) => document.getElementById(id);
  const state = { manifest: null, records: [], visible: [], selected: null, charts: [], theme: localStorage.getItem("spike-review-theme") || "dark", syncing: false };
  const formatters = {
    datetime: new Intl.DateTimeFormat("zh-CN", { timeZone: "Asia/Shanghai", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false }),
    date: new Intl.DateTimeFormat("zh-CN", { timeZone: "Asia/Shanghai", year: "numeric", month: "2-digit", day: "2-digit" }),
  };
  const presentNumber = (value) => value !== null && value !== undefined && value !== "" && Number.isFinite(Number(value));
  const fmtTime = (ms) => presentNumber(ms) ? `${formatters.datetime.format(new Date(Number(ms)))} BJT` : "—";
  const fmtPrice = (price) => presentNumber(price) ? Number(price).toLocaleString("en-US", { maximumFractionDigits: 10 }) : "—";
  const seconds = (ms) => Math.floor(Number(ms) / 1000);
  const readNotes = () => { try { return JSON.parse(localStorage.getItem(NOTES_KEY) || "{}"); } catch { return {}; } };
  const writeNotes = (notes) => localStorage.setItem(NOTES_KEY, JSON.stringify(notes));
  const recordId = (record) => String(record.id || record.event_id || `${record.sequence}:${record.symbol}:${record.signal?.time_ms || record.signal_time_ms}`);
  const signal = (record) => record.signal || record;
  const chartPath = (record) => {
    if (typeof record.chart_path !== "string" || !record.chart_path.startsWith("charts/")) throw new Error("记录缺少受控图表路径");
    return record.chart_path;
  };
  const tvUrl = (record) => record.tradingview_url || record.tv_url || "https://www.tradingview.com/chart/";
  const canonicalTimeframe = (value) => ({ 30: "30m", 60: "1H", 240: "4H", "30": "30m", "60": "1H", "240": "4H" }[value] || value || "—");
  const unavailable = (record) => record?.status === "missing" || record?.state?.status === "missing";
  const candleSeries = (chart) => chart.addCandlestickSeries({ upColor: "#3fbf8f", downColor: "#e6636d", wickUpColor: "#62d7ab", wickDownColor: "#f18890", borderVisible: false, priceLineVisible: false });
  const line = (chart, color, lineWidth = 1) => chart.addLineSeries({ color, lineWidth, crosshairMarkerVisible: false, lastValueVisible: true, priceLineVisible: false });
  function chartOptions(container, showTime = false) {
    const dark = state.theme !== "light";
    return { width: container.clientWidth, height: container.clientHeight, layout: { background: { color: dark ? "#111a28" : "#ffffff" }, textColor: dark ? "#dce7f5" : "#29374a" }, grid: { vertLines: { color: dark ? "#1b2a40" : "#e5ebf4" }, horzLines: { color: dark ? "#1b2a40" : "#e5ebf4" } }, rightPriceScale: { borderColor: dark ? "#33445e" : "#cad5e4" }, timeScale: { borderColor: dark ? "#33445e" : "#cad5e4", timeVisible: showTime, secondsVisible: false, tickMarkFormatter: (time) => formatters.datetime.format(new Date(Number(time) * 1000)) }, localization: { timeFormatter: (time) => formatters.datetime.format(new Date(Number(time) * 1000)) }, crosshair: { mode: LightweightCharts.CrosshairMode.Normal } };
  }
  function destroyCharts() { state.charts.forEach(({ chart, observer, cleanup }) => { observer?.disconnect(); cleanup?.(); chart.remove(); }); state.charts = []; }
  function watchChart(chart, container, cleanup) { const observer = new ResizeObserver(() => chart.resize(container.clientWidth, container.clientHeight)); observer.observe(container); state.charts.push({ chart, observer, cleanup }); }
  function syncCharts(charts) {
    charts.forEach((chart) => chart.timeScale().subscribeVisibleLogicalRangeChange((range) => {
      if (!range || state.syncing) return;
      state.syncing = true;
      charts.filter((other) => other !== chart).forEach((other) => other.timeScale().setVisibleLogicalRange(range));
      state.syncing = false;
    }));
  }
  function marker(time, position, color, shape, text) { return { time: seconds(time), position, color, shape, text }; }
  function signalGuide(chart, container, signalMs) {
    if (!presentNumber(signalMs)) return undefined;
    const guide = document.createElement("div"); guide.className = "signal-guide"; guide.setAttribute("aria-hidden", "true"); container.appendChild(guide);
    const position = () => { const x = chart.timeScale().timeToCoordinate(seconds(signalMs)); guide.hidden = x === null; if (x !== null) guide.style.transform = `translateX(${x}px)`; };
    chart.timeScale().subscribeVisibleLogicalRangeChange(position); requestAnimationFrame(position);
    return () => guide.remove();
  }
  function initialStopGuide(chart, series, container, startMs, stop, endMs) {
    if (![startMs, stop].every(presentNumber)) return undefined;
    const line = document.createElement("div"), label = document.createElement("span");
    line.className = "initial-stop-guide"; label.textContent = `初始 SL ${fmtPrice(stop)}`; line.appendChild(label); container.appendChild(line);
    const position = () => {
      const left = chart.timeScale().timeToCoordinate(seconds(startMs));
      const end = presentNumber(endMs) ? chart.timeScale().timeToCoordinate(seconds(endMs)) : null;
      const y = series.priceToCoordinate(Number(stop));
      if (left === null || y === null || (end !== null && end <= left)) { line.hidden = true; return; }
      const right = end === null ? container.clientWidth - 54 : end;
      if (right <= left) { line.hidden = true; return; }
      line.hidden = false; line.style.left = `${left}px`; line.style.top = `${y}px`; line.style.width = `${right - left}px`;
    };
    chart.timeScale().subscribeVisibleLogicalRangeChange(position); chart.subscribeCrosshairMove(position); requestAnimationFrame(position);
    return () => line.remove();
  }
  function fact(label, value) { return `<div class="fact"><span>${label}</span><strong>${value}</strong></div>`; }
  function setTheme(theme) { state.theme = theme; document.documentElement.dataset.theme = theme; localStorage.setItem("spike-review-theme", theme); $("theme-toggle").textContent = theme === "dark" ? "浅色" : "深色"; }
  function filtered() {
    const query = $("search").value.trim().normalize("NFKC").toLowerCase();
    const timeframe = $("timeframe").value;
    state.visible = state.records.filter((record) => {
      const haystack = [record.sequence, record.symbol, record.id, recordId(record), signal(record).time_ms || signal(record).bar_close_ms].join(" ").normalize("NFKC").toLowerCase();
      return (timeframe === "all" || canonicalTimeframe(record.timeframe || record.timeframe_min) === timeframe) && (!query || haystack.includes(query));
    });
    renderList();
    if (!state.visible.some((record) => recordId(record) === recordId(state.selected || {}))) selectRecord(state.visible[0] || null);
  }
  function renderList() {
    const available = state.records.filter((record) => !unavailable(record)).length;
    $("record-count").textContent = `显示 ${state.visible.length} / ${state.records.length} 笔 · 可绘制 ${available}`;
    $("record-list").innerHTML = state.visible.map((record) => {
      const active = recordId(record) === recordId(state.selected || {});
      const s = signal(record);
      const missing = unavailable(record);
      return `<button class="record${active ? " active" : ""}" data-record="${recordId(record)}"><span class="badge">#${record.sequence}</span><span><strong>${record.symbol || "—"}</strong><small>${canonicalTimeframe(record.timeframe || record.timeframe_min)} · ${missing ? "证据时间线无效" : fmtTime(record.signal_close_ms ?? s.time_ms ?? s.bar_close_ms)}</small></span><span>${missing ? "!" : "›"}</span></button>`;
    }).join("");
    document.querySelectorAll("[data-record]").forEach((node) => node.addEventListener("click", () => selectRecord(state.records.find((record) => recordId(record) === node.dataset.record))));
  }
  function notesFor(record) { return readNotes()[recordId(record)] || ""; }
  function setNotes(record) { $("notes").value = record ? notesFor(record) : ""; $("notes-status").textContent = record ? "仅保存于此浏览器。" : ""; }
  function renderFacts(record) {
    if (!record) { $("facts").innerHTML = ""; return; }
    const s = signal(record), entry = record.entry || null, exit = record.exit || null, initialStop = record.initial_stop ?? s.initial_stop;
    const facts = [fact("信号收盘", `${fmtPrice(s.price || s.signal_close)} · ${fmtTime(record.signal_close_ms ?? s.time_ms ?? s.bar_close_ms)}`), fact("初始 SL 参考（非动态）", fmtPrice(initialStop)), fact("账本实际次开盘", entry ? `${fmtPrice(entry.price)} · ${fmtTime(entry.time_ms)}` : (record.live_status || "无账本成交记录")), fact("回测退出", exit ? `${exit.reason || "—"} · ${fmtPrice(exit.price)} · ${fmtTime(exit.time_ms)}` : (record.live_status || "无账本退出记录")), fact("数据范围", record.coverage || record.coverage_status || "见来源记录"), fact("来源", record.provenance?.source_sha256 ? String(record.provenance.source_sha256).slice(0, 12) + "…" : (record.source_sha256 ? String(record.source_sha256).slice(0, 12) + "…" : "—"))];
    $("facts").innerHTML = facts.join("");
  }
  function renderRecordHeader(record) {
    if (!record) { $("record-sequence").textContent = "—"; $("record-title").textContent = "没有符合筛选的记录"; $("record-meta").textContent = ""; $("tv-link").removeAttribute("href"); return; }
    const s = signal(record);
    $("record-sequence").textContent = `第 ${record.sequence} / ${state.records.length} 笔`;
    $("record-title").textContent = `${record.symbol} · ${canonicalTimeframe(record.timeframe || record.timeframe_min)}`;
    $("record-meta").textContent = unavailable(record) ? `不可绘制：${record.error || "冻结 OHLC 不可用"}` : `信号：${fmtTime(record.signal_close_ms ?? s.time_ms ?? s.bar_close_ms)} · 后续 K 线仅作历史复盘`;
    $("tv-link").href = tvUrl(record);
  }
  function toCandles(rows) { return rows.map((row) => ({ time: seconds(row.t), open: Number(row.o), high: Number(row.h), low: Number(row.l), close: Number(row.c) })); }
  function draw(record, payload) {
    const rows = Array.isArray(payload.candles) ? payload.candles : [];
    if (!rows.length) throw new Error("该记录没有可绘制的冻结 OHLC");
    destroyCharts();
    const s = { ...signal(record), ...(payload.signal || {}) }, entry = payload.entry ?? record.entry, exit = payload.exit ?? record.exit, initialStop = payload.initial_stop ?? record.initial_stop ?? s.initial_stop;
    const priceContainer = $("price-chart"), mdContainer = $("momentum-chart"), volumeContainer = $("volume-chart");
    const priceChart = LightweightCharts.createChart(priceContainer, chartOptions(priceContainer, true));
    const mdChart = LightweightCharts.createChart(mdContainer, chartOptions(mdContainer, true));
    const volumeChart = LightweightCharts.createChart(volumeContainer, chartOptions(volumeContainer, true));
    watchChart(priceChart, priceContainer); watchChart(mdChart, mdContainer); watchChart(volumeChart, volumeContainer);
    const priceSeries = candleSeries(priceChart); priceSeries.setData(toCandles(rows));
    [["sma20", "#60a5fa"], ["ema20", "#93c5fd"], ["sma60", "#f5c85b"], ["ema60", "#f9df98"], ["sma120", "#a78bfa"], ["ema120", "#c4b5fd"]].forEach(([key, color]) => line(priceChart, color).setData(rows.filter((row) => Number.isFinite(Number(row[key]))).map((row) => ({ time: seconds(row.t), value: Number(row[key]) }))));
    const markers = [];
    const signalMs = s.time_ms ?? record.signal_close_ms ?? s.bar_close_ms;
    if (presentNumber(signalMs)) markers.push(marker(signalMs, "belowBar", "#eabf5f", "arrowUp", "V1 信号收盘"));
    if (presentNumber(entry?.time_ms) && presentNumber(entry?.price)) markers.push(marker(entry.time_ms, "belowBar", "#62d7ab", "circle", "账本实际 next open"));
    if (presentNumber(exit?.time_ms) && presentNumber(exit?.price)) markers.push(marker(exit.time_ms, "aboveBar", "#f2777a", "arrowDown", `退出 · ${exit.reason || "—"}`));
    priceSeries.setMarkers(markers.sort((a, b) => a.time - b.time));
    state.charts[0].cleanup = () => { state.charts[0].stopCleanup?.(); state.charts[0].signalCleanup?.(); };
    state.charts[0].stopCleanup = signalGuide(priceChart, priceContainer, signalMs);
    state.charts[0].signalCleanup = initialStopGuide(priceChart, priceSeries, priceContainer, signalMs, initialStop, exit?.time_ms);
    const md = line(mdChart, "#63b3ed", 2); md.setData(rows.filter((row) => presentNumber(row.md)).map((row) => ({ time: seconds(row.t), value: Number(row.md) })));
    const sb = line(mdChart, "#f5c85b", 2); sb.setData(rows.filter((row) => presentNumber(row.sb)).map((row) => ({ time: seconds(row.t), value: Number(row.sb) })));
    mdChart.addLineSeries({ color: "#8292aa", lineWidth: 1, lineStyle: LightweightCharts.LineStyle.Dotted, crosshairMarkerVisible: false, lastValueVisible: false, priceLineVisible: false }).setData([{ time: seconds(rows[0].t), value: 0 }, { time: seconds(rows.at(-1).t), value: 0 }]);
    volumeChart.addHistogramSeries({ color: "#5b8def", priceFormat: { type: "volume" }, priceScaleId: "" }).setData(rows.filter((row) => presentNumber(row.v)).map((row) => ({ time: seconds(row.t), value: Number(row.v), color: Number(row.c) >= Number(row.o) ? "#3fbf8f99" : "#e6636d99" })));
    syncCharts([priceChart, mdChart, volumeChart]);
    [priceChart, mdChart, volumeChart].forEach((chart) => chart.timeScale().fitContent());
    state.chartControls = { rows, charts: [priceChart, mdChart, volumeChart], signalMs };
  }
  function fit(mode) {
    const control = state.chartControls; if (!control) return;
    if (mode === "global") return control.charts.forEach((chart) => chart.timeScale().fitContent());
    const index = control.rows.findIndex((row) => Number(row.t) === Number(control.signalMs));
    if (index < 0) return;
    const from = Math.max(0, index - 100), to = Math.min(control.rows.length - 1, index + 144);
    control.charts.forEach((chart) => chart.timeScale().setVisibleLogicalRange({ from, to }));
  }
  async function selectRecord(record) {
    state.selected = record; renderList(); renderRecordHeader(record); renderFacts(record); setNotes(record); destroyCharts(); $("charts").hidden = true;
    if (!record) return;
    if (unavailable(record)) { $("chart-status").textContent = `图表不可用：${record.error || "冻结 OHLC 证据时间线无效"}。不使用当前行情或其他交易所替代。`; return; }
    $("chart-status").textContent = "正在加载该笔冻结 OHLC…";
    try {
      const response = await fetch(`${DATA_ROOT}${chartPath(record)}`, { cache: "no-store" });
      if (!response.ok) throw new Error(`图表数据 HTTP ${response.status}`);
      const payload = await response.json();
      if (recordId(record) !== recordId(state.selected || {})) return;
      renderFacts({ ...record, ...payload }); draw(record, payload); $("charts").hidden = false; $("chart-status").textContent = "十字光标、缩放和拖动均只作用于本地历史数据。"; fit("signal");
    } catch (error) { if (recordId(record) === recordId(state.selected || {})) $("chart-status").textContent = `图表不可用：${error.message}`; }
  }
  function move(delta) { const index = state.visible.findIndex((record) => recordId(record) === recordId(state.selected || {})); selectRecord(state.visible[Math.max(0, Math.min(state.visible.length - 1, index + delta))]); }
  function exportNotes() { const blob = new Blob([JSON.stringify(readNotes(), null, 2)], { type: "application/json" }); const url = URL.createObjectURL(blob); const a = document.createElement("a"); a.href = url; a.download = "spike-v1-okx-133-review-notes.json"; a.click(); URL.revokeObjectURL(url); }
  async function boot() {
    setTheme(state.theme);
    const response = await fetch(`${DATA_ROOT}manifest.json`, { cache: "no-store" });
    if (!response.ok) throw new Error(`清单 HTTP ${response.status}`);
    state.manifest = await response.json(); state.records = Array.isArray(state.manifest.records) ? state.manifest.records : [];
    if (state.records.length !== 133) console.warn("Expected 133 review records", state.records.length);
    filtered();
  }
  $("search").addEventListener("input", filtered); $("timeframe").addEventListener("change", filtered);
  $("previous").addEventListener("click", () => move(-1)); $("next").addEventListener("click", () => move(1)); $("fit-global").addEventListener("click", () => fit("global")); $("fit-signal").addEventListener("click", () => fit("signal"));
  $("theme-toggle").addEventListener("click", () => { setTheme(state.theme === "dark" ? "light" : "dark"); if (state.selected) selectRecord(state.selected); });
  $("fullscreen").addEventListener("click", () => { $("charts").classList.toggle("is-fullscreen"); state.charts.forEach(({ chart }) => chart.resize(chart.chartElement().parentElement.clientWidth, chart.chartElement().parentElement.clientHeight)); });
  $("notes").addEventListener("input", () => { if (!state.selected) return; const all = readNotes(); all[recordId(state.selected)] = $("notes").value; writeNotes(all); $("notes-status").textContent = "已保存于此浏览器。"; }); $("export-notes").addEventListener("click", exportNotes);
  document.addEventListener("keydown", (event) => { if (event.target.matches("input,textarea,select")) return; if (event.key === "ArrowLeft") move(-1); if (event.key === "ArrowRight") move(1); });
  boot().catch((error) => { $("chart-status").textContent = `无法载入图册：${error.message}`; });
})();
