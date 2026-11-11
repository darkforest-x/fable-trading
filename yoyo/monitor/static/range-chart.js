/* Shared OHLC renderer. Calendar/profile calculations stay in the API. */
(() => {
  'use strict';
  const esc=x=>String(x??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const wallpaperInteraction={handleScroll:{pressedMouseMove:true,mouseWheel:true,horzTouchDrag:true,vertTouchDrag:true},
          handleScale:{mouseWheel:true,pinch:true,axisPressedMouseMove:{time:true,price:true},axisDoubleClickReset:{time:true,price:true}},
          crosshair:{mode:0},timeScale:{rightOffset:10},kineticScroll:{mouse:false,touch:true}};
  const fin=x=>typeof x==='number'&&Number.isFinite(x);
  function rowSnapshot(row){
    const values={},keys=Object.keys(row);
    for(const key of keys)values[key]=row[key];
    return {values,count:keys.length};
  }
  function sameSnapshot(snapshot,row){
    let count=0;
    for(const key in row){
      if(!Object.hasOwn(row,key))continue;
      count++;
      if(!Object.hasOwn(snapshot.values,key)||!Object.is(snapshot.values[key],row[key]))return false;
    }
    return count===snapshot.count;
  }
  function inspectRows(previous,rows){
    if(!previous){
      return {snapshots:rows.map(rowSnapshot),previousLength:0,from:0,maFrom:0,
        changed:rows.map((_,i)=>i),structureChanged:true,timeChanged:true};
    }
    const snapshots=previous,previousLength=snapshots.length,common=Math.min(previousLength,rows.length),changed=[];
    let from=rows.length===previousLength?rows.length:common;
    let maFrom=from,timeChanged=rows.length!==previousLength;
    for(let i=0;i<common;i++){
      const old=snapshots[i],row=rows[i];
      if(sameSnapshot(old,row))continue;
      const priorTime=old.values.time??old.values.t,priorClose=old.values.close;
      const nextTime=row.time??row.t;
      const timeChangedHere=!Object.is(priorTime,nextTime);
      if(timeChangedHere)timeChanged=true;
      if(timeChangedHere||!Object.is(priorClose,row.close))maFrom=Math.min(maFrom,i);
      from=Math.min(from,i);
      changed.push(i);
      snapshots[i]=rowSnapshot(row);
    }
    if(rows.length>previousLength){
      for(let i=previousLength;i<rows.length;i++){
        snapshots.push(rowSnapshot(rows[i]));changed.push(i);
      }
    }else if(rows.length<previousLength)snapshots.length=rows.length;
    const structureChanged=rows.length!==previousLength;
    if(structureChanged){from=Math.min(from,common);maFrom=Math.min(maFrom,common);}
    return {snapshots,previousLength,from,maFrom,changed,structureChanged,timeChanged};
  }
  function samePoint(a,b){
    if(a===b)return true;
    if(!a||!b)return false;
    const keys=Object.keys(a);
    if(keys.length!==Object.keys(b).length)return false;
    return keys.every(key=>Object.is(a[key],b[key]));
  }
  function firstPointDifference(a,b){
    if(!a)return 0;
    const common=Math.min(a.length,b.length);
    for(let i=0;i<common;i++)if(!samePoint(a[i],b[i]))return i;
    return common;
  }
  const candlePoint=(r,color,custom)=>r.gap?{time:r.time}:({time:r.time,open:r.open,high:r.high,low:r.low,close:r.close,...(custom?{color,wickColor:color,borderColor:color}:{})});
  const volumePoint=(r,color,custom,T)=>r.gap||r.volume===null?{time:r.time}:({time:r.time,value:r.volume||0,color:custom?color+'38':r.close>=r.open?T.colors.volumeUp:T.colors.volumeDown});
  function rowMin(row){
    let min=Infinity;
    for(const value of [row.low??row.l,row.high??row.h,row.close??row.c])if(fin(value)&&value>0)min=Math.min(min,value);
    return min;
  }
  const priceFormats=new Map([2,4,8].map(digits=>[digits,new Intl.NumberFormat('zh-CN',{useGrouping:false,maximumFractionDigits:digits})]));
  const price=x=>fin(x)?priceFormats.get(Math.abs(x)>=100?2:Math.abs(x)>=1?4:8).format(x):'—';
  const clocks={
    short:new Intl.DateTimeFormat('zh-CN',{timeZone:'Asia/Shanghai',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false}),
    year:new Intl.DateTimeFormat('zh-CN',{timeZone:'Asia/Shanghai',year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false}),
  };
  const clock=(ms,year=false)=>ms==null?'—':clocks[year?'year':'short'].format(new Date(ms));
  // LWC 4.2 ignores vertical pan while autoScale is on. Unlock before its native
  // mouse/touch handler starts; do not capture or cancel the library's gestures.
  function bindFreePan(chart,canvas,env=window){
    let pointer=null;
    const end=e=>{if(e?.pointerId!=null&&pointer?.id!==e.pointerId)return;pointer=null;canvas.classList.remove('cv-panning');};
    const down=e=>{
      if(e.isPrimary===false||e.button!==0||e.target.closest?.('a,button'))return;
      const r=canvas.getBoundingClientRect(),left=chart.priceScale('left').width(),x=e.clientX-r.left,y=e.clientY-r.top;
      if(x<left||x>=left+chart.timeScale().width()||y<0||y>=canvas.clientHeight-chart.timeScale().height())return;
      chart.priceScale('right').applyOptions({autoScale:false});
      pointer={id:e.pointerId,x:e.clientX,y:e.clientY};
    };
    const move=e=>{if(pointer?.id===e.pointerId&&Math.hypot(e.clientX-pointer.x,e.clientY-pointer.y)>3)canvas.classList.add('cv-panning');};
    canvas.addEventListener('pointerdown',down,true);canvas.addEventListener('pointermove',move);
    for(const event of ['pointerup','pointercancel','blur'])env.addEventListener(event,end);
    return {destroy(){end();canvas.removeEventListener('pointerdown',down,true);canvas.removeEventListener('pointermove',move);for(const event of ['pointerup','pointercancel','blur'])env.removeEventListener(event,end);}};
  }
  function render(c, chartView){
    const colors=typeof window!=='undefined'?window.SpikeChartTools.colors:require('../../vision_research/static/chart-tools.js').colors;
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
    return `<svg class="ms-candle-chart" viewBox="0 0 ${w} ${h}" role="img" aria-label="OKX ${esc((c.symbol||'').replace('-USDT-SWAP','USDT.P'))} 15分钟K线与区间关键价位"><rect width="${w}" height="${h}" fill="var(--surface,#fff)"/>${Array.from({length:5},(_,i)=>{const p=low+(high-low)*i/4;return `<line x1="${left}" x2="${w-right}" y1="${y(p)}" y2="${y(p)}" stroke="var(--line-soft,#e8eeee)"/><text x="${left+4}" y="${y(p)-4}" fill="var(--muted,#71818a)" font-size="10">${f(p)}</text>`;}).join('')}${bars.map((b,i)=>{const tone=b.close>=b.open?colors.up:colors.down,bh=Math.max(1,Math.abs(y(b.open)-y(b.close))),bw=Math.max(.6,pw/bars.length*.65);return `<g><title>${clock(b.time,true)} 北京 · 开 ${f(b.open)} 高 ${f(b.high)} 低 ${f(b.low)} 收 ${f(b.close)}</title><rect x="${x(i)-bw/2}" y="${h-22-b.volume/maxVol*24}" width="${bw}" height="${b.volume/maxVol*24}" fill="${tone}" opacity=".25"/><line x1="${x(i)}" x2="${x(i)}" y1="${y(b.high)}" y2="${y(b.low)}" stroke="${tone}" stroke-width=".8"/><rect x="${x(i)-bw/2}" y="${Math.min(y(b.open),y(b.close))}" width="${bw}" height="${bh}" fill="${tone}"/></g>`;}).join('')}${labels.map(l=>`<g><line x1="${left}" x2="${w-right+4}" y1="${l.y}" y2="${l.y}" stroke="${color[l.kind]}" stroke-dasharray="${l.kind==='range'?'0':'5 4'}" opacity=".75"/><path d="M${w-right+4},${l.y}L${w-right+14},${l.ly}" fill="none" stroke="${color[l.kind]}"/><text x="${w-right+18}" y="${l.ly+3}" font-size="10" fill="${color[l.kind]}">${esc(l.label)} ${f(l.price)}</text></g>`).join('')}${[0,.25,.5,.75,1].map(r=>{const i=Math.min(bars.length-1,Math.floor(r*(bars.length-1)));return `<text x="${x(i)}" y="${h-5}" font-size="10" text-anchor="${r===0?'start':r===1?'end':'middle'}" fill="var(--muted,#71818a)">${clock(bars[i].time)}</text>`;}).join('')}</svg>`;
  }
  // A week is a time interval, not a chart-wide price line. Interpolate the
  // calendar boundary for coarse bars without inserting synthetic candles.
  function logicalAt(rows,stamp){
    if(!rows.length||!fin(stamp))return null;
    const t=stamp/1000,n=rows.length,step=n>1?rows[1].time-rows[0].time:86400;
    if(t<=rows[0].time)return (t-rows[0].time)/step;
    if(t<=rows[n-1].time){let lo=0,hi=n-1;while(lo+1<hi){const m=Math.floor((lo+hi)/2);if(rows[m].time<t)lo=m;else hi=m;}return lo+(t-rows[lo].time)/(rows[hi].time-rows[lo].time);}
    return n-1+(t-rows[n-1].time)/(n>1?rows[n-1].time-rows[n-2].time:step);
  }
  // LWC 4.2 truncates fractional logical coordinates; interpolate adjacent bars.
  function coordinateAt(scale,index){
    if(index==null)return null;
    const left=Math.floor(index),a=scale.logicalToCoordinate(left),b=scale.logicalToCoordinate(left+1);
    return a==null||b==null?null:a+(b-a)*(index-left);
  }
  class WeekLevels {
    constructor(owner){this.owner=owner;this.view={zOrder:()=> 'normal',renderer:()=>({draw:target=>this.paint(target)})};}
    attached({requestUpdate}){this.requestUpdate=requestUpdate;}
    detached(){this.requestUpdate=null;}
    paneViews(){return [this.view];}
    priceAxisViews(){
      const o=this.owner;
      if(this.axisLevels!==o.activeLevels){
        this.axisLevels=o.activeLevels;
        this.axes=(o.activeLevels||[]).filter(l=>l.axisLabelVisible!==false).map(l=>({
          coordinate:()=>o.series.priceToCoordinate(l.price)??-10000,
          text:()=>`${l.label} ${window.SpikeChartTools?.axisPrice(l.price)??price(l.price)}`,
          textColor:()=> '#fff',backColor:()=>l.color||'#528cc1',
          visible:()=>!!o.scaleLevels().length&&o.series.priceToCoordinate(l.price)!=null,
        }));
      }
      return this.axes;
    }
    updateAllViews(){}
    paint(target){
      const o=this.owner,w=o.levelWindow;if(!w||!fin(w.start_ms)||!fin(w.end_ms)||!o.rows.length)return;
      const scale=o.chart.timeScale();
      target.useMediaCoordinateSpace(({context:ctx,mediaSize})=>{
        ctx.save();ctx.beginPath();ctx.rect(0,0,mediaSize.width,mediaSize.height);ctx.clip();
        const occupied=[],monthlyLabels=[];
        for(const l of o.activeLevels||[]){
          const end=coordinateAt(scale,logicalAt(o.rows,l.end_ms??w.end_ms));if(end==null)continue;
          const b=Math.min(mediaSize.width,end);
          const start=coordinateAt(scale,logicalAt(o.rows,l.start_ms??w.start_ms)),a=Math.max(0,start??b);
          const y=o.series.priceToCoordinate(l.price);if(y==null||a>=b)continue;
          ctx.globalAlpha=l.historical ? .45 : 1;
          const color=l.color||'#528cc1';ctx.strokeStyle=color;ctx.lineWidth=l.lineWidth||1;ctx.setLineDash(l.lineStyle===2?[4,4]:l.lineStyle===1?[1,3]:[]);
          ctx.beginPath();ctx.moveTo(a,y);ctx.lineTo(b,y);ctx.stroke();
          if(l.startMarkerVisible!==false&&start>=0&&start<b){ctx.beginPath();ctx.arc(start,y,2.5,0,Math.PI*2);ctx.fillStyle=color;ctx.fill();}
          if((l.inlinePriceVisible||l.inlineNameVisible)&&y>=18&&y<=mediaSize.height-12){
            const number=window.SpikeChartTools?.axisPrice(l.price)??price(l.price),text=l.inlinePriceVisible?(l.inlineNameVisible?`${l.label} (${number})`:number):l.label;
            ctx.font='11px -apple-system, BlinkMacSystemFont, "PingFang SC", sans-serif';ctx.textAlign='right';ctx.textBaseline='bottom';
            const width=ctx.measureText(text).width+12;
            if(width<=b-a){
              // Fibonacci-style text leaves candles visible through the ray.
              ctx.fillStyle=typeof document!=='undefined'&&document.documentElement.dataset.theme==='dark'?'#a1adbd':'#627487';
              if(l.monthly)monthlyLabels.push({text,left:a,right:b-6,y:y-4,width,alpha:ctx.globalAlpha});
              else {ctx.fillText(text,b-6,y-4);occupied.push({left:b-width,right:b-6,y:y-4});}
            }
          }
        }
        // Keep original weekly labels anchored. If a monthly line is very close, slide
        // its text along the same ray first, and only then use the next free baseline.
        for(const label of monthlyLabels){
          const {text,left,right,y,width,alpha}=label;
          const space=(x,baseline)=>!occupied.some(p=>Math.abs(p.y-baseline)<14&&x>p.left&&x-width<p.right);
          let x=right,baseline=y;
          if(!space(x,baseline)){
            const other=left+width-6;
            if(space(other,baseline))x=other;
            else {
              baseline=[14,-14,28,-28,42,-42,56,-56].map(d=>y+d)
                .find(row=>row>=12&&row<mediaSize.height&&space(x,row));
              if(baseline===undefined)continue;
            }
          }
          ctx.globalAlpha=alpha;ctx.fillStyle=typeof document!=='undefined'&&document.documentElement.dataset.theme==='dark'?'#a1adbd':'#627487';
          ctx.fillText(text,x,baseline);occupied.push({left:x-width,right:x,y:baseline});
        }
        ctx.restore();
      });
    }
  }
  if(typeof module!=='undefined')Object.assign(module.exports,{logicalAt,WeekLevels});
  // Stable interactive instance: polling updates series without replacing the canvas.
  class Interactive {
    constructor(host){this.host=host;this.rows=[];this.dead=false;this.lines=[];this.key='';this.sync=window.SpikeChartTools.syncer();this.views=new window.SpikeChartTools.Views();}
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
      this.host.innerHTML=`<section class="cv-range"><div class="cv-range-header"><strong data-range-heading>已收盘 K 线 · 15min</strong>${T.toolbar({expand:true})}</div><p class="cv-range-status" role="status"></p><div class="cv-readout"></div><div class="cv-ma-legend chart-ma-legend" aria-label="六条均线数值" hidden></div><div class="cv-canvas"></div><details class="cv-level-details"><summary>关键价位 · 完整价格</summary><div class="cv-levels" aria-label="关键价位"></div></details></section>`;
      this.panel=this.host.firstElementChild;this.canvas=this.panel.querySelector('.cv-canvas');
      this.chart=window.LightweightCharts.createChart(this.canvas,{...T.interaction,width:this.canvas.clientWidth||600,height:this.canvas.clientHeight||420,
        rightPriceScale:{scaleMargins:{top:.08,bottom:.2}},localization:{locale:'zh-CN',priceFormatter:T.axisPrice,timeFormatter:T.clock},
        timeScale:{timeVisible:true,secondsVisible:false,rightOffset:5,tickMarkFormatter:T.clock}});
      this.series=this.chart.addCandlestickSeries({upColor:T.colors.up,downColor:T.colors.down,wickUpColor:T.colors.up,wickDownColor:T.colors.down,borderVisible:false,autoscaleInfoProvider:original=>{const value=original();if(!value)return null;const prices=this.scaleLevels();return {...value,priceRange:{minValue:Math.min(value.priceRange.minValue,...prices),maxValue:Math.max(value.priceRange.maxValue,...prices)}};}});
      this.volume=this.chart.addHistogramSeries({priceScaleId:'volume',priceFormat:{type:'volume'},lastValueVisible:false,priceLineVisible:false});
      this.volume.priceScale().applyOptions({scaleMargins:{top:.85,bottom:0}});
      this.controls=T.bind(this.panel,{chart:()=>this.chart,rows:()=>this.rows,canvas:this.canvas,panel:this.panel});
      if(this.pending.view.wallpaper){
        this.chart.applyOptions(wallpaperInteraction);
        this.freePan=bindFreePan(this.chart,this.canvas);
        this.canvas.setAttribute('aria-label','K 线交互图；拖动自由平移，滚轮或双指缩放，拖动坐标轴缩放，双击价格轴复位；Home 全图，End 最新');
      }
      this.crosshair=T.frame(p=>{if(!this.chart)return;this.hover=p?.time&&p.seriesData?.has(this.series)?p.time:null;this.readout();});
      this.chart.subscribeCrosshairMove(this.crosshair);
      this.band=document.createElement('div');this.band.className='cv-monday-band';this.band.setAttribute('aria-hidden','true');this.band.hidden=true;this.canvas.append(this.band);
      this.bandPaint=T.frame(()=>{this.paintMondayBand();this.visibleRange?.(this);});this.chart.timeScale().subscribeVisibleLogicalRangeChange(this.bandPaint);
      this.bandResize=new ResizeObserver(this.bandPaint);this.bandResize.observe(this.canvas);
      this.resize=T.resize(this.chart,this.canvas,{compactAxis:this.pending.view.wallpaper?'always':true});
      this.theme=new MutationObserver(()=>this.applyTheme());this.theme.observe(document.documentElement,{attributes:true,attributeFilter:['data-theme']});this.applyTheme();
    }
    scaleLevels(){
      if(this.referenceScale===false)return [];
      const w=this.levelWindow,r=this.chart?.timeScale().getVisibleLogicalRange();
      if(w&&r&&(r.to<logicalAt(this.rows,w.start_ms)||r.from>=logicalAt(this.rows,w.end_ms)))return [];
      return this.activeLevels?.filter(l=>!w||!r||r.to>=logicalAt(this.rows,l.start_ms??w.start_ms)&&r.from<logicalAt(this.rows,l.end_ms??w.end_ms)).map(l=>l.price)||[];
    }
    applyTheme(){
      const style=getComputedStyle(this.panel),dark=document.documentElement.dataset.theme==='dark';
      const value=(key,fallback)=>style.getPropertyValue(key).trim()||fallback;
      this.chart.applyOptions({layout:{background:{type:'solid',color:value('--surface',dark?'#0b0e14':'#fff')},textColor:value('--muted',dark?'#c7c4d7':'#64748b'),fontSize:this.pending.view.wallpaper?10:11},
        ...(this.pending.view.wallpaper?{rightPriceScale:{borderVisible:false,ticksVisible:false,minimumWidth:0},timeScale:{borderVisible:false,ticksVisible:false,minimumHeight:0}}:{}),
        grid:{vertLines:{color:value('--line-soft',dark?'#20232b':'#eef1f5')},horzLines:{color:value('--line-soft',dark?'#20232b':'#eef1f5')}}});
    }
    readout(){
      const row=this.rowByTime?.get(this.hover)||this.rows.at(-1),T=window.SpikeChartTools;
      this.panel.querySelector('.cv-readout').hidden=!!this.compactReplay&&!this.hover;
      const fields=row?[row.time,row.open,row.high,row.low,row.close,row.time!==this.hover]:[];
      if(!this.readoutFields||fields.length!==this.readoutFields.length||fields.some((v,i)=>!Object.is(v,this.readoutFields[i]))){
        const host=this.panel.querySelector('.cv-readout');
        // Retain the five readout spans during panning; only text values change.
        if(row&&this.readoutNodes?.length===5){
          const values=[`${fields[5]?'最新 K 线':'光标'} · ${T.clock(row.time)} 北京开盘`,...fields.slice(1,5).map(T.axisPrice)];
          values.forEach((value,i)=>{if(this.readoutNodes[i].textContent!==value)this.readoutNodes[i].textContent=value;});
        }else{
          host.innerHTML=row?T.readout(row,row.time,fields[5]):'';
          this.readoutNodes=host.querySelectorAll?.('.cv-time, b');
        }
        this.readoutFields=fields;
      }
      if(this.maSeries?.length){
        const style=window.SpikeChartStyle,values=style.lines.map(line=>row?.[line.key]);
        if(!this.legendValues||values.some((v,i)=>!Object.is(v,this.legendValues[i]))){
          const host=this.panel.querySelector('.cv-ma-legend');
          if(this.legendNodes?.length===style.lines.length){
            style.lines.forEach((line,i)=>{const node=this.legendNodes[i],value=values[i],text=style.legendPrice(value);
              node.title=`${line.label} · ${fin(value)?value:'历史不足'}`;
              const number=node.querySelector('b');if(number.textContent!==text)number.textContent=text;
            });
          }else{host.innerHTML=style.legend(row);this.legendNodes=host.querySelectorAll?.('.chart-ma-value');}
          this.legendValues=values;
        }
      }
    }
    processRows(data,view,key,step,oldTimes){
      const raw=data?.candles||[];
      const displayWindow=view.displayWindow;
      const processKey=JSON.stringify([key,!!view.ma,displayWindow?.from??null,displayWindow?.to??null]);
      const configChanged=this.processKey!==processKey;
      if(configChanged){
        this.inputSnapshots=null;
        this.computedRows=null;
        this.averages=null;
      }
      if(view.ma)this.averages ||= window.SpikeChartStyle.createAverages();
      else this.averages=null;

      const previousLength=this.processedRawLength||0;
      const previousStart=this.renderStart??0;
      const changes=inspectRows(this.inputSnapshots,raw);
      this.inputSnapshots=changes.snapshots;
      const dataChanged=changes.from<raw.length||changes.structureChanged||!this.computedRows;
      const visibleBars=Number.isFinite(view.bars)&&view.bars>0?view.bars:Infinity;
      const nextStart=displayWindow?null:Number.isFinite(visibleBars)?Math.max(0,raw.length-visibleBars):0;
      const tailSafe=!configChanged&&!displayWindow&&this.computedRows
        &&changes.from>=Math.max(0,previousLength-1,previousStart)
        &&raw.length>=previousLength&&nextStart===previousStart;
      let rebuild=configChanged||!this.computedRows||(dataChanged&&!tailSafe);
      if(dataChanged&&displayWindow)rebuild=true;
      if(!rebuild&&dataChanged&&view.ma&&changes.maFrom<raw.length){
        rebuild=this.averages.patchTail(raw,step,changes.maFrom)===null;
      }
      this.processKey=processKey;

      if(rebuild)this.rebuildRows(raw,view,step,displayWindow,oldTimes);
      else if(dataChanged)this.patchRows(raw,view,changes,previousStart);
      else{
        this.rowsDirtyFrom=this.rows.length;
        this.maDirtyFrom=this.rows.length;
        this.rowsFull=false;
        this.rowTimesChanged=false;
      }
      this.processedRawLength=raw.length;
      return {
        full:this.rowsFull,
        rowsFrom:this.rowsDirtyFrom,
        maFrom:this.maDirtyFrom,
        changed:changes.changed,
        timeChanged:this.rowTimesChanged,
      };
    }
    rebuildRows(raw,view,step,displayWindow,oldTimes){
      const computed=view.ma?this.averages(raw,step):raw.slice();
      this.computedRows=computed;
      const selected=displayWindow
        ?computed.filter(row=>row.time>=displayWindow.from&&row.time<displayWindow.to)
        :computed;
      this.rows=selected.slice(-view.bars).map(row=>({...row,time:row.time/1000}));
      this.rowByTime=new Map(this.rows.map(row=>[row.time,row]));
      this.renderStart=displayWindow?null:Math.max(0,computed.length-this.rows.length);
      this.rowTimes=this.rows.map(row=>row.time);
      this.rowsDirtyFrom=0;
      this.maDirtyFrom=0;
      this.rowsFull=true;
      this.rowTimesChanged=oldTimes.length!==this.rowTimes.length
        ||oldTimes.some((time,i)=>time!==this.rowTimes[i]);
    }
    patchRows(raw,view,changes,previousStart){
      for(let i=changes.from;i<raw.length;i++){
        const values=view.ma?this.averages.valueAt(i):null;
        this.computedRows[i]=view.ma?{...raw[i],...values}:{...raw[i]};
      }
      this.computedRows.length=raw.length;

      let firstChanged=Infinity;
      const start=Math.max(changes.from,previousStart);
      for(let i=start;i<raw.length;i++){
        const chartIndex=i-previousStart;
        const source=this.computedRows[i];
        const next={...source,time:source.time/1000};
        const old=this.rows[chartIndex];
        if(old){
          if(old.time!==next.time)this.rowByTime.delete(old.time);
          this.rows[chartIndex]=next;
          this.rowByTime.set(next.time,next);
          this.rowTimes[chartIndex]=next.time;
        }else if(chartIndex===this.rows.length){
          this.rows.push(next);
          this.rowByTime.set(next.time,next);
          this.rowTimes.push(next.time);
        }
        firstChanged=Math.min(firstChanged,chartIndex);
      }
      this.rowsDirtyFrom=Number.isFinite(firstChanged)?firstChanged:this.rows.length;
      this.maDirtyFrom=view.ma&&changes.maFrom<raw.length
        ?Math.max(0,changes.maFrom-previousStart)
        :this.rows.length;
      this.rowsFull=false;
      this.rowTimesChanged=changes.timeChanged;
    }
    updatePriceFormat(T,full,from){
      const scan=()=>{let minimum=Infinity,at=-1;this.rows.forEach((row,i)=>{const value=rowMin(row);if(value<minimum){minimum=value;at=i;}});this.minimumPrice=minimum;this.minimumPriceIndex=at;};
      if(full||this.minimumPrice===undefined)scan();
      else if(from<this.rows.length){
        if(this.minimumPriceIndex>=from)scan();
        else for(let i=from;i<this.rows.length;i++){const value=rowMin(this.rows[i]);if(value<this.minimumPrice){this.minimumPrice=value;this.minimumPriceIndex=i;}}
      }
      return T.priceFormatMinimum(this.minimumPrice);
    }
    draw(data,view,error){
      const T=window.SpikeChartTools,key=`${data?.symbol||''}|${data?.timezone||''}|${data?.bar||'15m'}|${view.bars}`;
      const oldTimes=this.rowTimes||T.times(this.rows),oldRange=this.chart.timeScale().getVisibleLogicalRange();
      this.views.save(this.key,oldRange,oldTimes);
      if(key!==this.key)this.chart.priceScale('right').applyOptions({autoScale:true});
      const saved=key===this.key?T.anchor(oldRange,oldTimes):null;
      // API time is milliseconds. Display-only live tips are explicitly merged
      // by their owning chart; analytical consumers keep their closed series.
      const step={'1m':60000,'2m':120000,'3m':180000,'5m':300000,'15m':900000,'30m':1800000,'1H':3600000,'4H':14400000,'12H':43200000,'1D':86400000}[data?.bar]||900000;
      const rowChanges=this.processRows(data,view,key,step,oldTimes),newTimes=this.rowTimes,timeChanged=rowChanges.timeChanged;
      this.rowDelta=rowChanges.full?null:{changedRows:rowChanges.changed.map(i=>i-(this.renderStart||0)).filter(i=>i>=0&&i<this.rows.length)};
      this.syncAverages(!!view.ma,rowChanges.maFrom,rowChanges.full);
      const custom=typeof view.candleColor==='function',candleColors=custom?this.rows.map(view.candleColor):null;
      let paletteFrom=this.rows.length;
      if(custom){
        if(!this.candleColorCustom||!this.candleColorValues||this.candleColorValues.length!==candleColors.length)paletteFrom=0;
        else for(let i=0;i<candleColors.length;i++)if(!Object.is(candleColors[i],this.candleColorValues[i])){paletteFrom=Math.min(paletteFrom,i);}
      }else if(this.candleColorCustom)paletteFrom=0;
      this.candleColorCustom=custom;this.candleColorValues=candleColors;
      this.syncCandleData(T,candleColors,custom,rowChanges.full,Math.min(rowChanges.rowsFrom,paletteFrom));
      this.compactReplay=!!view.compactReplay;
      const options={priceFormat:this.updatePriceFormat(T,rowChanges.full,rowChanges.rowsFrom),lastValueVisible:!this.compactReplay,priceLineVisible:!this.compactReplay,...(view.candleColors?{upColor:view.candleColors.up,wickUpColor:view.candleColors.up,downColor:view.candleColors.down,wickDownColor:view.candleColors.down}:{})},optionKey=JSON.stringify(options);
      if(optionKey!==this.optionKey){this.series.applyOptions(options);this.optionKey=optionKey;}
      const colors={range:'#3984b3',fib:'#9774c1',profile:'#cc8d35',npoc:'#b9689e',prior:'#8a969e'};
      const levels=(data?.levels||[]).filter(l=>view[l.kind]&&fin(l.price));this.activeLevels=levels;
      this.referenceScale=view.scaleReferenceLevels!==false;
      const wallpaperKey=view.wallpaper?(view.compactReplay?'replay':'live'):'';
      if(wallpaperKey&&this.wallpaperOptions!==wallpaperKey){this.wallpaperOptions=wallpaperKey;this.chart.applyOptions({rightPriceScale:{scaleMargins:{top:view.compactReplay ? .12 : .25,bottom:view.compactReplay ? .2 : .18}},grid:{vertLines:{visible:false},horzLines:{visible:false}}});}
      this.levelWindow=data?.level_window;
      if(this.levelWindow&&!this.weekLevels){this.weekLevels=new WeekLevels(this);this.series.attachPrimitive(this.weekLevels);}
      this.weekLevels?.requestUpdate?.();
      const levelKey=JSON.stringify([levels,this.levelWindow]);
      if(levelKey!==this.levelKey){
      for(const line of this.lines)this.series.removePriceLine(line);this.lines=[];
      if(!this.levelWindow)for(const level of levels)this.lines.push(this.series.createPriceLine({price:level.price,color:level.color||colors[level.kind],lineWidth:level.lineWidth||1,lineStyle:level.lineStyle??(level.kind==='range'?0:2),lineVisible:true,axisLabelVisible:level.axisLabelVisible!==false,title:level.label}));
      this.panel.querySelector('.cv-levels').innerHTML=levels.map(l=>`<span style="color:${l.color||colors[l.kind]}">${esc(l.label)} <b>${T.axisPrice(l.price)}</b></span>`).join('');
      this.levelKey=levelKey;}
      this.panel.querySelector('[data-range-heading]').textContent=`${(data?.symbol||'').replace('-USDT-SWAP','')} · ${data?.display_live?'实时':'已收盘'} ${data?.bar||'15m'}`;
      const status=this.panel.querySelector('.cv-range-status'),warning=error||data?.error||this.rows.length&&data?.stale&&'行情已过期 · 仅供查看历史位置';
      status.classList.toggle('is-warning',!!warning);status.textContent=warning||(!this.rows.length?'正在加载 K 线与关键价位…':`收盘边界：北京 ${clock(data.last_close_ms,true)} · ${data.monday?.complete?'周一区间已冻结':data.monday?.developing?'周一区间形成中':'周一数据不足，暂不画区间'}`);
      const retained=T.restore(saved,newTimes)||this.views.get(key,newTimes);
      if((timeChanged||key!==this.key)&&retained)this.chart.timeScale().setVisibleLogicalRange(retained);else if(!retained&&(key!==this.key||timeChanged||!this.key)){
        if(view.initialBars&&this.rows.length){const span=Math.min(this.rows.length+10,this.canvas.clientWidth<=700?70:view.initialBars),to=this.rows.length-1+10;this.chart.timeScale().setVisibleLogicalRange({from:to-span,to});}
        else if(!view.fitAll&&this.canvas.clientWidth<=700&&this.rows.length>70)this.chart.timeScale().setVisibleLogicalRange({from:this.rows.length-70,to:this.rows.length+3});
        else this.chart.timeScale().fitContent();
      }
      this.key=key;this.readout();this.controls.update();
      view.decorate?.(this,data);
      if(view.showLevels)this.panel.querySelector('.cv-level-details').open=true;
      this.mondayWindow=view.mondayBand?data?.monday:null;
      if(timeChanged||this.mondayBandKey!==JSON.stringify([this.mondayWindow?.start_ms,this.mondayWindow?.end_ms])){
        this.bandIndices=this.mondayWindow?this.rows.map((r,i)=>r.time*1000>=this.mondayWindow.start_ms&&r.time*1000<this.mondayWindow.end_ms?i:-1).filter(i=>i>=0):[];
        this.mondayBandKey=JSON.stringify([this.mondayWindow?.start_ms,this.mondayWindow?.end_ms]);
      }
      this.paintMondayBand();
      this.visibleRange=view.onVisibleRange;this.bandPaint?.();
    }
    syncCandleData(T,colors,custom,full,from){
      if(full||!this.candleData||!this.volumeData||this.candleData.length>this.rows.length||this.volumeData.length>this.rows.length){
        const oldCandles=this.candleData,oldVolumes=this.volumeData;
        this.candleData=this.rows.map((r,i)=>candlePoint(r,colors?.[i],custom));
        this.volumeData=this.rows.map((r,i)=>volumePoint(r,colors?.[i],custom,T));
        if(!oldCandles)this.sync(this.series,this.candleData);else{const first=firstPointDifference(oldCandles,this.candleData);if(first<this.candleData.length||oldCandles.length!==this.candleData.length)this.sync(this.series,this.candleData,first);}
        if(!oldVolumes)this.sync(this.volume,this.volumeData);else{const first=firstPointDifference(oldVolumes,this.volumeData);if(first<this.volumeData.length||oldVolumes.length!==this.volumeData.length)this.sync(this.volume,this.volumeData,first);}
        return;
      }
      if(from>=this.rows.length)return;
      while(this.candleData.length<this.rows.length){this.candleData.push(null);this.volumeData.push(null);}
      let candleFrom=this.rows.length,volumeFrom=this.rows.length;
      for(let i=from;i<this.rows.length;i++){
        const r=this.rows[i],candle=candlePoint(r,colors?.[i],custom),volume=volumePoint(r,colors?.[i],custom,T);
        if(!samePoint(this.candleData[i],candle)){this.candleData[i]=candle;candleFrom=Math.min(candleFrom,i);}
        if(!samePoint(this.volumeData[i],volume)){this.volumeData[i]=volume;volumeFrom=Math.min(volumeFrom,i);}
      }
      if(candleFrom<this.rows.length)this.sync(this.series,this.candleData,candleFrom);
      if(volumeFrom<this.rows.length)this.sync(this.volume,this.volumeData,volumeFrom);
    }
    syncAverages(visible,dirtyFrom=this.rows.length,full=false){
      const legend=this.panel.querySelector('.cv-ma-legend');if(legend)legend.hidden=!visible;
      if(!visible){for(const line of this.maSeries||[])this.chart.removeSeries(line);this.maSeries=[];this.maData=[];return;}
      const style=window.SpikeChartStyle;
      let created=false;if(!this.maSeries?.length){this.maSeries=style.lines.map(m=>this.chart.addLineSeries({color:m.color,lineWidth:1,priceLineVisible:false,lastValueVisible:false,crosshairMarkerVisible:false}));this.maData=style.lines.map(()=>[]);created=true;}
      if(full||created||this.maData.some(rows=>rows.length>this.rows.length)){
        const previous=this.maData;
        this.maData=style.lines.map(m=>this.rows.map(r=>fin(r[m.key])?{time:r.time,value:r[m.key]}:{time:r.time}));
        style.lines.forEach((m,i)=>{
          if(!previous?.[i])this.sync(this.maSeries[i],this.maData[i]);
          else{const first=firstPointDifference(previous[i],this.maData[i]);if(first<this.maData[i].length||previous[i].length!==this.maData[i].length)this.sync(this.maSeries[i],this.maData[i],first);}
        });return;
      }
      if(dirtyFrom>=this.rows.length)return;
      style.lines.forEach((m,i)=>{
        const rows=this.maData[i];let first=this.rows.length;
        while(rows.length<this.rows.length)rows.push(null);
        for(let j=dirtyFrom;j<this.rows.length;j++){const row=this.rows[j],next=fin(row[m.key])?{time:row.time,value:row[m.key]}:{time:row.time};if(!samePoint(rows[j],next)){rows[j]=next;first=Math.min(first,j);}}
        if(first<this.rows.length)this.sync(this.maSeries[i],rows,first);
      });
    }
    paintMondayBand(){
      if(!this.band)return;const scale=this.chart?.timeScale();
      const indices=this.bandIndices||[];
      if(!indices.length||!scale){this.band.hidden=true;return;}
      const first=scale.timeToCoordinate(this.rows[indices[0]].time),last=scale.timeToCoordinate(this.rows[indices.at(-1)].time),width=scale.width();
      if(first==null||last==null){this.band.hidden=true;return;}
      const half=indices.length>1?Math.abs(last-first)/(indices.length-1)/2:width/Math.max(1,this.rows.length)/2;
      const a=first-half,b=last+half;
      if(a==null||b==null||b<=0||a>=width){this.band.hidden=true;return;}
      this.band.hidden=false;this.band.style.left=Math.max(0,a)+'px';this.band.style.width=Math.max(0,Math.min(width,b)-Math.max(0,a))+'px';this.band.textContent='纽约周一定界';
    }
    destroy(){this.dead=true;this.drawingLayer?.destroy();this.freePan?.destroy();this.bandPaint?.cancel();this.bandResize?.disconnect();this.controls?.destroy();this.crosshair?.cancel();this.resize?.disconnect();this.theme?.disconnect();this.chart?.remove();this.chart=null;this.host.replaceChildren();}
  }
  const instances=new WeakMap();
  function mount(host,data,view,error=''){let instance=instances.get(host);if(!instance){instance=new Interactive(host);instances.set(host,instance);}instance.update(data,view,error);return instance;}
  function unmount(host){if(!host)return;instances.get(host)?.destroy();instances.delete(host);}
  window.SpikeRangeChart={render,price,clock,mount,unmount,WeekLevels,bindFreePan,wallpaperInteraction};
  if(typeof module!=='undefined')module.exports={render,price,bindFreePan,Interactive,logicalAt,coordinateAt,WeekLevels,wallpaperInteraction};
})();
