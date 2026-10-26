/* Stitch 0dc09018ec5241fc90f42a3909fa0ef4: one icon and a clear glass ticket.
 * A nonmodal native dialog keeps the chart reachable for focus gestures.
 * Chart focus can suspend/reopen it without discarding an order draft.
 */
(() => {
  'use strict';
  function create({root,context}) {
    const trigger=document.createElement('button');
    trigger.type='button';trigger.className='rp-order-trigger rp-glass';
    trigger.setAttribute('aria-label','1.618 挂单');trigger.setAttribute('aria-controls','rp-order-panel');trigger.setAttribute('aria-expanded','false');
    trigger.innerHTML='<svg width="21" height="21" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" aria-hidden="true"><path d="M7 3v18M3 7l4-4 4 4M17 21V3m-4 14 4 4 4-4"/></svg>';
    const panel=document.createElement('dialog');panel.id='rp-order-panel';panel.className='rp-order-panel';panel.setAttribute('aria-label','1.618 挂单面板');
    panel.innerHTML='<button type="button" class="mf-sheet-grabber" data-quick-grabber aria-label="下滑收起挂单面板"><span></span></button><header class="mf-quick-heading"><div><h2>1.618 挂单</h2><span data-home-symbol></span></div><button type="button" data-quick-close aria-label="关闭挂单面板">×</button></header><nav class="rp-order-modes" aria-label="交易环境"><button type="button" data-quick-mode="paper" aria-pressed="true">模拟</button><button type="button" data-quick-mode="live" aria-pressed="false">实盘</button></nav><p data-quick-status role="status"></p><div class="mf-quick-content"></div>';
    root.append(trigger,panel);
    const host=panel.querySelector('.mf-quick-content'),status=panel.querySelector('[data-quick-status]');
    let opened=false,owner=null,mode='paper',revision=0,captured=null;
    const motion=window.SpikeMondayQuick.sheetMotion({panel,env:window,active:()=>opened&&innerWidth<=760,dismiss:()=>close()});
    function viewport(){const v=window.visualViewport;panel.style.setProperty('--order-height',(v?.height||innerHeight)+'px');panel.style.setProperty('--order-bottom',Math.max(0,innerHeight-(v?.offsetTop||0)-(v?.height||innerHeight))+'px');}
    function close(restoreFocus=true){revision++;opened=false;root.classList.remove('has-home-ticket');owner?.closeQuick();owner=null;motion.reset();panel.close();host.textContent='';trigger.setAttribute('aria-expanded','false');if(restoreFocus)trigger.focus({preventScroll:true});}
    async function select(value){
      const generation=++revision;mode=value;owner?.closeQuick();owner=null;
      panel.dataset.mode=mode;
      for(const b of panel.querySelectorAll('[data-quick-mode]'))b.setAttribute('aria-pressed',String(b.dataset.quickMode===mode));
      status.textContent=mode==='live'?'实盘 · 手动专用账户':'模拟 · 手动账户';host.textContent='正在加载…';
      try{await window.SpikeModules.ensure('monday-live');if(!opened||generation!==revision)return;
        owner=mode==='live'?window.SpikeMondayLive:window.SpikeMondayPaper;owner.openQuick(host,{...captured,entryPolicy:'monday_1618'});
      }catch(e){if(opened&&generation===revision)host.textContent=e.message;}
    }
    trigger.addEventListener('click',()=>{if(opened){close();return;}captured=context();if(!captured?.symbol)return;
      opened=true;root.classList.add('has-home-ticket');motion.reset();viewport();panel.querySelector('[data-home-symbol]').textContent=captured.symbol.replace('-USDT-SWAP',' / USDT');panel.show();trigger.setAttribute('aria-expanded','true');select('paper');panel.querySelector('[data-quick-close]').focus({preventScroll:true});});
    panel.querySelector('[data-quick-close]').onclick=()=>close();
    panel.addEventListener('keydown',e=>{if(e.key==='Escape'){e.preventDefault();e.stopPropagation();close();}});
    for(const b of panel.querySelectorAll('[data-quick-mode]'))b.onclick=()=>{if(mode!==b.dataset.quickMode)select(b.dataset.quickMode);};
    for(const source of [window,window.visualViewport]){source?.addEventListener('resize',viewport);source?.addEventListener('scroll',viewport);}
    return {close:()=>close(false),refresh(){trigger.disabled=!context()?.symbol;}};
  }
  window.SpikeHomeTicket={create};
})();
