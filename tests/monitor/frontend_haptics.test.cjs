'use strict';
const test=require('node:test'),assert=require('node:assert/strict');
const {create}=require('../../yoyo/monitor/static/haptics.js');
function fixture(){let now=0,value=null,mobile=true;const pulses=[];const env={document:{hidden:false},performance:{now:()=>now},localStorage:{getItem:()=>value,setItem:(_,v)=>value=v},matchMedia:()=>({matches:mobile}),navigator:{userActivation:{hasBeenActive:true},vibrate:v=>{pulses.push(v);return true}}};return {env,pulses,api:create(env),time:v=>now=v,mobile:v=>mobile=v,value:()=>value};}
test('short mobile pulses throttle, respect persisted opt-out and do not run in background',()=>{
 const f=fixture();assert.equal(f.api.pulse(),true);assert.equal(f.api.pulse(),false);f.time(121);f.api.pulse('edge');assert.deepEqual(f.pulses,[8,18]);
 f.api.setEnabled(false);assert.equal(f.value(),'off');f.time(500);assert.equal(f.api.pulse(),false);f.api.setEnabled(true);assert.equal(f.pulses.length,3);
 f.env.document.hidden=true;f.time(1000);assert.equal(f.api.pulse(),false);f.env.document.hidden=false;f.mobile(false);assert.equal(f.api.pulse(),false);
});
test('unsupported API, denied activation, storage failure and vibration exceptions are non-fatal',()=>{
 const f=fixture();delete f.env.navigator.vibrate;assert.equal(f.api.supported(),false);assert.equal(f.api.pulse(),false);
 f.env.navigator.vibrate=()=>{throw Error('blocked')};assert.equal(f.api.pulse(),false);f.env.navigator.userActivation.hasBeenActive=false;assert.equal(f.api.pulse(),false);
 f.env.localStorage={getItem(){throw Error('storage')},setItem(){throw Error('storage')}};const a=create(f.env);assert.doesNotThrow(()=>a.setEnabled(false));assert.equal(a.enabled(),false);
});
