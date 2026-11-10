/* Cloud book links keep the monitor pane and share the same reader as the other accounts. */
(() => {
  'use strict';
  const key = 'live-cloud-v17';
  const columns = ['trade', 'query', 'side', 'outcome', 'from', 'until', 'order'];
  const chapters = ['overview', 'research', 'rules', 'forward', 'sources', 'history'];

  function tradeId(value) {
    const match = String(value ?? '').match(/^(?:cloud-v17-)?0*(\d+)$/i);
    if (!match) return '';
    const id = Number(match[1]);
    return Number.isSafeInteger(id) && id > 0 ? String(id) : '';
  }

  function read(search) {
    const query = new URLSearchParams(search);
    if (query.get('book') !== key) return null;
    const params = new URLSearchParams();
    for (const name of columns) if (query.has(name)) params.set(name, query.get(name));
    return {chapter: chapters.includes(query.get('chapter')) ? query.get('chapter') : 'overview', params};
  }
  function set(url, options) {
    if (!options && url.searchParams.get('book') !== key) return url;
    for (const name of ['book', 'chapter', ...columns]) url.searchParams.delete(name);
    if (options) {
      url.searchParams.set('book', key);
      if (chapters.includes(options.chapter) && options.chapter !== 'overview') url.searchParams.set('chapter', options.chapter);
      for (const name of columns) if (options.params?.get(name)) url.searchParams.set(name, options.params.get(name));
    }
    return url;
  }
  function write(options) {
    const url = set(new URL(location.href), options);
    if (url.href !== location.href) history.replaceState(history.state, '', url);
  }
  function backtestOptions(route) {
    const params = route?.params instanceof URLSearchParams
      ? route.params : new URLSearchParams(route?.params || '');
    const options = Object.fromEntries(columns.filter(name => params.has(name)).map(name => [name, params.get(name)]));
    if (params.has('trade')) options.trade = tradeId(params.get('trade'));
    if (options.side === '1') options.side = 'long';
    if (options.side === '-1') options.side = 'short';
    return options;
  }
  window.SpikeCloudBookRoute = {read, write, backtestOptions, tradeId};
  if (location.pathname.startsWith('/static/strategy-booklet/cloud-v17/') && /\/(?:index\.html)?$/.test(location.pathname)) {
    const [chapter, search] = location.hash.slice(1).split('?');
    const url = set(new URL('/', location.href), {chapter, params: new URLSearchParams(search)});
    url.hash = 'signals?pane=reclaim';
    location.replace(url);
  }
})();
