const DEFAULT_ENDPOINT = "http://127.0.0.1:8080/api/messages/ingest";

const enabledEl = document.getElementById("enabled");
const endpointEl = document.getElementById("endpoint");
const saveEl = document.getElementById("save");
const captureLatestEl = document.getElementById("captureLatest");
const captureVisibleEl = document.getElementById("captureVisible");
const stateEl = document.getElementById("state");
const submittedCountEl = document.getElementById("submittedCount");
const lastStatusEl = document.getElementById("lastStatus");
const lastErrorEl = document.getElementById("lastError");
const channelNameEl = document.getElementById("channelName");
const latestSignalEl = document.getElementById("latestSignal");

load();

saveEl.addEventListener("click", async () => {
  await chrome.storage.local.set({
    enabled: enabledEl.checked,
    endpoint: endpointEl.value.trim() || DEFAULT_ENDPOINT,
  });
  await load();
});

captureLatestEl.addEventListener("click", async () => {
  lastErrorEl.textContent = "";
  lastStatusEl.textContent = "正在提交当前可见最新信号…";
  try {
    const tab = await getActiveDiscordTab();
    const result = await chrome.tabs.sendMessage(tab.id, {
      type: "DISCORD_OKX_CAPTURE_LATEST_SIGNAL",
    });
    if (!result?.ok) {
      throw new Error(result?.error || result?.reason || "提交失败");
    }
    await load();
  } catch (error) {
    lastStatusEl.textContent = "";
    lastErrorEl.textContent = String(error?.message || error);
  }
});

captureVisibleEl.addEventListener("click", async () => {
  lastErrorEl.textContent = "";
  lastStatusEl.textContent = "正在提交当前可见全部开仓信号…";
  try {
    const tab = await getActiveDiscordTab();
    const result = await chrome.tabs.sendMessage(tab.id, {
      type: "DISCORD_OKX_CAPTURE_VISIBLE_SIGNALS",
    });
    if (!result?.ok) {
      throw new Error(result?.error || result?.reason || "提交失败");
    }
    lastStatusEl.textContent = `本屏发现 ${result.total || 0} 条，提交 ${result.submitted || 0} 条`;
    await load();
  } catch (error) {
    lastStatusEl.textContent = "";
    lastErrorEl.textContent = String(error?.message || error);
  }
});

async function load() {
  const settings = await chrome.storage.local.get([
    "enabled",
    "endpoint",
    "submittedCount",
    "lastStatus",
    "lastError",
    "lastMessageAt",
  ]);
  enabledEl.checked = settings.enabled !== false;
  endpointEl.value = settings.endpoint || DEFAULT_ENDPOINT;
  submittedCountEl.textContent = String(settings.submittedCount || 0);
  lastStatusEl.textContent = settings.lastStatus
    ? `${settings.lastStatus}${settings.lastMessageAt ? ` · ${settings.lastMessageAt}` : ""}`
    : "";
  lastErrorEl.textContent = settings.lastError || "";
  stateEl.textContent = enabledEl.checked ? "已开启" : "已暂停";
  await loadPageState();
}

async function loadPageState() {
  channelNameEl.textContent = "—";
  latestSignalEl.textContent = "—";
  try {
    const tab = await getActiveDiscordTab();
    const result = await chrome.tabs.sendMessage(tab.id, {
      type: "DISCORD_OKX_PAGE_STATE",
    });
    if (!result?.ok) throw new Error(result?.error || "读取页面失败");
    const state = result.state || {};
    channelNameEl.textContent = state.channel_name || state.channel_id || "Discord";
    latestSignalEl.textContent =
      state.latest_signal?.content_preview ||
      (state.latest_message ? "可见最新消息不是交易信号" : "当前可见区域暂无消息");
  } catch (error) {
    latestSignalEl.textContent = String(error?.message || error);
  }
}

async function getActiveDiscordTab() {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (!tab?.id || !tab.url?.startsWith("https://discord.com/channels/")) {
    throw new Error("请先切到 Discord 频道页");
  }
  return tab;
}
