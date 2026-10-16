/* Independent hourly observation board. All values come from immutable server
 * snapshots; filtering never recalculates ranks or writes a trading decision. */
(() => {
  'use strict';
  const state={benchmark:'ETH',data:null,history:[],error:'',filter:'candidates',query:'',page:0,selected:'',loading:false};
  let host=null,timer=null,generation=0,controller=null;
  const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const pct=v=>typeof v==='number'&&Number.isFinite(v)?`${(v*100).toFixed(2)}%`:'—';
  const pp=v=>typeof v==='number'&&Number.isFinite(v)?`${(v*100).toFixed(2)} pp`:'—';
  const time=v=>typeof v==='number'?new Date(v).toLocaleString('zh-CN',{hour12:false}):'—';
  const label={discovery:'发现池',wave_observing:'首波观察',not_participating:'首波未领先',downgraded:'结构降级',candidate:'领涨候选',unknown:'资料不足',late_listing:'本轮晚加入',
    wait_market:'等市场启动',wait_pullback:'等回调观察',observe_only:'继续观察',structure_lost:'起点已失守',wait_recovery:'等恢复',review_entry:'待人工核对位置',data_missing:'暂停判断',next_episode:'等下一轮',
    launch:'首波发展中',pullback:'回调观察中',recovery:'恢复已确认',waiting:'等待启动',
    missing_checkpoint:'没有行情记录',incomplete_common_window:'共同窗口缺 K 线',invalid_candle:'行情格式异常',unordered_candles:'行情顺序异常',unaligned_candle:'时间未对齐',no_longer_in_live_universe:'已不在当前交易池'};
  const rule=`<details class="al-rules"><summary>本版如何自动判断</summary><ol>
    <li>基准为所选 BTC 或 ETH。1H 收盘高于此前 24 根最高收盘，确认新一轮启动；起点取突破前一根收盘。</li>
    <li>首次收盘低于前一根时冻结首波。基准收盘重新高于首波最高收盘，确认恢复。跌回起点、恢复后的首次回落或满 120 小时结束本轮。</li>
    <li>首波收益为正、且不弱于基准，并仍守住本币起点的币进入候选。比较本轮相对收益、首波回吐、最大收盘回撤、恢复耗时。各维度都不差且有一项更好才排前一层；同层保留并列，层内按币名展示。</li>
    <li>没有已冻结首波时展示发现池，按同窗 24h 相对收益排列。它不是已确认的龙头名单。</li>
    <li>近 7 天高点是历史价格参考，不替代日线阻力；成交量比为同币合约张数与首波前等长窗口比较，不是成交额或主动买卖量。</li>
  </ol><p>这是 v1 研究规则，尚未验证收益。热搜、板块带动性、盘口深度和订单流尚未接入；候选仍需核对入场位置。初次启动可从历史行情恢复背景，但首次观察时间保留为现在，不补成历史选中。</p>
  <p>数据：OKX 分类明确的加密币 USDT 线性永续，排除 BTC／ETH 基准、稳定币、股票与商品；复用已有已确认 K 线，每分钟检查。币种池在本轮首次观察时冻结，缺数据者仍保留。<a href="https://www.okx.com/docs-v5/en/#public-data-rest-api-get-instruments" target="_blank" rel="noopener noreferrer">OKX 数据定义 ↗</a></p></details>`;

  function html(data=state.data){
    const d=data||{},ep=d.episode,cov=d.coverage||{},historical=!!d.historical_snapshot;
    const active=d.ranking_active===true&&!historical;
    const phase=label[ep?.phase||'waiting'];
    let rows=(d.items||[]).filter(r=>(state.filter==='all'||state.filter==='missing'&&r.quality!=='ok'||state.filter==='candidates'&&r.tier!=null)
      &&r.symbol.toLowerCase().includes(state.query.toLowerCase()));
    // Without a frozen wave, the default view still offers a labelled discovery
    // queue. A completed wave with no qualifying candidate stays honestly empty.
    const discovery=state.filter==='candidates'&&(!ep||ep.phase==='launch');
    if(discovery)rows=(d.items||[]).filter(r=>r.quality==='ok'&&r.symbol.toLowerCase().includes(state.query.toLowerCase()));
    const pages=Math.max(1,Math.ceil(rows.length/30));state.page=Math.min(state.page,pages-1);
    const shown=rows.slice(state.page*30,state.page*30+30);
    const paused=historical?'历史观察快照，不能作为当前排名':!data?'正在等待首份行情观察':!active?'当前判断暂停：数据过期或观察器未就绪。下表仅供查看已有记录。':'';
    return `<section class="al-board"><header class="al-header"><div><span class="al-eyebrow">OKX · 1H · 自动观察</span><h2>自动找龙头</h2><p>先看谁领涨，再判断有没有合适的位置。</p></div><div class="al-controls"><label>市场基准<select id="al-benchmark" aria-label="市场基准"><option value="ETH" ${state.benchmark==='ETH'?'selected':''}>ETH</option><option value="BTC" ${state.benchmark==='BTC'?'selected':''}>BTC</option></select></label><button id="al-refresh" type="button" ${state.loading?'disabled':''}>${state.loading?'读取中…':'刷新观察'}</button></div></header>
    ${state.error?`<p role="alert" class="al-alert">${esc(state.error)} <button id="al-retry" type="button">重试</button></p>`:''}
    ${paused?`<p role="status" class="al-alert">${esc(paused)}</p>`:''}
    <div class="al-stats"><div><span>市场阶段</span><strong>${esc(phase)}</strong></div><div><span>可比较 / 全部候选池</span><strong>${cov.comparable??0} / ${cov.total??0}</strong></div><div><span>领涨候选</span><strong>${cov.candidates??0}</strong></div><div><span>共同截止 · 本地时区</span><strong class="al-time">${time(d.asof_ms)}</strong></div></div>
    <p class="al-meta">首次记录本快照：${time(d.observed_ms)} · 最近检查：${time(d.health?.last_checked_ms)} · ${active?'观察运行中':'仅查看'} · 规则 ${esc(d.rule_version||'altcoin-leaders-1h-v1')}</p>
    ${ep?`<div class="al-episode"><span>启动确认 ${time(ep.launch_ms)}</span><span>首波结束 ${time(ep.wave_end_ms)}</span><span>恢复确认 ${time(ep.recovery_confirmed_ms)}</span><span>本轮首次观察 ${time(ep.first_observed_ms)}</span></div>`:''}
    ${rule}<div class="al-toolbar"><label>列表<select id="al-filter" aria-label="列表"><option value="candidates" ${state.filter==='candidates'?'selected':''}>候选 / 发现池</option><option value="all" ${state.filter==='all'?'selected':''}>全部记录</option><option value="missing" ${state.filter==='missing'?'selected':''}>资料不足</option></select></label><label>查找币种<input id="al-search" type="search" value="${esc(state.query)}" placeholder="例如 SOL" maxlength="48"></label><label>观察记录<select id="al-history" aria-label="观察记录"><option value="">当前观察</option>${state.history.map(x=>`<option value="${x.snapshot_id}" ${String(x.snapshot_id)===state.selected?'selected':''}>#${x.snapshot_id} · ${time(x.observed_ms)}</option>`).join('')}</select></label><button id="al-export" type="button" ${d.snapshot_id?'':'disabled'}>导出完整快照</button></div>
    <p class="al-caption">${discovery?'当前为发现池：首波与回调尚未完成，不标领涨层级。':'层级相同表示并列；“待人工核对位置”仍未验证盘口与入场条件。'} ${cov.comparable<cov.total?'数据覆盖不全，层级只反映可比较部分。':''}</p>
    <div class="al-table ${ep&&ep.phase!=='launch'?'':'al-discovery'}" tabindex="0" aria-label="龙头候选比较表，可横向滚动"><table><thead><tr><th>币种 / 本轮强弱</th><th>当前机会</th><th>1H末收盘</th><th>24h涨幅 / 相对基准</th><th>近7天高点距离</th>${ep&&ep.phase!=='launch'?'<th>首波 / 相对基准</th><th>首波回吐</th><th>收盘最大回撤</th><th>恢复</th><th>本轮相对基准</th><th>同币量比</th>':''}</tr></thead><tbody>${shown.map(r=>`<tr><td><a href="https://www.tradingview.com/chart/?symbol=${encodeURIComponent('OKX:'+r.symbol.replace('-USDT-SWAP','USDT.P'))}" target="_blank" rel="noopener noreferrer">${esc(r.symbol.replace('-USDT-SWAP',''))} ↗</a><small>${r.tier!=null?`${active?'':'当时'}第 ${r.tier} 层 · 并列观察`:esc(label[r.leadership]||r.leadership)}</small></td><td>${esc(label[r.action]||r.action)}${r.quality!=='ok'?`<small>${esc(label[r.quality]||r.quality)}</small>`:''}</td><td>${typeof r.price==='number'?esc(Number(r.price.toPrecision(8))):'—'}</td><td>${pct(r.return_24h)}<small>${pp(r.rs_24h)}</small></td><td>${r.room_to_prior_7d_high==null?'资料不足':r.room_to_prior_7d_high<0?'已超历史参考':pct(r.room_to_prior_7d_high)}</td>${ep&&ep.phase!=='launch'?`<td>${pct(r.wave_return)}<small>${pp(r.wave_rs)}</small></td><td>${pct(r.giveback)}</td><td>${pct(r.drawdown)}</td><td>${r.recovered_ms?`${r.recovery_bars} 小时<small>${time(r.recovered_ms)}</small><small>${r.holding_recovery?'当前仍守住':'已回落至首波高点下'}</small>`:'尚未确认'}</td><td>${pp(r.episode_rs)}</td><td>${typeof r.volume_ratio==='number'?r.volume_ratio.toFixed(2)+'×':'—'}</td>`:''}</tr>`).join('')}</tbody></table></div>
    ${!shown.length?'<p class="al-empty">本条件下暂无候选。可查看全部记录及未入选原因。</p>':''}<div class="al-pages"><span>${rows.length} 条 · ${state.page+1} / ${pages} 页</span><button id="al-prev" ${state.page?'':'disabled'}>上一页</button><button id="al-next" ${state.page+1<pages?'':'disabled'}>下一页</button></div>
    <p class="al-foot">本页输出观察候选，不发送交易通知、不下单。<a href="#langlang?tab=leaders">查看完整找龙头方法</a> · <a href="#market">原市场共振观察</a></p></section>`;
  }
  function render(){if(!host?.isConnected)return;host.innerHTML=html();bind();}
  async function get(url,signal){const r=await fetch(url,{cache:'no-store',signal});if(!r.ok)throw new Error(`读取失败（HTTP ${r.status}）`);return r.json();}
  async function load(){
    if(!host||state.loading)return;
    const gen=generation,selected=state.selected,benchmark=state.benchmark;
    controller=new AbortController();state.loading=true;render();
    try{
      const url=selected?`/api/altcoin-leaders/snapshots/${encodeURIComponent(selected)}`:`/api/altcoin-leaders?benchmark=${benchmark}`;
      const [data,history]=await Promise.all([get(url,controller.signal),get(`/api/altcoin-leaders/history?benchmark=${benchmark}`,controller.signal)]);
      if(gen!==generation)return;state.data=data;state.history=history.items||[];state.error='';
    }catch(e){if(gen!==generation||e.name==='AbortError')return;state.error=e.message; if(state.data)state.data={...state.data,ranking_active:false};}
    finally{if(gen===generation){state.loading=false;render();}}
  }
  function bind(){
    const on=(id,event,fn)=>host.querySelector('#'+id)?.addEventListener(event,fn);
    on('al-refresh','click',load);on('al-retry','click',load);
    on('al-benchmark','change',e=>{controller?.abort();generation++;state.loading=false;state.benchmark=e.target.value;state.selected='';state.data=null;state.history=[];state.page=0;load();});
    on('al-history','change',e=>{controller?.abort();generation++;state.loading=false;state.selected=e.target.value;state.data=null;state.page=0;load();});
    on('al-filter','change',e=>{state.filter=e.target.value;state.page=0;render();});
    on('al-search','input',e=>{const value=e.target.value;state.query=value;state.page=0;render();const input=host.querySelector('#al-search');input.focus();});
    on('al-prev','click',()=>{state.page--;render();});on('al-next','click',()=>{state.page++;render();});
    on('al-export','click',async()=>{try{const data=await get(`/api/altcoin-leaders/snapshots/${state.data.snapshot_id}`);const url=URL.createObjectURL(new Blob([JSON.stringify(data,null,2)],{type:'application/json'}));const a=document.createElement('a');a.href=url;a.download=`leaders-${state.benchmark}-${data.snapshot_id}.json`;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}catch(e){state.error=e.message;render();}});
  }
  function unmount(){generation++;clearInterval(timer);controller?.abort();state.loading=false;host=null;}
  function mount(target){unmount();host=target;render();load();timer=setInterval(()=>{if(!document.hidden&&!state.selected)load();},60000);}
  if(typeof document!=='undefined')document.addEventListener('visibilitychange',()=>{if(host&&!document.hidden&&!state.selected)load();});
  window.SpikeAutoLeaders={mount,unmount,html};
})();
