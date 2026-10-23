/* Optional mobile feedback. W3C Vibration API; unsupported browsers stay silent.
 * Trusted user actions only, no polling/scroll feedback or trade-result claims.
 */
(() => {
 'use strict';
 function create(env){
  const key='spike-web-haptics-v1',doc=env.document,nav=env.navigator;
  let enabled=true,last=-Infinity;
  try{enabled=env.localStorage.getItem(key)!=='off';}catch(_){}
  const mobile=()=>Boolean(env.matchMedia?.('(pointer: coarse)').matches);
  const supported=()=>typeof nav?.vibrate==='function';
  function pulse(kind='light'){
   try{
    if(!enabled||!mobile()||!supported()||doc?.hidden||nav.userActivation?.hasBeenActive===false)return false;
    const now=env.performance.now();if(now-last<(kind==='edge'?40:120))return false;last=now;
    return nav.vibrate(kind==='edge'?18:8);
   }catch(_){return false;}
  }
  function setEnabled(value){enabled=Boolean(value);try{env.localStorage.setItem(key,enabled?'on':'off');}catch(_){}if(enabled)pulse();}
  function read(){try{enabled=env.localStorage.getItem(key)!=='off';}catch(_){}}
  return {pulse,setEnabled,read,supported,mobile,enabled:()=>enabled,key};
 }
 function install(env){
  const api=create(env),doc=env.document,button=doc.getElementById('workspace-haptics'),ranges=new WeakMap();
  function sync(){if(!button)return;button.hidden=!api.mobile();button.disabled=!api.supported();button.setAttribute('aria-pressed',String(api.enabled()&&api.supported()));button.textContent=api.supported()?`震动 · ${api.enabled()?'开':'关'}`:'震动 · 浏览器不支持';}
  button?.addEventListener('click',()=>{api.setEnabled(!api.enabled());sync();});
  const eligible=target=>target&&!target.closest('[inert]')&&!target.matches(':disabled,[aria-disabled="true"]');
  doc.addEventListener('click',e=>{
   if(!e.isTrusted)return;
   const b=e.target.closest?.('button,summary,a[data-terminal-route]');
   if(!eligible(b)||b===button||b.matches('[data-quick-grabber],[aria-pressed="true"],[aria-current="page"]'))return;
   api.pulse();
  },true);
  doc.addEventListener('pointerdown',e=>{const n=e.target;if(n.matches?.('input[type="range"]'))ranges.set(n,Number(n.value));},{passive:true});
  doc.addEventListener('input',e=>{
   const n=e.target;if(!e.isTrusted||!eligible(n)||!n.matches?.('input[type="range"]'))return;
   const value=Number(n.value),previous=ranges.get(n),min=Number(n.min||0),max=Number(n.max||100);
   ranges.set(n,value);if(previous===undefined||previous===value||max<=min)return;
   if(value===min||value===max)api.pulse('edge');else if(Math.floor((previous-min)/(max-min)*20)!==Math.floor((value-min)/(max-min)*20))api.pulse();
  },true);
  doc.addEventListener('change',e=>{if(e.isTrusted&&eligible(e.target)&&e.target.matches?.('select,input[type="checkbox"]'))api.pulse();},true);
  env.addEventListener('storage',e=>{if(e.key===api.key||e.key===null){api.read();sync();}});
  env.matchMedia?.('(pointer: coarse)').addEventListener?.('change',sync);sync();return api;
 }
 if(typeof module!=='undefined')module.exports={create,install};
 if(typeof window!=='undefined')window.SpikeHaptics=install(window);
})();
