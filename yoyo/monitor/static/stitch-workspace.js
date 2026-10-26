/* Stitch menu dismissal and keyboard behavior. Existing controls keep their IDs,
 * listeners and form state; no polling or business actions are introduced.
 */
(() => {
  'use strict';
  let pointerMenu=null;
  function close(menu,restore=false){
    if(!menu)return;
    menu.open=false;
    if(restore)menu.querySelector('summary')?.focus();
  }
  document.addEventListener('pointerdown',event=>{
    pointerMenu=event.target.closest?.('details.quiet-menu,details.rp-navigation,details.mf-home-menu')||null;
    document.querySelectorAll('details.quiet-menu[open], details.rp-navigation[open], details.mf-home-menu[open]').forEach(menu=>{
      if(!menu.contains(event.target))close(menu);
    });
  },true);
  document.addEventListener('pointercancel',()=>{pointerMenu=null;},true);
  document.addEventListener('click',event=>{
    const menu=event.target.closest?.('details.rp-navigation,details.mf-home-menu');
    const selected=menu&&event.target.closest('a'),pressed=pointerMenu;
    if(!selected&&!pressed)return;
    // Native anchor activation follows click dispatch, including same-route links.
    setTimeout(()=>{if(selected)close(menu);if(pointerMenu===pressed)pointerMenu=null;},0);
  },true);
  document.addEventListener('keydown',event=>{
    pointerMenu=null;
    if(event.key!=='Escape')return;
    const menu=document.querySelector('details.quiet-menu[open], details.rp-navigation[open], details.mf-home-menu[open]');
    if(menu){event.preventDefault();event.stopPropagation();close(menu,true);}
  },true);
  document.addEventListener('focusin',event=>{
    document.querySelectorAll('details.quiet-menu[open], details.rp-navigation[open], details.mf-home-menu[open]').forEach(menu=>{
      // Safari taps can focus the containing main between mousedown and click.
      // Hiding the menu here retargets that click to the background.
      if(menu!==pointerMenu&&!menu.contains(event.target))close(menu);
    });
  });
  window.addEventListener('hashchange',()=>{pointerMenu=null;document.querySelectorAll('details.quiet-menu[open], details.rp-navigation[open], details.mf-home-menu[open]').forEach(menu=>close(menu));});
})();
/* Account navigation stays distinct from signal records despite a shared module. */
(() => {
  function sync(){
    const raw=location.hash.slice(1),[view,query]=raw.split('?');
    const account=view==='signals'&&new URLSearchParams(query||'').get('pane')==='account';
    const maAccount=view==='yolo-detect'&&new URLSearchParams(query||'').get('tab')==='account';
    const home=view==='signals'&&new URLSearchParams(query||'').get('pane')==='reclaim';
    const family=home?'reclaim':view==='monday-fib'?'reclaim':account||maAccount||['manual','copier'].includes(view)?'yolo-detect?tab=account':
      ['paper-desk','paper'].includes(view)?'paper-desk':view==='signals'?'signals':view==='yolo-detect'?'yolo-detect':
      ['platform','research','datasets','factors','models','strategies','yolo','vision','experiments','backtests'].includes(view)?'research':
      ['learning','trading-books','orderflow','langlang','rektproof','knowledge','content'].includes(view)?'learning':
      ['system','notifications'].includes(view)?'system':'monday-fib';
    document.querySelectorAll('[data-terminal-route]').forEach(a=>{const active=a.dataset.terminalRoute===family;a.classList.toggle('active',active);if(active){a.setAttribute('aria-current','page');a.scrollIntoView?.({block:'nearest',inline:'nearest'});}else a.removeAttribute('aria-current');});
    const more=document.getElementById('workspace-search');
    more?.classList.toggle('active',['research','learning','system'].includes(family)||view==='auto-leaders'||view==='paper');
    const link=document.querySelector('[data-primary-accounts]');
    if(link){link.classList.toggle('active',account||['manual','copier'].includes(view));if(link.classList.contains('active'))link.setAttribute('aria-current','page');else link.removeAttribute('aria-current');}
    if(account){const signal=document.querySelector('.stitch-primary-nav [data-view="signals"]');signal?.classList.remove('active');signal?.removeAttribute('aria-current');}
    window.SpikeWorkbench?.navigate(view||'monday-fib');
  }
  window.addEventListener('hashchange',sync);sync();
})();
