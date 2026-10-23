'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname, '../../yoyo/monitor/static/paper-desk.js'), 'utf8');
const flush = () => new Promise(resolve => setImmediate(resolve));

function harness(onPost, beforeGet=async()=>{}, preferences=new Map()) {
  let intervalCallback;
  let timerId=0, elapsed=0;
  const timers=new Map();
  const listeners = {}, storage = new Map(), calls = [], bodyClasses=new Set();
  let html='', focused='', editing=false;
  const nodes={};
  for(const selector of ['#desk-symbol-results','#desk-symbol-search','#desk-symbol-count','[data-desk-symbol-toggle]']){
    nodes[selector]={innerHTML:'',textContent:'',focus(){},setAttribute(){},removeAttribute(){},querySelector:()=>null};
  }
  for(const selector of ['.desk-submit','[data-desk-retry]','[data-desk-create-toggle]'])nodes[selector]={focus:()=>{focused=selector;}};
  const el = {get innerHTML(){return html;}, set innerHTML(value){
      html=value;
      const feedbackStart=value.indexOf('<div class="desk-feedback"');
      if(feedbackStart>=0){
        const start=value.indexOf('>',feedbackStart)+1, prefix=value.slice(0,start), suffix=value.slice(value.lastIndexOf('</div>'));
        nodes['[data-desk-feedback]']={open:false,
          get innerHTML(){return html.slice(start,html.length-suffix.length);},
          set innerHTML(markup){html=prefix+markup+suffix;},
          showPopover(){this.open=true;},hidePopover(){this.open=false;}};
      }else delete nodes['[data-desk-feedback]'];
      for(const name of ['takeProfit','stopLoss']){
        const match=value.match(new RegExp(`<output[^>]*data-desk-estimate="${name}"[^>]*>(.*?)<\\/output>`));
        const selector=`[data-desk-estimate="${name}"]`;
        if(match)nodes[selector]={innerHTML:match[1]};else delete nodes[selector];
      }
      delete nodes['#desk-journal-dialog'];delete nodes['#desk-create-dialog'];delete nodes['#desk-protection-dialog'];delete nodes['#desk-confirm-dialog'];delete nodes['[data-desk-confirm]'];delete nodes['.desk-quote-status'];
      if(!value.includes('<dialog'))return;
      const dialogId=value.match(/<dialog id="([^"]+)"/)[1];
      const handlers={};
      const dialog={open:false,handlers,showModal(){this.open=true;},
        addEventListener:(name,fn)=>{handlers[name]=fn;},
        getBoundingClientRect:()=>({left:100,top:100,right:600,bottom:700}),
        remove:()=>{delete nodes['#'+dialogId];}};
      nodes['#'+dialogId]=dialog;
      nodes['[data-desk-confirm]']={disabled:false,textContent:''};
      nodes['.desk-quote-status']={textContent:'',classList:{add(){}}};
    }, dataset:{}, addEventListener:(event, fn)=>listeners[event]=fn,
    contains:()=>editing, querySelector:selector=>nodes[selector]||null};
  const snapshot = {fee_rate:'0.001',accounts:[{id:'account-1',name:'Practice',initial_cash:'1000',balance:'1000',equity:'1000',
    available:'1000',reserved:'0',unrealized:'0',net_realized:'0',marks_complete:true}], positions:[],fills:[],now_ms:Date.now()};
  const quote = {quantity:'1',sizing:'equity_multiple',id:'a'.repeat(32),symbol:'BTC-USDT-SWAP',bid:'99',ask:'100',source_ms:Date.now(),received_ms:Date.now(),expires_ms:Date.now()+15000};
  const symbols={symbols:[{symbol:'BTC-USDT-SWAP',base:'BTC'},{symbol:'ETH-USDT-SWAP',base:'ETH'},{symbol:'RESOLV-USDT-SWAP',base:'RESOLV'},{symbol:'SOL-USDT-SWAP',base:'SOL'},{symbol:'W-USDT-SWAP',base:'W'}]};
  const sandbox = {window:{},document:{getElementById:()=>el,hidden:false,addEventListener(){},body:{classList:{toggle:(name,on)=>{if(on)bodyClasses.add(name);else bodyClasses.delete(name);}}}},Date,Number,String,JSON,Error,
    encodeURIComponent,crypto:{randomUUID:()=> 'request-uuid-12345678'},
    localStorage:{getItem:k=>preferences.get(k),setItem:(k,v)=>preferences.set(k,v)},
    setTimeout:(fn,delay)=>{const id=++timerId;timers.set(id,{fn,at:elapsed+delay});return id;},clearTimeout:id=>timers.delete(id),
    setInterval:fn=>{intervalCallback=fn;return 1;},clearInterval:()=>{},sessionStorage:{getItem:k=>storage.get(k),setItem:(k,v)=>storage.set(k,v),removeItem:k=>storage.delete(k)},
    FormData:class {constructor(form){this.values=form.values;}get(k){return this.values[k]??null;}},
    fetch:async (url, options)=>{
      calls.push({url,...options});
      if(options?.method==='POST') return onPost(url,options);
      const override=await beforeGet(url);
      if(override)return {ok:true,json:async()=>override};
      const params=new URL(url,'http://localhost').searchParams;
      if(url.includes('/records?')){
        const kind=params.get('kind'),offset=Number(params.get('offset')),limit=Number(params.get('limit'));
        const items=kind==='closed'?snapshot.positions.filter(p=>Number(p.remaining)===0):snapshot.fills;
        return {ok:true,json:async()=>({account_id:snapshot.active_account_id||snapshot.accounts[0]?.id,kind,offset,limit,total:items.length,has_more:offset+limit<items.length,items:items.slice(offset,offset+limit)})};
      }
      const closePosition=snapshot.positions.find(p=>url.includes('/positions/'+p.id+'/close-preview'));
      const closePercent=Number(params.get('percentage'));
      const responseQuote={...quote,position_multiple:Number(params.get('position_multiple')||10),sizing_equity:'1000',take_profit:params.get('take_profit'),stop_loss:params.get('stop_loss')};
      if(params.has('notional_usdt'))Object.assign(responseQuote,{sizing:'fixed_notional',requested_notional:params.get('notional_usdt'),quantity:String(Number(params.get('notional_usdt'))/100)});
      if(closePosition)Object.assign(responseQuote,{sizing:'position_percentage',close_percent:closePercent,quantity:String(Number(closePosition.remaining)*closePercent/100),position_remaining:closePosition.remaining});
      return {ok:true,json:async()=>(url.includes('/quote?')||url.includes('/estimate?')||url.includes('/open-preview?')||url.includes('/close-preview?'))?responseQuote:url.endsWith('/symbols')?symbols:snapshot};
    }};
  vm.runInNewContext(source,sandbox);
  const click = name => listeners.click({target:{closest:()=>({dataset:{},hasAttribute:n=>n==='data-desk-'+name})}});
  const submit = (kind,values) => listeners.submit({preventDefault(){},target:{values,matches:s=>s==='[data-desk-'+kind+']'}});
  const advance=ms=>{elapsed+=ms;for(const [id,timer] of [...timers])if(timer.at<=elapsed){timers.delete(id);timer.fn();}};
  const dismiss=()=>{const id=html.match(/data-desk-dismiss-feedback="(\d+)"/)?.[1];if(id)listeners.click({target:{closest:()=>({dataset:{deskDismissFeedback:id}})}});};
  return {el,nodes,storage,preferences,calls,click,submit,listeners,snapshot,quote,bodyClasses,document:sandbox.document,window:sandbox.window,focused:()=>focused,editing:value=>{editing=value;},api:sandbox.window.SpikePaperDesk,tick:()=>intervalCallback(),advance,dismiss,timers};
}

for(const empty of [false,true])test(`account creation cancels and stays closed after refresh (${empty?'no':'existing'} accounts)`,async()=>{
  const c=harness(()=>{throw new Error('unexpected POST');});if(empty)c.snapshot.accounts=[];
  c.api.setActive(true);await flush();
  assert.doesNotMatch(c.el.innerHTML,/data-desk-create class|<form data-desk-create/);
  if(empty)assert.match(c.el.innerHTML,/还没有模拟账户/);
  c.click('create-toggle');assert.equal(c.nodes['#desk-create-dialog'].open,true);
  assert.match(c.el.innerHTML,/type="button"[^>]*data-desk-create-cancel/);
  c.listeners.input({target:{name:'accountName',value:'Discard me'}});
  c.listeners.input({target:{name:'initialCash',value:'1000'}});
  c.click('create-cancel');
  assert.equal(c.nodes['#desk-create-dialog'],undefined);
  assert.equal(c.bodyClasses.has('desk-confirm-open'),false);
  assert.equal(c.focused(),'[data-desk-create-toggle]');
  c.click('refresh');await flush();c.tick();await flush();
  assert.equal(c.nodes['#desk-create-dialog'],undefined);
  c.click('create-toggle');
  assert.match(c.el.innerHTML,/name="accountName"[^>]*value=""/);
  assert.match(c.el.innerHTML,/name="initialCash"[^>]*value=""/);
  assert.equal(c.calls.filter(x=>x.method==='POST').length,0);
});

test('account dialog supports Escape, backdrop and repeated opening without treating inside clicks as cancel',async()=>{
  const c=harness(()=>{throw new Error('unexpected POST');});c.api.setActive(true);await flush();c.click('create-toggle');
  let dialog=c.nodes['#desk-create-dialog'];
  dialog.handlers.click({target:dialog,clientX:200,clientY:200});assert.equal(c.nodes['#desk-create-dialog'],dialog);
  dialog.handlers.click({target:{},clientX:50,clientY:50});assert.equal(c.nodes['#desk-create-dialog'],dialog);
  dialog.handlers.click({target:dialog,clientX:50,clientY:50});assert.equal(c.nodes['#desk-create-dialog'],undefined);
  c.click('create-toggle');dialog=c.nodes['#desk-create-dialog'];
  let prevented=false;dialog.handlers.cancel({preventDefault(){prevented=true;}});
  assert.equal(prevented,true);assert.equal(c.nodes['#desk-create-dialog'],undefined);
  c.click('create-toggle');c.click('create-toggle');assert.equal(c.nodes['#desk-create-dialog'],undefined);
  assert.equal(c.calls.filter(x=>x.method==='POST').length,0);
});

test('account draft survives polling and is discarded when leaving the page',async()=>{
  const c=harness(()=>{});c.api.setActive(true);await flush();c.click('create-toggle');
  const dialog=c.nodes['#desk-create-dialog'],count=c.calls.length;
  c.listeners.input({target:{name:'accountName',value:'Draft'}});c.tick();await flush();
  assert.equal(c.nodes['#desk-create-dialog'],dialog);assert.equal(c.calls.length,count);
  c.api.setActive(false);assert.equal(c.nodes['#desk-create-dialog'],undefined);assert.equal(c.bodyClasses.has('desk-confirm-open'),false);
  c.api.setActive(true);await flush();assert.equal(c.nodes['#desk-create-dialog'],undefined);
  c.click('create-toggle');assert.match(c.el.innerHTML,/name="accountName"[^>]*value=""/);
});

test('background response started before account dialog opens cannot replace it',async()=>{
  let release,hold=false;
  const c=harness(()=>{},async url=>{if(hold&&url.startsWith('/api/research/paper-desk/live'))await new Promise(resolve=>{release=resolve;});});
  c.api.setActive(true);await flush();hold=true;c.tick();await flush();
  assert.equal(typeof release,'function');
  c.click('create-toggle');const dialog=c.nodes['#desk-create-dialog'];
  c.listeners.input({target:{name:'accountName',value:'Keep typing'}});release();await flush();
  assert.equal(c.nodes['#desk-create-dialog'],dialog);
});

test('account submission cannot be dismissed while pending and closes on success',async()=>{
  let finish;
  const c=harness(()=>new Promise(resolve=>{finish=resolve;}));c.api.setActive(true);await flush();c.click('create-toggle');
  c.submit('create',{accountName:'New account',initialCash:'500'});
  const dialog=c.nodes['#desk-create-dialog'];c.click('create-cancel');dialog.handlers.cancel({preventDefault(){}});
  assert.equal(c.nodes['#desk-create-dialog'],dialog);
  assert.match(c.el.innerHTML,/创建中…/);
  finish({ok:true,json:async()=>({id:'account-1'})});await flush();
  assert.equal(c.nodes['#desk-create-dialog'],undefined);assert.equal(c.focused(),'[data-desk-create-toggle]');
  assert.equal(c.calls.filter(x=>x.method==='POST').length,1);
});

