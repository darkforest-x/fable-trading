/** One vertical gesture owner at scroll-top; charts, sheets and controls keep theirs. */
import {useEffect,useRef,useState,type RefObject} from 'react';
import {feedback} from './feedback';
export const pullOffset=(distance:number)=>Math.min(96,Math.max(0,distance)*.48);
export const pullReady=(offset:number)=>offset>=64;
const excluded='button,input,textarea,select,a,summary,[role="dialog"],[role="slider"],.candle-canvas,[data-no-refresh]';
export function canPull(target:Element,root:HTMLElement){
 if(target.closest(excluded)||document.querySelector('[role="dialog"]')||(document.scrollingElement?.scrollTop??0)>1)return false;
 for(let node:HTMLElement|null=target as HTMLElement;node&&node!==root;node=node.parentElement)if(node.scrollTop>1)return false;
 return root.scrollTop<=1;
}
export function usePullRefresh(root:RefObject<HTMLElement|null>,disabled:boolean,reload:()=>Promise<unknown>,page:string){
 const [status,setStatus]=useState<'idle'|'pull'|'ready'|'loading'|'done'|'error'>('idle');
 const latest=useRef({disabled,reload});latest.current={disabled,reload};
 useEffect(()=>{
  const element=root.current;if(!element)return;setStatus('idle');
  let start:{x:number;y:number;axis:'pending'|'vertical'}|null=null,offset=0,frame=0,timer:ReturnType<typeof setTimeout>|undefined,active=true,loading=false,ready=false;
  const paint=(value:number)=>{offset=value;if(!frame)frame=requestAnimationFrame(()=>{element.style.setProperty('--pull-distance',`${offset}px`);frame=0})};
  const reset=()=>{start=null;ready=false;paint(0);element.removeAttribute('data-pulling');setStatus('idle')};
  const begin=(event:TouchEvent)=>{
   if(latest.current.disabled||loading||event.touches.length!==1||!canPull(event.target as Element,element))return;
   clearTimeout(timer);const t=event.touches[0];start={x:t.clientX,y:t.clientY,axis:'pending'};ready=false;
  };
  const move=(event:TouchEvent)=>{
   if(!start)return;
   if(latest.current.disabled||event.touches.length!==1||document.querySelector('[role="dialog"]')){reset();return}
   const t=event.touches[0],dx=t.clientX-start.x,dy=t.clientY-start.y;
   if(start.axis==='pending'){
    if(Math.max(Math.abs(dx),Math.abs(dy))<8)return;
    if(dy<=0||Math.abs(dx)>dy){reset();return}
    start.axis='vertical';element.dataset.pulling='true';
   }
   if(event.cancelable)event.preventDefault();
   const value=pullOffset(dy);paint(value);const next=pullReady(value);
   if(next&&!ready)feedback('light');ready=next;setStatus(next?'ready':'pull');
  };
  const end=()=>{
   if(!start)return;
   const fire=start.axis==='vertical'&&pullReady(offset)&&!latest.current.disabled;start=null;
   element.removeAttribute('data-pulling');
   if(!fire){reset();return}
   loading=true;paint(56);setStatus('loading');
   void latest.current.reload().then(()=>{if(active){setStatus('done');feedback('success')}},()=>{if(active){setStatus('error');feedback('error')}}).finally(()=>{
    loading=false;if(active)timer=setTimeout(reset,650);
   });
  };
  element.addEventListener('touchstart',begin,{passive:true});element.addEventListener('touchmove',move,{passive:false});element.addEventListener('touchend',end);element.addEventListener('touchcancel',reset);
  return()=>{active=false;cancelAnimationFrame(frame);clearTimeout(timer);element.removeEventListener('touchstart',begin);element.removeEventListener('touchmove',move);element.removeEventListener('touchend',end);element.removeEventListener('touchcancel',reset);element.style.removeProperty('--pull-distance');element.removeAttribute('data-pulling')};
 },[root,page]);
 return status;
}
