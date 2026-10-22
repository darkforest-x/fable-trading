/** Stitch 9cd4181: one mounted page follows the finger; only release commits navigation.
 * RAF updates a CSS variable, avoiding quote renders and adjacent chart subscriptions.
 */
import {useEffect,useRef,type TouchEvent} from 'react';
import {MAIN_TABS,swipeAxis,swipeDestination,swipeOffset,type SwipeAxis} from './navigation';
export {MAIN_TABS} from './navigation';
const EXCLUDED='button,input,textarea,select,a,summary,[role="slider"],[role="dialog"],[role="listbox"],[data-no-swipe]';
type Gesture={x:number;y:number;at:number;axis:SwipeAxis;node:HTMLElement;reduced:boolean};
export function useSwipeNav(tab:string,disabled:boolean,navigate:(tab:typeof MAIN_TABS[number])=>void){
 const start=useRef<Gesture|null>(null),node=useRef<HTMLElement|null>(null);
 const frame=useRef(0),timer=useRef<ReturnType<typeof setTimeout>|null>(null),offset=useRef(0);
 const current=useRef({tab,disabled,navigate});current.current={tab,disabled,navigate};
 function clear(){
  cancelAnimationFrame(frame.current);frame.current=0;
  if(timer.current!==null)clearTimeout(timer.current);timer.current=null;
  start.current=null;offset.current=0;
  if(node.current){node.current.removeAttribute('data-swipe');node.current.style.removeProperty('--swipe-x')}
  node.current=null;
 }
 useEffect(()=>{clear();return clear},[tab,disabled]);
 function paint(element:HTMLElement,x:number){
  offset.current=x;cancelAnimationFrame(frame.current);
  frame.current=requestAnimationFrame(()=>{element.style.setProperty('--swipe-x',`${offset.current}px`);frame.current=0});
 }
 function rebound(){
  start.current=null;cancelAnimationFrame(frame.current);frame.current=0;
  if(timer.current!==null)clearTimeout(timer.current);timer.current=null;
  if(!node.current)return;
  node.current.dataset.swipe='cancel';node.current.style.setProperty('--swipe-x','0px');
  timer.current=setTimeout(clear,220);
 }
 return {onTouchStart(e:TouchEvent<HTMLElement>){
  if(timer.current!==null||disabled)return;
  clear();
  if(e.touches.length!==1||!MAIN_TABS.some(v=>v===tab)||(e.target as Element).closest(EXCLUDED))return;
  const t=e.touches[0];if(t.clientX<20||t.clientX>window.innerWidth-20)return;
  node.current=e.currentTarget;
  start.current={x:t.clientX,y:t.clientY,at:Date.now(),axis:'pending',node:e.currentTarget,reduced:window.matchMedia('(prefers-reduced-motion: reduce)').matches};
 },onTouchMove(e:TouchEvent<HTMLElement>){
  const s=start.current;if(!s)return;
  if(disabled||e.touches.length!==1){rebound();return}
  const t=e.touches[0],dx=t.clientX-s.x,dy=t.clientY-s.y;
  s.axis=swipeAxis(s.axis,dx,dy);
  if(s.axis==='vertical'){clear();return}
  if(s.axis!=='horizontal'||s.reduced)return;
  s.node.style.animation='none';s.node.dataset.swipe='drag';paint(s.node,swipeOffset(tab,dx,s.node.clientWidth));
 },onTouchEnd(e:TouchEvent<HTMLElement>){
  const s=start.current;start.current=null;
  if(!s)return;
  if(disabled||e.changedTouches.length!==1){rebound();return}
  const t=e.changedTouches[0],dx=t.clientX-s.x,dy=t.clientY-s.y;
  const next=swipeAxis(s.axis,dx,dy)==='horizontal'?swipeDestination(tab,dx,dy,Date.now()-s.at):null;
  if(!next){if(s.reduced)clear();else rebound();return}
  if(s.reduced){clear();navigate(next);return}
  cancelAnimationFrame(frame.current);frame.current=0;
  s.node.dataset.swipe='finish';s.node.style.setProperty('--swipe-x',`${offset.current+Math.sign(dx)*24}px`);
  // Preserve the current view through a short release; a new tab can cancel this timer.
  timer.current=setTimeout(()=>{const valid=current.current.tab===tab&&!current.current.disabled;clear();if(valid)current.current.navigate(next)},100);
 },onTouchCancel(){rebound()}};
}
