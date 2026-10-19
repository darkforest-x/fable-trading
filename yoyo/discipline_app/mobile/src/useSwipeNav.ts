/** Horizontal page swipes leave controls, scrolling, dialogs and OS edges alone. */
import {useRef,type TouchEvent} from 'react';
import {MAIN_TABS,swipeDestination} from './navigation';
export {MAIN_TABS} from './navigation';
export function useSwipeNav(tab:string,disabled:boolean,navigate:(tab:typeof MAIN_TABS[number])=>void){
 const start=useRef<{x:number;y:number;at:number}|null>(null);
 return {onTouchStart(e:TouchEvent){
  start.current=null;
  if(disabled||e.touches.length!==1||(e.target as Element).closest('button,input,textarea,select,a,summary,[role="slider"],[role="dialog"],[role="listbox"],[data-no-swipe]'))return;
  const t=e.touches[0];if(t.clientX<20||t.clientX>window.innerWidth-20)return;
  start.current={x:t.clientX,y:t.clientY,at:Date.now()};
 },onTouchEnd(e:TouchEvent){
  const s=start.current;start.current=null;if(!s||disabled||e.changedTouches.length!==1)return;
  const t=e.changedTouches[0],dx=t.clientX-s.x,dy=t.clientY-s.y;
  const next=swipeDestination(tab,dx,dy,Date.now()-s.at);if(next)navigate(next);
 },onTouchCancel(){start.current=null}};
}
