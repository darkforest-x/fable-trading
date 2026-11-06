/** Generated alarm tones (no audio asset). WebKit unlocks audio only after a user gesture, and the
 * iPhone silent switch mutes it; vibration still comes from haptics. */
let context:AudioContext|null=null;
export function unlockAlarmSound(){
 try{context??=new AudioContext();if(context.state==='suspended')void context.resume()}catch{/* Audio unavailable. */}
}
export function playAlarm(kind:'alarm'|'fib'){
 const ctx=context;if(!ctx||ctx.state!=='running')return;
 const start=ctx.currentTime+.02,tones=kind==='alarm'?[880,660,880,660,880,660]:[740,988];
 tones.forEach((frequency,i)=>{
  const at=start+i*.2,osc=ctx.createOscillator(),gain=ctx.createGain();
  osc.type='sine';osc.frequency.value=frequency;
  gain.gain.setValueAtTime(.0001,at);gain.gain.exponentialRampToValueAtTime(.22,at+.02);gain.gain.exponentialRampToValueAtTime(.0001,at+.18);
  osc.connect(gain).connect(ctx.destination);osc.start(at);osc.stop(at+.19);
 });
}
