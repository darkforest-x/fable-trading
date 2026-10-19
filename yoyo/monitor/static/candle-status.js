/* Shared public read-cache health. Viewing status never requests exchange data. */
(() => {
  'use strict';
  const root = document.getElementById('candle-status');
  if (!root) return;
  let pending=false, timer=null, search='', debounce=null;
  const esc = value => String(value ?? '—').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const clock = ms => ms ? new Date(ms).toLocaleString('zh-CN',{hour12:false}) : '尚未成功同步';
  const active = () => !document.hidden && location.hash.split('?')[0]==='#system';
  const labels = {ready:'最新收盘已齐',syncing:'正在补数',behind:'按需补齐',error:'同步失败',empty:'暂无收盘数据',partial:'历史数据不足'};
  root.innerHTML = '<div class="candle-heading"><div><h3>全站 K 线共享</h3><p>同币种、同周期共用一份行情，打开多个页面也只补一次缺失数据。</p></div><button type="button" id="candle-status-refresh">刷新状态</button></div><div id="candle-status-summary" role="status">正在读取…</div><div class="candle-filter"><label for="candle-symbol-search">查找币种</label><input id="candle-symbol-search" type="search" placeholder="例如 BTC、ETH" autocomplete="off"><span id="candle-status-count"></span></div><p id="candle-status-error" role="alert" hidden></p><div class="candle-table-wrap" role="region" aria-label="K 线同步明细，可横向滚动" tabindex="0"><table><thead><tr><th>币种 / 周期</th><th>同步状态</th><th>最新已收盘</th><th>最近成功补数</th><th>缓存复用 / 补数</th></tr></thead><tbody id="candle-status-rows"></tbody></table></div><p class="candle-footnote">按需更新：历史图表允许停留在历史时点；“按需补齐”表示缓存落后于当前收盘，不代表正在使用旧数据交易。未收盘 K 线最多缓存 2 秒，只用于实时图表。实盘新鲜度检查保持原规则。</p>';
  async function refresh() {
    if (!active() || pending) return;
    pending=true;
    const requestedSearch=search;
    const error=document.getElementById('candle-status-error');
    try {
      const response=await fetch('/api/candles/status?symbol='+encodeURIComponent(search),{cache:'no-store',signal:AbortSignal.timeout(8000)});
      if(!response.ok) throw new Error('状态读取失败 '+response.status);
      const data=await response.json(), s=data.summary, total=s.hits+s.requests;
      if(requestedSearch!==search)return;
      document.getElementById('candle-status-summary').innerHTML=`<span><b>${s.series}</b> 币种周期组合</span><span><b>${total?Math.round(s.hits/total*100):0}%</b> 请求直接复用</span><span><b>${s.syncing}</b> 正在同步</span><span class="${s.errors?'candle-error':''}"><b>${s.errors}</b> 同步异常</span><span><b>${(data.cache_bytes/1048576).toFixed(1)} MB</b> 本机缓存</span>`;
      document.getElementById('candle-status-count').textContent=`显示 ${data.items.length} / ${data.matched} 组 · 共享行情 ≤${data.total_rate} 次/秒，页面与研究共享 ≤${data.background_rate} 次/秒`;
      document.getElementById('candle-status-rows').innerHTML=data.items.map(r=>`<tr><td><strong>${esc(r.symbol.replace('-USDT-SWAP',''))}</strong><small>${esc(r.bar)} · OKX</small></td><td><span class="candle-state candle-${esc(r.state)}">${esc(r.state==='partial'&&r.last_close_ms&&r.lag_bars===0?'收盘已齐 · 历史有限':labels[r.state])}</span>${r.error?`<small class="candle-error">${esc(r.error)}</small>`:r.missing?`<small>本次历史查询少 ${r.missing} 根</small>`:r.lag_bars?`<small>距当前收盘 ${r.lag_bars} 根</small>`:''}</td><td>${esc(clock(r.last_close_ms))}</td><td>${esc(clock(r.success))}</td><td>${r.hits} / ${r.requests}</td></tr>`).join('')||'<tr><td colspan="5">'+(search?'没有匹配的币种。':'尚无共享数据；后台或图表读取行情后会自动出现。')+'</td></tr>';
      error.hidden=true;
    } catch(e) {
      error.textContent='暂时无法读取共享行情状态；下次刷新会重试。'+e.message;
      error.hidden=false;
    } finally {pending=false;if(requestedSearch!==search&&active())refresh();}
  }
  function schedule(){clearInterval(timer);timer=null;if(active()){refresh();timer=setInterval(refresh,15000);}}
  document.getElementById('candle-status-refresh').addEventListener('click',refresh);
  document.getElementById('candle-symbol-search').addEventListener('input',e=>{search=e.target.value.trim();clearTimeout(debounce);debounce=setTimeout(refresh,250);});
  window.addEventListener('hashchange',schedule);
  document.addEventListener('visibilitychange',schedule);
  schedule();
})();
