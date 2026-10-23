/** One composited page tracks the finger. Release commits immediately, without
 * a delayed outgoing phase, page remount, or layout reads on every touchmove.
 */
import {useEffect,useRef,type MouseEvent,type TouchEvent} from 'react';
import {MAIN_TABS,swipeAxis,swipeDestination,swipeOffset,type SwipeAxis} from './navigation';
export {MAIN_TABS} from './navigation';
const EXCLUDED='button,input,textarea,select,a,summary,[role="slider"],[role="dialog"],[role="listbox"],[data-no-swipe]';
type Gesture={x:number;y:number;at:number;axis:SwipeAxis;node:HTMLElement;width:number;reduced:boolean};
export function useSwipeNav(tab:string,disabled:boolean,navigate:(tab:typeof MAIN_TABS[number])=>void){
 const start=useRef<Gesture|null>(null),node=useRef<HTMLElement|null>(null);
 const frame=useRef(0),timer=useRef<ReturnType<typeof setTimeout>|null>(null),offset=useRef(0),suppressClick=useRef(0),animation=useRef<Animation|null>(null);
 function clear(){
  cancelAnimationFrame(frame.current);frame.current=0;
  if(timer.current!==null)clearTimeout(timer.current);timer.current=null;
  start.current=null;offset.current=0;
  if(node.current){node.current.removeAttribute('data-swipe');node.current.style.removeProperty('--swipe-x')}
  node.current=null;
 }
 useEffect(()=>{clear();return clear},[tab,disabled]);
 useEffect(()=>()=>animation.current?.cancel(),[]);
 function paint(element:HTMLElement,x:number){
  offset.current=x;if(frame.current)return;
  frame.current=requestAnimationFrame(()=>{element.style.setProperty('--swipe-x',`${offset.current}px`);frame.current=0});
 }
 function rebound(){
  start.current=null;cancelAnimationFrame(frame.current);frame.current=0;
  if(timer.current!==null)clearTimeout(timer.current);timer.current=null;
  if(!node.current)return;
  node.current.dataset.swipe='cancel';node.current.style.setProperty('--swipe-x','0px');
  timer.current=setTimeout(clear,180);
 }
 return {onTouchStart(e:TouchEvent<HTMLElement>){
  if(disabled)return;
  clear();animation.current?.cancel();suppressClick.current=0;
  const strip=e.currentTarget.tagName==='NAV';
  if(e.touches.length!==1||!MAIN_TABS.some(v=>v===tab)||(!strip&&(e.target as Element).closest(EXCLUDED)))return;
  const t=e.touches[0];if(t.clientX<20||t.clientX>window.innerWidth-20)return;
  const page=strip?e.currentTarget.closest('.app')?.querySelector<HTMLElement>('main'):e.currentTarget;if(!page)return;
  node.current=page;
  start.current={x:t.clientX,y:t.clientY,at:Date.now(),axis:'pending',node:page,width:page.clientWidth,reduced:window.matchMedia('(prefers-reduced-motion: reduce)').matches};
 },onTouchMove(e:TouchEvent<HTMLElement>){
  const s=start.current;if(!s)return;
  if(disabled||e.touches.length!==1){rebound();return}
  const t=e.touches[0],dx=t.clientX-s.x,dy=t.clientY-s.y;
  s.axis=swipeAxis(s.axis,dx,dy);
  if(s.axis==='vertical'){clear();return}
  if(s.axis!=='horizontal')return;
  suppressClick.current=Date.now()+400;
  if(!s.reduced){s.node.dataset.swipe='drag';paint(s.node,swipeOffset(tab,dx,s.width))}
 },onTouchEnd(e:TouchEvent<HTMLElement>){
  const s=start.current;start.current=null;if(!s)return;
  if(disabled||e.changedTouches.length!==1){rebound();return}
  const t=e.changedTouches[0],dx=t.clientX-s.x,dy=t.clientY-s.y;
  const next=swipeAxis(s.axis,dx,dy)==='horizontal'?swipeDestination(tab,dx,dy,Date.now()-s.at):null;
  if(!next){if(s.reduced)clear();else rebound();return}
  suppressClick.current=Date.now()+400;clear();navigate(next);
  if(!s.reduced)animation.current=s.node.animate([{transform:`translateX(${-Math.sign(dx)*16}px)`,opacity:.94},{transform:'translateX(0)',opacity:1}],{duration:180,easing:'cubic-bezier(.16,1,.3,1)'});
 },onTouchCancel(){rebound()},onClickCapture(e:MouseEvent<HTMLElement>){if(e.detail!==0&&Date.now()<suppressClick.current){suppressClick.current=0;e.preventDefault();e.stopPropagation()}}};
}
