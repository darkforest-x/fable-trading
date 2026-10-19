'use strict';
const test=require('node:test'),assert=require('node:assert/strict');
const {createMotion}=require('../../yoyo/monitor/static/workspace-motion.js');
function harness(){
  const media={matches:false,addEventListener(_,fn){this.change=fn;}};
  let hidden=false;
  const api=createMotion({reduced:media,hidden:()=>hidden,style:()=>({getPropertyValue:key=>key==='--motion-enter'?'280ms':key==='--motion-panel'?'220ms':''})});
  function node(){return {isConnected:true,visible:true,animations:[],getClientRects(){return this.visible?[1]:[];},animate(frames,options){
    let resolve,reject;const a={frames,options,cancelled:false,finished:new Promise((yes,no)=>{resolve=yes;reject=no;}),finish:()=>resolve(),cancel(){this.cancelled=true;reject(Error('cancelled'));}};
    this.animations.push(a);return a;
  }};}
  return {api,media,node,hide(){hidden=true;}};
}
test('rapid navigation cancels old motion; same-route refresh never replays entry',async()=>{
  const {api,node}=harness(),a=node(),b=node();
  api.route('market',a);api.route('market',a);assert.equal(a.animations.length,1);
  assert.equal(a.animations[0].frames[0].translate,undefined,'fixed charts keep their containing block');
  api.route('signals',b);assert.equal(a.animations[0].cancelled,true);assert.equal(b.animations.length,1);
  await Promise.resolve();api.stop();assert.equal(b.animations[0].cancelled,true);
});
test('completion of a superseded animation cannot remove its replacement',async()=>{
  const {api,node}=harness(),a=node();api.enter(a);api.enter(a);await Promise.resolve();
  api.cancel(a);assert.equal(a.animations[1].cancelled,true);
});
test('reduced motion cancels active animations and remains immediate until preference changes',()=>{
  const {api,media,node}=harness(),a=node();api.enter(a);media.matches=true;media.change();
  assert.equal(a.animations[0].cancelled,true);assert.equal(api.enter(a),null);
  media.matches=false;media.change();api.enter(a);assert.equal(a.animations.length,2);api.stop();
});
test('late signal content reveals at most six cards once; polling replacements and stale routes stay still',()=>{
  const {api,node}=harness(),page=node(),cards=Array.from({length:12},node);
  api.route('signals',page);api.cards('signals',[]);api.cards('signals',cards);
  assert.deepEqual(cards.map(c=>c.animations.length),[1,1,1,1,1,1,0,0,0,0,0,0]);
  assert.equal(cards[5].animations[0].options.delay,120);
  const polled=Array.from({length:12},node);api.cards('signals',polled);assert.equal(polled[0].animations.length,0);
  api.route('market',page);api.cards('signals',polled);assert.equal(polled[0].animations.length,0);api.stop();
});
test('hidden, disconnected, unsupported and background surfaces are never animated',()=>{
  const {api,node,hide}=harness(),a=node();a.visible=false;assert.equal(api.enter(a),null);
  a.visible=true;a.isConnected=false;assert.equal(api.enter(a),null);
  a.isConnected=true;const animate=a.animate;delete a.animate;assert.equal(api.enter(a),null);
  a.animate=animate;hide();assert.equal(api.enter(a),null);assert.equal(a.animations.length,0);
});
test('cold navigation waits for content; stale readiness cannot animate after leaving and returning',()=>{
  const {node}=harness(),callbacks=[],disposed=[];
  const api=createMotion({ready:(_node,run)=>{const i=callbacks.push(run)-1;return ()=>disposed.push(i);}});
  const a=node(),b=node();api.route('market',a);assert.equal(a.animations.length,0);
  api.route('signals',b);api.route('market',a);
  callbacks[0]();callbacks[1]();assert.equal(a.animations.length,0);assert.equal(b.animations.length,0);
  callbacks[2]();assert.equal(a.animations.length,1);assert.deepEqual(disposed,[0,1]);
  api.stop();assert.equal(a.animations[0].cancelled,true);assert.deepEqual(disposed,[0,1,2]);
});
