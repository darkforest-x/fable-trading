/* Signal workspace: full-cohort reference charts, independent of account P&L.
 * Apache ECharts 6.1.0 is vendored locally; no third-party runtime requests.
 */
(() => {
  'use strict';
  const finite = value => typeof value === 'number' && Number.isFinite(value);
  const r = value => finite(value) ? `${value > 0 ? '+' : ''}${value.toFixed(2)}R` : '—';
  const dateFormat = new Intl.DateTimeFormat('zh-CN', {timeZone:'Asia/Shanghai',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false});
  const date = value => dateFormat.format(new Date(value));
  function chartOptions(stats, mode, colors, loading=false) {
    const curve = stats?.curve, points = curve?.status === 'ready' ? curve.points : [];
    const drawdown = mode === 'drawdown';
    const empty = !points?.length;
    const common = {animation:false, textStyle:{fontFamily:'Inter, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif',color:colors.muted},
      grid:{left:48,right:22,top:24,bottom:38},
      tooltip:{trigger:'axis',renderMode:'richText',confine:true,backgroundColor:colors.surface,borderColor:colors.line,textStyle:{color:colors.text,fontSize:12}},
      aria:{enabled:true},
      xAxis:{axisLine:{lineStyle:{color:colors.line}},axisTick:{show:false},axisLabel:{color:colors.muted,fontSize:10,hideOverlap:true}},
      yAxis:{type:'value',axisLine:{show:false},axisTick:{show:false},axisLabel:{color:colors.muted,fontSize:10,formatter:value=>`${Number(value.toFixed(1))}R`},splitLine:{lineStyle:{color:colors.line,type:'dashed'}}}};
    const line = {...common, xAxis:{...common.xAxis,type:'time',show:!empty,axisLabel:{...common.xAxis.axisLabel,formatter:value=>date(value).slice(0,5)}},
      yAxis:{...common.yAxis,show:!empty,max:drawdown?0:undefined},
      tooltip:{...common.tooltip,formatter:params=>{
        const p=points[params[0]?.dataIndex];
        return p ? `${date(p.exit_ms)} · 北京时间\n${drawdown?'距峰值回撤':'累计净 R'}  ${r(drawdown ? -p.drawdown_r : p.cumulative_r)}\n同刻退出 ${p.closed} 笔 · ${r(p.net_r)}` : '';
      }},
      graphic:empty ? [{type:'text',left:'center',top:'middle',style:{text:loading?'正在读取信号表现…':curve?.status==='incomplete'?'退出 R 或时间缺失，无法绘制完整曲线':'当前筛选暂无已结束记录',fill:colors.muted,fontSize:12}}] : [],
      series:[{name:drawdown?'累计 R 回撤':'信号累计净 R',type:'line',step:'end',showSymbol:points?.length===1,symbolSize:7,
        data:(points||[]).map(p=>[p.exit_ms,drawdown?-p.drawdown_r:p.cumulative_r]),
        lineStyle:{width:2.5,color:drawdown?colors.negative:colors.positive},itemStyle:{color:drawdown?colors.negative:colors.positive},
        areaStyle:{color:{type:'linear',x:0,y:0,x2:0,y2:1,colorStops:[{offset:0,color:drawdown?colors.negative:colors.positive},{offset:1,color:colors.surface}]},opacity:.16}}]};
    const groups=stats?.by_timeframe || [];
    const bars={...common,graphic:loading?[{type:'text',left:'center',top:'middle',style:{text:'正在读取周期贡献…',fill:colors.muted,fontSize:12}}]:[],grid:{left:48,right:22,top:24,bottom:38},
      xAxis:{...common.xAxis,type:'category',data:groups.map(g=>g.label)},
      yAxis:{...common.yAxis},
      tooltip:{...common.tooltip,formatter:params=>{const g=groups[params[0]?.dataIndex];return g?`${g.label} · ${g.total} 笔结束\n净 R  ${r(g.net_r)}\n胜率  ${finite(g.win_rate)?(g.win_rate*100).toFixed(1)+'%':'—'}`:'';}},
      series:[{name:'周期净 R',type:'bar',barMaxWidth:38,data:groups.map(g=>({value:g.measured>0&&finite(g.net_r)?g.net_r:null,
        itemStyle:{color:g.net_r<0?colors.negative:colors.positive,borderRadius:g.net_r<0?[0,0,5,5]:[5,5,0,0]}})),
        label:{show:true,position:'outside',fontSize:10,color:colors.text,formatter:p=>finite(p.value)?r(p.value):'—'}}]};
    return {line,bars};
  }
  function activeFilters(values={}){
    const filters=[];
    if(values.q?.trim())filters.push(values.q.trim().toUpperCase());
    if(values.timeframe&&values.timeframe!=='all')filters.push({'15':'15m','30':'30m','60':'1H','240':'4H'}[values.timeframe]||values.timeframe);
    if(values.period&&values.period!=='all')filters.push(values.period==='today'?'今天':'本周');
    if(values.side&&values.side!=='all')filters.push(values.side==='long'?'多头':'空头');
    if(values.outcome&&values.outcome!=='all')filters.push({active:'参考运行中',profit:'盈利退出',loss:'亏损退出',breakeven:'保本退出',unknown:'等待 / 未知'}[values.outcome]||values.outcome);
    return filters;
  }
  if (typeof module !== 'undefined' && module.exports) {module.exports={chartOptions,activeFilters};return;}
  const root=document.getElementById('signals-view');
  if (!root) return;
  const context=document.getElementById('stitch-signal-context');
  let active=false, pane='records', mode='cumulative', ledger=null, layout='cards', frame=null;
  let lineChart=null, barChart=null, chartLoad=null, chartSignature=null;
  const resizeObserver=new ResizeObserver(schedule), themeObserver=new MutationObserver(schedule);
  const chartSizes=new WeakMap();let observing=null;
  const release=()=>{lineChart?.dispose();barChart?.dispose();lineChart=barChart=null;chartSignature=null;};
  const idle=window.SpikeRuntime?.idleRelease(release);
  function visible(){return active && !document.hidden && pane==='overview' && layout==='history';}
  function ownCharts(){
    const showing=visible();if(observing===showing)return;observing=showing;
    resizeObserver.disconnect();themeObserver.disconnect();
    if(visible()){
      resizeObserver.observe(document.getElementById('signal-curve-chart'));
      resizeObserver.observe(document.getElementById('signal-timeframe-chart'));
      themeObserver.observe(document.documentElement,{attributes:true,attributeFilter:['data-theme']});
    }else if(frame){cancelAnimationFrame(frame);frame=null;}
    idle?.setActive(visible());
  }
  function schedule(){if(!visible()||frame)return;frame=requestAnimationFrame(()=>{frame=null;draw();});}
  function resize(chart,node){const size=`${node.clientWidth}:${node.clientHeight}`;if(chartSizes.get(chart)!==size){chart.resize();chartSizes.set(chart,size);}}
  function draw(){
    const visuals=document.getElementById('signal-visuals');
    visuals.hidden=layout!=='history';
    if(!visible() || !root.getBoundingClientRect().width) return;
    if(!window.echarts){
      document.getElementById('signal-curve-caption').textContent='正在加载图表…';
      if(!chartLoad && window.SpikeModules) chartLoad=window.SpikeModules.ensure('signal-charts')
        .then(schedule).catch(()=>{document.getElementById('signal-curve-caption').textContent='图表未能加载，请切换页签或刷新重试。';})
        .finally(()=>{chartLoad=null;});
      return;
    }
    const styles=getComputedStyle(root), token=name=>styles.getPropertyValue(name).trim();
    const colors={surface:token('--surface'),line:token('--line-soft'),text:token('--text'),muted:token('--muted'),positive:token('--positive-text')||token('--mint'),negative:token('--negative-text')||token('--red')};
    const curveNode=document.getElementById('signal-curve-chart'),barNode=document.getElementById('signal-timeframe-chart');
    lineChart ||= window.echarts.init(curveNode,null,{renderer:'svg'});
    barChart ||= window.echarts.init(barNode,null,{renderer:'svg'});
    const stats=ledger?.closed_statistics;
    resize(lineChart,curveNode);resize(barChart,barNode);
    const signature=JSON.stringify([stats,mode,colors,!ledger]);
    if(signature!==chartSignature){const options=chartOptions(stats,mode,colors,!ledger);lineChart.setOption(options.line,true);barChart.setOption(options.bars,true);chartSignature=signature;}
    curveNode.setAttribute('aria-label',!ledger?'正在读取信号表现':`${mode==='drawdown'?'累计 R 回撤':'信号累计净 R'}，${stats?.total??0} 笔结束；${stats?.curve?.status==='ready'?`净 R ${r(stats.net_r)}，最大回撤 ${r(stats.max_drawdown_r)}`:'暂无完整曲线'}`);
    barNode.setAttribute('aria-label',!ledger?'正在读取周期贡献':`周期贡献：${(stats?.by_timeframe||[]).map(g=>`${g.label} ${g.total} 笔，${r(g.net_r)}`).join('；')}`);
    document.getElementById('signal-curve-caption').textContent=!ledger?'正在读取信号表现…':`信号参考，非账户收益 · ${stats?.total??0} 笔 · 按退出时间（北京）${ledger?.as_of_ms?' · 更新 '+date(ledger.as_of_ms):''}`;
  }
  function selectPane(value){
    if(!['overview','records','account'].includes(value)) return;
    const changed=pane!==value;
    pane=value;root.dataset.signalPane=pane;
    root.querySelectorAll('.signal-workspace-nav [data-signal-pane]').forEach(button=>{const selected=button.dataset.signalPane===pane;button.classList.toggle('selected',selected);button.classList.toggle('active',selected);button.setAttribute('aria-pressed',String(selected));});
    document.getElementById('signal-pane-title').textContent=pane==='overview'?'信号表现':layout==='history'?'已结束的信号':'确认信号';
    document.getElementById('signal-pane-description').textContent=pane==='overview'?'按退出顺序观察累计 R、回撤与周期差异。':layout==='history'?'核对每条信号的参考入场、退出与净 R。':'查看已收盘确认信号、参考表现与形态说明。';
    window.SpikeOKXExecution?.setActive(active && pane==='account');
    if(changed)window.SpikeMotion?.enter(root,{kind:'route'});
    document.getElementById('signal-visuals').hidden=layout!=='history';
    ownCharts();schedule();
  }
  root.addEventListener('click',event=>{
    const paneButton=event.target.closest('[data-signal-pane]');
    if(paneButton) window.SpikeSignals?.selectPane(paneButton.dataset.signalPane);
    const chartButton=event.target.closest('[data-signal-chart]');
    if(chartButton){mode=chartButton.dataset.signalChart;root.querySelectorAll('[data-signal-chart]').forEach(button=>{const selected=button===chartButton;button.classList.toggle('selected',selected);button.setAttribute('aria-pressed',String(selected));});schedule();}
  });
  document.addEventListener('visibilitychange',()=>{ownCharts();schedule();});
  window.SpikeSignalWorkspace={
    setActive(value){active=value;document.body.classList.toggle('signal-workspace-active',value);root.classList.toggle('signal-workspace',value);if(value) selectPane(pane);else{window.SpikeOKXExecution?.setActive(false);ownCharts();}},
    render(data,display,selectedPane='records'){
      ledger=data;layout=display;selectPane(selectedPane);
      const universe=document.getElementById('signal-universe-note'), policy=data?.entry_universe;
      if(universe) universe.textContent=policy ? `${policy.scope}。全周期名单：${policy.excluded_bases.join('、')}；分组名单只限制对应周期和方向的新信号、通知与自动开仓。历史记录保留，已有持仓继续跟踪退出。` : '';
      const stats=data?.stats;
      const count=value=>Number.isFinite(value)?value.toLocaleString('zh-CN'):'—';
      context.innerHTML=`<h3>信号概况</h3><dl><dt>确认信号</dt><dd>${count(stats?.total)}</dd><dt>参考运行中</dt><dd>${count(stats?.active)}</dd><dt>已结束</dt><dd>${count(stats?.closed)}</dd><dt>等待 / 未知</dt><dd>${count(stats?.unknown)}</dd></dl><p>参考信号与账户成交分别记录。模型分数与观察指数不代表胜率。</p><a href="#yolo-detect?tab=results">均线形态与专用账户 →</a><a href="#notifications">通知订阅 →</a>`;
      const summary=document.getElementById('signal-result-summary');
      summary.textContent=stats ? (layout==='history'
        ? `${stats.closed} 条已结束 · 盈利 ${stats.profit} · 亏损 ${stats.loss} · 保本 ${stats.breakeven}`
        : `${stats.total} 条确认 · 参考运行中 ${stats.active} · 已结束 ${stats.closed}${stats.unknown?' · 等待 / 未知 '+stats.unknown:''}`) : '正在读取信号…';
      const hasSearch=!!document.getElementById('symbol-search').value;
      document.getElementById('clear-signal-search').hidden=!hasSearch;
      root.querySelector('.search-field kbd').hidden=hasSearch;
      const filters=activeFilters({q:document.getElementById('symbol-search').value,
        timeframe:root.querySelector('[data-timeframe][aria-pressed=true]')?.dataset.timeframe,
        period:root.querySelector('[data-ledger-period][aria-pressed=true]')?.dataset.ledgerPeriod,
        side:document.getElementById('side-filter').value,
        outcome:layout==='history'?'all':document.getElementById('outcome-filter').value});
      const inspector=root.querySelector('.signal-query-inspector');
      inspector.dataset.inspectorDetail=filters.length?filters.join(' / '):'';
      inspector.dispatchEvent(new Event('spike:inspector-label'));
      const reset=document.getElementById('reset-signal-filters');
      reset.disabled=!filters.length&&document.getElementById('sort-filter').value==='newest';
      reset.textContent=filters.length?`清除筛选 (${filters.length})`:'重置';
      if(filters.length)summary.textContent+=' · 筛选：'+filters.join(' / ');
      schedule();
    }
  };
})();
