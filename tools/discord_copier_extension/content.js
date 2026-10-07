const DISCORD_EPOCH = 1420070400000n;
const STARTED_AT = Date.now();
const START_GRACE_MS = 15000;
const RECOVERY_DELAY_MS = 3500;
const RECOVERY_MAX_MESSAGES = 100;
const VISIBLE_RECENT_SCAN_MS = 5000;
const VISIBLE_RECENT_MAX_AGE_MS = 24 * 60 * 60 * 1000;
const VISIBLE_RECENT_MAX_MESSAGES = 40;
const seenIds = new Set();

let ready = false;
let observer = null;

init();

function init() {
  markExistingMessages();
  setTimeout(captureStartupBacklog, RECOVERY_DELAY_MS);
  setTimeout(() => {
    ready = true;
  }, 1200);
  setInterval(scanRecentVisibleMessages, VISIBLE_RECENT_SCAN_MS);

  observer = new MutationObserver((mutations) => {
    for (const mutation of mutations) {
      for (const node of mutation.addedNodes) {
        scanNode(node);
      }
    }
  });

  observer.observe(document.documentElement, {
    childList: true,
    subtree: true,
  });

  chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
    if (message?.type === "DISCORD_OKX_CAPTURE_LATEST_SIGNAL") {
      captureLatestVisibleSignal()
        .then((result) => sendResponse(result))
        .catch((error) => sendResponse({ ok: false, error: String(error?.message || error) }));
      return true;
    }

    if (message?.type === "DISCORD_OKX_CAPTURE_VISIBLE_SIGNALS") {
      captureVisibleSignals()
        .then((result) => sendResponse(result))
        .catch((error) => sendResponse({ ok: false, error: String(error?.message || error) }));
      return true;
    }

    if (message?.type === "DISCORD_OKX_PAGE_STATE") {
      sendResponse({ ok: true, state: getPageState() });
      return true;
    }

    return false;
  });
}

async function captureStartupBacklog() {
  const payloads = findMessageElements(document)
    .map(extractMessage)
    .filter((payload) => payload?.content)
    .slice(-RECOVERY_MAX_MESSAGES);

  for (const payload of payloads) {
    seenIds.add(payload.external_id);
    await chrome.runtime.sendMessage({
      type: "DISCORD_OKX_MESSAGE",
      payload: { ...payload, recovery_capture: true },
    });
  }
}

function markExistingMessages() {
  for (const element of findMessageElements(document)) {
    const payload = extractMessage(element);
    if (payload?.external_id) {
      seenIds.add(payload.external_id);
    }
  }
}

function scanNode(node) {
  if (!(node instanceof Element)) return;
  const candidates = findMessageElements(node);
  if (isMessageElement(node)) {
    candidates.unshift(node);
  }

  for (const element of candidates) {
    const payload = extractMessage(element);
    if (!payload || seenIds.has(payload.external_id)) continue;
    seenIds.add(payload.external_id);

    if (!ready) continue;
    if (payload.created_at_ms && payload.created_at_ms < STARTED_AT - START_GRACE_MS) {
      continue;
    }

    chrome.runtime.sendMessage(
      { type: "DISCORD_OKX_MESSAGE", payload },
      () => void chrome.runtime.lastError
    );
  }
}

async function scanRecentVisibleMessages() {
  if (!ready) return;

  const payloads = findMessageElements(document)
    .map(extractMessage)
    .filter((payload) => payload?.content)
    .slice(-VISIBLE_RECENT_MAX_MESSAGES);

  for (const payload of payloads) {
    if (seenIds.has(payload.external_id)) continue;
    if (!isRecentVisiblePayload(payload)) continue;

    seenIds.add(payload.external_id);
    await chrome.runtime.sendMessage(
      { type: "DISCORD_OKX_MESSAGE", payload: { ...payload, visible_recent_scan: true } },
      () => void chrome.runtime.lastError
    );
  }
}

function isRecentVisiblePayload(payload) {
  if (!payload?.created_at_ms) return false;
  return payload.created_at_ms >= Date.now() - VISIBLE_RECENT_MAX_AGE_MS;
}

