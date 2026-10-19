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
  if (message?.type === "DISCORD_OKX_HEARTBEAT") {
    const p=message.payload;
    if(p?.guild_id!=="1004707886657699901" || p?.channel_id!=="1131521990814089276") return false;
    chrome.storage.local.get(["enabled"]).then(settings => settings.enabled === false ? {ok:false} :
      fetch("http://127.0.0.1:8080/api/messages/heartbeat",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(p)}))
      .then(r=>sendResponse({ok:r.ok})).catch(()=>sendResponse({ok:false}));
    return true;
  }
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
  if(payload?.guild_id!=="1004707886657699901" || payload?.channel_id!=="1131521990814089276")
    return {ok:true,skipped:true,reason:"outside_watchlist"};
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

  const versionKey = JSON.stringify([externalId,content,(payload.attachments||[]).map(a=>String(a.url||"").split("?")[0])]);
  if (sentIds.has(versionKey)) {
    return { ok: true, skipped: true, reason: "duplicate" };
  }
  sentIds.add(versionKey);

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
        ? "discord-web-visible"
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

    if (data.skipped || data.message_id === null) {
      sentIds.delete(versionKey);
      return {ok:false,skipped:true,reason:data.reason||"not_ingested"};
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
    sentIds.delete(versionKey);
    await chrome.storage.local.set({
      lastStatus: "error",
      lastError: String(error?.message || error),
      lastMessageAt: new Date().toLocaleString(),
    });
    return { ok: false, error: String(error?.message || error) };
  }
}
