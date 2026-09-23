/* Read-only strategy registry and explicitly controlled paper-run workspace. */
(() => {
  "use strict";

  const $ = (id) => document.getElementById(id);
  const escapeHTML = (value) => String(value ?? "").replace(/[&<>"']/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[char]));
  const finite = (value) => value !== null && value !== "" && value !== undefined && Number.isFinite(Number(value));
  const number = (value) => finite(value) ? Number(value).toLocaleString("zh-CN") : "—";
  const dateTime = (value) => finite(value) && Number(value) > 0
    ? new Intl.DateTimeFormat("zh-CN", { timeZone: "Asia/Shanghai", year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false }).format(Number(value))
    : "—";
  const request = async (url, options = {}) => {
    const response = await fetch(url, { cache: "no-store", headers: { Accept: "application/json", ...(options.body ? { "Content-Type": "application/json" } : {}) }, ...options });
    let body = null;
    const contentType = response.headers?.get?.("content-type") || "";
    if (contentType.includes("json")) body = await response.json();
    if (!response.ok) throw new Error(typeof body?.detail === "string" ? body.detail : `服务返回 HTTP ${response.status}`);
    return body;
  };
  const strategyState = { active: false, loaded: false, loading: false, error: "", items: [], sources: [], query: "", stage: "all", pending: new Set() };
  const paperState = { active: false, loaded: false, loading: false, error: "", items: [], nowMs: null, selectedStrategyId: "", symbols: "BTC-USDT-SWAP, ETH-USDT-SWAP", timeframes: ["15m"], selectedRunId: "", detail: null, detailOffset: 0, detailRevision: 0, detailLoading: false, detailError: "", pendingRunId: "", creating: false };

  const strategyLabel = (value) => ({ research: "研究中", rejected: "已否定", archived: "已归档", active: "已登记", implemented: "已实现", draft: "草稿", paper_supported: "支持模拟（未验证）", unsupported_exit_contract: "模拟退出规则未就绪" }[value] || String(value || "未记录"));
  const sideLabel = (value) => ({ both: "多空", long: "多头", short: "空头" }[value] || String(value || "未记录"));
  const kindLabel = (value) => ({ signal: "信号策略", entry: "入场规则", exit: "退出规则", composite: "组合策略", spike_burst_v128: "启动信号", joint: "联合信号", trendline_breakout: "趋势线突破", yolo_confirmed: "模型确认" }[value] || String(value || "策略"));
  const runStateLabel = (value) => ({ running: "运行中", paused: "已暂停", stopped: "已停止", error: "运行异常" }[value] || String(value || "未知状态"));
  const decisionStateLabel = (value) => ({ pending: "等待未来开盘", accepted: "已模拟入场", open: "持仓观察中", closed: "已退出", skipped: "未入场", rejected: "未接受", error: "处理异常", censored: "截尾（无模拟平仓）", stopped: "截尾（无模拟平仓）" }[value] || String(value || "未记录"));
  const stageOptions = (value) => ["research", "rejected", "archived"].map((stage) => `<option value="${stage}"${value === stage ? " selected" : ""}>${strategyLabel(stage)}</option>`).join("");
  const tokens = (value) => Array.isArray(value) ? value : [];

  function strategyCard(item) {
    const id = String(item.id ?? "");
    const timeframes = tokens(item.timeframes).map(escapeHTML).join("、") || "未记录";
    const factors = tokens(item.factor_ids).map(String).join(", ");
    const experiments = tokens(item.experiment_ids).map(String).join(", ");
    const canPaper = item.paper_supported === true;
    const stage = ["research", "rejected", "archived"].includes(item.status) ? item.status : "research";
    const noteText = Array.isArray(item.notes) ? item.notes.join("\n") : (item.notes || "");
    return `<article class="strategy-card">
      <div class="strategy-card-top"><div><span class="research-badge">${escapeHTML(kindLabel(item.kind))}</span><span class="research-badge status-${escapeHTML(item.status || "unknown")}">${escapeHTML(strategyLabel(item.status))}</span></div><span class="research-mono">${escapeHTML(id)}</span></div>
      <h3>${escapeHTML(item.name || "未命名策略")} <small>${escapeHTML(item.version || "")}</small></h3>
      <p class="strategy-description">${escapeHTML(item.description || "暂无说明。")}</p>
      <dl class="strategy-facts"><dt>方向 / 周期</dt><dd>${escapeHTML(sideLabel(item.side))} · ${timeframes}</dd><dt>入场规则</dt><dd>${escapeHTML(item.entry_rule || "未记录")}</dd><dt>退出规则</dt><dd>${escapeHTML(item.exit_rule || "未记录")}</dd><dt>登记成本</dt><dd>${finite(item.cost_bp) ? `${escapeHTML(item.cost_bp)} bp` : "未记录"}</dd><dt>生产资格</dt><dd>登记为否</dd><dt>源码摘要</dt><dd class="research-mono">${escapeHTML(item.source_hash || "运行时冻结源码快照")}</dd></dl>
      <div class="strategy-card-actions">${canPaper ? `<button type="button" class="research-button primary" data-paper-strategy="${escapeHTML(id)}">在模拟实盘中选择</button>` : `<span class="research-caption">尚未登记为可模拟策略</span>`}</div>
      <details class="strategy-edit"><summary>研究备注与关联</summary><form data-strategy-edit="${escapeHTML(id)}" data-revision="${escapeHTML(item.revision ?? "")}">
        <label>研究阶段<select name="stage">${stageOptions(stage)}</select></label>
        <label>研究备注<textarea name="notes" rows="3">${escapeHTML(noteText)}</textarea></label>
        <label>关联因子编号 <small>多个编号用逗号分隔</small><input name="factor_ids" value="${escapeHTML(factors)}"></label>
        <label>关联实验编号 <small>多个编号用逗号分隔</small><input name="experiment_ids" value="${escapeHTML(experiments)}"></label>
        <button class="research-button" type="submit"${strategyState.pending.has(id) ? " disabled" : ""}>${strategyState.pending.has(id) ? "正在保存…" : "保存研究登记"}</button><span class="research-caption">保存备注、阶段和关联，不会运行或启用策略。</span>
      </form></details>
    </article>`;
  }

  function renderStrategies() {
    const root = $("strategies-workspace");
    if (!root) return;
    const query = strategyState.query.trim().toLocaleLowerCase();
    const filtered = strategyState.items.filter((item) => {
      if (strategyState.stage !== "all" && item.status !== strategyState.stage) return false;
      return !query || [item.id, item.name, item.description, item.kind, item.entry_rule, item.exit_rule, item.notes, ...tokens(item.factor_ids), ...tokens(item.experiment_ids)].some((value) => String(value ?? "").toLocaleLowerCase().includes(query));
    });
    const error = strategyState.error ? `<div class="research-error" role="alert">${escapeHTML(strategyState.error)} <button class="research-button" type="button" data-strategy-refresh>重试</button></div>` : "";
    const sourceList = strategyState.sources.length ? `<details class="strategy-sources"><summary>登记的策略来源（${number(strategyState.sources.length)}）</summary><ul>${strategyState.sources.map((source) => `<li>${escapeHTML(source.name || "来源")}${source.url ? ` · ${escapeHTML(source.url)}` : ""}</li>`).join("")}</ul></details>` : "";
    const statusText = strategyState.loading && !strategyState.loaded ? "正在读取策略登记…" : !strategyState.loaded ? "策略登记尚未读取。" : strategyState.items.length === 0 ? "当前没有登记策略。" : filtered.length === 0 ? "没有匹配的策略。" : "";
    root.innerHTML = `<div class="strategy-intro research-callout"><strong>策略登记与模拟观察</strong><p>策略库记录规则、研究状态和关联；模拟实盘只观察后续新信号，不下单。登记为可模拟不代表验证盈利或生产准入。</p></div>
      <div class="research-toolbar strategy-toolbar"><div><input id="strategy-search" type="search" value="${escapeHTML(strategyState.query)}" aria-label="搜索策略" placeholder="搜索策略、规则、编号…"><select id="strategy-stage" aria-label="研究阶段"><option value="all">全部阶段</option><option value="research"${strategyState.stage === "research" ? " selected" : ""}>研究中</option><option value="rejected"${strategyState.stage === "rejected" ? " selected" : ""}>已否定</option><option value="archived"${strategyState.stage === "archived" ? " selected" : ""}>已归档</option></select><span class="research-caption">显示 ${number(filtered.length)} / ${number(strategyState.items.length)} 项</span></div><button type="button" class="research-button" data-strategy-refresh${strategyState.loading ? " disabled" : ""}>${strategyState.loading ? "正在刷新…" : "刷新登记"}</button></div>
      ${error}${statusText ? `<p class="research-empty">${escapeHTML(statusText)}</p>` : `<div class="strategy-grid">${filtered.map(strategyCard).join("")}</div>`}${sourceList}`;
    bindStrategyEvents(root);
  }

  async function refreshStrategies(options = {}) {
    if (strategyState.loading) return;
    strategyState.loading = true;
    strategyState.error = "";
    renderStrategies();
    try {
      const data = await request("/api/research/strategies");
      if (!Array.isArray(data?.items)) throw new Error("策略登记数据格式有误。");
      strategyState.items = data.items;
      strategyState.sources = Array.isArray(data.sources) ? data.sources : [];
      strategyState.loaded = true;
    } catch (error) {
      strategyState.error = error.message || "读取策略登记失败。";
    } finally {
      strategyState.loading = false;
      renderStrategies();
      if (paperState.active && options.silentPaper !== true) renderPaper();
    }
  }

  function parseIds(value) {
    return [...new Set(String(value || "").split(/[\n,，]/).map((item) => item.trim()).filter(Boolean))];
  }
  async function saveStrategy(form) {
    const id = form.dataset.strategyEdit;
    if (!id || strategyState.pending.has(id)) return;
    const values = new FormData(form);
    const payload = {
      expected_revision: finite(form.dataset.revision) ? Number(form.dataset.revision) : form.dataset.revision,
      stage: values.get("stage"), notes: values.get("notes") || "",
      factor_ids: parseIds(values.get("factor_ids")), experiment_ids: parseIds(values.get("experiment_ids")),
    };
    strategyState.pending.add(id);
    renderStrategies();
    try {
      await request(`/api/research/strategies/${encodeURIComponent(id)}`, {
        method: "PUT", body: JSON.stringify(payload),
      });
      await refreshStrategies();
    } catch (error) {
      strategyState.error = error.message || "保存策略登记失败。";
    } finally {
      strategyState.pending.delete(id);
      renderStrategies();
    }
  }

  function bindStrategyEvents(root) {
    if (root.dataset.bound) return;
    root.dataset.bound = "true";
    root.addEventListener("input", (event) => {
      if (event.target.id === "strategy-search") { strategyState.query = event.target.value; renderStrategies(); $("strategy-search")?.focus(); }
    });
    root.addEventListener("change", (event) => {
      if (event.target.id === "strategy-stage") { strategyState.stage = event.target.value; renderStrategies(); }
    });
    root.addEventListener("click", (event) => {
      const refreshButton = event.target.closest?.("[data-strategy-refresh]");
      if (refreshButton) { refreshStrategies(); return; }
      const paperButton = event.target.closest?.("[data-paper-strategy]");
      if (paperButton) {
        paperState.selectedStrategyId = paperButton.dataset.paperStrategy;
        if (typeof window.setView === "function") window.setView("paper"); else location.hash = "#paper";
      }
    });
    root.addEventListener("submit", (event) => {
      const form = event.target.closest?.("form[data-strategy-edit]");
      if (!form) return;
      event.preventDefault();
      saveStrategy(form);
    });
  }

  const supportedStrategies = () => strategyState.items.filter((item) => item.paper_supported === true && item.production_eligible !== true);
  const selectedStrategy = () => strategyState.items.find((item) => item.id === paperState.selectedStrategyId);
  function runMetrics(metrics) {
    const values = metrics || {};
    return '<div class="paper-run-metrics"><span>接受 ' + number(values.accepted) + '</span><span>已结束 ' + number(values.closed) +
      '</span><span>观察中 ' + number(values.open) + '</span><span>待处理 ' + number(values.pending) + '</span><span>跳过 ' +
      number(values.skipped) + '</span><span>截尾 ' + number(values.censored) + '</span><span>合计 R ' +
      (finite(values.net_r) ? Number(values.net_r).toFixed(2) : "—") + '</span><span>胜率 ' +
      (finite(values.win_rate) ? (Number(values.win_rate) * 100).toFixed(1) + "%" : "—") + '</span></div>';
  }
  function runCard(run) {
    const selected = run.id === paperState.selectedRunId;
    const pending = paperState.pendingRunId === run.id;
    const status = String(run.status || "unknown");
    const runId = String(run.id ?? "");
    const heartbeatAge = finite(run.heartbeat_ms) && finite(paperState.nowMs)
      ? Math.max(0, Number(paperState.nowMs) - Number(run.heartbeat_ms)) : null;
    const heartbeatStale = ["running", "paused"].includes(status) && heartbeatAge !== null && heartbeatAge > 90000;
    const heartbeatText = ["running", "paused"].includes(status)
      ? heartbeatAge === null ? "等待后台启动"
        : heartbeatStale ? "后台未更新 · " + escapeHTML(dateTime(run.heartbeat_ms))
          : "后台心跳 " + escapeHTML(dateTime(run.heartbeat_ms))
      : finite(run.heartbeat_ms) ? "最近后台心跳 " + escapeHTML(dateTime(run.heartbeat_ms)) : "后台心跳未记录";
    const actionButton = (name, label) => '<button class="research-button" type="button" data-run-action="' + name + '" data-run-id="' + escapeHTML(runId) + '"' + (pending ? ' disabled' : '') + '>' + (pending ? '正在处理…' : label) + '</button>';
    const action = status === "running" ? actionButton("pause", "暂停新增入场") : status === "paused" ? actionButton("resume", "恢复新增入场") : "";
    const stop = ["running", "paused", "error"].includes(status)
      ? '<button class="research-button paper-stop" type="button" data-run-action="stop" data-run-id="' + escapeHTML(runId) + '"' + (pending ? ' disabled' : '') + '>停止并保留未平仓截尾</button>' : "";
    const symbols = Array.isArray(run.spec?.symbols) ? run.spec.symbols.join("、") : "";
    const timeframes = Array.isArray(run.spec?.timeframes) ? run.spec.timeframes.join("、") : "";
    const cost = finite(run.spec?.cost_bp) ? escapeHTML(run.spec.cost_bp) + " bp" : "未记录";
    return '<article class="paper-run-card' + (selected ? ' selected' : '') + '"><button class="paper-run-select" type="button" data-run-select="' + escapeHTML(runId) + '"><span><strong>' +
      escapeHTML(run.strategy_name || run.strategy_id || "模拟任务") + '</strong><small>' + escapeHTML(run.strategy_version || "") + " · " + escapeHTML(runId) +
      '</small></span><span class="research-badge status-' + escapeHTML(status) + '">' + escapeHTML(runStateLabel(status)) +
      '</span></button><div class="paper-run-meta"><span>登记 ' + escapeHTML(dateTime(run.created_ms)) + '</span><span>观察设置 ' +
      (symbols ? escapeHTML(symbols) : "未记录") + " · " + (timeframes ? escapeHTML(timeframes) : "未记录") + '</span><span>成本 ' + cost +
      '</span><span class="paper-heartbeat' + (heartbeatStale ? ' stale' : '') + '">' + heartbeatText + '</span></div>' + runMetrics(run.metrics) +
      (run.error ? '<p class="research-error">' + escapeHTML(run.error) + '</p>' : '') + '<div class="paper-run-actions">' + action + stop + '</div></article>';
  }
  function strategyChoices() {
    const choices = supportedStrategies();
    if (!choices.some((item) => item.id === paperState.selectedStrategyId)) paperState.selectedStrategyId = choices[0]?.id || "";
    return choices.map((item) => `<option value="${escapeHTML(item.id)}"${item.id === paperState.selectedStrategyId ? " selected" : ""}>${escapeHTML(item.name || item.id)} · ${escapeHTML(item.version || "")}</option>`).join("");
  }
  function exitPrecision(trade, timeframe) {
    const precision = trade.exit_time_precision;
    if (precision === "within_bar") {
      const start = trade.exit_bar_open_ms ?? trade.exit_time_start_ms ?? trade.exit_time_ms;
      const end = trade.exit_bar_end_ms ?? trade.exit_bar_close_ms ?? trade.exit_time_end_ms;
      return start !== undefined && end !== undefined
        ? dateTime(start) + " – " + dateTime(end) + "（K线区间，非秒级精确时点）"
        : "仅定位到 " + (timeframe || "该周期") + " K 线区间，未记录秒级精确时点";
    }
    const label = ({ bar_open: "K线开盘时点", bar_close: "K线收盘时点", bar_close_legacy: "历史K线收盘时间口径" })[precision];
    return label ? label + " · " + dateTime(trade.exit_time_ms) : dateTime(trade.exit_time_ms);
  }
  function decisionRow(item) {
    const trade = item.trade || {};
    const stopped = ["stopped", "censored"].includes(item.status) || ["stopped", "censored"].includes(trade.status);
    const exitLabel = stopped ? "截尾状态" : "退出时间精度";
    const exitValue = stopped && !finite(trade.exit_price)
      ? "停止时未模拟平仓；未记录退出时间" : exitPrecision(trade, item.timeframe);
    const rLabel = stopped && !finite(trade.net_r) ? "已实现 R · 未平仓截尾" : "已实现 R";
    return '<article class="paper-decision"><div class="paper-decision-heading"><strong>' +
      escapeHTML(item.symbol || "未知合约") + " · " + escapeHTML(item.timeframe || "未知周期") + " · " +
      escapeHTML(sideLabel(item.side)) + '</strong><span class="research-badge">' +
      escapeHTML(decisionStateLabel(item.status)) + '</span></div><dl class="paper-decision-facts">' +
      '<dt>信号收盘</dt><dd>' + escapeHTML(dateTime(item.signal_close_ms)) + '</dd>' +
      '<dt>实际观察</dt><dd>' + escapeHTML(dateTime(item.observed_ms)) + '</dd>' +
      '<dt>计划模拟入场</dt><dd>' + escapeHTML(dateTime(item.scheduled_entry_ms)) + '</dd>' +
      '<dt>入场价 / 时间</dt><dd>' + (finite(trade.entry_price) ? escapeHTML(trade.entry_price) : "—") + " · " +
      escapeHTML(dateTime(trade.entry_time_ms)) + '</dd><dt>退出价格</dt><dd>' +
      (finite(trade.exit_price) ? escapeHTML(trade.exit_price) : stopped ? "未模拟平仓" : "—") + '</dd><dt>' + exitLabel + '</dt><dd>' +
      escapeHTML(exitValue) + '</dd>' +
      '<dt>初始止损</dt><dd>' + (finite(trade.initial_stop) ? escapeHTML(trade.initial_stop) : "—") + '</dd>' +
      '<dt>' + rLabel + '</dt><dd>' + (finite(trade.net_r) ? Number(trade.net_r).toFixed(3) : "—") + '</dd>' +
      '<dt>浮动 R</dt><dd>' + (finite(trade.unrealized_r) ? Number(trade.unrealized_r).toFixed(3) : "—") + '</dd></dl>' +
      (item.reason ? '<p class="paper-decision-reason">' + escapeHTML(item.reason) + '</p>' : "") +
      '<small class="research-mono">事件 ' + escapeHTML(item.event_id || "未记录") + '</small></article>';
  }
  function renderPaperDetail() {
    const detail = paperState.detail;
    if (!paperState.selectedRunId) return `<div class="research-empty">选择已有模拟任务，或登记一个新的后续观察。</div>`;
    if (paperState.detailLoading && !detail) return `<p class="research-empty">正在读取模拟记录…</p>`;
    if (paperState.detailError && !detail) return `<div class="research-error" role="alert">${escapeHTML(paperState.detailError)} <button type="button" class="research-button" data-paper-retry-detail>重试</button></div>`;
    if (!detail) return `<p class="research-empty">尚未读取模拟记录。</p>`;
    const total = Number(detail.total || 0), offset = Number(detail.offset || 0), limit = Number(detail.limit || 50);
    const currentPage = Math.floor(offset / Math.max(1, limit)) + 1, pages = Math.max(1, Math.ceil(total / Math.max(1, limit)));
    const download = `/api/research/paper/runs/${encodeURIComponent(String(detail.id || paperState.selectedRunId))}/export`;
    const decisions = Array.isArray(detail.decisions) ? detail.decisions : [];
    const fillPolicy = detail.spec?.exit_fill_policy || detail.spec?.fill_policy || "未记录";
    return `<details class="paper-fill-assumptions"><summary>模拟成交假设</summary><p>成交策略标识：<code>${escapeHTML(fillPolicy)}</code></p><p>入场按实际观察后下一根尚未开始的 K 线开盘，使用该 K 线开盘价模拟；退出按预先冻结的 K 线规则模拟。未建模逐次退出下单延迟，闭合 OHLC 计算结果不等同真实报价或盘口成交。</p></details><div class="paper-detail-heading"><div><h3>观察记录</h3><p>决策在信号收盘后实际观察时记录；模拟入场安排在之后的未来开盘。</p></div><a class="research-button" href="${escapeHTML(download)}" download>导出 JSON</a><a class="research-button" href="${escapeHTML(download.replace("/export", "/sources"))}" download>下载源码快照</a></div><div class="paper-rules"><div><strong>入场规则</strong><p>${escapeHTML(detail.spec?.entry_rule || "未记录")}</p></div><div><strong>退出规则</strong><p>${escapeHTML(detail.spec?.exit_rule || "未记录")}</p></div><div><strong>源码摘要</strong><p class="research-mono">${escapeHTML(detail.spec?.source_hash || "未记录")}</p></div></div>${paperState.detailError ? `<div class="research-error" role="alert">${escapeHTML(paperState.detailError)}</div>` : ""}${decisions.length ? `<div class="paper-decisions">${decisions.map(decisionRow).join("")}</div>` : `<p class="research-empty">这个模拟任务目前没有可显示的决策记录。</p>`}<div class="paper-pagination"><button class="research-button" type="button" data-decision-page="prev"${offset <= 0 || paperState.detailLoading ? " disabled" : ""}>上一页</button><span>第 ${number(currentPage)} / ${number(pages)} 页 · 共 ${number(total)} 条</span><button class="research-button" type="button" data-decision-page="next"${offset + limit >= total || paperState.detailLoading ? " disabled" : ""}>下一页</button></div>`;
  }
  function paperResultsContentHTML() {
    const error = paperState.error ? `<div class="research-error" role="alert">${escapeHTML(paperState.error)} <button type="button" class="research-button" data-paper-refresh>重试</button></div>` : "";
    const loading = paperState.loading && !paperState.loaded ? `<p class="research-empty">正在读取模拟登记…</p>` : "";
    const empty = paperState.loaded && paperState.items.length === 0 ? `<p class="research-empty">暂无模拟任务。选择支持模拟的策略并登记后续观察。</p>` : "";
    const runs = paperState.items.length ? `<div class="paper-run-list" data-paper-run-list>${paperState.items.map(runCard).join("")}</div>` : "";
    return `${error}<div class="research-section-heading"><h2>模拟任务</h2><span>${paperState.loaded ? `${number(paperState.items.length)} 项` : "等待读取"}</span></div>${loading}${empty}${runs}${paperState.nowMs ? `<p class="research-caption">服务快照时间：${escapeHTML(dateTime(paperState.nowMs))} · 不代表逐笔行情进度或交易所账户状态</p>` : ""}`;
  }
  function paperResultsHTML() {
    return `<div data-paper-results>${paperResultsContentHTML()}</div>`;
  }
  function renderPaper() {
    const root = $("paper-workspace");
    if (!root) return;
    const choices = supportedStrategies();
    const choicesMarkup = strategyChoices();
    const selected = selectedStrategy();
    const details = selected ? `<div class="paper-selected-rule"><strong>所选策略</strong><span>${escapeHTML(selected.name || selected.id)} · ${escapeHTML(selected.version || "")}</span><small>入场：${escapeHTML(selected.entry_rule || "未记录")} · 退出：${escapeHTML(selected.exit_rule || "未记录")}</small></div>` : "";
    root.innerHTML = `<div class="paper-intro research-callout"><strong>前向模拟，不向交易所下单</strong><p>服务实际观察信号后，才按下一次未来开盘记录模拟入场，再按策略原退出规则跟踪。统计使用 R，不代表账户收益；模拟不计资金费和盘口滑点。暂停只停止新的入场，已有模拟仓位继续跟踪；停止时未平仓记录会保留为截尾，不会补造平仓。登记、暂停和停止不会训练或提升策略。</p><p><a href="#shadow">查看保留的 V7 / V8 前向影子旧视图 ↗</a></p></div>
      <div class="research-toolbar paper-toolbar"><div><h2>新建模拟运行</h2><span class="research-caption">默认 BTC / ETH · 固定往返成本 20 bp · 最多 20 个合约</span></div><button type="button" class="research-button" data-paper-refresh${paperState.loading ? " disabled" : ""}>${paperState.loading ? "正在刷新…" : "刷新任务"}</button></div>
      <form class="paper-create-form" data-paper-create><label>策略<select name="strategy_id" required${choices.length ? "" : " disabled"}>${choicesMarkup || `<option value="">没有登记为可模拟的策略</option>`}</select></label><label>合约 <small>逗号分隔，最多 20 个，例如 BTC-USDT-SWAP</small><input name="symbols" value="${escapeHTML(paperState.symbols)}" autocomplete="off" required></label><fieldset class="paper-timeframes"><legend>观察周期</legend>${["15m", "30m", "1H", "4H"].map((timeframe) => `<label><input type="checkbox" name="timeframes" value="${timeframe}"${paperState.timeframes.includes(timeframe) ? " checked" : ""}> ${timeframe === "1Dutc" ? "日线" : timeframe}</label>`).join("")}</fieldset><button class="research-button primary" type="submit"${!choices.length || paperState.creating ? " disabled" : ""}>${paperState.creating ? "正在启动…" : "启动模拟"}</button><span class="research-caption">没有历史回填；只有任务创建后的新观察会进入记录。</span></form>
      ${details}${paperResultsHTML()}
      <section class="paper-detail-section" aria-label="模拟决策记录">${renderPaperDetail()}</section>`;
    bindPaperEvents(root);
  }

  async function refreshPaper(trigger = "manual") {
    if (paperState.loading) return;
    const root = $("paper-workspace");
    const activeElement = document.activeElement;
    const editingForm = trigger === "periodic" && Boolean(activeElement?.closest?.(".paper-create-form"));
    paperState.loading = true;
    paperState.error = "";
    if (!editingForm) renderPaper();
    try {
      if (!strategyState.loaded) await refreshStrategies({ silentPaper: editingForm });
      if (!strategyState.loaded) throw new Error(strategyState.error || "策略列表暂不可用，无法登记模拟任务。");
      const data = await request("/api/research/paper/runs");
      if (!Array.isArray(data?.items)) throw new Error("模拟任务数据格式有误。");
      paperState.items = data.items;
      paperState.nowMs = data.now_ms;
      paperState.loaded = true;
      if (!paperState.items.some((run) => run.id === paperState.selectedRunId)) paperState.selectedRunId = paperState.items[0]?.id || "";
      if (paperState.selectedRunId) await refreshPaperDetail({ silent: editingForm });
      else { paperState.detail = null; paperState.detailError = ""; }
    } catch (error) {
      paperState.error = error.message || "读取模拟任务失败。";
    } finally {
      paperState.loading = false;
      if (editingForm) {
        const results = root?.querySelector?.("[data-paper-results]");
        if (results) results.innerHTML = paperResultsContentHTML();
      } else renderPaper();
    }
  }
  async function refreshPaperDetail(options = {}) {
    const id = paperState.selectedRunId;
    if (!id) return;
    const silent = options.silent === true;
    const revision = ++paperState.detailRevision;
    const offset = paperState.detailOffset;
    paperState.detailLoading = true;
    paperState.detailError = "";
    if (!paperState.detail || String(paperState.detail.id) !== String(id)) paperState.detail = null;
    if (!silent) renderPaper();
    try {
      const data = await request(`/api/research/paper/runs/${encodeURIComponent(String(id))}?limit=50&offset=${offset}`);
      if (paperState.selectedRunId === id && paperState.detailRevision === revision) paperState.detail = data;
    } catch (error) {
      if (paperState.detailRevision === revision) paperState.detailError = error.message || "读取决策记录失败。";
    } finally {
      if (paperState.detailRevision === revision) {
        paperState.detailLoading = false;
        if (!silent) renderPaper();
      }
    }
  }
  function requestId() {
    if (window.crypto?.randomUUID) return window.crypto.randomUUID();
    return `paper-${Date.now()}-${Math.random().toString(36).slice(2)}`;
  }
  async function createRun(form) {
    if (paperState.creating) return;
    const values = new FormData(form);
    const strategyId = String(values.get("strategy_id") || "");
    const symbols = [...new Set(String(values.get("symbols") || "").split(/[\s,，]+/).map((value) => value.trim().toUpperCase()).filter(Boolean))];
    const timeframes = values.getAll("timeframes").map(String);
    if (!strategyId) { paperState.error = "请先选择登记为可模拟的策略。"; renderPaper(); return; }
    if (!symbols.length || symbols.length > 20) { paperState.error = "请输入 1 至 20 个合约。"; renderPaper(); return; }
    if (!timeframes.length) { paperState.error = "请至少选择一个观察周期。"; renderPaper(); return; }
    if (symbols.some((symbol) => !/^[A-Z0-9]{1,25}-USDT-SWAP$/.test(symbol))) { paperState.error = "合约格式应为 BTC-USDT-SWAP 这样的永续合约编号。"; renderPaper(); return; }
    paperState.symbols = symbols.join(", ");
    paperState.timeframes = timeframes;
    paperState.selectedStrategyId = strategyId;
    paperState.creating = true;
    paperState.error = "";
    renderPaper();
    try {
      const run = await request("/api/research/paper/runs", { method: "POST", body: JSON.stringify({ strategy_id: strategyId, symbols, timeframes, request_id: requestId() }) });
      paperState.selectedRunId = run.id;
      paperState.detailOffset = 0;
      paperState.detail = null;
      paperState.loaded = false;
      await refreshPaper();
    } catch (error) {
      paperState.error = error.message || "无法登记模拟任务。";
    } finally {
      paperState.creating = false;
      renderPaper();
    }
  }
  async function runAction(runId, action) {
    if (!runId || paperState.pendingRunId) return;
    paperState.pendingRunId = runId;
    paperState.error = "";
    renderPaper();
    try {
      await request(`/api/research/paper/runs/${encodeURIComponent(String(runId))}/${action}`, { method: "POST" });
      await refreshPaper();
    } catch (error) {
      paperState.error = error.message || `无法${action === "pause" ? "暂停" : action === "resume" ? "继续" : "停止"}任务。`;
    } finally {
      paperState.pendingRunId = "";
      renderPaper();
    }
  }
  function bindPaperEvents(root) {
    if (root.dataset.bound) return;
    root.dataset.bound = "true";
    root.addEventListener("input", (event) => {
      const form = event.target.closest?.("form[data-paper-create]");
      if (!form) return;
      if (event.target.name === "symbols") paperState.symbols = event.target.value;
    });
    root.addEventListener("change", (event) => {
      const form = event.target.closest?.("form[data-paper-create]");
      if (!form) return;
      if (event.target.name === "strategy_id") paperState.selectedStrategyId = event.target.value;
      if (event.target.name === "timeframes") {
        const selected = [...form.querySelectorAll('input[name="timeframes"]:checked')].map((input) => input.value);
        paperState.timeframes = selected;
      }
      renderPaper();
    });
    root.addEventListener("submit", (event) => {
      const form = event.target.closest?.("form[data-paper-create]");
      if (!form) return;
      event.preventDefault();
      createRun(form);
    });
    root.addEventListener("click", (event) => {
      const refreshButton = event.target.closest?.("[data-paper-refresh]");
      if (refreshButton) { refreshPaper(); return; }
      if (event.target.closest?.("[data-paper-retry-detail]")) { refreshPaperDetail(); return; }
      const runSelect = event.target.closest?.("[data-run-select]");
      if (runSelect) {
        paperState.selectedRunId = runSelect.dataset.runSelect;
        paperState.detailOffset = 0;
        paperState.detail = null;
        refreshPaperDetail();
        return;
      }
      const action = event.target.closest?.("[data-run-action]");
      if (action) { runAction(action.dataset.runId, action.dataset.runAction); return; }
      const page = event.target.closest?.("[data-decision-page]");
      if (page && paperState.detail) {
        const limit = Number(paperState.detail.limit || 50);
        const total = Number(paperState.detail.total || 0);
        paperState.detailOffset = page.dataset.decisionPage === "next" ? Math.min(total - 1, paperState.detailOffset + limit) : Math.max(0, paperState.detailOffset - limit);
        refreshPaperDetail();
      }
    });
  }

  window.SpikeStrategies = {
    setActive(active) { strategyState.active = Boolean(active); if (strategyState.active && !strategyState.loaded) return refreshStrategies(); },
    refresh: refreshStrategies,
  };
  window.SpikePaper = {
    setActive(active) { paperState.active = Boolean(active); if (paperState.active && !paperState.loaded) return refreshPaper(); },
    refresh: refreshPaper,
  };
})();
