/* Real OHLC chart adapter; frozen signal references never become orders.
 * Shared LWC renderer/gestures: range-chart.js, Monday history and live feed.
 * Primitive API: tradingview.github.io/lightweight-charts/docs/4.2/plugins/series-primitives
 */
(() => {
  'use strict';
  const STEPS={'5m':300000,'15m':900000,'1H':3600000,'4H':14400000,'12H':43200000,'1D':86400000};
  function averages(rows,step,signal,period,history=[]){
    const anchors=new Map(history.filter(s=>!s.context_only).map(s=>[s.signal_open_ms,s]));if(!signal.context_only)anchors.set(signal.signal_open_ms,signal);
    let a,b,last;return rows.map(r=>{
      if(last==null||r.time-last!==step/1000)a=b=r.close;
      a+=(r.close-a)*2/21;b+=(r.close-b)*2/51;last=r.time;
      // The saved confirmation is authoritative, even if displayed history has
      // a shorter warmup prefix. Continue its recurrence forward from this bar.
      const saved=anchors.get(r.time*1000);
      if(period==='12H'&&saved){a=saved.ema20;b=saved.ema50;}
      return {time:r.time,a,b};
    });
  }
  function indexAt(rows,ms,step){
    if(!rows.length)return null;const t=ms/1000,n=rows.length;
    if(t<=rows[0].time)return (t-rows[0].time)/(step/1000);
    if(t>=rows[n-1].time)return n-1+(t-rows[n-1].time)/(step/1000);
    let a=0,b=n-1;while(b-a>1){const m=(a+b)>>1;if(rows[m].time<=t)a=m;else b=m;}
    return a+(t-rows[a].time)/(rows[b].time-rows[a].time);
  }
  function xAt(scale,index){const a=Math.floor(index),x=scale.logicalToCoordinate(a),y=scale.logicalToCoordinate(a+1);return x==null||y==null?null:x+(y-x)*(index-a);}
  const keyCandleTime=(signal,period)=>Math.floor((signal.confirmed_ms-1)/STEPS[period])*STEPS[period]/1000;
  // One symbol/week across both sides; mixed saved/reconstructed responses can
  // arrive out of order. A stale selected ID must not put a later signal back.
  function weeklyPlans(rows){
    const first=new Map();
    for(const s of [...rows].sort((a,b)=>a.confirmed_ms-b.confirmed_ms||a.id.localeCompare(b.id))){
      const week=s.week||s.range?.week,key=s.symbol+'|'+(week||s.id);
      if(!first.has(key))first.set(key,s);
    }
    return [...first.values()];
  }
  function keyCandleTimes(selected,history,period){
    return new Set(weeklyPlans([selected,...history].filter(s=>!s.context_only&&s.symbol===selected.symbol&&Number.isFinite(s.confirmed_ms))).map(s=>keyCandleTime(s,period)));
  }
  function levels(signal,dark=false,historical=false){
    const r=signal.range;
    return [['周一高',r.high,'range'],['周一低',r.low,'range'],['上 1.618',r.upper,'fib'],['下 1.618',r.lower,'fib']].map(([label,price,kind])=>({label,price,kind,color:kind==='range'?'#9399A2':dark?'#E3E7EC':'#17191D',lineWidth:1,lineStyle:0,historical,
      start_ms:r.end_ms,end_ms:r.expires_ms,startMarkerVisible:false,inlineNameVisible:false,inlinePriceVisible:false,axisLabelVisible:false}));
  }
  function historicalLevels(signal,weeks=[],dark=false){
    const key=r=>r.start_ms??r.end_ms,ranges=new Map(weeks.map(r=>[key(r),r]));
    // A refreshed week must never rewrite the selected signal's saved plan.
    if(signal.range)ranges.set(key(signal.range),signal.range);
    return [...ranges.values()].flatMap(r=>levels({range:r},dark,r!==signal.range));
  }
  function cloudSegments(points,step){
    const groups=[];let part=[];
    const flush=()=>{if(part.length>1)groups.push(part);part=[];};
    for(const p of points){const prev=part.at(-1);
      if(prev&&p.time-prev.time!==step/1000){flush();}
      else if(prev&&(prev.a-prev.b)*(p.a-p.b)<0){
        const ratio=(prev.a-prev.b)/(prev.a-prev.b-p.a+p.b),value=prev.a+(p.a-prev.a)*ratio;
        const cross={time:prev.time+(p.time-prev.time)*ratio,a:value,b:value};part.push(cross);flush();part.push(cross);
      }
      part.push(p);
    }flush();return groups;
  }
  function visibleCloudPoints(points,range){
    if(!points.length||!range||!Number.isFinite(range.from)||!Number.isFinite(range.to))return points;
    const low=Math.min(range.from,range.to),high=Math.max(range.from,range.to);
    const start=Math.max(0,Math.floor(low)-1),end=Math.min(points.length,Math.ceil(high)+2);
    if(start>=end)return [];
    return start===0&&end===points.length?points:points.slice(start,end);
  }
  function signalChartWindowEnd(signal,period,now=Date.now()){
    const step=STEPS[period];if(!step||!Number.isFinite(signal?.confirmed_ms)||!Number.isFinite(now))return null;
    const horizon=Math.floor((signal.confirmed_ms-1)/step)*step+81*step;
    return Math.min(horizon,Math.floor(now/step)*step);
  }
  function isFreshSignalChart(signal,period,value,now=Date.now()){
    const end=signalChartWindowEnd(signal,period,now);
    return end!=null&&value?.symbol===signal.symbol&&value?.bar===period&&value.last_close_ms===end;
  }
  function planWindows(selected,history=[],latestId=null){
    const unique=new Map(history.filter(s=>!s.context_only&&s.symbol===selected.symbol).map(s=>[s.id,s]));
    if(!selected.context_only&&!unique.has(selected.id))unique.set(selected.id,selected);
    const ordered=weeklyPlans([...unique.values()]);
    const latest=ordered.some(s=>s.id===latestId)?latestId:ordered.at(-1)?.id;
    return ordered.map((s,i)=>({...s,start_ms:s.confirmed_ms,
      end_ms:Math.min(s.confirmed_ms+12*STEPS['12H'],ordered[i+1]?.confirmed_ms??Infinity),
      latest:s.id===latest,selected:s.id===selected.id})).filter(s=>
      [s.entry,s.stop,s.target,s.planned_rr].every(Number.isFinite)&&
      (s.entry-s.stop)*s.side>0&&(s.target-s.entry)*s.side>0&&s.end_ms>s.start_ms);
  }
  function planPalette(latest,dark){
    return {reward:`rgba(16,185,129,${dark?.28:.23})`,
      risk:`rgba(244,63,94,${dark?.30:.27})`,
      target:dark?'#23E6AD':'#009B6B',stop:dark?'#FF6B8B':'#F43F5E'};
  }
  // Descriptive indicator path, not a fill engine or realized return. Start
  // after the confirmed signal bar and stop at the first intrabar EMA cross or
  // fixed stop. OHLC cannot establish exact event time or same-bar MFE order.
  function planExcursion(s,rows,closedMs){
    const risk=(s.entry-s.stop)*s.side,step=STEPS['12H'];
    if(![-1,1].includes(s.side)||!(risk>0)||!Number.isFinite(s.ema20)||!Number.isFinite(s.ema50))return null;
    const a20=2/21,a50=2/51,spreadSlope=a20-a50;
    let next=s.confirmed_ms,lo=0,hi=0,peak=s.entry,state='unavailable',stopMs=null,exitState=null,cloudFlipMs=null;
    let exitBarMs=null,exitBarEndMs=null,exitTimeMs=null,exitPrice=null,exitR=null,exitAtOpen=null,exitTimePrecision=null,exitBarConfirmed=null;
    let triggerLevel=null;
    let ema20=s.ema20,ema50=s.ema50;
    const emaAt=price=>({ema20:ema20+(price-ema20)*a20,ema50:ema50+(price-ema50)*a50});
    const crossedAt=price=>{
      const values=emaAt(price),spread=values.ema20-values.ema50;
      const tolerance=Number.EPSILON*Math.max(1,Math.abs(values.ema20),Math.abs(values.ema50))*8;
      return s.side===1?spread < -tolerance:spread > tolerance;
    };
    const crossPrice=()=>(((1-a50)*ema50-(1-a20)*ema20)/spreadSlope);
    const stopAt=price=>s.side===1?price<=s.stop:price>=s.stop;
    const favorableR=price=>Math.max(0,(price-s.entry)*s.side/risk);
    // All evidence windows are merged by timestamp. Jump to the confirmation
    // candle before walking the path, including the gap check on the first bar.
    let low=0,high=rows.length;
    while(low<high){
      const mid=Math.floor((low+high)/2);
      if(rows[mid].time<next)low=mid+1;else high=mid;
    }
    for(let i=low;i<rows.length;i++){
      const r=rows[i];
      if(r.time!==next){state=state==='unavailable'?'unavailable':'gap';break;}
      if(![r.open,r.high,r.low,r.close].every(Number.isFinite)||r.low<=0||r.high<Math.max(r.open,r.close,r.low)||r.low>Math.min(r.open,r.close)){state='gap';break;}
      const live=r.confirmed===false,closed=r.confirmed===true||(!live&&r.time+step<=closedMs);
      if(!closed&&!live)break;
      state='open';const threshold=crossPrice(),favorable=s.side===1?r.high:r.low;
      const crossesOpen=crossedAt(r.open),stopsOpen=stopAt(r.open);
      const crossedWithin=s.side===1?crossedAt(r.low):crossedAt(r.high);
      const stoppedWithin=s.side===1?r.low<=s.stop:r.high>=s.stop;
      // A gap that opens beyond both boundaries is conservatively attributed
      // to the fixed stop. Its descriptive trigger price is the actual open.
      const eventAtOpen=stopsOpen?'stop':crossesOpen?'cloud_flip':null;
      let eventWithin=null;
      if(!eventAtOpen){
        if(crossedWithin&&!stoppedWithin)eventWithin='cloud_flip';
        else if(stoppedWithin&&!crossedWithin)eventWithin='stop';
        else if(crossedWithin&&stoppedWithin){
          // The open is on the safe side of both levels; choose the first one
          // on the adverse move (higher for a long, lower for a short).
          eventWithin=s.side===1?(threshold>s.stop?'cloud_flip':'stop'):
            (threshold<s.stop?'cloud_flip':'stop');
        }
      }
      if(eventAtOpen||eventWithin){
        const atOpen=!!eventAtOpen,kind=eventAtOpen||eventWithin;
        exitBarMs=r.time;exitBarEndMs=r.time+step;exitBarConfirmed=closed;exitAtOpen=atOpen;
        exitTimeMs=atOpen?r.time:null;exitTimePrecision=atOpen?'bar_open':'within_12H_bar';
        triggerLevel=kind==='cloud_flip'?threshold:s.stop;
        exitPrice=atOpen?r.open:triggerLevel;exitR=(exitPrice-s.entry)*s.side/risk;
        if(kind==='stop'){stopMs=r.time;state='stopped';exitState='stop';}
        else{cloudFlipMs=r.time;state='cloud_flipped';exitState='cloud_flip';}
        // The open is known to precede later wicks. For an intrabar exit, only
        // the open MFE is certain; the full exit-bar wick is an upper bound.
        const beforeOpen=lo;lo=Math.max(lo,favorableR(r.open));
        if(lo>beforeOpen)peak=s.entry+s.side*lo*risk;
        hi=atOpen?lo:Math.max(lo,favorableR(favorable));
        // Clip drawings at the bar open; keep its full interval separately.
        next=r.time;break;
      }
      const excursion=favorableR(favorable);
      if(excursion>lo){lo=excursion;peak=favorable;}hi=lo;
      if(live){next=r.time;break;}
      next=r.time+step;ema20+=(r.close-ema20)*a20;ema50+=(r.close-ema50)*a50;
    }
    if(state==='unavailable')return null;
    return {state,exit_state:exitState,lowerR:lo,upperR:hi,peak:s.entry+s.side*lo*risk,through_ms:next,stop_ms:stopMs,cloud_flip_ms:cloudFlipMs,
      exit_bar_ms:exitBarMs,exit_bar_end_ms:exitBarEndMs,exit_time_ms:exitTimeMs,exit_price:exitPrice,exit_r:exitR,
      exit_at_open:exitAtOpen,exit_time_precision:exitTimePrecision,exit_bar_confirmed:exitBarConfirmed,trigger_level:triggerLevel,
      ambiguous:hi-lo>1e-8,observedPeak:s.entry+s.side*hi*risk};
  }
  function excursionLabel(path){
    if(!path)return '—';
    const r=path.ambiguous?path.lowerR.toFixed(2)+'–'+path.upperR.toFixed(2):path.lowerR.toFixed(2);
    return r+'R';
  }
  function rLadder(s,path){
    const risk=(s.entry-s.stop)*s.side,extent=Math.max(s.planned_rr,path?.lowerR||0);
    return [5,10].filter(r=>r<=extent+1e-8).map(r=>({r,price:s.entry+s.side*r*risk}));
  }
  // Stitch 450641ea68c2402ab07b8df5cb607bf3: tiny bare R values only.
  // Price/target/stop details stay in the disclosure; the risk fill stays visible.
  function planMarks(s,path,y,palette){
    const marks=rLadder(s,path).filter(level=>!path||path.ambiguous||Math.abs(level.r-path.lowerR)>=.08)
      .map(level=>({kind:'r',p:y(level.price),color:palette.target,label:level.r+'R'}));
    if(path&&(path.lowerR>0||path.ambiguous)){
      const number=v=>String(Number(v.toFixed(2)));
      const label=(path.ambiguous?number(path.lowerR)+'–'+number(path.upperR):number(path.lowerR))+'R';
      marks.push({kind:'peak',p:y(path.peak),color:palette.target,label});
    }
    return marks;
  }
  function mergePlans(stored,reconstructed){
    const rows=new Map(reconstructed.map(s=>[s.id,s]));for(const s of stored)rows.set(s.id,s);
    return weeklyPlans([...rows.values()]);
  }
  function createPlanHistory({symbol,rangeMode='fib',request=(url,options)=>fetch(url,options),changed=()=>{},status=()=>{},now=Date.now,
    completedCache=new Map(),cacheTtlMs=300000,cacheLimit=64}={}){
    const weeks=new Set(),complete=new Set(),retryAt=new Map(),results=new Map();let active=null,paused=true;
    const cacheKey=week=>JSON.stringify([symbol,week,rangeMode]);
    function cachedComplete(week){
      const key=cacheKey(week),entry=completedCache.get(key);if(!entry)return null;
      const age=now()-entry.saved_ms;
      if(!Number.isFinite(age)||age<0||age>=cacheTtlMs){completedCache.delete(key);return null;}
      completedCache.delete(key);completedCache.set(key,entry);return entry;
    }
    function rememberComplete(week,items){
      const key=cacheKey(week),entry={saved_ms:now(),items:items.map(item=>({...item}))};
      completedCache.delete(key);completedCache.set(key,entry);
      while(completedCache.size>cacheLimit)completedCache.delete(completedCache.keys().next().value);
    }
    async function pump(){
      if(paused||active)return;
      const c=new AbortController();active=c;
      try{while(!paused&&active===c){
        const week=[...weeks].find(w=>!complete.has(w)&&(retryAt.get(w)||0)<=now());if(!week)break;
        const timeout=setTimeout(()=>c.abort(),20000);
        try{
          const response=await request('/api/cloud-signals/history?'+new URLSearchParams({symbol,week,range_mode:rangeMode}),{signal:c.signal,cache:'no-store'});
          if(paused||active!==c)return;
          if(!response.ok)throw Error('历史参考框暂不可用，请重试');
          const value=await response.json();if(paused||active!==c)return;
          if(value.symbol!==symbol||value.week!==week||value.state!=='ready'||!Array.isArray(value.items)||value.items.some(s=>s.symbol!==symbol||!s.id||!Number.isFinite(s.confirmed_ms)))throw Error('历史参考框数据不完整');
          results.set(week,value.items);if(value.complete){complete.add(week);rememberComplete(week,value.items);}else retryAt.set(week,now()+30000);
          changed([...results.values()].flat());status('');
        }catch(e){if(!paused&&active===c){retryAt.set(week,now()+30000);status(e.name==='AbortError'?'历史参考框读取超时，请重试':e.message);}break;}
        finally{clearTimeout(timeout);}
      }}finally{if(active===c)active=null;}
    }
    return {observe(values){let restored=false;for(const w of values||[])if(w.symbol===symbol&&w.state==='ready'){
        weeks.add(w.week);if(!complete.has(w.week)){const entry=cachedComplete(w.week);if(entry){results.set(w.week,entry.items.map(item=>({...item})));complete.add(w.week);restored=true;}}
      }
      if(restored)changed([...results.values()].flat());return pump();},
      resume(){paused=false;return pump();},pause(){paused=true;active?.abort();active=null;},
      retry(){retryAt.clear();return pump();}};
  }
  if(typeof module!=='undefined')module.exports={averages,indexAt,levels,historicalLevels,keyCandleTime,keyCandleTimes,cloudSegments,visibleCloudPoints,signalChartWindowEnd,isFreshSignalChart,weeklyPlans,planWindows,planPalette,planMarks,planExcursion,excursionLabel,rLadder,mergePlans,createPlanHistory,STEPS};
  if(typeof window==='undefined')return;
  const price=v=>window.SpikeMondayList.price(v);
  class Drawing {
    constructor(owner){this.owner=owner;this.views=[false,true].map(labelsOnly=>({zOrder:()=>labelsOnly?'top':'bottom',renderer:()=>({draw:target=>this.paint(target,labelsOnly)})}));}
    attached({requestUpdate}){this.requestUpdate=requestUpdate;}
    detached(){this.requestUpdate=null;}
    paneViews(){return this.views;}
    update(signal,period,layers,history,latestId,pathRows,pathClosed,pathFor=planExcursion){
      this.signal=signal;this.step=STEPS[period];this.layers=layers;
      this.points=averages(this.owner.rows,this.step,signal,period,history);
      this.updatePath(signal,history,latestId,pathRows,pathClosed,pathFor);
    }
    updatePath(signal,history,latestId,pathRows,pathClosed,pathFor=planExcursion){
      this.plans=planWindows(signal,history,latestId).map(plan=>{
        const path=pathFor(plan,pathRows,pathClosed);
        const end=path?.exit_state?Math.min(plan.end_ms,path.through_ms):plan.end_ms;
        return {...plan,path,end_ms:end,startIndex:indexAt(this.owner.rows,plan.start_ms,this.step),
          endIndex:indexAt(this.owner.rows,end,this.step)};
      });
      this.requestUpdate?.();
    }
    autoscaleInfo(from,to){
      if(!this.signal)return null;
      let min=Infinity,max=-Infinity;
      if(this.layers.cloud){
        const first=Math.max(0,Math.floor(from)),last=Math.min(this.points.length-1,Math.ceil(to));
        for(let i=first;i<=last;i++){
          const p=this.points[i];min=Math.min(min,p.a,p.b);max=Math.max(max,p.a,p.b);
        }
      }
      if(this.layers.plan)for(const p of this.plans){
        if(p.startIndex>to||p.endIndex<from)continue;
        const peak=p.path?.peak??p.entry;
        min=Math.min(min,p.entry,p.stop,p.target,peak);max=Math.max(max,p.entry,p.stop,p.target,peak);
      }
      return min<=max?{priceRange:{minValue:min,maxValue:max}}:null;
    }
    paint(target,labelsOnly=false){
      if(!this.signal||!this.owner.rows.length)return;
      const o=this.owner,s=this.signal,scale=o.chart.timeScale(),y=v=>o.series.priceToCoordinate(v);
      target.useMediaCoordinateSpace(({context:c,mediaSize:{width:w,height:h}})=>{
        c.save();c.beginPath();c.rect(0,0,w,h);c.clip();
        const dark=document.documentElement.dataset.theme==='dark';
        const range=scale.getVisibleLogicalRange?.();
        if(!labelsOnly&&this.layers.cloud)for(const group of cloudSegments(visibleCloudPoints(this.points,range),this.step)){
          const points=group.map(p=>({x:xAt(scale,indexAt(o.rows,p.time*1000,this.step)),a:y(p.a),b:y(p.b)})).filter(p=>p.x!=null&&p.a!=null&&p.b!=null);
          if(points.length<2||points.at(-1).x<0||points[0].x>w)continue;
          const bullish=group.some(p=>p.a>p.b);
          c.fillStyle=bullish?(dark?'rgba(48,209,158,.32)':'rgba(16,185,129,.26)'):(dark?'rgba(255,99,125,.30)':'rgba(244,63,94,.23)');
          c.beginPath();points.forEach((p,i)=>i?c.lineTo(p.x,p.a):c.moveTo(p.x,p.a));
          for(let i=points.length-1;i>=0;i--)c.lineTo(points[i].x,points[i].b);c.closePath();c.fill();
        }
        const labels=[];
        if(this.layers.plan)for(const plan of this.plans){
          // Panning changes pixel coordinates, but a plan's logical range is
          // stable until its evidence or the underlying candle window changes.
          if(range&&(plan.endIndex<range.from-1||plan.startIndex>range.to+1))continue;
          const left=xAt(scale,plan.startIndex),right=xAt(scale,plan.endIndex);
          const extent=plan.side===1?Math.max(plan.target,plan.path?.peak??plan.entry):Math.min(plan.target,plan.path?.peak??plan.entry);
          const e=y(plan.entry),stop=y(plan.stop),tp=y(extent),palette=planPalette(plan.latest,dark);
          if([left,right,e,stop,tp].every(v=>v!=null)&&right>0&&left<w){
            const gutter=Math.min(1.5,Math.max(0,(right-left)/8));
            const l=Math.max(0,left+gutter),r=Math.min(w-8,right-gutter),width=Math.max(0,r-l);
            if(!labelsOnly){c.fillStyle=palette.reward;c.fillRect(l,Math.min(e,tp),width,Math.abs(e-tp));
            // Every plan has one paired risk rectangle with the exact same
            // horizontal bounds. No ATR band, extra steps or entry stroke.
            c.fillStyle=palette.risk;c.fillRect(l,Math.min(e,stop),width,Math.abs(e-stop));}
            for(const m of planMarks(plan,plan.path,y,palette)){
              if(!labelsOnly&&m.p>0&&m.p<h){c.strokeStyle=m.color;c.lineWidth=.8;c.setLineDash([]);c.beginPath();c.moveTo(Math.max(l,r-8),m.p);c.lineTo(r,m.p);c.stroke();}
              else if(labelsOnly&&(m.kind==='peak'||width>24)&&m.p>8&&m.p<h-12)labels.push({...m,l,r,priority:({peak:8,r:1}[m.kind])+(plan.latest?2:plan.selected?1:0)});
            }
          }
        }
        // Text uses its own top pane view, above candles and every fill.
        // Bare one-line R values; no pill backgrounds or prose over candles.
        c.font='400 9px -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif';c.textBaseline='middle';c.textAlign='left';
        const occupied=[];
        for(const label of labels.sort((a,b)=>b.priority-a.priority||b.r-a.r)){
          const width=c.measureText(label.label).width+4,height=11;
          const base=Math.max(6,Math.min(w-width-6,label.r-width-4));
          let placement=null;
          for(const x of label.kind==='r'?[base,Math.max(6,base-70),Math.max(6,label.l+4)]:[base]){
            const positions=label.kind==='r'?[label.p-height/2]:[label.p-height-4,label.p+4];
            const top=positions.find(y=>y>=5&&y+height<=h-5&&!occupied.some(b=>x<b.x+b.width+3&&x+width+3>b.x&&y<b.y+b.height+3&&y+height+3>b.y));
            if(top!=null){placement={x,top};break;}
          }
          if(!placement)continue;const {x,top}=placement;
          occupied.push({x,y:top,width,height});
          c.fillStyle=label.color;c.fillText(label.label,x+2,top+height/2);
        }
        c.restore();
      });
    }
  }
  function create(host,notice,onExcursion=()=>{},hooks={}){
    let signal=null,period='12H',data=null,instance=null,controller=null,planController=null,feed=null,pathFeed=null,pathController=null,pathContext=null,history=null,version=0,mode='signal',pendingFocus=true,plans=[],latestId=null,storedPlans=[],reconstructedPlans=[],replay=null,candleError='',planError='',reconstructionSupported=false;
    const signalChartCache=new Map(),signalChartCacheLimit=8,completedWeekCache=new Map();
    let layers={cloud:true,range:true,fib:true,plan:true};
    let rangeMode='fib',bulk=null,bulkPlans=[],bulkStatus={complete:false,running:false};
    const exitLatches=new Map();
    const stops=window.SpikeCloudStopSettings;let stopSettings=stops.normalize();
    function excursionKey(s){return JSON.stringify([s.id,s.symbol,s.side,s.entry,s.stop,s.stop_pct,stopSettings.btcEth,stopSettings.other]);}
    function pathFor(s,rows,closedMs){
      const key=excursionKey(s),current=planExcursion(s,rows,closedMs);
      if(exitLatches.has(key)){
        const locked=exitLatches.get(key);
        // Keep the first observed forming-bar MFE bounds even after later live
        // snapshots or the confirmed candle report a more favorable extreme.
        // The confirmed flag may advance once the same exit bar is closed.
        if(current?.exit_state&&current.exit_bar_ms===locked.exit_bar_ms){
          if(current.exit_bar_confirmed)locked.exit_bar_confirmed=true;
        }
        return {...locked};
      }
      if(current?.exit_state&&current.exit_bar_confirmed===false)exitLatches.set(key,{...current});
      return current;
    }
    // Keep a canonical 12H evidence window across display-period switches.
    // The current forming 12H candle is retained only for immediate path checks.
    let pathRows=[],pathClosed=0;
    function currentReference(){
      if(!signal)return null;
      if(!signal.context_only&&(signal.range_mode||'fib')===rangeMode)return signal;
      const sameWeek=plans.find(s=>s.week===signal.week);
      return (signal.context_only?plans.at(-1):sameWeek)||{...signal,context_only:true,range:null};
    }
    let lastStatsInputs=[];
    function refreshStats(){
      // Lower-timeframe quote ticks redraw candles but do not change the
      // canonical 12H evidence or the reference plans used by the statistics.
      const inputs=[plans,pathRows,pathClosed,stopSettings,bulkStatus,rangeMode,signal];
      if(inputs.every((value,i)=>value===lastStatsInputs[i]))return;
      lastStatsInputs=inputs;
      const rows=plans.map(s=>stops.project(s,stopSettings));
      const coverage={...bulkStatus,range_mode:rangeMode,from_ms:bulkStatus.from_ms??pathRows[0]?.time,to_ms:bulkStatus.to_ms??pathClosed};
      hooks.stats?.(window.SpikeCloudHistoryStats.summarize(rows,{pathFor:s=>pathFor(s,pathRows,pathClosed),coverage}),coverage);
      hooks.reference?.(currentReference());
    }
    function rebuildPlans(){plans=mergePlans(storedPlans,[...reconstructedPlans,...bulkPlans]);latestId=plans.at(-1)?.id;}
    function setupHistory(){
      const identity=signal,selectedMode=rangeMode;replay?.pause();bulk?.cancel();bulkPlans=[];bulkStatus={complete:false,running:false,stage:'idle',from_ms:null,to_ms:null,weeksDone:null,weeksTotal:null,failedWeeks:0,error:''};
      replay=createPlanHistory({symbol:identity.symbol,rangeMode,completedCache:completedWeekCache,changed:rows=>{if(signal!==identity||rangeMode!==selectedMode)return;reconstructedPlans=rows;rebuildPlans();draw();},status:message});
      bulk=window.SpikeCloudHistoryLoader.create({symbol:identity.symbol,rangeMode,changed:value=>{
        if(signal!==identity||rangeMode!==selectedMode)return;mergePathCandles(value);bulkPlans=value.items;rebuildPlans();draw();
      },status:value=>{if(signal!==identity||rangeMode!==selectedMode)return;bulkStatus=value;refreshStats();}});
    }
    let palette=window.SpikeCloudCandleColors.normalize(),candleColor=window.SpikeCloudCandleColors.colorizer(palette);
    const message=t=>{notice.querySelector('span').textContent=t;notice.hidden=!t;};
    function mergePathCandles(value,liveNow){
      if(value?.bar!=='12H')return;
      const closedThrough=Number.isFinite(value.last_close_ms)?value.last_close_ms:0;
      const currentOpen=Number.isFinite(liveNow)?Math.floor(liveNow/STEPS['12H'])*STEPS['12H']:closedThrough;
      pathClosed=Math.max(pathClosed,closedThrough);
      const rows=new Map(pathRows.map(r=>[r.time,r]));
      for(const r of value.candles||[]){
        const closed=r.confirmed===true||(r.confirmed!==false&&r.time+STEPS['12H']<=closedThrough);
        const forming=r.confirmed===false&&r.time===currentOpen;
        if(!closed&&!forming)continue;
        rows.set(r.time,r);if(closed)pathClosed=Math.max(pathClosed,r.time+STEPS['12H']);
      }
      pathRows=[...rows.values()].sort((a,b)=>a.time-b.time);
    }
    function refreshPath(){
      if(!signal)return;
      const viewSignal=stops.project(currentReference(),stopSettings),viewPlans=plans.map(s=>stops.project(s,stopSettings));
      onExcursion(viewSignal.plan_state==='ready'?pathFor(viewSignal,pathRows,pathClosed):null);
      instance?.cloudDrawing?.updatePath(viewSignal,viewPlans,latestId,pathRows,pathClosed,pathFor);
      refreshStats();
    }
    function draw(){
      if(!data||!signal)return;
      const ref=currentReference(),viewSignal=stops.project(ref,stopSettings),viewPlans=plans.map(s=>stops.project(s,stopSettings));
      onExcursion(viewSignal.plan_state==='ready'?pathFor(viewSignal,pathRows,pathClosed):null);
      const anchor=ref.range;
      const lines=historicalLevels(ref,data.weekly_ranges,document.documentElement.dataset.theme==='dark');
      const value={...data,levels:lines,level_window:{start_ms:Math.min(...lines.map(l=>l.start_ms)),end_ms:Math.max(...lines.map(l=>l.end_ms))},monday:{...anchor,complete:true}};
      const keyTimes=keyCandleTimes(ref,plans,period);
      instance=window.SpikeRangeChart.mount(host,value,{bars:Infinity,initialBars:100,range:layers.range,fib:layers.fib,ma:false,wallpaper:true,mondayBand:false,showLevels:false,candleColors:palette,candleColor:r=>keyTimes.has(r.time)?'#0A84FF':candleColor(r),onVisibleRange:o=>history?.observe(o),
        decorate:o=>{instance=o;if(!o.cloudDrawing){o.chart.priceScale('right').applyOptions({scaleMargins:{top:.09,bottom:.13}});o.cloudDrawing=new Drawing(o);o.series.attachPrimitive(o.cloudDrawing);}o.cloudDrawing.update(viewSignal,period,layers,viewPlans,latestId,pathRows,pathClosed,pathFor);if(pendingFocus&&o.rows.length){pendingFocus=false;focus(mode);}}});
      refreshStats();
    }
    function focus(which='signal'){
      if(!instance?.chart||!instance.rows.length)return;
      const scale=instance.chart.timeScale(),index=indexAt(instance.rows,signal.confirmed_ms,STEPS[period]);
      const end=which==='latest'?instance.rows.length-1+8:index+16,span=host.clientWidth<650?42:period==='12H'?60:80;
      instance.chart.priceScale('right').applyOptions({autoScale:true});scale.setVisibleLogicalRange({from:end-span,to:end});
    }
    function stopRequests(){version++;controller?.abort();controller=null;planController?.abort();planController=null;pathController?.abort();pathController=null;feed?.stop();feed=null;pathFeed?.stop();pathFeed=null;history?.pause();history=null;replay?.pause();bulk?.cancel();}
    async function readPlans(current,c){
      const response=await fetch('/api/cloud-signals/history?'+new URLSearchParams({symbol:current.symbol,range_mode:rangeMode}),{signal:c.signal,cache:'no-store'});
      if(!response.ok)throw Error('历史参考框读取失败，请重试');
      const value=await response.json();if(value.symbol!==current.symbol||!Array.isArray(value.items))throw Error('历史参考框数据不匹配');return value;
    }
    function rememberSignalChart(key,value){
      signalChartCache.delete(key);signalChartCache.set(key,value);
      while(signalChartCache.size>signalChartCacheLimit)signalChartCache.delete(signalChartCache.keys().next().value);
    }
    function cachedSignalChart(current,tf){
      const key=current.id+'|'+tf,value=signalChartCache.get(key);
      if(!value)return null;
      if(!isFreshSignalChart(current,tf,value)){signalChartCache.delete(key);return null;}
      signalChartCache.delete(key);signalChartCache.set(key,value);return value;
    }
    async function readCandles(current,tf,nextMode,c){
      if(nextMode==='signal'){
        const cached=cachedSignalChart(current,tf);if(cached)return cached;
      }
      const params=new URLSearchParams({symbol:current.symbol,period:tf,...(nextMode==='latest'?{wait_ms:'8000'}:{entry_ms:current.signal_open_ms,exit_ms:current.confirmed_ms-1})});
      const response=await fetch('/api/monday-fib/'+(nextMode==='latest'?'candles':'trade-candles')+'?'+params,{signal:c.signal,cache:'no-store'});
      const value=await response.json();if(!response.ok)throw Error(value.detail||'图表读取失败');
      return value;
    }
    async function loadPathWindow(current,epoch){
      const c=new AbortController();pathController=c;
      const params=new URLSearchParams({symbol:current.symbol,period:'12H',...(current.context_only?{wait_ms:8000}:{entry_ms:current.signal_open_ms,exit_ms:current.confirmed_ms-1})});
      try{
        const response=await fetch('/api/monday-fib/'+(current.context_only?'candles':'trade-candles')+'?'+params,{signal:c.signal,cache:'no-store'});
        const value=await response.json();
        if(epoch!==version||signal!==current)return;
        if(!response.ok||value.symbol!==current.symbol||value.bar!=='12H'||value.native_bar!=='12Hutc'||value.source!=='OKX'||!Array.isArray(value.candles))return;
        pathContext=value;mergePathCandles(value);refreshPath();
        const boundary=Math.floor(Date.now()/STEPS['12H'])*STEPS['12H'];
        if(value.last_close_ms>=boundary-STEPS['12H']){
          pathFeed=window.SpikeMondayCandleFeed?.create({symbol:current.symbol,period:'12H',onChange:live=>{
            if(epoch!==version||signal!==current||!pathContext)return;
            pathContext=window.SpikeMondayCandleFeed.mergeDisplay(pathContext,live,'12H');
            mergePathCandles(pathContext,live.now_ms);refreshPath();
          }});
          pathFeed?.start();
        }
      }catch{/* The display chart remains usable if excursion history is unavailable. */}
      finally{if(pathController===c)pathController=null;}
    }
    function resumePlanHistory(){
      if(!data||!reconstructionSupported)return;
      replay?.resume();replay?.observe(data.weekly_ranges);
    }
    async function fetchPlans(current,epoch,timeoutMs){
      const c=new AbortController();let timedOut=false;planController=c;const timeout=setTimeout(()=>{timedOut=true;c.abort();},timeoutMs);
      planError='';
      try{
        const value=await readPlans(current,c);if(epoch!==version||signal!==current)return;
        storedPlans=value.items;rebuildPlans();
        reconstructionSupported=value.reconstruction_supported===true;
        if(data)draw();resumePlanHistory();
        if(data)message(candleError||'');
      }catch(e){
        if(epoch===version&&signal===current&&(e.name!=='AbortError'||timedOut)){
          planError=e.name==='AbortError'?'历史参考框读取超时，请重试':e.message||'历史参考框读取失败，请重试';if(data)message(candleError||planError);
        }
      }finally{clearTimeout(timeout);if(planController===c)planController=null;}
    }
    async function refreshPlans(){
      if(!signal||!data||planController)return;
      return fetchPlans(signal,version,15000);
    }
    async function load(nextMode=mode){
      stopRequests();mode=nextMode;pendingFocus=true;const epoch=version,c=new AbortController();controller=c;const current=signal,tf=period;
      candleError='';planError='';message('正在读取 '+tf+' K 线…');data=null;
      window.SpikeRangeChart.mount(host,{symbol:current.symbol,bar:tf,candles:[],levels:[]},{bars:Infinity,wallpaper:true});
      let timedOut=false;const timeout=setTimeout(()=>{timedOut=true;c.abort();},25000);
      // Candle readiness is the visible-chart critical path. Plan history is
      // requested independently; the selected signal already carries its own
      // immutable reference plan while older overlays load.
      void fetchPlans(current,epoch,25000);
      try{
        const value=await readCandles(current,tf,mode,c);
        if(epoch!==version)return;
        if(value.symbol!==current.symbol||value.bar!==tf||value.source!=='OKX'||!value.candles?.length)throw Error('此周期 K 线尚未就绪，请重试');
        if(['12H','1D'].includes(tf)&&value.native_bar!==tf+'utc')throw Error('K 线时区不匹配');
        let previous=-Infinity;for(const r of value.candles){if(![r.time,r.open,r.high,r.low,r.close].every(Number.isFinite)||r.time<=previous||r.time+STEPS[tf]>value.last_close_ms)throw Error('K 线数据不完整');previous=r.time;}
        if(mode==='signal'&&isFreshSignalChart(current,tf,value))rememberSignalChart(current.id+'|'+tf,value);
        history=window.SpikeChartHistory?.create({symbol:current.symbol,period:tf,
          changed:v=>{if(epoch===version){data=v;mergePathCandles(v);draw();replay?.observe(v.weekly_ranges);}},
          status:v=>{if(epoch===version){const el=host.parentElement.querySelector('[data-cs-history-status]');if(el){el.textContent=v.retry?v.text:'';el.hidden=!v.retry;}}}});
        data=history?history.merge(value):value;mergePathCandles(data);draw();candleError='';message(planError||'');resumePlanHistory();
        if(tf!=='12H')void loadPathWindow(current,epoch);
        // Only merge live tips onto a contiguous recent window. Historical
        // lower-timeframe views must never jump across an unobserved gap.
        if(value.last_close_ms>=Math.floor(Date.now()/STEPS[tf])*STEPS[tf]-STEPS[tf]){
          feed=window.SpikeMondayCandleFeed?.create({symbol:current.symbol,period:tf,onChange:live=>{
            if(epoch!==version||!data)return;const merged=window.SpikeMondayCandleFeed.mergeDisplay(data,live,tf);
            if(tf==='12H')mergePathCandles(merged,live.now_ms);
            data=history?history.merge(merged,data):merged;draw();message(live.error||'');}});feed?.start();
        }
      }catch(e){if(epoch===version&&(e.name!=='AbortError'||timedOut)){candleError=e.name==='AbortError'?'K 线读取超时，请重试':e.message;message(candleError||'K 线读取失败，请重试');}}
      finally{clearTimeout(timeout);if(controller===c)controller=null;}
    }
    notice.querySelector('button').addEventListener('click',()=>signal&&load());
    host.parentElement.querySelector('[data-cs-history-status]')?.addEventListener('click',()=>history?.retry());
    new MutationObserver(()=>{if(signal&&data)draw();}).observe(document.documentElement,{attributes:true,attributeFilter:['data-theme']});
    function show(value){stopRequests();if(signal?.symbol!==value.symbol)exitLatches.clear();signal=value;storedPlans=!value.context_only&&(value.range_mode||'fib')===rangeMode?[value]:[];reconstructedPlans=[];plans=[...storedPlans];latestId=plans.at(-1)?.id;reconstructionSupported=false;pathRows=[];pathClosed=0;hooks.stats?.(null,{stage:'candles',running:true});onExcursion(null);period='12H';pathContext=null;layers={cloud:true,range:true,fib:true,plan:true};setupHistory();return load(value.context_only?'latest':'signal');}
    return {show,showSymbol(symbol){const t=Math.floor(Date.now()/STEPS['12H'])*STEPS['12H'];return show({id:'context:'+symbol,symbol,context_only:true,signal_open_ms:t-STEPS['12H'],confirmed_ms:t});},refreshPlans,
      period(value){if(STEPS[value]&&value!==period){period=value;return load(mode);}},
      rangeMode(value){if(!['fib','monday'].includes(value)||value===rangeMode)return;rangeMode=value;if(!signal)return;exitLatches.clear();storedPlans=[];reconstructedPlans=[];plans=[];setupHistory();draw();return load(mode);},
      async loadAll(){const current=bulk;replay?.pause();await current?.start();if(current===bulk&&signal)resumePlanHistory();},cancelAll(){bulk?.cancel();},
      layer(key,value){if(key in layers){layers[key]=value;draw();instance?.chart?.priceScale('right').applyOptions({autoScale:true});}},
      stops(value){const next=stops.normalize(value);if(next.btcEth!==stopSettings.btcEth||next.other!==stopSettings.other)exitLatches.clear();stopSettings=next;draw();instance?.chart?.priceScale('right').applyOptions({autoScale:true});},
      palette(value){palette=window.SpikeCloudCandleColors.normalize(value);candleColor=window.SpikeCloudCandleColors.colorizer(palette);draw();},
      focus(){if(signal?.context_only)return focus('latest');mode==='signal'?focus():load('signal');},latest(){mode==='latest'?focus('latest'):load('latest');},
      pause(){stopRequests();},resume(){if(signal)load();},close(){stopRequests();window.SpikeRangeChart.unmount(host);instance=null;data=null;signal=null;lastStatsInputs=[];}};
  }
  window.SpikeCloudChart={create};
})();
