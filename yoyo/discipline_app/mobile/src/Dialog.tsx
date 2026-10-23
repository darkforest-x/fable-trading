/** Accessible confirmation sheet: no swipe-to-trade, no dismiss during submission. */
import {useCallback,useEffect,useRef,useState,type ReactNode} from 'react';
import {Icon} from './Icon';
import {feedback} from './feedback';
import {shouldDismissSheet} from './sheetGesture';

export function Dialog({label,busy,onDismiss,children,variant='sheet'}:{label:string;busy:boolean;onDismiss:()=>void;children:ReactNode|((dismiss:()=>void)=>ReactNode);variant?:'sheet'|'alert'|'notice'}){
 const panel=useRef<HTMLElement>(null);
 const drag=useRef<{x:number;y:number;pointer:number;height:number;lastY:number;at:number;velocity:number;reduced:boolean}|null>(null);
 const dragFrame=useRef(0),offset=useRef(0);
 const setDragOffset=(value:number)=>{
  offset.current=value;
  if(dragFrame.current)return;
  dragFrame.current=requestAnimationFrame(()=>{
   dragFrame.current=0;panel.current?.style.setProperty('--sheet-drag',`${offset.current}px`);
  });
 };
 const rebound=()=>{
  drag.current=null;cancelAnimationFrame(dragFrame.current);dragFrame.current=0;offset.current=0;
  panel.current?.classList.remove('sheet-dragging');panel.current?.style.setProperty('--sheet-drag','0px');
 };
 const [closing,setClosing]=useState(false);
 const finished=useRef(false),closingRef=useRef(false),closeTimer=useRef<ReturnType<typeof setTimeout>|null>(null);
 const latest=useRef({busy,onDismiss});
 latest.current={busy,onDismiss};
 const finishDismiss=useCallback(()=>{if(finished.current)return;finished.current=true;if(closeTimer.current)clearTimeout(closeTimer.current);closeTimer.current=null;latest.current.onDismiss()},[]);
 const dismiss=useCallback(()=>{
  if(latest.current.busy||closingRef.current)return;
  feedback('light');
  if(matchMedia('(prefers-reduced-motion: reduce)').matches){latest.current.onDismiss();return}
  cancelAnimationFrame(dragFrame.current);dragFrame.current=0;drag.current=null;
  const node=panel.current;if(node){node.style.setProperty('--sheet-drag',`${offset.current}px`);node.style.setProperty('--sheet-exit',`${node.getBoundingClientRect().height+40}px`);node.classList.remove('sheet-dragging')}
  closingRef.current=true;setClosing(true);closeTimer.current=setTimeout(finishDismiss,260);
 },[finishDismiss]);
 useEffect(()=>()=>{if(closeTimer.current)clearTimeout(closeTimer.current);cancelAnimationFrame(dragFrame.current)},[]);
 useEffect(()=>{if(busy&&!closingRef.current)rebound()},[busy]);
 useEffect(()=>{
  const previous=document.activeElement instanceof HTMLElement?document.activeElement:null;
  const overflow=document.body.style.overflow;
  document.body.style.overflow='hidden';panel.current?.focus({preventScroll:true});
  const keys=(event:KeyboardEvent)=>{
   if(closingRef.current){event.preventDefault();return}
   if(event.key==='Escape'){
    event.preventDefault();dismiss();
   }
   if(event.key==='Tab'){
    const controls=Array.from(panel.current?.querySelectorAll<HTMLElement>('button:not(:disabled),input:not(:disabled),select:not(:disabled),[tabindex="0"],summary')??[]).filter(node=>node.getClientRects().length>0);
    const first=controls[0],last=controls.at(-1);
    if(!first){event.preventDefault();panel.current?.focus();return}
    if(event.shiftKey&&(document.activeElement===first||document.activeElement===panel.current)){
     event.preventDefault();last?.focus();
    }else if(!event.shiftKey&&(document.activeElement===last||document.activeElement===panel.current)){
     event.preventDefault();first.focus();
    }
   }
  };
  document.addEventListener('keydown',keys);
  return()=>{document.body.style.overflow=overflow;document.removeEventListener('keydown',keys);previous?.focus({preventScroll:true})};
 },[dismiss]);
 return <div className={`modal-backdrop ${variant!=='sheet'?'alert-backdrop':''} ${closing?'dialog-closing':''}`} onClick={e=>{if(e.target===e.currentTarget)dismiss()}} onClickCapture={e=>{if(closingRef.current){e.preventDefault();e.stopPropagation()}}}><section className={`modal ${variant!=='sheet'?'alert-modal':''} ${variant==='notice'?'notice-modal':''}`} ref={panel} tabIndex={-1} role={variant==='alert'?'alertdialog':'dialog'} aria-modal="true" aria-label={label} aria-busy={busy||closing} onAnimationEnd={e=>{if(closingRef.current&&e.target===e.currentTarget&&['perf-sheet-exit','alert-leave'].includes(e.animationName))finishDismiss()}}
  onPointerDown={e=>{
   if(variant!=='sheet'||busy||closingRef.current)return;
   if(!e.isPrimary){rebound();return}
   const target=e.target as HTMLElement;
   if(e.button!==0||!target.closest('.sheet-handle,h2,.fib-ticket-title')||target.closest('button,input,select,a,textarea'))return;
   const node=e.currentTarget;drag.current={x:e.clientX,y:e.clientY,pointer:e.pointerId,height:node.getBoundingClientRect().height,lastY:e.clientY,at:e.timeStamp,velocity:0,reduced:matchMedia('(prefers-reduced-motion: reduce)').matches};
   node.setPointerCapture(e.pointerId);
  }}
  onPointerMove={e=>{const start=drag.current;if(!start||start.pointer!==e.pointerId||busy)return;
   const dx=e.clientX-start.x,dy=e.clientY-start.y;
   if(Math.abs(dx)>Math.max(12,Math.abs(dy)*1.5)){rebound();return}
   const dt=e.timeStamp-start.at;if(dt>0){start.velocity=(e.clientY-start.lastY)/dt;start.lastY=e.clientY;start.at=e.timeStamp}
   if(dy>3&&!start.reduced){e.currentTarget.dataset.dragged='true';e.currentTarget.classList.add('sheet-dragging');setDragOffset(Math.min(start.height,Math.max(0,dy)))}
  }}
  onPointerUp={e=>{const start=drag.current;if(!start||start.pointer!==e.pointerId)return;
   drag.current=null;
   if(!busy&&shouldDismissSheet(e.clientX-start.x,e.clientY-start.y,e.timeStamp-start.at<100?start.velocity:0))dismiss();else rebound();
  }}
  onPointerCancel={rebound}
  onLostPointerCapture={()=>{if(drag.current)rebound()}}
 >{variant==='sheet'&&<div className="sheet-handle" aria-hidden="true"/>}<button className="sheet-close" aria-label="关闭窗口" disabled={busy||closing} onClick={dismiss}><Icon name="close" size={19}/></button>{typeof children==='function'?children(dismiss):children}</section></div>;
}

export function Spinner(){return <span className="spinner" aria-hidden="true"/>}
