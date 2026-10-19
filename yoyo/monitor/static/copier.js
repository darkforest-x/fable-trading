/* Focused, read-only Arthur monitoring and paper ledger. Real execution controls
   remain outside this view; all displayed statistics belong to the selected channel. */
(() => {
  "use strict";
  const CHANNEL = "1131521990814089276";
  const BASE = "/api/copier/";
  const state = {active:false, tab:"overview", focus:null, paper:null, messages:[], total:0,
    page:1, intent:"", status:"", pending:false, error:"", expanded:new Set(), timer:null};
  const root = () => document.getElementById("copier-workspace");
  const esc = v => String(v ?? "—").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
  const amount = (v,signed=false) => v == null || !Number.isFinite(Number(v)) ? "—" : `${signed && Number(v)>0?"+":""}${Number(v).toLocaleString("zh-CN",{minimumFractionDigits:2,maximumFractionDigits:2})}`;
  const count = v => v == null ? "—" : Number(v).toLocaleString("zh-CN");
  const pct = v => v == null ? "—" : `${(v*100).toFixed(1)}%`;
  const rText = v => v == null ? "—" : `${amount(v,true)}R`;
  const signedCls = v => v>0?"pos":v<0?"neg":"";
  const clock = v => { if(!v)return "—";const d=new Date(v);return Number.isFinite(+d)?new Intl.DateTimeFormat("zh-CN",{timeZone:"Asia/Shanghai",year:"numeric",month:"2-digit",day:"2-digit",hour:"2-digit",minute:"2-digit",hour12:false}).format(d):"—"; };
  const sidePill = v => `<span class="copier-side ${v==='short'?'short':'long'}">${v==='short'?'空':'多'}</span>`;
  const INTENTS = {open:"开仓",close:"平仓",partial_close:"部分平仓",cancel:"撤单",update:"更新",update_sl:"移止损",update_tp:"改止盈",hold:"观察",noise:"非信号"};
  const STATUSES = {executed:"已处理",skipped:"已跳过",error:"处理失败",pending:"等待处理",parsed:"已识别"};
  const EVENTS = {risk_rejected:"风控拦截",execution_skipped:"跳过执行",recovery_signal_skipped:"历史补录",execution_error:"执行异常",message_revision:"原文修订"};
  const reasonText = v => v === "offline recovery messages are never executed" ? "历史补录不执行交易" : v;
  const CLOSE_REASONS = {stop_loss:"止损",take_profit:"止盈",liquidation:"强平近似",manual_close:"博主平仓",partial_close:"部分平仓",reversed:"反手平仓"};
  const parse = v => {try{return JSON.parse(v||"{}")}catch{return {}}};
  const safeLink = v => {try{const u=new URL(v);return u.protocol==='https:'?u.href:""}catch{return ""}};
  async function api(path) {const r=await fetch(BASE+path,{cache:"no-store"});if(!r.ok)throw Error(`读取失败 HTTP ${r.status}`);return r.json();}
  function table(headers,rows,empty) {return `<div class="copier-table-wrap"><table class="copier-table"><thead><tr>${headers.map(h=>`<th>${h}</th>`).join('')}</tr></thead><tbody>${rows||`<tr><td colspan="${headers.length}" class="copier-empty">${empty}</td></tr>`}</tbody></table></div>`;}
  function metrics(items){return `<div class="copier-kpis">${items.map(([label,value,note])=>`<article><span>${label}</span><strong>${value}</strong>${note?`<small>${note}</small>`:""}</article>`).join('')}</div>`;}
  function rStatsPanel(s, title) {
    if (!s || !s.trades) return `<section class="copier-panel"><h3>${title}</h3><p class="copier-empty">${s?.closed_total?'已平仓记录缺少可核验的初始风险，R 保留未知。':'还没有已平仓的模拟交易，R 统计会在第一笔平仓后出现。'}</p></section>`;
    const facts = [["已平仓", count(s.trades)], ["胜率", pct(s.win_rate)], ["平均每笔", rText(s.mean_r)], ["中位数", rText(s.median_r)],
      ["累计", rText(s.sum_r)], ["平均盈利", rText(s.avg_win_r)], ["平均亏损", rText(s.avg_loss_r)],
      ["盈亏比(R)", s.profit_factor_r == null ? "—" : Number(s.profit_factor_r).toFixed(2)], ["最佳 / 最差", `${rText(s.best_r)} / ${rText(s.worst_r)}`],
      ["最大连亏", `${count(s.max_losing_streak)} 笔`], ["R 回撤", rText(s.max_drawdown_r == null ? null : -s.max_drawdown_r)],
      ["平均 1R", `${amount(s.avg_risk_usdt)} U（${pct(s.avg_risk_pct)} 起始资金）${s.legacy_trades ? ` · 不含 ${count(s.legacy_trades)} 笔旧口径` : ""}`]];
    const total = Object.values(s.buckets || {}).reduce((a, b) => a + b, 0) || 1;
    const bars = Object.entries(s.buckets || {}).map(([label, n]) => `<div class="copier-rbar"><span>${esc(label)}</span>
      <i style="width:${Math.max(2, n / total * 100)}%" class="${label.startsWith("-") || label.startsWith("≤") ? "neg" : "pos"}"></i><b>${n}</b></div>`).join("");
    return `<section class="copier-panel"><h3>${title}</h3><dl class="copier-rfacts">${facts.map(([k, v]) => `<div><dt>${k}</dt><dd>${esc(v)}</dd></div>`).join("")}</dl>
      <div class="copier-rdist">${bars}</div></section>`;
  }

  function paperSections(open, pending, closed) {
    const openRows = open.map((o) => `<tr><td>${esc(clock(o.opened_at))}</td><td>${esc(o.trader)}</td><td>${esc(o.inst_id)}</td><td>${sidePill(o.side)}</td>
      <td class="num">${esc(o.entry_px)}<small>${o.leverage}x · ${amount(o.notional_usdt)} U</small></td><td class="num">${o.mark == null ? "—" : esc(o.mark)}</td>
      <td class="num">${esc(o.sl)}<small>强平 ${Number(o.liquidation_px).toPrecision(6)}</small></td><td class="num">${esc(o.tp)}</td>
      <td class="num">${amount(o.risk_usdt)}<small>${pct(o.risk_pct_of_equity)} 账户 · 止损距 ${pct(o.stop_distance_pct)}${o.sizing === "legacy_margin" ? " · 旧口径" : ""}</small></td>
      <td class="num ${signedCls(o.unrealized_pnl)}">${amount(o.unrealized_pnl, true)}<small>${rText(o.current_r)}</small></td></tr>`).join("");
    const pendingRows = pending.map((o) => `<tr><td>${esc(clock(o.created_at))}</td><td>${esc(o.trader)}</td><td>${esc(o.inst_id)}</td><td>${sidePill(o.side)}</td>
      <td class="num">${esc(o.px)}</td><td class="num">${o.mark == null ? "—" : esc(o.mark)}</td><td class="num">${esc(o.sl)}</td><td class="num">${esc(o.tp)}</td>
      <td class="num">${amount(o.notional_usdt)}</td></tr>`).join("");
    const closedRows = closed.map((o) => `<tr><td>${esc(clock(o.closed_at))}</td><td>${esc(o.trader)}</td><td>${esc(o.inst_id)}</td><td>${sidePill(o.side)}</td>
      <td class="num">${esc(o.entry_px)} → ${o.exit_px == null ? "—" : Number(o.exit_px).toPrecision(6)}</td><td>${esc(CLOSE_REASONS[o.close_reason] || o.close_reason)}</td>
      <td class="num ${signedCls(o.r)}">${rText(o.r)}<small>1R = ${amount(o.risk_usdt)} U${o.sizing === "legacy_margin" ? " · 旧口径" : ""}</small></td><td class="num ${signedCls(o.net_pnl)}">${amount(o.net_pnl, true)}</td></tr>`).join("");
    return `<section class="copier-panel"><h3>模拟持仓 (${open.length})</h3>${table(["开仓 · 北京", "交易者", "品种", "方向", "入场", "现价", "止损", "止盈", "1R (USDT)", "浮动盈亏 / 累计净R"], openRows, "暂无模拟持仓")}</section>
      <section class="copier-panel"><h3>模拟挂单 (${pending.length})</h3>${table(["下单 · 北京", "交易者", "品种", "方向", "挂单价", "现价", "止损", "止盈", "名义 (USDT)"], pendingRows, "暂无模拟挂单")}</section>
      <section class="copier-panel"><h3>已平仓 (${closed.length})</h3>${table(["平仓 · 北京", "交易者", "品种", "方向", "入场 → 出场", "原因", "R", "净盈亏"], closedRows, "暂无已平仓的模拟交易")}</section>`;
  }

  function overview(){
    const f=state.focus,p=state.paper,s=p?.r_stats_all||{},a=p?.accounts?.[0];
    if(!f)return '<p class="copier-empty">正在读取频道…</p>';
    return `<div class="copier-overview-board">${!f.collector_online?`<div class="copier-collection-alert" role="status"><strong>采集中断 · 服务在线，未收到频道心跳</strong><p>最后入库：${clock(f.last_received?.created_at)}。请在 Chrome 保持 Arthur 频道打开并启用桥接扩展。</p><a href="${esc(f.url)}" target="_blank" rel="noopener noreferrer">打开 Arthur 频道 →</a><span>当前仅本地模拟 · 已识别消息不等于成交</span></div>`:''}${metrics([["已收录消息",count(f.total),"包含历史补录"],["已识别开仓",count(f.intents?.open||0),"识别不等于成交"],["模拟已平仓",count(s.closed_total||0),"挂单、撤单不计胜负"],["模拟净胜率",pct(s.net_win_rate),`${count(s.net_wins||0)} 盈 / ${count(s.net_losses||0)} 亏 / ${count(s.net_breakeven||0)} 平`],["累计净 R",rText(s.sum_r),`扣费用 · ${s.unknown_r||0} 笔风险未知`],["模拟权益",amount(a?.total_usdt),"USDT · 含浮动盈亏"]])}
      <div class="copier-focus-grid"><section class="copier-panel"><h3>采集是否正常</h3><dl class="copier-rfacts">
      <div><dt>频道范围</dt><dd>仅 Arthur 行情分析</dd></div><div><dt>采集器心跳</dt><dd>${f.collector_online?'在线':'尚未确认在线'}</dd></div>
      <div><dt>最近心跳 · 北京</dt><dd>${clock(f.heartbeat?.received_ms)}</dd></div><div><dt>最后入库 · 北京</dt><dd>${clock(f.last_received?.created_at)}</dd></div></dl>
      <p class="copier-footnote">浏览器扩展只能读取已加载的频道消息。心跳证明页面连接，不能证明历史消息完整；翻到旧消息时，新消息可能不在可见范围。</p>
      ${!f.collector_online?'<p class="notice">未收到最近两分钟的采集心跳。请保持指定 Discord 频道打开，并在扩展管理中重新加载桥接扩展；不能仅凭“监听已开启”判定采集正常。</p>':''}</section>
      <section class="copier-panel"><h3>博主实际业绩</h3><div class="copier-author-result"><strong>待核验</strong><span>胜率 —　累计 R —</span></div><p>${esc(f.author_performance?.reason)}</p><p class="copier-footnote">本页上方是本地模拟账本。博主原文里的目标价、保本止损、已处理状态，都不能代替完整成交凭证。</p><button class="copier-button" data-tab="signals">逐条核对消息 →</button></section></div>
      <section class="copier-panel"><h3>处理与跳过原因</h3>${table(['类别','具体原因','消息数'],(f.reasons||[]).map(r=>`<tr><td>${esc(EVENTS[r.event]||r.event)}</td><td>${esc(reasonText(r.detail))}</td><td>${r.n}</td></tr>`).join(''),'尚无异常或跳过记录')}<p class="copier-footnote">本表包含该频道过去的处理记录，不代表当前仍有这些问题。其他博主已退出当前监控和统计；历史账本保留。</p></section></div>`;
  }
  function paper(){const p=state.paper;if(!p)return '<p class="copier-empty">正在读取模拟账本…</p>';
    const canceled=(p.canceled||[]).map(o=>`<tr><td>${clock(o.updated_at)}</td><td>${esc(o.inst_id)}</td><td>${sidePill(o.side)}</td><td>${esc(o.px)}</td><td>已撤单 · 不计收益</td></tr>`).join('');
    return `${rStatsPanel(p.r_stats_all,"Arthur · 全量已平仓净 R")}
      <p class="copier-footnote">统计覆盖全部已平仓记录；明细最多显示最近 100 笔。缺少初始风险的 ${p.r_stats_all?.unknown_r||0} 笔不计 R 指标；概览净胜率按全部已平仓净盈亏计算，平局单列。</p>
      ${paperSections(p.open,p.pending,p.closed)}
      <section class="copier-panel"><h3>已撤模拟挂单</h3>${table(['撤单 · 北京','品种','方向','原挂单价','状态'],canceled,'暂无撤单')}</section>
      <details class="copier-panel"><summary>模拟成交与统计规则</summary><p>${esc(p.sizing_note)}</p><p>1 分钟已收盘 K 线加每 ${p.poll_seconds} 秒报价；同根止盈止损均触及按止损。限价补成交也检查该根止损；不能确认高低点先后时，不把可能发生在入场前的止盈计为盈利。</p><p>费用单边 ${pct(p.fee_rate_per_side)}；不计资金费与盘口冲击。Gate 备用报价不具备分钟线过程验证。睡眠、断网期间的缺失行情无法保证复原。模拟不是博主真实成交。</p></details>`;
  }
  function signals(){
    const rows=state.messages.map(m=>{const ai=parse(m.ai_json),audit=m.audit||[],opened=state.expanded.has(m.id);
      const changes=audit.filter(a=>a.event==='message_revision');
      const attachments=parse(m.attachments_json||'[]');
      return `<article class="copier-message"><header><span class="copier-status">${esc(INTENTS[m.intent]||'未解析')}</span><strong>${esc(ai.symbol||m.author||'Arthur')}${ai.side?' · '+(ai.side==='short'?'空':'多'):''}</strong><span>${esc(STATUSES[m.status]||m.status)}</span>${changes.length?'<b class="ms-warn">原文有修订 · 待人工核对</b>':''}</header>
        <p>${esc(ai.summary||m.content?.slice(0,180))}</p><div class="copier-footnote">发布 ${clock(m.sent_at)}　·　入库 ${clock(m.created_at)}</div>
        <details data-message="${m.id}" ${opened?'open':''}><summary>原文、解析与处理依据</summary><pre class="copier-original">${esc(m.content)}</pre>
        <a href="${esc(safeLink(m.source_url))}" target="_blank" rel="noopener noreferrer">打开原消息 ↗</a>
        ${Array.isArray(attachments)?attachments.filter(a=>safeLink(a.url)).map((a,i)=>`<a class="copier-attachment" href="${esc(safeLink(a.url))}" target="_blank" rel="noopener noreferrer">附件 ${i+1} ↗</a>`).join(''):''}
        <dl class="copier-rfacts"><div><dt>入场区</dt><dd>${esc(ai.entry_low)} — ${esc(ai.entry_high)}</dd></div><div><dt>止损 / 止盈</dt><dd>${esc(ai.stop_loss)} / ${esc(ai.take_profit)}</dd></div></dl>
        ${audit.map(a=>`<p class="copier-audit-line"><strong>${esc(a.event)}</strong> · ${esc(a.detail)}${a.event==='message_revision'?`<pre>${esc(parse(a.payload_json).content)}</pre>`:''}</p>`).join('')}</details></article>`;
    }).join('');
    return `<div class="copier-filterbar"><label>意图<select id="copier-intent"><option value="">全部消息</option>${Object.entries(INTENTS).map(([k,v])=>`<option value="${k}" ${state.intent===k?'selected':''}>${v}</option>`).join('')}</select></label><label>处理状态<select id="copier-status"><option value="">全部状态</option>${Object.entries(STATUSES).map(([k,v])=>`<option value="${k}" ${state.status===k?'selected':''}>${v}</option>`).join('')}</select></label><span>共 ${count(state.total)} 条 · 按入库时间倒序 · 北京时间</span></div>
      <p class="copier-footnote">“已处理”包含历史干跑和订单提交，不等于成交。超时补录只留档；原文修订留版本，不重复开单。</p>${rows||`<p class="copier-empty">${state.pending?'正在读取消息…':'没有符合条件的消息。'}</p>`}
      <div class="copier-pagination"><button data-page="prev" ${state.pending||state.page===1?'disabled':''}>上一页</button><span>${state.page} / ${Math.max(1,Math.ceil(state.total/30))}</span><button data-page="next" ${state.pending||state.page*30>=state.total?'disabled':''}>下一页</button></div>`;
  }
  function settings(){const f=state.focus;return `<section class="copier-panel"><h3>当前监控配置</h3><dl class="copier-rfacts"><div><dt>唯一频道 ID</dt><dd>${CHANNEL}</dd></div><div><dt>执行路由</dt><dd>${esc(f?.route)}</dd></div><div><dt>真实交易所保护</dt><dd>${f?.dry_run?'dry_run 已开启':'请核对实盘配置'}</dd></div><div><dt>新开仓保护</dt><dd>${f?.kill_switch?'已暂停':'沿用现有风控'}</dd></div></dl><p>频道按服务器 ID + 频道 ID 精确匹配，同名频道不会自动加入。其他频道的历史数据保留，未结束模拟仓位与挂单冻结，不再参与当前撮合和统计。</p><p class="copier-footnote">仅观察与模拟。修改实盘模式、杠杆、仓位等由你另行决定。</p></section>`;}
  function render(){if(!root())return;const f=state.focus;
    const focusTab=document.activeElement && root().contains(document.activeElement) ? document.activeElement.closest('.copier-tabs [data-tab]')?.dataset.tab : null;
    root().innerHTML=`${state.error?`<p class="notice" role="alert">${esc(state.error)}。显示上次成功读取的数据。</p>`:''}
      <section class="copier-focus-hero"><div><span class="eyebrow">ONE CHANNEL / PAPER JOURNAL</span><h2>Arthur 行情分析</h2><p>消息 → 意图 → 模拟成交 → 复盘</p></div><a class="copier-button" href="https://discord.com/channels/1004707886657699901/${CHANNEL}" target="_blank" rel="noopener noreferrer">打开唯一监控频道 ↗</a></section>
      <div class="copier-focus-status"><span class="copier-dot ${f?.collector_online?'ok':'bad'}"></span>${f?.collector_online?'采集器在线':'采集器待确认'}<span>${f?.enabled?'频道已启用':'频道状态待确认'}</span><span>${f?.route==='paper'?'本地模拟账本':'路由待确认'}</span></div>
      <div class="copier-tabs" role="tablist" aria-label="Arthur 监控分页">${[['overview','频道概览'],['paper','模拟账本'],['signals','消息核对'],['settings','运行设置']].map(([k,v])=>`<button type="button" role="tab" id="copier-tab-${k}" aria-controls="copier-active-panel" tabindex="${state.tab===k?0:-1}" aria-selected="${state.tab===k}" data-tab="${k}" class="${state.tab===k?'selected':''}">${v}</button>`).join('')}</div>
      <section id="copier-active-panel" role="tabpanel" aria-labelledby="copier-tab-${state.tab}">${state.tab==='paper'?paper():state.tab==='signals'?signals():state.tab==='settings'?settings():overview()}</section>`;
    if(focusTab)root().querySelector(`.copier-tabs [data-tab="${focusTab}"]`)?.focus();
    root().querySelectorAll('details[data-message]').forEach(el=>el.addEventListener('toggle',()=>{const id=Number(el.dataset.message);el.open?state.expanded.add(id):state.expanded.delete(id);}));
  }
  const messageQuery = () => [state.page,state.intent,state.status].join('|');
  async function refresh(){
    if(!state.active||document.hidden)return;
    if(state.pending){state.refreshQueued=true;return;}
    state.pending=true;
    const query=messageQuery();
    try{const q=new URLSearchParams({channel_id:CHANNEL,page:String(state.page),per_page:'30'});if(state.intent)q.set('intent',state.intent);if(state.status)q.set('status',state.status);
      const [f,p,m]=await Promise.all([api('monitor/focus'),api(`paper/summary?channel_id=${CHANNEL}`),api('messages?'+q)]);
      state.focus=f;state.paper=p;
      if(query===messageQuery()){state.messages=m.items;state.total=m.total;}
      state.error='';
    }catch(e){state.error=e.message}finally{
      state.pending=false;render();
      if(state.refreshQueued||query!==messageQuery()){state.refreshQueued=false;void refresh();}
    }
  }
  document.addEventListener('click',e=>{const b=e.target.closest('#copier-workspace [data-tab],#copier-workspace [data-page]');if(!b)return;
    if(b.dataset.tab){state.tab=b.dataset.tab;render();refresh()}else{state.page+=b.dataset.page==='next'?1:-1;refresh();}});
  document.addEventListener('keydown',e=>{
    const button=e.target.closest('#copier-workspace .copier-tabs [data-tab]');
    if(!button||!['ArrowLeft','ArrowRight','Home','End'].includes(e.key))return;
    e.preventDefault();const keys=['overview','paper','signals','settings'],index=keys.indexOf(button.dataset.tab);
    const next=e.key==='Home'?0:e.key==='End'?keys.length-1:(index+(e.key==='ArrowRight'?1:-1)+keys.length)%keys.length;
    state.tab=keys[next];render();root().querySelector(`[id="copier-tab-${state.tab}"]`)?.focus();refresh();
  });
  document.addEventListener('change',e=>{if(e.target.id==='copier-intent'||e.target.id==='copier-status'){state[e.target.id==='copier-intent'?'intent':'status']=e.target.value;state.page=1;state.messages=[];state.total=0;refresh();render();}});
  window.SpikeCopier={refresh,setActive(v){state.active=v;clearInterval(state.timer);if(v){render();refresh();state.timer=setInterval(refresh,30000)}}};
})();
