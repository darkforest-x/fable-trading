/* Local monitor client. Market data is read-only; app opening requires a click. */
(() => {
  "use strict";

  const $ = (id) => document.getElementById(id);
  const state = {
    view: "signals", signals: [], directSignals: [], performanceSignals: [], rawYoloSignals: [], candidates: [], signalScope: "direct", markets: [], status: null, health: null,
    signalsLoaded: false, directSignalsLoaded: false, directSignalTotal: 0, candidatesLoaded: false, candidateTotal: 0, candidateCounts: null, marketsLoaded: false, marketsLoading: false, marketsRetryTimer: null, signalTotal: 0, rowLimit: 24, watchLimit: 24, search: "", watchSearch: "", watchScope: "building",
    shadowStatus: null, shadowEvents: [], shadowMarket: [], shadowLoaded: false, shadowLoading: false, shadowTimeframe: "all",
    rawNextCursor: null, rawHasMore: false, rawPaged: false, rawLoadingMore: false,
    ledger: null, period: "all", outcome: "all", sort: "newest", page: 0,
    timeframe: "all", watchTimeframe: "all", side: "all", signalSource: "live",
    syncing: false, refreshQueued: null, signalQueryRevision: 0, lastSync: null, statusReceivedAt: null, errors: {},
    tradingViewPending: false,
    lines: { kind: null, items: [], status: null, timeframe: "all", search: "", liveOnly: false, limit: 24, loading: false, syncedAt: null,
      period: "all", outcome: "all", sort: "newest", performanceVersion: "current", ledger: null, revision: 0 },
  };
  const titles = {
    signals: ["信号中心", "指标启动与 YOLO 确认分开展示。Bark 通知周期以运行状态为准。"],
    warmup: ["预热历史", "初次启动前的回算信号，仅供复盘，不触发通知。"],
    watch: ["蓄势观察", "还在横盘的，单独观察。这里的结构尚不是启动信号。"],
    joints: ["突破+spike", "V9 多头框还开着时出现的第一次趋势线突破，本周期或上级周期都算（V11.2 默认「多头框内」）。"],
    breaks: ["趋势线突破", "15m、1H、4H、日线自己的三点下降线被收盘突破：连续 2 根收在线上 0.2 ATR。"],
    shadow: ["前向影子", "V7 与 V8 在相同新收盘数据上并行记录，积累未参与调参的新样本。"],
    ashare: ["A股回测", "沪深主板近三年 · 固定 V1 / V8 · 日线与周线对照。"],
    system: ["运行状态", "行情、扫描与通知，每个环节都清晰可见。"],
  };
  const eventNames = { tv_start: "V9 启动", yolo_confirmed: "YOLO 补充确认" };
  const modelStates = { pending: "等待确认", confirmed: "模型已通过", invalidated: "结构失效", expired: "等待已到期", error: "检测异常", disabled: "周期已关闭" };
  const TV_SETTINGS = "近零至少 12 根 · 0.1 ATR · 普通系统标记关闭";
  // Server cursor pages are intentionally smaller than its 2,000-row safety cap.
  // Cards need a browse path, not multi-megabyte concurrent JSON responses.
  const SIGNAL_PAGE_SIZE = 24;
  const TV_INTERVALS = new Map([["5", "5m"], ["15", "15m"], ["30", "30m"], ["60", "1H"], ["240", "4H"], ["1440", "1Dutc"], ["1Dutc", "1Dutc"]]);
  const apiTimeframe = (value) => ({ "5": "5m", "5m": "5m", "15": "15m", "15m": "15m", "30": "30m", "30m": "30m", "60": "1H", "1h": "1H", "1H": "1H", "240": "4H", "4h": "4H", "4H": "4H", "1440": "1Dutc", "1D": "1Dutc", "1Dutc": "1Dutc", 5: "5m", 15: "15m", 30: "30m", 60: "1H", 240: "4H", 1440: "1Dutc" }[value] || null);
  const uiTimeframe = (value) => ({ "5m": "5", "15m": "15", "30m": "30", "1H": "60", "4H": "240", "1Dutc": "1Dutc", "1D": "1Dutc", "5": "5", "15": "15", "30": "30", "60": "60", "240": "240", "1440": "1Dutc", 5: "5", 15: "15", 30: "30", 60: "60", 240: "240", 1440: "1Dutc" }[value] || null);
  const timeframeLabel = (value) => ({ "5": "5m", "5m": "5m", "15": "15m", "15m": "15m", "30": "30m", "30m": "30m", "60": "1H", "1H": "1H", "240": "4H", "4H": "4H", "1Dutc": "日线", "1D": "日线", "1440": "日线", 5: "5m", 15: "15m", 30: "30m", 60: "1H", 240: "4H", 1440: "日线" }[value] || value || "—");
  const phaseNames = {
    building: "蓄势中", accumulating: "蓄势中", accumulation: "蓄势中", compression: "密集蓄势",
    ready: "等待启动", armed: "等待启动", flat: "零轴横盘", neutral: "观察中",
    long: "多头趋势", short: "空头趋势", trending_long: "多头趋势", trending_short: "空头趋势",
    trend_long: "多头趋势", trend_short: "空头趋势", idle: "观察中", released: "动量释放",
    warmup: "数据预热", loading: "数据预热", error: "读取异常",
  };
  const escapeHTML = (value) => String(value ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const finite = (n) => n !== null && n !== "" && n !== undefined && Number.isFinite(Number(n));
  const numeric = (n, fallback = 0) => finite(n) ? Number(n) : fallback;
  const number = (n) => finite(n) ? Number(n).toLocaleString("zh-CN") : "—";
  const sideName = (side) => side === "long" ? "多头" : side === "short" ? "空头" : side === "unknown" || side == null ? "未就绪" : "中性";
  const sideArrow = (side) => side === "short" ? "↓" : side === "long" ? "↑" : "·";
  const shortSymbol = (symbol) => {
    const value = String(symbol || "—").replace(/-(USDT|USD)-SWAP$/, "").replace(/USDT\.P$/, "");
    return value.endsWith("USDT") && value !== "USDT" ? value.slice(0, -4) : value;
  };
  const focusRun = (item) => item.near_zero_bars;
  const modelProtocol = () => state.status?.runtime?.signal_kind === "yolo_confirmed" && typeof state.status?.protocol === "string";
  const isConfirmed = (item) => item?.confirmation === "yolo" || item?.confirmation === "raw_yolo";
  const isCandidate = (item) => item?.confirmation === "raw" || item?.confirmation === "raw_yolo";
  // The monitor's current raw event kind is SPIKE V9.  Keep tv_start only for
  // already-persisted legacy chart rows; it is not the current backend kind.
  const isV1StartMarker = (event) => event?.kind === "spike_burst_v9" || event?.kind === "tv_start";
  const isDirectRecord = (item) => isCandidate(item) && TV_INTERVALS.has(String(item.timeframe));
  const signalView = (view = state.view) => view === "signals" || view === "warmup";
  const signalQuerySource = () => state.view === "warmup" ? "warmup" : state.signalSource;
  const displayScope = (item) => item?.display_scope === "warmup" ? "warmup" : item?.display_scope === "replay" || item?.source === "replay" ? "replay" : "live";
  const isWarmupRecord = (item) => displayScope(item) === "warmup";
  const sourceName = (item) => displayScope(item) === "warmup" ? "预热历史" : displayScope(item) === "replay" ? "历史回放" : "实时";
  const milliseconds = (value) => typeof value === "string" ? Date.parse(value) : Number(value);
  function normalizeV1Event(row, requestedScope = null) {
    const minutes = Number(row.timeframe_min ?? row.timeframe);
    const confirmation = row.confirmation;
    const close = milliseconds(row.signal_close_time ?? row.bar_close_ms);
    const direction = row.direction ?? row.side;
    if (![5, 15, 30, 60, 240, 1440].includes(minutes) || !["live", "replay"].includes(row.source) || !["raw", "yolo", "raw_yolo"].includes(confirmation) || !["long", "short"].includes(direction) || !finite(close)) return null;
    const rowScope = ["live", "warmup", "replay"].includes(row.display_scope) ? row.display_scope : requestedScope || row.source;
    return { ...row, id: String(row.id ?? `${row.source}|${confirmation}|${row.venue}|${row.symbol}|${minutes}|${row.signal_close_time}`),
      source: row.source === "replay" ? "replay" : "live", confirmation, timeframe: String(minutes), timeframe_min: minutes,
      display_scope: rowScope,
      kind: confirmation === "raw" ? "tv_start" : "yolo_confirmed", side: direction, bar_close_ms: close,
      bar_open_ms: milliseconds(row.signal_bar_open ?? row.bar_open_ms) || close - minutes * 60000,
      price: row.signal_close ?? row.price, is_closed: row.is_closed === true,
      model: confirmation === "raw" ? row.model : { ...(row.model || {}), status: "confirmed" } };
  }
  const twoStage = () => state.status?.runtime?.notification_mode === "two_stage";
  const notificationChannels = () => {
    const configured = state.status?.runtime?.notification_channels;
    const channels = Array.isArray(configured) ? configured : ["bark"];
    return ["telegram", "bark"].filter((channel) => channels.includes(channel) && (channel !== "telegram" || state.status?.telegram?.disabled_by_owner !== true));
  };
  // Only the policy-filtered direct endpoint supplies first-stage receipts.
  // Candidate history is deliberately not a receipt source.
  const directReceipt = (item) => isDirectRecord(item) ? state.directSignals.find((row) => sameEvent(row, item)) : null;
  const originalSignal = (item) => isConfirmed(item) ? item.indicator || {} : item;
  const sourceItems = () => state.signalScope === "confirmed" ? state.signals : state.signalScope === "direct" ? state.directSignals : state.candidates;
  const sourceKey = () => state.signalScope === "confirmed" ? "signals" : state.signalScope === "direct" ? "directSignals" : "candidates";
  const modelState = (item) => modelStates[item.model?.status] || "等待模型状态";
  const DISPLAY_ONLY_NOTE = "仅前端 · Bark 已关闭";
  const runtimeTimeframes = (field) => {
    const values = state.status?.runtime?.[field];
    if (!Array.isArray(values)) return null;
    const normalized = values.map(uiTimeframe);
    return normalized.every((value) => value && TV_INTERVALS.has(value)) ? normalized : null;
  };
  const displayOnlyTimeframes = () => runtimeTimeframes("display_only_timeframes") || (runtimeTimeframes("bark_timeframes") ? (runtimeTimeframes("timeframes") || []).filter((value) => !runtimeTimeframes("bark_timeframes").includes(value)) : []);
  const isDisplayOnly = (item) => item?.display_only === true || displayOnlyTimeframes().includes(item?.timeframe);
  const notificationPolicy = () => {
    const muted = displayOnlyTimeframes(), bark = runtimeTimeframes("bark_timeframes");
    const mutedNote = muted.length ? `${muted.map(timeframeLabel).join(" / ")} ${DISPLAY_ONLY_NOTE}；` : "";
    const delivery = bark === null ? "Bark 通知周期尚未同步；以实际回执为准。" : !bark.length ? "当前所有周期的 Bark 推送均已关闭。" : twoStage() ? `${bark.map(timeframeLabel).join(" / ")} 收盘启动先推送 Bark，YOLO 通过后追加推送；历史箭头不补发。` : `${bark.map(timeframeLabel).join(" / ")} 仍按模型确认通知，分阶段通知规则尚未启用。`;
    return mutedNote + delivery + " V9 多空均按同一规则推送。";
  };
  function candidateNotificationNote(item) {
    if (isDisplayOnly(item)) return DISPLAY_ONLY_NOTE;
    if (item.model?.status === "disabled") return "该周期已关闭 · 不再推送";
    if (directReceipt(item)) return runtimeTimeframes("bark_timeframes")?.includes(item.timeframe) ? "启动通知见通道回执；YOLO 通过后追加通知" : "启动通知见通道回执；Bark 通知周期尚未同步";
    return twoStage() ? "未关联新规则启动回执 · 不推断已发送" : "候选记录 · 当前等待模型后通知";
  }
  function modelReason(item) {
    const reason = String(item.model?.reason || "");
    const known = { md_zero_or_reversal: "动量已回到零轴或反转", no_match_within_wait: "等待窗口内未检测到匹配结构", missing_causal_candles: "检测所需行情缺失，等待数据补齐", confirmation_history_unavailable: "确认窗口行情未补齐，已结束等待", timeframe_disabled_by_owner: "该周期已关闭，停止检测与通知" };
    return known[reason] || (reason.startsWith("inference_unavailable:") ? "模型检测暂时不可用，等待重试" : reason);
  }
  const modelScore = (item) => finite(item.model?.confidence) && Number(item.model.confidence) >= 0 && Number(item.model.confidence) <= 1 ? Number(item.model.confidence).toFixed(2) : "—";
  const sameEvent = (a, b) => a && b && a.kind === b.kind && String(a.id) === String(b.id) && a.symbol === b.symbol && a.timeframe === b.timeframe;
  const quoteSymbol = (symbol) => /-USD-SWAP$/.test(String(symbol)) ? "USD" : "USDT";
  // Preserve Unicode letters/numbers: replay contracts may have non-ASCII names.
  const normalSearch = (value) => String(value).normalize("NFKC").toUpperCase().replace(/[^\p{L}\p{N}]/gu, "");
  const displayPhase = (phase) => phaseNames[phase] || String(phase || "观察中");
  const marketPhase = (item) => item.error ? "读取异常" : item.stale ? "行情过期" : item.ready === false ? "数据预热" : displayPhase(item.phase);
  const shortDate = (ms) => finite(ms) && Number(ms) > 0 ? new Intl.DateTimeFormat("zh-CN", { timeZone: "Asia/Shanghai", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false }).format(Number(ms)) : "—";
  const fullDate = (ms) => finite(ms) && Number(ms) > 0 ? new Intl.DateTimeFormat("zh-CN", { timeZone: "Asia/Shanghai", year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false }).format(Number(ms)) : "—";
  const clockTime = (ms) => finite(ms) && Number(ms) > 0 ? new Intl.DateTimeFormat("zh-CN", { timeZone: "Asia/Shanghai", hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false }).format(Number(ms)) : "—";
  const signalClock = () => finite(state.status?.now_ms) && finite(state.statusReceivedAt) ? Number(state.status.now_ms) + Math.max(0, Date.now() - state.statusReceivedAt) : Date.now();
  // API freshness is authoritative; only expire cached badges using the same time budget.
  function isFresh(item, now = signalClock()) {
    if (isWarmupRecord(item)) return false;
    const minutes = state.status?.runtime?.fresh_minutes;
    const age = now - Number(item.bar_close_ms);
    const confirmed = isConfirmed(item), direct = directReceipt(item);
    const directTimeframes = state.status?.runtime?.direct_timeframes;
    const eligible = confirmed ? item?.source === "live" : Boolean(direct && twoStage() && Array.isArray(directTimeframes) && directTimeframes.includes(item.timeframe));
    const record = confirmed ? item : direct;
    return record?.is_fresh === true && eligible && !state.errors[confirmed ? "signals" : "directSignals"] && !state.errors.status &&
      finite(state.status?.now_ms) && finite(state.statusReceivedAt) && finite(minutes) && Number(minutes) > 0 && finite(item.bar_close_ms) && age >= 0 && age <= Number(minutes) * 60000;
  }
  function price(value) {
    if (!finite(value)) return "—";
    const n = Number(value);
    return n.toLocaleString("en-US", { minimumFractionDigits: Math.abs(n) >= 1 ? 2 : 0, maximumFractionDigits: 12 });
  }
  function ageLabel(ms) {
    if (!finite(ms) || Number(ms) <= 0) return "尚无记录";
    const seconds = Math.max(0, Math.floor((signalClock() - Number(ms)) / 1000));
    if (seconds < 60) return `${seconds} 秒前`;
    if (seconds < 3600) return `${Math.floor(seconds / 60)} 分钟前`;
    if (seconds < 86400) return `${Math.floor(seconds / 3600)} 小时前`;
    return `${Math.floor(seconds / 86400)} 天前`;
  }
  function duration(ms) {
    if (!finite(ms)) return "—";
    const minutes = Math.max(0, Math.floor(Number(ms) / 60000));
    if (minutes < 60) return `${minutes} 分钟`;
    if (minutes < 1440) return `${Math.floor(minutes / 60)} 小时 ${minutes % 60} 分钟`;
    return `${Math.floor(minutes / 1440)} 天 ${Math.floor(minutes / 60) % 24} 小时`;
  }
  async function api(path, signal) {
    const controller = signal ? null : new AbortController();
    const timer = controller ? setTimeout(() => controller.abort(), 12000) : null;
    try {
      const response = await fetch(path, { cache: "no-store", signal: signal || controller.signal, headers: { Accept: "application/json" } });
      if (!response.ok) throw new Error(`服务返回 HTTP ${response.status}`);
      const contentType = response.headers.get("content-type") || "";
      if (!contentType.includes("json")) throw new Error("服务未返回有效 JSON 数据");
      const result = await response.json();
      if (result === null || typeof result !== "object") throw new Error("服务返回的数据格式有误");
      return result;
    } catch (error) {
      if (error.name === "AbortError") throw new Error("服务响应超时");
      throw error;
    } finally {
      if (timer) clearTimeout(timer);
    }
  }
  function canOpenTradingView(item) {
    const symbol = String(item?.symbol || "").toUpperCase();
    const validSymbol = /^[A-Z0-9]{1,30}-(USDT|USDC|USD)-SWAP$/.test(symbol) || /^(?:OKX:)?[A-Z0-9]{1,30}(?:USDT|USDC|USD)(?:\.P)?$/.test(symbol);
    return validSymbol && Boolean(apiTimeframe(item?.timeframe));
  }
  function renderTradingViewButtons() {
    document.querySelectorAll("[data-tradingview-action]").forEach((button) => {
      button.disabled = state.tradingViewPending || !canOpenTradingView({ symbol: button.dataset.tvSymbol, timeframe: button.dataset.tvTimeframe });
      button.setAttribute("aria-busy", String(state.tradingViewPending));
    });
    document.querySelectorAll("[data-tradingview-label]").forEach((label) => {
      label.textContent = state.tradingViewPending ? "正在打开…" : label.dataset.tradingviewLabel;
    });
  }
  function tradingViewStatus(message, kind) {
    const status = $("tradingview-status");
    status.textContent = message;
    status.classList.remove("hidden", "error", "pending");
    if (kind) status.classList.add(kind);
  }
  // Only explicit user action handlers call this bridge; rendering never launches apps.
  async function openTradingView(item) {
    if (state.tradingViewPending) return;
    if (!canOpenTradingView(item)) {
      tradingViewStatus("当前合约或周期不支持在 TradingView 打开。", "error");
      return;
    }
    const request = { symbol: item.symbol, timeframe: apiTimeframe(item.timeframe) };
    const label = `${shortSymbol(request.symbol)} ${quoteSymbol(request.symbol)} · ${timeframeLabel(request.timeframe)}`;
    state.tradingViewPending = true;
    renderTradingViewButtons();
    tradingViewStatus(`正在请求 本机 TradingView 打开 ${label}…`, "pending");
    const controller = new AbortController();
    // TradingView Desktop may expose its tab before the chart canvas has
    // switched. The server verifies that canvas for up to 58 seconds.
    const timeout = setTimeout(() => controller.abort(), 62000);
    try {
      const response = await fetch("/api/tradingview/open", {
        method: "POST", cache: "no-store", signal: controller.signal,
        headers: { "Content-Type": "application/json", Accept: "application/json", "X-Spike-Action": "open-tradingview" },
        body: JSON.stringify(request),
      });
      const isJSON = (response.headers.get("content-type") || "").includes("json");
      const result = isJSON ? await response.json() : null;
      if (!response.ok) throw new Error(typeof result?.detail === "string" ? result.detail : `服务返回 HTTP ${response.status}`);
      if (result?.requested !== true || result?.verified !== true || result.timeframe !== request.timeframe) {
        throw new Error("服务未确认目标主图已加载。");
      }
      tradingViewStatus(`已在 Mac TradingView 打开 ${label}。`, "");
    } catch (error) {
      tradingViewStatus(error.name === "AbortError" ? "打开请求超时，尚无法确认结果；请先查看 TradingView。" : `无法请求 TradingView：${error.message || "连接失败"}`, "error");
    } finally {
      clearTimeout(timeout);
      state.tradingViewPending = false;
      renderTradingViewButtons();
    }
  }
  function setView(view, updateHash = true) {
    const previousView = state.view;
    const nextView = titles[view] ? view : "signals";
    state.view = nextView;
    const section = signalView(state.view) ? "signals" : linesView(state.view) ? "lines" : state.view;
    ["signals", "watch", "lines", "shadow", "ashare", "system"].forEach((key) => $(`${key}-view`).classList.toggle("hidden", key !== section));
    $("primary-metrics").classList.toggle("hidden", ["shadow", "ashare", "joints", "breaks"].includes(state.view));
    document.querySelectorAll("[data-view]").forEach((button) => {
      const active = button.dataset.view === state.view;
      button.classList.toggle("active", active);
      if (active) button.setAttribute("aria-current", "page"); else button.removeAttribute("aria-current");
    });
    $("exchange-mark").textContent = state.view === "ashare" ? "A股" : "OKX";
    $("market-scope").textContent = state.view === "ashare" ? "沪深主板" : "全市场永续";
    $("connection-label").classList.toggle("hidden", state.view === "ashare");
    $("bark-header").classList.toggle("hidden", state.view === "ashare");
    if (state.view === "ashare") $("sidebar-runtime").textContent = "历史回测独立运行";
    $("page-title").textContent = titles[state.view][0];
    $("breadcrumb-current").textContent = titles[state.view][0];
    $("page-description").textContent = state.view === "signals" && state.status ? notificationPolicy() : titles[state.view][1];
    $("signal-source-scope").classList.toggle("hidden", state.view === "warmup");
    $("signal-scope-note").classList.toggle("hidden", state.view === "warmup");
    $("warmup-notice").classList.toggle("hidden", state.view !== "warmup");
    document.title = `spike · ${titles[state.view][0]}`;
    if (updateHash) history.replaceState(null, "", `#${state.view}`);
    const changesWarmupScope = previousView === "warmup" || state.view === "warmup";
    const entersSignalView = !signalView(previousView) && signalView(state.view);
    if (changesWarmupScope || entersSignalView) {
      state.rowLimit = 24; state.page = 0;
      invalidateSignalQuery();
      if (signalView(state.view)) refresh();
    }
    if (state.view === "system") loadHealth();
    // Signals never load the expensive market overview.  Watch opts in once.
    if (state.view === "watch") loadMarkets();
    if (state.view === "shadow") loadShadow();
    if (state.view === "ashare") window.SpikeAshare.load();
    if (linesView(state.view)) {
      const kind = state.view === "joints" ? "joint" : "break";
      if (state.lines.kind !== kind) Object.assign(state.lines, { kind, items: [], timeframe: "all", limit: 24 });
      renderLines();
      loadLines();
    }
  }
  const LINE_TIMEFRAMES = { joint: ["15m", "30m", "1H", "4H"], break: ["15m", "1H", "4H", "1Dutc"] };
  const LINE_STATES = { live: ["实时", "admitted"], late: ["补录", "filtered"], history: ["启用前", "filtered"] };
  function linesView(view = state.view) { return view === "joints" || view === "breaks"; }
  // Capped-wick line prices are off the tick grid; show them at the contract's precision.
  function tickPrice(value, tick) {
    if (!finite(value)) return "—";
    const decimals = finite(tick) && Number(tick) > 0 ? Math.min(10, Math.max(0, Math.ceil(-Math.log10(Number(tick)) - 1e-9))) : null;
    return decimals === null ? price(value) : Number(value).toFixed(decimals);
  }
  function lineFacts(line, label, tick) {
    if (!line || !finite(line.a_price)) return "";
    const price = (value) => tickPrice(value, tick);
    const tf = timeframeLabel(line.line_timeframe === "2H" ? "2H" : line.line_timeframe);
    return `<div class="lines-geometry"><dt>${escapeHTML(label)} · ${escapeHTML(tf)}</dt><dd>A ${escapeHTML(shortDate(line.a_ms))} ${escapeHTML(price(line.a_price))} → B ${escapeHTML(shortDate(line.b_ms))} ${escapeHTML(price(line.b_price))} → C ${escapeHTML(shortDate(line.c_ms))} ${escapeHTML(price(line.c_price))}</dd><dd class="lines-born">三点确认 ${escapeHTML(shortDate(line.born_close_ms))}${line.track ? " · " + escapeHTML(line.track) : ""}</dd></div>`;
  }
  const LINE_UNOPENED = {
    awaiting_next_open: "等待下一根开盘入场", serial_position_already_open: "同币同周期已有持仓 · 本信号不开仓",
    data_gap_censored: "数据断档 · 无法跟踪", left_analysis_window: "超出计算窗口 · 停止跟踪",
    next_bar_is_gap: "下一根数据断档 · 未开仓", risk_invalid: "止损无效 · 未开仓",
  };
  const LINE_EXITS = { initial_stop: "初始止损", initial_stop_gap: "初始止损（跳空）", trailing_stop: "跟随保护",
    trailing_stop_gap: "跟随保护（跳空）", opposite_v6_next_open: "V9 空头确认平仓",
    rsi_seventh_reverse_next_open: "RSI 第7个空头大菱形 · 全平" };
  const RSI_LINES_BASIS = "v11_2_box_joint_rsi7_same_tf_streak_next_open_v1_net_cost";
  const linesSnapshot = () => state.lines.ledger?.selected_performance_version === "baseline";
  function rsiProgress(p) {
    if (p.performance_version !== RSI_LINES_BASIS && p.basis !== RSI_LINES_BASIS) return "";
    if (p.rsi_exit_pending) return " · 第7个空头大菱形已确认，待次根开盘全平";
    if (!p.rsi_counter_known) return " · RSI 连续计数待完整序列";
    if (p.rsi_run_side !== -1) return " · 空头大菱形计数待开始";
    return p.rsi_run_count >= 7 ? ` · 本轮空头大菱形 ${number(p.rsi_run_count)} 个 · 第7个在本仓入场前`
      : ` · 空头大菱形 ${number(p.rsi_run_count)} / 7`;
  }
  function linePerformanceView(item) {
    const p = item.performance;
    if (!p || !["active", "profit", "loss", "breakeven"].includes(p.status)) {
      return { className: "outcome-unknown", badge: LINE_UNOPENED[p?.reason] || "状态未知", value: "—", valueLabel: "当前 R",
        peak: "—", stop: finite(item.reference_stop) ? price(item.reference_stop) : "—", stopLabel: "参考 SL",
        note: "未开仓的信号不计入 R" };
    }
    const value = p.status === "active" ? p.current_r : p.exit_r;
    const up = finite(value) && Number(value) > 0.005, down = finite(value) && Number(value) < -0.005;
    const className = p.status === "profit" ? "outcome-win" : p.status === "loss" ? "outcome-loss" : p.status === "breakeven" ? "outcome-even" : up ? "outcome-active-win" : down ? "outcome-active-loss" : "outcome-active";
    const exitName = LINE_EXITS[p.exit_reason] || p.exit_reason || "";
    const snapshot = linesSnapshot();
    const stopExit = /^(initial_stop|trailing_stop)/.test(p.exit_reason || "");
    const badge = p.status === "active" ? `${snapshot ? "快照时运行中" : "运行中"} · ${signedR(value)}` : p.status === "loss" ? `${stopExit ? "已止损" : "亏损退出"} · ${signedR(value)}` : p.status === "breakeven" ? "保本退出 · 0.00R" : `已退出 · ${signedR(value)}`;
    const stop = p.stop_price ?? p.initial_stop;
    return { className, badge, value: signedR(value), valueLabel: p.status === "active" ? (snapshot ? "快照浮动 R" : "当前 R") : "退出 R",
      peak: signedR(p.peak_r), stop: finite(stop) ? price(stop) : "—", stopLabel: p.trailing_active ? "跟随保护" : "SL",
      note: p.status === "active"
        ? `入场 ${price(p.entry_price)} · 已持有 ${number(p.bars_held)} 根 · ${snapshot ? "仅为切换前快照，不再更新" : "按最新收盘估值"}${snapshot ? "" : rsiProgress(p)}`
        : `入场 ${price(p.entry_price)} → 出场 ${price(p.exit_price)}（${exitName}）· 持有 ${number(p.bars_held)} 根` };
  }
  function stopFact(item) {
    if (!finite(item.reference_stop) || !finite(item.close) || Number(item.close) <= 0) return "—";
    const distance = (Number(item.close) - Number(item.reference_stop)) / Number(item.close) * 100;
    return `${price(item.reference_stop)} · −${distance.toFixed(2)}%`;
  }
  function lineCardHTML(item) {
    const [stateName, stateClass] = LINE_STATES[item.display_state] || ["—", "filtered"];
    const joint = item.kind === "joint";
    const higher = joint && (item.source === "higher" || item.source === "both");
    const title = joint ? (higher ? "突破+spike（上级突破）" : "突破+spike") : "趋势线突破";
    const outcome = joint ? linePerformanceView(item) : null;
    const performance = joint
      ? `<span class="performance-badge">${escapeHTML(outcome.badge)}</span><dl class="card-performance"><div><dt>${escapeHTML(outcome.valueLabel)}</dt><dd>${escapeHTML(outcome.value)}</dd></div><div><dt>最高 R</dt><dd>${escapeHTML(outcome.peak)}</dd></div><div><dt>${escapeHTML(outcome.stopLabel)}</dt><dd>${escapeHTML(outcome.stop)}</dd></div></dl><span class="performance-note">${escapeHTML(outcome.note)} · 模拟持仓，并非账户实际成交</span>`
      : "";
    const facts = joint
      ? `<div><dt>V9 信号</dt><dd>${escapeHTML(shortDate(item.v9_signal_close_ms))} · 后第 ${escapeHTML(number(item.bars_after_v9))} 根</dd></div><div><dt>参考止损</dt><dd>${escapeHTML(stopFact(item))}</dd></div>`
      : `<div><dt>线上价（本根）</dt><dd>${escapeHTML(tickPrice(item.line_at_bar, item.tick))}</dd></div><div><dt>参考止损</dt><dd>${escapeHTML(stopFact(item))}</dd></div>`;
    const geometry = joint
      ? (item.source !== "higher" ? lineFacts(item, "本周期线", item.tick) : "") + (higher ? lineFacts(item.higher_line, "上级线", item.tick) : "")
      : lineFacts(item, "突破的线", item.tick);
    const delay = finite(item.detect_delay_ms) && item.display_state !== "history" ? ` · 收盘后 ${escapeHTML(duration(Math.max(0, Number(item.detect_delay_ms))))} 发现` : "";
    return `<article class="shadow-event-card lines-card long ${stateClass}${outcome ? " " + outcome.className : ""}${item.is_fresh ? " is-fresh" : ""}"><button type="button" class="card-primary-action" data-line-id="${escapeHTML(item.id)}" data-tradingview-action="lines" data-tv-symbol="${escapeHTML(item.symbol)}" data-tv-timeframe="${escapeHTML(item.timeframe)}" title="点击整张卡片，在本机 TradingView 打开" aria-label="在本机 TradingView 打开 ${escapeHTML(shortSymbol(item.symbol))} ${escapeHTML(timeframeLabel(item.timeframe))}"></button><span class="signal-card-top"><span class="card-symbol"><strong>${escapeHTML(shortSymbol(item.symbol))}</strong><small>OKX · ${escapeHTML(quoteSymbol(item.symbol))} 永续</small></span><span class="card-timeframe">${escapeHTML(timeframeLabel(item.timeframe))}</span></span><span class="signal-card-direction"><span class="card-direction">↑ ${escapeHTML(title)}</span><span class="shadow-v8-badge ${stateClass}">${item.is_fresh ? "新 · " : ""}${escapeHTML(stateName)}</span></span><span class="card-price-label">信号收盘价</span><span class="card-price">${escapeHTML(price(item.close))}</span>${performance}<dl class="shadow-event-facts">${facts}${geometry}</dl><span class="card-footer"><time title="${escapeHTML(fullDate(item.bar_close_ms))} 北京时间">${escapeHTML(shortDate(item.bar_close_ms))} 收盘${delay}</time><span class="card-open" data-tradingview-label="整卡打开 TradingView ↗" aria-hidden="true">整卡打开 TradingView ↗</span></span></article>`;
  }
  function renderLines() {
    const lines = state.lines;
    const kind = lines.kind || (state.view === "joints" ? "joint" : "break");
    const status = lines.status || {};
    const configured = status.configured === true;
    $("lines-policy-title").textContent = kind === "joint" ? "SPIKE V11.2 · 突破+spike（多头框内）" : "SPIKE V11.2 · 趋势线突破";
    $("lines-rule-note").textContent = kind === "joint"
      ? "上级：15m 看 1H，30m 看 2H，1H 看 4H，4H 看日线 · 每个 V9 框只算第一次"
      : "只看本周期自己的线 · 与 TV 指标同一套三点线规则";
    const rsiPolicy = status.performance_policy?.version === RSI_LINES_BASIS || lines.ledger?.basis === RSI_LINES_BASIS;
    $("lines-exit-rule").classList.toggle("hidden", kind !== "joint");
    $("lines-exit-rule").textContent = !lines.ledger && lines.versionOptions
      ? "正在读取所选退出规则…"
      : linesSnapshot()
      ? "切换前旧规则快照 · 原止损、4ATR跟随保护、V9反向退出 · 数值固定，不是当前持仓"
      : rsiPolicy
        ? "退出：原保护与同周期第7个空头大菱形，先触发先退出 · 仅大菱形连续同色计数，异色重置 · 收盘确认，次根开盘全平"
        : "退出：原止损、4ATR跟随保护、V9反向退出 · 等待退出规则同步";
    $("lines-timeframes").innerHTML = ["all", ...LINE_TIMEFRAMES[kind]].map((tf) => `<button type="button" data-lines-timeframe="${tf}" class="${lines.timeframe === tf ? "selected" : ""}" aria-pressed="${lines.timeframe === tf}">${tf === "all" ? "全部" : escapeHTML(timeframeLabel(tf))}</button>`).join("");
    $("lines-stats").classList.toggle("hidden", kind !== "joint");
    $("lines-ledger-filters").classList.toggle("hidden", kind !== "joint");
    if (kind === "joint") renderLinesStats();
    const q = lines.search.trim().toUpperCase();
    const day = Date.now() - 86_400_000;
    const items = lines.items.filter((item) => (lines.timeframe === "all" || item.timeframe === lines.timeframe)
      && (!q || String(item.symbol).toUpperCase().includes(q)) && (!lines.liveOnly || item.display_state === "live"));
    $("lines-day-count").textContent = lines.items.length || configured ? number(lines.items.filter((item) => item.bar_close_ms >= day).length) : "—";
    $("lines-live-count").textContent = lines.items.length || configured ? number(lines.items.filter((item) => item.display_state === "live").length) : "—";
    const scan = status.scan || {};
    $("lines-scan-state").textContent = !configured ? "未启动" : scan.status === "idle" ? "正常" : scan.status === "scanning" ? "扫描中" : scan.status === "degraded" ? "部分异常" : scan.status === "error" ? "失败" : "—";
    $("lines-scan-detail").textContent = !configured ? "工作进程随监控服务启动" : scan.finished_ms
      ? `上轮 ${shortDate(scan.finished_ms)} · ${number(scan.symbols)} 个合约${scan.errors ? ` · 异常 ${number(scan.errors)}` : ""}`
      : scan.status === "error" ? String(scan.error || "") : `进行中 · ${number(scan.cells)} 格`;
    $("lines-activation").textContent = configured && status.activation?.activated_ms
      ? `启用于 ${fullDate(status.activation.activated_ms)}（之前的是启用时回算，标「启用前」）` : "尚未启用";
    $("lines-last-sync").textContent = lines.syncedAt ? `同步于 ${clockTime(lines.syncedAt)}` : "尚未同步";
    $("lines-section-title").textContent = kind === "joint" ? "最新突破+spike" : "最新趋势线突破";
    $("lines-filtered-count").textContent = `${number(items.length)} 条`;
    $("lines-empty").classList.toggle("hidden", items.length > 0);
    $("lines-empty-title").textContent = configured ? "暂无符合条件的信号" : "等待第一次扫描";
    $("lines-empty-text").textContent = configured ? "换个周期或清空搜索试试；新信号在 K 线收盘后约 1–3 分钟出现。" : "工作进程启动后约几分钟出现结果。";
    $("lines-rows").innerHTML = items.slice(0, lines.limit).map(lineCardHTML).join("");
    $("load-more-lines").classList.toggle("hidden", items.length <= lines.limit);
    renderTradingViewButtons();
  }
  function changeLinesPerformanceVersion(value) {
    const lines = state.lines;
    lines.versionOptions = lines.ledger?.available_performance_versions || lines.versionOptions;
    lines.performanceVersion = value; lines.limit = 24;
    lines.items = []; lines.ledger = null;
    // Render the cleared values immediately, including during the debounce.
    renderLines();
  }
  function renderLinesStats() {
    const data = state.lines.ledger, stats = data?.stats;
    const versions = data?.available_performance_versions || state.lines.versionOptions || [{ value: "current", label: "当前退出规则" }];
    const select = $("lines-performance-version");
    select.innerHTML = versions.map((v) => `<option value="${escapeHTML(v.value)}">${escapeHTML(v.label)}</option>`).join("");
    select.value = state.lines.performanceVersion;
    select.disabled = versions.length < 2;
    const unknown = stats ? numeric(stats.unknown) : 0, empty = stats?.total === 0;
    const notice = $("lines-stats-notice");
    notice.classList.toggle("hidden", !stats || unknown === 0);
    notice.textContent = `${number(unknown)} 条信号没有开仓或状态未知（等待次根开盘、同币同周期已有持仓或数据断档），不计入运行中、已结束和胜率。`;
    const setR = (id, value) => {
      const node = $(id);
      node.textContent = signedR(value);
      node.classList.toggle("r-positive", finite(value) && Number(value) > 0);
      node.classList.toggle("r-negative", finite(value) && Number(value) < 0);
    };
    setR("lines-stats-realized", stats?.realized_r);
    setR("lines-stats-floating", stats?.floating_r);
    $("lines-stats-closed-note").textContent = empty ? "当前筛选无信号" : stats ? `${stats.measured_closed} 笔有 R / ${stats.closed} 笔已结束` : "等待统计";
    $("lines-stats-active-note").textContent = empty ? "当前筛选无信号" : stats ? `${stats.measured_active} 笔有 R / ${stats.active} 笔${linesSnapshot() ? "快照时运行中" : "运行中"}` : "等待统计";
    $("lines-stats-winrate").textContent = finite(stats?.win_rate) ? `${(stats.win_rate * 100).toFixed(1)}%` : "—";
    $("lines-stats-win-note").textContent = empty ? "当前筛选无信号" : stats ? `盈利 ${stats.profit} · 亏损 ${stats.loss} · 保本 ${stats.breakeven}${finite(stats.average_r) ? ` · 平均 ${signedR(stats.average_r)}` : ""}` : "只统计有退出 R 的信号";
    $("lines-stats-total").textContent = stats ? number(stats.total) : "—";
    $("lines-stats-side-note").textContent = stats ? `只做多 · 未开仓/未知 ${unknown}` : "每个 V9 框只算一次";
    const cellR = (r) => `<td class="${finite(r) && r > 0 ? "r-positive" : finite(r) && r < 0 ? "r-negative" : ""}">${escapeHTML(signedR(r))}</td>`;
    $("lines-stats-timeframes").innerHTML = (data?.by_timeframe || []).map((row) => `<tr><th scope="row">${escapeHTML(timeframeLabel(row.timeframe))}</th><td>${row.total}</td><td>${row.active}</td><td>${row.closed}</td><td>${row.unknown}</td>${cellR(row.realized_r)}${cellR(row.floating_r)}<td>${finite(row.win_rate) ? `${(row.win_rate * 100).toFixed(1)}%` : "—"}</td></tr>`).join("");
    const versionsInView = data?.projection_versions || [];
    const mixed = Array.isArray(versionsInView) ? versionsInView.length > 1 : Object.keys(versionsInView).length > 1;
    const timeNote = linesSnapshot() ? `切换前固定快照 ${fullDate(data.performance_snapshot_ms || data.as_of_ms)} · 不作新旧规则同步收益对照`
      : data ? `当前规则回算 · 更新 ${clockTime(data.as_of_ms)}${mixed ? " · 退出规则更新中，部分记录仍为旧版" : ""}` : "等待同步";
    $("lines-stats-basis").textContent = `按信号收盘日归属 · 北京时间 · 周一开始 · 周期、搜索、「只看实时」同样生效 · ${timeNote}`;
  }
  async function loadLinesStatus() {
    try {
      const status = await api("/api/lines/status");
      state.lines.status = status;
      const recent = status.recent_24h || {};
      $("nav-joints-count").textContent = status.configured ? number(recent.joint || 0) : "—";
      $("nav-breaks-count").textContent = status.configured ? number(recent.break || 0) : "—";
    } catch (error) { /* the page view reports load errors */ }
  }
  async function loadLines() {
    if (state.lines.loading || !linesView()) return;
    state.lines.loading = true;
    const kind = state.view === "joints" ? "joint" : "break";
    try {
      const lines = state.lines, revision = lines.revision;
      const query = kind === "joint"
        ? `/api/lines/ledger?kind=joint&limit=2000&performance_version=${encodeURIComponent(lines.performanceVersion)}&period=${lines.period}&outcome=${lines.outcome}&sort=${lines.sort}&scope=${lines.liveOnly ? "live" : "all"}${lines.timeframe === "all" ? "" : `&timeframe=${encodeURIComponent(lines.timeframe)}`}&search=${encodeURIComponent(lines.search.trim().slice(0, 24))}`
        : `/api/lines/events?kind=${kind}&limit=1000`;
      const [events] = await Promise.all([api(query), loadLinesStatus()]);
      if (!Array.isArray(events.items)) throw new Error("服务返回的数据格式有误");
      if (lines.kind === kind && lines.revision === revision) {
        lines.items = events.items.filter((item) => item && typeof item === "object" && item.id && item.symbol);
        lines.ledger = kind === "joint" ? events : null;
        lines.syncedAt = Date.now();
      }
      delete state.errors.lines;
    } catch (error) {
      state.errors.lines = error.message || "请求失败";
    } finally {
      state.lines.loading = false;
      renderErrors();
      if (linesView()) renderLines();
    }
  }
  function filteredSignals() {
    const q = normalSearch(state.search);
    return sourceItems().filter((item) => item && (!q || normalSearch(item.symbol).includes(q)) &&
      (state.timeframe === "all" || item.timeframe === state.timeframe) &&
      (state.side === "all" || item.side === state.side) && (state.signalScope === "confirmed" ? isConfirmed(item) : state.signalScope === "direct" ? isDirectRecord(item) : isCandidate(item) && (state.signalScope === "all" || ["pending", "error"].includes(item.model?.status))));
  }
  function notification(item, channel = "telegram") {
    if (isWarmupRecord(item)) {
      const saved = String(item[channel === "bark" ? "bark_notification_status" : "notification_status"] || "").toLowerCase();
      if (["sent", "delivered", "success"].includes(saved)) return ["保存回执 · 服务已接受", "muted"];
      if (["failed", "error", "dead"].includes(saved)) return ["保存回执 · 发送失败", "muted"];
      if (saved === "unknown") return ["保存回执 · 未知", "muted"];
      return ["预热历史不通知", "muted"];
    }
    if (item?.source === "replay") return ["历史回放不通知", "muted"];
    const value = String(item[channel === "bark" ? "bark_notification_status" : "notification_status"] || "").toLowerCase();
    if (["sent", "delivered", "success"].includes(value)) return [channel === "bark" ? "服务已接受" : "已发送", "sent"];
    if (["failed", "error", "dead"].includes(value)) return ["发送失败", "failed"];
    if (value === "unknown") return ["回执未知", "pending"];
    if (channel === "bark" && isDisplayOnly(item)) return [DISPLAY_ONLY_NOTE, "muted"];
    if (["pending", "queued", "retry", "sending"].includes(value)) return ["等待发送", "pending"];
    if (["disabled", "not_configured"].includes(value)) return ["通知未启用", "muted"];
    if (channel === "bark" && value === "skipped") return ["已跳过", "muted"];
    if (channel === "bark" && !value) return ["暂无状态", "muted"];
    if (item.is_fresh === false || ["history", "historical", "stale", "expired", "skipped"].includes(value)) return ["历史记录", "muted"];
    if (["suppressed", "duplicate"].includes(value)) return ["已去重", "muted"];
    return ["已记录", "muted"];
  }
  function notificationHTML(item) {
    return notificationChannels().map((channel) => {
      const [label, className] = notification(item, channel);
      const name = channel === "bark" ? "Bark" : "TG";
      const description = channel === "bark" && className === "sent" ? "Bark 服务已接受推送，不代表手机已收到或已读" : `${name} · ${label}`;
      const policyNote = channel === "bark" && isDisplayOnly(item) && label !== DISPLAY_ONLY_NOTE ? `<span class="candidate-notice">当前${DISPLAY_ONLY_NOTE}</span>` : "";
      return `<span class="row-status ${className}" data-notification-channel="${channel}" title="${escapeHTML(description)}" aria-label="${escapeHTML(description)}"><span class="notification-channel">${name}</span><span>${escapeHTML(label)}</span></span>${policyNote}`;
    }).join("");
  }
  function signalPerformance(item) {
    const original = originalSignal(item);
    if (Object.hasOwn(item, "origin_close_ms")) return item.performance || null;
    if (isConfirmed(item)) {
      const sourceId = String(item?.source_event_id || original?.id || "");
      const originalClose = milliseconds(original?.bar_close_ms ?? original?.signal_close_time ?? item.bar_close_ms);
      const latest = [...state.performanceSignals, ...state.directSignals].find((row) => !isConfirmed(row) &&
        ((sourceId && String(row.id || "") === sourceId) ||
          (row.symbol === item.symbol && row.timeframe === item.timeframe && row.side === item.side &&
            milliseconds(row.bar_close_ms ?? row.signal_close_time) === originalClose)));
      if (latest?.performance && typeof latest.performance === "object") return latest.performance;
    }
    if (original?.performance && typeof original.performance === "object") return original.performance;
    if (item?.performance && typeof item.performance === "object") return item.performance;
    return null;
  }
  function signedR(value) {
    if (!finite(value)) return "—";
    const amount = Number(value);
    return `${amount > 0 ? "+" : ""}${amount.toFixed(2)}R`;
  }
  function performanceView(item) {
    const performance = signalPerformance(item);
    if (!performance || !["active", "profit", "loss", "breakeven"].includes(performance.status)) return {
      className: "outcome-unknown", badge: "仅入场参考", value: "—", valueLabel: "当前 R",
      peak: "—", stop: finite(originalSignal(item)?.initial_stop) ? price(originalSignal(item).initial_stop) : "—",
      stopLabel: "初始 SL", note: "V9 暂不计算持仓路径 R",
    };
    const status = String(performance.status || "active");
    const outcomeR = status === "active" ? performance.current_r : performance.exit_r;
    const positive = finite(outcomeR) && Number(outcomeR) > 0.005;
    const negative = finite(outcomeR) && Number(outcomeR) < -0.005;
    const className = status === "profit" ? "outcome-win" : status === "loss" ? "outcome-loss" : status === "breakeven" ? "outcome-even" : positive ? "outcome-active-win" : negative ? "outcome-active-loss" : "outcome-active";
    const badge = status === "profit" ? `已保护退出 · ${signedR(outcomeR)}` : status === "loss" ? `已止损 · ${signedR(outcomeR)}` : status === "breakeven" ? "保本退出 · 0.00R" : `运行中 · ${signedR(outcomeR)}`;
    const stop = performance.stop_price ?? performance.initial_stop ?? originalSignal(item)?.initial_stop;
    return {
      className, badge, value: signedR(outcomeR), valueLabel: status === "active" ? "当前 R" : "退出 R",
      peak: signedR(performance.peak_r), stop: finite(stop) ? price(stop) : "—",
      stopLabel: performance.trailing_active ? "跟随保护" : "SL", note: status === "active" ? `${number(performance.bars_held)} 根已收盘 K 线 · 保护线下一根生效` : `已持有 ${number(performance.bars_held)} 根 K 线`,
    };
  }
  function renderSignals() {
    const items = sourceItems(), data = state.ledger;
    const loaded = Boolean(data), total = data?.total || 0;
    const confirmed = state.signalScope === "confirmed", warmup = state.view === "warmup";
    $("filtered-count").textContent = loaded ? `${number(total)} 条` : "—";
    $("signal-section-title").textContent = confirmed ? "YOLO 补充确认" : "V9 启动 · 多空";
    $("signal-scope-note").textContent = confirmed
      ? "YOLO 追加确认关联原始 V9 启动，不重复计数。"
      : "V9 多空过滤后启动；价格与止损为确认收盘参考，暂不计算持仓路径 R。";
    $("signal-window-note").textContent = loaded ? `全量 ${number(total)} 条 · 第 ${state.page + 1} 页` : "正在读取全量统计";
    $("load-more-signals").classList.toggle("hidden", !data?.has_more);
    $("load-more-signals").disabled = state.syncing;
    $("load-more-signals").textContent = "下一页 →";
    $("load-earlier-signals").classList.toggle("hidden", state.page === 0);
    $("load-earlier-signals").disabled = state.syncing;
    $("load-earlier-signals").textContent = "← 上一页";
    $("signal-empty").classList.toggle("hidden", items.length > 0);
    if (!items.length) {
      $("signal-empty-title").textContent = state.errors[sourceKey()] ? "信号数据暂时不可用" : loaded ? "没有符合筛选的信号" : "正在读取信号";
      $("signal-empty-description").textContent = loaded ? "可调整时间范围、方向、周期或状态。" : "正在同步已收盘行情与统计。";
    }
    const focused = document.activeElement?.dataset;
    $("signal-rows").innerHTML = `<div class="signal-card-grid">${items.map((item) => signalCardHTML(item)).join("")}</div>`;
    $("signal-footer-note").textContent = warmup ? "预热回算 · 不触发通知 · 北京时间" : "已收盘确认 · 北京时间";
    renderLedgerStats();
    renderTradingViewButtons();
    if (focused?.signalId) Array.from($("signal-rows").querySelectorAll("[data-signal-id]")).find((card) => card.dataset.signalId === focused.signalId)?.focus({ preventScroll: true });
  }
  function renderLedgerStats() {
    const data = state.ledger, stats = data?.stats;
    const setR = (id, value) => {
      const node = $(id);
      node.textContent = signedR(value);
      node.classList.toggle("r-positive", finite(value) && Number(value) > 0);
      node.classList.toggle("r-negative", finite(value) && Number(value) < 0);
    };
    setR("stats-realized", stats?.realized_r);
    setR("stats-floating", stats?.floating_r);
    $("stats-closed-note").textContent = stats ? `${stats.measured_closed} 笔有 R / ${stats.closed} 笔已结束` : "等待统计";
    $("stats-active-note").textContent = stats ? `${stats.measured_active} 笔有 R / ${stats.active} 笔运行中` : "等待统计";
    $("stats-winrate").textContent = finite(stats?.win_rate) ? `${(stats.win_rate * 100).toFixed(1)}%` : "—";
    $("stats-win-note").textContent = stats ? `盈利 ${stats.profit} · 亏损 ${stats.loss} · 保本 ${stats.breakeven}` : "只统计有退出 R 的信号";
    $("stats-total").textContent = stats ? number(stats.total) : "—";
    $("stats-side-note").textContent = stats ? `多 ${stats.long} · 空 ${stats.short} · 缺 R ${stats.missing_r}` : "按原始启动去重";
    const cellR = (r) => `<td class="${finite(r) && r > 0 ? "r-positive" : finite(r) && r < 0 ? "r-negative" : ""}">${escapeHTML(signedR(r))}</td>`;
    $("stats-timeframes").innerHTML = (data?.by_timeframe || []).map((row) => `<tr><th scope="row">${escapeHTML(row.timeframe)}</th><td>${row.total}</td><td>${row.active}</td><td>${row.closed}</td>${cellR(row.realized_r)}${cellR(row.floating_r)}<td>${finite(row.win_rate) ? `${(row.win_rate * 100).toFixed(1)}%` : "—"}</td></tr>`).join("");
    $("stats-basis").textContent = `按原始信号收盘日归属 · 北京时间 · 周一开始 · 全部筛选条件生效 · ${state.errors[sourceKey()] ? "同步失败，保留上次快照" : data ? `更新 ${clockTime(data.as_of_ms)}` : "等待同步"}`;
  }
  function signalCardHTML(item, now = signalClock()) {
    const confirmed = isConfirmed(item), original = originalSignal(item), direct = directReceipt(item);
    const side = item.side === "short" ? "short" : item.side === "long" ? "long" : "neutral";
    const venue = String(item.venue || "OKX").toUpperCase();
    const fresh = isFresh(item, now);
    const status = confirmed ? "YOLO 补充确认" : "V9 启动", caption = "信号收盘价";
    const outcome = performanceView(item);
    return `<article class="signal-card ${side} ${outcome.className}${confirmed || direct ? "" : " candidate-card"}${fresh ? " is-fresh" : ""}"><button type="button" class="card-primary-action" data-signal-id="${escapeHTML(item.id)}" data-signal-kind="${escapeHTML(item.kind)}" data-tradingview-action="signal" data-tv-symbol="${escapeHTML(item.symbol)}" data-tv-timeframe="${escapeHTML(item.timeframe)}" title="点击整张卡片，在本机 TradingView 打开" aria-label="${escapeHTML(`${venue} ${shortSymbol(item.symbol)} ${quoteSymbol(item.symbol)} ${timeframeLabel(item.timeframe)} ${sideName(item.side)}，${status}，${caption} ${price(item.price)}，${outcome.badge}，${shortDate(item.bar_close_ms)}，在本机 TradingView 打开`)}"></button>
      <span class="signal-card-top"><span class="card-symbol"><strong>${escapeHTML(shortSymbol(item.symbol))}</strong><small>${escapeHTML(venue)} · ${escapeHTML(quoteSymbol(item.symbol))} 永续</small></span><span class="card-timeframe">${escapeHTML(timeframeLabel(item.timeframe))}</span></span>
      <span class="signal-card-direction"><span class="card-direction-group"><span class="card-direction"><span class="direction-icon" aria-hidden="true"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><path d="${side === "short" ? "m3 6 6 6 4-4 8 10M15 18h6v-6" : side === "long" ? "m3 18 6-6 4 4 8-10M15 6h6v6" : "M5 12h14"}"/></svg></span><span class="direction-name">${sideName(item.side)}</span></span><span class="direction-stage">${confirmed ? "确认" : "启动"}</span></span><span class="card-recency">${isWarmupRecord(item) ? "预热历史" : item.source === "replay" ? "历史回放" : fresh ? "新 · " + ageLabel(item.bar_close_ms) : ageLabel(item.bar_close_ms)}</span></span>
      <span class="model-card-status"><span class="model-badge ${confirmed ? "confirmed" : "pending"}">${escapeHTML(status)}</span><span>${isWarmupRecord(item) ? "预热回算 · 不通知" : item.source === "replay" ? "回放记录 · 不通知" : confirmed ? "补充确认 · 非启动门" : "第一阶段 · 已收盘"}</span></span>
      <span class="card-price-label">${caption}</span><span class="card-price">${escapeHTML(price(item.price))}</span>
      ${confirmed && item.indicator ? `<span class="card-origin">V9 ${escapeHTML(price(original.price))} · ${escapeHTML(shortDate(original.bar_close_ms))}</span>` : ""}
      <span class="performance-badge">${escapeHTML(outcome.badge)}</span>
      <dl class="card-performance"><div><dt>${escapeHTML(outcome.valueLabel)}</dt><dd>${escapeHTML(outcome.value)}</dd></div><div><dt>最高 R</dt><dd>${escapeHTML(outcome.peak)}</dd></div><div><dt>${escapeHTML(outcome.stopLabel)}</dt><dd>${escapeHTML(outcome.stop)}</dd></div></dl>
      <span class="performance-note">${escapeHTML(outcome.note)} · 信号收盘参考，并非账户实际成交</span>
      <span class="card-context"><span>${"信号 K 线"}</span><strong>${item.is_closed ? "V9 已确认" : "待确认"}</strong></span>
      <span class="card-confirmed"><span>${isWarmupRecord(item) ? "回算信号 · 仅供复盘" : item.executable_entry_time ? item.source === "replay" ? `回放执行时钟 ${escapeHTML(shortDate(milliseconds(item.executable_entry_time)))}` : `实际进场 ${escapeHTML(shortDate(milliseconds(item.executable_entry_time)))}` : item.entry_reference === "next_open" ? "次开盘参考 · 等待实际成交" : "仅信号收盘参考"}</span><time title="${escapeHTML(fullDate(item.bar_close_ms))} 北京时间">${escapeHTML(shortDate(item.bar_close_ms))}</time></span>
      <span class="card-footer"><span class="notification-stack">${item.source === "replay" ? `<span class="candidate-notice">历史回放不通知</span>` : notificationHTML(item)}</span><span class="card-open" data-tradingview-label="整卡打开 TradingView ↗" aria-hidden="true">整卡打开 TradingView ↗</span></span>
    </article>`;
  }
  function applySignalFilters() {
    state.page = 0; invalidateSignalQuery(); refresh();
  }
  function invalidateSignalQuery() {
    state.signalQueryRevision++;
    state.ledger = null;
    state.signals = [];
    state.directSignals = [];
    state.performanceSignals = [];
    state.rawYoloSignals = [];
    state.signalsLoaded = false;
    state.directSignalsLoaded = false;
    state.signalTotal = 0;
    state.directSignalTotal = 0;
    state.rawNextCursor = null;
    state.rawHasMore = false;
    state.rawPaged = false;
    state.rawLoadingMore = false;
    renderSignals();
  }
  function isBuilding(item) {
    return !item.error && !item.stale && item.ready !== false && (item.focus === true || numeric(item.near_zero_bars) > 0);
  }
  function renderWatch() {
    const q = normalSearch(state.watchSearch);
    const items = state.markets.filter((item) => (state.watchScope === "all" || isBuilding(item)) &&
      (state.watchTimeframe === "all" || item.timeframe === state.watchTimeframe) && (!q || normalSearch(item.symbol).includes(q)))
      .sort((a, b) => numeric(b.near_zero_bars) - numeric(a.near_zero_bars) || numeric(b.zero_bars) - numeric(a.zero_bars) || String(a.symbol).localeCompare(String(b.symbol)));
    $("watch-count").textContent = `${items.length} 个窗口`;
    $("watch-section-title").textContent = state.watchScope === "all" ? "全市场合约" : "蓄势中的合约";
    $("watch-explanation").textContent = state.watchScope === "all" ? "包含趋势、蓄势与预热状态，点击合约查看结构。" : "跟踪近零蓄势，等待主图可见的释放标记。";
    $("watch-empty").classList.toggle("hidden", items.length > 0);
    const emptyTitle = $("watch-empty").querySelector("h3");
    const emptyDescription = $("watch-empty").querySelector("p");
    const hasFilters = Boolean(q) || state.watchTimeframe !== "all";
    if (state.errors.markets && !state.marketsLoaded) {
      emptyTitle.textContent = "观察数据暂时不可用";
      emptyDescription.textContent = "正在自动重试，连接恢复后会显示真实状态。";
    } else if (!state.marketsLoaded) {
      emptyTitle.textContent = "正在读取观察窗口";
      emptyDescription.textContent = "扫描完成后，符合条件的观察窗口会列在这里。";
    } else {
      emptyTitle.textContent = hasFilters ? "没有符合筛选的观察窗口" : state.watchScope === "all" ? "等待全市场扫描" : "等待蓄势结构出现";
      emptyDescription.textContent = hasFilters ? "试试其他合约、周期，或切换全部合约。" : state.watchScope === "all" ? "全市场合约会在扫描后列出，当前状态不等于入场信号。" : "可切换全部合约查看其他交易对；蓄势状态不代表已经启动。";
    }
    const focused = document.activeElement?.dataset;
    const focusedSymbol = focused?.tvSymbol, focusedTimeframe = focused?.tvTimeframe;
    $("load-more-watch").classList.toggle("hidden", items.length <= state.watchLimit);
    $("load-more-watch").textContent = `显示更多（${Math.min(state.watchLimit, items.length)} / ${items.length}）`;
    $("watch-rows").innerHTML = items.slice(0, state.watchLimit).map((item) => {
      const valid = !state.errors.markets && !item.error && !item.stale && item.ready !== false;
      const phaseClass = state.errors.markets ? "stale" : item.error ? "error" : item.stale ? "stale" : item.ready === false ? "loading" : item.focus === true ? "ready" : "";
      return `<article class="watch-card"><button type="button" class="card-primary-action" data-tradingview-action="watch" data-tv-symbol="${escapeHTML(item.symbol)}" data-tv-timeframe="${escapeHTML(item.timeframe)}" title="点击整张卡片，在本机 TradingView 打开" aria-label="在本机 TradingView 打开 ${escapeHTML(shortSymbol(item.symbol))} ${escapeHTML(quoteSymbol(item.symbol))} ${escapeHTML(timeframeLabel(item.timeframe))}，${escapeHTML(marketPhase(item))}"></button>
        <span class="watch-card-top"><span class="card-symbol"><strong>${escapeHTML(shortSymbol(item.symbol))}</strong><small>${escapeHTML(quoteSymbol(item.symbol))} 永续</small></span><span class="card-timeframe">${escapeHTML(timeframeLabel(item.timeframe))}</span></span>
        <span class="watch-card-phase"><span class="phase-badge ${phaseClass}" title="${escapeHTML(item.error || (item.stale ? "当前保留过期行情，等待更新" : "当前结构尚不是启动信号"))}">${escapeHTML(state.errors.markets ? "缓存 · 待同步" : marketPhase(item))}</span><span class="card-status">${!valid ? "等待更新" : item.focus ? "已达蓄势门槛" : "观察中"}</span></span>
        <span class="watch-card-run"><strong>${valid ? escapeHTML(number(item.near_zero_bars)) : "—"}<small> 根</small></strong><span>当前近零蓄势</span></span>
        <span class="watch-card-background"><span>均线密集<strong>${valid ? item.dense === true ? "已密集" : item.dense === false ? "未密集" : "—" : "—"}</strong></span><span>高周期背景<strong>${valid ? escapeHTML(sideName(item.htf_side)) : "—"}</strong></span></span>
        <div class="watch-card-foot"><time title="${escapeHTML(fullDate(item.bar_close_ms))} 北京时间">收盘 ${escapeHTML(shortDate(item.bar_close_ms))}</time><span class="card-open" data-tradingview-label="整卡打开 TradingView ↗" aria-hidden="true">整卡打开 TradingView ↗</span></div>
      </article>`;
    }).join("");
    renderTradingViewButtons();
    if (focusedSymbol) Array.from($("watch-rows").querySelectorAll("[data-tradingview-action]"))
      .find((card) => card.dataset.tvSymbol === focusedSymbol && card.dataset.tvTimeframe === focusedTimeframe)?.focus({ preventScroll: true });
  }
  const percentage = (value) => finite(value) ? `${(Number(value) * 100).toFixed(1)}%` : "—";
  const percentageDelta = (value) => finite(value) ? `${Number(value) >= 0 ? "+" : ""}${(Number(value) * 100).toFixed(1)}pp` : "—";
  const shadowSide = (value) => Number(value) === -1 ? "short" : Number(value) === 1 ? "long" : "unknown";
  function renderShadow() {
    const status = state.shadowStatus || {};
    const configured = status.configured === true;
    const cellCounts = status.cells && typeof status.cells === "object" ? status.cells : {};
    const cells = Object.values(cellCounts).reduce((sum, value) => sum + numeric(value), 0);
    $("shadow-event-count").textContent = configured ? number(status.events) : "—";
    $("shadow-v8-count").textContent = configured ? number(status.v8_admitted) : "—";
    $("shadow-cell-count").textContent = configured ? number(cells) : "—";
    $("nav-shadow-count").textContent = configured ? number(status.events) : "—";
    $("shadow-event-detail").textContent = configured ? `${number(status.path_bars)} 根前向路径 K 线` : "影子服务尚未启用";
    $("shadow-v8-detail").textContent = configured && numeric(status.events) > 0
      ? `保留率 ${percentage(numeric(status.v8_admitted) / numeric(status.events))} · 距六线边缘 ≤ 3 ATR`
      : "单变量：距六线边缘不超过 3 ATR";
    $("shadow-cell-detail").textContent = configured
      ? `就绪 ${number(cellCounts.ready || 0)} · 预热 ${number(cellCounts.warming || 0)} · 异常 ${number(cellCounts.error || 0)}`
      : "30m / 1H / 4H";
    $("shadow-activation").textContent = configured && status.activation?.activated_ms
      ? `固定启用点 ${fullDate(status.activation.activated_ms)} 北京时间`
      : "尚未启用前向账本";
    $("shadow-last-scan").textContent = configured && status.scan
      ? `${status.scan.status === "degraded" ? "部分异常" : status.scan.status === "error" ? "扫描失败" : "上轮扫描"} · ${fullDate(status.scan.finished_ms || status.scan.started_ms)}`
      : "等待扫描";

    const latest = new Map();
    state.shadowMarket.forEach((item) => { if (item && !latest.has(item.timeframe)) latest.set(item.timeframe, item); });
    $("shadow-market-grid").innerHTML = ["30m", "1H", "4H"].map((timeframe) => {
      const item = latest.get(timeframe);
      if (!item) return `<article class="shadow-market-card waiting"><span class="shadow-market-top"><strong>${timeframe}</strong><small>等待共同收盘</small></span><p>覆盖达到 80% 后显示同一时点的市场广度。</p></article>`;
      const delta = finite(item.joint_up_delta_60m) ? item.joint_up_delta_60m : item.joint_up_delta_previous_bar;
      const deltaLabel = finite(item.joint_up_delta_60m) ? "联合广度 60m 变化" : "联合广度上一根变化";
      return `<article class="shadow-market-card"><span class="shadow-market-top"><strong>${timeframe}</strong><small>${escapeHTML(shortDate(item.bar_close_ms))}</small></span><dl><div><dt>上涨参与率</dt><dd>${escapeHTML(percentage(item.up_share))}</dd></div><div><dt>实体站上六线</dt><dd>${escapeHTML(percentage(item.body_above_six_share))}</dd></div><div><dt>${deltaLabel}</dt><dd class="${numeric(delta) > 0 ? "positive" : numeric(delta) < 0 ? "negative" : ""}">${escapeHTML(percentageDelta(delta))}</dd></div><div><dt>V7 / V8 当根</dt><dd>${escapeHTML(number(item.v7_signal_count))} / ${escapeHTML(number(item.v8_signal_count))}</dd></div></dl><span class="shadow-coverage">覆盖 ${escapeHTML(number(item.coverage))} / ${escapeHTML(number(item.expected))} · 仅作复盘分层</span></article>`;
    }).join("");

    const events = state.shadowEvents.filter((item) => state.shadowTimeframe === "all" || item.timeframe === state.shadowTimeframe);
    $("shadow-filtered-count").textContent = `${events.length} 条`;
    $("shadow-empty").classList.toggle("hidden", events.length > 0);
    const focusedId = document.activeElement?.dataset?.shadowId;
    $("shadow-rows").innerHTML = events.map((item) => {
      const side = shadowSide(item.side);
      const admitted = item.v8_admitted === true;
      return `<article class="shadow-event-card ${side} ${admitted ? "admitted" : "filtered"}"><button type="button" class="card-primary-action" data-shadow-id="${escapeHTML(item.id)}" data-tradingview-action="shadow" data-tv-symbol="${escapeHTML(item.symbol)}" data-tv-timeframe="${escapeHTML(item.timeframe)}" title="点击整张卡片，在本机 TradingView 打开" aria-label="在本机 TradingView 打开 ${escapeHTML(shortSymbol(item.symbol))} ${escapeHTML(timeframeLabel(item.timeframe))}"></button><span class="signal-card-top"><span class="card-symbol"><strong>${escapeHTML(shortSymbol(item.symbol))}</strong><small>OKX · ${escapeHTML(quoteSymbol(item.symbol))} 永续</small></span><span class="card-timeframe">${escapeHTML(timeframeLabel(item.timeframe))}</span></span><span class="signal-card-direction"><span class="card-direction">${sideArrow(side)} ${sideName(side)} · V7</span><span class="shadow-v8-badge ${admitted ? "admitted" : "filtered"}">${admitted ? "V8 保留" : "V8 过滤"}</span></span><span class="card-price-label">信号收盘价</span><span class="card-price">${escapeHTML(price(item.close))}</span><dl class="shadow-event-facts"><div><dt>距六线边缘</dt><dd>${finite(item.rope_distance_atr) ? `${Number(item.rope_distance_atr).toFixed(2)} ATR` : "—"}</dd></div><div><dt>V8 原因</dt><dd>${admitted ? "未过热" : "超过 3 ATR"}</dd></div></dl><span class="card-footer"><time title="${escapeHTML(fullDate(item.bar_close_ms))} 北京时间">${escapeHTML(shortDate(item.bar_close_ms))}</time><span class="card-open" data-tradingview-label="整卡打开 TradingView ↗" aria-hidden="true">整卡打开 TradingView ↗</span></span></article>`;
    }).join("");
    renderTradingViewButtons();
    if (focusedId) Array.from($("shadow-rows").querySelectorAll("[data-shadow-id]"))
      .find((card) => card.dataset.shadowId === focusedId)?.focus({ preventScroll: true });
  }
  async function loadShadow() {
    if (state.shadowLoading) return;
    state.shadowLoading = true;
    try {
      const [status, events, market] = await Promise.all([
        api("/api/shadow/status"), api("/api/shadow/events?limit=200"), api("/api/shadow/market-state?limit=12"),
      ]);
      if (!Array.isArray(events.items) || !Array.isArray(market.items)) throw new Error("服务返回的数据格式有误");
      state.shadowStatus = status;
      state.shadowEvents = events.items.filter((item) => item && typeof item === "object" && item.id && item.symbol);
      state.shadowMarket = market.items.filter((item) => item && typeof item === "object" && item.timeframe);
      state.shadowLoaded = true;
      delete state.errors.shadow;
    } catch (error) {
      state.errors.shadow = error.message || "请求失败";
    } finally {
      state.shadowLoading = false;
      renderErrors();
      renderShadow();
    }
  }
  function factsHTML(entries) {
    return entries.map(([key, value]) => `<dt>${escapeHTML(key)}</dt><dd>${escapeHTML(value)}</dd>`).join("");
  }
  function renderStatus() {
    const status = state.status;
    const online = status !== null && !state.errors.status;
    $("connection-label").className = `live-indicator ${online ? "online" : "offline"}`;
    $("connection-label").innerHTML = `<i></i>${online ? "已连接" : state.errors.status ? "连接中断" : "连接中"}`;
    $("local-light").classList.toggle("online", online);
    if (!status) return;
    const scan = status.scan || {};
    const counts = status.counts || {};
    const bark = status.bark;
    const runtime = status.runtime || {};
    const timeframes = Array.isArray(runtime.timeframes) ? runtime.timeframes : [];
    if (state.view === "signals") $("page-description").textContent = notificationPolicy();
    $("metric-timeframes").textContent = timeframes.length ? timeframes.map(timeframeLabel).join(" + ") : "—";
    $("watch-timeframes").textContent = timeframes.length ? timeframes.map(timeframeLabel).join(" / ") : "—";
    const directTimeframes = runtimeTimeframes("direct_timeframes");
    const directScopeTimeframes = $("direct-scope-timeframes");
    if (directScopeTimeframes) directScopeTimeframes.textContent = directTimeframes ? directTimeframes.map(timeframeLabel).join(" / ") : "周期待同步";
    $("metric-signals").textContent = twoStage() ? number(counts.indicator_starts_24h) : "—";
    $("nav-signal-count").textContent = twoStage() ? number(counts.indicator_starts_24h) : "—";
    $("metric-building").textContent = number(counts.building ?? 0);
    $("metric-universe").textContent = number(status.universe?.count);
    const scanning = ["running", "scanning", "in_progress", "starting", "bootstrap"].includes(scan.status);
    const scanErrorCount = Array.isArray(scan.errors) ? scan.errors.length : numeric(scan.errors);
    const complete = numeric(scan.completed);
    const total = numeric(scan.total);
    // `counts.ready` describes persisted feature phase and may belong to a
    // prior pass.  It cannot prove every cell has caught the current close.
    $("metric-building-detail").textContent = scanning
      ? `已扫描 ${number(complete)} / ${number(total)} · 预热/追平中`
      : total > 0 && complete >= total
        ? `已扫描 ${number(complete)} / ${number(total)} · 已覆盖，按收盘刷新`
        : "扫描状态待同步";
    $("metric-signals-detail").textContent = modelProtocol() ? `另有 ${number(counts.signals_24h)} 条 YOLO 追加确认` : "模型口径待同步";
    $("metric-scan-detail").textContent = scanning ? `扫描 ${number(complete)} / ${number(total)}` : scan.finished_at_ms ? `${ageLabel(scan.finished_at_ms)}更新` : "等待扫描";
    $("sidebar-runtime").textContent = online ? status.started_at_ms ? `已运行 ${duration(Date.now() - Number(status.started_at_ms))}` : "服务运行中" : "连接中断 · 自动重连";
    const scanDegraded = scan.status === "degraded" || scanErrorCount > 0;
    $("scan-state-badge").textContent = scan.status === "error" || scan.status === "failed" ? "扫描异常" : scanDegraded ? "部分异常" : scanning ? "扫描中" : scan.finished_at_ms ? "扫描完成" : "等待扫描";
    $("scan-state-badge").className = `neutral-badge ${scan.status === "error" || scan.status === "failed" || scanDegraded ? "warn" : scan.finished_at_ms || scanning ? "good" : ""}`;
    $("scan-progress-fill").style.width = `${total > 0 ? Math.min(100, Math.max(0, complete / total * 100)) : 0}%`;
    $("scan-facts").innerHTML = factsHTML([
      ["扫描进度", `${number(complete)} / ${number(total)}`],
      ["上轮完成", fullDate(scan.finished_at_ms)], ["下轮扫描", fullDate(scan.next_scan_ms)],
      ["本轮错误", number(scanErrorCount)], ["监控范围", status.universe?.scope || "OKX 全市场永续"],
    ]);
    const barkReady = Boolean(bark?.configured && bark?.enabled);
    const barkProblem = numeric(bark?.failed) > 0 || numeric(bark?.unknown) > 0;
    $("bark-header").textContent = !bark ? "Bark · 待接入" : barkReady ? barkProblem ? "Bark · 异常" : "Bark · 已启用" : "Bark · 未启用";
    $("bark-header").classList.toggle("good", barkReady && !barkProblem);
    $("bark-state-badge").textContent = !bark ? "状态待接入" : barkReady ? barkProblem ? "需检查发送结果" : "通知已启用" : bark.configured ? "通知已关闭" : "尚未配置";
    $("bark-state-badge").className = `neutral-badge ${barkReady && !barkProblem ? "good" : "warn"}`;
    $("bark-description").textContent = !bark ? "服务尚未提供 Bark 通道状态，等待下一次同步。" : barkReady ? `${notificationPolicy()}${numeric(bark.unknown) > 0 ? "部分发送结果未知，为避免重复通知不自动重发。" : ""}服务已接受不代表手机已收到或已读。` : bark.configured ? "Bark 已配置，当前发送开关关闭；前端继续记录信号。" : "Bark 尚未配置；前端继续记录信号。";
    $("bark-facts").innerHTML = factsHTML([["本次服务接受", number(bark?.sent)], ["最近服务接受", fullDate(bark?.last_success_ms)], ["待发送", number(bark?.pending)], ["发送失败", number(bark?.failed)], ["发送结果未知", number(bark?.unknown)], ["历史服务接受", number(bark?.historical_sent)], ["配置状态", !bark ? "等待状态" : bark.configured ? "已配置（敏感信息不展示）" : "未配置"]]);
    $("service-version").textContent = status.version ? `v${String(status.version).replace(/^v/, "")}` : "本机服务";
    const runtimeFacts = [["监控台", "spike"], ["启动时间", fullDate(status.started_at_ms)], ["服务时间", fullDate(status.now_ms)], ["运行时长", duration(Date.now() - numeric(status.started_at_ms, Date.now()))]];
    if (timeframes.length) runtimeFacts.push(["监控周期", timeframes.map(timeframeLabel).join(" / ")]);
    if (runtime.host) runtimeFacts.push(["主机", runtime.host === "This Mac" ? "Mac（扫描与推送）" : runtime.host]);
    if (runtime.pid) runtimeFacts.push(["进程", runtime.pid]);
    if (runtime.data_dir) runtimeFacts.push(["数据位置", runtime.data_dir]);
    if (runtime.signal_mode || runtime.strategy || status.strategy) runtimeFacts.push(["信号规则", runtime.signal_mode || runtime.strategy || status.strategy]);
    if (runtime.higher_mode) runtimeFacts.push(["高周期规则", runtime.higher_mode]);
    const gate = runtime.model_gate || {};
    const gateImpact = twoStage() ? `指标启动记录独立运行；仅 YOLO 追加确认需要模型通过。${notificationPolicy()}` : "模型确认通知暂不可用，候选保留等待。";
    const gateIdle = gate.status === "idle" && gate.loaded !== true && Number(gate.queue_depth || 0) === 0;
    const gateNotice = !modelProtocol() ? "模型确认口径尚未同步，原始箭头不会显示为模型确认。" : gate.last_error ? `模型检测异常：${String(gate.last_error)}。${gateImpact}` : gate.status === "error" ? `部分候选检测异常，可在等待确认中查看。${gateImpact}` : gateIdle ? `YOLO 待命；当前没有合格V9 候选，出现候选时才加载模型。${gateImpact}` : gate.loaded !== true ? `YOLO 模型正在加载；V9 启动不受影响。${gateImpact}` : "";
    $("model-gate-notice").textContent = gateNotice;
    $("model-gate-notice").classList.toggle("hidden", !gateNotice);
    runtimeFacts.push(["模型检测", gate.last_error ? "检测异常 · 暂无模型确认" : gate.loaded === true ? "已加载" : "等待加载"]);
    runtimeFacts.push(["通知阶段", notificationPolicy()]);
    if (gate.profile_id || gate.profile) runtimeFacts.push(["模型配置", gate.profile_id || gate.profile]);
    if (gate.last_error) runtimeFacts.push(["模型异常", String(gate.last_error)]);
    if (finite(gate.queue_depth ?? gate.queue)) runtimeFacts.push(["模型待检测", number(gate.queue_depth ?? gate.queue)]);
    runtimeFacts.push(["确认方式", "原箭头出现后，在等待窗口内检测同一段结构"]);
    runtimeFacts.push(["主图设置快照", TV_SETTINGS]);
    runtimeFacts.push(["参数同步", "固定快照；TradingView 参数修改后，需同步更新监控配置"]);
    if (finite(runtime.fresh_minutes)) runtimeFacts.push(["新鲜信号时限", `${runtime.fresh_minutes} 分钟`]);
    if (finite(runtime.interval_seconds)) runtimeFacts.push(["扫描间隔", `${runtime.interval_seconds} 秒`]);
    if (finite(counts.loading)) runtimeFacts.push(["新合约预热中", `${counts.loading} 个窗口`]);
    $("runtime-facts").innerHTML = factsHTML(runtimeFacts);
  }
  function renderErrors() {
    const errors = Object.entries(state.errors);
    $("error-notice").classList.toggle("hidden", !errors.length);
    if (errors.length) {
      const names = { status: "运行状态", signals: "模型确认", directSignals: "指标启动", performanceSignals: "走势状态", earlierSignals: "更早历史记录", candidates: "指标候选", markets: "蓄势观察", shadow: "前向影子", lines: "趋势线突破" };
      $("error-notice").textContent = `${errors.map(([key, error]) => `${names[key] || key}：${error}`).join("；")}。${state.lastSync ? "当前保留上次成功获取的数据，" : ""}15 秒后自动重试。`;
    }
  }
  function queueRefresh(trigger) {
    state.refreshQueued = trigger === "manual" ? "manual" : (state.refreshQueued || trigger);
  }
  function ledgerPath() {
    const pairs = { source: signalQuerySource(), confirmation: state.signalScope === "confirmed" ? "yolo" : "raw",
      period: state.period, outcome: state.outcome, sort: state.sort,
      offset: state.page * SIGNAL_PAGE_SIZE, limit: SIGNAL_PAGE_SIZE, search: state.search };
    if (state.timeframe !== "all") pairs.timeframe = apiTimeframe(state.timeframe);
    if (state.side !== "all") pairs.side = state.side;
    return "/api/signals?view=ledger&" + Object.entries(pairs).map(([k, v]) => `${k}=${encodeURIComponent(v)}`).join("&");
  }
  async function refresh(trigger = "manual") {
    if (state.view === "ashare") { await window.SpikeAshare.load(); return; }
    if (state.view === "shadow") { await loadShadow(); return; }
    if (state.syncing) { queueRefresh(trigger); return; }
    const revision = state.signalQueryRevision, view = state.view, source = signalQuerySource(), key = sourceKey();
    state.syncing = true;
    $("refresh-button").disabled = true;
    $("refresh-button").classList.add("loading");
    try {
      const requests = [{ key: "status", path: "/api/status" }];
      if (signalView()) requests.push({ key, path: ledgerPath() });
      const results = await Promise.allSettled(requests.map((request) => api(request.path)));
      if (revision !== state.signalQueryRevision || view !== state.view || source !== signalQuerySource() || key !== sourceKey()) return;
      results.forEach((result, index) => {
        const current = requests[index].key;
        if (result.status === "rejected") { state.errors[current] = result.reason.message || "请求失败"; return; }
        const data = result.value;
        if (current === "status") { state.status = data; state.statusReceivedAt = Date.now(); }
        else {
          if (!Array.isArray(data.items) || !data.stats || !Array.isArray(data.by_timeframe)) {
            state.errors[current] = "统计数据格式有误"; return;
          }
          state.ledger = data;
          state[current] = data.items.map((item) => normalizeV1Event(item, source)).filter(Boolean);
          state[`${current}Loaded`] = true;
          if (current === "directSignals") state.directSignalTotal = data.total;
          else state.signalTotal = data.total;
          // A dataset shrinking on refresh should return to a valid page.
          if (!data.items.length && state.page > 0) { state.page = 0; queueRefresh("manual"); }
        }
        delete state.errors[current];
        state.lastSync = Date.now();
      });
      renderErrors(); renderStatus(); renderSignals(); renderWatch();
      $("last-sync").textContent = state.errors[key] ? "同步失败 · 保留缓存" : `同步 ${clockTime(state.lastSync)}`;
      if (state.view === "system" && $("health-json").closest("details").open) loadHealth();
    } finally {
      state.syncing = false;
      $("refresh-button").disabled = false;
      $("refresh-button").classList.remove("loading");
      $("load-more-signals").disabled = false;
      $("load-earlier-signals").disabled = false;
      if (state.refreshQueued) {
        const queued = state.refreshQueued; state.refreshQueued = null; refresh(queued);
      }
    }
  }
  async function loadMarkets() {
    // The overview is explicitly user-entered and cannot overlap itself.  It
    // is not part of the 15-second signal refresh, preventing abandoned pages
    // from piling synchronous market reads onto the API process.
    if (state.marketsLoaded || state.marketsLoading) return;
    state.marketsLoading = true;
    renderWatch();
    try {
      const result = await api("/api/markets");
      if (!Array.isArray(result.items)) throw new Error("服务返回的数据格式有误");
      state.markets = result.items.filter((item) => item && typeof item === "object" && item.symbol && item.timeframe);
      state.marketsLoaded = true;
      delete state.errors.markets;
    } catch (error) {
      state.errors.markets = error.message || "请求失败";
      // A legacy summary backfill returns 503 instead of an empty success.
      // Retry one serialized request only while the user remains in Watch.
      if (!state.marketsRetryTimer && state.view === "watch") {
        state.marketsRetryTimer = window.setTimeout(() => {
          state.marketsRetryTimer = null;
          if (state.view === "watch" && !state.marketsLoaded) loadMarkets();
        }, 3000);
      }
    } finally {
      state.marketsLoading = false;
      renderErrors(); renderWatch();
    }
  }

  function loadEarlierRawSignals() {
    if (state.page > 0 && !state.syncing) { state.page--; invalidateSignalQuery(); refresh(); }
  }

  function redact(value) {
    if (Array.isArray(value)) return value.map(redact);
    if (value && typeof value === "object") return Object.fromEntries(Object.entries(value).map(([key, item]) => [key, /token|secret|password|api.?key|chat.?id|authorization|device.?key|(?:bark|push|server).?(?:url|address)|endpoint|^url$/i.test(key) ? "[已隐藏]" : redact(item)]));
    return value;
  }
  async function loadHealth() {
    try { state.health = await api("/api/health"); $("health-json").textContent = JSON.stringify(redact(state.health), null, 2); }
    catch (error) { $("health-json").textContent = `诊断信息暂时不可用：${error.message}`; }
  }
  document.querySelectorAll("[data-view]").forEach((button) => button.addEventListener("click", () => setView(button.dataset.view)));
  document.querySelectorAll("[data-timeframe]").forEach((button) => button.addEventListener("click", () => {
    state.timeframe = button.dataset.timeframe; state.page = 0;
    state.rowLimit = 24;
    document.querySelectorAll("[data-timeframe]").forEach((other) => { const selected = other === button; other.classList.toggle("selected", selected); other.setAttribute("aria-pressed", String(selected)); });
    invalidateSignalQuery();
    refresh();
  }));
  document.querySelectorAll("[data-signal-scope]").forEach((button) => button.addEventListener("click", () => {
    state.signalScope = button.dataset.signalScope; state.rowLimit = 24; state.page = 0;
    document.querySelectorAll("[data-signal-scope]").forEach((other) => { const selected = other === button; other.classList.toggle("selected", selected); other.setAttribute("aria-pressed", String(selected)); });
    invalidateSignalQuery(); refresh();
  }));
  let searchTimer;
  $("symbol-search").addEventListener("input", (event) => {
    state.search = event.target.value; state.page = 0;
    invalidateSignalQuery(); clearTimeout(searchTimer);
    searchTimer = setTimeout(() => refresh(), 250);
  });
  document.querySelectorAll("[data-ledger-period]").forEach((button) => button.addEventListener("click", () => {
    state.period = button.dataset.ledgerPeriod;
    document.querySelectorAll("[data-ledger-period]").forEach((other) => { const selected = other === button; other.classList.toggle("selected", selected); other.setAttribute("aria-pressed", String(selected)); });
    applySignalFilters();
  }));
  $("outcome-filter").addEventListener("change", (event) => { state.outcome = event.target.value; applySignalFilters(); });
  $("sort-filter").addEventListener("change", (event) => { state.sort = event.target.value; applySignalFilters(); });
  $("watch-search").addEventListener("input", (event) => { state.watchSearch = event.target.value; state.watchLimit = 24; renderWatch(); });
  document.querySelectorAll("[data-watch-scope]").forEach((button) => button.addEventListener("click", () => {
    state.watchScope = button.dataset.watchScope; state.watchLimit = 24;
    document.querySelectorAll("[data-watch-scope]").forEach((other) => { const selected = other === button; other.classList.toggle("selected", selected); other.setAttribute("aria-pressed", String(selected)); });
    renderWatch();
  }));
  document.querySelectorAll("[data-watch-timeframe]").forEach((button) => button.addEventListener("click", () => {
    state.watchTimeframe = button.dataset.watchTimeframe; state.watchLimit = 24;
    document.querySelectorAll("[data-watch-timeframe]").forEach((other) => { const selected = other === button; other.classList.toggle("selected", selected); other.setAttribute("aria-pressed", String(selected)); });
    renderWatch();
  }));
  document.querySelectorAll("[data-shadow-timeframe]").forEach((button) => button.addEventListener("click", () => {
    state.shadowTimeframe = button.dataset.shadowTimeframe;
    document.querySelectorAll("[data-shadow-timeframe]").forEach((other) => { const selected = other === button; other.classList.toggle("selected", selected); other.setAttribute("aria-pressed", String(selected)); });
    renderShadow();
  }));
  document.querySelectorAll("[data-signal-source]").forEach((button) => button.addEventListener("click", () => {
    state.signalSource = button.dataset.signalSource; state.page = 0;
    state.rowLimit = 24; invalidateSignalQuery();
    document.querySelectorAll("[data-signal-source]").forEach((other) => { const selected = other === button; other.classList.toggle("selected", selected); other.setAttribute("aria-pressed", String(selected)); });
    refresh();
  }));
  const sideFilter = $("side-filter");
  if (sideFilter) sideFilter.addEventListener("change", (event) => { state.side = event.target.value; state.rowLimit = 24; applySignalFilters(); });
  $("refresh-button").addEventListener("click", () => refresh());
  $("load-more-signals").addEventListener("click", () => {
    if (state.ledger?.has_more && !state.syncing) { state.page++; invalidateSignalQuery(); refresh(); }
  });
  $("load-earlier-signals").addEventListener("click", loadEarlierRawSignals);
  $("lines-timeframes").addEventListener("click", (event) => {
    const button = event.target.closest("[data-lines-timeframe]");
    if (!button) return;
    state.lines.timeframe = button.dataset.linesTimeframe; state.lines.limit = 24; renderLines(); reloadLinesLedger();
  });
  function reloadLinesLedger() {
    if (state.lines.kind !== "joint") return;
    state.lines.revision++;
    clearTimeout(state.lines.timer);
    state.lines.timer = setTimeout(() => { state.lines.loading = false; loadLines(); }, 250);
  }
  document.querySelectorAll("[data-lines-period]").forEach((button) => button.addEventListener("click", () => {
    state.lines.period = button.dataset.linesPeriod;
    document.querySelectorAll("[data-lines-period]").forEach((b) => { b.classList.toggle("selected", b === button); b.setAttribute("aria-pressed", String(b === button)); });
    state.lines.limit = 24; reloadLinesLedger();
  }));
  $("lines-outcome").addEventListener("change", (event) => { state.lines.outcome = event.target.value; state.lines.limit = 24; reloadLinesLedger(); });
  $("lines-sort").addEventListener("change", (event) => { state.lines.sort = event.target.value; reloadLinesLedger(); });
  $("lines-performance-version").addEventListener("change", (event) => {
    // Clear the prior projection while the new version loads; never label old
    // values as the newly selected version during an asynchronous request.
    changeLinesPerformanceVersion(event.target.value); reloadLinesLedger();
  });
  document.querySelectorAll("[data-lines-state]").forEach((button) => button.addEventListener("click", () => {
    state.lines.liveOnly = button.dataset.linesState === "live";
    document.querySelectorAll("[data-lines-state]").forEach((b) => { b.classList.toggle("selected", b === button); b.setAttribute("aria-pressed", String(b === button)); });
    state.lines.limit = 24; renderLines(); reloadLinesLedger();
  }));
  $("lines-search").addEventListener("input", (event) => { state.lines.search = event.target.value; state.lines.limit = 24; renderLines(); reloadLinesLedger(); });
  $("load-more-lines").addEventListener("click", () => { state.lines.limit += 24; renderLines(); });
  function activateRow(event, type) {
    const cardClass = type === "signal" ? ".signal-card" : type === "shadow" || type === "lines" ? ".shadow-event-card" : ".watch-card";
    const row = event.target.closest("[data-tradingview-action]") || event.target.closest(cardClass)?.querySelector("[data-tradingview-action]");
    if (!row) return;
    if (event.type === "keydown") {
      if (!["Enter", " "].includes(event.key)) return;
      event.preventDefault();
      if (event.repeat) return;
    }
    event.preventDefault();
    if (state.tradingViewPending) return;
    const item = type === "signal"
      ? sourceItems().find((candidate) => sameEvent(candidate, { id: row.dataset.signalId, kind: row.dataset.signalKind, symbol: row.dataset.tvSymbol, timeframe: row.dataset.tvTimeframe }))
      : type === "shadow"
        ? state.shadowEvents.find((candidate) => candidate.id === row.dataset.shadowId)
        : type === "lines"
          ? state.lines.items.find((candidate) => candidate.id === row.dataset.lineId)
          : state.markets.find((candidate) => candidate.symbol === row.dataset.tvSymbol && candidate.timeframe === row.dataset.tvTimeframe);
    if (!item) return;
    openTradingView(item);
  }
  ["click", "keydown"].forEach((eventType) => {
    $("signal-rows").addEventListener(eventType, (event) => activateRow(event, "signal"));
    $("watch-rows").addEventListener(eventType, (event) => activateRow(event, "market"));
    $("shadow-rows").addEventListener(eventType, (event) => activateRow(event, "shadow"));
    $("lines-rows").addEventListener(eventType, (event) => activateRow(event, "lines"));
  });
  $("load-more-watch").addEventListener("click", () => { state.watchLimit += 24; renderWatch(); });
  $("health-json").closest("details").addEventListener("toggle", (event) => { if (event.target.open) loadHealth(); });
  document.addEventListener("keydown", (event) => {
    if (event.key === "/" && !event.metaKey && !event.ctrlKey && !event.altKey && !["INPUT", "TEXTAREA", "SELECT"].includes(document.activeElement?.tagName)) {
      event.preventDefault();
      if (["system", "shadow", "ashare"].includes(state.view)) setView("signals");
      (state.view === "watch" ? $("watch-search") : linesView() ? $("lines-search") : $("symbol-search")).focus();
    }
  });
  window.addEventListener("hashchange", () => setView(location.hash.slice(1), false));
  document.addEventListener("visibilitychange", () => { if (!document.hidden) refresh("periodic"); });
  setView(location.hash.slice(1) || "signals", false);
  setInterval(() => { $("local-clock").textContent = clockTime(Date.now()); }, 1000);
  $("local-clock").textContent = clockTime(Date.now());
  if (state.view !== "warmup") refresh();
  setInterval(() => refresh("periodic"), 15000);
  loadLinesStatus();
  setInterval(() => { if (linesView()) loadLines(); else loadLinesStatus(); }, 15000);
})();
