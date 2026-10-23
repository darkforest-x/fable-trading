/* Apply before CSS paints. Appearance stays local and never touches monitoring. */
(() => {
  "use strict";
  const storageKey = "spike.theme";
  const fixedDesign = document.documentElement.dataset?.design === "stitch-obsidian";
  const glassDesign = document.documentElement.dataset?.design === "apple-glass";
  const valid = (value) => ["light", "dark", "system"].includes(value) ? value : "system";
  const system = window.matchMedia("(prefers-color-scheme: dark)");
  function readPreference() {
    try { return valid(window.localStorage.getItem(storageKey) || "dark"); }
    catch { return "system"; } // Restricted storage must not prevent page startup.
  }
  let preference = readPreference();
  function apply() {
    const theme = glassDesign ? "light" : fixedDesign ? "dark" : preference === "system" ? (system.matches ? "dark" : "light") : preference;
    document.documentElement.setAttribute("data-theme", theme);
    document.querySelector('meta[name="theme-color"]')?.setAttribute("content", glassDesign ? "#f7f8f6" : theme === "dark" ? (fixedDesign ? "#0b0e14" : "#101114") : "#f5f5f7");
    const picker = document.getElementById("theme-select");
    if (picker) picker.value = glassDesign ? "light" : fixedDesign ? "dark" : preference;
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
      if (fixedDesign || glassDesign) return;
      preference = valid(event.target.value);
      try { window.localStorage.setItem(storageKey, preference); } catch { /* Still work for this tab. */ }
      apply();
    });
  }, { once: true });
})();