async function captureLatestVisibleSignal() {
  const payload = findLatestVisibleOpenSignalPayload();
  if (!payload) {
    return {
      ok: false,
      error: "当前可见区域没有找到可提交的信号",
      state: getPageState(),
    };
  }
  seenIds.add(payload.external_id);
  return await chrome.runtime.sendMessage({
    type: "DISCORD_OKX_MESSAGE",
    payload: { ...payload, manual_capture: true },
  });
}

async function captureVisibleSignals() {
  const payloads = findVisibleOpenSignalPayloads();
  if (!payloads.length) {
    return {
      ok: false,
      error: "当前可见区域没有找到可提交的开仓信号",
      state: getPageState(),
    };
  }

  const results = [];
  for (const payload of payloads) {
    seenIds.add(payload.external_id);
    const result = await chrome.runtime.sendMessage({
      type: "DISCORD_OKX_MESSAGE",
      payload: { ...payload, manual_capture: true },
    });
    results.push({ external_id: payload.external_id, result });
  }

  const ok_count = results.filter((item) => item.result?.ok).length;
  const duplicate_count = results.filter((item) => item.result?.data?.duplicate).length;
  return {
    ok: true,
    submitted: ok_count,
    duplicate: duplicate_count,
    total: payloads.length,
    results,
  };
}

function findLatestVisibleOpenSignalPayload() {
  const elements = findMessageElements(document);
  for (let i = elements.length - 1; i >= 0; i -= 1) {
    const payload = extractMessage(elements[i]);
    if (payload?.content && looksLikeOpenSignal(payload.content)) return payload;
  }
  return null;
}

function findVisibleOpenSignalPayloads() {
  const payloads = [];
  const localSeen = new Set();
  for (const element of findMessageElements(document)) {
    const payload = extractMessage(element);
    if (!payload?.content || localSeen.has(payload.external_id)) continue;
    localSeen.add(payload.external_id);
    if (looksLikeOpenSignal(payload.content)) {
      payloads.push(payload);
    }
  }
  return payloads;
}

function getPageState() {
  const latestSignal = findLatestVisibleOpenSignalPayload();
  const latestMessage = findLatestVisibleMessagePayload();
  return {
    channel_id: getChannelId() || "discord-web",
    channel_name: getChannelName(),
    guild_id: getGuildId(),
    direct_message: !getGuildId(),
    page_url: location.href,
    latest_signal: latestSignal ? summarizePayload(latestSignal) : null,
    latest_message: latestMessage ? summarizePayload(latestMessage) : null,
  };
}

function summarizePayload(payload) {
  return {
    external_id: payload.external_id,
    author: payload.author,
    content_preview: payload.content.slice(0, 180),
  };
}

function findLatestVisibleMessagePayload() {
  const elements = findMessageElements(document);
  for (let i = elements.length - 1; i >= 0; i -= 1) {
    const payload = extractMessage(elements[i]);
    if (payload?.content) return payload;
  }
  return null;
}

function looksLikeOpenSignal(content) {
  const text = cleanText(content).toLowerCase();
  if (isResultUpdate(text)) {
    return false;
  }

  const strongRules = [
    /飞扬合约策略/,
    /进场点位/,
    /止损点位/,
    /止盈点位/,
    /具体产品/,
    /进行方向/,
  ];
  const strongHits = strongRules.filter((rule) => rule.test(text)).length;
  if (strongHits >= 2) return true;

  const chartPrimeStandard =
    /\b(long|short)\s*:/i.test(text) &&
    /\bentry\s*:/i.test(text) &&
    /\b(exit|tp)\s*:/i.test(text) &&
    /\bsl\s*:/i.test(text);
  const chartPrimeIdea =
    /\bentry\s*(long|short|buy|sell)?\s*[:\-]?\s*[\d.]+\s*(?:-|to|–)\s*[\d.]+/i.test(text) &&
    /\bsl\b/i.test(text) &&
    /\btp\s*\d*/i.test(text);
  const hasCryptoSignal =
    /@(?:free\s+)?crypto signal/i.test(text) ||
    /#?[a-z0-9]{2,15}(?:usdt\.p)?\s*\([^)]+\)/i.test(text);
  if ((chartPrimeStandard || chartPrimeIdea) && hasCryptoSignal) return true;
  if (isWoodsShorthandOpenSignal(content)) return true;

  const hasSymbol =
    /\b[A-Z0-9]{2,15}(?:USDT\.P)?\b/.test(cleanText(content)) ||
    /\b(btc|eth|sol|bnb|zec|xrp|doge|ada|hype|link|ltc)\b/i.test(text);
  const hasSide = /(做多|做空|多单|空单|long|short)/i.test(text);
  const hasRisk = /(止损|止盈|sl|tp|stop)/i.test(text);
  const hasEntry = /(进场|入场|entry|区间|点位)/i.test(text);
  return hasSymbol && hasSide && hasRisk && hasEntry;
}

