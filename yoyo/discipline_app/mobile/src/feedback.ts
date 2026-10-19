/** Native-only, opt-out feedback. Failures must never affect a trading command.
 * Source: https://capacitorjs.com/docs/apis/haptics (Capacitor 8).
 */
import {Capacitor} from '@capacitor/core';
import {Haptics,ImpactStyle,NotificationType} from '@capacitor/haptics';
import {rangeImpact} from './rangeHaptics';
const key='spike-discipline-haptics';
let enabled=true;
try{enabled=localStorage.getItem(key)!=='off'}catch{/* Storage can be unavailable. */}
let lastTick=0;
let lastRange=-Infinity;
export function sliderFeedback(previous:number,next:number){
 const now=performance.now(),kind=rangeImpact(previous,next,now-lastRange);
 if(kind){lastRange=now;feedback(kind)}
}
export const feedbackEnabled=()=>enabled;
export function setFeedbackEnabled(value:boolean){
 enabled=value;
 try{localStorage.setItem(key,value?'on':'off')}catch{/* Keep the session preference. */}
 if(value)feedback('light');
}
export function feedback(kind:'light'|'tick'|'medium'|'heavy'|'success'|'warning'|'error'='light'){
 if(!enabled||!Capacitor.isNativePlatform())return;
 if(kind==='tick'){
  const now=performance.now();
  if(now-lastTick<120)return;
  lastTick=now;
 }
 try{
  const result=kind==='light'||kind==='tick'||kind==='medium'||kind==='heavy'?Haptics.impact({style:kind==='heavy'?ImpactStyle.Heavy:kind==='medium'?ImpactStyle.Medium:ImpactStyle.Light}):
   Haptics.notification({type:kind==='success'?NotificationType.Success:kind==='warning'?NotificationType.Warning:NotificationType.Error});
  void result.catch(()=>{});
 }catch{/* Unsupported hardware or bridge failure is non-fatal. */}
}
