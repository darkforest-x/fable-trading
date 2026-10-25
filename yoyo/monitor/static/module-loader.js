/* Load only the selected workspace. Failed assets can be retried; shared scripts run once. */
(() => {
  'use strict';
  const groups = {
    signals: ['signal-history.css','signal-components.css','okx-execution.css','signal-workspace.css','monday-reclaim-paper.css','okx-execution.js','range-chart.js','monday-candle-feed.js','monday-fib-chart.js','yolo-preview-sheet.js','reclaim-chart.js','monday-list-model.js','reclaim-watchlist.js','reclaim-analytics.js','reclaim-close.js','monday-reclaim-paper.js','signal-workspace.js'],
    'signal-charts': ['echarts-6.1.0.min.js'],
    research: ['research.css','research.js'],
    platform: ['research.css','platform.css','platform.js'],
    datasets: ['research.css','datasets.css','datasets.js'],
    strategies: ['research.css','strategies.css','strategies.js'],
    manual: ['research.css','manual.css','manual.js'],
    'paper-desk': ['research.css','paper-desk.css','range-chart.css','range-chart.js','paper-desk-analytics.js','paper-desk-market.js','paper-desk.js'],
    notifications: ['research.css','notifications.css','notifications.js'],
    knowledge: ['research.css','knowledge.css','knowledge.js'],
    retest: ['v130.css','v130.js'],
    copier: ['research.css','market-sync.css','copier.css','copier.js'],
    market: ['market-terminal.css','research.css','v130.css','market-sync.css','market-accounts.css','range-chart.css','range-chart.js','market-accounts.js','market-intelligence.js','market-terminal.js','market-sync.js'],
    'trading-books': ['research.css','reading-tools.css','reading-tools.js','trading-books.css','trading-books-study.js','trading-books.js'],
    orderflow: ['research.css','market-sync.css','reading-tools.css','reading-tools.js','orderflow.css','orderflow.js'],
    'monday-fib': ['monday-fib.css','range-chart.js','monday-candle-feed.js','monday-fib-chart.js','monday-quick.js','monday-list-model.js','monday-fib.js'],
    'monday-live': ['monday-live.css','monday-ledger.js','monday-live.js'],
    'auto-leaders': ['research.css','market-sync.css','altcoin-leaders.css','altcoin-leaders.js','leader-page.js'],
    'yolo-detect': ['workbench-bootstrap.css','research.css','yolo-detect.css','okx-execution.css','manual-live.css','manual-account.js','manual-close.js','manual-position-chart.js','manual-live.js','workbench-bootstrap.js','range-chart.js','yolo-chart-levels.js','yolo-preview-sheet.js','yolo-market.js','yolo-detect.js','yolo-compact.css','yolo-wallpaper.css'],
    langlang: ['research.css','market-sync.css','reading-tools.css','reading-tools.js','trading-playbooks.css','trading-playbooks.js','altcoin-leaders.css','altcoin-leaders.js','langlang.css','transcript-search.js','langlang.js'],
    rektproof: ['research.css','market-sync.css','reading-tools.css','reading-tools.js','trading-playbooks.css','trading-playbooks.js','rektproof.css','rektproof-core.js','rektproof-study.js','rektproof.js'],
    yolo: ['research.css','research.js','yolo-workflow.css','yolo-workflow.js'],
    vision: ['vision-module.js'],
  };
  const aliases = {factors:'research',experiments:'research',backtests:'research',models:'platform',paper:'strategies',learning:'knowledge',content:'knowledge'};
  const assets = new Map(), views = new Map(), preloads = new Map();
  let versions = {};
  try { versions = JSON.parse(document.getElementById('asset-versions')?.textContent || '{}'); } catch { /* Unversioned preview still works. */ }
  const url = name => `/static/${name}${versions[name] ? '?v='+versions[name] : ''}`;
  function preload(name){
    if(assets.has(name)||preloads.has(name))return;
    const link=document.createElement('link');link.rel='preload';link.as='script';link.href=url(name);
    preloads.set(name,link);document.head.append(link);
  }
  function clearPreload(name){preloads.get(name)?.remove();preloads.delete(name);}
  function asset(name) {
    if (assets.has(name)) return assets.get(name);
    const pending = new Promise((resolve,reject) => {
      const css = name.endsWith('.css'), el = document.createElement(css ? 'link' : 'script');
      if (css) { el.rel='stylesheet'; el.href=url(name); } else { el.src=url(name); el.async=false; }
      el.onload=()=>{clearPreload(name);resolve();}; el.onerror=()=>{clearPreload(name);el.remove();assets.delete(name);reject(new Error('页面资源加载失败，请点击刷新重试。'));};
      // Module sheets precede global polish regardless of visit order.
      if(css) document.head.insertBefore(el,document.getElementById(name==='market-terminal.css'?'workspace-page-style':'workspace-polish')); else document.head.append(el);
    });
    assets.set(name,pending); return pending;
  }
  function ensure(view) {
    const key = aliases[view] || view;
    if (!views.has(key)) views.set(key,(async()=>{
      const names=groups[key] || [];
      // Fetch all scripts immediately; execution still waits for CSS and dependencies.
      names.filter(n=>n.endsWith('.js')).forEach(preload);
      await Promise.all(names.filter(n=>n.endsWith('.css')).map(asset));
      for(const name of names.filter(n=>n.endsWith('.js'))) await asset(name);
    })().catch(error=>{views.delete(key);throw error;}));
    return views.get(key);
  }
  window.SpikeModules={ensure,url};
})();
