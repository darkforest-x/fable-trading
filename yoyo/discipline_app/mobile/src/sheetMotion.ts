/** Direct manipulation for every bottom sheet. Body scrolling never owns this gesture.
 * Reduced motion disables settling, not finger tracking. DOM writes are frame-coalesced.
 */
import {shouldDismissSheet} from './sheetGesture.ts';

export function createSheetMotion(panel:HTMLElement,backdrop:HTMLElement,active:()=>boolean,dismiss:()=>void,env:Window=window){
 let gesture:{id:number;x:number;y:number;base:number;lastY:number;at:number;velocity:number}|null=null;
 let frame:number|null=null,timer:number|null=null,offset=0,height=1,done:(()=>void)|undefined;
 const reduced=()=>env.matchMedia('(prefers-reduced-motion: reduce)').matches;
 const classes=(name:string,value:boolean)=>{panel.classList.toggle(name,value);backdrop.classList.toggle(name,value)};
 const cancelFrame=()=>{if(frame!==null)env.cancelAnimationFrame(frame);frame=null};
 const cancelTimer=()=>{if(timer!==null)env.clearTimeout(timer);timer=null;done=undefined};
 const paint=()=>{frame=null;panel.style.setProperty('--sheet-drag',`${offset}px`);backdrop.style.setProperty('--sheet-dim',String(Math.max(0,1-offset/height)))};
 const write=(value:number)=>{offset=Math.max(0,Math.min(height+40,value));if(frame===null)frame=env.requestAnimationFrame(paint)};
 const release=()=>{const old=gesture;gesture=null;if(old&&panel.hasPointerCapture(old.id))panel.releasePointerCapture(old.id)};
 function complete(){const callback=done;cancelTimer();classes('sheet-settling',false);callback?.()}
 function freeze(){
  const style=env.getComputedStyle(panel),matrix=style.transform.match(/^matrix(3d)?\(([^)]+)\)$/);
  const values=matrix?.[2].split(',').map(Number);
  const transformY=values?.[matrix?.[1]?13:5]||0,translateY=parseFloat(style.translate.split(' ')[1]||'0')||0;
  offset=Math.max(0,transformY+translateY);
  cancelFrame();cancelTimer();classes('sheet-settling',false);
  panel.dataset.dragged='true';backdrop.dataset.dragged='true';paint();
 }
 function settle(value:number,callback?:()=>void){
  cancelFrame();paint();release();classes('sheet-dragging',false);
  if(reduced()||Math.abs(value-offset)<.5){offset=value;paint();callback?.();return}
  panel.getBoundingClientRect(); // Commit the finger position before setting the release target.
  done=callback;classes('sheet-settling',true);offset=value;paint();timer=env.setTimeout(complete,280);
 }
 function cancel(){if(gesture)settle(0)}
 function down(e:PointerEvent){
  if(!active())return;
  if(!e.isPrimary){cancel();return}
  const target=e.target as HTMLElement;
  if(e.button!==0||!target.closest('.sheet-handle,h2,.fib-ticket-title')||target.closest('button,input,select,textarea,a'))return;
  height=panel.getBoundingClientRect().height||1;freeze();
  gesture={id:e.pointerId,x:e.clientX,y:e.clientY,base:offset,lastY:e.clientY,at:e.timeStamp,velocity:0};
  classes('sheet-dragging',true);panel.setPointerCapture(e.pointerId);e.preventDefault();
 }
 function move(e:PointerEvent){
  const g=gesture;if(!g||g.id!==e.pointerId)return;if(!active()){cancel();return}
  const dx=e.clientX-g.x,dy=e.clientY-g.y;
  if(Math.abs(dx)>Math.max(12,Math.abs(dy)*1.5)){cancel();return}
  const dt=e.timeStamp-g.at;if(dt>0&&e.clientY!==g.lastY){g.velocity=(e.clientY-g.lastY)/dt;g.lastY=e.clientY;g.at=e.timeStamp}
  write(g.base+dy);e.preventDefault();
 }
 function up(e:PointerEvent){
  const g=gesture;if(!g||g.id!==e.pointerId)return;
  const dx=e.clientX-g.x,dy=e.clientY-g.y,velocity=e.timeStamp-g.at<100?g.velocity:0;
  write(g.base+dy);cancelFrame();paint();release();
  if(active()&&dy>Math.abs(dx)*1.5&&(offset>=64||shouldDismissSheet(dx,dy,velocity)))dismiss();else settle(0);
 }
 const interrupted=(e:PointerEvent)=>{if(gesture?.id===e.pointerId)cancel()};
 const transition=(e:TransitionEvent)=>{if(e.target===panel&&e.propertyName==='translate'&&timer!==null)complete()};
 panel.addEventListener('pointerdown',down);panel.addEventListener('pointermove',move);panel.addEventListener('pointerup',up);
 panel.addEventListener('pointercancel',interrupted);panel.addEventListener('lostpointercapture',interrupted);panel.addEventListener('transitionend',transition);
 env.addEventListener('blur',cancel);
 return {
  cancel,
  exit(callback:()=>void){height=panel.getBoundingClientRect().height||1;freeze();settle(height+40,callback)},
  destroy(){release();cancelFrame();cancelTimer();panel.removeEventListener('pointerdown',down);panel.removeEventListener('pointermove',move);panel.removeEventListener('pointerup',up);panel.removeEventListener('pointercancel',interrupted);panel.removeEventListener('lostpointercapture',interrupted);panel.removeEventListener('transitionend',transition);env.removeEventListener('blur',cancel)},
 };
}
