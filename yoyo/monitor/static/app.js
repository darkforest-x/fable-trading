/* Local monitor client. Market data is read-only; app opening requires a click. */
(() => {
  "use strict";

  const $ = (id) => document.getElementById(id);
  const state = {
    view: "signals", signals: [], markets: [], status: null, health: null,
    signalsLoaded: false, marketsLoaded: false, signalTotal: 0, rowLimit: 24, watchLimit: 24, search: "", watchSearch: "", watchScope: "building",
    timeframe: "all", watchTimeframe: "all", side: "all", selected: null,
    chartKey: null, chart: null, chartRequest: 0, chartController: null, chartExpanded: false,
    syncing: false, lastSync: null, statusReceivedAt: null, errors: {}, chartHover: null, detailOrigin: "signals",
    tradingViewPending: false,
  };
  const titles = {
    signals: ["主图启动", "从长时间近零蓄势，到主图确认启动。先看新信号，再看结构。"],
    watch: ["蓄势观察", "还在横盘的，单独观察。这里的结构尚不是启动信号。"],
    system: ["运行状态", "行情、扫描与通知，每个环节都清晰可见。"],
  };
  const eventNames = { tv_start: "蓄势释放" };
  const TV_PROTOCOL = "imacd-tv-visible-start-monitor-v3";
  const TV_PROFILE = "imacd-v2.2-focus12-band0.10-marks-off";
  const TV_SETTINGS = "近零至少 12 根 · 0.1 ATR · 普通系统标记关闭";
  const TV_INTERVALS = new Map([["15m", "15"], ["1H", "60"], ["4H", "240"]]);
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
  const shortSymbol = (symbol) => String(symbol || "—").replace(/-(USDT|USD)-SWAP$/, "").replace(/USDT\.P$/, "");
  const focusRun = (item) => item.near_zero_bars;
  const tvProtocol = () => state.status?.runtime?.signal_kind === "tv_start" && state.status?.protocol === TV_PROTOCOL;
  const quoteSymbol = (symbol) => /-USD-SWAP$/.test(String(symbol)) ? "USD" : "USDT";
  const normalSearch = (value) => String(value).toUpperCase().replace(/[^A-Z0-9]/g, "");
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
    return item.is_fresh === true && tvProtocol() && !state.errors.signals && !state.errors.status &&
      finite(state.status?.now_ms) && finite(state.statusReceivedAt) && finite(minutes) && Number(minutes) > 0 && finite(item.bar_close_ms) && age >= 0 && age <= Number(minutes) * 60000;
  }
  function price(value) {
    if (!finite(value)) return "—";
    const n = Number(value);
    return n.toLocaleString("en-US", { minimumFractionDigits: Math.abs(n) >= 1 ? 2 : 0, maximumFractionDigits: 12 });
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
  }
  function tradingViewStatus(message, kind) {
    const status = $("tradingview-status");
    status.textContent = message;
    status.classList.remove("hidden", "error", "pending");
    if (kind) status.classList.add(kind);
  }
  // Only explicit click handlers call this bridge; rendering never launches apps.
  async function openTradingView(item) {
    if (state.tradingViewPending) return;
    if (!canOpenTradingView(item)) {
      tradingViewStatus("当前合约或周期不支持在 TradingView 打开。", "error");
      return;
    }
    const request = { symbol: item.symbol, timeframe: item.timeframe };
    const label = `${shortSymbol(request.symbol)} ${quoteSymbol(request.symbol)} · ${request.timeframe}`;
    state.tradingViewPending = true;
    renderTradingViewButtons();
    tradingViewStatus(`正在请求 Mac TradingView 打开 ${label}…`, "pending");
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
    $("page-description").textContent = titles[state.view][1];
    document.title = `spike · ${titles[state.view][0]}`;
    if (updateHash) history.replaceState(null, "", `#${state.view}`);
    if (state.view === "system") loadHealth();
  }
  function filteredSignals() {
    const q = normalSearch(state.search);
    return state.signals.filter((item) => (!q || normalSearch(item.symbol).includes(q)) &&
      (state.timeframe === "all" || item.timeframe === state.timeframe) &&
      (state.side === "all" || item.side === state.side) && item.kind === "tv_start");
  }
  function notification(item, channel = "telegram") {
    const value = String(item[channel === "bark" ? "bark_notification_status" : "notification_status"] || "").toLowerCase();
    if (["sent", "delivered", "success"].includes(value)) return [channel === "bark" ? "服务已接受" : "已发送", "sent"];
    if (["failed", "error", "dead"].includes(value)) return ["发送失败", "failed"];
    if (value === "unknown") return ["回执未知", "pending"];
    if (["pending", "queued", "retry", "sending"].includes(value)) return ["等待发送", "pending"];
    if (["disabled", "not_configured"].includes(value)) return ["通知未启用", "muted"];
    if (channel === "bark" && value === "skipped") return ["已跳过", "muted"];
    if (channel === "bark" && !value) return ["暂无状态", "muted"];
    if (item.is_fresh === false || ["history", "historical", "stale", "expired", "skipped"].includes(value)) return ["历史记录", "muted"];
    if (["suppressed", "duplicate"].includes(value)) return ["已去重", "muted"];
    return ["已记录", "muted"];
  }
  function notificationHTML(item) {
    return ["telegram", "bark"].map((channel) => {
      const [label, className] = notification(item, channel);
      const name = channel === "bark" ? "Bark" : "TG";
      const description = channel === "bark" && className === "sent" ? "Bark 服务已接受推送，不代表手机已收到或已读" : `${name} · ${label}`;
      return `<span class="row-status ${className}" data-notification-channel="${channel}" title="${escapeHTML(description)}" aria-label="${escapeHTML(description)}"><span class="notification-channel">${name}</span><span>${escapeHTML(label)}</span></span>`;
    }).join("");
  }
  function renderSignals() {
    const items = filteredSignals();
    $("filtered-count").textContent = `${items.length} 条`;
    $("filtered-count").title = `共 ${number(state.signalTotal)} 条记录；筛选最近 ${state.signals.length} 条`;
    $("signal-window-note").textContent = state.signalsLoaded ? `最近 ${number(state.signals.length)} / 共 ${number(state.signalTotal)} 条` : "最近 2,000 条 · 每 15 秒同步";
    $("load-more-signals").classList.toggle("hidden", items.length <= state.rowLimit);
    $("load-more-signals").textContent = `显示更多（${Math.min(state.rowLimit, items.length)} / ${items.length}）`;
    $("signal-empty").classList.toggle("hidden", items.length > 0);
    if (!items.length) {
      const hasFilters = state.search || state.timeframe !== "all" || state.side !== "all";
      if (state.errors.signals && !state.signalsLoaded) {
        $("signal-empty-title").textContent = "信号服务暂时不可用";
        $("signal-empty-description").textContent = "正在自动重试。连接恢复后会展示真实信号。";
      } else if (!state.signalsLoaded) {
        $("signal-empty-title").textContent = "正在连接行情服务";
        $("signal-empty-description").textContent = "真实信号会在这里出现。";
      } else {
        $("signal-empty-title").textContent = hasFilters ? "没有符合筛选的信号" : "等待主图蓄势释放";
        $("signal-empty-description").textContent = hasFilters ? "试试其他合约、周期或方向。" : "近零蓄势满足当前设置后，主图可见的释放标记会在收盘确认后列出。";
      }
    }
    const focusedId = document.activeElement?.dataset?.signalId;
    const now = signalClock(); // One timestamp keeps both groups consistent at the expiry boundary.
    const fresh = items.filter((item) => isFresh(item, now));
    const earlier = items.filter((item) => !isFresh(item, now));
    const visible = [...fresh, ...earlier].slice(0, state.rowLimit);
    const freshVisible = visible.filter((item) => isFresh(item, now));
    const earlierVisible = visible.filter((item) => !isFresh(item, now));
    const minutes = state.status?.runtime?.fresh_minutes;
    const group = (heading, list, recent) => list.length ? `<div class="signal-group-heading${recent ? " fresh-heading" : ""}"><h3>${heading}<span class="group-count">${list.length}</span></h3><span>${recent ? `收盘后 ${escapeHTML(number(minutes))} 分钟内` : "按确认时间排列"}</span></div><div class="signal-card-grid">${list.map((item) => signalCardHTML(item, now)).join("")}</div>` : "";
    const freshnessKnown = tvProtocol() && finite(minutes) && Number(minutes) > 0 && finite(state.status?.now_ms) && finite(state.statusReceivedAt);
    const pendingFreshness = state.errors.signals || state.errors.status || !freshnessKnown;
    const noFresh = !fresh.length && items.length ? `<div id="fresh-empty" class="fresh-empty"><strong>${pendingFreshness ? "新鲜状态待同步" : "当前筛选下暂无新鲜启动"}</strong><span>${pendingFreshness ? "保留已获取的记录，状态同步后重新确认时效。" : `收盘 ${escapeHTML(number(minutes))} 分钟内的信号会优先出现在这里。下方可回看此前启动。`}</span></div>` : "";
    $("signal-rows").innerHTML = noFresh + group("新鲜启动", freshVisible, true) + group(fresh.length ? "更早启动" : "已记录启动", earlierVisible, false);
    if (focusedId) Array.from($("signal-rows").querySelectorAll("[data-signal-id]")).find((card) => card.dataset.signalId === focusedId)?.focus({ preventScroll: true });
  }
  function signalCardHTML(item, now = signalClock()) {
    const selected = state.selected && String(state.selected.id) === String(item.id) && state.selected.symbol === item.symbol && state.selected.timeframe === item.timeframe;
    const side = item.side === "short" ? "short" : item.side === "long" ? "long" : "neutral";
    const fresh = isFresh(item, now);
    return `<button type="button" class="signal-card ${side}${selected ? " selected" : ""}${fresh ? " is-fresh" : ""}" data-signal-id="${escapeHTML(item.id)}" aria-pressed="${Boolean(selected)}" aria-label="${escapeHTML(`${shortSymbol(item.symbol)} ${quoteSymbol(item.symbol)} ${item.timeframe} ${sideName(item.side)}，信号收盘价 ${price(item.price)}，${shortDate(item.bar_close_ms)} 确认，查看图表`)}">
      <span class="signal-card-top"><span class="card-symbol"><strong>${escapeHTML(shortSymbol(item.symbol))}</strong><small>${escapeHTML(quoteSymbol(item.symbol))} 永续</small></span><span class="card-timeframe">${escapeHTML(item.timeframe)}</span></span>
      <span class="signal-card-direction"><span class="card-direction">${sideArrow(item.side)} ${escapeHTML(sideName(item.side))}启动</span><span class="card-recency">${fresh ? "新 · " : ""}${escapeHTML(ageLabel(item.bar_close_ms))}</span></span>
      <span class="card-price-label">信号收盘价</span><span class="card-price">${escapeHTML(price(item.price))}</span>
      <span class="card-context"><span>启动前近零蓄势</span><strong>${escapeHTML(number(focusRun(item)))} <small>根</small></strong></span>
      <span class="card-confirmed"><span>收盘确认</span><time title="${escapeHTML(fullDate(item.bar_close_ms))} 北京时间">${escapeHTML(shortDate(item.bar_close_ms))}</time></span>
      <span class="card-footer"><span class="notification-stack">${notificationHTML(item)}</span><span class="card-open">${selected ? "正在查看" : "看图"} ↗</span></span>
    </button>`;
  }
  function applySignalFilters() {
    const items = filteredSignals();
    if (!items.some((item) => state.selected?.id !== undefined && String(item.id) === String(state.selected.id))) {
      if (items.length) chooseSignal(items[0]);
      else {
        state.selected = null;
        state.chartController?.abort();
        state.chartRequest++;
        state.chartKey = null;
        state.chart = null;
        renderDetail();
      }
    }
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
      return `<article class="watch-card"><button type="button" class="watch-preview" data-market-symbol="${escapeHTML(item.symbol)}" data-market-timeframe="${escapeHTML(item.timeframe)}" aria-label="在 spike 预览 ${escapeHTML(shortSymbol(item.symbol))} ${escapeHTML(quoteSymbol(item.symbol))} ${escapeHTML(item.timeframe)} ${escapeHTML(marketPhase(item))}图表">
        <span class="watch-card-top"><span class="card-symbol"><strong>${escapeHTML(shortSymbol(item.symbol))}</strong><small>${escapeHTML(quoteSymbol(item.symbol))} 永续</small></span><span class="card-timeframe">${escapeHTML(item.timeframe)}</span></span>
        <span class="watch-card-phase"><span class="phase-badge ${phaseClass}" title="${escapeHTML(item.error || (item.stale ? "当前保留过期行情，等待更新" : "当前结构尚不是启动信号"))}">${escapeHTML(state.errors.markets ? "缓存 · 待同步" : marketPhase(item))}</span><span class="card-status">${!valid ? "等待更新" : item.focus ? "已达蓄势门槛" : "观察中"}</span></span>
        <span class="watch-card-run"><strong>${valid ? escapeHTML(number(item.near_zero_bars)) : "—"}<small> 根</small></strong><span>当前近零蓄势</span></span>
        <span class="watch-card-background"><span>均线密集<strong>${valid ? item.dense === true ? "已密集" : item.dense === false ? "未密集" : "—" : "—"}</strong></span><span>高周期背景<strong>${valid ? escapeHTML(sideName(item.htf_side)) : "—"}</strong></span></span>
        <span class="watch-preview-label">站内预览</span></button>
        <div class="watch-card-foot"><time title="${escapeHTML(fullDate(item.bar_close_ms))} 北京时间">收盘 ${escapeHTML(shortDate(item.bar_close_ms))}</time><button type="button" class="watch-open-app" data-tradingview-action="watch" data-tv-symbol="${escapeHTML(item.symbol)}" data-tv-timeframe="${escapeHTML(item.timeframe)}" title="在 Mac TradingView 打开" aria-label="在 Mac TradingView 打开 ${escapeHTML(shortSymbol(item.symbol))} ${escapeHTML(quoteSymbol(item.symbol))} ${escapeHTML(item.timeframe)}">查看结构 ↗</button></div>
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
    const telegram = status.telegram || {};
    const bark = status.bark;
    const runtime = status.runtime || {};
    const timeframes = Array.isArray(runtime.timeframes) ? runtime.timeframes : [];
    $("metric-timeframes").textContent = timeframes.length ? timeframes.join(" + ") : "—";
    $("watch-timeframes").textContent = timeframes.length ? timeframes.join(" / ") : "—";
    $("metric-signals").textContent = tvProtocol() ? number(counts.signals_24h ?? 0) : "—";
    $("nav-signal-count").textContent = tvProtocol() ? number(counts.signals_24h ?? 0) : "—";
    $("metric-building").textContent = number(counts.building ?? 0);
    $("metric-universe").textContent = number(status.universe?.count);
    $("metric-building-detail").textContent = finite(counts.ready) ? `${number(counts.ready)} 个窗口已就绪` : "零轴横盘与均线密集";
    $("metric-signals-detail").textContent = tvProtocol() ? "蓄势释放 · 收盘确认" : "规则升级中 · 等待新口径";
    const scanning = ["running", "scanning", "in_progress", "starting", "bootstrap"].includes(scan.status);
    const scanErrorCount = Array.isArray(scan.errors) ? scan.errors.length : numeric(scan.errors);
    const complete = numeric(scan.completed);
    const total = numeric(scan.total);
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
    const tgReady = telegram.configured && telegram.enabled;
    const tgProblem = numeric(telegram.failed) > 0 || numeric(telegram.unknown) > 0;
    $("telegram-header").textContent = tgReady ? tgProblem ? "TG · 异常" : "TG · 已启用" : "TG · 未启用";
    $("telegram-header").classList.toggle("good", Boolean(tgReady && !tgProblem));
    $("telegram-state-badge").textContent = tgReady ? tgProblem ? "需检查发送结果" : "通知已启用" : telegram.configured ? "通知已关闭" : "尚未配置";
    $("telegram-state-badge").className = `neutral-badge ${tgReady && !tgProblem ? "good" : "warn"}`;
    $("telegram-description").textContent = tgReady ? numeric(telegram.unknown) > 0 ? "部分发送未收到确定回执，为避免重复通知不自动重发，请核对 Telegram。" : "新鲜信号进入通知队列；发送结果与行情记录分开显示。" : telegram.configured ? "通道已配置，当前发送开关关闭。前端继续记录信号。" : "尚未读取到可用的通知配置，当前仅在前端记录信号。";
    $("telegram-facts").innerHTML = factsHTML([["最近成功", fullDate(telegram.last_success_ms)], ["待发送", number(telegram.pending)], ["发送失败", number(telegram.failed)], ["发送结果未知", number(telegram.unknown ?? 0)], ["配置状态", telegram.configured ? "已配置（敏感信息不展示）" : "未配置"]]);
    const barkReady = Boolean(bark?.configured && bark?.enabled);
    const barkProblem = numeric(bark?.failed) > 0 || numeric(bark?.unknown) > 0;
    $("bark-header").textContent = !bark ? "Bark · 待接入" : barkReady ? barkProblem ? "Bark · 异常" : "Bark · 已启用" : "Bark · 未启用";
    $("bark-header").classList.toggle("good", barkReady && !barkProblem);
    $("bark-state-badge").textContent = !bark ? "状态待接入" : barkReady ? barkProblem ? "需检查发送结果" : "通知已启用" : bark.configured ? "通知已关闭" : "尚未配置";
    $("bark-state-badge").className = `neutral-badge ${barkReady && !barkProblem ? "good" : "warn"}`;
    $("bark-description").textContent = !bark ? "服务尚未提供 Bark 通道状态，等待下一次同步。" : barkReady ? numeric(bark.unknown) > 0 ? "部分发送结果未知，为避免重复通知不自动重发。服务接受不代表手机已收到或已读。" : "与 TG 分别记录推送结果。服务已接受不代表手机已收到或已读。" : bark.configured ? "Bark 已配置，当前发送开关关闭；TG 与前端记录独立运行。" : "Bark 尚未配置；TG 与前端记录独立运行。";
    $("bark-facts").innerHTML = factsHTML([["本次服务接受", number(bark?.sent)], ["最近服务接受", fullDate(bark?.last_success_ms)], ["待发送", number(bark?.pending)], ["发送失败", number(bark?.failed)], ["发送结果未知", number(bark?.unknown)], ["历史服务接受", number(bark?.historical_sent)], ["配置状态", !bark ? "等待状态" : bark.configured ? "已配置（敏感信息不展示）" : "未配置"]]);
    $("service-version").textContent = status.version ? `v${String(status.version).replace(/^v/, "")}` : "本机服务";
    const runtimeFacts = [["监控台", "spike"], ["启动时间", fullDate(status.started_at_ms)], ["服务时间", fullDate(status.now_ms)], ["运行时长", duration(Date.now() - numeric(status.started_at_ms, Date.now()))]];
    if (timeframes.length) runtimeFacts.push(["监控周期", timeframes.join(" / ")]);
    if (runtime.host) runtimeFacts.push(["主机", runtime.host]);
    if (runtime.pid) runtimeFacts.push(["进程", runtime.pid]);
    if (runtime.data_dir) runtimeFacts.push(["数据位置", runtime.data_dir]);
    if (runtime.signal_mode || runtime.strategy || status.strategy) runtimeFacts.push(["信号规则", runtime.signal_mode || runtime.strategy || status.strategy]);
    if (runtime.higher_mode) runtimeFacts.push(["高周期规则", runtime.higher_mode]);
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
      const names = { status: "运行状态", signals: "信号列表", markets: "蓄势观察" };
      $("error-notice").textContent = `${errors.map(([key, error]) => `${names[key] || key}：${error}`).join("；")}。${state.lastSync ? "当前保留上次成功获取的数据，" : ""}15 秒后自动重试。`;
    }
  }
  function chooseSignal(item, scroll = false, origin = "signals") {
    if (!item) return;
    state.selected = { ...item };
    state.detailOrigin = origin;
    renderSignals();
    renderDetail();
    if (scroll && window.innerWidth <= 1100) {
      $("detail-content").closest("aside").scrollIntoView({ behavior: "instant", block: "start" });
      $("detail-symbol").focus({ preventScroll: true });
    }
  }
  function renderDetail() {
    const item = state.selected;
    $("detail-empty").classList.toggle("hidden", Boolean(item));
    $("detail-content").classList.toggle("hidden", !item);
    $("back-to-signals").classList.toggle("hidden", !item);
    if (!item) { setChartExpanded(false, false); return; }
    $("back-to-signals").textContent = state.detailOrigin === "watch" ? "← 返回蓄势观察" : "← 返回信号卡片";
    $("detail-price-caption").textContent = item.kind === "tv_start" ? "信号收盘价" : "最新已收盘价 · 观察结构";
    $("detail-symbol").textContent = shortSymbol(item.symbol);
    $("detail-market-label").textContent = `OKX · ${quoteSymbol(item.symbol)} 永续${quoteSymbol(item.symbol) === "USD" ? " · 币本位" : ""}`;
    $("detail-timeframe").textContent = item.timeframe || "—";
    $("chart-title").textContent = `${shortSymbol(item.symbol)} · ${item.timeframe || "—"} · K 线 / IMACD`;
    $("detail-price").textContent = price(item.price);
    const name = item.kind ? eventNames[item.kind] || item.kind : marketPhase(item);
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
    const facts = [
      [item.kind === "tv_start" ? "信号收盘 · 北京时间" : "最近收盘 · 北京时间", shortDate(item.bar_close_ms), ""],
      [item.kind === "tv_start" ? "启动前近零蓄势" : "当前近零蓄势", `${number(focusRun(item))} 根`, ""],
      ["精确零轴 · 仅作背景", `${number(item.zero_bars)} 根`, ""],
      ["均线密集 · 背景参考", item.dense === true ? "已密集" : item.dense === false ? "未密集" : "—", item.dense ? "mint" : ""],
      ["高周期方向 · 背景参考", sideName(item.htf_side), ""],
      ["主图标记", item.kind === "tv_start" ? "蓄势释放" : "观察结构", ""],
    ];
    $("detail-facts").innerHTML = facts.map(([label, value, color]) => `<div><div class="fact-label">${escapeHTML(label)}</div><div class="fact-value ${color}">${escapeHTML(value)}</div></div>`).join("");
    $("detail-reason").textContent = item.kind === "tv_start" ? `启动前近零蓄势 ${number(focusRun(item))} 根，主图${item.side === "short" ? "向下" : "向上"}出现蓄势释放标记，已收盘确认。点位为该根原收盘价；精确零轴根数、均线与高周期仅作背景。${item.tv_profile && item.tv_profile !== TV_PROFILE ? " 此记录的设置快照不同，请核对监控配置。" : ""}` : item.error ? `行情读取异常：${String(item.error)}。当前结构仅供查看。` : item.stale ? "当前行情已过期，等待重新同步；不会将缓存结构当作新信号。" : "当前为行情观察。按固定主图设置记录蓄势释放；普通系统标记已关闭，亮色回踩 K 线仅作参考。";
    const market = state.markets.find((row) => row.symbol === item.symbol && row.timeframe === item.timeframe);
    const key = `${item.symbol}|${item.timeframe}|${item.bar_close_ms || ""}|${market?.bar_close_ms || ""}|${market?.stale || false}|${market?.error || ""}`;
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
    $("chart-hint").textContent = "仅展示已返回的真实 K 线";
    const timeout = setTimeout(() => controller.abort(), 20000);
    try {
      const data = await api(`/api/chart?symbol=${encodeURIComponent(item.symbol)}&timeframe=${encodeURIComponent(item.timeframe)}`, controller.signal);
      if (request !== state.chartRequest) return;
      if (!Array.isArray(data.candles)) throw new Error("图表数据格式有误");
      state.chart = data;
      renderChart();
    } catch (error) {
      if (request !== state.chartRequest) return;
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
  function renderChart() {
    if (!state.chart) return;
    const valid = state.chart.candles.filter((bar) => [bar.t, bar.o, bar.h, bar.l, bar.c].every(finite));
    const selectedIndex = state.selected?.kind ? valid.findIndex((bar) => Number(bar.t) === Number(state.selected.bar_open_ms)) : -1;
    const startIndex = selectedIndex >= 0 ? Math.min(Math.max(0, valid.length - 120), Math.max(0, selectedIndex - 90)) : Math.max(0, valid.length - 120);
    const candles = valid.slice(startIndex, startIndex + 120);
    document.querySelector(".chart-bars-note").textContent = selectedIndex >= 0 && startIndex < valid.length - 120 ? "信号附近 120 根" : "最近 120 根";
    if (!candles.length) {
      $("chart-container").innerHTML = '<div class="chart-placeholder">该合约尚无足够的已收盘行情。<br>后续扫描会继续补充。</div>';
      $("chart-hint").textContent = "暂无可绘制的真实数据";
      return;
    }
    const width = 600, height = 294, left = 17, right = 67, priceTop = 10, priceBottom = 162, impulseTop = 194, impulseBottom = 262;
    const plotWidth = width - left - right;
    const step = plotWidth / candles.length;
    const x = (i) => left + (i + .5) * step;
    const maKeys = ["sma20", "ema20", "sma60", "ema60", "sma120", "ema120"];
    const rangeValues = candles.flatMap((bar) => [bar.h, bar.l, ...maKeys.map((key) => bar[key])]).filter(finite).map(Number);
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
      parts.push(`<line x1="${left}" x2="${width - right + 3}" y1="${y}" y2="${y}" stroke="#21313e" stroke-width=".65" stroke-dasharray="2 4"/><text x="${width - right + 9}" y="${y + 3}">${escapeHTML(axisPrice(value))}</text>`);
    }
    const labelIndices = [...new Set([0, Math.round((candles.length - 1) / 3), Math.round((candles.length - 1) * 2 / 3), candles.length - 1])];
    labelIndices.forEach((i) => {
      parts.push(`<line x1="${x(i)}" x2="${x(i)}" y1="${priceTop}" y2="${impulseBottom}" stroke="#1c2b36" stroke-width=".6" stroke-dasharray="2 5"/>`);
      const date = new Date(Number(candles[i].t));
      const label = `${date.getMonth() + 1}/${date.getDate()} ${String(date.getHours()).padStart(2, "0")}:${String(date.getMinutes()).padStart(2, "0")}`;
      parts.push(`<text x="${x(i)}" y="${height - 13}" text-anchor="${i === 0 ? "start" : i === candles.length - 1 ? "end" : "middle"}">${label}</text>`);
    });
    parts.push('<g clip-path="url(#price-clip)">');
    const maColors = ["#82aaa0", "#48695e", "#7194b0", "#405c72", "#909eae", "#556576"];
    maKeys.forEach((key, i) => parts.push(`<path d="${path(key, py)}" fill="none" stroke="${maColors[i]}" stroke-width=".8" opacity=".95"/>`));
    candles.forEach((bar, i) => {
      const bright = bar.retest_side === "long" || bar.retest_side === "short";
      const rising = Number(bar.c) >= Number(bar.o);
      const color = bright ? bar.retest_side === "short" ? "#ffcc8d" : "#c5ec95" : rising ? "#65b697" : "#b7737e";
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
    if (state.selected?.kind === "tv_start") eventList.push(state.selected);
    eventList.filter((event) => event.kind === "tv_start").forEach((event) => breakouts.set(`${event.bar_open_ms ?? event.t}|${event.side}`, event));
    Array.from(breakouts.values()).slice(-50).forEach((event) => {
      const index = candles.findIndex((bar) => Number(bar.t) === Number(event.bar_open_ms ?? event.t));
      if (index < 0 || !["long", "short"].includes(event.side)) return;
      const long = event.side === "long", cy = long ? py(candles[index].l) + 6 : py(candles[index].h) - 6;
      const cx = x(index), direction = long ? 1 : -1;
      parts.push(`<g data-event-kind="tv_start" data-side="${event.side}"><title>主图启动 · 蓄势释放${sideArrow(event.side)} · ${escapeHTML(number(event.near_zero_bars))} 根 · 收盘 ${escapeHTML(price(event.price ?? candles[index].c))}</title><path d="M${cx},${cy}l-3.5,${direction * 5}h7Z" fill="${long ? "#a4ecc9" : "#f0a2a8"}" stroke="#0e161f" stroke-width=".55"/></g>`);
    });
    parts.push("</g>");
    parts.push(`<line x1="${left}" x2="${width - 12}" y1="180" y2="180" stroke="#263640" stroke-width=".7"/><text x="${left}" y="191" style="font-size:7px;fill:#728797">IMACD</text><line x1="65" x2="76" y1="188.5" y2="188.5" stroke="#799ed5" stroke-width="1.2"/><text x="80" y="191" style="font-size:7px">主线</text><line x1="109" x2="120" y1="188.5" y2="188.5" stroke="#d8b17b" stroke-width="1.2"/><text x="124" y="191" style="font-size:7px">信号线</text>`);
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
        parts.push(`<g class="focus-zone" data-source="backend-focus"><polygon points="${points}" fill="#c8ad7119" stroke="#bda576" stroke-width=".65"/><title>合格近零蓄势 · ${accumulationBars} 根</title></g>`);
        if (focusWidth > 58) {
          const bottom = Math.max(...lower.map((point) => point[1]));
          parts.push(`<text x="${startX + focusWidth / 2}" y="${Math.min(impulseBottom - 1, bottom + 13)}" text-anchor="middle" style="font-size:7px;fill:#a18e69">近零蓄势 ${accumulationBars} 根</text>`);
        }
        focusStart = null;
      }
    }
    // Keep the zero axis distinct from the qualified near-zero band.
    const zeroY = my(0);
    parts.push(`<line x1="${left}" x2="${width - right + 3}" y1="${zeroY}" y2="${zeroY}" stroke="#71818d" stroke-width=".8" stroke-dasharray="3 3"/><text x="${width - right + 9}" y="${zeroY + 3}" style="fill:#95a3ad">0.00</text>`);
    parts.push(`<path d="${path("md", my)}" stroke="#799ed5" stroke-width="1.35" fill="none"/><path d="${path("sb", my)}" stroke="#d8b17b" stroke-width="1.2" fill="none"/>`);
    parts.push(`<g class="chart-crosshair" visibility="hidden"><line class="crosshair-line" x1="0" x2="0" y1="${priceTop}" y2="${impulseBottom}" stroke="#789187" stroke-width=".8" stroke-dasharray="3 3"/><circle class="crosshair-dot" r="2.5" fill="#afe5c8" stroke="#0e161f" stroke-width="1.2"/></g><rect class="chart-hit-area" x="${left}" y="${priceTop}" width="${plotWidth}" height="${impulseBottom - priceTop}" fill="transparent" stroke="none"/></svg>`);
    $("chart-container").innerHTML = parts.join("");
    $("chart-container").setAttribute("aria-label", `${shortSymbol(state.selected?.symbol)} ${state.selected?.timeframe}，${candles.length} 根真实 K 线，上图六条细均线，下图 IMACD 双线与零轴，无柱状图`);
    $("chart-hint").textContent = state.chart.state?.stale ? "行情缓存已过期 · 等待重新同步" : state.chart.state?.error ? "行情存在读取异常 · 当前为缓存" : "箭头：主图蓄势释放 · 金色：合格近零区";
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
    svg.addEventListener("pointerleave", () => { crosshair.setAttribute("visibility", "hidden"); $("chart-hint").textContent = state.chart.state?.stale ? "行情缓存已过期 · 等待重新同步" : state.chart.state?.error ? "行情存在读取异常 · 当前为缓存" : "箭头：主图蓄势释放 · 金色：合格近零区"; });
  }
  async function refresh() {
    if (state.syncing) return;
    state.syncing = true;
    $("refresh-button").disabled = true;
    $("refresh-button").classList.add("loading");
    try {
      const results = await Promise.allSettled([api("/api/status"), api("/api/signals?limit=2000&kind=tv_start"), api("/api/markets")]);
      const keys = ["status", "signals", "markets"];
      let anySuccess = false;
      results.forEach((result, index) => {
        const key = keys[index];
        if (result.status === "rejected") { state.errors[key] = result.reason.message || "请求失败"; return; }
        if (key !== "status" && !Array.isArray(result.value.items)) { state.errors[key] = "服务返回的数据格式有误"; return; }
        delete state.errors[key];
        anySuccess = true;
        if (key === "status") { state.status = result.value; state.statusReceivedAt = Date.now(); }
        else {
          state[key] = result.value.items.filter((item) => item && typeof item === "object" && item.symbol && (key !== "signals" || item.kind === "tv_start"));
          state[`${key}Loaded`] = true;
          if (key === "signals") state.signalTotal = tvProtocol() ? numeric(result.value.total, state.signals.length) : state.signals.length;
          if (key === "signals") state.signals.sort((a, b) => numeric(b.bar_close_ms) - numeric(a.bar_close_ms) || numeric(b.detected_at_ms) - numeric(a.detected_at_ms));
        }
      });
      if (anySuccess) state.lastSync = Date.now();
      renderErrors(); renderStatus(); renderSignals(); renderWatch();
      $("last-sync").textContent = state.errors.signals ? "同步失败 · 保留缓存" : `同步 ${clockTime(state.lastSync)}`;
      if (!state.selected && filteredSignals().length) chooseSignal(filteredSignals()[0]);
      else if (state.selected) {
        const updated = state.selected.id !== undefined ? state.signals.find((item) => String(item.id) === String(state.selected.id)) : state.markets.find((item) => item.symbol === state.selected.symbol && item.timeframe === state.selected.timeframe);
        if (updated) state.selected = { ...updated };
        renderDetail();
      }
      if (state.view === "system" && $("health-json").closest("details").open) loadHealth();
    } finally {
      state.syncing = false;
      $("refresh-button").disabled = false;
      $("refresh-button").classList.remove("loading");
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
  $("side-filter").addEventListener("change", (event) => { state.side = event.target.value; state.rowLimit = 24; applySignalFilters(); });
  $("refresh-button").addEventListener("click", refresh);
  $("tradingview-open").addEventListener("click", () => openTradingView(state.selected));
  $("chart-expand").addEventListener("click", () => setChartExpanded(!state.chartExpanded));
  $("chart-dialog").addEventListener("close", () => {
    if (!$("chart-dialog").open) setChartExpanded(false);
  });
  $("chart-dialog").addEventListener("click", (event) => {
    if (event.target === $("chart-dialog")) setChartExpanded(false);
  });
  $("load-more-signals").addEventListener("click", () => { state.rowLimit += 24; renderSignals(); });
  function activateRow(event, type) {
    const row = event.target.closest(type === "signal" ? "[data-signal-id]" : "[data-market-symbol]");
    if (!row) return;
    event.preventDefault();
    if (type === "signal") chooseSignal(state.signals.find((item) => String(item.id) === row.dataset.signalId), true);
    else {
      const item = state.markets.find((candidate) => candidate.symbol === row.dataset.marketSymbol && candidate.timeframe === row.dataset.marketTimeframe);
      if (item) {
        state.search = shortSymbol(item.symbol); state.timeframe = item.timeframe; state.side = "all"; state.rowLimit = 24;
        $("symbol-search").value = state.search; $("side-filter").value = "all";
        document.querySelectorAll("[data-timeframe]").forEach((button) => { const selected = button.dataset.timeframe === item.timeframe; button.classList.toggle("selected", selected); button.setAttribute("aria-pressed", String(selected)); });
        setView("signals"); chooseSignal(item, true, "watch");
      }
    }
  }
  $("signal-rows").addEventListener("click", (event) => activateRow(event, "signal"));
  $("watch-rows").addEventListener("click", (event) => {
    const opener = event.target.closest("[data-tradingview-action]");
    if (opener) {
      event.preventDefault();
      openTradingView({ symbol: opener.dataset.tvSymbol, timeframe: opener.dataset.tvTimeframe });
      return;
    }
    activateRow(event, "market");
  });
  $("load-more-watch").addEventListener("click", () => { state.watchLimit += 24; renderWatch(); });
  $("back-to-signals").addEventListener("click", () => {
    const watch = state.detailOrigin === "watch";
    if (watch) setView("watch");
    const cards = document.querySelectorAll(watch ? "[data-market-symbol]" : "[data-signal-id]");
    const card = Array.from(cards).find((node) => watch ? node.dataset.marketSymbol === state.selected?.symbol && node.dataset.marketTimeframe === state.selected?.timeframe : node.dataset.signalId === String(state.selected?.id));
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
