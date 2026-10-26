/* Shared glass-panel dragging: chart gestures only belong to the canvas. */
(() => {
  'use strict';
  // Display coordinates are isolated from chart/data state. Only grip gestures move UI.
  function clampPanel(x,y,w,h,vw,vh){const gap=8;return {x:Math.max(gap,Math.min(Number.isFinite(x)?x:gap,Math.max(gap,vw-w-gap))),y:Math.max(gap,Math.min(Number.isFinite(y)?y:gap,Math.max(gap,vh-h-gap)))};}
  function mountLayout(root,env=window,key='spike:monday-fib:layout:v1'){
    const blocks=Array.from(root.querySelectorAll('[data-mf-movable]'));
    let saved={},drag=null,active=false,frame=null,dragFrame=null;
    try{saved=JSON.parse(env.localStorage?.getItem(key)||'{}')||{};if(typeof saved!=='object'||Array.isArray(saved))saved={};}catch{saved={};}
    const surface=()=>env.innerWidth<=760?'mobile':'desktop';
    const write=()=>{try{env.localStorage?.setItem(key,JSON.stringify(saved));}catch{/* Storage can be unavailable in private mode. */}};
    const resetStyle=b=>{for(const k of ['left','top','right','bottom','transform'])b.style[k]='';};
    const rect=b=>b.getBoundingClientRect();
    function move(b,x,y,r=rect(b)){const p=clampPanel(x,y,r.width,r.height,env.innerWidth,env.innerHeight);Object.assign(b.style,{left:p.x+'px',top:p.y+'px',right:'auto',bottom:'auto',transform:'none'});return p;}
    function remember(b){const r=rect(b),s=surface();if(!saved[s]||typeof saved[s]!=='object')saved[s]={};saved[s][b.dataset.mfMovable]={x:r.x/Math.max(1,env.innerWidth-r.width),y:r.y/Math.max(1,env.innerHeight-r.height)};write();}
    function restore(){
      if(!active||drag)return;
      for(const b of blocks){
        resetStyle(b);if((surface()==='mobile'&&b.dataset.mfMovable==='quick')||!b.getClientRects().length)continue;
        const p=saved[surface()]?.[b.dataset.mfMovable],r=rect(b);
        if(p&&Number.isFinite(p.x)&&Number.isFinite(p.y))move(b,p.x*(env.innerWidth-r.width),p.y*(env.innerHeight-r.height));
      }
    }
    function reset(b){if(saved[surface()])delete saved[surface()][b.dataset.mfMovable];resetStyle(b);write();}
    function paintDrag(){dragFrame=null;if(drag?.next)move(drag.block,drag.next.x,drag.next.y,drag);}
    function finish(commit=true){
      if(dragFrame!==null){env.cancelAnimationFrame?.(dragFrame);dragFrame=null;if(commit)paintDrag();}
      if(!drag)return;const d=drag;drag=null;d.block.classList.remove('mf-dragging');
      d.handle.releasePointerCapture?.(d.id);
      if(commit&&d.moved)remember(d.block);else if(d.moved){Object.assign(d.block.style,d.before);}
    }
    const resize=()=>{finish(false);if(frame)env.cancelAnimationFrame?.(frame);frame=env.requestAnimationFrame?.(()=>{frame=null;restore();});};
    const bindings=[];
    for(const block of blocks){
      const handle=block.querySelector('[data-mf-grip]');if(!handle)continue;
      const down=e=>{if((surface()==='mobile'&&block.dataset.mfMovable==='quick')||!active||e.isPrimary===false||(e.button!=null&&e.button!==0))return;e.preventDefault();handle.focus?.({preventScroll:true});const r=rect(block);drag={block,handle,id:e.pointerId,startX:e.clientX,startY:e.clientY,x:r.x,y:r.y,width:r.width,height:r.height,moved:false,before:Object.fromEntries(['left','top','right','bottom','transform'].map(k=>[k,block.style[k]]))};handle.setPointerCapture?.(e.pointerId);};
      const pointer=e=>{if(!drag||drag.handle!==handle||drag.id!==e.pointerId)return;const dx=e.clientX-drag.startX,dy=e.clientY-drag.startY;if(!drag.moved&&Math.hypot(dx,dy)<5)return;drag.moved=true;block.classList.add('mf-dragging');drag.next={x:drag.x+dx,y:drag.y+dy};if(env.requestAnimationFrame){if(dragFrame===null)dragFrame=env.requestAnimationFrame(paintDrag);}else paintDrag();};
      const up=e=>{if(drag?.handle===handle&&drag.id===e.pointerId)finish();};
      const cancel=()=>finish(false);
      const keyboard=e=>{
        if(e.key==='Escape'&&drag){e.preventDefault();finish(false);return;}
        if(e.key==='Home'){e.preventDefault();reset(block);return;}
        const deltas={ArrowLeft:[-1,0],ArrowRight:[1,0],ArrowUp:[0,-1],ArrowDown:[0,1]},d=deltas[e.key];if(!d)return;
        e.preventDefault();e.stopPropagation();const r=rect(block),step=e.shiftKey?40:12;move(block,r.x+d[0]*step,r.y+d[1]*step);remember(block);
      };
      const dbl=()=>reset(block);
      for(const [event,fn] of [['pointerdown',down],['pointermove',pointer],['pointerup',up],['pointercancel',cancel],['lostpointercapture',cancel],['keydown',keyboard],['dblclick',dbl]]){handle.addEventListener(event,fn);bindings.push([handle,event,fn]);}
    }
    const blur=()=>finish(false);
    return {refresh:restore,setActive(value){active=value;finish(false);if(value){env.addEventListener?.('resize',resize);env.addEventListener?.('blur',blur);restore();}else{env.removeEventListener?.('resize',resize);env.removeEventListener?.('blur',blur);if(frame)env.cancelAnimationFrame?.(frame);frame=null;}},destroy(){this.setActive(false);for(const [h,e,f] of bindings)h.removeEventListener(e,f);}};
  }
  const api={clampPanel,mountLayout};
  if(typeof module!=='undefined'&&module.exports)module.exports=api;
  else window.SpikeFloatingLayout=api;
})();
