/** Sliding white lens behind the active segment — the 监控 page standard for every segmented control
 * (owner 2026-11-07 “交互和动画做好一点”). Measures the pressed button; snaps on first paint and on
 * resize, springs between segments otherwise. Reduced motion turns the spring off in CSS. */
import {useLayoutEffect,useRef} from 'react';
export function SegmentLens({value}:{value:string}){
 const ref=useRef<HTMLElement>(null);
 useLayoutEffect(()=>{
  const lens=ref.current,host=lens?.parentElement;if(!lens||!host)return;
  const place=(animate:boolean)=>{
   const active=host.querySelector<HTMLElement>('button[aria-pressed="true"],button.active');
   if(!active){lens.style.opacity='0';return}
   lens.style.transition=animate?'':'none';lens.style.opacity='1';
   lens.style.width=`${active.offsetWidth}px`;lens.style.transform=`translateX(${active.offsetLeft}px)`;
  };
  place(lens.dataset.ready==='1');lens.dataset.ready='1';
  const observer=new ResizeObserver(()=>place(false));observer.observe(host);
  return()=>observer.disconnect();
 },[value]);
 return <i ref={ref} className="segment-lens" aria-hidden="true"/>;
}
