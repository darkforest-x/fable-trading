/* Display-only drawings, adapted from Stitch 9fcc51e5d9214869872995f628e3d4aa.
 * LWC 4.2 coordinates are transient; stored points use UTC seconds and prices.
 * OKX candle volume is contracts (r[5]), not quote-currency turnover. Prefix
 * sums include both endpoint candles; partial coverage is explicitly marked.
 * No trading API, candle mutation, autoscale contribution or background timer.
 */
(() => {
  'use strict';
  const finite=Number.isFinite,MAX=100,PREFIX='spike:chart-drawings:v1:';
  const labels={select:'选择',measure:'测量',trend:'趋势线',channel:'平行通道'};
  const copy=value=>JSON.parse(JSON.stringify(value));
  const clamp=(v,a,b)=>Math.max(a,Math.min(b,v));
  const pointOK=p=>p&&finite(p.time)&&p.time>0&&p.time<1e12&&finite(p.price)&&p.price>0&&p.price<1e15;
  function decode(raw){
    if(raw==null)return [];
    const data=JSON.parse(raw);
    if(data?.version!==1||!Array.isArray(data.items)||data.items.length>MAX)throw Error('invalid_drawings');
    const ids=new Set();
    for(const d of data.items){
      if(!d||typeof d.id!=='string'||d.id.length>80||ids.has(d.id)||!['measure','trend','channel'].includes(d.type)||!Array.isArray(d.points)||d.points.length!==(d.type==='channel'?3:2)||!d.points.every(pointOK)||(d.type==='channel'&&d.points[0].time===d.points[1].time))throw Error('invalid_drawing');
      ids.add(d.id);
    }
    return data.items.map(d=>({id:d.id,type:d.type,points:d.points.map(p=>({time:p.time,price:p.price}))}));
  }
  function lower(rows,time){let a=0,b=rows.length;while(a<b){const m=(a+b)>>>1;if(rows[m].time<time)a=m+1;else b=m;}return a;}
  class DataIndex {
    constructor(rows,step){
      this.rows=rows;this.step=step;this.vol=[0];this.missing=[0];this.gaps=[0];
      rows.forEach((r,i)=>{const valid=finite(r.volume)&&r.volume>=0;this.vol.push(this.vol[i]+(valid?r.volume:0));this.missing.push(this.missing[i]+Number(!valid));this.gaps.push(this.gaps[i]+Number(i>0&&r.time-rows[i-1].time!==step));});
    }
    timeAt(i){const rows=this.rows,n=rows.length;if(!n||!finite(i))return null;
      if(i<=0)return rows[0].time+i*this.step;if(i>=n-1)return rows[n-1].time+(i-n+1)*this.step;
      const a=Math.floor(i);return rows[a].time+(rows[a+1].time-rows[a].time)*(i-a);
    }
    logical(time){const r=this.rows,n=r.length;if(!n||!finite(time))return null;
      if(time<=r[0].time)return (time-r[0].time)/this.step;
      if(time>=r[n-1].time)return n-1+(time-r[n-1].time)/this.step;
      const b=lower(r,time),a=b-1;return a+(time-r[a].time)/(r[b].time-r[a].time);
    }
    measure(a,b){
      const from=Math.min(a.time,b.time),to=Math.max(a.time,b.time),lo=lower(this.rows,from),end=lower(this.rows,to),hi=end+Number(this.rows[end]?.time===to),count=hi-lo;
      const complete=count>0&&this.rows[lo]?.time===from&&this.rows[hi-1]?.time===to&&this.gaps[hi]-this.gaps[lo+1]===0;
      return {delta:b.price-a.price,percent:a.price>0?(b.price-a.price)/a.price*100:null,seconds:to-from,bars:Math.max(0,count-1),count,
        volume:count&&this.missing[hi]===this.missing[lo]?this.vol[hi]-this.vol[lo]:null,complete,
        live:count>0&&this.rows.slice(hi-1,hi).some(r=>r.confirmed===false||r.live===true)};
    }
  }
  function channelPoints(p){
    if(p.length<3||Math.abs(p[1].x-p[0].x)<.01)return null;
    const offset=p[2].y-(p[0].y+(p[1].y-p[0].y)*(p[2].x-p[0].x)/(p[1].x-p[0].x));
    return [p[0],p[1],{x:p[1].x,y:p[1].y+offset},{x:p[0].x,y:p[0].y+offset}];
  }
  function distance(p,a,b){const dx=b.x-a.x,dy=b.y-a.y,t=clamp(((p.x-a.x)*dx+(p.y-a.y)*dy)/(dx*dx+dy*dy||1),0,1);return Math.hypot(p.x-a.x-t*dx,p.y-a.y-t*dy);}
  const amount=v=>finite(v)?v.toLocaleString('en-US',{maximumFractionDigits:Math.abs(v)>=100?2:Math.abs(v)>=1?4:10}):'—';
  function duration(seconds){const parts=[];for(const [unit,size] of [['天',86400],['小时',3600],['分钟',60],['秒',1]]){const n=Math.floor(seconds/size);if(n){parts.push(n+' '+unit);seconds-=n*size;}if(parts.length===2)break;}return parts.join(' ')||'0 分钟';}
  function measurementText(m){const sign=m.delta>0?'+':'';return [sign+amount(m.delta)+' ('+(m.percent>0?'+':'')+amount(m.percent)+'%)',m.bars+' 根间隔 · '+duration(m.seconds),'成交量 '+amount(m.volume)+' 张',m.complete?'含首尾 K 线'+(m.live?' · 最新 K 未收盘':''):'仅 '+m.count+' 根已载入 · 覆盖不完整'];}
  const icons={select:'M5 3 20 12l-7 1-4 7Z',measure:'m3 16 13-13 5 5-13 13Z M8 11l3 3 M11 8l3 3 M14 5l3 3',trend:'M4 19 20 5 M4 16v3h3 M17 5h3v3',channel:'M3 15 18 4 M6 20 21 9 M5 17.5l14-11'};
  function toolsHTML(){return `<div class="cd-tools"><strong>绘图工具</strong><div class="cd-tool-grid">${Object.entries(labels).map(([id,label])=>`<button type="button" data-cd-tool="${id}" aria-label="${label==='选择'?'选择绘图':label}" aria-pressed="${id==='select'}"><svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linejoin="round" aria-hidden="true"><path d="${icons[id]}"/></svg><span>${label}</span></button>`).join('')}</div><div class="cd-actions"><button type="button" data-cd-action="delete" disabled>删除所选</button><button type="button" data-cd-action="undo" disabled>撤销</button><button type="button" data-cd-action="clear" disabled>清空绘图</button></div><small>绘图保存在本浏览器 · 按币种独立保存</small></div>`;}
  class Primitive {
    constructor(owner){this.owner=owner;this.view={zOrder:()=> 'top',renderer:()=>({draw:target=>target.useMediaCoordinateSpace(scope=>owner.paint(scope))})};}
    attached({requestUpdate}){this.requestUpdate=requestUpdate;}
    detached(){this.requestUpdate=null;}
    paneViews(){return [this.view];}updateAllViews(){}
  }
  function create({root,onTool=()=>{}}){
    let instance=null,index=new DataIndex([],900),symbol='',mode='select',items=[],selected=null,draft=null,preview=null,gesture=null,backup=null,undo=[],warning='',blocked=false,suppressUntil=0,dead=false;
    const memory=new Map();let primitive=null,lockedOptions=null,published='';
    const strip=document.createElement('div');strip.className='cd-status rp-glass';strip.hidden=true;
    strip.innerHTML='<div><b data-cd-title></b><span data-cd-hint role="status"></span></div><button type="button" data-cd-action="cancel">退出绘图</button>';
    const announcement=document.createElement('span');announcement.className='cd-sr';announcement.setAttribute('aria-live','polite');strip.append(announcement);root.append(strip);
    const state=()=>({mode,selected,items:copy(items),draft:copy(draft),symbol,warning});
    const publish=()=>{
      const active=mode!=='select',choice=items.find(d=>d.id===selected);
      const text=choice?.type==='measure'?measurementText(index.measure(...choice.points)).join('；'):'';
      const key=JSON.stringify([mode,!!gesture,selected,draft?.points.length,!!index.rows.length,items.length,undo.length,warning,text]);
      // Candle ticks still invalidate the canvas primitive, but unchanged tool
      // chrome must not rebuild text nodes or invalidate the page's layout.
      primitive?.requestUpdate?.();
      if(key===published)return;published=key;
      root.classList.toggle('is-drawing',active||!!gesture);strip.hidden=!active&&!choice&&!warning;
      root.querySelectorAll('[data-cd-tool]').forEach(b=>{b.disabled=!index.rows.length;b.setAttribute('aria-pressed',String(b.dataset.cdTool===mode));});
      root.querySelectorAll('[data-cd-action="delete"]').forEach(b=>b.disabled=!choice);
      root.querySelectorAll('[data-cd-action="clear"]').forEach(b=>b.disabled=!items.length);
      root.querySelectorAll('[data-cd-action="undo"]').forEach(b=>b.disabled=!undo.length);
      strip.querySelector('[data-cd-title]').textContent=active?labels[mode]+' · '+(draft?.points.length||0)+' / '+(mode==='channel'?3:2):choice?labels[choice.type]+'已选中':'绘图';
      strip.querySelector('[data-cd-hint]').textContent=warning||(active?(mode==='channel'&&draft?.points.length===2?'点第三点确定通道宽度':draft?'点终点；Esc 取消':'拖动或点选起点、终点；方向键＋Enter 也可绘制'):choice?'拖动端点调整 · Delete 删除':'');
      strip.querySelector('[data-cd-action="cancel"]').textContent=active?'取消':'完成';
      if(announcement.textContent!==text)announcement.textContent=text;
    };
    function lock(value){
      if(!instance)return;
      if(value&&!lockedOptions){const o=instance.chart.options();lockedOptions=copy({handleScroll:o.handleScroll,handleScale:o.handleScale,kineticScroll:o.kineticScroll});instance.chart.applyOptions({handleScroll:false,handleScale:false,kineticScroll:{mouse:false,touch:false}});}
      if(!value&&lockedOptions){instance.chart.applyOptions(lockedOptions);lockedOptions=null;}
      instance.canvas.classList.toggle('cd-placing',value);
    }
    function save(){
      if(blocked)warning='本地绘图暂不可读取；新增绘图仅本页保留，原内容未改';
      else try{window.localStorage.setItem(PREFIX+symbol,JSON.stringify({version:1,items}));warning='';}catch{warning='浏览器未允许保存；绘图仅在本次页面保留';}
      memory.set(symbol,{items:copy(items),blocked,warning});while(memory.size>40)memory.delete(memory.keys().next().value);
    }
    function checkpoint(){undo.push(copy(items));if(undo.length>20)undo.shift();}
    function cancel(){
      if(gesture&&backup)items=backup;
      if(gesture&&instance?.canvas.hasPointerCapture?.(gesture.id))instance.canvas.releasePointerCapture(gesture.id);
      gesture=null;backup=null;draft=preview=null;mode='select';selected=null;lock(false);publish();
    }
    function choose(value){
      if(!index.rows.length||!Object.hasOwn(labels,value))return;
      cancel();mode=value;lock(mode!=='select');publish();onTool();instance?.canvas.focus({preventScroll:true});
    }
    function project(point){
      if(!instance)return null;const logical=index.logical(point.time);if(logical==null)return null;
      const scale=instance.chart.timeScale(),floor=Math.floor(logical),a=scale.logicalToCoordinate(floor),b=scale.logicalToCoordinate(floor+1),y=instance.series.priceToCoordinate(point.price);
      return a==null||b==null||y==null?null:{x:a+(b-a)*(logical-floor),y};
    }
    function dimensions(){return {width:instance.chart.timeScale().width(),height:instance.canvas.clientHeight-instance.chart.timeScale().height(),left:instance.chart.priceScale('left').width()};}
    function pixel(e){if(!instance)return null;const rect=instance.canvas.getBoundingClientRect(),s=dimensions();const p={x:e.clientX-rect.left-s.left,y:e.clientY-rect.top};return p.x>=0&&p.x<s.width&&p.y>=0&&p.y<s.height?p:null;}
    function at(p,measuring=mode==='measure'){
      const logical=instance?.chart.timeScale().coordinateToLogical(p.x),price=instance?.series.coordinateToPrice(p.y);
      if(!finite(logical)||!finite(price)||price<=0||!index.rows.length)return null;
      const i=measuring?clamp(Math.round(logical),0,index.rows.length-1):Math.round(logical),time=index.timeAt(i);
      return pointOK({time,price})?{time,price}:null;
    }
    function hit(p){
      for(const d of [...items].sort((a,b)=>Number(a.id===selected)-Number(b.id===selected)).reverse()){
        const pts=d.points.map(project);if(pts.some(p=>!p))continue;
        const handle=pts.findIndex(v=>Math.hypot(p.x-v.x,p.y-v.y)<=12);if(handle>=0)return {id:d.id,handle};
        let edges=[[pts[0],pts[1]]];
        if(d.type==='measure'){const [a,b]=pts,c={x:a.x,y:b.y},e={x:b.x,y:a.y};edges=[[a,c],[c,b],[b,e],[e,a]];}
        if(d.type==='channel'){const q=channelPoints(pts);if(q)edges=[[q[0],q[1]],[q[2],q[3]]];}
        if(edges.some(([a,b])=>distance(p,a,b)<9))return {id:d.id,handle:-1};
      }return null;
    }
    function accept(point){
      if(!draft){draft={id:Date.now().toString(36)+'-'+Math.random().toString(36).slice(2,10),type:mode,points:[point]};preview=point;publish();return;}
      const p=draft.points;
      if(p.length===1&&point.time===p[0].time&&Math.abs(point.price-p[0].price)<1e-12)return;
      if(mode==='channel'&&p.length===1&&point.time===p[0].time){warning='通道基线的两点请选在不同 K 线上';publish();return;}
      if(mode==='channel'&&p.length===2){const q=channelPoints([...p,point].map(project));if(!q||Math.abs(q[3].y-q[0].y)<2){warning='请点在基线之外，确定通道宽度';publish();return;}}
      p.push(point);preview=point;warning='';
      if(p.length===(mode==='channel'?3:2)){
        if(items.length>=MAX){warning='当前币种已保存 100 个绘图，请先删除部分绘图';cancel();return;}
        checkpoint();items.push(draft);selected=draft.id;draft=preview=null;mode='select';lock(false);save();
      }publish();
    }
    const consume=e=>{e.preventDefault();e.stopImmediatePropagation();};
    const inside=e=>instance?.canvas.contains(e.target);
    function down(e){
      if(!inside(e)||e.button!==0)return;
      if(e.isPrimary===false){if(mode!=='select'||gesture){consume(e);cancel();}return;}
      const p=pixel(e);if(!p)return;
      if(mode!=='select'){
        const point=at(p);if(!point)return;consume(e);
        gesture={id:e.pointerId,start:p,first:!draft,pointer:e.pointerType,moved:false};
        if(!draft)accept(point);preview=point;lock(true);instance.canvas.setPointerCapture?.(e.pointerId);publish();
      }else{
        const found=hit(p);
        if(!found){if(selected){selected=null;publish();}return;}
        consume(e);selected=found.id;backup=copy(items);gesture={id:e.pointerId,start:p,handle:found.handle,moved:false};
        lock(true);instance.canvas.setPointerCapture?.(e.pointerId);publish();
      }
    }
    function move(e){
      if(!instance)return;
      if(gesture&&e.pointerId!==gesture.id)return;
      if(!gesture&&mode==='select')return;
      const p=pixel(e);if(!p)return;
      if(gesture){consume(e);gesture.moved ||= Math.hypot(p.x-gesture.start.x,p.y-gesture.start.y)>5;}
      const point=at(p,mode==='measure'||items.find(d=>d.id===selected)?.type==='measure');if(!point)return;
      if(mode!=='select'){preview=point;primitive?.requestUpdate?.();}
      else if(gesture?.handle>=0&&gesture.moved){const d=items.find(d=>d.id===selected);const next=copy(d);next.points[gesture.handle]=point;if(d.type!=='channel'||next.points[0].time!==next.points[1].time){d.points=next.points;primitive?.requestUpdate?.();}}
    }
    function up(e){
      if(!gesture||gesture.id!==e.pointerId)return;consume(e);suppressUntil=performance.now()+500;
      const g=gesture,p=pixel(e);gesture=null;if(instance?.canvas.hasPointerCapture?.(e.pointerId))instance.canvas.releasePointerCapture(e.pointerId);
      if(mode!=='select'){
        const point=p&&at(p);if(point&&(!g.first||g.moved))accept(point);
      }else if(g.moved&&g.handle>=0){undo.push(backup);if(undo.length>20)undo.shift();save();}
      backup=null;lock(mode!=='select');publish();
    }
    function abort(e){if(gesture&&(!e||e.pointerId===gesture.id)){suppressUntil=performance.now()+500;cancel();}}
    // Suppress the completing click/double-click only. A fresh pan or wheel
    // gesture must work immediately after cancel/finish, without a dead period.
    const native=e=>{if(inside(e)&&(mode!=='select'||gesture||(['click','dblclick'].includes(e.type)&&performance.now()<suppressUntil)))consume(e);};
    function key(e){
      if(e.target.closest?.('input,select,textarea,[contenteditable=true]'))return;
      if(e.key==='Escape'&&(mode!=='select'||selected||gesture)){consume(e);cancel();return;}
      if((e.key==='Delete'||e.key==='Backspace')&&selected){consume(e);action('delete');return;}
      if((e.ctrlKey||e.metaKey)&&e.key.toLowerCase()==='z'&&undo.length&&inside(e)){consume(e);action('undo');return;}
      if(mode==='select'||!inside(e)||!['ArrowLeft','ArrowRight','ArrowUp','ArrowDown','Enter'].includes(e.key))return;
      consume(e);const s=dimensions(),p=preview&&project(preview)||{x:s.width/2,y:s.height/2};
      if(e.key==='Enter'){const value=preview||at(p);if(value)accept(value);return;}
      const step=e.shiftKey?30:8;p.x=clamp(p.x+(e.key==='ArrowRight'?step:e.key==='ArrowLeft'?-step:0),0,s.width-1);p.y=clamp(p.y+(e.key==='ArrowDown'?step:e.key==='ArrowUp'?-step:0),0,s.height-1);preview=at(p);primitive?.requestUpdate?.();
    }
    function action(value){
      if(value==='cancel'){cancel();return;}
      if(value==='delete'&&selected){checkpoint();items=items.filter(d=>d.id!==selected);selected=null;}
      else if(value==='clear'&&items.length){checkpoint();items=[];selected=null;}
      else if(value==='undo'&&undo.length){items=undo.pop();selected=null;}
      else return;
      draft=preview=null;mode='select';gesture=null;lock(false);save();publish();
    }
    function click(e){const b=e.target.closest?.('[data-cd-tool],[data-cd-action]');if(!b||!root.contains(b))return;e.preventDefault();b.dataset.cdTool?choose(b.dataset.cdTool):action(b.dataset.cdAction);}
    function attach(next,data){
      if(dead)return;
      if(instance!==next){detach();instance=next;primitive=new Primitive(api);next.series.attachPrimitive(primitive);next.drawingLayer={destroy:detach};}
      if(symbol!==data.symbol){cancel();symbol=data.symbol;undo=[];warning='';blocked=false;
        if(memory.has(symbol)){const cached=memory.get(symbol);items=copy(cached.items);blocked=cached.blocked;warning=cached.warning;}else try{items=decode(window.localStorage.getItem(PREFIX+symbol));}catch{items=[];warning='本地绘图暂不可读取；新增绘图仅本页保留，原内容未改';blocked=true;}
      }
      const step=({'1m':60,'5m':300,'15m':900,'30m':1800,'1H':3600,'4H':14400,'12H':43200,'1D':86400})[data.bar]||900;
      if(index.step!==step){cancel();}
      index=new DataIndex(next.rows,step);publish();
    }
    function detach(){if(!instance)return;cancel();instance.series.detachPrimitive?.(primitive);instance.drawingLayer=null;instance=null;primitive=null;index=new DataIndex([],900);publish();}
    function paint({context:ctx,mediaSize:s}){
      const pending=draft&&{...draft,points:preview&&draft.points.length<(draft.type==='channel'?3:2)?[...draft.points,preview]:draft.points};
      const shapes=[...items,...(pending?[pending]:[])];
      ctx.save();ctx.beginPath();ctx.rect(0,0,s.width,s.height);ctx.clip();ctx.lineJoin='round';
      const line=(a,b)=>{ctx.beginPath();ctx.moveTo(a.x,a.y);ctx.lineTo(b.x,b.y);ctx.stroke();};
      const arrow=(a,b)=>{line(a,b);const angle=Math.atan2(b.y-a.y,b.x-a.x);for(const d of [-.55,.55])line(b,{x:b.x-7*Math.cos(angle+d),y:b.y-7*Math.sin(angle+d)});};
      for(const d of shapes){
        const pts=d.points.map(project);if(pts.some(p=>!p)||!pts.length)continue;
        const active=d.id===selected||d===pending,tone=d.type==='measure'&&d.points[1]?.price<d.points[0].price?'#d7355d':'#1684ed';
        ctx.strokeStyle=tone;ctx.fillStyle=tone;ctx.lineWidth=active?2:1.5;ctx.setLineDash([]);
        if(d.type==='channel'&&pts.length===3){const q=channelPoints(pts);if(q){ctx.globalAlpha=.10;ctx.beginPath();q.forEach((p,i)=>i?ctx.lineTo(p.x,p.y):ctx.moveTo(p.x,p.y));ctx.closePath();ctx.fill();ctx.globalAlpha=1;line(q[0],q[1]);line(q[2],q[3]);ctx.setLineDash([5,5]);line({x:q[0].x,y:(q[0].y+q[3].y)/2},{x:q[1].x,y:(q[1].y+q[2].y)/2});ctx.setLineDash([]);}}
        else if(pts.length>=2){const [a,b]=pts;if(d.type==='measure'){
          const left=Math.min(a.x,b.x),top=Math.min(a.y,b.y),width=Math.abs(b.x-a.x),height=Math.abs(b.y-a.y);
          ctx.globalAlpha=.12;ctx.fillRect(left,top,width,height);ctx.globalAlpha=1;ctx.setLineDash([4,4]);ctx.strokeRect(left,top,width,height);ctx.setLineDash([]);
          arrow({x:a.x,y:(a.y+b.y)/2},{x:b.x,y:(a.y+b.y)/2});arrow({x:(a.x+b.x)/2,y:a.y},{x:(a.x+b.x)/2,y:b.y});
          if(active&&Math.max(a.x,b.x)>=0&&left<s.width&&Math.max(a.y,b.y)>=0&&top<s.height){
            const text=measurementText(index.measure(...d.points)),w=Math.min(258,s.width-16),h=90,x=clamp((a.x+b.x-w)/2,8,s.width-w-8),y=clamp(top-h-12,8,s.height-h-8);
            ctx.fillStyle=tone;ctx.beginPath();ctx.roundRect(x,y,w,h,10);ctx.fill();ctx.fillStyle='#fff';ctx.textAlign='center';ctx.textBaseline='middle';
            text.forEach((t,i)=>{ctx.font=(i===0?'600 13':'11')+'px -apple-system, BlinkMacSystemFont, "PingFang SC", sans-serif';ctx.fillText(t,x+w/2,y+17+i*19,w-16);});ctx.fillStyle=tone;
          }
        }else line(a,b);}
        if(active)for(const p of pts){ctx.beginPath();ctx.arc(p.x,p.y,4.5,0,Math.PI*2);ctx.fillStyle='#fff';ctx.fill();ctx.stroke();}
      }
      if(mode!=='select'&&preview){const p=project(preview);if(p){ctx.strokeStyle='#1684ed88';ctx.lineWidth=1;ctx.setLineDash([3,4]);line({x:0,y:p.y},{x:s.width,y:p.y});line({x:p.x,y:0},{x:p.x,y:s.height});}}
      ctx.restore();
    }
    root.addEventListener('pointerdown',down,true);root.addEventListener('pointermove',move,true);root.addEventListener('pointerup',up,true);root.addEventListener('pointercancel',abort,true);root.addEventListener('keydown',key,true);root.addEventListener('click',click);
    for(const name of ['mousedown','touchstart','touchmove','touchend','click','dblclick','wheel'])root.addEventListener(name,native,{capture:true,passive:false});
    const blur=()=>{if(mode!=='select'||gesture)cancel();};window.addEventListener('blur',blur);
    const visibility=()=>{if(document.hidden)blur();};document.addEventListener('visibilitychange',visibility);
    const api={attach,cancel,choose,state,paint,project,action,destroy(){detach();dead=true;strip.remove();root.removeEventListener('pointerdown',down,true);root.removeEventListener('pointermove',move,true);root.removeEventListener('pointerup',up,true);root.removeEventListener('pointercancel',abort,true);root.removeEventListener('keydown',key,true);root.removeEventListener('click',click);for(const name of ['mousedown','touchstart','touchmove','touchend','click','dblclick','wheel'])root.removeEventListener(name,native,true);window.removeEventListener('blur',blur);document.removeEventListener('visibilitychange',visibility);}};
    publish();return api;
  }
  const api={DataIndex,channelPoints,distance,decode,duration,measurementText,toolsHTML,create,PREFIX,MAX};
  if(typeof module!=='undefined')module.exports=api;
  if(typeof window!=='undefined')window.SpikeChartDrawings=api;
})();
