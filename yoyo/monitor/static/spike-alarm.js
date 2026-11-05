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
    return coins.includes('BTC-USDT-SWAP')?coins:['BTC-USDT-SWAP',...coins];
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
    return {start,stop,state,acknowledge,test,refreshTime(){prune();emit();},select(id){if(alerts.some(a=>a.id===id)){selected=id;emit();}},reconnect(){stop();start();}};
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
  // Motion 14.6.0, MIT, https://motion.dev/docs/animate. Animate only compositor
  // transforms/opacity: text never pulses, and no layout/frame timer is needed.
  function sirenMotion({root,animate,reduced,hidden=()=>false}){
    let loops=[],transitions=[];
    const clear=list=>{for(const control of list)control.cancel();};
    const enabled=()=>typeof animate==='function'&&!reduced.matches&&!hidden();
    const one=(selector,frames,options)=>animate(root.querySelector(selector),frames,options);
    function stop(){clear(loops);clear(transitions);loops=[];transitions=[];}
    function sync(open){
      if(!open||!enabled()){stop();return;}
      if(loops.length)return;
      const breathe={duration:1.8,repeat:Infinity,ease:'easeInOut'};
      loops.push(one('.sa-atmosphere',{opacity:[.22,1,.22],transform:['scale(.96)','scale(1.12)','scale(.96)']},breathe));
      loops.push(one('.sa-curtain-left',{opacity:[.12,1,.12],transform:['translateX(-12%)','translateX(8%)','translateX(-12%)']},breathe));
      loops.push(one('.sa-curtain-right',{opacity:[1,.12,1],transform:['translateX(-8%)','translateX(12%)','translateX(-8%)']},breathe));
      loops.push(one('.sa-wave',{opacity:[0,.85,0],transform:['translateX(-110%) skewX(-18deg)','translateX(0%) skewX(-18deg)','translateX(110%) skewX(-18deg)']},{...breathe,duration:2.1}));
      for(const [i,ring] of [...root.querySelectorAll('.sa-ring')].entries())loops.push(animate(ring,{opacity:[0,.85,0],transform:['scale(.35)','scale(.85)','scale(1.7)']},{duration:1.8,repeat:Infinity,delay:i*.6,ease:'easeOut'}));
    }
    function transition(kind){
      clear(transitions);transitions=[];if(!enabled())return;
      if(kind==='leave')transitions.push(animate(root,{opacity:[1,0]},{duration:.24,ease:'easeIn'}));
      else if(kind==='enter'){
        transitions.push(animate(root,{opacity:[0,1]},{duration:.18,ease:'easeOut'}));
        transitions.push(one('.sa-arrival',{opacity:[.8,0],transform:['scale(.25)','scale(1.8)']},{duration:.65,ease:'easeOut'}));
        transitions.push(one('.sa-content',{opacity:[0,1],transform:['translateY(22px)','translateY(0)']},{duration:.6,ease:[.16,1,.3,1]}));
      }else transitions.push(one('.sa-content',{opacity:[.6,1],transform:['translateY(8px)','translateY(0)']},{duration:.24,ease:'easeOut'}));
    }
    return {sync,stop,enter:()=>transition('enter'),change:()=>transition('change'),leave:()=>transition('leave')};
  }
  const bell='<svg aria-hidden="true" viewBox="0 0 24 24"><path d="M18 8a6 6 0 0 0-12 0c0 7-3 7-3 9h18c0-2-3-2-3-9M9 21h6"/></svg>';
  const chartIcon='<svg aria-hidden="true" viewBox="0 0 24 24"><path d="M3 3v18h18M7 14l4-4 3 3 6-6"/></svg>';
  const closeIcon='<svg aria-hidden="true" viewBox="0 0 24 24"><path d="M6 6l12 12M18 6 6 18"/></svg>';
  const speaker='<svg aria-hidden="true" viewBox="0 0 24 24"><path d="m11 5-6 4H2v6h3l6 4zM15 8a6 6 0 0 1 0 8M18 5a10 10 0 0 1 0 14"/></svg>';
  function install(env){
    const doc=env.document;if(doc.getElementById('spike-alarm-dock'))return env.SpikeAlarm;
    const dock=doc.createElement('div');dock.id='spike-alarm-dock';dock.dataset.globalAlert='';
    dock.innerHTML=`<button type="button" class="sa-trigger" aria-label="报警设置" aria-expanded="false" aria-controls="spike-alarm-settings">${bell}<i class="sa-badge" aria-hidden="true"></i></button>
<section id="spike-alarm-settings" class="sa-settings" aria-labelledby="spike-alarm-settings-title" hidden>
<header class="sa-settings-head"><span class="sa-settings-icon" aria-hidden="true">${bell}</span><div><h2 id="spike-alarm-settings-title">全市场报警</h2><p>多币 5 分钟内触及 1.618 时全屏提醒</p></div></header>
<div class="sa-tabs" role="group" aria-label="报警中心视图"><button type="button" data-sa-tab="overview" aria-pressed="true">概览</button><button type="button" data-sa-tab="history" aria-pressed="false">历史 <span data-sa="history-count"></span></button></div>
<div class="sa-body">
<div class="sa-pane" data-sa-pane="overview">
<div class="sa-status" role="status"><div class="sa-status-row"><i class="sa-dot" aria-hidden="true"></i><span data-sa="connection"></span><span class="sa-status-aside">实时</span><i class="sa-spin" aria-hidden="true"></i><button type="button" data-sa-action="reconnect" hidden>重新连接</button></div><p class="sa-health" data-sa="health" hidden></p></div>
<div class="sa-list"><div class="sa-row"><div class="sa-row-text"><span class="sa-row-title" id="spike-alarm-sound-title">报警声音</span><span class="sa-row-note" data-sa="sound-note"></span></div><button type="button" class="sa-switch" role="switch" aria-checked="false" aria-labelledby="spike-alarm-sound-title" data-sa-action="sound"><span></span></button></div><div class="sa-row"><div class="sa-row-text"><span class="sa-row-title" id="spike-alarm-test-title">测试报警</span><span class="sa-row-note" data-sa="test-note"></span></div><button type="button" class="sa-pill" aria-describedby="spike-alarm-test-title" data-sa-action="test">测试</button></div></div>
<p class="sa-settings-error" data-sa="test-error" role="status"></p>
<ul class="sa-rules" data-sa="rules" hidden></ul>
<p class="sa-last" data-sa="last"></p>
</div>
<div class="sa-pane" data-sa-pane="history" hidden>
<div class="sa-history-bar"><span data-sa="history-summary"></span><label class="sa-check"><input type="checkbox" data-sa="show-tests"/>显示测试</label></div>
<p class="sa-settings-error" data-sa="history-error" role="status"></p>
<div class="sa-history" data-sa="history" aria-label="历史报警"></div>
</div>
</div>
</section>`;
    const replay=doc.createElement('dialog');replay.id='spike-alarm-replay';replay.setAttribute('aria-labelledby','spike-alarm-replay-title');
    replay.innerHTML=`<div class="sa-rp"><header class="sa-rp-head"><button type="button" class="sa-rp-close" data-sa-replay="close" aria-label="关闭回看">${closeIcon}</button><div class="sa-rp-title"><h2 id="spike-alarm-replay-title"></h2><p></p></div><div class="sa-rp-periods" role="group" aria-label="K 线周期">${Object.keys(REPLAY_PERIODS).map(k=>`<button type="button" data-sa-period="${k}" aria-pressed="false">${k}</button>`).join('')}</div></header><div class="sa-rp-coins" role="group" aria-label="切换币种"></div><div class="sa-rp-chart"></div><p class="sa-rp-foot"></p></div>`;
    const dialog=doc.createElement('dialog');dialog.id='spike-alarm-dialog';dialog.dataset.globalAlert='';dialog.setAttribute('aria-labelledby','spike-alarm-title');dialog.setAttribute('aria-describedby','spike-alarm-summary');
    dialog.innerHTML=`<div class="sa-scene" aria-hidden="true"><div class="sa-atmosphere"></div><div class="sa-curtain sa-curtain-left"></div><div class="sa-curtain sa-curtain-right"></div><div class="sa-wave"></div><div class="sa-rings"><i class="sa-ring"></i><i class="sa-ring"></i><i class="sa-ring"></i></div><div class="sa-arrival"></div><div class="sa-vignette"></div></div><article class="sa-stage"><header class="sa-header"><span class="sa-count"></span><span class="sa-test" hidden>测试报警</span></header><nav class="sa-queue" aria-label="待确认报警"></nav><div class="sa-content"><p class="sa-eyebrow">全市场异动</p><h2 id="spike-alarm-title">全市场插针报警</h2><div class="sa-direction"></div><p id="spike-alarm-summary"></p><p class="sa-time"></p><div class="sa-coins" tabindex="0" aria-label="触及币种"></div></div><footer class="sa-footer"><p class="sa-connection" role="status"></p><p class="sa-error" role="status"></p><div class="sa-actions"><button type="button" class="sa-ack">知道了</button><button type="button" class="sa-sound" data-sa-action="sound" aria-label="启用报警声音">${speaker}</button></div><p class="sa-audio" data-sa="sound-state"></p></footer></article>`;
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
      dock.querySelector('[data-sa=sound-note]').textContent=on?(state?.alerts.length?'已启用 · 循环提示中':'已启用，报警时循环提示'):soundNotes[audioState];
      speakerButton.title=label;speakerButton.setAttribute('aria-label',label);speakerButton.setAttribute('aria-pressed',String(on));speakerButton.disabled=busy;
    }
    function settings(open){
      const was=!panel.hidden;panel.hidden=!open;trigger.setAttribute('aria-expanded',String(open));
      if(open&&!was){refreshHistory();(dock.querySelector(`[data-sa-tab=${tab}]`)).focus();}
    }
    // ---- History (read on demand) ----
    let tab='overview',showTests=false,historySeen=null,lastData=null,fetchedAt=0;const expanded=new Set();
    const historyStore=historyLoader({request:(url,opts)=>env.fetch(url,opts),later:env.setTimeout.bind(env),cancel:env.clearTimeout.bind(env),change:renderHistory});
    const serverNow=()=>lastData?lastData.now_ms+Math.max(0,env.Date.now()-fetchedAt):env.Date.now();
    function refreshHistory(){historySeen=state?.snapshots??null;historyStore.load();}
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
      const now=serverNow(),real=d?d.alerts.filter(a=>!a.test):[],rows=d?(showTests?d.alerts:real):[],q=k=>dock.querySelector(`[data-sa=${k}]`);
      q('history-count').textContent=d?String(real.length):'';
      const health=q('health');health.hidden=!d;
      if(d)health.innerHTML=`<span class="${d.workerAlive?'':'sa-ch-bad'}">${d.workerAlive?'检测运行中':'检测线程未运行'}</span> · Telegram ${d.channels.telegram?'✓':'未配置'} · Bark ${d.channels.bark?'✓':'未配置'}`;
      const rules=q('rules');rules.hidden=!d;
      if(d)rules.innerHTML=`<li>触发：${d.window_minutes} 分钟内同方向 ≥${d.threshold} 个币触及 1.618</li><li>同方向冷却 ${d.cooldown_minutes} 分钟</li>${d.notifyOnly?'<li>只通知，不动任何仓位</li>':''}`;
      q('last').textContent=real[0]?`上次报警 ${short(real[0].last_ms)} · ${ago(real[0].last_ms,now)}`:d?'还没有真实报警记录':'';
      const tests=d?d.alerts.length-real.length:0;
      q('history-summary').textContent=!d?(s.loading?'正在读取…':''):`最近 ${rows.length} 条`+(!showTests&&tests?` · 已隐藏 ${tests} 条测试`:'');
      q('history-error').textContent=s.error;
      const list=q('history'),focused=list.contains(doc.activeElement)?doc.activeElement:null,key=focused&&(focused.dataset.saRow?'[data-sa-row="'+focused.dataset.saRow+'"]':focused.matches('.sa-h-chart')?'.sa-h-chart[data-sa-chart="'+focused.dataset.saChart+'"]':'');
      list.innerHTML=!d?'':rows.length?rows.map(historyRow).join(''):`<div class="sa-empty"><span aria-hidden="true">${bell}</span><b>暂无报警记录</b><small>${tests?'只有测试报警，勾选“显示测试”查看':'条件较严格，只有极端插针时才会出现'}</small></div>`;
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
    function markAlert(instance,data){
      const a=replayState?.alert,step=REPLAY_PERIODS[data?.bar];if(!a||!step||!instance.series?.setMarkers)return;
      const times=new Set((instance.rows||[]).map(r=>r.time)),first=Math.floor(a.first_ms/step)*step/1000,last=Math.floor(a.last_ms/step)*step/1000,down=a.side==='lower';
      const list=[];if(times.has(first))list.push({time:first,position:down?'belowBar':'aboveBar',color:'#e11d48',shape:down?'arrowUp':'arrowDown',text:'报警 '+hm(a.first_ms)});
      if(last!==first&&times.has(last))list.push({time:last,position:down?'belowBar':'aboveBar',color:'#e11d48',shape:'circle',text:''});
      try{instance.series.setMarkers(list);}catch{/* Markers are optional decoration. */}
      // First paint of each coin/period shows the whole window: 12 bars before the alert through the latest bar.
      const fit=a.id+'|'+data.symbol+'|'+data.bar+'|'+(instance.rows||[]).length;
      if(instance.saFit!==fit&&instance.rows?.length){instance.saFit=fit;try{instance.chart.timeScale().setVisibleLogicalRange({from:-1,to:instance.rows.length+3});}catch{/* Older renderer: keep its default view. */}}
    }
    const rq=selector=>replay.querySelector(selector);
    function replayMessage(text){replayChart?.hide();rq('.sa-rp-chart').innerHTML=`<p class="sa-rp-empty" role="status">${esc(text)}</p>`;}
    function drawReplayChrome(){
      const {alert:a,coins,symbol,period}=replayState,down=a.side==='lower',step=REPLAY_PERIODS[period];
      rq('h2').innerHTML=`${down?'急跌':'急涨'} · ${a.n} 个币 <em class="${down?'is-long':'is-short'}">${down?'做多方向':'做空方向'}</em>`;
      rq('.sa-rp-title p').textContent=`${short(a.first_ms)} → ${hm(a.last_ms)} · 北京时间`;
      for(const b of replay.querySelectorAll('[data-sa-period]'))b.setAttribute('aria-pressed',String(b.dataset.saPeriod===period));
      rq('.sa-rp-coins').innerHTML=coins.map(s=>`<button type="button" data-sa-symbol="${esc(s)}" aria-pressed="${s===symbol}">${coin(s)}${a.symbols.includes(s)?'':' · 对照'}</button>`).join('');
      rq('.sa-rp-coins [aria-pressed=true]')?.scrollIntoView?.({block:'nearest',inline:'nearest'});
      rq('.sa-rp-foot').textContent=`OKX 已收盘 K 线 · ${coin(symbol)} ${period} · 报警前 ${span(12*step)} / 报警后最多 ${span((REPLAY_AFTER+13)*step)} · 只读回看，不下单`;
    }
    async function showReplayChart(){
      const current=replayState;if(!current)return;drawReplayChrome();
      const w=replayWindow(current.alert,current.period,serverNow());
      if(!w||!w.ready){replayMessage(`报警所在的 ${current.period} K 线还没收盘，请切换更小周期或稍后再看`);return;}
      if(!replayChart){replayMessage('正在加载图表组件…');try{await ensureChart();}catch(error){if(replayState===current)replayMessage(error.message);return;}if(replayState!==current)return;replayChart=env.SpikeMondayFibChart.create({decorate:markAlert});rq('.sa-rp-chart').replaceChildren();}
      replayChart.showReplay(rq('.sa-rp-chart'),'spike-alert',{id:'alert-'+current.alert.id,symbol:current.symbol,fill_ms:w.fill_ms,exit_ms:w.exit_ms,week:env.SpikePublicChartCache?.weekAt(current.alert.first_ms)},{period:current.period,paper:true});
    }
    function openReplay(id,symbol,opener){
      const a=lastData?.alerts.find(x=>x.id===id),coins=replayCoins(a);if(!coins.length)return;
      const fallback=a.symbols.find(s=>['BTC-USDT-SWAP','ETH-USDT-SWAP'].includes(s))||a.symbols[0];
      replayState={alert:a,coins,symbol:coins.includes(symbol)?symbol:fallback,period:replayPeriod};
      if(!replay.open){replayOpener={id,node:opener||doc.activeElement};replay.showModal();}
      showReplayChart();rq('.sa-rp-close').focus({preventScroll:true});
    }
    replay.addEventListener('click',e=>{
      if(e.target===replay||e.target.closest('[data-sa-replay=close]')){replay.close();return;}
      const symbol=e.target.closest('[data-sa-symbol]')?.dataset.saSymbol,period=e.target.closest('[data-sa-period]')?.dataset.saPeriod;
      if(symbol&&replayState&&symbol!==replayState.symbol){replayState={...replayState,symbol};showReplayChart();}
      if(period&&replayState&&period!==replayState.period){replayPeriod=period;try{env.localStorage?.setItem('spike.alarm.replayPeriod',period);}catch{}replayState={...replayState,period};showReplayChart();}
    });
    replay.addEventListener('close',()=>{replayChart?.hide();replayState=null;rq('.sa-rp-chart').replaceChildren();const back=replayOpener?.node?.isConnected?replayOpener.node:replayOpener&&dock.querySelector(`.sa-h-chart[data-sa-chart="${replayOpener.id}"]`);if(back?.getClientRects().length)back.focus({preventScroll:true});replayOpener=null;});
    function finishClose(){closing=null;if(state.alerts.length)return;motion.stop();dialog.classList.remove('sa-leaving');dialog.close();shown=null;if(focusBefore?.isConnected&&focusBefore.getClientRects().length&&!focusBefore.closest('[inert]'))focusBefore.focus({preventScroll:true});else trigger.focus({preventScroll:true});}
    function render(next){
      state=next;syncSound();
      if(!panel.hidden&&historySeen!==next.snapshots)refreshHistory();
      const connection=next.connection==='live'?'报警推送已连接':next.connection==='connecting'?'正在连接报警推送…':next.connection==='unsupported'?'此浏览器无法接收报警，请使用新版浏览器。':next.connection==='invalid'?'报警数据异常，正在等待有效推送。':next.connection==='offline'?'报警推送已断开，请重新连接。':'报警推送已断开，正在重连…';
      const strip=dock.querySelector('.sa-status');dock.dataset.tone=next.connection==='live'?'ok':['connecting','reconnecting'].includes(next.connection)?'wait':'bad';
      strip.querySelector('[data-sa=connection]').textContent=statusText[next.connection]||statusText.reconnecting;strip.title=connection;$('.sa-connection').textContent=next.connection==='live'?'':connection;
      dock.querySelector('[data-sa-action=reconnect]').hidden=!['offline','unsupported','invalid'].includes(next.connection);
      const testButton=dock.querySelector('[data-sa-action=test]');testButton.disabled=next.testPending||next.connection!=='live';testButton.textContent=next.testPending?'测试中…':'测试';dock.querySelector('[data-sa=test-note]').textContent=next.connection==='live'?'只在已打开的页面弹出，不推送消息':'连接后可测试';dock.querySelector('[data-sa=test-error]').textContent=next.testError;
      audio.setActive(next.running&&next.alerts.length>0&&!['connecting','unsupported'].includes(next.connection));
      const alert=next.alerts.find(a=>a.id===next.selected);
      if(!alert){if(dialog.open&&!closing){dialog.classList.add('sa-leaving');motion.leave();closing=env.setTimeout(finishClose,reduced.matches?0:240);}return;}
      if(closing){env.clearTimeout(closing);closing=null;dialog.classList.remove('sa-leaving');motion.enter();}
      settings(false);
      const changed=shown!==alert.id;shown=alert.id;dialog.dataset.alertId=String(alert.id);dialog.dataset.side=alert.side;
      $('.sa-count').textContent=`待确认 ${next.alerts.length} 条`;
      const nextQueue=next.alerts.map(a=>a.id).join(',')+'|'+alert.id;
      if(queueKey!==nextQueue){queueKey=nextQueue;$('.sa-queue').innerHTML=next.alerts.length>1?next.alerts.map(a=>`<button type="button" data-alert-id="${a.id}" aria-pressed="${a.id===alert.id}">${esc(at(a.last_ms))} · ${a.side==='lower'?'急跌':'急涨'}${a.test?' · 测试':''}</button>`).join(''):'';}
      if(changed){
        $('.sa-test').hidden=!alert.test;$('.sa-direction').textContent=alert.side==='lower'?'急跌 · 做多方向':'急涨 · 做空方向';
        $('#spike-alarm-summary').innerHTML=`<strong>${alert.n}</strong><span>个币 · 5 分钟内<br>触及${alert.side==='lower'?'下':'上'}方 <b>1.618</b></span>`;
        $('.sa-time').textContent=at(alert.last_ms)+'（北京时间）';$('.sa-coins').innerHTML=alert.symbols.map(s=>`<span>${esc(s.replace('-USDT-SWAP',''))}</span>`).join('');$('.sa-coins').scrollTop=0;
        if(dialog.open)motion.change();
      }
      $('.sa-error').textContent=next.ackError;$('.sa-ack').disabled=next.ackPending;$('.sa-ack').textContent=next.ackPending?'确认中…':next.ackError?'重试确认':'知道了';
      if(!dialog.open){env.SpikeWorkspaceDisplay?.setMobileNavigation(false,false);focusBefore=doc.activeElement;dialog.inert=false;dialog.showModal();$('.sa-ack').focus({preventScroll:true});motion.enter();}
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
    dock.querySelector('[data-sa=show-tests]').addEventListener('change',e=>{showTests=e.target.checked;renderHistory();});
    $('.sa-ack').addEventListener('click',()=>controller.acknowledge());
    dialog.addEventListener('cancel',e=>e.preventDefault());
    doc.addEventListener('click',e=>{if(!dock.contains(e.target)&&!replay.contains(e.target))settings(false);});
    doc.addEventListener('keydown',e=>{if(e.key==='Escape'&&!dialog.open&&!replay.open&&!panel.hidden){settings(false);trigger.focus();}});
    const hide=()=>{controller.stop();audio.stop();motion.stop();historyStore.abort();if(replay.open)replay.close();};env.addEventListener('pagehide',hide);env.addEventListener('pageshow',()=>controller.start());
    doc.addEventListener('visibilitychange',()=>{if(!doc.hidden)controller.refreshTime();motion.sync(dialog.open&&state.running);});
    reduced.addEventListener('change',()=>motion.sync(dialog.open&&state.running));
    controller.start();return {state:controller.state};
  }
  if(typeof module!=='undefined')module.exports={create,sound,packet,historyPacket,historyLoader,replayWindow,replayCoins,alertStatus,REPLAY_PERIODS,ACTIVE_MS,sirenMotion};
  if(typeof document!=='undefined')window.SpikeAlarm=install(window);
})();
