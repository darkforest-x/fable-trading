/* Right-click the existing Submit/Update button in the owner's LS project 77.
 * Source: installed LS .lsf-editor / BottomBar Controls aria-label="submit".
 * The native button retains validation, comments, permissions and save behavior.
 * No annotation API calls, synthetic keyboard shortcuts or automatic retries.
 */
(function (factory) {
  if (typeof module === "object" && module.exports) module.exports = factory;
  if (typeof window !== "undefined") factory(window);
})(function installRightClickSubmit(win) {
  "use strict";
  const key = "__fableRightClickSubmitV1";
  if (win[key]) return win[key];
  const doc = win.document;
  const origins = new Set([
    "http://192.168.1.4:8081", "http://127.0.0.1:8081", "http://localhost:8081",
  ]);
  const excluded = 'input,textarea,select,[contenteditable]:not([contenteditable="false"]),[role="textbox"],[role="menu"],[role="dialog"],[aria-modal="true"]';
  let lastActivation = -Infinity;
  let leftDown = false;
  let blockedGesture = false;

  function visible(el) {
    if (!el || !el.isConnected || el.closest('[hidden],[inert],[aria-hidden="true"]')) return false;
    const css = win.getComputedStyle(el);
    return css.display !== "none" && css.visibility !== "hidden" && el.getClientRects().length > 0;
  }

  function context(target) {
    const url = new URL(win.location.href);
    if (!origins.has(url.origin) || !/^\/projects\/77\/data\/?$/.test(url.pathname)) return null;
    if (url.searchParams.get("labeling") !== "1" && !/^[1-9]\d*$/.test(url.searchParams.get("task") || "")) return null;
    if (!target || typeof target.closest !== "function" || target.closest(excluded)) return null;
    if (Array.from(doc.querySelectorAll('[role="dialog"],[aria-modal="true"],[role="menu"],.ant-modal-wrap')).some(visible)) return null;
    const editors = Array.from(doc.querySelectorAll(".lsf-editor")).filter(visible);
    if (editors.length !== 1 || !editors[0].contains(target)) return null;
    const editor = editors[0];
    const taskNode = editor.querySelector(".lsf-current-task__task-id");
    const taskId = taskNode && Array.from(taskNode.childNodes)
      .filter(node => node.nodeType === 3).map(node => node.textContent).join("").trim();
    if (!/^[1-9]\d*$/.test(taskId || "")) return null;
    const buttons = Array.from(editor.querySelectorAll('button[aria-label="submit"]')).filter(visible);
    if (buttons.length !== 1) return null;
    return { button: buttons[0], taskId };
  }

  function onPointerDown(event) {
    if (event.button === 0) leftDown = true;
    if (event.button === 2) blockedGesture = leftDown || !!(event.buttons & 1);
  }
  function onPointerUp(event) { if (event.button === 0) leftDown = false; }
  function resetPointer() { leftDown = false; blockedGesture = false; }

  function onContextMenu(event) {
    if (event.button !== 2 || event.shiftKey || event.ctrlKey || event.altKey || event.metaKey) return;
    if (leftDown || blockedGesture || (event.buttons & 1)) return;
    const current = context(event.target);
    if (!current) return;
    // Suppress the browser/canvas menu even while saving, without clicking twice.
    event.preventDefault();
    event.stopImmediatePropagation();
    const button = current.button;
    if (button.disabled || button.matches(":disabled") || button.getAttribute("aria-disabled") === "true") return;
    const now = win.performance.now();
    if (now - lastActivation < 900) return; // Also protects a rapidly loaded next task.
    lastActivation = now;
    button.click();
  }

  // A title only: do not change React-owned children or claim the save succeeded.
  function annotateButton() {
    for (const editor of doc.querySelectorAll(".lsf-editor")) {
      const current = context(editor);
      if (current) current.button.setAttribute("title", "右键提交 / 更新；Shift+右键打开菜单");
    }
  }
  win.addEventListener("contextmenu", onContextMenu, true);
  win.addEventListener("pointerdown", onPointerDown, true);
  win.addEventListener("pointerup", onPointerUp, true);
  win.addEventListener("pointercancel", resetPointer, true);
  win.addEventListener("blur", resetPointer, true);
  const observer = new win.MutationObserver(annotateButton);
  observer.observe(doc.documentElement, { childList: true, subtree: true });
  annotateButton();
  const state = {
    version: "20260908-v1",
    dispose() {
      observer.disconnect();
      win.removeEventListener("contextmenu", onContextMenu, true);
      win.removeEventListener("pointerdown", onPointerDown, true);
      win.removeEventListener("pointerup", onPointerUp, true);
      win.removeEventListener("pointercancel", resetPointer, true);
      win.removeEventListener("blur", resetPointer, true);
      delete win[key];
    },
  };
  win[key] = state;
  return state;
});
