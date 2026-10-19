'use strict';
const test=require('node:test'),assert=require('node:assert/strict');
const {Flow}=require('../../yoyo/monitor/static/manual-live.js');
const ref={job_id:'a'.repeat(32),symbol:'ETH-USDT-SWAP',detection_id:'b'.repeat(24)};
const draft=id=>({id,queue_state:'preview',expires_ms:2000,side:'long'});
function harness(){const calls=[],resolvers=[];const f=new Flow((path,opts)=>{calls.push({path,opts});return new Promise((resolve,reject)=>resolvers.push({resolve,reject}));},()=>1000);f.reset(ref);return{f,calls,resolvers};}
test('opening/preview has no confirmation or order request',async()=>{const h=harness(),p=h.f.preview({rr:3});assert.equal(h.f.canConfirm(),false);h.resolvers[0].resolve(draft('one'));await p;assert.equal(h.f.canConfirm(),true);assert.equal(h.calls.length,1);assert.equal(h.calls[0].path,'/preview');});
test('editing invalidates in-flight preview and confirmed values',async()=>{const h=harness(),p=h.f.preview({rr:3});h.f.edit();h.resolvers[0].resolve(draft('old'));await p;assert.equal(h.f.draft,null);assert.equal(h.f.canConfirm(),false);});
test('late preview cannot replace newly selected detection',async()=>{const h=harness(),old=h.f.preview({rr:3});h.f.reset({...ref,symbol:'BTC-USDT-SWAP'});const next=h.f.preview({rr:5});h.resolvers[1].resolve(draft('new'));await next;h.resolvers[0].resolve(draft('old'));await old;assert.equal(h.f.draft.id,'new');assert.equal(JSON.parse(h.calls[1].opts.body).symbol,'BTC-USDT-SWAP');});
test('double click sends exactly one confirmation, with no edited terms',async()=>{const h=harness();h.f.draft=draft('one');const p=h.f.confirm();await h.f.confirm();assert.equal(h.calls.length,1);assert.deepEqual(JSON.parse(h.calls[0].opts.body),{acknowledgement:'CONFIRM-LIVE-ORDER'});h.resolvers[0].resolve({...draft('one'),state:'queued',queue_state:'confirmed'});await p;assert.equal(h.f.canConfirm(),false);});
test('unknown confirmation polls original id instead of reposting',async()=>{const h=harness();h.f.draft=draft('one');const p=h.f.confirm();h.resolvers[0].reject(Error('timeout'));await p;await h.f.confirm();assert.equal(h.calls.length,1);const get=h.f.status();assert.equal(h.calls[1].path,'/one');assert.equal(h.calls[1].opts,undefined);h.resolvers[1].resolve({...draft('one'),queue_state:'confirmed',state:'entry_pending'});await get;assert.equal(h.f.sent,'one');});
test('a confirmed order cannot change terms or start another preview',async()=>{const h=harness();h.f.draft=draft('one');const p=h.f.confirm();h.f.edit();await h.f.preview({rr:5});assert.equal(h.calls.length,1);h.resolvers[0].resolve({...draft('one'),state:'queued'});await p;});
test('expiry prevents submission even before next render',async()=>{const h=harness();h.f.draft={...draft('one'),expires_ms:1000};await h.f.confirm();assert.equal(h.calls.length,0);});
test('closing or switching modal prevents stale confirmation response overwrites',async()=>{const h=harness();h.f.draft=draft('one');const p=h.f.confirm();h.f.reset({...ref,symbol:'BTC-USDT-SWAP'});h.resolvers[0].resolve({...draft('one'),state:'queued'});await p;assert.equal(h.f.draft,null);assert.equal(h.f.ref.symbol,'BTC-USDT-SWAP');});
const {validateTerms}=require('../../yoyo/monitor/static/manual-live.js');
const {limitMessage}=require('../../yoyo/monitor/static/manual-live.js');
test('venue limit explains risk budget and contract quantity rather than a generic failure',()=>{
  const d={code:'market_order_size_limit',requested_size:'7873.6',max_market_size:'7000',target_risk:'4.724166',price:'0.07697',stop:'0.07691',stop_distance:'0.00006'};
  const message=limitMessage(d);assert.match(message,/7,873.6 张/);assert.match(message,/7,000 张/);assert.match(message,/4.724166 USDT/);assert.match(message,/0.00006/);
  assert.equal(limitMessage({...d,requested_size:'<script>'}),'');assert.equal(limitMessage(null),'');
});
test('custom stop validation catches invalid numbers before requesting a preview',()=>{for(const v of ['','0','-1','NaN','1e3','1,000','1000000000000'])assert.ok(validateTerms(v,3,true));assert.equal(validateTerms('0.005',5,true),'');assert.equal(validateTerms('',3,false),'');assert.ok(validateTerms('',4,false));});
test('display-only direction and timeframe never enter the strict preview request',async()=>{const h=harness();h.f.reset({...ref,side:'short',timeframe:'5m'});const p=h.f.preview({rr:5});assert.deepEqual(JSON.parse(h.calls[0].opts.body),{...ref,rr:5});h.resolvers[0].resolve(draft('one'));await p;});
test('late status from a previous order cannot overwrite a new ticket',async()=>{const h=harness();h.f.sent='one';const p=h.f.status();h.f.reset({...ref,symbol:'BTC-USDT-SWAP'});h.resolvers[0].resolve({...draft('one'),state:'active'});await p;assert.equal(h.f.draft,null);assert.equal(h.f.sent,null);});
const {marginMessage}=require('../../yoyo/monitor/static/manual-live.js');
test('margin rejection shows leverage, required funds, available funds and difference',()=>{
  const d={code:'insufficient_margin',required_margin:'98.3274',usable_margin:'90.4515',shortfall:'7.8759',leverage:'10',requested_size:'870',target_risk:'4.7887'};
  const msg=marginMessage(d);for(const word of ['98.33 USDT','90.45 USDT','7.88 USDT','10 倍','870 张'])assert.ok(msg.includes(word));
  assert.equal(marginMessage({...d,shortfall:'<script>'}),'');assert.equal(marginMessage(null),'');
});
const reference=()=>({display_only:true,symbol:ref.symbol,side:'long',rr:3,price:'100',initial_stop:'98',take_profit:'106',expires_ms:2000});
test('reference appears while eligibility waits and can never confirm',async()=>{
  const h=harness(),p=h.f.preview({rr:3}),r=h.f.referenceFor({rr:3});
  h.resolvers[1].resolve(reference());await r;
  assert.equal(h.f.busy,true);assert.equal(h.f.display().take_profit,'106');assert.equal(h.f.canConfirm(),false);
  await h.f.confirm();assert.equal(h.calls.length,2);
  h.resolvers[0].reject(Error('margin'));await p;assert.equal(h.f.display().price,'100');assert.equal(h.f.draft,null);
});
test('rejection keeps its exact prices over a later public quote',async()=>{
  const h=harness(),p=h.f.preview({rr:3}),r=h.f.referenceFor({rr:3});
  const e=Error('margin');e.reference={...reference(),price:'101',take_profit:'110'};
  h.resolvers[0].reject(e);await p;h.resolvers[1].resolve(reference());await r;
  assert.equal(h.f.display().price,'101');assert.equal(h.f.canConfirm(),false);
});
test('editing or closing discards late reference responses',async()=>{
  for(const action of ['edit','reset']){const h=harness(),r=h.f.referenceFor({rr:3});h.f[action]();h.resolvers[0].resolve(reference());await r;assert.equal(h.f.reference,null);}
});
test('executable preview takes precedence and survives reference failure',async()=>{
  const h=harness(),p=h.f.preview({rr:3}),r=h.f.referenceFor({rr:3});
  h.resolvers[0].resolve(draft('one'));await p;h.resolvers[1].reject(Error('market unavailable'));await r;
  assert.equal(h.f.display().id,'one');assert.equal(h.f.canConfirm(),true);
});
test('foreign symbol, direction or confirmable data are not reference prices',async()=>{
  for(const delta of [{symbol:'BTC-USDT-SWAP'},{side:'short'},{rr:5},{id:'order'},{queue_state:'preview'}]){
    const h=harness();h.f.reset({...ref,side:'long'});const r=h.f.referenceFor({rr:3});h.resolvers[0].resolve({...reference(),...delta});await r;
    assert.equal(h.f.reference,null);assert.ok(h.f.referenceError);assert.equal(h.f.canConfirm(),false);
  }
});
test('a blocking account error exposes the cause and polls GET-only until recovery',async()=>{
  const h=harness(),p=h.f.preview({rr:3});
  const error=Error('SAND 旧单需要对账');error.detail={status:'blocked',code:'reconciliation_blocked',message:error.message,issues:[{symbol:'SAND-USDT-SWAP'}],checked_ms:1000};
  h.resolvers[0].reject(error);await p;
  assert.equal(h.f.blocker.code,'reconciliation_blocked');assert.equal(h.f.canConfirm(),false);
  const check=h.f.readiness();assert.equal(h.calls[1].path,'/readiness');assert.equal(h.calls[1].opts,undefined);
  h.resolvers[1].resolve({...error.detail,checked_ms:1500});assert.equal(await check,false);
  const recovery=h.f.readiness();h.resolvers[2].resolve({status:'ready'});assert.equal(await recovery,true);
  assert.equal(h.f.blocker,null);assert.equal(h.f.error,'');assert.equal(h.f.recovered,true);
  assert.equal(h.f.canConfirm(),false);assert.equal(h.f.draft,null);
  assert.equal(h.calls.filter(c=>c.path==='/preview').length,1);
});
test('late account recovery cannot overwrite another ticket',async()=>{
  const h=harness();h.f.blocker={status:'blocked',code:'reconciliation_blocked'};
  const check=h.f.readiness();h.f.reset({...ref,symbol:'BTC-USDT-SWAP'});
  h.resolvers[0].resolve({status:'ready'});await check;assert.equal(h.f.recovered,false);
});

