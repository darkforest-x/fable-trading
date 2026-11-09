/* Stitch 3db5c4dc931244f8a1ea859d4acb8d49. Read-only, source-backed strategy books.
 * Picker refinement: 040e7b6c98104d0f9bb77ec995dabb1a.
 * Month calendar: Stitch 744049734c8745ec9111be91a2fca95c.
 * Daily settlements: Stitch 35ddb15e36454b709d1ade43eedae412.
 * Time navigation: Stitch 06e18dbe8d574fafb80a01fe6b5db29f.
 * Live rule facts: Stitch 4a92f2ca253d46b2a9a15478daa8216a; execution rules remain API-backed.
 * Live-first grouped picker: Stitch 31e8546ebdde4a7f978815d39f0d6d9e.
 * The selected book and local display order are independent of the paper account. */
(() => {
  'use strict';
  const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const BREADTH='breadth-ema20',LIVE_BREADTH='live-breadth';
  const label=t=>({60:'60m',5:'5m',[BREADTH]:'全市场下轨反弹'}[t]||t);
  // Stitch 1e1df06e25b64257b3f0de741e8f8c81: independent pre-touch account.
  const TITLES={'live-breadth':'全市场宽度信号 · SOL 计资金','live-wick6x3':'插针收盘 · 6份3倍','live-wick6x3mid':'浅穿中点 · 6份3倍','live-wick3x3':'插针收盘 · 3份3倍','live-wick6x3hi':'插针收盘 · 6份3倍 · 高分加仓','live-wick3x3far':'插针收盘 · 3份3倍 · 离1.618≥1.5%','live-allin3':'触价前评分 · 模拟运行',60:'等待回收',5:'快速确认',v3:'分数决定目标',v3add:'逆向分步加仓',spike:'排列过滤插针',v3lock:'回收后锁住利润',allin3:'单仓 · 梭哈3倍',v3add20:'20份动态保证金 · 3倍加仓'};
  // Existing account picker and book, Stitch 1caa5938d14240e6b8e95e0436d44c5e.
  const BOOK='<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" aria-hidden="true"><path d="M12 5C8 2 4 3 2 4v15c3-1 6-1 10 2 4-3 7-3 10-2V4c-2-1-6-2-10 1Zm0 0v16"/></svg>';
  const BACKTEST='<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" aria-hidden="true"><path d="M3 17 9 11l4 4 8-10M15 5h6v6"/></svg>';
  const num=(v,d=2)=>typeof v==='number'&&Number.isFinite(v)?v.toLocaleString('en-US',{maximumFractionDigits:d}):'—';
  const ORDER_KEY='spike:paper-account-order:v1';
  const LAYOUT_KEY='spike:paper-account-layout:v2';
  const isWickTrack=t=>['live-wick6x3','live-wick6x3mid','live-wick3x3','live-wick6x3hi','live-wick3x3far'].includes(t);
  function overviewHTML(d,track){
    if(track===LIVE_BREADTH){const b=d?.[LIVE_BREADTH];return `<div class="rs-section-title"><h1>${esc(label(track))}<em>${esc(TITLES[track])}</em></h1></div><div class="rs-facts"><div><small>信号</small><b>${num(b?.count_min,0)}–${num(b?.count_max,0)} <small>个币 / 5分钟</small></b></div><div><small>观察</small><b>${num(b?.observe_minutes,0)} <small>分钟零新增</small></b></div><div><small>持有</small><b>${num(b?.hold_days,0)} <small>天</small></b></div></div>${!b?'<p class="rs-warning">策略规则尚未读取，参数保持空缺。稍后重新打开可重试。</p>':''}`;}
    const current=d?.tracks?.[track],wick=isWickTrack(track),lock=track==='v3lock',v3=['v3','v3add','v3lock','v3add20'].includes(track);
    // The common model threshold classifies signals; each live account has its own entry gate.
    const own=wick||['allin3','live-allin3','spike'].includes(track);
    const threshold=own?d?.[track]?.min_score:v3?d?.v3?.min_score:d?.model?.threshold;
    const offset=wick?(d?.[track]?0:undefined):lock?d?.v3lock?.breakeven_offset_bp:d?.breakeven_offset_bp;
    const protection=typeof offset==='number'&&Number.isFinite(offset)?`${offset>0?'+':''}${num(offset/100)}%`:'—';
    return `<div class="rs-section-title"><h1>${esc(label(track))}<em>${esc(TITLES[track])}</em></h1></div><div class="rs-facts"><div><small>确认周期</small><b>${num(current?.confirm_minutes,0)} <small>分钟</small></b></div><div><small>入场分门槛</small><b>≥ ${num(threshold,4)}</b></div><div><small>${lock?'利润锁':'保护偏移'}</small><b>${protection}</b></div></div>${!current?'<p class="rs-warning">策略规则尚未读取，参数保持空缺。稍后重新打开可重试。</p>':''}`;
  }
  function flowHTML(track){
    if(track===LIVE_BREADTH)return '<ol class="rs-flow"><li><b>数币</b><span>每根 UTC 5 分钟 K 线统计首次碰到周一下轨 1.618 的币数；31–62 个 OKX 币（近似币安 50–99）记为信号。</span></li><li><b>等 30 分钟</b><span>之后 30 分钟没有新币碰下轨才确认；有新币就取消。</span></li><li><b>买入</b><span>按 OKX 1 分钟开盘价买 SOL，1 倍全仓；BTC、ETH 同额记为对照，不计入净值。</span></li><li><b>第 6 天平仓</b><span>不设止损，6 天后按 1 分钟开盘价平仓；持有期间的新信号跳过。</span></li></ol>';
    const wick=isWickTrack(track);
    const entry=track==='live-allin3'?'冻结入场评分仅使用触价前已收盘信息；当前模拟运行。':wick?'触价5m收盘时评分并检查收回比例，收盘后市价入场，不回记；当前模拟运行。':'原回测评分包含触价5m收盘信息，收盘后回记触价入场。';
    const confirm=wick?'触价所在1小时K线之后，下一根1小时K线收盘，以 1.618 判断是否收回。未收回则退出；目标可在确认前成交。':'触价所在K线之后，下一根确认周期K线收盘。未收回则退出；目标可在确认前成交。';
    const target=track==='live-wick6x3mid'?'<li><b>定止盈</b><span>触价 5 分钟收盘价超出 1.618 不足 1.5% 时，全仓止盈取纽约周一高低点的中点。达到 1.5% 时，多单取周一低点，空单取周一高点。</span></li>':'';
    const protection=wick?'收回后止损移到 1.618，不加偏移。':'收回且越过保护价才移动止损；收回但未越过时按确认收盘退出。多单加偏移，空单减偏移。';
    return `<ol class="rs-flow"><li><b>划区间</b><span>纽约周一高低点 H / L，宽度 W=H−L。上沿 H+0.618W，下沿 L−0.618W。</span></li><li><b>等触价</b><span>每周每侧首次触及；下沿做多、上沿做空。${entry}分数不是胜率。</span></li>${target}<li><b>等回收</b><span>${confirm}</span></li><li><b>保护余仓</b><span>${protection}</span></li></ol>`;
  }
  function accountOrder(saved,tracks){const defaults=[...tracks.filter(t=>t==='spike'),...tracks.filter(t=>t!=='spike')];return [...new Set([...(Array.isArray(saved)?saved:[]).filter(t=>defaults.includes(t)),...defaults])];}
  function moveAccount(order,id,index){const next=order.filter(t=>t!==id);if(!order.includes(id))return [...order];next.splice(Math.max(0,Math.min(next.length,index)),0,id);return next;}
  function accountLayout(saved,tracks,legacy=null){
    const valid=saved&&Array.isArray(saved.main)&&Array.isArray(saved.more),defaults=accountOrder(legacy,tracks),seen=new Set(),layout={main:[],more:[]};
    if(valid)for(const group of ['main','more'])for(const id of saved[group])if(tracks.includes(id)&&!seen.has(id)){layout[group].push(id);seen.add(id);}
    for(const id of defaults)if(!seen.has(id)){layout[id.startsWith('live-')||id===BREADTH?'main':'more'].push(id);seen.add(id);}
    return layout;
  }
  function moveAccountGroup(layout,id,group,index){
    if(!['main','more'].includes(group)||![...layout.main,...layout.more].includes(id))return {main:[...layout.main],more:[...layout.more]};
    const next={main:layout.main.filter(t=>t!==id),more:layout.more.filter(t=>t!==id)};
    next[group].splice(Math.max(0,Math.min(next[group].length,index)),0,id);return next;
  }
  function pnlView(value){
    if(typeof value!=='number'||!Number.isFinite(value))return {text:'—',tone:'',title:'已实现盈亏暂未读取'};
    const sign=value>0?'+':value<0?'−':'',amount=Math.abs(value);
    return {text:sign+(amount>0&&amount<.005?'<0.01':amount.toLocaleString('en-US',{minimumFractionDigits:2,maximumFractionDigits:2})),tone:value>0?'rp-positive':value<0?'rp-negative':'',title:'已实现净盈亏 · USDT · 不含浮盈亏和补充本金'};
  }
  function pickerHTML(tracks){return tracks.map(t=>`<div class="rs-account-row" data-account-row="${esc(t)}"><button type="button" data-account-drag="${esc(t)}" aria-label="拖动调整 ${esc(label(t))} 顺序" aria-description="拖动排序或移入移出常用；Alt 加上下键排序，Alt 加左键移入常用、右键收入更多" title="拖动排序或移入移出常用 · Alt + ↑/↓ 排序 · Alt + ←/→ 分组"><svg width="14" height="20" viewBox="0 0 14 20" fill="currentColor" aria-hidden="true"><circle cx="4" cy="5" r="1.2"/><circle cx="10" cy="5" r="1.2"/><circle cx="4" cy="10" r="1.2"/><circle cx="10" cy="10" r="1.2"/><circle cx="4" cy="15" r="1.2"/><circle cx="10" cy="15" r="1.2"/></svg></button><button type="button" data-strategy-select="${esc(t)}" aria-pressed="false"><span>${esc(label(t))}${t===LIVE_BREADTH?'<em class="rs-account-tag">宽度·SOL</em>':t==='live-wick6x3mid'?'<em class="rs-account-tag">浅穿中点</em>':''}</span>${t===BREADTH?'<small title="查看前向模拟账户">模拟</small>':`<strong data-account-pnl="${esc(t)}">—</strong>`}<i aria-hidden="true">✓</i></button><button type="button" data-strategy-book="${esc(t)}" aria-label="查看 ${esc(label(t))} 策略小册" title="策略小册">${BOOK}</button><button type="button" data-strategy-backtest="${esc(t)}" aria-label="查看 ${esc(label(t))} 回测交易" title="回测交易">${BACKTEST}</button></div>`).join('');}
  function tableHTML(headers,rows){return `<div class="rs-table-wrap"><table><thead><tr>${headers.map(h=>`<th scope="col">${esc(h)}</th>`).join('')}</tr></thead><tbody>${rows.map(row=>`<tr>${row.map((v,i)=>i?`<td>${typeof v==='number'?num(v):esc(v??'—')}</td>`:`<th scope="row">${esc(v)}</th>`).join('')}</tr>`).join('')}</tbody></table></div>`;}
  const historyDate=ms=>new Date(ms).toISOString().slice(0,10);
  function validateHistory(h,track){
    if(h?.schema!==1||h.meta?.track!==track||!h.summary||!Array.isArray(h.points)||h.points.length<2||!h.points.every((p,i)=>Array.isArray(p)&&p.length===5&&p.every(Number.isFinite)&&p[1]>=0&&(!i||p[0]>=h.points[i-1][0]))||!['monthly','yearly','sides','recent'].every(k=>Array.isArray(h[k]))||!h.distribution||!h.holding)throw Error('历史曲线的数据或策略不匹配');
    return h;
  }
  function heatmapHTML(h){
    const years=[...new Set(h.monthly.map(p=>p[0].slice(0,4)))],values=new Map(h.monthly),unit=h.refilled?'USDT':'%';
    const max=Math.max(1,...h.monthly.map(p=>Math.abs(p[1]||0)));
    const cell=(v,key='')=>{
      if(v==null&&!values.has(key))return '<td class="rs-month-missing">—</td>';
      const value=num(v,h.refilled?0:1),title=key?`${key} · ${num(v)} ${unit} · 点击查看每日盈亏`:num(v)+' '+unit;
      return `<td style="--cell-alpha:${(.06+.3*Math.sqrt(Math.abs(v||0)/max)).toFixed(3)}" class="${v>0?'is-gain':v<0?'is-loss':'is-flat'}${key?' rs-month-cell':''}" title="${esc(title)}">${key?`<button type="button" data-history-month="${esc(key)}" aria-haspopup="dialog" aria-expanded="false" aria-controls="rs-month-calendar" aria-label="${esc(title)}">${value}</button>`:value}</td>`;
    };
    return `<div class="rs-table-wrap rs-heatmap"><table aria-label="${h.refilled?'月度交易净盈亏':'月度收益率'}"><thead><tr><th>年 / 月</th>${Array.from({length:12},(_,i)=>`<th>${i+1}</th>`).join('')}<th>全年</th></tr></thead><tbody>${years.map(y=>`<tr><th>${y}</th>${Array.from({length:12},(_,i)=>{const key=y+'-'+String(i+1).padStart(2,'0');return cell(values.get(key),values.has(key)?key:'');}).join('')}${cell(h.yearly.find(p=>p[0]===y)?.[1])}</tr>`).join('')}</tbody></table></div>`;
  }
  // Read the complete saved settlement path, never the truncated recent-trade list.
  // Point columns: UTC timestamp, equity, cumulative P&L OR return %, DD %, cash.
  // Equity changes less cash changes are trading P&L, including same-time refills.
  function monthCalendar(h,key){
    if(!/^\d{4}-(0[1-9]|1[0-2])$/.test(key)||!h.monthly.some(p=>p[0]===key))return null;
    const [year,month]=key.split('-').map(Number),first=h.points[0],last=h.points.at(-1);
    const begin=Date.UTC(year,month-1,1),end=Date.UTC(year,month,1),dayMs=86400000;
    let index=1,previous=first;
    while(index<h.points.length&&h.points[index][0]<begin)previous=h.points[index++];
    const days=[];
    for(let time=begin;time<end;time+=dayMs){
      const date=historyDate(time),covered=date>=historyDate(first[0])&&date<=historyDate(last[0]);
      const start=previous[1];let pnl=0,changed=false;
      while(index<h.points.length&&h.points[index][0]<time+dayMs){
        const p=h.points[index++],delta=p[1]-previous[1]-(p[4]-previous[4]);
        pnl+=delta;changed||=Math.abs(delta)>1e-8;previous=p;
      }
      if(Math.abs(pnl)<1e-8)pnl=0;
      days.push({date,day:days.length+1,covered,pnl:covered?pnl:null,changed,
        returnPct:covered&&!h.refilled&&start>0?100*(previous[1]/start-1):null});
    }
    const months=h.monthly.map(p=>p[0]),at=months.indexOf(key);
    return {key,title:`${year}年${month}月`,days,offset:(new Date(begin).getUTCDay()+6)%7,
      pnl:days.reduce((sum,d)=>sum+(d.pnl||0),0),returnPct:h.refilled?null:h.monthly[at][1],refilled:h.refilled,
      previous:months[at-1]||null,next:months[at+1]||null,firstMs:first[0],lastMs:last[0]};
  }
  const signed=v=>v==null?'—':pnlView(v).text;
  function compactPnl(v){
    if(Math.abs(v)<1000)return signed(v);
    const scale=Math.abs(v)>=1e6?1e6:1e3;
    return (v<0?'−':'+')+num(Math.abs(v)/scale,1)+(scale===1e6?'m':'k');
  }
  function calendarDayLabel(d){return `${d.date} · ${d.covered?`${signed(d.pnl)} USDT${d.returnPct==null?'':` · 日收益 ${signed(d.returnPct)}%`}${d.changed?'':' · 无已实现盈亏'}`:'未覆盖'}`;}
  const closeAmount=(a,b)=>Number.isFinite(a)&&Number.isFinite(b)&&Math.abs(a-b)<=Math.max(1e-6,Math.abs(b)*1e-9);
  function validateDayPage(p,q,offset,validRow){
    if(p?.schema!==1||p.track!==q.track||p.date!==q.date||p.timezone!=='UTC'||p.source!=='Binance'||p.offset!==offset||!Array.isArray(p.rows)||!p.rows.every(r=>validRow(r)&&historyDate(r.exit_ms)===q.date)||new Set(p.rows.map(r=>r.id)).size!==p.rows.length||!Number.isInteger(p.total)||p.total<offset+p.rows.length||p.has_more!==(offset+p.rows.length<p.total)||(p.has_more&&!p.rows.length))throw Error('当日交易数据不完整，请重试');
    if(!closeAmount(p.pnl,q.expectedPnl))throw Error('交易合计与日历金额不一致，请刷新小册后重试');
    return p;
  }
  function createDayModel({read,validRow,cancel=()=>{},change=()=>{}}){
    let version=0,pending=null,state={track:'',date:'',expectedPnl:0,rows:[],data:null,busy:false,error:''};
    function pause(){version++;cancel();pending=null;state.busy=false;}
    async function load(){
      if(pending||!state.date)return pending;
      const revision=version,q={track:state.track,date:state.date,expectedPnl:state.expectedPnl},offset=state.rows.length;
      state.busy=true;state.error='';change(state);
      pending=Promise.resolve().then(async()=>{
        if(revision!==version)return;
        try{
          const p=validateDayPage(await read({track:q.track,date:q.date,offset,limit:50}),q,offset,validRow);
          if(revision!==version)return;
          const rows=[...state.rows,...p.rows];
          if(new Set(rows.map(r=>r.id)).size!==rows.length||(state.data&&p.total!==state.data.total)||(!p.has_more&&!closeAmount(rows.reduce((sum,r)=>sum+r.pnl,0),p.pnl)))throw Error('当日分页合计不一致，请重新选择日期');
          state={...state,rows,data:p};
        }catch(e){if(revision===version)state.error=e.message||'当日交易读取失败，请重试';}
        finally{if(revision===version){state.busy=false;pending=null;change(state);}}
      });return pending;
    }
    return {load,pause,getState:()=>state,select(track,date,expectedPnl){pause();state={track,date,expectedPnl,rows:[],data:null,busy:false,error:''};return load();}};
  }
  const utcTime=ms=>new Date(ms).toISOString().slice(0,16).replace('T',' ');
  function dayTradeHTML(r){
    const score=r.entry_rules?.score,view=pnlView(r.pnl),side=r.side===1?'做多':'做空';
    return `<button type="button" class="rs-day-trade" data-day-trade="${r.id}" aria-label="查看 ${esc(r.symbol.replace(/USDT$/,''))} ${side} ${utcTime(r.entry_ms)} 历史K线"><span class="rs-day-trade-head"><span><b>${esc(r.symbol.replace(/USDT$/,''))}</b><em class="${r.side===1?'rp-positive':'rp-negative'}">${side}</em></span><strong class="${view.tone}">${view.text}<small> USDT</small></strong></span><span class="rs-day-trade-times"><span>开 ${utcTime(r.entry_ms)}</span><span>平 ${utcTime(r.exit_ms)}</span></span><span class="rs-day-trade-foot"><span>${r.eventId?'下轨反弹':Number.isFinite(score)?'评分 '+score.toFixed(4):'评分未留存'} · 单笔 ${signed(r.return_pct)}%</span><span>查看K线 ↗</span></span></button>`;
  }
  function dayTradesHTML(s){
    const [y,m,d]=s.date.split('-').map(Number);
    return `<header class="rs-calendar-header"><button type="button" data-calendar-back aria-label="返回月历">‹</button><div class="rs-day-title"><strong id="rs-calendar-title" tabindex="-1">${y}年${m}月${d}日</strong><small>当日结算 · UTC</small></div><div class="rs-calendar-nav"><button type="button" data-calendar-close aria-label="关闭每日盈亏日历">×</button></div></header><div class="rs-calendar-summary"><div><small>当日净盈亏 / USDT</small><strong class="${pnlView(s.expectedPnl).tone}">${signed(s.expectedPnl)}</strong></div><div><small>结算交易</small><strong>${s.data?s.data.total:'—'}<small> 笔</small></strong></div></div><p class="rs-day-hint">点交易查看历史K线 · 单笔 % 按名义本金</p><div class="rs-day-scroll" tabindex="-1"><p class="rs-day-status" role="status">${esc(s.error||(s.busy?'正在读取当日交易…':s.data?.total===0?'当天无结算交易':''))}</p>${s.error?'<button type="button" data-day-retry>重试读取</button>':''}<div class="rs-day-rows">${s.rows.map(dayTradeHTML).join('')}</div>${s.data?.has_more?`<button type="button" class="rs-day-more" data-day-more ${s.busy?'disabled':''}>${s.busy?'读取中…':'再显示 50 笔'}</button>`:''}${s.data?`<small class="rs-day-count">显示 ${s.rows.length} / ${s.data.total} 笔 · 按平仓时间排列</small>`:''}</div><footer>按 UTC 结算日归集 · 已扣原回测成本<br>补充本金不计收益 · K线回放时间显示为北京时间</footer>`;
  }
  function calendarHTML(m){
    const cells=m.days.map(d=>`<button type="button" class="rs-calendar-day ${!d.covered?'is-uncovered':d.pnl>0?'is-gain':d.pnl<0?'is-loss':'is-flat'}" data-calendar-day="${d.day}" ${!d.covered?'disabled':''} aria-label="${esc(calendarDayLabel(d))}" title="${esc(calendarDayLabel(d))}"><time datetime="${d.date}">${d.day}</time><b>${d.changed&&d.covered?compactPnl(d.pnl):'—'}</b><small>${!d.covered?'未覆盖':d.changed&&d.returnPct!=null?signed(d.returnPct)+'%':''}</small></button>`);
    const blanks=n=>'<span class="rs-calendar-blank" aria-hidden="true"></span>'.repeat(n);
    return `<header class="rs-calendar-header"><div><strong id="rs-calendar-title" tabindex="-1">${m.title}</strong><small>每日已实现盈亏 · USDT · UTC</small></div><div class="rs-calendar-nav"><button type="button" data-calendar-nav="previous" aria-label="上一月" ${m.previous?'':'disabled'}>‹</button><button type="button" data-calendar-nav="next" aria-label="下一月" ${m.next?'':'disabled'}>›</button><button type="button" data-calendar-close aria-label="关闭每日盈亏日历">×</button></div></header><div class="rs-calendar-summary"><div><small>月度净盈亏 / USDT</small><strong class="${pnlView(m.pnl).tone}">${signed(m.pnl)}</strong></div>${m.refilled?'':`<div><small>月收益率</small><strong class="${pnlView(m.returnPct).tone}">${signed(m.returnPct)}${m.returnPct==null?'':'%'}</strong></div>`}</div><div class="rs-calendar-week" aria-hidden="true">${['一','二','三','四','五','六','日'].map(d=>`<span>${d}</span>`).join('')}</div><div class="rs-calendar-grid" role="group" aria-label="${m.title}每日盈亏">${blanks(m.offset)}${cells.join('')}${blanks((7-(m.offset+cells.length)%7)%7)}</div><div class="rs-calendar-detail" role="status">点日期查看当天交易 · — 无已实现盈亏</div><footer>按结算日统计 · 不含浮盈亏${m.refilled?' · 补充本金不计收益':''}<br>数据截至 ${new Date(m.lastMs).toISOString().slice(0,16).replace('T',' ')} UTC</footer>`;
  }
  const DAY=86400000;
  function historyWindow(h,period='all',anchor=h.points.at(-1)[0]){
    const first=h.points[0][0],last=Math.max(first+1,h.points.at(-1)[0]);
    if(period==='all')return {period,start:first,end:last};
    const d=new Date(Math.max(first,Math.min(last,anchor))),y=d.getUTCFullYear(),m=d.getUTCMonth(),day=d.getUTCDate();
    const start=period==='month'?Date.UTC(y,m,1):Date.UTC(y,m,day),end=period==='month'?Date.UTC(y,m+1,1):start+DAY;
    return {period,start:Math.max(first,start),end:Math.max(Math.max(first,start)+1,Math.min(last,end-1))};
  }
  function shiftHistoryWindow(h,view,direction){
    if(view.period==='all')return {...view};
    const first=h.points[0][0],last=h.points.at(-1)[0];
    if(view.period==='day'||view.period==='month'){
      const anchor=direction<0?view.start-1:view.end+1;
      if(anchor<first||anchor>last)return {...view};
      return historyWindow(h,view.period,anchor);
    }
    const span=view.end-view.start,start=Math.max(first,Math.min(last-span,view.start+direction*span));
    return {period:'custom',start,end:Math.min(last,start+span)};
  }
  function historyAxes(h,mode,view){
    // Include the step carried into the window and every same-time settlement.
    // Never aggregate away a loss followed by a refill or reset the DD peak.
    let lo=0,hi=h.points.length;
    while(lo<hi){const mid=(lo+hi)>>1;if(h.points[mid][0]<view.start)lo=mid+1;else hi=mid;}
    let min=Infinity,max=-Infinity,dd=0;
    for(let i=Math.max(0,lo-1);i<h.points.length&&h.points[i][0]<=view.end;i++){
      const p=h.points[i],v=p[mode==='returns'?2:1];min=Math.min(min,v);max=Math.max(max,v);dd=Math.min(dd,p[3]);
      if(h.refilled&&mode!=='returns'){min=Math.min(min,p[4]);max=Math.max(max,p[4]);}
    }
    const pad=Math.max((max-min)*.1,Math.abs(max)*.002,.01);
    const span=max-min,formatter=v=>Math.abs(v)>=1e6&&span>1e5?num(v/1e6,2)+'m':Math.abs(v)>=1e3&&span>2000?num(v/1e3,1)+'k':num(v,span<10?2:0);
    return [{min:min-pad,max:max+pad,axisLabel:{formatter,showMinLabel:false,showMaxLabel:false}},{min:dd<0?dd*1.12:-1,max:0}];
  }
  function historyWindowLabel(view){
    const start=historyDate(view.start),end=historyDate(view.end);
    return start===end?start:`${start} — ${end}`;
  }
  function historyToolbar(){
    return `<div class="rs-timeline-toolbar"><div class="rs-chart-modes rs-time-modes" role="group" aria-label="资金曲线时间范围">${[['all','全部'],['month','月度'],['day','日度']].map(([k,v])=>`<button type="button" data-history-range="${k}" aria-pressed="${k==='all'}">${v}</button>`).join('')}</div><div class="rs-time-nav"><button type="button" data-history-step="-1" aria-label="上一时间段">‹</button><span data-history-window aria-live="polite"></span><button type="button" data-history-step="1" aria-label="下一时间段">›</button><button type="button" data-history-latest>最新</button></div></div><div class="rs-chart-legend"><span><i></i>已实现资金</span><span><i></i>历史高点回撤</span><small>UTC · 左右拖动 · 双指缩放</small></div>`;
  }
  function historyHTML(h){
    const s=h.summary,pf=s.profit_factor_state==='no_losses'?'∞':num(s.profit_factor),unit=h.refilled?'USDT':'%';
    const metrics=[[h.refilled?'交易净盈亏':'累计净收益',num(h.refilled?s.pnl:s.return_pct)+(h.refilled?' USDT':'%')],['最大已实现回撤',num(s.max_drawdown_pct)+'%'],['资金成交胜率',num(s.win_pct,1)+'%'],['已平仓笔数',num(s.trades,0)],['盈利因子',pf],['期末资金',num(s.end)+' USDT']];
    return `<div class="rs-history-meta"><span>${historyDate(h.points[0][0])} — ${historyDate(h.points.at(-1)[0])} · UTC</span><span>1000 USDT 起步 · 已实现净值</span></div><p class="rs-research-note">${esc(h.meta.note)}</p><div class="rs-backtest-metrics">${metrics.map(([k,v])=>`<div><small>${k}</small><strong>${v}</strong></div>`).join('')}</div><figure class="rs-primary-figure"><figcaption><b>资金路径与回撤</b><div class="rs-chart-modes" role="group" aria-label="历史曲线显示"><button type="button" data-history-mode="equity" aria-pressed="true">资金 USDT</button><button type="button" data-history-mode="returns" aria-pressed="false">${h.refilled?'交易净盈亏':'累计收益 %'}</button></div></figcaption>${historyToolbar()}<div class="rs-history-chart" tabindex="0" role="region" aria-label="历史资金与回撤曲线，左右方向键平移，Home 查看全部"></div><p class="rs-muted">${h.refilled?'灰线为累计净投入；补充本金不计入交易收益。回撤按每轮本金计算，补充本金重置高水位。':'蓝线为已实现资金／累计净收益，红色为相对历史高点的回撤。'} ${h.meta.track===BREADTH?'曲线按平仓结算，不含持仓浮盈亏；上方最大盯市回撤按每 5 分钟估值。':'曲线保留每笔结算，不含持仓浮盈亏；同一时刻的先亏损后补款也保留。'}</p></figure><div class="rs-period-grid"><figure><figcaption>${h.refilled?'月度交易净盈亏':'月度收益热力图'} <small>/ ${unit}</small></figcaption>${heatmapHTML(h)}</figure><figure><figcaption>${h.refilled?'年度交易净盈亏':'年度收益'} <small>/ ${unit}</small></figcaption><div class="rs-bar-chart" data-history-chart="yearly" role="img" aria-label="年度收益柱状图"></div></figure></div><div class="rs-chart-grid"><figure><figcaption>单笔净收益分布 <small>/ 名义本金收益 % · 纵轴笔数</small></figcaption><div class="rs-bar-chart" data-history-chart="distribution" role="img" aria-label="单笔净收益分布"></div></figure><figure><figcaption>持仓时长分布 <small>/ 笔</small></figcaption><div class="rs-bar-chart" data-history-chart="holding" role="img" aria-label="持仓时长分布"></div></figure></div><div class="rs-sides"><h3>多空表现</h3>${tableHTML(['方向','成交笔数','胜率 %','净盈亏 USDT'],h.sides)}</div><details class="rs-detail-table"><summary>逐笔成交 · 最近 ${h.recent.length} 笔 / 共 ${s.trades} 笔</summary>${tableHTML(['币种','开仓 UTC','平仓 UTC','方向','净盈亏 USDT','净收益 %'],h.recent.map(r=>[r[0],new Date(r[1]).toISOString().slice(0,16).replace('T',' '),new Date(r[2]).toISOString().slice(0,16).replace('T',' '),...r.slice(3)]))}</details>`;
  }
  function equityOptions(h,mode='equity',dark=false,view=historyWindow(h)){
    const c=dark?'#abb8c5':'#607382',gridColor=dark?'#ffffff12':'#7184941c',returns=mode==='returns';
    const name=returns?(h.refilled?'累计交易净盈亏':'累计净收益'):'已实现资金',unit=returns&&!h.refilled?'%':' USDT',bounds=historyAxes(h,mode,view);
    const axis=i=>({gridIndex:i,type:'time',min:h.points[0][0],max:Math.max(h.points[0][0]+1,h.points.at(-1)[0]),boundaryGap:false,splitNumber:4,axisLine:{show:false},axisTick:{show:false},axisLabel:{show:i===1,color:c,fontSize:10,hideOverlap:true},splitLine:{show:false}});
    const series=[{name,type:'line',step:'end',showSymbol:false,smooth:false,xAxisIndex:0,yAxisIndex:0,data:h.points.map(p=>[p[0],p[returns?2:1]]),lineStyle:{color:'#2586e7',width:2},itemStyle:{color:'#2586e7'},areaStyle:{color:{type:'linear',x:0,y:0,x2:0,y2:1,colorStops:[{offset:0,color:'#2586e728'},{offset:1,color:'#2586e703'}]}}},
      {name:h.refilled?'本轮本金回撤':'已实现回撤',type:'line',step:'end',showSymbol:false,smooth:false,xAxisIndex:1,yAxisIndex:1,data:h.points.map(p=>[p[0],p[3]]),lineStyle:{color:'#e04b66',width:1.4},itemStyle:{color:'#e04b66'},areaStyle:{color:'#e04b66',opacity:.12}}];
    if(h.refilled&&!returns)series.push({name:'累计净投入',type:'line',step:'end',showSymbol:false,data:h.points.map(p=>[p[0],p[4]]),lineStyle:{color:'#8897a6',type:'dashed',width:1.2},itemStyle:{color:'#8897a6'}});
    const zoom={xAxisIndex:[0,1],filterMode:'none',startValue:view.start,endValue:view.end,minValueSpan:60000,throttle:40};
    return {animation:false,useUTC:true,grid:[{left:54,right:16,top:28,height:'46%'},{left:54,right:16,top:'64%',height:'18%'}],axisPointer:{link:[{xAxisIndex:'all'}],lineStyle:{color:dark?'#ffffff45':'#71849455',type:'dashed'}},tooltip:{trigger:'axis',confine:true,backgroundColor:dark?'#1c222c':'#fff',borderColor:gridColor,padding:[10,14],textStyle:{color:dark?'#e8edf4':'#182b39',fontSize:11},extraCssText:'max-width:calc(100% - 8px);white-space:normal;overflow-wrap:anywhere;border-radius:12px;box-shadow:0 8px 24px #071b301a;',formatter:items=>items.length?`${utcTime(items[0].value[0])} UTC<br>`+items.map(v=>`${esc(v.seriesName)}: ${num(v.value[1])}${v.seriesIndex===1?'%':unit}`).join('<br>'):''},xAxis:[axis(0),axis(1)],yAxis:[{...bounds[0],type:'value',scale:true,name:unit.trim(),splitNumber:4,nameTextStyle:{color:c,fontSize:10},axisLabel:{...bounds[0].axisLabel,color:c,fontSize:10},splitLine:{lineStyle:{color:gridColor,type:'dashed'}}},{...bounds[1],gridIndex:1,type:'value',splitNumber:3,name:'回撤 %',nameTextStyle:{color:c,fontSize:10},axisLabel:{color:c,fontSize:10,formatter:v=>num(v,1)+'%'},splitLine:{lineStyle:{color:gridColor}}}],dataZoom:[{...zoom,id:'history-inside',type:'inside',zoomOnMouseWheel:'ctrl',moveOnMouseWheel:'shift',moveOnMouseMove:true,preventDefaultMouseMove:false},{...zoom,id:'history-slider',type:'slider',left:54,right:16,bottom:8,height:24,showDetail:false,brushSelect:false,borderColor:'transparent',backgroundColor:dark?'#ffffff08':'#7184940c',fillerColor:'#2586e724',handleSize:'110%',handleStyle:{color:dark?'#1c222c':'#fff',borderColor:'#2586e7',borderWidth:1.5},dataBackground:{lineStyle:{color:'#2586e7',opacity:.35},areaStyle:{color:'#2586e7',opacity:.05}},selectedDataBackground:{lineStyle:{color:'#2586e7',opacity:.7},areaStyle:{color:'#2586e7',opacity:.12}}}],series};
  }
  function researchHTML(data,t,history=null){
    const r=data.accounts[t];if(!r)throw Error('该策略的小册资料尚未收录');
    const image=im=>`<figure class="rs-history-figure"><img class="rs-research-image" loading="lazy" src="/static/strategy-booklet/${esc(im.file)}" alt="${esc(im.caption)}"><figcaption>${esc(im.caption)} <a href="/static/strategy-booklet/${esc(im.file)}" target="_blank" rel="noopener">查看原图 ↗</a></figcaption></figure>`;
    return `<div class="rs-section-title"><h2>历史回测</h2><span class="rs-badge">${esc(r.status)}</span></div>${history?historyHTML(history):'<p class="rs-missing-path">历史资金曲线暂时无法读取，以下保留汇总结果。</p><button type="button" data-book-retry>重试曲线</button>'}<p class="rs-chart-status" role="status"></p><details class="rs-detail-table"><summary>原研究分区间统计与参数口径</summary><p class="rs-research-note">${esc(r.note)}</p>${tableHTML(r.headers,r.rows)}${r.capital_rows?`<h3>原研究资金回放 · 多次顺序取中位数</h3>${tableHTML(r.capital_headers,r.capital_rows)}`:''}${r.detail?`<p class="rs-muted">${esc(r.detail)}</p>`:''}${t==='live-wick6x3mid'?'':`<p class="rs-muted">${esc(data.period_note)}</p><p class="rs-muted">上方曲线是预先固定的单次顺序；表中多次中位数不必等于该路径终点，不能把各区间相乘或拼接。</p>`}</details>${r.controls?`<details class="rs-detail-table"><summary>同条件随机入场对照</summary><p class="rs-muted">${esc(r.controls.note)}</p>${tableHTML(r.controls.headers,r.controls.rows)}</details>`:''}${(r.sections||[]).map(x=>`<details class="rs-detail-table"><summary>${esc(x.title)}</summary>${x.note?`<p class="rs-muted">${esc(x.note)}</p>`:''}${tableHTML(x.headers,x.rows)}</details>`).join('')}${r.images?.length||r.references?.length?`<details class="rs-detail-table"><summary>原研究图片 · 保留原版本参数</summary>${[...(r.images||[]),...(r.references||[])].map(image).join('')}</details>`:''}`;
  }

  function barOptions(r,dark=false){const c=dark?'#9ba9b8':'#738392';return {animation:!matchReduced(),animationDuration:450,grid:{left:55,right:18,top:25,bottom:r.labels.length>8?45:60},tooltip:{trigger:'axis',confine:true,valueFormatter:v=>num(v)+' '+r.unit},xAxis:{type:'category',data:r.labels,axisTick:{show:false},axisLabel:{color:c,fontSize:9,hideOverlap:true,interval:r.labels.length>8?'auto':0,formatter:v=>v.replace(' · ','\n')}},yAxis:{type:'value',name:r.unit,nameTextStyle:{color:c},axisLabel:{color:c},splitLine:{lineStyle:{color:dark?'#ffffff12':'#71849420'}}},series:[{type:'bar',barMaxWidth:40,data:r.values,itemStyle:{color:'#3285dc',borderRadius:[4,4,0,0]}}]};}
  function matchReduced(){return typeof matchMedia==='function'&&matchMedia('(prefers-reduced-motion:reduce)').matches;}
  const api={pickerHTML,accountOrder,moveAccount,pnlView,ORDER_KEY,LAYOUT_KEY,accountLayout,moveAccountGroup,overviewHTML,flowHTML,tableHTML,researchHTML,barOptions,historyHTML,heatmapHTML,monthCalendar,calendarHTML,validateDayPage,createDayModel,dayTradeHTML,dayTradesHTML,equityOptions,historyWindow,shiftHistoryWindow,historyAxes,historyWindowLabel,validateHistory,TITLES};
  if(typeof module!=='undefined'&&module.exports){module.exports=api;return;}
  function create({root,tracks,getTrack,getSnapshot,getPnl,renderRules,createFeed,onSelect,onBacktest}){
    const trigger=root.querySelector('[data-rp="track"]');
    let storage;try{storage=window.localStorage;}catch{}
    const stored=key=>{try{return JSON.parse(storage?.getItem(key)||'null');}catch{return null;}};
    const catalog=[...tracks,BREADTH];
    let layout=accountLayout(stored(LAYOUT_KEY),catalog,stored(ORDER_KEY)),drag=null,dragFrame=0,blockedUntil=0;
    const picker=document.createElement('div');picker.id='rs-account-picker';picker.className='rs-account-picker';picker.setAttribute('popover','auto');picker.setAttribute('role','dialog');picker.setAttribute('aria-label','策略');
    picker.innerHTML='<header><b>策略</b><small>已实现盈亏 · USDT</small></header><div class="rs-account-list"><div class="rs-group-title">常用策略 <span data-main-count></span></div><div class="rs-account-group" data-account-group="main" aria-label="常用策略">'+pickerHTML(layout.main)+'</div><button type="button" class="rs-more-toggle" data-account-more aria-expanded="false" aria-controls="rs-more-accounts"><span>更多策略</span><span class="rs-group-count" data-more-count></span><small data-more-current></small><span class="rs-more-chevron" aria-hidden="true">⌄</span></button><div id="rs-more-accounts" class="rs-account-group rs-more-accounts" data-account-group="more" aria-label="更多策略" hidden>'+pickerHTML(layout.more)+'</div></div><p class="rs-order-status" role="status">拖动把手排序或移入常用</p>';root.append(picker);
    const accountList=picker.querySelector('.rs-account-list'),orderStatus=picker.querySelector('.rs-order-status'),moreToggle=picker.querySelector('[data-account-more]');
    const groups={main:picker.querySelector('[data-account-group="main"]'),more:picker.querySelector('[data-account-group="more"]')};
    const accountNodes=new Map([...picker.querySelectorAll('[data-account-row]')].map(n=>[n.dataset.accountRow,n]));
    const groupOf=id=>layout.main.includes(id)?'main':'more';
    function setMore(open){
      if(!open&&groups.more.contains(document.activeElement))moreToggle.focus({preventScroll:true});
      groups.more.hidden=!open;moreToggle.setAttribute('aria-expanded',String(open));
      if(picker.matches(':popover-open')&&!drag)positionPicker();
    }
    function applyOrder(animate=false){
      const before=new Map([...accountNodes].filter(([,n])=>n.getClientRects().length).map(([id,n])=>[id,n.getBoundingClientRect().top]));
      const focused=document.activeElement;
      for(const group of ['main','more'])layout[group].forEach((id,i)=>{const n=accountNodes.get(id),parent=groups[group];if(parent.children[i]!==n)parent.insertBefore(n,parent.children[i]||null);});
      if(picker.contains(focused)&&focused.getClientRects().length&&document.activeElement!==focused)focused.focus({preventScroll:true});
      picker.querySelector('[data-main-count]').textContent=layout.main.length;
      picker.querySelector('[data-more-count]').textContent=layout.more.length;
      picker.querySelector('[data-more-current]').textContent=layout.more.includes(getTrack())?'当前：'+label(getTrack()):'';
      if(animate&&!matchReduced())for(const [id,n] of accountNodes){if(!before.has(id)||!n.getClientRects().length)continue;const delta=before.get(id)-n.getBoundingClientRect().top;n.getAnimations().forEach(a=>a.cancel());if(Math.abs(delta)>.5)n.animate([{transform:`translateY(${delta}px)`},{transform:'translateY(0)'}],{duration:160,easing:'cubic-bezier(.2,.8,.2,1)'});}
    }
    function saveOrder(id){const group=groupOf(id),text=`${label(id)} 已移至${group==='main'?'常用':'更多'}第 ${layout[group].indexOf(id)+1} 位`;try{if(!storage)throw Error();storage.setItem(LAYOUT_KEY,JSON.stringify(layout));orderStatus.textContent=text;}catch{orderStatus.textContent=text+'；浏览器未允许保存';}}
    function moveDrag(){
      if(!drag?.moved)return;
      const group=drag.y<moreToggle.getBoundingClientRect().top?'main':'more';
      if(group==='more'&&groups.more.hidden)setMore(true);
      const rows=layout[group].filter(id=>id!==drag.id),index=rows.filter(id=>{const r=accountNodes.get(id).getBoundingClientRect();return drag.y>r.top+r.height/2;}).length;
      const next=moveAccountGroup(layout,drag.id,group,index);
      if(next.main.join()!==layout.main.join()||next.more.join()!==layout.more.join()){layout=next;applyOrder();}
      picker.dataset.dropGroup=group;
      const top=drag.node.getBoundingClientRect().top-drag.dy;drag.dy=drag.y-drag.offset-top;drag.node.style.transform=`translateY(${drag.dy}px)`;
    }
    function scrollDrag(){
      if(!drag?.moved)return;const r=accountList.getBoundingClientRect(),delta=drag.y<r.top+28?-10:drag.y>r.bottom-28?10:0;
      if(delta){accountList.scrollTop+=delta;moveDrag();}dragFrame=requestAnimationFrame(scrollDrag);
    }
    function finishDrag(cancel=false){
      if(!drag)return;const done=drag;drag=null;cancelAnimationFrame(dragFrame);done.node.classList.remove('is-dragging');done.node.style.transform='';picker.classList.remove('is-sorting');picker.style.removeProperty('--rs-picker-height');delete picker.dataset.dropGroup;
      if(picker.hasPointerCapture?.(done.pointer))picker.releasePointerCapture(done.pointer);
      if(done.moved){blockedUntil=Date.now()+700;if(cancel){layout=done.before;applyOrder();setMore(done.moreOpen);orderStatus.textContent='已取消排序';}else saveOrder(done.id);}
      sync();if(picker.matches(':popover-open'))positionPicker();
    }
    picker.addEventListener('pointerdown',e=>{const grip=e.target.closest('[data-account-drag]');if(!grip||e.button!==0||e.isPrimary===false)return;e.preventDefault();grip.focus({preventScroll:true});for(const n of accountNodes.values())n.getAnimations().forEach(a=>a.cancel());const node=grip.closest('[data-account-row]');drag={id:grip.dataset.accountDrag,node,pointer:e.pointerId,start:e.clientY,y:e.clientY,offset:e.clientY-node.getBoundingClientRect().top,dy:0,moved:false,before:{main:[...layout.main],more:[...layout.more]},moreOpen:!groups.more.hidden};picker.setPointerCapture(e.pointerId);});
    picker.addEventListener('pointermove',e=>{if(!drag||e.pointerId!==drag.pointer)return;drag.y=e.clientY;if(!drag.moved&&Math.abs(e.clientY-drag.start)>5){drag.moved=true;picker.style.setProperty('--rs-picker-height',Math.max(100,innerHeight-picker.getBoundingClientRect().top-8)+'px');drag.node.classList.add('is-dragging');picker.classList.add('is-sorting');dragFrame=requestAnimationFrame(scrollDrag);}if(drag.moved){e.preventDefault();moveDrag();}},{passive:false});
    picker.addEventListener('pointerup',e=>{if(e.pointerId===drag?.pointer){const r=accountList.getBoundingClientRect();finishDrag(e.clientX<r.left||e.clientX>r.right||e.clientY<r.top||e.clientY>r.bottom);}});
    picker.addEventListener('pointercancel',e=>{if(e.pointerId===drag?.pointer)finishDrag(true);});
    picker.addEventListener('lostpointercapture',()=>finishDrag(true));
    picker.addEventListener('dragstart',e=>e.preventDefault());
    window.addEventListener('blur',()=>finishDrag(true));
    window.addEventListener('storage',e=>{if([LAYOUT_KEY,ORDER_KEY,null].includes(e.key)&&!drag){layout=accountLayout(stored(LAYOUT_KEY),catalog,stored(ORDER_KEY));applyOrder();if(picker.matches(':popover-open'))positionPicker();}});
    applyOrder();
    const dialog=document.createElement('dialog');dialog.className='rs-book';dialog.setAttribute('aria-label','策略小册');
    dialog.innerHTML=`<article class="rs-sheet"><header class="rs-header"><span>${BOOK} 策略小册</span><b data-book-label tabindex="-1"></b><button type="button" data-book-close aria-label="关闭策略小册">×</button></header><div class="rs-layout"><nav class="rs-toc" aria-label="小册章节">${[['overview','概览'],['research','历史回测'],['rules','执行规则'],['forward','前向资金'],['sources','来源']].map(([id,title],i)=>`<button type="button" data-book-chapter="${id}"><small>0${i+1}</small>${title}</button>`).join('')}</nav><div class="rs-reading"><section id="rs-overview"></section><section id="rs-research" aria-live="polite"></section><section id="rs-rules"></section><section id="rs-forward"><div class="rs-section-title"><h2>前向模拟资金曲线</h2></div><p>当前策略完整账本 · USDT · 已实现净值</p><div class="rs-forward-status" role="status"></div><div class="rs-forward-metrics"></div><div class="rs-forward-chart" role="img" aria-label="前向模拟资金曲线"></div><p class="rs-forward-note"></p><button type="button" class="rs-text-button" data-book-refresh>刷新曲线</button></section><section id="rs-sources"></section></div></div></article>`;
    document.body.append(dialog);
    const el=s=>dialog.querySelector(s);let requests=window.SpikeRuntime.createRequests();
    let open=false,track='',epoch=0,closing=null,analytics=null,research=null,bars=[],line=null,chartData=null,active=true,history=null,historyChart=null,historyMode='equity',historyPaintKey='';
    let breadthData=null,replayRouteHandled=false,breadthScope=null,breadthPending=null;
    const forwardTemplate=el('#rs-forward').innerHTML;
    let breadthForwardTimer=null;
    let historyView=null,chartFrame=null,resizeFrame=null,zoomFrame=null,barPaintKeys=[],forwardPaintData=null,forwardPaintTheme='';
    const visibleCharts=new Set(),reading=el('.rs-reading');
    const chartObserver=new IntersectionObserver(entries=>{
      for(const entry of entries){if(entry.isIntersecting)visibleCharts.add(entry.target);else visibleCharts.delete(entry.target);}
      analytics?.setActive(active&&open&&!document.hidden&&visibleCharts.has(el('.rs-forward-chart')));
      queueCharts();
    },{root:reading,rootMargin:'100px 0px'});
    function observeCharts(){dialog.querySelectorAll('.rs-history-chart,.rs-bar-chart,.rs-forward-chart').forEach(n=>chartObserver.observe(n));}
    function queueCharts(){if(!open||!active||document.hidden||chartFrame!==null)return;chartFrame=requestAnimationFrame(()=>{chartFrame=null;charts();});}
    function updateHistoryControls(){
      if(!history||!historyView)return;
      const first=history.points[0][0],last=history.points.at(-1)[0];
      dialog.querySelectorAll('[data-history-range]').forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.historyRange===historyView.period)));
      el('[data-history-window]').textContent=historyWindowLabel(historyView);
      el('[data-history-step="-1"]').disabled=historyView.start<=first;
      el('[data-history-step="1"]').disabled=historyView.end>=last;
      el('[data-history-latest]').disabled=historyView.end>=last;
      el('.rs-chart-legend span:first-child').lastChild.textContent=historyMode==='equity'?'已实现资金':history.refilled?'累计交易净盈亏':'累计收益';
      el('.rs-chart-legend span:nth-child(2)').lastChild.textContent=history.refilled?'本轮本金回撤':'历史高点回撤';
    }
    function setHistoryView(view){
      historyView=view;updateHistoryControls();
      if(historyChart){historyChart.setOption({yAxis:historyAxes(history,historyMode,view)});historyChart.dispatchAction({type:'dataZoom',dataZoomIndex:0,startValue:view.start,endValue:view.end},{silent:true});}
      else queueCharts();
    }
    function onHistoryZoom(event){
      if(!history||!historyChart)return;
      const z=event.batch?.[0]||event,first=history.points[0][0],last=history.points.at(-1)[0],span=last-first;
      const start=z.startValue??first+span*(z.start??0)/100,end=z.endValue??first+span*(z.end??100)/100;
      if(!Number.isFinite(start)||!Number.isFinite(end)||end<=start)return;
      historyView={period:'custom',start:Math.max(first,start),end:Math.min(last,end)};updateHistoryControls();
      cancelAnimationFrame(zoomFrame);zoomFrame=requestAnimationFrame(()=>{zoomFrame=null;if(historyChart)historyChart.setOption({yAxis:historyAxes(history,historyMode,historyView)});});
    }
    const calendar=document.createElement('div');calendar.id='rs-month-calendar';calendar.className='rs-calendar';calendar.setAttribute('popover','auto');calendar.setAttribute('role','dialog');calendar.setAttribute('aria-labelledby','rs-calendar-title');dialog.append(calendar);
    let calendarOrigin=null,calendarMonth=null,calendarDay=0,dayView=false;
    const dayRequests=window.SpikeRuntime.createRequests();
    const dayModel=createDayModel({read:q=>dayRequests.json('/api/strategy-backtest/day?'+new URLSearchParams(q)),validRow:window.SpikeStrategyBacktest.valid,cancel:()=>dayRequests.cancel(),change:renderDay});
    function renderDay(s){
      if(!open||!dayView||!calendar.matches(':popover-open'))return;
      const scroll=calendar.querySelector('.rs-day-scroll')?.scrollTop||0,focused=document.activeElement;
      const hadFocus=calendar.contains(focused),focusId=hadFocus?focused.id:'',action=['data-day-more','data-day-retry'].find(a=>focused?.hasAttribute(a));
      calendar.innerHTML=dayTradesHTML(s);calendar.classList.add('is-day-view');positionCalendar();
      calendar.querySelector('.rs-day-scroll').scrollTop=scroll;
      const target=focusId?calendar.querySelector('#'+focusId):action?calendar.querySelector(`[${action}]:not(:disabled)`):focused?.dataset.dayTrade?calendar.querySelector(`[data-day-trade="${focused.dataset.dayTrade}"]`):null;
      // Replacing a loading/pagination button must not leave focus on body:
      // Escape then bypasses this dialog's day -> month navigation handler.
      if(hadFocus)(target||calendar.querySelector('#rs-calendar-title')).focus({preventScroll:true});
    }
    function showDay(number){
      const d=calendarMonth?.days[number-1];if(!d?.covered)return;
      calendarDay=number;dayView=true;
      if(track===BREADTH){
        const rows=(history.dayRows[d.date]||[]).map((r,i)=>({...r,side:1,id:i+1}));
        renderDay({date:d.date,expectedPnl:d.pnl,data:{total:rows.length},rows});
      }else dayModel.select(track,d.date,d.pnl);
      calendar.querySelector('#rs-calendar-title').focus({preventScroll:true});
    }
    function backToCalendar(){showCalendar(calendarMonth.key,null,'day');}
    function resetMonthButtons(){dialog.querySelectorAll('[data-history-month][aria-expanded="true"]').forEach(b=>b.setAttribute('aria-expanded','false'));}
    function closeCalendar(restore=false){dayModel.pause();dayView=false;if(calendar.matches(':popover-open'))calendar.hidePopover();resetMonthButtons();if(restore&&calendarOrigin?.isConnected)calendarOrigin.focus({preventScroll:true});}
    function positionCalendar(){
      if(!calendar.matches(':popover-open')||!calendarOrigin)return;
      const r=calendarOrigin.getBoundingClientRect(),w=calendar.offsetWidth,h=calendar.offsetHeight;
      const mobile=innerWidth<=700,left=mobile?(innerWidth-w)/2:Math.max(12,Math.min(r.left,innerWidth-w-12));
      const top=mobile?innerHeight-h-12:r.bottom+8+h<=innerHeight-12?r.bottom+8:r.top-h-8;
      calendar.style.left=left+'px';calendar.style.top=Math.max(12,Math.min(top,innerHeight-h-12))+'px';
    }
    function showCalendar(key,origin=null,focus=''){
      const month=history&&monthCalendar(history,key);if(!month)return;
      dayModel.pause();dayView=false;calendar.classList.remove('is-day-view');if(key!==calendarMonth?.key)calendarDay=0;
      if(origin)calendarOrigin=origin;calendarMonth=month;resetMonthButtons();
      el(`[data-history-month="${key}"]`)?.setAttribute('aria-expanded','true');
      calendar.innerHTML=calendarHTML(month);if(!calendar.matches(':popover-open'))calendar.showPopover();positionCalendar();
      if(calendarDay)calendar.querySelector(`[data-calendar-day="${calendarDay}"]`)?.setAttribute('aria-pressed','true');
      const target=focus==='day'?calendar.querySelector(`[data-calendar-day="${calendarDay}"]`):focus?calendar.querySelector(`[data-calendar-nav="${focus}"]:not(:disabled)`):null;
      (target||calendar.querySelector('#rs-calendar-title')).focus({preventScroll:true});
    }
    calendar.addEventListener('toggle',e=>{if(e.newState==='closed'){resetMonthButtons();dayModel.pause();dayView=false;}});
    calendar.addEventListener('click',e=>{
      const nav=e.target.closest('[data-calendar-nav]'),day=e.target.closest('[data-calendar-day]');
      if(nav)showCalendar(calendarMonth[nav.dataset.calendarNav],null,nav.dataset.calendarNav);
      if(e.target.closest('[data-calendar-close]'))closeCalendar(true);
      if(day)showDay(Number(day.dataset.calendarDay));
      if(e.target.closest('[data-calendar-back]'))backToCalendar();
      if(e.target.closest('[data-day-more],[data-day-retry]'))dayModel.load();
      const trade=e.target.closest('[data-day-trade]');if(trade&&track===BREADTH){const rows=history.dayRows[calendarMonth.days[calendarDay-1].date]||[];const row=rows[Number(trade.dataset.dayTrade)-1];if(row){closeCalendar();jumpBreadth('replay',row.eventId);}return;}
      if(trade){const s=dayModel.getState(),row=s.rows.find(r=>r.id===Number(trade.dataset.dayTrade));if(row){const selectedTrack=s.track;close(true);onBacktest?.(selectedTrack,row);}}
    });
    calendar.addEventListener('focusin',e=>{const day=e.target.closest('[data-calendar-day]');if(day)calendar.querySelector('.rs-calendar-detail').textContent=calendarDayLabel(calendarMonth.days[Number(day.dataset.calendarDay)-1]);});
    dialog.addEventListener('keydown',e=>{if(e.key==='Escape'&&calendar.matches(':popover-open')){e.preventDefault();e.stopPropagation();if(dayView)backToCalendar();else closeCalendar(true);}});
    // A click may finish the reader's smooth scroll after opening the popover.
    // Follow its anchor instead of dismissing the just-opened calendar.
    el('.rs-reading').addEventListener('scroll',()=>positionCalendar(),{passive:true});
    function positionPicker(){const r=trigger.getBoundingClientRect();picker.style.left=Math.max(8,Math.min(r.left,innerWidth-picker.offsetWidth-8))+'px';picker.style.top=Math.max(8,Math.min(r.bottom+6,innerHeight-picker.offsetHeight-8))+'px';}
    function sync(opening=false){
      const track=getTrack(),name=label(track),labelNode=trigger.querySelector('b');
      if(labelNode.textContent!==name)labelNode.textContent=name;
      if(!opening&&!picker.matches(':popover-open'))return;
      picker.querySelector('[data-more-current]').textContent=layout.more.includes(track)?'当前：'+name:'';
      for(const button of picker.querySelectorAll('[data-strategy-select]')){
        const selected=String(button.dataset.strategySelect===track);
        button.setAttribute('aria-pressed',selected);
        button.closest('[data-account-row]').dataset.selected=selected;
      }
      for(const node of picker.querySelectorAll('[data-account-pnl]')){
        const value=pnlView(getPnl?.(node.dataset.accountPnl));
        node.textContent=value.text;node.className=value.tone;node.title=value.title;
      }
    }
    trigger.addEventListener('click',()=>{if(picker.matches(':popover-open'))picker.hidePopover();else{setMore(false);orderStatus.textContent='拖动把手排序或移入常用';sync(true);picker.showPopover();accountList.scrollTop=0;positionPicker();(layout.main.includes(getTrack())?picker.querySelector(`[data-strategy-select="${getTrack()}"]`):moreToggle).focus({preventScroll:true});}});
    picker.addEventListener('toggle',()=>{const shown=picker.matches(':popover-open');trigger.setAttribute('aria-expanded',String(shown));root.classList.toggle('rs-picker-webkit',shown&&/AppleWebKit\//.test(navigator.userAgent)&&!/(Chrome|Chromium|Edg)\//.test(navigator.userAgent));if(!shown)finishDrag(true);});
    picker.addEventListener('keydown',event=>{
      if(event.key==='Escape'){event.stopPropagation();if(drag){event.preventDefault();finishDrag(true);}return;}
      const row=event.target.closest('[data-account-row]'),isMore=event.target===moreToggle;
      if(!row&&!isMore)return;
      if(row&&event.altKey&&['ArrowDown','ArrowUp','ArrowLeft','ArrowRight'].includes(event.key)){
        event.preventDefault();const id=row.dataset.accountRow,from=groupOf(id),group=event.key==='ArrowLeft'?'main':event.key==='ArrowRight'?'more':from;
        const index=group!==from?layout[group].length:layout[from].indexOf(id)+(event.key==='ArrowDown'?1:event.key==='ArrowUp'?-1:0);
        if(group==='more')setMore(true);layout=moveAccountGroup(layout,id,group,index);applyOrder(true);saveOrder(id);positionPicker();event.target.focus();return;
      }
      if(['ArrowDown','ArrowUp','Home','End'].includes(event.key)){
        event.preventDefault();const visible=[...layout.main.map(id=>accountNodes.get(id)),moreToggle,...(groups.more.hidden?[]:layout.more.map(id=>accountNodes.get(id)))];
        const i=visible.indexOf(row||moreToggle),column=row?[...row.querySelectorAll('button')].indexOf(event.target):0;
        const index=event.key==='Home'?0:event.key==='End'?visible.length-1:(i+(event.key==='ArrowDown'?1:-1)+visible.length)%visible.length,target=visible[index];
        (target===moreToggle?target:target.querySelectorAll('button')[Math.max(0,column)]).focus();
      }
    });
    picker.addEventListener('click',event=>{if(event.detail!==0&&Date.now()<blockedUntil){event.preventDefault();event.stopPropagation();return;}const b=event.target.closest('button');if(!b)return;if(b.hasAttribute('data-account-more')){setMore(groups.more.hidden);return;}if(b.dataset.strategySelect===BREADTH){picker.hidePopover();show(BREADTH);return;}if(b.dataset.strategySelect){onSelect(b.dataset.strategySelect);sync();picker.hidePopover();trigger.focus({preventScroll:true});}if(b.dataset.strategyBook){picker.hidePopover();show(b.dataset.strategyBook);}if(b.dataset.strategyBacktest){picker.hidePopover();if(b.dataset.strategyBacktest===BREADTH)openBreadthReplay({});else onBacktest?.(b.dataset.strategyBacktest);}});
    function stopCharts(){chartObserver.disconnect();visibleCharts.clear();for(const frame of [chartFrame,resizeFrame,zoomFrame])cancelAnimationFrame(frame);chartFrame=resizeFrame=zoomFrame=null;bars.forEach(c=>c?.dispose());bars=[];barPaintKeys=[];line?.dispose();line=null;forwardPaintData=null;historyChart?.dispose();historyChart=null;historyPaintKey='';}
    function close(immediate=false){
      closeCalendar();
      if(!open&&!dialog.open)return;open=false;epoch++;requests.cancel();analytics?.setActive(false);stopCharts();dialog.classList.remove('is-open');clearTimeout(closing);
      if(track===BREADTH)clearBookLocation();
      clearTimeout(breadthForwardTimer);breadthData=null;breadthPending=null;
      const finish=()=>{dialog.close();document.body.classList.remove('rs-book-open','rs-book-webkit');trigger.focus({preventScroll:true});};
      if(immediate||matchReduced())finish();else closing=setTimeout(finish,280);
    }
    async function charts(){
      if(!visibleCharts.size)return;
      const token=epoch;try{await window.SpikeModules.ensure('signal-charts');if(!open||!active||document.hidden||token!==epoch)return;
        const dark=document.documentElement.dataset.theme==='dark';
        if(history){
          historyView??=historyWindow(history);updateHistoryControls();
          if(visibleCharts.has(el('.rs-history-chart'))&&historyPaintKey!==`${epoch}|${historyMode}|${dark}`){
            if(!historyChart){historyChart=window.echarts.init(el('.rs-history-chart'));historyChart.on('datazoom',onHistoryZoom);}
            historyChart.setOption(equityOptions(history,historyMode,dark,historyView),true);historyPaintKey=`${epoch}|${historyMode}|${dark}`;
          }
          const plots=[{labels:history.yearly.map(p=>p[0]),values:history.yearly.map(p=>p[1]),unit:history.refilled?'USDT':'%'},history.distribution,history.holding];
          dialog.querySelectorAll('[data-history-chart]').forEach((node,i)=>{if(!visibleCharts.has(node)||barPaintKeys[i]===`${epoch}|${dark}`)return;bars[i]??=window.echarts.init(node);const option=barOptions(plots[i],dark);option.series[0].itemStyle.color=p=>p.value<0?'#df526c':'#3285dc';bars[i].setOption(option,true);barPaintKeys[i]=`${epoch}|${dark}`;});
          el('.rs-chart-status').textContent='';
        }
        if(chartData&&visibleCharts.has(el('.rs-forward-chart'))&&(chartData!==forwardPaintData||dark!==forwardPaintTheme)){line??=window.echarts.init(el('.rs-forward-chart'));line.setOption(window.SpikeReclaimAnalytics.chartOptions(chartData,dark),true);forwardPaintData=chartData;forwardPaintTheme=dark;}
      }catch{if(open&&token===epoch){const n=el('.rs-chart-status');if(n)n.textContent='图表加载失败，表格数值仍可阅读。';const note=el('.rs-forward-note');if(note)note.textContent+=' 曲线绘制失败，请刷新重试。';}}
    }
    function renderForward(){
      if(!open||track===BREADTH||!analytics)return;const {data,error,busy}=analytics.getState();
      el('.rs-forward-status').textContent=error?`读取失败：${error}${data?' · 保留上次统计':''}`:data?`更新于 ${new Date(data.now_ms).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai'})} 北京时间`:'正在读取完整账本…';
      el('[data-book-refresh]').disabled=busy;
      if(!data)return;chartData=data;const s=data.summary;
      el('.rs-forward-metrics').innerHTML=[['已实现净盈亏',num(s.realized_pnl)+' USDT'],['已完全平仓',num(s.closed_count,0)+' 笔'],['最大已实现回撤',num(s.max_drawdown_pct)+'%']].map(([k,v])=>`<div><small>${k}</small><strong>${v}</strong></div>`).join('');
      const pair=data.paired_wick;
      const pairedNote=pair?` 同信号已结清 ${pair.pairs} 对，原版盈亏比 ${num(pair.original.payoff)}，浅穿中点版 ${num(pair.midpoint.payoff)}。`:'';
      el('.rs-forward-note').textContent=window.SpikeReclaimAnalytics.curveNote(data)+' 不含浮动盈亏。'+pairedNote+window.SpikeReclaimAnalytics.coverageNote(data.coverage);queueCharts();
    }
    function rules(){
      const d=getSnapshot(),current=d?.tracks?.[track];
      el('#rs-overview').innerHTML=overviewHTML(d,track);
      el('#rs-rules').innerHTML=`<div class="rs-section-title"><h2>从触价到退出</h2></div>${flowHTML(track)}<h3>本策略当前规则</h3><div class="rs-current-rules">${current?renderRules(d,track):'<p>正在读取策略数据。</p>'}</div><p class="rs-muted">规则取自当前只读模拟盘接口；历史回测版本差异见回测章节。</p>`;
    }
    function clearBookLocation(){window.SpikeBreadthBookRoute?.write(null);}
    function openBreadthReplay(options){
      const scope=options.scope||(open?breadthScope:null)||'all_history_fee_only';
      const data=breadthData;
      close(true);
      replayRouteHandled=true;
      onBacktest?.(BREADTH,null,{...options,scope,data,replay_source:BREADTH});
    }
    function jumpBreadth(chapter,eventId=null){
      if(chapter==='replay'){openBreadthReplay({eventId});return;}
      el('#rs-breadth-stats').scrollIntoView({block:'start',behavior:'instant'});
      window.SpikeBreadthBookRoute?.write({chapter:'stats',scope:breadthScope});
    }
    function renderBreadthScope(scope){
      const h=validateHistory(window.SpikeBreadthHistory.createBreadthBookHistory(breadthData,{track:BREADTH,scope}),BREADTH);
      closeCalendar();stopCharts();history=h;historyView=null;historyMode='equity';breadthScope=scope;
      el('[data-breadth-path]').innerHTML=historyHTML(history);
      el('#rs-breadth-stats').innerHTML='<h3>统计分析</h3>'+window.SpikeBreadthBook.statsHTML(breadthData,{scope,history:h});
      el('#rs-rules').innerHTML='<div class="rs-section-title"><h2>执行规则</h2></div>'+window.SpikeBreadthBook.rulesHTML(breadthData,{scope});
      dialog.querySelectorAll('[data-breadth-scope]').forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.breadthScope===scope)));
      observeCharts();queueCharts();
    }
    async function pollBreadthForward(token){
      if(!open||track!==BREADTH||epoch!==token)return;
      if(active&&!document.hidden){
        try{
          const [,state]=await Promise.all([window.SpikeModules.ensure('breadth-booklet'),requests.json('/static/strategy-booklet/breadth-ema20-forward.json')]);
          if(open&&epoch===token)el('#rs-forward').innerHTML=window.SpikeBreadthBook.forwardHTML(state);
        }catch{
          if(open&&epoch===token)el('#rs-forward').innerHTML='<div class="rs-section-title"><h2>前向资金</h2></div><p class="rs-warning">暂时读不到模拟账户，30 秒后重试。</p>';
        }
      }
      if(open&&epoch===token)breadthForwardTimer=setTimeout(()=>pollBreadthForward(token),30000);
    }
    async function showBreadth(token,options){
      window.SpikeBreadthBookRoute?.write(options);
      try{
        const [,data]=await Promise.all([window.SpikeModules.ensure('breadth-booklet'),requests.json('/static/strategy-booklet/breadth-ema20-data.json')]);
        if(!open||epoch!==token)return;
        breadthData=data;
        el('#rs-research').innerHTML='<div class="rs-section-title"><h2>历史回测</h2><span class="rs-badge">组合回测</span></div><div class="rs-breadth-tools"><div class="rs-chart-modes" role="group" aria-label="回测区间"><button type="button" data-breadth-scope="all_history_fee_only">全历史 · 仅手续费</button><button type="button" data-breadth-scope="2026_actual_funded">2026 · 含资金费</button></div><button class="rs-text-button" type="button" data-breadth-jump="replay">查看回测交易与 K 线</button><button class="rs-text-button" type="button" data-breadth-jump="stats">统计分析</button></div><div data-breadth-path></div><p class="rs-chart-status" role="status"></p><div id="rs-breadth-stats"></div>';
        renderBreadthScope(options.scope==='2026_actual_funded'?options.scope:'all_history_fee_only');
        const p=data.provenance;
        el('#rs-sources').innerHTML='<div class="rs-section-title"><h2>口径与来源</h2></div>'+window.SpikeBreadthBook.originHTML()+'<p>原实验 · '+esc(p.experiment_id)+'</p><p>信号覆盖至 '+esc(historyDate(Date.parse(data.signal_cutoff_exclusive_utc)-1))+'（UTC）。普通 BTC 的 EMA20 条件是在看过这段历史后选出的，2026 年并非盲测。</p>'+[['BTC 逐笔账本',p.event_sources.BTC],['山寨篮子与成员退出',p.event_sources.ALT],['山寨资金费核对',p.ALT_2026_funding],['山寨开单间隔实验',p.spacing_study],['BTC 的 EMA 筛选与仓位预留',p.btc_admission_study],['急跌 BTC 独立资金实验',p.btc_reserve_study]].filter(([,path])=>path).map(([name,path])=>'<details class="rs-source-note"><summary>'+esc(name)+'</summary><p>'+esc(path)+'</p></details>').join('');
        if(options.chapter&&options.chapter!=='overview'){const dest=options.chapter==='stats'?el('#rs-breadth-stats'):el('#rs-'+options.chapter);dest?.scrollIntoView({block:'start',behavior:'instant'});}
      }catch(error){if(open&&epoch===token)el('#rs-research').innerHTML='<p class="rs-warning">回测资料暂时不可用：'+esc(error.message)+'</p><button type="button" data-book-retry>重试读取</button>';}
    }
    async function show(next,options={}){
      closeCalendar();
      if(!catalog.includes(next))return;
      if(track===BREADTH&&next!==track)clearBookLocation();
      clearTimeout(breadthForwardTimer);breadthData=null;breadthPending=next===BREADTH?options:null;
      clearTimeout(closing);epoch++;const token=epoch;track=next;open=true;chartData=null;research=null;history=null;historyView=null;historyMode='equity';analytics?.setActive(false);requests.cancel();requests=window.SpikeRuntime.createRequests();stopCharts();
      analytics=null;
      el('[data-book-label]').textContent=label(track);
      el('#rs-forward').innerHTML=track===BREADTH?'<div class="rs-section-title"><h2>前向资金</h2></div><p>正在读取模拟账户…</p>':forwardTemplate;
      if(track===BREADTH){
        el('#rs-overview').innerHTML='<div class="rs-section-title"><h1>全市场下轨反弹<em>普通 BTC · 4H EMA20 过滤</em></h1></div><div class="rs-facts"><div><small>方向</small><b>做多</b></div><div><small>交易标的</small><b>BTC / 山寨篮子</b></div><div><small>信号</small><b>周一下轨首触</b></div></div>';
        el('#rs-rules').innerHTML='';
      }else rules();
      reading.scrollTo({top:0,behavior:'instant'});
      el('#rs-research').innerHTML='<p class="rs-muted">正在读取回测资料…</p>';el('#rs-sources').innerHTML='';
      document.body.classList.add('rs-book-open');document.body.classList.toggle('rs-book-webkit',/AppleWebKit\//.test(navigator.userAgent)&&!/(Chrome|Chromium|Edg)\//.test(navigator.userAgent));if(!dialog.open)dialog.showModal();el('.rs-sheet').getBoundingClientRect();requestAnimationFrame(()=>{if(open&&token===epoch)dialog.classList.add('is-open');});el('[data-book-label]').focus({preventScroll:true});
      const selected=track;
      if(selected===BREADTH){void pollBreadthForward(token);await showBreadth(token,options);return;}
      analytics=createFeed({read:()=>requests.json(`/api/monday-reclaim-paper/analytics?track=${selected}&days=0`),validateData:d=>window.SpikeReclaimAnalytics.validate(d,selected,0),cancel:()=>{},change:renderForward});
      try{
        const receipts=await Promise.allSettled([requests.json('/static/strategy-booklet/research.json'),requests.json(`/static/strategy-booklet/${selected}-history.json`)]);if(!open||token!==epoch)return;
        if(receipts[0].status!=='fulfilled')throw receipts[0].reason;
        const data=receipts[0].value;
        if(receipts[1].status==='fulfilled'){try{history=validateHistory(receipts[1].value,selected);}catch{history=null;}}
        el('#rs-research').innerHTML=researchHTML(data,selected,history);research=data;
        const r=data.accounts[selected];
        el('#rs-sources').innerHTML=`<div class="rs-section-title"><h2>口径与来源</h2></div><h3>回测方法</h3><p>${esc(data.methodology)}</p><p>${esc(data.comparison)}</p><details class="rs-source-note"><summary>结果有效性说明</summary><p>${esc(data.integrity)}</p></details><h3>连续曲线核算</h3><p>复用原始框架成交与仓位核算函数，保存每次结算；未重新生成交易。费用与滑点已在成交净收益中，不二次扣除。不含浮盈亏，因此不计算基于每日盯市收益的夏普比率。</p>${history?`<p>路径导出版本：${esc(history.source_commit)} · 固定种子：${esc(history.meta.seed??'原研究稳定排序')}</p>`:''}<p>版式参考 <a href="https://www.tradingview.com/support/solutions/43000764138-tradingview-strategy-report-how-to-start/" target="_blank" rel="noopener">TradingView</a>、<a href="https://github.com/ranaroussi/quantstats" target="_blank" rel="noopener">QuantStats</a>。未有同口径基准账本的策略不叠加伪造基准。</p><h3>原始资料</h3>${r.sources.map(s=>`<details class="rs-source-note"><summary>${esc(s.path.split('/').at(-2))}</summary><p>${esc(s.path)}</p><p>读取字段：${esc(s.selection)}</p><p>研究代码：${esc(s.commit||'原研究未记录')}</p></details>`).join('')}<p class="rs-muted">资料核对 ${esc(data.reviewed_on)} · 历史研究与前向模拟分别展示</p>`;
        el('#rs-research').querySelectorAll('img').forEach(img=>img.addEventListener('error',()=>{img.hidden=true;img.parentElement.insertAdjacentHTML('beforeend','<p>原图暂时无法读取，请稍后重试。</p>');},{once:true}));observeCharts();queueCharts();
      }catch(error){if(open&&token===epoch)el('#rs-research').innerHTML=`<p class="rs-warning">回测资料暂时不可用：${esc(error.message)}</p><button type="button" data-book-retry>重试读取</button>`;}finally{if(open&&token===epoch)observeCharts();}
    }
    dialog.addEventListener('cancel',e=>{e.preventDefault();if(calendar.matches(':popover-open')){if(dayView)backToCalendar();else closeCalendar(true);}else close();});
    dialog.addEventListener('click',e=>{const month=e.target.closest('[data-history-month]');if(month)showCalendar(month.dataset.historyMonth,month);});
    dialog.addEventListener('click',e=>{
      if(e.target===dialog||e.target.closest('[data-book-close]'))close();
      if(e.target.closest('[data-book-refresh]')){analytics?.refresh();queueCharts();}
      if(e.target.closest('[data-book-retry]'))show(track,breadthPending||{});
      const scope=e.target.closest('[data-breadth-scope]'),jump=e.target.closest('[data-breadth-jump]');
      if(scope&&breadthData&&scope.dataset.breadthScope!==breadthScope){renderBreadthScope(scope.dataset.breadthScope);window.SpikeBreadthBookRoute?.write({chapter:'research',scope:breadthScope});}
      if(jump)jumpBreadth(jump.dataset.breadthJump);
      const example=e.target.closest('[data-breadth-case]');
      if(example&&breadthData)openBreadthReplay({eventId:example.dataset.breadthCase,memberSymbol:example.dataset.member,scope:breadthScope});
      const period=e.target.closest('#rs-breadth-stats [data-period]');
      if(period&&breadthData){
        const entryPeriod=period.dataset.period;
        openBreadthReplay({entryPeriod,scope:entryPeriod.length===4?'all_history_fee_only':breadthScope});
      }
      const mode=e.target.closest('[data-history-mode]');
      if(mode&&history){historyMode=mode.dataset.historyMode;dialog.querySelectorAll('[data-history-mode]').forEach(b=>b.setAttribute('aria-pressed',String(b===mode)));queueCharts();}
      const range=e.target.closest('[data-history-range]'),step=e.target.closest('[data-history-step]');
      if(range&&history)setHistoryView(historyWindow(history,range.dataset.historyRange,historyView?.end));
      if(step&&history)setHistoryView(shiftHistoryWindow(history,historyView,Number(step.dataset.historyStep)));
      if(e.target.closest('[data-history-latest]')&&history){
        if(historyView.period==='custom'){const end=history.points.at(-1)[0];setHistoryView({period:'custom',start:Math.max(history.points[0][0],end-(historyView.end-historyView.start)),end});}
        else setHistoryView(historyWindow(history,historyView.period));
      }
      const chapter=e.target.closest('[data-book-chapter]');if(chapter&&track===BREADTH)window.SpikeBreadthBookRoute?.write({chapter:chapter.dataset.bookChapter,scope:breadthScope});if(chapter)el('#rs-'+chapter.dataset.bookChapter).scrollIntoView({behavior:matchReduced()?'instant':'smooth',block:'start'});
    });
    dialog.addEventListener('keydown',e=>{
      if(!e.target.matches('.rs-history-chart')||!history)return;
      if(['ArrowLeft','ArrowRight','Home'].includes(e.key)){e.preventDefault();setHistoryView(e.key==='Home'?historyWindow(history):shiftHistoryWindow(history,historyView,e.key==='ArrowLeft'?-1:1));}
    });
    new ResizeObserver(()=>{cancelAnimationFrame(resizeFrame);resizeFrame=requestAnimationFrame(()=>{resizeFrame=null;if(!open)return;bars.forEach(c=>c?.resize());line?.resize();historyChart?.resize();});}).observe(reading);
    new MutationObserver(()=>{if(open)queueCharts();}).observe(document.documentElement,{attributes:true,attributeFilter:['data-theme']});
    window.addEventListener('resize',()=>{if(picker.matches(':popover-open'))positionPicker();positionCalendar();});
    document.addEventListener('visibilitychange',()=>{analytics?.setActive(active&&open&&!document.hidden&&visibleCharts.has(el('.rs-forward-chart')));if(!document.hidden)queueCharts();});
    sync();return {sync,close,show,getOrder:()=>[...layout.main,...layout.more].filter(t=>tracks.includes(t)),setActive(value){active=value;if(value&&!open){const dest=window.SpikeBreadthBookRoute?.read(location.search);if(dest?.chapter==='replay'){if(!replayRouteHandled)openBreadthReplay(dest);}else{replayRouteHandled=false;if(dest)show(BREADTH,dest);}}if(!value){if(picker.matches(':popover-open'))picker.hidePopover();close(true);}}};
  }
  window.SpikeStrategyBook={...api,create};
})();
