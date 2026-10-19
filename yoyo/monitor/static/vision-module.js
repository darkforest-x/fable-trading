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
    style.textContent = `:host{display:block;min-width:0;color-scheme:dark}
      .vision-body{font:12px/1.6 var(--font-ui);color:var(--text);background:var(--bg)}
      .app-frame,.main-content{display:block;max-width:none;padding:0}.app-sidebar{display:block;position:static;width:auto;height:auto;padding:0;margin:0 0 8px;background:var(--surface);border:1px solid var(--line);border-radius:4px}
      .primary-nav{display:flex;flex-direction:row;flex-wrap:nowrap;overflow:auto;gap:4px;margin:0;padding:0 8px}.nav-tab{flex-direction:row;font-size:12px;min-height:36px;padding:8px 10px;border-radius:0;border:0;border-bottom:2px solid transparent;background:none;color:var(--muted)}.nav-tab.active{border-bottom-color:var(--mint);color:var(--mint);background:none}
      .topbar{position:static;height:auto;padding:6px 8px;background:var(--surface);border:1px solid var(--line);border-radius:4px;margin-bottom:8px}.page-identity{display:none}.topbar-meta{font:10px var(--mono);justify-content:flex-end;gap:10px}
      .workspace-grid{display:grid;grid-template-columns:220px minmax(0,1fr) 260px;grid-template-areas:'candidates chart result' 'candidates criteria result';gap:8px}.candidates-panel{top:102px;max-height:calc(100dvh - 114px);padding:8px}.panel{background:var(--surface);border:1px solid var(--line);border-radius:4px;box-shadow:none}.chart-panel{padding:8px}.chart-toolbar{padding:6px 8px;gap:8px}.chart-title h2{font-size:14px}.drop-zone{height:440px}.result-panel{padding:12px}.result-panel .empty-result{padding:20px 8px}.analysis-controls{flex-wrap:wrap;padding:8px;gap:8px;background:var(--surface-lowest)}.signal-item{border:1px solid var(--line);border-radius:4px;padding:8px;background:var(--surface-lowest)}.signal-item.active{border-color:var(--mint);background:var(--surface-high)}.signal-topline strong{font:500 12px var(--mono)}.signal-meta,.signal-timeframe{font:10px var(--mono)}.button{font-size:11px;min-height:30px;padding:5px 8px;border-radius:4px}.button.primary{background:var(--mint);color:var(--on-accent)}h2{font-size:15px}h3{font-size:13px}.chart-tools{gap:6px}.workspace-footer{margin-left:0;font-size:10px}.image-panel:has(#empty-stage:not([hidden])) #upload-chart,.image-panel:has(#empty-stage:not([hidden])) #clear-input{display:none}
      @media(max-width:1150px){.workspace-grid{grid-template-columns:200px minmax(0,1fr);grid-template-areas:'candidates chart' 'candidates result' 'candidates criteria'}}
      @media(max-width:700px){.workspace-grid{grid-template-columns:1fr;grid-template-areas:'candidates' 'chart' 'result' 'criteria'}.candidates-panel{position:static;max-height:220px}.drop-zone{height:320px}.primary-nav{overflow:auto}.topbar-meta{flex-wrap:wrap}}
    `;
    const sheets = ['/vision-assets/styles.css', '/vision-assets/replay.css', '/vision-assets/chart-tools.css', ...['design-tokens.css','fonts/stitch-fonts.css','workspace-motion.css'].map(file=>window.SpikeModules?.url(file)||'/static/'+file)].map((href) => {
      const link = native.createElement('link'); link.rel = 'stylesheet'; link.href = href; shadow.append(link);
      return new Promise((resolve, reject) => { link.onload = resolve; link.onerror = reject; });
    });
    const body = native.createElement('div');
    body.className = 'vision-body';
    body.append(...Array.from(page.body.childNodes));
    shadow.append(style, body);
    window.SpikeMotion?.attach(shadow);
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
