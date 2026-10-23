/** Opt-out feedback. Failures must never affect a trading command.
 * Source: https://capacitorjs.com/docs/apis/haptics (Capacitor 8).
 */
import {Capacitor} from '@capacitor/core';
import {Haptics,ImpactStyle,NotificationType} from '@capacitor/haptics';
import {rangeImpact} from './rangeHaptics';
import {createHapticDispatch,type FeedbackKind} from './hapticDispatch';
const key='spike-discipline-haptics';
let enabled=true;
try{enabled=localStorage.getItem(key)!=='off'}catch{/* Storage can be unavailable. */}
const dispatch=createHapticDispatch({
 native:()=>Capacitor.isNativePlatform(),visible:()=>!document.hidden,
 mobile:()=>matchMedia('(pointer: coarse)').matches,now:()=>performance.now(),
 vibrate:pattern=>navigator.vibrate?.(pattern)??false,
 impact:kind=>Haptics.impact({style:kind==='heavy'?ImpactStyle.Heavy:kind==='medium'?ImpactStyle.Medium:ImpactStyle.Light}),
 notification:kind=>Haptics.notification({type:kind==='success'?NotificationType.Success:kind==='warning'?NotificationType.Warning:NotificationType.Error}),
});
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
export function feedback(kind:FeedbackKind='light'){
 if(enabled)dispatch(kind);
}
