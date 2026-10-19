'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const source=fs.readFileSync(require('node:path').join(__dirname,'../../yoyo/monitor/static/okx-execution.js'),'utf8');
function fixture(){return {read_only:true,config:{capital_usdt:'125',risk_fraction:'0.02',nominal_risk_usdt:'2.50',margin_mode:'cross',timeframes:['15m','30m','1H','4H'],demo:false},blockers:['position_mode'],account:{status:'ready',updated_ms:Date.now(),data:{balance:{eq:'125',cashBal:'125',availBal:'999',availEq:'125',frozenBal:'0',upl:'0'},positions:[],orders:[],protection_orders:[],position_count:0,order_count:0,protection_count:0}},ledger:{status:'ready',worker:{status:'not_started'},items:[],total:0}};}
function harness(){
 const handlers={},intervals=new Map(),timeouts=new Map(),nodes=new Map(),visibility={};let next=0,respond=async()=>({ok:true,status:200,json:async()=>fixture()});
 const node=key=>{if(!nodes.has(key))nodes.set(key,{_content:'',get innerHTML(){return this._content;},set innerHTML(v){this._content=v;},get textContent(){return this._content;},set textContent(v){this._content=v;},classList:{toggle(){}},querySelector(){return null;},setAttribute(k,v){this[k]=v;},focus(){document.activeElement=this;}});return nodes.get(key);};
 let shell='';const root={hidden:true,addEventListener(name,fn){handlers[name]=fn;},querySelector:node,
  set innerHTML(v){shell=v;},get innerHTML(){return shell+[...nodes.values()].map(n=>n.innerHTML+n.textContent).join('');}};
 const document={hidden:false,activeElement:null,getElementById:()=>root,addEventListener(name,fn){visibility[name]=fn;}};
 const calls=[],window={};
 vm.runInNewContext(source,{window,document,AbortController,Date,
  setTimeout:(fn,ms)=>{timeouts.set(++next,{fn,ms});return next;},clearTimeout:id=>timeouts.delete(id),
  setInterval:fn=>{intervals.set(++next,fn);return next;},clearInterval:id=>intervals.delete(id),fetch:(url,options)=>{calls.push({url,options});return respond(url,options);}});
 return {root,node,document,api:window.SpikeOKXExecution,calls,intervals,timeouts,respond(fn){respond=fn;},
  hide(value){document.hidden=value;visibility.visibilitychange();},
  tab(id){handlers.click({target:{closest:selector=>selector==='[data-okx-tab]'?{dataset:{okxTab:id}}:null}});}};
}

const tick=()=>new Promise(resolve=>setImmediate(resolve));
test('shows actual zero balance fields, budget and mode blocker without trading controls',async()=>{
 const c=harness();c.api.setActive(true);await tick();
 assert.match(c.root.innerHTML,/125\.00/);assert.match(c.root.innerHTML,/2\.50 U/);
 assert.match(c.root.innerHTML,/全仓/);assert.doesNotMatch(c.root.innerHTML,/999/);assert.match(c.root.innerHTML,/可用保证金/);assert.match(c.root.innerHTML,/执行器未启动/);assert.match(c.root.innerHTML,/双向持仓模式/);
 assert.match(c.root.innerHTML,/暂无真实持仓/);assert.match(c.root.innerHTML,/无额外持仓数/);
 assert.ok(c.calls.every(call=>!call.options.method));
 assert.doesNotMatch(c.root.innerHTML,/<button[^>]*>.*(?:启动交易|立即下单|撤单|停止交易)/);
 c.tab('executions');assert.match(c.root.innerHTML,/暂无执行器记录/);
 c.api.setActive(false);assert.equal(c.intervals.size,0);assert.equal(c.root.hidden,true);
});
test('first failure remains unknown; later failure labels cached balances stale',async()=>{
 const c=harness();c.respond(async()=>{throw new Error('offline');});c.api.setActive(true);await tick();
 assert.match(c.root.innerHTML,/未知数值显示 —/);assert.doesNotMatch(c.root.innerHTML,/暂无真实持仓/);
 c.respond(async()=>({ok:true,status:200,json:async()=>fixture()}));await c.api.refresh();
 c.respond(async()=>{throw new Error('offline');});await c.api.refresh();
 assert.match(c.root.innerHTML,/125\.00/);assert.match(c.root.innerHTML,/上次成功快照/);
 assert.match(c.root.innerHTML,/连接失败/);
});
test('access denial removes previously displayed account values',async()=>{
 const c=harness();c.api.setActive(true);await tick();
 c.respond(async()=>({status:403}));await c.api.refresh();
 assert.match(c.root.innerHTML,/未获准读取账户/);assert.doesNotMatch(c.root.innerHTML,/125|2\.50|暂无真实持仓/);
});
test('late response after leaving page cannot resurrect account UI',async()=>{
 const c=harness();let release;c.respond(()=>new Promise(resolve=>{release=resolve;}));
 c.api.setActive(true);const html=c.root.innerHTML;c.api.setActive(false);
 release({ok:true,status:200,json:async()=>fixture()});await tick();
 assert.equal(c.root.hidden,true);assert.equal(c.root.innerHTML,html);
});
test('populated rows escape external text and unknown realized PnL stays unknown',async()=>{
 const c=harness(),value=fixture();value.account.data.positions=[{instId:'<img src=x onerror=alert(1)>',pos:'2',posSide:'long',avgPx:'1',markPx:'2',upl:'2',ccy:'USDT',lever:'50',mgnMode:'isolated'}];
 value.ledger.items=[{symbol:'ALT-USDT-SWAP',side:'long',timeframe:'1H',state:'closed',entry_price:'1',exit_price:'2',stop:'.9',planned_risk:'2.5',realized_net_usdt:null}];
 value.ledger.total=1;
 c.respond(async()=>({ok:true,status:200,json:async()=>value}));c.api.setActive(true);await tick();
 assert.match(c.root.innerHTML,/&lt;img/);assert.doesNotMatch(c.root.innerHTML,/<img/);
 c.tab('executions');assert.match(c.root.innerHTML,/已平仓/);assert.match(c.root.innerHTML,/缺失显示 —/);
 assert.doesNotMatch(c.root.innerHTML,/undefined|NaN|99999/);
});
test('skipped and retrying signals explain reasons with identity and original signal time',async()=>{
 const c=harness(),value=fixture();
 value.ledger.items=[{symbol:'XLM-USDT-SWAP',timeframe:'15m',side:'short',state:'skipped',reason_code:'quote_stale',signal_ms:1792171800000},
 {symbol:'ALT-USDT-SWAP',timeframe:'1H',side:'long',state:'retry_pending',reason_code:'read_unavailable',next_retry_ms:1792172200000}];
 value.ledger.total=2;
 c.respond(async()=>({ok:true,status:200,json:async()=>value}));c.api.setActive(true);await tick();c.tab('executions');
 assert.match(c.root.innerHTML,/XLM-USDT-SWAP/);assert.match(c.root.innerHTML,/15m · 空/);
 assert.match(c.root.innerHTML,/报价时间未通过校验/);assert.match(c.root.innerHTML,/等待重试/);
 assert.match(c.root.innerHTML,/交易所读取暂时失败/);assert.match(c.root.innerHTML,/仅在信号有效期内重试/);
 assert.doesNotMatch(c.root.innerHTML,/undefined|NaN/);
});

