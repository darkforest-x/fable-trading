/* Discord copier: live-money controls proxied to the loopback copier service (8080).
   Overview data comes from local snapshots; only "同步持仓" asks the exchanges. */
(() => {
  "use strict";

  const BASE = "/api/copier/";
  const POLL_MS = 60_000;
  const PAGE_SIZE = 30;
  const INTENTS = { open: "开仓", close: "平仓", partial_close: "部分平仓", cancel: "撤单", update: "更新", update_sl: "调整止损", update_tp: "调整止盈", hold: "观察", noise: "非信号" };
  const STATUSES = { parsed: "已识别", executed: "已执行", skipped: "已跳过", live: "实盘记录", open: "已提交", new: "新订单",
    filled: "已成交", partially_filled: "部分成交", closed: "已平仓", canceled: "已撤销", stale: "已过期", error: "失败",
    failed: "失败", dry_run: "模拟", pending: "处理中", finished: "已完成" };
  const ORDER_TYPES = { limit: "限价", market: "市价", conditional: "条件", trigger: "触发", post_only: "只挂单" };
  const RISK_FIELDS = [
    ["position_pct", "仓位比例", "每单使用可用保证金的比例，1 = 100%", 0.01],
    ["max_open_positions", "最多持仓", "同时持有的仓位上限", 1],
    ["max_leverage", "最大杠杆", "解析出的杠杆超过此值会被拒绝", 1],
    ["min_confidence", "最低置信度", "DeepSeek 解析置信度低于此值不下单", 0.05],
    ["max_daily_loss_usdt", "日亏损上限 (USDT)", "当日亏损超过后停止开新仓", 1],
  ];
  const state = { active: false, tab: "overview", ordersTab: "positions", card: null, signals: [], total: 0, page: 1,
    filters: { intent: "", status: "", channel_id: "" }, expanded: null, settings: null, switches: null, telegram: null,
    system: null, paper: null, error: "", flash: "", busy: false, timer: null, generation: 0, recent: [] };
  const CLOSE_REASONS = { stop_loss: "止损", take_profit: "止盈", liquidation: "强平", manual_close: "博主平仓",
    partial_close: "部分平仓", reversed: "反手平仓" };

  const $ = (id) => document.getElementById(id);
  const root = () => $("copier-workspace");
  const esc = (value) => String(value ?? "—").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const amount = (value, signed = false) => {
    const n = Number(value);
    if (value == null || value === "" || !Number.isFinite(n)) return "—";
    const sign = signed && n > 0 ? "+" : signed && n < 0 ? "−" : "";
    return sign + Math.abs(n).toLocaleString("zh-CN", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  };
  const count = (value) => Number.isFinite(Number(value)) ? Number(value).toLocaleString("zh-CN") : "—";
  const clock = (value) => {
    if (!value) return "—";
    const text = String(value);
    const ms = Date.parse(/[zZ]|[+-]\d\d:?\d\d$/.test(text) ? text : text + "Z");
    return Number.isFinite(ms) ? new Intl.DateTimeFormat("zh-CN", { timeZone: "Asia/Shanghai", month: "2-digit", day: "2-digit",
      hour: "2-digit", minute: "2-digit", hour12: false }).format(ms) : "—";
  };
  const isShort = (side) => ["sell", "short"].includes(String(side || "").toLowerCase());
  const sidePill = (side) => side ? `<span class="copier-side ${isShort(side) ? "short" : "long"}">${isShort(side) ? "空" : "多"}</span>` : "—";
  const statusTone = (status) => {
    const s = String(status || "").toLowerCase();
    if (["error", "failed"].includes(s)) return "bad";
    if (["skipped", "dry_run", "canceled", "stale"].includes(s)) return "warn";
    if (s === "pending") return "info";
    return s ? "good" : "";
  };
  const statusPill = (status, label) => `<span class="copier-status ${statusTone(status)}">${esc(label || STATUSES[String(status || "").toLowerCase()] || status || "—")}</span>`;
  const channelName = (id) => (state.card?.channel_names || {})[String(id || "")] || (id ? String(id) : "—");
  const parseJSON = (text) => { try { return JSON.parse(text || "{}") || {}; } catch { return {}; } };

  async function api(path, options = {}) {
    const init = { cache: "no-store", ...options, headers: { ...(options.body ? { "content-type": "application/json" } : {}), ...(options.headers || {}) } };
    const response = await fetch(BASE + path, init);
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(typeof data.detail === "string" ? data.detail : `请求失败 HTTP ${response.status}`);
    return data;
  }

  /* ---------- rendering ---------- */

  function modeBanner(card) {
    if (!card) return "";
    const paperOn = (card.channels || []).some((c) => c.exchange === "模拟盘");
    const [tone, title, body] = card.kill_switch
      ? ["halt", "已停止新开单", "保护开关开启：新信号只入库，不下单。已有持仓不会被自动平掉。"]
      : paperOn && card.dry_run
        ? ["sim", "模拟实盘进行中", "名单内频道的信号按实盘同一套逻辑下到本地模拟账户（每个频道 1000 USDT），真实交易所不下单。"]
      : card.dry_run
        ? ["sim", "模拟模式", "新信号会解析并记录模拟订单，不向交易所下单。切到实盘需在设置中由你确认。"]
        : ["live", "实盘模式", "新信号会按路由直接在交易所下单（全仓、按配置杠杆）。"];
    return `<section class="copier-mode ${tone}" aria-live="polite"><div><strong>${title}</strong><p>${body}</p></div>
      <div class="copier-mode-actions">
        ${card.dry_run ? "" : `<button type="button" class="copier-button" data-action="dry-on">切回模拟</button>`}
        ${card.kill_switch ? "" : `<button type="button" class="copier-button danger" data-action="kill-on">停止新开单</button>`}
      </div></section>`;
  }

  function renderOverview() {
    const card = state.card;
    if (!card) return `<p class="copier-empty">正在读取跟单服务…</p>`;
    const t = card.totals || {};
    const simOrders = Number(t.orders_dry_run || 0) + Number(t.orders_paper || 0);
    const liveOrders = Math.max(0, Number(t.orders_total || 0) - simOrders);
    const kpis = [["消息总数", t.total_messages], ["开仓信号", t.open_signals], ["已执行", t.executed], ["已跳过", t.skipped],
      ["实盘订单", liveOrders], ["模拟订单", simOrders]];
    const channels = card.channels.map((c) => `<article class="copier-channel" role="button" tabindex="0" data-channel-detail="${esc(c.channel_id)}" aria-label="查看 ${esc(c.name)} 详情">
        <header><span class="copier-dot ${c.account_ok ? "ok" : "bad"}" aria-hidden="true"></span><h3>${esc(c.name)}</h3>
          <span class="copier-route">${esc(c.exchange)}${c.leverage ? " · " + esc(c.leverage) + "x" : ""}</span></header>
        <dl><div><dt>账户余额</dt><dd>${amount(c.account_total_usdt)} <small>USDT</small></dd></div>
          <div><dt>未实现盈亏</dt><dd class="${c.account_unrealized_pnl >= 0 ? "pos" : "neg"}">${amount(c.account_unrealized_pnl, true)}</dd></div></dl>
        <footer><span>持仓 ${count(c.account_positions)}</span><span>开仓信号 ${count(c.open_signals)} · 执行 ${count(c.executed)}</span>
          <span>${c.monitor_enabled ? "监听中" : "监听已关"} · ${c.account_ok ? "账户在线" : "账户异常"}</span></footer>
      </article>`).join("") || `<p class="copier-empty">没有已路由的频道。</p>`;
    const tabs = [["positions", `持仓 (${count(card.position_count)})`], ["pending", `挂单 (${card.pending.length})`], ["history", `历史 (${card.history.length})`]];
    return `<section class="copier-kpis" aria-label="累计统计">${kpis.map(([k, v]) => `<div><span>${k}</span><strong>${count(v)}</strong></div>`).join("")}</section>
      <section class="copier-channels" aria-label="交易者账户">${channels}</section>
      <p class="copier-footnote">余额与持仓来自跟单服务的健康检查快照（${esc(clock(card.generated_at))} 读取），打开页面不会请求交易所。</p>
      <section class="copier-panel"><div class="copier-panel-head"><h3>当前持仓与订单</h3>
          <div class="segmented" role="group" aria-label="订单分类">${tabs.map(([k, label]) => `<button type="button" data-orders-tab="${k}" class="${state.ordersTab === k ? "selected" : ""}" aria-pressed="${state.ordersTab === k}">${label}</button>`).join("")}</div>
          <button type="button" class="copier-button" data-action="sync"${state.busy ? " disabled" : ""}>同步持仓</button></div>
        ${ordersTable(card)}</section>
      <section class="copier-panel"><div class="copier-panel-head"><h3>最近开仓信号</h3><button type="button" class="copier-link" data-tab="signals">查看全部信号 →</button></div>
        ${signalsTable(state.recent, false)}</section>`;
  }

  function ordersTable(card) {
    if (state.ordersTab === "positions") {
      const rows = card.positions.map((p) => `<tr><td>${esc(p.trader)}</td><td>${esc(p.inst_id || "账户持仓")}</td><td>${sidePill(p.side)}</td>
        <td class="num">${amount(p.notional_usdt)}</td><td class="num ${p.unrealized_pnl >= 0 ? "pos" : "neg"}">${amount(p.unrealized_pnl, true)}</td>
        <td class="num">${amount(p.margin_usdt)}</td><td class="num">${esc(p.sl_trigger)}</td><td class="num">${esc(p.tp_trigger)}</td></tr>`).join("");
      return table(["交易者", "品种", "方向", "持仓价值", "盈亏", "保证金", "止损", "止盈"], rows, "暂无实时持仓");
    }
    const list = state.ordersTab === "pending" ? card.pending : card.history;
    const rows = list.map((o) => `<tr><td>${esc(clock(o.created_at))}</td><td>${esc(o.trader)}</td><td>${esc(o.inst_id)}</td><td>${sidePill(o.side)}</td>
      <td>${esc(ORDER_TYPES[String(o.ord_type || "").toLowerCase()] || o.ord_type)}</td><td class="num">${esc(o.px)}</td>
      <td class="num">${amount(o.notional_usdt)}</td><td>${o.pending ? statusPill("pending", o.paper ? "模拟挂单" : "挂单中") : o.paper ? statusPill("dry_run", "模拟单") : o.local_record ? statusPill("live", "本地记录") : statusPill(o.status)}</td></tr>`).join("");
    return table(["时间", "交易者", "品种", "方向", "类型", "价格", "名义金额", "状态"], rows, state.ordersTab === "pending" ? "暂无交易所挂单" : "暂无历史订单");
  }

  function table(headers, rows, empty) {
    return `<div class="copier-table-wrap"><table class="copier-table"><thead><tr>${headers.map((h) => `<th>${h}</th>`).join("")}</tr></thead>
      <tbody>${rows || `<tr><td colspan="${headers.length}" class="copier-empty">${empty}</td></tr>`}</tbody></table></div>`;
  }

  function signalsTable(items, expandable) {
    const rows = items.map((m) => {
      const ai = parseJSON(m.ai_json);
      const open = expandable && state.expanded === m.id;
      const summary = ai.summary || String(m.content || "").slice(0, 120);
      const head = `<tr class="${expandable ? "copier-row-button" : ""}"${expandable ? ` data-expand="${esc(m.id)}" tabindex="0" aria-expanded="${open}"` : ""}>
        <td>${esc(clock(m.created_at))}</td><td>${esc(channelName(m.channel_id))}<small>${esc(m.author)}</small></td>
        <td>${esc(INTENTS[m.intent] || m.intent || "未解析")}${ai.symbol ? `<small>${esc(ai.symbol)} ${ai.side ? (isShort(ai.side) ? "空" : "多") : ""}</small>` : ""}</td>
        <td class="num">${m.confidence == null ? "—" : Number(m.confidence).toFixed(2)}</td><td>${statusPill(m.status)}</td>
        <td class="copier-summary">${esc(summary)}</td></tr>`;
      if (!open) return head;
      const facts = [["入场", ai.entry ?? ai.entry_price ?? ai.entry_range], ["止损", ai.stop_loss], ["止盈", ai.take_profit], ["杠杆", ai.leverage], ["平仓比例", ai.close_pct]]
        .filter(([, v]) => v != null && v !== "" && !(Array.isArray(v) && !v.length));
      return head + `<tr class="copier-detail"><td colspan="6"><pre>${esc(m.content)}</pre>
        ${facts.length ? `<dl>${facts.map(([k, v]) => `<div><dt>${k}</dt><dd>${esc(Array.isArray(v) ? v.join(" / ") : typeof v === "object" ? JSON.stringify(v) : v)}</dd></div>`).join("")}</dl>` : ""}
        ${m.reject_reason ? `<p class="copier-reject">未执行原因：${esc(m.reject_reason)}</p>` : ""}</td></tr>`;
    }).join("");
    return table(["时间 · 北京", "频道 / 作者", "意图", "置信度", "状态", "摘要"], rows, "暂无信号记录");
  }

  function renderSignals() {
    const names = state.card?.channels || [];
    const select = (key, label, options) => `<label>${label}<select data-filter="${key}"><option value="">全部</option>${options.map(([v, l]) =>
      `<option value="${esc(v)}"${state.filters[key] === v ? " selected" : ""}>${esc(l)}</option>`).join("")}</select></label>`;
    const pages = Math.max(1, Math.ceil(state.total / PAGE_SIZE));
    return `<div class="copier-filters">${select("intent", "意图", Object.entries(INTENTS))}
        ${select("status", "状态", [["executed", "已执行"], ["skipped", "已跳过"], ["parsed", "已识别"], ["error", "失败"], ["pending", "处理中"]])}
        ${select("channel_id", "频道", names.map((c) => [c.channel_id, c.name]))}</div>
      <section class="copier-panel">${signalsTable(state.signals, true)}
        <div class="copier-pager"><span>共 ${count(state.total)} 条 · 第 ${state.page} / ${pages} 页 · 点击一行查看原文</span><div>
          <button type="button" class="copier-button" data-page="-1"${state.page <= 1 ? " disabled" : ""}>上一页</button>
          <button type="button" class="copier-button" data-page="1"${state.page >= pages ? " disabled" : ""}>下一页</button></div></div></section>`;
  }

  function switchButton(attr, key, on, label) {
    return `<button type="button" class="copier-switch${on ? " on" : ""}" role="switch" aria-checked="${on}" ${attr}="${esc(key)}"${state.busy ? " disabled" : ""}>
      <span aria-hidden="true"></span>${esc(label)}</button>`;
  }

  function renderSettings() {
    const s = state.settings, sw = state.switches, card = state.card;
    if (!s || !sw || !card) return `<p class="copier-empty">正在读取设置…</p>`;
    const risk = s.risk || {};
    const live = !card.dry_run;
    const features = Object.entries(sw.features || {}).map(([key, on]) => {
      const meta = (sw.feature_meta || {})[key] || {};
      return `<li><div><strong>${esc(meta.label || key)}</strong><small>${esc(meta.desc || "")}</small></div>${switchButton("data-feature", key, on, on ? "开" : "关")}</li>`;
    }).join("");
    const routes = Object.fromEntries((card.channels || []).map((c) => [c.channel_id, c]));
    const blocked = new Set(card.trade_blocked_channels || []);
    const tradeLabel = (id) => blocked.has(String(id)) ? "仅监控，不下单"
      : routes[id] ? `跟单 · ${routes[id].exchange}${routes[id].leverage ? " " + routes[id].leverage + "x" : ""}` : "未设交易路由";
    const channels = (sw.channels || []).map((c) => `<li><div><strong>${esc(channelName(c.channel_id) !== String(c.channel_id) ? channelName(c.channel_id) : c.channel_name)}</strong><small>${esc(tradeLabel(c.channel_id))} · ${esc(c.channel_id)}</small></div>
      ${switchButton("data-channel", c.channel_id, c.enabled, c.enabled ? "监听" : "已关")}</li>`).join("");
    const tg = state.telegram || {}, sys = state.system || {};
    return `<div class="copier-settings">
      <section class="copier-panel"><h3>运行开关</h3>
        <ul class="copier-toggles">
          <li><div><strong>交易模式</strong><small>${live ? "实盘：新信号直接下单" : "模拟：只记录模拟订单，不下单"}</small></div>
            ${live ? `<button type="button" class="copier-button" data-action="dry-on">切回模拟</button>` : `<button type="button" class="copier-button danger" data-action="go-live">切到实盘…</button>`}</li>
          <li><div><strong>停止新开单（保护开关）</strong><small>${card.kill_switch ? "已开启：新信号不下单" : "未开启"}。只阻止新订单，不会平掉已有持仓。</small></div>
            ${card.kill_switch ? `<button type="button" class="copier-button" data-action="kill-off">解除保护…</button>` : `<button type="button" class="copier-button danger" data-action="kill-on">停止新开单</button>`}</li>
        </ul>
        <p class="copier-footnote">恢复实盘、解除保护和修改参数只允许在本机工作台操作；公网入口只能停止交易。</p></section>
      <section class="copier-panel"><h3>风控参数</h3>
        <form id="copier-risk-form" class="copier-form">
          ${RISK_FIELDS.map(([key, label, hint, step]) => `<label><span>${label}</span><input name="${key}" type="number" step="${step}" min="0" value="${esc(risk[key])}" required><small>${hint}</small></label>`).join("")}
          <label><span>默认杠杆</span><input name="default_leverage" type="number" step="1" min="1" value="${esc(s.okx?.default_leverage)}" required><small>频道未单独配置时使用</small></label>
          <label class="copier-check"><input name="require_stop_loss" type="checkbox"${risk.require_stop_loss ? " checked" : ""}> 必须带止损才下单</label>
          <div class="copier-form-actions"><button type="submit" class="copier-button primary"${state.busy ? " disabled" : ""}>保存参数</button></div>
        </form></section>
      <section class="copier-panel"><h3>功能开关</h3><ul class="copier-toggles">${features}</ul></section>
      <section class="copier-panel"><h3>频道监听</h3>
        <p class="copier-footnote">扩展只能读到 Chrome 里正在显示的频道：每个要监控的频道保留一个标签页。第一次读到的新频道会自动加入这里，默认仅监控、不下单。</p>
        <ul class="copier-toggles">${channels || `<li class="copier-empty">没有频道</li>`}</ul></section>
      <section class="copier-panel"><h3>Telegram 与系统</h3>
        <dl class="copier-facts"><div><dt>Telegram 通知</dt><dd>${tg.configured ? (tg.enabled ? "已启用" : "已配置未启用") : "未配置"}${tg.chat_id ? ` · ${esc(tg.chat_id)}` : ""}</dd></div>
          <div><dt>交易所</dt><dd>${esc(sys.exchange)} · ${esc(sys.exchange_mode)}</dd></div>
          <div><dt>意图解析模型</dt><dd>${esc(sys.deepseek_model)}</dd></div>
          <div><dt>Telegram 监听</dt><dd>${sys.telegram_listener_enabled ? "开启" : "关闭"}</dd></div></dl>
        <div class="copier-form-actions"><button type="button" class="copier-button" data-action="tg-test"${state.busy ? " disabled" : ""}>发送测试通知</button>
          <button type="button" class="copier-button" data-action="tg-panel"${state.busy ? " disabled" : ""}>推送控制面板</button></div></section>
    </div>`;
  }

  const signedCls = (v) => v == null ? "" : v >= 0 ? "pos" : "neg";
  const rText = (v) => v == null ? "—" : `${v >= 0 ? "+" : "−"}${Math.abs(v).toFixed(2)}R`;
  const pct = (v) => v == null || !Number.isFinite(Number(v)) ? "—" : `${(Number(v) * 100).toFixed(1)}%`;

  function rStatsPanel(s, title) {
    if (!s || !s.trades) return `<section class="copier-panel"><h3>${title}</h3><p class="copier-empty">还没有已平仓的模拟交易，R 统计会在第一笔平仓后出现。</p></section>`;
    const facts = [["已平仓", count(s.trades)], ["胜率", pct(s.win_rate)], ["平均每笔", rText(s.mean_r)], ["中位数", rText(s.median_r)],
      ["累计", rText(s.sum_r)], ["平均盈利", rText(s.avg_win_r)], ["平均亏损", rText(s.avg_loss_r)],
      ["盈亏比(R)", s.profit_factor_r == null ? "—" : Number(s.profit_factor_r).toFixed(2)], ["最佳 / 最差", `${rText(s.best_r)} / ${rText(s.worst_r)}`],
      ["最大连亏", `${count(s.max_losing_streak)} 笔`], ["R 回撤", rText(s.max_drawdown_r == null ? null : -s.max_drawdown_r)],
      ["平均 1R", `${amount(s.avg_risk_usdt)} U（${pct(s.avg_risk_pct)} 起始资金）`]];
    const total = Object.values(s.buckets || {}).reduce((a, b) => a + b, 0) || 1;
    const bars = Object.entries(s.buckets || {}).map(([label, n]) => `<div class="copier-rbar"><span>${esc(label)}</span>
      <i style="width:${Math.max(2, n / total * 100)}%" class="${label.startsWith("-") || label.startsWith("≤") ? "neg" : "pos"}"></i><b>${n}</b></div>`).join("");
    return `<section class="copier-panel"><h3>${title}</h3><dl class="copier-rfacts">${facts.map(([k, v]) => `<div><dt>${k}</dt><dd>${esc(v)}</dd></div>`).join("")}</dl>
      <div class="copier-rdist">${bars}</div></section>`;
  }

  function rTable(accounts) {
    const rows = accounts.filter((a) => a.r_stats?.trades).map((a) => { const s = a.r_stats; return `<tr><td>${esc(a.trader)}</td>
      <td class="num">${count(s.trades)}</td><td class="num">${pct(s.win_rate)}</td><td class="num ${signedCls(s.mean_r)}">${rText(s.mean_r)}</td>
      <td class="num">${rText(s.median_r)}</td><td class="num">${rText(s.avg_win_r)}</td><td class="num">${rText(s.avg_loss_r)}</td>
      <td class="num">${s.profit_factor_r == null ? "—" : Number(s.profit_factor_r).toFixed(2)}</td><td class="num">${count(s.max_losing_streak)}</td>
      <td class="num">${amount(s.avg_risk_usdt)}<small>${pct(s.avg_risk_pct)}</small></td></tr>`; }).join("");
    return `<section class="copier-panel"><h3>各频道 R 统计</h3>${table(["交易者", "笔数", "胜率", "平均R", "中位R", "平均盈利", "平均亏损", "盈亏比", "最大连亏", "平均1R (U)"], rows, "还没有已平仓的模拟交易")}</section>`;
  }


  function paperSections(open, pending, closed) {
    const openRows = open.map((o) => `<tr><td>${esc(clock(o.opened_at))}</td><td>${esc(o.trader)}</td><td>${esc(o.inst_id)}</td><td>${sidePill(o.side)}</td>
      <td class="num">${esc(o.entry_px)}<small>${o.leverage}x · ${amount(o.notional_usdt)} U</small></td><td class="num">${o.mark == null ? "—" : esc(o.mark)}</td>
      <td class="num">${esc(o.sl)}<small>强平 ${Number(o.liquidation_px).toPrecision(6)}</small></td><td class="num">${esc(o.tp)}</td>
      <td class="num">${amount(o.risk_usdt)}<small>${pct(o.risk_pct_of_equity)} 账户 · 止损距 ${pct(o.stop_distance_pct)}</small></td>
      <td class="num ${signedCls(o.unrealized_pnl)}">${amount(o.unrealized_pnl, true)}<small>${rText(o.current_r)}</small></td></tr>`).join("");
    const pendingRows = pending.map((o) => `<tr><td>${esc(clock(o.created_at))}</td><td>${esc(o.trader)}</td><td>${esc(o.inst_id)}</td><td>${sidePill(o.side)}</td>
      <td class="num">${esc(o.px)}</td><td class="num">${o.mark == null ? "—" : esc(o.mark)}</td><td class="num">${esc(o.sl)}</td><td class="num">${esc(o.tp)}</td>
      <td class="num">${amount(o.notional_usdt)}</td></tr>`).join("");
    const closedRows = closed.map((o) => `<tr><td>${esc(clock(o.closed_at))}</td><td>${esc(o.trader)}</td><td>${esc(o.inst_id)}</td><td>${sidePill(o.side)}</td>
      <td class="num">${esc(o.entry_px)} → ${o.exit_px == null ? "—" : Number(o.exit_px).toPrecision(6)}</td><td>${esc(CLOSE_REASONS[o.close_reason] || o.close_reason)}</td>
      <td class="num ${signedCls(o.r)}">${rText(o.r)}<small>1R = ${amount(o.risk_usdt)} U</small></td><td class="num ${signedCls(o.net_pnl)}">${amount(o.net_pnl, true)}</td></tr>`).join("");
    return `<section class="copier-panel"><h3>模拟持仓 (${open.length})</h3>${table(["开仓 · 北京", "交易者", "品种", "方向", "入场", "现价", "止损", "止盈", "1R (USDT)", "未实现 / 当前R"], openRows, "暂无模拟持仓")}</section>
      <section class="copier-panel"><h3>模拟挂单 (${pending.length})</h3>${table(["下单 · 北京", "交易者", "品种", "方向", "挂单价", "现价", "止损", "止盈", "名义 (USDT)"], pendingRows, "暂无模拟挂单")}</section>
      <section class="copier-panel"><h3>已平仓 (${closed.length})</h3>${table(["平仓 · 北京", "交易者", "品种", "方向", "入场 → 出场", "原因", "R", "净盈亏"], closedRows, "暂无已平仓的模拟交易")}</section>`;
  }

  function accountRow(a) {
    return `<tr><td>${esc(a.trader)}${a.routed ? "" : "<small>已不在名单</small>"}</td>
      <td class="num">${amount(a.equity)}</td><td class="num ${signedCls(a.unrealized_pnl)}">${amount(a.unrealized_pnl, true)}</td>
      <td class="num">${count(a.open_positions)}</td><td class="num">${count(a.closed_trades)}</td>
      <td class="num">${a.closed_trades ? Math.round(a.wins / a.closed_trades * 100) + "%" : "—"}</td>
      <td class="num ${signedCls(a.sum_r)}">${rText(a.sum_r)}</td><td class="num ${signedCls(a.net_pnl)}">${amount(a.net_pnl, true)}</td></tr>`;
  }
  const ACCOUNT_HEAD = ["交易者", "权益 (USDT)", "未实现", "持仓", "已平仓", "胜率", "累计 R", "净盈亏"];

  function renderPaper() {
    const p = state.paper;
    if (!p) return `<p class="copier-empty">正在读取模拟盘…</p>`;
    return `<div class="notice">每个频道独立模拟账户，起始 ${amount(p.starting_equity)} USDT；入场价和杠杆上限沿用实盘规则，仓位按固定风险计算（见下）。按 OKX 已收盘 1 分钟 K 线的高低点加每 ${esc(p.poll_seconds)} 秒最新价撮合，
        同一根 K 线同时碰到止损和止盈按止损算；只在 Gate 上市的合约只按最新价撮合；成本按单边 ${(p.fee_rate_per_side * 100).toFixed(2)}%（往返 ${(p.fee_rate_per_side * 200).toFixed(1)}%），未计资金费。模拟结果不代表能跑赢随机入场。</div>
      <div class="notice">${esc(p.sizing_note)}</div>
      ${rStatsPanel(p.r_stats_all, "总体 R 统计（全部频道、已平仓、扣成本）")}
      <section class="copier-panel"><h3>各频道账户</h3>${table(ACCOUNT_HEAD, p.accounts.map(accountRow).join(""), "还没有模拟账户：名单内频道出现开仓信号后自动建立")}</section>
      ${rTable(p.accounts)}
      ${paperSections(p.open, p.pending, p.closed)}`;
  }

  function renderChannel() {
    const id = state.channelId;
    const c = (state.card?.channels || []).find((x) => x.channel_id === id);
    const back = `<button type="button" class="copier-link" data-back="overview">← 返回总览</button>`;
    if (!state.paper) return `${back}<p class="copier-empty">正在读取频道详情…</p>`;
    const mine = (rows) => rows.filter((r) => String(r.channel_id) === String(id));
    const account = state.paper.accounts.find((a) => String(a.channel_id) === String(id));
    return `<div class="copier-detail-head">${back}
        <h2>${esc(c?.name || channelName(id))}</h2>
        <span class="copier-route">${esc(c?.exchange || "—")}${c?.leverage ? " · " + esc(c.leverage) + "x" : ""}</span>
        <span class="copier-footnote">${c ? (c.monitor_enabled ? "监听中" : "监听已关") : "不在当前路由"} · 频道 ID ${esc(id)}</span></div>
      <section class="copier-panel"><h3>模拟账户</h3>${table(ACCOUNT_HEAD, account ? accountRow(account) : "", "这个频道还没有模拟交易")}</section>
      ${rStatsPanel(account?.r_stats, "R 统计（已平仓、扣成本）")}
      <p class="copier-footnote">${esc(state.paper.sizing_note)}</p>
      ${paperSections(mine(state.paper.open), mine(state.paper.pending), mine(state.paper.closed))}
      <section class="copier-panel"><h3>最近消息（点击一行查看原文）</h3>${signalsTable(state.channelMessages || [], true)}</section>`;
  }

  function render() {
    const el = root();
    if (!el) return;
    const tabs = [["overview", "总览"], ["paper", "模拟盘"], ["signals", "信号流水"], ["settings", "设置"]];
    const body = state.tab === "signals" ? renderSignals() : state.tab === "settings" ? renderSettings()
      : state.tab === "paper" ? renderPaper() : state.tab === "channel" ? renderChannel() : renderOverview();
    el.innerHTML = `${modeBanner(state.card)}
      ${state.error ? `<div class="notice copier-error" role="alert">${esc(state.error)}</div>` : ""}
      ${state.flash ? `<div class="notice copier-flash" role="status">${esc(state.flash)}</div>` : ""}
      <div class="segmented copier-tabs" role="tablist" aria-label="跟单分页">${tabs.map(([k, label]) =>
        `<button type="button" role="tab" data-tab="${k}" class="${state.tab === k ? "selected" : ""}" aria-selected="${state.tab === k}">${label}</button>`).join("")}</div>
      ${body}
      <p class="copier-footnote">信号由 Chrome 扩展从 Discord 网页直接提交到本机跟单服务（127.0.0.1:8080）；工作台不提供信号注入入口。</p>`;
  }

  /* ---------- data ---------- */

  async function loadCard() { state.card = await api("dashboard/orders-card"); }
  async function loadRecent() { state.recent = (await api(`messages?intent=open&per_page=8`)).items || []; }
  async function loadSignals() {
    const q = new URLSearchParams({ page: String(state.page), per_page: String(PAGE_SIZE) });
    Object.entries(state.filters).forEach(([k, v]) => { if (v) q.set(k, v); });
    const data = await api("messages?" + q.toString());
    state.signals = data.items || []; state.total = Number(data.total || 0);
  }
  async function loadSettings() {
    const [settings, switches, telegram, system] = await Promise.all([api("settings"), api("monitor/switches"),
      api("notifications/telegram/status"), api("system/monitor")]);
    Object.assign(state, { settings, switches, telegram, system });
  }

  async function refresh(trigger = "manual") {
    // Only the background poll pauses for hidden pages; opening the view always loads.
    if (!state.active || (trigger === "periodic" && document.hidden)) return;
    const generation = ++state.generation;
    try {
      const jobs = [loadCard()];
      if (state.tab === "overview") jobs.push(loadRecent());
      if (state.tab === "signals") jobs.push(loadSignals());
      if (state.tab === "settings") jobs.push(loadSettings());
      if (state.tab === "paper" || state.tab === "channel") jobs.push(api("paper/summary").then((data) => { state.paper = data; }));
      if (state.tab === "channel") jobs.push(api(`messages?channel_id=${encodeURIComponent(state.channelId)}&per_page=30`)
        .then((data) => { state.channelMessages = data.items || []; }));
      await Promise.all(jobs);
      if (generation === state.generation) state.error = "";
    } catch (error) {
      if (generation === state.generation) state.error = error.message || "跟单服务暂不可用";
    }
    if (generation === state.generation) render();
  }

  async function act(label, request) {
    if (state.busy) return;
    state.busy = true; state.flash = ""; render();
    try {
      await request();
      state.flash = label; state.error = "";
    } catch (error) {
      state.error = error.message || "操作失败";
    } finally {
      state.busy = false;
      await refresh();
    }
  }

  const post = (path, body) => api(path, { method: "POST", body: JSON.stringify(body ?? {}) });
  const actions = {
    "kill-on": () => act("已停止新开单。", () => post("control/kill-switch", { enabled: true })),
    "kill-off": () => {
      if (!confirm("解除保护后，新信号会按当前交易模式处理。确定解除？")) return;
      act("保护已解除。", () => post("control/kill-switch", { enabled: false }));
    },
    "dry-on": () => act("已切回模拟模式。", () => post("control/dry-run", { enabled: true })),
    "go-live": () => {
      const typed = prompt("切到实盘后，新信号会直接在交易所下单（按配置的仓位比例与杠杆）。\n确认请输入：实盘");
      if (typed === null) return;
      if (typed.trim() !== "实盘") { state.error = "未输入“实盘”，交易模式未改变。"; render(); return; }
      act("已切到实盘模式。", () => post("control/dry-run", { enabled: false }));
    },
    sync: () => act("已向交易所同步持仓。", () => post("orders/sync")),
    "tg-test": () => act("测试通知已发送。", () => post("notifications/telegram/test")),
    "tg-panel": () => act("控制面板已推送。", () => post("notifications/telegram/panel")),
  };

  function onClick(event) {
    const target = event.target.closest("button, tr[data-expand], [data-channel-detail]");
    if (!target || !root()?.contains(target)) return;
    if (target.dataset.action) return actions[target.dataset.action]?.();
    if (target.dataset.channelDetail) {
      state.channelId = target.dataset.channelDetail; state.channelMessages = []; state.expanded = null;
      state.tab = "channel"; state.flash = ""; render(); window.scrollTo?.({ top: 0 }); return refresh();
    }
    if (target.dataset.back) { state.tab = target.dataset.back; render(); return refresh(); }
    if (target.dataset.tab) { state.tab = target.dataset.tab; state.flash = ""; render(); return refresh(); }
    if (target.dataset.ordersTab) { state.ordersTab = target.dataset.ordersTab; return render(); }
    if (target.dataset.page) { state.page = Math.max(1, state.page + Number(target.dataset.page)); state.expanded = null; return refresh(); }
    if (target.dataset.expand) { const id = Number(target.dataset.expand); state.expanded = state.expanded === id ? null : id; return render(); }
    if (target.dataset.feature) {
      const on = target.getAttribute("aria-checked") !== "true";
      return act("功能开关已更新。", () => api("monitor/switches", { method: "PUT", body: JSON.stringify({ features: { [target.dataset.feature]: on } }) }));
    }
    if (target.dataset.channel) {
      const on = target.getAttribute("aria-checked") !== "true";
      return act("频道监听已更新。", () => post("monitor/channels/toggle", { channel_id: target.dataset.channel, enabled: on }));
    }
  }

  function onKey(event) {
    if ((event.key === "Enter" || event.key === " ") && event.target.matches?.("tr[data-expand], [data-channel-detail]")) { event.preventDefault(); onClick(event); }
  }

  function onChange(event) {
    const key = event.target.dataset?.filter;
    if (!key) return;
    state.filters[key] = event.target.value; state.page = 1; state.expanded = null;
    refresh();
  }

  function onSubmit(event) {
    if (event.target.id !== "copier-risk-form") return;
    event.preventDefault();
    const form = new FormData(event.target);
    const risk = Object.fromEntries(RISK_FIELDS.map(([key]) => [key, Number(form.get(key))]));
    risk.require_stop_loss = form.get("require_stop_loss") === "on";
    // dry_run / kill_switch are deliberately not part of this form.
    const body = { risk, okx: { default_leverage: Number(form.get("default_leverage")) } };
    act("风控参数已保存。", () => api("settings", { method: "PUT", body: JSON.stringify(body) }));
  }

  function setActive(value) {
    if (state.active === value) return;
    state.active = value;
    clearInterval(state.timer);
    if (!value) return;
    const el = root();
    if (el && !el.dataset.bound) {
      el.dataset.bound = "1";
      el.addEventListener("click", onClick);
      el.addEventListener("keydown", onKey);
      el.addEventListener("change", onChange);
      el.addEventListener("submit", onSubmit);
    }
    render();
    refresh();
    state.timer = setInterval(() => { if (["overview", "paper", "channel"].includes(state.tab)) refresh("periodic"); }, POLL_MS);
  }

  window.SpikeCopier = { refresh, setActive };
})();
