/* Stitch 978ce337aef8424b9b21d7f2dc5cd0df: a host for existing order owners.
 * Opening, closing and changing environments never submit an order. The symbol
 * is captured once; monitor refreshes cannot mutate an in-progress ticket. */
(() => {
  'use strict';
  function create({panel,host,trigger,context,visibility,load,paper,live}) {
    let open=false,mode='paper',revision=0,captured=null,owner=null;
    const modes=Array.from(panel.querySelectorAll('[data-quick-mode]'));
    const status=panel.querySelector('[data-quick-status]');
    function release(){owner?.closeQuick();owner=null;}
    function sync(){panel.hidden=!open;trigger.setAttribute('aria-expanded',String(open));for(const b of modes)b.setAttribute('aria-pressed',String(mode===b.dataset.quickMode));panel.dataset.mode=mode;visibility(open);}
    async function select(value){
      if(!open||!['paper','live'].includes(value))return;
      const version=++revision;release();mode=value;sync();
      status.textContent=value==='live'?'实盘 · 均线手动账户':'模拟交易 · 与模拟开单页共用账户';host.textContent='正在加载开单面板…';
      try {
        await load(value);
        if(!open||version!==revision)return;
        const next=value==='paper'?paper():live();
        if(!next)throw Error('开单模块尚未就绪，请重新打开。');
        host.textContent='';next.openQuick(host,{...captured});owner=next;
      } catch(error){if(open&&version===revision){host.textContent=error.message;host.setAttribute('role','status');}}
    }
    function close(focus=true){
      if(!open)return;open=false;revision++;release();host.textContent='';sync();if(focus)trigger.focus({preventScroll:true});
    }
    function show(){
      if(open){close();return;}
      captured=context();if(!captured?.symbol)return;
      open=true;mode='paper';sync();select(mode);
    }
    trigger.addEventListener('click',show);
    panel.querySelector('[data-quick-close]').addEventListener('click',()=>close());
    for(const button of modes)button.addEventListener('click',()=>{if(mode!==button.dataset.quickMode)select(button.dataset.quickMode);});
    panel.addEventListener('keydown',event=>{if(event.key==='Escape'&&!event.defaultPrevented){event.preventDefault();event.stopPropagation();close();}});
    return {close,isOpen:()=>open};
  }
  if(typeof module!=='undefined')module.exports={create};
  if(typeof window!=='undefined')window.SpikeMondayQuick={create};
})();
