/* Apply appearance before CSS paints; preference never changes research state.
 * Sources: MDN Window.matchMedia, Window.localStorage and DOMContentLoaded.
 * A same-origin script keeps the existing Content Security Policy intact.
 */
"use strict";

(() => {
  const key = "rotation.appearance";
  const choices = new Set(["system", "light", "dark"]);
  const system = window.matchMedia("(prefers-color-scheme: dark)");
  let preference = "system";
  try {
    const saved = window.localStorage.getItem(key);
    if (choices.has(saved)) preference = saved;
  } catch (_) { /* Restricted storage still allows an in-session choice. */ }

  function apply() {
    document.documentElement.dataset.theme = preference === "system"
      ? (system.matches ? "dark" : "light") : preference;
  }
  apply();
  system.addEventListener("change", () => {
    if (preference === "system") apply();
  });

  document.addEventListener("DOMContentLoaded", () => {
    const select = document.getElementById("theme-select");
    select.value = preference;
    select.addEventListener("change", () => {
      preference = choices.has(select.value) ? select.value : "system";
      apply();
      try { window.localStorage.setItem(key, preference); }
      catch (_) { /* Do not interrupt the dashboard when storage is unavailable. */ }
    });
  });
})();
