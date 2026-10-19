/* Hermetic DOM contracts: original exported documents, actual adapters, no network. */
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const {JSDOM}=require('jsdom');
const root=path.resolve(__dirname,'../../..');
const assets=path.join(root,'yoyo/monitor/static/pro');
const source=path.join(root,'yoyo/monitor/pro/source');
const settle=async()=>{for(let i=0;i<8;i++)await new Promise(r=>setImmediate(r));};
const symbols=[{symbol:'BTC-USDT-SWAP',base:'BTC',last:100,change_24h_percent:1,volume_usdt_24h:1000},{symbol:'ETH-USDT-SWAP',base:'ETH',last:50,change_24h_percent:-3,volume_usdt_24h:500}];
const positions=[{instId:'BTC-USDT-SWAP',posSide:'long',pos:'2',avgPx:'100',markPx:'102',upl:'4',lever:'5'}];
function fixture(url){
 const p=new URL(url,'http://localhost').pathname;
 if(p.endsWith('/symbols'))return {symbols};
 if(p.endsWith('/market')||p.endsWith('/quote'))return {id:'quote',last:100,bid:99,ask:101};
 if(p==='/api/pro/live/status')return {settings:{revision:0,sources:[],follows:[]},session:{effective:false},worker:{},account:{},configured:false};
 if(p==='/api/pro/sources')return {settings:{revision:0,sources:[],follows:[]},catalog:[],items:[]};
 if(p==='/api/manual-live/dashboard')return {account:{data:{total_equity_usdt:1000,positions,balance:{availEq:800,upl:4,imr:200}}},executions:{statistics:{},active_items:[]},entry_status:{status:'ready',message:'ready'}};
 if(p==='/api/research/paper-desk')return {accounts:[{id:'paper1',name:'Test',equity:10000,available:9000}],positions:[],orders:[],fills:[]};
 if(p.startsWith('/api/pro/workspace/'))return {value:{},revision:0};
 if(p==='/api/chart')return {candles:[{t:1,o:1,h:2,l:.5,c:1.5,v:1}]};
 if(p==='/api/pro/library')return {books:[]};
 return {items:[],models:[],symbols:[],candles:[],fields:{},worker:{},scan:{},data:{},sources:[]};
}
async function harness(page,respond=fixture){
 const html=fs.readFileSync(path.join(source,page+'.html'),'utf8').replace(/<script\b[^>]*>[\s\S]*?<\/script>/gi,'').replace(/\s+on\w+=(?:"[^"]*"|'[^']*')/g,'');
 const dom=new JSDOM(html,{url:'http://localhost/pro/'+page,runScripts:'outside-only',pretendToBeVisual:true}),w=dom.window;
 w.document.body.dataset.proPage=page;w.document.body.dataset.proMode='connected';
 w.HTMLDialogElement.prototype.showModal=function(){this.open=true;};w.HTMLDialogElement.prototype.close=function(){this.open=false;this.dispatchEvent(new w.Event('close'));};
 w.ResizeObserver=class{observe(){}disconnect(){}};
 w.requestAnimationFrame=fn=>{queueMicrotask(fn);return 1;};
 w.Element.prototype.scrollIntoView=function(){};
 w.URL.createObjectURL=()=> 'blob:test';w.URL.revokeObjectURL=()=>{};
 w.fetch=async(url,opts={})=>{const data=await respond(url,opts);return {ok:true,status:200,json:async()=>data};};
 const timers=new Set();w.setTimeout=(fn,ms)=>{const id=setTimeout(fn,ms);id.unref();timers.add(id);return id;};w.clearTimeout=id=>{clearTimeout(id);timers.delete(id);};
 const ctx=dom.getInternalVMContext(),modules=new Map();
 async function load(name){if(modules.has(name))return modules.get(name);const m=new vm.SourceTextModule(fs.readFileSync(path.join(assets,name+'.js'),'utf8'),{context:ctx,identifier:name,importModuleDynamically:async spec=>{const mod=await load(spec.replace('./','').replace('.js',''));if(mod.status==='linked')await mod.evaluate();return mod;}});modules.set(name,m);await m.link(spec=>load(spec.replace('./','').replace('.js','')));return m;}
 const core=await load('core');await core.evaluate();core.namespace.rememberSources();core.namespace.navigation();
 const module=await load(page);await module.evaluate();module.namespace.mount();await settle();
 return {w,doc:w.document,core:core.namespace,load,click:async selector=>{const el=typeof selector==='string'?w.document.querySelector(selector):selector;assert.ok(el,'control exists');el.click();await settle();},close:()=>{for(const t of timers)clearTimeout(t);dom.window.close();}};
}
test('all ten adapters mount the exact exported document without an exception',async()=>{
 for(const page of Object.keys(JSON.parse(fs.readFileSync(path.join(source,'manifest.json'))).screens)){const h=await harness(page);try{assert.ok(h.doc.querySelector('main'));assert.equal(h.doc.querySelectorAll('button:not([data-pro-bound])').length,0,page+' has unbound buttons');}finally{h.close();}}
});
test('market header sort and settings perform their named actions',async()=>{const h=await harness('market');try{
 const header=[...h.doc.querySelectorAll('[role=button]')].find(e=>e.getAttribute('aria-label')==='切换涨幅与跌幅排序');await h.click(header);assert.equal(h.doc.querySelector('[data-symbol]').dataset.symbol,'BTC-USDT-SWAP');await h.click(header);assert.equal(h.doc.querySelector('[data-symbol]').dataset.symbol,'ETH-USDT-SWAP');
 const settings=[...h.doc.querySelectorAll('main button')].find(e=>e.textContent.trim()==='settings');await h.click(settings);assert.ok(h.doc.querySelector('dialog [name=ma]'));assert.ok(h.doc.querySelector('dialog [name=volume]'));
 }finally{h.close();}});
test('trade type changes unlock the form immediately; leverage and table filters work',async()=>{const h=await harness('trading');try{
 assert.equal(h.doc.querySelector('#orderQty').readOnly,true);await h.click('#type-twap');assert.equal(h.doc.querySelector('#orderQty').readOnly,false);assert.equal(h.doc.querySelector('#orderPrice').readOnly,true);
 const lev=[...h.doc.querySelectorAll('main button')].find(e=>e.textContent.trim()==='10×');await h.click(lev);const input=h.doc.querySelector('dialog [name=leverage]');assert.ok(input);input.value='5';await h.click([...h.doc.querySelectorAll('dialog button')].find(b=>b.textContent==='保存'));assert.equal(h.doc.querySelector('#leverageRange').value,'5');assert.equal(lev.textContent,'5×');
 const filter=[...h.doc.querySelectorAll('main button')].find(b=>b.textContent.trim()==='filter_list');await h.click(filter);h.doc.querySelector('dialog [name=symbol]').value='ETH';await h.click([...h.doc.querySelectorAll('dialog button')].find(b=>b.textContent==='保存'));assert.equal(h.doc.querySelector('table tbody').textContent.trim(),'暂无记录');
 }finally{h.close();}});
test('copy tab uses real rules and system switches have reachable actions',async()=>{const h=await harness('trading');try{
 await h.click('#tab-btn-copytrade');assert.equal(h.doc.querySelector('dialog'),null);assert.ok(h.doc.querySelector('#tab-copytrade').textContent.includes('尚无自动跟单规则'));assert.ok(!h.doc.querySelector('#tab-copytrade').textContent.includes('Arbitrage'));assert.ok(!/胜率|夏普|20,000|4,180/.test(h.doc.querySelector('#tab-copytrade').textContent));
 await h.click('#tab-btn-system');await h.click(h.doc.querySelector('#tab-system [role=button]'));assert.ok(h.doc.querySelector('dialog').textContent.includes('当前账户成交回执'));
 }finally{h.close();}});
test('runtime exception button survives label substitution',async()=>{const h=await harness('runtime');try{await h.click([...h.doc.querySelectorAll('button')].find(b=>b.textContent.includes('查看后台异常记录')));assert.ok(h.doc.querySelector('dialog').textContent.includes('后台异常记录'));}finally{h.close();}});
test('account history filters refresh when history arrives after initial empty response',async()=>{let reads=0;const h=await harness('account',url=>{if(new URL(url,'http://localhost').pathname==='/api/manual-live/dashboard'){const d=fixture(url);d.account.data.history={items:reads++?[{instId:'NEW-USDT-SWAP',side:'buy',posSide:'long',state:'filled'}]:[]};return d;}return fixture(url);});try{h.w.dispatchEvent(new h.w.Event('pro-account-changed'));await settle();assert.ok(h.doc.querySelector('main select').textContent.includes('NEW-USDT-SWAP'));}finally{h.close();}});
test('timeout is visible, user cancellation remains an AbortError, duplicate click is suppressed',async()=>{const h=await harness('mobile');try{
 h.w.fetch=(url,opts)=>new Promise((resolve,reject)=>opts.signal.addEventListener('abort',()=>reject(new h.w.DOMException('aborted','AbortError'))));
 await assert.rejects(h.core.api('/slow',{timeout:5}),/请求超时/);
 const ctl=new h.w.AbortController();const req=h.core.api('/cancel',{signal:ctl.signal});ctl.abort();await assert.rejects(req,{name:'AbortError'});
 let calls=0,done;const b=h.doc.createElement('button');h.doc.body.append(b);h.core.on(b,()=>{calls++;return new Promise(r=>done=r);});b.click();b.click();assert.equal(calls,1);assert.equal(b.getAttribute('aria-busy'),'true');done();await settle();assert.equal(b.hasAttribute('aria-busy'),false);
 }finally{h.close();}});
test('Enter submits through the dialog action without navigating or duplicate writes',async()=>{const h=await harness('mobile');try{let saves=0,release;const d=h.core.form('Test',h.core.field('value','Value','hello'),async()=>{saves++;await new Promise(r=>release=r);});const f=d.querySelector('form');const submit=()=>f.dispatchEvent(new h.w.Event('submit',{bubbles:true,cancelable:true}));assert.equal(submit(),false);assert.equal(submit(),false);assert.equal(saves,1);release();await settle();}finally{h.close();}});
test('MA chart ignores a slower response for the previously selected timeframe',async()=>{
 const record={id:'r1',job_id:'j1',detection_id:'d1',symbol:'BTC-USDT-SWAP',side:'long',timeframe:'15m',confidence:.9};let resolveOld;
 const bar=price=>({t:1,o:price,h:price+1,l:price-1,c:price,v:1});
 const h=await harness('ma-detection',url=>{if(url.includes('/discoveries'))return {items:[record]};if(url.includes('/results/'))return {candles:[bar(100)]};if(url.includes('/api/chart?')){if(url.endsWith('15m'))return new Promise(r=>resolveOld=r);return {candles:[bar(200)]};}return fixture(url);});try{
 const period=value=>[...h.doc.querySelectorAll('main button')].find(e=>e.textContent.trim()===value);period('15m').click();await settle();await h.click(period('1H'));assert.ok(h.doc.querySelector('#activePriceTag').textContent.includes('200'));resolveOld({candles:[bar(50)]});await settle();assert.ok(h.doc.querySelector('#activePriceTag').textContent.includes('200'));
 }finally{h.close();}
});
test('workspace preferences, model cards and MA filtering expose actual controls',async()=>{
 const h=await harness('vision-research');try{const button=[...h.doc.querySelectorAll('aside button')].find(b=>b.title==='终端偏好设置');await h.click(button);assert.ok(h.doc.querySelector('dialog [name=shortcuts]'));h.doc.querySelector('dialog').close();const card=h.doc.querySelector('[aria-label="查看六均线输入契约"]');await h.click(card);assert.ok(h.doc.querySelector('dialog').textContent.includes('SMA20'));}finally{h.close();}
 const ma=await harness('ma-detection');try{await ma.click([...ma.doc.querySelectorAll('main button')].find(b=>b.textContent.trim()==='filter_list'));assert.ok(ma.doc.querySelector('dialog [name=symbol]'));assert.ok(ma.doc.querySelector('dialog [name=timeframe]'));}finally{ma.close();}
});
