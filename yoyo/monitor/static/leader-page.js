/* Standalone navigation entry for the existing leader observer. */
(() => {
  let active=false;
  window.SpikeLeaderPage={
    setActive(value){if(value===active)return;active=value;if(value)window.SpikeAutoLeaders.mount(document.getElementById('auto-leaders-workspace'));else window.SpikeAutoLeaders.unmount();},
    refresh(){if(active)window.SpikeAutoLeaders.mount(document.getElementById('auto-leaders-workspace'));}
  };
})();
