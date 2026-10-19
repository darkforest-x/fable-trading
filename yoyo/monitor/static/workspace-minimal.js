/* Presentation-only progressive disclosure. Existing controls keep their IDs,
 * listeners and form state; no polling or business actions are introduced.
 */
(() => {
  'use strict';
  const rail=document.querySelector('.workspace-rail');
  const preferences=document.querySelector('.workspace-preferences');
  if(rail&&preferences){
    preferences.querySelector('summary').innerHTML='<span class="rail-icon" style="--icon:url(/static/icons/settings.svg)" aria-hidden="true"></span><span>界面</span>';
    rail.append(preferences);
    const status=document.querySelector('.sidebar-bottom');
    if(status){
      const fold=status.querySelector('[data-sidebar-fold]');
      if(fold)status.before(fold);
      preferences.querySelector('.quiet-menu-panel').append(status);
    }
  }
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
