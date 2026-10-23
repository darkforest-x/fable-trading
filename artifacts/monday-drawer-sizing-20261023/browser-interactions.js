async page => {
 await page.setViewportSize({width:390,height:844});
 await page.route('**/api/manual-live/free/recent',r=>r.fulfill({contentType:'application/json',body:'{"items":[]}'}));
 await page.goto('http://127.0.0.1:8766/?interaction-qa='+Date.now()+'#monday-fib');
 await page.evaluate(()=>localStorage.setItem('spike:monday-fib:layout:v1',JSON.stringify({mobile:{quick:{x:0.8,y:0.2}}})));
 await page.reload();await page.locator('#mf-quick-toggle').click();await page.waitForFunction(()=>document.querySelector('[name=leverage]')?.value==='75');
 const initialInert=await page.evaluate(()=>document.querySelector('#mf-wallpaper').inert);
 await page.locator('[data-quick-mode=live]').click();await page.waitForFunction(()=>document.querySelector('[name=leverage]')?.value==='75');
 await page.route('**/api/monday-fib/order-context?**',async r=>{await new Promise(x=>setTimeout(x,1000));await r.fulfill({contentType:'application/json',body:JSON.stringify({stop_loss:'95',candle_ms:Date.now()-900000})});});
 await page.locator('[data-fl-auto-stop]').click();await page.locator('[name=stop_loss]').fill('91');
 await page.waitForTimeout(1100);
 const manualKept=await page.locator('[name=stop_loss]').inputValue();
 const pane=page.locator('#mf-quick-panel');
 await page.locator('[data-fl-preview]').click();await page.locator('[data-fl-confirm]').waitFor({state:'visible'});
 await page.locator('[data-fl-edit]').click();
 const edited=await page.locator('[data-fl-confirm]').isHidden();
 const first=page.locator('[data-quick-grabber]');await first.focus();await page.keyboard.press('Shift+Tab');
 const trap=await page.evaluate(()=>({inside:document.querySelector('#mf-quick-panel').contains(document.activeElement),element:document.activeElement.outerHTML.slice(0,120)}));
 await page.evaluate(()=>{Object.defineProperty(window.visualViewport,'height',{configurable:true,get:()=>320});Object.defineProperty(window.visualViewport,'offsetTop',{configurable:true,get:()=>80});window.visualViewport.dispatchEvent(new Event('resize'));});
 const keyboard=await pane.evaluate(e=>({top:e.getBoundingClientRect().top,bottom:e.getBoundingClientRect().bottom,footer:e.querySelector('.mf-ticket-footer').getBoundingClientRect().bottom,scrollHeight:e.querySelector('.mf-ticket-scroll').clientHeight}));
 await page.evaluate(()=>{delete window.visualViewport.height;delete window.visualViewport.offsetTop;window.visualViewport.dispatchEvent(new Event('resize'));});
 const handle=await first.boundingBox();await page.mouse.move(handle.x+handle.width/2,handle.y+8);await page.mouse.down();await page.mouse.move(handle.x+handle.width/2,handle.y+110,{steps:8});await page.mouse.up();
 const swipeClosed=await pane.isHidden();
 await page.locator('#mf-quick-toggle').click();await page.waitForFunction(()=>document.querySelector('[name=leverage]')?.value==='75');
 // Background taps close only through the explicitly bound backdrop.
 await page.locator('#mf-quick-backdrop').dispatchEvent('click');
 const backdropClosed=await pane.isHidden();
 const restored=await page.evaluate(()=>({chartInert:document.querySelector('#mf-wallpaper').inert,focus:document.activeElement.id}));
 return {initialInert,manualKept,edited,trap,keyboard,swipeClosed,backdropClosed,restored};
}
