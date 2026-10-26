/* Stitch f6ca485dbc9440bcabc2a9058a9d4768. Complete-ledger analytics are
 * fetched only while visible; cash tranches and final trade outcomes stay distinct. */
(() => {
  'use strict';
  const finite=v=>typeof v==='number'&&Number.isFinite(v);
  const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const num=(v,d=2)=>finite(v)?v.toLocaleString('en-US',{minimumFractionDigits:d,maximumFractionDigits:d}):'—';
  const signed=v=>finite(v)?(v>0?'+':'')+num(v):'—';
  const tone=v=>finite(v)&&v!==0?(v>0?'rp-positive':'rp-negative'):'';
  const rate=v=>finite(v)?num(v,1)+'%':'—';
  const reason=v=>({target:'到达目标',breakeven:'保本止损',backstop:'兜底止损',reclaimed_inside_be:'收回但未越过保本价',failed_reclaim:'确认未收回',manual:'手动平仓'}[v]||'其他退出');
  const time=v=>new Intl.DateTimeFormat('zh-CN',{timeZone:'Asia/Shanghai',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hourCycle:'h23'}).format(v);
  function validate(d,track,days){
    if(d?.paper_only!==true||d.track!==({v3:'1060',v3add:'2060',spike:'3060'}[track]||track)||d.days!==days||!finite(d.now_ms)||!d.summary||!d.coverage||!['curve','by_side','by_symbol','by_reason'].every(k=>Array.isArray(d[k]))||!d.curve.every(p=>finite(p.time_ms)&&finite(p.equity)))throw Error('统计返回格式不完整');
    return d;
  }
  const pf=s=>s.profit_factor_state==='no_losses'?'∞':num(s.profit_factor);
  function metricsHTML(s){
    const cell=(label,value,hint,cls='')=>`<div class="rp-analysis-metric"><small>${label}</small><strong class="${cls}">${value}</strong><span>${hint}</span></div>`;
    return cell('已实现净盈亏',signed(s.realized_pnl),'USDT · 含分批减仓',tone(s.realized_pnl))+
      cell('参考浮动盈亏',signed(s.unrealized_pnl),s.missing_quotes?`${s.missing_quotes} 笔行情待更新`:'USDT · 当前余仓，未扣费',tone(s.unrealized_pnl))+
      cell('已结算手续费',num(s.settled_fees),'USDT · 按已平份额分摊')+
      cell('完全平仓',num(s.closed_count,0)+' 笔',`${s.wins} 胜 / ${s.losses} 负 / ${s.breakeven} 净零`)+
      cell('交易胜率',rate(s.win_rate),'按整笔最终盈亏')+
      cell('盈利因子',pf(s),s.profit_factor_state==='no_losses'?'已有盈利，尚无亏损':'整笔净盈利 ÷ 净亏损绝对值')+
      cell('最大已实现回撤',num(s.max_drawdown_pct)+'%',num(s.max_drawdown)+' USDT')+
      cell('当前名义敞口',num(s.open_notional),`${s.open_count} 笔余仓 · USDT`);
  }
  function breakdownHTML(d){
    const section=(name,headers,rows,empty)=>`<section class="rp-analysis-section"><h3>${name}</h3>${rows.length?`<table><thead><tr>${headers.map(v=>`<th>${v}</th>`).join('')}</tr></thead><tbody>${rows.join('')}</tbody></table>`:`<p class="rp-analysis-empty">${empty}</p>`}</section>`;
    const tradeRow=(name,r)=>`<tr><th scope="row">${name}</th><td>${r.closed_count} / ${rate(r.win_rate)}</td><td class="${tone(r.realized_pnl)}">${signed(r.realized_pnl)}</td></tr>`;
    return section('多空表现',['方向','结清 / 胜率','已实现 USDT'],d.by_side.map(r=>tradeRow(r.side===1?'做多':'做空',r)),'暂无交易')+
      section('退出原因',['原因','成交批次','已实现 USDT'],d.by_reason.map(r=>`<tr><th scope="row">${reason(r.reason)}</th><td>${r.exit_count}</td><td class="${tone(r.realized_pnl)}">${signed(r.realized_pnl)}</td></tr>`),'所选时间内尚无平仓成交')+
      section('币种表现 · 按盈亏影响排序',['币种','结清 / 胜率','已实现 USDT'],d.by_symbol.map(r=>tradeRow(esc(r.symbol.replace('-USDT-SWAP','')),r)),'所选时间内尚无已实现盈亏');
  }
  function chartOptions(d,dark=false){const muted=dark?'#9aaabc':'#748493',line=dark?'#ffffff1a':'#cbd9e4',grid=dark?'#ffffff12':'#a1b5c322',accent=dark?'#5aa9ff':'#0071e3';return {animation:false,grid:{left:58,right:12,top:18,bottom:32},tooltip:{trigger:'axis',confine:true,backgroundColor:dark?'#1c222c':'#ffffff',borderColor:line,textStyle:{color:dark?'#e8edf4':'#182b39'},formatter:rows=>{const p=d.curve[rows[0]?.dataIndex];return p?`${time(p.time_ms)} 北京<br>已实现净值 ${num(p.equity)} USDT<br>区间净盈亏 ${signed(p.pnl)} USDT`:'';}},xAxis:{type:'time',axisLabel:{fontSize:10,color:muted,hideOverlap:true,formatter:v=>d.now_ms-d.start_ms<172800000?time(v).slice(6):time(v).slice(0,5)},axisLine:{lineStyle:{color:line}},axisTick:{show:false},splitLine:{show:false}},yAxis:{type:'value',scale:true,axisLabel:{fontSize:10,color:muted,formatter:v=>num(v)},splitNumber:3,splitLine:{lineStyle:{color:grid,type:'dashed'}}},series:[{type:'line',step:'end',showSymbol:false,data:d.curve.map(p=>[p.time_ms,p.equity]),lineStyle:{width:2,color:accent},itemStyle:{color:accent},areaStyle:{color:accent,opacity:.055}}]};}
  const api={validate,metricsHTML,breakdownHTML,chartOptions,pf};
  if(typeof module!=='undefined'&&module.exports){module.exports=api;return;}
  function create({root,createFeed,onOpen,onTrack}){
    const trigger=root.querySelector('[data-rp-action="analytics"]'),panel=document.createElement('section');
    panel.id='rp-analysis-panel';panel.className='rp-analysis rp-glass';panel.setAttribute('aria-label','模拟盘统计分析');
    panel.innerHTML=`<header class="rp-record-heading" data-yd-sheet-drag><div><strong data-analysis-title>统计分析</strong><small>仅计入资金的模拟交易</small></div><button data-analysis-action="close" aria-label="收起统计分析">×</button></header><div class="rp-analysis-filters"><label class="rp-track-picker"><span>账户</span><select data-analysis-track aria-label="统计模拟账户">${['60','5','v3','v3add','spike'].map(t=>`<option value="${t}">${t==='60'?'60m':t==='5'?'5m':t}</option>`).join('')}</select></label><div class="rp-tabs" role="group" aria-label="统计时段">${[[7,'近 7 天'],[30,'近 30 天'],[0,'全部']].map(([n,l])=>`<button data-analysis-days="${n}" aria-pressed="false">${l}</button>`).join('')}</div></div><p class="rp-analysis-status" role="status"></p><div class="rp-analysis-body"><div class="rp-analysis-warning" role="alert" hidden></div><div class="rp-analysis-grid"></div><section class="rp-analysis-section"><h3>已实现资金曲线 <small>USDT</small></h3><div class="rp-equity-chart" role="img" aria-label="已实现资金曲线"></div><p class="rp-curve-note"></p></section><div class="rp-analysis-breakdowns"></div><p class="rp-analysis-note">盈亏与手续费按每次平仓的成交时间计入，包含部分平仓；胜率与盈利因子按整笔最终结清时间统计。资金曲线不含浮动盈亏。手续费包含已平份额对应的开仓费与平仓费，未平份额不计入已结算费用。影子记录和待成交加仓不参与统计。v3add 的主单与已成交加仓分别计笔，共属一个账户。</p></div><footer class="rp-analysis-footer"><span>完整账本 · 北京时间</span><button data-analysis-action="refresh">刷新统计</button></footer>`;
    root.append(panel);
    let track='60',days=0,active=false,open=false,feed=null,chart=null,chartLoading=false,chartError='',signature='',lastChartData=null;
    const requests=window.SpikeRuntime.createRequests(),el=s=>panel.querySelector(s);
    const sheet=window.SpikeYoloPreviewSheet.bind({panel,isOpen:()=>open,dismiss:()=>setOpen(false)});
    function stopChart(){chart?.dispose();chart=null;lastChartData=null;}
    function newFeed(){
      feed?.setActive(false);requests.cancel();signature='';stopChart();
      const currentTrack=track,currentDays=days;
      feed=createFeed({read:()=>requests.json(`/api/monday-reclaim-paper/analytics?track=${currentTrack}&days=${currentDays}`),validateData:d=>validate(d,currentTrack,currentDays),cancel:()=>requests.cancel(),change:render});
      feed.setActive(active&&open);render();
    }
    function setOpen(value,focus=true){
      sheet.reset();open=value;panel.classList.toggle('is-open',open);panel.inert=!open;panel.setAttribute('aria-hidden',String(!open));trigger.setAttribute('aria-expanded',String(open));
      if(value){onOpen?.();feed.setActive(active);render();el('[data-analysis-action="close"]').focus({preventScroll:true});}else{feed.setActive(false);stopChart();if(focus)trigger.focus({preventScroll:true});}
    }
    async function paint(d){
      if(!active||!open)return;
      if(!window.echarts){if(chartLoading||chartError)return;chartLoading=true;try{await window.SpikeModules.ensure('signal-charts');chartError='';}catch{chartError='曲线暂未加载，点击刷新统计重试';}finally{chartLoading=false;}if(active&&open)render();return;}
      if(lastChartData===d&&chart)return;
      chart??=window.echarts.init(el('.rp-equity-chart'),null,{renderer:'canvas'});chart.setOption(chartOptions(d,document.documentElement.dataset.theme==='dark'),true);lastChartData=d;
    }
    function render(){
      if(!open)return;
      el('[data-analysis-title]').textContent='统计分析 · '+({60:'60m',5:'5m'}[track]||track);
      const {data,error,busy}=feed?.getState()||{};
      el('[data-analysis-track]').value=track;
      panel.querySelectorAll('[data-analysis-days]').forEach(b=>b.setAttribute('aria-pressed',String(Number(b.dataset.analysisDays)===days)));
      el('.rp-analysis-status').textContent=error?`${error}${data?' · 保留上次统计':''}`:data?`${time(data.start_ms)} 至 ${time(data.now_ms)} · ${busy?'同步中':'每 10 秒同步'}`:'正在读取完整账本…';
      el('[data-analysis-action="refresh"]').disabled=busy;
      el('.rp-analysis-body').hidden=!data;
      if(!data)return;
      const s=data.summary,c=data.coverage,key=JSON.stringify([s,c,data.by_side,data.by_symbol,data.by_reason]);
      if(signature!==key){el('.rp-analysis-grid').innerHTML=metricsHTML(s);el('.rp-analysis-breakdowns').innerHTML=breakdownHTML(data);signature=key;}
      const issues=[c.invalid_events&&`${c.invalid_events} 条成交字段不全`,c.missing_trade_pnl&&`${c.missing_trade_pnl} 笔交易缺少盈亏`,c.missing_fees&&`${c.missing_fees} 批手续费缺失`,Math.abs(c.ledger_difference)>.005&&'账本与账户净值尚未对齐'].filter(Boolean);
      el('.rp-analysis-warning').hidden=!issues.length;el('.rp-analysis-warning').textContent=issues.length?issues.join(' · ')+'；当前统计仅依据可核对记录。':'';
      el('.rp-curve-note').textContent=chartError||(!s.exit_count?'所选时间内尚无平仓成交，资金曲线保持不变。':`已实现 ${signed(s.realized_pnl)} USDT · ${s.exit_count} 批平仓${data.curve_points>data.curve.length?' · 曲线按局部极值压缩，指标使用完整账本':''}`);
      el('.rp-equity-chart').setAttribute('aria-label',`已实现资金曲线，从 ${num(s.period_start_equity)} 到 ${num(data.curve.at(-1)?.equity)} USDT`);
      paint(data);
    }
    panel.addEventListener('click',e=>{const b=e.target.closest('button');if(!b)return;if(b.dataset.analysisDays!==undefined){const n=Number(b.dataset.analysisDays);if(n!==days){days=n;newFeed();}}if(b.dataset.analysisAction==='close')setOpen(false);if(b.dataset.analysisAction==='refresh'){chartError='';feed.refresh();}});
    el('[data-analysis-track]').addEventListener('change',e=>onTrack(e.target.value));
    panel.addEventListener('keydown',e=>{if(e.key==='Escape'&&open){e.stopPropagation();setOpen(false);}});
    new ResizeObserver(()=>chart?.resize()).observe(el('.rp-equity-chart'));
    new MutationObserver(()=>{lastChartData=null;if(active&&open)render();}).observe(document.documentElement,{attributes:true,attributeFilter:['data-theme']});
    newFeed();setOpen(false,false);
    return {setOpen,toggle:()=>setOpen(!open),setTrack(value){if(value!==track){track=value;newFeed();}},refresh:()=>feed.refresh(),setActive(value){active=value;if(!value){sheet.reset();stopChart();}feed.setActive(value&&open);if(value)render();}};
  }
  window.SpikeReclaimAnalytics={...api,create};
})();
