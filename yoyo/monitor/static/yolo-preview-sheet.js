/* Stitch 57a4f7f2e33e46b3b0ecf390afe4f2b5: a 75dvh mobile preview sheet.
 * Only marked chrome owns dismissal; candles and controls retain their input.
 * Closing goes through the dialog owner so charts, requests and body locks
 * are released together. Navigation/reopen cancels every pending animation.
 */
(() => {
  'use strict';
  function bind({panel,dismiss,env=window,isOpen=()=>panel.open}){
    const mobile=env.matchMedia('(max-width:700px)'),reduced=env.matchMedia('(prefers-reduced-motion: reduce)');
    const interactive='button,a,input,select,textarea,[role="button"],[contenteditable="true"]';
    let gesture=null,frame=null,timer=null,offset=0,height=1,completion=null,closing=false,suppressClick=false;
    const listeners=[];
    const listen=(target,type,fn,capture=false)=>{target.addEventListener(type,fn,capture);listeners.push(()=>target.removeEventListener(type,fn,capture));};
    const toggle=(name,value)=>panel.classList.toggle(name,value);
    const paint=()=>{frame=null;panel.style.setProperty('--yd-sheet-offset',offset+'px');};
    const cancelFrame=()=>{if(frame!==null)env.cancelAnimationFrame(frame);frame=null;};
    const cancelTimer=()=>{if(timer!==null)env.clearTimeout(timer);timer=null;completion=null;};
    const drop=()=>{const old=gesture;gesture=null;if(old&&panel.hasPointerCapture?.(old.id))panel.releasePointerCapture(old.id);};
    function reset(){drop();cancelFrame();cancelTimer();closing=false;suppressClick=false;toggle('yd-sheet-dragging',false);toggle('yd-sheet-settling',false);offset=0;paint();}
    function finish(){const done=completion;cancelTimer();toggle('yd-sheet-settling',false);done?.();}
    function freeze(){
      const match=env.getComputedStyle(panel).transform.match(/^matrix(3d)?\(([^)]+)\)$/);
      if(match){const values=match[2].split(',').map(Number),y=values[match[1]?13:5];if(Number.isFinite(y))offset=Math.max(0,y);}
      cancelFrame();cancelTimer();toggle('yd-sheet-settling',false);paint();
    }
    function settle(value,done){
      cancelFrame();paint();drop();toggle('yd-sheet-dragging',false);
      if(reduced.matches||Math.abs(value-offset)<.5){offset=value;paint();done?.();return;}
      panel.getBoundingClientRect();
      completion=done;toggle('yd-sheet-settling',true);offset=value;paint();timer=env.setTimeout(finish,260);
    }
    function cancel(){if(gesture)settle(0);}
    function down(e){
      suppressClick=false;
      if(!isOpen()||!mobile.matches||closing)return;
      if(e.isPrimary===false){cancel();return;}
      if((e.button!=null&&e.button!==0)||!e.target.closest?.('[data-yd-sheet-drag]')||e.target.closest(interactive))return;
      freeze();height=panel.getBoundingClientRect().height||1;
      gesture={id:e.pointerId,x:e.clientX,y:e.clientY,base:offset,lastY:e.clientY,at:e.timeStamp,velocity:0};
      toggle('yd-sheet-dragging',true);panel.setPointerCapture?.(e.pointerId);e.preventDefault();
    }
    function move(e){
      const g=gesture;if(!g||e.pointerId!==g.id)return;
      const dx=e.clientX-g.x,dy=e.clientY-g.y;
      if(Math.abs(dx)>Math.max(12,Math.abs(dy)*1.5)){cancel();return;}
      const dt=e.timeStamp-g.at;if(dt>0&&g.lastY!==e.clientY){g.velocity=(e.clientY-g.lastY)/dt;g.at=e.timeStamp;g.lastY=e.clientY;}
      if(Math.abs(dy)>6)suppressClick=true;
      offset=Math.max(0,Math.min(height+32,g.base+dy));
      if(frame===null)frame=env.requestAnimationFrame(paint);
      e.preventDefault();
    }
    function up(e){
      const g=gesture;if(!g||e.pointerId!==g.id)return;
      const dy=e.clientY-g.y,dx=e.clientX-g.x,velocity=e.timeStamp-g.at<100?g.velocity:0;
      offset=Math.max(0,Math.min(height+32,g.base+dy));
      if(dy>Math.abs(dx)*1.5&&(offset>=90||(dy>=28&&velocity>=.65))){
        closing=true;settle(height+32,()=>{reset();if(isOpen())dismiss();});
      }else settle(0);
    }
    listen(panel,'pointerdown',down);listen(panel,'pointermove',move);listen(panel,'pointerup',up);
    for(const event of ['pointercancel','lostpointercapture'])listen(panel,event,e=>{if(gesture?.id===e.pointerId)cancel();});
    listen(panel,'click',e=>{if(suppressClick&&e.detail!==0){suppressClick=false;e.preventDefault();e.stopImmediatePropagation();}},true);
    listen(panel,'transitionend',e=>{if(e.target===panel&&e.propertyName==='transform'&&timer!==null)finish();});
    listen(panel,'close',reset);listen(env,'blur',cancel);listen(env,'resize',reset);listen(mobile,'change',reset);
    return {reset,destroy(){reset();listeners.splice(0).forEach(remove=>remove());}};
  }
  if(typeof module!=='undefined')module.exports={bind};
  if(typeof window!=='undefined')window.SpikeYoloPreviewSheet={bind};
})();
