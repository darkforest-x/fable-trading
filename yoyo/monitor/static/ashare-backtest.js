/* Frozen A-share research summaries. No scanner or execution API is called. */
(function (root) {
  "use strict";
  const prefix = "/static/ashare-backtest/";
  const escape = value => String(value ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
  const number = (value, digits = 2) => typeof value === "number" && Number.isFinite(value) ? value.toLocaleString("zh-CN", { maximumFractionDigits: digits, minimumFractionDigits: digits }) : "—";
  const percent = value => typeof value === "number" && Number.isFinite(value) ? `${number(value * 100)}%` : "—";
  const safeLink = path => typeof path === "string" && /^\/static\/ashare-backtest\/[a-zA-Z0-9_.-]+$/.test(path) ? path : null;
  const filter = (rows, version, timeframe) => rows.filter(row => (version === "all" || row.version === version) && (timeframe === "all" || row.timeframe === timeframe));
  function table(rows, annual = false) {
    if (!rows.length) return `<tr><td colspan="${annual ? 6 : 12}" class="ashare-empty">尚无已完成的统计</td></tr>`;
    return rows.map(r => `<tr>${annual ? `<td>${escape(r.year)}</td>` : ""}<th scope="row">${escape(String(r.version).toUpperCase())} · ${r.timeframe === "1W" ? "周线" : "日线"}${r.status && r.status !== "complete" ? `<small> · ${escape(({partial:"部分覆盖",pending:"待完成",error:"异常",incomplete:"缺数"})[r.status] || r.status)}</small>` : ""}</th>${annual ? "" : `<td>${number(r.ready_symbols, 0)}</td><td>${number(r.signals, 0)}</td>`}<td>${number(r.closed_trades,0)}</td>${annual ? "" : `<td>${number(r.open_trades,0)}</td>`}<td>${percent(r.win_rate)}</td>${annual ? "" : `<td>${number(r.profit_factor)}</td>`}<td>${percent(r.mean_net_return)}</td><td>${number(r.sum_net_r)}</td>${annual ? "" : `<td>${percent(r.random_mean_net_return)}</td><td>${percent(r.excess_mean_net_return)}</td><td>${number(r.control_p_value,4)}</td>`}</tr>`).join("");
  }
  let data = null, loading = false;
  const $ = id => root.document.getElementById(id);
  function coverageText(value) {
    const stopped = value.owner_stopped || value.collection?.owner_stopped;
    const omitted = value.uncollected_symbols ?? value.collection?.uncollected;
    const failed = value.collection_failed_symbols ?? value.collection?.errors;
    const isolated = value.evaluation_isolated_symbols ?? (typeof failed === "number" ? value.failed_symbols - failed : null);
    return `目标 ${number(value.universe_count,0)} 只 · 已覆盖 ${number(value.covered_symbols,0)} 只 · ${stopped ? `采集失败 ${number(failed,0)} 只 · 评估隔离 ${number(isolated,0)} 只 · 未采集 ${number(omitted,0)} 只（不再补抓）` : `异常/待重试 ${number(value.failed_symbols,0)} 只`}`;
  }
  function render() {
    if (!data) return;
    const version = $("ashare-version").value, timeframe = $("ashare-timeframe").value;
    const stopped = data.owner_stopped || data.collection?.owner_stopped;
    const status = stopped ? ((data.status === "complete" || data.status === "incomplete") ? "已有数据回测完成 · 部分主板覆盖" : "已停止取数 · 已有数据回测中") : (({ collecting:"历史数据采集中", running:"固定配置回测中", complete:"回测完成", incomplete:"结果覆盖不完整", error:"数据读取失败" })[data.status] || "等待统计");
    $("ashare-status").textContent = status;
    $("ashare-period").textContent = `${data.start || "—"} 至 ${data.end || "—"}`;
    $("ashare-coverage").textContent = coverageText(data);
    $("ashare-source").textContent = `${data.source || "BaoStock"} · 更新 ${data.generated_at ? new Date(data.generated_at).toLocaleString("zh-CN",{timeZone:"Asia/Shanghai"}) : "—"}`;
    $("ashare-rows").innerHTML = table(filter(data.groups, version, timeframe));
    $("ashare-annual").innerHTML = table(filter(data.annual, version, timeframe), true);
    $("ashare-notes").innerHTML = (data.warnings || []).map(note => stopped && note.startsWith("全量仍在运行") ? "已按用户要求停止取数，仅完成已有数据；当前覆盖未代表全主板。" : note).map(note => `<li>${escape(note)}</li>`).join("");
    $("ashare-downloads").innerHTML = [["完整报告",data.report_url],["逐笔交易 CSV",data.trades_url],["股票覆盖 CSV",data.coverage_url],["全池采集清单",data.source_coverage_url]]
      .filter(([,url]) => safeLink(url)).map(([label,url]) => `<a class="load-more" href="${escape(url)}" target="_blank" rel="noopener">${label} ↗</a>`).join("");
  }
  async function load() {
    if (loading) return;
    loading = true;
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 12000);
    try {
      const response = await root.fetch(prefix + "summary.json", { cache:"no-store", signal:controller.signal });
      if (!response.ok) throw new Error(response.status === 404 ? "回测尚未生成结果，采集完成后自动更新。" : "回测统计暂不可读取。");
      const value = await response.json();
      if (value.schema_version !== 1 || !Array.isArray(value.groups) || !Array.isArray(value.annual)) throw new Error("回测统计格式异常。");
      data = value;
      $("ashare-error").textContent = "";
      render();
    } catch (error) {
      $("ashare-error").textContent = error.name === "AbortError" ? "回测统计读取超时，下次刷新会重试。" : error.message;
      $("ashare-status").textContent = data ? "上次快照 · 刷新失败" : "等待可核验结果";
    } finally { clearTimeout(timer); loading = false; }
  }
  const api = {load,render,number,percent,safeLink,filter,table,coverageText};
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else {
    root.SpikeAshare = api;
    root.document.addEventListener("DOMContentLoaded", () => {
      ["ashare-version","ashare-timeframe"].forEach(id => $(id).addEventListener("change",render));
    });
  }
})(typeof window === "undefined" ? globalThis : window);
