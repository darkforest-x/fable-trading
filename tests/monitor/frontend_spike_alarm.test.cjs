'use strict';
const test=require('node:test'),assert=require('node:assert/strict');
const {create,sound,packet,ACTIVE_MS,sirenMotion}=require('../../yoyo/monitor/static/spike-alarm.js');
const NOW=Date.parse('2026-10-27T04:00Z');
const alert=(id=1,extra={})=>({id,side:'lower',n:2,symbols:['BTC-USDT-SWAP','ETH-USDT-SWAP'],first_ms:NOW-60000,last_ms:NOW,created_ms:NOW,ack_ms:null,test:0,...extra});
const flush=async()=>{for(let i=0;i<15;i++)await Promise.resolve();};
test('siren releases all looping animations on hide, acknowledgement and motion preference changes',()=>{
 const reduced={matches:false};let hidden=false;const controls=[];
 const motion=sirenMotion({root:{querySelector:x=>x,querySelectorAll:()=>['ring1','ring2','ring3']},reduced,hidden:()=>hidden,
  animate:(node,frames,options)=>{const control={node,frames,options,cancel(){this.cancelled=true}};controls.push(control);return control;}});
 motion.enter();motion.sync(true);const first=controls.length;assert.equal(controls.filter(c=>c.options.repeat===Infinity).length,7);
 motion.sync(true);assert.equal(controls.length,first,'updates must not duplicate loops');
 reduced.matches=true;motion.sync(true);assert.ok(controls.every(c=>c.cancelled));
 reduced.matches=false;motion.sync(true);hidden=true;motion.sync(true);assert.ok(controls.every(c=>c.cancelled));
 hidden=false;motion.sync(true);motion.leave();motion.sync(false);assert.ok(controls.every(c=>c.cancelled));
 motion.sync(true);motion.stop();assert.ok(controls.every(c=>c.cancelled));
});
function harness(){
 let now=0,sequence=0;const sources=[],timers=new Map(),requests=[],states=[];
 const api=create({source:url=>{const s={url,readyState:1,listeners:{},addEventListener(k,v){this.listeners[k]=v},close(){this.closed=true},send(active,extra={}){this.listeners.alerts({data:JSON.stringify({version:1,now_ms:NOW+now,active,...extra})})}};sources.push(s);return s;},
  request:(url,opts)=>new Promise((resolve,reject)=>requests.push({url,opts,resolve,reject})),clock:()=>now,later:(fn,ms)=>{timers.set(++sequence,{fn,ms});return sequence},cancel:id=>timers.delete(id),change:s=>states.push(s)});
 return {api,sources,timers,requests,states,advance:ms=>now+=ms,runExpiry(){const job=[...timers].find(([,t])=>t.ms!==12000);if(job){timers.delete(job[0]);job[1].fn();}}};
}
test('one event stream survives updates without polling and reconnect snapshots can reset version',()=>{
 const h=harness();h.api.start();h.api.start();assert.equal(h.sources.length,1);assert.equal(h.sources[0].url,'/api/spike-alerts/stream');
 h.sources[0].send([alert()],{version:200});assert.equal(h.api.state().selected,1);
 h.sources[0].onerror();assert.equal(h.api.state().connection,'reconnecting');assert.equal(h.api.state().alerts.length,1);
 h.sources[0].send([alert(2)],{version:0});assert.equal(h.api.state().selected,2);assert.equal(h.requests.length,0);
 h.api.stop();assert.equal(h.sources[0].closed,true);assert.equal(h.timers.size,0);h.sources[0].send([alert(9)]);assert.equal(h.api.state().selected,2);
 h.api.start();assert.equal(h.sources.length,2);h.api.stop();
});
test('six-hour expiry is local server-relative scheduling, including returning from a suspended page',()=>{
 const h=harness();h.api.start();h.sources[0].send([alert()]);assert.equal([...h.timers.values()][0].ms,ACTIVE_MS+1);
 h.advance(ACTIVE_MS+1);h.runExpiry();assert.equal(h.api.state().alerts.length,0);assert.equal(h.requests.length,0);assert.equal(h.timers.size,0);
 h.sources[0].send([alert(2,{created_ms:NOW+ACTIVE_MS+1})]);h.api.stop();h.advance(ACTIVE_MS+1);h.api.start();assert.equal(h.api.state().alerts.length,0);h.api.stop();
});
test('malformed payloads retain the current alarm and expose a data error',()=>{
 const h=harness();h.api.start();h.sources[0].send([alert()]);
 for(const extra of [{side:'long'},{n:3},{symbols:['<img>','ETH-USDT-SWAP']},{ack_ms:NOW},{created_ms:'bad'}]){
  assert.throws(()=>packet({version:1,now_ms:NOW,active:[alert(2,extra)]}));h.sources[0].send([alert(2,extra)]);assert.equal(h.api.state().selected,1);assert.equal(h.api.state().connection,'invalid');
 }
 h.sources[0].send([]);assert.equal(h.api.state().alerts.length,0);h.api.stop();
});
test('acknowledgement is explicit, deduplicated, identity-bound and cannot clear a newer alert',async()=>{
 const h=harness();h.api.start();h.sources[0].send([alert()]);
 const first=h.api.acknowledge();h.api.acknowledge();assert.equal(h.requests.length,1);const r=h.requests[0];
 assert.equal(r.url,'/api/spike-alerts/1/ack');assert.equal(r.opts.method,'POST');assert.equal(r.opts.headers['x-spike-action'],'ack-spike-alert');assert.equal(r.opts.credentials,'same-origin');
 assert.equal(h.api.state().alerts.length,1);assert.equal(h.api.state().ackPending,true);
 h.sources[0].send([alert(2),alert()]);assert.equal(h.api.state().selected,1);
 r.resolve({ok:true,json:async()=>({id:1,ack_ms:NOW+1})});assert.equal(await first,true);assert.equal(h.api.state().selected,2);
 h.sources[0].send([alert(),alert(2)]);assert.equal(h.api.state().selected,2,'late snapshot must not reopen our acknowledged ID');h.api.stop();
});
test('failed ack stays visible for retry; another page acknowledgement removes it',async()=>{
 const h=harness();h.api.start();h.sources[0].send([alert()]);const first=h.api.acknowledge();h.requests[0].resolve({ok:false});assert.equal(await first,false);
 assert.equal(h.api.state().alerts.length,1);assert.match(h.api.state().ackError,/重试/);
 const retry=h.api.acknowledge();h.sources[0].send([]);h.requests[1].resolve({ok:true,json:async()=>({id:1,ack_ms:NOW+1})});await retry;assert.equal(h.api.state().selected,null);h.api.stop();
});
test('test sends only the local alert action, no optimistic synthetic alarm or trade request',async()=>{
 const h=harness();h.api.start();assert.equal(await h.api.test(),false);h.sources[0].send([]);
 const t=h.api.test();h.api.test();assert.equal(h.requests.length,1);const r=h.requests[0];assert.equal(r.url,'/api/spike-alerts/test');assert.equal(r.opts.headers['x-spike-action'],'test-spike-alert');
 assert.equal(h.api.state().alerts.length,0);r.resolve({ok:true,json:async()=>({id:8,test:true})});assert.equal(await t,true);
 h.sources[0].send([alert(8,{test:1})]);assert.equal(h.api.state().alerts[0].test,1);h.api.stop();
});
test('retired acknowledgements cannot mutate a resumed page',async()=>{
 const h=harness();h.api.start();h.sources[0].send([alert()]);const t=h.api.acknowledge();h.api.stop();assert.equal(h.requests[0].opts.signal.aborted,true);
 h.api.start();h.sources[1].send([alert(2)]);h.requests[0].resolve({ok:true,json:async()=>({id:1,ack_ms:NOW})});await t;assert.equal(h.api.state().selected,2);h.api.stop();
});
test('sound requires enable, loops in the audio engine, stops on ack/mute, and reports suspension',async()=>{
 const contexts=[],nodes=[];class Audio {
  constructor(){this.state='suspended';this.sampleRate=8000;contexts.push(this)}
  async resume(){this.state='running';this.onstatechange?.()}
  async suspend(){this.state='suspended';this.onstatechange?.()}
  createBuffer(ch,n,rate){const data=new Float32Array(n);return {getChannelData:()=>data,data,rate}}
  createBufferSource(){const n={connect(){},disconnect(){},start(){this.started=true},stop(){this.stopped=true}};nodes.push(n);return n}
 }
 const a=sound({AudioContext:Audio});a.setActive(true);assert.equal(contexts.length,0);
 assert.equal(await a.enable(),true);assert.equal(a.state(),'on');assert.equal(nodes.length,1);assert.equal(nodes[0].loop,true);assert.ok(nodes[0].buffer.data.some(v=>Math.abs(v)>.05));assert.ok(nodes[0].buffer.data.every(v=>Math.abs(v)<=.121));
 a.setActive(true);assert.equal(nodes.length,1);a.setActive(false);assert.equal(nodes[0].stopped,true);a.setActive(true);assert.equal(nodes.length,2);
 contexts[0].state='suspended';contexts[0].onstatechange();assert.equal(a.state(),'blocked');assert.equal(nodes[1].stopped,true);
 await a.enable();assert.equal(nodes.length,3);a.mute();assert.equal(a.state(),'off');assert.equal(nodes[2].stopped,true);
 assert.equal(sound({AudioContext:null}).state(),'unsupported');
});
