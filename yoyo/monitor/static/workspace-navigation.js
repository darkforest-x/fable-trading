/* Browser history owns route identity; session state owns reading position, never trade drafts. */
(() => {
  'use strict';
  const KEY='spike.workspace.navigation.v1', entries=new Map(), adapters=new Map();
  let current='', pending=null, restoring=false;
  try { for(const [key,value] of Object.entries(JSON.parse(sessionStorage.getItem(KEY)||'{}'))) entries.set(key,value); } catch { /* Session-only fallback. */ }
  if ('scrollRestoration' in history) history.scrollRestoration='manual';
  const persist=()=>{try{sessionStorage.setItem(KEY,JSON.stringify(Object.fromEntries(entries)));}catch{}};
  const route=()=>location.hash.slice(1).split('?')[0];
  const params=()=>Object.fromEntries(new URLSearchParams(location.hash.split('?')[1]||''));
  function capture() {
    if(!current)return;
    const old=entries.get(current)||{};
    entries.set(current,{...old,top:window.scrollY||0}); persist();
  }
  function begin(view, updateHash) {
    capture();
    if(updateHash){
      const remembered=view==='yolo-detect'? '#yolo-detect' : entries.get(`@${view}`)?.hash || `#${view}`;
      if(location.hash!==remembered)history.pushState(null,'',remembered);
    }
    current=location.hash;
    pending={hash:current,top:Math.max(0,Number(entries.get(current)?.top)||0),until:Date.now()+4000};
    entries.set(`@${view}`,{hash:current});persist();
    return current;
  }
  function restore(view) {
    if(route()!==view)return;
    restoring=true;
    try {adapters.get(view)?.(params());} finally {restoring=false;}
  }
  function settled() {
    if(!pending||pending.hash!==location.hash)return;
    const target=pending;
    requestAnimationFrame(()=>{
      if(pending!==target)return;
      window.scrollTo({top:target.top,behavior:'instant'});
      if(document.documentElement.scrollHeight-innerHeight>=target.top || Date.now()>target.until)pending=null;
    });
  }
  function sync(view, values) {
    if(restoring||route()!==view)return;
    const next=new URLSearchParams();
    for(const [key,value] of Object.entries(values))if(value!==null&&value!==undefined&&value!=='')next.set(key,String(value));
    const hash=`#${view}${next.size?'?'+next:''}`;
    if(hash===location.hash){settled();return;}
    const before=params(), major=['tab','pane','layout','chapter','book','mode','unit'].some(k=>String(before[k]||'')!==String(values[k]||''));
    capture();
    // An initial canonical URL does not insert an extra Back step.
    (major&&location.hash.includes('?')?history.pushState:history.replaceState).call(history,null,'',hash);
    current=hash;entries.set(`@${view}`,{hash});persist();
    if(pending)pending.hash=hash;
  }
  for(const event of ['wheel','touchstart','pointerdown','keydown'])window.addEventListener(event,()=>{pending=null;},{passive:true});
  let scrollTimer;
  window.addEventListener('scroll',()=>{clearTimeout(scrollTimer);scrollTimer=setTimeout(()=>{if(!pending)capture();},150);},{passive:true});
  window.addEventListener('pagehide',capture);
  new MutationObserver(settled).observe(document.getElementById('main'),{subtree:true,childList:true});
  window.SpikeNavigation={begin,restore,sync,settled,register(view,apply){adapters.set(view,apply);restore(view);}};
})();
