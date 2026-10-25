/* Real closed candles with display-only long/short risk rectangles. */
(() => {
  'use strict';
  const green='#01e599',red='#ff4f73';
  function levels(d){
    const entry=Number(d?.entry_price??d?.price),stop=Number(d?.stop??d?.initial_stop),target=Number(d?.take_profit);
    if(![entry,stop,target].every(v=>Number.isFinite(v)&&v>0)||!['long','short'].includes(d?.side))return null;
    // Invalid or crossed prices must never become a plausible-looking trade.
    if(d.side==='long'?!(stop<entry&&entry<target):!(target<entry&&entry<stop))return null;
    return {entry,stop,target,side:d.side,rr:d.rr};
  }
  function validCandles(data,symbol){
    if(data?.symbol!==symbol||data.timeframe!=='5m'||!Array.isArray(data.candles)||!data.candles.length)throw Error('K 线数据不完整');
    const rows=data.candles;
    rows.forEach((r,i)=>{
      if(!Number.isInteger(r.time)||r.time%300||!['open','high','low','close'].every(k=>Number.isFinite(r[k])&&r[k]>0)
        ||r.low>Math.min(r.open,r.close)||r.high<Math.max(r.open,r.close)||i&&r.time-rows[i-1].time!==300)throw Error('K 线数据不完整');
    });
    if((rows.at(-1).time+300)*1000!==data.cutoff_ms)throw Error('K 线收盘时间不一致');
    return rows;
  }
  function stopBar(d,rows){
    if(d?.stop_source!=='latest_closed_5m')return null;
    const c=d.stop_candle;
    if(!c||!Number.isInteger(c.open_ms)||c.close_ms-c.open_ms!==300000)return {error:'止损来源时间不完整'};
    const row=rows.find(r=>r.time*1000===c.open_ms);
    if(!row)return {error:'止损来源 K 线尚未载入，请重试图表'};
    if(row.high!==Number(c.high)||row.low!==Number(c.low))return {error:'图表与止损来源高低点不一致，请重新预览'};
    return {time:row.time,high:row.high,low:row.low,open_ms:c.open_ms,close_ms:c.close_ms,
      position:d.side==='short'?'aboveBar':'belowBar',shape:d.side==='short'?'arrowDown':'arrowUp',
      text:d.side==='short'?'止损取高点':'止损取低点'};
  }
  const maColors={sma20:'#c0c1ff',sma60:'#00e599',sma120:'#cbd5e1',ema20:'#9d7cff',ema60:'#f59e0b',ema120:'#ff3366'};
  function maBoundary(d,rows){
    if(d?.stop_source!=='ma_envelope_5m')return null;
    const c=d.stop_candle,row=rows.find(r=>r.time*1000===c?.open_ms);
    if(!row)return {error:'均线取值 K 线未载入'};
    if(!Object.keys(maColors).every(k=>Number.isFinite(row[k])&&Number.isFinite(Number(c.ma?.[k]))&&Math.abs(row[k]-Number(c.ma[k]))<=Number(c.tick)*.1))return {error:'图表与报价均线值未对齐，请重新预览'};
    return {time:row.time,key:c.boundary_key,price:Number(c.boundary_price)};
  }
  class CandleRead {
    constructor(fetcher){this.fetcher=fetcher;this.version=0;}
    clear(){this.version++;this.controller?.abort();this.controller=null;this.symbol=null;this.promise=null;}
    select(symbol){
      if(this.symbol===symbol&&this.promise)return this.promise;
      this.clear();this.symbol=symbol;this.controller=new AbortController();const v=this.version;
      this.promise=this.fetcher(symbol,this.controller.signal).then(data=>{
        if(v!==this.version)return null;
        validCandles(data,symbol);return data;
      }).catch(e=>{if(v!==this.version)return null;throw e;});
      // Selection starts while the private preview is still loading.
      this.promise.catch(()=>{});return this.promise;
    }
  }
  if(typeof module!=='undefined')module.exports={levels,validCandles,CandleRead,stopBar,maBoundary};
  if(typeof window==='undefined')return;
  const chartTools=window.SpikeChartTools;
  const library=()=>chartTools.library();
  const time=t=>new Intl.DateTimeFormat('zh-CN',{timeZone:'Asia/Shanghai',hour:'2-digit',minute:'2-digit',hour12:false}).format(new Date(t*1000));
  class PositionBox {
    constructor(values,last){this.values=values;this.last=last;this.view={zOrder:()=> 'bottom',renderer:()=>({draw:target=>target.useMediaCoordinateSpace(({context:ctx})=>this.draw(ctx))})};}
    attached({chart,series}){this.chart=chart;this.series=series;}
    paneViews(){return [this.view];}
    draw(ctx){
      const scale=this.chart.timeScale(),x=scale.logicalToCoordinate(this.last+1),right=scale.logicalToCoordinate(this.last+12);
      const v=this.values,entry=this.series.priceToCoordinate(v.entry);
      if(x==null||right==null||entry==null)return;
      for(const [price,color,label] of [[v.target,green,`盈利区 · ${v.rr}R`],[v.stop,red,'亏损区 · 1R']]){
        const y=this.series.priceToCoordinate(price);if(y==null)continue;
        ctx.save();ctx.fillStyle=color+'25';ctx.strokeStyle=color;ctx.lineWidth=1;
        ctx.fillRect(x,Math.min(y,entry),right-x,Math.abs(y-entry));ctx.strokeRect(x,Math.min(y,entry),right-x,Math.abs(y-entry));
        if(right-x>90&&Math.abs(y-entry)>20){ctx.fillStyle=color;ctx.font='11px system-ui';ctx.textAlign='center';ctx.fillText(label,(x+right)/2,(y+entry)/2+4);}
        ctx.restore();
      }
    }
  }
  class TicketChart {
    constructor(){
      this.version=0;
      this.read=new CandleRead(async(symbol,signal)=>{
        const controller=new AbortController(),abort=()=>controller.abort();signal.addEventListener('abort',abort,{once:true});
        if(signal.aborted)abort();const timeout=setTimeout(abort,20000);
        try{
          const r=await fetch('/api/manual-live/chart?'+new URLSearchParams({symbol}),{cache:'no-store',signal:controller.signal});
          if(!r.ok)throw Error('unavailable');return await r.json();
        }catch{throw Error('K 线暂未加载，可重试；订单金额以本次报价为准。');}
        finally{clearTimeout(timeout);signal.removeEventListener('abort',abort);}
      });
    }
    select(symbol){return this.read.select(symbol);}
    unmount(){this.version++;this.controls?.destroy();this.controls=null;this.crosshair?.cancel();this.resize?.disconnect();this.resize=null;this.theme?.disconnect();this.theme=null;this.chart?.remove();this.chart=null;}
    close(){this.unmount();this.read.clear();}
    mount(host,draft,expired=false){
      this.unmount();if(!host)return;
      const v=this.version,values=levels(draft);
      host.innerHTML=`<header><div><strong>开仓位置</strong><span>5 分钟 K 线 · 六均线 · 已收盘</span></div><div class="ml-chart-actions">${chartTools.toolbar({expand:true})}<button type="button" class="ml-chart-reset">还原视图</button></div></header><div class="cv-readout" data-position-readout></div><div class="chart-ma-legend" data-position-ma></div><div class="ml-chart-canvas cv-canvas"></div><p class="ml-chart-caption" role="status">正在读取 K 线…</p><div class="ml-chart-legend"><span class="profit">绿色 · 止盈区</span><span class="loss">红色 · 止损区</span><span>SMA / EMA 20 · 60 · 120</span><span>框为订单价位，非未来行情</span></div><a class="ml-chart-credit" href="https://www.tradingview.com/" target="_blank" rel="noopener">TradingView Lightweight Charts™</a>`;
      const caption=host.querySelector('.ml-chart-caption'),reset=host.querySelector('.ml-chart-reset');reset.disabled=true;
      const current=()=>v===this.version&&host.isConnected;
      const load=()=>Promise.all([this.select(draft.symbol),library()]).then(([data])=>{
        if(!current()||!data)return;
        const rows=data.candles,canvas=host.querySelector('.ml-chart-canvas');
        const style=getComputedStyle(host),palette=()=>{const s=getComputedStyle(host);return {layout:{background:{color:s.getPropertyValue('--surface').trim()},textColor:s.getPropertyValue('--muted').trim(),fontSize:11},grid:{vertLines:{color:s.getPropertyValue('--line').trim()},horzLines:{color:s.getPropertyValue('--line').trim()}}};};
        const chart=this.chart=window.LightweightCharts.createChart(canvas,{...palette(),...chartTools.interaction,width:canvas.clientWidth,height:canvas.clientHeight,
          rightPriceScale:{borderVisible:false,scaleMargins:{top:.12,bottom:.12}},timeScale:{borderVisible:false,timeVisible:true,secondsVisible:false,tickMarkFormatter:time},
          localization:{locale:'zh-CN',priceFormatter:chartTools.axisPrice,timeFormatter:time},handleScroll:{vertTouchDrag:false,horzTouchDrag:true},crosshair:{mode:0}});
        const precision=Math.min(10,Math.max(2,Math.ceil(-Math.log10(Math.min(...rows.map(r=>r.close))))+4,...[draft.price,draft.initial_stop,draft.take_profit].map(p=>(String(p).split('.')[1]||'').length)));
        this.controls=chartTools.bind(host,{chart:()=>this.chart,rows:()=>rows,panel:host,canvas});
        const series=chart.addCandlestickSeries({upColor:chartTools.colors.up,downColor:chartTools.colors.down,wickUpColor:chartTools.colors.up,wickDownColor:chartTools.colors.down,borderVisible:false,priceLineVisible:false,lastValueVisible:false,
          priceFormat:{type:'price',precision,minMove:10**-precision},autoscaleInfoProvider:original=>{const a=original();if(!values)return a;return {priceRange:{minValue:Math.min(a?.priceRange.minValue??values.stop,values.stop,values.target),maxValue:Math.max(a?.priceRange.maxValue??values.target,values.stop,values.target)}};}});
        series.setData(rows);
        const boundary=maBoundary(draft,rows);
        for(const [key,color] of Object.entries(maColors)){
          const points=rows.filter(r=>Number.isFinite(r[key])).map(r=>({time:r.time,value:r[key]}));
          if(!points.length)continue;
          const ma=chart.addLineSeries({color,lineWidth:1,priceLineVisible:false,lastValueVisible:false,crosshairMarkerVisible:false,priceFormat:{type:'price',precision,minMove:10**-precision}});ma.setData(points);
          if(boundary?.key===key&&!boundary.error)ma.setMarkers([{time:boundary.time,position:draft.side==='short'?'aboveBar':'belowBar',shape:'circle',color:red,text:'止损外侧边界 '+key.toUpperCase()}]);
        }
        if(values)series.attachPrimitive(new PositionBox(values,rows.length-1));
        const source=stopBar(draft,rows);
        if(source&&!source.error)series.setMarkers([{time:source.time,position:source.position,shape:source.shape,color:red,text:source.text}]);
        const linePrices=[[Number(draft.entry_price??draft.price),style.getPropertyValue('--text').trim(),draft.entry_price?'成交':draft.display_only?'参考':'入场'],[Number(draft.stop??draft.initial_stop),red,'止损'],[Number(draft.take_profit),green,`止盈 ${draft.rr}R`]];
        for(const [p,color,title] of linePrices.filter(([p])=>Number.isFinite(p)&&p>0))series.createPriceLine({price:p,color,lineWidth:1,lineStyle:2,axisLabelVisible:true,title});
        const resetView=()=>{chart.priceScale('right').applyOptions({autoScale:true});chart.timeScale().setVisibleLogicalRange({from:Math.max(0,rows.length-40),to:rows.length+20});};resetView();reset.disabled=false;reset.onclick=resetView;this.controls.update();
        const maOrigin=boundary?(boundary.error?' · '+boundary.error:` · ${boundary.key.toUpperCase()} ${Number(boundary.price).toLocaleString('en-US',{maximumFractionDigits:8})} · 外侧 1 tick`):'';
        const origin=maOrigin+(source?(source.error?' · '+source.error:` · 取值 ${time(source.open_ms/1000)}–${time(source.close_ms/1000)} · 高 ${source.high} / 低 ${source.low}`):'');
        const status=`${values?(values.side==='long'?'多头':'空头')+(draft.display_only?'参考框':'盈亏框'):'行情图'} · K 线至 ${time(data.cutoff_ms/1000)} 北京时间${origin}${expired?' · 点位已过期':''}`;
        caption.textContent=status;
        const lookup=new Map(rows.map(r=>[r.time,r]));
        this.crosshair=chartTools.frame(t=>{if(!current())return;const row=lookup.get(t)||rows.at(-1);host.querySelector('[data-position-readout]').innerHTML=chartTools.readout(row,row.time,!lookup.has(t));host.querySelector('[data-position-ma]').innerHTML=window.SpikeChartStyle.legend(row);});
        chart.subscribeCrosshairMove(p=>this.crosshair(p.seriesData.get(series)?p.time:null));this.crosshair(null);
        this.resize=chartTools.resize(chart,canvas);
        this.theme=new MutationObserver(()=>{if(current())chart.applyOptions(palette());});this.theme.observe(document.documentElement,{attributes:true,attributeFilter:['data-theme']});
      }).catch(e=>{
        if(!current())return;caption.textContent=e.message;reset.textContent='重试图表';reset.disabled=false;
        reset.onclick=()=>{this.read.clear();this.mount(host,draft,expired);};
      });
      load();
    }
  }
  window.SpikeTicketChart=TicketChart;
})();
