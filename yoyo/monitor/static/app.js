/* Local monitor client. Market data is read-only; app opening requires a click. */
(() => {
  "use strict";

  const $ = (id) => document.getElementById(id);
  const state = {
    view: "signals", signals: [], directSignals: [], rawYoloSignals: [], candidates: [], signalScope: "direct", markets: [], status: null, health: null,
    signalsLoaded: false, directSignalsLoaded: false, directSignalTotal: 0, candidatesLoaded: false, candidateTotal: 0, candidateCounts: null, marketsLoaded: false, signalTotal: 0, rowLimit: 24, watchLimit: 24, search: "", watchSearch: "", watchScope: "building",
    rawNextCursor: null, rawHasMore: false, rawPaged: false, rawLoadingMore: false,
    timeframe: "all", watchTimeframe: "all", side: "long", signalSource: "live", selected: null,
    chartKey: null, chart: null, chartRequest: 0, chartController: null, chartExpanded: false, chartViewport: null,
    syncing: false, refreshQueued: false, signalQueryRevision: 0, lastSync: null, statusReceivedAt: null, errors: {}, chartHover: null, detailOrigin: "signals",
    tradingViewPending: false,
  };
  const titles = {
    signals: ["信号中心", "指标启动与 YOLO 确认分开展示。Bark 通知周期以运行状态为准。"],
    watch: ["蓄势观察", "还在横盘的，单独观察。这里的结构尚不是启动信号。"],
    system: ["运行状态", "行情、扫描与通知，每个环节都清晰可见。"],
  };
  const eventNames = { tv_start: "原始 V1 启动", yolo_confirmed: "YOLO 补充确认" };
  const modelStates = { pending: "等待确认", confirmed: "模型已通过", invalidated: "结构失效", expired: "等待已到期", error: "检测异常", disabled: "周期已关闭" };
  const TV_SETTINGS = "近零至少 12 根 · 0.1 ATR · 普通系统标记关闭";
  // Server cursor pages are intentionally smaller than its 2,000-row safety cap.
  // Cards need a browse path, not multi-megabyte concurrent JSON responses.
  const SIGNAL_PAGE_SIZE = 500;
  const TV_INTERVALS = new Map([["30", "30"], ["60", "60"], ["240", "240"]]);
  const apiTimeframe = (value) => ({ "30": "30m", "60": "1H", "240": "4H", 30: "30m", 60: "1H", 240: "4H" }[value] || null);
  const uiTimeframe = (value) => ({ "30m": "30", "1H": "60", "4H": "240", "30": "30", "60": "60", "240": "240", 30: "30", 60: "60", 240: "240" }[value] || null);
  const timeframeLabel = (value) => ({ "30": "30m", "60": "1H", "240": "4H", 30: "30m", 60: "1H", 240: "4H" }[value] || value || "—");
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
  const isDirectRecord = (item) => isCandidate(item) && TV_INTERVALS.has(String(item.timeframe));
  const sourceName = (item) => item?.source === "replay" ? "历史回放" : "实时";
  const milliseconds = (value) => typeof value === "string" ? Date.parse(value) : Number(value);
  function normalizeV1Event(row) {
    const minutes = Number(row.timeframe_min ?? row.timeframe);
    const confirmation = row.confirmation;
    const close = milliseconds(row.signal_close_time ?? row.bar_close_ms);
    const direction = row.direction ?? row.side;
    if (![30, 60, 240].includes(minutes) || !["live", "replay"].includes(row.source) || !["raw", "yolo", "raw_yolo"].includes(confirmation) || direction !== "long" || !finite(close)) return null;
    return { ...row, id: String(row.id ?? `${row.source}|${confirmation}|${row.venue}|${row.symbol}|${minutes}|${row.signal_close_time}`),
      source: row.source === "replay" ? "replay" : "live", confirmation, timeframe: String(minutes), timeframe_min: minutes,
      kind: confirmation === "raw" ? "tv_start" : "yolo_confirmed", side: "long", bar_close_ms: close,
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
  const sameSelection = (a, b) => sameEvent(a, b) && a.source === b.source && a.confirmation === b.confirmation && a.bar_close_ms === b.bar_close_ms;
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
  const replayExitReason = (value) => ({ protective_stop: "保护止损", target: "目标退出", timeout: "观察期结束" }[String(value)] || "未知回测退出");
  function coveredLedgerFacts(item) {
    // A replay receipt is an identity link to the covered ledger, never a
    // strategy verdict.  Keep censored rows out of every realized outcome.
    if (item?.source !== "replay") return [];
    const link = item.covered_ledger, outcome = link?.outcome;
    if (item.performance_status === "covered_linked_realized_unverified" && outcome?.status === "realized") {
      return [
        ["覆盖账本关联", "已关联 · 未独立收益审核", "model-color"],
        ["回测退出 · 北京时间", `${replayExitReason(outcome.exit_reason)} · ${shortDate(outcome.exit_time_ms)}`, ""],
        ["单笔净 R", finite(outcome.net_r) ? `${Number(outcome.net_r).toFixed(3)} R · 非账户收益，未独立核验` : "—", ""],
      ];
    }
    if (item.performance_status === "covered_linked_censored_unverified" && outcome?.status === "censored") {
      return [["覆盖账本关联", "已关联 · 样本结束时尚未退出", "model-color"],
              ["账本结果", "未实现；不计胜率、PF 或净收益", ""]];
    }
    if (item.performance_status === "stale_evidence_unverified" && link?.link_status === "stale_evidence") {
      return [["覆盖账本关联", "账本证据已过期 · 不展示收益", "muted"],
              ["账本结果", "等待新的不可变账本快照重链", ""]];
    }
    if (item.performance_status === "unverified" && link?.link_status === "ohlc_missing") {
      return [["覆盖账本关联", "缺少同源冻结 OHLC · 不展示收益", "muted"],
              ["账本结果", "该回放无法核验历史图，不使用其他交易所或当前行情替代", ""]];
    }
    return [["覆盖账本关联", "尚未关联 v2 覆盖账本 · 不展示收益", ""]];
  }
  function axisPrice(value) {
    if (!finite(value)) return "—";
    const n = Number(value), abs = Math.abs(n);
    const digits = abs >= 1000 ? 2 : abs >= 10 ? 3 : abs >= 1 ? 4 : Math.min(12, Math.max(5, -Math.floor(Math.log10(abs || 1)) + 3));
    return n.toLocaleString("en-US", { minimumFractionDigits: abs >= 1 ? 2 : 0, maximumFractionDigits: digits });
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
    return typeof item?.symbol === "string" && /^[A-Z0-9]{1,30}-[A-Z0-9]{2,10}-SWAP$/.test(item.symbol) && TV_INTERVALS.has(item.timeframe);
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
    const request = { symbol: item.symbol, timeframe: item.timeframe };
    const label = `${shortSymbol(request.symbol)} ${quoteSymbol(request.symbol)} · ${timeframeLabel(request.timeframe)}`;
    state.tradingViewPending = true;
    renderTradingViewButtons();
    tradingViewStatus(`正在请求 本机 TradingView 打开 ${label}…`, "pending");
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 30000);
    try {
      const response = await fetch("/api/tradingview/open", {
        method: "POST", cache: "no-store", signal: controller.signal,
        headers: { "Content-Type": "application/json", Accept: "application/json", "X-Spike-Action": "open-tradingview" },
        body: JSON.stringify(request),
      });
      const isJSON = (response.headers.get("content-type") || "").includes("json");
      const result = isJSON ? await response.json() : null;
      if (!response.ok) throw new Error(typeof result?.detail === "string" ? result.detail : `服务返回 HTTP ${response.status}`);
      if (result?.requested !== true || result.symbol !== request.symbol || result.timeframe !== request.timeframe) throw new Error("服务未返回有效的打开请求回执。");
      tradingViewStatus(`已请求 TradingView 打开 ${label}。`, "");
    } catch (error) {
      tradingViewStatus(error.name === "AbortError" ? "打开请求超时，尚无法确认结果；请先查看 TradingView。" : `无法请求 TradingView：${error.message || "连接失败"}`, "error");
    } finally {
      clearTimeout(timeout);
      state.tradingViewPending = false;
      renderTradingViewButtons();
    }
  }
  function setView(view, updateHash = true) {
    if (state.chartExpanded && view !== state.view) setChartExpanded(false, false);
    state.view = titles[view] ? view : "signals";
    Object.keys(titles).forEach((key) => $(`${key}-view`).classList.toggle("hidden", key !== state.view));
    document.querySelectorAll("[data-view]").forEach((button) => {
      const active = button.dataset.view === state.view;
      button.classList.toggle("active", active);
      if (active) button.setAttribute("aria-current", "page"); else button.removeAttribute("aria-current");
    });
    $("page-title").textContent = titles[state.view][0];
    $("breadcrumb-current").textContent = titles[state.view][0];
    $("page-description").textContent = state.view === "signals" && state.status ? notificationPolicy() : titles[state.view][1];
    document.title = `spike · ${titles[state.view][0]}`;
    if (updateHash) history.replaceState(null, "", `#${state.view}`);
    if (state.view === "system") loadHealth();
  }
  function filteredSignals() {
    const q = normalSearch(state.search);
    return sourceItems().filter((item) => item && (!q || normalSearch(item.symbol).includes(q)) &&
      (state.timeframe === "all" || item.timeframe === state.timeframe) &&
      (state.side === "all" || item.side === state.side) && (state.signalScope === "confirmed" ? isConfirmed(item) : state.signalScope === "direct" ? isDirectRecord(item) : isCandidate(item) && (state.signalScope === "all" || ["pending", "error"].includes(item.model?.status))));
  }
  function notification(item, channel = "telegram") {
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
  function renderSignals() {
    const items = filteredSignals();
    const confirmed = state.signalScope === "confirmed", direct = state.signalScope === "direct", notifying = confirmed || direct;
    const loaded = state[`${sourceKey()}Loaded`];
    const fetchError = state.errors[sourceKey()];
    const source = sourceItems();
    const hasEarlierPage = state.signalScope === "direct" && state.rawHasMore;
    $("filtered-count").textContent = `${items.length} 条${hasEarlierPage ? "（当前页）" : ""}`;
    $("filtered-count").title = hasEarlierPage
      ? `当前页 ${number(source.length)} 条；可直接读取更早记录`
      : `当前已加载 ${number(source.length)} 条记录`;
    $("signal-section-title").textContent = confirmed ? "YOLO 补充确认" : "原始 V1 启动";
    $("signal-scope-note").textContent = state.signalSource === "replay"
      ? "历史回放只展示已记录事件与之后的真实行情；不触发、也不暗示通知。"
      : confirmed ? "YOLO 是原始 V1 之后的补充确认，不是启动门；两类实时记录各自以服务回执为准。" : "只展示已收盘的原始 V1 多头启动；次开盘的实际成交时间须由后端提供。";
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
        $("signal-empty-title").textContent = state.signalSource === "replay" ? "正在读取历史回放" : "正在连接实时信号服务";
        $("signal-empty-description").textContent = "真实记录会在这里出现。";
      } else {
        $("signal-empty-title").textContent = hasFilters ? "没有符合筛选的记录" : state.signalSource === "replay" ? "尚无导入的历史回放记录" : confirmed ? "等待 YOLO 补充确认" : "等待新的收盘启动";
        $("signal-empty-description").textContent = hasFilters ? "试试其他合约或周期。" : state.signalSource === "replay" ? "回放数据导入后会在这里单独显示，不会被当作实时通知。" : confirmed ? "YOLO 确认会与原始 V1 启动分开显示。" : "只显示新的已收盘 V1 启动。";
      }
    }
    const focused = document.activeElement?.dataset;
    const focusedPreview = Boolean(focused?.previewSignalId);
    const focusedId = focused?.signalId || focused?.previewSignalId, focusedKind = focused?.signalKind;
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
    $("signal-rows").innerHTML = noFresh + group(direct ? "新鲜启动" : "新鲜确认", freshVisible, true) + group(confirmed ? fresh.length ? "更早确认" : "已记录确认" : direct ? "已记录启动" : "指标候选 · 模型等待状态", earlierVisible, false);
    renderTradingViewButtons();
    if (focusedId) Array.from($("signal-rows").querySelectorAll(focusedPreview ? "[data-preview-signal-id]" : "[data-signal-id]")).find((card) => (card.dataset.signalId || card.dataset.previewSignalId) === focusedId && card.dataset.signalKind === focusedKind && card.dataset.tvSymbol === focused.tvSymbol && card.dataset.tvTimeframe === focused.tvTimeframe)?.focus({ preventScroll: true });
  }
  function signalCardHTML(item, now = signalClock()) {
    const selected = sameEvent(state.selected, item);
    const confirmed = isConfirmed(item), original = originalSignal(item), direct = directReceipt(item);
    const side = item.side === "short" ? "short" : item.side === "long" ? "long" : "neutral";
    const venue = String(item.venue || "OKX").toUpperCase();
    const fresh = isFresh(item, now);
    const status = confirmed ? "YOLO 补充确认" : "原始 V1 启动", caption = "信号收盘价";
    const waiting = `${number(item.model?.wait_bars)} / ${number(item.model?.max_wait_bars)} 根`;
    return `<article class="signal-card ${side}${confirmed || direct ? "" : " candidate-card"}${selected ? " selected" : ""}${fresh ? " is-fresh" : ""}"><button type="button" class="card-primary-action" data-signal-id="${escapeHTML(item.id)}" data-signal-kind="${escapeHTML(item.kind)}" data-tradingview-action="signal" data-tv-symbol="${escapeHTML(item.symbol)}" data-tv-timeframe="${escapeHTML(item.timeframe)}" title="点击卡片，在 本机 TradingView 打开" aria-label="${escapeHTML(`${venue} ${shortSymbol(item.symbol)} ${quoteSymbol(item.symbol)} ${timeframeLabel(item.timeframe)} ${sideName(item.side)}，${status}，${caption} ${price(item.price)}，${shortDate(item.bar_close_ms)}，在 本机 TradingView 打开`)}"></button>
      <span class="signal-card-top"><span class="card-symbol"><strong>${escapeHTML(shortSymbol(item.symbol))}</strong><small>${escapeHTML(venue)} · ${escapeHTML(quoteSymbol(item.symbol))} 永续</small></span><span class="card-timeframe">${escapeHTML(timeframeLabel(item.timeframe))}</span></span>
      <span class="signal-card-direction"><span class="card-direction">↑ 多头${confirmed ? " · 确认" : " · 启动"}</span><span class="card-recency">${item.source === "replay" ? "历史回放" : fresh ? "新 · " + ageLabel(item.bar_close_ms) : ageLabel(item.bar_close_ms)}</span></span>
      <span class="model-card-status"><span class="model-badge ${confirmed ? "confirmed" : "pending"}">${escapeHTML(status)}</span><span>${item.source === "replay" ? "回放记录 · 不通知" : confirmed ? "补充确认 · 非启动门" : "第一阶段 · 已收盘"}</span></span>
      <span class="card-price-label">${caption}</span><span class="card-price">${escapeHTML(price(item.price))}</span>
      ${confirmed && item.indicator ? `<span class="card-origin">原始 V1 ${escapeHTML(price(original.price))} · ${escapeHTML(shortDate(original.bar_close_ms))}</span>` : ""}
      <span class="card-context"><span>信号 K 线</span><strong>${item.is_closed ? "已确认" : "待确认"}</strong></span>
      <span class="card-confirmed"><span>${item.executable_entry_time ? item.source === "replay" ? `回放执行时钟 ${escapeHTML(shortDate(milliseconds(item.executable_entry_time)))}` : `实际进场 ${escapeHTML(shortDate(milliseconds(item.executable_entry_time)))}` : item.entry_reference === "next_open" ? "次开盘参考 · 等待实际成交" : "仅信号收盘参考"}</span><time title="${escapeHTML(fullDate(item.bar_close_ms))} 北京时间">${escapeHTML(shortDate(item.bar_close_ms))}</time></span>
      <span class="card-footer"><span class="notification-stack">${item.source === "replay" ? `<span class="candidate-notice">历史回放不通知</span>` : notificationHTML(item)}</span><span class="card-actions"><button type="button" class="card-preview" data-preview-signal-id="${escapeHTML(item.id)}" data-signal-kind="${escapeHTML(item.kind)}" data-tv-symbol="${escapeHTML(item.symbol)}" data-tv-timeframe="${escapeHTML(item.timeframe)}" aria-pressed="${Boolean(selected)}" aria-label="${escapeHTML(`页内预览 ${venue} ${shortSymbol(item.symbol)} ${quoteSymbol(item.symbol)} ${timeframeLabel(item.timeframe)}`)}"><span>页内预览</span></button><span class="card-open" data-tradingview-label="TradingView ↗" aria-hidden="true">TradingView ↗</span></span></span>
    </article>`;
  }
  function applySignalFilters() {
    const items = filteredSignals();
    if (!items.some((item) => sameSelection(state.selected, item))) {
      if (items.length) chooseSignal(items[0]);
      else clearSelectedSignal();
    }
    renderSignals();
  }
  function invalidateSignalQuery() {
    state.signalQueryRevision++;
    state.signals = [];
    state.directSignals = [];
    state.rawYoloSignals = [];
    state.signalsLoaded = false;
    state.directSignalsLoaded = false;
    state.signalTotal = 0;
    state.directSignalTotal = 0;
    state.rawNextCursor = null;
    state.rawHasMore = false;
    state.rawPaged = false;
    state.rawLoadingMore = false;
    clearSelectedSignal();
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
    const focusedAction = focused?.tradingviewAction ? "tradingview" : "preview";
    const focusedSymbol = focused?.marketSymbol || focused?.tvSymbol, focusedTimeframe = focused?.marketTimeframe || focused?.tvTimeframe;
    $("load-more-watch").classList.toggle("hidden", items.length <= state.watchLimit);
    $("load-more-watch").textContent = `显示更多（${Math.min(state.watchLimit, items.length)} / ${items.length}）`;
    $("watch-rows").innerHTML = items.slice(0, state.watchLimit).map((item) => {
      const valid = !state.errors.markets && !item.error && !item.stale && item.ready !== false;
      const phaseClass = state.errors.markets ? "stale" : item.error ? "error" : item.stale ? "stale" : item.ready === false ? "loading" : item.focus === true ? "ready" : "";
      const selected = state.detailOrigin === "watch" && state.selected?.symbol === item.symbol && state.selected?.timeframe === item.timeframe;
      return `<article class="watch-card${selected ? " selected" : ""}"><button type="button" class="card-primary-action" data-tradingview-action="watch" data-tv-symbol="${escapeHTML(item.symbol)}" data-tv-timeframe="${escapeHTML(item.timeframe)}" title="点击卡片，在 本机 TradingView 打开" aria-label="在 本机 TradingView 打开 ${escapeHTML(shortSymbol(item.symbol))} ${escapeHTML(quoteSymbol(item.symbol))} ${escapeHTML(timeframeLabel(item.timeframe))}，${escapeHTML(marketPhase(item))}"></button>
        <span class="watch-card-top"><span class="card-symbol"><strong>${escapeHTML(shortSymbol(item.symbol))}</strong><small>${escapeHTML(quoteSymbol(item.symbol))} 永续</small></span><span class="card-timeframe">${escapeHTML(timeframeLabel(item.timeframe))}</span></span>
        <span class="watch-card-phase"><span class="phase-badge ${phaseClass}" title="${escapeHTML(item.error || (item.stale ? "当前保留过期行情，等待更新" : "当前结构尚不是启动信号"))}">${escapeHTML(state.errors.markets ? "缓存 · 待同步" : marketPhase(item))}</span><span class="card-status">${!valid ? "等待更新" : item.focus ? "已达蓄势门槛" : "观察中"}</span></span>
        <span class="watch-card-run"><strong>${valid ? escapeHTML(number(item.near_zero_bars)) : "—"}<small> 根</small></strong><span>当前近零蓄势</span></span>
        <span class="watch-card-background"><span>均线密集<strong>${valid ? item.dense === true ? "已密集" : item.dense === false ? "未密集" : "—" : "—"}</strong></span><span>高周期背景<strong>${valid ? escapeHTML(sideName(item.htf_side)) : "—"}</strong></span></span>
        <div class="watch-card-foot"><time title="${escapeHTML(fullDate(item.bar_close_ms))} 北京时间">收盘 ${escapeHTML(shortDate(item.bar_close_ms))}</time><span class="card-actions"><button type="button" class="card-preview" data-market-symbol="${escapeHTML(item.symbol)}" data-market-timeframe="${escapeHTML(item.timeframe)}" aria-pressed="${Boolean(selected)}" aria-label="页内预览 ${escapeHTML(shortSymbol(item.symbol))} ${escapeHTML(quoteSymbol(item.symbol))} ${escapeHTML(timeframeLabel(item.timeframe))} ${escapeHTML(marketPhase(item))}图表"><span>页内预览</span></button><span class="card-open" data-tradingview-label="TradingView ↗" aria-hidden="true">TradingView ↗</span></span></div>
      </article>`;
    }).join("");
    renderTradingViewButtons();
    if (focusedSymbol) Array.from($("watch-rows").querySelectorAll(focusedAction === "tradingview" ? "[data-tradingview-action]" : "[data-market-symbol]"))
      .find((card) => (card.dataset.marketSymbol || card.dataset.tvSymbol) === focusedSymbol && (card.dataset.marketTimeframe || card.dataset.tvTimeframe) === focusedTimeframe)?.focus({ preventScroll: true });
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
      const names = { status: "运行状态", signals: "模型确认", directSignals: "指标启动", candidates: "指标候选", markets: "蓄势观察" };
      $("error-notice").textContent = `${errors.map(([key, error]) => `${names[key] || key}：${error}`).join("；")}。${state.lastSync ? "当前保留上次成功获取的数据，" : ""}15 秒后自动重试。`;
    }
  }
  function chooseSignal(item, scroll = false, origin = "signals") {
    if (!item) return;
    // Invalidate any chart response for the prior card before changing detail.
    if (!sameSelection(state.selected, item)) {
      state.chartController?.abort();
      state.chartRequest++;
      state.chartKey = null;
      state.chart = null;
    }
    state.chartViewport = null;
    state.selected = { ...item };
    state.detailOrigin = origin;
    renderSignals();
    renderDetail();
    if (scroll && window.innerWidth <= 1100) {
      $("detail-content").closest("aside").scrollIntoView({ behavior: "instant", block: "start" });
      $("detail-symbol").focus({ preventScroll: true });
    }
  }
  function clearSelectedSignal() {
    state.selected = null;
    state.chartController?.abort();
    state.chartController = null;
    state.chartRequest++;
    state.chartKey = null;
    state.chart = null;
    state.chartViewport = null;
    renderDetail();
  }
  function renderDetail() {
    const item = state.selected;
    $("detail-empty").classList.toggle("hidden", Boolean(item));
    $("detail-content").classList.toggle("hidden", !item);
    $("back-to-signals").classList.toggle("hidden", !item);
    if (!item) {
      $("chart-container").removeAttribute("aria-label");
      setChartExpanded(false, false);
      return;
    }
    $("back-to-signals").textContent = state.detailOrigin === "watch" ? "← 返回蓄势观察" : "← 返回信号卡片";
    $("detail-price-caption").textContent = "信号收盘价";
    $("detail-symbol").textContent = shortSymbol(item.symbol);
    $("detail-market-label").textContent = `${String(item.venue || "OKX").toUpperCase()} · ${quoteSymbol(item.symbol)} 永续 · ${sourceName(item)}`;
    $("detail-timeframe").textContent = timeframeLabel(item.timeframe);
    $("chart-title").textContent = `${shortSymbol(item.symbol)} · ${timeframeLabel(item.timeframe)} · K 线 / 6 MA`;
    $("detail-price").textContent = price(item.price);
    const name = directReceipt(item) ? "指标启动" : item.kind === "tv_start" ? modelState(item) : item.kind ? eventNames[item.kind] || item.kind : marketPhase(item);
    $("detail-event-badge").innerHTML = `<span class="signal-badge ${item.side === "short" ? "short" : item.side === "long" ? "" : "neutral"}">${sideArrow(item.side)} ${escapeHTML(name)}</span>`;
    const tvSymbol = String(item.symbol || "").replace(/-/g, "").replace(/SWAP$/, ".P");
    const tvInterval = TV_INTERVALS.get(item.timeframe);
    $("tradingview-open").dataset.tvSymbol = item.symbol;
    $("tradingview-open").dataset.tvTimeframe = item.timeframe;
    renderTradingViewButtons();
    if (canOpenTradingView(item)) {
      $("tradingview-web").href = `https://www.tradingview.com/chart/?symbol=${encodeURIComponent(`OKX:${tvSymbol}`)}&interval=${tvInterval}`;
      $("tradingview-web").removeAttribute("aria-disabled");
    } else {
      $("tradingview-web").removeAttribute("href");
      $("tradingview-web").setAttribute("aria-disabled", "true");
    }
    const original = originalSignal(item), confirmed = isConfirmed(item), candidate = isCandidate(item), direct = directReceipt(item);
    const facts = [
      ["记录来源", sourceName(item), item.source === "replay" ? "model-color" : "mint"],
      ...coveredLedgerFacts(item),
      ["信号 K 线", item.is_closed ? "交易所已确认收盘" : "尚未确认 · 不作为可执行 V1", item.is_closed ? "mint" : "red"],
      ["可执行次开盘", item.executable_entry_time ? item.source === "replay" ? `回放执行时钟 ${shortDate(milliseconds(item.executable_entry_time))}` : shortDate(milliseconds(item.executable_entry_time)) : item.source === "replay" ? "回放未提供成交时钟" : item.entry_reference === "next_open" ? "等待真实成交记录" : "后端未提供", ""],
      ["V1 风险参考", finite(item.risk) ? price(item.risk) : "—", ""],
      ["特征来源哈希", item.source_sha256 ? `${String(item.source_sha256).slice(0, 12)}…` : "—", ""],
      [confirmed || candidate ? "原箭头收盘 · 北京时间" : "最近收盘 · 北京时间", shortDate(original.bar_close_ms), ""],
      ...(confirmed || candidate ? [["原箭头收盘价", price(original.price), ""]] : []),
      ...(confirmed ? [["模型确认 · 北京时间", shortDate(item.bar_close_ms), "model-color"], ["模型确认收盘价", price(item.price), "model-color"]] : []),
      ...(direct ? [["事件阶段", "指标启动 · 未经 YOLO 确认", ""], ["Bark 启动回执", notification(direct, "bark")[0], ""]] : []),
      ...(confirmed ? [["Bark 确认回执", notification(item, "bark")[0], ""]] : []),
      ...(isDisplayOnly(item) ? [["当前通知方式", DISPLAY_ONLY_NOTE, ""]] : []),
      ...(item.model ? [["模型状态", modelState(item), item.model.status === "error" ? "red" : ""], ["等待 / 最大窗口", `${number(item.model.wait_bars)} / ${number(item.model.max_wait_bars)} 根`, ""], ...(confirmed ? [["检测分数 · 非胜率", modelScore(item), ""], ["核心区间", `${shortDate(item.model.core_start_ms)} → ${shortDate(item.model.core_end_ms)}`, ""]] : [["最近检测收盘", shortDate(item.model.last_checked_close_ms), ""], ["等待截止", shortDate(item.model.expires_at_ms), ""]])] : []),
      [confirmed || candidate ? "启动前近零蓄势" : "当前近零蓄势", `${number(focusRun(item))} 根`, ""],
      ["均线密集 · 背景参考", original.dense === true ? "已密集" : original.dense === false ? "未密集" : "—", original.dense ? "mint" : ""],
      ["高周期方向 · 背景参考", sideName(original.htf_side), ""],
    ];
    $("detail-facts").innerHTML = facts.map(([label, value, color]) => `<div><div class="fact-label">${escapeHTML(label)}</div><div class="fact-value ${color}">${escapeHTML(value)}</div></div>`).join("");
    const confirmationNotification = item.source === "replay" ? "历史回放不触发通知；图上的之后行情只用于回看，绝不表示当时已知。" : "实时原始 V1 与 YOLO 补充确认各自以服务回执为准。";
    $("detail-reason").textContent = item.source === "replay"
      ? `这是历史回放记录。${confirmed ? "YOLO 仅为额外确认，不改变原始 V1 的启动时点。" : "原始 V1 启动以信号收盘为参考。"}${confirmationNotification}`
      : confirmed ? `YOLO 在原始 V1 启动之后提供补充确认，不是启动门。${confirmationNotification}`
      : `这是已收盘的原始 V1 多头启动。信号收盘价不是成交价；${item.executable_entry_time ? "实际成交时间已由后端记录。" : "实际次开盘成交仍待后端记录。"}${confirmationNotification}`;
    const market = state.markets.find((row) => row.symbol === item.symbol && row.timeframe === item.timeframe);
    const key = `${item.kind || "market"}|${item.id || ""}|${item.model?.status || ""}|${item.model?.core_start_ms || ""}|${item.model?.core_end_ms || ""}|${item.symbol}|${item.timeframe}|${item.bar_close_ms || ""}|${market?.bar_close_ms || ""}|${market?.stale || false}|${market?.error || ""}`;
    if (state.chartKey !== key) loadChart(item, key);
  }
  async function loadChart(item, key) {
    if (state.chartController) state.chartController.abort();
    const request = ++state.chartRequest;
    const controller = new AbortController();
    state.chartController = controller;
    state.chartKey = key;
    state.chart = null;
    $("chart-container").innerHTML = '<div class="chart-placeholder">正在加载真实行情…</div>';
    $("chart-container").setAttribute("aria-label", `正在加载 ${sourceName(item)} ${shortSymbol(item.symbol)} ${timeframeLabel(item.timeframe)} 图表`);
    $("chart-hint").textContent = "仅展示已返回的真实 K 线";
    const timeout = setTimeout(() => controller.abort(), 20000);
    try {
      const chartTimeframe = apiTimeframe(item.timeframe);
      if (item.source !== "replay" && !chartTimeframe) throw new Error("图表周期不受当前 V1 服务支持");
      const data = await api(item.source === "replay"
        ? `/api/replay/chart?event_id=${encodeURIComponent(item.id)}`
        : `/api/chart?symbol=${encodeURIComponent(item.symbol)}&timeframe=${encodeURIComponent(chartTimeframe)}`, controller.signal);
      if (request !== state.chartRequest || !sameSelection(state.selected, item)) return;
      if (!Array.isArray(data.candles)) throw new Error("图表数据格式有误");
      state.chart = data;
      renderChart();
    } catch (error) {
      if (request !== state.chartRequest || !sameSelection(state.selected, item)) return;
      state.chartKey = null; // Retry the same selected chart on the next successful poll.
      $("chart-container").innerHTML = `<div class="chart-placeholder">图表暂时不可用<br>${escapeHTML(error.message || "无法连接行情服务")}</div>`;
      $("chart-hint").textContent = "图表读取失败，信号记录仍可查看";
    } finally {
      clearTimeout(timeout);
    }
  }
  function setChartExpanded(expanded, restoreFocus = true) {
    if (expanded === state.chartExpanded || (expanded && !state.selected)) return;
    const dialog = $("chart-dialog"), frame = $("chart-frame"), button = $("chart-expand");
    state.chartExpanded = expanded;
    // Move the one live chart, preserving its data, crosshair and refresh target.
    if (expanded) {
      dialog.appendChild(frame);
      dialog.showModal();
    } else {
      if (dialog.open) dialog.close();
      $("chart-slot").appendChild(frame);
    }
    frame.classList.toggle("is-expanded", expanded);
    button.setAttribute("aria-expanded", String(expanded));
    button.setAttribute("aria-label", expanded ? "收起 K 线图" : "放大 K 线图");
    button.setAttribute("title", expanded ? "收起 K 线图（Esc）" : "放大 K 线图");
    $("chart-expand-label").textContent = expanded ? "收起 · Esc" : "放大";
    if (expanded || (restoreFocus && state.selected && state.view === "signals")) button.focus({ preventScroll: true });
  }
  // The core is a time interval; its vertical envelope uses only core candles,
  // never the following move. Backend confirmation timestamps remain authoritative.
  function modelOverlayHTML(item, candles, x, py, bounds) {
    const model = item?.model;
    if (!isConfirmed(item) || ![model.core_start_ms, model.core_end_ms, model.window_end_ms, item.bar_open_ms, item.bar_close_ms, item.price].every(finite) ||
      Number(model.core_start_ms) > Number(model.core_end_ms) || Number(model.core_end_ms) > Number(model.window_end_ms) ||
      Number(model.window_end_ms) > Number(item.bar_close_ms)) return { core: "", confirmation: "" };
    const indices = candles.flatMap((bar, i) => Number(bar.t) >= Number(model.core_start_ms) && Number(bar.t) <= Number(model.core_end_ms) ? [i] : []);
    let core = "";
    if (indices.length) {
      const coreBars = indices.map((i) => candles[i]);
      const top = Math.min(...coreBars.map((bar) => py(bar.h))), bottom = Math.max(...coreBars.map((bar) => py(bar.l)));
      const left = x(indices[0]) - bounds.step / 2, right = x(indices[indices.length - 1]) + bounds.step / 2;
      core = `<g class="model-core" data-source="model-core-interval"><title>模型核心区间 · ${escapeHTML(shortDate(model.core_start_ms))} 至 ${escapeHTML(shortDate(model.core_end_ms))}；纵向为区间 K 线高低包络</title><rect x="${left}" y="${top}" width="${right - left}" height="${Math.max(bottom - top, 1)}" fill="var(--chart-model-fill)" stroke="var(--chart-model)" stroke-width=".8" stroke-dasharray="3 2"/></g>`;
    }
    const index = candles.findIndex((bar) => Number(bar.t) === Number(item.bar_open_ms));
    const confirmation = index < 0 ? "" : `<g class="model-confirmation" data-event-kind="yolo_confirmed"><title>模型确认 · 收盘 ${escapeHTML(shortDate(item.bar_close_ms))} · ${escapeHTML(price(item.price))} · 等待 ${escapeHTML(number(model.wait_bars))} 根</title><line x1="${x(index)}" x2="${x(index)}" y1="${bounds.top}" y2="${bounds.bottom}" stroke="var(--chart-model)" stroke-width="1" stroke-dasharray="4 3"/><circle cx="${x(index)}" cy="${py(item.price)}" r="2.4" fill="var(--chart-model)" stroke="var(--chart-marker-bg)" stroke-width="1"/><text x="${x(index) + (index > candles.length * .8 ? -4 : 4)}" y="${bounds.top + 8}" text-anchor="${index > candles.length * .8 ? "end" : "start"}" style="font-size:7px;fill:var(--chart-model)">模型确认</text></g>`;
    return { core, confirmation };
  }
  function chartHint() {
    return state.chart?.source === "replay" ? "冻结历史 OHLC；信号之后的 K 线仅用于回看，不参与当时模型输入" : state.chart?.state?.stale ? "行情缓存已过期 · 等待重新同步" : state.chart?.state?.error ? "行情存在读取异常 · 当前为缓存" : isConfirmed(state.selected) ? "箭头：原指标 · 紫框：模型核心区间 · 紫线：模型确认" : "箭头：指标启动 · 金色：合格近零区";
  }
  function renderChart() {
    if (!state.chart) return;
    const valid = state.chart.candles.filter((bar) => [bar.t, bar.o, bar.h, bar.l, bar.c].every(finite));
    const selectedIndex = state.selected?.kind ? valid.findIndex((bar) => Number(bar.t) === Number(state.selected.bar_open_ms)) : -1;
    const defaultStart = selectedIndex >= 0 ? Math.min(Math.max(0, valid.length - 120), Math.max(0, selectedIndex - 90)) : Math.max(0, valid.length - 120);
    const viewportCount = Math.max(24, Math.min(180, Math.round(state.chartViewport?.count || 120)));
    const startIndex = Math.min(Math.max(0, Number.isInteger(state.chartViewport?.start) ? state.chartViewport.start : defaultStart), Math.max(0, valid.length - viewportCount));
    const candles = valid.slice(startIndex, startIndex + viewportCount);
    document.querySelector(".chart-bars-note").textContent = state.chart.source === "replay" ? "冻结 OHLC · 前后历史回看" : selectedIndex >= 0 && startIndex < valid.length - 120 ? "信号附近 120 根" : "最近 120 根";
    if (!candles.length) {
      $("chart-container").innerHTML = '<div class="chart-placeholder">该合约尚无足够的已收盘行情。<br>后续扫描会继续补充。</div>';
      $("chart-hint").textContent = "暂无可绘制的真实数据";
      return;
    }
    const width = 600, height = 294, left = 17, right = 67, priceTop = 10, priceBottom = 162, impulseTop = 194, impulseBottom = 262;
    const plotWidth = width - left - right;
    const nominalMs = Math.max(1, Number(state.selected?.timeframe_min || state.selected?.timeframe || 60) * 60000);
    const firstTime = Number(candles[0].t), lastTime = Number(candles[candles.length - 1].t) + nominalMs;
    const timeRange = Math.max(nominalMs, lastTime - firstTime);
    const x = (i) => left + ((Number(candles[i].t) - firstTime + nominalMs / 2) / timeRange) * plotWidth;
    const step = Math.max(1, nominalMs / timeRange * plotWidth);
    const maKeys = ["sma20", "ema20", "sma60", "ema60", "sma120", "ema120"];
    // A risk line is meaningful only when the backend supplied an explicit stop price.
    // `risk` may be a distance or a ratio, so it must never be guessed as a chart price.
    const stopPrice = finite(state.selected?.initial_stop) ? Number(state.selected.initial_stop) : null;
    const rangeValues = candles.flatMap((bar) => [bar.h, bar.l, ...maKeys.map((key) => bar[key])]).filter(finite).map(Number);
    if (stopPrice !== null) rangeValues.push(stopPrice);
    let pMin = Math.min(...rangeValues), pMax = Math.max(...rangeValues);
    const pPadding = Math.max((pMax - pMin) * .07, pMax * .0001, .00000001);
    pMin -= pPadding; pMax += pPadding;
    const py = (v) => priceBottom - (Number(v) - pMin) / (pMax - pMin) * (priceBottom - priceTop);
    const impulseValues = candles.flatMap((bar) => [bar.md, bar.sb, ...(bar.focus === true && finite(bar.focus_band) && Number(bar.focus_band) > 0 ? [Number(bar.focus_band), -Number(bar.focus_band)] : [])]).filter(finite).map(Number);
    let mMin = Math.min(0, ...impulseValues), mMax = Math.max(0, ...impulseValues);
    const mPadding = Math.max((mMax - mMin) * .18, (pMax - pMin) * .0001, .00000001);
    mMin -= mPadding; mMax += mPadding;
    const my = (v) => impulseBottom - (Number(v) - mMin) / (mMax - mMin) * (impulseBottom - impulseTop);
    const path = (key, y) => {
      let started = false;
      return candles.map((bar, i) => {
        if (!finite(bar[key])) { started = false; return ""; }
        const command = `${started ? "L" : "M"}${x(i).toFixed(2)},${y(bar[key]).toFixed(2)}`;
        started = true;
        return command;
      }).join(" ");
    };
    const parts = [`<svg class="market-chart" viewBox="0 0 ${width} ${height}" preserveAspectRatio="none" role="presentation"><defs><clipPath id="price-clip"><rect x="${left}" y="${priceTop}" width="${plotWidth}" height="${priceBottom - priceTop}"/></clipPath></defs>`];
    for (let i = 0; i < 4; i++) {
      const y = priceTop + i / 3 * (priceBottom - priceTop);
      const value = pMax - i / 3 * (pMax - pMin);
      parts.push(`<line x1="${left}" x2="${width - right + 3}" y1="${y}" y2="${y}" stroke="var(--chart-grid)" stroke-width=".65" stroke-dasharray="2 4"/><text x="${width - right + 9}" y="${y + 3}">${escapeHTML(axisPrice(value))}</text>`);
    }
    const labelIndices = [...new Set([0, Math.round((candles.length - 1) / 3), Math.round((candles.length - 1) * 2 / 3), candles.length - 1])];
    labelIndices.forEach((i) => {
      parts.push(`<line x1="${x(i)}" x2="${x(i)}" y1="${priceTop}" y2="${impulseBottom}" stroke="var(--chart-grid)" stroke-width=".6" stroke-dasharray="2 5"/>`);
      const date = new Date(Number(candles[i].t));
      const label = `${date.getMonth() + 1}/${date.getDate()} ${String(date.getHours()).padStart(2, "0")}:${String(date.getMinutes()).padStart(2, "0")}`;
      parts.push(`<text x="${x(i)}" y="${height - 13}" text-anchor="${i === 0 ? "start" : i === candles.length - 1 ? "end" : "middle"}">${label}</text>`);
    });
    const modelOverlay = modelOverlayHTML(state.selected, candles, x, py, { step, top: priceTop, bottom: impulseBottom });
    parts.push('<g clip-path="url(#price-clip)">');
    parts.push(modelOverlay.core);
    const maColors = ["var(--chart-ma20)", "var(--chart-ma20-muted)", "var(--chart-ma60)", "var(--chart-ma60-muted)", "var(--chart-ma120)", "var(--chart-ma120-muted)"];
    maKeys.forEach((key, i) => parts.push(`<path d="${path(key, py)}" fill="none" stroke="${maColors[i]}" stroke-width=".8" opacity=".95"/>`));
    if (stopPrice !== null) {
      const stopY = py(stopPrice);
      parts.push(`<line class="chart-risk-line" x1="${left}" x2="${width - right + 3}" y1="${stopY}" y2="${stopY}" stroke="var(--amber)" stroke-width=".9" stroke-dasharray="4 3"/><text x="${width - right + 9}" y="${stopY + 3}" style="fill:var(--amber)">V1 风险 ${escapeHTML(axisPrice(stopPrice))}</text>`);
    }
    candles.forEach((bar, i) => {
      const bright = bar.retest_side === "long" || bar.retest_side === "short";
      const rising = Number(bar.c) >= Number(bar.o);
      const color = bright ? bar.retest_side === "short" ? "var(--chart-retest-down)" : "var(--chart-retest-up)" : rising ? "var(--chart-up)" : "var(--chart-down)";
      const bodyWidth = Math.max(1, Math.min(6.4, step * .62));
      const bodyY = Math.min(py(bar.o), py(bar.c));
      const bodyHeight = Math.max(.7, Math.abs(py(bar.o) - py(bar.c)));
      parts.push(`<line x1="${x(i)}" x2="${x(i)}" y1="${py(bar.h)}" y2="${py(bar.l)}" stroke="${color}" stroke-width="${bright ? 1.2 : .85}"/><rect x="${x(i) - bodyWidth / 2}" y="${bodyY}" width="${bodyWidth}" height="${bodyHeight}" fill="${color}" stroke="none"/>`);
    });
    const breakouts = new Map();
    candles.forEach((bar, i) => {
      if (["long", "short"].includes(bar.tv_start_side)) breakouts.set(`${bar.t}|${bar.tv_start_side}`, { bar_open_ms: bar.t, side: bar.tv_start_side, price: bar.c, near_zero_bars: numeric(bar.near_zero_bars) > 0 ? bar.near_zero_bars : candles[i - 1]?.near_zero_bars });
    });
    const eventList = [...(Array.isArray(state.chart.events) ? state.chart.events : [])];
    if (isCandidate(state.selected)) eventList.push(state.selected);
    if (isConfirmed(state.selected) && state.selected.indicator) eventList.push({ ...state.selected.indicator, kind: "tv_start" });
    eventList.filter((event) => event.kind === "tv_start").forEach((event) => breakouts.set(`${event.bar_open_ms ?? event.t}|${event.side}`, event));
    Array.from(breakouts.values()).slice(-50).forEach((event) => {
      const index = candles.findIndex((bar) => Number(bar.t) === Number(event.bar_open_ms ?? event.t));
      if (index < 0 || !["long", "short"].includes(event.side)) return;
      const long = event.side === "long", cy = long ? py(candles[index].l) + 6 : py(candles[index].h) - 6;
      const cx = x(index), direction = long ? 1 : -1;
      parts.push(`<g data-event-kind="tv_start" data-side="${event.side}"><title>主图启动 · 蓄势释放${sideArrow(event.side)} · ${escapeHTML(number(event.near_zero_bars))} 根 · 收盘 ${escapeHTML(price(event.price ?? candles[index].c))}</title><path d="M${cx},${cy}l-3.5,${direction * 5}h7Z" fill="${long ? "var(--chart-marker-up)" : "var(--chart-marker-down)"}" stroke="var(--chart-marker-bg)" stroke-width=".55"/></g>`);
    });
    parts.push("</g>");
    parts.push(`<line x1="${left}" x2="${width - 12}" y1="180" y2="180" stroke="var(--line)" stroke-width=".7"/><text x="${left}" y="191" style="font-size:7px;fill:var(--chart-text)">IMACD</text><line x1="65" x2="76" y1="188.5" y2="188.5" stroke="var(--chart-md)" stroke-width="1.2"/><text x="80" y="191" style="font-size:7px">主线</text><line x1="109" x2="120" y1="188.5" y2="188.5" stroke="var(--chart-signal)" stroke-width="1.2"/><text x="124" y="191" style="font-size:7px">信号线</text>`);
    // Qualified accumulation bands come exclusively from backend focus state.
    // Exact md == 0 runs must not stand in for the TradingView near-zero area.
    let focusStart = null;
    for (let i = 0; i <= candles.length; i++) {
      const qualified = i < candles.length && candles[i].focus === true && finite(candles[i].focus_band) && Number(candles[i].focus_band) > 0;
      if (qualified && focusStart === null) focusStart = i;
      if (!qualified && focusStart !== null) {
        const segment = candles.slice(focusStart, i);
        const upper = segment.flatMap((bar, j) => [[left + (focusStart + j) * step, my(Number(bar.focus_band))], [left + (focusStart + j + 1) * step, my(Number(bar.focus_band))]]);
        const lower = segment.flatMap((bar, j) => [[left + (focusStart + j) * step, my(-Number(bar.focus_band))], [left + (focusStart + j + 1) * step, my(-Number(bar.focus_band))]]);
        const points = [...upper, ...lower.slice().reverse()].map(([px, yy]) => `${px.toFixed(2)},${yy.toFixed(2)}`).join(" ");
        const startX = left + focusStart * step, focusWidth = segment.length * step;
        const accumulationBars = Math.max(...segment.map((bar) => numeric(bar.near_zero_bars)));
        parts.push(`<g class="focus-zone" data-source="backend-focus"><polygon points="${points}" fill="var(--chart-focus-fill)" stroke="var(--chart-focus-border)" stroke-width=".65"/><title>合格近零蓄势 · ${accumulationBars} 根</title></g>`);
        if (focusWidth > 58) {
          const bottom = Math.max(...lower.map((point) => point[1]));
          parts.push(`<text x="${startX + focusWidth / 2}" y="${Math.min(impulseBottom - 1, bottom + 13)}" text-anchor="middle" style="font-size:7px;fill:var(--chart-focus-text)">近零蓄势 ${accumulationBars} 根</text>`);
        }
        focusStart = null;
      }
    }
    // Keep the zero axis distinct from the qualified near-zero band.
    const zeroY = my(0);
    parts.push(`<line x1="${left}" x2="${width - right + 3}" y1="${zeroY}" y2="${zeroY}" stroke="var(--chart-zero)" stroke-width=".8" stroke-dasharray="3 3"/><text x="${width - right + 9}" y="${zeroY + 3}" style="fill:var(--chart-zero)">0.00</text>`);
    parts.push(`<path d="${path("md", my)}" stroke="var(--chart-md)" stroke-width="1.35" fill="none"/><path d="${path("sb", my)}" stroke="var(--chart-signal)" stroke-width="1.2" fill="none"/>`);
    parts.push(modelOverlay.confirmation);
    parts.push(`<g class="chart-crosshair" visibility="hidden"><line class="crosshair-line" x1="0" x2="0" y1="${priceTop}" y2="${impulseBottom}" stroke="var(--chart-crosshair)" stroke-width=".8" stroke-dasharray="3 3"/><circle class="crosshair-dot" r="2.5" fill="var(--chart-marker-up)" stroke="var(--chart-marker-bg)" stroke-width="1.2"/></g><rect class="chart-hit-area" x="${left}" y="${priceTop}" width="${plotWidth}" height="${impulseBottom - priceTop}" fill="transparent" stroke="none"/></svg>`);
    $("chart-container").innerHTML = parts.join("");
    $("chart-container").setAttribute("aria-label", `${shortSymbol(state.selected?.symbol)} ${timeframeLabel(state.selected?.timeframe)}，${candles.length} 根真实 K 线、六条均线、V1 启动标记${stopPrice === null ? "；后端未提供可绘制的风险价位" : "与风险参考线"}`);
    $("chart-hint").textContent = chartHint();
    const svg = $("chart-container").querySelector("svg");
    const crosshair = svg.querySelector(".chart-crosshair");
    const move = (event) => {
      const rect = svg.getBoundingClientRect();
      const localX = (event.clientX - rect.left) / rect.width * width;
      const index = Math.min(candles.length - 1, Math.max(0, Math.floor((localX - left) / step)));
      const bar = candles[index];
      const line = crosshair.querySelector("line"), dot = crosshair.querySelector("circle");
      line.setAttribute("x1", x(index)); line.setAttribute("x2", x(index));
      dot.setAttribute("cx", x(index)); dot.setAttribute("cy", py(bar.c));
      crosshair.setAttribute("visibility", "visible");
      const marker = Array.from(breakouts.values()).find((event) => Number(event.bar_open_ms ?? event.t) === Number(bar.t));
      $("chart-hint").textContent = marker ? `${shortDate(bar.t)} · 蓄势释放${sideArrow(marker.side)} · ${number(marker.near_zero_bars)} 根 · 收盘 ${price(marker.price ?? bar.c)}` : `${shortDate(bar.t)}  O ${price(bar.o)}  H ${price(bar.h)}  L ${price(bar.l)}  C ${price(bar.c)}  MD ${price(bar.md)}`;
    };
    svg.addEventListener("pointermove", move);
    svg.addEventListener("pointerleave", () => { crosshair.setAttribute("visibility", "hidden"); $("chart-hint").textContent = chartHint(); });
    svg.addEventListener("wheel", (event) => {
      event.preventDefault();
      const rect = svg.getBoundingClientRect();
      const local = (event.clientX - rect.left) / rect.width * width;
      const anchor = Math.min(candles.length - 1, Math.max(0, Math.floor((local - left) / Math.max(step, 1))));
      const next = Math.max(24, Math.min(180, Math.round(viewportCount * (event.deltaY < 0 ? .8 : 1.25))));
      const absolute = startIndex + anchor;
      state.chartViewport = { count: next, start: Math.max(0, Math.min(valid.length - next, absolute - Math.round(anchor * next / candles.length))) };
      renderChart();
    }, { passive: false });
    let drag = null;
    svg.addEventListener("pointerdown", (event) => { drag = { x: event.clientX, start: startIndex }; svg.setPointerCapture?.(event.pointerId); });
    svg.addEventListener("pointerup", (event) => { if (!drag) return; const delta = Math.round((event.clientX - drag.x) / Math.max(step, 1)); state.chartViewport = { count: viewportCount, start: Math.max(0, Math.min(valid.length - viewportCount, drag.start - delta)) }; drag = null; renderChart(); });
  }
  async function refresh() {
    if (state.syncing) { state.refreshQueued = true; return; }
    const queryRevision = state.signalQueryRevision;
    const querySource = state.signalSource;
    const queryTimeframe = state.timeframe;
    state.syncing = true;
    $("refresh-button").disabled = true;
    $("refresh-button").classList.add("loading");
    try {
      const source = encodeURIComponent(querySource);
      const timeframe = queryTimeframe === "all" ? "" : `&timeframe=${encodeURIComponent(apiTimeframe(queryTimeframe) || "")}`;
      const results = await Promise.allSettled([
        api("/api/status"),
        api(`/api/signals?limit=${SIGNAL_PAGE_SIZE}&source=${source}&confirmation=yolo${timeframe}`),
        api(`/api/signals?limit=${SIGNAL_PAGE_SIZE}&source=${source}&confirmation=raw${timeframe}`),
        api(`/api/signals?limit=${SIGNAL_PAGE_SIZE}&source=${source}&confirmation=raw_yolo${timeframe}`),
      ]);
      if (queryRevision !== state.signalQueryRevision || querySource !== state.signalSource || queryTimeframe !== state.timeframe) return;
      const keys = ["status", "signals", "directSignals", "rawYoloSignals"];
      let anySuccess = false;
      results.forEach((result, index) => {
        const key = keys[index];
        if (result.status === "rejected") { state.errors[key] = result.reason.message || "请求失败"; return; }
        if (key !== "status" && !Array.isArray(result.value.items)) { state.errors[key] = "服务返回的数据格式有误"; return; }
        delete state.errors[key];
        anySuccess = true;
        if (key === "status") { state.status = result.value; state.statusReceivedAt = Date.now(); }
        else {
          const items = result.value.items.filter((item) => item && typeof item === "object" && item.symbol).map(normalizeV1Event)
            .filter((item) => key === "signals" ? isConfirmed(item) : key === "directSignals" ? isDirectRecord(item) : Boolean(item));
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
      const rawYoloTotal = numeric(results[3]?.status === "fulfilled" ? results[3].value.total : 0);
      if (results[1]?.status === "fulfilled") state.signalTotal = numeric(results[1].value.total, state.signals.length) + rawYoloTotal;
      if (results[2]?.status === "fulfilled") state.directSignalTotal = numeric(results[2].value.total, state.directSignals.length) + rawYoloTotal;
      if (anySuccess) state.lastSync = Date.now();
      const currentItems = filteredSignals();
      if (state.detailOrigin === "signals" && !currentItems.some((item) => sameSelection(state.selected, item))) {
        if (currentItems.length) chooseSignal(currentItems[0]);
        else clearSelectedSignal();
      } else if (!state.selected && currentItems.length) chooseSignal(currentItems[0]);
      renderErrors(); renderStatus(); renderSignals(); renderWatch();
      $("last-sync").textContent = state.errors[sourceKey()] ? "同步失败 · 保留缓存" : `同步 ${clockTime(state.lastSync)}`;
      if (state.selected) {
        const updated = state.selected.id !== undefined ? currentItems.find((item) => sameSelection(item, state.selected)) : state.markets.find((item) => item.symbol === state.selected.symbol && item.timeframe === state.selected.timeframe);
        if (updated) state.selected = { ...updated };
        renderDetail();
      }
      if (state.view === "system" && $("health-json").closest("details").open) loadHealth();
    } finally {
      state.syncing = false;
      $("refresh-button").disabled = false;
      $("refresh-button").classList.remove("loading");
      if (state.refreshQueued) {
        state.refreshQueued = false;
        refresh();
      }
    }
  }
  async function loadEarlierRawSignals() {
    if (state.rawLoadingMore || !state.rawHasMore || !state.rawNextCursor || state.signalScope !== "direct") return;
    const queryRevision = state.signalQueryRevision;
    const querySource = state.signalSource;
    const queryTimeframe = state.timeframe;
    const cursor = state.rawNextCursor;
    state.rawLoadingMore = true;
    renderSignals();
    try {
      const timeframe = queryTimeframe === "all" ? "" : `&timeframe=${encodeURIComponent(apiTimeframe(queryTimeframe) || "")}`;
      const path = `/api/signals?limit=${SIGNAL_PAGE_SIZE}&source=${encodeURIComponent(querySource)}&confirmation=raw${timeframe}`
        + `&before_close_ms=${encodeURIComponent(cursor.close_ms)}&before_id=${encodeURIComponent(cursor.event_id)}`;
      const result = await api(path);
      if (queryRevision !== state.signalQueryRevision || querySource !== state.signalSource || queryTimeframe !== state.timeframe) return;
      if (!Array.isArray(result.items)) throw new Error("服务返回的数据格式有误");
      const older = result.items.filter((item) => item && typeof item === "object" && item.symbol)
        .map(normalizeV1Event).filter(isDirectRecord);
      const existing = state.directSignals;
      state.directSignals = [...existing, ...older].filter((item, index, rows) => rows.findIndex((other) => sameEvent(other, item)) === index)
        .sort((a, b) => numeric(b.bar_close_ms) - numeric(a.bar_close_ms) || String(b.id).localeCompare(String(a.id)));
      state.directSignalTotal = state.directSignals.length;
      state.rawNextCursor = result.next_cursor || null;
      state.rawHasMore = Boolean(state.rawNextCursor);
      state.rawPaged = true;
      delete state.errors.directSignals;
    } catch (error) {
      state.errors.directSignals = error.message || "请求失败";
    } finally {
      state.rawLoadingMore = false;
      renderErrors(); renderSignals();
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
    applySignalFilters();
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
  $("refresh-button").addEventListener("click", refresh);
  $("tradingview-open").addEventListener("click", () => openTradingView(state.selected));
  $("chart-expand").addEventListener("click", () => setChartExpanded(!state.chartExpanded));
  $("chart-reset").addEventListener("click", () => { state.chartViewport = null; renderChart(); });
  $("chart-dialog").addEventListener("close", () => {
    if (!$("chart-dialog").open) setChartExpanded(false);
  });
  $("chart-dialog").addEventListener("click", (event) => {
    if (event.target === $("chart-dialog")) setChartExpanded(false);
  });
  $("load-more-signals").addEventListener("click", () => {
    if (state.rowLimit < sourceItems().length) { state.rowLimit += 24; renderSignals(); }
  });
  $("load-earlier-signals").addEventListener("click", loadEarlierRawSignals);
  function activateRow(event, type) {
    const preview = event.target.closest(type === "signal" ? "[data-preview-signal-id]" : "[data-market-symbol]");
    const row = preview || event.target.closest("[data-tradingview-action]") || event.target.closest(type === "signal" ? ".signal-card" : ".watch-card")?.querySelector("[data-tradingview-action]");
    if (!row) return;
    if (event.type === "keydown") {
      // Preview buttons keep native keyboard clicks. Primary actions handle both
      // keys here and cancel the synthesized click so the bridge runs once.
      if (preview || !["Enter", " "].includes(event.key)) return;
      event.preventDefault();
      if (event.repeat) return;
    }
    event.preventDefault();
    if (!preview && state.tradingViewPending) return;
    const item = type === "signal"
      ? sourceItems().find((candidate) => sameEvent(candidate, { id: row.dataset.signalId || row.dataset.previewSignalId, kind: row.dataset.signalKind, symbol: row.dataset.tvSymbol, timeframe: row.dataset.tvTimeframe }))
      : state.markets.find((candidate) => candidate.symbol === (row.dataset.marketSymbol || row.dataset.tvSymbol) && candidate.timeframe === (row.dataset.marketTimeframe || row.dataset.tvTimeframe));
    if (!item) return;
    if (!preview && !canOpenTradingView(item)) { openTradingView(item); return; }
    if (preview && type === "market") {
      state.search = shortSymbol(item.symbol); state.timeframe = item.timeframe; state.side = "long"; state.rowLimit = 24;
      $("symbol-search").value = state.search; if (sideFilter) sideFilter.value = "long";
      document.querySelectorAll("[data-timeframe]").forEach((button) => { const selected = button.dataset.timeframe === item.timeframe; button.classList.toggle("selected", selected); button.setAttribute("aria-pressed", String(selected)); });
      setView("signals");
    }
    chooseSignal(item, Boolean(preview), type === "market" ? "watch" : "signals");
    if (type === "market") renderWatch();
    if (!preview) openTradingView(item);
  }
  ["click", "keydown"].forEach((eventType) => {
    $("signal-rows").addEventListener(eventType, (event) => activateRow(event, "signal"));
    $("watch-rows").addEventListener(eventType, (event) => activateRow(event, "market"));
  });
  $("load-more-watch").addEventListener("click", () => { state.watchLimit += 24; renderWatch(); });
  $("back-to-signals").addEventListener("click", () => {
    const watch = state.detailOrigin === "watch";
    if (watch) setView("watch");
    const cards = document.querySelectorAll(watch ? "[data-market-symbol]" : "[data-signal-id]");
    const card = Array.from(cards).find((node) => watch ? node.dataset.marketSymbol === state.selected?.symbol && node.dataset.marketTimeframe === state.selected?.timeframe : sameEvent(state.selected, { id: node.dataset.signalId, kind: node.dataset.signalKind, symbol: node.dataset.tvSymbol, timeframe: node.dataset.tvTimeframe }));
    const target = card || $(watch ? "watch-search" : "symbol-search");
    target.scrollIntoView({ behavior: "instant", block: "center" });
    target.focus({ preventScroll: true });
  });
  $("health-json").closest("details").addEventListener("toggle", (event) => { if (event.target.open) loadHealth(); });
  document.addEventListener("keydown", (event) => {
    if (state.chartExpanded) return; // Native dialog handles Escape and focus containment.
    if (event.key === "/" && !event.metaKey && !event.ctrlKey && !event.altKey && !["INPUT", "TEXTAREA", "SELECT"].includes(document.activeElement?.tagName)) {
      event.preventDefault();
      if (state.view === "system") setView("signals");
      (state.view === "watch" ? $("watch-search") : $("symbol-search")).focus();
    }
  });
  window.addEventListener("hashchange", () => setView(location.hash.slice(1), false));
  document.addEventListener("visibilitychange", () => { if (!document.hidden) refresh(); });
  setView(location.hash.slice(1) || "signals", false);
  setInterval(() => { $("local-clock").textContent = clockTime(Date.now()); }, 1000);
  $("local-clock").textContent = clockTime(Date.now());
  refresh();
  setInterval(refresh, 15000);
})();
