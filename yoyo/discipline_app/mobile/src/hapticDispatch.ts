/** A best-effort adapter: haptic failure cannot delay or change an action.
 * Native impact/notification APIs are distinct from the limited web vibration API.
 */
export type FeedbackKind='light'|'tick'|'medium'|'heavy'|'success'|'warning'|'error';
type Driver={native:()=>boolean;visible:()=>boolean;mobile:()=>boolean;now:()=>number;vibrate:(pattern:number|number[])=>boolean;impact:(kind:'light'|'medium'|'heavy')=>Promise<void>;notification:(kind:'success'|'warning'|'error')=>Promise<void>};
export function createHapticDispatch(driver:Driver){
 let lastImpact=-Infinity,lastNotice=-Infinity;
 return (kind:FeedbackKind)=>{
  try{
   if(!driver.visible()||(!driver.native()&&!driver.mobile()))return false;
   const notice=kind==='success'||kind==='warning'||kind==='error',now=driver.now();
   if(now-(notice?lastNotice:lastImpact)<(notice?250:kind==='heavy'?40:kind==='medium'?80:120))return false;
   if(notice)lastNotice=now;else lastImpact=now;
   if(driver.native()){
    const result=notice?driver.notification(kind):driver.impact(kind==='tick'?'light':kind);
    void result.catch(()=>{});return true;
   }
   return driver.vibrate(kind==='success'?[12,45,12]:kind==='warning'||kind==='error'?[20,45,20]:kind==='heavy'?18:kind==='medium'?12:8);
  }catch{return false}
 };
}
