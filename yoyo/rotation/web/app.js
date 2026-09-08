/*
 * Read-only research dashboard client; server owns source, as-of and policy.
 * Sources: MDN Using Fetch, HTML dialog and Document.createElementNS references.
 * https://developer.mozilla.org/en-US/docs/Web/API/Fetch_API/Using_Fetch
 * https://developer.mozilla.org/en-US/docs/Web/HTML/Reference/Elements/dialog
 * https://developer.mozilla.org/en-US/docs/Web/API/Document/createElementNS
 * All API text is inserted through textContent. No generated trading orders.
 */
"use strict";

(() => {
  const $ = (id) => document.getElementById(id);
  const state = { snapshot: null, config: null, status: null, filter: "all", sector: "all", search: "", loading: false, exportBusy: false, stale: false };
  const labels = {
    watch: "待突破", breakout: "突破观察", pullback: "回踩观察", extended: "涨幅延伸",
    avoid: "风险隔离", invalidated: "结构失效", insufficient_data: "数据不足",
    supportive: "条件偏支持", mixed: "分化观察", defensive: "防御观察", unknown: "数据待完善",
    ready: "日线/4h条件齐备", review: "需要复核", blocked: "观察受限", unavailable: "未提供",
    not_collected: "未采集", partial: "部分可用", ok: "可用",
    pending: "等待确认", confirmed: "观察已确认", none: "未形成", triggered: "触发观察",
    illustration: "测算示例", block: "阻断", warning: "需留意", info: "信息",
  };
  const RS_FORMULA = "相对 BTC 收益 = (1 + 币种收益) / (1 + BTC 收益) − 1，以百分比（%）显示";
  const reasonLabels = {
    zero_median_quote_volume: "近期成交额中位数为零",
    benchmark_observation_dates_differ: "BTC 与 ETH 的观察日期不一致",
    invalid_benchmark_return: "基准收益数据无效",
    no_eligible_altcoin_breadth_denominator: "没有可用于计算广度的山寨样本",
    missing_btc_sma50_state: "缺少 BTC 50 日均线状态",
    "btc_negative_below_sma50_or_breadth_at_most_0.35": "BTC 走弱或上涨广度偏低",
    "btc_trend_eth_relative_strength_and_breadth_at_least_0.60": "BTC 趋势、ETH 相对强度与市场广度偏支持",
    complete_inputs_with_mixed_trend_and_breadth: "趋势与市场广度仍有分化",
    descriptive_heuristic_not_a_validated_forecast: "环境描述规则尚未验证预测能力",
    no_declustered_breakout_anchor_in_last_12_subsequent_candles: "近期没有独立突破结构",
    raw_breakout_cluster_needs_12_quiet_candles_before_a_new_anchor: "连续上冲中，尚未形成新的独立启动",
    zero_prior_median_volume_breakout_unverifiable: "此前成交量为零，无法确认放量突破",
    close_above_prior_range_after_12_raw_breakout_free_candles: "整理后放量收盘突破此前平台",
    frozen_breakout_anchor_active_waiting_for_new_structure: "保留首次突破平台，等待后续结构",
    frozen_structural_low_breached_anchor_invalid_for_remaining_lifetime: "价格曾跌破结构低点，本轮结构已失效",
    first_later_retest_holds_frozen_range_high_on_smaller_volume: "首次缩量回踩后收盘守住原平台上沿",
    close_beyond_frozen_range_extension_heuristic: "价格已明显远离原平台，留意追高风险",
    unvalidated_structure_observation_only: "结构规则仅供观察，尚未验证交易效果",
    liquidity_not_eligible: "流动性未达到观察条件",
    blocking_event_risk: "存在阻断性的事件风险",
    daily_data_insufficient: "日线数据不足",
    setup_invalidated: "4 小时结构已失效",
    setup_insufficient_data: "4 小时结构数据不足",
    market_regime_defensive: "市场环境处于防御状态",
    daily_and_benchmark_observation_times_differ: "币种与基准的日线截止时间不同",
    sector_metadata_unknown_not_invented: "板块归属尚未核实",
    event_risk_unknown_needs_manual_review: "事件与供给风险需人工复核",
    relative_strength_incomplete: "相对 BTC 收益数据不完整",
    market_regime_unknown: "市场环境数据不完整",
    heuristic_score_not_win_probability: "观察分仅用于排序，不代表胜率",
    trigger_15m_missing: "缺少 15 分钟辅助观察",
    trigger_15m_insufficient_data: "15 分钟数据覆盖不足",
    trigger_15m_invalidated: "15 分钟结构已失效",
    trigger_15m_unknown_state: "15 分钟观察状态尚不能确认",
    non_crypto_asset: "不属于本观察池的加密资产",
    non_crypto_or_leveraged_asset: "非加密资产或杠杆代币",
    not_active_spot: "现货未处于可交易状态",
    missing_quote_volume: "缺少成交额数据",
    outside_frozen_volume_budget: "超出本次固定成交额观察范围",
    independent_current_endpoint_observations_not_a_synchronized_snapshot: "各接口独立读取，不是同一时刻的同步快照",
    single_open_interest_snapshot_cannot_infer_change_direction_or_spot_flow: "单次持仓量不能说明增减、方向或现货资金流入",
    latest_reported_funding_rate_is_not_a_realized_settlement_record: "费率为接口最新上报值，不代表已核验的结算记录",
    funding_info_has_no_exchange_event_timestamp: "资金费率间隔接口未提供交易所事件时间",
    mark_and_open_interest_have_different_exchange_event_times: "标记价与持仓量的交易所时间不同",
    derived_notional_omitted_event_time_skew_exceeds_60_seconds: "两源时间相差超过 60 秒，未估算持仓名义额",
    derived_notional_nonfinite: "持仓名义额无法有效计算",
    derived_quote_notional_uses_USDT_approximately_one_USD_not_a_verified_FX_rate: "名义额由持仓量乘标记价估算，单位为 USDT；未核验美元汇率",
    remaining_derivatives_requests_skipped: "已停止其余衍生品请求",
    no_matching_usdm_perpetual_symbol: "没有匹配的 USD-M 永续合约",
    rate_limited_no_retry: "接口限流，本次不重试",
    schema_invalid: "接口数据结构不符合预期",
    schema_or_symbol_mismatch: "接口数据结构或币种不匹配",
    interval_not_reported_no_eight_hour_assumption: "未上报费率间隔，保持未知",
    duplicate_symbol_records: "接口返回了重复币种记录",
    missing_or_invalid_interval_hours: "费率间隔缺失或无效",
    missing_or_invalid_event_time: "交易所事件时间缺失或无效",
    future_event_time_values_masked: "事件时间晚于收到时间，已隐藏该源数值",
    event_older_than_300_seconds_values_masked: "事件时间过旧，已隐藏该源数值",
    missing_or_invalid_lastFundingRate: "最新上报费率缺失或无效",
    missing_or_invalid_markPrice: "标记价缺失或无效",
    missing_or_invalid_openInterest: "持仓量缺失或无效",
  };
  const derivativeSourceLabels = { premium_index: "标记价 / 费率", open_interest: "未平仓量", funding_info: "费率间隔" };
  const arrays = (value) => Array.isArray(value) ? value : [];
  const finite = (value) => typeof value === "number" && Number.isFinite(value);
  const label = (value) => labels[value] || value || "—";

  function reasonText(value) {
    const text = String(value ?? "");
    if (Object.hasOwn(reasonLabels, text)) return reasonLabels[text];
    let match = text.match(/^need_(\d+)_completed_daily_candles:have_(\d+)$/);
    if (match) return `日线需 ${match[1]} 根完整 K 线，当前 ${match[2]} 根`;
    match = text.match(/^need_(\d+)_completed_candles_for_origin_invariance:have_(\d+)$/);
    if (match) return `结构判断需 ${match[1]} 根完整 K 线，当前 ${match[2]} 根`;
    match = text.match(/^missing_or_insufficient_benchmark:(.+)$/);
    if (match) return `${match[1]} 基准数据不足`;
    match = text.match(/^(premium_index|open_interest|funding_info):(.+)$/);
    if (match) return `${derivativeSourceLabels[match[1]]}：${reasonText(match[2])}`;
    match = text.match(/^http_(\d+)(_rate_limited_no_retry)?$/);
    if (match) return `接口返回 HTTP ${match[1]}${match[2] ? "，限流后不重试" : "，数据暂不可用"}`;
    match = text.match(/^request_failed:(.+)$/);
    if (match) return "接口请求未成功，数据暂不可用";
    match = text.match(/^api_error:(.+)$/);
    if (match) return `接口返回错误（${match[1]}）`;
    return text;
  }

  function node(tag, className, text) {
    const item = document.createElement(tag);
    if (className) item.className = className;
    if (text !== undefined) item.textContent = text;
    return item;
  }
  function setText(id, text) { $(id).textContent = text; }
  function number(value, digits = 2) {
    return finite(value) ? new Intl.NumberFormat("en-US", { maximumFractionDigits: digits }).format(value) : "—";
  }
  function price(value) {
    if (!finite(value)) return "—";
    return number(value, Math.abs(value) < 0.01 ? 8 : Math.abs(value) < 1 ? 6 : 4);
  }
  function pct(value, { signed = true, digits = 1, suffix = "%" } = {}) {
    if (!finite(value)) return "—";
    return `${signed && value > 0 ? "+" : ""}${(value * 100).toFixed(digits)}${suffix}`;
  }
  function tone(value) { return !finite(value) || value === 0 ? "neutral" : value > 0 ? "positive" : "negative"; }
  function time(value, compact = false) {
    if (!value) return "—";
    const parsed = new Date(value);
    if (!Number.isFinite(parsed.getTime())) return String(value);
    const iso = parsed.toISOString();
    return compact ? iso.slice(0, 10) : `${iso.slice(0, 10)} ${iso.slice(11, 19)} UTC`;
  }
  function shortSymbol(value) { return String(value || "—").replace(/[-/]?USDT(?::USDT)?$/i, "").replace(/-SWAP$/i, ""); }
  function safeURL(value) {
    try {
      const url = new URL(value);
      return ["https:", "http:"].includes(url.protocol) ? url.href : null;
    } catch (_) { return null; }
  }
  function externalLink(text, url, className) {
    const href = safeURL(url);
    if (!href) return node("span", className, `${text}（链接未提供或不可用）`);
    const link = node("a", className, text);
    link.href = href;
    link.target = "_blank";
    link.rel = "noopener noreferrer";
    return link;
  }
  function readableError(error) {
    if (error.name === "AbortError") return "请求等待超时；可稍后重新读取。";
    return error.message || "无法读取本地服务，请检查服务是否运行。";
  }

  async function request(path, options = {}) {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), options.timeout || 180000);
    try {
      const response = await fetch(path, {
        method: options.method || "GET", cache: "no-store", signal: controller.signal,
        headers: { Accept: "application/json", ...(options.method ? { "Content-Type": "application/json" } : {}) },
        ...(options.method ? { body: "{}" } : {}),
      });
      const data = await response.json().catch(() => null);
      if (!response.ok) {
        const detail = data?.detail;
        const message = typeof detail === "string" ? detail : detail ? JSON.stringify(detail) : `请求失败（HTTP ${response.status}）`;
        const error = new Error(message);
        error.status = response.status;
        throw error;
      }
      if (!data || typeof data !== "object") throw new Error("服务返回的数据格式无效。");
      return data;
    } finally { clearTimeout(timeout); }
  }

  function connection(online, text) {
    $("connection").className = `connection ${online ? "online" : "offline"}`;
    setText("connection-text", text);
  }
  function updateButtons() {
    $("scan-button").disabled = state.loading || !state.status;
    $("export-button").disabled = state.loading || state.exportBusy || !state.snapshot;
    setText("scan-label", state.loading ? "扫描中…" : "扫描一次");
    document.body.classList.toggle("loading", state.loading);
    $("candidates").setAttribute("aria-busy", String(state.loading));
  }
  function showError(message, stale = Boolean(state.snapshot)) {
    state.stale = stale;
    $("request-error").hidden = false;
    setText("request-error", `${message}${stale ? ` 当前保留上次快照（截止 ${time(state.snapshot.as_of)}），本次刷新未更新数据。` : ""}`);
    $("mode-banner").classList.toggle("stale", stale);
  }
  function clearError() {
    state.stale = false;
    $("request-error").hidden = true;
    $("mode-banner").classList.remove("stale");
  }

  function renderMode(snapshot = state.snapshot) {
    const config = snapshot || state.config || state.status?.config || {};
    const mode = config.mode;
    const historical = mode === "historical_research";
    const synthetic = mode === "synthetic_demo";
    $("mode-banner").className = `mode-banner${historical ? " historical" : synthetic ? " synthetic" : ""}${state.stale ? " stale" : ""}`;
    setText("mode-tag", historical ? "HISTORICAL" : synthetic ? "SYNTHETIC" : mode === "live_observation" ? "OBSERVATION" : "LOCAL RESEARCH");
    setText("mode-title", historical ? "历史研究 · 以历史截止时间为准" : synthetic ? "合成场景演示 · 非真实行情" : mode === "live_observation" ? "实时数据观察 · 仅用于研究" : "等待服务配置");
    setText("mode-description", historical ? "下方数据属于历史窗口，不能解读为当前市场或新鲜交易信号。" : synthetic ? "价格、状态与风险数值均来自合成场景，仅验证系统流程。" : mode === "live_observation" ? "读取服务允许的观察数据；参考位仍需事件审核与人工判断。" : "扫描将使用本地服务的固定配置，不会自动持续抓取。");
    setText("asof-label", historical ? "历史研究截止时间 · UTC" : synthetic ? "合成场景截止时间 · UTC" : "观察截止时间 · UTC");
    setText("asof-time", time(config.as_of));
    setText("generated-time", snapshot ? `快照生成于 ${time(snapshot.generated_at)}` : "尚无扫描快照");
  }

  function setMetric(id, value) {
    setText(id, pct(value));
    $(id).className = `metric-value numeric ${tone(value)}`;
  }
  function renderRegime(snapshot) {
    const regime = snapshot.regime || {};
    setText("regime-state", label(regime.state));
    $("regime-state").className = `metric-value ${regime.state === "supportive" ? "positive" : regime.state === "defensive" ? "negative" : "neutral"}`;
    setText("regime-caption", { supportive: "趋势、轮动与广度条件相对改善", mixed: "条件尚未同向，逐币检查", defensive: "弱势环境，优先观察风险", unknown: "环境数据不足，暂不下判断" }[regime.state] || "等待完整环境数据");
    setMetric("btc-return", regime.btc_return_30d);
    setMetric("eth-btc-return", regime.eth_btc_return_30d);
    setText("breadth", pct(regime.breadth_above_sma20, { signed: false, digits: 0 }));
    setText("breadth-caption", `覆盖 ${number(regime.coverage_count, 0)} 个标的 · 固定样本池口径`);
    $("regime-reasons").replaceChildren(...arrays(regime.reasons).map((reason) => node("span", "", reasonText(reason))));
  }

  function renderSectors(snapshot) {
    const sectors = arrays(snapshot.sectors);
    const sectorNames = [...new Set([...sectors.map((item) => item.sector), ...arrays(snapshot.candidates).map((item) => item.sector)].filter(Boolean))];
    if (!sectorNames.includes(state.sector)) state.sector = "all";
    $("sector-filter").replaceChildren(new Option("全部板块", "all"), ...sectorNames.map((sector) => new Option(sector, sector)));
    $("sector-filter").value = state.sector;
    const chips = sectors.map((sector) => {
      const button = node("button", "sector-chip");
      button.type = "button";
      button.setAttribute("aria-pressed", String(state.sector === sector.sector));
      button.title = RS_FORMULA;
      button.append(node("strong", "", sector.sector || "未分类"), node("span", `sector-value ${tone(sector.median_rs_30d)}`, pct(sector.median_rs_30d)), node("small", "", `${number(sector.strong_count, 0)} 个偏强 / ${number(sector.count, 0)} 个观察标的`));
      button.addEventListener("click", () => {
        state.sector = state.sector === sector.sector ? "all" : sector.sector;
        renderSectors(state.snapshot);
        renderTable();
      });
      return button;
    });
    $("sector-strip").replaceChildren(...(chips.length ? chips : [node("p", "subtle", "当前快照没有足够的板块数据。") ]));
  }

  function sparkline(points, { width = 92, height = 28, detail = false, levels = [], lastClose = null } = {}) {
    const data = arrays(points).filter((point) => finite(point?.value) && point.value > 0);
    if (data.length < 2) return node("span", "subtle", "走势不足");
    const ns = "http://www.w3.org/2000/svg";
    const svgNode = (tag, attrs, text) => {
      const element = document.createElementNS(ns, tag);
      Object.entries(attrs).forEach(([key, value]) => element.setAttribute(key, String(value)));
      if (text !== undefined) element.textContent = text;
      return element;
    };
    const svg = svgNode("svg", { viewBox: `0 0 ${width} ${height}`, role: "img", "aria-label": detail ? "日线收盘相对走势，首个可用点归一为 100；不含盘中价格" : "日线收盘走势" });
    svg.append(svgNode("title", {}, detail ? "日线收盘相对走势与结构参考位" : "日线收盘走势"));
    const initial = data[0].value;
    const values = data.map((point) => point.value / initial * 100);
    // Input points are already rebased; reconstruct the first actual price
    // from the final actual close before projecting absolute structure prices.
    const basePrice = finite(lastClose) && lastClose > 0 ? lastClose / (data[data.length - 1].value / initial) : null;
    const visibleLevels = detail && finite(basePrice) && basePrice > 0
      ? levels.filter((line) => finite(line.value) && line.value > 0)
        .map((line) => ({ ...line, normalized: line.value / basePrice * 100 }))
        .filter((line) => finite(line.normalized) && line.normalized > 0) : [];
    const minimum = Math.min(...values, ...visibleLevels.map((line) => line.normalized));
    const maximum = Math.max(...values, ...visibleLevels.map((line) => line.normalized));
    const spread = Math.max(maximum - minimum, Math.abs(maximum) * 0.02, 0.001);
    const min = minimum - spread * 0.1;
    const max = maximum + spread * 0.1;
    const padding = detail ? 15 : 2;
    const graphWidth = width - padding * 2 - (detail ? 66 : 0);
    const x = (index) => padding + index / (data.length - 1) * graphWidth;
    const y = (value) => height - padding - (value - min) / (max - min) * (height - padding * 2);
    if (detail) {
      for (let step = 0; step < 4; step += 1) {
        const value = min + (max - min) * step / 3;
        svg.append(svgNode("line", { x1: padding, x2: width - 5, y1: y(value), y2: y(value), stroke: "#25303c", "stroke-width": 1 }));
        svg.append(svgNode("text", { x: width - 5, y: y(value) - 5, "text-anchor": "end", fill: "#6b7c90", "font-family": "monospace", "font-size": 9 }, number(value, 1)));
      }
      visibleLevels.forEach((line) => {
        svg.append(svgNode("line", { x1: padding, x2: padding + graphWidth, y1: y(line.normalized), y2: y(line.normalized), stroke: line.color, "stroke-width": 1, "stroke-dasharray": "4 4", opacity: 0.7, "data-reference-price": line.value, "data-normalized-level": line.normalized }));
        svg.append(svgNode("text", { x: padding + graphWidth + 5, y: y(line.normalized) + 3, fill: line.color, "font-size": 8 }, line.title));
      });
    }
    const path = values.map((value, index) => `${index === 0 ? "M" : "L"}${x(index).toFixed(2)},${y(value).toFixed(2)}`).join(" ");
    const color = values[values.length - 1] >= values[0] ? "#8fd5b0" : "#f2959c";
    if (detail) svg.append(svgNode("path", { d: `${path} L${x(values.length - 1)},${height - padding} L${x(0)},${height - padding} Z`, fill: color, opacity: 0.04 }));
    svg.append(svgNode("path", { d: path, fill: "none", stroke: color, "stroke-width": detail ? 1.9 : 1.5, "stroke-linecap": "round", "stroke-linejoin": "round" }));
    return svg;
  }

  function candidateRow(candidate) {
    const row = node("tr", "candidate-row");
    const first = node("td");
    const name = node("div", "candidate-name");
    const symbol = shortSymbol(candidate.symbol);
    const monogram = node("span", "coin-monogram", symbol.slice(0, 2));
    monogram.setAttribute("aria-hidden", "true");
    const button = node("button", "symbol-button");
    button.type = "button";
    button.setAttribute("aria-label", `查看 ${candidate.symbol} 的观察依据`);
    button.append(node("strong", "", symbol), node("small", "", candidate.sector || "未分类"));
    button.addEventListener("click", () => openCandidate(candidate));
    name.append(monogram, button); first.append(name); row.append(first);
    const status = node("td"); status.append(node("span", `state-pill ${Object.hasOwn(labels, candidate.status) ? candidate.status : ""}`, label(candidate.status))); row.append(status);
    [candidate.daily?.return_7d, candidate.daily?.return_30d, candidate.rs_30d].forEach((value, index) => {
      const cell = node("td", `number-col numeric ${tone(value)}`, pct(value));
      if (index === 2) cell.title = RS_FORMULA;
      row.append(cell);
    });
    row.append(node("td", "number-col numeric", finite(candidate.setup?.relative_volume) ? `${number(candidate.setup.relative_volume)}×` : "—"));
    const score = node("td", "number-col numeric score", finite(candidate.score) ? number(candidate.score * 100, 0) : "—");
    score.title = "观察排序分（0–100），不代表胜率"; row.append(score);
    const chart = node("td"); const chartWrap = node("div", "row-sparkline"); chartWrap.append(sparkline(candidate.daily?.sparkline)); chart.append(chartWrap); row.append(chart);
    const last = node("td"); const open = node("button", "row-open", "↗"); open.type = "button"; open.setAttribute("aria-label", `打开 ${symbol} 详情`); open.addEventListener("click", () => openCandidate(candidate)); last.append(open); row.append(last);
    return row;
  }

  function renderTable() {
    const candidates = arrays(state.snapshot?.candidates);
    const filtered = candidates.filter((candidate) => {
      const filterMatch = state.filter === "all" || (state.filter === "risk" ? ["avoid", "invalidated", "extended", "insufficient_data"].includes(candidate.status) || candidate.review_status === "blocked" : candidate.status === state.filter);
      return filterMatch && (state.sector === "all" || candidate.sector === state.sector) && `${candidate.symbol || ""} ${candidate.sector || ""}`.toLowerCase().includes(state.search);
    }).sort((a, b) => (finite(a.rank) ? a.rank : Infinity) - (finite(b.rank) ? b.rank : Infinity) || (b.score || 0) - (a.score || 0) || String(a.symbol).localeCompare(String(b.symbol)));
    $("candidate-rows").replaceChildren(...filtered.map(candidateRow));
    setText("candidate-count", number(candidates.length, 0));
    $("empty-state").hidden = filtered.length > 0;
    if (state.snapshot) {
      setText("empty-title", candidates.length ? "没有符合当前筛选的候选" : "本次扫描没有观察候选");
      setText("empty-description", candidates.length ? "尝试切换状态、板块或搜索条件。" : "请查看数据覆盖、排除原因和错误明细；空池也是有效结果。");
      setText("result-caption", `显示 ${filtered.length} / ${candidates.length} · 无自动交易`);
    }
    document.querySelectorAll("[data-filter]").forEach((button) => button.setAttribute("aria-pressed", String(button.dataset.filter === state.filter)));
  }

  function renderPolicy(snapshot) {
    const policy = snapshot?.policy || state.status?.policy || {};
    const items = [
      ["实盘执行", policy.execution_eligible === false ? "不接入" : "未确认 · 按不可执行处理"],
      ["训练 / 模型评分", policy.training_eligible === false && policy.model_scored === false ? "不启用" : "策略信息需复核"],
      ["Holdout 消耗", policy.holdout_consumed === false ? "未消耗（服务声明）" : policy.holdout_consumed === true ? "已消耗 · 需核查授权记录" : "等待服务确认"],
    ];
    $("policy-list").replaceChildren(...items.map(([title, value]) => { const item = node("li"); item.append(node("span", "", title), node("strong", "", value)); return item; }));
  }
  function renderEvidence(snapshot) {
    const sources = arrays(snapshot.sources);
    setText("source-count", String(sources.length));
    const sourceElements = sources.map((source) => {
      const item = node("div", "source-item");
      item.append(externalLink(source.name || "数据来源", source.url), node("small", "", `读取于 ${time(source.observed_at)}`));
      return item;
    });
    const basis = snapshot.universe?.selection_basis;
    sourceElements.push(node("p", "", `选池依据：${typeof basis === "string" ? basis : basis ? JSON.stringify(basis) : "未提供"}`));
    sourceElements.push(node("p", "", `配置哈希：${snapshot.config_hash || "—"}`));
    $("source-list").replaceChildren(...sourceElements);
    const errors = arrays(snapshot.errors);
    const exclusions = arrays(snapshot.universe?.exclusions);
    setText("error-count", String(errors.length + exclusions.length));
    const errorElements = [
      ...errors.map((error) => node("p", "", `[数据错误] ${error.symbol || "全局"} · ${error.stage || "—"}：${error.message || "未提供详情"}`)),
      ...exclusions.map((item) => node("p", "", `[未纳入] ${item.symbol || "—"}：${reasonText(item.reason || "未提供原因")}`)),
    ];
    $("error-list").replaceChildren(...(errorElements.length ? errorElements : [node("p", "", "本次快照未报告排除或数据错误。") ]));
    const partial = snapshot.status === "partial" || errors.length > 0;
    $("partial-notice").hidden = !partial;
    setText("partial-notice", `本次仅获得部分数据：已观察 ${number(snapshot.universe?.observed, 0)} / 请求 ${number(snapshot.universe?.requested, 0)}，${errors.length} 项错误。未成功覆盖的标的不能当作弱势或无机会；请查看“未纳入与数据错误”。`);
  }

  function renderSnapshot(snapshot) {
    if (snapshot.schema_version !== "altcoin-rotation-v1" || !Array.isArray(snapshot.candidates)) throw new Error("快照版本或候选数据格式不受支持，未覆盖已有结果。");
    state.snapshot = snapshot;
    clearError(); renderMode(snapshot); renderRegime(snapshot); renderSectors(snapshot); renderTable(); renderPolicy(snapshot); renderEvidence(snapshot);
    setText("coverage-caption", `请求 ${number(snapshot.universe?.requested, 0)} · 已观察 ${number(snapshot.universe?.observed, 0)} · 纳入 ${number(snapshot.universe?.eligible, 0)} 个`);
    setText("scan-id", snapshot.scan_id || "—");
    $("scan-id").title = snapshot.scan_id || "";
    setText("activity", `快照已读取 · ${time(snapshot.as_of)} · 扫描用时 ${number(snapshot.duration_seconds, 1)} 秒 · 不自动持续扫描`);
    updateButtons();
  }

  function reasonsList(reasons, fallback = "服务未提供更多依据。") {
    const list = node("ul", "reason-list");
    const items = arrays(reasons);
    list.append(...(items.length ? items : [fallback]).map((reason) => node("li", "", reasonText(reason))));
    return list;
  }
  function metric(title, value, color = "") {
    const item = node("div", "detail-metric"); item.append(node("span", "", title), node("strong", color, value)); return item;
  }
  function section(title) {
    const item = node("section", "detail-section"); item.append(node("h3", "", title)); return item;
  }
  function openCandidate(candidate) {
    setText("detail-title", `${shortSymbol(candidate.symbol)} / ${candidate.sector || "未分类"}`);
    const content = document.createDocumentFragment();
    const intro = node("div", "detail-intro");
    intro.append(node("span", `state-pill ${Object.hasOwn(labels, candidate.status) ? candidate.status : ""}`, label(candidate.status)), node("p", "", `观察分 ${finite(candidate.score) ? number(candidate.score * 100, 0) : "—"} / 100 · ${candidate.review_status ? label(candidate.review_status) : "需人工复核"} · 分数不是胜率`));
    content.append(intro);
    content.append(node("p", "detail-warning", `${state.snapshot.mode === "historical_research" ? `历史研究截止 ${time(state.snapshot.as_of)}。` : state.snapshot.mode === "synthetic_demo" ? "合成场景，不是真实行情。" : `观察截止 ${time(state.snapshot.as_of)}。`} 下方为观察参考，事件风险需人工审核，非可执行买点。${state.stale ? " 当前显示上次快照，刷新未成功。" : ""}`));
    const chartSection = section("日线收盘相对走势");
    const chart = node("div", "detail-chart");
    chart.append(sparkline(candidate.daily?.sparkline, { width: 640, height: 190, detail: true, lastClose: candidate.daily?.close, levels: [
      { title: "平台上沿", value: candidate.setup?.range_high, color: "#edc28a" },
      { title: "平台下沿", value: candidate.setup?.range_low, color: "#a0c1e6" },
    ] }));
    const dates = arrays(candidate.daily?.sparkline).filter((point) => finite(point?.value) && point.value > 0);
    const dateLabels = node("div", "chart-dates"); dateLabels.append(node("span", "", time(dates[0]?.time, true)), node("span", "", time(dates[dates.length - 1]?.time, true))); chart.append(dateLabels); chartSection.append(chart);
    chartSection.append(node("p", "caption", `首个可用收盘点 = 100。虚线按最后日线真实价格还原同一基准；缺少真实价格时不绘参考线。走势图不含盘中价格。`));
    const headlineMetrics = node("div", "detail-metrics");
    const rs7 = metric("7 日 / BTC 相对收益", pct(candidate.rs_7d), tone(candidate.rs_7d));
    const rs30 = metric("30 日 / BTC 相对收益", pct(candidate.rs_30d), tone(candidate.rs_30d));
    rs7.title = rs30.title = RS_FORMULA;
    headlineMetrics.append(metric("日线末值", price(candidate.daily?.close)), rs7, rs30); chartSection.append(headlineMetrics);
    content.append(chartSection);

    const structure = section("结构与触发依据");
    const grid = node("div", "structure-grid");
    [["4 小时结构", candidate.setup], ["15 分钟观察", candidate.trigger]].forEach(([title, data]) => {
      const block = node("div", "structure-block"); block.append(node("h4", "", `${title} · ${label(data?.state)}`), reasonsList(data?.reasons), node("p", "caption", `判断截止 ${time(data?.decision_at)}`)); grid.append(block);
    }); structure.append(grid);
    const levels = node("div", "detail-metrics"); levels.append(metric("平台上沿", price(candidate.setup?.range_high)), metric("平台下沿", price(candidate.setup?.range_low)), metric("4h 相对成交量", finite(candidate.setup?.relative_volume) ? `${number(candidate.setup.relative_volume)}×` : "—")); structure.append(levels);
    if (arrays(candidate.reasons).length) { structure.append(node("p", "caption", "候选层依据"), reasonsList(candidate.reasons)); }
    content.append(structure);

    const risk = candidate.risk || {};
    const riskSection = section("风险测算示例 · 每 10,000 资金单位");
    riskSection.append(node("p", "caption", `测算状态：${label(risk.status)}。以下沿用服务端风险设置，仅帮助检查参考位与仓位关系。`));
    const riskMetrics = node("div", "detail-metrics");
    riskMetrics.append(metric("观察参考价", price(risk.entry_reference)), metric("失效参考价", price(risk.stop_reference)), metric("参考距离", pct(risk.distance_pct, { signed: false })), metric("示例名义仓位", number(risk.notional_per_10000, 2)), metric("模型化损失预算", number(risk.max_loss_per_10000, 2)), metric("测算往返成本", pct(risk.cost_pct, { signed: false, digits: 2 })));
    riskSection.append(riskMetrics, node("p", "caption", "损失预算是计算值，不能保证成交或限制实际损失；跳空、深度变化及滑点可能使实际结果偏离。"), reasonsList(risk.reasons, "未提供完整风险测算条件。")); content.append(riskSection);

    const eventsSection = section("事件与供给风险");
    const events = arrays(candidate.events);
    if (!events.length) eventsSection.append(node("p", "caption", "本快照未提供已登记事件。空记录不代表没有解锁、迁移、下架或其他风险，仍需人工核实。"));
    events.forEach((event) => {
      const card = node("div", "event-card"); const heading = node("div", "event-title"); heading.append(node("strong", "", event.title || "未命名事件"), node("span", "event-severity", event.severity ? label(event.severity) : "待核实"));
      card.append(heading, node("small", "", `发布：${time(event.published_at)} · 生效：${time(event.effective_at)}`), externalLink("查看事件原始来源 ↗", event.source_url, "event-link")); eventsSection.append(card);
    }); content.append(eventsSection);

    const derivative = candidate.derivatives || {};
    const derivativesSection = section("衍生品观察");
    const oiUnit = derivative.open_interest_unit === "base_asset_units_as_reported" ? `基础资产 ${derivative.base_asset || "源单位"}` : derivative.open_interest_unit || "源单位";
    const quoteCurrency = derivative.quote_currency || "源报价单位";
    const derivativeMetrics = node("div", "detail-metrics"); derivativeMetrics.append(
      metric("最新上报资金费率", pct(derivative.funding_rate, { digits: 4 })),
      metric("费率周期", finite(derivative.interval_hours) ? `${number(derivative.interval_hours)} 小时` : "未知"),
      metric(`未平仓量（${oiUnit}）`, number(derivative.open_interest, 2)),
      metric(`估算持仓名义额（${quoteCurrency}）`, number(derivative.open_interest_quote, 2)));
    const derivativeSource = derivative.source === "Binance USD-M, not spot flow" ? "Binance USD-M 衍生品接口；不是现货资金流" : derivative.source || "未提供";
    derivativesSection.append(node("p", "caption", "辅助背景，不纳入观察评分。各接口独立读取，不能视为同步快照。"),
      derivativeMetrics, node("p", "caption", `来源：${derivativeSource}。数据状态：${label(derivative.status)} · 最后读取：${time(derivative.observed_at)}。`),
      node("p", "caption", "持仓名义额由基础资产数量乘标记价估算，单位为源报价货币；USDT 仅近似美元，未核验汇率。最新费率不是已核验的结算记录，间隔未知时不假定为 8 小时。"));
    const derivativeSources = arrays(derivative.sources);
    if (derivativeSources.length) derivativeSources.forEach((source) => {
      const block = node("p", "caption");
      block.append(externalLink(derivativeSourceLabels[source.kind] || source.kind || source.name || "接口来源", source.url),
        node("span", "", ` · ${label(source.status)} · 交易所事件：${time(source.event_time)} · 本地读取：${time(source.observed_at)}`));
      derivativesSection.append(block);
    });
    else if (derivative.event_time && typeof derivative.event_time === "object") {
      Object.entries(derivative.event_time).forEach(([kind, stamp]) => derivativesSection.append(
        node("p", "caption", `${derivativeSourceLabels[kind] || kind} · 交易所事件：${time(stamp)}`)));
    }
    const derivativeReasons = [...arrays(derivative.reasons), ...(derivative.reason ? [derivative.reason] : [])];
    derivativesSection.append(reasonsList(derivativeReasons, "未提供衍生品背景；缺失不能视为杠杆健康。"));
    content.append(derivativesSection);
    content.append(node("p", "caption subtle", `日线最后一根：${time(candidate.daily?.last_bar_at)} · 快照：${state.snapshot.scan_id || "—"}`));
    $("candidate-detail").replaceChildren(content);
    $("candidate-dialog").showModal();
    $("candidate-dialog").scrollTop = 0;
  }

  function renderJournal(data) {
    const events = arrays(data.events).slice(0, 40);
    setText("journal-count", number(data.total ?? events.length, 0));
    $("journal-empty").hidden = events.length > 0;
    setText("journal-empty", "还没有状态变化记录。");
    const items = events.map((event) => {
      const item = node("li"); item.append(node("span", "journal-dot"));
      const body = node("div"); const heading = node("div", "journal-item-heading"); heading.append(node("strong", "", shortSymbol(event.symbol)), node("span", "", `${event.previous_state ? label(event.previous_state) : "首次观察"} → ${label(event.state)}`));
      body.append(heading, node("p", "journal-description", `${event.kind || "状态变化"} · ${event.scan_id || "—"}`));
      const stamp = node("time", "journal-time", time(event.as_of));
      if (event.as_of) stamp.dateTime = String(event.as_of);
      item.append(body, stamp); return item;
    });
    $("journal-list").replaceChildren(...items);
    setText("journal-note", `展示服务返回的前 ${events.length} 条。时间为各次研究截止时间（UTC），不等于此刻行情。`);
  }
  async function loadJournal() {
    try { renderJournal(await request("/api/journal", { timeout: 15000 })); }
    catch (error) { setText("journal-note", `日志读取失败：${readableError(error)}。现有日志未更新。`); }
  }
  async function loadStatus() {
    try {
      state.status = await request("/api/status", { timeout: 15000 });
      connection(true, state.status.busy ? "本地服务 · 正在扫描" : "本地服务已连接");
      updateButtons(); return true;
    } catch (error) {
      connection(false, "本地服务连接失败");
      showError(readableError(error)); updateButtons(); return false;
    }
  }

  async function scan() {
    if (state.loading) return;
    state.loading = true; updateButtons(); clearError();
    setText("activity", "正在按服务端固定配置扫描…完成后更新快照，当前结果暂时保留。");
    try {
      const snapshot = await request("/api/scan", { method: "POST" });
      renderSnapshot(snapshot);
      connection(true, "本地服务已连接");
      await loadJournal();
    } catch (error) {
      showError(`${error.status === 409 ? "已有扫描正在执行。" : "扫描未完成。"} ${readableError(error)}`);
      setText("activity", "本次扫描未更新结果。可在服务恢复后再次扫描。");
    } finally { state.loading = false; updateButtons(); }
  }
  async function exportCSV() {
    if (!state.snapshot || state.exportBusy) return;
    state.exportBusy = true; updateButtons();
    let url;
    try {
      const response = await fetch("/api/export.csv", { cache: "no-store", headers: { Accept: "text/csv" } });
      if (!response.ok) throw new Error(`导出失败（HTTP ${response.status}）`);
      const contentType = response.headers.get("content-type") || "";
      if (!contentType.toLowerCase().includes("csv")) throw new Error("导出未返回 CSV 文件，请检查本地服务。");
      url = URL.createObjectURL(await response.blob());
      const link = node("a"); link.href = url;
      link.download = `rotation-${String(state.snapshot.scan_id || "snapshot").replace(/[^a-zA-Z0-9_-]/g, "_")}.csv`;
      document.body.append(link); link.click(); link.remove();
      setText("activity", "已导出服务端当前完整候选快照 CSV；页面筛选不改变导出范围。");
    } catch (error) { showError(readableError(error), state.stale); }
    finally { if (url) setTimeout(() => URL.revokeObjectURL(url), 1000); state.exportBusy = false; updateButtons(); }
  }

  async function initialize() {
    const [status, config, snapshot, journal] = await Promise.allSettled([
      request("/api/status", { timeout: 15000 }), request("/api/config", { timeout: 15000 }),
      request("/api/snapshot", { timeout: 15000 }), request("/api/journal", { timeout: 15000 }),
    ]);
    const errors = [];
    if (status.status === "fulfilled") { state.status = status.value; connection(true, status.value.busy ? "本地服务 · 正在扫描" : "本地服务已连接"); }
    else { connection(false, "本地服务连接失败"); errors.push(`状态读取失败：${readableError(status.reason)}`); }
    if (config.status === "fulfilled") { state.config = config.value; setText("config-content", JSON.stringify(config.value, null, 2)); }
    else { setText("config-content", `配置读取失败：${readableError(config.reason)}`); errors.push(`配置读取失败：${readableError(config.reason)}`); }
    renderMode(); renderPolicy();
    if (snapshot.status === "fulfilled") {
      try { renderSnapshot(snapshot.value); }
      catch (error) { errors.push(readableError(error)); }
    } else if (snapshot.reason.status === 404) {
      setText("activity", "尚无扫描记录。点击“扫描一次”，沿用当前研究配置生成快照。");
      if (state.config?.mode === "historical_research") setText("empty-description", `当前是历史研究模式，截止 ${time(state.config.as_of)}。点击“扫描一次”读取这个历史窗口。`);
    } else errors.push(`快照读取失败：${readableError(snapshot.reason)}`);
    if (journal.status === "fulfilled") renderJournal(journal.value);
    else setText("journal-note", `日志读取失败：${readableError(journal.reason)}`);
    if (errors.length) showError(errors.join("；"), false);
    updateButtons();
  }

  $("scan-button").addEventListener("click", scan);
  $("export-button").addEventListener("click", exportCSV);
  $("config-button").addEventListener("click", async () => {
    $("config-dialog").showModal();
    try {
      state.config = await request("/api/config", { timeout: 15000 });
      setText("config-content", JSON.stringify(state.config, null, 2));
      if (!state.snapshot) renderMode();
      if (!state.status) await loadStatus();
    } catch (error) { setText("config-content", `配置读取失败：${readableError(error)}`); }
  });
  document.querySelectorAll("[data-filter]").forEach((button) => button.addEventListener("click", () => { state.filter = button.dataset.filter; renderTable(); }));
  $("sector-filter").addEventListener("change", (event) => { state.sector = event.target.value; if (state.snapshot) renderSectors(state.snapshot); renderTable(); });
  $("search").addEventListener("input", (event) => { state.search = event.target.value.trim().toLowerCase(); renderTable(); });
  document.querySelectorAll("[data-close]").forEach((button) => button.addEventListener("click", () => $(button.dataset.close).close()));
  document.querySelectorAll("dialog").forEach((dialog) => dialog.addEventListener("click", (event) => {
    if (event.target !== dialog) return;
    const rect = dialog.getBoundingClientRect();
    if (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom) dialog.close();
  }));
  initialize().catch((error) => { connection(false, "界面初始化失败"); showError(readableError(error)); updateButtons(); });
})();
