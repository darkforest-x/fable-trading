'use strict';
const test=require('node:test'),assert=require('node:assert/strict');
const {create,sound,packet,historyPacket,historyLoader,liveFacts,deskFacts,particleField,replayWindow,replayCoins,alertStatus,alertTimeline,rayItems,RAY_BARS,MAJORS,REPLAY_PERIODS,ACTIVE_MS,sirenMotion}=require('../../yoyo/monitor/static/spike-alarm.js');
const NOW=Date.parse('2026-10-27T04:00Z');
const alert=(id=1,extra={})=>({id,side:'lower',n:2,symbols:['BTC-USDT-SWAP','ETH-USDT-SWAP'],first_ms:NOW-60000,last_ms:NOW,created_ms:NOW,ack_ms:null,test:0,...extra});
const flush=async()=>{for(let i=0;i<15;i++)await Promise.resolve();};
test('alarm motion releases all looping animations on hide, acknowledgement and motion preference changes',()=>{
 const reduced={matches:false};let hidden=false;const controls=[];
 const motion=sirenMotion({root:{querySelector:x=>x,querySelectorAll:()=>['ring1','ring2','ring3']},reduced,hidden:()=>hidden,
  animate:(node,frames,options)=>{const control={node,frames,options,cancel(){this.cancelled=true}};controls.push(control);return control;}});
 motion.enter();motion.sync(true);const first=controls.length;assert.equal(controls.filter(c=>c.options.repeat===Infinity).length,6,'edge, vignette, scan and one loop per ripple');
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
test('history packet keeps delivery and acknowledgement facts, sorts newest first and rejects malformed rows',()=>{
 const row=(id,extra={})=>({id,side:'upper',n:2,symbols:['BTC-USDT-SWAP','ETH-USDT-SWAP'],first_ms:NOW-60000,last_ms:NOW,created_ms:NOW+id,ack_ms:NOW+5,test:0,telegram:'sent',bark:'failed',error:'bark HTTPError',text:'x',active:false,...extra});
 const base={now_ms:NOW,threshold:25,window_minutes:5,cooldown_minutes:30,active_hours:6,channels:{telegram:true,bark:false},notify_only:true,worker_alive:true,active:[]};
 const v=historyPacket({...base,alerts:[row(1),row(2,{ack_ms:null,test:1,telegram:'test',bark:'weird'})]});
 assert.deepEqual(v.alerts.map(a=>a.id),[2,1]);assert.equal(v.alerts[0].test,true);assert.equal(v.alerts[0].bark,'unknown');assert.equal(v.alerts[1].bark,'failed');
 assert.equal(v.alerts[0].ack_ms,null);assert.equal('error' in v.alerts[1],false,'server error strings are not rendered');
 assert.equal(v.threshold,25);assert.equal(v.workerAlive,true);assert.deepEqual(v.channels,{telegram:true,bark:false});
 assert.throws(()=>historyPacket({...base,alerts:[row(1),row(1)]}));assert.throws(()=>historyPacket({...base,alerts:[row(1,{symbols:['<b>-USDT-SWAP']})]}));
 assert.throws(()=>historyPacket({...base,alerts:[row(1,{side:'both'})]}));assert.throws(()=>historyPacket({...base,threshold:'25',alerts:[]}));
});
test('history loads on demand with one same-origin GET, keeps the last good data on failure and drops superseded replies',async()=>{
 const calls=[],states=[];const loader=historyLoader({request:(url,opts)=>new Promise((resolve,reject)=>calls.push({url,opts,resolve,reject})),later:()=>0,cancel:()=>{},change:s=>states.push(s)});
 const good={now_ms:NOW,threshold:25,window_minutes:5,cooldown_minutes:30,channels:{},alerts:[]};
 const first=loader.load();assert.equal(calls.length,1);assert.equal(calls[0].url,'/api/spike-alerts');assert.equal(calls[0].opts.method,undefined);assert.equal(calls[0].opts.credentials,'same-origin');
 const second=loader.load();assert.equal(calls[0].opts.signal.aborted,true,'a newer load aborts the old one');
 calls[0].resolve({ok:true,json:async()=>({...good,threshold:99})});assert.equal(await first,false);
 calls[1].resolve({ok:true,json:async()=>good});assert.equal(await second,true);assert.equal(loader.state().data.threshold,25);
 const third=loader.load();calls[2].resolve({ok:false});assert.equal(await third,false);assert.match(loader.state().error,/重试/);assert.equal(loader.state().data.threshold,25);
 const fourth=loader.load();loader.abort();assert.equal(calls[3].opts.signal.aborted,true);assert.equal(loader.state().loading,false);calls[3].reject(Error('abort'));await fourth;assert.equal(loader.state().error,'');
});
test('replay window starts at the first touch, waits for the alert bar to close and never asks for future bars',()=>{
 const H=3600000,M=60000,a={first_ms:NOW-4*M,last_ms:NOW,symbols:['ETH-USDT-SWAP'],test:false};
 const later=NOW+30*24*H,w=replayWindow(a,'15m',later);
 assert.equal(w.fill_ms,a.first_ms,'entry stays in the alert week so the Monday 1.618 anchors match');assert.equal(w.exit_ms,a.last_ms+96*REPLAY_PERIODS['15m']);assert.equal(w.ready,true);
 const soon=NOW+2*H,near=replayWindow(a,'15m',soon);assert.ok(near.exit_ms<=Math.floor(soon/900000)*900000-900000,'exit bar must already be closed');assert.ok(near.exit_ms>=a.last_ms);
 const open=replayWindow(a,'4H',NOW+60000);assert.equal(open.ready,false);assert.equal(open.exit_ms,a.last_ms);
 assert.equal(replayWindow(a,'2H',later),null);assert.equal(replayWindow({...a,first_ms:NOW+1},'15m',later),null);
});
test('replay coins add BTC and ETH as controls, keep alert order and refuse local test alerts',()=>{
 assert.deepEqual(replayCoins({symbols:['SOL-USDT-SWAP','ETH-USDT-SWAP'],test:false}),['BTC-USDT-SWAP','SOL-USDT-SWAP','ETH-USDT-SWAP']);
 assert.deepEqual(replayCoins({symbols:['ADA-USDT-SWAP','BTC-USDT-SWAP'],test:false}),['ETH-USDT-SWAP','ADA-USDT-SWAP','BTC-USDT-SWAP']);
 assert.deepEqual(replayCoins({symbols:['TEST0-USDT-SWAP'],test:true}),[]);
 assert.equal(alertStatus({ack_ms:NOW,active:false}),'acked');assert.equal(alertStatus({ack_ms:null,active:true}),'pending');assert.equal(alertStatus({ack_ms:null,active:false}),'expired');
});
test('alarm timeline puts replayed alarms before the live book, drops tests and keeps live identity',()=>{
 const live=[alert(5,{last_ms:NOW,first_ms:NOW-60000,created_ms:NOW}),alert(6,{test:1,last_ms:NOW+1,first_ms:NOW+1,created_ms:NOW+1})];
 const replayed=[{ms:NOW-86400000,side:'lower'},{ms:NOW+5,side:'upper'},{ms:'x',side:'lower'},{ms:NOW-2*86400000,side:'both'}];
 const t=alertTimeline(live,replayed);
 assert.deepEqual(t.map(a=>a.id),['r'+(NOW-86400000),5],'replayed rows after the first live alert and malformed rows are dropped');
 assert.equal(t[0].live,false);assert.equal(t[0].first_ms,t[0].last_ms);assert.equal(t[1].live,true);
});
test('alarm rays start at the alarm bar close and run RAY_BARS bars on every period, clipped at the last bar',()=>{
 const step=900000,base=Math.floor(NOW/step)*step,rows=Array.from({length:100},(_,i)=>({time:(base+i*step)/1000,close:100+i}));
 const alerts=[{id:1,side:'lower',last_ms:base+10*step+120000,live:true},{id:'r1',side:'upper',last_ms:base+90*step,live:false},{id:2,side:'lower',last_ms:base-step,live:true}];
 const items=rayItems(rows,alerts,step,1);
 assert.equal(items.length,2,'alarms outside the loaded bars are not drawn');
 assert.deepEqual([items[0].from,items[0].to,items[0].price,items[0].selected],[10,10+RAY_BARS,110,true]);
 assert.deepEqual([items[1].from,items[1].to,items[1].live],[90,99,false]);
 assert.equal(RAY_BARS,72);assert.ok(MAJORS.has('BTC-USDT-SWAP')&&!MAJORS.has('1INCH-USDT-SWAP'));
});
test('desk facts sanitize the live block and measure depth beyond the 1.618 in the trade direction',()=>{
 const M=60000,base={id:9,n:25,symbols:['A-USDT-SWAP'],first_ms:NOW-4*M,last_ms:NOW,created_ms:NOW+M,ack_ms:null,test:0};
 const live={window_end_ms:NOW+30*M,reference:{'BTC-USDT-SWAP':{at:100,now:99},'ETH-USDT-SWAP':{at:'x',now:5}},paper:[{track:'live-wick6x3',opened:1},{track:'<b>',opened:2},{track:'live-allin3',opened:-1}],
  touches:[{symbol:'SOL-USDT-SWAP',level:100,candle_ms:NOW-M,observed_ms:NOW-M+5,price:99},{symbol:'ADA-USDT-SWAP',level:1,candle_ms:NOW+3*M,observed_ms:NOW+3*M+9,price:1.01},
   {symbol:'XRP-USDT-SWAP',level:2,candle_ms:NOW+5*M,observed_ms:NOW+5*M,price:null},{symbol:'bad',level:1,candle_ms:NOW},{symbol:'DOT-USDT-SWAP',level:-1,candle_ms:NOW}]};
 const lf=liveFacts({...base,live});
 assert.equal(lf.touches.length,3);assert.deepEqual(lf.paper,[{track:'live-wick6x3',opened:1}]);assert.deepEqual(lf.reference['ETH-USDT-SWAP'],{at:null,now:5});
 const low=deskFacts({...base,side:'lower',live},NOW+11*M);
 assert.deepEqual(low.flow.map(t=>t.symbol),['XRP-USDT-SWAP','ADA-USDT-SWAP','SOL-USDT-SWAP'],'newest touch first');
 const sol=low.flow.find(t=>t.symbol==='SOL-USDT-SWAP'),ada=low.flow.find(t=>t.symbol==='ADA-USDT-SWAP');
 assert.ok(Math.abs(sol.depth-1)<1e-9);assert.equal(sol.outside,true,'long alarm: price below the lower 1.618 is still outside');assert.equal(ada.outside,false);
 assert.equal(low.later,2);assert.deepEqual(low.latest,['XRP-USDT-SWAP','ADA-USDT-SWAP']);assert.equal(low.first,'SOL-USDT-SWAP');
 assert.ok(Math.abs(low.btc+1)<1e-9);assert.equal(low.eth,null);assert.equal(low.elapsed,10*M);assert.equal(low.remaining,19*M);assert.equal(low.windowMs,30*M);
 const up=deskFacts({...base,side:'upper',live},NOW+40*M);
 assert.equal(up.flow.find(t=>t.symbol==='ADA-USDT-SWAP').outside,true,'short alarm: price above the upper 1.618 is still outside');assert.equal(up.remaining,0);
 const bare=deskFacts({...base,side:'lower'},NOW);assert.deepEqual([bare.flow.length,bare.btc,bare.remaining],[0,null,30*M]);
});
test('particle field drifts with the move, surges on new touches and releases its frame loop',()=>{
 const strokes=[],frames=new Map();let id=0;
 const ctx={setTransform(){},clearRect(){},beginPath(){},moveTo(x,y){strokes.push(['m',y])},lineTo(x,y){strokes.push(['l',y])},stroke(){}};
 const canvas={clientWidth:700,clientHeight:700,getContext:()=>ctx};
 const field=particleField({canvas,raf:fn=>{frames.set(++id,fn);return id},caf:k=>frames.delete(k),random:()=>.5,ratio:()=>1});
 assert.equal(field.start({side:'lower',tone:'#10b981'}),true);assert.equal(field.running,true);assert.equal(field.count,70);
 const run=t=>{const [k,fn]=[...frames][0];frames.delete(k);fn(t);};
 run(1000);strokes.length=0;run(1050);const down=strokes[0][1]>strokes[1][1];assert.equal(down,true,'急跌: the streak head is below its tail');
 field.stop();assert.equal(frames.size,0);assert.equal(field.running,false);
 field.start({side:'upper'});run(2000);strokes.length=0;run(2050);assert.ok(strokes[0][1]<strokes[1][1],'急涨: the streak head is above its tail');
 field.surge();field.stop();assert.equal(frames.size,0);
 assert.equal(particleField({canvas:{getContext:()=>null}}).start(),false,'no 2D context: nothing runs');
});
test('owner 2026-11-09: alerts show the complete coin count; the firing moment is optional and kept apart',()=>{
 const M=60000,S=k=>`C${k}-USDT-SWAP`,all=[0,1,2,3].map(S);
 const full=alert(5,{n:4,symbols:all,trigger_n:2,trigger_symbols:all.slice(0,2),complete:false,follow_end_ms:NOW+30*M});
 assert.equal(packet({version:1,now_ms:NOW,active:[full]}).active[0].n,4);
 assert.doesNotThrow(()=>packet({version:1,now_ms:NOW,active:[alert(6)]}),'older server: no trigger fields');
 for(const extra of [{trigger_n:5},{trigger_n:0},{trigger_symbols:['<b>']},{trigger_symbols:[0,1,2,3,4].map(S)}])
  assert.throws(()=>packet({version:1,now_ms:NOW,active:[{...full,...extra}]}));
 const base={now_ms:NOW,threshold:25,window_minutes:5,cooldown_minutes:30,channels:{telegram:true,bark:true},alerts:[]};
 const [h]=historyPacket({...base,alerts:[{...full,ack_ms:NOW,telegram:'sent',bark:'sent'}]}).alerts;
 assert.deepEqual([h.n,h.trigger_n,h.complete,h.follow_end_ms,h.symbols.length],[4,2,false,NOW+30*M,4]);
 const [old]=historyPacket({...base,alerts:[{...alert(7),ack_ms:NOW,telegram:'sent',bark:'sent'}]}).alerts;
 assert.deepEqual([old.n,old.trigger_n,old.complete,old.follow_end_ms],[2,2,true,null]);
 // 新增 counts coins outside the firing set, including a late touch inside the firing candle.
 const live={window_end_ms:NOW+30*M,touches:[{symbol:S(0),level:1,candle_ms:NOW-M,observed_ms:NOW-M},{symbol:S(1),level:1,candle_ms:NOW,observed_ms:NOW},
  {symbol:S(2),level:1,candle_ms:NOW,observed_ms:NOW+40000},{symbol:S(3),level:1,candle_ms:NOW+4*M,observed_ms:NOW+4*M},{symbol:'PRE-USDT-SWAP',level:1,candle_ms:NOW-3*M,observed_ms:NOW-3*M}]};
 const f=deskFacts({...full,live},NOW+5*M);
 assert.equal(f.later,2);assert.deepEqual(f.latest,[S(3),S(2)]);
});
