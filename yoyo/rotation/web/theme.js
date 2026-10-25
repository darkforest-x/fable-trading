/* Binary appearance, applied before CSS paints. No data or account side effects.
 * Stitch 2acc1f5244714b8bb6ca736e54b25bc7; one sun/moon action across lazy routes. */
(() => {
  'use strict';
  const key='spike.theme';
  const valid=value=>value==='dark'?'dark':'light';
  function read(){try{return valid(window.localStorage.getItem(key))}catch{return 'light'}}
  let current=read();
  function apply(){
    document.documentElement.setAttribute('data-theme',current);
    document.querySelector('meta[name="theme-color"]')?.setAttribute('content',current==='dark'?'#101318':'#f7f8f6');
  }
  function toggle(){
    current=current==='dark'?'light':'dark';
    try{window.localStorage.setItem(key,current)}catch{/* Switching still works with restricted storage. */}
    apply();
  }
  window.SpikeTheme=Object.freeze({get:()=>current,toggle});
  apply();
  document.addEventListener('click',event=>{
    const button=event.target.closest?.('[data-theme-toggle]');
    if(button&&!button.disabled)toggle();
  });
  window.addEventListener('storage',event=>{if(event.key===key||event.key===null){current=read();apply()}});
})();