function isWoodsShorthandOpenSignal(content) {
  const price = String.raw`(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?`;
  const text = cleanText(content);
  return new RegExp(
    String.raw`\b#?[A-Z][A-Z0-9]{1,15}\b\s+` +
      String.raw`(?:limit\s+)?` +
      String.raw`(?:(?:long|short)\s+)?` +
      String.raw`(?:at\s+)?` +
      price +
      String.raw`(?:\s*[-–—]\s*` +
      price +
      String.raw`)?\s+(?:stop|sl)\s*:?\s*` +
      price +
      String.raw`\b`,
    "i"
  ).test(text);
}

function isResultUpdate(text) {
  return /target hit|targets hit|all targets hit|trade closed|止盈命中|止损命中|sl hit|stop\s*loss\s*(hit|triggered)/i.test(
    text
  );
}

function findMessageElements(root) {
  return Array.from(
    root.querySelectorAll(
      [
        '[id^="chat-messages-"]',
        '[data-list-item-id^="chat-messages"]',
        '[class*="messageListItem"]',
      ].join(",")
    )
  ).filter(isMessageElement);
}

function isMessageElement(element) {
  if (!(element instanceof Element)) return false;
  const key = getMessageId(element);
  return Boolean(key);
}

function extractMessage(element) {
  // Only server channels. Direct messages (/channels/@me/...) are never read or sent.
  const guildId = getGuildId();
  if (!guildId) return null;
  const messageId = getMessageId(element);
  if (!messageId) return null;

  const channelId = getChannelId();
  const attachments = getMessageAttachments(element);
  const content = getMessageContent(element) || (attachments.length ? "[图片消息]" : "");
  if (!content && !attachments.length) return null;

  return {
    external_id: `discord_web_${channelId || "unknown"}_${messageId}`,
    channel_id: channelId || "discord-web",
    channel_name: getChannelName(),
    guild_id: guildId,
    author: getAuthor(element),
    content,
    attachments,
    created_at_ms: snowflakeToMs(messageId),
    page_url: location.href,
  };
}

function getMessageId(element) {
  const raw =
    element.getAttribute("data-list-item-id") ||
    element.getAttribute("id") ||
    "";
  const match = raw.match(/(\d{15,25})(?!.*\d)/);
  return match?.[1] || "";
}

function getChannelId() {
  const parts = location.pathname.split("/").filter(Boolean);
  if (parts[0] === "channels" && parts[2]) {
    return parts[2];
  }
  return "";
}

function getGuildId() {
  const parts = location.pathname.split("/").filter(Boolean);
  if (parts[0] === "channels" && /^\d{15,25}$/.test(parts[1] || "")) {
    return parts[1];
  }
  return "";
}

function getChannelName() {
  const title = document.title.replace(/\s*\|\s*Discord\s*$/i, "").trim();
  if (title) return title;
  return getChannelId() || "Discord Web";
}

function getAuthor(element) {
  const selectors = [
    '[class*="username"]',
    '[class*="headerText"] span',
    "h3 span",
    "h2 span",
  ];
  for (const selector of selectors) {
    const value = cleanText(element.querySelector(selector)?.textContent || "");
    if (value) return value;
  }
  return "discord-web";
}