test('polling preserves controls, settings and focused table without rebuilding identical data',async()=>{
 const c=harness();c.api.setActive(true);await tick();
 const tab=c.node('[data-okx-tab="positions"]'),settings=c.node('[data-okx-part="settings-title"]');tab.focus();
 const detail=c.node('[data-okx-part="detail"]'),first=detail._markup;
 await c.api.refresh(false);
 assert.equal(c.document.activeElement,tab);assert.equal(c.node('[data-okx-part="settings-title"]'),settings);
 assert.equal(detail._markup,first);assert.equal(tab.tabIndex,0);assert.equal(c.node('[data-okx-tab="orders"]').tabIndex,-1);
});
test('hidden document aborts reads and timers; late response cannot overwrite resume',async()=>{
 const c=harness();let release;c.respond(()=>new Promise(r=>release=r));c.api.setActive(true);
 const old=c.calls[0];c.hide(true);assert.equal(old.options.signal.aborted,true);assert.equal(c.intervals.size,0);
 c.respond(async()=>({ok:true,status:200,json:async()=>fixture()}));c.hide(false);await tick();
 release({ok:true,status:200,json:async()=>({...fixture(),blockers:['old']})});await tick();
 assert.equal(c.calls.length,2);assert.equal(c.intervals.size,1);assert.equal(c.timeouts.size,0);
});
test('stalled account read times out and unlocks retry',async()=>{
 const c=harness();c.respond((url,{signal})=>new Promise((resolve,reject)=>signal.addEventListener('abort',()=>reject(Object.assign(new Error('aborted'),{name:'AbortError'})))));
 c.api.setActive(true);const timeout=[...c.timeouts.values()][0];assert.equal(timeout.ms,15000);timeout.fn();await tick();
 assert.match(c.root.innerHTML,/连接失败/);assert.equal(c.node('[data-okx-refresh]').disabled,false);
 c.respond(async()=>({ok:true,status:200,json:async()=>fixture()}));await c.api.refresh();assert.match(c.root.innerHTML,/125\.00/);
});
test('changed account rows restore both scroll axes and focus; unchanged rows stay mounted',()=>{
 const {updateDetail}=require('../../yoyo/monitor/static/okx-execution.js');
 const before={scrollLeft:240,scrollTop:70},after={focus(){this.focused=true;}};let writes=0;
 const panel={_markup:'old',querySelector:()=>writes?after:before,set innerHTML(v){writes++;}};
 updateDetail(panel,'new',before);assert.equal(after.scrollLeft,240);assert.equal(after.scrollTop,70);assert.equal(after.focused,true);
 updateDetail(panel,'new',after);assert.equal(writes,1);
});

test('aged ready snapshots cannot keep a green connected label',async()=>{
 const c=harness(),value=fixture();value.blockers=[];value.account.updated_ms=Date.now()-61000;
 c.respond(async()=>({ok:true,status:200,json:async()=>value}));c.api.setActive(true);await tick();
 assert.match(c.root.innerHTML,/快照已过时/);assert.match(c.root.innerHTML,/上次成功快照/);
 assert.doesNotMatch(c.root.innerHTML,/okx-badge good/);
});
