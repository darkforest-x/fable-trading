/* Stitch 3db5c4dc931244f8a1ea859d4acb8d49. Read-only, source-backed strategy books.
 * Picker refinement: 040e7b6c98104d0f9bb77ec995dabb1a.
 * The selected book and local display order are independent of the paper account. */
(() => {
  'use strict';
  const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const label=t=>({60:'60m',5:'5m'}[t]||t);
  const TITLES={60:'等待回收',5:'快速确认',v3:'分数决定目标',v3add:'逆向分步加仓',spike:'排列过滤插针',v3lock:'回收后锁住利润',allin3:'单仓 · 梭哈3倍',v3add20:'20份动态保证金 · 3倍加仓'};
  const BOOK='<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" aria-hidden="true"><path d="M12 5C8 2 4 3 2 4v15c3-1 6-1 10 2 4-3 7-3 10-2V4c-2-1-6-2-10 1Zm0 0v16"/></svg>';
  const BACKTEST='<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" aria-hidden="true"><path d="M3 17 9 11l4 4 8-10M15 5h6v6"/></svg>';
  const num=(v,d=2)=>typeof v==='number'&&Number.isFinite(v)?v.toLocaleString('en-US',{maximumFractionDigits:d}):'—';
  const ORDER_KEY='spike:paper-account-order:v1';
  function accountOrder(saved,tracks){const defaults=[...tracks.filter(t=>t==='spike'),...tracks.filter(t=>t!=='spike')];return [...new Set([...(Array.isArray(saved)?saved:[]).filter(t=>defaults.includes(t)),...defaults])];}
  function moveAccount(order,id,index){const next=order.filter(t=>t!==id);if(!order.includes(id))return [...order];next.splice(Math.max(0,Math.min(next.length,index)),0,id);return next;}
  function pnlView(value){
    if(typeof value!=='number'||!Number.isFinite(value))return {text:'—',tone:'',title:'已实现盈亏暂未读取'};
    const sign=value>0?'+':value<0?'−':'',amount=Math.abs(value);
    return {text:sign+(amount>0&&amount<.005?'<0.01':amount.toLocaleString('en-US',{minimumFractionDigits:2,maximumFractionDigits:2})),tone:value>0?'rp-positive':value<0?'rp-negative':'',title:'已实现净盈亏 · USDT · 不含浮盈亏和补充本金'};
  }
  function pickerHTML(tracks){return tracks.map(t=>`<div class="rs-account-row" data-account-row="${esc(t)}"><button type="button" data-account-drag="${esc(t)}" aria-label="拖动调整 ${esc(label(t))} 顺序" aria-description="拖动排序，或按 Alt 加上下方向键" title="拖动排序 · Alt + ↑/↓"><svg width="14" height="20" viewBox="0 0 14 20" fill="currentColor" aria-hidden="true"><circle cx="4" cy="5" r="1.2"/><circle cx="10" cy="5" r="1.2"/><circle cx="4" cy="10" r="1.2"/><circle cx="10" cy="10" r="1.2"/><circle cx="4" cy="15" r="1.2"/><circle cx="10" cy="15" r="1.2"/></svg></button><button type="button" data-strategy-select="${esc(t)}" aria-pressed="false"><span>${esc(label(t))}</span><strong data-account-pnl="${esc(t)}">—</strong><i aria-hidden="true">✓</i></button><button type="button" data-strategy-book="${esc(t)}" aria-label="查看 ${esc(label(t))} 策略小册" title="策略小册">${BOOK}</button><button type="button" data-strategy-backtest="${esc(t)}" aria-label="查看 ${esc(label(t))} 回测交易" title="回测交易">${BACKTEST}</button></div>`).join('');}
  function tableHTML(headers,rows){return `<div class="rs-table-wrap"><table><thead><tr>${headers.map(h=>`<th scope="col">${esc(h)}</th>`).join('')}</tr></thead><tbody>${rows.map(row=>`<tr>${row.map((v,i)=>i?`<td>${typeof v==='number'?num(v):esc(v??'—')}</td>`:`<th scope="row">${esc(v)}</th>`).join('')}</tr>`).join('')}</tbody></table></div>`;}
  const historyDate=ms=>new Date(ms).toISOString().slice(0,10);
  function validateHistory(h,track){
    if(h?.schema!==1||h.meta?.track!==track||!h.summary||!Array.isArray(h.points)||h.points.length<2||!h.points.every((p,i)=>Array.isArray(p)&&p.length===5&&p.every(Number.isFinite)&&p[1]>=0&&(!i||p[0]>=h.points[i-1][0]))||!['monthly','yearly','sides','recent'].every(k=>Array.isArray(h[k]))||!h.distribution||!h.holding)throw Error('历史曲线的数据或策略不匹配');
    return h;
  }
  function heatmapHTML(h){
    const years=[...new Set(h.monthly.map(p=>p[0].slice(0,4)))],values=new Map(h.monthly),unit=h.refilled?'USDT':'%';
    const max=Math.max(1,...h.monthly.map(p=>Math.abs(p[1]||0)));
    const cell=v=>v==null?'<td class="rs-month-missing">—</td>':`<td style="--cell-alpha:${(.06+.3*Math.sqrt(Math.abs(v)/max)).toFixed(3)}" class="${v>0?'is-gain':v<0?'is-loss':'is-flat'}" title="${esc(num(v)+' '+unit)}">${num(v,h.refilled?0:1)}</td>`;
    return `<div class="rs-table-wrap rs-heatmap"><table aria-label="${h.refilled?'月度交易净盈亏':'月度收益率'}"><thead><tr><th>年 / 月</th>${Array.from({length:12},(_,i)=>`<th>${i+1}</th>`).join('')}<th>全年</th></tr></thead><tbody>${years.map(y=>`<tr><th>${y}</th>${Array.from({length:12},(_,i)=>cell(values.get(y+'-'+String(i+1).padStart(2,'0')))).join('')}${cell(h.yearly.find(p=>p[0]===y)?.[1])}</tr>`).join('')}</tbody></table></div>`;
  }
  function historyHTML(h){
    const s=h.summary,pf=s.profit_factor_state==='no_losses'?'∞':num(s.profit_factor),unit=h.refilled?'USDT':'%';
    const metrics=[[h.refilled?'交易净盈亏':'累计净收益',num(h.refilled?s.pnl:s.return_pct)+(h.refilled?' USDT':'%')],['最大已实现回撤',num(s.max_drawdown_pct)+'%'],['资金成交胜率',num(s.win_pct,1)+'%'],['已平仓笔数',num(s.trades,0)],['盈利因子',pf],['期末资金',num(s.end)+' USDT']];
    return `<div class="rs-history-meta"><span>${historyDate(h.points[0][0])} — ${historyDate(h.points.at(-1)[0])} · UTC</span><span>1000 USDT 起步 · 已实现净值</span></div><p class="rs-research-note">${esc(h.meta.note)}</p><div class="rs-backtest-metrics">${metrics.map(([k,v])=>`<div><small>${k}</small><strong>${v}</strong></div>`).join('')}</div><figure class="rs-primary-figure"><figcaption><b>资金路径与回撤</b><div class="rs-chart-modes" role="group" aria-label="历史曲线显示"><button type="button" data-history-mode="equity" aria-pressed="true">资金 USDT</button><button type="button" data-history-mode="returns" aria-pressed="false">${h.refilled?'交易净盈亏':'累计收益 %'}</button></div></figcaption><div class="rs-history-chart" role="img" aria-label="历史资金与回撤曲线"></div><p class="rs-muted">${h.refilled?'灰线为累计净投入；补充本金不计入交易收益。回撤按每轮本金计算，补充本金重置高水位。':'蓝线为已实现资金／累计净收益，红色为相对历史高点的回撤。'} 曲线保留每笔结算，不含持仓浮盈亏；同一时刻的先亏损后补款也保留。</p></figure><div class="rs-period-grid"><figure><figcaption>${h.refilled?'月度交易净盈亏':'月度收益热力图'} <small>/ ${unit}</small></figcaption>${heatmapHTML(h)}</figure><figure><figcaption>${h.refilled?'年度交易净盈亏':'年度收益'} <small>/ ${unit}</small></figcaption><div class="rs-bar-chart" data-history-chart="yearly" role="img" aria-label="年度收益柱状图"></div></figure></div><div class="rs-chart-grid"><figure><figcaption>单笔净收益分布 <small>/ 名义本金收益 % · 纵轴笔数</small></figcaption><div class="rs-bar-chart" data-history-chart="distribution" role="img" aria-label="单笔净收益分布"></div></figure><figure><figcaption>持仓时长分布 <small>/ 笔</small></figcaption><div class="rs-bar-chart" data-history-chart="holding" role="img" aria-label="持仓时长分布"></div></figure></div><div class="rs-sides"><h3>多空表现</h3>${tableHTML(['方向','成交笔数','胜率 %','净盈亏 USDT'],h.sides)}</div><details class="rs-detail-table"><summary>逐笔成交 · 最近 ${h.recent.length} 笔 / 共 ${s.trades} 笔</summary>${tableHTML(['币种','开仓 UTC','平仓 UTC','方向','净盈亏 USDT','净收益 %'],h.recent.map(r=>[r[0],new Date(r[1]).toISOString().slice(0,16).replace('T',' '),new Date(r[2]).toISOString().slice(0,16).replace('T',' '),...r.slice(3)]))}</details>`;
  }
  function equityOptions(h,mode='equity',dark=false){
    const c=dark?'#abb8c5':'#607382',gridColor=dark?'#ffffff14':'#71849422',returns=mode==='returns';
    const name=returns?(h.refilled?'累计交易净盈亏':'累计净收益'):'已实现资金',unit=returns&&!h.refilled?'%':' USDT';
    const axis=i=>({gridIndex:i,type:'time',min:h.points[0][0],max:h.points.at(-1)[0],boundaryGap:false,axisLine:{show:false},axisTick:{show:false},axisLabel:{show:i===1,color:c,fontSize:10,hideOverlap:true},splitLine:{show:false}});
    const series=[{name,type:'line',step:'end',showSymbol:false,smooth:false,xAxisIndex:0,yAxisIndex:0,data:h.points.map(p=>[p[0],p[returns?2:1]]),lineStyle:{color:'#2586e7',width:1.8},itemStyle:{color:'#2586e7'},areaStyle:{color:'#2586e7',opacity:.08}},
      {name:h.refilled?'本轮本金回撤':'已实现回撤',type:'line',step:'end',showSymbol:false,smooth:false,xAxisIndex:1,yAxisIndex:1,data:h.points.map(p=>[p[0],p[3]]),lineStyle:{color:'#e04b66',width:1.2},itemStyle:{color:'#e04b66'},areaStyle:{color:'#e04b66',opacity:.18}}];
    if(h.refilled&&!returns)series.push({name:'累计净投入',type:'line',step:'end',showSymbol:false,data:h.points.map(p=>[p[0],p[4]]),lineStyle:{color:'#8897a6',type:'dashed',width:1.2},itemStyle:{color:'#8897a6'}});
    return {animation:!matchReduced(),animationDuration:350,useUTC:true,grid:[{left:64,right:16,top:25,height:'52%'},{left:64,right:16,top:'68%',height:'18%'}],axisPointer:{link:[{xAxisIndex:'all'}]},tooltip:{trigger:'axis',confine:true,formatter:items=>`${historyDate(items[0].value[0])}<br>`+items.map(v=>`${esc(v.seriesName)}: ${num(v.value[1])}${v.seriesIndex===1?'%':unit}`).join('<br>')},xAxis:[axis(0),axis(1)],yAxis:[{type:'value',scale:true,name:unit.trim(),nameTextStyle:{color:c},axisLabel:{color:c,formatter:v=>Math.abs(v)>=1e6?num(v/1e6,1)+'m':Math.abs(v)>=1e3?num(v/1e3,1)+'k':num(v)},splitLine:{lineStyle:{color:gridColor}}},{gridIndex:1,type:'value',max:0,name:'回撤 %',nameTextStyle:{color:c},axisLabel:{color:c,formatter:v=>num(v,0)+'%'},splitLine:{lineStyle:{color:gridColor}}}],dataZoom:[{type:'inside',xAxisIndex:[0,1],zoomOnMouseWheel:false,moveOnMouseWheel:false},{type:'slider',xAxisIndex:[0,1],bottom:0,height:16,showDetail:false,borderColor:'transparent'}],series};
  }
  function researchHTML(data,t,history=null){
    const r=data.accounts[t];if(!r)throw Error('该策略的小册资料尚未收录');
    const image=im=>`<figure class="rs-history-figure"><img class="rs-research-image" loading="lazy" src="/static/strategy-booklet/${esc(im.file)}" alt="${esc(im.caption)}"><figcaption>${esc(im.caption)} <a href="/static/strategy-booklet/${esc(im.file)}" target="_blank" rel="noopener">查看原图 ↗</a></figcaption></figure>`;
    return `<div class="rs-section-title"><h2>历史回测</h2><span class="rs-badge">${esc(r.status)}</span></div>${history?historyHTML(history):'<p class="rs-missing-path">历史资金曲线暂时无法读取，以下保留汇总结果。</p><button type="button" data-book-retry>重试曲线</button>'}<p class="rs-chart-status" role="status"></p><details class="rs-detail-table"><summary>原研究分区间统计与参数口径</summary><p class="rs-research-note">${esc(r.note)}</p>${tableHTML(r.headers,r.rows)}${r.capital_rows?`<h3>原研究资金回放 · 多次顺序取中位数</h3>${tableHTML(r.capital_headers,r.capital_rows)}`:''}${r.detail?`<p class="rs-muted">${esc(r.detail)}</p>`:''}<p class="rs-muted">${esc(data.period_note)}</p><p class="rs-muted">上方曲线是预先固定的单次顺序；表中多次中位数不必等于该路径终点，不能把各区间相乘或拼接。</p></details>${r.controls?`<details class="rs-detail-table"><summary>同条件随机入场对照</summary><p class="rs-muted">${esc(r.controls.note)}</p>${tableHTML(r.controls.headers,r.controls.rows)}</details>`:''}${r.images?.length||r.references?.length?`<details class="rs-detail-table"><summary>原研究图片 · 保留原版本参数</summary>${[...(r.images||[]),...(r.references||[])].map(image).join('')}</details>`:''}`;
  }

  function barOptions(r,dark=false){const c=dark?'#9ba9b8':'#738392';return {animation:!matchReduced(),animationDuration:450,grid:{left:55,right:18,top:25,bottom:r.labels.length>8?45:60},tooltip:{trigger:'axis',confine:true,valueFormatter:v=>num(v)+' '+r.unit},xAxis:{type:'category',data:r.labels,axisTick:{show:false},axisLabel:{color:c,fontSize:9,hideOverlap:true,interval:r.labels.length>8?'auto':0,formatter:v=>v.replace(' · ','\n')}},yAxis:{type:'value',name:r.unit,nameTextStyle:{color:c},axisLabel:{color:c},splitLine:{lineStyle:{color:dark?'#ffffff12':'#71849420'}}},series:[{type:'bar',barMaxWidth:40,data:r.values,itemStyle:{color:'#3285dc',borderRadius:[4,4,0,0]}}]};}
  function matchReduced(){return typeof matchMedia==='function'&&matchMedia('(prefers-reduced-motion:reduce)').matches;}
  const api={pickerHTML,accountOrder,moveAccount,pnlView,ORDER_KEY,tableHTML,researchHTML,barOptions,historyHTML,heatmapHTML,equityOptions,validateHistory,TITLES};
  if(typeof module!=='undefined'&&module.exports){module.exports=api;return;}
  function create({root,tracks,getTrack,getSnapshot,getPnl,renderRules,createFeed,onSelect,onBacktest}){
    const trigger=root.querySelector('[data-rp="track"]');
    let storage,saved;try{storage=window.localStorage;saved=JSON.parse(storage.getItem(ORDER_KEY)||'null');}catch{}
    let order=accountOrder(saved,tracks),drag=null,dragFrame=0,blockedUntil=0;
    const picker=document.createElement('div');picker.id='rs-account-picker';picker.className='rs-account-picker';picker.setAttribute('popover','auto');picker.setAttribute('role','dialog');picker.setAttribute('aria-label','策略');picker.innerHTML='<header><b>策略</b><small>已实现盈亏 · USDT</small></header><div class="rs-account-list">'+pickerHTML(order)+'</div><p class="rs-order-status" role="status">拖动左侧把手排序</p>';root.append(picker);
    const accountList=picker.querySelector('.rs-account-list'),orderStatus=picker.querySelector('.rs-order-status');
    const accountNodes=new Map([...picker.querySelectorAll('[data-account-row]')].map(n=>[n.dataset.accountRow,n]));
    function applyOrder(animate=false){
      const before=new Map([...accountNodes].map(([id,n])=>[id,n.getBoundingClientRect().top]));
      const focused=document.activeElement;
      order.forEach((id,i)=>{const n=accountNodes.get(id);if(accountList.children[i]!==n)accountList.insertBefore(n,accountList.children[i]||null);});
      if(picker.contains(focused)&&document.activeElement!==focused)focused.focus({preventScroll:true});
      if(animate&&!matchReduced())for(const [id,n] of accountNodes){if(id===drag?.id)continue;const delta=before.get(id)-n.getBoundingClientRect().top;n.getAnimations().forEach(a=>a.cancel());if(Math.abs(delta)>.5)n.animate([{transform:`translateY(${delta}px)`},{transform:'translateY(0)'}],{duration:160,easing:'cubic-bezier(.2,.8,.2,1)'});}
    }
    function saveOrder(id){try{if(!storage)throw Error();storage.setItem(ORDER_KEY,JSON.stringify(order));orderStatus.textContent=`${label(id)} 已移至第 ${order.indexOf(id)+1} 位`; }catch{orderStatus.textContent='顺序已调整；浏览器未允许保存';}}
    function moveDrag(){
      if(!drag?.moved)return;
      const rows=order.filter(id=>id!==drag.id),index=rows.filter(id=>{const n=accountNodes.get(id);return drag.y>accountList.getBoundingClientRect().top+n.offsetTop-accountList.scrollTop+n.offsetHeight/2;}).length;
      const next=moveAccount(order,drag.id,index);
      if(next.join()!==order.join()){order=next;applyOrder(true);}
      const top=drag.node.getBoundingClientRect().top-drag.dy;drag.dy=drag.y-drag.offset-top;drag.node.style.transform=`translateY(${drag.dy}px)`;
    }
    function scrollDrag(){
      if(!drag?.moved)return;const r=accountList.getBoundingClientRect(),delta=drag.y<r.top+28?-10:drag.y>r.bottom-28?10:0;
      if(delta){accountList.scrollTop+=delta;moveDrag();}dragFrame=requestAnimationFrame(scrollDrag);
    }
    function finishDrag(cancel=false){
      if(!drag)return;const done=drag;drag=null;cancelAnimationFrame(dragFrame);done.node.classList.remove('is-dragging');done.node.style.transform='';picker.classList.remove('is-sorting');
      if(picker.hasPointerCapture?.(done.pointer))picker.releasePointerCapture(done.pointer);
      if(done.moved){blockedUntil=Date.now()+700;if(cancel){order=done.before;applyOrder(true);orderStatus.textContent='已取消排序';}else saveOrder(done.id);}
      sync();
    }
    picker.addEventListener('pointerdown',e=>{const grip=e.target.closest('[data-account-drag]');if(!grip||e.button!==0||e.isPrimary===false)return;e.preventDefault();grip.focus({preventScroll:true});const node=grip.closest('[data-account-row]');drag={id:grip.dataset.accountDrag,node,pointer:e.pointerId,start:e.clientY,y:e.clientY,offset:e.clientY-node.getBoundingClientRect().top,dy:0,moved:false,before:[...order]};picker.setPointerCapture(e.pointerId);});
    picker.addEventListener('pointermove',e=>{if(!drag||e.pointerId!==drag.pointer)return;drag.y=e.clientY;if(!drag.moved&&Math.abs(e.clientY-drag.start)>5){drag.moved=true;drag.node.classList.add('is-dragging');picker.classList.add('is-sorting');dragFrame=requestAnimationFrame(scrollDrag);}if(drag.moved){e.preventDefault();moveDrag();}},{passive:false});
    picker.addEventListener('pointerup',e=>{if(e.pointerId===drag?.pointer)finishDrag();});
    picker.addEventListener('pointercancel',e=>{if(e.pointerId===drag?.pointer)finishDrag(true);});
    picker.addEventListener('lostpointercapture',()=>finishDrag(true));
    picker.addEventListener('dragstart',e=>e.preventDefault());
    window.addEventListener('blur',()=>finishDrag(true));
    window.addEventListener('storage',e=>{if((e.key===ORDER_KEY||e.key===null)&&!drag){try{order=accountOrder(JSON.parse(e.newValue||'null'),tracks);applyOrder();}catch{}}});
    const dialog=document.createElement('dialog');dialog.className='rs-book';dialog.setAttribute('aria-label','策略小册');
    dialog.innerHTML=`<article class="rs-sheet"><header class="rs-header"><span>${BOOK} 策略小册</span><b data-book-label tabindex="-1"></b><button type="button" data-book-close aria-label="关闭策略小册">×</button></header><div class="rs-layout"><nav class="rs-toc" aria-label="小册章节">${[['overview','概览'],['research','历史回测'],['rules','执行规则'],['forward','前向资金'],['sources','来源']].map(([id,title],i)=>`<button type="button" data-book-chapter="${id}"><small>0${i+1}</small>${title}</button>`).join('')}</nav><div class="rs-reading"><section id="rs-overview"></section><section id="rs-research" aria-live="polite"></section><section id="rs-rules"></section><section id="rs-forward"><div class="rs-section-title"><h2>前向模拟资金曲线</h2></div><p>当前策略完整账本 · USDT · 已实现净值</p><div class="rs-forward-status" role="status"></div><div class="rs-forward-metrics"></div><div class="rs-forward-chart" role="img" aria-label="前向模拟资金曲线"></div><p class="rs-forward-note"></p><button type="button" class="rs-text-button" data-book-refresh>刷新曲线</button></section><section id="rs-sources"></section></div></div></article>`;
    document.body.append(dialog);
    const el=s=>dialog.querySelector(s);let requests=window.SpikeRuntime.createRequests();
    let open=false,track='',epoch=0,closing=null,analytics=null,research=null,bars=[],line=null,chartData=null,active=true,history=null,historyChart=null,historyMode='equity',historyPaintKey='';
    function positionPicker(){const r=trigger.getBoundingClientRect();picker.style.left=Math.max(8,Math.min(r.left,innerWidth-picker.offsetWidth-8))+'px';picker.style.top=Math.max(8,Math.min(r.bottom+6,innerHeight-picker.offsetHeight-8))+'px';}
    function sync(){trigger.querySelector('b').textContent=label(getTrack());picker.querySelectorAll('[data-strategy-select]').forEach(b=>{const selected=b.dataset.strategySelect===getTrack();b.setAttribute('aria-pressed',String(selected));b.closest('[data-account-row]').dataset.selected=String(selected);});picker.querySelectorAll('[data-account-pnl]').forEach(n=>{const v=pnlView(getPnl?.(n.dataset.accountPnl));n.textContent=v.text;n.className=v.tone;n.title=v.title;});}
    trigger.addEventListener('click',()=>{if(picker.matches(':popover-open'))picker.hidePopover();else{sync();picker.showPopover();positionPicker();picker.querySelector(`[data-strategy-select="${getTrack()}"]`).focus({preventScroll:true});}});
    picker.addEventListener('toggle',()=>{const shown=picker.matches(':popover-open');trigger.setAttribute('aria-expanded',String(shown));root.classList.toggle('rs-picker-webkit',shown&&/AppleWebKit\//.test(navigator.userAgent)&&!/(Chrome|Chromium|Edg)\//.test(navigator.userAgent));if(!shown)finishDrag(true);});
    picker.addEventListener('keydown',event=>{if(event.key==='Escape'){event.stopPropagation();if(drag){event.preventDefault();finishDrag(true);}return;}const row=event.target.closest('[data-account-row]');if(!row)return;const i=order.indexOf(row.dataset.accountRow);if(event.altKey&&['ArrowDown','ArrowUp'].includes(event.key)){event.preventDefault();order=moveAccount(order,row.dataset.accountRow,i+(event.key==='ArrowDown'?1:-1));applyOrder(true);saveOrder(row.dataset.accountRow);return;}if(['ArrowDown','ArrowUp','Home','End'].includes(event.key)){event.preventDefault();const column=[...row.querySelectorAll('button')].indexOf(event.target),next=event.key==='Home'?0:event.key==='End'?order.length-1:(i+(event.key==='ArrowDown'?1:-1)+order.length)%order.length;accountNodes.get(order[next]).querySelectorAll('button')[Math.max(0,column)].focus();}});
    picker.addEventListener('click',event=>{if(event.detail!==0&&Date.now()<blockedUntil){event.preventDefault();event.stopPropagation();return;}const b=event.target.closest('button');if(!b)return;if(b.dataset.strategySelect){onSelect(b.dataset.strategySelect);sync();picker.hidePopover();trigger.focus({preventScroll:true});}if(b.dataset.strategyBook){picker.hidePopover();show(b.dataset.strategyBook);}if(b.dataset.strategyBacktest){picker.hidePopover();onBacktest?.(b.dataset.strategyBacktest);}});
    function stopCharts(){bars.forEach(c=>c.dispose());bars=[];line?.dispose();line=null;historyChart?.dispose();historyChart=null;historyPaintKey='';}
    function close(immediate=false){
      if(!open&&!dialog.open)return;open=false;epoch++;requests.cancel();analytics?.setActive(false);stopCharts();dialog.classList.remove('is-open');clearTimeout(closing);
      const finish=()=>{dialog.close();document.body.classList.remove('rs-book-open','rs-book-webkit');trigger.focus({preventScroll:true});};
      if(immediate||matchReduced())finish();else closing=setTimeout(finish,280);
    }
    async function charts(){
      const token=epoch;try{await window.SpikeModules.ensure('signal-charts');if(!open||token!==epoch)return;
        const dark=document.documentElement.dataset.theme==='dark';
        if(history&&historyPaintKey!==`${epoch}|${historyMode}|${dark}`){
          historyChart??=window.echarts.init(el('.rs-history-chart'));historyChart.setOption(equityOptions(history,historyMode,dark),true);
          const plots=[{labels:history.yearly.map(p=>p[0]),values:history.yearly.map(p=>p[1]),unit:history.refilled?'USDT':'%'},history.distribution,history.holding];
          dialog.querySelectorAll('[data-history-chart]').forEach((node,i)=>{bars[i]??=window.echarts.init(node);const option=barOptions(plots[i],dark);option.series[0].itemStyle.color=p=>p.value<0?'#df526c':'#3285dc';bars[i].setOption(option,true);});
          historyPaintKey=`${epoch}|${historyMode}|${dark}`;el('.rs-chart-status').textContent='';
        }
        if(chartData){line??=window.echarts.init(el('.rs-forward-chart'));line.setOption(window.SpikeReclaimAnalytics.chartOptions(chartData,dark),true);}
      }catch{if(open&&token===epoch){const n=el('.rs-chart-status');if(n)n.textContent='图表加载失败，表格数值仍可阅读。';el('.rs-forward-note').textContent+=' 曲线绘制失败，请刷新重试。';}}
    }
    function renderForward(){
      if(!open)return;const {data,error,busy}=analytics.getState();
      el('.rs-forward-status').textContent=error?`读取失败：${error}${data?' · 保留上次统计':''}`:data?`更新于 ${new Date(data.now_ms).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai'})} 北京时间`:'正在读取完整账本…';
      el('[data-book-refresh]').disabled=busy;
      if(!data)return;chartData=data;const s=data.summary;
      el('.rs-forward-metrics').innerHTML=[['已实现净盈亏',num(s.realized_pnl)+' USDT'],['已完全平仓',num(s.closed_count,0)+' 笔'],['最大已实现回撤',num(s.max_drawdown_pct)+'%']].map(([k,v])=>`<div><small>${k}</small><strong>${v}</strong></div>`).join('');
      el('.rs-forward-note').textContent=window.SpikeReclaimAnalytics.curveNote(data)+' 不含浮动盈亏。'+window.SpikeReclaimAnalytics.coverageNote(data.coverage);charts();
    }
    function rules(){
      const d=getSnapshot(),current=d?.tracks?.[track],lock=track==='v3lock',sp=track==='spike',all=track==='allin3',v3=['v3','v3add','v3lock','v3add20'].includes(track);
      const threshold=all?d?.allin3?.min_score:sp?d?.spike?.min_score:v3?d?.v3?.min_score:d?.model?.threshold;
      const offset=lock?d?.v3lock?.breakeven_offset_bp:d?.breakeven_offset_bp;
      el('#rs-overview').innerHTML=`<div class="rs-section-title"><h1>${esc(label(track))}<em>${esc(TITLES[track])}</em></h1></div><div class="rs-facts"><div><small>确认周期</small><b>${num(current?.confirm_minutes,0)} <small>分钟</small></b></div><div><small>入场分门槛</small><b>≥ ${num(threshold,4)}</b></div><div><small>${lock?'利润锁':'保护偏移'}</small><b>+${num(offset/100)}%</b></div></div>${!current?'<p class="rs-warning">策略规则尚未读取，参数保持空缺。稍后重新打开可重试。</p>':''}`;
      el('#rs-rules').innerHTML=`<div class="rs-section-title"><h2>从触价到退出</h2></div><ol class="rs-flow"><li><b>划区间</b><span>纽约周一高低点 H / L，宽度 W=H−L。上沿 H+0.618W，下沿 L−0.618W。</span></li><li><b>等触价</b><span>每周每侧首次触及；下沿做多、上沿做空。冻结入场评分按触价前信息计算，不是胜率。</span></li><li><b>等回收</b><span>触价所在K线之后，下一根确认周期K线收盘。未收回则退出；目标可在确认前成交。</span></li><li><b>保护余仓</b><span>收回且越过保护价才移动止损；收回但未越过时按确认收盘退出。多单加偏移，空单减偏移。</span></li></ol><h3>本策略当前规则</h3><div class="rs-current-rules">${current?renderRules(d,track):'<p>正在读取策略数据。</p>'}</div><p class="rs-muted">规则取自当前只读模拟盘接口；历史回测版本差异见回测章节。</p>`;
    }
    async function show(next){
      if(!tracks.includes(next))return;clearTimeout(closing);epoch++;const token=epoch;track=next;open=true;chartData=null;research=null;history=null;historyMode='equity';analytics?.setActive(false);requests.cancel();requests=window.SpikeRuntime.createRequests();stopCharts();
      el('[data-book-label]').textContent=label(track);rules();el('.rs-reading').scrollTop=0;
      el('#rs-research').innerHTML='<p class="rs-muted">正在读取回测资料…</p>';el('#rs-sources').innerHTML='';el('.rs-forward-metrics').innerHTML='';el('.rs-forward-note').textContent='';
      document.body.classList.add('rs-book-open');document.body.classList.toggle('rs-book-webkit',/AppleWebKit\//.test(navigator.userAgent)&&!/(Chrome|Chromium|Edg)\//.test(navigator.userAgent));if(!dialog.open)dialog.showModal();el('.rs-sheet').getBoundingClientRect();requestAnimationFrame(()=>{if(open&&token===epoch)dialog.classList.add('is-open');});el('[data-book-label]').focus({preventScroll:true});
      const selected=track;
      analytics=createFeed({read:()=>requests.json(`/api/monday-reclaim-paper/analytics?track=${selected}&days=0`),validateData:d=>window.SpikeReclaimAnalytics.validate(d,selected,0),cancel:()=>{},change:renderForward});analytics.setActive(active&&!document.hidden);
      try{
        const receipts=await Promise.allSettled([requests.json('/static/strategy-booklet/research.json'),requests.json(`/static/strategy-booklet/${selected}-history.json`)]);if(!open||token!==epoch)return;
        if(receipts[0].status!=='fulfilled')throw receipts[0].reason;
        const data=receipts[0].value;
        if(receipts[1].status==='fulfilled'){try{history=validateHistory(receipts[1].value,selected);}catch{history=null;}}
        el('#rs-research').innerHTML=researchHTML(data,selected,history);research=data;
        const r=data.accounts[selected];
        el('#rs-sources').innerHTML=`<div class="rs-section-title"><h2>口径与来源</h2></div><h3>回测方法</h3><p>${esc(data.methodology)}</p><p>${esc(data.comparison)}</p><details class="rs-source-note"><summary>结果有效性说明</summary><p>${esc(data.integrity)}</p></details><h3>连续曲线核算</h3><p>复用原始框架成交与仓位核算函数，保存每次结算；未重新生成交易。费用与滑点已在成交净收益中，不二次扣除。不含浮盈亏，因此不计算基于每日盯市收益的夏普比率。</p>${history?`<p>路径导出版本：${esc(history.source_commit)} · 固定种子：${esc(history.meta.seed??'原研究稳定排序')}</p>`:''}<p>版式参考 <a href="https://www.tradingview.com/support/solutions/43000764138-tradingview-strategy-report-how-to-start/" target="_blank" rel="noopener">TradingView</a>、<a href="https://github.com/ranaroussi/quantstats" target="_blank" rel="noopener">QuantStats</a>。未有同口径基准账本的策略不叠加伪造基准。</p><h3>原始资料</h3>${r.sources.map(s=>`<details class="rs-source-note"><summary>${esc(s.path.split('/').at(-2))}</summary><p>${esc(s.path)}</p><p>读取字段：${esc(s.selection)}</p><p>研究代码：${esc(s.commit||'原研究未记录')}</p></details>`).join('')}<p class="rs-muted">资料核对 ${esc(data.reviewed_on)} · 历史研究与前向模拟分别展示</p>`;
        el('#rs-research').querySelectorAll('img').forEach(img=>img.addEventListener('error',()=>{img.hidden=true;img.parentElement.insertAdjacentHTML('beforeend','<p>原图暂时无法读取，请稍后重试。</p>');},{once:true}));charts();
      }catch(error){if(open&&token===epoch)el('#rs-research').innerHTML=`<p class="rs-warning">回测资料暂时不可用：${esc(error.message)}</p><button type="button" data-book-retry>重试读取</button>`;}
    }
    dialog.addEventListener('cancel',e=>{e.preventDefault();close();});
    dialog.addEventListener('click',e=>{if(e.target===dialog||e.target.closest('[data-book-close]'))close();if(e.target.closest('[data-book-refresh]')){analytics?.refresh();charts();}if(e.target.closest('[data-book-retry]'))show(track);const mode=e.target.closest('[data-history-mode]');if(mode&&history){historyMode=mode.dataset.historyMode;dialog.querySelectorAll('[data-history-mode]').forEach(b=>b.setAttribute('aria-pressed',String(b===mode)));charts();}const chapter=e.target.closest('[data-book-chapter]');if(chapter)el('#rs-'+chapter.dataset.bookChapter).scrollIntoView({behavior:matchReduced()?'instant':'smooth',block:'start'});});
    new ResizeObserver(()=>{bars.forEach(c=>c.resize());line?.resize();historyChart?.resize();}).observe(el('.rs-reading'));
    new MutationObserver(()=>{if(open)charts();}).observe(document.documentElement,{attributes:true,attributeFilter:['data-theme']});
    window.addEventListener('resize',()=>{if(picker.matches(':popover-open'))positionPicker();});
    document.addEventListener('visibilitychange',()=>analytics?.setActive(active&&open&&!document.hidden));
    sync();return {sync,close,getOrder:()=>[...order],setActive(value){active=value;if(!value){if(picker.matches(':popover-open'))picker.hidePopover();close(true);}}};
  }
  window.SpikeStrategyBook={...api,create};
})();
