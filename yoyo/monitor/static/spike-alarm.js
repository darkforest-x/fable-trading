/* Global notify-only alarm. Contract: spike_alert.py; Stitch b8f0df9af24b482c874dc308a5bd50dd.
 * One EventSource per document; no request polling. Audio requires a user gesture.
 * MDN: Server-sent_events/Using_server-sent_events, HTMLDialogElement/showModal,
 * Web_Audio_API/Best_practices. Six-hour expiry uses server time + monotonic elapsed time.
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
  function create({source=url=>new EventSource(url),request=(url,opts)=>fetch(url,opts),clock=()=>performance.now(),later=setTimeout,cancel=clearTimeout,change=()=>{}}={}){
    let stream=null,running=false,connection='connecting',alerts=[],selected=null,serverNow=0,received=0,expiry=null,generation=0,testPending=false,testError='';
    const acknowledged=new Set(),pending=new Map(),errors=new Map();
    const now=()=>serverNow+Math.max(0,clock()-received);
    const state=()=>({running,connection,alerts:[...alerts],selected,ackPending:pending.has(selected),ackError:errors.get(selected)||'',testPending,testError});
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
            const v=packet(JSON.parse(event.data));serverNow=v.now_ms;received=clock();
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
  const speaker='<svg aria-hidden="true" viewBox="0 0 24 24"><path d="m11 5-6 4H2v6h3l6 4zM15 8a6 6 0 0 1 0 8M18 5a10 10 0 0 1 0 14"/></svg>';
  function install(env){
    const doc=env.document;if(doc.getElementById('spike-alarm-dock'))return env.SpikeAlarm;
    const dock=doc.createElement('div');dock.id='spike-alarm-dock';dock.dataset.globalAlert='';
    dock.innerHTML=`<button type="button" class="sa-trigger" aria-label="报警设置" aria-expanded="false" aria-controls="spike-alarm-settings">${bell}</button><section id="spike-alarm-settings" class="sa-settings" aria-label="报警设置" hidden><h2>全市场报警</h2><p data-sa="connection" role="status"></p><button type="button" data-sa-action="sound">启用报警声音</button><p class="sa-note">声音需要在本页点击启用。</p><button type="button" data-sa-action="test">测试报警</button><p class="sa-note">测试会显示在已打开的页面，不推送消息。</p><button type="button" data-sa-action="reconnect" hidden>重新连接</button><p data-sa="test-error" role="status"></p></section>`;
    const dialog=doc.createElement('dialog');dialog.id='spike-alarm-dialog';dialog.dataset.globalAlert='';dialog.setAttribute('aria-labelledby','spike-alarm-title');dialog.setAttribute('aria-describedby','spike-alarm-summary');
    dialog.innerHTML=`<div class="sa-scene" aria-hidden="true"><div class="sa-atmosphere"></div><div class="sa-curtain sa-curtain-left"></div><div class="sa-curtain sa-curtain-right"></div><div class="sa-wave"></div><div class="sa-rings"><i class="sa-ring"></i><i class="sa-ring"></i><i class="sa-ring"></i></div><div class="sa-arrival"></div><div class="sa-vignette"></div></div><article class="sa-stage"><header class="sa-header"><span class="sa-count"></span><span class="sa-test" hidden>测试报警</span></header><nav class="sa-queue" aria-label="待确认报警"></nav><div class="sa-content"><p class="sa-eyebrow">全市场异动</p><h2 id="spike-alarm-title">全市场插针报警</h2><div class="sa-direction"></div><p id="spike-alarm-summary"></p><p class="sa-time"></p><div class="sa-coins" tabindex="0" aria-label="触及币种"></div></div><footer class="sa-footer"><p class="sa-connection" role="status"></p><p class="sa-error" role="status"></p><div class="sa-actions"><button type="button" class="sa-ack">知道了</button><button type="button" class="sa-sound" data-sa-action="sound" aria-label="启用报警声音">${speaker}</button></div><p class="sa-audio" data-sa="sound-state"></p></footer></article>`;
    doc.body.append(dock,dialog);
    const $=selector=>dialog.querySelector(selector),panel=dock.querySelector('.sa-settings'),trigger=dock.querySelector('.sa-trigger');
    const reduced=env.matchMedia('(prefers-reduced-motion: reduce)');let state,shown=null,closing=null,focusBefore=null,queueKey='',audioState='off';
    const motion=sirenMotion({root:dialog,animate:env.Motion?.animate,reduced,hidden:()=>doc.hidden});
    const audio=sound({AudioContext:env.AudioContext||env.webkitAudioContext,change:value=>{audioState=value;syncSound();}});
    const labels={on:'声音已启用',off:'声音未启用',blocked:'声音被暂停，请点击启用',unsupported:'浏览器不支持声音',enabling:'正在启用声音…'};
    function syncSound(){
      const connected=state?.connection==='live',status=connected?(audioState==='on'?'声音开':'静音'):state?.connection==='connecting'?'连接中':'重连中';
      dock.dataset.connection=state?.connection||'connecting';dock.dataset.sound=audioState;
      trigger.title='报警 · '+status;trigger.setAttribute('aria-label','报警设置 · '+status);
      $('[data-sa=sound-state]').textContent=labels[audioState]+(audioState==='on'&&state?.alerts.length?' · 循环提示中':'');
      for(const button of [dock.querySelector('[data-sa-action=sound]'),$('[data-sa-action=sound]')]){const label=audioState==='on'?'静音':'启用报警声音';if(!button.classList.contains('sa-sound'))button.textContent=label;button.title=label;button.setAttribute('aria-label',label);button.setAttribute('aria-pressed',String(audioState==='on'));button.disabled=['unsupported','enabling'].includes(audioState);}
    }
    function settings(open){panel.hidden=!open;trigger.setAttribute('aria-expanded',String(open));if(open)dock.querySelector('[data-sa-action=sound]').focus();}
    function finishClose(){closing=null;if(state.alerts.length)return;motion.stop();dialog.classList.remove('sa-leaving');dialog.close();shown=null;if(focusBefore?.isConnected&&focusBefore.getClientRects().length&&!focusBefore.closest('[inert]'))focusBefore.focus({preventScroll:true});else trigger.focus({preventScroll:true});}
    function render(next){
      state=next;syncSound();
      const connection=next.connection==='live'?'报警推送已连接':next.connection==='connecting'?'正在连接报警推送…':next.connection==='unsupported'?'此浏览器无法接收报警，请使用新版浏览器。':next.connection==='invalid'?'报警数据异常，正在等待有效推送。':next.connection==='offline'?'报警推送已断开，请重新连接。':'报警推送已断开，正在重连…';
      dock.querySelector('[data-sa=connection]').textContent=connection;$('.sa-connection').textContent=next.connection==='live'?'':connection;
      dock.querySelector('[data-sa-action=reconnect]').hidden=!['offline','unsupported','invalid'].includes(next.connection);
      const testButton=dock.querySelector('[data-sa-action=test]');testButton.disabled=next.testPending||next.connection!=='live';testButton.textContent=next.testPending?'正在测试…':'测试报警';dock.querySelector('[data-sa=test-error]').textContent=next.testError;
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
    });
    $('.sa-ack').addEventListener('click',()=>controller.acknowledge());
    dialog.addEventListener('cancel',e=>e.preventDefault());
    doc.addEventListener('click',e=>{if(!dock.contains(e.target))settings(false);});
    doc.addEventListener('keydown',e=>{if(e.key==='Escape'&&!dialog.open&&!panel.hidden){settings(false);trigger.focus();}});
    const hide=()=>{controller.stop();audio.stop();motion.stop();};env.addEventListener('pagehide',hide);env.addEventListener('pageshow',()=>controller.start());
    doc.addEventListener('visibilitychange',()=>{if(!doc.hidden)controller.refreshTime();motion.sync(dialog.open&&state.running);});
    reduced.addEventListener('change',()=>motion.sync(dialog.open&&state.running));
    controller.start();return {state:controller.state};
  }
  if(typeof module!=='undefined')module.exports={create,sound,packet,ACTIVE_MS,sirenMotion};
  if(typeof document!=='undefined')window.SpikeAlarm=install(window);
})();
