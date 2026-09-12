/* Local monitor client. Market data is read-only; app opening requires a click. */
(() => {
  "use strict";

  const $ = (id) => document.getElementById(id);
  const state = {
    view: "signals", signals: [], directSignals: [], performanceSignals: [], rawYoloSignals: [], candidates: [], signalScope: "direct", markets: [], status: null, health: null,
    signalsLoaded: false, directSignalsLoaded: false, directSignalTotal: 0, candidatesLoaded: false, candidateTotal: 0, candidateCounts: null, marketsLoaded: false, marketsLoading: false, marketsRetryTimer: null, signalTotal: 0, rowLimit: 24, watchLimit: 24, search: "", watchSearch: "", watchScope: "building",
    rawNextCursor: null, rawHasMore: false, rawPaged: false, rawLoadingMore: false,
    timeframe: "all", watchTimeframe: "all", side: "all", signalSource: "live",
    syncing: false, refreshQueued: null, signalQueryRevision: 0, lastSync: null, statusReceivedAt: null, errors: {},
    tradingViewPending: false,
  };
  const titles = {
    signals: ["信号中心", "指标启动与 YOLO 确认分开展示。Bark 通知周期以运行状态为准。"],
    warmup: ["预热历史", "初次启动前的回算信号，仅供复盘，不触发通知。"],
    watch: ["蓄势观察", "还在横盘的，单独观察。这里的结构尚不是启动信号。"],
    system: ["运行状态", "行情、扫描与通知，每个环节都清晰可见。"],
  };
  const eventNames = { tv_start: "原始 V1 启动", yolo_confirmed: "YOLO 补充确认" };
  const modelStates = { pending: "等待确认", confirmed: "模型已通过", invalidated: "结构失效", expired: "等待已到期", error: "检测异常", disabled: "周期已关闭" };
  const TV_SETTINGS = "近零至少 12 根 · 0.1 ATR · 普通系统标记关闭";
  // Server cursor pages are intentionally smaller than its 2,000-row safety cap.
  // Cards need a browse path, not multi-megabyte concurrent JSON responses.
  const SIGNAL_PAGE_SIZE = 500;
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
  // The monitor's current raw event kind is SPIKE V1.  Keep tv_start only for
  // already-persisted legacy chart rows; it is not the current backend kind.
  const isV1StartMarker = (event) => event?.kind === "spike_burst_v1" || event?.kind === "tv_start";
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
  const isDisplayOnly = (item) => displayOnlyTimeframes().includes(item?.timeframe);
  const notificationPolicy = () => {
    const muted = displayOnlyTimeframes(), bark = runtimeTimeframes("bark_timeframes");
    const mutedNote = muted.length ? `${muted.map(timeframeLabel).join(" / ")} ${DISPLAY_ONLY_NOTE}；` : "";
    const delivery = bark === null ? "Bark 通知周期尚未同步；以实际回执为准。" : !bark.length ? "当前所有周期的 Bark 推送均已关闭。" : twoStage() ? `${bark.map(timeframeLabel).join(" / ")} 收盘启动先推送 Bark，YOLO 通过后追加推送；历史箭头不补发。` : `${bark.map(timeframeLabel).join(" / ")} 仍按模型确认通知，分阶段通知规则尚未启用。`;
    return mutedNote + delivery;
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
    ["signals", "watch", "system"].forEach((key) => $(`${key}-view`).classList.toggle("hidden", key !== (signalView(state.view) ? "signals" : state.view)));
    document.querySelectorAll("[data-view]").forEach((button) => {
      const active = button.dataset.view === state.view;
      button.classList.toggle("active", active);
      if (active) button.setAttribute("aria-current", "page"); else button.removeAttribute("aria-current");
    });
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
      state.rowLimit = 24;
      invalidateSignalQuery();
      if (signalView(state.view)) refresh();
    }
    if (state.view === "system") loadHealth();
    // Signals never load the expensive market overview.  Watch opts in once.
    if (state.view === "watch") loadMarkets();
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
    if (!performance) return {
      className: "outcome-unknown", badge: "走势计算中", value: "—", valueLabel: "当前 R",
      peak: "—", stop: finite(originalSignal(item)?.initial_stop) ? price(originalSignal(item).initial_stop) : "—",
      stopLabel: "初始 SL", note: "等待已收盘行情更新",
    };
    const status = String(performance.status || "active");
    const outcomeR = status === "active" ? performance.current_r : performance.exit_r ?? performance.current_r;
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
    const items = filteredSignals();
    const confirmed = state.signalScope === "confirmed", direct = state.signalScope === "direct", warmup = state.view === "warmup", notifying = !warmup && (confirmed || direct);
    const loaded = state[`${sourceKey()}Loaded`];
    const fetchError = state.errors[sourceKey()];
    const source = sourceItems();
    const hasEarlierPage = state.signalScope === "direct" && state.rawHasMore;
    $("filtered-count").textContent = `${items.length} 条${hasEarlierPage ? "（当前页）" : ""}`;
    $("filtered-count").title = hasEarlierPage
      ? `当前页 ${number(source.length)} 条；可直接读取更早记录`
      : `当前已加载 ${number(source.length)} 条记录`;
    $("signal-section-title").textContent = confirmed ? "YOLO 补充确认" : "原始 V1 启动";
    $("signal-scope-note").textContent = warmup
      ? "初次启动前的回算信号，仅供复盘，不触发通知。"
      : state.signalSource === "replay"
      ? "历史回放只展示已记录事件与之后的真实行情；不触发、也不暗示通知。"
      : confirmed ? "YOLO 是原始 V1 之后的补充确认，不是启动门；两类实时记录各自以服务回执为准。" : "原版 V1 的冻结监控协议只生成多头启动；卡片 R 按信号收盘参考路径持续更新。";
    $("signal-window-note").textContent = loaded
      ? state.signalScope === "direct" && state.rawHasMore
        ? `已加载 ${number(source.length)} 条；可继续读取更早记录`
        : `已加载 ${number(source.length)} 条`
      : "最近 2,000 条 · 每 15 秒同步";
    const candidateCount = $("candidate-count");
    if (candidateCount) { candidateCount.textContent = state.candidatesLoaded ? number(state.candidateCounts ? numeric(state.candidateCounts.pending) + numeric(state.candidateCounts.error) : state.candidates.filter((item) => ["pending", "error"].includes(item.model?.status)).length) : "—"; candidateCount.title = "候选状态仅在兼容旧服务时显示"; }
    $("load-more-signals").classList.toggle("hidden", items.length <= state.rowLimit);
    $("load-more-signals").disabled = false;
    $("load-more-signals").textContent = `显示更多（${Math.min(state.rowLimit, items.length)} / ${items.length}）`;
    $("load-earlier-signals").classList.toggle("hidden", !hasEarlierPage);
    $("load-earlier-signals").disabled = state.rawLoadingMore;
    $("load-earlier-signals").textContent = state.rawLoadingMore ? "正在读取更早记录…" : `加载更早记录（再取最多 ${SIGNAL_PAGE_SIZE.toLocaleString("zh-CN")} 条）`;
    $("signal-empty").classList.toggle("hidden", items.length > 0);
    if (!items.length) {
      const hasFilters = state.search || state.timeframe !== "all" || state.side !== "all";
      if (fetchError && !loaded) {
        $("signal-empty-title").textContent = "信号服务暂时不可用";
        $("signal-empty-description").textContent = "正在自动重试。连接恢复后会展示真实记录。";
      } else if (!loaded) {
        $("signal-empty-title").textContent = warmup ? "正在读取预热历史" : state.signalSource === "replay" ? "正在读取历史回放" : "正在连接实时信号服务";
        $("signal-empty-description").textContent = "真实记录会在这里出现。";
      } else {
        $("signal-empty-title").textContent = hasFilters ? "没有符合筛选的记录" : warmup ? "尚无预热回算记录" : state.signalSource === "replay" ? "尚无导入的历史回放记录" : confirmed ? "等待 YOLO 补充确认" : "等待新的收盘启动";
        $("signal-empty-description").textContent = hasFilters ? "试试其他合约或周期。" : warmup ? "初次启动前的回算信号会在这里单独显示，仅供复盘。" : state.signalSource === "replay" ? "回放数据导入后会在这里单独显示，不会被当作实时通知。" : confirmed ? "YOLO 确认会与原始 V1 启动分开显示。" : "只显示新的已收盘 V1 启动。";
      }
    }
    const focused = document.activeElement?.dataset;
    const focusedId = focused?.signalId, focusedKind = focused?.signalKind;
    const now = signalClock();
    const fresh = notifying ? items.filter((item) => isFresh(item, now)) : [];
    const earlier = items.filter((item) => !notifying || !isFresh(item, now));
    const visible = [...fresh, ...earlier].slice(0, state.rowLimit);
    const freshVisible = visible.filter((item) => notifying && isFresh(item, now));
    const earlierVisible = visible.filter((item) => !notifying || !isFresh(item, now));
    const minutes = state.status?.runtime?.fresh_minutes;
    const clockLabel = direct ? "箭头收盘" : "模型确认";
    const group = (heading, list, recent) => list.length ? `<div class="signal-group-heading${recent ? " fresh-heading" : ""}"><h3>${heading}<span class="group-count">${list.length}</span></h3><span>${recent ? `${clockLabel}后 ${escapeHTML(number(minutes))} 分钟内` : confirmed ? "按模型确认时间排列" : "按原箭头时间排列"}</span></div><div class="signal-card-grid">${list.map((item) => signalCardHTML(item, now)).join("")}</div>` : "";
    const freshnessKnown = (direct ? twoStage() : true) && finite(minutes) && Number(minutes) > 0 && finite(state.status?.now_ms) && finite(state.statusReceivedAt);
    const pendingFreshness = fetchError || state.errors.status || !freshnessKnown;
    const noFresh = notifying && !fresh.length && items.length ? `<div id="fresh-empty" class="fresh-empty"><strong>${pendingFreshness ? "新鲜状态待同步" : direct ? "当前筛选下暂无新鲜启动" : "当前筛选下暂无新鲜确认"}</strong><span>${pendingFreshness ? "保留已获取的记录，状态同步后重新确认时效。" : `${clockLabel} ${escapeHTML(number(minutes))} 分钟内的信号会优先出现在这里。下方可回看此前记录。`}</span></div>` : "";
    $("signal-rows").innerHTML = noFresh + group(direct ? "新鲜启动" : "新鲜确认", freshVisible, true) + group(warmup ? confirmed ? "预热 YOLO 补充确认" : "预热原始 V1 启动" : confirmed ? fresh.length ? "更早确认" : "已记录确认" : direct ? "已记录启动" : "指标候选 · 模型等待状态", earlierVisible, false);
    $("signal-footer-note").textContent = warmup ? "预热回算 · 不触发通知 · 北京时间" : "已收盘确认 · 北京时间";
    renderTradingViewButtons();
    if (focusedId) Array.from($("signal-rows").querySelectorAll("[data-signal-id]")).find((card) => card.dataset.signalId === focusedId && card.dataset.signalKind === focusedKind && card.dataset.tvSymbol === focused.tvSymbol && card.dataset.tvTimeframe === focused.tvTimeframe)?.focus({ preventScroll: true });
  }
  function signalCardHTML(item, now = signalClock()) {
    const confirmed = isConfirmed(item), original = originalSignal(item), direct = directReceipt(item);
    const side = item.side === "short" ? "short" : item.side === "long" ? "long" : "neutral";
    const venue = String(item.venue || "OKX").toUpperCase();
    const fresh = isFresh(item, now);
    const status = confirmed ? "YOLO 补充确认" : "原始 V1 启动", caption = "信号收盘价";
    const outcome = performanceView(item);
    return `<article class="signal-card ${side} ${outcome.className}${confirmed || direct ? "" : " candidate-card"}${fresh ? " is-fresh" : ""}"><button type="button" class="card-primary-action" data-signal-id="${escapeHTML(item.id)}" data-signal-kind="${escapeHTML(item.kind)}" data-tradingview-action="signal" data-tv-symbol="${escapeHTML(item.symbol)}" data-tv-timeframe="${escapeHTML(item.timeframe)}" title="点击整张卡片，在本机 TradingView 打开" aria-label="${escapeHTML(`${venue} ${shortSymbol(item.symbol)} ${quoteSymbol(item.symbol)} ${timeframeLabel(item.timeframe)} ${sideName(item.side)}，${status}，${caption} ${price(item.price)}，${outcome.badge}，${shortDate(item.bar_close_ms)}，在本机 TradingView 打开`)}"></button>
      <span class="signal-card-top"><span class="card-symbol"><strong>${escapeHTML(shortSymbol(item.symbol))}</strong><small>${escapeHTML(venue)} · ${escapeHTML(quoteSymbol(item.symbol))} 永续</small></span><span class="card-timeframe">${escapeHTML(timeframeLabel(item.timeframe))}</span></span>
      <span class="signal-card-direction"><span class="card-direction">${sideArrow(item.side)} ${sideName(item.side)}${confirmed ? " · 确认" : " · 启动"}</span><span class="card-recency">${isWarmupRecord(item) ? "预热历史" : item.source === "replay" ? "历史回放" : fresh ? "新 · " + ageLabel(item.bar_close_ms) : ageLabel(item.bar_close_ms)}</span></span>
      <span class="model-card-status"><span class="model-badge ${confirmed ? "confirmed" : "pending"}">${escapeHTML(status)}</span><span>${isWarmupRecord(item) ? "预热回算 · 不通知" : item.source === "replay" ? "回放记录 · 不通知" : confirmed ? "补充确认 · 非启动门" : "第一阶段 · 已收盘"}</span></span>
      <span class="card-price-label">${caption}</span><span class="card-price">${escapeHTML(price(item.price))}</span>
      ${confirmed && item.indicator ? `<span class="card-origin">原始 V1 ${escapeHTML(price(original.price))} · ${escapeHTML(shortDate(original.bar_close_ms))}</span>` : ""}
      <span class="performance-badge">${escapeHTML(outcome.badge)}</span>
      <dl class="card-performance"><div><dt>${escapeHTML(outcome.valueLabel)}</dt><dd>${escapeHTML(outcome.value)}</dd></div><div><dt>最高 R</dt><dd>${escapeHTML(outcome.peak)}</dd></div><div><dt>${escapeHTML(outcome.stopLabel)}</dt><dd>${escapeHTML(outcome.stop)}</dd></div></dl>
      <span class="performance-note">${escapeHTML(outcome.note)} · 信号收盘参考，并非账户实际成交</span>
      <span class="card-context"><span>信号 K 线</span><strong>${item.is_closed ? "已确认" : "待确认"}</strong></span>
      <span class="card-confirmed"><span>${isWarmupRecord(item) ? "回算信号 · 仅供复盘" : item.executable_entry_time ? item.source === "replay" ? `回放执行时钟 ${escapeHTML(shortDate(milliseconds(item.executable_entry_time)))}` : `实际进场 ${escapeHTML(shortDate(milliseconds(item.executable_entry_time)))}` : item.entry_reference === "next_open" ? "次开盘参考 · 等待实际成交" : "仅信号收盘参考"}</span><time title="${escapeHTML(fullDate(item.bar_close_ms))} 北京时间">${escapeHTML(shortDate(item.bar_close_ms))}</time></span>
      <span class="card-footer"><span class="notification-stack">${item.source === "replay" ? `<span class="candidate-notice">历史回放不通知</span>` : notificationHTML(item)}</span><span class="card-open" data-tradingview-label="整卡打开 TradingView ↗" aria-hidden="true">整卡打开 TradingView ↗</span></span>
    </article>`;
  }
  function applySignalFilters() {
    renderSignals();
  }
  function invalidateSignalQuery() {
    state.signalQueryRevision++;
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
    const gateNotice = !modelProtocol() ? "模型确认口径尚未同步，原始箭头不会显示为模型确认。" : gate.last_error ? `模型检测异常：${String(gate.last_error)}。${gateImpact}` : gate.status === "error" ? `部分候选检测异常，可在等待确认中查看。${gateImpact}` : gateIdle ? `YOLO 待命；当前没有合格原始 V1 候选，出现候选时才加载模型。${gateImpact}` : gate.loaded !== true ? `YOLO 模型正在加载；原始 V1 启动不受影响。${gateImpact}` : "";
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
      const names = { status: "运行状态", signals: "模型确认", directSignals: "指标启动", performanceSignals: "走势状态", earlierSignals: "更早历史记录", candidates: "指标候选", markets: "蓄势观察" };
      $("error-notice").textContent = `${errors.map(([key, error]) => `${names[key] || key}：${error}`).join("；")}。${state.lastSync ? "当前保留上次成功获取的数据，" : ""}15 秒后自动重试。`;
    }
  }
  function shouldRefreshSignalList(trigger) {
    // Replay records are immutable journal entries.  After their visible
    // family has loaded, the 15-second clock only needs the lightweight
    // runtime status; re-fetching the same 500 cards can overlap chart work.
    return trigger !== "periodic" || !["replay", "warmup"].includes(signalQuerySource()) || !state[`${sourceKey()}Loaded`];
  }
  function queueRefresh(trigger) {
    // A user action or changed query must win over an automatically queued
    // status tick, so a source/timeframe switch cannot leave an empty list.
    state.refreshQueued = trigger === "manual" ? "manual" : (state.refreshQueued || trigger);
  }
  async function refresh(trigger = "manual") {
    // Keep cursor pages serialized with the periodic top-page refresh.  An
    // aborted client fetch does not cancel the synchronous server work.
    if (state.rawLoadingMore) { queueRefresh(trigger); return; }
    if (state.syncing) { queueRefresh(trigger); return; }
    const queryRevision = state.signalQueryRevision;
    const querySource = signalQuerySource();
    const queryView = state.view;
    const queryTimeframe = state.timeframe;
    const queryScope = state.signalScope;
    state.syncing = true;
    $("refresh-button").disabled = true;
    $("refresh-button").classList.add("loading");
    try {
      const source = encodeURIComponent(querySource);
      const timeframe = queryTimeframe === "all" ? "" : `&timeframe=${encodeURIComponent(apiTimeframe(queryTimeframe) || "")}`;
      // Fetch only the visible signal family.  Replay imports are raw-only;
      // repeatedly asking for hidden YOLO variants wastes a large response
      // budget while the reader is paging historical V1 starts.
      const requests = [{ key: "status", path: "/api/status" }];
      if (shouldRefreshSignalList(trigger) && queryScope === "confirmed") {
        requests.push({ key: "signals", path: `/api/signals?limit=${SIGNAL_PAGE_SIZE}&source=${source}&confirmation=yolo${timeframe}` });
        if (["live", "warmup"].includes(querySource)) requests.push({ key: "rawYoloSignals", path: `/api/signals?limit=${SIGNAL_PAGE_SIZE}&source=${source}&confirmation=raw_yolo${timeframe}` });
        // A YOLO event embeds the original signal as it looked at confirmation
        // time. Load the current raw read-model separately so its R/SL keeps
        // moving after source/timeframe switches without turning it into a
        // notification receipt or a visible raw-card family.
        if (["live", "warmup"].includes(querySource)) requests.push({ key: "performanceSignals", path: `/api/signals?limit=${SIGNAL_PAGE_SIZE}&source=${source}&confirmation=raw${timeframe}` });
      } else if (shouldRefreshSignalList(trigger) && queryScope === "direct") {
        requests.push({ key: "directSignals", path: `/api/signals?limit=${SIGNAL_PAGE_SIZE}&source=${source}&confirmation=raw${timeframe}` });
        if (["live", "warmup"].includes(querySource)) requests.push({ key: "rawYoloSignals", path: `/api/signals?limit=${SIGNAL_PAGE_SIZE}&source=${source}&confirmation=raw_yolo${timeframe}` });
      }
      const results = await Promise.allSettled(requests.map((request) => api(request.path)));
      if (queryRevision !== state.signalQueryRevision || querySource !== signalQuerySource() || queryView !== state.view || queryTimeframe !== state.timeframe || queryScope !== state.signalScope) return;
      const keys = requests.map((request) => request.key);
      let anySuccess = false;
      results.forEach((result, index) => {
        const key = keys[index];
        if (result.status === "rejected") { state.errors[key] = result.reason.message || "请求失败"; return; }
        if (key !== "status" && !Array.isArray(result.value.items)) { state.errors[key] = "服务返回的数据格式有误"; return; }
        delete state.errors[key];
        anySuccess = true;
        if (key === "status") { state.status = result.value; state.statusReceivedAt = Date.now(); }
        else {
          const items = result.value.items.filter((item) => item && typeof item === "object" && item.symbol).map((item) => normalizeV1Event(item, querySource))
            .filter((item) => key === "signals" ? isConfirmed(item) : ["directSignals", "performanceSignals"].includes(key) ? isDirectRecord(item) : Boolean(item));
          if (key === "directSignals" && state.rawPaged) {
            state.directSignals = [...items, ...state.directSignals].filter((item, index, rows) => rows.findIndex((other) => sameEvent(other, item)) === index);
          } else state[key] = items;
          state[`${key}Loaded`] = true;
          if (key === "signals") state.signalTotal = numeric(result.value.total, state.signals.length);
          if (key === "directSignals") {
            state.directSignalTotal = state.directSignals.length;
            if (!state.rawPaged) {
              state.rawNextCursor = result.value.next_cursor || null;
              state.rawHasMore = Boolean(state.rawNextCursor);
            }
          }
          if (key !== "markets") state[key].sort((a, b) => numeric(b.bar_close_ms) - numeric(a.bar_close_ms) || numeric(b.detected_at_ms) - numeric(a.detected_at_ms));
        }
      });
      // raw_yolo is an explicit API confirmation value. It belongs in both views,
      // while keeping raw and YOLO endpoint records otherwise independent.
      const rawYolo = state.rawYoloSignals.filter((item) => item?.confirmation === "raw_yolo");
      const appendUnique = (items, additional) => [...items, ...additional].filter((item, index, list) => list.findIndex((other) => sameEvent(other, item)) === index);
      state.signals = appendUnique(state.signals, rawYolo);
      state.directSignals = appendUnique(state.directSignals, rawYolo);
      const resultFor = (key) => results[keys.indexOf(key)];
      const rawYoloResult = resultFor("rawYoloSignals");
      const rawYoloTotal = numeric(rawYoloResult?.status === "fulfilled" ? rawYoloResult.value.total : 0);
      const yoloResult = resultFor("signals");
      const rawResult = resultFor("directSignals");
      if (yoloResult?.status === "fulfilled") state.signalTotal = numeric(yoloResult.value.total, state.signals.length) + rawYoloTotal;
      if (rawResult?.status === "fulfilled") state.directSignalTotal = numeric(rawResult.value.total, state.directSignals.length) + rawYoloTotal;
      if (anySuccess) state.lastSync = Date.now();
      renderErrors(); renderStatus(); renderSignals(); renderWatch();
      $("last-sync").textContent = state.errors[sourceKey()] ? "同步失败 · 保留缓存" : `同步 ${clockTime(state.lastSync)}`;
      if (state.view === "system" && $("health-json").closest("details").open) loadHealth();
    } finally {
      state.syncing = false;
      $("refresh-button").disabled = false;
      $("refresh-button").classList.remove("loading");
      if (state.refreshQueued) {
        const queuedTrigger = state.refreshQueued;
        state.refreshQueued = null;
        refresh(queuedTrigger);
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

  async function loadEarlierRawSignals() {
    if (state.rawLoadingMore || !state.rawHasMore || !state.rawNextCursor || state.signalScope !== "direct") return;
    const queryRevision = state.signalQueryRevision;
    const querySource = signalQuerySource();
    const queryView = state.view;
    const queryTimeframe = state.timeframe;
    const cursor = state.rawNextCursor;
    state.rawLoadingMore = true;
    renderSignals();
    try {
      const timeframe = queryTimeframe === "all" ? "" : `&timeframe=${encodeURIComponent(apiTimeframe(queryTimeframe) || "")}`;
      const path = `/api/signals?limit=${SIGNAL_PAGE_SIZE}&source=${encodeURIComponent(querySource)}&confirmation=raw${timeframe}`
        + `&before_close_ms=${encodeURIComponent(cursor.close_ms)}&before_id=${encodeURIComponent(cursor.event_id)}`;
      const result = await api(path);
      if (queryRevision !== state.signalQueryRevision || querySource !== signalQuerySource() || queryView !== state.view || queryTimeframe !== state.timeframe) return;
      if (!Array.isArray(result.items)) throw new Error("服务返回的数据格式有误");
      const older = result.items.filter((item) => item && typeof item === "object" && item.symbol)
        .map((item) => normalizeV1Event(item, querySource)).filter(isDirectRecord);
      const existing = state.directSignals;
      state.directSignals = [...existing, ...older].filter((item, index, rows) => rows.findIndex((other) => sameEvent(other, item)) === index)
        .sort((a, b) => numeric(b.bar_close_ms) - numeric(a.bar_close_ms) || String(b.id).localeCompare(String(a.id)));
      state.directSignalTotal = state.directSignals.length;
      state.rawNextCursor = result.next_cursor || null;
      state.rawHasMore = Boolean(state.rawNextCursor);
      state.rawPaged = true;
      delete state.errors.earlierSignals;
    } catch (error) {
      // Keep pagination failures separate: a succeeding top-page refresh must
      // not erase the reason the cursor page stayed at its prior boundary.
      state.errors.earlierSignals = error.message || "请求失败";
    } finally {
      state.rawLoadingMore = false;
      renderErrors(); renderSignals();
      if (state.refreshQueued) {
        const queuedTrigger = state.refreshQueued;
        state.refreshQueued = null;
        refresh(queuedTrigger);
      }
    }
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
    state.timeframe = button.dataset.timeframe;
    state.rowLimit = 24;
    document.querySelectorAll("[data-timeframe]").forEach((other) => { const selected = other === button; other.classList.toggle("selected", selected); other.setAttribute("aria-pressed", String(selected)); });
    invalidateSignalQuery();
    refresh();
  }));
  document.querySelectorAll("[data-signal-scope]").forEach((button) => button.addEventListener("click", () => {
    state.signalScope = button.dataset.signalScope; state.rowLimit = 24;
    document.querySelectorAll("[data-signal-scope]").forEach((other) => { const selected = other === button; other.classList.toggle("selected", selected); other.setAttribute("aria-pressed", String(selected)); });
    // Hidden families are fetched only when the reader actually switches to them.
    if (!state[`${sourceKey()}Loaded`]) refresh(); else applySignalFilters();
  }));
  $("symbol-search").addEventListener("input", (event) => { state.search = event.target.value; state.rowLimit = 24; applySignalFilters(); });
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
  document.querySelectorAll("[data-signal-source]").forEach((button) => button.addEventListener("click", () => {
    state.signalSource = button.dataset.signalSource;
    state.rowLimit = 24; invalidateSignalQuery();
    document.querySelectorAll("[data-signal-source]").forEach((other) => { const selected = other === button; other.classList.toggle("selected", selected); other.setAttribute("aria-pressed", String(selected)); });
    refresh();
  }));
  const sideFilter = $("side-filter");
  if (sideFilter) sideFilter.addEventListener("change", (event) => { state.side = event.target.value; state.rowLimit = 24; applySignalFilters(); });
  $("refresh-button").addEventListener("click", () => refresh());
  $("load-more-signals").addEventListener("click", () => {
    if (state.rowLimit < sourceItems().length) { state.rowLimit += 24; renderSignals(); }
  });
  $("load-earlier-signals").addEventListener("click", loadEarlierRawSignals);
  function activateRow(event, type) {
    const row = event.target.closest("[data-tradingview-action]") || event.target.closest(type === "signal" ? ".signal-card" : ".watch-card")?.querySelector("[data-tradingview-action]");
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
      : state.markets.find((candidate) => candidate.symbol === row.dataset.tvSymbol && candidate.timeframe === row.dataset.tvTimeframe);
    if (!item) return;
    openTradingView(item);
  }
  ["click", "keydown"].forEach((eventType) => {
    $("signal-rows").addEventListener(eventType, (event) => activateRow(event, "signal"));
    $("watch-rows").addEventListener(eventType, (event) => activateRow(event, "market"));
  });
  $("load-more-watch").addEventListener("click", () => { state.watchLimit += 24; renderWatch(); });
  $("health-json").closest("details").addEventListener("toggle", (event) => { if (event.target.open) loadHealth(); });
  document.addEventListener("keydown", (event) => {
    if (event.key === "/" && !event.metaKey && !event.ctrlKey && !event.altKey && !["INPUT", "TEXTAREA", "SELECT"].includes(document.activeElement?.tagName)) {
      event.preventDefault();
      if (state.view === "system") setView("signals");
      (state.view === "watch" ? $("watch-search") : $("symbol-search")).focus();
    }
  });
  window.addEventListener("hashchange", () => setView(location.hash.slice(1), false));
  document.addEventListener("visibilitychange", () => { if (!document.hidden) refresh("periodic"); });
  setView(location.hash.slice(1) || "signals", false);
  setInterval(() => { $("local-clock").textContent = clockTime(Date.now()); }, 1000);
  $("local-clock").textContent = clockTime(Date.now());
  if (state.view !== "warmup") refresh();
  setInterval(() => refresh("periodic"), 15000);
})();
