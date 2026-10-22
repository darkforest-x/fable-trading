/* Stitch menu dismissal and keyboard behavior. Existing controls keep their IDs,
 * listeners and form state; no polling or business actions are introduced.
 */
(() => {
  'use strict';
  function close(menu,restore=false){
    if(!menu)return;
    menu.open=false;
    if(restore)menu.querySelector('summary')?.focus();
  }
  document.addEventListener('click',event=>{
    document.querySelectorAll('details.quiet-menu[open]').forEach(menu=>{
      if(!menu.contains(event.target))close(menu);
    });
  });
  document.addEventListener('keydown',event=>{
    if(event.key!=='Escape')return;
    const menu=event.target.closest?.('details.quiet-menu[open]');
    if(menu){event.preventDefault();event.stopPropagation();close(menu,true);}
  },true);
  document.addEventListener('focusin',event=>{
    document.querySelectorAll('details.quiet-menu[open]').forEach(menu=>{
      if(!menu.contains(event.target))close(menu);
    });
  });
  window.addEventListener('hashchange',()=>document.querySelectorAll('details.quiet-menu[open]').forEach(menu=>close(menu)));
})();
/* Account navigation stays distinct from signal records despite a shared module. */
(() => {
  function sync(){
    const raw=location.hash.slice(1),[view,query]=raw.split('?');
    const account=view==='signals'&&new URLSearchParams(query||'').get('pane')==='account';
    const maAccount=view==='yolo-detect'&&new URLSearchParams(query||'').get('tab')==='account';
    const family=view==='monday-fib'?'monday-fib':account||maAccount||['manual','copier'].includes(view)?'yolo-detect?tab=account':
      ['paper-desk','paper'].includes(view)?'paper-desk':view==='signals'?'signals':view==='yolo-detect'?'yolo-detect':
      ['platform','research','datasets','factors','models','strategies','yolo','vision','experiments','backtests'].includes(view)?'research':
      ['learning','trading-books','orderflow','langlang','rektproof','knowledge','content'].includes(view)?'learning':
      ['system','notifications'].includes(view)?'system':'market';
    document.querySelectorAll('[data-terminal-route]').forEach(a=>{const active=a.dataset.terminalRoute===family;a.classList.toggle('active',active);if(active){a.setAttribute('aria-current','page');a.scrollIntoView?.({block:'nearest',inline:'nearest'});}else a.removeAttribute('aria-current');});
    const link=document.querySelector('[data-primary-accounts]');
    if(link){link.classList.toggle('active',account||['manual','copier'].includes(view));if(link.classList.contains('active'))link.setAttribute('aria-current','page');else link.removeAttribute('aria-current');}
    if(account){const signal=document.querySelector('.stitch-primary-nav [data-view="signals"]');signal?.classList.remove('active');signal?.removeAttribute('aria-current');}
    window.SpikeWorkbench?.navigate(view||'market');
  }
  window.addEventListener('hashchange',sync);sync();
})();
