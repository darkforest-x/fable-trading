/* Channel health and explicit subscription controls for the local monitor. */
(() => {
  "use strict";

  const $ = (id) => document.getElementById(id);
  const CHANNELS = ["telegram"];
  const CHANNEL_NAMES = { telegram: "Telegram（TG）", bark: "Bark" };
  const TOPICS = ["spike_v130", "spike_v128", "yolo_confirmation", "joint"];
  const TOPIC_NAMES = {
    spike_v130: "SPIKE V13.0 回踩 · 15m",
    spike_v128: "SPIKE V12.8 普通启动",
    yolo_confirmation: "YOLO 补充确认",
    joint: "突破 + spike 联合信号",
    legacy: "旧版 SPIKE 历史",
    spike_v9: "旧版 SPIKE 历史",
    v9: "旧版 SPIKE 历史",
  };
  const TIMEFRAME_NAMES = { "15m": "15m", "30m": "30m", "1H": "1H", "4H": "4H", "1D": "日线", "1Dutc": "日线" };
  const state = {
    active: false, snapshot: null,
    snapshotError: "", saveError: "", snapshotLoading: false,
    saving: false, snapshotRevision: 0,
    activeGeneration: 0, timer: null,
  };

  const escapeHTML = (value) => String(value ?? "").replace(/[&<>"']/g,
    (character) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[character]));
  const number = (value) => Number.isFinite(Number(value)) ? Number(value).toLocaleString("zh-CN") : "—";
  const channelName = (id) => CHANNEL_NAMES[id] || "其他渠道";
  const topicName = (topic) => TOPIC_NAMES[topic] || "其他通知来源";
  const displayTopicLabel = (label, topic) => !label || Object.prototype.hasOwnProperty.call(TOPIC_NAMES, label)
    ? topicName(topic || label) : String(label);
  const timeframeName = (timeframe) => TIMEFRAME_NAMES[timeframe] || "其他周期";
  const sideName = (side) => side === "long" ? "多头" : side === "short" ? "空头" : "未标注方向";
  const dateTime = (value) => {
    const timestamp = typeof value === "string" ? Date.parse(value) : Number(value);
    if (!Number.isFinite(timestamp) || timestamp <= 0) return "—";
    return new Intl.DateTimeFormat("zh-CN", {
      timeZone: "Asia/Shanghai", year: "numeric", month: "2-digit", day: "2-digit",
      hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false,
    }).format(timestamp);
  };
  const validSnapshot = (value) => value && typeof value === "object" &&
    Array.isArray(value.channels) && Array.isArray(value.routes);

  function renderNotice() {
    const notice = $("notifications-notice");
    if (!notice) return;
    const messages = [];
    if (state.snapshotError) messages.push(`通知配置读取失败：${state.snapshotError}`);
    if (state.saveError) messages.push(state.saveError);
    if (!state.snapshot && !state.snapshotError) messages.push("正在读取通知通道和路由状态…");
    notice.textContent = messages.join("；") || (state.snapshot?.notice ? String(state.snapshot.notice) :
      "订阅调整只影响后续事件，不补发旧记录；发送结果未知时不自动重试。服务已接受不代表手机已收到或已读。");
    notice.classList.toggle("hidden", !messages.length && !state.snapshot?.notice);
    notice.classList.toggle("error", Boolean(state.snapshotError || state.eventsError || state.saveError));
  }

  function channelState(channel) {
    if (channel.configured !== true) return ["未配置", "unconfigured"];
    if (channel.enabled === true) return ["已启用", "ready"];
    if (channel.enabled === false) return ["已关闭", "disabled"];
    return ["状态待同步", "unknown"];
  }

  function renderChannels() {
    const root = $("notification-channel-cards");
    if (!root) return;
    const channels = state.snapshot?.channels || [];
    root.innerHTML = CHANNELS.map((id) => {
      const channel = channels.find((item) => item?.id === id);
      if (!channel) return `<article class="notification-channel-card"><div class="notification-card-heading"><h3>${escapeHTML(channelName(id))}</h3><span class="notification-state unknown">状态待同步</span></div><p class="notification-card-note">服务尚未提供此渠道状态。</p></article>`;
      const counts = channel.counts || {};
      const [label, statusClass] = channelState(channel);
      return `<article class="notification-channel-card">
        <div class="notification-card-heading"><h3>${escapeHTML(channelName(id))}</h3><span class="notification-state ${statusClass}">${label}</span></div>
        <dl class="notification-channel-counts">
          <div><dt>累计服务已接受</dt><dd>${number(counts.sent)}</dd></div>
          <div><dt>待发送</dt><dd>${number(counts.pending)}</dd></div>
          <div><dt>发送中</dt><dd>${number(counts.sending)}</dd></div>
          <div><dt>失败</dt><dd>${number(counts.failed)}</dd></div>
          <div><dt>结果未知</dt><dd>${number(counts.unknown)}</dd></div>
          <div><dt>已跳过</dt><dd>${number(counts.skipped)}</dd></div>
        </dl>
        <p class="notification-card-note">最近一次服务接受：${dateTime(channel.last_success_ms)}。不代表设备已收到或已读。</p>
      </article>`;
    }).join("");
  }

  function routeState(route, channel) {
    if (!route) return `<span class="notification-route-missing">服务尚未返回此订阅</span>`;
    const enabled = route.enabled === true;
    const ready = route.armed === true;
    const readiness = ready ? "已就绪" : "尚未就绪";
    const routeLabel = `${topicName(route.topic)}：${channelName(channel)} 订阅开关`;
    const details = [
      Array.isArray(route.timeframes) && route.timeframes.length ? `周期 ${route.timeframes.map(timeframeName).join(" / ")}` : "周期待同步",
      Array.isArray(route.sides) && route.sides.length ? `方向 ${route.sides.map(sideName).join(" / ")}` : "方向待同步",
      route.activated_ms != null ? `接收此时间之后的信号：${dateTime(route.activated_ms)}` : "尚无接收起始时间",
    ];
      return `<div class="notification-route-control">
      <button type="button" class="notification-switch${enabled ? " on" : ""}" role="switch"
        aria-checked="${enabled}" aria-label="${escapeHTML(routeLabel)}" data-route-topic="${escapeHTML(route.topic)}"
        data-route-channel="${escapeHTML(channel)}"${state.saving ? " disabled" : ""}>
        <span aria-hidden="true"></span><strong>${enabled ? "已订阅" : "未订阅"}</strong>
      </button>
      <span class="notification-route-readiness${ready ? " ready" : ""}">${readiness}</span>
      <small>${details.map(escapeHTML).join(" · ")}</small>
    </div>`;
  }

  function renderRoutes() {
    const root = $("notification-route-matrix");
    if (!root) return;
    const routes = state.snapshot?.routes || [];
    if (!state.snapshot) {
      root.innerHTML = `<p class="notification-empty">${state.snapshotLoading ? "正在读取路由…" : "路由状态暂不可用。"}</p>`;
      return;
    }
    const known = TOPICS.map((topic) => routes.find((route) => route?.topic === topic));
    root.innerHTML = `<div class="notification-matrix-wrap"><table class="notification-matrix">
      <thead><tr><th scope="col">通知来源</th><th scope="col">Telegram（TG）</th></tr></thead>
      <tbody>${TOPICS.map((topic, index) => {
        const route = known[index];
        const label = displayTopicLabel(route?.label, topic);
        return `<tr><th scope="row"><strong>${escapeHTML(label)}</strong><small>${escapeHTML(topicName(topic))}</small></th>
          ${CHANNELS.map((channel) => `<td>${route?.channel === channel ? routeState(route, channel) : routeState(routes.find((item) => item?.topic === topic && item?.channel === channel), channel)}</td>`).join("")}
        </tr>`;
      }).join("")}</tbody></table></div>`;
  }

  function render() {
    renderNotice(); renderChannels(); renderRoutes();
    const updated = $("notifications-updated");
    if (updated) updated.textContent = state.snapshot?.as_of_ms ? `状态更新于 ${dateTime(state.snapshot.as_of_ms)}` : "状态更新时间待同步";
  }

  async function requestJSON(path, options) {
    const response = await fetch(path, { cache: "no-store", ...options,
      headers: { Accept: "application/json", ...(options?.headers || {}) } });
    if (!response.ok) throw new Error(`服务返回 HTTP ${response.status}`);
    const contentType = response.headers?.get?.("content-type") || "";
    if (contentType && !contentType.includes("json")) throw new Error("服务未返回有效 JSON 数据");
    const value = await response.json();
    if (!value || typeof value !== "object") throw new Error("服务返回的数据格式有误");
    return value;
  }

  async function loadSnapshot() {
    if (!state.active) return;
    const revision = ++state.snapshotRevision;
    const generation = state.activeGeneration;
    state.snapshotLoading = true;
    renderRoutes();
    try {
      const data = await requestJSON("/api/notifications");
      if (!state.active || generation !== state.activeGeneration || revision !== state.snapshotRevision || state.saving) return;
      if (!validSnapshot(data)) throw new Error("服务返回的通知配置格式有误");
      state.snapshot = data;
      state.snapshotError = "";
    } catch (error) {
      if (state.active && generation === state.activeGeneration && revision === state.snapshotRevision && !state.saving) {
        state.snapshotError = error.message || "请求失败";
      }
    } finally {
      if (state.active && generation === state.activeGeneration && revision === state.snapshotRevision && !state.saving) {
        state.snapshotLoading = false;
        render();
      }
    }
  }

  async function refresh() {
    if (!state.active) return;
    await loadSnapshot();
  }

  async function saveRoute(topic, channel, enabled) {
    if (state.saving || !state.active || !TOPICS.includes(topic) || !CHANNELS.includes(channel)) return;
    state.saving = true;
    state.saveError = "";
    state.snapshotRevision += 1;
    renderRoutes(); renderNotice();
    try {
      const snapshot = await requestJSON(`/api/notifications/routes/${encodeURIComponent(topic)}/${encodeURIComponent(channel)}`, {
        method: "PUT", headers: { "Content-Type": "application/json", "x-spike-action": "update-notification-route" },
        body: JSON.stringify({ enabled: enabled === true }),
      });
      if (!validSnapshot(snapshot)) throw new Error("服务未返回更新后的通知路由");
      state.snapshotRevision += 1;
      state.snapshot = snapshot;
      state.snapshotError = "";
    } catch (error) {
      state.snapshotRevision += 1;
      state.saveError = `保存订阅失败，正在重新读取服务状态：${error.message || "请求失败"}`;
    } finally {
      state.saving = false;
      state.snapshotLoading = false;
      render();
    }
    if (state.saveError) await loadSnapshot();
  }

  const routeRoot = $("notification-route-matrix");
  if (routeRoot) routeRoot.addEventListener("click", (event) => {
    const button = event.target.closest?.("[data-route-topic][data-route-channel]");
    if (!button || button.disabled) return;
    void saveRoute(button.dataset.routeTopic, button.dataset.routeChannel, button.getAttribute("aria-checked") !== "true");
  });

  function setActive(active) {
    const next = active === true;
    if (state.active === next) return;
    state.active = next;
    state.activeGeneration += 1;
    state.snapshotRevision += 1;
    if (state.timer !== null) clearInterval(state.timer);
    state.timer = null;
    if (next) {
      render();
      void refresh();
      state.timer = setInterval(() => { if (state.active && !document.hidden) void refresh(); }, 15000);
    }
  }

  window.SpikeNotifications = { setActive, refresh };
  render();
})();
