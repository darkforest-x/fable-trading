// Apply the saved palette before styles load, without requiring inline CSP access.
(() => {
  const storageKey = 'spike-vision-theme';
  const root = document.documentElement;
  let currentTheme = 'light';

  try {
    if (localStorage.getItem(storageKey) === 'dark') currentTheme = 'dark';
  } catch {
    // Storage may be disabled; the switch still works for the current page.
  }

  function applyTheme(theme) {
    currentTheme = theme;
    root.dataset.theme = theme;
    document.querySelector('meta[name="theme-color"]').content =
      theme === 'dark' ? '#0a1016' : '#f1f5f7';
    const toggle = document.getElementById('theme-toggle');
    if (!toggle) return;
    const action = theme === 'light' ? '切换到深色模式' : '切换到浅色模式';
    toggle.setAttribute('aria-label', action);
    toggle.title = action;
    toggle.querySelector('.theme-icon').textContent = theme === 'light' ? '☀' : '☾';
    toggle.querySelector('.theme-label').textContent = theme === 'light' ? '浅色' : '深色';
  }

  applyTheme(currentTheme);
  document.addEventListener('DOMContentLoaded', () => {
    applyTheme(currentTheme);
    document.getElementById('theme-toggle').addEventListener('click', () => {
      applyTheme(currentTheme === 'light' ? 'dark' : 'light');
      try {
        localStorage.setItem(storageKey, currentTheme);
      } catch {
        // Never prevent switching when the browser rejects preference storage.
      }
    });
  }, { once: true });
})();
