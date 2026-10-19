/* 全市场异动: live sync-shock observer + v2 research history. Records only; no orders, no pushes. */
(() => {
  "use strict";
  const HOLDS = ["1h", "4h", "12h", "24h"];
  const INSTRUMENTS = {eth: "ETH", btc: "BTC", alts: "山寨篮子"};
  const NOTION_TRACKER = "https://app.notion.com/p/nick-wiki/3f28856479af8068b0cef31b5baec989";
  // Joint volume-ratio tercile edges, frozen on events before 2025 (exp-market-sync-shock-20261008-v3 edges.csv).
  // Only the 30m up-shock high tier was better in both periods; the rest is a label, not a signal.
  const VOLUME_EDGES = {"30|1": [3.750, 5.399], "30|-1": [3.640, 5.592], "60|1": [3.274, 4.729], "60|-1": [3.543, 5.450]};
  const TIER_NAMES = ["低档", "中档", "高档"];
  // v4 vetoes, computed by the observer (yoyo/monitor/market_sync.py VETO_EDGES): labels, not filters.
  const VETO_NAMES = {rally: "大涨后·慎追", high_vol: "高波动·慎空"};
  const state = {active: false, pending: false, live: null, history: {}, iterations: null, error: null,
    tf: "60", kind: "sync", config: "z3.0_v3.0_b0.75", side: "1", hold: "12h", inst: "eth", vol: "all", limit: 30, panel: "overview", eventDays: "all"};
  const root = () => document.getElementById("market-sync-workspace");
  const esc = (v) => String(v ?? "—").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
  const fin = (v) => typeof v === "number" && Number.isFinite(v);
  const clock = (ms, year = false) => ms == null ? "—" : new Intl.DateTimeFormat("zh-CN", {timeZone: "Asia/Shanghai", ...(year ? {year: "numeric"} : {}), month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false}).format(new Date(ms));
  const pct = (v, d = 2) => fin(v) ? `${v >= 0 ? "+" : ""}${(v * 100).toFixed(d)}%` : "—";
  const share = (v) => fin(v) ? `${Math.round(v * 100)}%` : "—";
  // API outcomes remain in basis points; convert only at the presentation boundary.
  const returnPct = (v) => fin(v) ? `${v >= 0 ? "+" : ""}${(v / 100).toFixed(3).replace(/(\.\d{2})0$/, "$1")}%` : "—";
  const returnText = (text) => String(text ?? "").replace(/([+-]?\d+(?:\.\d+)?)\s*bps?\b/gi, (_, v) => returnPct(Number(v)));
  const num = (v, d = 2) => fin(v) ? v.toFixed(d) : "—";
  const magnitude = (v) => fin(v) ? num(Math.abs(v)) : "—";
  const tone = (v) => fin(v) ? (v > 0 ? "r-positive" : v < 0 ? "r-negative" : "") : "";
  const parseConfig = (key) => { const m = /^z([\d.]+)_v([\d.]+)_b([\d.]+)$/.exec(key || ""); return m ? {z: +m[1], v: +m[2], b: +m[3]} : null; };
  const configLabel = (key) => { const c = parseConfig(key); return c ? `波动 ≥${c.z} 倍 · 成交量 ≥${c.v} 倍 · 山寨同向 ≥${Math.round(c.b * 100)}%` : key; };
  const tfLabel = (tf) => tf === "60" ? "1H" : "30m";
  const groupKey = () => `${state.tf}|${state.kind}|${state.config}`;
  const sideName = (s) => s > 0 ? "上涨冲击" : "下跌冲击";
  const moveName = () => state.side === "all" ? "同涨或同跌" : +state.side > 0 ? "一起上涨" : "一起下跌";
  const holdLabel = (h = state.hold) => `${parseInt(h, 10)} 小时`;
  const tradePlan = () => state.side === "all" ? `上涨后做多、下跌后做空 ${INSTRUMENTS[state.inst]}（下一根开盘入场）` : `下一根开盘${+state.side > 0 ? "做多" : "做空"} ${INSTRUMENTS[state.inst]}`;
  const isForward = e => e.out_of_sample === true && Number.isFinite(e.first_observed_ms);

  let contextData=null,contextError='',contextLoading=false,activationVersion=0;
  const chartView={height:500,zone:'America/New_York',bars:192,range:true,fib:false,profile:true,npoc:false,prior:false};
  let soundEnabled=false,audioContext=null;
  const sounded=new Set();
  try{chartView.zone=localStorage.getItem('spike-market-zone')||chartView.zone;soundEnabled=localStorage.getItem('spike-market-sound')==='true';}catch(_){}
  if(!['America/New_York','UTC','Asia/Shanghai'].includes(chartView.zone))chartView.zone='America/New_York';
  function beep(){
    try{const Audio=window.AudioContext||window.webkitAudioContext;if(!Audio)return;audioContext||=new Audio();audioContext.resume().catch(()=>{});const o=audioContext.createOscillator(),g=audioContext.createGain();o.connect(g);g.connect(audioContext.destination);o.frequency.value=660;g.gain.setValueAtTime(.10,audioContext.currentTime);g.gain.exponentialRampToValueAtTime(.001,audioContext.currentTime+.5);o.start();o.stop(audioContext.currentTime+.5);}catch(_){}
  }
  function currentAlert(){
    const health=state.live?.freshness?.[state.tf],r=state.live?.snapshot?.latest?.[state.tf]?.at(-1);
    if(state.error||health?.stale||!health?.ready||!r)return {kind:'unavailable',title:'数据异常 · 暂停当前行情判断',detail:'先确认行情已恢复，再看共振和方向。历史研究仍可查看。',row:r};
    const cfg=parseConfig(state.config),side=Math.sign(r.btc_ret),same=side!==0&&side===Math.sign(r.eth_ret);
    const z=fin(r.btc_z)&&fin(r.eth_z)&&Math.min(Math.abs(r.btc_z),Math.abs(r.eth_z))>=cfg.z;
    const volume=fin(r.btc_vr)&&fin(r.eth_vr)&&Math.min(r.btc_vr,r.eth_vr)>=cfg.v;
    const breadth=side>0?r.breadth_up:r.breadth_down,wide=fin(breadth)&&breadth>=cfg.b;
    const decision=r.decisions?.[`${state.kind}|${state.config}`],met=[same&&z,volume,wide].filter(Boolean).length;
    return {kind:decision==='event'?'event':same&&z&&volume&&wide?'watch':'normal',title:decision==='event'?`共振异动 · ${side>0?'集体拉升':'集体下跌'}`:same&&z&&volume&&wide?'条件满足 · '+(decision==='cooldown'?'冷却中，不重复报警':'等待重复确认'):met>=2?'接近共振条件 · 继续观察':'当前没有共振异动',detail:`BTC / ETH 波动 ${magnitude(r.btc_z)}× / ${magnitude(r.eth_z)}× · 量比 ${num(r.btc_vr,1)}× / ${num(r.eth_vr,1)}× · 同向山寨 ${share(breadth)}`,row:r,side,met};
  }
  function alertMarkup(){
    const a=currentAlert();
    return `<section class="ms-alert ms-alert-${a.kind}" role="${a.kind==='event'||a.kind==='unavailable'?'alert':'status'}" aria-label="市场异常提示"><div class="ms-alert-icon" aria-hidden="true">${a.kind==='event'?'!':a.kind==='unavailable'?'!':'◎'}</div><div><span class="ms-eyebrow">实时观察 · ${tfLabel(state.tf)}</span><h2>${a.title}</h2><p>${esc(a.detail)}</p></div><button type="button" class="research-button" data-ms-sound aria-pressed="${soundEnabled}">${soundEnabled?'提示音已开':'开启提示音'}</button></section><div class="ms-quick-guide"><span><b>01</b> 先看是否有异动</span><span><b>02</b> 对照 BTC 区间与关键位</span><span><b>03</b> 等入场确认，再写交易计划</span></div>`;
  }
  // Stitch ce937c6c3f794297a3e408a802e8ddda: the left half of the summary strip.
  // Reuse the observer's closed-bar snapshot and freshness; never infer missing values as zero.
  function renderResonance(){
    const host=document.getElementById('market-resonance-overview');if(!host)return;
    if(!host.querySelector('[data-resonance-status]')){
      host.innerHTML=`<header><h2>行情共振监控</h2><span class="market-resonance-source" data-resonance-source></span><div class="market-resonance-period" role="group" aria-label="共振监控周期"><button type="button" data-resonance-tf="30" aria-pressed="false">30m</button><button type="button" data-resonance-tf="60" aria-pressed="true">1H</button></div></header>
        <div class="market-resonance-bias" data-resonance-bias title="1H 广度与动量 + 15m 结构；观察指数，不是胜率"><span>多空倾向</span><div><strong data-resonance-score>—</strong><span data-resonance-label>等待行情</span></div></div>
        <table aria-label="BTC 与 ETH 共振指标"><thead><tr><th scope="col">标的</th><th scope="col">涨跌幅</th><th scope="col">波动倍数</th><th scope="col">成交量倍数</th></tr></thead><tbody>${['btc','eth'].map(id=>`<tr><th scope="row">${id.toUpperCase()}</th><td data-resonance-value="${id}_ret">—</td><td data-resonance-value="${id}_z">—</td><td data-resonance-value="${id}_vr">—</td></tr>`).join('')}</tbody></table>
        <div class="market-resonance-breadth"><span><span data-resonance-direction>同向山寨</span> <b data-resonance-breadth>—</b></span><span data-resonance-coverage></span></div>
        <footer><span data-resonance-status role="status"></span><time data-resonance-close></time></footer>`;
      host.querySelectorAll('[data-resonance-tf]').forEach(button=>listen(button,'click',()=>{
        state.tf=button.dataset.resonanceTf;state.limit=30;render();loadPanelData();
      }));
    }
    const health=state.live?.freshness?.[state.tf],alert=currentAlert();
    const ready=!state.error&&health?.stale===false&&health?.ready===true&&!!alert.row,row=ready?alert.row:null;
    const set=(selector,value)=>{const node=host.querySelector(selector);if(node.textContent!==value)node.textContent=value;};
    host.querySelectorAll('[data-resonance-tf]').forEach(button=>button.setAttribute('aria-pressed',String(button.dataset.resonanceTf===state.tf)));
    for(const id of ['btc','eth'])for(const metric of ['ret','z','vr']){
      const key=`${id}_${metric}`,value=row?.[key],selector=`[data-resonance-value="${key}"]`;
      set(selector,metric==='ret'?pct(value):fin(value)?`${metric==='z'?magnitude(value):num(value)}×`:'—');
      host.querySelector(selector).className=metric==='ret'?tone(value):'';
    }
    const side=fin(row?.btc_ret)?Math.sign(row.btc_ret):0,breadth=side>0?row.breadth_up:side<0?row.breadth_down:null;
    set('[data-resonance-direction]',side?`山寨随 BTC ${side>0?'上涨':'下跌'}`:'同向山寨 · 方向待定');
    set('[data-resonance-breadth]',share(breadth));
    set('[data-resonance-coverage]',`样本覆盖 ${fin(health?.valid_alts)?health.valid_alts:'—'} / ${fin(health?.expected_alts)?health.expected_alts:'—'}`);
    const bias=contextData?.bias,biasReady=!contextError&&!contextData?.stale&&!contextData?.error&&fin(bias?.score);
    set('[data-resonance-score]',biasReady?`${bias.score>0?'+':''}${bias.score}`:'—');
    set('[data-resonance-label]',biasReady?`${bias.score>0?'↗ ':bias.score<0?'↘ ':''}${bias.label}`:'等待行情');
    host.querySelector('[data-resonance-bias]').className='market-resonance-bias '+(biasReady?tone(bias.score):'');
    set('[data-resonance-source]',state.live?.source?.label||'等待行情');
    set('[data-resonance-status]',ready?alert.title:state.error?'行情读取失败':health?.stale?'行情已过期':health?.ready===false?'行情不完整':'等待新鲜行情');
    host.dataset.state=ready?alert.kind:'unavailable';
    set('[data-resonance-close]',ready&&fin(row.close_ms)?`收盘 ${clock(row.close_ms)}`:'收盘 —');
  }
  function checkAlertSound(){
    const a=currentAlert();if(a.kind!=='event')return;
    const key=[state.tf,state.kind,state.config,a.row.close_ms,a.side].join('|');
    if(sounded.has(key))return;sounded.add(key);
    try{if(sessionStorage.getItem('spike-market-alert-'+key))return;sessionStorage.setItem('spike-market-alert-'+key,'1');}catch(_){}
    if(soundEnabled&&state.active)beep();
  }
  function biasMarkup(){
    const c=contextData,b=c?.bias,valid=!contextError&&!c?.stale&&fin(b?.score),value=valid?b.score:null;
    return `<section class="ms-bias" aria-label="市场情绪与结构"><div class="ms-heading"><div><span class="ms-eyebrow">市场情绪代理 · 1H 广度与动量 + 15m 结构</span><h2>多空倾向</h2></div><strong class="ms-bias-value ${value>0?'r-positive':value<0?'r-negative':''}">${valid?(value>0?'+':'')+value+'%':'—'} <small>${valid?esc(b.label):'等待新鲜行情'}</small></strong></div><div class="ms-bias-track" role="meter" aria-label="多空倾向指数" aria-valuemin="-100" aria-valuemax="100" ${valid?`aria-valuenow="${value}"`:'aria-valuetext="数据不足"'}><span class="ms-bias-center"></span>${valid?`<i style="left:${(value+100)/2}%"></i>`:''}</div><div class="ms-bias-scale"><span>−100% 强空</span><span>0 多空分歧</span><span>+100% 强多</span></div><div class="ms-bias-components">${(valid?b.components:[]).map(x=>`<span>${esc(x.name)} <b class="${tone(x.score)}">${/结构/.test(x.name)?esc(c?.structure?.label||'—'):(x.score>0?'+':'')+x.score}</b></span>`).join('')}</div><p class="quiet-text">三项等权的观察指数，反映多空条件是否一致；不是胜率，±100% 也不是满仓指令。</p><div class="ms-terminal-levels"><h3>关键价位</h3>${(c?.levels||[]).filter(l=>['range','profile'].includes(l.kind)).slice(0,4).map(l=>`<div><span>${esc(l.label)}</span><b>${num(l.price)}</b></div>`).join('')||'<p class="quiet-text">等待区间数据</p>'}</div></section>`;
  }
  const candleChart=c=>window.SpikeRangeChart.render(c,chartView);
  function contextMarkup(){
    const c=contextData;
    const zones=[['America/New_York','纽约日界'],['UTC','UTC 日界'],['Asia/Shanghai','北京时间日界']];
    return `${biasMarkup()}<section class="ms-context" aria-label="BTC区间与成交量分布"><div class="ms-heading"><div><span class="ms-eyebrow">OKX · BTCUSDT.P · 15min</span><h2>BTC 区间与关键价位</h2></div><label class="ms-zone">区间日界 <select id="ms-chart-zone" aria-label="区间日界">${zones.map(([v,label])=>`<option value="${v}" ${chartView.zone===v?'selected':''}>${label}</option>`).join('')}</select></label></div><div class="ms-chart-controls">${[['range','周一区间'],['fib','双向 Fib'],['profile','昨日 POC / VA'],['npoc','nPOC'],['prior','上周高低']].map(([key,label])=>`<button type="button" data-ms-chart="${key}" aria-pressed="${chartView[key]}">${label}</button>`).join('')}<button type="button" data-ms-range aria-label="切换图表时间范围">${chartView.bars===192?'近 2 天':'近 7 天'}</button></div>${contextError?`<p class="notice" role="alert">${esc(contextError)} · 不使用旧图判断当前行情</p>`:''}${c?.error?`<p class="notice" role="status">BTC 更新暂时失败：${esc(c.error)} · 保留最后成功收盘的数据</p>`:''}${c?.candles?.length?`${c.stale?'<p class="notice" role="alert">BTC 图表已过期 · 仅供查看历史位置</p>':''}<div class="ms-chart-scroll">${candleChart(c)}</div><div class="ms-chart-caption"><span>最新收盘：北京 ${clock(c.last_close_ms,true)} · 每分钟检查</span><span>${c.monday?.complete?'本周周一区间已冻结':c.monday?.developing?'周一区间形成中，尚未冻结':'周一数据不足，暂不画区间'}</span></div><p class="ms-profile-note">POC / VAH / VAL 根据 15min K 线成交量估算；nPOC 检查近 14 个完整日，未触及才保留。</p><details class="ms-level-details" id="ms-level-details"><summary>全部关键价位与计算口径</summary><div class="ms-level-grid">${(c.levels||[]).map(l=>`<span>${esc(l.label)}<b>${num(l.price)}</b></span>`).join('')}</div><p>${esc(c.profile_method)}。与 TradingView 原生逐级成交量分布可能不同。</p><p>${esc(c.npoc_method)}。</p><p>周一窗口：北京 ${clock(c.monday?.start_ms,true)} 至 ${clock(c.monday?.end_ms,true)}。Fib ↑ = 低点向上量，Fib ↓ = 高点向下量；1.13 为区间外扩展。</p><a href="https://www.tradingview.com/support/solutions/43000502040-volume-profile-indicators-basic-concepts/" target="_blank" rel="noopener">成交量分布口径参考 ↗</a></details>`:`<p class="quiet-text">${contextLoading?'正在加载 OKX BTC K 线与关键位…':'BTC 图表暂不可用，稍后自动重试。'}</p>`}</section>`;
  }
  async function loadContext(paint=true){
    if(contextLoading)return;
    contextLoading=true;const zone=chartView.zone,epoch=activationVersion;
    try{const data=await getJSON('/api/market-sync/context?zone='+encodeURIComponent(zone));if(epoch===activationVersion&&zone===chartView.zone){contextData=data;contextError='';}}
    catch(error){if(error.name!=='AbortError'&&epoch===activationVersion&&zone===chartView.zone)contextError='BTC 图表读取失败：'+error.message;}
    finally{contextLoading=false;if(state.active){if(zone!==chartView.zone||epoch!==activationVersion)loadContext();else if(paint) render();}}
  }

  function stats(values, controls = []) {
    const paired = controls.length > 0;
    const valid = values.map((v, i) => fin(v) && (!paired || fin(controls[i])));
    const xs = values.filter((_, i) => valid[i]);
    if (!xs.length) return null;
    const sorted = [...xs].sort((a, b) => a - b);
    const mean = xs.reduce((a, b) => a + b, 0) / xs.length;
    const mid = sorted.length % 2 ? sorted[(sorted.length - 1) / 2] : (sorted[sorted.length / 2 - 1] + sorted[sorted.length / 2]) / 2;
    const trimmed = sorted.slice(0, Math.max(0, sorted.length - 5));
    const cs = controls.filter((_, i) => valid[i]);
    const ctrl = cs.length ? cs.reduce((a, b) => a + b, 0) / cs.length : null;
    return {n: xs.length, mean, median: mid, win: xs.filter(x => x > 0).length / xs.length, ctrl,
      excess: ctrl == null ? null : mean - ctrl, trimmed: trimmed.length ? trimmed.reduce((a, b) => a + b, 0) / trimmed.length : null};
  }

  function historyView() {
    const h = state.history[groupKey()];
    if (!h || h.error) return {rows: [], h};
    const idx = h.exits.indexOf(`time_${state.hold}`) * h.instruments.length + h.instruments.indexOf(state.inst);
    const rows = h.rows.filter(r => state.side === "all" || String(r[1]) === state.side)
      .map(r => ({time: r[0], side: r[1], btc_z: r[2], eth_z: r[3], breadth: r[4], net: r[5][idx], ctrl: r[6][idx]}));
    return {rows, h};
  }

  function volumeTier(e) {
    const edges = VOLUME_EDGES[`${e.minutes}|${e.side}`], v = Math.min(e.btc_vr, e.eth_vr);
    return edges && fin(v) ? edges.filter(x => v >= x).length : null;
  }

  function liveEvents() {
    const snap = state.live?.snapshot;
    if (!snap) return [];
    return snap.events.filter(e => (state.eventDays==="all"||e.open_ms>=Date.now()-Number(state.eventDays)*86400000) && String(e.minutes) === state.tf && e.kind === state.kind && e.config === state.config
      && (state.side === "all" || String(e.side) === state.side) && (state.vol === "all" || volumeTier(e) === 2));
  }

  function envNow() {
    const env = state.live?.snapshot?.environment_now, edges = state.live?.snapshot?.veto_edges;
    if (!env || !fin(env.trend30)) return "";
    const rally = edges && env.trend30 >= edges.rally.edges[state.tf], hot = edges && fin(env.vol30) && env.vol30 >= edges.high_vol.edges[state.tf];
    const warn = [rally ? "上涨冲击处于研究中的趋势过滤区" : "", hot ? "下跌冲击处于研究中的高波动过滤区" : ""].filter(Boolean).join("；");
    return `<small>BTC 30 天 ${pct(env.trend30, 1)} · 小时波动 ${pct(env.vol30, 2).replace("+", "")}${warn ? ` · <b class="ms-warn">${esc(warn)}</b>` : ""}</small>`;
  }

  function feedStatus() {
    const snap = state.live?.snapshot, status = state.live?.status || {};
    const latest = snap?.latest?.[state.tf]?.at(-1);
    const health = state.live?.freshness?.[state.tf] || {}, stale = health.stale !== false;
    const ready = health.ready === true, restricted = status.error === "binance_http_451";
    const heading = stale ? "行情停更 · 现在只能看历史" : !ready ? "行情不完整 · 暂不能判断触发" : "行情已更新 · 观察最新收盘数据";
    return `<div class="ms-feed ${!ready || status.error ? "ms-feed-stale" : ""}" role="status"><strong>${heading}</strong>
      <span>${esc(state.live?.source?.label || "币安 USDT 永续")} · ${latest ? `最新 K 线收盘：北京 ${esc(clock(latest.close_ms, true))}。` : "还没有可用行情。"} ${stale ? "缓存已过期，无法判断现在是否触发。" : !ready ? "BTC、ETH 指标或山寨数据不足，不能当成没有信号。" : "每 30 分钟检查一次；新共振在本页突出提示，可开启提示音。"}</span>
      ${fin(health.expected_alts) && health.expected_alts > 0 ? `<small>山寨覆盖 ${esc(health.valid_alts)} / ${esc(health.expected_alts)}${health.valid_alts < health.expected_alts ? " · 部分样本缺失" : ""}</small>` : ""}
      ${status.error ? `<small>${restricted ? "币安因地区限制拒绝访问（HTTP 451），当前数据源尚未恢复。" : `最近更新失败：${esc(status.error)}`} 最近成功更新：${esc(clock(status.updated_ms || snap?.generated_ms, true))}。</small>` : ""}</div>`;
  }

  function ruleGuide() {
    const c = parseConfig(state.config), up = state.side === "1", down = state.side === "-1";
    const movement = up ? "涨幅" : down ? "跌幅" : "涨跌幅度";
    return `<section class="ms-guide" aria-label="触发条件说明">
      <div class="ms-heading"><h2>什么情况会记一次？</h2><span>${state.tf === "60" ? "每根 1 小时 K 线" : "每根 30 分钟 K 线"} · ${moveName()}</span></div>
      <div class="ms-conditions">
        <article><span class="ms-step">1</span><div><h3>BTC、ETH ${up ? "都明显上涨" : down ? "都明显下跌" : "同方向明显波动"}</h3><p>两者的${movement}都达到各自<strong>平常波动的 ${c.z} 倍</strong></p></div></article>
        <article><span class="ms-step">2</span><div><h3>两者都放量</h3><p>BTC、ETH 的成交量都达到各自<strong>平常的 ${c.v} 倍</strong></p></div></article>
        <article><span class="ms-step">3</span><div><h3>多数山寨一起${up ? "涨" : down ? "跌" : "动"}</h3><p>有数据的山寨样本中，<strong>至少 ${Math.round(c.b * 100)}% 同方向${up ? "上涨" : down ? "下跌" : "涨跌"}</strong></p></div></article>
      </div>
      <p class="ms-rule-note">${state.kind === "cluster" ? "当前这一根满足三条，且前两根里至少一根也同方向满足，才记录。" : "同一根已收盘 K 线同时满足三条，才算触发。"}每组规则记录后，24 小时内不再重复记录。</p>
      ${state.vol === "high" ? `<p class="ms-rule-note ms-warn">当前结果另筛选“放量最强的一档”。相对各自平常成交量，BTC、ETH 都需达到：${state.side === "all" ? `上涨时至少 ${VOLUME_EDGES[`${state.tf}|1`][1]} 倍，下跌时至少 ${VOLUME_EDGES[`${state.tf}|-1`][1]} 倍` : `至少 ${VOLUME_EDGES[`${state.tf}|${state.side}`][1]} 倍`}。这是历史研究的分档，仍需后续验证。</p>` : ""}
      <div class="ms-plan"><span>研究基线 · 用来比较冲击后的延续</span><strong>${esc(tradePlan())} → 持有 ${holdLabel()} → 平仓</strong><small>结果已扣 0.2% 往返成本 · 仅作研究假设</small><p>信号收盘后才能确认，因此用下一根开盘统一比较。观察器发现信号时，开盘可能已经过去，这个价格不是可保证的成交价。默认的 12 小时来自事后研究，尚未证明是最佳退出。</p></div>
    </section>`;
  }

  function overview() {
    const forward = liveEvents().filter(isForward);
    const fstats = stats(forward.map(e => e.outcomes?.[state.hold]?.done ? e.outcomes[state.hold][state.inst] : null));
    const hs = state.vol === "all" ? stats(historyView().rows.map(r => r.net), historyView().rows.map(r => r.ctrl)) : null;
    const unavailable = state.vol === "high" ? "历史文件缺少逐条量比，无法统计这个筛选结果。" : state.history[groupKey()]?.error ? "历史结果读取失败，请稍后刷新。" : !state.history[groupKey()] ? "正在读取历史结果…" : "这组条件没有可用历史样本。";
    return `<section class="ms-results" aria-label="结果说明"><div class="ms-heading"><h2>以前这样做，结果怎样？</h2><span>2023 年 1 月至 2026 年 9 月 · 历史回算</span></div>
      ${hs ? `<div class="ms-overview">
        <article class="ms-primary-stat ${tone(hs.mean)}"><span>每次平均净收益</span><strong class="${tone(hs.mean)}">${returnPct(hs.mean)}</strong><small>中位数 ${returnPct(hs.median)}（排序后居中的结果）</small></article>
        <article><span>赚钱的比例</span><strong>${(hs.win * 100).toFixed(0)}%</strong><small>扣除成本后，收益大于 0 的比例</small></article>
        <article><span>历史出现次数</span><strong>${hs.n} 次</strong><small>符合当前所选条件的样本</small></article>
        <article><span>随机入场的平均净收益</span><strong class="${tone(hs.ctrl)}">${returnPct(hs.ctrl)}</strong><small>同月、相近波动环境作比较</small></article>
      </div>` : `<p class="notice">${unavailable}</p>`}
      <div class="ms-forward"><div><strong>上线后及时记录：${forward.length} 次</strong><span>${fstats ? `已持满 ${holdLabel()}：${fstats.n} 次 · 平均净收益 ${returnPct(fstats.mean)}` : "还没有完成观察的样本，尚不能验证实时效果。"}</span></div><p>历史结果不代表下一次收益。事后补算的记录不会计入这里。</p></div>
    </section>`;
  }

  function entryExitEvidence() {
    const h = state.history[groupKey()];
    if (!h || h.error) return "";
    const inst = h.instruments.indexOf(state.inst);
    const offsets = HOLDS.map(hold => h.exits.indexOf(`time_${hold}`) * h.instruments.length + inst);
    const supported = state.vol === "all" && inst >= 0 && HOLDS.every(hold => h.exits.includes(`time_${hold}`));
    // Compare holds on the exact same events, requiring both return and control
    // for every horizon. Missing paths must not give one hold an easier sample.
    const common = supported ? h.rows.filter(r => (state.side === "all" || String(r[1]) === state.side)
      && offsets.every(i => fin(r[5]?.[i]) && fin(r[6]?.[i]))) : [];
    const split = Date.UTC(2025, 0, 1), early = common.filter(r => r[0] < split), late = common.filter(r => r[0] >= split);
    const cell = (rows, idx) => { const s = stats(rows.map(r => r[5][idx]), rows.map(r => r[6][idx]));
      return s ? `<strong class="${tone(s.mean)}">${returnPct(s.mean)}</strong><small>随机 ${returnPct(s.ctrl)} · 差 ${returnPct(s.excess).replace("%", " 个百分点")}</small>` : "—"; };
    return `<section class="ms-section ms-evidence" aria-label="进出场依据"><div class="ms-heading"><h2>为什么持有这么久？</h2><span>同一批事件 · ${esc(INSTRUMENTS[state.inst])} · 已扣成本</span></div>
      ${common.length ? `<div class="v130-table-wrap"><table class="v130-table ms-table"><thead><tr><th>固定持有</th><th>2023–2024 · ${early.length} 次</th><th>2025 起 · ${late.length} 次</th></tr></thead><tbody>${HOLDS.map((hold, i) => `<tr${hold === state.hold ? ' class="ms-selected-row"' : ""}><td>${holdLabel(hold)}${hold === state.hold ? "<small>当前观察窗口</small>" : ""}</td><td>${cell(early, offsets[i])}</td><td>${cell(late, offsets[i])}</td></tr>`).join("")}</tbody></table></div><p class="quiet-text">仅比较四个持有时间都有完整收益及对照的 ${common.length} 次事件。后半段也已被研究看过；收益更高不等于统计上更好，也不代表实时可成交。</p>` : `<p class="quiet-text">当前筛选没有可比较的完整四时长样本，不能据此选择出场。</p>`}
      <details id="ms-exit-study" class="ms-method"><summary>跟踪止损、保本、分批退出，研究到了哪一步？</summary>${exitStudy(h.exit_study)}</details>
      <p class="ms-entry-status"><b>入场仍待验证：</b>当前只核算次开盘基线；回踩确认与延迟入场还没有对照结果，不能标为更好的进场方式。</p></section>`;
  }

  function exitStudy(study) {
    if (!study?.available) return '<p class="quiet-text">退出对比数据暂不可用，不能判断哪种方案更好。</p>';
    const set = study.event_set, same = state.kind === set.kind && state.config === `z${set.z_min.toFixed(1)}_v${set.v_min.toFixed(1)}_b${set.b_min}` && state.vol === "all";
    const labels = {hold_12h: "固定 12 小时", hold_24h: "固定 24 小时", trail3_24h: "3 ATR 跟踪 · 最长 24h", trail2_24h: "2 ATR 跟踪 · 最长 24h", trail3_48h: "3 ATR 跟踪 · 最长 48h", be_24h: "盈利后保本 · 最长 24h", tp2_trail3_24h: "2 ATR 减半，其余跟踪", split_4h_24h: "4h、24h 各退出一半"};
    const rows = study.rows.filter(r => String(r.minutes) === state.tf && String(r.side) === state.side && r.instrument === state.inst);
    const pairs = study.exits.map(exit => ({exit, a: rows.find(r => r.exit === exit && r.period === "select"), b: rows.find(r => r.exit === exit && r.period === "check")})).filter(r => r.a && r.b);
    const qualifies = pairs.filter(r => r.exit !== study.baseline && r.a.vs_base_bp > 0 && r.b.vs_base_bp > 0 && (r.a.p_vs_base < .05 || r.b.p_vs_base < .05));
    const cell = r => `<strong class="${tone(r.mean_net_bp)}">${returnPct(r.mean_net_bp)}</strong><small>随机 ${returnPct(r.control_net_bp)} · 相比随机 p=${num(r.p_excess, 4)}</small>${r.exit === study.baseline ? "<small>比较基线</small>" : `<small>较 12h ${returnPct(r.vs_base_bp).replace("%", " 个百分点")} · p=${num(r.p_vs_base, 4)}</small>`}`;
    return `<p class="quiet-text"><b>${same ? "这一研究使用当前信号门槛。" : "以下研究不对应当前筛选，不能直接用于当前信号。"}</b>原研究固定为单根触发：波动 ≥${set.z_min} 倍、量比 ≥${set.v_min} 倍、山寨同向 ≥${set.b_min * 100}%。每个退出方案及随机对照使用相同完整 48 小时路径，往返成本 ${returnPct(study.cost_bp).replace("+", "")}。</p>
      ${pairs.length ? `<p class="quiet-text">${qualifies.length ? "本组存在符合原研究比较条件的方案，仍需后续验证。" : "本组没有方案达到原研究的改善条件：前后两段均优于 12h，且至少一段 p＜0.05。"}</p><div class="v130-table-wrap"><table class="v130-table ms-table"><thead><tr><th>退出方案</th><th>2023–2024 · ${pairs[0].a.events} 次</th><th>2025 起 · ${pairs[0].b.events} 次</th></tr></thead><tbody>${pairs.map(r => `<tr><td>${esc(labels[r.exit] || r.exit)}</td><td>${cell(r.a)}</td><td>${cell(r.b)}</td></tr>`).join("")}</tbody></table></div>` : '<p class="quiet-text">请单独选择上涨或下跌方向，研究没有把两个方向合并比较。</p>'}
      <p class="quiet-text">退出表沿用 v5 固定种子 ${esc(study.control_seed)} 重新抽取同月、同波动档对照，因此随机收益与上方 v2 持有时长表不同；表内各退出方案共用同一组入场点。p 值衡量证据强度，越小越不容易由随机波动解释，不是胜率。</p><p class="quiet-text">跟踪和保本方案以 2 ATR 为初始止损；保本在收盘盈利达到 1 ATR 后启用。止损按收盘判断，不模拟盘中触价成交；固定持有与定时分批没有这道止损。来源 ${esc(study.experiment_id)} @ ${esc(study.source_commit.slice(0, 10))}。复核已看过的历史不算新的独立验证，不自动更换观察规则。</p>`;
  }

  function segmented(name, options, value) {
    const labels = {tf: "看多长的 K 线", kind: "触发方式", side: "观察方向", hold: "假设持有多久", inst: "观察哪个品种的收益", vol: "成交量筛选"};
    return `<div class="ms-control-group"><span>${labels[name]}</span><div class="segmented" role="group" aria-label="${labels[name]}">${options.map(([v, label]) => `<button type="button" data-ms="${esc(name)}" data-value="${esc(v)}" class="${v === value ? "selected" : ""}" aria-pressed="${v === value}">${esc(label)}</button>`).join("")}</div></div>`;
  }

  function controls() {
    const grid = state.live?.snapshot?.grid || ["z2.0_v2.0_b0.6", "z2.0_v2.0_b0.75", "z2.0_v3.0_b0.6", "z2.0_v3.0_b0.75", "z3.0_v2.0_b0.6", "z3.0_v2.0_b0.75", "z3.0_v3.0_b0.6", "z3.0_v3.0_b0.75"];
    return `<div class="ms-controls">
      ${segmented("tf", [["60", "1 小时"], ["30", "30 分钟"]], state.tf)}
      ${segmented("kind", [["sync", "一根满足就记录"], ["cluster", "三根内重复出现"]], state.kind)}
      ${segmented("side", [["1", "一起上涨"], ["-1", "一起下跌"], ["all", "涨跌都看"]], state.side)}
      ${segmented("hold", HOLDS.map(h => [h, holdLabel(h)]), state.hold)}
      ${segmented("inst", Object.entries(INSTRUMENTS), state.inst)}
      ${segmented("vol", [["all", "全部符合条件的事件"], ["high", "只看放量最强的一档"]], state.vol)}
      <label class="ms-select">触发门槛<select id="ms-config" aria-label="触发门槛">${grid.map(k => `<option value="${esc(k)}" ${k === state.config ? "selected" : ""}>${esc(configLabel(k))}</option>`).join("")}</select></label>
    </div>`;
  }

  function latestTable() {
    const rows = (state.live?.snapshot?.latest?.[state.tf] || []).slice(-12).reverse();
    const c = parseConfig(state.config);
    if (!rows.length) return `<div class="empty-state"><h3>还没有实时数据</h3><p>服务启动后会先回补 7 月底以来的 K 线，约需几分钟。</p></div>`;
    const hit = (ok) => ok ? "ms-hit" : "";
    return `<div class="v130-table-wrap"><table class="v130-table ms-table"><thead><tr><th>K线开盘 · 北京</th><th>BTC 涨跌 / 波动倍数 / 成交量倍数</th><th>ETH 涨跌 / 波动倍数 / 成交量倍数</th><th>山寨同涨 / 同跌</th><th>状态</th></tr></thead><tbody>${rows.map(r => {
      const side = Math.sign(r.btc_ret || 0), same = side !== 0 && side === Math.sign(r.eth_ret || 0);
      const zOk = fin(r.btc_z) && fin(r.eth_z) && Math.abs(r.btc_z) >= c.z && Math.abs(r.eth_z) >= c.z;
      const vOk = fin(r.btc_vr) && fin(r.eth_vr) && r.btc_vr >= c.v && r.eth_vr >= c.v;
      const wide = side > 0 ? r.breadth_up : r.breadth_down, bOk = fin(wide) && wide >= c.b;
      const met = [same && zOk, vOk, bOk].filter(Boolean).length;
      const decision = r.decisions?.[`${state.kind}|${state.config}`];
      const complete = [r.btc_ret, r.eth_ret, r.btc_z, r.eth_z, r.btc_vr, r.eth_vr, r.breadth_up, r.breadth_down].every(fin);
      const label = !complete ? "数据不足 · 无法判断" : decision === "event" ? (side > 0 ? "已记录 · 上涨" : "已记录 · 下跌")
        : decision === "cooldown" ? "条件满足 · 冷却中" : decision === "waiting_cluster" ? "单根满足 · 待重复"
        : !decision && same && zOk && vOk && bOk ? "条件满足 · 旧缓存未核对" : met >= 2 ? "接近阈值" : "未满足";
      return `<tr><td>${esc(clock(r.open_ms))}</td>
        <td class="${tone(r.btc_ret)}">${pct(r.btc_ret)}<small><span class="${hit(fin(r.btc_z) && Math.abs(r.btc_z) >= c.z)}">波动 ${magnitude(r.btc_z)} 倍</span> · <span class="${hit(fin(r.btc_vr) && r.btc_vr >= c.v)}">成交量 ${num(r.btc_vr, 1)} 倍</span></small></td>
        <td class="${tone(r.eth_ret)}">${pct(r.eth_ret)}<small><span class="${hit(fin(r.eth_z) && Math.abs(r.eth_z) >= c.z)}">波动 ${magnitude(r.eth_z)} 倍</span> · <span class="${hit(fin(r.eth_vr) && r.eth_vr >= c.v)}">成交量 ${num(r.eth_vr, 1)} 倍</span></small></td>
        <td><span class="${hit(side > 0 && fin(r.breadth_up) && r.breadth_up >= c.b)}">${share(r.breadth_up)}</span> / <span class="${hit(side < 0 && fin(r.breadth_down) && r.breadth_down >= c.b)}">${share(r.breadth_down)}</span><small>${esc(r.n_alts)} / ${esc(r.expected_alts ?? 50)} 个样本有数据</small></td>
        <td>${label.startsWith("已记录") ? `<strong class="ms-badge">${esc(label)}</strong>` : esc(label)}</td></tr>`;
    }).join("")}</tbody></table></div>`;
  }

  function forwardTable() {
    const events = liveEvents().slice().reverse();
    const research = new Set((state.live?.source?.id==='okx-usdt-swap'?[]:state.history[groupKey()]?.rows || []).map(r => `${r[0]}|${r[1]}`));
    const end = state.live?.snapshot?.research_end_ms;
    const inOverlap = (state.live?.source?.id==='okx-usdt-swap'?[]:state.history[groupKey()]?.rows || []).filter(r => r[0] >= (state.live?.snapshot?.observe_start_ms || Infinity) && r[0] < end
      && (state.side === "all" || String(r[1]) === state.side));
    const liveKeys = new Set(liveEvents().map(e => `${e.open_ms}|${e.side}`));
    const missing = inOverlap.filter(r => !liveKeys.has(`${r[0]}|${r[1]}`)).length;
    const overlap = events.filter(e => e.open_ms < end);
    const matched = overlap.filter(e => research.has(`${e.open_ms}|${e.side}`)).length;
    const vetoNote = `<p class="quiet-text">冲击前环境用冲击 K 线开盘前已收盘的 BTC 1H 数据。否决标签来自 v4（边界在 2025 年前冻结）：「大涨后·慎追」= 上涨冲击且 BTC 30 天涨幅在最高档（≥约 13%）；「高波动·慎空」= 下跌冲击且 BTC 30 天小时波动在最高档。只做标记，不剔除事件，用来积累样本外记录。</p>`;
    const tierNote = vetoNote + `<p class="quiet-text">量比档 = BTC、ETH 量比的较小值，按 v3 在 2025 年前事件上冻结的三档划分（30m 上涨高档 ≥${VOLUME_EDGES["30|1"][1]}×，1H 上涨高档 ≥${VOLUME_EDGES["60|1"][1]}×）。v3 里只有 30m 上涨冲击的高量比档在 2025 前后都更好，属于事后发现，这里用来积累样本外记录。</p>`;
    const note = tierNote + (overlap.length || inOverlap.length
      ? `<p class="quiet-text">8/1–9/22 与研究重叠：观察器回算 ${overlap.length} 次，其中 ${matched} 次研究里也有；研究有而观察器没有 ${missing} 次。山寨池按 K 线成交额排名、只含仍在交易的合约，个别边缘事件可能不同。</p>` : "");
    if (!events.length) return `<div class="empty-state"><h3>暂无符合条件的事件</h3><p>当前来源独立记录；换个阈值或方向看看。</p></div>${note}`;
    const outcome = (e, h) => {
      const o = e.outcomes?.[h] || {}, v = o[state.inst];
      return `<span class="${tone(v)}">${o.started ? returnPct(v) : "待入场"}</span>${o.started && !o.done ? "<small>持有中 · 暂时收益</small>" : ""}`;
    };
    return `<div class="v130-table-wrap"><table class="v130-table ms-table ms-events"><thead><tr><th>发生时间 · 北京</th><th>当时发生了什么</th><th class="ms-focus">${esc(INSTRUMENTS[state.inst])} · ${holdLabel()}净收益</th><th>此前市场环境</th><th>记录方式</th></tr></thead><tbody>${events.map(e => {
      const source = isForward(e) ? `<strong class="ms-badge">当时及时记录</strong>` : research.has(`${e.open_ms}|${e.side}`) ? "历史回算<small>与研究记录一致</small>" : "事后补算<small>不计入实时验证</small>";
      return `<tr><td>${esc(clock(e.open_ms))}<small>${e.side > 0 ? "一起上涨 → 假设做多" : "一起下跌 → 假设做空"}</small></td>
        <td>BTC ${pct(e.btc_ret)} · ETH ${pct(e.eth_ret)}<small>${share(e.breadth)} 山寨一起${e.side > 0 ? "涨" : "跌"}</small><details id="ms-event-${esc(e.open_ms)}-${esc(e.side)}-metrics"><summary>查看触发指标</summary><small>波动为平常的 ${magnitude(e.btc_z)} / ${magnitude(e.eth_z)} 倍<br>成交量为平常的 ${num(e.btc_vr, 1)} / ${num(e.eth_vr, 1)} 倍（BTC / ETH）${volumeTier(e) == null ? "" : `<br>成交量分档：${TIER_NAMES[volumeTier(e)]}`}</small></details></td>
        <td class="ms-focus"><strong class="ms-event-return">${outcome(e, state.hold)}</strong><details id="ms-event-${esc(e.open_ms)}-${esc(e.side)}-returns"><summary>其他持有时间</summary>${HOLDS.filter(h => h !== state.hold).map(h => `<div class="ms-other-return">${holdLabel(h)}：${outcome(e, h)}</div>`).join("")}</details></td>
        <td>${fin(e.env?.trend30) ? `BTC 之前 30 天 ${pct(e.env.trend30, 1)}` : "—"}${(e.vetoes || []).map(v => `<small><span class="ms-badge ms-badge-warn">${esc(VETO_NAMES[v] || v)}</span></small>`).join("")}</td><td>${source}</td></tr>`;
    }).join("")}</tbody></table></div><details class="ms-method" id="ms-environment"><summary>查看环境标签、成交量分档与核对说明</summary>${note}</details>`;
  }

  function terminalRecords() {
    const rows=liveEvents().slice().sort((a,b)=>b.open_ms-a.open_ms).slice(0,8);
    return `<div class="mt-record-scroll" tabindex="0" aria-label="最近共振记录，可横向滚动"><table class="mt-record-table"><thead><tr><th>时间 · 北京</th><th>观察对象</th><th>方向</th><th>同向比例</th><th>记录类型</th><th>说明</th></tr></thead><tbody>${rows.map(e=>`<tr><td>${esc(clock(e.open_ms,true))}</td><td>BTC · ETH</td><td class="${e.side>0?'up':'down'}">${e.side>0?'上涨':'下跌'}</td><td>${share(e.breadth)}</td><td>${isForward(e)?'上线后及时记录':'事后补算'}</td><td>BTC ${pct(e.btc_ret)} · ETH ${pct(e.eth_ret)}；${share(e.breadth)} 山寨同向${e.side>0?'上涨':'下跌'}。</td></tr>`).join('')||'<tr><td colspan="6">当前条件下暂无共振记录，继续等待已收盘行情。</td></tr>'}</tbody></table></div><details class="mt-record-details" id="ms-event-outcomes"><summary>收益与原始触发详情 · 已扣 0.2% 成本</summary>${forwardTable()}</details><div class="mt-record-footer"><span>数据来源　${esc(state.live?.source?.label||'OKX')} · 研究观察</span><span>最近收盘　${clock(state.live?.snapshot?.latest?.[state.tf]?.at(-1)?.close_ms,true)}　· 共 ${liveEvents().length} 条记录</span></div>`;
  }

  function historySection() {
    if (state.vol === "high") return `<p class="notice">历史文件没有逐条量比，无法可靠筛选高量比子集。请切回「全部符合条件的事件」查看研究统计。</p>`;
    const {rows, h} = historyView();
    if (!h) return `<p class="research-empty">正在读取研究事件…</p>`;
    if (h.error) return `<div class="notice" role="alert">${esc(h.error)}</div>`;
    const s = stats(rows.map(r => r.net), rows.map(r => r.ctrl));
    const years = {};
    rows.forEach(r => { const y = new Date(r.time).getUTCFullYear(); (years[y] ||= []).push(r.net); });
    const list = rows.slice().reverse().slice(0, state.limit);
    return `${s ? `<div class="ms-stats">
        <article><span>事件数</span><strong>${s.n}</strong></article>
        <article><span>平均 / 中位</span><strong class="${tone(s.mean)}">${returnPct(s.mean)}</strong><small>中位 ${returnPct(s.median)}</small></article>
        <article><span>胜率</span><strong>${(s.win * 100).toFixed(0)}%</strong></article>
        <article><span>随机入场 / 收益差</span><strong>${returnPct(s.ctrl)}</strong><small class="${tone(s.excess)}">比随机入场${s.excess >= 0 ? "多" : "少"} ${fin(s.excess) ? (Math.abs(s.excess) / 100).toFixed(3) : "—"} 个百分点</small></article>
        <article><span>去掉最好 5 次</span><strong class="${tone(s.trimmed)}">${returnPct(s.trimmed)}</strong></article>
      </div>
      <div class="ms-years">${Object.entries(years).map(([y, xs]) => { const t = stats(xs); return `<span><b>${y}</b> ${t.n} 次 · <i class="${tone(t.mean)}">${returnPct(t.mean)}</i> · 胜率 ${(t.win * 100).toFixed(0)}%</span>`; }).join("")}</div>` : `<div class="empty-state"><h3>该组合没有研究事件</h3></div>`}
      ${list.length ? `<div class="v130-table-wrap"><table class="v130-table ms-table"><thead><tr><th>信号K线 · 北京</th><th>方向</th><th>BTC / ETH 波动倍数</th><th>同向</th><th class="ms-focus">${holdLabel()} · ${esc(INSTRUMENTS[state.inst])}净收益</th><th>随机入场净收益</th></tr></thead><tbody>${list.map(r => `<tr><td>${esc(clock(r.time, true))}</td><td>${esc(sideName(r.side))}</td><td>${magnitude(r.btc_z)} / ${magnitude(r.eth_z)}</td><td>${share(r.breadth)}</td><td class="ms-focus ${tone(r.net)}">${returnPct(r.net)}</td><td class="${tone(r.ctrl)}">${returnPct(r.ctrl)}</td></tr>`).join("")}</tbody></table></div>
      ${rows.length > state.limit ? `<button type="button" class="load-more ms-more" id="ms-more">显示更多（共 ${rows.length} 次）</button>` : ""}` : ""}
      ${state.vol === "high" ? `<p class="quiet-text">研究历史没有逐条量比，这里仍显示全部事件。30m 上涨冲击高量比档的研究结果：ETH 持有 24 小时，扣费后 2025 前 +0.75%、2025 后 +1.02%（v3）。</p>` : ""}
      <p class="quiet-text">来源 ${esc(h.experiment_id)} @ ${esc(String(h.source_commit).slice(0, 10))}。对照为同月、同 BTC 波动档的 20 个随机入场，方向与持有相同。这些规律是看过数据后找到的，要靠样本外记录验证。</p>`;
  }

  function iterationsSection() {
    const items = state.iterations;
    if (!items) return `<p class="research-empty">${esc(state.iterationsError || "正在读取实验登记…")}</p>`;
    const status = {superseded: "已被取代", inconclusive: "不确定", running: "进行中", done: "完成", rejected: "否决"};
    return `<ol class="ms-iterations">${items.map(it => `<li><div><strong>${esc(it.experiment_id)}</strong><span class="ms-badge">${esc(status[it.status] || it.status)}</span></div>
      <p>${esc(it.single_variable || it.question)}</p>
      <details id="ms-iteration-${esc(it.experiment_id)}"><summary>结论</summary><p>${esc(returnText(it.result))}</p>${it.notes ? `<p class="quiet-text">${esc(returnText(it.notes))}</p>` : ""}</details></li>`).join("")}</ol>
      <p class="quiet-text">下一步与分工在 <a href="${NOTION_TRACKER}" target="_blank" rel="noopener noreferrer">Notion 任务跟踪器 ↗</a>。</p>`;
  }

  // Preserve unchanged regions, including focus, open disclosures and chart DOM.
  const renderedRegions=new Map(), boundEvents=new WeakMap();
  function listen(el,type,handler){
    if(!el)return;let events=boundEvents.get(el);if(!events){events=new Set();boundEvents.set(el,events);}
    if(!events.has(type)){events.add(type);el.addEventListener(type,handler);}
  }
  const regionMarkup=new Map(),regionTemplates=new Map();
  function region(id,pane,build){if(!window.SpikeMarketTerminal||state.panel===pane)regionMarkup.set(id,build());return regionMarkup.get(id)||'';}
  function patchRegions(fragments){
    const host=root();if(!Array.isArray(fragments))fragments=[fragments];
    if(!document.createElement){host.innerHTML=fragments.join('');return;}
    const next=new Map();let index=0;
    fragments.forEach((markup,slot)=>{
      let cached=regionTemplates.get(slot);
      if(!cached||cached.markup!==markup){const template=document.createElement('template');template.innerHTML=markup;cached={markup,nodes:[...template.content.children].map(node=>({node,text:node.outerHTML}))};regionTemplates.set(slot,cached);}
      for(const {node,text} of cached.nodes){
        const key=node.id||node.tagName+':'+node.className,previous=renderedRegions.get(key);
        const element=previous?.markup===text&&previous.node.parentNode===host?previous.node:node;
        if(host.children[index]!==element)host.insertBefore(element,host.children[index]||null);
        next.set(key,{node:element,markup:text});index++;
      }
    });
    const kept=new Set([...next.values()].map(v=>v.node));for(const node of [...host.children])if(!kept.has(node))node.remove();
    renderedRegions.clear();for(const [k,v] of next)renderedRegions.set(k,v);
  }

  function render() {
    if (!state.active || !root()) return;
    if (state.error && !state.live && !window.SpikeMarketTerminal) { root().innerHTML = `<div class="notice" role="alert">${esc(state.error)}；稍后重试。</div>`; return; }
    // Keep disclosures open across filter changes and the periodic refresh.
    window.SpikeNavigation?.sync('market',{tab:state.panel});
    const openDetails = new Set([...root().querySelectorAll("details[id][open]")].map(d => d.id));
    patchRegions([
      // Keep this shell constant: changing market values must not remount the account controls.
      '<div id="market-overview-strip"><section id="market-resonance-overview" aria-label="行情共振监控"></section><section id="market-account-overview" aria-label="实盘账户统计"></section></div>',
      `${state.error ? `<p class="notice" role="alert">${esc(state.error)}</p>` : ""}${window.SpikeMarketTerminal?'<div id="ms-terminal-top"></div>':alertMarkup()+feedStatus()+contextMarkup()}<nav class="ms-terminal-tabs" aria-label="市场观察内容">${[["overview","共振记录"],["notes","观察笔记"]].map(([value,label])=>`<button type="button" data-ms-panel="${value}" aria-pressed="${state.panel===value}">${label}</button>`).join("")}<details id="ms-more-menu" class="quiet-menu"><summary>${state.panel==="history"?"历史事件":state.panel==="settings"?"观察设置":"更多"}</summary><div class="quiet-menu-panel">${[["history","历史事件"],["settings","观察设置"]].map(([value,label])=>`<button type="button" data-ms-panel="${value}" aria-pressed="${state.panel===value}">${label}</button>`).join("")}</div></details><label class="ms-record-filter" ${state.panel!=="overview"?"hidden":""}>时间范围<select id="ms-event-days" aria-label="记录时间范围">${[["all","全部"],["7","近 7 天"],["30","近 30 天"]].map(([v,l])=>`<option value="${v}" ${state.eventDays===v?"selected":""}>${l}</option>`).join('')}</select></label></nav>`,
      region('1','settings',()=>`<details class="ms-section panel" id="ms-rule-guide"><summary>怎么用：共振条件与观察步骤</summary>${ruleGuide()}</details>`),
      region('2','history',()=>`<details class="ms-section panel" id="ms-research-baseline"><summary>历史研究依据 · 币安数据，与当前 OKX 观察分开</summary>${overview()}${entryExitEvidence()}</details>`),
      region('3','settings',()=>`<details class="ms-section panel" id="ms-filters"><summary>调整观察条件 <span class="ms-summary-hint">${tfLabel(state.tf)} · ${moveName()} · ${esc(INSTRUMENTS[state.inst])} · ${holdLabel()}</span></summary>${controls()}</details>`),
      region('4','overview',()=>`<section class="ms-section" id="ms-recent-events" aria-label="最近共振">${terminalRecords()}</section>`),
      region('5','notes',()=>`<section class="ms-section" id="ms-terminal-notes" aria-label="观察笔记"></section>`),
      region('6','settings',()=>`<details class="ms-section panel" id="ms-latest"><summary>最近 12 根 ${tfLabel(state.tf)} K 线 <span class="ms-summary-hint">${state.live?.freshness?.[state.tf]?.stale !== false ? "历史缓存 · 非当前行情" : "查看逐根条件"}</span></summary>${envNow()}${latestTable()}</details>`),
      region('7','history',()=>`<details class="ms-section panel" id="ms-history"><summary>完整历史结果 · 按年份和事件查看</summary>${historySection()}</details>`),
      region('8','settings',()=>`<details class="ms-section panel" id="ms-method"><summary>这些数字怎么算？</summary><div class="ms-method-copy"><p><b>平常波动：</b>用此前 96 根 K 线涨跌幅的均方根计算。当前涨跌幅除以这个值，得到页面的“波动倍数”（原指标 z）。3 倍不等于涨跌了 3%。</p><p><b>平常成交量：</b>此前 96 根 K 线成交量的中位数。当前成交量除以它，得到成交量倍数。</p><p><b>山寨样本：</b>上月成交额排名前 50 的山寨合约。同涨或同跌比例按当根有数据的样本计算；并非全部市场，也不能证明谁带动谁。</p><p><b>历史回算与实时验证：</b>历史回算是事后用旧行情计算；只有规则启用后、信号收盘 35 分钟内首次发现的事件才计入“上线后及时记录”。后来补算的事件不会混进去。</p><p><b>收益与对照：</b>用下一根开盘价入场，按所选时长退出，扣 0.2% 往返成本。同月、同 BTC 波动档随机挑选 20 个入场点，用相同方向、品种和持有时长作比较。这里展示单次收益，不是累计收益或账户收益。</p><p>当前行情来源：${esc(state.live?.source?.label||"OKX USDT 永续")}已收盘 K 线。原历史研究仍为币安数据，两者不混算；旧研究的量比档位与环境标签尚未在 OKX 单独验证。这里只做页面观察与提示音，不下单。这些规律来自历史研究，仍需后续及时记录的样本验证。</p></div></details>`),
      region('9','history',()=>`<details class="ms-section panel" id="ms-iterations"><summary>研究过程与已知限制</summary>${iterationsSection()}</details>`)
    ]);
    window.SpikeMarketAccounts?.mount();
    renderResonance();
    window.SpikeMarketTerminal?.update({live:state.live,context:contextData,error:state.error,zone:chartView.zone,alert:currentAlert(),alertPeriod:tfLabel(state.tf),soundEnabled,
      toggleSound(){soundEnabled=!soundEnabled;try{localStorage.setItem('spike-market-sound',String(soundEnabled));}catch(_){}if(soundEnabled)beep();render();},
      setZone(zone){chartView.zone=zone;contextData=null;try{localStorage.setItem('spike-market-zone',zone);}catch(_){}loadContext();render();}});
    const panes={overview:['ms-recent-events'],notes:['ms-terminal-notes'],history:['ms-research-baseline','ms-history','ms-iterations'],settings:['ms-filters','ms-rule-guide','ms-method','ms-latest']};
    for(const [pane,ids] of Object.entries(panes))for(const id of ids){const node=document.getElementById(id);if(node)node.hidden=state.panel!==pane;}
    root().querySelectorAll('[data-ms-panel]').forEach(button=>listen(button,'click',()=>{
      const changed=state.panel!==button.dataset.msPanel;
      state.panel=button.dataset.msPanel;render();loadPanelData();window.SpikeNavigation?.sync('market',{tab:state.panel});
      const selectedTab=root().querySelector(`[data-ms-panel="${state.panel}"]`),menu=selectedTab?.closest('details.quiet-menu');
      if(menu){menu.open=false;menu.querySelector('summary')?.focus({preventScroll:true});}
      else selectedTab?.focus({preventScroll:true});
      if(changed)for(const id of panes[state.panel])window.SpikeMotion?.enter(document.getElementById(id));
    }));
    listen(document.getElementById('ms-event-days'),'change',e=>{state.eventDays=e.target.value;render();});
    listen(document.getElementById('ms-chart-zone'),'change',e=>{chartView.zone=e.target.value;contextData=null;try{localStorage.setItem('spike-market-zone',chartView.zone);}catch(_){}loadContext();render();});
    root().querySelectorAll('[data-ms-chart]').forEach(b=>listen(b,'click',()=>{chartView[b.dataset.msChart]=!chartView[b.dataset.msChart];render();}));
    listen(root().querySelector?.('[data-ms-range]'),'click',()=>{chartView.bars=chartView.bars===192?672:192;render();});
    listen(root().querySelector?.('[data-ms-sound]'),'click',()=>{soundEnabled=!soundEnabled;try{localStorage.setItem('spike-market-sound',String(soundEnabled));}catch(_){}if(soundEnabled)beep();render();});
    openDetails.forEach(id => { const d = document.getElementById(id); if (d) d.open = true; });
    root().querySelectorAll("[data-ms]").forEach(b => listen(b,"click", () => { state[b.dataset.ms] = b.dataset.value; state.limit = 30; render(); loadHistory(); }));
    listen(document.getElementById("ms-config"),"change", e => { state.config = e.target.value; state.limit = 30; render(); loadHistory(); });
    listen(document.getElementById("ms-more"),"click", () => { state.limit += 50; render(); });
  }

  const requests=window.SpikeRuntime?.createRequests();
  async function getJSON(url) {
    if(requests)return requests.json(url);
    const response = await fetch(url, {cache: "no-store"});
    if (!response.ok) throw new Error(`读取失败 ${response.status}`);
    return response.json();
  }

  async function loadHistory(paint=true) {
    const key = groupKey(),epoch=activationVersion;
    if (state.history[key] && !state.history[key].error) return;
    state.history[key] = null;
    try { const data=await getJSON(`/api/market-sync/history?group=${encodeURIComponent(key)}`);if(epoch===activationVersion)state.history[key]=data; }
    catch (error) { if(epoch!==activationVersion)return;if(error.name==='AbortError'){delete state.history[key];return;}state.history[key] = {error: `研究事件${error.message}`}; }
    if (state.active && paint) render();
  }

  let panelPending=false;
  async function loadPanelData(){
    if(!state.active||window.SpikeMarketTerminal&&state.panel!=='history'||panelPending)return;
    panelPending=true;const epoch=activationVersion;
    try{await Promise.all([loadHistory(false),(async()=>{if(!state.iterations){try{const data=await getJSON('/api/market-sync/iterations');if(epoch===activationVersion){state.iterations=data.items;state.iterationsError=null;}}catch{if(epoch===activationVersion)state.iterationsError='研究登记暂不可用；行情与事件仍可独立查看。';}}})()]);}
    finally{panelPending=false;if(state.active){if(epoch===activationVersion)render();else loadPanelData();}}
  }
  async function refresh() {
    if (!state.active || state.pending || document.hidden) return;
    state.pending = true;const epoch=activationVersion;
    // The terminal and chart can start before optional context/history arrives.
    render();loadContext();loadPanelData();
    try {const live=await getJSON('/api/market-sync');if(epoch!==activationVersion)return;state.live=live;state.error=null;}
    catch(error){if(epoch===activationVersion)state.error=`行情读取失败：${error.message}`;}
    finally {state.pending=false;if(epoch!==activationVersion&&state.active)Promise.resolve().then(refresh);}
    if(!state.active||epoch!==activationVersion)return;
    checkAlertSound();render();
  }

  const idle=window.SpikeRuntime?.idleRelease(()=>{if(state.active)return;root()?.replaceChildren();renderedRegions.clear();regionTemplates.clear();regionMarkup.clear();contextData=null;state.live=null;state.history={};state.iterations=null;audioContext?.close().catch(()=>{});audioContext=null;});
  window.SpikeNavigation?.register('market',params=>{state.panel=['overview','history','settings','notes'].includes(params.tab)?params.tab:'overview';render();loadPanelData();});
  window.SpikeMarketSync = {refresh, setActive(value) { if(state.active!==value)activationVersion++;state.active = value;window.SpikeMarketAccounts?.setActive(value);window.SpikeMarketTerminal?.setActive(value);idle?.setActive(value);if(value)refresh();else requests?.cancel(); }};
})();
