/* Stitch b28188b2e438404593e684445325de54. Read-only, source-backed strategy books.
 * The selected book is independent of the selected paper account. */
(() => {
  'use strict';
  const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const label=t=>({60:'60m',5:'5m'}[t]||t);
  const TITLES={60:'等待回收',5:'快速确认',v3:'分数决定目标',v3add:'逆向分步加仓',spike:'排列过滤插针',v3lock:'回收后锁住利润',allin3:'单仓 · 梭哈3倍',v3add20:'20份动态保证金 · 3倍加仓'};
  const BOOK='<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" aria-hidden="true"><path d="M12 5C8 2 4 3 2 4v15c3-1 6-1 10 2 4-3 7-3 10-2V4c-2-1-6-2-10 1Zm0 0v16"/></svg>';
  const num=(v,d=2)=>typeof v==='number'&&Number.isFinite(v)?v.toLocaleString('en-US',{maximumFractionDigits:d}):'—';
  function pickerHTML(tracks){return tracks.map(t=>`<div class="rs-account-row"><button type="button" data-strategy-select="${esc(t)}" aria-pressed="false"><span>${esc(label(t))}</span><small>${esc(TITLES[t])}</small><i aria-hidden="true">✓</i></button><button type="button" data-strategy-book="${esc(t)}" aria-label="查看 ${esc(label(t))} 策略小册" title="策略小册">${BOOK}</button></div>`).join('');}
  function tableHTML(headers,rows){return `<div class="rs-table-wrap"><table><thead><tr>${headers.map(h=>`<th scope="col">${esc(h)}</th>`).join('')}</tr></thead><tbody>${rows.map(row=>`<tr>${row.map((v,i)=>i?`<td>${typeof v==='number'?num(v):esc(v??'—')}</td>`:`<th scope="row">${esc(v)}</th>`).join('')}</tr>`).join('')}</tbody></table></div>`;}
  function researchHTML(data,t){
    const r=data.accounts[t];if(!r)throw Error('该策略的小册资料尚未收录');
    const image=im=>`<figure class="rs-history-figure"><img class="rs-research-image" loading="lazy" src="/static/strategy-booklet/${esc(im.file)}" alt="${esc(im.caption)}"><figcaption>${esc(im.caption)} <a href="/static/strategy-booklet/${esc(im.file)}" target="_blank" rel="noopener">查看原图 ↗</a></figcaption></figure>`;
    const plots=[r.chart,...(r.charts||[])];
    return `<div class="rs-section-title"><h2>历史回测</h2><span class="rs-badge">${esc(r.status)}</span></div><p class="rs-research-note">${esc(r.note)}</p>${r.images?.length?image(r.images[0]):`<p class="rs-missing-path">本版本未保存连续历史资金路径，现有资金结果为分区间汇总。${r.references?.length?'下方可展开早期版本曲线作参考。':''}</p>`}<div class="rs-chart-grid">${plots.map((plot,i)=>`<figure><figcaption>${esc(plot.title)} <small> / ${esc(plot.unit)}</small></figcaption><div class="rs-bar-chart" data-research-chart="${i}" role="img" aria-label="${esc(plot.title)}"></div></figure>`).join('')}</div><p class="rs-chart-status" role="status"></p>${r.detail?`<p class="rs-muted">${esc(r.detail)}</p>`:''}<details class="rs-detail-table" open><summary>回测明细</summary>${tableHTML(r.headers,r.rows)}${r.capital_rows?`<h3>资金回放</h3>${tableHTML(r.capital_headers,r.capital_rows)}`:''}</details><p class="rs-muted">${esc(data.period_note)}</p>${(r.images||[]).slice(1).map(image).join('')}${r.controls?`<h3>是否优于同条件随机入场</h3><p class="rs-muted">${esc(r.controls.note)}</p>${tableHTML(r.controls.headers,r.controls.rows)}`:''}${r.references?.length?`<details class="rs-detail-table"><summary>早期研发资金曲线 · 参数与当前版本不同</summary>${r.references.map(image).join('')}</details>`:''}`;
  }
  function barOptions(r,dark=false){const c=dark?'#9ba9b8':'#738392';return {animation:!matchReduced(),animationDuration:450,grid:{left:55,right:18,top:25,bottom:r.labels.length>8?45:60},tooltip:{trigger:'axis',confine:true,valueFormatter:v=>num(v)+' '+r.unit},xAxis:{type:'category',data:r.labels,axisTick:{show:false},axisLabel:{color:c,fontSize:9,hideOverlap:true,interval:r.labels.length>8?'auto':0,formatter:v=>v.replace(' · ','\n')}},yAxis:{type:'value',name:r.unit,axisLabel:{color:c},splitLine:{lineStyle:{color:dark?'#ffffff12':'#71849420'}}},series:[{type:'bar',barMaxWidth:40,data:r.values,itemStyle:{color:'#3285dc',borderRadius:[4,4,0,0]}}]};}
  function matchReduced(){return typeof matchMedia==='function'&&matchMedia('(prefers-reduced-motion:reduce)').matches;}
  const api={pickerHTML,tableHTML,researchHTML,barOptions,TITLES};
  if(typeof module!=='undefined'&&module.exports){module.exports=api;return;}
  function create({root,tracks,getTrack,getSnapshot,renderRules,createFeed,onSelect}){
    const trigger=root.querySelector('[data-rp="track"]');
    const picker=document.createElement('div');picker.id='rs-account-picker';picker.className='rs-account-picker';picker.setAttribute('popover','auto');picker.setAttribute('role','dialog');picker.setAttribute('aria-label','模拟账户');picker.innerHTML=pickerHTML(tracks);root.append(picker);
    const dialog=document.createElement('dialog');dialog.className='rs-book';dialog.setAttribute('aria-label','策略小册');
    dialog.innerHTML=`<article class="rs-sheet"><header class="rs-header"><span>${BOOK} 策略小册</span><b data-book-label tabindex="-1"></b><button type="button" data-book-close aria-label="关闭策略小册">×</button></header><div class="rs-layout"><nav class="rs-toc" aria-label="小册章节">${[['overview','概览'],['research','历史回测'],['rules','执行规则'],['forward','前向资金'],['sources','来源']].map(([id,title],i)=>`<button type="button" data-book-chapter="${id}"><small>0${i+1}</small>${title}</button>`).join('')}</nav><div class="rs-reading"><section id="rs-overview"></section><section id="rs-research" aria-live="polite"></section><section id="rs-rules"></section><section id="rs-forward"><div class="rs-section-title"><h2>前向模拟资金曲线</h2></div><p>当前账户完整账本 · USDT · 已实现净值</p><div class="rs-forward-status" role="status"></div><div class="rs-forward-metrics"></div><div class="rs-forward-chart" role="img" aria-label="前向模拟资金曲线"></div><p class="rs-forward-note"></p><button type="button" class="rs-text-button" data-book-refresh>刷新曲线</button></section><section id="rs-sources"></section></div></div></article>`;
    document.body.append(dialog);
    const el=s=>dialog.querySelector(s);let requests=window.SpikeRuntime.createRequests();
    let open=false,track='',epoch=0,closing=null,analytics=null,research=null,bars=[],line=null,chartData=null,active=true;
    function positionPicker(){const r=trigger.getBoundingClientRect();picker.style.left=Math.max(8,Math.min(r.left,innerWidth-288))+'px';picker.style.top=Math.max(8,Math.min(r.bottom+6,innerHeight-picker.offsetHeight-8))+'px';}
    function sync(){trigger.querySelector('b').textContent=label(getTrack());picker.querySelectorAll('[data-strategy-select]').forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.strategySelect===getTrack())));}
    trigger.addEventListener('click',()=>{if(picker.matches(':popover-open'))picker.hidePopover();else{sync();picker.showPopover();positionPicker();picker.querySelector(`[data-strategy-select="${getTrack()}"]`).focus({preventScroll:true});}});
    picker.addEventListener('toggle',()=>trigger.setAttribute('aria-expanded',String(picker.matches(':popover-open'))));
    picker.addEventListener('keydown',event=>{if(event.key==='Escape')event.stopPropagation();if(['ArrowDown','ArrowUp','Home','End'].includes(event.key)){event.preventDefault();const buttons=[...picker.querySelectorAll('button')],i=buttons.indexOf(document.activeElement);buttons[event.key==='Home'?0:event.key==='End'?buttons.length-1:(i+(event.key==='ArrowDown'?2:-2)+buttons.length)%buttons.length].focus();}});
    picker.addEventListener('click',event=>{const b=event.target.closest('button');if(!b)return;if(b.dataset.strategySelect){onSelect(b.dataset.strategySelect);sync();picker.hidePopover();trigger.focus({preventScroll:true});}if(b.dataset.strategyBook){picker.hidePopover();show(b.dataset.strategyBook);}});
    function stopCharts(){bars.forEach(c=>c.dispose());bars=[];line?.dispose();line=null;}
    function close(immediate=false){
      if(!open&&!dialog.open)return;open=false;epoch++;requests.cancel();analytics?.setActive(false);stopCharts();dialog.classList.remove('is-open');clearTimeout(closing);
      const finish=()=>{dialog.close();document.body.classList.remove('rs-book-open','rs-book-webkit');trigger.focus({preventScroll:true});};
      if(immediate||matchReduced())finish();else closing=setTimeout(finish,280);
    }
    async function charts(){
      const token=epoch;try{await window.SpikeModules.ensure('signal-charts');if(!open||token!==epoch)return;
        const dark=document.documentElement.dataset.theme==='dark';
        if(research){const r=research.accounts[track],plots=[r.chart,...(r.charts||[])];dialog.querySelectorAll('[data-research-chart]').forEach((node,i)=>{bars[i]??=window.echarts.init(node);bars[i].setOption(barOptions(plots[i],dark),true);});el('.rs-chart-status').textContent='';}
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
      el('#rs-overview').innerHTML=`<div class="rs-section-title"><h1>${esc(label(track))}<em>${esc(TITLES[track])}</em></h1></div><div class="rs-facts"><div><small>确认周期</small><b>${num(current?.confirm_minutes,0)} <small>分钟</small></b></div><div><small>入场分门槛</small><b>≥ ${num(threshold,4)}</b></div><div><small>${lock?'利润锁':'保护偏移'}</small><b>+${num(offset/100)}%</b></div></div>${!current?'<p class="rs-warning">账户规则尚未读取，参数保持空缺。稍后重新打开可重试。</p>':''}`;
      el('#rs-rules').innerHTML=`<div class="rs-section-title"><h2>从触价到退出</h2></div><ol class="rs-flow"><li><b>划区间</b><span>纽约周一高低点 H / L，宽度 W=H−L。上沿 H+0.618W，下沿 L−0.618W。</span></li><li><b>等触价</b><span>每周每侧首次触及；下沿做多、上沿做空。冻结入场评分按触价前信息计算，不是胜率。</span></li><li><b>等回收</b><span>触价所在K线之后，下一根确认周期K线收盘。未收回则退出；目标可在确认前成交。</span></li><li><b>保护余仓</b><span>收回且越过保护价才移动止损；收回但未越过时按确认收盘退出。多单加偏移，空单减偏移。</span></li></ol><h3>本账户当前规则</h3><div class="rs-current-rules">${current?renderRules(d,track):'<p>等待账户接口返回。</p>'}</div><p class="rs-muted">规则取自当前只读模拟盘接口；历史回测版本差异见回测章节。</p>`;
    }
    async function show(next){
      if(!tracks.includes(next))return;clearTimeout(closing);epoch++;const token=epoch;track=next;open=true;chartData=null;research=null;analytics?.setActive(false);requests.cancel();requests=window.SpikeRuntime.createRequests();stopCharts();
      el('[data-book-label]').textContent=label(track);rules();el('.rs-reading').scrollTop=0;
      el('#rs-research').innerHTML='<p class="rs-muted">正在读取回测资料…</p>';el('#rs-sources').innerHTML='';el('.rs-forward-metrics').innerHTML='';el('.rs-forward-note').textContent='';
      document.body.classList.add('rs-book-open');document.body.classList.toggle('rs-book-webkit',/AppleWebKit\//.test(navigator.userAgent)&&!/(Chrome|Chromium|Edg)\//.test(navigator.userAgent));if(!dialog.open)dialog.showModal();el('.rs-sheet').getBoundingClientRect();requestAnimationFrame(()=>{if(open&&token===epoch)dialog.classList.add('is-open');});el('[data-book-label]').focus({preventScroll:true});
      const selected=track;
      analytics=createFeed({read:()=>requests.json(`/api/monday-reclaim-paper/analytics?track=${selected}&days=0`),validateData:d=>window.SpikeReclaimAnalytics.validate(d,selected,0),cancel:()=>{},change:renderForward});analytics.setActive(active&&!document.hidden);
      try{
        const data=await requests.json('/static/strategy-booklet/research.json');if(!open||token!==epoch)return;
        el('#rs-research').innerHTML=researchHTML(data,selected);research=data;
        const r=data.accounts[selected];
        el('#rs-sources').innerHTML=`<div class="rs-section-title"><h2>口径与来源</h2></div><h3>回测方法</h3><p>${esc(data.methodology)}</p><p>${esc(data.comparison)}</p><details class="rs-source-note"><summary>结果有效性说明</summary><p>${esc(data.integrity)}</p></details><h3>原始资料</h3>${r.sources.map(s=>`<details class="rs-source-note"><summary>${esc(s.path.split('/').at(-2))}</summary><p>${esc(s.path)}</p><p>读取字段：${esc(s.selection)}</p><p>研究代码：${esc(s.commit||'原研究未记录')}</p></details>`).join('')}<p class="rs-muted">资料核对 ${esc(data.reviewed_on)} · 历史研究与前向模拟分别展示</p>`;
        el('#rs-research').querySelectorAll('img').forEach(img=>img.addEventListener('error',()=>{img.hidden=true;img.parentElement.insertAdjacentHTML('beforeend','<p>原图暂时无法读取，请稍后重试。</p>');},{once:true}));charts();
      }catch(error){if(open&&token===epoch)el('#rs-research').innerHTML=`<p class="rs-warning">回测资料暂时不可用：${esc(error.message)}</p><button type="button" data-book-retry>重试读取</button>`;}
    }
    dialog.addEventListener('cancel',e=>{e.preventDefault();close();});
    dialog.addEventListener('click',e=>{if(e.target===dialog||e.target.closest('[data-book-close]'))close();if(e.target.closest('[data-book-refresh]')){analytics?.refresh();charts();}if(e.target.closest('[data-book-retry]'))show(track);const chapter=e.target.closest('[data-book-chapter]');if(chapter)el('#rs-'+chapter.dataset.bookChapter).scrollIntoView({behavior:matchReduced()?'instant':'smooth',block:'start'});});
    new ResizeObserver(()=>{bars.forEach(c=>c.resize());line?.resize();}).observe(el('.rs-reading'));
    new MutationObserver(()=>{if(open)charts();}).observe(document.documentElement,{attributes:true,attributeFilter:['data-theme']});
    window.addEventListener('resize',()=>{if(picker.matches(':popover-open'))positionPicker();});
    document.addEventListener('visibilitychange',()=>analytics?.setActive(active&&open&&!document.hidden));
    sync();return {sync,close,setActive(value){active=value;if(!value){if(picker.matches(':popover-open'))picker.hidePopover();close(true);}}};
  }
  window.SpikeStrategyBook={...api,create};
})();
