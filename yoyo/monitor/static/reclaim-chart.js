/* Paper fills decorate an independent instance of the Monday wallpaper chart.
 * Price/time geometry uses actual fills, exits and activated entry stops only.
 * Weekly rays and every loaded same-symbol fill retain independent lifetimes.
 */
(() => {
  'use strict';
  const fin=Number.isFinite,periods={'1m':60000,'2m':120000,'3m':180000,'5m':300000,'15m':900000,'1H':3600000,'4H':14400000,'12H':43200000,'1D':86400000};
  const valid=p=>p && /^[A-Z0-9]{1,32}-USDT-SWAP$/.test(p.symbol) && [1,-1].includes(p.side) && fin(p.fill_ms) && p.fill_ms>0 && fin(p.entry) && p.entry>0;
  function latest(track){return [...(track?.open||[]),...(track?.closed||[]),...(track?.latest_funded?[track.latest_funded]:[])].filter(p=>valid(p)&&p.taken===1&&p.notional>0).sort((a,b)=>b.fill_ms-a.fill_ms||b.id-a.id)[0]||null;}
  function select(track,id,pinned){return id ? [...(track?.open||[]),...(track?.closed||[]),...(track?.latest_funded?[track.latest_funded]:[])].find(p=>String(p.id)===id&&valid(p)) || (pinned&&String(pinned.id)===id?pinned:null) : latest(track);}
  function candleIndex(rows,stamp,step){
    if(!fin(stamp)||!fin(step)||step<=0)return -1;
    let lo=0,hi=rows.length;
    while(lo<hi){const mid=(lo+hi)>>>1;if(rows[mid].time*1000<=stamp)lo=mid+1;else hi=mid;}
    const i=lo-1;return i>=0&&stamp<rows[i].time*1000+step?i:-1;
  }
  function geometry(p,rows,step,now){
    if(!valid(p)||!rows.length)return null;
    const end=p.stage==='closed'?p.exit_ms:now;
    if(!fin(end)||end<p.fill_ms||end<rows[0].time*1000)return null;
    return {entry:candleIndex(rows,p.fill_ms,step),exit:p.stage==='closed'?candleIndex(rows,p.exit_ms,step):-1,
      from:p.fill_ms,to:end,stop:fin(p.be_from_ms)&&p.be_from_ms>=p.fill_ms&&p.be_from_ms<=end?p.be_from_ms:null};
  }
  function replayRange(trade,rows,step,contextBars=45){
    if(!rows.length)return null;
    const a=candleIndex(rows,trade.fills?.entry_start_ms??trade.entry_start_ms??trade.entry_ms,step),b=candleIndex(rows,trade.fills?.exit_kind==='interval'?trade.exit_ms-1:trade.exit_ms,step);
    if(a<0)return null;
    const end=b>=0?b:rows.length-1,span=Math.max(contextBars,end-a+1),pad=Math.max(contextBars<45?8:12,span*.18);
    return {from:a-pad,to:Math.max(a+contextBars,end+pad),entry:a,exit:b};
  }
  function replayFills(trade){
    const f=trade?.fills;
    if(!f)return [];
    const marks=[],short=trade.side===-1;
    function add(key,label,start,end,price){
      if(![start,end,price].every(fin)||price<=0||end<start)return;
      marks.push({key,label,start,end,price,color:key==='entry'?'#2879bf':'#738392',
        above:key==='entry'?short:!short});
    }
    add('entry',short?'① 开空':'① 开多',f.entry_start_ms,f.entry_end_ms,f.entry_price);
    if(f.exit_legs?.length){
      f.exit_legs.forEach((leg,i)=>{
        const time=leg?.time_ms??leg?.exit_ms;
        const cloud=trade.kind==='cloud-v17';
        const label=cloud ? i===f.exit_legs.length-1?'② 平仓':leg.action==='trim'?'':(leg.label||'分批平仓')
          :leg?.label||(f.exit_legs.length===1?'② 平仓':`② 出场 ${i+1}/${f.exit_legs.length}`);
        add('exit_leg',label,leg.start_ms??time,leg.end_ms??time,leg?.price);
      });
    }else{
      add('exit','② 平仓',f.exit_start_ms,f.exit_end_ms,f.exit_price);
    }
    return marks;
  }
  // Frozen EMA values carry the warmup preceding each window. A separate line
  // per continuous run prevents the library from bridging a missing 12H bar.
  function frozenCloudLines(instance,data){
    const key=data.backtestSource==='live-cloud-v17'?data.trade_id:null;
    if(instance.cloudLineKey===key)return;
    for(const series of instance.cloudLines||[])instance.chart.removeSeries(series);
    instance.cloudLines=[];instance.cloudLineKey=key;
    if(key==null)return;
    for(const [field,color] of [['ema20','#397cb2'],['ema50','#ba801f']]){
      let points=[];
      const finish=()=>{
        if(!points.length)return;
        const line=instance.chart.addLineSeries({color,lineWidth:1,priceLineVisible:false,lastValueVisible:false,
          crosshairMarkerVisible:false,autoscaleInfoProvider:()=>null});
        line.setData(points);instance.cloudLines.push(line);points=[];
      };
      for(const b of data.candles){
        if(!b.gap&&b.ema_ready&&fin(b[field]))points.push({time:b.time/1000,value:b[field]});
        else finish();
      }
      finish();
    }
  }
  function replayScore(trade,rows,step){
    const rules=trade?.entry_rules,close=trade?.fills?.score_close_ms;
    if(step!==60000||rules?.status!=='saved'||!fin(rules.score)||rules.score<0||rules.score>1||!fin(close)||close<=0)return null;
    // Candle timestamps denote opens: the score belongs to the bar that ENDS
    // here, never the next bar opening at the same timestamp. Do not snap gaps.
    const index=candleIndex(rows,close-1,step),row=rows[index];
    if(!row||row.time*1000+step!==close||!fin(row.low))return null;
    const clock=new Intl.DateTimeFormat('zh-CN',{timeZone:'Asia/Shanghai',hour:'2-digit',minute:'2-digit',hourCycle:'h23'}).format(close);
    return {key:'score',index,close,score:rules.score,label:`${clock} 收盘 · ${rules.score.toFixed(4)}分`,above:false};
  }
  function paperReplayTrade(p){
    return {id:p.id,symbol:p.symbol.replace('-USDT-SWAP','USDT'),side:p.side,entry_ms:p.fill_ms,exit_ms:p.exit_ms,pnl:p.pnl,
      return_pct:fin(p.net_bp)?p.net_bp/100:null,point_times:true,
      fills:{entry_start_ms:p.fill_ms,entry_end_ms:p.fill_ms,entry_price:p.entry,exit_start_ms:p.exit_ms,exit_end_ms:p.exit_ms,exit_price:p.exit_price,exit_kind:'point'}};
  }
  function paperReplayFills(p){
    const partial=(p.exits||[]).slice(0,-1).filter(f=>fin(f.exit_ms)&&f.exit_ms>=p.fill_ms&&f.exit_ms<=p.exit_ms&&fin(f.price)&&f.price>0)
      .map(f=>({key:'partial',label:'',color:'#738392',start:f.exit_ms,end:f.exit_ms,price:f.price}));
    return [...replayFills(paperReplayTrade(p)),...partial];
  }
  function logicalAt(rows,stamp,step){
    const i=candleIndex(rows,stamp,step);
    if(i>=0)return i+(stamp-rows[i].time*1000)/step;
    const before=candleIndex(rows,stamp-1,step);
    return before>=0&&rows[before].time*1000+step===stamp?before+1:null;
  }
  // Label clearance uses all OHLC envelopes under the full text width, not
  // just the fill candle. Pixel geometry is recomputed on every pan/zoom/resize.
  function replayLabelLayout(marks,candles,width,height){
    const placed=[],overlapX=(a,b)=>a.left<b.right+4&&a.right>b.left-4;
    for(const mark of marks){
      if(mark.width>width-16||mark.anchor<0||mark.anchor>width)continue;
      const left=Math.max(8,Math.min(width-mark.width-8,mark.anchor-mark.width/2)),right=left+mark.width;
      const neighbors=candles.filter(c=>overlapX({left,right},c));
      if(!neighbors.length)continue; // Missing OHLC must not invent a clear area.
      const column=candles.filter(c=>c.left<mark.anchor+4&&c.right>mark.anchor-4);
      for(const above of [mark.above,!mark.above]){
        const edge=above?Math.min(mark.y,...neighbors.map(c=>c.top)):Math.max(mark.y,...neighbors.map(c=>c.bottom));
        let top=above?edge-36:edge+18;
        for(const other of placed.filter(p=>overlapX({left,right},p)).sort((a,b)=>above?b.top-a.top:a.top-b.top)){
          if(top<other.bottom+4&&top+18>other.top-4)top=above?other.top-22:other.bottom+4;
        }
        if(top<6||top+18>Math.min(height-6,mark.maxBottom??height))continue;
        const tip=above?Math.min(mark.y,...column.map(c=>c.top))-5:Math.max(mark.y,...column.map(c=>c.bottom))+5;
        placed.push({...mark,left,right,top,bottom:top+18,above,tip});break;
      }
    }
    return placed;
  }
  // Stitch c8d8589c0a3b4ad59e1cce3f221564fe: labels and arrows in candle whitespace.
  class ReplayFillDrawing {
    constructor(owner){this.owner=owner;this.items=[];this.view={zOrder:()=> 'top',renderer:()=>({draw:target=>this.paint(target)})};}
    attached({requestUpdate}){this.requestUpdate=requestUpdate;}
    detached(){this.requestUpdate=null;}
    paneViews(){return [this.view];}updateAllViews(){}
    autoscaleInfo(from,to){
      const prices=this.items.filter(f=>{const a=logicalAt(this.owner.rows,f.start,this.step),b=logicalAt(this.owner.rows,f.end,this.step);return a!=null&&b!=null&&a<=to&&b>=from;}).map(f=>f.price);
      return prices.length?{priceRange:{minValue:Math.min(...prices),maxValue:Math.max(...prices)}}:null;
    }
    paint(target){
      const o=this.owner,scale=o.chart.timeScale();
      // LWC's integer logical conversion cannot project an interval midpoint or
      // an intrabar timestamp directly (see range-chart.js coordinateAt).
      const at=index=>{const n=Math.floor(index),a=scale.logicalToCoordinate(n),b=scale.logicalToCoordinate(n+1);return a==null||b==null?null:a+(b-a)*(index-n);};
      target.useMediaCoordinateSpace(({context:ctx,mediaSize:s})=>{
        ctx.save();ctx.beginPath();ctx.rect(0,0,s.width,s.height);ctx.clip();
        ctx.font='600 12px -apple-system, BlinkMacSystemFont, "PingFang SC", sans-serif';ctx.textAlign='center';ctx.textBaseline='middle';
        const marks=[],candles=[],dark=typeof document!=='undefined'&&document.documentElement.dataset.theme==='dark';
        const spacing=Math.abs(scale.logicalToCoordinate(1)-scale.logicalToCoordinate(0)),half=Math.max(1,spacing*.4)+1;
        for(let i=0;i<o.rows.length;i++){
          const row=o.rows[i],x=scale.logicalToCoordinate(i);
          if(x==null||x+half<0||x-half>s.width||!fin(row.high)||!fin(row.low))continue;
          const high=o.series.priceToCoordinate(row.high),low=o.series.priceToCoordinate(row.low);
          if(high!=null&&low!=null)candles.push({left:x-half,right:x+half,top:Math.min(high,low),bottom:Math.max(high,low)});
        }
        for(const f of this.items){
          const a=logicalAt(o.rows,f.start,this.step),b=logicalAt(o.rows,f.end,this.step);
          if(a==null||b==null)continue;
          const x=at(a),z=at(b),y=o.series.priceToCoordinate(f.price);
          if(x==null||z==null||y==null||z<0||x>s.width)continue;
          ctx.strokeStyle=f.color;ctx.lineWidth=1.25;ctx.setLineDash([]);ctx.beginPath();ctx.moveTo(x,y);ctx.lineTo(z,y);
          // Exact fill prices/timestamps remain independent of displaced labels.
          if(f.start===f.end){ctx.moveTo(x-3,y);ctx.lineTo(x+3,y);}
          ctx.moveTo(x,y-3);ctx.lineTo(x,y+3);
          if(f.start!==f.end){ctx.moveTo(z,y-3);ctx.lineTo(z,y+3);}
          ctx.stroke();
          const anchor=at(f.start===f.end?Math.floor(a):(a+b)/2);
          if(f.label&&anchor!=null)marks.push({...f,anchor,y,width:ctx.measureText(f.label).width+8});
        }
        // Stitch 25d7e8cf6617402db2bc95aedc911e95: one hollow amber mark,
        // with plain text outside all candles and above the volume region.
        if(this.score){
          const f=this.score,anchor=scale.logicalToCoordinate(f.index),y=o.series.priceToCoordinate(o.rows[f.index].low);
          ctx.font='500 11px -apple-system, BlinkMacSystemFont, "PingFang SC", sans-serif';
          if(anchor!=null&&y!=null)marks.push({...f,anchor,y,width:ctx.measureText(f.label).width+8,maxBottom:s.height*.84});
        }
        this.labels=replayLabelLayout(marks,candles,s.width,s.height);
        for(const label of this.labels){
          const {anchor,tip,above,left,right,top,bottom}=label,dir=above?-1:1;
          const score=label.key==='score',color=score?(dark?'#d6aa65':'#8b641f'):label.key==='entry'?(dark?'#69b4ff':'#1673bb'):(dark?'#c0ccd8':'#526678');
          ctx.font=(score?'500 11px':'600 12px')+' -apple-system, BlinkMacSystemFont, "PingFang SC", sans-serif';
          ctx.fillStyle=color;ctx.strokeStyle=color;ctx.lineWidth=1;ctx.setLineDash([]);
          ctx.beginPath();ctx.moveTo(anchor,tip);
          if(score){ctx.lineTo(anchor-3,tip+dir*3);ctx.lineTo(anchor,tip+dir*6);ctx.lineTo(anchor+3,tip+dir*3);ctx.closePath();ctx.stroke();}
          else{ctx.lineTo(anchor-3,tip+dir*5);ctx.lineTo(anchor+3,tip+dir*5);ctx.closePath();ctx.fill();}
          const end=above?bottom+3:top-3,start=tip+dir*6;
          // A stacked label must not acquire a leader through another label.
          if(!this.labels.some(p=>p!==label&&anchor>=p.left&&anchor<=p.right&&Math.min(start,end)<p.bottom&&Math.max(start,end)>p.top)){
            ctx.beginPath();ctx.moveTo(anchor,start);ctx.lineTo(anchor,end);ctx.stroke();
          }
          ctx.fillText(label.label,(left+right)/2,(top+bottom)/2);
        }
        ctx.restore();
      });
    }
  }
  class TradeDrawing {
    constructor(owner){this.owner=owner;this.view={zOrder:()=> 'top',renderer:()=>({draw:target=>this.paint(target)})};}
    attached({requestUpdate}){this.requestUpdate=requestUpdate;}
    detached(){this.requestUpdate=null;}
    paneViews(){return [this.view];}updateAllViews(){}
    autoscaleInfo(from,to){const g=this.shape,p=this.trade;if(!g||to<g.entry||(g.exit>=0&&from>g.exit))return null;const values=[p.entry,p.target,p.exit_price,g.stop!=null?p.be_price:null].filter(fin);return {priceRange:{minValue:Math.min(...values),maxValue:Math.max(...values)}};}
    paint(target){
      const o=this.owner,p=this.trade,g=this.shape;if(!g)return;
      const scale=o.chart.timeScale(),x=i=>scale.logicalToCoordinate(i),y=v=>o.series.priceToCoordinate(v),entry=y(p.entry),targetY=fin(p.target)?y(p.target):null;
      if(entry==null)return;
      const at=(i,stamp)=>{const a=x(i),b=x(i+1);return a==null||b==null?null:a+(b-a)*(stamp-o.rows[i].time*1000)/this.step;};
      const a=g.entry<0?(g.from<o.rows[0].time*1000?0:null):at(g.entry,g.from),b=g.exit>=0?at(g.exit,g.to):scale.width()-12;
      if(a==null||b==null)return;
      const color=p.side===1?'#169873':'#d7355d',format=window.SpikeChartTools.axisPrice,dark=typeof document!=='undefined'&&document.documentElement.dataset.theme==='dark';
      target.useMediaCoordinateSpace(({context:ctx,mediaSize:s})=>{
        ctx.save();ctx.beginPath();ctx.rect(0,0,s.width,s.height);ctx.clip();ctx.font='11px -apple-system, BlinkMacSystemFont, "PingFang SC", sans-serif';ctx.textBaseline='middle';
        const left=Math.max(0,a),right=Math.min(s.width,b);
        const line=(value,label,tone,start=left,dash=[],finish=right)=>{const yy=y(value);if(yy==null||start>finish)return;ctx.strokeStyle=tone;ctx.lineWidth=1.4;ctx.setLineDash(dash);ctx.beginPath();ctx.moveTo(start,yy);ctx.lineTo(finish,yy);ctx.stroke();ctx.setLineDash([]);const text=label+' '+format(value),w=ctx.measureText(text).width+14;if(finish-start>w){ctx.fillStyle=dark?'#1c222cee':'#f7faf8ee';ctx.fillRect(finish-w,yy+3,w,19);ctx.fillStyle=dark?({'#169873':'#55c7a5','#d7355d':'#f57e98','#8b6ab1':'#ba97ef'}[tone]||'#e8edf4'):tone;ctx.textAlign='right';ctx.fillText(text,finish-6,yy+12);}};
        if(right>=left){if(targetY!=null){ctx.fillStyle=color;ctx.globalAlpha=.065;ctx.fillRect(left,Math.min(entry,targetY),right-left,Math.abs(entry-targetY));ctx.globalAlpha=1;line(p.target,'目标',color,left,[4,4]);}line(p.entry,p.track===9260?'买入':'开仓',color);}
        const stopIndex=g.stop!=null?candleIndex(o.rows,g.stop,this.step):-1;
        const stopX=g.stop==null?null:stopIndex>=0?at(stopIndex,g.stop):g.stop<o.rows[0].time*1000?0:null;
        if(fin(p.backstop))line(p.backstop,'确认前兜底止损','#d7355d',left,[3,4],g.stop==null?right:Math.min(right,stopX??left));
        if(stopX!=null)line(fin(p.be_price)?p.be_price:p.entry,fin(p.be_price)?'保本保护止损':'入场价止损已启用','#8b6ab1',Math.max(0,stopX),[3,4]);
        const marker=(i,value,up,tone)=>{const xx=x(i),yy=y(value);if(xx==null||yy==null||xx<0||xx>s.width)return;const dir=up?1:-1;ctx.setLineDash([]);ctx.fillStyle=tone;ctx.beginPath();ctx.moveTo(xx,yy);ctx.lineTo(xx-4,yy+dir*7);ctx.lineTo(xx+4,yy+dir*7);ctx.closePath();ctx.fill();};
        if(g.entry>=0)marker(g.entry,p.entry,p.side===1,color);
        for(const fill of p.exits||[]){if(fill.reason!=='manual'||(p.stage==='closed'&&fill===p.exits.at(-1)))continue;const i=candleIndex(o.rows,fill.exit_ms,this.step);if(i>=0)marker(i,fill.price,p.side!==1,'#617488');}
        if(g.exit>=0&&fin(p.exit_price))marker(g.exit,p.exit_price,p.side!==1,'#617488');
        // live-breadth (track 9260, Stitch 2762455ffefe4f5a9784c2e2e0fd4db5): mark the scheduled six-day exit of an open trade.
        if(p.track===9260&&p.stage!=='closed'&&fin(p.decision_ms)&&o.rows.length){const last=o.rows.length-1,xx=x(last+(p.decision_ms-o.rows[last].time*1000)/this.step);if(xx!=null&&xx>=0&&xx<=s.width){ctx.strokeStyle=dark?'#9aa8b6':'#617488';ctx.lineWidth=1;ctx.setLineDash([4,4]);ctx.beginPath();ctx.moveTo(xx,0);ctx.lineTo(xx,s.height);ctx.stroke();ctx.setLineDash([]);ctx.fillStyle=dark?'#c9d2dc':'#617488';ctx.textAlign='right';ctx.fillText('第6天平仓',xx-5,14);}}
        ctx.restore();
      });
    }
  }
  if(typeof module!=='undefined'&&module.exports){module.exports={latest,select,valid,candleIndex,geometry,TradeDrawing,replayRange,replayFills,replayScore,paperReplayTrade,paperReplayFills,logicalAt,replayLabelLayout,ReplayFillDrawing};return;}
  function create(host,notice,drawings,onReplay=()=>{}){
    let active=true,request=null,epoch=0,desired=null,anchor=null,received=0,pinnedFocus='',appearance=null;
    const shared=window.SpikePublicChartCache;
    const chart=window.SpikeMondayFibChart.create({decorate:(instance,data)=>{
      frozenCloudLines(instance,data);
      drawings?.attach(instance,data);
      const p=data.paperTrade,step=periods[data.bar],historical=data.paperHistory&&p?.stage==='closed';
      if(!instance.paperInteractionReady){
        instance.chart?.applyOptions?.({handleScale:{axisDoubleClickReset:false}});
        instance.canvas?.setAttribute('aria-label','K 线交互图；双击或双点任意图表位置隐藏／恢复悬浮组件；拖动平移，滚轮或双指缩放；Home 全图，End 最新');
        instance.paperInteractionReady=true;
      }
      const trades=new Map((historical?[]:[...(data.paperTrades||[]),...(p?[p]:[])]).filter(t=>valid(t)&&t.symbol===data.symbol).map(t=>[String(t.id),t]));
      instance.paperDrawings ||= new Map();
      for(const [id,old] of instance.paperDrawings)if(!trades.has(id)){old.shape=null;instance.series.detachPrimitive?.(old);instance.paperDrawings.delete(id);}
      for(const [id,t] of trades){let drawing=instance.paperDrawings.get(id);
        if(!drawing){drawing=new TradeDrawing(instance);instance.paperDrawings.set(id,drawing);instance.series.attachPrimitive(drawing);}
        drawing.trade=t;drawing.step=step;drawing.shape=geometry(t,instance.rows,step,Math.max(data.now_ms,p?.asof_ms||0));drawing.requestUpdate?.();
      }
      const draw=instance.paperDrawings.get(String(p?.id));instance.paperDrawing=draw||{shape:null};
      if(data.backtestTrade||historical){
        const t=historical?paperReplayTrade(p):data.backtestTrade;
        const breadth=data.backtestSource==='breadth-ema20';
        const g=historical||data.backtestSource==='live-cloud-v17'?(instance.rows.length?{from:-.5,to:instance.rows.length-.5}:null):replayRange(t,instance.rows,step,host.clientWidth<700?25:45),
          key=breadth?`breadth|${data.backtestScope||''}|${data.backtestEventId||t.event_id}|${t.symbol}|${data.bar}`:(historical?'paper-history|':'backtest|')+(data.track||desired?.track)+'|'+t.id+'|'+data.bar;
        if(!historical)onReplay(t,data.track);
        instance.series.setMarkers([]);
        if(!instance.backtestDrawing){instance.backtestDrawing=new ReplayFillDrawing(instance);instance.series.attachPrimitive(instance.backtestDrawing);}
        instance.backtestDrawing.items=historical?paperReplayFills(p):replayFills(t);instance.backtestDrawing.step=step;
        instance.backtestDrawing.score=historical||data.backtestSource==='breadth-ema20'?null:replayScore(t,instance.rows,step);instance.backtestDrawing.requestUpdate?.();
        instance.hasBacktestMarkers=true;
        if(g&&pinnedFocus!==key){pinnedFocus=key;instance.chart.priceScale('right').applyOptions({autoScale:true});instance.chart.timeScale().setVisibleLogicalRange({from:g.from,to:g.to});}
        notice(!g?'开仓区间的原始 K 线缺失；请切换周期或重新加载。':!instance.backtestDrawing.items.length?'原始成交价暂不可读，未绘制成交标记。':'');return;
      }
      if(instance.hasBacktestMarkers){instance.series.setMarkers([]);instance.hasBacktestMarkers=false;}
      if(instance.backtestDrawing){instance.backtestDrawing.items=[];instance.series.detachPrimitive?.(instance.backtestDrawing);instance.backtestDrawing=null;}
      if(!p){notice('');return;}
      const focusKey=p.id+'|'+data.bar;
      if(draw.shape&&pinnedFocus!==focusKey){pinnedFocus=focusKey;
        if(draw.shape.entry>=0){const i=draw.shape.entry,span=host.clientWidth<700?55:115;
          const range={from:i-span*.7,to:i+span*.3};
          instance.chart.priceScale('right').applyOptions({autoScale:true});instance.chart.timeScale().setVisibleLogicalRange({from:range.from,to:range.to});}
      }
      notice(!draw.shape||draw.shape.entry===-1?'开仓时间不在当前已加载 K 线内；可切换更大周期查看。':'');
    }});
    function paint(){if(active&&anchor&&desired)chart.show(host,anchor,anchor.current_week||anchor.week,{period:desired.period,ma:desired.ma,internal:desired.internal,trade:desired.trade,trades:desired.trades,anchors:anchor});}
    function showReplay(track,trade,period='15m',ma=false,internal=false,paper=false,replayOptions={}){
      const mode=paper?'history':'backtest';
      if(desired?.mode!==mode){epoch++;request?.abort();request=null;anchor=null;chart.pause();pinnedFocus='';}
      desired={mode,track,trade,period,ma,internal,paper,replayOptions};
      if(active)chart.showReplay(host,track,trade,{period,ma,internal,paper,replayOptions,refocus:!pinnedFocus});
    }
    async function show(trade,period='5m',ma=false,symbol=trade?.symbol||'',internal=false,trades=[]){
      const changed=desired?.symbol!==symbol;desired={trade:valid(trade)&&trade.symbol===symbol?trade:null,period,ma,symbol,internal,trades};
      if(changed){epoch++;request?.abort();request=null;anchor=null;chart.hide();pinnedFocus='';}
      if(!active)return;
      if(!/^[A-Z0-9]{1,32}-USDT-SWAP$/.test(symbol)){chart.hide();notice('尚无资金交易，可从监控币种选择 K 线。');return;}
      const levelKey=shared?symbol+'|'+shared.weekAt(Date.now()):'';
      if(!anchor&&shared){anchor=shared.levels.get(levelKey)||null;const generation=epoch;
        if(!anchor)shared.restoreLevels(levelKey).then(a=>{if(a&&active&&epoch===generation&&!anchor){anchor=a;paint();}});}
      if(anchor)paint();
      if(request||(anchor&&Date.now()-received<60000))return;
      const revision=epoch,c=new AbortController();request=c;const timeout=setTimeout(()=>c.abort(),15000);
      if(!anchor)notice('正在加载 '+symbol.replace('-USDT-SWAP','')+' 的 1.618 K 线…');
      try{const warming=window.SpikeStartup?.take('levels:'+symbol,c.signal),warm=warming?await warming:null;let a=warm?.value;
        if(!a){const r=await fetch('/api/monday-fib/levels?symbol='+encodeURIComponent(symbol),{signal:c.signal,cache:'no-store'});if(!r.ok)throw Error('周一区间读取失败');a=await r.json();}if(revision!==epoch||!active)return;
        if(a.symbol!==symbol||a.timezone!=='America/New_York'||!a.week)throw Error('周一区间币种或时间不匹配');
        anchor=a;received=Date.now();if(shared?.saveLevels){shared.saveLevels(a);if(a.state==='ready'){shared.levels.set(symbol+'|'+a.week,a);while(shared.levels.size>32)shared.levels.delete(shared.levels.keys().next().value);}}notice('');paint();
      }catch(e){if(revision===epoch&&active)notice((e.name==='AbortError'?'图表读取超时':e.message)+' · 点击刷新重试');}
      finally{clearTimeout(timeout);if(request===c)request=null;}
    }
    function setAppearance(value){appearance=value;chart.setAppearance?.(appearance);}
    return {show,showReplay,showHistory:(track,trade,period='15m',ma=false,internal=false)=>showReplay(track,trade,period,ma,internal,true),setAppearance,fit:chart.fit,latest:chart.latest,zoom:chart.zoom,retry(){if(desired?.mode){chart.retry();return;}received=0;if(desired)show(desired.trade,desired.period,desired.ma,desired.symbol,desired.internal,desired.trades);},
      setActive(value){active=value;if(!value){drawings?.cancel();epoch++;request?.abort();request=null;chart.pause();}else if(desired?.mode)showReplay(desired.track,desired.trade,desired.period,desired.ma,desired.internal,desired.paper,desired.replayOptions);else if(desired)show(desired.trade,desired.period,desired.ma,desired.symbol,desired.internal,desired.trades);},
      resetFocus(){pinnedFocus='';},destroy(){active=false;epoch++;request?.abort();chart.hide();}};
  }
  window.SpikeReclaimChart={create,latest,select,valid,paperReplayTrade};
})();
