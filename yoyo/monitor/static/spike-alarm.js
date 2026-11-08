/* Global notify-only alarm. Contract: spike_alert.py; Stitch b8f0df9af24b482c874dc308a5bd50dd.
 * One EventSource per document; no request polling. Audio requires a user gesture.
 * MDN: Server-sent_events/Using_server-sent_events, HTMLDialogElement/showModal,
 * Web_Audio_API/Best_practices. Six-hour expiry uses server time + monotonic elapsed time.
 * History (owner 2026-11-05 "连历史消息都没有"): GET /api/spike-alerts (last 50 rows, already served) is read
 * only when the settings panel opens and again when the stream version changes while it is open; never polled.
 */
(() => {
  'use strict';
  const ACTIVE_MS=6*60*60*1000;
  const timestamp=v=>Number.isSafeInteger(v)&&v>0&&v<=8640000000000000;
  function packet(value){
    if(!value||!timestamp(value.now_ms)||!Number.isInteger(value.version)||!Array.isArray(value.active)||value.active.length>50)throw Error('invalid_alert_packet');
    const ids=new Set();
    for(const a of value.active){
      if(!a||!Number.isSafeInteger(a.id)||a.id<=0||ids.has(a.id)||!['lower','upper'].includes(a.side)||!Number.isSafeInteger(a.n)||a.n<1||
        ![a.first_ms,a.last_ms,a.created_ms].every(timestamp)||a.first_ms>a.last_ms||a.ack_ms!=null||![0,1,false,true].includes(a.test)||
        !Array.isArray(a.symbols)||a.symbols.length!==a.n||a.n>1000||!a.symbols.every(s=>typeof s==='string'&&/^[A-Z0-9]{1,32}-USDT-SWAP$/.test(s)))throw Error('invalid_alert_row');
      ids.add(a.id);
    }
    return value;
  }
  const DELIVERY=new Set(['pending','no_channel','sent','failed','unknown','test']);
  function historyPacket(value){
    const int=v=>Number.isSafeInteger(v)&&v>=0;
    if(!value||!timestamp(value.now_ms)||!int(value.threshold)||!int(value.window_minutes)||!int(value.cooldown_minutes)||
      !value.channels||typeof value.channels!=='object'||!Array.isArray(value.alerts)||value.alerts.length>200)throw Error('invalid_history');
    const ids=new Set(),alerts=[];
    for(const a of value.alerts){
      if(!a||!Number.isSafeInteger(a.id)||a.id<=0||ids.has(a.id)||!['lower','upper'].includes(a.side)||!Number.isSafeInteger(a.n)||a.n<1||
        ![a.first_ms,a.last_ms,a.created_ms].every(timestamp)||!(a.ack_ms==null||timestamp(a.ack_ms))||![0,1,false,true].includes(a.test)||
        !Array.isArray(a.symbols)||a.symbols.length>1000||!a.symbols.every(s=>typeof s==='string'&&/^[A-Z0-9]{1,32}-USDT-SWAP$/.test(s)))throw Error('invalid_history_row');
      ids.add(a.id);
      alerts.push({id:a.id,side:a.side,n:a.n,symbols:a.symbols.slice(),first_ms:a.first_ms,last_ms:a.last_ms,created_ms:a.created_ms,ack_ms:a.ack_ms??null,
        test:Boolean(a.test),active:a.active===true,telegram:DELIVERY.has(a.telegram)?a.telegram:'unknown',bark:DELIVERY.has(a.bark)?a.bark:'unknown'});
    }
    alerts.sort((a,b)=>b.created_ms-a.created_ms||b.id-a.id);
    return {now_ms:value.now_ms,threshold:value.threshold,window_minutes:value.window_minutes,cooldown_minutes:value.cooldown_minutes,
      channels:{telegram:value.channels.telegram===true,bark:value.channels.bark===true},workerAlive:value.worker_alive===true,notifyOnly:value.notify_only!==false,alerts};
  }
  function historyLoader({request=(url,opts)=>fetch(url,opts),later=setTimeout,cancel=clearTimeout,change=()=>{}}={}){
    let data=null,loading=false,error='',current=null,token=0;
    const state=()=>({data,loading,error});
    async function load(){
      current?.abort();const c=new AbortController(),mine=++token;current=c;loading=true;error='';change(state());
      const timeout=later(()=>c.abort(),12000);
      try{
        const r=await request('/api/spike-alerts',{credentials:'same-origin',cache:'no-store',signal:c.signal});
        if(!r.ok)throw Error('request_failed');const v=historyPacket(await r.json());
        if(mine!==token)return false;data=v;return true;
      }catch(_){if(mine===token)error='历史记录读取失败，请重试。';return false;}
      finally{cancel(timeout);if(mine===token){loading=false;current=null;change(state());}}
    }
    function abort(){token++;current?.abort();current=null;if(loading){loading=false;change(state());}}
    return {state,load,abort};
  }
  // Alert replay reuses /api/monday-fib/trade-candles (OKX closed bars, alert-week Monday range).
  // The chart shows 12 bars before the first touch and up to REPLAY_AFTER+13 bars after the last one.
  const REPLAY_PERIODS={'5m':300000,'15m':900000,'1H':3600000,'4H':14400000},REPLAY_AFTER=96;
  function replayWindow(alert,period,nowMs){
    const step=REPLAY_PERIODS[period];
    if(!step||!alert||![alert.first_ms,alert.last_ms,nowMs].every(timestamp)||alert.first_ms>alert.last_ms)return null;
    const closed=Math.floor(nowMs/step)*step-step,ready=Math.floor(alert.last_ms/step)*step<=closed;
    return {fill_ms:alert.first_ms,exit_ms:ready?Math.max(alert.last_ms,Math.min(alert.last_ms+REPLAY_AFTER*step,closed)):alert.last_ms,ready};
  }
  function replayCoins(alert){
    if(!alert||alert.test)return [];
    const coins=alert.symbols.filter(s=>/^[A-Z0-9]{1,32}-USDT-SWAP$/.test(s));
    return [...CONTROLS.filter(s=>!coins.includes(s)),...coins];
  }
  // Owner 2026-11-05: every alarm ray is RAY_BARS candles long on every period (counted in bars, not time),
  // measured from the owner's 5m example. Majors get a scrollable year with all alarms marked.
  const RAY_BARS=72,MAJORS=new Set(['BTC-USDT-SWAP','ETH-USDT-SWAP','SOL-USDT-SWAP','XRP-USDT-SWAP','DOGE-USDT-SWAP','BNB-USDT-SWAP']),CONTROLS=['BTC-USDT-SWAP','ETH-USDT-SWAP'];
  function alertTimeline(live,replayed){
    const real=(live||[]).filter(a=>!a.test).map(a=>({...a,live:true}));
    const start=real.length?Math.min(...real.map(a=>a.first_ms)):Infinity;
    const old=(replayed||[]).filter(r=>timestamp(r.ms)&&['lower','upper'].includes(r.side)&&r.ms<start)
      .map(r=>({id:'r'+r.ms,side:r.side,n:25,first_ms:r.ms,last_ms:r.ms,symbols:[],test:false,live:false}));
    return [...old,...real].sort((a,b)=>a.last_ms-b.last_ms);
  }
  function rayItems(rows,alerts,step,selected){
    if(!rows?.length||!step)return [];
    const index=new Map(rows.map((r,i)=>[r.time,i])),out=[];
    for(const a of alerts){
      const i=index.get(Math.floor(a.last_ms/step)*step/1000);if(i==null)continue;
      out.push({id:a.id,ms:a.last_ms,side:a.side,live:a.live!==false,selected:a.id===selected,time:rows[i].time,price:rows[i].close,from:i,to:Math.min(i+RAY_BARS,rows.length-1)});
    }
    return out.sort((a,b)=>a.time-b.time);
  }
  class AlertRays {
    constructor(owner){this.owner=owner;this.items=[];this.view={zOrder:()=>'top',renderer:()=>({draw:target=>this.paint(target)})};}
    attached({requestUpdate}){this.requestUpdate=requestUpdate;}detached(){this.requestUpdate=null;}
    paneViews(){return [this.view];}updateAllViews(){}
    paint(target){
      const o=this.owner,scale=o.chart?.timeScale();if(!scale||!this.items.length)return;
      target.useMediaCoordinateSpace(({context:ctx,mediaSize:m})=>{
        ctx.save();ctx.beginPath();ctx.rect(0,0,m.width,m.height);ctx.clip();
        for(const it of this.items){
          const y=o.series.priceToCoordinate(it.price),a=scale.logicalToCoordinate(it.from),b=scale.logicalToCoordinate(it.to);
          if(y==null||a==null||b==null||b<0||a>m.width)continue;
          ctx.strokeStyle=it.color||(it.live?'#e11d48':'#c2677c');ctx.globalAlpha=it.selected?.95:it.live?.7:.7;ctx.lineWidth=it.selected?2.2:1.4;ctx.setLineDash(it.live?[]:[5,4]);
          ctx.beginPath();ctx.moveTo(a,y);ctx.lineTo(b,y);ctx.stroke();
        }
        ctx.restore();
      });
    }
  }
  function alertStatus(a){return a.ack_ms!=null?'acked':a.active?'pending':'expired';}
  function create({source=url=>new EventSource(url),request=(url,opts)=>fetch(url,opts),clock=()=>performance.now(),later=setTimeout,cancel=clearTimeout,change=()=>{}}={}){
    let stream=null,running=false,connection='connecting',alerts=[],selected=null,serverNow=0,received=0,expiry=null,generation=0,testPending=false,testError='',snapshots=0;
    const acknowledged=new Set(),pending=new Map(),errors=new Map();
    const now=()=>serverNow+Math.max(0,clock()-received);
    const state=()=>({running,connection,alerts:[...alerts],selected,ackPending:pending.has(selected),ackError:errors.get(selected)||'',testPending,testError,snapshots});
    const emit=()=>change(state());
    function prune(){
      cancel(expiry);expiry=null;
      alerts=alerts.filter(a=>!acknowledged.has(a.id)&&now()-a.created_ms<=ACTIVE_MS);
      if(!alerts.some(a=>a.id===selected))selected=alerts[0]?.id??null;
      if(running&&alerts.length)expiry=later(()=>{prune();emit();},Math.max(1,Math.min(...alerts.map(a=>a.created_ms+ACTIVE_MS-now()+1))));
    }
    function start(){
      if(running)return;running=true;connection='connecting';prune();emit();
      try{
        const current=source('/api/spike-alerts/stream');stream=current;
        current.addEventListener('alerts',event=>{
          if(stream!==current)return;
          try{
            const v=packet(JSON.parse(event.data));serverNow=v.now_ms;received=clock();snapshots++;
            // A server restart resets version; every connection carries a full authoritative snapshot.
            alerts=v.active.slice().sort((a,b)=>a.created_ms-b.created_ms||a.id-b.id);connection='live';testError='';prune();emit();
          }catch(_){connection='invalid';emit();}
        });
        current.onerror=()=>{if(stream===current){connection=current.readyState===2?'offline':'reconnecting';emit();}};
      }catch(_){connection='unsupported';emit();}
    }
    function stop(){
      running=false;generation++;stream?.close();stream=null;cancel(expiry);expiry=null;
      for(const c of pending.values())c.abort();pending.clear();testPending=false;connection='offline';emit();
    }
    async function post(url,action,controller){
      const timeout=later(()=>controller.abort(),12000);
      try{const r=await request(url,{method:'POST',headers:{'x-spike-action':action},credentials:'same-origin',cache:'no-store',signal:controller.signal});if(!r.ok)throw Error('request_failed');return await r.json();}
      finally{cancel(timeout);}
    }
    async function acknowledge(id=selected){
      if(!alerts.some(a=>a.id===id)||pending.has(id))return false;
      const c=new AbortController(),token=generation;pending.set(id,c);errors.delete(id);emit();
      try{
        const v=await post('/api/spike-alerts/'+id+'/ack','ack-spike-alert',c);
        if(token!==generation)return false;
        if(v.id!==id||!Number.isFinite(v.ack_ms))throw Error('invalid_ack');
        acknowledged.add(id);prune();return true;
      }catch(_){if(token===generation)errors.set(id,'确认未成功，请重试。');return false;}
      finally{if(token===generation){pending.delete(id);emit();}}
    }
    async function test(){
      if(testPending||connection!=='live')return false;
      const c=new AbortController(),token=generation;testPending=true;testError='';pending.set('test',c);emit();
      try{
        const v=await post('/api/spike-alerts/test','test-spike-alert',c);
        if(!Number.isSafeInteger(v.id)||v.test!==true)throw Error('invalid_test');
        return token===generation;
      }catch(_){if(token===generation)testError='测试请求未完成，请检查连接后重试。';return false;}
      finally{if(token===generation){testPending=false;pending.delete('test');emit();}}
    }
    return {start,stop,state,acknowledge,test,now,refreshTime(){prune();emit();},select(id){if(alerts.some(a=>a.id===id)){selected=id;emit();}},reconnect(){stop();start();}};
  }
  function sound({AudioContext=globalThis.AudioContext||globalThis.webkitAudioContext,change=()=>{}}={}){
    let context=null,node=null,wanted=false,active=false,enabling=false,revision=0;
    const state=()=>!AudioContext?'unsupported':enabling?'enabling':!wanted?'off':context?.state==='running'?'on':'blocked';
    function stop(){if(node){node.stop();node.disconnect();node=null;}}
    function sync(){
      if(!active||state()!=='on')stop();
      else if(!node){
        // Two gentle tones every 2.4s, scheduled by the audio engine (no timer loop).
        const buffer=context.createBuffer(1,Math.ceil(context.sampleRate*2.4),context.sampleRate),data=buffer.getChannelData(0);
        for(let i=0;i<data.length;i++){
          const t=i/context.sampleRate,phase=t<.35?t:t>=.55&&t<.9?t-.55:-1;
          if(phase>=0){const envelope=Math.min(1,phase/.025,(.35-phase)/.06);data[i]=.12*envelope*Math.sin(2*Math.PI*(t<.35?660:880)*phase);}
        }
        node=context.createBufferSource();node.buffer=buffer;node.loop=true;node.connect(context.destination);node.start();
      }
      change(state());
    }
    async function enable(){
      if(!AudioContext||enabling)return false;
      const token=++revision;wanted=true;enabling=true;change(state());
      try{
        if(!context||context.state==='closed'){context=new AudioContext();context.onstatechange=()=>{if(!enabling)sync();};}
        await context.resume();
        if(token!==revision)return false;
        enabling=false;sync();return state()==='on';
      }catch(_){if(token===revision){enabling=false;sync();}return false;}
    }
    return {state,enable,setActive(value){active=Boolean(value);sync();},mute(){revision++;wanted=false;enabling=false;stop();context?.suspend().catch(()=>{});change(state());},stop};
  }
  const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const parts=(ms,opts)=>Object.fromEntries(new Intl.DateTimeFormat('zh-CN',{timeZone:'Asia/Shanghai',hour12:false,...opts}).formatToParts(ms).map(p=>[p.type,p.value]));
  const short=ms=>{const p=parts(ms,{month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit'});return `${p.month}-${p.day} ${p.hour}:${p.minute}`;};
  const hms=ms=>{const p=parts(ms,{hour:'2-digit',minute:'2-digit',second:'2-digit'});return `${p.hour}:${p.minute}:${p.second}`;};
  const hm=ms=>hms(ms).slice(0,5);
  const ago=(ms,now)=>{const m=Math.max(0,Math.round((now-ms)/60000));return m<1?'刚刚':m<60?m+' 分钟前':m<1440?Math.floor(m/60)+' 小时前':Math.floor(m/1440)+' 天前';};
  const span=ms=>ms>=172800000?Math.round(ms/86400000)+' 天':ms>=3600000?Math.round(ms/3600000)+' 小时':Math.round(ms/60000)+' 分钟';
  const coin=s=>esc(String(s).replace('-USDT-SWAP',''));
  const at=ms=>new Intl.DateTimeFormat('zh-CN',{timeZone:'Asia/Shanghai',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',second:'2-digit',hour12:false}).format(ms);
  // Motion 14.6.0, MIT, https://motion.dev/docs/animate. Alarm response desk (Stitch 5b0423b0 / 1e45b37d):
  // only compositor transforms/opacity animate; text never pulses. Loops: a breathing edge glow, light streaks
  // that flow with the move (down for 急跌, up for 急涨) and ripples behind the direction badge. Entry: a burst
  // from the edge and staggered panels. Everything stops on hide, acknowledgement and reduced motion.
  function sirenMotion({root,animate,reduced,hidden=()=>false}){
    let loops=[],transitions=[],loopSide='';
    const clear=list=>{for(const control of list)control?.cancel?.();};
    const enabled=()=>typeof animate==='function'&&!reduced.matches&&!hidden();
    const one=(selector,frames,options)=>{const node=root.querySelector(selector);return node?animate(node,frames,options):null;};
    function stop(){clear(loops);clear(transitions);loops=[];transitions=[];loopSide='';}
    function sync(open){
      if(!open||!enabled()){stop();return;}
      const side=root.dataset?.side||'lower';
      if(loops.length&&loopSide===side)return;
      clear(loops);loops=[];loopSide=side;
      const down=side==='lower',breathe={duration:1.8,repeat:Infinity,ease:'easeInOut'};
      loops.push(one('.sa-edge',{opacity:[.35,1,.35]},breathe));
      loops.push(one('.sa-vignette',{opacity:[.75,1,.75]},{...breathe,duration:3.2}));
      loops.push(one('.sa-scan',{opacity:[0,.9,0],transform:down?['translateY(-20vh)','translateY(50vh)','translateY(120vh)']:['translateY(120vh)','translateY(50vh)','translateY(-20vh)']},{duration:3.6,repeat:Infinity,ease:'easeInOut',repeatDelay:.8}));
      for(const [i,ring] of [...root.querySelectorAll('.sa-ripple')].entries())
        loops.push(animate(ring,{opacity:[.9,0],transform:['scale(.6)','scale(2.6)']},{duration:1.8,repeat:Infinity,delay:i*.6,ease:'easeOut'}));
      loops=loops.filter(Boolean);
    }
    function transition(kind){
      clear(transitions);transitions=[];if(!enabled())return;
      if(kind==='leave')transitions.push(animate(root,{opacity:[1,0],transform:['scale(1)','scale(.985)']},{duration:.26,ease:'easeIn'}));
      else if(kind==='enter'){
        transitions.push(animate(root,{opacity:[0,1]},{duration:.16,ease:'easeOut'}));
        transitions.push(one('.sa-burst',{opacity:[1,0],transform:['scale(.15)','scale(2.6)']},{duration:1,ease:[.16,1,.3,1]}));
        transitions.push(one('.sa-wall',{opacity:[0,1],transform:['scale(1.12)','scale(1)'],filter:['blur(8px)','blur(0px)']},{duration:1.2,ease:[.16,1,.3,1]}));
        transitions.push(one('.sa-edge',{opacity:[1,.5]},{duration:1.2,ease:'easeOut'}));
        for(const [i,panel] of [...root.querySelectorAll('.sa-rise')].entries())
          transitions.push(animate(panel,{opacity:[0,1],transform:['translateY(26px) scale(.98)','translateY(0) scale(1)']},{duration:.55,delay:.12+i*.07,ease:[.16,1,.3,1]}));
      }else for(const panel of root.querySelectorAll('.sa-rise'))transitions.push(animate(panel,{opacity:[.5,1],transform:['translateY(8px)','translateY(0)']},{duration:.28,ease:'easeOut'}));
      transitions=transitions.filter(Boolean);
    }
    return {sync,stop,enter:()=>transition('enter'),change:()=>transition('change'),leave:()=>transition('leave')};
  }
  // Full-screen particle field for the alarm wallpaper: short glowing streaks that drift with the move
  // (down for 急跌, up for 急涨). One canvas, one requestAnimationFrame loop, stopped whenever the alarm is
  // hidden, acknowledged or reduced motion is on; surge() briefly speeds it up when a new touch arrives.
  function particleField({canvas,raf=globalThis.requestAnimationFrame,caf=globalThis.cancelAnimationFrame,random=Math.random,ratio=()=>globalThis.devicePixelRatio||1}){
    let frame=null,parts=[],dir=1,color='#34d399',boost=0,last=0,w=0,h=0;
    const ctx=canvas?.getContext?.('2d');
    function size(){const r=Math.min(2,ratio());w=canvas.clientWidth||innerWidth;h=canvas.clientHeight||innerHeight;canvas.width=Math.round(w*r);canvas.height=Math.round(h*r);ctx?.setTransform(r,0,0,r,0,0);}
    function spawn(anywhere){const len=10+random()*46;return {x:random()*w,y:anywhere?random()*h:(dir>0?-len:h+len),len,speed:40+random()*150,alpha:.15+random()*.6,width:random()<.18?2:1};}
    function step(t){
      frame=raf(step);if(!ctx)return;const dt=Math.min(.05,(t-(last||t))/1000);last=t;boost=Math.max(0,boost-dt*1.4);
      ctx.clearRect(0,0,w,h);ctx.globalCompositeOperation='lighter';ctx.strokeStyle=color;ctx.lineCap='round';
      for(const p of parts){
        p.y+=dir*p.speed*(1+boost*2.5)*dt;
        if(dir>0?p.y-p.len>h:p.y+p.len<0)Object.assign(p,spawn(false));
        ctx.globalAlpha=p.alpha*(.7+boost*.6);ctx.lineWidth=p.width;ctx.beginPath();ctx.moveTo(p.x,p.y);ctx.lineTo(p.x,p.y-dir*p.len*(1+boost));ctx.stroke();
      }
      ctx.globalAlpha=1;ctx.globalCompositeOperation='source-over';
    }
    return {
      start({side='lower',tone}={}){
        if(!ctx||!raf)return false;dir=side==='lower'?1:-1;color=tone||color;
        if(frame===null){size();parts=Array.from({length:Math.round(Math.min(220,w*h/7000))},()=>spawn(true));last=0;frame=raf(step);}
        return true;
      },
      stop(){if(frame!==null)caf?.(frame);frame=null;ctx?.clearRect(0,0,w,h);},
      surge(){boost=1;},resize(){if(frame!==null)size();},
      get running(){return frame!==null;},get count(){return parts.length;}
    };
  }
  // Display facts for one active alert, from the stream's `live` block (spike_alert.py _live). Untrusted until here.
  function liveFacts(alert){
    const v=alert?.live,fin=x=>Number.isFinite(x)&&x>0,out={touches:[],reference:{},paper:[],window_end_ms:(alert?.last_ms||0)+30*60000};
    if(v&&typeof v==='object'){
      if(timestamp(v.window_end_ms))out.window_end_ms=v.window_end_ms;
      for(const t of Array.isArray(v.touches)?v.touches.slice(0,300):[])
        if(typeof t?.symbol==='string'&&/^[A-Z0-9]{1,32}-USDT-SWAP$/.test(t.symbol)&&fin(t.level)&&timestamp(t.candle_ms))
          out.touches.push({symbol:t.symbol,level:t.level,candle_ms:t.candle_ms,observed_ms:timestamp(t.observed_ms)?t.observed_ms:t.candle_ms,price:fin(t.price)?t.price:null});
      for(const p of Array.isArray(v.paper)?v.paper.slice(0,10):[])
        if(typeof p?.track==='string'&&/^live-[a-z0-9]{1,24}$/.test(p.track)&&Number.isInteger(p.opened)&&p.opened>=0)out.paper.push({track:p.track,opened:p.opened});
    }
    for(const s of CONTROLS){const r=v?.reference?.[s];out.reference[s]={at:fin(r?.at)?r.at:null,now:fin(r?.now)?r.now:null};}
    return out;
  }
  function deskFacts(alert,nowMs){
    const live=liveFacts(alert),d=alert.side==='lower'?1:-1,start=timestamp(alert.created_ms)?alert.created_ms:alert.last_ms;
    const flow=live.touches.map(t=>{const depth=t.price==null?null:d*(t.level-t.price)/t.level*100;return {...t,depth,outside:depth==null?null:depth>0,later:t.candle_ms>alert.last_ms};})
      .sort((a,b)=>b.observed_ms-a.observed_ms||a.symbol.localeCompare(b.symbol));
    const later=flow.filter(t=>t.later),move=s=>{const r=live.reference[s];return r.at&&r.now?(r.now/r.at-1)*100:null;};
    const first=[...live.touches].sort((a,b)=>a.candle_ms-b.candle_ms||a.observed_ms-b.observed_ms)[0]?.symbol||null;
    return {flow,later:later.length,latest:later.slice(0,3).map(t=>t.symbol),btc:move('BTC-USDT-SWAP'),eth:move('ETH-USDT-SWAP'),btcNow:live.reference['BTC-USDT-SWAP'].now,
      elapsed:Math.max(0,nowMs-start),remaining:Math.max(0,live.window_end_ms-nowMs),windowMs:Math.max(1,live.window_end_ms-alert.last_ms),paper:live.paper,first};
  }
  const bell='<svg aria-hidden="true" viewBox="0 0 24 24"><path d="M18 8a6 6 0 0 0-12 0c0 7-3 7-3 9h18c0-2-3-2-3-9M9 21h6"/></svg>';
  const chartIcon='<svg aria-hidden="true" viewBox="0 0 24 24"><path d="M3 3v18h18M7 14l4-4 3 3 6-6"/></svg>';
  const replayIcon='<svg aria-hidden="true" viewBox="0 0 24 24"><path d="M3 12a9 9 0 1 0 3-6.7L3 8M3 3v5h5M12 7v5l3 2"/></svg>';
  const linkIcon='<svg aria-hidden="true" viewBox="0 0 24 24"><path d="M7 17 17 7M9 7h8v8"/></svg>';
  const closeIcon='<svg aria-hidden="true" viewBox="0 0 24 24"><path d="M6 6l12 12M18 6 6 18"/></svg>';
  const speaker='<svg aria-hidden="true" viewBox="0 0 24 24"><path d="m11 5-6 4H2v6h3l6 4zM15 8a6 6 0 0 1 0 8M18 5a10 10 0 0 1 0 14"/></svg>';
  function install(env){
    const doc=env.document;if(doc.getElementById('spike-alarm-dock'))return env.SpikeAlarm;
    const dock=doc.createElement('div');dock.id='spike-alarm-dock';dock.dataset.globalAlert='';
    dock.innerHTML=`<button type="button" class="sa-trigger" aria-label="报警设置" aria-expanded="false" aria-controls="spike-alarm-settings">${bell}<i class="sa-badge" aria-hidden="true"></i></button>
<section id="spike-alarm-settings" class="sa-settings" aria-labelledby="spike-alarm-settings-title" hidden>
<header class="sa-settings-head"><span class="sa-settings-icon" aria-hidden="true">${bell}</span><h2 id="spike-alarm-settings-title">全市场报警</h2><div class="sa-status" role="status"><i class="sa-dot" aria-hidden="true"></i><span data-sa="connection"></span><span class="sa-health" data-sa="health" hidden></span><i class="sa-spin" aria-hidden="true"></i></div></header>
<button type="button" class="sa-reconnect" data-sa-action="reconnect" hidden>重新连接</button>
<div class="sa-tabs" role="group" aria-label="报警中心视图"><button type="button" data-sa-tab="overview" aria-pressed="true">概览</button><button type="button" data-sa-tab="history" aria-pressed="false">历史 <span data-sa="history-count"></span></button></div>
<a class="sa-book-link" data-breadth-book href="/?book=breadth-ema20#signals?pane=reclaim">全市场下轨反弹 · 策略小册 ↗</a>
<div class="sa-body">
<div class="sa-pane" data-sa-pane="overview">
<div class="sa-list"><div class="sa-row"><div class="sa-row-text"><span class="sa-row-title" id="spike-alarm-sound-title">报警声音</span><span class="sa-row-note" data-sa="sound-note"></span></div><button type="button" class="sa-switch" role="switch" aria-checked="false" aria-labelledby="spike-alarm-sound-title" data-sa-action="sound"><span></span></button></div><div class="sa-row"><div class="sa-row-text"><span class="sa-row-title" id="spike-alarm-test-title">测试报警</span><span class="sa-row-note" data-sa="test-note"></span></div><button type="button" class="sa-pill" aria-describedby="spike-alarm-test-title" data-sa-action="test">测试</button></div></div>
<p class="sa-settings-error" data-sa="test-error" role="status"></p>
<p class="sa-rule" data-sa="rules" hidden></p>
<p class="sa-last" data-sa="last"></p>
</div>
<div class="sa-pane" data-sa-pane="history" hidden>
<div class="sa-history-bar"><span data-sa="history-summary"></span></div>
<p class="sa-settings-error" data-sa="history-error" role="status"></p>
<div class="sa-history" data-sa="history" aria-label="历史报警"></div>
</div>
</div>
</section>`;
    const replay=doc.createElement('dialog');replay.id='spike-alarm-replay';replay.setAttribute('aria-labelledby','spike-alarm-replay-title');
    replay.innerHTML=`<div class="sa-rp"><header class="sa-rp-head"><button type="button" class="sa-rp-close" data-sa-replay="close" aria-label="关闭回看">${closeIcon}</button><div class="sa-rp-title"><h2 id="spike-alarm-replay-title"></h2><p></p></div><a class="sa-rp-link" data-sa-replay="monday" href="#signals?pane=reclaim">${linkIcon}1.618 页面</a><div class="sa-rp-periods" role="group" aria-label="K 线周期">${Object.keys(REPLAY_PERIODS).map(k=>`<button type="button" data-sa-period="${k}" aria-pressed="false">${k}</button>`).join('')}</div></header><div class="sa-rp-coins" role="group" aria-label="切换币种"></div><div class="sa-rp-bar" hidden><span class="sa-rp-info"></span><span class="sa-rp-legend"><i class="is-live"></i>实盘报警<i class="is-replay"></i>回测复算<span data-sa="rp-gap"></span></span><span class="sa-rp-nav"><button type="button" data-sa-nav="-1" aria-label="上一个报警">◀ 上一个</button><b data-sa="rp-count"></b><button type="button" data-sa-nav="1" aria-label="下一个报警">下一个 ▶</button></span></div><div class="sa-rp-chart"></div><p class="sa-rp-foot"></p></div>`;
    const dialog=doc.createElement('dialog');dialog.id='spike-alarm-dialog';dialog.dataset.globalAlert='';dialog.setAttribute('aria-labelledby','spike-alarm-title');dialog.setAttribute('aria-describedby','spike-alarm-summary');
    dialog.innerHTML=`<div class="sa-wall" aria-hidden="true"><div class="sa-chart"></div></div><canvas class="sa-particles" aria-hidden="true"></canvas><div class="sa-fx" aria-hidden="true"><div class="sa-tint"></div><div class="sa-scan"></div><div class="sa-vignette"></div><div class="sa-edge"></div><div class="sa-wave"></div><div class="sa-burst"></div></div>
<article class="sa-hud">
<header class="sa-head sa-rise"><span class="sa-dir-badge" aria-hidden="true"><i class="sa-ripple"></i><i class="sa-ripple"></i><b></b></span><div><h2 id="spike-alarm-title"></h2><p class="sa-time"></p></div><span class="sa-test" hidden>测试</span><nav class="sa-queue" aria-label="待确认报警"></nav></header>
<div class="sa-clock sa-rise" role="timer" aria-label="回测窗口剩余时间"><b class="sa-remaining">30:00</b><small class="sa-window-total">/ 30m</small><svg viewBox="0 0 36 36" aria-hidden="true"><circle cx="18" cy="18" r="16"/><circle class="sa-window-arc" cx="18" cy="18" r="16" pathLength="100"/></svg></div>
<section class="sa-stats" id="spike-alarm-summary"><p class="sa-stat sa-rise"><b data-kpi="within">0</b><span>币触及</span></p><p class="sa-stat sa-rise" data-kpi-box="later"><b data-kpi="later">+0</b><span>新增</span></p><p class="sa-stat sa-rise"><b data-kpi="btc">—</b><span>BTC</span></p></section>
<div class="sa-chart-tabs sa-rise" role="group" aria-label="切换壁纸币种"></div>
<footer class="sa-bar sa-rise"><button type="button" class="sa-sound" data-sa-action="sound" aria-label="启用报警声音">${speaker}</button><button type="button" class="sa-open-replay" aria-label="打开回看（同时确认）" title="打开回看">${replayIcon}</button><button type="button" class="sa-ack">知道了</button><a class="sa-open-page" href="#signals?pane=reclaim" aria-label="打开 1.618 页面（同时确认）" title="1.618 页面">${linkIcon}</a></footer>
<p class="sa-status"><span class="sa-connection" role="status"></span><span class="sa-error" role="status"></span><span class="sa-audio" data-sa="sound-state"></span></p>
</article>`;
    doc.body.append(dock,dialog,replay);
    const $=selector=>dialog.querySelector(selector),panel=dock.querySelector('.sa-settings'),trigger=dock.querySelector('.sa-trigger');
    const reduced=env.matchMedia('(prefers-reduced-motion: reduce)');let state,shown=null,closing=null,focusBefore=null,queueKey='',audioState='off';
    const motion=sirenMotion({root:dialog,animate:env.Motion?.animate,reduced,hidden:()=>doc.hidden});
    const audio=sound({AudioContext:env.AudioContext||env.webkitAudioContext,change:value=>{audioState=value;syncSound();}});
    const labels={on:'声音已启用',off:'声音未启用',blocked:'声音被暂停，请点击启用',unsupported:'浏览器不支持声音',enabling:'正在启用声音…'};
    const soundNotes={off:'需在本页点击一次才能发声',blocked:'浏览器暂停了声音，请再点一次',unsupported:'此浏览器不支持声音',enabling:'正在启用…'};
    const statusText={live:'推送已连接',connecting:'正在连接…',reconnecting:'正在重连…',offline:'推送已断开',invalid:'推送数据异常',unsupported:'浏览器无法接收推送'};
    function syncSound(){
      const connected=state?.connection==='live',status=connected?(audioState==='on'?'声音开':'静音'):state?.connection==='connecting'?'连接中':'重连中';
      dock.dataset.connection=state?.connection||'connecting';dock.dataset.sound=audioState;
      trigger.title='报警 · '+status;trigger.setAttribute('aria-label','报警设置 · '+status);
      $('[data-sa=sound-state]').textContent=labels[audioState]+(audioState==='on'&&state?.alerts.length?' · 循环提示中':'');
      const on=audioState==='on',label=on?'静音':'启用报警声音',busy=['unsupported','enabling'].includes(audioState),toggle=dock.querySelector('.sa-switch'),speakerButton=$('.sa-sound');
      toggle.setAttribute('aria-checked',String(on));toggle.disabled=busy;
      dock.querySelector('[data-sa=sound-note]').textContent=on||audioState==='off'?'':soundNotes[audioState];
      speakerButton.title=label;speakerButton.setAttribute('aria-label',label);speakerButton.setAttribute('aria-pressed',String(on));speakerButton.disabled=busy;
    }
    function settings(open){
      const was=!panel.hidden;panel.hidden=!open;trigger.setAttribute('aria-expanded',String(open));
      if(open&&!was){refreshHistory();(dock.querySelector(`[data-sa-tab=${tab}]`)).focus();}
    }
    // ---- History (read on demand) ----
    let tab='overview',historySeen=null,lastData=null,fetchedAt=0;const expanded=new Set();
    const historyStore=historyLoader({request:(url,opts)=>env.fetch(url,opts),later:env.setTimeout.bind(env),cancel:env.clearTimeout.bind(env),change:renderHistory});
    const serverNow=()=>lastData?lastData.now_ms+Math.max(0,env.Date.now()-fetchedAt):env.Date.now();
    function refreshHistory(){historySeen=state?state.alerts.map(a=>a.id).join(','):null;historyStore.load();}
    function setTab(next){
      tab=next;for(const b of dock.querySelectorAll('[data-sa-tab]'))b.setAttribute('aria-pressed',String(b.dataset.saTab===tab));
      for(const pane of dock.querySelectorAll('[data-sa-pane]'))pane.hidden=pane.dataset.saPane!==tab;
    }
    const marks={sent:['✓','ok'],failed:['✕','bad'],unknown:['?','wait'],pending:['…','muted'],no_channel:['—','muted']};
    const channel=(name,value)=>{const [m,t]=marks[value]||marks.unknown;return `<span class="sa-ch-${t}">${name} ${m}</span>`;};
    const stateText={acked:'已确认',pending:'待确认',expired:'未确认'};
    function historyRow(a){
      const st=alertStatus(a),down=a.side==='lower',open=expanded.has(a.id),preview=a.symbols.slice(0,4).map(coin).join(' ')+(a.symbols.length>4?' 等':'');
      const coins=replayCoins(a),chart=coins.length?'':' disabled title="测试报警没有行情"';
      return `<div class="sa-h" data-side="${a.side}" data-state="${st}"${a.test?' data-test=""':''}>
<button type="button" class="sa-h-main" data-sa-row="${a.id}" aria-expanded="${open}" aria-controls="spike-alarm-h-${a.id}"><i class="sa-h-dir" aria-hidden="true">${down?'↘':'↗'}</i><span class="sa-h-text"><span class="sa-h-title"><b>${down?'急跌':'急涨'} · ${a.n} 个币</b>${a.test?'<small class="sa-h-test">测试</small>':`<em>${down?'做多方向':'做空方向'}</em>`}<span class="sa-h-state">${stateText[st]}</span></span><span class="sa-h-meta">${short(a.last_ms)} · ${preview}</span></span></button>
<button type="button" class="sa-h-chart" data-sa-chart="${a.id}" aria-label="查看 ${short(a.last_ms)} 报警后的走势"${chart}>${chartIcon}<span>走势</span></button>
<div class="sa-h-detail" id="spike-alarm-h-${a.id}"${open?'':' hidden'}><dl><dt>触及</dt><dd>首次 ${hms(a.first_ms)} → 最后 ${hms(a.last_ms)}</dd><dt>推送</dt><dd class="sa-h-ch">${a.test?'<span class="sa-ch-muted">测试不推送</span>':channel('Telegram',a.telegram)+channel('Bark',a.bark)}</dd><dt>确认</dt><dd>${a.ack_ms!=null?short(a.ack_ms)+':'+hms(a.ack_ms).slice(6):st==='pending'?'待确认（全屏报警中）':'未确认 · 已超过 6 小时'}</dd></dl>
<div class="sa-h-coins">${coins.length?a.symbols.map(s=>`<button type="button" data-sa-chart="${a.id}" data-symbol="${esc(s)}" aria-label="查看 ${coin(s)} 报警后走势">${coin(s)}</button>`).join(''):a.symbols.slice(0,12).map(s=>`<span>${coin(s)}</span>`).join('')+(a.symbols.length>12?`<span>+${a.symbols.length-12}</span>`:'')}</div>
${coins.length?`<button type="button" class="sa-h-open" data-sa-chart="${a.id}">${chartIcon}查看走势</button>`:''}</div></div>`;
    }
    function renderHistory(){
      const s=historyStore.state(),d=s.data;
      if(d!==lastData){lastData=d;fetchedAt=env.Date.now();}
      const now=serverNow(),real=d?d.alerts.filter(a=>!a.test):[],rows=real,q=k=>dock.querySelector(`[data-sa=${k}]`);
      q('history-count').textContent=d?String(real.length):'';
      const health=q('health');health.hidden=!d;
      if(d)health.innerHTML=`${d.workerAlive?'':'<span class="sa-ch-bad">检测未运行</span> · '}TG ${d.channels.telegram?'✓':'—'} · Bark ${d.channels.bark?'✓':'—'}`;
      const rules=q('rules');rules.hidden=!d;
      if(d)rules.textContent=`${d.window_minutes} 分钟 ≥${d.threshold} 币 · 冷却 ${d.cooldown_minutes} 分钟${d.notifyOnly?' · 只通知':''}`;
      q('last').textContent=real[0]?`上次报警 ${short(real[0].last_ms)} · ${ago(real[0].last_ms,now)}`:d?'还没有真实报警记录':'';
      q('history-summary').textContent=!d?(s.loading?'正在读取…':''):`最近 ${rows.length} 条真实报警`;
      q('history-error').textContent=s.error;
      const list=q('history'),focused=list.contains(doc.activeElement)?doc.activeElement:null,key=focused&&(focused.dataset.saRow?'[data-sa-row="'+focused.dataset.saRow+'"]':focused.matches('.sa-h-chart')?'.sa-h-chart[data-sa-chart="'+focused.dataset.saChart+'"]':'');
      list.innerHTML=!d?'':rows.length?rows.map(historyRow).join(''):`<div class="sa-empty"><span aria-hidden="true">${bell}</span><b>暂无报警记录</b><small>条件较严格，只有极端插针时才会出现</small></div>`;
      if(key)list.querySelector(key)?.focus({preventScroll:true});
    }
    // ---- Alert replay viewer: read-only OKX closed bars around the alert ----
    let replayState=null,replayChart=null,replayOpener=null,chartReady=null,replayPeriod='15m';
    try{const saved=env.localStorage?.getItem('spike.alarm.replayPeriod');if(REPLAY_PERIODS[saved])replayPeriod=saved;}catch{}
    function loadAsset(name,global){
      if(global&&env[global])return Promise.resolve();
      const css=name.endsWith('.css'),existing=doc.querySelector(css?`link[href^="/static/${name}"]`:`script[src^="/static/${name}"]`);
      if(css&&existing)return Promise.resolve();
      return new Promise((resolve,reject)=>{
        const el=existing||doc.createElement(css?'link':'script'),timer=env.setTimeout(()=>reject(Error('图表组件加载超时')),20000);
        el.addEventListener('load',()=>{env.clearTimeout(timer);global&&!env[global]?reject(Error('图表组件加载失败')):resolve();},{once:true});
        el.addEventListener('error',()=>{env.clearTimeout(timer);reject(Error('图表组件加载失败'));},{once:true});
        if(!existing){const url=env.SpikeModules?.url?env.SpikeModules.url(name):'/static/'+name;if(css){el.rel='stylesheet';el.href=url;}else{el.src=url;el.async=false;}doc.head.append(el);}
      });
    }
    function ensureChart(){
      return chartReady||=(async()=>{
        if(!env.SpikeChartTools)throw Error('图表组件不可用，请刷新页面');
        await loadAsset('range-chart.css');
        for(const [name,global] of [['public-chart-cache.js','SpikePublicChartCache'],['chart-history.js','SpikeChartHistory'],['range-chart.js','SpikeRangeChart'],['monday-fib-chart.js','SpikeMondayFibChart']])await loadAsset(name,global);
      })().catch(error=>{chartReady=null;throw error;});
    }
    let replayed=null,display=null,jumpTimer=null;
    const MAX_JUMP_PAGES=30;
    async function loadReplayed(){
      if(replayed)return replayed;
      try{const r=await env.fetch(env.SpikeModules?.url?env.SpikeModules.url('spike-alarm-replay.json'):'/static/spike-alarm-replay.json',{cache:'no-store'});const v=await r.json();replayed=v?.schema===1&&Array.isArray(v.alarms)?v:{alarms:[],end_ms:null};}
      catch{replayed={alarms:[],end_ms:null};}
      return replayed;
    }
    async function loadDisplay(){
      if(display&&env.Date.now()-display.at<60000)return display.value;
      const r=await env.fetch('/api/monday-fib/display',{cache:'no-store',credentials:'same-origin'});if(!r.ok)throw Error('1.618 区间读取失败');
      display={at:env.Date.now(),value:await r.json()};return display.value;
    }
    const timeline=()=>alertTimeline(lastData?.alerts||[],replayed?.alarms||[]);
    function overlay(instance,data){
      const st=replayState,step=REPLAY_PERIODS[data?.bar];if(!st||!step||!instance.series||!instance.rows?.length)return;
      const items=rayItems(instance.rows,timeline(),step,st.alert.id);
      try{instance.series.setMarkers(items.map(it=>({time:it.time,position:it.side==='lower'?'belowBar':'aboveBar',color:it.live?'#e11d48':'#c2677c',shape:it.live?(it.side==='lower'?'arrowUp':'arrowDown'):'circle',text:it.selected?'报警 '+(st.long?short(it.ms):hm(it.ms)):''})));}catch{/* Markers are optional decoration. */}
      if(!instance.saRays&&instance.series.attachPrimitive){instance.saRays=new AlertRays(instance);instance.series.attachPrimitive(instance.saRays);}
      if(instance.saRays){instance.saRays.items=items;instance.saRays.requestUpdate?.();}
      const scale=instance.chart?.timeScale();if(!scale)return;
      if(!st.long){
        // First paint of each coin/period shows the whole window: 12 bars before the alert through the latest bar.
        const fit=st.alert.id+'|'+data.symbol+'|'+data.bar+'|'+instance.rows.length;
        if(instance.saFit!==fit){instance.saFit=fit;try{scale.setVisibleLogicalRange({from:-1,to:instance.rows.length+3});}catch{/* keep default view */}}
        return;
      }
      const j=st.jump;if(!j||j.symbol!==data.symbol||j.period!==data.bar)return;
      const rows=instance.rows,target=Math.floor(st.alert.last_ms/step)*step/1000;
      if(rows[0].time>target){
        if(data.history_has_more===false||j.pages>=MAX_JUMP_PAGES){st.jump=null;barNote(`这个周期最多回看到 ${short(rows[0].time*1000)}，切到 4H 可看更早的报警`);return;}
        if(j.first!==rows[0].time){j.first=rows[0].time;j.pages++;}
        barNote(`正在回看到 ${short(st.alert.last_ms)}…`);
        env.requestAnimationFrame(()=>{try{scale.setVisibleLogicalRange({from:-2,to:Math.min(rows.length,140)});}catch{}});
        return;
      }
      st.jump=null;env.clearTimeout(jumpTimer);barNote('');
      const i=rows.findIndex(r=>r.time>=target);
      env.requestAnimationFrame(()=>{try{scale.setVisibleLogicalRange({from:Math.max(-5,i-50),to:i+RAY_BARS+50});}catch{}});
    }
    const rq=selector=>replay.querySelector(selector);
    function barNote(text){const info=rq('.sa-rp-info');info.textContent=text||'往左拖动回看更早 · 显示全部报警';info.classList.toggle('is-note',!!text);}
    function replayMessage(text){replayChart?.hide();replayMode='';rq('.sa-rp-chart').innerHTML=`<p class="sa-rp-empty" role="status">${esc(text)}</p>`;}
    let replayMode='';
    function drawReplayChrome(){
      const {alert:a,coins,symbol,period,long}=replayState,down=a.side==='lower',step=REPLAY_PERIODS[period],old=new Date(a.last_ms).getFullYear()!==new Date(serverNow()).getFullYear();
      rq('h2').innerHTML=`${down?'急跌':'急涨'} · ${a.live?a.n:'≥25'} 个币 <em class="${down?'is-long':'is-short'}">${down?'做多方向':'做空方向'}</em>`;
      rq('.sa-rp-title p').textContent=a.live?`${short(a.first_ms)} → ${hm(a.last_ms)} · 北京时间`:`${old?new Date(a.last_ms).getFullYear()+'-':''}${short(a.last_ms)} · 北京时间 · 回测复算`;
      rq('.sa-rp-link').href='#signals?'+new URLSearchParams({pane:'reclaim',symbol,period});rq('.sa-rp-link').setAttribute('aria-label',`打开 ${coin(symbol)} 的 1.618 页面`);
      for(const b of replay.querySelectorAll('[data-sa-period]'))b.setAttribute('aria-pressed',String(b.dataset.saPeriod===period));
      rq('.sa-rp-coins').innerHTML=coins.map(s=>`<button type="button" data-sa-symbol="${esc(s)}" aria-pressed="${s===symbol}">${coin(s)}${CONTROLS.includes(s)&&!a.symbols.includes(s)?' · 对照':''}</button>`).join('');
      rq('.sa-rp-coins [aria-pressed=true]')?.scrollIntoView?.({block:'nearest',inline:'nearest'});
      const bar=rq('.sa-rp-bar');bar.hidden=!long;
      if(long){
        const list=timeline(),i=list.findIndex(x=>x.id===a.id),live=list.filter(x=>x.live),end=replayed?.end_ms;
        rq('[data-sa=rp-count]').textContent=`第 ${i+1} / ${list.length} 个`;
        rq('[data-sa-nav="-1"]').disabled=i<=0;rq('[data-sa-nav="1"]').disabled=i<0||i>=list.length-1;
        rq('[data-sa=rp-gap]').textContent=end&&live.length&&live[0].first_ms-end>2*86400000?`${short(end).slice(0,5)} 至 ${short(live[0].first_ms).slice(0,5)} 之间无记录`:'';
        if(!st_jumping())barNote('');
      }
      rq('.sa-rp-foot').textContent=long?`OKX 已收盘 K 线 · ${coin(symbol)} ${period} · 每条射线 ${RAY_BARS} 根 · 回测复算来自 Binance 5m 首次触价（≥25 币，至 ${replayed?.end_ms?short(replayed.end_ms).slice(0,5):'—'}）· 只读回看，不下单`
        :`OKX 已收盘 K 线 · ${coin(symbol)} ${period} · 报警前 ${span(12*step)} / 报警后最多 ${span((REPLAY_AFTER+13)*step)} · 射线 ${RAY_BARS} 根 · 只读回看，不下单`;
    }
    const st_jumping=()=>!!replayState?.jump;
    async function showReplayChart(){
      const current=replayState;if(!current)return;
      if(!replayChart){replayMessage('正在加载图表组件…');try{await ensureChart();await loadReplayed();}catch(error){if(replayState===current)replayMessage(error.message);return;}if(replayState!==current)return;replayChart=env.SpikeMondayFibChart.create({decorate:overlay});rq('.sa-rp-chart').replaceChildren();}
      // Owner 2026-11-06 ("历史的稍微看远一点点…k线和信号也加载不出来"): every coin can be dragged back in
      // history with all alarms marked (was majors only; others showed a fixed window and blank space).
      // Same rule as the 1.618 page: a recovering quote stream still has valid Monday levels.
      let d=null;try{d=await loadDisplay();}catch{/* fall back to the fixed window */}
      if(replayState!==current)return;
      const row=d?.items?.find(x=>x.symbol===current.symbol&&['high','low','upper','lower'].every(k=>Number.isFinite(x[k])));
      current.long=!!row;
      drawReplayChrome();
      const host=rq('.sa-rp-chart'),mode=current.long?'long':'window';
      if(replayMode&&replayMode!==mode)replayChart.hide();
      if(current.long){
        current.jump={symbol:current.symbol,period:current.period,pages:0,first:null};barNote(`正在回看到 ${short(current.alert.last_ms)}…`);
        env.clearTimeout(jumpTimer);jumpTimer=env.setTimeout(()=>{if(replayState===current&&current.jump){current.jump=null;barNote('历史 K 线加载较慢，可手动往左拖动继续回看');}},30000);
        replayMode=mode;replayChart.show(host,row,d.week,{period:current.period,ma:false,internal:false});
        replayChart.setMa(false);// same coin and period: show() keeps its chart, so redraw once to run the jump
        return;
      }
      const w=replayWindow(current.alert,current.period,serverNow());
      if(!w||!w.ready){replayMessage(`报警所在的 ${current.period} K 线还没收盘，请切换更小周期或稍后再看`);return;}
      replayMode=mode;
      replayChart.showReplay(host,'spike-alert',{id:'alert-'+current.alert.id,symbol:current.symbol,fill_ms:w.fill_ms,exit_ms:w.exit_ms,week:env.SpikePublicChartCache?.weekAt(current.alert.first_ms)},{period:current.period,paper:true});
    }
    function openReplay(id,symbol,opener){
      const a=lastData?.alerts.find(x=>x.id===id),coins=replayCoins(a);if(!coins.length)return;
      const fallback=a.symbols.find(s=>CONTROLS.includes(s))||a.symbols[0];
      replayState={alert:{...a,live:true},coins,symbol:coins.includes(symbol)?symbol:fallback,period:replayPeriod};
      if(!replay.open){replayOpener={id,node:opener||doc.activeElement};replay.showModal();}
      showReplayChart();rq('.sa-rp-close').focus({preventScroll:true});
    }
    function stepAlert(delta){
      const list=timeline(),i=list.findIndex(x=>x.id===replayState?.alert.id),next=list[i+delta];if(!next)return;
      replayState={...replayState,alert:next,jump:null};showReplayChart();
    }
    replay.addEventListener('click',e=>{
      if(e.target===replay||e.target.closest('[data-sa-replay=close]')){replay.close();return;}
      if(e.target.closest('[data-sa-replay=monday]')){replay.close();settings(false);return;}
      const nav=e.target.closest('[data-sa-nav]');if(nav&&!nav.disabled){stepAlert(Number(nav.dataset.saNav));return;}
      const symbol=e.target.closest('[data-sa-symbol]')?.dataset.saSymbol,period=e.target.closest('[data-sa-period]')?.dataset.saPeriod;
      if(symbol&&replayState&&symbol!==replayState.symbol){replayState={...replayState,symbol,jump:null};showReplayChart();}
      if(period&&replayState&&period!==replayState.period){replayPeriod=period;try{env.localStorage?.setItem('spike.alarm.replayPeriod',period);}catch{}replayState={...replayState,period,jump:null};showReplayChart();}
    });
    replay.addEventListener('close',()=>{replayChart?.hide();replayMode='';env.clearTimeout(jumpTimer);replayState=null;rq('.sa-rp-chart').replaceChildren();const back=replayOpener?.node?.isConnected?replayOpener.node:replayOpener&&dock.querySelector(`.sa-h-chart[data-sa-chart="${replayOpener.id}"]`);if(back?.getClientRects().length)back.focus({preventScroll:true});replayOpener=null;});
    function finishClose(){closing=null;if(state.alerts.length)return;stopDesk();motion.stop();dialog.classList.remove('sa-leaving');dialog.close();shown=null;if(focusBefore?.isConnected&&focusBefore.getClientRects().length&&!focusBefore.closest('[inert]'))focusBefore.focus({preventScroll:true});else trigger.focus({preventScroll:true});}
    // ---- Alarm response desk (Stitch 5b0423b0 layout, 1e45b37d long/short themes) ----
    let deskChart=null,deskAlert=null,deskSymbol='',deskTimer=null,deskTabs='',deskShown=null,lastLater=0;const seenFlow=new Set();
    const pct=v=>v==null?'—':(v>0?'+':'')+v.toFixed(2)+'%',clockText=ms=>{const t=Math.floor(ms/1000);return String(Math.floor(t/60)).padStart(2,'0')+':'+String(t%60).padStart(2,'0');};
    function countUp(node,to,prefix=''){
      if(reduced.matches||!env.requestAnimationFrame){node.textContent=prefix+to;return;}
      const from=Number(node.dataset.value||0),t0=env.performance.now();node.dataset.value=String(to);
      const step=t=>{const k=Math.min(1,(t-t0)/700),v=Math.round(from+(to-from)*(1-Math.pow(1-k,3)));node.textContent=prefix+v;if(k<1)env.requestAnimationFrame(step);};
      env.requestAnimationFrame(step);
    }
    function deskOverlay(instance,data){
      const a=deskAlert,step=REPLAY_PERIODS[data?.bar];if(!a||!step||!instance.series||!instance.rows?.length)return;
      // Wallpaper: scale to the candles only, so price fills the screen; a 1.618 line shows when price is near it.
      // The numbers sit at the right (desktop) or top-right (phone): keep the newest candles clear of them.
      const narrow=!!env.matchMedia?.('(max-width:700px)').matches;
      if(!instance.saCandleScale){instance.saCandleScale=true;try{instance.series.applyOptions({autoscaleInfoProvider:original=>original()});instance.chart.priceScale('right').applyOptions({scaleMargins:narrow?{top:.36,bottom:.22}:{top:.16,bottom:.14}});}catch{/* keep shared scale */}}
      const down=a.side==='lower',color=down?'#10b981':'#f43f5e',items=rayItems(instance.rows,[{...a,live:true}],step,a.id).map(it=>({...it,color}));
      try{instance.series.setMarkers(items.map(it=>({time:it.time,position:down?'belowBar':'aboveBar',color,shape:down?'arrowUp':'arrowDown',text:'报警 '+hm(a.last_ms)})));}catch{/* optional */}
      if(!instance.saRays&&instance.series.attachPrimitive){instance.saRays=new AlertRays(instance);instance.series.attachPrimitive(instance.saRays);}
      if(instance.saRays){instance.saRays.items=items;instance.saRays.requestUpdate?.();}
      if(data.symbol===deskSymbol&&data.candles?.length&&$('.sa-wall').dataset.state)wallState('');
      const fit=a.id+'|'+data.symbol;
      if(instance.saDeskFit!==fit&&items.length){instance.saDeskFit=fit;const from=Math.max(-2,items[0].from-60),pad=narrow?8:Math.max(12,Math.round((instance.rows.length-from)*.42));try{instance.chart.timeScale().setVisibleLogicalRange({from,to:instance.rows.length+pad});}catch{/* keep default */}}
    }
    // Loading shows a faint candle skeleton; a coin switch crossfades once the new candles are drawn.
    let wallTimer=null;
    function wallState(value){const wall=$('.sa-wall');env.clearTimeout(wallTimer);if(value){wall.dataset.state=value;wallTimer=env.setTimeout(()=>{delete wall.dataset.state;},8000);}else delete wall.dataset.state;}
    async function showDeskChart(){
      const a=deskAlert,symbol=deskSymbol,host=$('.sa-chart');if(!a||!symbol)return;
      if(!host.querySelector('canvas'))wallState('loading');
      try{await ensureChart();}catch{return;}
      let d;try{d=await loadDisplay();}catch{return;}
      if(deskAlert!==a||deskSymbol!==symbol)return;
      const row=d.items?.find(x=>x.symbol===symbol&&['high','low','upper','lower'].every(k=>Number.isFinite(x[k])));
      if(!row){deskChart?.hide();host.replaceChildren();return;}
      if(!deskChart){host.replaceChildren();deskChart=env.SpikeMondayFibChart.create({decorate:deskOverlay});}
      deskChart.show(host,row,d.week,{period:'5m',ma:false,internal:false});deskChart.setMa(false);
    }
    function deskTick(){
      if(!deskAlert||!dialog.open)return;
      const f=deskFacts(deskAlert,controller.now());
      $('.sa-window-total').textContent='/ '+Math.round(f.windowMs/60000)+'m';
      $('.sa-remaining').textContent=f.remaining?clockText(f.remaining):'00:00';
      $('.sa-window-arc').style.strokeDashoffset=String(100*(1-f.remaining/f.windowMs));
      dialog.classList.toggle('sa-window-closed',!f.remaining);
    }
    let particles=null;
    function fieldSync(){
      particles||=particleField({canvas:$('.sa-particles'),raf:env.requestAnimationFrame?.bind(env),caf:env.cancelAnimationFrame?.bind(env)});
      if(dialog.open&&deskAlert&&!reduced.matches&&!doc.hidden)particles.start({side:deskAlert.side,tone:deskAlert.side==='lower'?'#34d399':'#fb7185'});else particles.stop();
    }
    function startDeskTimer(){env.clearInterval(deskTimer);deskTimer=env.setInterval(()=>{if(!doc.hidden)deskTick();},1000);deskTick();fieldSync();}
    function stopDesk(){env.clearInterval(deskTimer);deskTimer=null;deskChart?.hide();particles?.stop();wallState('');deskAlert=null;deskShown=null;deskTabs='';seenFlow.clear();}
    function surge(){
      particles?.surge();if(reduced.matches||typeof env.Motion?.animate!=='function')return;
      env.Motion.animate($('.sa-wave'),{opacity:[.85,0],transform:['scale(.2)','scale(2.4)']},{duration:1.1,ease:[.16,1,.3,1]});
      const box=$('[data-kpi-box=later]');box.classList.remove('is-bump');void box.offsetWidth;box.classList.add('is-bump');
    }
    function renderDesk(alert,changed){
      const down=alert.side==='lower',f=deskFacts(alert,controller.now());deskAlert=alert;
      if(changed){
        $('.sa-test').hidden=!alert.test;$('.sa-dir-badge b').textContent=down?'↓':'↑';
        $('#spike-alarm-title').textContent=down?'急跌 · 做多':'急涨 · 做空';$('.sa-time').textContent=hms(alert.last_ms);
        countUp($('[data-kpi=within]'),alert.n);seenFlow.clear();lastLater=f.later;$('[data-kpi=later]').textContent='+'+f.later;$('[data-kpi=later]').dataset.value=String(f.later);
      }else if(f.later!==lastLater){countUp($('[data-kpi=later]'),f.later,'+');if(f.later>lastLater)surge();lastLater=f.later;}
      const btc=$('[data-kpi=btc]');btc.textContent=pct(f.btc);btc.dataset.tone=f.btc==null||Math.abs(f.btc)<.005?'':((down?1:-1)*f.btc>0?'with':'against');
      // Owner 2026-11-06 (Stitch 1ebf02793e9d42b2b38e5476ac68c2f5): one chip row — BTC, ETH, then the touched
      // coins with their depth past 1.618. The first chip is the default wallpaper; any chip switches it.
      const fresh=deskShown===alert.id,touched=new Map();
      for(const t of f.flow)if(!touched.has(t.symbol))touched.set(t.symbol,t);
      const tabs=[...new Set(['BTC-USDT-SWAP','ETH-USDT-SWAP',...(alert.test?[]:[...touched.keys()])])].slice(0,16);
      if(changed||!tabs.includes(deskSymbol))deskSymbol=tabs[0];
      const chip=s=>{const t=touched.get(s),key=t?s+'|'+t.candle_ms:'',isNew=fresh&&t&&!seenFlow.has(key);if(t)seenFlow.add(key);
        return `<button type="button" data-desk-symbol="${esc(s)}" aria-pressed="${s===deskSymbol}"${t?` data-state="${t.outside==null?'':t.outside?'out':'in'}"`:''}${isNew?' class="is-new"':''}><b>${coin(s)}</b>${t&&t.depth!=null?`<span>${Math.abs(t.depth).toFixed(1)}%</span>`:''}</button>`;};
      const tabKey=tabs.map(s=>s+':'+(touched.get(s)?.depth?.toFixed(1)??'')).join(',')+'|'+deskSymbol;
      deskShown=alert.id;
      if(tabKey!==deskTabs){deskTabs=tabKey;const row=$('.sa-chart-tabs'),left=row.scrollLeft,focus=doc.activeElement?.dataset?.deskSymbol;row.innerHTML=tabs.map(chip).join('');row.scrollLeft=left;if(focus)row.querySelector(`[data-desk-symbol="${focus}"]`)?.focus({preventScroll:true});}
      const page=$('.sa-open-page');page.href='#signals?'+new URLSearchParams({pane:'reclaim',symbol:deskSymbol,period:'5m'});page.setAttribute('aria-label',`打开 ${coin(deskSymbol)} 的 1.618 页面（同时确认）`);
      $('.sa-open-replay').disabled=alert.test;
      if(changed){showDeskChart();fieldSync();}
      deskTick();
    }
    function render(next){
      state=next;syncSound();
      if(!panel.hidden&&historySeen!==next.alerts.map(a=>a.id).join(','))refreshHistory();
      const connection=next.connection==='live'?'报警推送已连接':next.connection==='connecting'?'正在连接报警推送…':next.connection==='unsupported'?'此浏览器无法接收报警，请使用新版浏览器。':next.connection==='invalid'?'报警数据异常，正在等待有效推送。':next.connection==='offline'?'报警推送已断开，请重新连接。':'报警推送已断开，正在重连…';
      const strip=dock.querySelector('.sa-status');dock.dataset.tone=next.connection==='live'?'ok':['connecting','reconnecting'].includes(next.connection)?'wait':'bad';
      strip.querySelector('[data-sa=connection]').textContent=statusText[next.connection]||statusText.reconnecting;strip.title=connection;$('.sa-connection').textContent=next.connection==='live'?'':connection;
      dock.querySelector('[data-sa-action=reconnect]').hidden=!['offline','unsupported','invalid'].includes(next.connection);
      const testButton=dock.querySelector('[data-sa-action=test]');testButton.disabled=next.testPending||next.connection!=='live';testButton.textContent=next.testPending?'测试中…':'测试';dock.querySelector('[data-sa=test-note]').textContent=next.connection==='live'?'':'连接后可测试';dock.querySelector('[data-sa=test-error]').textContent=next.testError;
      audio.setActive(next.running&&next.alerts.length>0&&!['connecting','unsupported'].includes(next.connection));
      const alert=next.alerts.find(a=>a.id===next.selected);
      if(!alert){if(dialog.open&&!closing){dialog.classList.add('sa-leaving');motion.leave();closing=env.setTimeout(finishClose,reduced.matches?0:240);}return;}
      if(closing){env.clearTimeout(closing);closing=null;dialog.classList.remove('sa-leaving');motion.enter();}
      settings(false);
      const changed=shown!==alert.id;shown=alert.id;dialog.dataset.alertId=String(alert.id);dialog.dataset.side=alert.side;
      const nextQueue=next.alerts.map(a=>a.id).join(',')+'|'+alert.id;
      if(queueKey!==nextQueue){queueKey=nextQueue;$('.sa-queue').innerHTML=next.alerts.length>1?next.alerts.map(a=>`<button type="button" data-alert-id="${a.id}" aria-pressed="${a.id===alert.id}">${esc(at(a.last_ms))} · ${a.side==='lower'?'急跌':'急涨'}${a.test?' · 测试':''}</button>`).join(''):'';}
      renderDesk(alert,changed);
      if(changed&&dialog.open)motion.change();
      $('.sa-error').textContent=next.ackError;$('.sa-ack').disabled=next.ackPending;$('.sa-ack').textContent=next.ackPending?'确认中…':next.ackError?'重试确认':'知道了';
      if(!dialog.open){env.SpikeWorkspaceDisplay?.setMobileNavigation(false,false);focusBefore=doc.activeElement;dialog.inert=false;dialog.showModal();$('.sa-ack').focus({preventScroll:true});motion.enter();startDeskTimer();}
      motion.sync(next.running);
    }
    const controller=create({source:url=>new env.EventSource(url),request:(url,opts)=>env.fetch(url,opts),clock:()=>env.performance.now(),later:env.setTimeout.bind(env),cancel:env.clearTimeout.bind(env),change:render});
    trigger.addEventListener('click',()=>settings(panel.hidden));
    for(const host of [dock,dialog])host.addEventListener('click',e=>{
      const action=e.target.closest('[data-sa-action]')?.dataset.saAction;
      if(action==='sound'){if(audioState==='on')audio.mute();else audio.enable();}
      if(action==='test')controller.test();if(action==='reconnect')controller.reconnect();
      const id=e.target.closest('[data-alert-id]')?.dataset.alertId;if(id)controller.select(Number(id));
      const tabButton=e.target.closest('[data-sa-tab]');if(tabButton)setTab(tabButton.dataset.saTab);
      const row=e.target.closest('[data-sa-row]');
      if(row){const rid=Number(row.dataset.saRow),open=!expanded.has(rid);if(open)expanded.add(rid);else expanded.delete(rid);row.setAttribute('aria-expanded',String(open));doc.getElementById('spike-alarm-h-'+rid).hidden=!open;}
      const chart=e.target.closest('[data-sa-chart]');if(chart&&!chart.disabled)openReplay(Number(chart.dataset.saChart),chart.dataset.symbol,chart);
    });
    $('.sa-ack').addEventListener('click',()=>controller.acknowledge());
    dialog.addEventListener('click',e=>{
      const tab=e.target.closest('[data-desk-symbol]');
      if(tab&&tab.dataset.deskSymbol!==deskSymbol){deskSymbol=tab.dataset.deskSymbol;deskTabs='';if(deskAlert){renderDesk(deskAlert,false);if(!reduced.matches)wallState('switching');showDeskChart();}}
    });
    $('.sa-open-replay').addEventListener('click',async()=>{
      const a=deskAlert,coins=replayCoins(a);if(!a||!coins.length)return;
      if(!await controller.acknowledge(a.id))return;
      replayState={alert:{...a,live:true},coins,symbol:coins.includes(deskSymbol)?deskSymbol:coins[0],period:replayPeriod};
      replayOpener={id:a.id,node:trigger};env.setTimeout(()=>{if(!replay.open)replay.showModal();showReplayChart();rq('.sa-rp-close').focus({preventScroll:true});},reduced.matches?0:260);
    });
    $('.sa-open-page').addEventListener('click',()=>{if(deskAlert)controller.acknowledge(deskAlert.id);});
    dialog.addEventListener('cancel',e=>e.preventDefault());
    doc.addEventListener('click',e=>{if(!dock.contains(e.target)&&!replay.contains(e.target))settings(false);});
    doc.addEventListener('keydown',e=>{if(e.key==='Escape'&&!dialog.open&&!replay.open&&!panel.hidden){settings(false);trigger.focus();}});
    const hide=()=>{controller.stop();audio.stop();motion.stop();historyStore.abort();stopDesk();if(replay.open)replay.close();};env.addEventListener('pagehide',hide);env.addEventListener('pageshow',()=>controller.start());
    // The shared chart skips its fetch while the page is hidden and does not retry by itself. Alarms often fire in
    // a background tab, so refresh the wallpaper and replay charts once the page is visible again.
    doc.addEventListener('visibilitychange',()=>{if(!doc.hidden){controller.refreshTime();if(dialog.open)deskChart?.retry();if(replay.open)replayChart?.retry();}motion.sync(dialog.open&&state.running);fieldSync();});
    reduced.addEventListener('change',()=>{motion.sync(dialog.open&&state.running);fieldSync();});
    env.addEventListener('resize',()=>particles?.resize());
    controller.start();return {state:controller.state};
  }
  if(typeof module!=='undefined')module.exports={create,sound,packet,historyPacket,historyLoader,liveFacts,deskFacts,particleField,replayWindow,replayCoins,alertStatus,alertTimeline,rayItems,RAY_BARS,MAJORS,REPLAY_PERIODS,ACTIVE_MS,sirenMotion};
  if(typeof document!=='undefined')window.SpikeAlarm=install(window);
})();
