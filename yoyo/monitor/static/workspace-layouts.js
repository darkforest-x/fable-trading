/* Linear workspace navigation and reading tools. Presentation-only: no account,
 * model, scheduler, or trading mutations. Existing route buttons remain owners
 * of module lifecycle; forms/charts are never cloned or re-mounted here.
 */
(() => {
  'use strict';
  const GROUPS = {
    market: {title:'1.618', views:['monday-fib','auto-leaders']},
    signals: {title:'信号与检测', views:['signals','yolo-detect']},
    accounts: {title:'账户与交易', views:['manual','copier']},
    simulation: {title:'模拟交易', views:['paper-desk','paper']},
    research: {title:'研究工作区', views:['platform','research','datasets','factors','models','strategies','yolo','vision','experiments','backtests']},
    learn: {title:'学习与复盘', views:['learning','trading-books','orderflow','langlang','rektproof','knowledge','content']},
    manage: {title:'运行管理', views:['notifications','system']},
  };
  const legacyViews=['watch','warmup','shadow'];
  const groupFor = view => legacyViews.includes(view)?'manage':Object.keys(GROUPS).find(key=>GROUPS[key].views.includes(view)) || 'market';
  const familyFor = view => ['market','auto-leaders','monday-fib','signals','yolo-detect','paper-desk','copier'].includes(view)?'terminal':
    ['trading-books','orderflow','langlang','rektproof'].includes(view)?'reader':
    ['learning','knowledge','content'].includes(view)?'collection':
    ['notifications','system'].includes(view)?'settings':'registry';
  if(typeof module!=='undefined')module.exports={GROUPS,groupFor,familyFor};
  if(typeof document==='undefined')return;
  const main=document.getElementById('main'), sidebar=document.getElementById('main-sidebar');
  if(!main||!sidebar)return;
  const filter=document.getElementById('workspace-nav-filter'), outline=document.getElementById('workspace-outline');
  const rail=[...document.querySelectorAll('[data-workspace-group]')];
  let current='', group='', outlineKey='', timer, sections=[];
  const last={};
  const read=(key,fallback)=>{try{return localStorage.getItem(key)||fallback;}catch{return fallback;}};
  const write=(key,value)=>{try{localStorage.setItem(key,value);}catch{}};
  function applyFilter(){
    const query=filter.value.trim().toLowerCase();let count=0;
    sidebar.querySelectorAll('.nav-item[data-view]').forEach(button=>{
      const row=button.closest('.nav-order-row')||button;
      const match=button.textContent.toLowerCase().includes(query);
      row.hidden=!match;
      if(match&&groupFor(button.dataset.view)===group)count++;
    });
    document.getElementById('workspace-nav-empty').hidden=count>0;
  }
  function navigate(view){
    if(!view)return;
    const routeParams=new URLSearchParams(location.hash.split('?')[1]||'');
    const accountRoute=(view==='signals'&&routeParams.get('pane')==='account')||(view==='yolo-detect'&&routeParams.get('tab')==='account');
    const nextGroup=accountRoute?'accounts':groupFor(view),changed=current!==view;
    document.body.dataset.accountRoute=String(accountRoute);
    const accountLink=document.querySelector('[data-primary-accounts]');
    if(accountLink){const selected=accountRoute||nextGroup==='accounts';accountLink.classList.toggle('active',selected);if(selected)accountLink.setAttribute('aria-current','page');else accountLink.removeAttribute('aria-current');}
    if(accountRoute){document.getElementById('page-title').textContent='交易与账户';document.getElementById('page-description').textContent='实盘权益、持仓、保护单与执行记录';const signalLink=document.querySelector('.stitch-primary-nav [data-view="signals"]');signalLink?.classList.remove('active');signalLink?.removeAttribute('aria-current');}
    current=view;group=nextGroup;last[group]=legacyViews.includes(view)?'system':view;
    document.body.dataset.workspace=view;
    document.body.dataset.workspaceGroup=group;
    document.body.dataset.workspaceFamily=familyFor(view);
    document.getElementById('workspace-group-title').textContent=GROUPS[group].title;
    rail.forEach(button=>button.setAttribute('aria-pressed',String(button.dataset.workspaceGroup===group)));
    sidebar.querySelectorAll('[data-nav-group]').forEach(node=>{node.hidden=node.dataset.navGroup!==group;});
    sidebar.querySelectorAll('.nav-item[href]').forEach(link=>{
      const [target,query='']=link.getAttribute('href').slice(1).split('?');
      const selected=target===view&&[...new URLSearchParams(query)].every(([key,value])=>routeParams.get(key)===value);
      link.classList.toggle('active',selected);
      if(selected)link.setAttribute('aria-current','page');else link.removeAttribute('aria-current');
    });
    if(changed){filter.value='';outlineKey='';outline.replaceChildren();outline.hidden=true;applyFilter();}
    // Route attributes determine whether mobile context navigation is visible.
    window.SpikeWorkspaceDisplay?.syncNavigation();
    observePanel();schedule();
  }
  function enhance(){
    const panel=main.querySelector('.view-panel:not(.hidden)');if(!panel)return;
    // Focusable overflow wrappers let keyboard users reach wide data tables.
    panel.querySelectorAll('.research-table-wrap,.ledger-table-wrap,.bk-table-scroll,.notification-matrix-wrap').forEach(el=>{
      if(el.scrollWidth>el.clientWidth){el.tabIndex=0;el.setAttribute('aria-label','数据表，可横向滚动');}
    });
    // Local headings and tabs already describe the page; do not duplicate a live index.
    if(!outline.hidden){outline.replaceChildren();outline.hidden=true;}

  }
  function schedule(){clearTimeout(timer);timer=setTimeout(enhance,100);}
  rail.forEach(button=>button.addEventListener('click',()=>{
    const key=button.dataset.workspaceGroup,target=last[key]||GROUPS[key].views[0];
    sidebar.querySelector(`[data-view="${target}"]`)?.click();
  }));
  rail.forEach((button,index)=>button.addEventListener('keydown',event=>{
    if(!['ArrowUp','ArrowDown','Home','End'].includes(event.key))return;
    event.preventDefault();const next=event.key==='Home'?0:event.key==='End'?rail.length-1:(index+(event.key==='ArrowDown'?1:-1)+rail.length)%rail.length;rail[next].focus();
  }));
  filter.addEventListener('input',applyFilter);
  filter.addEventListener('keydown',event=>{
    if(event.key==='Escape'){filter.value='';applyFilter();}
    if(event.key==='Enter'){
      const button=[...sidebar.querySelectorAll(`[data-nav-group="${group}"] .nav-item`)].find(el=>el.getClientRects().length);
      if(button){event.preventDefault();button.click();}
    }
  });
  outline.addEventListener('click',event=>{
    const button=event.target.closest('[data-outline]');if(!button)return;
    const target=sections[Number(button.dataset.outline)];if(!target?.isConnected)return;
    if(target.tagName==='SUMMARY')target.parentElement.open=true;
    target.scrollIntoView({block:'start',behavior:'instant'});target.tabIndex=-1;target.focus({preventScroll:true});
    window.SpikeWorkspaceDisplay?.setMobileNavigation(false,{restoreFocus:false});
  });
  const notificationPanes={channels:'notification-channel-cards',routes:'notification-routes-section'};
  function notificationPane(value){
    for(const [key,id] of Object.entries(notificationPanes)){const el=document.getElementById(id);if(el)el.hidden=key!==value;}
    document.querySelectorAll('[data-notification-pane]').forEach(button=>button.setAttribute('aria-pressed',String(button.dataset.notificationPane===value)));
    schedule();
  }
  document.querySelectorAll('[data-notification-pane]').forEach(button=>button.addEventListener('click',()=>notificationPane(button.dataset.notificationPane)));
  notificationPane('channels');
  const density=document.getElementById('workspace-density');
  function setDensity(value){document.body.dataset.density=value;density.textContent=value==='compact'?'紧凑':'舒展';density.setAttribute('aria-pressed',String(value==='compact'));}
  setDensity(read('spike.workspace.density','compact')==='comfortable'?'comfortable':'compact');
  density.addEventListener('click',()=>{const value=document.body.dataset.density==='compact'?'comfortable':'compact';setDensity(value);write('spike.workspace.density',value);});
  const structural='table,.research-table-wrap,.ledger-table-wrap,.bk-table-scroll,.notification-matrix-wrap';
  let observedPanel=null;
  const observer=new MutationObserver(records=>{
    if(records.some(r=>[...r.addedNodes,...r.removedNodes].some(n=>n.nodeType===1&&(n.matches(structural)||n.querySelector(structural)))))schedule();
  });
  function observePanel(){const panel=main.querySelector('.view-panel:not(.hidden)');if(panel===observedPanel)return;observer.disconnect();observedPanel=panel;if(panel)observer.observe(panel,{childList:true,subtree:true});}
  main.addEventListener('toggle',()=>schedule(),true);
  window.addEventListener('resize',schedule,{passive:true});
  window.SpikeWorkbench={navigate,refresh:schedule,GROUPS};
  navigate(location.hash.slice(1).split('?')[0]||'market');
})();
