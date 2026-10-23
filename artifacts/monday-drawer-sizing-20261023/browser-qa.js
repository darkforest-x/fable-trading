async page => {
 await page.setViewportSize({width:390,height:844});
 const calls=[],errors=[];page.on('pageerror',e=>errors.push(e.message));
 const base={equity:'1000',available:'750',exposure_pool:'BTC-USDT-SWAP',exposure_multiple:10,exposure_cap:'10000',exposure_used:'0',exposure_remaining:'10000',max_leverage:'75',leverage:'75',price:'100',notional_usdt:'10000',notional:'10000',tick_size:'.1'};
 let draft;
 const reply=(route,data,status=200)=>route.fulfill({status,contentType:'application/json',body:JSON.stringify(data)});
 await page.route('**/api/manual-live/**',async route=>{
  const req=route.request(),path=new URL(req.url()).pathname;calls.push({mode:'live',path,method:req.method()});
  if(path.endsWith('/recent'))return reply(route,{items:[]});
  if(path.endsWith('/defaults')){const t=req.postDataJSON(),n=t.notional_usdt||String(10000*Number(t.allocation_percent)/100);return reply(route,{...base,notional_usdt:n,notional:n,leverage:t.leverage||'75'});}
  if(path.endsWith('/preview')){const t=req.postDataJSON();draft={...base,...t,id:'a'.repeat(32),size:'10000',base_quantity:'100',leverage:t.leverage||'75',notional:t.notional_usdt||String(10000*Number(t.allocation_percent)/100),margin:'133.333',fee_reserve:'5',required:'138.333',state:'preview',created_ms:Date.now(),expires_ms:Date.now()+60000,maker_fee:'.0002',taker_fee:'.0005'};return reply(route,draft);}
  if(path.endsWith('/confirm'))return reply(route,{...draft,state:'confirmed'});
  if(path.endsWith('/'+('a'.repeat(32))))return reply(route,{...draft,state:'filled',protection:'verified'});
  return reply(route,{detail:'QA blocked unmocked manual API'},403);
 });
 await page.route('**/api/research/paper-desk/**',async route=>{
  const req=route.request(),path=new URL(req.url()).pathname;calls.push({mode:'paper',path,method:req.method()});
  if(path.endsWith('/live'))return reply(route,{active_account_id:'qa-paper',accounts:[{id:'qa-paper',name:'验收模拟账户'}]});
  if(path.endsWith('/defaults')){const t=req.postDataJSON(),n=t.notional_usdt||String(10000*Number(t.allocation_percent)/100);return reply(route,{...base,notional_usdt:n,notional:n,leverage:t.leverage||'75'});}
  if(path.endsWith('/preview')){const t=req.postDataJSON();return reply(route,{...base,...t,id:'b'.repeat(32),account_id:'qa-paper',quantity:'100',execution_price:'100',margin:'133.333',fee:'5',leverage:t.leverage||'75',expires_ms:Date.now()+15000});}
  if(path.endsWith('/open'))return reply(route,{position:{id:'qa-position'}});
  return reply(route,{detail:'QA blocked unmocked paper API'},403);
 });
 await page.route('**/api/monday-fib/order-context?**',route=>reply(route,{stop_loss:new URL(route.request().url()).searchParams.get('side')==='long'?'95':'105',candle_ms:Date.now()-900000,timeframe:'15m'}));
 await page.goto('http://127.0.0.1:8766/?drawer-qa='+Date.now()+'#monday-fib');
 await page.locator('#mf-quick-toggle').waitFor({state:'visible'});await page.locator('#mf-quick-toggle').click();
 await page.locator('[name=leverage]').waitFor();await page.waitForFunction(()=>document.querySelector('[name=leverage]')?.value==='75');
 const box=await page.locator('#mf-quick-panel').boundingBox();
 const dims=await page.locator('#mf-quick-panel').evaluate(e=>({client:e.clientWidth,scroll:e.scrollWidth,modal:e.getAttribute('aria-modal'),top:e.getBoundingClientRect().top,bottom:e.getBoundingClientRect().bottom,bodyOverflow:document.body.style.overflow}));
 await page.screenshot({path:'output/playwright/monday-drawer/mobile-paper.png'});
 await page.locator('[data-quick-mode=live]').click();await page.waitForFunction(()=>document.querySelector('[name=leverage]')?.value==='75');
 await page.locator('[data-fl-side=long]').click();await page.waitForFunction(()=>document.querySelector('[name=stop_loss]')?.value==='95');
 await page.locator('[data-fl-percent="50"]').click();await page.waitForFunction(()=>document.querySelector('[name=notional_usdt]')?.value==='5000');
 await page.locator('[name=leverage]').fill('50');await page.waitForFunction(()=>!document.querySelector('[data-fl-preview]').disabled);
 const afterLeverage=await page.locator('[name=notional_usdt]').inputValue();
 await page.locator('[name=stop_loss]').fill('90');
 const afterStop=await page.locator('[name=notional_usdt]').inputValue();
 await page.locator('[data-fl-preview]').click();await page.locator('[data-fl-confirm]').waitFor({state:'visible'});
 await page.locator('[data-fl-confirm]').click();await page.waitForFunction(()=>document.querySelector('[data-fl-review]')?.textContent.includes('已成交'));
 await page.screenshot({path:'output/playwright/monday-drawer/mobile-live-confirmed.png'});
 const confirms=calls.filter(c=>c.path.endsWith('/confirm')).length;
 await page.locator('[data-quick-close]').click();
 const restored=await page.evaluate(()=>({focus:document.activeElement.id,inert:document.querySelectorAll('[inert]').length,hidden:document.querySelector('#mf-quick-panel').hidden}));
 await page.locator('#mf-quick-toggle').click();await page.waitForFunction(()=>document.querySelector('[name=leverage]')?.value==='75');
 await page.setViewportSize({width:320,height:568});await page.screenshot({path:'output/playwright/monday-drawer/mobile-320.png'});
 const narrow=await page.locator('#mf-quick-panel').evaluate(e=>({client:e.clientWidth,scroll:e.scrollWidth,bottom:e.getBoundingClientRect().bottom}));
 await page.evaluate(()=>{Object.defineProperty(window.visualViewport,'height',{configurable:true,get:()=>300});Object.defineProperty(window.visualViewport,'offsetTop',{configurable:true,get:()=>30});window.visualViewport.dispatchEvent(new Event('resize'));});
 await page.locator('[name=stop_loss]').focus();
 await page.screenshot({path:'output/playwright/monday-drawer/mobile-keyboard.png'});
 const keyboard=await page.locator('#mf-quick-panel').evaluate(e=>({top:e.getBoundingClientRect().top,bottom:e.getBoundingClientRect().bottom,form:e.querySelector('.mf-ticket-scroll').getBoundingClientRect().height,footer:e.querySelector('.mf-ticket-footer').getBoundingClientRect().bottom}));
 await page.evaluate(()=>{delete window.visualViewport.height;delete window.visualViewport.offsetTop;window.visualViewport.dispatchEvent(new Event('resize'));});
 await page.keyboard.press('Escape');await page.setViewportSize({width:1440,height:1000});await page.locator('#mf-quick-toggle').click();await page.waitForFunction(()=>document.querySelector('[name=leverage]')?.value==='75');
 await page.screenshot({path:'output/playwright/monday-drawer/desktop.png'});
 const desktop=await page.locator('#mf-quick-panel').boundingBox();
 if(dims.client!==dims.scroll||dims.modal!=='true'||afterLeverage!=='5000'||afterStop!=='5000'||confirms!==1||restored.focus!=='mf-quick-toggle'||!restored.hidden||narrow.client!==narrow.scroll||keyboard.footer>keyboard.bottom+1||keyboard.form<80||errors.length)throw Error('Drawer invariant failed');
 return {box,dims,afterLeverage,afterStop,confirms,restored,narrow,keyboard,desktop,errors};
}
