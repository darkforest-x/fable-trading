/* Local monitor client. Market data is read-only; app opening requires a click. */
(() => {
  "use strict";

  const $ = (id) => document.getElementById(id);
  const DEFAULT_VIEW = "monday-fib";
  const state = {
    view: DEFAULT_VIEW, signals: [], directSignals: [], performanceSignals: [], rawYoloSignals: [], candidates: [], signalScope: "retest", markets: [], status: null, health: null,
    signalsLoaded: false, directSignalsLoaded: false, directSignalTotal: 0, candidatesLoaded: false, candidateTotal: 0, candidateCounts: null, marketsLoaded: false, marketsLoading: false, marketsRetryTimer: null, signalTotal: 0, rowLimit: 24, watchLimit: 24, search: "", watchSearch: "", watchScope: "building",
    shadowStatus: null, shadowEvents: [], shadowMarket: [], shadowLoaded: false, shadowLoading: false, shadowTimeframe: "all",
    rawNextCursor: null, rawHasMore: false, rawPaged: false, rawLoadingMore: false,
    ledger: null, period: "all", outcome: "all", sort: "newest", page: 0,
    signalLayout: "cards", signalPane: "records", recordLayout: "cards", signalDetailId: null, historyStatsOpen: { groups: false },
    timeframe: "all", watchTimeframe: "all", side: "all", signalSource: "live",
    syncing: false, refreshQueued: null, signalQueryRevision: 0, lastSync: null, statusReceivedAt: null, errors: {},
    tradingViewPending: false,
    lines: { kind: null, items: [], status: null, timeframe: "all", search: "", liveOnly: false, limit: 24, loading: false, syncedAt: null,
      period: "all", outcome: "all", sort: "newest", performanceVersion: "current", ledger: null, revision: 0 },
  };
  const titles = {
    platform: ["体系总览", "个人交易、学习输出、策略研究与自动策略验证，共用知识、数据与模型。"],
    "trading-books": ["交易书架", "从原文到小册：选书、逐章研读、自己的笔记与复习。"],
    learning: ["学习中心", "围绕问题安排学习、复述与复习，把答案沉淀为自己的知识。"],
    orderflow: ["订单流框架", "订单流与机构框架学习小册：逐章阅读、笔记、间隔复习与练习清单。小册内容只在本机。"],
    "monday-fib": ["周一1.618监控", "OKX 全部 USDT 永续 · 纽约周一区间外触价提醒"],
    "auto-leaders": ["自动找龙头", "筛选山寨币观察候选，追踪相对强度、回吐与恢复进度。"],
    "yolo-detect": ["均线密集检测", "选择币种与周期，用 YOLO 在全局 K 线上定位均线密集形态。"],
    langlang: ["Bit浪浪体系", "主升浪交易法：体系图、逐章阅读、直播原话检索、案例盲测与开单检查。资料只在本机。"],
    rektproof: ["RektProof 区间", "RektProof（Arthur）学习体系：逐章精读、作者原图、交易案例、回放练习与周计划。资料只在本机。"],
    knowledge: ["知识库", "按领域组织概念、原则、假设与经验，保留来源、证据和适用边界。"],
    content: ["内容工作室", "知识形成母稿，按平台编写文章、短帖与视频脚本，导出并登记发布记录。"],
    "paper-desk": ["模拟开单", "手动模拟开仓、平仓，跟踪账户权益与逐笔盈亏。"],
    manual: ["个人交易系统", "沉淀交易原则 → 核对后开单 → 记录与复盘。"],
    market: ["市场共振观察", "BTC、ETH 同时放量，多数山寨也同方向涨跌，之后会怎样？"],
    copier: ["Arthur 监控", "一个频道，一套可核对的模拟账本。发布、采集、处理与成交分别记录。"],
    models: ["模型中心", "查看登记制品、来源、特征语义和身份校验；身份校验不代表模型效果或生产准入。"],
    signals: ["信号中心", "SPIKE V13.1 多空回踩再突破确认 · 15m/30m/1H/4H；通知按通知中心订阅。"],
    warmup: ["预热历史", "V12.8 启用前按当前规则重算的信号，仅供复盘，不补发通知。"],
    watch: ["蓄势观察", "还在横盘的，单独观察。这里的结构尚不是启动信号。"],
    shadow: ["旧版影子记录", "已退役的 V7 / V8 独立观察记录，仅供历史对照；新运行统一进入模拟实盘。"],
    research: ["研究总览", "围绕均线密集系统，跟踪因子、实验、回测与视觉判断。"],
    datasets: ["数据集", "统一管理币圈数据目录，核对覆盖、整理状态与回测读取入口。"],
    factors: ["因子库", "定义、可知时点、实现来源与研究结论，一处追踪。"],
    strategies: ["策略库", "登记策略规则、研究状态与关联，为后续模拟观察保留版本记录。"],
    paper: ["策略前向账本", "只观察创建任务之后的新信号，按未来开盘模拟，不向交易所下单。"],
    experiments: ["实验登记", "保留每次问题、唯一变化、原始结果与失败证据。"],
    backtests: ["回测任务", "冻结参数与数据，独立运行，留存逐笔与随机对照。"],
    yolo: ["YOLO 工作流", "样本、标注、数据集、训练与评估，沿同一条研究流程追踪。"],
    vision: ["VLM 工作流", "视觉语言模型的参考图、形态判断、人工复核与历史回放，与 YOLO 并行研究。"],
    notifications: ["通知中心", "管理通知渠道状态与来源订阅。"],
    system: ["运行状态", "行情、扫描与通知，每个环节都清晰可见。"],
  };
  const workspaceView = (view = state.view) => ["learning", "trading-books", "orderflow", "langlang", "auto-leaders", "monday-fib", "yolo-detect", "rektproof", "knowledge", "content", "manual", "paper-desk", "copier", "market", "platform", "models", "research", "datasets", "factors", "strategies", "paper", "experiments", "backtests", "yolo", "vision"].includes(view);
  const eventNames = { tv_start: "V12.8 启动", yolo_confirmed: "YOLO 补充确认" };
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
  // The current raw event kind is SPIKE V12.8. Keep older names only for
  // already-persisted legacy chart rows; it is not the current backend kind.
  const isV1StartMarker = (event) => event?.kind === "spike_burst_v128" || event?.kind === "spike_burst_v9" || event?.kind === "tv_start";
  const isDirectRecord = (item) => isCandidate(item) && TV_INTERVALS.has(String(item.timeframe));
  const signalView = (view = state.view) => view === "signals" || view === "warmup";
  const signalQuerySource = () => state.view === "warmup" ? "warmup" : state.signalSource;
  const displayScope = (item) => item?.display_scope === "legacy" ? "legacy" : item?.display_scope === "warmup" ? "warmup" : item?.display_scope === "replay" || item?.source === "replay" ? "replay" : "live";
  const isWarmupRecord = (item) => displayScope(item) === "warmup";
  const sourceName = (item) => displayScope(item) === "legacy" ? "V9 旧版记录" : displayScope(item) === "warmup" ? "预热历史" : displayScope(item) === "replay" ? "历史回放" : "实时";
  const milliseconds = (value) => typeof value === "string" ? Date.parse(value) : Number(value);
  function normalizeV1Event(row, requestedScope = null) {
    const minutes = Number(row.timeframe_min ?? row.timeframe);
    const confirmation = row.confirmation;
    const close = milliseconds(row.signal_close_time ?? row.bar_close_ms);
    const direction = row.direction ?? row.side;
    if (![5, 15, 30, 60, 240, 1440].includes(minutes) || !["live", "replay"].includes(row.source) || !["raw", "yolo", "raw_yolo", "retest"].includes(confirmation) || !["long", "short"].includes(direction) || !finite(close)) return null;
    const rowScope = ["live", "warmup", "replay", "legacy"].includes(row.display_scope) ? row.display_scope : requestedScope || row.source;
    return { ...row, id: String(row.id ?? `${row.source}|${confirmation}|${row.venue}|${row.symbol}|${minutes}|${row.signal_close_time}`),
      source: row.source === "replay" ? "replay" : "live", confirmation, timeframe: String(minutes), timeframe_min: minutes,
      display_scope: rowScope,
      kind: confirmation === "raw" ? "tv_start" : confirmation === "retest" ? "v131_retest" : "yolo_confirmed", side: direction, bar_close_ms: close,
      bar_open_ms: milliseconds(row.signal_bar_open ?? row.bar_open_ms) || close - minutes * 60000,
      price: row.signal_close ?? row.price, is_closed: row.is_closed === true,
      model: confirmation === "yolo" || confirmation === "raw_yolo" ? { ...(row.model || {}), status: "confirmed" } : row.model };
  }
  const twoStage = () => state.status?.runtime?.notification_mode === "two_stage";
  const notificationChannels = () => {
    const configured = state.status?.runtime?.notification_channels;
    const channels = Array.isArray(configured) ? configured : [];
    return ["telegram"].filter((channel) => channels.includes(channel) && (channel !== "telegram" || state.status?.telegram?.disabled_by_owner !== true));
  };
  // Only the policy-filtered direct endpoint supplies first-stage receipts.
  // Candidate history is deliberately not a receipt source.
  const directReceipt = (item) => isDirectRecord(item) ? state.directSignals.find((row) => sameEvent(row, item)) : null;
  const isRetest = (item) => item?.confirmation === "retest";
  const signalVersion = (item) => { const protocol = item?.protocol || item?.indicator?.protocol || ""; return protocol.includes("v130") ? "V13.1" : protocol.includes("v128") ? "V12.8" : "V9"; };
  const originalSignal = (item) => isConfirmed(item) ? item.indicator || {} : item;
  const sourceItems = () => state.signalScope === "confirmed" ? state.signals : ["direct", "retest"].includes(state.signalScope) ? state.directSignals : state.candidates;
  const sourceKey = () => state.signalScope === "confirmed" ? "signals" : ["direct", "retest"].includes(state.signalScope) ? "directSignals" : "candidates";
  const modelState = (item) => modelStates[item.model?.status] || "等待模型状态";
  const DISPLAY_ONLY_NOTE = "仅前端 · Bark 已关闭";
  const runtimeTimeframes = (field) => {
    const values = state.status?.runtime?.[field];
    if (!Array.isArray(values)) return null;
    const normalized = values.map(uiTimeframe);
    return normalized.every((value) => value && TV_INTERVALS.has(value)) ? normalized : null;
  };
  const displayOnlyTimeframes = () => notificationChannels().includes("telegram") ? [] : runtimeTimeframes("display_only_timeframes") || (runtimeTimeframes("bark_timeframes") ? (runtimeTimeframes("timeframes") || []).filter((value) => !runtimeTimeframes("bark_timeframes").includes(value)) : []);
  const isDisplayOnly = (item) => !notificationChannels().includes("telegram") && (item?.display_only === true || displayOnlyTimeframes().includes(item?.timeframe));
  const notificationPolicy = () => {
    const channels = notificationChannels().map((channel) => channel === "telegram" ? "TG" : "Bark");
    const delivery = channels.length ? `通知渠道：${channels.join(" + ")}；按通知中心来源订阅。` : "通知渠道状态待同步；请查看通知中心。";
    return `${delivery} V13.1 多空回踩再突破，15m/30m/1H/4H；15m 候选须符合已完成 H1 SMA60 方向；1H 收盘 2R 后按 2ATR 追踪，其他周期 4ATR。历史记录不补发。`;
  };
  function candidateNotificationNote(item) {
    if (isDisplayOnly(item)) return DISPLAY_ONLY_NOTE;
    if (item.model?.status === "disabled") return "该周期已关闭 · 不再推送";
    if (directReceipt(item)) return "启动通知见通道回执；YOLO 通过后追加确认";
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
  function remoteWorkspace() {
    const host = typeof window !== "undefined" ? window.location?.hostname : "";
    return Boolean(host && !["localhost", "127.0.0.1", "::1", "[::1]"].includes(host));
  }
  function tradingViewTargetLabel() {
    return remoteWorkspace() ? "当前设备浏览器" : "本机 TradingView";
  }
  function remoteTradingViewURL(item) {
    if (!canOpenTradingView(item)) return "";
    const symbol = String(item.symbol).toUpperCase().replace(/^OKX:/, "").replace(/\.P$/, "")
      .replace(/-SWAP$/, "").replace(/-/g, "");
    const interval = { "5m": "5", "15m": "15", "30m": "30", "1H": "60", "4H": "240", "1Dutc": "1D" }[apiTimeframe(item.timeframe)];
    return "https://www.tradingview.com/chart/?symbol=" + encodeURIComponent("OKX:" + symbol + ".P") + "&interval=" + interval;
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
    if (remoteWorkspace()) {
      window.open(remoteTradingViewURL(item), "_blank", "noopener,noreferrer");
      tradingViewStatus("已请求在当前设备浏览器打开 TradingView。", "");
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
  function toggleNavigation(open) {
    if(window.SpikeWorkspaceDisplay){window.SpikeWorkspaceDisplay.setMobileNavigation(open);return;}
    document.body.classList.toggle("nav-open", open);
    $("nav-toggle")?.setAttribute("aria-expanded", String(open));
    if ($("nav-backdrop")) $("nav-backdrop").hidden = !open;
  }
  function setView(view, updateHash = true) {
    if (view.startsWith("langlang?") && new URLSearchParams(view.split("?")[1]).get("tab") === "auto-leaders") {view="auto-leaders";history.replaceState(null,"","#auto-leaders");}
    view = view.split("?")[0];
    // The accessibility skip target is an anchor, not another application view.
    if (view === "main") { $("main").focus?.({ preventScroll: true }); return; }
    if (["joints", "breaks", "vidya"].includes(view)) {view = "signals"; history.replaceState(null, "", "#signals");}
    if (!titles[view] || view === "market") {view = DEFAULT_VIEW; history.replaceState(null, "", `#${view}`);}
    const navigationWasOpen = document.body.classList.contains("nav-open");
    window.SpikeWorkspaceDisplay?.setMobileNavigation(false, { restoreFocus: false });
    document.body.classList.remove("nav-open");
    $("nav-toggle")?.setAttribute("aria-expanded", "false");
    if ($("nav-backdrop")) $("nav-backdrop").hidden = true;
    window.SpikeWorkspaceDisplay?.syncNavigation();
    const previousView = state.view;
    const nextView = view;
    window.SpikeNavigation?.begin(nextView, updateHash);
    state.view = nextView;
    syncRefreshButton();
    const section = signalView(state.view) ? "signals" : linesView(state.view) ? "lines" : state.view;
    ["retest", "signals", "watch", "lines", "shadow", "notifications", "system", "learning", "trading-books", "orderflow", "langlang", "auto-leaders", "monday-fib", "yolo-detect", "rektproof", "knowledge", "content", "manual", "paper-desk", "copier", "market", "platform", "models", "research", "datasets", "factors", "strategies", "paper", "experiments", "backtests", "yolo", "vision"].forEach((key) => $(`${key}-view`).classList.toggle("hidden", key !== section));
    $("primary-metrics").classList.toggle("hidden", ["retest", "shadow", "joints", "breaks", "notifications", "learning", "trading-books", "orderflow", "langlang", "auto-leaders", "monday-fib", "yolo-detect", "rektproof", "knowledge", "content", "manual", "paper-desk", "copier", "market", "platform", "models", "research", "datasets", "factors", "strategies", "paper", "experiments", "backtests", "yolo", "vision"].includes(state.view));
    document.querySelectorAll("[data-view]").forEach((button) => {
      const active = button.dataset.view === state.view;
      button.classList.toggle("active", active);
      if (active) button.setAttribute("aria-current", "page"); else button.removeAttribute("aria-current");
      button.title = titles[button.dataset.view]?.[0] || "";
      if (active) { const group = button.closest?.("details"); if (group) group.open = true; button.scrollIntoView?.({ block: "nearest", inline: "nearest" }); }
    });
    const researchView = workspaceView();
    $("exchange-mark").textContent = state.view === "manual" ? "个人" : state.view === "copier" ? "跟单" : researchView ? "研究" : "OKX";
    $("market-scope").textContent = researchView ? "本机工作台" : "全市场永续";
    $("connection-label").classList.toggle("hidden", researchView);
    $("telegram-header")?.classList.toggle("hidden", researchView);
    $("model-gate-notice").classList.toggle("hidden", researchView || state.view === "retest" || state.signalScope === "retest" || !$("model-gate-notice").textContent);
    // The embedded VLM owns its chart, references and history refresh actions.
    $("refresh-button").classList.toggle("hidden", state.view === "vision");
    $("refresh-button").setAttribute("aria-label", researchView ? `刷新${titles[state.view][0]}` : "立即刷新监控数据");
    $("page-title").textContent = titles[state.view][0];
    $("breadcrumb-current").textContent = titles[state.view][0];
    $("page-description").textContent = state.view === "signals" ? "追踪全市场确认信号，查看参考表现与 OKX 账户。" : titles[state.view][1];
    $("signal-source-scope").classList.toggle("hidden", state.view === "warmup" || state.signalScope === "retest");
    $("signal-scope-note").classList.toggle("hidden", state.view === "warmup");
    $("warmup-notice").classList.toggle("hidden", state.view !== "warmup");
    document.title = `spike · ${titles[state.view][0]}`;
    window.SpikeWorkbench?.navigate(state.view);
    if(state.status)renderStatus();
    if (!window.SpikeNavigation && updateHash && location.hash !== `#${state.view}`) history.pushState(null, "", `#${state.view}`);
    if (!window.SpikeNavigation && previousView !== state.view) {
      window.scrollTo?.({ top: 0, behavior: "auto" });
    }
    if (previousView !== state.view || navigationWasOpen) $("main").focus?.({ preventScroll: true });
    if (researchView || state.view === "shadow" || state.view === "notifications") refreshShellStatus();
    const changesWarmupScope = previousView === "warmup" || state.view === "warmup";
    const entersSignalView = !signalView(previousView) && signalView(state.view);
    if (changesWarmupScope) {
      state.rowLimit = 24; state.page = 0;
      invalidateSignalQuery();
      if (signalView(state.view)) refresh();
    }
    if (window.SpikeModules) {
      if (previousView !== state.view) activateModules(null);
      const requestedView=state.view, generation=++viewGeneration;
      const message=$('module-load-state');
      if(message){message.hidden=false;message.dataset.loading='true';message.textContent='正在载入页面…';}
      viewLoad=window.SpikeModules.ensure(requestedView).then(()=>{
        if(generation!==viewGeneration)return;
        if(message){message.hidden=true;delete message.dataset.loading;}
        window.SpikeNavigation?.restore(requestedView);
        activateModules(requestedView);
        window.SpikeMotion?.route(requestedView, $(`${section}-view`));
        if(requestedView==='signals')window.SpikeMotion?.cards('signals',document.querySelectorAll('#signal-rows .signal-card'));
        if(signalView(requestedView))refresh("navigation");
        window.SpikeNavigation?.settled();
      }).catch(error=>{
        if(generation===viewGeneration&&message){message.hidden=false;delete message.dataset.loading;message.textContent=error.message;}
      });
    } else {activateModules(state.view);window.SpikeMotion?.route(state.view, $(`${section}-view`));}
    $("research-error").classList.add("hidden");
    if (state.view === "system") loadHealth();
    // Signals never load the expensive market overview.  Watch opts in once.
    if (state.view === "watch") loadMarkets();
    if (state.view === "shadow") loadShadow();
    if (linesView(state.view)) {
      const kind = state.view === "joints" ? "joint" : "break";
      if (state.lines.kind !== kind) Object.assign(state.lines, { kind, items: [], timeframe: "all", limit: 24 });
      renderLines();
      loadLines();
    }
  }
  let viewLoad=Promise.resolve(), viewGeneration=0;
  function activateModules(activeView) {
    window.SpikeOKXExecution?.setActive(activeView === "signals" && state.signalPane === "account");
    window.SpikeSignalWorkspace?.setActive(activeView === "signals");
    window.SpikeResearch?.setView(activeView);
    window.SpikePlatform?.setActive(activeView === "platform");
    window.SpikeManual?.setActive(activeView === "manual");
    window.SpikePaperDesk?.setActive(activeView === "paper-desk");
    window.SpikeCopier?.setActive(activeView === "copier");
    window.SpikeMarketSync?.setActive(activeView === "market");
    window.SpikeTradingBooks?.setActive(activeView === "trading-books");
    window.SpikeOrderflow?.setActive(activeView === "orderflow");
    window.SpikeLanglang?.setActive(activeView === "langlang");
    window.SpikeMondayFib?.setActive(activeView === "monday-fib");
    window.SpikeLeaderPage?.setActive(activeView === "auto-leaders");
    window.SpikeYoloDetect?.setActive(activeView === "yolo-detect");
    window.SpikeRektproof?.setActive(activeView === "rektproof");
    window.SpikeKnowledge?.setView(activeView);
    window.SpikeModels?.setActive(activeView === "models");
    window.SpikeDatasets?.setActive(activeView === "datasets");
    window.SpikeStrategies?.setActive(activeView === "strategies");
    window.SpikePaper?.setActive(activeView === "paper");
    window.SpikeYolo?.setActive(activeView === "yolo");
    window.SpikeVisionModule?.setActive(activeView === "vision").catch(() => {});
    window.SpikeNotifications?.setActive(activeView === "notifications");
    window.SpikeV130?.setActive(activeView === "retest");
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
  const RSI_GLOBAL_LINES_BASIS = "v11_2_box_joint_rsi7_same_tf_streak_next_open_v1_net_cost";
  const RSI_LINES_BASIS = "v11_2_box_joint_rsi7_since_entry_same_tf_streak_next_open_v2_net_cost";
  const linesSnapshot = () => ["baseline", "previous", "legacy"].includes(state.lines.ledger?.selected_performance_version);
  const linesSnapshotName = () => state.lines.ledger?.selected_performance_version === "legacy" ? "V11.2 升级前记录" : state.lines.ledger?.selected_performance_version === "previous" ? "上一版退出快照" : "原价格退出快照";
  function rsiProgress(p) {
    if (p.performance_version !== RSI_LINES_BASIS && p.basis !== RSI_LINES_BASIS) return "";
    if (p.rsi_exit_pending) return " · 第7个空头大菱形已确认，待次根开盘全平";
    if (!p.rsi_counter_known) return " · RSI 入场后计数暂不可用";
    return ` · 入场后空头大菱形 ${number(p.rsi_run_side === -1 ? p.rsi_run_count : 0)} / 7`;
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
        ? `入场 ${price(p.entry_price)} · 已持有 ${number(p.bars_held)} 根 · ${snapshot ? `${linesSnapshotName()}，不再更新` : "按最新收盘估值"}${snapshot ? "" : rsiProgress(p)}`
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
    return `<article class="shadow-event-card lines-card long ${stateClass}${outcome ? " " + outcome.className : ""}${item.is_fresh ? " is-fresh" : ""}"><button type="button" class="card-primary-action" data-line-id="${escapeHTML(item.id)}" data-tradingview-action="lines" data-tv-symbol="${escapeHTML(item.symbol)}" data-tv-timeframe="${escapeHTML(item.timeframe)}" title="点击整张卡片，在${tradingViewTargetLabel()} 打开" aria-label="在${tradingViewTargetLabel()} 打开 ${escapeHTML(shortSymbol(item.symbol))} ${escapeHTML(timeframeLabel(item.timeframe))}"></button><span class="signal-card-top"><span class="card-symbol"><strong>${escapeHTML(shortSymbol(item.symbol))}</strong><small>OKX · ${escapeHTML(quoteSymbol(item.symbol))} 永续</small></span><span class="card-timeframe">${escapeHTML(timeframeLabel(item.timeframe))}</span></span><span class="signal-card-direction"><span class="card-direction">↑ ${escapeHTML(title)}</span><span class="shadow-v8-badge ${stateClass}">${item.is_fresh ? "新 · " : ""}${escapeHTML(stateName)}</span></span><span class="card-price-label">信号收盘价</span><span class="card-price">${escapeHTML(price(item.close))}</span>${performance}${jointReceiptsHTML(item)}<dl class="shadow-event-facts">${facts}${geometry}</dl><span class="card-footer"><time title="${escapeHTML(fullDate(item.bar_close_ms))} 北京时间">${escapeHTML(shortDate(item.bar_close_ms))} 收盘${delay}</time><span class="card-open" data-tradingview-label="整卡打开 TradingView ↗" aria-hidden="true">整卡打开 TradingView ↗</span></span>${manualObservationButton({...item, side: item.side || "long"})}</article>`;
  }
  function jointNotificationText(channels) {
    if (!channels || !Object.keys(channels).length) return "已收盘 K 线 · 通知状态同步中 · 不下单";
    const parts = ["bark", "telegram"].map((channel) => {
      const value = channels[channel], name = channel === "bark" ? "Bark" : "TG";
      if (!value?.configured) return `${name} 未配置`;
      if (!value.enabled || value.activated_ms == null) return `${name} 待启用`;
      return `${name} 已启用 · 成功 ${number(value.sent || 0)}${value.pending ? ` · 待发 ${number(value.pending)}` : ""}${value.unknown ? ` · 结果未知 ${number(value.unknown)}` : ""}${value.failed ? ` · 失败 ${number(value.failed)}` : ""}`;
    });
    return `${parts.join("；")} · 仅通知启用后新信号，收盘30分钟内 · 不下单`;
  }
  function jointReceiptsHTML(item) {
    if (item.kind !== "joint" || linesSnapshot()) return "";
    const labels = { sent: "已发送", pending: "待发送", sending: "发送中", unknown: "结果未知", failed: "发送失败", skipped: "已跳过", history: "未推送" };
    return `<span class="notification-stack">${["bark", "telegram"].map((channel) => {
      const value = item.notifications?.[channel]?.status || "history";
      const label = value === "sent" ? "服务已接受" : labels[value] || "未推送";
      return `<span class="row-status ${value === "sent" ? "sent" : "muted"}">${channel === "bark" ? "Bark" : "TG"} · ${escapeHTML(label)}</span>`;
    }).join("")}</span>`;
  }
  function renderLines() {
    const lines = state.lines;
    const kind = lines.kind || (state.view === "joints" ? "joint" : "break");
    const status = lines.status || {};
    const configured = status.configured === true;
    const lineVersion = linesSnapshot() ? "SPIKE V11.2 · 旧版归档" : "SPIKE V12.8";
    $("lines-policy-title").textContent = `${lineVersion} · ${kind === "joint" ? "突破+spike" : "趋势线突破"}`;
    $("lines-policy-text").textContent = kind === "joint" ? jointNotificationText(status.notifications) : "已收盘 K 线 · 与回测同一套代码 · 只显示 · 不推送 · 不下单";
    $("lines-rule-note").textContent = kind === "joint"
      ? "上级：15m 看 1H，30m 看 2H，1H 看 4H，4H 看日线 · 每个 V9 框只算第一次"
      : "本周期主高点与局部触点结构 · 已收盘确认";
    const policyVersion = lines.ledger?.basis || status.performance_policy?.version;
    const rsiPolicy = policyVersion === RSI_LINES_BASIS;
    $("lines-exit-rule").classList.toggle("hidden", kind !== "joint");
    $("lines-exit-rule").textContent = !lines.ledger && lines.versionOptions
      ? "正在读取所选退出规则…"
      : linesSnapshot()
      ? `${linesSnapshotName()} · 数值固定，不是当前持仓`
      : rsiPolicy
        ? "退出：从本仓开仓后开始计数 · 同周期大菱形连续同色、异色重置 · 第7个空头大菱形收盘确认，次根开盘全平 · 原保护先触发先退出"
        : policyVersion === RSI_GLOBAL_LINES_BASIS ? "旧全局计数仍在运行 · 正在修正为从本仓开仓后计数"
          : status.protocol === "spike-v128-lines-monitor-v1"
            ? "V12.8：原止损、2R启动4ATR跟随保护、原始反向确认退出 · 模拟参考"
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
    if(workspaceView()){
      const count=status.runtime?.v130?.signals_24h;
      $('nav-signal-count').textContent=finite(count)?number(count):'—';
      $('sidebar-runtime').textContent=online?status.started_at_ms?`已运行 ${duration(Date.now()-Number(status.started_at_ms))}`:'服务运行中':'连接中断 · 自动重连';
      return;
    }
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
    const timeNote = linesSnapshot() ? `${linesSnapshotName()} · 固定快照 ${fullDate(data.performance_snapshot_ms || data.as_of_ms)} · 不作新旧规则同步收益对照`
      : data ? `当前规则回算 · 更新 ${clockTime(data.as_of_ms)}${mixed ? " · 退出规则更新中，部分记录仍为旧版" : ""}` : "等待同步";
    $("lines-stats-basis").textContent = `按信号收盘日归属 · 北京时间 · 周一开始 · 周期、搜索、「只看实时」同样生效 · ${timeNote}`;
  }
  async function loadLinesStatus() {
    try {
      const status = await api("/api/lines/status");
      state.lines.status = status;
      const recent = status.recent_24h || {};
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
    if (["sent", "delivered", "success"].includes(value)) return ["服务已接受", "sent"];
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
      const description = className === "sent" ? `${name} 服务已接受推送，不代表手机已收到或已读` : `${name} · ${label}`;
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
      className: "outcome-unknown", badge: performance?.reason === "awaiting_next_closed_bar" ? "等待入场参考" : "仅入场参考", value: "—", valueLabel: "当前 R",
      peak: "—", stop: finite(originalSignal(item)?.initial_stop) ? price(originalSignal(item).initial_stop) : "—",
      stopLabel: "初始 SL", note: ({awaiting_next_closed_bar:"等待下一根已收盘 K 线同步",next_bar_is_gap:"入场行情缺口，参考状态不可用",data_gap_censored:"行情缺口，参考跟踪中断",delayed_risk_invalid:"入场参考风险无效"})[performance?.reason] || "持仓路径暂不可用",
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
  let signalRowsSignature = null;
  function renderSignals() {
    if (state.view === "signals" && state.signalPane === "reclaim") {
      window.SpikeSignalWorkspace?.render(null, state.signalLayout, "reclaim");
      return;
    }
    const items = sourceItems(), data = state.ledger;
    const history = state.signalLayout === "history";
    $("signal-outcome-control").classList.toggle("hidden", history);
    $("signal-overview").classList.toggle("hidden", history);
    $("history-statistics").classList.toggle("hidden", !history);
    $("signal-statistics-title").textContent = history ? "历史开单统计" : "信号统计";
    $("signal-sort-note").textContent = history ? "仅已结束记录 · 按退出记录时间排序 · 净 R 缺值排最后。" : "R 排序：运行中用浮动 R，已结束用退出 R；缺值排最后。";
    $("sort-filter").querySelectorAll("option").forEach((option) => {
      if (option.value === "newest") option.textContent = history ? "最近结束优先" : "最新信号优先";
      if (option.value === "oldest") option.textContent = history ? "最早结束优先" : "最早信号优先";
    });
    const loaded = Boolean(data), total = data?.total || 0;
    const confirmed = state.signalScope === "confirmed", warmup = state.view === "warmup";
    $("filtered-count").textContent = loaded ? `${number(total)} 条` : "—";
    const retest = state.signalScope === "retest" && !warmup;
    $("signal-section-title").textContent = retest ? "V13.1 回踩确认 · 多空" : confirmed ? "YOLO 补充确认" : signalQuerySource() === "legacy" ? "V9 旧版记录 · 多空" : "V12.8 启动 · 多空";
    $("signal-scope-note").textContent = retest
      ? "突破 → 回踩守住 → 再突破收盘确认；次开盘参考、原止损，最多等待 24 根。价格与 R 为信号参考。"
      : confirmed ? "YOLO 追加确认关联原始 SPIKE 启动，不重复计数。"
      : signalQuerySource() === "legacy" ? "升级前的 V9 记录，结果停止更新，保留通知回执，不作为 V12.8 信号。" : "V12.8 默认规则；15m 收盘须符合已完成 H1 SMA60 方向。价格与 R 为信号参考。";
    $("signal-window-note").textContent = loaded ? `共 ${number(total)} 条 · 第 ${state.page + 1} / ${Math.max(1, Math.ceil(total / SIGNAL_PAGE_SIZE))} 页` : "正在读取全量统计";
    $("load-more-signals").classList.toggle("hidden", !data?.has_more);
    $("load-more-signals").disabled = state.syncing;
    $("load-more-signals").textContent = "下一页 →";
    $("load-earlier-signals").classList.toggle("hidden", state.page === 0);
    $("load-earlier-signals").disabled = state.syncing;
    $("load-earlier-signals").textContent = "← 上一页";
    $("signal-empty").classList.toggle("hidden", items.length > 0);
    if (!items.length) {
      $("signal-empty-title").textContent = state.errors[sourceKey()] ? "信号数据暂时不可用" : loaded ? history ? "没有已结束的开单" : "没有符合筛选的信号" : "正在读取信号";
      $("signal-empty-description").textContent = loaded ? history ? "可调整时间范围、合约、方向或周期；运行中的记录在信号卡片查看。" : "可调整时间范围、方向、周期或状态。" : "正在同步已收盘行情与统计。";
    }
    const focused = document.activeElement?.dataset;
    const focusedSummary = document.activeElement?.matches?.(".signal-row-details > summary")
      ? document.activeElement.parentElement.dataset.signalRow : null;
    const openRows = new Set(Array.from($("signal-rows").querySelectorAll(".signal-row-details[open]"), el => el.dataset.signalRow));
    const openChecks = new Set(Array.from($("signal-rows").querySelectorAll(".pb-tv-check[open]"), (el) => el.dataset.signalCheck));
    const rowsSignature = JSON.stringify([state.signalLayout, state.signalDetailId, items,
      state.signalLayout === 'cards' ? Math.floor(signalClock()/60000) : null]);
    if (signalRowsSignature !== rowsSignature) {
      $("signal-rows").innerHTML = state.signalLayout === "history" ? signalHistoryHTML(items)
        : `<div class="signal-card-grid">${items.map((item) => signalCardHTML(item)).join("")}</div>`;
      $("signal-rows").querySelectorAll(".signal-row-details").forEach(el => { el.open = openRows.has(el.dataset.signalRow); });
      signalRowsSignature = rowsSignature;
    }
    document.querySelectorAll("[data-signal-layout]").forEach((button) => {
      const selected = button.dataset.signalLayout === state.signalLayout;
      button.classList.toggle("selected", selected); button.setAttribute("aria-pressed", String(selected));
    });
    $("signal-rows").querySelectorAll(".pb-tv-check").forEach((el) => { el.open = openChecks.has(el.dataset.signalCheck); });
    $("signal-footer-note").textContent = warmup ? "预热回算 · 不触发通知 · 北京时间" : "已收盘确认 · 北京时间";
    renderLedgerStats();
    if (history) renderHistoryStatistics();
    window.SpikeSignalWorkspace?.render(state.ledger, state.signalLayout, state.signalPane);
    renderTradingViewButtons();
    window.SpikeMotion?.cards('signals',$("signal-rows").querySelectorAll('.signal-card'));
    if (focusedSummary) Array.from($("signal-rows").querySelectorAll(".signal-row-details")).find(el => el.dataset.signalRow === focusedSummary)?.querySelector("summary")?.focus({ preventScroll: true });
    if (focused?.signalId) Array.from($("signal-rows").querySelectorAll("[data-signal-id]")).find((card) => card.dataset.signalId === focused.signalId)?.focus({ preventScroll: true });
    if (focused?.signalDetail) Array.from($("signal-rows").querySelectorAll("[data-signal-detail]")).find((button) => button.dataset.signalDetail === focused.signalDetail)?.focus({ preventScroll: true });
  }
  function changeSignalPane(pane) {
    if (!['records','overview','account','reclaim'].includes(pane) || pane === state.signalPane) return;
    if (state.signalPane === 'records') state.recordLayout = state.signalLayout;
    state.signalPane = pane;
    if (pane !== 'account' && pane !== 'reclaim') state.signalLayout = pane === 'overview' ? 'history' : state.recordLayout;
    state.page = 0;
    invalidateSignalQuery();
    window.SpikeSignalWorkspace?.render(null, state.signalLayout, pane);
    window.SpikeOKXExecution?.setActive(state.view === 'signals' && pane === 'account');
    refresh('navigation');
  }
  window.SpikeSignals = {selectPane:changeSignalPane};
  function changeSignalLayout(layout) {
    if (!["history", "cards"].includes(layout) || state.signalLayout === layout) return;
    state.signalLayout = layout;
    state.recordLayout = layout;
    applySignalFilters();
  }
  function renderHistoryStatistics() {
    const target = $("history-statistics"), stats = state.ledger?.closed_statistics;
    target.querySelectorAll("details").forEach((el) => { state.historyStatsOpen[el.dataset.historyBreakdown] = el.open; });
    if (!stats) { target.innerHTML = `<p class="ledger-basis">${state.errors[sourceKey()] ? "历史统计暂时不可用，请刷新重试。" : "正在读取全部已结束记录…"}</p>`; return; }
    const ratio = (v) => finite(v) ? Number(v).toFixed(2) : "—";
    const percent = (v) => finite(v) ? `${(Number(v) * 100).toFixed(1)}%` : "—";
    const metric = (label, value, note, r = null) => `<article><span>${label}</span><strong class="${finite(r) && r > 0 ? "r-positive" : finite(r) && r < 0 ? "r-negative" : ""}">${escapeHTML(value)}</strong><small>${escapeHTML(note)}</small></article>`;
    const breakdown = (rows) => `<div class="ledger-table-wrap"><table><thead><tr><th>分组</th><th>结束笔数</th><th>胜 / 负 / 平</th><th>胜率</th><th>净 R</th><th>平均 R</th><th>盈利因子</th></tr></thead><tbody>${(rows || []).map((row) => `<tr><th scope="row">${escapeHTML(row.label)}</th><td>${number(row.total)}</td><td>${number(row.wins)} / ${number(row.losses)} / ${number(row.breakeven)}</td><td>${percent(row.win_rate)}</td><td>${signedR(row.net_r)}</td><td>${signedR(row.average_r)}</td><td>${ratio(row.profit_factor)}</td></tr>`).join("")}</tbody></table></div>`;
    target.innerHTML = `<div class="history-stat-grid">
      ${metric("已结束", number(stats.total) + " 笔", `胜 ${number(stats.wins)} · 负 ${number(stats.losses)} · 平 ${number(stats.breakeven)}`)}
      ${metric("累计净 R", signedR(stats.net_r), `全部 ${number(stats.measured)} 笔有退出 R`, stats.net_r)}
      ${metric("胜率", percent(stats.win_rate), "盈利笔数 / 有退出 R 的结束笔数")}
      ${metric("最大累计 R 回撤", finite(stats.max_drawdown_r) ? `${ratio(stats.max_drawdown_r)}R` : "—", "按退出先后累计；同刻退出合并")}
    </div>
    <div class="history-stat-extras"><details class="history-stat-details" data-history-breakdown="metrics"><summary>更多统计与盈亏明细</summary><div class="history-stat-grid secondary">
      ${metric("平均净 R", signedR(stats.average_r), "每笔已结束记录的平均结果", stats.average_r)}
      ${metric("盈利因子", ratio(stats.profit_factor), "盈利 R 合计 / 亏损 R 绝对值")}
      ${metric("平均盈亏比", ratio(stats.payoff_ratio), "平均盈利 R / 平均亏损 R 绝对值")}
      ${metric("平均持有时长", duration(stats.average_holding_ms), `${number(stats.measured_holding)} 笔时间完整的结束记录`)}

      ${metric("平均盈利", signedR(stats.average_win_r), `${number(stats.wins)} 笔盈利`, stats.average_win_r)}
      ${metric("平均亏损", signedR(stats.average_loss_r), `${number(stats.losses)} 笔亏损`, stats.average_loss_r)}
      ${metric("最佳单笔", signedR(stats.best_r), "已结束净 R 最高值", stats.best_r)}
      ${metric("最差单笔", signedR(stats.worst_r), "已结束净 R 最低值", stats.worst_r)}
      ${metric("中位数净 R", signedR(stats.median_r), "已结束记录的中间水平", stats.median_r)}
      ${metric("最长连续盈利", finite(stats.max_consecutive_wins) ? `${stats.max_consecutive_wins} 笔` : "—", "保本打断；顺序不明时不计算")}
      ${metric("最长连续亏损", finite(stats.max_consecutive_losses) ? `${stats.max_consecutive_losses} 笔` : "—", "保本打断；顺序不明时不计算")}
      ${metric("缺失退出 R", `${number(stats.missing_r)} 笔`, "保留在历史列表，不补零")}
    </div></details>
    <details class="ledger-breakdown" data-history-breakdown="groups"><summary>按周期与方向统计<span>全部已结束记录</span></summary>${breakdown([...(stats.by_timeframe || []), ...(stats.by_side || [])])}</details>
    <details class="ledger-breakdown" data-history-breakdown="reasons"><summary>按退出原因统计</summary>${breakdown(stats.by_exit_reason)}</details>
    <p class="ledger-basis">按退出记录日归属 · 北京时间 · 全部筛选结果，翻页与排序不影响统计。净 R 沿用记录中的成本，不重复扣费；累计 R 是等风险参考，非账户收益率。无亏损或无可用样本的比值显示“—”。</p>
    ${stats.missing_r || stats.missing_exit_time || stats.ambiguous_exit_order ? `<p class="ledger-basis">${stats.missing_r || stats.missing_exit_time ? `缺失退出 R ${number(stats.missing_r)} 笔、退出时间 ${number(stats.missing_exit_time)} 笔；缺失 R 不进入收益及胜率，记录不完整时不计算回撤与连续结果。` : ""}${stats.ambiguous_exit_order ? "同刻退出包含不同结果，连胜连亏顺序无法确认。" : ""}</p>` : ""}
    <p class="ledger-basis">${state.errors[sourceKey()] ? "同步失败，保留上次统计快照" : `更新 ${clockTime(state.ledger.as_of_ms)}`}</p></div>`;
    target.querySelectorAll("details").forEach((el) => { el.open = Boolean(state.historyStatsOpen[el.dataset.historyBreakdown]); });
  }
  // History is another view of the server-paginated ledger. Entry/exit facts
  // come only from its projection, never from signal-close prices or peak R.
  function signalHistoryHTML(items) {
    if (!items.length) return "";
    const reasons = { initial_stop: "初始止损", trailing_stop: "跟随保护", raw_opposite_next_open: "反向信号退出",
      awaiting_next_closed_bar: "等待入场参考", next_bar_is_gap: "入场行情缺口", data_gap_censored: "行情缺口，跟踪中断", delayed_risk_invalid: "入场风险无效" };
    const time = (value) => `<time title="${escapeHTML(fullDate(value))} 北京时间">${escapeHTML(shortDate(value))}</time>`;
    const rows = items.map((item, index) => {
      const p = signalPerformance(item) || {}, status = p.status;
      const active = status === "active", closed = ["profit", "loss", "breakeven"].includes(status);
      const label = active ? "运行中" : closed ? { profit: "盈利退出", loss: "亏损退出", breakeven: "保本退出" }[status] : "状态未知";
      const r = active ? p.current_r : closed ? p.exit_r : null;
      const rClass = finite(r) && Number(r) > 0 ? "r-positive" : finite(r) && Number(r) < 0 ? "r-negative" : "";
      const reason = reasons[p.exit_reason || p.reason] || (closed ? "已结束" : active ? "已收盘 K 线跟踪" : "未跟踪或记录缺失");
      const expanded = state.signalDetailId === String(item.id), detailId = `signal-history-detail-${index}`;
      const side = item.side === "long" ? "long" : item.side === "short" ? "short" : "neutral";
      const fact = (title, value) => `<div><dt>${escapeHTML(title)}</dt><dd>${escapeHTML(value)}</dd></div>`;
      return `<tr class="signal-history-row">
        <th scope="row"><strong>${escapeHTML(shortSymbol(item.symbol))}</strong><small><span class="history-side ${side}">${escapeHTML(sideName(item.side))}</span> · ${escapeHTML(timeframeLabel(item.timeframe))}</small></th>
        <td><span>${escapeHTML(price(p.entry_price))}</span><small>${time(p.entry_time_ms)}</small></td>
        <td class="history-secondary">${escapeHTML(price(p.initial_stop ?? originalSignal(item)?.initial_stop))}</td>
        <td class="history-secondary">${escapeHTML(price(closed ? p.exit_price : null))}<small>${closed ? time(p.exit_time_ms) : "—"}</small></td>
        <td><strong class="${rClass}">${escapeHTML(signedR(r))}</strong><small>${label}${active ? " · 浮动" : ""}</small></td>
        <td class="history-secondary">${finite(p.bars_held) ? `${escapeHTML(number(p.bars_held))} 根` : "—"}</td>
        <td class="history-actions"><button type="button" data-signal-detail="${escapeHTML(item.id)}" aria-expanded="${expanded}" aria-controls="${detailId}" aria-label="${escapeHTML(shortSymbol(item.symbol))} 开单详情">${expanded ? "收起" : "详情"}</button></td>
      </tr><tr id="${detailId}" class="signal-history-detail"${expanded ? "" : " hidden"}><td colspan="7">
        <div class="history-detail-heading"><strong>${escapeHTML(item.symbol)} · ${escapeHTML(timeframeLabel(item.timeframe))} · ${escapeHTML(sideName(item.side))}</strong><span>${escapeHTML(reason)}</span></div>
        <dl>${fact("信号确认 · 北京时间", fullDate(item.bar_close_ms))}${fact("参考入场 · 北京时间", fullDate(p.entry_time_ms))}
          ${fact("参考入场价", price(p.entry_price))}${fact("初始止损", price(p.initial_stop ?? originalSignal(item)?.initial_stop))}
          ${fact("当前保护价", price(p.stop_price))}${fact("退出价", price(closed ? p.exit_price : null))}
          ${fact("退出记录 · 北京时间", fullDate(closed ? p.exit_time_ms : null))}${fact("持仓 K 线", finite(p.bars_held) ? `${number(p.bars_held)} 根` : "—")}
          ${fact(active ? "浮动净 R" : "退出净 R", signedR(r))}${fact("峰值 R", signedR(p.peak_r))}
          ${fact("往返成本", finite(p.round_trip_cost) ? `${(Number(p.round_trip_cost) * 100).toFixed(2)}%` : "—")}${fact("跟踪更新 · 北京时间", fullDate(p.updated_at_ms))}</dl>
        <p class="history-record-id">记录 ID · ${escapeHTML(item.id)}</p>
        <div class="history-detail-actions">${manualObservationButton(item)}</div>
      </td></tr>`;
    }).join("");
    return `<div class="signal-history-wrap"><table class="signal-history-table" aria-label="历史开单列表"><thead><tr><th scope="col">合约 / 方向</th><th scope="col">参考入场</th><th scope="col" class="history-secondary">初始止损</th><th scope="col" class="history-secondary">退出价</th><th scope="col">净 R / 状态</th><th scope="col" class="history-secondary">持仓</th><th scope="col">操作</th></tr></thead><tbody>${rows}</tbody></table></div>`;
  }
  function renderLedgerStats() {
    const data = state.ledger, stats = data?.stats;
    // Unknown outcomes belong in the denominator; an untracked cohort cannot
    // establish that zero positions are active or closed. Use full-cohort
    // statistics, never the current page of cards, to make this distinction.
    const unknownCount = (row) => row ? numeric(row.unknown, Math.max(0, row.total - row.active - row.closed)) : 0;
    const allUnknown = (row) => row?.total > 0 && unknownCount(row) === row.total;
    const unavailable = allUnknown(stats), empty = stats?.total === 0;
    const unknown = unknownCount(stats);
    const notice = $("stats-availability");
    notice.classList.toggle("hidden", !stats || unknown === 0);
    notice.textContent = unavailable && state.status?.protocol === "spike-burst-v128-yolo-confirmation-v1"
      ? `这 ${number(unknown)} 条 SPIKE 信号仅记录入场参考，尚未跟踪持仓与退出；运行状态、R 和胜率暂不可用。`
      : `${number(unknown)} 条信号的持仓或退出状态未知，未计入运行中、已结束及胜率；未知不代表没有持仓。`;
    const setR = (id, value) => {
      const node = $(id);
      node.textContent = signedR(value);
      node.classList.toggle("r-positive", finite(value) && Number(value) > 0);
      node.classList.toggle("r-negative", finite(value) && Number(value) < 0);
    };
    setR("stats-realized", stats?.realized_r);
    setR("stats-floating", stats?.floating_r);
    $("stats-closed-note").textContent = unavailable ? "退出状态未知 · 暂无退出 R" : empty ? "当前筛选无信号" : stats ? `${stats.measured_closed} 笔有 R / ${stats.closed} 笔已结束` : "等待统计";
    $("stats-active-note").textContent = unavailable ? "持仓状态未知 · 暂无浮动 R" : empty ? "当前筛选无信号" : stats ? `${stats.measured_active} 笔有 R / ${stats.active} 笔运行中` : "等待统计";
    $("stats-winrate").textContent = finite(stats?.win_rate) ? `${(stats.win_rate * 100).toFixed(1)}%` : "—";
    $("stats-win-note").textContent = unavailable ? "暂无可计算胜率的退出记录" : empty ? "当前筛选无信号" : stats ? `盈利 ${stats.profit} · 亏损 ${stats.loss} · 保本 ${stats.breakeven}` : "只统计有退出 R 的信号";
    $("stats-total").textContent = stats ? number(stats.total) : "—";
    $("stats-side-note").textContent = stats ? `多 ${stats.long} · 空 ${stats.short} · 状态未知 ${unknown}${unavailable ? "" : ` · 缺 R ${stats.missing_r}`}` : "每次确认计一条";
    const cellR = (r) => `<td class="${finite(r) && r > 0 ? "r-positive" : finite(r) && r < 0 ? "r-negative" : ""}">${escapeHTML(signedR(r))}</td>`;
    $("stats-timeframes").innerHTML = (data?.by_timeframe || []).map((row) => `<tr><th scope="row">${escapeHTML(row.timeframe)}</th><td>${row.total}</td><td>${allUnknown(row) ? "—" : row.active}</td><td>${allUnknown(row) ? "—" : row.closed}</td><td>${unknownCount(row)}</td>${cellR(row.realized_r)}${cellR(row.floating_r)}<td>${finite(row.win_rate) ? `${(row.win_rate * 100).toFixed(1)}%` : "—"}</td></tr>`).join("");
    $("stats-basis").textContent = `按原始信号收盘日归属 · 北京时间 · 周一开始 · 全部筛选条件生效 · ${state.errors[sourceKey()] ? "同步失败，保留上次快照" : data ? `更新 ${clockTime(data.as_of_ms)}` : "等待同步"}`;
  }
  function manualObservationButton(item) {
    if (!/^[A-Z0-9]{2,24}-USDT-SWAP$/.test(item.symbol || "") || !["long", "short"].includes(item.side)) return "";
    const ref = `${item.kind || "signal"}:${item.id} · ${item.source || "live"} · bar_close_ms=${item.bar_close_ms ?? "unknown"}`;
    return `<button type="button" class="research-button manual-observe-button" data-manual-observe data-manual-symbol="${escapeHTML(item.symbol)}" data-manual-side="${escapeHTML(item.side)}" data-manual-timeframe="${escapeHTML(timeframeLabel(item.timeframe))}" data-manual-ref="${escapeHTML(ref)}">按个人原则看这笔</button>`;
  }
  function signalCardHTML(item, now = signalClock()) {
    const confirmed = isConfirmed(item), original = originalSignal(item), direct = directReceipt(item);
    const side = item.side === "short" ? "short" : item.side === "long" ? "long" : "neutral";
    const venue = String(item.venue || "OKX").toUpperCase();
    const fresh = isFresh(item, now);
    const retest = isRetest(item);
    const status = retest ? "V13.1 回踩确认" : confirmed ? "YOLO 补充确认" : `${signalVersion(item)} 启动`, caption = retest ? "确认收盘价" : "信号收盘价";
    const outcome = performanceView(item);
    return `<article class="signal-card ${side} ${outcome.className}${confirmed || direct ? "" : " candidate-card"}${fresh ? " is-fresh" : ""}">
      <span class="signal-card-top"><span class="card-symbol"><strong>${escapeHTML(shortSymbol(item.symbol))}</strong><small>${escapeHTML(venue)} · ${escapeHTML(quoteSymbol(item.symbol))} 永续</small></span><span class="card-timeframe">${escapeHTML(timeframeLabel(item.timeframe))}</span></span>
      <span class="signal-card-direction"><span class="card-direction-group"><span class="card-direction"><span class="direction-icon" aria-hidden="true"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><path d="${side === "short" ? "m3 6 6 6 4-4 8 10M15 18h6v-6" : side === "long" ? "m3 18 6-6 4 4 8-10M15 6h6v6" : "M5 12h14"}"/></svg></span><span class="direction-name">${sideName(item.side)}</span></span><span class="direction-stage">${confirmed || retest ? "确认" : "启动"}</span></span></span>
      <span class="signal-card-price"><span class="card-price-label">${caption}</span><span class="card-price">${escapeHTML(price(item.price))}</span></span>
      <span class="performance-badge">${escapeHTML(outcome.badge)}</span>
      <dl class="card-performance"><div><dt>${escapeHTML(outcome.valueLabel)}</dt><dd>${escapeHTML(outcome.value)}</dd></div><div><dt>最高 R</dt><dd>${escapeHTML(outcome.peak)}</dd></div><div><dt>${escapeHTML(outcome.stopLabel)}</dt><dd>${escapeHTML(outcome.stop)}</dd></div></dl>
      <details class="signal-row-details" data-signal-row="${escapeHTML(item.id)}"><summary>形态详情<span class="signal-confirmed-time">${escapeHTML(shortDate(item.bar_close_ms))} 收盘</span></summary>
      <span class="card-recency">${escapeHTML(venue)} · ${escapeHTML(quoteSymbol(item.symbol))} 永续 · ${displayScope(item) === "legacy" ? "V9 旧版记录" : isWarmupRecord(item) ? "预热历史" : item.source === "replay" ? "历史回放" : fresh ? "新 · " + ageLabel(item.bar_close_ms) : ageLabel(item.bar_close_ms)}</span>
      <span class="model-card-status"><span class="model-badge ${confirmed || retest ? "confirmed" : "pending"}">${escapeHTML(status)}</span><span>${retest ? `等待 ${escapeHTML(number(item.wait_bars))} 根 · ${finite(item.trail_atr) ? `${escapeHTML(item.trail_atr)}ATR 追踪` : "已收盘"}` : displayScope(item) === "legacy" ? "旧版归档 · 不补发" : isWarmupRecord(item) ? "预热回算 · 不通知" : item.source === "replay" ? "回放记录 · 不通知" : confirmed ? "补充确认 · 非启动门" : "第一阶段 · 已收盘"}</span></span>
      ${retest && finite(item.anchor_close_ms) ? `<span class="card-origin">原启动 ${escapeHTML(shortDate(item.anchor_close_ms))} · 回踩 ${escapeHTML(shortDate(item.retest_close_ms))}</span>` : ""}
      ${confirmed && item.indicator ? `<span class="card-origin">${signalVersion(original)} ${escapeHTML(price(original.price))} · ${escapeHTML(shortDate(original.bar_close_ms))}</span>` : ""}
      <span class="performance-note">${escapeHTML(outcome.note)} · 信号收盘参考，并非账户实际成交</span>
      </details>
    </article>`;
  }
  function applySignalFilters() {
    state.page = 0; invalidateSignalQuery(); refresh();
  }
  function syncSignalLocation(){
    if(state.view==='signals' && state.signalPane==='reclaim'){
      window.SpikeNavigation?.sync('signals',{pane:'reclaim',...window.SpikeReclaimPaper?.route()});return;
    }
    if(signalView())window.SpikeNavigation?.sync(state.view,{pane:state.signalPane,layout:state.signalLayout,q:state.search,timeframe:state.timeframe,side:state.side,period:state.period,outcome:state.outcome,sort:state.sort,scope:state.signalScope,source:state.signalSource,page:state.page});
  }
  function invalidateSignalQuery() {
    syncSignalLocation();
    state.signalQueryRevision++;
    state.signalDetailId = null;
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
    const focusedSummary = document.activeElement?.matches?.(".signal-row-details > summary")
      ? document.activeElement.parentElement.dataset.signalRow : null;
    const focusedSymbol = focused?.tvSymbol, focusedTimeframe = focused?.tvTimeframe;
    $("load-more-watch").classList.toggle("hidden", items.length <= state.watchLimit);
    $("load-more-watch").textContent = `显示更多（${Math.min(state.watchLimit, items.length)} / ${items.length}）`;
    $("watch-rows").innerHTML = items.slice(0, state.watchLimit).map((item) => {
      const valid = !state.errors.markets && !item.error && !item.stale && item.ready !== false;
      const phaseClass = state.errors.markets ? "stale" : item.error ? "error" : item.stale ? "stale" : item.ready === false ? "loading" : item.focus === true ? "ready" : "";
      return `<article class="watch-card"><button type="button" class="card-primary-action" data-tradingview-action="watch" data-tv-symbol="${escapeHTML(item.symbol)}" data-tv-timeframe="${escapeHTML(item.timeframe)}" title="点击整张卡片，在${tradingViewTargetLabel()} 打开" aria-label="在${tradingViewTargetLabel()} 打开 ${escapeHTML(shortSymbol(item.symbol))} ${escapeHTML(quoteSymbol(item.symbol))} ${escapeHTML(timeframeLabel(item.timeframe))}，${escapeHTML(marketPhase(item))}"></button>
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
    const navShadowCount = $("nav-shadow-count");
    if (navShadowCount) navShadowCount.textContent = configured ? number(status.events) : "—";
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
      return `<article class="shadow-event-card ${side} ${admitted ? "admitted" : "filtered"}"><button type="button" class="card-primary-action" data-shadow-id="${escapeHTML(item.id)}" data-tradingview-action="shadow" data-tv-symbol="${escapeHTML(item.symbol)}" data-tv-timeframe="${escapeHTML(item.timeframe)}" title="点击整张卡片，在${tradingViewTargetLabel()} 打开" aria-label="在${tradingViewTargetLabel()} 打开 ${escapeHTML(shortSymbol(item.symbol))} ${escapeHTML(timeframeLabel(item.timeframe))}"></button><span class="signal-card-top"><span class="card-symbol"><strong>${escapeHTML(shortSymbol(item.symbol))}</strong><small>OKX · ${escapeHTML(quoteSymbol(item.symbol))} 永续</small></span><span class="card-timeframe">${escapeHTML(timeframeLabel(item.timeframe))}</span></span><span class="signal-card-direction"><span class="card-direction">${sideArrow(side)} ${sideName(side)} · V7</span><span class="shadow-v8-badge ${admitted ? "admitted" : "filtered"}">${admitted ? "V8 保留" : "V8 过滤"}</span></span><span class="card-price-label">信号收盘价</span><span class="card-price">${escapeHTML(price(item.close))}</span><dl class="shadow-event-facts"><div><dt>距六线边缘</dt><dd>${finite(item.rope_distance_atr) ? `${Number(item.rope_distance_atr).toFixed(2)} ATR` : "—"}</dd></div><div><dt>V8 原因</dt><dd>${admitted ? "未过热" : "超过 3 ATR"}</dd></div></dl><span class="card-footer"><time title="${escapeHTML(fullDate(item.bar_close_ms))} 北京时间">${escapeHTML(shortDate(item.bar_close_ms))}</time><span class="card-open" data-tradingview-label="整卡打开 TradingView ↗" aria-hidden="true">整卡打开 TradingView ↗</span></span></article>`;
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
    $("connection-label").classList.toggle("online", online);
    $("connection-label").classList.toggle("offline", !online);
    $("connection-label").innerHTML = `<i></i>${online ? "已连接" : state.errors.status ? "连接中断" : "连接中"}`;
    $("local-light").classList.toggle("online", online);
    if (!status) $("sidebar-runtime").textContent = state.errors.status ? "连接中断 · 自动重连" : "正在连接服务";
    if (!status) return;
    if ($("strategy-version")) $("strategy-version").textContent = status.runtime?.v130 ? "SPIKE V13.1" : status.protocol?.includes("v128") ? "SPIKE V12.8" : "SPIKE V9 · 等待升级";
    if(workspaceView()){
      const count=status.runtime?.v130?.signals_24h;
      $('nav-signal-count').textContent=finite(count)?number(count):'—';
      $('sidebar-runtime').textContent=online?status.started_at_ms?`已运行 ${duration(Date.now()-Number(status.started_at_ms))}`:'服务运行中':'连接中断 · 自动重连';
      return;
    }
    const scan = status.scan || {};
    const counts = status.counts || {};
    const jointBark = status.joint_notifications?.bark;
    const bark = status.bark ? { ...status.bark,
      sent: numeric(status.bark.sent) + numeric(jointBark?.sent),
      pending: numeric(status.bark.pending) + numeric(jointBark?.pending),
      unknown: numeric(status.bark.unknown) + numeric(jointBark?.unknown),
      failed: numeric(status.bark.failed) + numeric(jointBark?.failed),
      last_success_ms: Math.max(numeric(status.bark.last_success_ms), numeric(jointBark?.last_success_ms)) || null,
    } : jointBark;
    const runtime = status.runtime || {};
    const timeframes = Array.isArray(runtime.timeframes) ? runtime.timeframes : [];
    if (state.view === "signals") $("page-description").textContent = "追踪全市场确认信号，查看参考表现与 OKX 账户。";
    $("metric-timeframes").textContent = timeframes.length ? timeframes.map(timeframeLabel).join(" + ") : "—";
    $("watch-timeframes").textContent = timeframes.length ? timeframes.map(timeframeLabel).join(" / ") : "—";
    const directTimeframes = runtimeTimeframes("direct_timeframes");
    const directScopeTimeframes = $("direct-scope-timeframes");
    if (directScopeTimeframes) directScopeTimeframes.textContent = directTimeframes ? directTimeframes.map(timeframeLabel).join(" / ") : "周期待同步";
    const retestCount = runtime.v130?.signals_24h;
    $("metric-signals").textContent = finite(retestCount) ? number(retestCount) : "—";
    $("nav-signal-count").textContent = finite(retestCount) ? number(retestCount) : "—";
    $("metric-universe").textContent = number(status.universe?.count);
    const scanning = ["running", "scanning", "in_progress", "starting", "bootstrap"].includes(scan.status);
    const scanErrorCount = Array.isArray(scan.errors) ? scan.errors.length : numeric(scan.errors);
    const complete = numeric(scan.completed);
    const total = numeric(scan.total);
    // `counts.ready` describes persisted feature phase and may belong to a
    // prior pass.  It cannot prove every cell has caught the current close.
    $("metric-signals-detail").textContent = "回踩再突破 · 已收盘确认";
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
    const telegramAvailable = notificationChannels().includes("telegram");
    $("telegram-header").textContent = telegramAvailable ? "TG · 来源订阅已接入" : status.telegram?.configured ? "TG · 待启用" : "TG · 状态待同步";
    $("telegram-header").classList.toggle("good", telegramAvailable);
    const tg = status.telegram || {};
    $("tg-state-badge").textContent = tg.configured ? "已配置" : "尚未配置";
    $("tg-description").textContent = "按通知中心的来源订阅发送；服务接受不代表设备已读。";
    $("tg-facts").innerHTML = factsHTML([["服务已接受", number(tg.sent)], ["待发送", number(tg.pending)], ["发送失败", number(tg.failed)], ["最近接受", fullDate(tg.last_success_ms)]]);
    $("service-version").textContent = status.version ? `v${String(status.version).replace(/^v/, "")}` : "本机服务";
    const runtimeFacts = [["监控台", "spike"], ["启动时间", fullDate(status.started_at_ms)], ["服务时间", fullDate(status.now_ms)], ["运行时长", duration(Date.now() - numeric(status.started_at_ms, Date.now()))]];
    if (timeframes.length) runtimeFacts.push(["监控周期", timeframes.map(timeframeLabel).join(" / ")]);
    if (runtime.host) runtimeFacts.push(["主机", runtime.host === "This Mac" ? "Mac（扫描与推送）" : runtime.host]);
    if (runtime.pid) runtimeFacts.push(["进程", runtime.pid]);
    if (runtime.data_dir) runtimeFacts.push(["数据位置", runtime.data_dir]);
    const signalMode = runtime.signal_mode || runtime.strategy || status.strategy;
    if (signalMode) runtimeFacts.push(["信号规则", String(signalMode).replace("Bark 启动与 YOLO 追加确认", "通知来源由通知中心配置")]);
    if (runtime.higher_mode) runtimeFacts.push(["高周期规则", runtime.higher_mode]);
    const gate = runtime.model_gate || {};
    const gateImpact = twoStage() ? `指标启动记录独立运行；仅 YOLO 追加确认需要模型通过。${notificationPolicy()}` : "模型确认通知暂不可用，候选保留等待。";
    const gateIdle = gate.status === "idle" && gate.loaded !== true && Number(gate.queue_depth || 0) === 0;
    const gateNotice = !modelProtocol() ? "模型确认口径尚未同步，原始箭头不会显示为模型确认。" : gate.last_error ? `模型检测异常：${String(gate.last_error)}。${gateImpact}` : gate.status === "error" ? `部分候选检测异常，可在等待确认中查看。${gateImpact}` : gateIdle ? `YOLO 待命；当前没有合格 V12.8 候选，出现候选时才加载模型。${gateImpact}` : gate.loaded !== true ? `YOLO 模型正在加载；V12.8 启动不受影响。${gateImpact}` : "";
    $("model-gate-notice").textContent = gateNotice;
    $("model-gate-notice").classList.toggle("hidden", workspaceView() || state.view === "retest" || state.signalScope === "retest" || !gateNotice);
    runtimeFacts.push(["模型检测", gate.last_error ? "检测异常 · 暂无模型确认" : gate.loaded === true ? "已加载" : "等待加载"]);
    runtimeFacts.push(["通知阶段", notificationPolicy()]);
    if (gate.profile_id || gate.profile) runtimeFacts.push(["模型配置", gate.profile_id || gate.profile]);
    if (gate.last_error) runtimeFacts.push(["模型异常", String(gate.last_error)]);
    if (finite(gate.queue_depth ?? gate.queue)) runtimeFacts.push(["模型待检测", number(gate.queue_depth ?? gate.queue)]);
    runtimeFacts.push(["确认方式", "后台信号出现后，在等待窗口内检测同一段结构；未核验 TradingView 图上信号"]);
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
  let statusRequest = null, shellStatusRequest = null;
  function fetchStatus() {
    if (!statusRequest) statusRequest = api("/api/status").finally(() => { statusRequest = null; });
    return statusRequest;
  }
  function refreshShellStatus(force = false) {
    if (shellStatusRequest) return shellStatusRequest;
    if (!force && state.statusReceivedAt && Date.now() - state.statusReceivedAt < 15000) return Promise.resolve();
    shellStatusRequest = (async () => {
      try {
        state.status = await fetchStatus();
        state.statusReceivedAt = Date.now();
        delete state.errors.status;
      } catch (error) {
        state.errors.status = error.message || "请求失败";
      } finally {
        renderStatus(); renderErrors();
        shellStatusRequest = null;
      }
    })();
    return shellStatusRequest;
  }
  function ledgerPath() {
    const retest = state.signalScope === "retest" && state.view === "signals";
    const pairs = { source: retest ? "live" : signalQuerySource(), confirmation: retest ? "retest" : state.signalScope === "confirmed" ? "yolo" : "raw",
      period: state.period, outcome: state.signalLayout === "history" ? "closed" : state.outcome, sort: state.sort,
      offset: state.page * SIGNAL_PAGE_SIZE, limit: SIGNAL_PAGE_SIZE, search: state.search };
    if (state.timeframe !== "all") pairs.timeframe = apiTimeframe(state.timeframe);
    if (state.side !== "all") pairs.side = state.side;
    return "/api/signals?view=ledger&" + Object.entries(pairs).map(([k, v]) => `${k}=${encodeURIComponent(v)}`).join("&");
  }
  const pendingRefreshes = new Map();
  function syncRefreshButton() {
    const busy = (pendingRefreshes.get(state.view) || 0) > 0;
    const button = $("refresh-button");
    button.disabled = busy;
    button.classList.toggle("loading", busy);
    button.setAttribute("aria-busy", String(busy));
    const label = button.querySelector?.("span");
    if (label) label.textContent = busy ? "刷新中" : "刷新";
  }
  async function refresh(trigger = "manual") {
    if (document.hidden && trigger === "periodic") return;
    const view = state.view;
    pendingRefreshes.set(view, (pendingRefreshes.get(view) || 0) + 1);
    syncRefreshButton();
    try { await refreshView(trigger); }
    finally {
      const remaining = (pendingRefreshes.get(view) || 1) - 1;
      if (remaining) pendingRefreshes.set(view, remaining); else pendingRefreshes.delete(view);
      syncRefreshButton();
    }
  }
  async function refreshView(trigger = "manual") {
    if(window.SpikeModules){
      const requestedView=state.view;
      await viewLoad;
      if(requestedView!==state.view)return;
      try {await window.SpikeModules.ensure(requestedView);} catch(error){
        const message=$('module-load-state');if(message){message.hidden=false;message.textContent=error.message;}return;
      }
      if(requestedView!==state.view)return;
      const message=$('module-load-state');if(message&&!message.hidden){message.hidden=true;activateModules(requestedView);}
    }
    if (document.hidden && trigger === "periodic") return;
    if (state.view === "retest") {
      await Promise.allSettled([refreshShellStatus(trigger === "manual"), window.SpikeV130?.refresh()]);
      return;
    }
    if (state.view === "signals" && state.signalPane === "reclaim") {
      window.SpikeSignalWorkspace?.render(null, state.signalLayout, 'reclaim');
      await Promise.allSettled([refreshShellStatus(trigger === "manual"), trigger === "manual" ? window.SpikeReclaimPaper?.refresh() : null]);
      return;
    }
    if (state.view === "signals" && state.signalPane === "account") {
      await Promise.allSettled([refreshShellStatus(trigger === "manual"), trigger === "manual" ? window.SpikeOKXExecution?.refresh() : null]);
      return;
    }
    if (state.view === "notifications") {
      const pending = [refreshShellStatus(trigger === "manual")];
      if (trigger !== "periodic") pending.push(window.SpikeNotifications?.refresh());
      await Promise.allSettled(pending);
      return;
    }
    // Research pages need the shared connection state, not the signal ledger.
    if (workspaceView() || state.view === "shadow") refreshShellStatus(trigger === "manual");
    if (["learning", "knowledge", "content"].includes(state.view)) { if (trigger !== "periodic") await window.SpikeKnowledge?.refresh(); return; }
    if (state.view === "platform") { if (trigger !== "periodic") await window.SpikePlatform?.refresh(); return; }
    if (state.view === "paper-desk") { if (trigger !== "periodic") await window.SpikePaperDesk?.refresh(); return; }
    if (state.view === "manual") { if (trigger !== "periodic") await window.SpikeManual?.refresh(); return; }
    if (state.view === "copier") { if (trigger !== "periodic") await window.SpikeCopier?.refresh(); return; }
    if (state.view === "market") { await window.SpikeMarketSync?.refresh(); return; }
    if (state.view === "trading-books") { if (trigger !== "periodic") await window.SpikeTradingBooks?.refresh(); return; }
    if (state.view === "orderflow") { if (trigger !== "periodic") await window.SpikeOrderflow?.refresh(); return; }
    if (state.view === "monday-fib") { if (trigger !== "periodic") await window.SpikeMondayFib?.refresh(); return; }
    if (state.view === "auto-leaders") { if (trigger !== "periodic") await window.SpikeLeaderPage?.refresh(); return; }
    if (state.view === "yolo-detect") { if (trigger !== "periodic") await window.SpikeYoloDetect?.refresh(); return; }
    if (state.view === "langlang") { if (trigger !== "periodic") await window.SpikeLanglang?.refresh(); return; }
    if (state.view === "rektproof") { if (trigger !== "periodic") await window.SpikeRektproof?.refresh(); return; }
    if (state.view === "models") { if (trigger !== "periodic") await window.SpikeModels?.refresh(); return; }
    if (state.view === "datasets") { if (trigger !== "periodic") await window.SpikeDatasets?.refresh(); return; }
    if (["research", "factors", "experiments", "backtests"].includes(state.view)) { await window.SpikeResearch.refresh(trigger); return; }
    if (state.view === "strategies") { if (trigger !== "periodic") await window.SpikeStrategies?.refresh(); return; }
    if (state.view === "paper") { await window.SpikePaper?.refresh(trigger); return; }
    if (state.view === "yolo") { if (trigger !== "periodic") await window.SpikeYolo?.refresh(); return; }
    if (state.view === "vision") return;
    if (state.view === "shadow") { await loadShadow(); return; }
    if (state.syncing) { queueRefresh(trigger); return; }
    const revision = state.signalQueryRevision, view = state.view, source = signalQuerySource(), key = sourceKey();
    state.syncing = true;
    try {
      const requests = [{ key: "status", path: "/api/status" }];
      if (signalView()) requests.push({ key, path: ledgerPath() });
      const results = await Promise.allSettled(requests.map((request) => request.key === "status" ? fetchStatus() : api(request.path)));
      if (revision !== state.signalQueryRevision || view !== state.view || source !== signalQuerySource() || key !== sourceKey()) return;
      results.forEach((result, index) => {
        const current = requests[index].key;
        if (result.status === "rejected") { state.errors[current] = result.reason.message || "请求失败"; return; }
        const data = result.value;
        if (current === "status") { state.status = data; state.statusReceivedAt = Date.now(); }
        else {
          if (!Array.isArray(data.items) || !data.stats || !Array.isArray(data.by_timeframe) ||
              (state.signalLayout === "history" && (!data.closed_statistics || data.items.some((item) => !["profit", "loss", "breakeven"].includes(item.performance?.status))))) {
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
  document.addEventListener("click", event => {
    if (event.target.closest?.("#nav-toggle")) {
      if(window.SpikeWorkspaceDisplay)window.SpikeWorkspaceDisplay.toggleNavigation();
      else toggleNavigation(!document.body.classList.contains("nav-open"));
    }
    if (event.target.closest?.("#nav-backdrop")) toggleNavigation(false);
  });
  document.addEventListener("keydown", event => { if (event.key === "Escape") toggleNavigation(false); });
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
  document.querySelectorAll("[data-signal-layout]").forEach((button) => button.addEventListener("click", () => {
    changeSignalLayout(button.dataset.signalLayout);
  }));
  $("signal-rows").addEventListener("click", (event) => {
    const button = event.target.closest("[data-signal-detail]");
    if (!button) return;
    state.signalDetailId = state.signalDetailId === button.dataset.signalDetail ? null : button.dataset.signalDetail;
    renderSignals();
  });
  let searchTimer;
  $("symbol-search").addEventListener("input", (event) => {
    state.search = event.target.value; state.page = 0;
    invalidateSignalQuery(); clearTimeout(searchTimer);
    searchTimer = setTimeout(() => refresh(), 250);
  });
  $("clear-signal-search")?.addEventListener("click", () => {
    clearTimeout(searchTimer);state.search="";$("symbol-search").value="";
    window.SpikeSymbols?.close();applySignalFilters();$("symbol-search").focus();window.SpikeSymbols?.close();
  });
  document.querySelectorAll("[data-ledger-period]").forEach((button) => button.addEventListener("click", () => {
    state.period = button.dataset.ledgerPeriod;
    document.querySelectorAll("[data-ledger-period]").forEach((other) => { const selected = other === button; other.classList.toggle("selected", selected); other.setAttribute("aria-pressed", String(selected)); });
    applySignalFilters();
  }));
  $("reset-signal-filters")?.addEventListener("click", () => {
    clearTimeout(searchTimer);
    Object.assign(state, {search:"", timeframe:"all", side:"all", period:"all", outcome:"all", sort:"newest"});
    $("symbol-search").value=""; $("side-filter").value="all";
    $("outcome-filter").value="all"; $("sort-filter").value="newest";
    document.querySelectorAll("[data-timeframe], [data-ledger-period]").forEach(button => {
      const selected=(button.dataset.timeframe || button.dataset.ledgerPeriod)==="all";
      button.classList.toggle("selected",selected);button.setAttribute("aria-pressed",String(selected));
    });
    applySignalFilters();
  });
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
    const manualButton = event.target.closest("[data-manual-observe]");
    if (manualButton) {
      if (event.type !== "click") return;
      event.preventDefault();
      window.SpikeManual?.fromSignal({ symbol: manualButton.dataset.manualSymbol,
        side: manualButton.dataset.manualSide, timeframe: manualButton.dataset.manualTimeframe,
        signal_ref: manualButton.dataset.manualRef });
      setView("manual");
      return;
    }
    if (type === "signal") return;
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
  window.addEventListener("hashchange", () => setView(location.hash.slice(1), false));
  document.addEventListener("visibilitychange", () => { if (!document.hidden) refresh("periodic"); });
  function restoreSignalRoute(p) {
    if(p.pane==='reclaim' && state.view==='signals'){
      const changed=state.signalPane!=='reclaim';
      state.signalPane='reclaim';
      window.SpikeReclaimPaper?.restore(p);
      if(changed)invalidateSignalQuery();
      renderSignals();return;
    }
    const previous=JSON.stringify([state.search,state.page,state.timeframe,state.side,state.period,state.outcome,state.sort,state.signalScope,state.signalSource,state.signalLayout,state.signalPane]);
    state.signalLayout=['history','cards'].includes(p.layout)?p.layout:'cards';
    state.signalPane=['records','overview','account'].includes(p.pane)?p.pane:'records';
    if(state.signalPane==='overview')state.signalLayout='history';
    else if(state.signalPane==='records')state.recordLayout=state.signalLayout;
    state.search=(p.q||'').slice(0,100);$('symbol-search').value=state.search;
    state.page=Math.max(0,Math.min(100000,Number.parseInt(p.page,10)||0));
    for(const [param,field,id] of [['side','side','side-filter'],['outcome','outcome','outcome-filter'],['sort','sort','sort-filter']]){
      const control=$(id);state[field]=p[param]&&Array.from(control.options).some(x=>x.value===p[param])?p[param]:field==='sort'?'newest':'all';control.value=state[field];
    }
    for(const [param,field,attribute] of [['timeframe','timeframe','timeframe'],['period','period','ledger-period'],['scope','signalScope','signal-scope'],['source','signalSource','signal-source']]){
      const buttons=Array.from(document.querySelectorAll(`[data-${attribute}]`));
      state[field]=p[param]&&buttons.some(b=>b.getAttribute(`data-${attribute}`)===p[param])?p[param]:({scope:'retest',source:'live'}[param]||'all');
      buttons.forEach(b=>{const selected=b.getAttribute(`data-${attribute}`)===state[field];b.classList.toggle('selected',selected);b.setAttribute('aria-pressed',String(selected));});
    }
    const next=JSON.stringify([state.search,state.page,state.timeframe,state.side,state.period,state.outcome,state.sort,state.signalScope,state.signalSource,state.signalLayout,state.signalPane]);
    if(next!==previous)invalidateSignalQuery();
    renderSignals();
  }
  for(const view of ['signals','warmup'])window.SpikeNavigation?.register(view,restoreSignalRoute);
  setView(location.hash.slice(1), false);
  setInterval(() => { $("local-clock").textContent = clockTime(Date.now()); }, 1000);
  $("local-clock").textContent = clockTime(Date.now());
  if (!window.SpikeModules && state.view !== "warmup" && state.view !== "notifications") refresh();
  setInterval(() => refresh("periodic"), 15000);
  // Retired joint/break views no longer poll their status in the background.
})();
