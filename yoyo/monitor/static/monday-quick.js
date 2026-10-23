/* Stitch 978ce337aef8424b9b21d7f2dc5cd0df: a host for existing order owners.
 * Opening, closing and changing environments never submit an order. The symbol
 * is captured once; monitor refreshes cannot mutate an in-progress ticket. */
(() => {
  'use strict';
  // Direct manipulation owns only transform/opacity. The ticket body retains
  // its native scroller, and trading owners stay mounted until exit completes.
  function sheetMotion({panel,backdrop,env,active,dismiss}){
    let gesture=null,frame=null,timer=null,offset=0,height=1,settled=null;
    const reduced=()=>env?.matchMedia?.('(prefers-reduced-motion: reduce)').matches;
    const cancelTimer=()=>{if(timer!==null)(env?.clearTimeout||clearTimeout)(timer);timer=null;settled=null;};
    const cancelFrame=()=>{if(frame!==null)env?.cancelAnimationFrame?.(frame);frame=null;};
    const classes=(name,on)=>{panel.classList?.toggle(name,on);backdrop?.classList?.toggle(name,on);};
    function paint(){frame=null;panel.style?.setProperty('--mf-sheet-offset',offset+'px');backdrop?.style?.setProperty('opacity',String(Math.max(0,1-offset/height)));}
    function write(value){offset=Math.max(0,Math.min(height+32,value));if(env?.requestAnimationFrame){if(frame===null)frame=env.requestAnimationFrame(paint);}else paint();}
    function dropPointer(){const g=gesture;gesture=null;if(g&&panel.hasPointerCapture?.(g.id))panel.releasePointerCapture?.(g.id);}
    function finish(){cancelTimer();classes('mf-sheet-settling',false);}
    function complete(){const done=settled;finish();done?.();}
    function freeze(){
      const transform=env?.getComputedStyle?.(panel).transform||'',parts=transform.match(/^matrix(3d)?\(([^)]+)\)$/);
      if(parts){const numbers=parts[2].split(',').map(Number),y=numbers[parts[1]?13:5];if(Number.isFinite(y))offset=Math.max(0,y);}
      cancelFrame();cancelTimer();classes('mf-sheet-settling',false);paint();
    }
    function settle(value,done){
      cancelFrame();paint();dropPointer();classes('mf-sheet-dragging',false);
      if(reduced()||Math.abs(value-offset)<.5){offset=value;paint();done?.();return;}
      panel.getBoundingClientRect(); // Commit the dragged position before animating away from it.
      settled=done;classes('mf-sheet-settling',true);offset=value;paint();
      timer=(env?.setTimeout||setTimeout)(complete,280);
    }
    function rebound(){if(!gesture)return;settle(0);}
    panel.addEventListener('pointerdown',e=>{
      if(!active())return;
      if(e.isPrimary===false){rebound();return;}
      const grab=e.target.closest?.('[data-quick-grabber]'),heading=e.target.closest?.('.mf-quick-heading');
      if((e.button!=null&&e.button!==0)||(!grab&&!heading)||(!grab&&e.target.closest?.('button,input,select,textarea,a')))return;
      freeze();height=panel.getBoundingClientRect().height||1;
      gesture={id:e.pointerId,x:e.clientX,y:e.clientY,base:offset,lastY:e.clientY,at:e.timeStamp,velocity:0};
      classes('mf-sheet-dragging',true);panel.setPointerCapture?.(e.pointerId);e.preventDefault();
    });
    panel.addEventListener('pointermove',e=>{
      const g=gesture;if(!g||g.id!==e.pointerId)return;
      const dx=e.clientX-g.x,dy=e.clientY-g.y;
      if(Math.abs(dx)>Math.max(12,Math.abs(dy)*1.5)){rebound();return;}
      const dt=e.timeStamp-g.at;if(dt>0&&e.clientY!==g.lastY){g.velocity=(e.clientY-g.lastY)/dt;g.lastY=e.clientY;g.at=e.timeStamp;}
      write(g.base+dy);e.preventDefault();
    });
    panel.addEventListener('pointerup',e=>{
      const g=gesture;if(!g||g.id!==e.pointerId)return;
      const dy=e.clientY-g.y,dx=e.clientX-g.x,velocity=e.timeStamp-g.at<100?g.velocity:0;
      write(g.base+dy);cancelFrame();paint();dropPointer();
      if(dy>Math.abs(dx)*1.5&&(offset>=70||(dy>=28&&velocity>=.65)))dismiss();else settle(0);
    });
    for(const event of ['pointercancel','lostpointercapture'])panel.addEventListener(event,rebound);
    panel.addEventListener('transitionend',e=>{if(e.target===panel&&e.propertyName==='transform'&&timer!==null)complete();});
    return {
      exit(done){freeze();height=panel.getBoundingClientRect().height||1;settle(height+32,done);},
      reset(){dropPointer();cancelFrame();finish();classes('mf-sheet-dragging',false);offset=0;paint();},
      cancel:rebound,
    };
  }
  function create({panel,host,trigger,context,visibility,load,paper,live}) {
    let open=false,closing=false,mode='paper',revision=0,captured=null,owner=null;
    const doc=panel.ownerDocument,env=doc?.defaultView,backdrop=doc?.getElementById('mf-quick-backdrop');
    let locked=false,overflow='',blocked=[];
    const mobile=()=>env?.innerWidth<=760;
    const motion=sheetMotion({panel,backdrop,env,active:()=>open&&!closing&&mobile(),dismiss:()=>close()});
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
    function listen(active){for(const source of [env,env?.visualViewport]){source?.[active?'addEventListener':'removeEventListener']('resize',viewport);source?.[active?'addEventListener':'removeEventListener']('scroll',viewport);}env?.[active?'addEventListener':'removeEventListener']('blur',motion.cancel);}
    backdrop?.addEventListener('click',()=>close());
    const modes=Array.from(panel.querySelectorAll('[data-quick-mode]'));
    const status=panel.querySelector('[data-quick-status]');
    function release(){owner?.closeQuick();owner=null;}
    function sync(){panel.hidden=!open;trigger.setAttribute('aria-expanded',String(open));for(const b of modes)b.setAttribute('aria-pressed',String(mode===b.dataset.quickMode));panel.dataset.mode=mode;visibility(open);viewport();}
    async function select(value){
      if(!open||closing||!['paper','live'].includes(value))return;
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
      if(!open||closing&&focus)return;
      revision++;closing=true;panel.classList?.add('mf-sheet-closing');
      const finish=()=>{open=false;closing=false;motion.reset();panel.classList?.remove('mf-sheet-closing');release();host.textContent='';listen(false);sync();if(focus)trigger.focus({preventScroll:true});};
      // Page navigation/teardown must release immediately even during a drag.
      if(focus&&mobile())motion.exit(finish);else finish();
    }
    function show(){
      if(closing)return;
      if(open){close();return;}
      captured=context();if(!captured?.symbol)return;
      if(doc)panel.querySelector('.mf-quick-heading h2').textContent=captured.symbol.replace('-USDT-SWAP',' / USDT');
      motion.reset();open=true;mode='paper';listen(true);sync();select(mode);panel.querySelector('[data-quick-close]').focus?.({preventScroll:true});
    }
    trigger.addEventListener('click',show);
    panel.querySelector('[data-quick-close]').addEventListener('click',()=>close());
    for(const button of modes)button.addEventListener('click',()=>{if(mode!==button.dataset.quickMode)select(button.dataset.quickMode);});
    panel.addEventListener('keydown',event=>{
      if(closing){event.preventDefault();event.stopPropagation();return;}
      if(event.key==='Tab'&&open&&mobile()){
        const items=Array.from(panel.querySelectorAll('button,input,select,textarea,a[href],[tabindex]')).filter(n=>!n.disabled&&n.tabIndex>=0&&n.getClientRects().length);
        const first=items[0],last=items.at(-1);
        if(event.shiftKey&&doc.activeElement===first){event.preventDefault();last?.focus();}
        else if(!event.shiftKey&&doc.activeElement===last){event.preventDefault();first?.focus();}
      }
      if(event.key==='Escape'&&!event.defaultPrevented){event.preventDefault();event.stopPropagation();close();}});
    return {close,isOpen:()=>open};
  }
  if(typeof module!=='undefined')module.exports={create,sheetMotion};
  if(typeof window!=='undefined')window.SpikeMondayQuick={create};
})();