function getMessageContent(element) {
  const contentElements = Array.from(
    element.querySelectorAll(
      [
        '[id^="message-content-"]',
        '[class*="messageContent"]',
        '[class*="markup"]',
      ].join(",")
    )
  );

  const lines = contentElements
    .map((el) => cleanText(el.textContent || ""))
    .filter(Boolean);

  const uniqueLines = [...new Set(lines)];
  if (uniqueLines.length) {
    return uniqueLines.join("\n");
  }

  const imageAlts = Array.from(element.querySelectorAll("img[alt]"))
    .map((img) => cleanText(img.getAttribute("alt") || ""))
    .filter(Boolean);
  if (imageAlts.length) {
    return `[图片消息]\n${[...new Set(imageAlts)].join("\n")}`;
  }

  return "";
}

function getMessageAttachments(element) {
  const urls = new Map();
  const addImageUrl = (value, alt = "") => {
    const src = normalizeAttachmentUrl(value);
    if (!src) return;
    urls.set(src, {
      url: src,
      filename: attachmentFilename(src),
      content_type: "image",
      alt: cleanText(alt || ""),
    });
  };

  for (const img of element.querySelectorAll("img")) {
    const alt = img.getAttribute("alt") || "";
    addImageUrl(img.getAttribute("src") || "", alt);
    addImageUrl(img.currentSrc || "", alt);
    for (const src of srcsetUrls(img.getAttribute("srcset") || "")) {
      addImageUrl(src, alt);
    }
  }

  for (const source of element.querySelectorAll("source[srcset]")) {
    for (const src of srcsetUrls(source.getAttribute("srcset") || "")) {
      addImageUrl(src, source.getAttribute("alt") || "");
    }
  }

  for (const link of element.querySelectorAll("a[href]")) {
    addImageUrl(link.getAttribute("href") || "", link.textContent || "");
  }

  for (const node of element.querySelectorAll("[style]")) {
    const styleValue = `${node.getAttribute("style") || ""} ${node.style?.backgroundImage || ""}`;
    for (const url of cssUrls(styleValue)) {
      addImageUrl(url);
    }
  }

  return [...urls.values()];
}

function srcsetUrls(value) {
  if (!value) return [];
  return value
    .split(",")
    .map((part) => part.trim().split(/\s+/)[0])
    .filter(Boolean);
}

function cssUrls(value) {
  if (!value) return [];
  const urls = [];
  const pattern = /url\((['"]?)(.*?)\1\)/gi;
  let match;
  while ((match = pattern.exec(value))) {
    if (match[2]) urls.push(match[2]);
  }
  return urls;
}

function normalizeAttachmentUrl(value) {
  if (!value) return "";
  let parsed;
  try {
    parsed = new URL(value, location.href);
  } catch {
    return "";
  }
  if (parsed.protocol !== "https:") {
    return "";
  }

  const host = parsed.hostname.toLowerCase();
  const isDiscordMediaHost =
    /^(?:cdn|media)\.discordapp\.(?:com|net)$/.test(host) ||
    /^images-ext-\d+\.discordapp\.net$/.test(host);
  if (!isDiscordMediaHost) {
    return "";
  }

  const path = parsed.pathname;
  const isMessageMediaPath =
    path.includes("/attachments/") ||
    path.includes("/ephemeral-attachments/") ||
    path.includes("/external/");
  if (!isMessageMediaPath) {
    return "";
  }

  const format = (parsed.searchParams.get("format") || "").toLowerCase();
  const looksLikeImage =
    /\.(?:png|jpe?g|webp|gif)$/i.test(path) ||
    /^(?:png|jpe?g|webp|gif)$/.test(format);
  if (!looksLikeImage) {
    return "";
  }
  return parsed.href;
}

function attachmentFilename(url) {
  try {
    const parsed = new URL(url);
    const path = parsed.pathname;
    const name = decodeURIComponent(path.split("/").filter(Boolean).pop() || "image");
    if (/\.(?:png|jpe?g|webp|gif)$/i.test(name)) return name;
    const format = (parsed.searchParams.get("format") || "").toLowerCase();
    if (/^(?:png|jpe?g|webp|gif)$/.test(format)) return `${name}.${format}`;
    return name;
  } catch {
    return "image";
  }
}

function cleanText(value) {
  return value.replace(/\s+/g, " ").trim();
}

function snowflakeToMs(value) {
  try {
    return Number((BigInt(value) >> 22n) + DISCORD_EPOCH);
  } catch {
    return 0;
  }
}
