/** Stitch f5d3f952: one live page and one continuous indicator. Content is
 * exchanged offscreen, without mounting extra feeds; re-grab freezes real position. */
import {useEffect,useLayoutEffect,useRef,type MouseEvent,type PointerEvent} from 'react';
import {MAIN_TABS,swipeAxis,swipeDestination,swipeOffset,type SwipeAxis} from './navigation';
export {MAIN_TABS} from './navigation';
const EXCLUDED='button,input,textarea,select,a,summary,canvas,.candle-canvas,[role="slider"],[role="dialog"],[role="listbox"],[data-no-swipe]';
const EASE='cubic-bezier(.16,1,.3,1)';
type Gesture={x:number;y:number;at:number;axis:SwipeAxis;width:number;step:number;base:number;navBase:number;pointer:number;owner:HTMLElement;lastX:number;lastAt:number;velocity:number};
export function useSwipeNav(tab:string,disabled:boolean,navigate:(tab:typeof MAIN_TABS[number])=>void){
 const gesture=useRef<Gesture|null>(null),page=useRef<HTMLElement|null>(null),strip=useRef<HTMLElement|null>(null);
 const motion=useRef<Animation|null>(null),offset=useRef(0),pill=useRef(0),suppressClick=useRef(0);
 const incoming=useRef<{tab:string;from:number}|null>(null),generation=useRef(0);
 const latest=useRef({tab,disabled,navigate});latest.current={tab,disabled,navigate};
 const reduced=()=>window.matchMedia('(prefers-reduced-motion: reduce)').matches;
 function paint(x:number,navX:number){offset.current=x;pill.current=navX;page.current?.style.setProperty('--swipe-x',`${x}px`);strip.current?.style.setProperty('--nav-drag',`${navX}px`)}
 function stop(){generation.current++;motion.current?.cancel();motion.current=null;incoming.current=null}
 function reset(){stop();gesture.current=null;paint(0,0);page.current?.removeAttribute('data-swipe');strip.current?.removeAttribute('data-swipe')}
 function settle(x:number,navX:number,duration:number,done:()=>void){
  const node=page.current;if(!node){done();return}
  const from=offset.current;stop();const token=generation.current;
  node.dataset.swipe='settle';if(strip.current)strip.current.dataset.swipe='settle';paint(x,navX);
  if(reduced()){done();return}
  const animation=node.animate([{translate:`${from}px 0`},{translate:`${x}px 0`}],{duration,easing:EASE});motion.current=animation;
  animation.onfinish=()=>{if(token!==generation.current)return;motion.current=null;done()};
 }
 function rebound(){gesture.current=null;settle(0,0,260,reset)}
 function freeze(node:HTMLElement,nav:HTMLElement){
  const x=parseFloat(getComputedStyle(node).translate)||0,transform=getComputedStyle(nav,'::before').transform;
  const position=transform==='none'?0:new DOMMatrixReadOnly(transform).m41;
  const index=Math.max(0,MAIN_TABS.findIndex(v=>v===latest.current.tab)),step=nav.querySelector('button')!.getBoundingClientRect().width+5;
  stop();node.dataset.swipe='drag';nav.dataset.swipe='drag';paint(x,position-index*step);return step;
 }
 useLayoutEffect(()=>{const next=incoming.current;if(!disabled&&next?.tab===tab){incoming.current=null;if(strip.current)strip.current.dataset.swipe='drag';paint(next.from,0);settle(0,0,160,reset)}else reset()},[tab,disabled]);
 useEffect(()=>{const cancel=()=>reset();window.addEventListener('blur',cancel);window.addEventListener('resize',cancel);return()=>{reset();window.removeEventListener('blur',cancel);window.removeEventListener('resize',cancel)}},[]);
 return {onPointerDown(e:PointerEvent<HTMLElement>){
  if(!e.isPrimary){if(gesture.current)rebound();return}
  if(disabled||e.button!==0||!MAIN_TABS.some(v=>v===tab))return;
  const isNav=e.currentTarget.tagName==='NAV';if(!isNav&&(e.target as Element).closest(EXCLUDED))return;
  if(e.clientX<20||e.clientX>window.innerWidth-20)return;
  const app=e.currentTarget.closest('.app'),node=app?.querySelector<HTMLElement>('main'),nav=app?.querySelector<HTMLElement>('.bottom-nav');if(!node||!nav)return;
  page.current=node;strip.current=nav;const step=freeze(node,nav),at=performance.now();suppressClick.current=0;
  gesture.current={x:e.clientX,y:e.clientY,at,axis:'pending',width:node.clientWidth,step,base:offset.current,navBase:pill.current,pointer:e.pointerId,owner:e.currentTarget,lastX:e.clientX,lastAt:at,velocity:0};
 },onPointerMove(e:PointerEvent<HTMLElement>){
  const s=gesture.current;if(!s||e.pointerId!==s.pointer)return;if(disabled){rebound();return}
  const dx=e.clientX-s.x,dy=e.clientY-s.y,now=performance.now();s.axis=swipeAxis(s.axis,dx,dy);
  if(s.axis==='vertical'){rebound();return}if(s.axis!=='horizontal')return;
  if(!s.owner.hasPointerCapture(e.pointerId))s.owner.setPointerCapture(e.pointerId);if(e.cancelable)e.preventDefault();
  const dt=now-s.lastAt;if(dt>0)s.velocity=(e.clientX-s.lastX)/dt;s.lastX=e.clientX;s.lastAt=now;suppressClick.current=Date.now()+500;
  const travel=swipeOffset(tab,dx,s.width);paint(s.base+travel,s.navBase-travel/s.width*s.step);
 },onPointerUp(e:PointerEvent<HTMLElement>){
  const s=gesture.current;if(!s||s.pointer!==e.pointerId)return;gesture.current=null;
  if(s.owner.hasPointerCapture(e.pointerId))s.owner.releasePointerCapture(e.pointerId);
  if(s.axis!=='horizontal'){rebound();return}
  const dx=e.clientX-s.x,dy=e.clientY-s.y,now=performance.now(),velocity=now-s.lastAt>90?0:s.velocity;
  const next=disabled?null:swipeDestination(tab,dx,dy,now-s.at,velocity);suppressClick.current=Date.now()+500;
  if(!next){rebound();return}if(reduced()){reset();latest.current.navigate(next);return}
  const direction=Math.sign(dx);settle(direction*s.width,-direction*s.step,100,()=>{incoming.current={tab:next,from:-direction*s.width};latest.current.navigate(next)});
 },onPointerCancel(){if(gesture.current)rebound()},onLostPointerCapture(e:PointerEvent<HTMLElement>){const s=gesture.current;if(s&&e.target===s.owner&&!s.owner.hasPointerCapture(s.pointer))rebound()},onClickCapture(e:MouseEvent<HTMLElement>){if(e.detail!==0&&Date.now()<suppressClick.current){suppressClick.current=0;e.preventDefault();e.stopPropagation()}}};
}
