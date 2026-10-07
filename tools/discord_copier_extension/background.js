const DEFAULTS = {
  enabled: true,
  endpoint: "http://127.0.0.1:8080/api/messages/ingest",
  submittedCount: 0,
  lastStatus: "idle",
  lastError: "",
  lastMessageAt: "",
};

const sentIds = new Set();

chrome.runtime.onInstalled.addListener(async () => {
  const current = await chrome.storage.local.get(Object.keys(DEFAULTS));
  await chrome.storage.local.set({ ...DEFAULTS, ...current });
});

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (message?.type === "DISCORD_OKX_MESSAGE") {
    handleDiscordMessage(message.payload)
      .then((result) => sendResponse(result))
      .catch((error) => sendResponse({ ok: false, error: String(error) }));
    return true;
  }

  if (message?.type === "DISCORD_OKX_STATUS") {
    chrome.storage.local
      .get(Object.keys(DEFAULTS))
      .then((settings) => sendResponse({ ok: true, settings }))
      .catch((error) => sendResponse({ ok: false, error: String(error) }));
    return true;
  }

  return false;
});

async function handleDiscordMessage(payload) {
  const settings = await chrome.storage.local.get(Object.keys(DEFAULTS));
  const merged = { ...DEFAULTS, ...settings };

  if (!merged.enabled) {
    return { ok: false, skipped: true, reason: "disabled" };
  }

  const externalId = String(payload?.external_id || "");
  const content = String(payload?.content || "").trim();
  if (!externalId || !content) {
    return { ok: false, skipped: true, reason: "empty" };
  }

  if (sentIds.has(externalId)) {
    return { ok: false, skipped: true, reason: "duplicate" };
  }
  sentIds.add(externalId);

  const body = {
    content,
    author: payload.author || "discord-web",
    channel_id: payload.channel_id || "discord-web",
    channel_name: payload.channel_name || "Discord Web",
    guild_id: payload.guild_id || "",
    external_id: externalId,
    attachments: Array.isArray(payload.attachments) ? payload.attachments : [],
    source: payload.recovery_capture
      ? "discord-web_recovery"
      : payload.visible_recent_scan
        ? "discord-web-visible_recovery"
      : payload.manual_capture
        ? "discord-web-manual"
        : "discord-web",
  };

  try {
    const response = await fetch(merged.endpoint, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw new Error(data.detail || response.statusText);
    }

    const duplicate = Boolean(data.duplicate);
    const submittedCount = duplicate
      ? Number(merged.submittedCount || 0)
      : Number(merged.submittedCount || 0) + 1;
    await chrome.storage.local.set({
      submittedCount,
      lastStatus: duplicate ? "duplicate skipped" : `submitted #${data.message_id || "?"}`,
      lastError: "",
      lastMessageAt: new Date().toLocaleString(),
    });
    return { ok: true, data };
  } catch (error) {
    sentIds.delete(externalId);
    await chrome.storage.local.set({
      lastStatus: "error",
      lastError: String(error?.message || error),
      lastMessageAt: new Date().toLocaleString(),
    });
    return { ok: false, error: String(error?.message || error) };
  }
}
