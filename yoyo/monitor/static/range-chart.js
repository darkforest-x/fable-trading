/* Shared closed-candle renderer. Calendar/profile calculations stay in the API. */
(() => {
  'use strict';
  const esc=x=>String(x??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const fin=x=>typeof x==='number'&&Number.isFinite(x);
  const price=x=>fin(x)?x.toLocaleString('zh-CN',{useGrouping:false,maximumFractionDigits:Math.abs(x)>=100?2:Math.abs(x)>=1?4:8}):'—';
  const clock=(ms,year=false)=>ms==null?'—':new Intl.DateTimeFormat('zh-CN',{timeZone:'Asia/Shanghai',...(year?{year:'numeric'}:{}),month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false}).format(new Date(ms));
  function render(c, chartView){
    const bars=(c.candles||[]).slice(-chartView.bars);if(!bars.length)return '';
    const levels=(c.levels||[]).filter(l=>chartView[l.kind]&&fin(l.price));
    const w=1160,h=chartView.height||420,left=12,right=170,top=18,bottom=55,pw=w-left-right,ph=h-top-bottom;
    let low=Math.min(...bars.map(b=>b.low),...levels.map(l=>l.price)),high=Math.max(...bars.map(b=>b.high),...levels.map(l=>l.price));
    const pad=(high-low||high*.01)*.06;low-=pad;high+=pad;
    const y=p=>top+(high-p)/(high-low)*ph,x=i=>left+(i+.5)*pw/bars.length;
    const f=price;
    const color={range:'#3984b3',fib:'#9774c1',profile:'#cc8d35',npoc:'#b9689e',prior:'#8a969e'};
    const labels=levels.map(l=>({...l,y:y(l.price)})).sort((a,b)=>a.y-b.y);let prev=top-16;
    for(const l of labels){l.ly=Math.max(l.y,prev+16);prev=l.ly;}
    if(prev>top+ph){let next=top+ph+16;for(const l of [...labels].reverse()){l.ly=Math.min(l.ly,next-16);next=l.ly;}}
    const maxVol=Math.max(...bars.map(b=>b.volume),1);
    return `<svg class="ms-candle-chart" viewBox="0 0 ${w} ${h}" role="img" aria-label="OKX ${esc((c.symbol||'').replace('-USDT-SWAP','USDT.P'))} 15分钟K线与区间关键价位"><rect width="${w}" height="${h}" fill="var(--surface,#fff)"/>${Array.from({length:5},(_,i)=>{const p=low+(high-low)*i/4;return `<line x1="${left}" x2="${w-right}" y1="${y(p)}" y2="${y(p)}" stroke="var(--line-soft,#e8eeee)"/><text x="${left+4}" y="${y(p)-4}" fill="var(--muted,#71818a)" font-size="10">${f(p)}</text>`;}).join('')}${bars.map((b,i)=>{const tone=b.close>=b.open?'#258571':'#d06472',bh=Math.max(1,Math.abs(y(b.open)-y(b.close))),bw=Math.max(.6,pw/bars.length*.65);return `<g><title>${clock(b.time,true)} 北京 · 开 ${f(b.open)} 高 ${f(b.high)} 低 ${f(b.low)} 收 ${f(b.close)}</title><rect x="${x(i)-bw/2}" y="${h-22-b.volume/maxVol*24}" width="${bw}" height="${b.volume/maxVol*24}" fill="${tone}" opacity=".25"/><line x1="${x(i)}" x2="${x(i)}" y1="${y(b.high)}" y2="${y(b.low)}" stroke="${tone}" stroke-width=".8"/><rect x="${x(i)-bw/2}" y="${Math.min(y(b.open),y(b.close))}" width="${bw}" height="${bh}" fill="${tone}"/></g>`;}).join('')}${labels.map(l=>`<g><line x1="${left}" x2="${w-right+4}" y1="${l.y}" y2="${l.y}" stroke="${color[l.kind]}" stroke-dasharray="${l.kind==='range'?'0':'5 4'}" opacity=".75"/><path d="M${w-right+4},${l.y}L${w-right+14},${l.ly}" fill="none" stroke="${color[l.kind]}"/><text x="${w-right+18}" y="${l.ly+3}" font-size="10" fill="${color[l.kind]}">${esc(l.label)} ${f(l.price)}</text></g>`).join('')}${[0,.25,.5,.75,1].map(r=>{const i=Math.min(bars.length-1,Math.floor(r*(bars.length-1)));return `<text x="${x(i)}" y="${h-5}" font-size="10" text-anchor="${r===0?'start':r===1?'end':'middle'}" fill="var(--muted,#71818a)">${clock(bars[i].time)}</text>`;}).join('')}</svg>`;
  }
  // Stable interactive instance: polling updates series without replacing the canvas.
  class Interactive {
    constructor(host){this.host=host;this.rows=[];this.dead=false;this.lines=[];this.key='';this.sync=window.SpikeChartTools.syncer();}
    async update(data,view,error=''){
      this.pending={data,view:{...view},error};
      if(!this.chart){
        if(this.loading)return;
        this.loading=true;
        try{await window.SpikeChartTools.library();if(this.dead)return;this.create();}
        catch(e){if(!this.dead)this.host.innerHTML=`<p role="status">${esc(e.message)} · 稍后自动重试</p>`;return;}
        finally{this.loading=false;}
      }
      if(this.dead)return;
      const current=this.pending;this.draw(current.data,current.view,current.error);
    }
    create(){
      const T=window.SpikeChartTools;
      this.host.innerHTML=`<section class="cv-range"><div class="cv-range-header"><strong data-range-heading>已收盘 K 线 · 15min</strong>${T.toolbar({expand:true})}</div><p class="cv-range-status" role="status"></p><div class="cv-readout"></div><div class="cv-canvas"></div><details class="cv-level-details"><summary>关键价位 · 完整价格</summary><div class="cv-levels" aria-label="关键价位"></div></details></section>`;
      this.panel=this.host.firstElementChild;this.canvas=this.panel.querySelector('.cv-canvas');
      this.chart=window.LightweightCharts.createChart(this.canvas,{...T.interaction,width:this.canvas.clientWidth||600,height:this.canvas.clientHeight||420,
        rightPriceScale:{scaleMargins:{top:.08,bottom:.2}},localization:{locale:'zh-CN',priceFormatter:T.axisPrice,timeFormatter:T.clock},
        timeScale:{timeVisible:true,secondsVisible:false,rightOffset:5,tickMarkFormatter:T.clock}});
      this.series=this.chart.addCandlestickSeries({upColor:T.colors.up,downColor:T.colors.down,wickUpColor:T.colors.up,wickDownColor:T.colors.down,borderVisible:false,autoscaleInfoProvider:original=>{const value=original();if(!value)return null;const prices=this.activeLevels?.map(l=>l.price)||[];return {...value,priceRange:{minValue:Math.min(value.priceRange.minValue,...prices),maxValue:Math.max(value.priceRange.maxValue,...prices)}};}});
      this.volume=this.chart.addHistogramSeries({priceScaleId:'volume',priceFormat:{type:'volume'},lastValueVisible:false,priceLineVisible:false});
      this.volume.priceScale().applyOptions({scaleMargins:{top:.85,bottom:0}});
      this.controls=T.bind(this.panel,{chart:()=>this.chart,rows:()=>this.rows,canvas:this.canvas,panel:this.panel});
      this.crosshair=T.frame(p=>{if(!this.chart)return;this.hover=p?.time&&p.seriesData?.has(this.series)?p.time:null;this.readout();});
      this.chart.subscribeCrosshairMove(this.crosshair);this.resize=T.resize(this.chart,this.canvas,{compactAxis:true});
      this.theme=new MutationObserver(()=>this.applyTheme());this.theme.observe(document.documentElement,{attributes:true,attributeFilter:['data-theme']});this.applyTheme();
    }
    applyTheme(){
      const style=getComputedStyle(this.panel),dark=document.documentElement.dataset.theme==='dark';
      const value=(key,fallback)=>style.getPropertyValue(key).trim()||fallback;
      this.chart.applyOptions({layout:{background:{type:'solid',color:value('--surface',dark?'#0b0e14':'#fff')},textColor:value('--muted',dark?'#c7c4d7':'#64748b'),fontSize:11},
        grid:{vertLines:{color:value('--line-soft',dark?'#20232b':'#eef1f5')},horzLines:{color:value('--line-soft',dark?'#20232b':'#eef1f5')}}});
    }
    readout(){const row=this.rows.find(r=>r.time===this.hover)||this.rows.at(-1);this.panel.querySelector('.cv-readout').innerHTML=row?window.SpikeChartTools.readout(row,row.time,row.time!==this.hover):'';}
    draw(data,view,error){
      const T=window.SpikeChartTools,key=`${data?.symbol||''}|${data?.timezone||''}|${view.bars}`;
      const saved=key===this.key?T.anchor(this.chart.timeScale().getVisibleLogicalRange(),T.times(this.rows)):null;
      // API time is milliseconds. Keep only the API's disclosed, closed candle set.
      this.rows=(data?.candles||[]).slice(-view.bars).map(r=>({...r,time:r.time/1000}));
      this.sync(this.series,this.rows.map(({time,open,high,low,close})=>({time,open,high,low,close})));
      this.sync(this.volume,this.rows.map(r=>({time:r.time,value:r.volume||0,color:r.close>=r.open?'#01e59938':'#ff4f7338'})));
      this.series.applyOptions({priceFormat:T.priceFormat(this.rows)});
      for(const line of this.lines)this.series.removePriceLine(line);this.lines=[];
      const colors={range:'#3984b3',fib:'#9774c1',profile:'#cc8d35',npoc:'#b9689e',prior:'#8a969e'};
      const levels=(data?.levels||[]).filter(l=>view[l.kind]&&fin(l.price));this.activeLevels=levels;
      for(const level of levels)this.lines.push(this.series.createPriceLine({price:level.price,color:colors[level.kind],lineWidth:1,lineStyle:level.kind==='range'?0:2,axisLabelVisible:true,title:level.label}));
      this.panel.querySelector('.cv-levels').innerHTML=levels.map(l=>`<span style="color:${colors[l.kind]}">${esc(l.label)} <b>${T.axisPrice(l.price)}</b></span>`).join('');
      this.panel.querySelector('[data-range-heading]').textContent=`${(data?.symbol||'').replace('-USDT-SWAP','')} · 已收盘 15min`;
      const status=this.panel.querySelector('.cv-range-status'),warning=error||data?.error||data?.stale&&'行情已过期 · 仅供查看历史位置';
      status.classList.toggle('is-warning',!!warning);status.textContent=warning||(!this.rows.length?'正在加载 K 线与关键价位…':`收盘边界：北京 ${clock(data.last_close_ms,true)} · ${data.monday?.complete?'周一区间已冻结':data.monday?.developing?'周一区间形成中':'周一数据不足，暂不画区间'}`);
      const retained=T.restore(saved,T.times(this.rows));
      if(retained)this.chart.timeScale().setVisibleLogicalRange(retained);else if(key!==this.key||!saved){
        if(this.canvas.clientWidth<=700&&this.rows.length>70)this.chart.timeScale().setVisibleLogicalRange({from:this.rows.length-70,to:this.rows.length+3});
        else this.chart.timeScale().fitContent();
      }
      this.key=key;this.readout();this.controls.update();
    }
    destroy(){this.dead=true;this.controls?.destroy();this.crosshair?.cancel();this.resize?.disconnect();this.theme?.disconnect();this.chart?.remove();this.chart=null;this.host.replaceChildren();}
  }
  const instances=new WeakMap();
  function mount(host,data,view,error=''){let instance=instances.get(host);if(!instance){instance=new Interactive(host);instances.set(host,instance);}instance.update(data,view,error);return instance;}
  function unmount(host){if(!host)return;instances.get(host)?.destroy();instances.delete(host);}
  window.SpikeRangeChart={render,price,clock,mount,unmount};
  if(typeof module!=='undefined')module.exports={render,price};
})();
