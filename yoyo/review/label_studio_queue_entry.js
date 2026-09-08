/* Keep project 77 / view 44's list entry inside its displayed native queue.
 * LS 1.13.1 LabelButton.jsx binds the main button to onLabelAll, while its
 * sibling AsDisplayed button preserves filters. Forward only a user's click
 * to that native button. No direct storage/history/API writes; the native
 * handler retains its normal stream-mode, navigation and validation behavior.
 */
(function (factory) {
  if (typeof module === "object" && module.exports) module.exports = factory;
  if (typeof window !== "undefined") factory(window);
})(function installQueueEntry(win) {
  "use strict";
  const key = "__fableQueueEntryV1";
  if (win[key]) return win[key];
  const doc = win.document;
  const origins = new Set([
    "http://192.168.1.4:8081", "http://127.0.0.1:8081", "http://localhost:8081",
  ]);
  const modalSelector = '[role="dialog"],[aria-modal="true"],[role="menu"],.ant-modal-wrap';
  const unavailableSelector = '[hidden],[inert],[aria-hidden="true"]';
  const originals = new Map();
  const help = "点击审核本周1043题；项目3556包含历史资料。本按钮按当前列表筛选继续审核。";
  const blocked = "当前审核入口不可用：请关闭弹窗或刷新页面；未启动全部任务。";
  let forwarded = 0;

  function listScope() {
    const url = new URL(win.location.href);
    return origins.has(url.origin) && /^\/projects\/77\/data\/?$/.test(url.pathname)
      && url.searchParams.get("tab") === "44"
      && !["task", "labeling", "annotation"].some(name => url.searchParams.has(name));
  }

  function visible(el) {
    if (!el || !el.isConnected || el.closest(unavailableSelector)) return false;
    const style = win.getComputedStyle(el);
    return style.display !== "none" && style.visibility !== "hidden" && el.getClientRects().length > 0;
  }

  function text(el) { return el.textContent.replace(/\s+/g, " ").trim(); }
  function primary(el) { return /^Label (?:All|\d+) Tasks?$/.test(text(el)); }
  function disabled(el) {
    return el.disabled || el.matches(":disabled") || el.getAttribute("aria-disabled") === "true"
      || !!el.closest(unavailableSelector);
  }
  function setTitle(el, value) {
    if (!originals.has(el)) originals.set(el, el.getAttribute("title"));
    el.setAttribute("title", value);
  }
  function restoreTitles() {
    for (const [el, title] of originals) {
      if ([help, blocked].includes(el.getAttribute("title"))) {
        if (title === null) el.removeAttribute("title");
        else el.setAttribute("title", title);
      }
    }
    originals.clear();
  }
  function annotate() {
    if (!listScope()) { restoreTitles(); return; }
    for (const el of doc.querySelectorAll(".dm-tab-panel button")) {
      if (primary(el) && visible(el)) setTitle(el, help);
    }
  }

  function onClick(event) {
    if (event.button !== 0 || event.ctrlKey || event.metaKey || event.altKey || event.shiftKey || !listScope()) return;
    const el = event.target && typeof event.target.closest === "function" && event.target.closest("button");
    if (!el || !primary(el) || !visible(el)) return;
    const toolbar = el.closest(".dm-tab-panel");
    if (!toolbar) return;
    // Once this protected main entry is recognized, never fall back to All.
    event.preventDefault();
    event.stopImmediatePropagation();
    const primaries = Array.from(toolbar.querySelectorAll("button")).filter(button => primary(button) && visible(button));
    const targets = Array.from(toolbar.querySelectorAll("button"))
      .filter(button => text(button) === "Label Tasks As Displayed");
    const target = targets[0];
    const group = el.parentElement && el.parentElement.parentElement;
    if (primaries.length !== 1 || targets.length !== 1 || !target.isConnected
        || target.parentElement !== group || disabled(el) || disabled(target)
        || Array.from(doc.querySelectorAll(modalSelector)).some(visible)) {
      setTitle(el, blocked);
      return;
    }
    // Native AsDisplayed is deliberately display:none while its menu is shut.
    // Its React handler sets the native stream mode and runs normal validation.
    target.click();
    forwarded += 1;
  }

  win.addEventListener("click", onClick, true);
  win.addEventListener("popstate", annotate);
  const observer = new win.MutationObserver(annotate);
  observer.observe(doc.documentElement, { childList: true, subtree: true });
  annotate();
  const state = {
    version: "20260908-v1",
    get forwardedActivations() { return forwarded; },
    dispose() {
      observer.disconnect();
      win.removeEventListener("click", onClick, true);
      win.removeEventListener("popstate", annotate);
      restoreTitles();
      delete win[key];
    },
  };
  win[key] = state;
  return state;
});
