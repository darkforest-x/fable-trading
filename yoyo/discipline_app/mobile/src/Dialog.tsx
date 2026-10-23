/** Accessible confirmation sheet: no swipe-to-trade, no dismiss during submission. */
import {useCallback,useEffect,useRef,useState,type ReactNode} from 'react';
import {createPortal} from 'react-dom';
import {Icon} from './Icon';
import {feedback} from './feedback';
import {createSheetMotion} from './sheetMotion';

export function Dialog({label,busy,onDismiss,children,variant='sheet'}:{label:string;busy:boolean;onDismiss:()=>void;children:ReactNode|((dismiss:()=>void)=>ReactNode);variant?:'sheet'|'alert'|'notice'}){
 const panel=useRef<HTMLElement>(null);
 const backdrop=useRef<HTMLDivElement>(null),motion=useRef<ReturnType<typeof createSheetMotion>|null>(null);
 const [closing,setClosing]=useState(false);
 const finished=useRef(false),closingRef=useRef(false),closeTimer=useRef<ReturnType<typeof setTimeout>|null>(null);
 const latest=useRef({busy,onDismiss});
 latest.current={busy,onDismiss};
 const finishDismiss=useCallback(()=>{if(finished.current)return;finished.current=true;if(closeTimer.current)clearTimeout(closeTimer.current);closeTimer.current=null;latest.current.onDismiss()},[]);
 const dismiss=useCallback(()=>{
  if(latest.current.busy||closingRef.current)return;
  feedback('light');
  closingRef.current=true;setClosing(true);
  if(motion.current){motion.current.exit(finishDismiss);return}
  if(matchMedia('(prefers-reduced-motion: reduce)').matches){finishDismiss();return}
  closeTimer.current=setTimeout(finishDismiss,260);
 },[finishDismiss]);
 useEffect(()=>{
  if(variant==='sheet'&&panel.current&&backdrop.current){
   motion.current=createSheetMotion(panel.current,backdrop.current,()=>!latest.current.busy&&!closingRef.current,dismiss);
   feedback('light');
  }
  return()=>{motion.current?.destroy();motion.current=null;if(closeTimer.current)clearTimeout(closeTimer.current)};
 },[variant,dismiss]);
 useEffect(()=>{if(busy&&!closingRef.current)motion.current?.cancel()},[busy]);
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
 const content=<div ref={backdrop} className={`modal-backdrop ${variant!=='sheet'?'alert-backdrop':'sheet-backdrop'} ${closing?'dialog-closing':''}`} onClick={e=>{if(e.target===e.currentTarget)dismiss()}} onClickCapture={e=>{if(closingRef.current){e.preventDefault();e.stopPropagation()}}}><section className={`modal ${variant!=='sheet'?'alert-modal':''} ${variant==='notice'?'notice-modal':''}`} ref={panel} tabIndex={-1} role={variant==='alert'?'alertdialog':'dialog'} aria-modal="true" aria-label={label} aria-busy={busy||closing} onAnimationEnd={e=>{if(closingRef.current&&e.target===e.currentTarget&&e.animationName==='alert-leave')finishDismiss()}} >{variant==='sheet'&&<div className="sheet-handle" aria-hidden="true"/>}<button className="sheet-close" aria-label="关闭窗口" disabled={busy||closing} onClick={dismiss}><Icon name="close" size={19}/></button>{typeof children==='function'?children(dismiss):children}</section></div>;
 // Escape page transforms and clipping while retaining the app theme.
 const host=document.querySelector('.app');
 return host?createPortal(content,host):content;
}

export function Spinner(){return <span className="spinner" aria-hidden="true"/>}
