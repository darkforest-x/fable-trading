/** Keep floating navigation away from text entry in a mobile WebView. */
import {useEffect,useState} from 'react';

export function useKeyboard(){
 const [editing,setEditing]=useState(false);
 useEffect(()=>{
  let timer:ReturnType<typeof setTimeout>;
  let frame:number|null=null;
  const measure=()=>{
   frame=null;
   const input=document.activeElement;
   const typing=input instanceof HTMLInputElement&&!['range','checkbox','radio'].includes(input.type);
   const mobile=matchMedia('(pointer: coarse)').matches;
   setEditing(typing&&mobile);
   const viewport=window.visualViewport;
   const style=document.documentElement.style;
   const set=(name:string,value:string)=>{if(style.getPropertyValue(name)!==value)style.setProperty(name,value)};
   set('--visual-height',`${viewport?.height??window.innerHeight}px`);
   set('--visual-top',`${viewport?.offsetTop??0}px`);
   set('--keyboard-bottom',`${viewport?Math.max(0,window.innerHeight-viewport.height-viewport.offsetTop):0}px`);
  };
  const update=()=>{if(frame===null)frame=requestAnimationFrame(measure)};
  const focus=()=>{clearTimeout(timer);timer=setTimeout(update,0)};
  update();
  document.addEventListener('focusin',focus);document.addEventListener('focusout',focus);
  window.visualViewport?.addEventListener('resize',update);window.visualViewport?.addEventListener('scroll',update);
  return()=>{clearTimeout(timer);if(frame!==null)cancelAnimationFrame(frame);document.removeEventListener('focusin',focus);document.removeEventListener('focusout',focus);window.visualViewport?.removeEventListener('resize',update);window.visualViewport?.removeEventListener('scroll',update)};
 },[]);
 return editing;
}
