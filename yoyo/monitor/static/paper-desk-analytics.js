/* Account-only history. Values originate in recorded fills or sampled equity. */
(() => {
  'use strict';
  const escape = v => String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const amount = v => Number(v).toLocaleString('zh-CN',{useGrouping:false,minimumFractionDigits:2,maximumFractionDigits:2});
  const signed = v => `${Number(v)>0?'+':Number(v)<0?'−':''}${amount(Math.abs(Number(v)))}`;
  const color = v => Number(v)<0?'desk-short':Number(v)>0?'desk-long':'';
  const date = at => new Date(at).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false});
  function chartModel(data,view) {
    const equity=view.kind==='equity', key=equity?'equity':'balance';
    let points=(equity?data.equity_curve:data.balance_curve).map(p=>({at:p.at??p.observed_ms,value:p[key]==null?null:Number(p[key])}));
    const start=view.span==='all'?0:data.now_ms-Number(view.span)*86400000;
    if(start){const previous=[...points].reverse().find(p=>p.at<start);points=points.filter(p=>p.at>=start);if(previous&&!equity)points.unshift({...previous,at:start});}
    const valid=points.filter(p=>p.value!==null&&Number.isFinite(p.value));
    if(!valid.length)return null;
    const min=Math.min(...valid.map(p=>p.value)),max=Math.max(...valid.map(p=>p.value));
    const padding=Math.max((max-min)*.18,Math.abs(max)*.0005,.01),low=min-padding,high=max+padding;
    const t0=points[0].at,t1=Math.max(points[points.length-1].at,t0+60000);
    const x=at=>8+(at-t0)/(t1-t0)*984,y=value=>210-(value-low)/(high-low)*196;
    const segments=[];let segment=null,last=null;
    for(const point of points){
      if(point.value===null||!Number.isFinite(point.value)){last=null;continue;}
      const px=x(point.at).toFixed(2),py=y(point.value).toFixed(2);
      const gap=equity&&last&&point.at-last.at>125000;
      if(!last||gap){segment={path:` M ${px},${py}`,firstX:px,lastX:px};segments.push(segment);}
      else {segment.path+=equity?` L ${px},${py}`:` H ${px} V ${py}`;segment.lastX=px;}
      last=point;
    }
    const latest=valid[valid.length-1],change=latest.value-valid[0].value;
    return {equity,valid,latest,change,t0,t1,low,high,x,y,
      path:segments.map(s=>s.path).join(''),
      area:segments.map(s=>`${s.path} L ${s.lastX},224 L ${s.firstX},224 Z`).join('')};
  }
  function pointIndex(model,ratio) {
    const target=model.t0+(model.t1-model.t0)*Math.max(0,Math.min(1,ratio));
    let index=0,distance=Infinity;
    model.valid.forEach((point,i)=>{const delta=Math.abs(point.at-target);if(delta<=distance){index=i;distance=delta;}});
    return index;
  }
  function cursorIndex(model,cursor) {return Math.max(0,Math.min(Number.isInteger(cursor)?cursor:model.valid.length-1,model.valid.length-1));}
  function chart(data,view) {
    const m=chartModel(data,view);
    if(!m)return '<div class="desk-history-empty">权益正在采样<span>保留真实行情观测，约一分钟后出现首个数据点</span></div>';
    const index=cursorIndex(m,view.cursor),selected=m.valid[index],active=view.cursor!=null;
    const percent=m.valid[0].value===0?'':` (${signed(m.change/Math.abs(m.valid[0].value)*100)}%)`;
    return `<div class="desk-chart-overview"><div class="desk-chart-value"><div><strong>${amount(m.latest.value)}</strong><span>USDT</span></div><span class="desk-chart-change ${color(m.change)}">${signed(m.change)} USDT${percent}<small>区间变化</small></span></div><div class="desk-chart-periods" aria-label="资金曲线时间范围">${[['7','7 天'],['30','30 天'],['all','全部']].map(([k,label])=>`<button type="button" data-desk-chart-span="${k}" aria-pressed="${view.span===k}">${label}</button>`).join('')}</div></div>
      <div class="desk-chart-frame ${m.change<0?'is-negative':''}"><div class="desk-chart-plot ${active?'is-inspecting':''}" data-desk-chart-plot role="slider" tabindex="0" aria-label="${m.equity?'账户权益':'已结算余额'}曲线，左右键查看数据点" aria-valuemin="0" aria-valuemax="${m.valid.length-1}" aria-valuenow="${index}" aria-valuetext="${date(selected.at)}，${amount(selected.value)} USDT">
      <svg class="desk-equity-svg" viewBox="0 0 1000 224" preserveAspectRatio="none" aria-hidden="true">
      <defs><linearGradient id="desk-curve-fill" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stop-color="var(--chart-accent)" stop-opacity=".16"/><stop offset="100%" stop-color="var(--chart-accent)" stop-opacity=".01"/></linearGradient></defs>
      ${[0,1,2,3].map(i=>{const py=m.y(m.low+(m.high-m.low)*i/3);return `<line x1="0" x2="1000" y1="${py}" y2="${py}" class="desk-chart-grid" vector-effect="non-scaling-stroke"/>`;}).join('')}
      <path d="${m.path}" fill="none" class="desk-chart-line" vector-effect="non-scaling-stroke"/>
      <path d="${m.area}" class="desk-chart-area"/>
      <line class="desk-chart-cursor" x1="${m.x(selected.at)}" x2="${m.x(selected.at)}" y1="0" y2="224" vector-effect="non-scaling-stroke"/></svg>
      <span class="desk-chart-point" data-chart-point style="left:${m.x(selected.at)/10}%;top:${m.y(selected.value)/224*100}%" aria-hidden="true"></span>
      <div class="desk-chart-tooltip" data-chart-tooltip style="--cursor-x:${m.x(selected.at)/10}%" aria-hidden="true"><span data-chart-date>${date(selected.at)}</span><strong data-chart-amount>${amount(selected.value)} <small>USDT</small></strong></div>
      </div><div class="desk-chart-y-axis" aria-hidden="true">${[0,1,2,3].map(i=>{const value=m.low+(m.high-m.low)*i/3;return `<span style="top:${m.y(value)/224*100}%">${amount(value)}</span>`;}).join('')}</div></div>
      <div class="desk-chart-x-axis" aria-hidden="true"><span>${date(m.t0)}</span><span>${date(m.t1)}</span></div>
      <p class="desk-history-note">${m.equity?'每分钟记录权益；断线留空，上线前不补造历史浮盈。':'余额包含已实现盈亏和全部手续费；未平仓浮盈不计入。'}</p>`;
  }
  function updateCursor(plot,m,view,cursor) {
    view.cursor=cursor;const index=cursorIndex(m,cursor),selected=m.valid[index],x=m.x(selected.at),y=m.y(selected.value);
    plot.classList.toggle('is-inspecting',cursor!=null);
    plot.setAttribute('aria-valuenow',String(index));plot.setAttribute('aria-valuetext',`${date(selected.at)}，${amount(selected.value)} USDT`);
    const guide=plot.querySelector('.desk-chart-cursor');guide.setAttribute('x1',x);guide.setAttribute('x2',x);
    const point=plot.querySelector('[data-chart-point]');point.style.left=x/10+'%';point.style.top=y/224*100+'%';
    plot.querySelector('[data-chart-tooltip]').style.setProperty('--cursor-x',x/10+'%');
    plot.querySelector('[data-chart-date]').textContent=date(selected.at);
    plot.querySelector('[data-chart-amount]').innerHTML=amount(selected.value)+' <small>USDT</small>';
  }
  function bind(root,getContext) {
    let cachedData,cachedKind,cachedSpan,model;
    const context=target=>{
      const plot=target.closest?.('[data-desk-chart-plot]'),{data,view}=getContext();if(!plot||!data)return null;
      if(data!==cachedData||view.kind!==cachedKind||view.span!==cachedSpan){model=chartModel(data,view);cachedData=data;cachedKind=view.kind;cachedSpan=view.span;}
      return model?{plot,model,view}:null;
    };
    function point(event){
      if(event.type==='pointermove'&&event.pointerType!=='mouse')return;
      if(event.type==='pointerdown'&&event.button!==0)return;
      const c=context(event.target);if(!c)return;
      const rect=c.plot.getBoundingClientRect();
      updateCursor(c.plot,c.model,c.view,pointIndex(c.model,((event.clientX-rect.left)/rect.width*1000-8)/984));
    }
    root.addEventListener('pointerdown',point);root.addEventListener('pointermove',point);
    root.addEventListener('pointerout',event=>{const c=context(event.target);if(c&&event.pointerType==='mouse'&&!c.plot.contains(event.relatedTarget))updateCursor(c.plot,c.model,c.view,null);});
    root.addEventListener('keydown',event=>{
      const c=context(event.target);if(!c)return;
      const current=cursorIndex(c.model,c.view.cursor),keys={ArrowLeft:current-1,ArrowRight:current+1,Home:0,End:c.model.valid.length-1,Escape:null};
      if(!(event.key in keys))return;event.preventDefault();
      updateCursor(c.plot,c.model,c.view,event.key==='Escape'?null:cursorIndex(c.model,keys[event.key]));
    });
  }
  function calendar(data,view) {
    const [year,month]=data.month.split('-').map(Number), days=new Date(Date.UTC(year,month,0)).getUTCDate();
    const offset=(new Date(Date.UTC(year,month-1,1)).getUTCDay()+6)%7;
    const byDate=new Map(data.days.map(d=>[d.date,d]));
    const chosen=byDate.get(view.day), blanks=Array.from({length:offset},()=>'<span class="desk-calendar-blank" aria-hidden="true"></span>').join('');
    const today=new Date(data.now_ms+8*3600000).toISOString().slice(0,10);
    return `<div class="desk-calendar-summary"><strong class="${color(data.month_net)}">${signed(data.month_net)} <small>USDT</small></strong><span><i class="desk-long">${data.profitable_days} 盈利日</i> / <i class="desk-short">${data.losing_days} 亏损日</i></span></div>
      <div class="desk-calendar-week">${['一','二','三','四','五','六','日'].map(d=>`<span>${d}</span>`).join('')}</div><div class="desk-calendar-grid">${blanks}${Array.from({length:days},(_,i)=>{
        const day=`${data.month}-${String(i+1).padStart(2,'0')}`,entry=byDate.get(day),n=entry?Number(entry.net):null;
        return `<button type="button" class="desk-calendar-day ${n===null?'':n<0?'is-loss':n>0?'is-profit':'is-flat'} ${day===today?'is-today':''}" data-desk-day="${day}" aria-pressed="${view.day===day}" aria-label="${day} ${entry?'净盈亏 '+signed(n)+' USDT，'+entry.fills+' 笔成交':'无成交'}" ${day>today?'disabled':''}><span>${i+1}</span><strong>${entry?(n>0?'+':n<0?'−':'')+Math.abs(n).toLocaleString('zh-CN',{useGrouping:false,maximumFractionDigits:Math.abs(n)>=100?0:2}):'—'}</strong></button>`;
      }).join('')}</div><div class="desk-calendar-detail" aria-live="polite">${view.day?`<strong>${escape(view.day)}</strong><span>${chosen?`净盈亏 <b class="${color(chosen.net)}">${signed(chosen.net)} U</b> · ${chosen.closes} 笔平仓 · 费用 ${amount(chosen.fees)} U`:'当天暂无成交'}</span>`:'选择日期查看当天盈亏和手续费'}</div><p class="desk-history-note">北京时间 · 当日已实现盈亏减去当日全部费用</p>`;
  }
  function statistics(data) {
    const s=data.statistics;
    if(!s)return '<section class="desk-card desk-statistics"><header class="desk-history-header"><h2>账户交易统计</h2></header><p class="desk-history-note">交易统计暂不可用，请刷新重试。</p></section>';
    const number=(v,unit='',sign=false)=>v==null?'—':`${sign?signed(v):amount(v)}${unit}`;
    const duration=s.average_holding_ms==null?'—':s.average_holding_ms<60000?'< 1 分钟':s.average_holding_ms<3600000?`${Math.floor(s.average_holding_ms/60000)} 分钟`:s.average_holding_ms<86400000?`${amount(s.average_holding_ms/3600000)} 小时`:`${amount(s.average_holding_ms/86400000)} 天`;
    const winWidth=s.closed_positions?s.wins/s.closed_positions*100:0,lossWidth=s.closed_positions?s.losses/s.closed_positions*100:0;
    const metrics=[
      ['盈利因子',s.profit_factor==null?(s.wins?'无亏损':'—'):amount(s.profit_factor),'盈利总额 ÷ 亏损总额绝对值',''],
      ['累计手续费',number(s.total_fees,' USDT'),'全部开仓、平仓及清算费用',''],
      ['最大余额回撤',number(s.max_balance_drawdown_percent,'%'),'已结算余额从历史高点的最大跌幅，不包含浮动权益回撤',''],
      ['成交总额',number(s.turnover,' USDT'),'累计开仓与平仓成交金额',''],
      ['平均盈利',number(s.average_win,' USDT',true),'盈利仓位的平均净收益',color(s.average_win)],
      ['平均亏损',number(s.average_loss,' USDT',true),'亏损仓位的平均净收益',color(s.average_loss)],
      ['最佳单笔',number(s.best_trade,' USDT',true),'已完全平仓仓位中的最高净收益',color(s.best_trade)],
      ['最差单笔',number(s.worst_trade,' USDT',true),'已完全平仓仓位中的最低净收益',color(s.worst_trade)]
    ];
    return `<section class="desk-card desk-statistics" aria-label="账户交易统计"><header class="desk-history-header"><h2>账户交易统计</h2><span class="desk-stat-scope">全部记录</span></header>
      <div class="desk-stat-highlights">
        <div class="desk-stat-result"><span>已结算净收益 <small>USDT</small></span><strong class="${color(s.total_net)}">${number(s.total_net,'',true)}</strong><small class="${color(s.return_percent)}">收益率 ${number(s.return_percent,'%',true)}</small></div>
        <div><span>已完成交易</span><strong>${s.closed_positions}<small> 笔</small></strong><small><i class="desk-long">${s.wins} 盈利</i> / <i class="desk-short">${s.losses} 亏损</i> / ${s.breakeven} 持平</small></div>
        <div><span>胜率</span><strong>${number(s.win_rate,'%')}</strong><span class="desk-stat-winbar" aria-hidden="true"><i style="width:${winWidth}%"></i><i style="width:${lossWidth}%"></i></span></div>
        <div><span title="平均盈利 ÷ 平均亏损绝对值">平均盈亏比</span><strong>${number(s.payoff_ratio)}</strong><small>${s.closed_positions?'已扣除开平仓费用':'完成首笔交易后统计'}</small></div>
      </div>
      <dl class="desk-stat-grid">${metrics.map(([label,value,title,tone])=>`<div title="${title}"><dt>${label}</dt><dd class="${tone}">${value}</dd></div>`).join('')}</dl>
      <footer class="desk-stat-footer"><span>平均持仓 <strong>${escape(duration)}</strong></span><details><summary>统计口径</summary><p>胜率、盈亏比按已完全平仓的仓位统计；加仓和分批平仓合并为一笔，扣除全部手续费，持平计入交易总数。已结算净收益包含未平仓仓位已结算的收益和手续费，不包含浮动盈亏。余额回撤按每笔成交结算计算。</p></details></footer></section>`;
  }
  function render(data, view, error='') {
    if(!data)return `<section class="desk-card desk-history-loading" aria-label="账户资金分析">${error?escape(error):'正在读取账户资金记录…'}<button type="button" class="research-button" data-desk-history-refresh>刷新</button></section>`;
    return `<section class="desk-account-history" aria-label="账户资金分析">${statistics(data)}<div class="desk-card desk-curve-card"><header class="desk-history-header"><h2>资金曲线</h2><div class="desk-history-switch">${[['balance','余额'],['equity','权益']].map(([k,label])=>`<button type="button" data-desk-chart-kind="${k}" aria-pressed="${view.kind===k}">${label}</button>`).join('')}</div></header><div data-desk-chart-content>${chart(data,view)}</div></div><div class="desk-card desk-calendar-card"><header class="desk-history-header"><div><span class="desk-history-eyebrow">DAILY P&L</span><h2>盈亏日历</h2></div><div class="desk-calendar-nav"><button type="button" data-desk-month="-1" aria-label="上个月">‹</button><strong>${escape(data.month.replace('-',' / '))}</strong><button type="button" data-desk-month="1" aria-label="下个月">›</button></div></header>${calendar(data,view)}</div></section>${error?`<p class="research-error">${escape(error)}</p>`:''}`;
  }
  window.SpikePaperHistory={render,chart,bind,chartModel,pointIndex,statistics};
})();
