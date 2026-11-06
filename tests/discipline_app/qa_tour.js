// QA-only simulator tour for the synthetic iPhone fixture (serve_iphone_ui_fixture.py).
// Injected by vite.config.ts only when VITE_SPIKE_QA_TOUR=1; release bundles never contain it.
// It connects with the fixture's constant token, then walks every page on a fixed 6 s clock so
// `xcrun simctl io screenshot` can capture each state. It never presses buy/sell/close/cancel.
(()=>{
 const TOKEN='ui-fixture-only-not-an-account-secret-123456',STEP=6000;
 const q=(s,root=document)=>root.querySelector(s);
 const byText=(sel,text)=>[...document.querySelectorAll(sel)].find(e=>e.textContent.trim().includes(text));
 const nav=name=>byText('nav.bottom-nav button',name)?.click();
 const forbidden=/开多|开空|加仓|平仓|撤单|确认实盘|确认模拟|止损移|止损到/;
 const click=el=>{if(el&&!forbidden.test(el.textContent))el.click()};
 const setInput=(el,value)=>{Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(el,value);el.dispatchEvent(new Event('input',{bubbles:true}))};
 const steps=[
  ['connect',()=>{nav('统计');setTimeout(()=>{click(q('button[aria-label="设置"]'));setTimeout(()=>{const d=q('form.connection-form details');if(d)d.open=true;const i=q('input[aria-label="服务访问令牌"]');if(i){setInput(i,TOKEN);click(q('form.connection-form button.primary'))}},700)},700)}],
  ['1.618',()=>nav('1.618')],
  ['合约',()=>nav('合约')],
  ['监控-模拟盘',()=>{nav('监控');setTimeout(()=>click(byText('.monitor-segments button','模拟盘')),300)}],
  ['监控-模拟盘-账户',()=>click(q('.monitor-account'))],
  ['监控-报警',()=>{document.dispatchEvent(new KeyboardEvent('keydown',{key:'Escape',bubbles:true}));setTimeout(()=>click(byText('.monitor-segments button','报警')),900)}],
  ['监控-报警-展开',()=>click(q('.coin-chips button.more'))],
  ['监控-云带',()=>click(byText('.monitor-segments button','云带'))],
  ['持仓',()=>nav('持仓')],
  ['统计',()=>nav('统计')],
  ['纪律',()=>click(q('.discipline-link'))],
  ['设置',()=>{click(q('.page-back'));setTimeout(()=>click(q('button[aria-label="设置"]')),900)}],
  ['深色-合约',()=>{click(q('.page-back'));setTimeout(()=>{nav('合约');setTimeout(()=>click(q('.theme-toggle')),600)},700)}],
  ['深色-监控',()=>{nav('监控');setTimeout(()=>click(byText('.monitor-segments button','模拟盘')),300)}],
  ['深色-1.618',()=>nav('1.618')],
 ];
 const start=performance.now()+2500;
 steps.forEach(([name,run],i)=>setTimeout(()=>{document.documentElement.dataset.qaStep=name;try{run()}catch(e){console.error('qa-tour',name,e)}},start-performance.now()+i*STEP));
})();
