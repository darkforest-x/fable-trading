/* Shared navigation preference and page fullscreen lifecycle.
 * Keep the existing workspace/TradingView DOM mounted while expanding only
 * the current workspace within the browser viewport, preserving browser chrome.
 */
(() => {
  'use strict';
  const key='spike-sidebar-collapsed-v1';
  const mobile=window.matchMedia('(max-width:700px)');
  const sidebar=document.getElementById('main-sidebar');
  const context=sidebar?.querySelector('.workspace-context');
  const toggle=document.getElementById('nav-toggle');
  const backdrop=document.getElementById('nav-backdrop');
  const closeNavigation=document.getElementById('nav-close');
  let collapsed=false, target=null;
  let isolated=[], navigationIsolation=[], closedDrawerIsolation=[], drawerOpen=false;
  try {collapsed=localStorage.getItem(key)==='true';} catch (_) {}
  function navigationControls() {
    return Array.from(sidebar?.querySelectorAll('a[href],button:not([disabled]),input:not([disabled]),select:not([disabled]),textarea:not([disabled]),summary,[tabindex="0"]')||[])
      .filter(node=>node.getClientRects().length&&!node.closest('[inert]'));
  }
  function syncNavigation() {
    const fixedRail=document.body.dataset?.design==='stitch-obsidian';
    const open=mobile.matches?document.body.classList.contains('nav-open'):fixedRail||!collapsed;
    const nextDrawer=mobile.matches&&open&&!target;
    if(nextDrawer!==drawerOpen){
      drawerOpen=nextDrawer;
      if(drawerOpen){
        // A drawer is modal: preserve the prior inert state independently of fullscreen.
        for(const node of document.querySelectorAll('.workspace,.skip-link')){
          navigationIsolation.push([node,node.inert]);node.inert=true;
        }
        sidebar?.setAttribute('role','dialog');sidebar?.setAttribute('aria-modal','true');
      }else{
        for(const [node,wasInert] of navigationIsolation)node.inert=wasInert;
        navigationIsolation=[];
        sidebar?.removeAttribute('role');sidebar?.removeAttribute('aria-modal');
      }
    }
    document.body.classList.toggle('sidebar-collapsed',!fixedRail&&collapsed);
    // The compact mobile layout keeps context tabs visible outside the drawer.
    // Inert on their shared ancestor would swallow both taps and keyboard input.
    for(const [node,wasInert] of closedDrawerIsolation)node.inert=wasInert;
    closedDrawerIsolation=[];
    const visibleContext=mobile.matches&&fixedRail&&!open&&Boolean(context?.getClientRects().length);
    if(visibleContext){
      for(const node of sidebar.children){
        if(node===context)continue;
        closedDrawerIsolation.push([node,node.inert]);node.inert=true;
      }
    }
    if(sidebar)sidebar.inert=(!open&&!visibleContext)||Boolean(target);
    if(backdrop)backdrop.hidden=!mobile.matches||!open;
    if(toggle){
      const label=mobile.matches?(open?'关闭导航菜单':'打开导航菜单'):(open?'收起左侧菜单':'展开左侧菜单');
      toggle.setAttribute('aria-expanded',String(open));toggle.setAttribute('aria-label',label);toggle.title=label;
    }
  }
  function setMobileNavigation(open,{restoreFocus=true}={}) {
    const wasOpen=drawerOpen;
    document.body.classList.toggle('nav-open',mobile.matches&&open);
    syncNavigation();
    if(drawerOpen&&!wasOpen){
      (sidebar?.querySelector('[aria-current="page"]')||closeNavigation)?.focus({preventScroll:true});
    }else if(wasOpen&&!drawerOpen&&restoreFocus){toggle?.focus({preventScroll:true});}
  }
  function toggleNavigation() {
    if(mobile.matches){setMobileNavigation(!document.body.classList.contains('nav-open'));return;}
    collapsed=!collapsed;
    try {localStorage.setItem(key,String(collapsed));} catch (_) {}
    syncNavigation();
    if(!collapsed)sidebar?.querySelector('[aria-current="page"]')?.focus({preventScroll:true});
  }
  function sync() {
    for(const button of document.querySelectorAll('[data-workspace-fullscreen]')){
      const active=Boolean(target)&&button.closest('#'+target.id)===target;
      button.setAttribute('aria-pressed',String(active));
      button.setAttribute('aria-label',active?'退出交易页全屏':'交易页全屏');
      button.title=active?'退出全屏（Esc）':'全屏显示交易页';
      const label=button.querySelector('[data-fullscreen-label]');if(label)label.textContent=active?'退出全屏':'全屏';
    }
  }
  function isolate(element) {
    for(let node=element;node.parentElement;node=node.parentElement){
      for(const sibling of node.parentElement.children){
        if(sibling===node||sibling.hasAttribute?.('data-global-alert')||['SCRIPT','STYLE','LINK','HEAD'].includes(sibling.tagName))continue;
        isolated.push([sibling,sibling.inert]);sibling.inert=true;
      }
    }
  }
  function leaveFullscreen() {
    if(!target)return;
    const previous=target;target=null;
    previous.classList.remove('is-workspace-fullscreen');
    document.body.classList.remove('workspace-fullscreen-open');
    for(const [node,wasInert] of isolated)node.inert=wasInert;isolated=[];
    syncNavigation();sync();
    previous.querySelector('[data-workspace-fullscreen]')?.focus({preventScroll:true});
  }
  function enterFullscreen(element) {
    if(target){leaveFullscreen();return;}
    target=element;
    setMobileNavigation(false);isolate(element);
    element.classList.add('is-workspace-fullscreen');document.body.classList.add('workspace-fullscreen-open');sync();
    element.querySelector('[data-workspace-fullscreen]')?.focus({preventScroll:true});
    // Expand this workspace inside the browser viewport; retain browser chrome.
  }
  document.addEventListener('click',event=>{
    if(event.target.closest?.('[data-sidebar-fold]')){toggleNavigation();toggle?.focus({preventScroll:true});}
    if(event.target.closest?.('#nav-close'))setMobileNavigation(false);
    if(event.target.closest?.('[data-workspace-fullscreen]')){
      const element=document.getElementById('paper-desk-workspace');if(element)enterFullscreen(element);
    }
  });
  document.addEventListener('keydown',event=>{
    if(drawerOpen&&event.key==='Tab'){
      const controls=navigationControls(),first=controls[0],last=controls[controls.length-1];
      if(!controls.length)return;
      if(event.shiftKey&&(document.activeElement===first||!controls.includes(document.activeElement))){event.preventDefault();last.focus();}
      else if(!event.shiftKey&&(document.activeElement===last||!controls.includes(document.activeElement))){event.preventDefault();first.focus();}
    }
    if(event.key==='Escape'&&target&&!document.querySelector('dialog[open]'))leaveFullscreen();
    // Search stays in the visible page. Never steal text, composition, or modal input.
    const origin=event.composedPath?.()[0]||document.activeElement;
    if(event.key!=='/'||event.defaultPrevented||event.metaKey||event.ctrlKey||event.altKey||event.isComposing||drawerOpen||
       ['INPUT','TEXTAREA','SELECT'].includes(origin?.tagName)||origin?.isContentEditable||origin?.closest?.('[role="textbox"]')||document.querySelector('dialog[open]'))return;
    const search=Array.from(document.querySelectorAll('.view-panel:not(.hidden) input[type="search"]'))
      .find(node=>!node.disabled&&!node.closest('[inert]')&&node.getClientRects().length);
    if(search){event.preventDefault();search.focus();}
  });
  mobile.addEventListener('change',()=>setMobileNavigation(false));
  window.addEventListener('storage',event=>{
    if(event.key===key||event.key===null){collapsed=event.newValue==='true';syncNavigation();}
  });
  window.SpikeWorkspaceDisplay={toggleNavigation,setMobileNavigation,syncNavigation,sync,leaveFullscreen};
  syncNavigation();sync();
})();