test('editing and closing abort obsolete preview and reference reads only',async()=>{
 const h=harness();const p=h.f.preview({rr:3}),r=h.f.referenceFor({rr:3});
 h.f.edit();assert.equal(h.calls[0].opts.signal.aborted,true);assert.equal(h.calls[1].opts.signal.aborted,true);
 h.resolvers[0].resolve(draft('old'));h.resolvers[1].resolve(reference());await Promise.all([p,r]);
 assert.equal(h.f.draft,null);assert.equal(h.f.reference,null);
 h.f.draft=draft('real');const c=h.f.confirm();h.f.reset();
 assert.equal(h.calls[2].opts.signal,undefined);h.resolvers[2].resolve(draft('real'));await c;
});

test('an explicit pre-confirmation block recovers as a blocker, unknown submission still polls',async()=>{
 const h=harness();h.f.draft=draft('one');const p=h.f.confirm();
 const e=Error('账户核验暂未完成');e.detail={status:'blocked',code:'worker_check_failed',message:e.message};
 h.resolvers[0].reject(e);await p;
 assert.equal(h.f.sent,null);assert.equal(h.f.draft,null);assert.equal(h.f.blocker.code,'worker_check_failed');
 assert.equal(h.f.canConfirm(),false);
});

test('preview expiry never labels confirmed position protection as expired',()=>{
 const {quoteExpired}=require('../../yoyo/monitor/static/manual-live.js');
 assert.equal(quoteExpired({expires_ms:100},false,200),true);
 assert.equal(quoteExpired({expires_ms:100,state:'active'},true,200),false);
 assert.equal(quoteExpired({expires_ms:100},false,50),false);
 assert.equal(quoteExpired(null,false,200),false);
});
