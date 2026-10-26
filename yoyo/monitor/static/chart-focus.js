/* Stitch 86d19d3dabb141408620cf1ad0ce4553: reversible chart chrome visibility.
 * A double tap requires two short, nearby, single-pointer releases; dragging,
 * pinching, cancellation and native control activation never count as taps.
 * Hiding chrome does not remount the chart or alter any panel's open state.
 */
(() => {
  'use strict';
  function createGesture(toggle) {
    const pointers=new Set();let start=null,last=null;
    const distance=(a,b)=>Math.hypot(a.clientX-b.clientX,a.clientY-b.clientY);
    return {
      down(e,eligible=true){
        pointers.add(e.pointerId);
        if(pointers.size!==1||!eligible||e.isPrimary===false){start=last=null;return;}
        start={pointerId:e.pointerId,clientX:e.clientX,clientY:e.clientY,timeStamp:e.timeStamp};
      },
      move(e){if(start&&start.pointerId===e.pointerId&&distance(start,e)>12)start=last=null;},
      cancel(e){pointers.delete(e.pointerId);start=last=null;},
      up(e){
        const tap=start&&start.pointerId===e.pointerId&&pointers.size===1&&e.timeStamp-start.timeStamp<=300&&distance(start,e)<=12;
        pointers.delete(e.pointerId);start=null;
        if(!tap){last=null;return false;}
        if(last&&e.timeStamp-last.timeStamp<=350&&distance(last,e)<=28){last=null;toggle();return true;}
        last={clientX:e.clientX,clientY:e.clientY,timeStamp:e.timeStamp};return false;
      },
      reset(){pointers.clear();start=last=null;}
    };
  }
  function bind(root) {
    let hidden=false,dialogs=[],suppressUntil=0;
    const controls='button,a,input,select,textarea,summary,[role="button"],[contenteditable="true"],[data-rp-record]';
    const eligible=e=>!e.target.closest(controls);
    function setHidden(value,restoreDialogs=true){
      if(hidden===value)return;
      hidden=value;root.classList.toggle('is-chart-focused',hidden);
      if(hidden){
        // A hidden modal still makes the canvas inert. Suspend its top-layer
        // presence, retaining its form values and original modal/nonmodal mode.
        dialogs=[...root.querySelectorAll('dialog[open]')].map(dialog=>({dialog,modal:dialog.matches(':modal')}));
        dialogs.forEach(({dialog})=>dialog.close());
        (root.querySelector?.('.cv-canvas')||root).focus({preventScroll:true});
      }else{
        const previous=dialogs;dialogs=[];
        if(restoreDialogs)previous.forEach(({dialog,modal})=>{if(dialog.isConnected&&!dialog.open)modal?dialog.showModal():dialog.show();});
      }
    }
    const gesture=createGesture(()=>setHidden(!hidden));
    root.tabIndex=-1;
    root.addEventListener('dblclick',e=>{
      if(e.button!==0||!eligible(e))return;
      e.preventDefault();e.stopPropagation();
      // Some touch browsers synthesize dblclick after the second pointerup.
      if(e.timeStamp<suppressUntil)return;
      setHidden(!hidden);
    },true);
    root.addEventListener('pointerdown',e=>{if(e.pointerType==='touch')gesture.down(e,eligible(e));},true);
    root.addEventListener('pointermove',e=>{if(e.pointerType==='touch')gesture.move(e);},true);
    root.addEventListener('pointercancel',e=>gesture.cancel(e),true);
    root.addEventListener('pointerup',e=>{
      if(e.pointerType==='touch'&&gesture.up(e)){suppressUntil=e.timeStamp+600;e.preventDefault();}
    },true);
    // Prevent the release from activating a newly exposed control at this point.
    root.addEventListener('click',e=>{if(e.timeStamp<suppressUntil){e.preventDefault();e.stopImmediatePropagation();}},true);
    root.addEventListener('keydown',e=>{if(hidden&&e.key==='Escape'){e.preventDefault();e.stopImmediatePropagation();setHidden(false);}},true);
    return {get hidden(){return hidden;},reset(){gesture.reset();suppressUntil=0;setHidden(false,false);}};
  }
  const api={createGesture,bind};
  if(typeof module!=='undefined'&&module.exports)module.exports=api;
  else window.SpikeChartFocus=api;
})();
