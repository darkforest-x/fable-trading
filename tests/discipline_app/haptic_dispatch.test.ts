import test from 'node:test';
import assert from 'node:assert/strict';
import {createHapticDispatch} from '../../yoyo/discipline_app/mobile/src/hapticDispatch.ts';
test('native feedback coalesces duplicate open/select/close pulses but preserves outcome notifications',async()=>{
 let time=0;const impacts:string[]=[],notices:string[]=[];
 const dispatch=createHapticDispatch({native:()=>true,visible:()=>true,mobile:()=>false,now:()=>time,vibrate:()=>{throw Error('native must not also vibrate')},impact:async k=>{impacts.push(k)},notification:async k=>{notices.push(k)}});
 assert.equal(dispatch('light'),true);assert.equal(dispatch('light'),false);assert.equal(dispatch('success'),true);
 time=121;assert.equal(dispatch('tick'),true);assert.deepEqual(impacts,['light','light']);assert.deepEqual(notices,['success']);
});
test('unsupported, background, desktop and failing native bridges remain non-fatal',async()=>{
 let native=false,visible=true,mobile=true,calls=0;
 const dispatch=createHapticDispatch({native:()=>native,visible:()=>visible,mobile:()=>mobile,now:()=>1000,vibrate:()=>false,impact:async()=>{calls++;throw Error('no hardware')},notification:async()=>{throw Error('no hardware')}});
 assert.equal(dispatch('light'),false);visible=false;assert.equal(dispatch('medium'),false);visible=true;mobile=false;assert.equal(dispatch('heavy'),false);
 native=true;assert.equal(dispatch('success'),true);await new Promise(resolve=>setTimeout(resolve,0));assert.equal(calls,0);
});
test('mobile web uses short pulses and never fabricates hardware support',()=>{
 const values:(number|number[])[]=[];let time=0;
 const dispatch=createHapticDispatch({native:()=>false,visible:()=>true,mobile:()=>true,now:()=>time,vibrate:p=>{values.push(p);return true},impact:async()=>{},notification:async()=>{}});
 dispatch('light');time=50;dispatch('heavy');time=70;dispatch('heavy');assert.deepEqual(values,[8,18]);
});
