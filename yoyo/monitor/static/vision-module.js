/* One shell, one origin. Shadow DOM scopes the existing review module's IDs
 * and styles while its API/worker and persisted observations remain unchanged.
 * https://developer.mozilla.org/en-US/docs/Web/API/Web_components/Using_shadow_DOM
 */
(() => {
  let loaded, active = false, shadow, host;
  const native = document;
  async function mount() {
    host = native.getElementById('vision-module');
    const response = await fetch('/vision-assets/index.html');
    if (!response.ok) throw new Error('VLM 工作流界面暂不可读');
    const page = new DOMParser().parseFromString(await response.text(), 'text/html');
    page.querySelectorAll('script, .skip-link, .brand, .sidebar-bottom').forEach((el) => el.remove());
    shadow = host.attachShadow({ mode: 'open' });
    const style = native.createElement('style');
    style.textContent = `:host { display:block; min-width:0; }
      .app-frame { display:block; }
      .app-sidebar { display:block; position:static; height:auto; padding:0 0 16px; border:0; background:transparent; }
      .primary-nav { flex-direction:row; flex-wrap:wrap; margin:0; gap:6px; }
      .nav-tab { font-size:14px; min-height:40px; }
      .topbar { position:static; height:auto; padding:0 0 14px; background:transparent; border:0; }
      .page-identity { display:none; }
      .main-content { display:block; padding:0; max-width:none; }
      @media(max-width:700px) { .app-sidebar { display:block; width:auto; } .primary-nav { overflow:auto; } }
    `;
    const sheets = ['/vision-assets/styles.css', '/vision-assets/replay.css'].map((href) => {
      const link = native.createElement('link'); link.rel = 'stylesheet'; link.href = href; shadow.append(link);
      return new Promise((resolve, reject) => { link.onload = resolve; link.onerror = reject; });
    });
    const body = native.createElement('div');
    body.className = 'vision-body';
    body.append(...Array.from(page.body.childNodes));
    shadow.append(style, body);
    const syncTheme = () => host.setAttribute('data-theme', native.documentElement.dataset.theme || 'light');
    syncTheme();
    new MutationObserver(syncTheme).observe(native.documentElement, { attributes: true, attributeFilter: ['data-theme'] });
    const scoped = {
      getElementById: (id) => shadow.getElementById(id),
      querySelector: (selector) => shadow.querySelector(selector),
      querySelectorAll: (selector) => shadow.querySelectorAll(selector),
      createElement: (...args) => native.createElement(...args),
      createTextNode: (...args) => native.createTextNode(...args),
      addEventListener: (...args) => shadow.addEventListener(...args),
      get documentElement() { return host; }, get body() { return body; },
      get hidden() { return native.hidden || !active; },
    };
    native.addEventListener('visibilitychange', () => shadow.dispatchEvent(new Event('visibilitychange')));
    window.SpikeVision = { document: scoped };
    if (!window.LightweightCharts) {
      await new Promise((resolve, reject) => {
        const script = native.createElement('script');
        script.src = '/vision-assets/vendor/lightweight-charts.standalone.production.js';
        script.onload = resolve; script.onerror = reject; native.head.append(script);
      });
    }
    await Promise.all(sheets);
    await import('/vision-assets/app.js');
  }
  window.SpikeVisionModule = {
    async setActive(value) {
      active = value;
      if (active && !loaded) loaded = mount().catch((error) => {
        native.getElementById('vision-module-error').textContent = error.message || 'VLM 工作流加载失败，请刷新页面。';
        throw error;
      });
      if (active) await loaded;
      shadow?.dispatchEvent(new Event('visibilitychange'));
    },
  };
})();
