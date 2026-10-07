/* V13 closed-bar observations; the existing notification center owns routing. */
(() => {
  "use strict";
  let active = false, pending = false, filter = "", latest = null;
  const root = () => document.getElementById("v130-workspace");
  const esc = (value) => String(value ?? "—").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
  const clock = (value) => value == null ? "—" : new Intl.DateTimeFormat("zh-CN", {timeZone:"Asia/Shanghai",month:"2-digit",day:"2-digit",hour:"2-digit",minute:"2-digit",hour12:false}).format(new Date(value));
  const number = (value) => typeof value === "number" && Number.isFinite(value) ? value.toLocaleString("zh-CN", {maximumSignificantDigits:8}) : "—";
  const receipts = {sent:"服务已接受",pending:"待发送",sending:"发送中",skipped:"已跳过",failed:"发送失败",unknown:"结果未知",history:"未推送"};
  function render() {
    if (!root() || !latest) return;
    const rows = latest.items.filter(e => !filter || e.symbol.toUpperCase().includes(filter.toUpperCase()));
    const since = latest.activation?.activated_ms;
    root().innerHTML = `<div class="v130-overview"><article><span>监控范围</span><strong>15m · 普通多空</strong></article><article><span>最近24小时确认</span><strong>${esc(latest.signals_24h)}</strong></article><article><span>启用时间 · 北京</span><strong>${esc(clock(since))}</strong></article></div>
      <div class="notice">确认收盘通知，次开建立模型参考；不是账户成交。原止损保留，最多等待24根。后台行情计算尚未逐笔核验 TradingView 图上信号。<a href="#notifications">查看通知订阅与回执</a></div>
      <div class="section-toolbar"><h2>回踩再突破确认</h2><label class="search-field"><input id="v130-search" type="search" placeholder="搜索合约" aria-label="搜索 V13 合约" value="${esc(filter)}"></label></div>
      ${rows.length ? `<div class="v130-table-wrap"><table class="v130-table"><thead><tr><th>合约 / 方向</th><th>确认 · 北京</th><th>原候选 / 等待</th><th>确认价 / 原止损</th><th>模型参考</th><th>Telegram / Bark</th><th>图表</th></tr></thead><tbody>${rows.map(e => {
        const p = e.performance || {};
        const label = {active:"运行中",profit:"已结束盈利",loss:"已结束亏损",breakeven:"已结束持平",unknown:"待次开核验"}[p.status] || "暂无参考";
        const symbol = e.symbol.replace(/-SWAP$/, "").replace(/-/g, "") + ".P";
        const url = `https://www.tradingview.com/chart/?symbol=${encodeURIComponent("OKX:"+symbol)}&interval=15`;
        return `<tr><td><strong>${esc(e.symbol)}</strong><small>${e.side === "long" ? "多头 ↑" : "空头 ↓"}${e.is_fresh ? " · 新鲜" : ""}</small></td><td>${esc(clock(e.bar_close_ms))}</td><td>${esc(clock(e.anchor_close_ms))}<small>${esc(e.wait_bars)} 根</small></td><td>${esc(number(e.price))}<small>止损 ${esc(number(e.initial_stop))}</small></td><td>${esc(label)}<small>${p.current_r == null ? "尚无净R" : esc(p.current_r.toFixed(2))+"R（扣20bp）"}</small></td><td>${esc(receipts[e.notification_status] || e.notification_status)}<small>${esc(receipts[e.bark_notification_status] || e.bark_notification_status)}</small></td><td><a href="${esc(url)}" target="_blank" rel="noopener noreferrer">打开15m ↗</a></td></tr>`;
      }).join("")}</tbody></table></div>` : `<div class="empty-state"><h3>${since == null ? "正在初始化监控" : "暂无符合条件的新确认"}</h3><p>${filter ? "当前搜索没有匹配记录。" : "启用前的历史不补发。V13 需要依次完成突破、回踩守住、再次突破。"}</p></div>`}
      <p class="quiet-text">最近扫描：${esc(clock(latest.last_observation?.last_observed_ms))} · ${esc(latest.last_observation?.symbol || "等待首轮扫描")}。显示最近100条记录。</p>`;
    document.getElementById("v130-search")?.addEventListener("change", e => {filter=e.target.value.trim(); render();});
  }
  async function refresh() {
    if (!active || pending || document.hidden) return;
    pending = true;
    try {
      const response = await fetch("/api/v13/signals", {cache:"no-store"});
      if (!response.ok) throw new Error(`读取失败 ${response.status}`);
      latest = await response.json(); render();
    } catch (error) {
      if (root()) root().innerHTML = `<div class="notice" role="alert">${esc(error.message)}；稍后重试。</div>`;
    } finally { pending = false; }
  }
  window.SpikeV130 = {refresh, setActive(value) {active = value; if (active) refresh();}};
})();
