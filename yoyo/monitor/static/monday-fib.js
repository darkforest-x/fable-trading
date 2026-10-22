/* Stitch Apple Glass screen 07032a6d28ba421790a667c5dc50eccc, adapted to
 * the existing read-only OKX API. Opening this page never enables Telegram.
 * Snapshot generations fence late GETs and subscription writes; no trade API.
 */
(() => {
  'use strict';
  const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const finite=v=>typeof v==='number'&&Number.isFinite(v);
  const price=v=>finite(v)?v.toLocaleString('en-US',{maximumFractionDigits:Math.min(12,Math.max(2,6-Math.floor(Math.log10(Math.abs(v)||1))))}):'—';
  const STATES={loading:'正在同步',developing:'周一尚未结束',missing_monday:'缺少完整周一',missing_history:'历史触价待核对',recovering:'正在核对断线区间',fetch_error:'数据暂不可用',invalid_range:'周一区间无效'};
  const RECEIPTS={history:'历史触及 · 不补发',muted:'未启用推送',pending:'待发送',sending:'发送中',sent:'TG 服务已接受',skipped:'已跳过',failed:'发送失败',unknown:'结果未知 · 不重发'};
  const FILTERS={all:'全部已触及',recent:'刚触及 · 15分钟内',upper:'触及上沿',lower:'触及下沿'};
  // Display emphasis only; does not change notification or execution freshness.
  const RECENT_MS=15*60000;
  const touchTime=e=>e?(e.source==='history'?e.candle_ms:e.observed_ms)||0:0;
  const latestTouch=r=>Object.entries(r.touches||{}).map(([side,e])=>({...e,side})).sort((a,b)=>touchTime(b)-touchTime(a))[0]||null;
  const recentTouch=(e,now)=>!!e&&e.source==='live'&&finite(e.observed_ms)&&now>=e.observed_ms&&now-e.observed_ms<RECENT_MS;
  const available=r=>r.state==='ready'&&!r.stale;
  function selectRows(items,q='',filter='all',now=Date.now()) {
    const query=q.trim().toUpperCase().replace(/[^A-Z0-9]/g,'');
    return items.filter(r=>!!latestTouch(r)&&(!query||r.symbol.replace(/[^A-Z0-9]/g,'').includes(query))&&(
      filter==='recent'?recentTouch(latestTouch(r),now):
      filter==='upper'||filter==='lower'?!!r.touches?.[filter]:true))
      .sort((a,b)=>touchTime(latestTouch(b))-touchTime(latestTouch(a))||a.symbol.localeCompare(b.symbol));
  }
  if(typeof module!=='undefined')module.exports={esc,price,selectRows,latestTouch,touchTime,recentTouch,RECENT_MS,STATES,RECEIPTS};
  if(typeof document==='undefined')return;
  const root=document.getElementById('monday-fib-root');if(!root)return;
  const icon=(name)=>`<span class="mf-icon" aria-hidden="true" style="--mf-icon:url(/static/icons/${name}.svg)"></span>`;
  root.innerHTML=`
    <header class="mf-nav mf-glass"><a class="mf-brand" href="#market">${icon('chart-candle')}<b>SPIKE</b><span>RESEARCH</span></a>
      <nav aria-label="监控页面导航"><a href="#market">市场观察</a><a href="#yolo-detect">均线密集</a><a href="#monday-fib" aria-current="page">周一 1.618</a><a href="#signals">信号中心</a><a href="#research">研究资产</a></nav>
      <span id="mf-stream" class="mf-badge">连接中</span></header>
    <div class="mf-heading"><div><p class="mf-eyebrow">WEEKLY RANGE MONITOR <span>每周定界</span></p><h1>周一 1.618 扩展位监控</h1><p id="mf-week">正在读取纽约周一区间…</p></div>
      <div class="mf-formula mf-glass"><span><b>上沿</b> H + 0.618 × (H − L)</span><span><b>下沿</b> L − 0.618 × (H − L)</span><small>H / L 为纽约时间完整周一的高 / 低点</small></div></div>
    <div id="mf-notice" class="mf-notice" role="status" hidden></div>
    <div id="mf-stats" class="mf-stats" aria-label="监控概览"></div>
    <div class="mf-columns"><div class="mf-market">
      <div class="mf-toolbar mf-glass"><div class="mf-search-row"><label class="mf-search">${icon('search')}<input id="mf-search" type="search" placeholder="搜索已触及合约，如 BTC、ETH" aria-label="搜索监控合约" autocomplete="off"></label><button id="mf-refresh" class="mf-refresh" title="刷新监控数据" aria-label="刷新监控数据">${icon('refresh')}</button><span class="mf-sort">按最近触价时间排序</span></div><div id="mf-filters" class="mf-filters" role="group" aria-label="筛选合约状态"></div></div>
      <div class="mf-list mf-glass"><div class="mf-table-head" aria-hidden="true"><span>合约</span><span>最新成交价</span><span>周一 1.618 扩展区间</span><span>距最近点位</span><span>本周触及</span><span></span></div><div id="mf-rows"><p class="mf-empty">正在读取监控数据…</p></div></div>
      <div class="mf-pagination"><span id="mf-page-info"></span><div><label>每页 <select id="mf-page-size" aria-label="每页合约数"><option value="5">5</option><option value="20">20</option><option value="50">50</option></select></label><button id="mf-prev" aria-label="上一页">上一页</button><span id="mf-page-number"></span><button id="mf-next" aria-label="下一页">下一页</button></div></div>
    </div><aside class="mf-aside">
      <section class="mf-tg mf-glass"><div class="mf-card-title"><div><h2>Telegram 触价推送</h2><p>到达点位，及时知晓</p></div><button id="mf-tg-toggle" class="mf-switch" role="switch" aria-label="Telegram 触价推送" aria-checked="false" disabled><span></span></button></div>
        <div class="mf-tg-status"><span id="mf-tg-state">读取订阅状态…</span><span id="mf-tg-config"></span></div><div id="mf-save-message" role="status" hidden></div>
        <div class="mf-rule"><b>只推启用后的新触价，不补历史</b><p>每周、每合约、每个方向首次触及推送一次，无需等待 K 线收盘。</p></div><small>“TG 服务已接受”不代表手机已收到或已读。</small></section>
      <section class="mf-events mf-glass"><div class="mf-card-title"><h2>本周最新触及</h2><span id="mf-event-count" class="mf-secondary"></span></div><div id="mf-events-list"><p class="mf-empty">正在读取记录…</p></div></section>
      <details class="mf-rules mf-glass"><summary>监控规则与数据说明</summary><p>仅使用 OKX USDT 永续行情。纽约自然周一结束后锁定区间，周二至下周一监控两侧扩展位。</p><p>周一未结束或历史不完整时不启动该合约报警；断线后先核对历史，避免补发旧触价。</p><p>本机服务需持续运行并联网。这里是位置提醒，不会自动下单。</p></details>
    </aside></div><footer class="mf-footer"><span>OKX · USDT 永续 · America/New_York</span><span id="mf-updated">等待同步</span></footer>`;
  const $=id=>document.getElementById(id);
  let active=false,data=null,receivedAt=0,revision=0,controller=null,pending=null,timer=null,saving=false,error='',saveMessage='';
  let query='',filter='all',page=0,pageSize=5;
  const expanded=new Set();
  const nowMs=()=>data?data.now_ms+Math.max(0,Date.now()-receivedAt):Date.now();
  function currentRows(){const now=nowMs();return (data?.items||[]).map(r=>({...r,stale:r.stale||!finite(r.received_ms)||now-r.received_ms>90000}));}
  const time=(ms,historical=false)=>finite(ms)?new Intl.DateTimeFormat('zh-CN',{timeZone:'America/New_York',month:'2-digit',day:'2-digit',hour:'2-digit',...(historical?{}:{minute:'2-digit'}),hourCycle:'h23'}).format(new Date(ms))+(historical?'内':'')+' 纽约':'—';
  const status=r=>r.state!=='ready'?STATES[r.state]||'数据待就绪':r.stale?'行情已滞后':'实时观察中';
  function row(r){
    const name=r.symbol.replace('-USDT-SWAP',''),good=available(r),t=r.touches||{},key=esc(r.symbol),event=latestTouch(r),recent=recentTouch(event,nowMs());
    const knownCoin=['BTC','ETH','SOL','BNB','AVAX','LINK'].includes(name);
    const coin=knownCoin?`<img alt="" src="/static/icons/coins/${name.toLowerCase()}.png">`:icon('chart-candle');
    const ago=Math.floor(Math.max(0,nowMs()-touchTime(event))/60000),age=ago<1?'刚刚':`${ago} 分钟前`;
    return `<details class="mf-row${recent?' mf-just-touched':''}" data-symbol="${key}"${expanded.has(r.symbol)?' open':''}><summary data-row="${key}">
      <span class="mf-symbol"><span class="mf-coin">${coin}</span><span><b>${esc(name)}<small>USDT</small></b><span class="mf-touch-age${recent?' mf-recent-badge':''}">${recent?'刚触及 · '+age:time(touchTime(event),event?.source==='history')}</span><em>${good?(expanded.has(r.symbol)?'收起区间 K 线':'点击展开区间 K 线'):esc(status(r))}</em></span></span>
      <span class="mf-price"><small>最新成交价${r.stale?' · 非实时':''}</small><strong>${price(r.price)}</strong></span>
      <span class="mf-trigger"><small>触及${event?.side==='upper'?'上':'下'}沿 1.618</small><strong class="${event?.side==='upper'?'mf-upper-text':'mf-lower-text'}">${price(event?.level??r[event?.side])}</strong><em>${event?.source==='history'?'历史触价':esc(RECEIPTS[event?.status]||'已触及')}${t.upper&&t.lower?' · 两侧均已触及':''}</em></span>${icon('chevron-down')}</summary>
      <div class="mf-row-detail"><div class="mf-row-meta"><span>周一高点 <b>${price(r.high)}</b></span><span>周一低点 <b>${price(r.low)}</b></span><span>区间宽度 <b>${finite(r.high)&&finite(r.low)?price(r.high-r.low):'—'}</b></span>${['upper','lower'].filter(side=>t[side]).map(side=>`<span>${side==='upper'?'上沿':'下沿'}首次：${time(touchTime(t[side]),t[side].source==='history')} · ${esc(RECEIPTS[t[side].status]||'状态未知')}</span>`).join('')}</div>
      <div class="mf-chart" data-chart-symbol="${key}"></div></div></details>`;
  }
  // Reconcile keyed rows so 5-second quote polling preserves the live chart canvas and zoom.
  function patchRows(markup){
    const host=$('mf-rows');if(!document.createElement||!host.replaceChildren){host.innerHTML=markup;return;}
    const template=document.createElement('template');template.innerHTML=markup;
    const old=new Map([...host.querySelectorAll(':scope > .mf-row')].map(n=>[n.dataset.symbol,n]));
    const nodes=[...template.content.childNodes].map(fresh=>{
      const node=old.get(fresh.dataset?.symbol);if(!node)return fresh;
      node.className=fresh.className;node.querySelector('summary').innerHTML=fresh.querySelector('summary').innerHTML;
      node.querySelector('.mf-row-meta').innerHTML=fresh.querySelector('.mf-row-meta').innerHTML;return node;
    });
    for(let i=0;i<nodes.length;i++)if(host.childNodes[i]!==nodes[i])host.insertBefore(nodes[i],host.childNodes[i]||null);
    while(host.childNodes.length>nodes.length)host.lastChild.remove();
  }
  function syncChart(){
    const node=$('mf-rows').querySelector('.mf-row[open]'),r=data?.items.find(x=>x.symbol===node?.dataset.symbol);
    if(active&&!document.hidden&&node&&r)window.SpikeMondayFibChart?.show(node.querySelector('.mf-chart'),r,data.week);
    else window.SpikeMondayFibChart?.hide();
  }
  function renderRows(){
    const rows=selectRows(currentRows(),query,filter,nowMs()),pages=Math.max(1,Math.ceil(rows.length/pageSize));page=Math.min(page,pages-1);
    const focus=document.activeElement?.closest?.('[data-row]')?.dataset.row,focusFilter=document.activeElement?.dataset?.filter;
    patchRows(rows.slice(page*pageSize,(page+1)*pageSize).map(row).join('')||`<p class="mf-empty">${data?(query||filter!=='all'?'没有符合条件的已触及合约':'本周暂无触价，触及后自动出现在这里'):'正在读取监控数据…'}</p>`);
    if(focus){const el=[...$('mf-rows').querySelectorAll('[data-row]')].find(e=>e.dataset.row===focus);el?.focus({preventScroll:true});}
    $('mf-page-info').textContent=rows.length?`显示 ${page*pageSize+1}–${Math.min((page+1)*pageSize,rows.length)} / ${rows.length} 个合约`:'0 个合约';
    $('mf-page-number').textContent=`${page+1} / ${pages}`;$('mf-prev').disabled=page===0;$('mf-next').disabled=page>=pages-1;
    $('mf-filters').innerHTML=Object.entries(FILTERS).map(([k,v])=>`<button type="button" data-filter="${k}" aria-pressed="${k===filter}">${v}<span>${selectRows(currentRows(),query,k,nowMs()).length}</span></button>`).join('');
    if(focusFilter)$('mf-filters').querySelector(`[data-filter="${focusFilter}"]`)?.focus({preventScroll:true});
    syncChart();
  }
  function render(){
    const rows=currentRows(),ready=rows.filter(available).length,touched=selectRows(rows).length,recent=selectRows(rows,'','recent',nowMs()).length;
    $('mf-stats').innerHTML=[['监控合约',data?rows.length:'—','全部 USDT 永续'],['本周已触及',data?touched:'—','已触及的合约'],['刚触及',data?recent:'—','最近 15 分钟 · 实时首次触价'],['数据已就绪',data?ready:'—',data?`${rows.length-ready} 个待就绪`:'正在同步']].map(([a,b,c],i)=>`<article class="mf-glass"><p>${a}</p><div class="mf-stat-value mf-stat-${i}">${b}</div><small>${c}</small></article>`).join('');
    const now=(data?.now_ms||0)+Math.max(0,Date.now()-receivedAt);
    const connected=data?.stream.connected&&finite(data.stream.last_message_ms)&&now-data.stream.last_message_ms<90000&&!error;
    $('mf-stream').textContent=connected?'OKX 行情在线':data?'行情连接待恢复':'连接中';$('mf-stream').classList.toggle('mf-online',!!connected);
    if(data)$('mf-week').textContent=`纽约周一 ${data.week} · ${data.monday_complete?'区间已锁定，监控至下周一 00:00':'自然日尚未结束，区间形成中'}`;
    const notice=error||data?.stream.catalog_error||data?.stream.error||'';$('mf-notice').hidden=!notice;$('mf-notice').textContent=notice;
    const tg=data?.telegram;$('mf-tg-toggle').disabled=!tg||saving||!!error||(!tg.configured&&!tg.enabled);$('mf-tg-toggle').setAttribute('aria-checked',String(!!tg?.enabled));
    $('mf-tg-state').textContent=saving?'正在保存…':tg?.enabled?'推送已开启':'推送未开启';$('mf-tg-config').textContent=tg?(tg.configured?'通道已配置':'本机尚未配置通道'):'读取中';
    $('mf-save-message').hidden=!saveMessage;$('mf-save-message').textContent=saveMessage;
    const events=data?.events||[];$('mf-event-count').textContent=events.length?`最近 ${events.length} 条`:'';
    $('mf-events-list').innerHTML=events.length?events.map(e=>`<article class="${recentTouch(e,nowMs())?'mf-just-touched':''}"><div><b>${esc(e.symbol.replace('-USDT-SWAP',''))}</b><span class="${e.side==='upper'?'mf-upper-text':'mf-lower-text'}">触及${e.side==='upper'?'上':'下'}沿 ${price(e.level)}</span></div><div><time>${time(e.source==='history'?e.candle_ms:e.observed_ms,e.source==='history')}</time><span class="mf-receipt ${e.status==='sent'?'mf-lower':e.status==='failed'?'mf-upper':''}">${esc(RECEIPTS[e.status]||'状态未知')}</span></div></article>`).join(''):`<p class="mf-empty">${data?'本周暂无触价记录':'正在读取记录…'}</p>`;
    $('mf-updated').textContent=data?`最近同步 ${new Intl.DateTimeFormat('zh-CN',{hour:'2-digit',minute:'2-digit',second:'2-digit',hourCycle:'h23'}).format(new Date(data.now_ms))} · 本地时间`:'等待同步';
    renderRows();
  }
  function sync(){window.SpikeNavigation?.sync('monday-fib',{q:query||null,filter:filter==='all'?null:filter,page:page?String(page+1):null,size:pageSize===5?null:pageSize});}
  function schedule(){clearTimeout(timer);if(active&&!document.hidden&&!saving)timer=setTimeout(()=>refresh(),5000);}
  async function request(url,options={}){const res=await fetch(url,{cache:'no-store',credentials:'same-origin',...options});const d=await res.json();if(!res.ok)throw new Error(typeof d.detail==='string'?d.detail:'监控暂不可用，请稍后重试');if(!Array.isArray(d.items)||!d.telegram||!d.counts||!d.stream)throw new Error('监控数据格式异常');return d;}
  function refresh(){
    if(!active||document.hidden||saving)return Promise.resolve();if(pending)return pending;
    clearTimeout(timer);const rev=revision,c=new AbortController();controller=c;const timeout=setTimeout(()=>c.abort(),12000);
    const task=(async()=>{try{const d=await request('/api/monday-fib',{signal:c.signal});if(rev!==revision||!active)return;data=d;receivedAt=Date.now();error='';render();}
      catch(e){if(rev===revision&&active){error=e.name==='AbortError'?'数据请求超时，正在重试':e.message;render();}}
      finally{clearTimeout(timeout);if(controller===c)controller=null;if(pending===task)pending=null;if(rev===revision)schedule();}})();pending=task;return task;
  }
  async function toggle(){
    if(!data||saving||$('mf-tg-toggle').disabled)return;
    const target=!data.telegram.enabled;saving=true;saveMessage='';revision++;controller?.abort();controller=null;pending=null;clearTimeout(timer);render();
    const c=new AbortController(),timeout=setTimeout(()=>c.abort(),12000);
    try{const d=await request('/api/monday-fib/telegram',{method:'PUT',headers:{'Content-Type':'application/json','X-Spike-Action':'update-monday-fib-telegram'},body:JSON.stringify({enabled:target}),signal:c.signal});data=d;receivedAt=Date.now();error='';saveMessage=d.telegram.enabled?'已开启，仅推送之后的新触价':'已关闭后续推送';}
    catch(e){saveMessage=`保存结果未确认：${e.name==='AbortError'?'请求超时':e.message}。正在重新读取实际订阅状态。`;}
    finally{clearTimeout(timeout);saving=false;if(active){render();await refresh();}}
  }
  function setActive(value){if(active===value)return;active=value;revision++;controller?.abort();controller=null;pending=null;clearTimeout(timer);if(active){render();refresh();}else window.SpikeMondayFibChart?.hide();}
  $('mf-search').addEventListener('input',e=>{query=e.target.value;page=0;renderRows();sync();});
  $('mf-filters').addEventListener('click',e=>{const b=e.target.closest('[data-filter]');if(!b)return;filter=b.dataset.filter;page=0;renderRows();sync();$('mf-filters').querySelector(`[data-filter="${filter}"]`)?.focus({preventScroll:true});});
  $('mf-page-size').addEventListener('change',e=>{pageSize=Number(e.target.value);page=0;renderRows();sync();});
  for(const [id,delta] of [['mf-prev',-1],['mf-next',1]])$(id).addEventListener('click',()=>{page+=delta;renderRows();sync();});
  $('mf-rows').addEventListener('toggle',e=>{const d=e.target;if(!d.dataset.symbol||!d.isConnected)return;if(d.open){expanded.clear();expanded.add(d.dataset.symbol);for(const other of $('mf-rows').querySelectorAll('.mf-row[open]'))if(other!==d)other.open=false;}else expanded.delete(d.dataset.symbol);syncChart();},true);
  $('mf-refresh').addEventListener('click',()=>refresh());$('mf-tg-toggle').addEventListener('click',toggle);
  document.addEventListener('visibilitychange',()=>{if(document.hidden){revision++;controller?.abort();controller=null;pending=null;clearTimeout(timer);window.SpikeMondayFibChart?.hide();}else if(active)refresh();});
  window.SpikeNavigation?.register('monday-fib',p=>{query=p.q||'';filter=Object.hasOwn(FILTERS,p.filter)?p.filter:'all';page=Math.max(0,(Number(p.page)||1)-1);pageSize=[5,20,50].includes(Number(p.size))?Number(p.size):5;$('mf-search').value=query;$('mf-page-size').value=String(pageSize);if(data)renderRows();});
  window.SpikeMondayFib={setActive,refresh};
})();
