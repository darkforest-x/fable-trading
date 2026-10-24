/* Isolated account reads; explicit per-lot management uses the confirmation panel. */
(() => {
  'use strict';
  const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const num=(v,d=4)=>v==null||v===''||!Number.isFinite(Number(v))?'—':Number(v).toLocaleString('en-US',{minimumFractionDigits:d===2?2:0,maximumFractionDigits:d});
  const coin=v=>String(v||'').replace('-USDT-SWAP','');
  const fmt=t=>t?new Intl.DateTimeFormat('zh-CN',{timeZone:'Asia/Shanghai',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',second:'2-digit',hour12:false}).format(new Date(t)):'—';
  const side=v=>({long:'多头',short:'空头',buy:'买入',sell:'卖出',net:'净持仓'}[v]||v||'—');
  const states={queued:'已确认 · 等待执行',prepared:'准备市价开仓',leverage_pending:'核对杠杆',entry_pending:'核对市价成交',protecting:'核对止盈止损',manual_tp_pending:'按成交价校准止盈',active:'持仓中',closed:'已平仓',skipped:'未开仓',rejected:'交易所拒绝',filled:'已成交',canceled:'已撤销',live:'待成交',partially_filled:'部分成交',exit_needed:'准备退出',cancel_stop_pending:'处理本笔保护单',exit_pending:'核对平仓成交',stop_executing:'核对止盈止损成交'};
  const reasons={manual_expired:'执行前报价已过期，本次未开仓。请重新预览并确认。',manual_repreview_required:'订单条件已变化，本次未开仓。打开详情核对原因后重新预览。',stop_crossed:'价格已越过止损点',insufficient_margin:'可用保证金不足',quote_stale:'报价已过期'};
  // Only the dedicated account route owns account polling, never patrol charts.
  const pollMode=(active,tab,hidden)=>active&&!hidden&&tab==='account'?'full':null;
  const tabs=[['positions','当前持仓'],['orders','挂单与保护'],['history','历史订单'],['executions','开仓记录']];
  class Poller {
    constructor(request,render,{set=(fn,ms)=>setTimeout(fn,ms),clear=id=>clearTimeout(id)}={}){Object.assign(this,{request,render,set,clear,version:0,active:false});}
    activate(active){if(active===this.active)return;this.active=active;this.version++;this.clear(this.timer);this.controller?.abort();if(active)this.refresh();}
    restart(){if(this.active){this.activate(false);this.activate(true);}}
    async refresh(){const v=this.version;if(!this.active)return;const controller=this.controller=new AbortController();
      const timeout=this.set(()=>controller.abort(),15000);
      try{const d=await this.request(controller.signal);if(v===this.version&&this.active){if(d.account_scope!=='yolo_manual')throw Error('账户来源不匹配');this.render(d,null);}}
      catch(e){if(v===this.version&&this.active)this.render(null,'连接暂不可用，正在自动重试');}
      finally{this.clear(timeout);if(v===this.version&&this.active)this.timer=this.set(()=>this.refresh(),5000);}
    }
  }
  function ownership(position,records,ready){
    if(!ready)return '待核对';
    const matching=records.filter(r=>r.symbol===position.instId&&r.side===position.posSide&&!['skipped','rejected','closed'].includes(r.state));
    if(!matching.length)return '自行管理';
    const size=matching.reduce((n,r)=>n+Number(r.size||0),0);
    return size>0&&Math.abs(size-Math.abs(Number(position.pos)))<1e-8?'系统管理':'数量待核对';
  }
  function closeActions(position,d,connected=d?.account?.status==='ready'){
    const activity=d?.executions,owned=ownership(position,activity?.owned_lots||[],activity?.status==='ready');
    if(!connected)return '<small>更新持仓后可平仓</small>';
    if(owned!=='系统管理')return `<small>${owned==='自行管理'?'自行管理 · 请到交易所平仓':esc(owned)+' · 暂不可平仓'}</small>`;
    const lots=(activity?.active_items||[]).filter(r=>r.symbol===position.instId&&r.side===position.posSide);
    return '<div class="ma-close-actions">'+(lots.length?lots.map(r=>`<button type="button" data-ma-close="${esc(r.id)}" ${r.state!=='active'||!r.protection_verified?'disabled title="持仓与保护核对完成后可平仓"':''}>${lots.length>1?'平仓 · '+num(r.size,8)+' 张':'平仓'}</button>`).join(''):'<small>分笔记录待核对</small>')+'</div>';
  }
  function table(head,rows,empty){return rows.length?`<div class="okx-table-scroll" tabindex="0" role="region" aria-label="账户明细，可横向滚动"><table><thead><tr>${head.map(h=>`<th scope="col">${h}</th>`).join('')}</tr></thead><tbody>${rows.map(r=>`<tr>${r.map(c=>`<td>${c}</td>`).join('')}</tr>`).join('')}</tbody></table>`:`<p class="okx-empty">${empty}</p>`;}
  function body(d,tab,connected=d?.account?.status==='ready'){
    const a=d?.account?.data,records=d?.executions?.items||[],ready=d?.executions?.status==='ready';
    if(tab==='executions')return table(['确认时间','币种 / 方向','状态','委托数量','成交均价','止损 / 止盈','详情'],records.map(r=>[
      fmt(r.confirmed_ms),`<b>${esc(coin(r.symbol))}</b><small>${esc(side(r.side))} · 市价 · ${esc(r.rr)}R</small>`,
      `${esc(r.exit_reason==='external_flattened'?'已在交易所平仓':states[r.state]||'核对订单中')}<small>${esc(reasons[r.reason_code]|| (r.state==='active'?(r.protection_verified?'止盈止损已核验':'保护状态待核验'):''))}</small>`,
      `${num(r.size??r.requested_size,8)} 张`,num(r.entry_price,8),`${num(r.stop??r.initial_stop,8)} / ${num(r.take_profit,8)}`,
      `<button data-ma-order="${esc(r.id)}">查看</button>`]),ready?'还没有确认提交的订单。形态旁的开仓预览不会出现在这里。':'执行记录暂不可用，正在自动重试。');
    if(!a)return '<p class="okx-empty">正在读取专用账户…</p>';
    if(tab==='positions')return table(['币种 / 方向','持仓数量','开仓均价','标记价格','浮动盈亏','杠杆 / 保证金','管理方式','操作'],a.positions.map(p=>[
      `<button type="button" data-yd-position="${esc(p.instId)}" title="查看 ${esc(coin(p.instId))} K 线">${esc(coin(p.instId))} ↗</button><small>${esc(side(p.posSide))}</small>`,`${num(p.pos,8)} 张`,num(p.avgPx,8),num(p.markPx,8),
      `<span class="${Number(p.upl)<0?'okx-negative':'okx-positive'}">${num(p.upl)} USDT</span>`,`${num(p.lever,0)}× · ${p.mgnMode==='cross'?'全仓':'逐仓'}<small>${num(p.margin)} USDT</small>`,
      esc(ownership(p,d?.executions?.owned_lots||[],ready)),closeActions(p,d,connected)]),'当前没有持仓。');
    if(tab==='orders')return table(['币种 / 方向','订单类型','委托价格','委托数量','止损触发价','止盈触发价','状态'],[...(a.orders||[]),...(a.protection_orders||[])].map(r=>[
      `<b>${esc(coin(r.instId))}</b><small>${esc(side(r.posSide))} · ${esc(side(r.side))}</small>`,esc(({market:'市价',limit:'限价',oco:'止盈止损',conditional:'条件单',trigger:'触发单',move_order_stop:'移动止损'})[r.ordType]||r.ordType),
      r.ordType==='market'?'市价':num(r.px,8),`${num(r.sz,8)} 张`,num(r.slTriggerPx,8),num(r.tpTriggerPx,8),esc(states[r.state]||r.state||'—')]),'当前没有挂单或保护单。')+(a.orders_truncated?'<p class="okx-notice">订单较多，当前列表有截断，请在交易所核对完整挂单。</p>':'');
    const history=a.history;
    if(history?.status!=='ready')return '<p class="okx-empty">交易所历史订单暂不可用，持仓数据不受影响，系统会自动重试。</p>';
    return '<p class="okx-footnote">最近 7 天 · 最多 100 笔永续订单。交易所仅保留最近 2 小时未成交即撤销的订单。</p>'+table(['时间','币种 / 方向','类型','成交均价','成交 / 委托数量','手续费','状态'],history.items.map(r=>[
      fmt(r.updated_ms||r.created_ms),`<b>${esc(coin(r.instId))}</b><small>${esc(side(r.posSide))} · ${esc(side(r.side))}</small>`,r.ordType==='market'?'市价':r.ordType==='limit'?'限价':esc(r.ordType),num(r.avgPx,8),`${num(r.accFillSz,8)} / ${num(r.sz,8)} 张`,`${num(r.fee,6)} ${esc(r.feeCcy||'')}`,esc(states[r.state]||r.state)]),'最近 7 天没有可显示的历史订单。')+(history.truncated?'<p class="okx-footnote">已显示最近 100 笔，更多历史请在交易所查看。</p>':'');
  }
  function updateTable(panel,next,focused){
    if(panel._markup===next)return;
    const previous=panel.querySelector('.okx-table-scroll');
    const position={left:previous?.scrollLeft||0,top:previous?.scrollTop||0};
    const order=panel.contains(focused)?focused?.dataset?.maOrder:null;
    const close=panel.contains(focused)?focused?.dataset?.maClose:null;
    const symbol=panel.contains(focused)?focused?.dataset?.ydPosition:null;
    const disclosure=panel.contains(focused)&&focused?.tagName==='SUMMARY'?focused.parentElement?.dataset?.maDisclosure:null;
    const restore=focused===previous;
    const expanded=[...(panel.querySelectorAll?.('details[data-ma-disclosure][open]')||[])].map(el=>el.dataset?.maDisclosure).filter(Boolean);
    panel.innerHTML=next;panel._markup=next;
    for(const el of panel.querySelectorAll?.('details[data-ma-disclosure]')||[])if(expanded.includes(el.dataset?.maDisclosure))el.open=true;
    const scroll=panel.querySelector('.okx-table-scroll');
    if(scroll){scroll.scrollLeft=position.left;scroll.scrollTop=position.top;}
    const target=close?([...panel.querySelectorAll('[data-ma-close]')].find(b=>b.dataset.maClose===close)||scroll):order?([...panel.querySelectorAll('[data-ma-order]')].find(b=>b.dataset.maOrder===order)||scroll):
      symbol?[...panel.querySelectorAll('[data-yd-position]')].find(b=>b.dataset.ydPosition===symbol):
      disclosure?[...panel.querySelectorAll('details[data-ma-disclosure]')].find(b=>b.dataset.maDisclosure===disclosure)?.querySelector('summary'):restore?scroll:null;
    target?.focus({preventScroll:true});
  }
  function deskMetrics(d){
    const a=d?.account?.data,s=d?.executions?.statistics;
    return [['账户权益',a?.total_equity_usdt],['可用保证金',a?.balance?.availEq],['持仓浮盈亏',a?.balance?.upl],['已结算净收益',s?.realized_net_usdt]]
      .map(([label,value],i)=>`<div><span>${label}</span><strong class="${i>1&&value!=null?(Number(value)<0?'is-loss':'is-profit'):''}">${num(value,2)}</strong></div>`).join('');
  }
  function deskPositions(d,connected=d?.account?.status==='ready'){
    const a=d?.account?.data,activity=d?.executions,verified=activity?.status==='ready';
    if(!a)return '<p class="ma-desk-empty">正在读取账户持仓…</p>';
    return (a.positions||[]).map(p=>{
      const owned=ownership(p,activity?.owned_lots||[],verified),lots=(activity?.active_items||[]).filter(r=>r.symbol===p.instId&&r.side===p.posSide);
      const safe=owned==='系统管理'&&lots.length&&lots.every(r=>r.protection_verified);
      return `<article class="ma-position"><header><button data-yd-position="${esc(p.instId)}" title="查看 ${esc(coin(p.instId))} K 线">${esc(coin(p.instId))}<span class="ma-side ${p.posSide==='short'?'is-loss':'is-profit'}">${esc(side(p.posSide))}</span></button><strong class="${Number(p.upl)<0?'is-loss':'is-profit'}">${num(p.upl,2)}</strong></header><div class="ma-position-prices"><span>开仓 <b>${num(p.avgPx,8)}</b></span><span>标记 <b>${num(p.markPx,8)}</b></span></div><footer><span>${num(p.pos,8)} 张 · ${num(p.lever,0)}×</span><span class="${safe?'is-profit':''}">${safe?'止盈止损已核验':owned==='自行管理'?'自行管理':owned==='系统管理'?'保护待核验':owned}</span></footer>${lots.length?`<details data-ma-disclosure="${esc(p.instId)}:${esc(p.posSide)}"><summary>${lots.length} 笔开仓 · 止盈止损</summary>${lots.map(r=>`<div class="ma-lot"><span>止损 ${num(r.stop??r.initial_stop,8)}<br>止盈 ${num(r.take_profit,8)}</span><button data-ma-order="${esc(r.id)}">${num(r.size,8)} 张 ↗</button></div>`).join('')}</details>`:''}${closeActions(p,d,connected)}</article>`;
    }).join('')||'<p class="ma-desk-empty">当前没有持仓</p>';
  }
  function deskActivity(d){
    const activity=d?.executions;if(activity?.status!=='ready')return '<p class="ma-desk-empty">开仓记录暂不可用</p>';
    const pending=(activity.active_items||[]).filter(r=>r.state!=='active');
    const items=[...pending,...(activity.items||[]).filter(r=>!pending.some(p=>p.id===r.id))].slice(0,Math.max(5,pending.length));
    return items.map(r=>`<button class="ma-activity" data-ma-order="${esc(r.id)}"><span>${esc(coin(r.symbol))}<small>${esc(side(r.side))} · ${fmt(r.confirmed_ms||r.created_ms).slice(-8)}</small></span><span>${esc(states[r.state]||'核对中')}<small>${r.realized_net_usdt!=null?num(r.realized_net_usdt,2)+' USDT':esc(reasons[r.reason_code]||'')}</small></span></button>`).join('')||'<p class="ma-desk-empty">尚无确认开仓记录</p>';
  }
  function deskBlocker(entry){
    return entry?.status==='blocked'?`<strong>${esc(entry.title)}</strong><p>${esc(entry.message)}</p>${(entry.issues||[]).map(i=>`<button data-ma-order="${esc(i.id)}" ${!i.id?'disabled':''}>${esc(coin(i.symbol)||'订单')} · ${esc(i.message)} ↗</button>`).join('')}`:'';
  }
  if(typeof module!=='undefined')module.exports={Poller,ownership,body,updateTable,pollMode,deskMetrics,deskPositions,deskActivity,deskBlocker,closeActions};
  if(typeof document==='undefined')return;
  let host,summary,snapshot=null,summarySnapshot=null,error='',selected='positions',workspaceActive=false,workspaceTab='results';
  const poller=new Poller(async signal=>{const r=await fetch('/api/manual-live/dashboard'+(workspaceTab==='account'?'':'?view=workspace'),{cache:'no-store',signal});if(!r.ok)throw Error('unavailable');return r.json();},(d,e)=>{if(d){summarySnapshot=d;if(!d.view)snapshot=d;}error=e||'';render();});
  function render(){
    if(!host?.isConnected)return;
    const current=workspaceTab==='account'?snapshot:summarySnapshot||snapshot;
    const a=current?.account,data=a?.data,worker=current?.ledger?.worker,ready=a?.status==='ready'&&!error;
    const equity=data?.total_equity_usdt,available=data?.balance?.availEq,entry=current?.entry_status,entryReady=entry?.status==='ready';
    if(summary){
      const status=summary.querySelector('[data-ma-state]');
      status.classList.toggle('is-good',ready&&entryReady);
      status.textContent=ready?(entryReady?'可预览订单':entry?.title||'读取执行状态'):error?'连接中断 · 保留上次数据':a?.status==='stale'?'数据过时 · 更新中':a?.status==='error'?'账户读取失败 · 自动重试':'读取账户中';
      summary.classList.toggle('is-stale',!ready);
      const update=(selector,markup)=>updateTable(summary.querySelector(selector),markup,document.activeElement);
      update('[data-ma-metrics]',deskMetrics(current));
      update('[data-ma-positions]',deskPositions(current,ready));
      update('[data-ma-activity]',deskActivity(current));
      update('[data-ma-blocker]',deskBlocker(entry));
      summary.querySelector('[data-ma-blocker]').hidden=entry?.status!=='blocked';
      summary.querySelector('[data-ma-position-count]').textContent=data?.position_count??'—';
      const stats=current?.executions?.statistics;
      summary.querySelector('[data-ma-statistics]').textContent=stats?`累计开仓 ${stats.filled} 笔 · 已平仓 ${stats.closed} 笔${stats.unattributed?' · '+stats.unattributed+' 笔收益待归属':''}`:'交易统计读取中';
      summary.querySelector('[data-ma-updated]').textContent=`${a?.updated_ms?'更新 '+fmt(a.updated_ms).slice(-8):'首次读取中'} · USDT`;
    }
    if(workspaceTab!=='account')return;
    const title=error|| (a?.status==='error'?'账户读取失败，保留上次数据并自动重试':a?.status==='stale'?'数据已过时，正在自动更新':data?'账户已连接':'正在连接账户');
    const banner=host.querySelector('[data-ma-summary]');
    banner.innerHTML=`<div class="okx-status" role="status"><span class="okx-badge ${ready?'good':'warn'}">${esc(title)}</span><span>${worker?.status==='running'?(worker.error_present?'执行服务待核对':'执行服务运行中'):'执行服务未就绪'}</span><small>自动更新 · ${a?.updated_ms?'账户数据 '+fmt(a.updated_ms):'首次读取中'}</small></div>${entry?.status==='blocked'?`<div class="ml-error" role="status"><strong>${esc(entry.title)}</strong><p>${esc(entry.message)}</p></div>`:entryReady?'<p class="ma-readiness">账户已就绪 · 可返回形态预览订单</p>':''}<div class="okx-metrics ${ready?'':'is-stale'}">${[['账户总权益',equity,'USDT · 全部资产计价'],['可用保证金',available,'USDT'],['持仓浮动盈亏',data?.balance?.upl,'USDT'],['每笔风险 · 1R',equity==null?null:Number(equity)*.03,'总权益的 3%']].map(([label,value,unit])=>`<article><span>${label}</span><strong>${num(value,2)}</strong><small>${unit}</small></article>`).join('')}</div><div class="okx-config"><span><b>市价开仓</b> · 逐笔手动确认</span><span>最大可用杠杆 · 全仓</span><span>5m 六均线外止损 · 止盈 3R / 5R</span></div>`;
    host.querySelectorAll('[data-ma-tab]').forEach(b=>{const yes=b.dataset.maTab===selected;b.setAttribute('aria-selected',String(yes));b.tabIndex=yes?0:-1;});
    const panel=host.querySelector('[data-ma-body]');
    updateTable(panel,body(snapshot,selected,ready),document.activeElement);
    panel.setAttribute('aria-labelledby','ma-tab-'+selected);
  }
  function bind(element,bar){host=element;summary=bar;host.classList.add('okx-live','ma-account');
    if(summary){summary.innerHTML='<header class="ma-desk-heading"><div><h2>实盘账户</h2><span>OKX · 均线密集专用 · USDT</span></div><button type="button" data-yd-tab="account">明细 ↗</button></header><div class="ma-desk-health"><span data-ma-state></span><button type="button" data-ma-refresh aria-label="刷新实盘账户">↻</button></div><div data-ma-blocker class="ma-desk-blocker" role="status" hidden></div><div class="ma-desk-metrics" data-ma-metrics></div><p class="ma-desk-statistics" data-ma-statistics></p><p class="ma-desk-policy">1R = 总权益的 3% · 每笔手动确认</p><section class="ma-desk-positions"><h3>当前持仓 <span data-ma-position-count>—</span></h3><div data-ma-positions></div></section><details class="ma-desk-recent"><summary>最近开仓</summary><div data-ma-activity></div></details><footer class="ma-desk-updated" data-ma-updated></footer>';
      summary.addEventListener('click',e=>{const b=e.target.closest('button');if(b?.dataset.maClose&&!b.disabled)window.SpikeManualClose?.open(b.dataset.maClose);if(b?.dataset.maOrder)window.SpikeManualLive?.openStatus(b.dataset.maOrder);if(b?.hasAttribute('data-ma-refresh')){b.disabled=true;poller.restart();setTimeout(()=>{b.disabled=false;},1500);}});
    }
    host.innerHTML=`<header class="okx-heading"><div><div class="okx-eyebrow">OKX <span>均线密集专用账户</span></div><h2>实盘账户</h2></div><button type="button" data-yd-tab="results" class="ma-back">返回形态 →</button></header><div data-ma-summary></div><div class="okx-tabs" role="tablist" aria-label="实盘账户明细">${tabs.map(([key,label])=>`<button id="ma-tab-${key}" role="tab" data-ma-tab="${key}" aria-controls="ma-detail">${label}</button>`).join('')}</div><div id="ma-detail" data-ma-body role="tabpanel"></div><p class="okx-separation">仅展示均线密集专用账户。系统管理今后由你确认的新单；自行管理的仓位和挂单在这里可见，仍由你管理。</p>`;
    host.addEventListener('click',e=>{const b=e.target.closest('button');if(b?.dataset.maClose&&!b.disabled)window.SpikeManualClose?.open(b.dataset.maClose);if(b?.dataset.maTab){selected=b.dataset.maTab;render();}if(b?.dataset.maOrder)window.SpikeManualLive?.openStatus(b.dataset.maOrder);});
    host.querySelector('[role="tablist"]').addEventListener('keydown',e=>{if(!['ArrowLeft','ArrowRight','Home','End'].includes(e.key))return;e.preventDefault();let index=tabs.findIndex(t=>t[0]===selected);index=e.key==='Home'?0:e.key==='End'?3:(index+(e.key==='ArrowRight'?1:3))%4;selected=tabs[index][0];render();host.querySelector('#ma-tab-'+selected).focus();});
    render();poller.activate(shouldPoll());
  }
  const shouldPoll=()=>!!host&&!!pollMode(workspaceActive,workspaceTab,document.hidden);
  window.SpikeManualAccount={bind,setActive(active,tab='results'){const changed=workspaceTab!==tab;workspaceActive=active;workspaceTab=tab;if(changed)poller.activate(false);if(active)render();poller.activate(shouldPoll());}};
  document.addEventListener('visibilitychange',()=>poller.activate(shouldPoll()));
  window.addEventListener('spike:manual-order-update',()=>poller.restart());
})();
