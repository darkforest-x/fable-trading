/* Stitch 978ce337aef8424b9b21d7f2dc5cd0df: a host for existing order owners.
 * Opening, closing and changing environments never submit an order. The symbol
 * is captured once; monitor refreshes cannot mutate an in-progress ticket. */
(() => {
  'use strict';
  function create({panel,host,trigger,context,visibility,load,paper,live}) {
    let open=false,mode='paper',revision=0,captured=null,owner=null;
    const doc=panel.ownerDocument,env=doc?.defaultView,backdrop=doc?.getElementById('mf-quick-backdrop');
    const grabber=panel.querySelector('[data-quick-grabber]');
    let locked=false,overflow='',blocked=[],gesture=null;
    const mobile=()=>env?.innerWidth<=760;
    function viewport(){
      if(!doc)return;
      const v=env.visualViewport;
      panel.classList.toggle('mf-keyboard',mobile()&&(v?.height||env.innerHeight)<450);
      panel.style.setProperty('--mf-visible-height',(v?.height||env.innerHeight)+'px');
      panel.style.setProperty('--mf-visible-bottom',Math.max(0,env.innerHeight-(v?.offsetTop||0)-(v?.height||env.innerHeight))+'px');
      if(open&&mobile()&&!locked){
        locked=true;overflow=doc.body.style.overflow;doc.body.style.overflow='hidden';
        let child=panel,parent=panel.parentElement;
        while(parent){for(const sibling of parent.children){if(sibling!==child&&sibling!==backdrop&&!sibling.inert){blocked.push(sibling);sibling.inert=true;}}child=parent;parent=parent.parentElement;}
        panel.setAttribute('role','dialog');panel.setAttribute('aria-modal','true');
      }else if((!open||!mobile())&&locked){
        locked=false;doc.body.style.overflow=overflow;for(const b of blocked)b.inert=false;blocked=[];
        panel.removeAttribute('aria-modal');panel.removeAttribute('role');
      }
      if(backdrop)backdrop.hidden=!(open&&mobile());
    }
    function listen(active){for(const source of [env,env?.visualViewport]){source?.[active?'addEventListener':'removeEventListener']('resize',viewport);source?.[active?'addEventListener':'removeEventListener']('scroll',viewport);}}
    backdrop?.addEventListener('click',()=>close());
    grabber?.addEventListener('pointerdown',e=>{if(!open||!mobile()||e.isPrimary===false)return;gesture={id:e.pointerId,x:e.clientX,y:e.clientY};grabber.setPointerCapture?.(e.pointerId);});
    grabber?.addEventListener('pointerup',e=>{if(gesture?.id!==e.pointerId)return;const g=gesture;gesture=null;grabber.releasePointerCapture?.(e.pointerId);if(e.clientY-g.y>70&&Math.abs(e.clientX-g.x)<60)close();});
    grabber?.addEventListener('pointercancel',()=>{gesture=null;});
    const modes=Array.from(panel.querySelectorAll('[data-quick-mode]'));
    const status=panel.querySelector('[data-quick-status]');
    function release(){owner?.closeQuick();owner=null;}
    function sync(){panel.hidden=!open;trigger.setAttribute('aria-expanded',String(open));for(const b of modes)b.setAttribute('aria-pressed',String(mode===b.dataset.quickMode));panel.dataset.mode=mode;visibility(open);viewport();}
    async function select(value){
      if(!open||!['paper','live'].includes(value))return;
      const version=++revision;release();mode=value;sync();
      status.textContent=value==='live'?'实盘 · 手动下单':'模拟交易 · 与模拟开单页共用账户';host.textContent='正在加载开单面板…';
      try {
        await load(value);
        if(!open||version!==revision)return;
        const next=value==='paper'?paper():live();
        if(!next)throw Error('开单模块尚未就绪，请重新打开。');
        host.textContent='';next.openQuick(host,{...captured});owner=next;
      } catch(error){if(open&&version===revision){host.textContent=error.message;host.setAttribute('role','status');}}
    }
    function close(focus=true){
      if(!open)return;open=false;revision++;release();host.textContent='';listen(false);sync();if(focus)trigger.focus({preventScroll:true});
    }
    function show(){
      if(open){close();return;}
      captured=context();if(!captured?.symbol)return;
      if(doc)panel.querySelector('.mf-quick-heading h2').textContent=captured.symbol.replace('-USDT-SWAP',' / USDT');
      open=true;mode='paper';listen(true);sync();select(mode);panel.querySelector('[data-quick-close]').focus?.({preventScroll:true});
    }
    trigger.addEventListener('click',show);
    panel.querySelector('[data-quick-close]').addEventListener('click',()=>close());
    for(const button of modes)button.addEventListener('click',()=>{if(mode!==button.dataset.quickMode)select(button.dataset.quickMode);});
    panel.addEventListener('keydown',event=>{
      if(event.key==='Tab'&&open&&mobile()){
        const items=Array.from(panel.querySelectorAll('button,input,select,textarea,a[href],[tabindex]')).filter(n=>!n.disabled&&n.tabIndex>=0&&n.getClientRects().length);
        const first=items[0],last=items.at(-1);
        if(event.shiftKey&&doc.activeElement===first){event.preventDefault();last?.focus();}
        else if(!event.shiftKey&&doc.activeElement===last){event.preventDefault();first?.focus();}
      }
      if(event.key==='Escape'&&!event.defaultPrevented){event.preventDefault();event.stopPropagation();close();}});
    return {close,isOpen:()=>open};
  }
  if(typeof module!=='undefined')module.exports={create};
  if(typeof window!=='undefined')window.SpikeMondayQuick={create};
})();
