'use strict';
const test=require('node:test'),assert=require('node:assert/strict');
const {create}=require('../../yoyo/monitor/static/monday-quick.js');
const flush=()=>new Promise(resolve=>setImmediate(resolve));
function node(dataset={}){const events={};return {dataset,events,hidden:true,textContent:'',attrs:{},addEventListener:(k,f)=>events[k]=f,setAttribute(k,v){this.attrs[k]=v;},focus(){this.focused=true;}};}
function harness(load=async()=>{}){
  const trigger=node(),close=node(),paper=node({quickMode:'paper'}),live=node({quickMode:'live'}),status=node(),host=node(),panel=node();
  panel.querySelector=s=>s==='[data-quick-close]'?close:status;panel.querySelectorAll=()=>[paper,live];
  const calls=[],symbol={value:'BTC-USDT-SWAP'};
  const owners=Object.fromEntries(['paper','live'].map(mode=>[mode,{openQuick:(h,c)=>calls.push([mode,'open',c.symbol]),closeQuick:()=>calls.push([mode,'close'])}]));
  const api=create({panel,host,trigger,context:()=>({symbol:symbol.value}),visibility:()=>{},load,paper:()=>owners.paper,live:()=>owners.live});
  return {api,trigger,close,paper,live,host,panel,status,calls,symbol};
}
test('opening captures the symbol once and changing mode releases the previous owner',async()=>{
  const c=harness();c.trigger.events.click();await flush();
  c.symbol.value='ETH-USDT-SWAP';c.live.events.click();await flush();
  assert.deepEqual(c.calls,[['paper','open','BTC-USDT-SWAP'],['paper','close'],['live','open','BTC-USDT-SWAP']]);
  c.close.events.click();assert.equal(c.panel.hidden,true);assert.equal(c.trigger.focused,true);
  assert.deepEqual(c.calls.at(-1),['live','close']);
});
test('closing while scripts load cannot mount a hidden trading owner',async()=>{
  let resolve;const c=harness(()=>new Promise(r=>resolve=r));
  c.trigger.events.click();c.api.close(false);resolve();await flush();
  assert.deepEqual(c.calls,[]);assert.equal(c.panel.hidden,true);assert.equal(c.trigger.focused,undefined);
});
test('a slow previous environment cannot overwrite the selected environment',async()=>{
  let resolve;const c=harness(mode=>mode==='paper'?new Promise(r=>resolve=r):Promise.resolve());
  c.trigger.events.click();c.live.events.click();await flush();resolve();await flush();
  assert.deepEqual(c.calls,[['live','open','BTC-USDT-SWAP']]);assert.equal(c.panel.dataset.mode,'live');
});
test('each new opening returns to simulation and refreshes the captured symbol',async()=>{
  const c=harness();c.trigger.events.click();await flush();c.live.events.click();await flush();c.api.close();
  c.symbol.value='W-USDT-SWAP';c.trigger.events.click();await flush();
  assert.equal(c.panel.dataset.mode,'paper');assert.deepEqual(c.calls.at(-1),['paper','open','W-USDT-SWAP']);
});
