/** Accessible confirmation sheet: no swipe-to-trade, no dismiss during submission. */
import {useCallback,useEffect,useRef,useState,type ReactNode} from 'react';
import {Icon} from './Icon';
import {feedback} from './feedback';
import {shouldDismissSheet} from './sheetGesture';

export function Dialog({label,busy,onDismiss,children,variant='sheet'}:{label:string;busy:boolean;onDismiss:()=>void;children:ReactNode|((dismiss:()=>void)=>ReactNode);variant?:'sheet'|'alert'|'notice'}){
 const panel=useRef<HTMLElement>(null);
 const drag=useRef<{x:number;y:number;pointer:number}|null>(null);
 const dragFrame=useRef<number|null>(null);
 const setDragOffset=(offset:number)=>{
  if(dragFrame.current!==null)cancelAnimationFrame(dragFrame.current);
  dragFrame.current=requestAnimationFrame(()=>{
   dragFrame.current=null;
   const node=panel.current;if(!node)return;
   const reduced=matchMedia('(prefers-reduced-motion: reduce)').matches;
   node.style.setProperty('--sheet-drag',`${reduced?0:offset}px`);
   node.classList.toggle('sheet-dragging',offset>0);
  });
 };
 const [closing,setClosing]=useState(false);
 const closingRef=useRef(false),closeTimer=useRef<ReturnType<typeof setTimeout>|null>(null);
 const latest=useRef({busy,onDismiss});
 latest.current={busy,onDismiss};
 const finishDismiss=useCallback(()=>{if(closeTimer.current)clearTimeout(closeTimer.current);closeTimer.current=null;latest.current.onDismiss()},[]);
 const dismiss=useCallback(()=>{
  if(latest.current.busy||closingRef.current)return;
  feedback('light');
  if(matchMedia('(prefers-reduced-motion: reduce)').matches){latest.current.onDismiss();return}
  closingRef.current=true;setClosing(true);closeTimer.current=setTimeout(finishDismiss,200);
 },[finishDismiss]);
 useEffect(()=>()=>{if(closeTimer.current)clearTimeout(closeTimer.current);if(dragFrame.current!==null)cancelAnimationFrame(dragFrame.current)},[]);
 useEffect(()=>{
  const previous=document.activeElement instanceof HTMLElement?document.activeElement:null;
  const overflow=document.body.style.overflow;
  document.body.style.overflow='hidden';panel.current?.focus();
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
 return <div className={`modal-backdrop ${variant!=='sheet'?'alert-backdrop':''} ${closing?'dialog-closing':''}`} onClick={e=>{if(e.target===e.currentTarget)dismiss()}} onClickCapture={e=>{if(closingRef.current){e.preventDefault();e.stopPropagation()}}}><section className={`modal ${variant!=='sheet'?'alert-modal':''} ${variant==='notice'?'notice-modal':''}`} ref={panel} tabIndex={-1} role={variant==='alert'?'alertdialog':'dialog'} aria-modal="true" aria-label={label} aria-busy={busy||closing} onAnimationEnd={e=>{if(closingRef.current&&e.target===e.currentTarget)finishDismiss()}}>{variant==='sheet'&&<div className="sheet-handle" aria-hidden="true"
  onPointerDown={e=>{if(!e.isPrimary||e.button!==0||busy||closing)return;drag.current={x:e.clientX,y:e.clientY,pointer:e.pointerId};e.currentTarget.setPointerCapture(e.pointerId)}}
  onPointerMove={e=>{const start=drag.current;if(start?.pointer===e.pointerId&&!busy)setDragOffset(Math.min(180,Math.max(0,e.clientY-start.y)))}}
  onPointerUp={e=>{const start=drag.current;drag.current=null;setDragOffset(0);if(start?.pointer===e.pointerId&&shouldDismissSheet(e.clientX-start.x,e.clientY-start.y))dismiss()}}
  onPointerCancel={()=>{drag.current=null;setDragOffset(0)}}
 /> }<button className="sheet-close" aria-label="关闭窗口" disabled={busy||closing} onClick={dismiss}><Icon name="close" size={19}/></button>{typeof children==='function'?children(dismiss):children}</section></div>;
}

export function Spinner(){return <span className="spinner" aria-hidden="true"/>}
