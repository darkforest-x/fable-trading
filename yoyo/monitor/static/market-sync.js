/* 全市场异动: live sync-shock observer + v2 research history. Records only; no orders, no pushes. */
(() => {
  "use strict";
  const HOLDS = ["1h", "4h", "12h", "24h"];
  const INSTRUMENTS = {eth: "ETH", btc: "BTC", alts: "山寨篮子"};
  const NOTION_TRACKER = "https://app.notion.com/p/nick-wiki/3f28856479af8068b0cef31b5baec989";
  // Joint volume-ratio tercile edges, frozen on events before 2025 (exp-market-sync-shock-20261008-v3 edges.csv).
  // Only the 30m up-shock high tier was better in both periods; the rest is a label, not a signal.
  const VOLUME_EDGES = {"30|1": [3.750, 5.399], "30|-1": [3.640, 5.592], "60|1": [3.274, 4.729], "60|-1": [3.543, 5.450]};
  const TIER_NAMES = ["低档", "中档", "高档"];
  // v4 vetoes, computed by the observer (yoyo/monitor/market_sync.py VETO_EDGES): labels, not filters.
  const VETO_NAMES = {rally: "大涨后·慎追", high_vol: "高波动·慎空"};
  const state = {active: false, pending: false, live: null, history: {}, iterations: null, error: null,
    tf: "60", kind: "sync", config: "z3.0_v3.0_b0.75", side: "1", hold: "12h", inst: "eth", vol: "all", limit: 30};
  const root = () => document.getElementById("market-sync-workspace");
  const esc = (v) => String(v ?? "—").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
  const fin = (v) => typeof v === "number" && Number.isFinite(v);
  const clock = (ms, year = false) => ms == null ? "—" : new Intl.DateTimeFormat("zh-CN", {timeZone: "Asia/Shanghai", ...(year ? {year: "numeric"} : {}), month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false}).format(new Date(ms));
  const pct = (v, d = 2) => fin(v) ? `${v >= 0 ? "+" : ""}${(v * 100).toFixed(d)}%` : "—";
  const share = (v) => fin(v) ? `${Math.round(v * 100)}%` : "—";
  const bp = (v) => fin(v) ? `${v >= 0 ? "+" : ""}${v.toFixed(1)}bp` : "—";
  const num = (v, d = 2) => fin(v) ? v.toFixed(d) : "—";
  const tone = (v) => fin(v) ? (v > 0 ? "r-positive" : v < 0 ? "r-negative" : "") : "";
  const parseConfig = (key) => { const m = /^z([\d.]+)_v([\d.]+)_b([\d.]+)$/.exec(key || ""); return m ? {z: +m[1], v: +m[2], b: +m[3]} : null; };
  const configLabel = (key) => { const c = parseConfig(key); return c ? `|z|≥${c.z} · 量≥${c.v}× · 同向≥${Math.round(c.b * 100)}%` : key; };
  const tfLabel = (tf) => tf === "60" ? "1H" : "30m";
  const groupKey = () => `${state.tf}|${state.kind}|${state.config}`;
  const sideName = (s) => s > 0 ? "上涨 → 做多" : "下跌 → 做空";

  function stats(values, controls = []) {
    const xs = values.filter(fin);
    if (!xs.length) return null;
    const sorted = [...xs].sort((a, b) => a - b);
    const mean = xs.reduce((a, b) => a + b, 0) / xs.length;
    const mid = sorted.length % 2 ? sorted[(sorted.length - 1) / 2] : (sorted[sorted.length / 2 - 1] + sorted[sorted.length / 2]) / 2;
    const trimmed = sorted.slice(0, Math.max(0, sorted.length - 5));
    const cs = controls.filter(fin);
    const ctrl = cs.length ? cs.reduce((a, b) => a + b, 0) / cs.length : null;
    return {n: xs.length, mean, median: mid, win: xs.filter(x => x > 0).length / xs.length, ctrl,
      excess: ctrl == null ? null : mean - ctrl, trimmed: trimmed.length ? trimmed.reduce((a, b) => a + b, 0) / trimmed.length : null};
  }

  function historyView() {
    const h = state.history[groupKey()];
    if (!h || h.error) return {rows: [], h};
    const idx = h.exits.indexOf(`time_${state.hold}`) * h.instruments.length + h.instruments.indexOf(state.inst);
    const rows = h.rows.filter(r => state.side === "all" || String(r[1]) === state.side)
      .map(r => ({time: r[0], side: r[1], btc_z: r[2], eth_z: r[3], breadth: r[4], net: r[5][idx], ctrl: r[6][idx]}));
    return {rows, h};
  }

  function volumeTier(e) {
    const edges = VOLUME_EDGES[`${e.minutes}|${e.side}`], v = Math.min(e.btc_vr, e.eth_vr);
    return edges && fin(v) ? edges.filter(x => v >= x).length : null;
  }

  function liveEvents() {
    const snap = state.live?.snapshot;
    if (!snap) return [];
    return snap.events.filter(e => String(e.minutes) === state.tf && e.kind === state.kind && e.config === state.config
      && (state.side === "all" || String(e.side) === state.side) && (state.vol === "all" || volumeTier(e) === 2));
  }

  function envNow() {
    const env = state.live?.snapshot?.environment_now, edges = state.live?.snapshot?.veto_edges;
    if (!env || !fin(env.trend30)) return "";
    const rally = edges && env.trend30 >= edges.rally.edges[state.tf], hot = edges && fin(env.vol30) && env.vol30 >= edges.high_vol.edges[state.tf];
    const warn = [rally ? "现在追上涨冲击要慎重" : "", hot ? "现在空下跌冲击要慎重" : ""].filter(Boolean).join("；");
    return `<small>BTC 30 天 ${pct(env.trend30, 1)} · 小时波动 ${pct(env.vol30, 2).replace("+", "")}${warn ? ` · <b class="ms-warn">${esc(warn)}</b>` : ""}</small>`;
  }

  function overview() {
    const snap = state.live?.snapshot, status = state.live?.status || {};
    const latest = snap?.latest?.[state.tf]?.at(-1);
    const forward = liveEvents().filter(e => e.out_of_sample);
    const fstats = stats(forward.map(e => e.outcomes?.[state.hold]?.done ? e.outcomes[state.hold][state.inst] : null));
    const hs = stats(historyView().rows.map(r => r.net), historyView().rows.map(r => r.ctrl));
    const stateText = {ok: "正常", updating: "更新中", starting: "启动中", error: "出错"}[status.state] || "未运行";
    return `<div class="ms-overview">
      <article><span>当前查看的规则</span><strong>${tfLabel(state.tf)} ${state.kind === "sync" ? "单根" : "连续两根"} · ${esc(state.side === "all" ? "多空都看" : sideName(+state.side))}</strong><small>${esc(configLabel(state.config))} · 持有 ${esc(state.hold)} · ${esc(INSTRUMENTS[state.inst])}</small></article>
      <article><span>最近一根 ${tfLabel(state.tf)} · 北京</span><strong>${latest ? esc(clock(latest.open_ms)) : "—"}</strong><small>${latest ? `BTC z ${num(latest.btc_z)} · ETH z ${num(latest.eth_z)} · 同涨 ${share(latest.breadth_up)}` : "等待首轮数据"}</small>${envNow()}</article>
      <article><span>样本外记录（9/23 起）</span><strong>${forward.length} 次</strong><small>${fstats ? `已满 ${esc(state.hold)} ${fstats.n} 次 · 平均 ${bp(fstats.mean)}` : "还没有已结束的样本"}</small></article>
      <article><span>研究样本 2023-01 至 2026-09</span><strong>${hs ? `${hs.n} 次 · ${bp(hs.mean)}` : "—"}</strong><small>${hs ? `中位 ${bp(hs.median)} · 胜率 ${(hs.win * 100).toFixed(0)}% · 对照 ${bp(hs.ctrl)}` : "该组合没有事件"}</small></article>
    </div>
    <div class="notice">只记录，不下单、不推送。实时数据为币安 USDT 永续已收盘 K 线，每 30 分钟更新（${esc(stateText)}${status.error ? ` · ${esc(status.error)}` : ""}；最近更新 ${esc(clock(status.updated_ms || snap?.generated_ms))}）。研究数据截止 2026-09-23，之后的事件才是新证据。收益均扣 0.2% 往返成本，次根开盘入场。</div>`;
  }

  function segmented(name, options, value) {
    return `<div class="segmented" role="group" aria-label="${esc(name)}">${options.map(([v, label]) => `<button type="button" data-ms="${esc(name)}" data-value="${esc(v)}" class="${v === value ? "selected" : ""}" aria-pressed="${v === value}">${esc(label)}</button>`).join("")}</div>`;
  }

  function controls() {
    const grid = state.live?.snapshot?.grid || ["z2.0_v2.0_b0.6", "z2.0_v2.0_b0.75", "z2.0_v3.0_b0.6", "z2.0_v3.0_b0.75", "z3.0_v2.0_b0.6", "z3.0_v2.0_b0.75", "z3.0_v3.0_b0.6", "z3.0_v3.0_b0.75"];
    return `<div class="ms-controls">
      ${segmented("tf", [["60", "1H"], ["30", "30m"]], state.tf)}
      ${segmented("kind", [["sync", "单根冲击"], ["cluster", "连续两根"]], state.kind)}
      ${segmented("side", [["1", "上涨做多"], ["-1", "下跌做空"], ["all", "全部"]], state.side)}
      ${segmented("hold", HOLDS.map(h => [h, h]), state.hold)}
      ${segmented("inst", Object.entries(INSTRUMENTS), state.inst)}
      ${segmented("vol", [["all", "全部量比"], ["high", "只看高量比"]], state.vol)}
      <label class="ms-select">阈值<select id="ms-config" aria-label="触发阈值">${grid.map(k => `<option value="${esc(k)}" ${k === state.config ? "selected" : ""}>${esc(configLabel(k))}</option>`).join("")}</select></label>
    </div>`;
  }

  function latestTable() {
    const rows = (state.live?.snapshot?.latest?.[state.tf] || []).slice(-12).reverse();
    const c = parseConfig(state.config);
    if (!rows.length) return `<div class="empty-state"><h3>还没有实时数据</h3><p>服务启动后会先回补 7 月底以来的 K 线，约需几分钟。</p></div>`;
    const hit = (ok) => ok ? "ms-hit" : "";
    return `<div class="v130-table-wrap"><table class="v130-table ms-table"><thead><tr><th>K线开盘 · 北京</th><th>BTC 涨跌 / z / 量比</th><th>ETH 涨跌 / z / 量比</th><th>山寨同涨 / 同跌</th><th>状态</th></tr></thead><tbody>${rows.map(r => {
      const side = Math.sign(r.btc_ret || 0), same = side !== 0 && side === Math.sign(r.eth_ret || 0);
      const zOk = fin(r.btc_z) && fin(r.eth_z) && Math.abs(r.btc_z) >= c.z && Math.abs(r.eth_z) >= c.z;
      const vOk = fin(r.btc_vr) && fin(r.eth_vr) && r.btc_vr >= c.v && r.eth_vr >= c.v;
      const wide = side > 0 ? r.breadth_up : r.breadth_down, bOk = fin(wide) && wide >= c.b;
      const met = [same && zOk, vOk, bOk].filter(Boolean).length;
      const label = same && zOk && vOk && bOk ? (side > 0 ? "触发 · 上涨" : "触发 · 下跌") : met >= 2 ? "接近" : "—";
      return `<tr><td>${esc(clock(r.open_ms))}</td>
        <td class="${tone(r.btc_ret)}">${pct(r.btc_ret)}<small><span class="${hit(fin(r.btc_z) && Math.abs(r.btc_z) >= c.z)}">z ${num(r.btc_z)}</span> · <span class="${hit(fin(r.btc_vr) && r.btc_vr >= c.v)}">${num(r.btc_vr, 1)}×</span></small></td>
        <td class="${tone(r.eth_ret)}">${pct(r.eth_ret)}<small><span class="${hit(fin(r.eth_z) && Math.abs(r.eth_z) >= c.z)}">z ${num(r.eth_z)}</span> · <span class="${hit(fin(r.eth_vr) && r.eth_vr >= c.v)}">${num(r.eth_vr, 1)}×</span></small></td>
        <td><span class="${hit(side > 0 && fin(r.breadth_up) && r.breadth_up >= c.b)}">${share(r.breadth_up)}</span> / <span class="${hit(side < 0 && fin(r.breadth_down) && r.breadth_down >= c.b)}">${share(r.breadth_down)}</span><small>${esc(r.n_alts)} 个山寨有数据</small></td>
        <td>${label.startsWith("触发") ? `<strong class="ms-badge">${esc(label)}</strong>` : esc(label)}</td></tr>`;
    }).join("")}</tbody></table></div>`;
  }

  function forwardTable() {
    const events = liveEvents().slice().reverse();
    const research = new Set((state.history[groupKey()]?.rows || []).map(r => `${r[0]}|${r[1]}`));
    const end = state.live?.snapshot?.research_end_ms;
    const inOverlap = (state.history[groupKey()]?.rows || []).filter(r => r[0] >= (state.live?.snapshot?.observe_start_ms || Infinity) && r[0] < end
      && (state.side === "all" || String(r[1]) === state.side));
    const liveKeys = new Set(liveEvents().map(e => `${e.open_ms}|${e.side}`));
    const missing = inOverlap.filter(r => !liveKeys.has(`${r[0]}|${r[1]}`)).length;
    const overlap = events.filter(e => !e.out_of_sample);
    const matched = overlap.filter(e => research.has(`${e.open_ms}|${e.side}`)).length;
    const vetoNote = `<p class="quiet-text">冲击前环境用冲击 K 线开盘前已收盘的 BTC 1H 数据。否决标签来自 v4（边界在 2025 年前冻结）：「大涨后·慎追」= 上涨冲击且 BTC 30 天涨幅在最高档（≥约 13%）；「高波动·慎空」= 下跌冲击且 BTC 30 天小时波动在最高档。只做标记，不剔除事件，用来积累样本外记录。</p>`;
    const tierNote = vetoNote + `<p class="quiet-text">量比档 = BTC、ETH 量比的较小值，按 v3 在 2025 年前事件上冻结的三档划分（30m 上涨高档 ≥${VOLUME_EDGES["30|1"][1]}×，1H 上涨高档 ≥${VOLUME_EDGES["60|1"][1]}×）。v3 里只有 30m 上涨冲击的高量比档在 2025 前后都更好，属于事后发现，这里用来积累样本外记录。</p>`;
    const note = tierNote + (overlap.length || inOverlap.length
      ? `<p class="quiet-text">8/1–9/22 与研究重叠：实时重算 ${overlap.length} 次，其中 ${matched} 次研究里也有；研究有而实时没有 ${missing} 次。山寨池按 K 线成交额排名、只含仍在交易的合约，个别边缘事件可能不同。</p>` : "");
    if (!events.length) return `<div class="empty-state"><h3>暂无符合条件的事件</h3><p>从 2026-08-01 起记录；换个阈值或方向看看。</p></div>${note}`;
    return `<div class="v130-table-wrap"><table class="v130-table ms-table"><thead><tr><th>信号K线 · 北京</th><th>方向</th><th>BTC / ETH z</th><th>量比</th><th>同向</th><th>冲击前环境</th>${HOLDS.map(h => `<th class="${h === state.hold ? "ms-focus" : ""}">${h} · ${esc(INSTRUMENTS[state.inst])}</th>`).join("")}<th>来源</th></tr></thead><tbody>${events.map(e => {
      const source = e.out_of_sample ? `<strong class="ms-badge">样本外</strong>` : research.has(`${e.open_ms}|${e.side}`) ? "研究期 · 已对上" : "研究期 · 研究里没有";
      return `<tr><td>${esc(clock(e.open_ms))}</td><td>${esc(sideName(e.side))}</td><td>${num(e.btc_z)} / ${num(e.eth_z)}</td><td>${num(e.btc_vr, 1)}× / ${num(e.eth_vr, 1)}×${volumeTier(e) == null ? "" : volumeTier(e) === 2 ? `<small><span class="ms-badge">高档</span></small>` : `<small>${TIER_NAMES[volumeTier(e)]}</small>`}</td><td>${share(e.breadth)}</td><td>${fin(e.env?.trend30) ? `BTC 30天 ${pct(e.env.trend30, 1)}` : "—"}${(e.vetoes || []).map(v => `<small><span class="ms-badge ms-badge-warn">${esc(VETO_NAMES[v] || v)}</span></small>`).join("")}</td>${HOLDS.map(h => {
        const o = e.outcomes?.[h] || {}, v = o[state.inst];
        return `<td class="${h === state.hold ? "ms-focus " : ""}${tone(v)}">${o.started ? bp(v) : "待入场"}${o.started && !o.done ? "<small>进行中</small>" : ""}</td>`;
      }).join("")}<td>${source}</td></tr>`;
    }).join("")}</tbody></table></div>${note}`;
  }

  function historySection() {
    const {rows, h} = historyView();
    if (!h) return `<p class="research-empty">正在读取研究事件…</p>`;
    if (h.error) return `<div class="notice" role="alert">${esc(h.error)}</div>`;
    const s = stats(rows.map(r => r.net), rows.map(r => r.ctrl));
    const years = {};
    rows.forEach(r => { const y = new Date(r.time).getUTCFullYear(); (years[y] ||= []).push(r.net); });
    const list = rows.slice().reverse().slice(0, state.limit);
    return `${s ? `<div class="ms-stats">
        <article><span>事件数</span><strong>${s.n}</strong></article>
        <article><span>平均 / 中位</span><strong class="${tone(s.mean)}">${bp(s.mean)}</strong><small>中位 ${bp(s.median)}</small></article>
        <article><span>胜率</span><strong>${(s.win * 100).toFixed(0)}%</strong></article>
        <article><span>随机对照 / 超额</span><strong>${bp(s.ctrl)}</strong><small class="${tone(s.excess)}">超额 ${bp(s.excess)}</small></article>
        <article><span>去掉最好 5 次</span><strong class="${tone(s.trimmed)}">${bp(s.trimmed)}</strong></article>
      </div>
      <div class="ms-years">${Object.entries(years).map(([y, xs]) => { const t = stats(xs); return `<span><b>${y}</b> ${t.n} 次 · <i class="${tone(t.mean)}">${bp(t.mean)}</i> · 胜率 ${(t.win * 100).toFixed(0)}%</span>`; }).join("")}</div>` : `<div class="empty-state"><h3>该组合没有研究事件</h3></div>`}
      ${list.length ? `<div class="v130-table-wrap"><table class="v130-table ms-table"><thead><tr><th>信号K线 · 北京</th><th>方向</th><th>BTC / ETH z</th><th>同向</th><th class="ms-focus">${esc(state.hold)} · ${esc(INSTRUMENTS[state.inst])}</th><th>对照</th></tr></thead><tbody>${list.map(r => `<tr><td>${esc(clock(r.time, true))}</td><td>${esc(sideName(r.side))}</td><td>${num(r.btc_z)} / ${num(r.eth_z)}</td><td>${share(r.breadth)}</td><td class="ms-focus ${tone(r.net)}">${bp(r.net)}</td><td class="${tone(r.ctrl)}">${bp(r.ctrl)}</td></tr>`).join("")}</tbody></table></div>
      ${rows.length > state.limit ? `<button type="button" class="load-more ms-more" id="ms-more">显示更多（共 ${rows.length} 次）</button>` : ""}` : ""}
      ${state.vol === "high" ? `<p class="quiet-text">研究历史没有逐条量比，这里仍显示全部事件。30m 上涨冲击高量比档的研究结果：ETH 24h 扣费后 2025 前 +75bp、2025 后 +102bp（v3）。</p>` : ""}
      <p class="quiet-text">来源 ${esc(h.experiment_id)} @ ${esc(String(h.source_commit).slice(0, 10))}。对照为同月、同 BTC 波动档的 20 个随机入场，方向与持有相同。这些规律是看过数据后找到的，要靠样本外记录验证。</p>`;
  }

  function iterationsSection() {
    const items = state.iterations;
    if (!items) return `<p class="research-empty">正在读取实验登记…</p>`;
    const status = {superseded: "已被取代", inconclusive: "不确定", running: "进行中", done: "完成", rejected: "否决"};
    return `<ol class="ms-iterations">${items.map(it => `<li><div><strong>${esc(it.experiment_id)}</strong><span class="ms-badge">${esc(status[it.status] || it.status)}</span></div>
      <p>${esc(it.single_variable || it.question)}</p>
      <details><summary>结论</summary><p>${esc(it.result)}</p>${it.notes ? `<p class="quiet-text">${esc(it.notes)}</p>` : ""}</details></li>`).join("")}</ol>
      <p class="quiet-text">下一步与分工在 <a href="${NOTION_TRACKER}" target="_blank" rel="noopener noreferrer">Notion 任务跟踪器 ↗</a>。</p>`;
  }

  function render() {
    if (!root()) return;
    if (state.error && !state.live) { root().innerHTML = `<div class="notice" role="alert">${esc(state.error)}；稍后重试。</div>`; return; }
    root().innerHTML = `${overview()}${controls()}
      <section class="ms-section"><div class="section-toolbar"><h2>最近 12 根 ${tfLabel(state.tf)} K 线</h2></div>${latestTable()}</section>
      <section class="ms-section"><div class="section-toolbar"><h2>前向观察记录</h2></div>${forwardTable()}</section>
      <section class="ms-section"><div class="section-toolbar"><h2>研究历史事件</h2></div>${historySection()}</section>
      <section class="ms-section"><div class="section-toolbar"><h2>研究迭代</h2></div>${iterationsSection()}</section>`;
    root().querySelectorAll("[data-ms]").forEach(b => b.addEventListener("click", () => { state[b.dataset.ms] = b.dataset.value; state.limit = 30; render(); loadHistory(); }));
    document.getElementById("ms-config")?.addEventListener("change", e => { state.config = e.target.value; state.limit = 30; render(); loadHistory(); });
    document.getElementById("ms-more")?.addEventListener("click", () => { state.limit += 50; render(); });
  }

  async function getJSON(url) {
    const response = await fetch(url, {cache: "no-store"});
    if (!response.ok) throw new Error(`读取失败 ${response.status}`);
    return response.json();
  }

  async function loadHistory() {
    const key = groupKey();
    if (state.history[key]) return;
    state.history[key] = null;
    try { state.history[key] = await getJSON(`/api/market-sync/history?group=${encodeURIComponent(key)}`); }
    catch (error) { state.history[key] = {error: `研究事件${error.message}`}; }
    if (state.active) render();
  }

  async function refresh() {
    if (!state.active || state.pending || document.hidden) return;
    state.pending = true;
    try {
      const [live, iterations] = await Promise.all([getJSON("/api/market-sync"), state.iterations ? null : getJSON("/api/market-sync/iterations")]);
      state.live = live; state.error = null;
      if (iterations) state.iterations = iterations.items;
    } catch (error) { state.error = error.message; }
    finally { state.pending = false; }
    render();
    loadHistory();
  }

  window.SpikeMarketSync = {refresh, setActive(value) { state.active = value; if (value) refresh(); }};
})();
