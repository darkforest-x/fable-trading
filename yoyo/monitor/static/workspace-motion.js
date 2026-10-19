/* Presentation-only motion. Route/task owners commit state immediately; motion
 * never postpones focus, form actions, data reads, or trading confirmations.
 * Only explicit navigation/interaction enters; polling DOM replacements do not.
 */
(() => {
  'use strict';
  function createMotion({reduced, hidden=()=>false, style=()=>null, ready=(_node,run)=>{run();return null;}}={}) {
    const running=new Map();
    let current=null, pendingCards=false, awaiting=null, generation=0;
    const enabled=()=>!reduced?.matches&&!hidden();
    function cancel(node){const animation=running.get(node);if(animation){running.delete(node);animation.cancel();}}
    function stop(){generation++;awaiting?.();awaiting=null;for(const node of [...running.keys()])cancel(node);}
    function enter(node,{kind='panel',delay=0}={}){
      if(!node)return null;
      cancel(node);
      if(!enabled()||!node.isConnected||!node.getClientRects().length||!node.animate)return null;
      const tokens=style(node), value=tokens?.getPropertyValue(kind==='route'?'--motion-enter':'--motion-panel');
      const duration=parseFloat(value)|| (kind==='route'?280:220);
      // Route containers can contain fixed charts: opacity never establishes a
      // new fixed-position containing block, unlike transform/translate.
      const frames=kind==='route'?[{opacity:.35},{opacity:1}]:[{opacity:0,translate:'0 6px'},{opacity:1,translate:'0 0'}];
      const animation=node.animate(frames,{duration,delay,easing:tokens?.getPropertyValue('--motion-ease').trim()||'cubic-bezier(.22,1,.36,1)',fill:'backwards'});
      animation.id='spike-'+kind;
      running.set(node,animation);
      animation.finished.then(()=>{if(running.get(node)===animation)running.delete(node);},()=>{if(running.get(node)===animation)running.delete(node);});
      return animation;
    }
    function route(name,node){
      if(name===current)return;
      stop();current=name;pendingCards=name==='signals';
      const token=generation;
      awaiting=ready(node,()=>{if(token===generation)enter(node,{kind:'route'});});
    }
    function cards(name,nodes){
      if(name!==current||!pendingCards)return;
      const visible=[...nodes].filter(node=>node.isConnected&&node.getClientRects().length).slice(0,6);
      if(!visible.length)return;
      pendingCards=false;
      visible.forEach((node,index)=>enter(node,{kind:'card',delay:index*24}));
    }
    reduced?.addEventListener?.('change',stop);
    return {enter,route,cards,cancel,stop};
  }
  if(typeof module!=='undefined')module.exports={createMotion};
  if(typeof document==='undefined')return;
  function ready(node,run){
    if(!node)return null;
    const usable=()=>[...node.querySelectorAll('button,input,select,table,article,details,canvas')].some(n=>n.getClientRects().length);
    if(usable()){run();return null;}
    // Cold routes initially contain only a loading message. Observe this one
    // route until its first usable surface, then disconnect before polling.
    let timer;
    const observer=new MutationObserver(()=>{if(usable()){cleanup();run();}});
    const cleanup=()=>{observer.disconnect();clearTimeout(timer);};
    observer.observe(node,{childList:true,subtree:true});timer=setTimeout(cleanup,8000);
    return cleanup;
  }
  const motion=createMotion({reduced:matchMedia('(prefers-reduced-motion: reduce)'),hidden:()=>document.hidden,style:node=>getComputedStyle(node),ready});
  const roots=new WeakSet();
  function attach(root){
    if(roots.has(root))return;roots.add(root);
    root.addEventListener('click',event=>{
      // Capture old state, animate after the owning listener/native action.
      // No mutation observer: data refresh and restored disclosures stay still.
      const summary=event.target.closest?.('summary'),details=summary?.parentElement;
      if(details?.tagName==='DETAILS'&&!details.open){
        requestAnimationFrame(()=>{if(details.open)for(const child of details.children)if(child!==summary)motion.enter(child);});
      }
      const tab=event.target.closest?.('[role="tab"][aria-controls]');
      if(tab&&tab.getAttribute('aria-selected')!=='true'){
        requestAnimationFrame(()=>{if(tab.getAttribute('aria-selected')==='true')motion.enter(root.getElementById(tab.getAttribute('aria-controls')));});
      }
    },true);
    root.addEventListener('keydown',event=>{
      const tab=event.target.closest?.('[role="tab"][aria-controls]');
      if(!tab||!['ArrowLeft','ArrowRight','Home','End'].includes(event.key))return;
      requestAnimationFrame(()=>{const selected=tab.closest('[role="tablist"]')?.querySelector('[aria-selected="true"]');if(selected&&selected!==tab)motion.enter(root.getElementById(selected.getAttribute('aria-controls')));});
    });
  }
  document.addEventListener('visibilitychange',()=>{if(document.hidden)motion.stop();});
  attach(document);
  window.SpikeMotion={...motion,attach};
})();
