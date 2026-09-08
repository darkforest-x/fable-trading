/* Apply before CSS paints. Appearance stays local and never touches monitoring. */
(() => {
  "use strict";
  const storageKey = "spike.theme";
  const valid = (value) => ["light", "dark", "system"].includes(value) ? value : "system";
  const system = window.matchMedia("(prefers-color-scheme: dark)");
  function readPreference() {
    try { return valid(window.localStorage.getItem(storageKey)); }
    catch { return "system"; } // Restricted storage must not prevent page startup.
  }
  let preference = readPreference();
  function apply() {
    const theme = preference === "system" ? (system.matches ? "dark" : "light") : preference;
    document.documentElement.setAttribute("data-theme", theme);
    document.querySelector('meta[name="theme-color"]')?.setAttribute("content", theme === "dark" ? "#0a1016" : "#f4f7f9");
    const picker = document.getElementById("theme-select");
    if (picker) picker.value = preference;
  }
  apply();
  system.addEventListener("change", () => { if (preference === "system") apply(); });
  window.addEventListener("storage", (event) => {
    if (event.key === storageKey || event.key === null) {
      preference = readPreference();
      apply();
    }
  });
  document.addEventListener("DOMContentLoaded", () => {
    apply();
    document.getElementById("theme-select")?.addEventListener("change", (event) => {
      preference = valid(event.target.value);
      try { window.localStorage.setItem(storageKey, preference); } catch { /* Still work for this tab. */ }
      apply();
    });
  }, { once: true });
})();