test('closing after an uncertain account result keeps its idempotent retry receipt',async()=>{
  const c=harness(async()=>{throw new Error('offline');});c.api.setActive(true);await flush();c.click('create-toggle');
  c.submit('create',{accountName:'Uncertain',initialCash:'500'});await flush();
  assert.match(c.el.innerHTML,/请关闭弹窗后重试原请求/);
  const receipt=[...c.storage.values()];c.click('create-cancel');
  assert.equal(c.nodes['#desk-create-dialog'],undefined);assert.deepEqual([...c.storage.values()],receipt);
  assert.match(c.el.innerHTML,/重试原请求/);assert.equal(c.calls.filter(x=>x.method==='POST').length,1);
});

test('entry stops default collapsed and keep values and expansion across form redraws',async()=>{
  const c=harness(()=>{throw new Error('unexpected POST');});c.api.setActive(true);await flush();
  assert.match(c.el.innerHTML,/<details class="desk-protection" data-desk-entry-stops >/);
  assert.doesNotMatch(c.el.innerHTML,/class="desk-segments desk-mode"/);
  assert.doesNotMatch(c.el.innerHTML,/>导出<|\/export"/);
  const label={textContent:''};
  const detail={open:true,hasAttribute:k=>k==='data-desk-entry-stops',querySelector:()=>label};
  c.listeners.toggle({target:detail});
  c.listeners.input({target:{name:'takeProfit',value:'120'}});
  c.listeners.input({target:{name:'stopLoss',value:'90'}});
  c.listeners.change({target:{name:'orderType',value:'limit',matches:()=>false}});
  assert.match(c.el.innerHTML,/data-desk-entry-stops open/);assert.match(c.el.innerHTML,/name="takeProfit"[^>]*value="120"/);
  detail.open=false;c.listeners.toggle({target:detail});assert.equal(label.textContent,'已设置');
  c.click('refresh');await flush();assert.match(c.el.innerHTML,/<details class="desk-protection" data-desk-entry-stops >/);
  assert.match(c.el.innerHTML,/name="stopLoss"[^>]*value="90"/);
});

test('closing starts at the held-position action and provides a return to entry',async()=>{
  const c=harness(()=>{});c.snapshot.positions=[{id:'p',account_id:'account-1',symbol:'BTC-USDT-SWAP',side:'long',leverage:100,remaining:'2',available_to_close:'2',entry_price:'100',mark:'101'}];
  c.api.setActive(true);await flush();
  c.listeners.click({target:{closest:()=>({dataset:{deskClose:'p'},hasAttribute:()=>false})}});
  assert.match(c.el.innerHTML,/返回开仓/);assert.match(c.el.innerHTML,/预计平仓 2 \/ 持仓 2/);
  c.click('trade-open');assert.doesNotMatch(c.el.innerHTML,/返回开仓/);assert.match(c.el.innerHTML,/止盈 \/ 止损/);
});

test('selecting an altcoin caps leverage and clears the prior contract limit price',async()=>{
  const c=harness(()=>{throw new Error('unexpected POST');});c.api.setActive(true);await flush();
  c.listeners.input({target:{name:'limitPrice',value:'80000'}});
  c.listeners.click({target:{closest:()=>({dataset:{deskSymbol:'SOL-USDT-SWAP'},hasAttribute:()=>false})}});
  const options=c.el.innerHTML.match(/<select name="leverage"[^>]*>(.*?)<\/select>/s)[1];
  assert.match(options,/<option value="20" selected>/);
  assert.doesNotMatch(options,/value="(?:30|50|100)"/);
  c.listeners.change({target:{name:'orderType',value:'limit',matches:()=>false}});
  assert.match(c.el.innerHTML,/name="limitPrice"[^>]*value=""/);
});

test('compact position cards retain exposure, entry, liquidation, fees and stops without changing original data',async()=>{
  const c=harness(()=>{});c.snapshot.positions=[{id:'p',account_id:'account-1',symbol:'BTC-USDT-SWAP',side:'long',leverage:100,
    remaining:'1',available_to_close:'1',notional_usdt:'80000.2856',available_usdt:'79999.6809',entry_price:'79000.31',mark:'80000.42',
    liquidation_price:'71000.51',mark_price:'80001.61',risk_complete:true,reserved_margin:'790.5153',fees:'39.5204',unrealized:'1000.1234',take_profit:'84788.12'}];
  const original=JSON.stringify(c.snapshot.positions);
  c.api.setActive(true);await flush();
  assert.match(c.el.innerHTML,/持仓 · USDT<\/dt><dd>80000<\/dd>/);
  assert.match(c.el.innerHTML,/手续费 · USDT<\/dt><dd>39\.5204<\/dd>/);
  assert.match(c.el.innerHTML,/预估爆仓价<\/dt><dd>71000\.51<\/dd>/);
  assert.match(c.el.innerHTML,/开仓均价<\/dt><dd>79000\.31<\/dd>/);
  const metrics=c.el.innerHTML.match(/<dl class="desk-position-metrics">(.*?)<\/dl>/s)[1];
  assert.doesNotMatch(metrics,/可平|标记价|当前价|保证金|80001\.61|80000\.42/);
  assert.match(c.el.innerHTML,/1000\.1234/);assert.match(c.el.innerHTML,/84788\.12/);
  assert.match(c.el.innerHTML,/data-desk-protection="p" >止盈止损<\/button>/);
  assert.doesNotMatch(c.el.innerHTML,/data-desk-add|修改止盈止损|设置止盈止损/);
  assert.match(c.el.innerHTML,/做多 · 仓位 80×/);
  assert.match(c.el.innerHTML,/<small>永续<\/small> <span class="desk-contract-leverage" title="合约杠杆">100×<\/span>/);
  c.tick();await flush();assert.equal(JSON.stringify(c.snapshot.positions),original);
});

async function submitEntry(c, leverage='1') {
  c.api.setActive(true); await flush();
  c.submit('trade',{symbol:'BTC-USDT-SWAP',side:'long',quantity:'1',note:'test',leverage});await flush();
  assert.equal(c.nodes['#desk-confirm-dialog'],undefined);
}

test('opening, editing and polling never creates a fill before explicit submission',async()=>{
  const c=harness(()=>{throw new Error('unexpected POST');});c.api.setActive(true);await flush();
  c.listeners.input({target:{name:'takeProfit',value:'110'}});
  c.listeners.input({target:{name:'leverage',value:'20'}});c.tick();await flush();
  assert.equal(c.calls.some(r=>r.method==='POST'),false);
  assert.doesNotMatch(c.el.innerHTML,/开仓前确认|data-desk-toggle-confirm|确认模拟成交/);
});

test('uncertain response retains the original request for an exact idempotent retry',async()=>{
  let attempt=0;
  const c=harness(async()=>{
    if(attempt++===0)throw new Error('response lost');
    return {ok:true,json:async()=>({fill:{action:'open',quantity:'1',price:'100',fee:'.1'}})};
  });
  await submitEntry(c,'20');await flush();
  assert.equal(c.storage.size,1);
  assert.match(c.el.innerHTML,/重试原请求/);
  c.click('retry');await flush();
  const posts=c.calls.filter(r=>r.method==='POST');
  assert.equal(posts.length,2);assert.equal(posts[0].body,posts[1].body);
  assert.equal(JSON.parse(posts[0].body).leverage,20);
  assert.equal(c.storage.size,0);
  assert.match(c.el.innerHTML,/已模拟开仓/);
});

test('direct submission freezes leverage and quantity from its own request',async()=>{
  let release;const gate=new Promise(resolve=>{release=resolve;});
  const c=harness(async()=>({ok:true,json:async()=>({fill:{action:'open',quantity:'1',price:'100',fee:'.1'}})}),url=>url.includes('/open-preview?')?gate:Promise.resolve());
  c.api.setActive(true);await flush();
  c.submit('trade',{symbol:'BTC-USDT-SWAP',leverage:'10'});
  c.listeners.input({target:{name:'leverage',value:'5'}});release();await flush();
  const order=JSON.parse(c.calls.find(r=>r.method==='POST').body);
  assert.equal(order.leverage,10);assert.equal(order.quantity,'1');
  c.submit('trade',{symbol:'BTC-USDT-SWAP',leverage:'5'});await flush();
  assert.equal(JSON.parse(c.calls.filter(r=>r.method==='POST')[1].body).leverage,5);
});

test('invalid leverage is rejected before requesting a quote',async()=>{
  const c=harness(()=>{throw new Error('unexpected POST');});
  c.api.setActive(true);await flush();
  c.submit('trade',{symbol:'BTC-USDT-SWAP',side:'long',quantity:'1',leverage:'2.5'});await flush();
  assert.match(c.el.innerHTML,/请选择有效的杠杆档位/);
  assert.equal(c.calls.some(r=>r.url.includes('/quote?')),false);
});

test('pending market refresh visibly disables actions instead of silently dropping clicks',async()=>{
  let release, paused=false;
  const gate=new Promise(resolve=>{release=resolve;});
  const c=harness(()=>{throw new Error('unexpected POST');}, async()=>{if(paused)await gate;});
  c.api.setActive(true);await flush();
  paused=true;
  const refreshing=c.api.refresh();
  assert.match(c.el.innerHTML,/data-desk-refresh disabled/);
  assert.match(c.el.innerHTML,/select name="leverage" disabled/);
  release();await refreshing;
  assert.doesNotMatch(c.el.innerHTML,/data-desk-refresh disabled/);
  assert.doesNotMatch(c.el.innerHTML,/select name="leverage" disabled/);
});

test('definitive rejection unlocks the form without an unresolved retry',async()=>{
  const c=harness(async()=>({ok:false,status:409,json:async()=>({detail:'模拟可用资金不足'})}));
  await submitEntry(c);await flush();
  assert.equal(c.storage.size,0);
  assert.match(c.el.innerHTML,/模拟可用资金不足/);
  assert.doesNotMatch(c.el.innerHTML,/重试原请求/);
  assert.doesNotMatch(c.el.innerHTML,/data-desk-confirm/);
});

test('errors use dismissible popups, escape text, and can appear again on a new attempt',async()=>{
  const c=harness(async()=>({ok:false,status:409,json:async()=>({detail:'模拟可用资金不足 <img src=x>'})}));
  await submitEntry(c);await flush();
  assert.match(c.el.innerHTML,/desk-toast-error" role="alert"/);
  assert.match(c.el.innerHTML,/模拟可用资金不足 &lt;img src=x&gt;/);
  assert.doesNotMatch(c.el.innerHTML,/role="alert" class="research-error"|<img src=x>/);
  assert.equal(c.nodes['[data-desk-feedback]'].open,true);
  c.advance(60000);assert.match(c.el.innerHTML,/模拟可用资金不足/);
  c.dismiss();assert.equal(c.nodes['[data-desk-feedback]'].open,false);
  c.click('flip');assert.doesNotMatch(c.el.innerHTML,/模拟可用资金不足/);
  c.submit('trade',{symbol:'BTC-USDT-SWAP',leverage:'1'});await flush();
  assert.match(c.el.innerHTML,/模拟可用资金不足/);
});

test('success expires after five seconds across renders without redrawing the active form',async()=>{
  const c=harness(async()=>({ok:true,json:async()=>({fill:{action:'open',quantity:'1',price:'100',fee:'.1'}})}));
  await submitEntry(c);await flush();
  assert.match(c.el.innerHTML,/desk-toast-success" role="status"/);
  c.advance(3000);c.click('flip');
  const host=c.nodes['[data-desk-feedback]'];
  c.editing(true);c.advance(1999);assert.match(c.el.innerHTML,/已模拟开仓/);
  c.advance(1);assert.doesNotMatch(c.el.innerHTML,/已模拟开仓/);
  assert.equal(c.nodes['[data-desk-feedback]'],host);
  assert.equal(host.open,false);
  c.click('flip');assert.doesNotMatch(c.el.innerHTML,/已模拟开仓/);
});

test('account errors stay inside the modal top layer and dismissal preserves the draft',async()=>{
  const c=harness(async()=>({ok:false,status:409,json:async()=>({detail:'账户名称已存在'})}));
  c.api.setActive(true);await flush();c.click('create-toggle');
  c.listeners.input({target:{name:'accountName',value:'Keep this'}});
  c.submit('create',{accountName:'Keep this',initialCash:'1000'});await flush();
  const dialog=c.nodes['#desk-create-dialog'];
  assert.match(c.el.innerHTML,/<dialog id="desk-create-dialog"[\s\S]*<div class="desk-feedback"[\s\S]*账户名称已存在[\s\S]*<\/dialog>/);
  assert.equal((c.el.innerHTML.match(/账户名称已存在/g)||[]).length,1);
  c.dismiss();assert.equal(c.nodes['#desk-create-dialog'],dialog);
  assert.match(c.el.innerHTML,/name="accountName"[^>]*value="Keep this"/);
});

test('closing an uncertain-result popup keeps the durable retry request',async()=>{
  const c=harness(async()=>{throw new Error('连接中断');});
  await submitEntry(c);await flush();
  const receipt=[...c.storage.values()];
  c.dismiss();assert.doesNotMatch(c.el.innerHTML,/连接中断/);
  assert.deepEqual([...c.storage.values()],receipt);
  assert.match(c.el.innerHTML,/请求结果待核对/);
  assert.equal(c.calls.filter(x=>x.method==='POST').length,1);
});

test('background errors do not reappear every poll after dismissal; recovery resets deduplication',async()=>{
  let offline=false;
  const c=harness(()=>{},async url=>{if(offline&&url.includes('/paper-desk/live'))throw new Error('行情连接中断');});
  c.api.setActive(true);await flush();c.editing(true);offline=true;
  c.tick();await flush();assert.match(c.el.innerHTML,/行情连接中断/);
  c.dismiss();c.tick();await flush();assert.doesNotMatch(c.el.innerHTML,/行情连接中断/);
  offline=false;c.tick();await flush();offline=true;c.tick();await flush();
  assert.match(c.el.innerHTML,/行情连接中断/);
});

test('committed success remains distinct from a subsequent account refresh failure',async()=>{
  let committed=false;
  const c=harness(async()=>{committed=true;return {ok:true,json:async()=>({fill:{action:'open',quantity:'1',price:'100',fee:'.1'}})};},async url=>{if(committed&&url.includes('/paper-desk/live'))throw new Error('offline');});
  await submitEntry(c);await flush();
  assert.match(c.el.innerHTML,/desk-toast-success[\s\S]*已模拟开仓/);
  assert.match(c.el.innerHTML,/desk-toast-error[\s\S]*操作已记账，账户刷新失败/);
  assert.equal(c.storage.size,0);assert.doesNotMatch(c.el.innerHTML,/重试原请求/);
  c.advance(5000);assert.doesNotMatch(c.el.innerHTML,/已模拟开仓/);
  assert.match(c.el.innerHTML,/操作已记账，账户刷新失败/);
});

test('leaving the page clears popup timers and does not replay feedback on return',async()=>{
  const c=harness(async()=>({ok:true,json:async()=>({fill:{action:'open',quantity:'1',price:'100',fee:'.1'}})}));
  await submitEntry(c);await flush();assert.equal(c.timers.size,1);
  c.api.setActive(false);assert.equal(c.timers.size,0);
  c.api.setActive(true);await flush();assert.doesNotMatch(c.el.innerHTML,/已模拟开仓/);
});

test('search filters case and separators; Enter selects an actual contract without submitting until the form is sent',async()=>{
  const c=harness(async()=>({ok:true,json:async()=>({fill:{action:'open',quantity:'1',price:'100',fee:'.1'}})}));
  c.api.setActive(true);await flush();
  c.click('symbol-toggle');
  assert.match(c.el.innerHTML,/搜索币种/);
  c.listeners.input({target:{id:'desk-symbol-search',value:'eth/usdt'}});
  assert.match(c.nodes['#desk-symbol-results'].innerHTML,/ETH-USDT-SWAP/);
  assert.doesNotMatch(c.nodes['#desk-symbol-results'].innerHTML,/BTC-USDT-SWAP/);
  c.listeners.keydown({target:{id:'desk-symbol-search'},key:'Enter',preventDefault(){}});
  assert.match(c.el.innerHTML,/name="symbol" value="ETH-USDT-SWAP"/);
  await flush();
  assert.equal(c.calls.some(r=>r.method==='POST'),false);
  c.submit('trade',{symbol:'ETH-USDT-SWAP',side:'long',quantity:'1',leverage:'10'});await flush();
  assert.ok(c.calls.some(r=>r.url.includes('/open-preview?')&&r.url.includes('symbol=ETH-USDT-SWAP')));
});

for(const query of ['wusdt','W/USDT','W-USDT-SWAP'])test(`single-character contract search accepts ${query} without submitting`,async()=>{
  const c=harness(()=>{throw new Error('unexpected POST');});
  c.api.setActive(true);await flush();c.click('symbol-toggle');
  c.listeners.input({target:{id:'desk-symbol-search',value:query}});
  assert.match(c.nodes['#desk-symbol-results'].innerHTML,/data-desk-symbol="W-USDT-SWAP"/);
  assert.equal(c.nodes['#desk-symbol-count'].textContent,'1 个合约 · 按 24h 成交额排序');
  c.listeners.keydown({target:{id:'desk-symbol-search'},key:'Enter',preventDefault(){}});
  assert.match(c.el.innerHTML,/name="symbol" value="W-USDT-SWAP"/);
  assert.match(c.el.innerHTML,/<option value="20" selected>/);
  assert.equal(c.calls.some(r=>r.method==='POST'),false);
});

test('unmatched search cannot select or submit an invented contract',async()=>{
  const c=harness(()=>{throw new Error('unexpected POST');});
  c.api.setActive(true);await flush();
  c.click('symbol-toggle');
  c.listeners.input({target:{id:'desk-symbol-search',value:'notarealcoin'}});
  assert.match(c.nodes['#desk-symbol-results'].innerHTML,/没有匹配的币种/);
  c.listeners.keydown({target:{id:'desk-symbol-search'},key:'Enter',preventDefault(){}});
  c.submit('trade',{symbol:'NOTREAL-USDT-SWAP',side:'long',quantity:'1',leverage:'1'});await flush();
  assert.match(c.el.innerHTML,/请从下拉列表选择币种/);
  assert.equal(c.calls.some(r=>r.url.includes('/quote?')),false);
});

test('catalog failure is visible and can be retried without creating orders',async()=>{
  let offline=true;
  const c=harness(()=>{throw new Error('unexpected POST');},async url=>{if(url.endsWith('/symbols')&&offline)throw new Error('OKX 币种列表暂不可用');});
  c.api.setActive(true);await flush();
  assert.match(c.el.innerHTML,/OKX 币种列表暂不可用/);
  assert.match(c.el.innerHTML,/重试加载币种/);
  offline=false;c.click('symbol-retry');await flush();
  assert.doesNotMatch(c.el.innerHTML,/重试加载币种/);
  assert.match(c.el.innerHTML,/name="symbol" value="BTC-USDT-SWAP"/);
  assert.equal(c.calls.some(r=>r.method==='POST'),false);
});

test('exact currency matches precede substring matches when confirming with Enter',async()=>{
  const c=harness(()=>{throw new Error('unexpected POST');});
  c.api.setActive(true);await flush();c.click('symbol-toggle');
  c.listeners.input({target:{id:'desk-symbol-search',value:'sol'}});
  c.listeners.keydown({target:{id:'desk-symbol-search'},key:'Enter',preventDefault(){}});
  assert.match(c.el.innerHTML,/name="symbol" value="SOL-USDT-SWAP"/);
});

test('compact entry keeps side and swap icon without the direction hint',async()=>{
 const c=harness(async()=>({ok:true,json:async()=>({fill:{action:'open',quantity:'1',price:'100',fee:'.1'}})}));
 c.api.setActive(true);await flush();
 assert.doesNotMatch(c.el.innerHTML,/name="(?:quantity|note|side)"|切换方向/);
 assert.match(c.el.innerHTML,/data-desk-flip aria-label="切换为做空"/);
 assert.match(c.el.innerHTML,/desk-swap-icon/);
 const select=c.el.innerHTML.match(/<select name="leverage"[^>]*>(.*?)<\/select>/)[1];
 assert.deepEqual([...select.matchAll(/value="(\d+)"/g)].map(m=>Number(m[1])),[1,2,5,10,15,20,30,50,100]);
 c.click('flip');assert.equal(c.calls.some(r=>r.method==='POST'),false);
 assert.match(c.el.innerHTML,/切换为做多/);
 c.submit('trade',{symbol:'BTC-USDT-SWAP',leverage:'50'});await flush();
 const payload=JSON.parse(c.calls.find(r=>r.method==='POST').body);
 assert.equal(payload.side,'short');assert.equal(payload.quantity,'1');assert.equal(payload.note,'');
});
test('limit entry submits quoted quantity and limit price directly to the pending-order route',async()=>{
 const c=harness(async()=>({ok:true,json:async()=>({order:{symbol:'BTC-USDT-SWAP',quantity:'1',limit_price:'90',status:'pending'}})}));
 c.api.setActive(true);await flush();
 c.submit('trade',{symbol:'BTC-USDT-SWAP',leverage:'20',orderType:'limit',limitPrice:'90'});await flush();
 const post=c.calls.find(r=>r.method==='POST');assert.match(post.url,/limit-open\?include_snapshot=true$/);
 const payload=JSON.parse(post.body);assert.equal(payload.limit_price,'90');assert.equal(payload.quantity,'1');
 assert.equal(c.nodes['#desk-confirm-dialog'],undefined);assert.match(c.el.innerHTML,/限价委托已提交/);
});

test('background refresh cannot unlock a pending direct order',async()=>{
 let release,finish,paused=false;
 const gate=new Promise(resolve=>{release=resolve;}),postGate=new Promise(resolve=>{finish=resolve;});
 const c=harness(async()=>{await postGate;return {ok:true,json:async()=>({fill:{action:'open',quantity:'1',price:'100',fee:'.1'}})};},async url=>{if(paused&&url.includes('/paper-desk/live'))await gate;});
 c.api.setActive(true);await flush();paused=true;c.tick();
 assert.doesNotMatch(c.el.innerHTML,/select name="leverage" disabled/);
 c.submit('trade',{symbol:'BTC-USDT-SWAP',leverage:'10'});await flush();release();await flush();
 assert.match(c.el.innerHTML,/select name="leverage" disabled/);
 c.submit('trade',{symbol:'BTC-USDT-SWAP',leverage:'20'});assert.equal(c.calls.filter(r=>r.method==='POST').length,1);
 finish();await flush();assert.match(c.el.innerHTML,/已模拟开仓/);
});

for(const orderType of ['market','limit'])test(`optional protection prices survive ${orderType} direct submission`,async()=>{
 const c=harness(async()=>({ok:true,json:async()=>({fill:{action:'open',quantity:'1',price:'100',fee:'.1'}})}));
 c.api.setActive(true);await flush();
 c.submit('trade',{symbol:'BTC-USDT-SWAP',leverage:'10',orderType,limitPrice:'95',takeProfit:'110',stopLoss:'90'});await flush();
 assert.ok(c.calls.some(r=>r.url.includes('take_profit=110&stop_loss=90')));
 assert.equal(c.nodes['#desk-confirm-dialog'],undefined);
 const post=c.calls.find(r=>r.method==='POST');const payload=JSON.parse(post.body);
 assert.equal(payload.take_profit,'110');assert.equal(payload.stop_loss,'90');
 assert.ok(post.url.split('?')[0].endsWith(orderType==='limit'?'/limit-open':'/open'));
});
test('editing protection is inert and flipping direction clears both draft prices',async()=>{
 const c=harness(()=>{throw new Error('unexpected POST');});c.api.setActive(true);await flush();
 c.listeners.input({target:{name:'takeProfit',value:'110'}});
 c.listeners.input({target:{name:'stopLoss',value:'95'}});c.click('flip');
 assert.equal(c.calls.some(r=>r.method==='POST'),false);
 assert.match(c.el.innerHTML,/name="takeProfit" type="number" value=""/);
 assert.match(c.el.innerHTML,/name="stopLoss" type="number" value=""/);
 assert.match(c.el.innerHTML,/切换为做多/);
});
test('nonfinite protection prices fail before quote retrieval',async()=>{
 const c=harness(()=>{throw new Error('unexpected POST');});
 c.api.setActive(true);await flush();
 c.submit('trade',{symbol:'BTC-USDT-SWAP',leverage:'1',stopLoss:'Infinity'});await flush();
 assert.match(c.el.innerHTML,/止盈止损价格必须是有效正数/);
 assert.equal(c.calls.some(r=>r.url.includes('/open-preview?')),false);
});
test('fill history distinguishes automatic take-profit and stop-loss exits',async()=>{
 const c=harness(()=>{throw new Error('unexpected POST');});
 c.snapshot.fills=['take_profit','stop_loss'].map(exit_reason=>({account_id:'account-1',action:'close',symbol:'BTC-USDT-SWAP',side:'long',leverage:10,quantity:'1',price:'100',fee:'.1',gross_pnl:'0',exit_reason}));
 c.api.setActive(true);await flush();
 c.listeners.click({target:{closest:()=>({dataset:{deskTab:'fills'},hasAttribute:()=>false})}});
 assert.match(c.el.innerHTML,/止盈自动平仓/);assert.match(c.el.innerHTML,/止损自动平仓/);
});

test('direct entry works from every ledger tab without opening a modal',async()=>{
 for(const tab of ['positions','orders','closed','fills']){
  const c=harness(async()=>({ok:true,json:async()=>({fill:{action:'open',quantity:'1',price:'100',fee:'.1'}})}));
  c.api.setActive(true);await flush();dataClick(c,{deskTab:tab});
  c.submit('trade',{symbol:'BTC-USDT-SWAP',leverage:'10'});await flush();
  assert.equal(c.calls.filter(r=>r.method==='POST').length,1);
  assert.equal(c.nodes['#desk-confirm-dialog'],undefined);assert.equal(c.bodyClasses.has('desk-confirm-open'),false);
 }
});
test('account dialog still traps Tab and Shift-Tab after order confirmation is removed',async()=>{
 const c=harness(()=>{throw new Error('unexpected POST');});c.api.setActive(true);await flush();c.click('create-toggle');
 const dialog=c.nodes['#desk-create-dialog'];let prevented=0;
 const first={focus(){c.document.activeElement=first;}},last={focus(){c.document.activeElement=last;}};
 dialog.querySelectorAll=()=>[first,last];c.document.activeElement=last;
 dialog.handlers.keydown({key:'Tab',preventDefault(){prevented++;}});assert.equal(c.document.activeElement,first);
 dialog.handlers.keydown({key:'Tab',shiftKey:true,preventDefault(){prevented++;}});assert.equal(c.document.activeElement,last);
 assert.equal(prevented,2);assert.equal(c.calls.some(r=>r.method==='POST'),false);
});

test('direct entry displays returned fees without injecting client fee overrides',async()=>{
  const c=harness(async()=>({ok:true,json:async()=>({fill:{action:'open',quantity:'1',price:'100',fee:'.023'}})}));
  c.snapshot.fee_rate='0.0002';c.quote.fee='0.023';await submitEntry(c);
  assert.match(c.el.innerHTML,/保证金 \+ Taker 费<\/dt><dd>100 \+ 2 USDT/);assert.match(c.el.innerHTML,/手续费 0.023 USDT/);
  const payload=JSON.parse(c.calls.find(r=>r.method==='POST').body);
  assert.equal(Object.hasOwn(payload,'fee_rate'),false);assert.equal(Object.hasOwn(payload,'fee'),false);
});

for(const orderType of ['market','limit'])test(`${orderType} direct short close submits the percentage quantity without client fee overrides`,async()=>{
  const c=harness(async()=>({ok:true,json:async()=>({fill:{action:'close',quantity:'0.5',price:'100',fee:'.05'}})}));
  c.snapshot.positions=[{id:'position-1',account_id:'account-1',symbol:'BTC-USDT-SWAP',side:'short',leverage:20,remaining:'2',available_to_close:'2',entry_price:'120',reserved_margin:'12',fees:'.24',mark:'100',unrealized:'40'}];
  c.api.setActive(true);await flush();c.click('trade-close');
  assert.match(c.el.innerHTML,/class="desk-contract-leverage" title="合约杠杆">20×<\/span>/);
  c.submit('trade',{closePercent:'25',orderType,limitPrice:'90'});await flush();
  assert.equal(c.nodes['#desk-confirm-dialog'],undefined);
  const post=c.calls.find(r=>r.method==='POST');
  assert.ok(post.url.split('?')[0].endsWith(`/positions/position-1/${orderType==='limit'?'limit-close':'close'}`));
  assert.deepEqual(JSON.parse(post.body),{quantity:'0.5',quote_id:c.quote.id,request_id:'request-uuid-12345678',...(orderType==='limit'?{limit_price:'90'}:{})});
});

for(const preference of [undefined,'on','off','unreadable'])test(`entry is direct with legacy confirmation preference ${preference}`,async()=>{
  const preferences=new Map(preference?[['spike-paper-desk-confirm-entry-v1',preference]]:[]);
  if(preference==='unreadable')preferences.get=()=>{throw new Error('storage unavailable');};
  const c=harness(async()=>({ok:true,json:async()=>({fill:{action:'open',quantity:'1',price:'100',fee:'.1'}})}),undefined,preferences);
  c.api.setActive(true);await flush();
  assert.doesNotMatch(c.el.innerHTML,/开仓前确认|data-desk-toggle-confirm/);
  assert.equal(c.calls.some(r=>r.method==='POST'),false);
  c.submit('trade',{symbol:'BTC-USDT-SWAP',leverage:'10'});await flush();
  assert.equal(c.calls.filter(r=>r.method==='POST').length,1);assert.equal(c.nodes['#desk-confirm-dialog'],undefined);
});

for(const orderType of ['market','limit'])test(`direct ${orderType} entry uses a fresh quote and the same frozen order without showing a modal`,async()=>{
  let release;const gate=new Promise(resolve=>{release=resolve;});
  const c=harness(async()=>{await gate;return {ok:true,json:async()=>({fill:{action:'open',quantity:'1',price:'100',fee:'.1'}})};});
  c.api.setActive(true);await flush();c.click('flip');
  c.submit('trade',{symbol:'BTC-USDT-SWAP',leverage:'20',orderType,limitPrice:'95',takeProfit:'90',stopLoss:'110'});await flush();
  const quoteCall=c.calls.findIndex(r=>r.url.includes('/open-preview?'));
  const postIndex=c.calls.findIndex(r=>r.method==='POST');
  assert.ok(postIndex>quoteCall&&quoteCall>=0);
  assert.ok(c.calls[postIndex].url.split('?')[0].endsWith(orderType==='limit'?'/limit-open':'/open'));
  const payload=JSON.parse(c.calls[postIndex].body);
  assert.equal(payload.quantity,'1');assert.equal(payload.side,'short');assert.equal(payload.leverage,20);
  assert.equal(payload.take_profit,'90');assert.equal(payload.stop_loss,'110');assert.equal(payload.quote_id,c.quote.id);
  assert.equal(c.nodes['#desk-confirm-dialog'],undefined);
  assert.equal(c.bodyClasses.has('desk-confirm-open'),false);
  assert.match(c.el.innerHTML,/select name="leverage" disabled/);
  c.submit('trade',{symbol:'BTC-USDT-SWAP',leverage:'20'});
  assert.equal(c.calls.filter(r=>r.method==='POST').length,1);
  release();await flush();
  assert.match(c.el.innerHTML,/已模拟开仓/);
  assert.equal(c.nodes['#desk-confirm-dialog'],undefined);
});

test('direct entry suppresses duplicate clicks during quote retrieval and cancels after leaving and returning',async()=>{
  let release;const gate=new Promise(resolve=>{release=resolve;});
  const c=harness(()=>{throw new Error('unexpected POST');},url=>url.includes('/open-preview?')?gate:Promise.resolve());
  c.api.setActive(true);await flush();
  c.submit('trade',{symbol:'BTC-USDT-SWAP',leverage:'10'});
  c.submit('trade',{symbol:'BTC-USDT-SWAP',leverage:'10'});
  assert.equal(c.calls.filter(r=>r.url.includes('/open-preview?')).length,1);
  c.api.setActive(false);c.api.setActive(true);release();await flush();
  assert.equal(c.calls.some(r=>r.method==='POST'),false);
  assert.equal(c.nodes['#desk-confirm-dialog'],undefined);
  assert.doesNotMatch(c.el.innerHTML,/select name="leverage" disabled/);
});

for(const failure of ['expired','unavailable'])test(`direct entry never posts with an ${failure} quote`,async()=>{
  const c=harness(()=>{throw new Error('unexpected POST');},async url=>{if(failure==='unavailable'&&url.includes('/open-preview?'))throw new Error('行情不可用');});
  if(failure==='expired')c.quote.expires_ms=Date.now()-1;
  c.api.setActive(true);await flush();
  c.submit('trade',{symbol:'BTC-USDT-SWAP',leverage:'10'});await flush();
  assert.equal(c.calls.some(r=>r.method==='POST'),false);
  assert.equal(c.nodes['#desk-confirm-dialog'],undefined);
  assert.match(c.el.innerHTML,failure==='expired'?/报价已过期/:/行情不可用/);
  assert.doesNotMatch(c.el.innerHTML,/select name="leverage" disabled/);
});

test('uncertain direct entry retains its original request and prevents a second new order',async()=>{
  let attempt=0;
  const c=harness(async()=>{if(attempt++===0)throw new Error('response lost');return {ok:true,json:async()=>({fill:{action:'open',quantity:'1',price:'100',fee:'.1'}})};});
  c.api.setActive(true);await flush();
  c.submit('trade',{symbol:'BTC-USDT-SWAP',leverage:'10'});await flush();
  assert.match(c.el.innerHTML,/重试原请求/);assert.equal(c.nodes['#desk-confirm-dialog'],undefined);
  c.submit('trade',{symbol:'BTC-USDT-SWAP',leverage:'10'});await flush();
  assert.equal(c.calls.filter(r=>r.method==='POST').length,1);
  c.click('retry');await flush();
  const posts=c.calls.filter(r=>r.method==='POST');
  assert.equal(posts.length,2);assert.equal(posts[0].body,posts[1].body);
  assert.equal(c.storage.size,0);
});

for(const orderType of ['market','limit'])for(const confirmEntry of [true,false])test(`direct ${orderType} close skips the modal with entry confirmation ${confirmEntry?'on':'off'}`,async()=>{
  let release;const gate=new Promise(resolve=>{release=resolve;});
  const c=harness(async()=>{await gate;return {ok:true,json:async()=>orderType==='limit'
    ?{order:{action:'close',status:'pending',symbol:'BTC-USDT-SWAP',side:'long',limit_price:'120'}}
    :{fill:{action:'close',quantity:'2',price:'100',fee:'.1'}}};});
  c.preferences.set('spike-paper-desk-confirm-entry-v1',confirmEntry?'on':'off');
  c.snapshot.positions=[heldPosition()];c.api.setActive(true);await flush();
  dataClick(c,{deskClose:'position-1'});
  c.submit('trade',{closePercent:'100',orderType,limitPrice:'120'});await flush();
  const quoteIndex=c.calls.findIndex(r=>r.url.includes('/close-preview?percentage=100'));
  const postIndex=c.calls.findIndex(r=>r.method==='POST');assert.ok(quoteIndex>=0&&postIndex>quoteIndex);
  const post=c.calls[postIndex];assert.ok(post.url.split('?')[0].endsWith(`/positions/position-1/${orderType==='limit'?'limit-close':'close'}`));
  assert.deepEqual(JSON.parse(post.body),{quantity:'2',quote_id:c.quote.id,request_id:'request-uuid-12345678',...(orderType==='limit'?{limit_price:'120'}:{})});
  assert.equal(c.nodes['#desk-confirm-dialog'],undefined);assert.equal(c.bodyClasses.has('desk-confirm-open'),false);
  c.submit('trade',{closePercent:'100',orderType});await flush();
  assert.equal(c.calls.filter(r=>r.method==='POST').length,1);
  release();await flush();assert.match(c.el.innerHTML,orderType==='limit'?/限价委托已提交/:/已模拟平仓/);
});

test('direct close prevents duplicate quote requests and discards quotes after leaving the page',async()=>{
  let release;const gate=new Promise(resolve=>{release=resolve;});
  const c=harness(()=>{throw new Error('unexpected POST');},url=>url.includes('/close-preview?')?gate:Promise.resolve());
  c.snapshot.positions=[heldPosition()];c.api.setActive(true);await flush();dataClick(c,{deskClose:'position-1'});
  c.submit('trade',{closePercent:'50'});c.submit('trade',{closePercent:'50'});
  assert.equal(c.calls.filter(r=>r.url.includes('/close-preview?')).length,1);
  c.api.setActive(false);c.api.setActive(true);release();await flush();
  assert.equal(c.calls.some(r=>r.method==='POST'),false);assert.equal(c.nodes['#desk-confirm-dialog'],undefined);
});

for(const failure of ['expired','unavailable'])test(`direct close never posts with an ${failure} quote`,async()=>{
  const c=harness(()=>{throw new Error('unexpected POST');},async url=>{if(failure==='unavailable'&&url.includes('/close-preview?'))throw new Error('行情不可用');});
  if(failure==='expired')c.quote.expires_ms=Date.now()-1;
  c.snapshot.positions=[heldPosition()];c.api.setActive(true);await flush();dataClick(c,{deskClose:'position-1'});
  c.submit('trade',{closePercent:'25'});await flush();
  assert.equal(c.calls.some(r=>r.method==='POST'),false);assert.equal(c.nodes['#desk-confirm-dialog'],undefined);
  assert.match(c.el.innerHTML,failure==='expired'?/报价已过期/:/行情不可用/);
});

test('uncertain direct close retries the same quantity even when the remaining position changes',async()=>{
  let attempt=0;
  const c=harness(async()=>{if(attempt++===0)throw new Error('response lost');return {ok:true,json:async()=>({fill:{action:'close',quantity:'1',price:'100',fee:'.05'}})};});
  c.snapshot.positions=[heldPosition()];c.api.setActive(true);await flush();dataClick(c,{deskClose:'position-1'});
  c.submit('trade',{closePercent:'50'});await flush();
  assert.match(c.el.innerHTML,/重试原请求/);assert.equal(c.nodes['#desk-confirm-dialog'],undefined);
  c.snapshot.positions[0].remaining='1';c.submit('trade',{closePercent:'100'});await flush();
  assert.equal(c.calls.filter(r=>r.method==='POST').length,1);
  c.click('retry');await flush();const posts=c.calls.filter(r=>r.method==='POST');
  assert.equal(posts.length,2);assert.equal(posts[0].body,posts[1].body);assert.equal(JSON.parse(posts[1].body).quantity,'1');
  assert.equal(c.storage.size,0);
});

const inputEstimate=(c,name,value)=>c.listeners.input({target:{name,value}});
const estimateText=(c,name)=>c.nodes[`[data-desk-estimate="${name}"]`]?.innerHTML||'';

test('limit protection updates with equity-sized exposure and both fees without trading',async()=>{
  const c=harness(()=>{throw new Error('unexpected POST');});c.api.setActive(true);await flush();
  inputEstimate(c,'orderType','limit');inputEstimate(c,'limitPrice','80960');
  inputEstimate(c,'takeProfit','81964');inputEstimate(c,'stopLoss','80678');
  assert.match(estimateText(c,'takeProfit'),/预计净赚.*\+103\.89/);
  assert.match(estimateText(c,'stopLoss'),/预计净亏.*−54\.80/);
  inputEstimate(c,'positionMultiple','20');
  assert.match(estimateText(c,'takeProfit'),/\+207\.78/);
  inputEstimate(c,'limitPrice','81000');
  assert.doesNotMatch(estimateText(c,'takeProfit'),/207\.78/);
  assert.equal(c.calls.some(r=>r.method==='POST'||r.url.includes('/open-preview?')),false);
});

test('market estimates use ask for long and bid for short, with immediate updates after quoting',async()=>{
  const c=harness(()=>{throw new Error('unexpected POST');});c.api.setActive(true);await flush();
  inputEstimate(c,'takeProfit','110');inputEstimate(c,'stopLoss','90');await flush();
  assert.match(estimateText(c,'takeProfit'),/\+979\.00/);
  assert.match(estimateText(c,'stopLoss'),/−1019\.00/);
  c.click('flip');inputEstimate(c,'takeProfit','90');inputEstimate(c,'stopLoss','110');await flush();
  assert.match(estimateText(c,'takeProfit'),/\+890\.00/);
  assert.match(estimateText(c,'stopLoss'),/−1132\.22/);
  assert.equal(c.calls.filter(r=>r.url.includes('/estimate?')).length,1);
  assert.equal(c.calls.some(r=>r.method==='POST'),false);
});

test('a valid but very close take-profit honestly shows a net loss after fees',async()=>{
  const c=harness(()=>{});c.api.setActive(true);await flush();
  inputEstimate(c,'orderType','limit');inputEstimate(c,'limitPrice','100');inputEstimate(c,'takeProfit','100.1');
  assert.match(estimateText(c,'takeProfit'),/预计净亏.*−10\.01/);
  c.snapshot.fee_rate='0.0002';await c.api.refresh();
  assert.match(estimateText(c,'takeProfit'),/预计净赚.*\+6\.00/);
});

test('invalid prices, wrong stop direction, absent funds and missing rates never show a misleading amount',async()=>{
  const c=harness(()=>{});c.api.setActive(true);await flush();inputEstimate(c,'orderType','limit');
  inputEstimate(c,'takeProfit','110');assert.match(estimateText(c,'takeProfit'),/先填写有效委托价/);
  inputEstimate(c,'limitPrice','100');inputEstimate(c,'takeProfit','90');inputEstimate(c,'stopLoss','110');
  assert.match(estimateText(c,'takeProfit'),/须高于开仓价 100/);assert.match(estimateText(c,'stopLoss'),/须低于开仓价 100/);
  inputEstimate(c,'takeProfit','Infinity');assert.match(estimateText(c,'takeProfit'),/请输入有效价格/);
  inputEstimate(c,'takeProfit','110');c.snapshot.accounts[0].available='0';await c.api.refresh();
  assert.match(estimateText(c,'takeProfit'),/暂无可用资金/);
  c.snapshot.accounts[0].available=null;await c.api.refresh();assert.match(estimateText(c,'takeProfit'),/可用资金暂不可用/);
  c.snapshot.accounts[0].available='1000';c.snapshot.fee_rate=null;await c.api.refresh();assert.match(estimateText(c,'takeProfit'),/等待有效费率/);
  inputEstimate(c,'takeProfit','');assert.match(estimateText(c,'takeProfit'),/输入价格后计算/);
});

test('background balance refresh recalculates estimates without replacing the focused input',async()=>{
  const c=harness(()=>{});c.api.setActive(true);await flush();inputEstimate(c,'orderType','limit');
  inputEstimate(c,'limitPrice','100');inputEstimate(c,'takeProfit','110');
  c.editing(true);const html=c.el.innerHTML, node=c.nodes['[data-desk-estimate="takeProfit"]'];
  c.snapshot.accounts[0].available='500';c.tick();await flush();
  assert.equal(c.el.innerHTML,html);assert.equal(c.nodes['[data-desk-estimate="takeProfit"]'],node);
  assert.match(estimateText(c,'takeProfit'),/\+979\.00/);
  c.snapshot.accounts[0].equity='500';c.tick();await flush();
  assert.match(estimateText(c,'takeProfit'),/\+489\.50/);
  c.snapshot.now_ms=Date.now()-16000;c.tick();await flush();assert.match(estimateText(c,'takeProfit'),/资金待刷新/);
});

test('quote expiry or network failure hides the estimate; returning to the page can recover',async()=>{
  let offline=false;
  const c=harness(()=>{},async url=>{if(offline&&url.includes('/estimate?'))throw new Error('offline');});
  c.api.setActive(true);await flush();inputEstimate(c,'takeProfit','110');await flush();
  assert.match(estimateText(c,'takeProfit'),/预计净赚/);
  c.quote.expires_ms=Date.now()-1;c.api.setActive(false);c.api.setActive(true);await flush();
  assert.doesNotMatch(estimateText(c,'takeProfit'),/USDT/);
  offline=true;c.api.setActive(false);c.api.setActive(true);await flush();
  assert.match(estimateText(c,'takeProfit'),/行情不可用/);
  offline=false;c.quote.expires_ms=Date.now()+15000;c.api.setActive(false);c.api.setActive(true);await flush();
  assert.match(estimateText(c,'takeProfit'),/预计净赚/);
});

test('late estimates cannot apply another symbol or trigger direct entry',async()=>{
  let release;const gate=new Promise(resolve=>{release=resolve;});
  const c=harness(()=>{throw new Error('unexpected POST');},url=>url.includes('/estimate?')?gate:Promise.resolve());
  c.api.setActive(true);await flush();inputEstimate(c,'takeProfit','110');
  c.click('symbol-toggle');c.listeners.input({target:{id:'desk-symbol-search',value:'ETH'}});
  c.listeners.keydown({target:{id:'desk-symbol-search'},key:'Enter',preventDefault(){}});
  release();await flush();
  assert.match(estimateText(c,'takeProfit'),/输入价格后计算/);
  inputEstimate(c,'takeProfit','110');await flush();
  assert.doesNotMatch(estimateText(c,'takeProfit'),/USDT/);
  assert.equal(c.calls.some(r=>r.method==='POST'),false);
  assert.equal(c.nodes['#desk-confirm-dialog'],undefined);
});

test('default exposure is equity times ten with one-hundred-times contract leverage',async()=>{
  const c=harness(async()=>({ok:true,json:async()=>({fill:{action:'open',quantity:'1',price:'100',fee:'.1'}})}));c.api.setActive(true);await flush();
  assert.match(c.el.innerHTML,/<select name="leverage"[^>]*>.*?<option value="100" selected>100×/);
  assert.match(c.el.innerHTML,/<select name="positionMultiple"[^>]*>.*?<option value="10" selected>10×/);
  assert.match(c.el.innerHTML,/仓位金额<\/dt><dd>10000 USDT/);
  assert.match(c.el.innerHTML,/保证金 \+ Taker 费<\/dt><dd>100 \+ 10 USDT/);
  assert.match(c.el.innerHTML,/下单后可用<\/dt><dd class="">890 USDT/);
  c.submit('trade',{symbol:'BTC-USDT-SWAP'});await flush();
  assert.ok(c.calls.some(r=>r.url.includes('leverage=100&position_multiple=10')));
  assert.equal(JSON.parse(c.calls.find(r=>r.method==='POST').body).leverage,100);
});

test('contract leverage alone preserves PnL while position multiple changes exposure',async()=>{
  const c=harness(()=>{throw new Error('unexpected POST');});c.api.setActive(true);await flush();
  inputEstimate(c,'orderType','limit');inputEstimate(c,'limitPrice','100');inputEstimate(c,'takeProfit','110');
  const before=estimateText(c,'takeProfit');
  inputEstimate(c,'leverage','50');assert.equal(estimateText(c,'takeProfit'),before);
  inputEstimate(c,'positionMultiple','5');assert.match(estimateText(c,'takeProfit'),/\+489\.50/);
  inputEstimate(c,'positionMultiple','10');inputEstimate(c,'leverage','10');assert.match(estimateText(c,'takeProfit'),/可用资金不足/);
  c.submit('trade',{symbol:'BTC-USDT-SWAP',leverage:'100',positionMultiple:'2.5'});await flush();
  assert.match(c.el.innerHTML,/请选择有效的仓位倍数/);assert.equal(c.calls.some(r=>r.method==='POST'),false);
});

test('direct entry fails closed if the server still returns legacy all-available sizing',async()=>{
  const c=harness(()=>{throw new Error('unexpected POST');});c.quote.sizing='all_available';
  c.api.setActive(true);await flush();c.submit('trade',{symbol:'BTC-USDT-SWAP'});await flush();
  assert.equal(c.calls.some(r=>r.method==='POST'),false);
  assert.match(c.el.innerHTML,/服务尚未支持当前仓位设置/);
});

test('direct entry submits frozen quantity and contract leverage independently of the position multiple',async()=>{
  const c=harness(async()=>({ok:true,json:async()=>({fill:{action:'open',quantity:'100',price:'100',fee:'10'}})}));
  c.quote.quantity='100';c.quote.available_after='890';
  c.api.setActive(true);await flush();c.submit('trade',{symbol:'BTC-USDT-SWAP',leverage:'100',positionMultiple:'10'});await flush();
  assert.equal(c.nodes['#desk-confirm-dialog'],undefined);
  await flush();
  const payload=JSON.parse(c.calls.find(r=>r.method==='POST').body);
  assert.equal(payload.quantity,'100');assert.equal(payload.leverage,100);
});

const heldPosition=()=>({id:'position-1',account_id:'account-1',symbol:'BTC-USDT-SWAP',side:'long',leverage:100,remaining:'2',available_to_close:'2',entry_price:'100',reserved_margin:'2',fees:'.2',mark:'120',mark_ms:Date.now(),unrealized:'40',take_profit:null,stop_loss:null});
const dataClick=(c,dataset)=>c.listeners.click({target:{closest:()=>({dataset,hasAttribute:()=>false})}});

for(const side of ['long','short'])test(`${side} position return uses unrounded remaining margin without applying direction or leverage twice`,async()=>{
 const c=harness(()=>{throw new Error('unexpected POST');});
 c.snapshot.positions=[{...heldPosition(),side,unrealized:'29.9564',reserved_margin:'94.0204',initial_margin:'188.0408',leverage:100}];
 c.api.setActive(true);await flush();
 assert.match(c.el.innerHTML,/desk-position-return desk-long[^>]*><strong>\+31\.86%<\/strong><span>收益率<\/span>/);
 assert.match(c.el.innerHTML,/浮动盈亏 ÷ 当前持仓保证金 × 100%，未扣手续费/);
 assert.equal(c.calls.some(r=>r.method==='POST'),false);
});

for(const [pnl,margin,label,tone] of [
 ['-12.5','50','-25.00%','desk-short'],['0','50','0.00%','desk-muted'],
 [null,'50','—','desk-muted'],[undefined,'50','—','desk-muted'],
 ['10','0','—','desk-muted'],['10',null,'—','desk-muted'],['10','-1','—','desk-muted'],
 ['NaN','50','—','desk-muted'],['10','Infinity','—','desk-muted'],['','50','—','desk-muted'],
])test(`position return handles PnL ${pnl} and margin ${margin}`,async()=>{
 const c=harness(()=>{throw new Error('unexpected POST');});
 c.snapshot.positions=[{...heldPosition(),unrealized:pnl,reserved_margin:margin}];
 c.api.setActive(true);await flush();
 const roi=c.el.innerHTML.match(/<div class="desk-position-return ([^"]+)"[^>]*><strong>([^<]+)<\/strong>/);
 assert.ok(roi);assert.equal(roi[1],tone);assert.equal(roi[2],label);
});

test('position return stays consistent after partial closing and refreshes while the ticket is focused',async()=>{
 const c=harness(()=>{throw new Error('unexpected POST');});c.snapshot.positions=[heldPosition()];
 c.api.setActive(true);await flush();
 assert.match(c.el.innerHTML,/desk-position-return desk-long[^>]*><strong>\+2000\.00%/);
 const ledger={innerHTML:''};c.nodes['.desk-ledger-content']=ledger;c.editing(true);
 Object.assign(c.snapshot.positions[0],{remaining:'1',reserved_margin:'1',unrealized:'20'});
 c.tick();await flush();assert.match(ledger.innerHTML,/desk-position-return desk-long[^>]*><strong>\+2000\.00%/);
 c.snapshot.positions[0].unrealized='-10';c.tick();await flush();
 assert.match(ledger.innerHTML,/desk-position-return desk-short[^>]*><strong>-1000\.00%/);
 c.snapshot.positions[0].unrealized=null;c.tick();await flush();
 assert.match(ledger.innerHTML,/desk-position-return desk-muted[^>]*><strong>—/);
 assert.equal(c.calls.some(r=>r.method==='POST'),false);
});

test('position protection editor accepts profit lock and saves fresh quote without opening another position',async()=>{
 const c=harness(async(_url,opt)=>({ok:true,json:async()=>({protection:{...heldPosition(),take_profit:JSON.parse(opt.body).take_profit,stop_loss:JSON.parse(opt.body).stop_loss}})}));
 c.snapshot.positions=[heldPosition()];c.api.setActive(true);await flush();
 assert.match(c.el.innerHTML,/>止盈止损<\/button>/);dataClick(c,{deskProtection:'position-1'});
 assert.equal(c.nodes['#desk-protection-dialog'].open,true);
 c.listeners.input({target:{name:'heldstopLoss',value:'110'}});
 // Inputs are retained across a background refresh without recreating the dialog.
 const dialog=c.nodes['#desk-protection-dialog'];c.tick();await flush();assert.equal(c.nodes['#desk-protection-dialog'],dialog);
 c.submit('protection-form',{heldtakeProfit:'130',heldstopLoss:'110'});await flush();
 const post=c.calls.find(r=>r.method==='POST');assert.ok(post.url.split('?')[0].endsWith('/positions/position-1/protection'));
 assert.deepEqual(JSON.parse(post.body),{take_profit:'130',stop_loss:'110',quote_id:'a'.repeat(32),request_id:'request-uuid-12345678'});
 assert.match(c.el.innerHTML,/持仓止盈止损已更新/);assert.doesNotMatch(c.el.innerHTML,/模拟账户已创建/);
 assert.match(c.el.innerHTML,/Practice/);assert.equal(c.storage.size,0);
});

test('clearing both protection legs saves nulls without a quote and uncertain results replay exactly',async()=>{
 let tries=0;const c=harness(async()=>{if(tries++===0)throw new Error('lost response');return {ok:true,json:async()=>({protection:{...heldPosition()}})};});
 c.snapshot.positions=[{...heldPosition(),take_profit:'130',stop_loss:'110'}];c.api.setActive(true);await flush();
 dataClick(c,{deskProtection:'position-1'});c.click('clear-protection');c.submit('protection-form',{});await flush();
 assert.equal(c.calls.some(x=>x.url.includes('/quote?')),false);assert.equal(c.storage.size,1);
 assert.doesNotMatch(c.el.innerHTML,/id="desk-protection-dialog"/);c.click('retry');await flush();
 const posts=c.calls.filter(x=>x.method==='POST');assert.equal(posts.length,2);assert.equal(posts[0].body,posts[1].body);
 assert.equal(JSON.parse(posts[0].body).quote_id,null);assert.match(c.el.innerHTML,/已取消该持仓的止盈止损/);
});

test('leaving while fetching protection quote prevents a delayed mutation',async()=>{
 let release;const gate=new Promise(r=>{release=r;});
 const c=harness(()=>{throw new Error('unexpected POST');},async url=>{if(url.includes('/quote?'))await gate;});
 c.snapshot.positions=[heldPosition()];c.api.setActive(true);await flush();dataClick(c,{deskProtection:'position-1'});
 c.submit('protection-form',{heldtakeProfit:'130',heldstopLoss:'110'});await flush();c.api.setActive(false);release();await flush();
 assert.equal(c.calls.some(x=>x.method==='POST'),false);assert.doesNotMatch(c.el.innerHTML,/id="desk-protection-dialog"/);
});

test('percentage shortcuts directly submit the server-frozen remaining quantity',async()=>{
 const c=harness(async()=>({ok:true,json:async()=>({fill:{action:'close',quantity:'.5',price:'100',fee:'.05'}})}));
 c.snapshot.positions=[heldPosition()];c.api.setActive(true);await flush();dataClick(c,{deskClose:'position-1'});
 assert.doesNotMatch(c.el.innerHTML,/name="quantity"/);assert.match(c.el.innerHTML,/data-desk-percent="100" aria-pressed="true"/);
 dataClick(c,{deskPercent:'25'});assert.match(c.el.innerHTML,/预计平仓 0.5 \/ 持仓 2/);
 c.submit('trade',{closePercent:'25',orderType:'market'});await flush();
 assert.ok(c.calls.some(x=>x.url.includes('/close-preview?percentage=25')));
 assert.equal(c.nodes['#desk-confirm-dialog'],undefined);
 assert.match(c.el.innerHTML,/已模拟平仓/);
 assert.equal(JSON.parse(c.calls.find(x=>x.method==='POST').body).quantity,'0.5');
});

test('percentage input rejects invalid values and explains reservations rather than capping silently',async()=>{
 const c=harness(()=>{throw new Error('unexpected POST');});
 c.snapshot.positions=[{...heldPosition(),available_to_close:'.5',pending_close_quantity:'1.5'}];c.api.setActive(true);await flush();c.click('trade-close');
 assert.match(c.el.innerHTML,/预计平仓 2 \/ 持仓 2 个 · 超出可平数量/);
 c.submit('trade',{closePercent:'101',orderType:'market'});await flush();
 assert.match(c.el.innerHTML,/平仓比例须为 1 至 100/);assert.equal(c.calls.some(x=>x.url.includes('/close-preview?')),false);
});

test('dismissing position protection restores its own trigger instead of the entry submit button',async()=>{
 const c=harness(()=>{throw new Error('unexpected POST');});let returned=false;
 c.snapshot.positions=[heldPosition()];c.api.setActive(true);await flush();
 c.nodes['[data-desk-protection="position-1"]']={focus:()=>{returned=true;}};
 dataClick(c,{deskProtection:'position-1'});c.click('dismiss-protection');
 assert.equal(returned,true);assert.doesNotMatch(c.el.innerHTML,/id="desk-protection-dialog"/);
 assert.equal(c.calls.some(r=>r.method==='POST'),false);
});

test('closed positions show all-fill summary and related trades rather than deriving average from final entry',async()=>{
  const c=harness(()=>{});c.snapshot.positions=[{id:'closed-p',account_id:'account-1',symbol:'BTC-USDT-SWAP',side:'long',leverage:100,
    remaining:'0',entry_price:'115',average_open_price:'112',average_close_price:'126',opened_notional:'560',closed_notional:'630',cumulative_entry_margin:'5.6',net_realized:'69.405',fees:'.595',opened_ms:1000,closed_ms:2000,closing_reason:'take_profit',related_fills:[{action:'close',quantity:'4',price:'130',fee:'.26',gross_pnl:'60',created_ms:2000,exit_reason:'take_profit',fee_role:'taker'}]}];
  c.api.setActive(true);await flush();dataClick(c,{deskTab:'closed'});
  assert.match(c.el.innerHTML,/开仓均价<\/dt><dd>112/);assert.match(c.el.innerHTML,/平仓均价<\/dt><dd>126/);
  assert.match(c.el.innerHTML,/止盈平仓/);assert.match(c.el.innerHTML,/关联成交 · 1/);assert.match(c.el.innerHTML,/收益率 \/ 累计保证金/);
});

test('orders show only pending USDT orders and actual exposure with cancel actions',async()=>{
  const c=harness(()=>{throw new Error('unexpected POST');});
  const order={account_id:'account-1',symbol:'BTC-USDT-SWAP',side:'long',leverage:100,action:'open',quantity:'1',limit_price:'80000',fee_rate:'0.0002'};
  c.snapshot.orders=[{...order,id:'pending',status:'pending',reason:'等待提交后的新鲜报价触价'},{...order,id:'rejected',status:'rejected',reason:'可用资金不足'}];
  c.snapshot.limit_worker={running:true,last_check_ms:Date.now()};
  c.api.setActive(true);await flush();
  c.listeners.click({target:{closest:()=>({dataset:{deskTab:'orders'},hasAttribute:()=>false})}});
  assert.match(c.el.innerHTML,/data-desk-cancel-order="pending"/);assert.match(c.el.innerHTML,/仓位 80×/);
  assert.match(c.el.innerHTML,/80000 USDT/);
  assert.doesNotMatch(c.el.innerHTML,/等待提交后的新鲜报价触价|desk-ledger-footer|desk-worker-dot|历史委托|可用资金不足|data-desk-unit/);
});
test('order exposure follows account equity while editing and handles unavailable or tiny exposure',async()=>{
  const c=harness(()=>{throw new Error('unexpected POST');});
  c.snapshot.orders=[{id:'pending',account_id:'account-1',symbol:'BTC-USDT-SWAP',side:'long',leverage:100,
    action:'open',quantity:'0.1',limit_price:'100000',fee_rate:'0.0002',status:'pending'}];
  c.api.setActive(true);await flush();dataClick(c,{deskTab:'orders'});
  assert.match(c.el.innerHTML,/仓位 10×/);
  const content={innerHTML:''};c.nodes['.desk-ledger-content']=content;c.editing(true);
  c.snapshot.accounts[0].equity='2000';await c.tick();await flush();
  assert.match(content.innerHTML,/仓位 5×/);assert.doesNotMatch(content.innerHTML,/仓位 100×/);
  for(const equity of [null,'0','-1','unavailable']){
    c.snapshot.accounts[0].equity=equity;await c.tick();await flush();
    assert.match(content.innerHTML,/仓位 —/);assert.doesNotMatch(content.innerHTML,/Infinity|NaN/);
  }
  c.snapshot.accounts[0].equity='1000';c.snapshot.orders[0].quantity='0.00001';
  await c.tick();await flush();assert.match(content.innerHTML,/仓位 &lt;0.01×/);
  assert.equal(c.calls.some(r=>r.method==='POST'),false);
});
test('live polling surfaces an actual worker failure and removes it after recovery',async()=>{
  const c=harness(()=>{});c.snapshot.limit_worker={running:true};c.api.setActive(true);await flush();
  const alert={innerHTML:''};c.nodes['[data-desk-worker-alert]']=alert;c.editing(true);
  c.snapshot.limit_worker={running:true,error:'报价连接中断'};await c.tick();await flush();assert.match(alert.innerHTML,/报价连接中断/);
  c.snapshot.limit_worker={running:false};await c.tick();await flush();assert.match(alert.innerHTML,/暂不触发/);
  c.snapshot.limit_worker={running:true};await c.tick();await flush();assert.equal(alert.innerHTML,'');
});

test('receipt snapshot updates funds immediately without waiting for another live request',async()=>{
  let committed=false;
  const c=harness(async()=>{
    committed=true;
    const snapshot=structuredClone(c.snapshot);
    snapshot.accounts[0].available='888';snapshot.accounts[0].equity='999';
    return {ok:true,json:async()=>({fill:{action:'open',quantity:'1',price:'100',fee:'.1'},snapshot})};
  },async url=>{if(committed&&url.includes('/live'))throw new Error('must not wait for follow-up');});
  c.snapshot.view='live';c.snapshot.active_account_id='account-1';
  await submitEntry(c);await flush();
  assert.match(c.el.innerHTML,/888 USDT/);
  assert.match(c.el.innerHTML,/已模拟开仓/);
  assert.doesNotMatch(c.el.innerHTML,/读取中|提交中|账户刷新失败/);
  assert.equal(c.calls.filter(x=>x.url.includes('/live')).length,1);
  assert.equal(c.storage.size,0);
});

test('a delayed pre-trade snapshot cannot overwrite a committed receipt snapshot',async()=>{
  let hold=false,release;
  const gate=new Promise(resolve=>{release=resolve;});
  const c=harness(async()=>{
    const snapshot=structuredClone(c.snapshot);snapshot.accounts[0].available='777';
    return {ok:true,json:async()=>({fill:{action:'open',quantity:'1',price:'100',fee:'.1'},snapshot})};
  },async url=>{if(hold&&url.includes('/live')){const old=structuredClone(c.snapshot);await gate;return old;}});
  c.api.setActive(true);await flush();hold=true;c.tick();await flush();
  c.submit('trade',{symbol:'BTC-USDT-SWAP',leverage:'100'});await flush();
  assert.match(c.el.innerHTML,/777 USDT/);
  release();await flush();
  assert.match(c.el.innerHTML,/777 USDT/);
});

test('slow fallback account refresh does not keep a committed order button disabled',async()=>{
  let committed=false,release;
  const gate=new Promise(resolve=>{release=resolve;});
  const c=harness(async()=>{committed=true;return {ok:true,json:async()=>({fill:{action:'open',quantity:'1',price:'100',fee:'.1'}})};},
    async url=>{if(committed&&url.includes('/live'))await gate;});
  await submitEntry(c);await flush();
  assert.equal(c.storage.size,0);
  assert.doesNotMatch(c.el.innerHTML,/提交中|读取中/);
  assert.match(c.el.innerHTML,/已模拟开仓/);
  release();await flush();
});

test('live pages load history only on demand and navigate bounded record pages',async()=>{
  const c=harness(()=>{});c.snapshot.view='live';c.snapshot.active_account_id='account-1';
  c.snapshot.fills=Array.from({length:51},(_,i)=>({id:String(i),account_id:'account-1',symbol:'BTC-USDT-SWAP',action:'close',quantity:'1',price:String(100+i),fee:'.1',gross_pnl:'1'}));
  c.api.setActive(true);await flush();
  assert.equal(c.calls.some(r=>r.url.includes('/records?')),false);
  const clickData=dataset=>c.listeners.click({target:{closest:()=>({dataset,hasAttribute:()=>false})}});
  clickData({deskTab:'fills'});await flush();
  assert.equal((c.el.innerHTML.match(/class="desk-fill"/g)||[]).length,50);
  assert.match(c.el.innerHTML,/第 1 页 · 共 51 条/);
  clickData({deskRecordPage:'50'});await flush();
  assert.equal((c.el.innerHTML.match(/class="desk-fill"/g)||[]).length,1);
  assert.match(c.el.innerHTML,/第 2 页 · 共 51 条/);
  assert.match(c.el.innerHTML,/data-desk-record-page="100" disabled/);
  c.tick();await flush();
  assert.match(c.el.innerHTML,/第 2 页/);
  assert.equal(c.calls.filter(r=>r.url.includes('/records?')).length,2);
  assert.equal(c.calls.some(r=>r.url==='/api/research/paper-desk'),false);
});

test('switching accounts requests a scoped live snapshot and rejects an old pending response',async()=>{
  let hold=false,release;
  const gate=new Promise(resolve=>{release=resolve;});
  const c=harness(()=>{},async url=>{
    if(!url.includes('/live'))return;
    if(hold&&url.includes('account_id=account-1')){const old=structuredClone(c.snapshot);await gate;return old;}
    if(url.includes('account_id=account-2')){
      const next=structuredClone(c.snapshot);next.active_account_id='account-2';next.accounts[1].available='222';return next;
    }
  });
  c.snapshot.view='live';c.snapshot.active_account_id='account-1';
  c.snapshot.accounts.push({...c.snapshot.accounts[0],id:'account-2',name:'Second'});
  c.api.setActive(true);await flush();hold=true;c.tick();await flush();
  c.listeners.change({target:{value:'account-2',matches:selector=>selector==='[data-desk-account]'}});
  release();await flush();
  assert.ok(c.calls.some(r=>r.url.includes('/live?account_id=account-2')));
  assert.match(c.el.innerHTML,/222 USDT/);
});

test('receipt triggers fresh history while a pre-trade history request is still pending',async()=>{
  let release,historyCalls=0;
  const gate=new Promise(resolve=>{release=resolve;});
  const c=harness(async()=>({ok:true,json:async()=>({fill:{action:'open',quantity:'1',price:'100',fee:'.1'},snapshot:structuredClone(c.snapshot)})}),async url=>{
    if(url.includes('/history')){
      const sequence=++historyCalls;
      if(sequence===1)await gate;
      return {account_id:'account-1',month:'2026-10',total_net:sequence===1?'old':'new'};
    }
  });
  c.window.SpikePaperHistory={render:data=>data?.total_net||'',bind(){}};
  const historyNode={innerHTML:'',querySelector:()=>null};c.nodes['[data-desk-history]']=historyNode;
  c.api.setActive(true);await flush();
  assert.equal(historyCalls,1);
  c.submit('trade',{symbol:'BTC-USDT-SWAP',leverage:'100'});await flush();
  assert.ok(historyCalls>=2);assert.equal(historyNode.innerHTML,'new');
  release();await flush();
  assert.equal(historyNode.innerHTML,'new');
});

function journalFixture(overrides={}) {
  let data={account_name:'Journal QA',position:{id:'p',account_id:'account-1',symbol:'BTC-USDT-SWAP',side:'long',leverage:100,quantity:'2',remaining:'2',entry_price:'80000',fees:'80',opened_ms:Date.now()},journal:{position_id:'p',entry_reason:'Original reason',analysis:'',plan:'',review:'',version:0,created_ms:null,updated_ms:null}};
  const c=harness(async(url,options)=>{
    if(overrides.post)return overrides.post(url,options);
    assert.match(url,/\/positions\/p\/journal$/);
    const payload=JSON.parse(options.body);
    data={...data,journal:{...data.journal,...payload,version:payload.version+1,created_ms:Date.now(),updated_ms:Date.now()}};
    return {ok:true,json:async()=>structuredClone(data)};
  },async url=>{
    if(url.endsWith('/journal'))return overrides.get?overrides.get(url):structuredClone(data);
    return overrides.other?.(url);
  });
  c.snapshot.positions=[{...data.position,notional_usdt:'160000',reserved_margin:'1600'}];
  const open=()=>c.listeners.click({target:{closest:()=>({dataset:{deskJournal:'p'},hasAttribute:()=>false})}});
  const input=(field,value)=>c.listeners.input({target:{name:'journal_'+field,value}});
  return {...c,open,input,setData:value=>{data=value;},data:()=>structuredClone(data)};
}

test('position journal loads lazily and saves all fields, reopening the durable content',async()=>{
  const c=journalFixture();c.api.setActive(true);await flush();
  assert.match(c.el.innerHTML,/data-desk-journal-card="p" tabindex="0"/);
  assert.equal(c.calls.filter(x=>x.url.endsWith('/journal')).length,0);
  c.open();await flush();assert.equal(c.nodes['#desk-journal-dialog'].open,true);
  for(const label of ['开单逻辑','行情分析','交易计划','复盘记录'])assert.match(c.el.innerHTML,new RegExp(label));
  c.input('entry_reason','Breakout\nRetest');c.input('analysis','<script>no execution</script>');c.input('plan','Exit if invalid');c.input('review','Review later');
  c.submit('journal-form',{});await flush();
  assert.match(c.el.innerHTML,/开单日志已保存/);assert.match(c.el.innerHTML,/&lt;script&gt;no execution&lt;\/script&gt;/);
  assert.equal(c.calls.filter(x=>x.method==='POST').length,1);
  const body=JSON.parse(c.calls.find(x=>x.method==='POST').body);
  assert.deepEqual(body,{version:0,entry_reason:'Breakout\nRetest',analysis:'<script>no execution</script>',plan:'Exit if invalid',review:'Review later'});
  c.click('journal-close');assert.equal(c.nodes['#desk-journal-dialog'],undefined);
  c.open();await flush();assert.match(c.el.innerHTML,/Breakout\nRetest/);assert.match(c.el.innerHTML,/Review later/);
});

test('journal caret and unsaved draft survive polling, close and navigation without sending a trade',async()=>{
  const c=journalFixture();c.api.setActive(true);await flush();c.open();await flush();
  const dialog=c.nodes['#desk-journal-dialog'],count=c.calls.length;
  c.input('analysis','Keep this draft');c.tick();await flush();
  assert.equal(c.nodes['#desk-journal-dialog'],dialog);assert.equal(c.calls.length,count);
  dialog.handlers.cancel({preventDefault(){}});c.open();await flush();
  assert.match(c.el.innerHTML,/Keep this draft/);assert.match(c.el.innerHTML,/未保存/);
  c.api.setActive(false);c.api.setActive(true);await flush();c.open();await flush();
  assert.match(c.el.innerHTML,/Keep this draft/);assert.equal(c.calls.filter(x=>x.method==='POST').length,0);
});

test('card click and keyboard open journals, nested details and position actions stay independent',async()=>{
  const c=journalFixture();c.api.setActive(true);await flush();
  const card={dataset:{deskJournalCard:'p'}};
  c.listeners.click({target:{closest:s=>s==='[data-desk-journal-card]'?card:s.startsWith('a,input')?{}:null}});
  await flush();assert.equal(c.nodes['#desk-journal-dialog'],undefined);
  c.listeners.click({target:{closest:s=>s==='[data-desk-journal-card]'?card:null}});await flush();
  assert.equal(c.nodes['#desk-journal-dialog'].open,true);c.click('journal-close');
  c.listeners.keydown({target:card,key:'Enter',preventDefault(){}});await flush();
  assert.equal(c.nodes['#desk-journal-dialog'].open,true);c.click('journal-close');
  c.listeners.click({target:{closest:()=>({dataset:{deskClose:'p'},hasAttribute:()=>false})}});
  assert.equal(c.nodes['#desk-journal-dialog'],undefined);assert.match(c.el.innerHTML,/返回开仓/);
});

test('historical position cards open the same log even when absent from the live positions list',async()=>{
  const c=journalFixture();const closed={...c.data().position,remaining:'0',closed_ms:Date.now()};
  c.snapshot.positions=[closed];c.setData({...c.data(),position:closed});
  c.api.setActive(true);await flush();
  c.listeners.click({target:{closest:()=>({dataset:{deskTab:'closed'},hasAttribute:()=>false})}});
  assert.match(c.el.innerHTML,/desk-closed-position" data-desk-journal-card="p"/);
  c.snapshot.positions=[];c.open();await flush();
  assert.match(c.el.innerHTML,/已平仓/);assert.match(c.el.innerHTML,/Original reason/);
});

test('journal load errors are retryable and old responses cannot reopen a dismissed log',async()=>{
  let fail=true,release;
  const c=journalFixture({get:async()=>{if(fail)throw new Error('journal unavailable');return new Promise(resolve=>{release=resolve;});}});
  c.api.setActive(true);await flush();c.open();await flush();
  assert.match(c.el.innerHTML,/journal unavailable/);assert.match(c.el.innerHTML,/重新加载/);
  fail=false;c.click('journal-retry');await flush();c.click('journal-close');release(c.data());await flush();
  assert.equal(c.nodes['#desk-journal-dialog'],undefined);
});

test('uncertain journal save keeps draft and version for a safe retry, Escape cannot dismiss an in-flight save',async()=>{
  let release;
  const c=journalFixture({post:()=>new Promise(resolve=>{release=resolve;})});c.api.setActive(true);await flush();c.open();await flush();
  c.input('review','Must survive');c.submit('journal-form',{});
  const dialog=c.nodes['#desk-journal-dialog'];dialog.handlers.cancel({preventDefault(){}});c.click('journal-close');
  assert.equal(c.nodes['#desk-journal-dialog'],dialog);
  release({ok:false,status:503,json:async()=>({detail:'save unavailable'})});await flush();
  assert.match(c.el.innerHTML,/Must survive/);assert.match(c.el.innerHTML,/save unavailable/);
  c.submit('journal-form',{});const posts=c.calls.filter(x=>x.method==='POST');assert.equal(posts[1].body,posts[0].body);
  release({ok:true,json:async()=>({...c.data(),journal:{...c.data().journal,review:'Must survive',version:1,updated_ms:Date.now()}})});await flush();
  assert.match(c.el.innerHTML,/开单日志已保存/);
});

test('conflict keeps local text and presents latest content before explicit replacement',async()=>{
  let conflict=true;
  const c=journalFixture({post:async(_,options)=>conflict?{ok:false,status:409,json:async()=>({detail:'这笔日志已在其他页面更新'})}:{ok:true,json:async()=>({...c.data(),journal:{...c.data().journal,...JSON.parse(options.body),version:2}})}});
  c.api.setActive(true);await flush();c.open();await flush();c.input('analysis','My local analysis');
  c.setData({...c.data(),journal:{...c.data().journal,analysis:'Other tab analysis',version:1,updated_ms:Date.now()}});
  c.submit('journal-form',{});await flush();
  assert.match(c.el.innerHTML,/My local analysis/);assert.match(c.el.innerHTML,/Other tab analysis/);assert.match(c.el.innerHTML,/采用最新内容/);
  const count=c.calls.length;c.submit('journal-form',{});await flush();assert.equal(c.calls.length,count);
  conflict=false;c.click('journal-keep-draft');await flush();
  const payload=JSON.parse(c.calls.filter(x=>x.method==='POST').at(-1).body);
  assert.equal(payload.version,1);assert.equal(payload.analysis,'My local analysis');
  assert.doesNotMatch(c.el.innerHTML,/日志版本冲突/);
});

test('a pending background overview cannot replace the journal editor',async()=>{
  let hold=false,release;
  const c=journalFixture({other:async url=>{if(hold&&url.includes('/live'))await new Promise(resolve=>{release=resolve;});}});
  c.api.setActive(true);await flush();hold=true;c.tick();await flush();c.open();await flush();
  c.input('analysis','Still editing');const dialog=c.nodes['#desk-journal-dialog'];release();await flush();
  assert.equal(c.nodes['#desk-journal-dialog'],dialog);
});

test('fixed USDT mode submits one sizing input and retains its exposure after invalid input',async()=>{
  const c=harness(async()=>({ok:true,json:async()=>({fill:{action:'open',quantity:'100',price:'100',fee:'5'}})}));
  c.api.setActive(true);await flush();
  c.listeners.change({target:{name:'sizingMode',value:'notional',matches:()=>false}});
  assert.match(c.el.innerHTML,/name="notionalUsdt"/);assert.doesNotMatch(c.el.innerHTML,/name="positionMultiple"/);
  const values={symbol:'BTC-USDT-SWAP',leverage:'100',notionalUsdt:'0'};
  c.submit('trade',values);await flush();assert.match(c.el.innerHTML,/请输入有效仓位金额/);
  assert.equal(c.calls.filter(x=>x.url.includes('/open-preview')).length,0);
  c.listeners.input({target:{name:'notionalUsdt',value:'10000'}});
  c.submit('trade',{...values,notionalUsdt:'10000'});await flush();
  const preview=c.calls.find(x=>x.url.includes('/open-preview'));
  assert.match(preview.url,/notional_usdt=10000/);assert.doesNotMatch(preview.url,/position_multiple/);
  const posted=c.calls.find(x=>x.method==='POST');assert.equal(JSON.parse(posted.body).quantity,'100');
  c.listeners.change({target:{name:'sizingMode',value:'multiple',matches:()=>false}});
  assert.match(c.el.innerHTML,/name="positionMultiple"/);assert.doesNotMatch(c.el.innerHTML,/name="notionalUsdt"/);
});

test('journal image upload preserves text draft, disables close while pending and supports reversible removal',async()=>{
  let finish;
  const image={id:'image-1',name:'截图.png',deleted:0};
  const c=journalFixture({post:async(url,options)=>{
    if(url.endsWith('/images'))return new Promise(resolve=>{finish=resolve;});
    assert.match(url,/\/images\/image-1\/visibility$/);
    return {ok:true,json:async()=>[{...image,deleted:JSON.parse(options.body).deleted?1:0}]};
  }});
  c.api.setActive(true);await flush();c.open();await flush();c.input('analysis','Unsaved analysis');
  const file={type:'image/png',size:100,name:'截图.png'};
  c.listeners.change({target:{hasAttribute:n=>n==='data-desk-journal-images',files:[file]}});
  await flush();assert.match(c.el.innerHTML,/上传中/);c.click('journal-close');assert.ok(c.nodes['#desk-journal-dialog']);
  finish({ok:true,json:async()=>[image]});await flush();
  assert.match(c.el.innerHTML,/Unsaved analysis/);assert.match(c.el.innerHTML,/<img[^>]*image-1/);
  assert.equal(c.calls.find(x=>x.url.endsWith('/images')).body,file);
  c.listeners.click({target:{closest:()=>({dataset:{deskImageRemove:'image-1'}})}});await flush();
  assert.match(c.el.innerHTML,/已移除的图片/);assert.doesNotMatch(c.el.innerHTML,/<img[^>]*image-1/);
  c.listeners.click({target:{closest:()=>({dataset:{deskImageRestore:'image-1'}})}});await flush();
  assert.match(c.el.innerHTML,/<img[^>]*image-1/);assert.match(c.el.innerHTML,/Unsaved analysis/);
});

test('pasted image upload errors retain the journal text and allow retry',async()=>{
  let failed=true;
  const c=journalFixture({post:async()=>{if(failed)throw new Error('upload offline');return {ok:true,json:async()=>[{id:'img',name:'clip.png',deleted:0}]};}});
  c.api.setActive(true);await flush();c.open();await flush();c.input('plan','Keep the plan');
  let prevented=0;
  const event={preventDefault(){prevented++;},clipboardData:{items:[{kind:'file',getAsFile:()=>({type:'image/png',size:10,name:'clip.png'})}]}};
  c.listeners.paste(event);await flush();assert.match(c.el.innerHTML,/upload offline/);assert.match(c.el.innerHTML,/Keep the plan/);
  failed=false;c.listeners.paste(event);await flush();assert.equal(prevented,2);assert.match(c.el.innerHTML,/<img[^>]*\/img/);
  assert.match(c.el.innerHTML,/Keep the plan/);
});


test('position symbol switches the selected chart coin without opening journal or submitting',async()=>{
 const c=harness(()=>{throw new Error('unexpected trade');});c.api.setActive(true);await flush();
 c.snapshot.positions=[{id:'eth-position',account_id:'account-1',symbol:'ETH-USDT-SWAP',side:'long',remaining:'1',notional_usdt:'2400',unrealized:'10',leverage:100}];
 c.tick();await flush();
 assert.match(c.el.innerHTML,/data-desk-position-symbol="ETH-USDT-SWAP"/);
 c.listeners.click({target:{closest:()=>({dataset:{deskPositionSymbol:'ETH-USDT-SWAP'},hasAttribute:()=>false})}});await flush();
 assert.match(c.el.innerHTML,/name="symbol"[^>]*value="ETH-USDT-SWAP"/);
 assert.equal(c.nodes['#desk-journal-dialog'],undefined);
 assert.equal(c.calls.some(x=>x.url.includes('/journal')||x.method==='POST'),false);
});

test('entry stop is one click, sends only intent, and preserves original-request retry',async()=>{
 const c=harness(async(_url,opt)=>({ok:true,json:async()=>({protection:{...heldPosition(),stop_loss:'100'},stop_to_entry:true,unchanged:false})}));
 c.snapshot.positions=[heldPosition()];c.api.setActive(true);await flush();
 assert.match(c.el.innerHTML,/data-desk-entry-stop="position-1"/);
 dataClick(c,{deskEntryStop:'position-1'});dataClick(c,{deskEntryStop:'position-1'});await flush();
 const posts=c.calls.filter(x=>x.method==='POST');assert.equal(posts.length,1);
 assert.deepEqual(JSON.parse(posts[0].body),{stop_to_entry:true,quote_id:'a'.repeat(32),request_id:'request-uuid-12345678'});
 assert.match(c.el.innerHTML,/止损已移到开仓均价 100 USDT/);assert.match(c.el.innerHTML,/未包含手续费/);
});
test('entry stop does not submit for loss, stale marks, or already protected long and short positions',async()=>{
 for(const props of [{mark:'99'},{mark_ms:Date.now()-20000},{stop_loss:'100'},{stop_loss:'110'},{side:'short',mark:'90',stop_loss:'95'}]){
  const c=harness(()=>{throw Error('unexpected POST');});c.snapshot.positions=[{...heldPosition(),...props}];c.api.setActive(true);await flush();
  assert.match(c.el.innerHTML,/data-desk-entry-stop="position-1"[^>]*disabled/);
  dataClick(c,{deskEntryStop:'position-1'});await flush();assert.equal(c.calls.filter(x=>x.method==='POST').length,0);
 }
});
test('leaving during the fresh quote read cannot submit an entry-stop change',async()=>{
 let release;const c=harness(()=>{throw Error('unexpected POST');},url=>url.includes('/quote?')?new Promise(r=>release=()=>r(c.quote)):undefined);
 c.snapshot.positions=[heldPosition()];c.api.setActive(true);await flush();dataClick(c,{deskEntryStop:'position-1'});await flush();
 c.api.setActive(false);release();await flush();assert.equal(c.calls.filter(x=>x.method==='POST').length,0);
});

test('floating ticket uses the original desk and pins its initial symbol across router refreshes',async()=>{
  const c=harness(()=>{throw Error('opening must not submit');});
  const classes=new Set();
  c.el.classList={add:x=>classes.add(x),remove:x=>classes.delete(x)};
  const home={insertBefore(el){el.parentNode=home;}};
  const host={append(el){el.parentNode=host;}};
  c.el.parentNode=home;
  c.api.openQuick(host,{symbol:'ETH-USDT-SWAP'});await flush();
  assert.equal(c.el.parentNode,host);
  assert.equal(classes.has('desk-quick'),true);
  assert.match(c.el.innerHTML,/value="ETH-USDT-SWAP"/);
  c.api.setActive(false);c.tick();await flush();
  assert.equal(c.el.parentNode,host);
  assert.match(c.el.innerHTML,/value="ETH-USDT-SWAP"/);
  assert.equal(c.calls.some(x=>x.method==='POST'),false);
  c.api.closeQuick();assert.equal(c.el.parentNode,home);assert.equal(c.el.innerHTML,'');
  assert.equal(classes.has('desk-quick'),false);
  c.api.setActive(true);await flush();assert.match(c.el.innerHTML,/data-desk-trade/);
});

test('floating ticket returns its node when navigating to the full desk',async()=>{
  const c=harness(()=>{throw Error('navigation must not submit');});
  c.el.classList={add(){},remove(){}};
  const home={insertBefore(el){el.parentNode=home;}};
  const host={append(el){el.parentNode=host;}};c.el.parentNode=home;
  c.api.openQuick(host,{symbol:'W-USDT-SWAP'});await flush();
  c.api.setActive(true);await flush();
  assert.equal(c.el.parentNode,home);assert.match(c.el.innerHTML,/value="W-USDT-SWAP"/);
  c.api.closeQuick();assert.match(c.el.innerHTML,/data-desk-trade/);
  assert.equal(c.calls.some(x=>x.method==='POST'),false);
});

test('floating ticket sends market entries through the same frozen-quote and idempotent paper endpoint',async()=>{
  const c=harness(async()=>({ok:true,json:async()=>({fill:{action:'open',quantity:'1',price:'100',fee:'.1'}})}));
  c.el.classList={add(){},remove(){}};
  const home={insertBefore(el){el.parentNode=home;}};const host={append(el){el.parentNode=host;}};c.el.parentNode=home;
  c.api.openQuick(host,{symbol:'BTC-USDT-SWAP'});await flush();
  await c.submit('trade',{symbol:'BTC-USDT-SWAP',leverage:'100',positionMultiple:'10',orderType:'market'});await flush();
  const posts=c.calls.filter(x=>x.method==='POST');assert.equal(posts.length,1);
  assert.equal(posts[0].url,'/api/research/paper-desk/accounts/account-1/open?include_snapshot=true');
  const payload=JSON.parse(posts[0].body);assert.equal(payload.quote_id,'a'.repeat(32));assert.equal(payload.request_id,'request-uuid-12345678');
  assert.equal(c.storage.size,0);c.api.closeQuick();
});
