/* Explicit per-lot close confirmation. Only the locked worker writes to OKX. */
(() => {
  'use strict';
  const terminal=new Set(['done','rejected','unknown','expired','superseded']);
  function receipt(value,lot,id){
    if(value?.account_scope!=='yolo_manual'||value.action!=='close'||value.lot_id!==lot||!value.id||id&&value.id!==id)throw Error('平仓回执归属不匹配，请核对账户');
    return value;
  }
  class CloseFlow {
    constructor(request){this.request=request;this.version=0;this.pending=new Map();this.reset();}
    reset(){this.version++;this.value=null;this.phase='idle';this.error='';this.lot=null;}
    async open(lot){
      this.reset();this.lot=lot;const v=this.version;this.phase='loading';
      const previous=this.pending.get(lot);
      try{const d=previous?await this.request('/'+previous):await this.request('/preview',{method:'POST',body:JSON.stringify({lot_id:lot})});
        if(v!==this.version)return;this.value=receipt(d,lot,previous);this.phase=previous?'tracking':'preview';
      }catch(e){if(v===this.version){this.error=e.message;this.phase='error';}}
    }
    async confirm(now=Date.now()){
      if(this.phase!=='preview'||!this.value)return;
      if(now>=this.value.expires_ms){this.phase='expired';return;}
      const v=this.version,d=this.value;this.phase='submitting';this.pending.set(this.lot,d.id);this.error='';
      try{const r=await this.request('/'+d.id+'/confirm',{method:'POST',body:JSON.stringify({acknowledgement:'CONFIRM-LIVE-CLOSE'})});if(v===this.version){this.value=receipt(r,d.lot_id,d.id);this.phase='tracking';}}
      catch(e){if(v===this.version){this.error=e.message;this.phase='tracking';}} // Unknown writes are read, never resent.
    }
    async read(){
      const v=this.version,d=this.value;if(this.phase!=='tracking'||!d)return;
      try{const r=receipt(await this.request('/'+d.id),d.lot_id,d.id);if(v!==this.version)return;this.value=r;this.error='';
        if(terminal.has(r.state)){this.phase=r.state;if(r.state!=='unknown')this.pending.delete(d.lot_id);}
      }catch(e){if(v===this.version)this.error=e.message;}
    }
  }
  if(typeof module!=='undefined')module.exports={CloseFlow,receipt};
  if(typeof document==='undefined')return;
  const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const messages={'exchange positions do not match owned signal lots':'账户持仓与系统分笔数量不一致，请先核对持仓。','unresolved execution intent; reconcile before new entries':'已有订单正在处理，请等待回执核对后重试。','unmanaged account position':'账户含自行管理的仓位，请先在交易所核对。'};
  async function request(path,options={}){
    const controller=new AbortController();let timer;
    const operation=(async()=>{const r=await fetch('/api/manual-live/close'+path,{...options,cache:'no-store',signal:controller.signal,headers:{'Content-Type':'application/json','X-Fable-Manual':'owner-confirmation'}});const d=await r.json();if(!r.ok)throw Error(messages[d.detail]||(typeof d.detail==='string'?d.detail:'平仓请求未通过，请重新核对'));return d;})();
    try{return await Promise.race([operation,new Promise((_,reject)=>{timer=setTimeout(()=>{if(!path.endsWith('/confirm'))controller.abort();reject(Error('暂未取得回执，正在核对，请勿重复提交'));},20000);})]);}
    finally{clearTimeout(timer);}
  }
  const flow=new CloseFlow(request);let dialog,timer,origin,scrollLock;
  const $=selector=>dialog.querySelector(selector);
  function init(){if(dialog)return;dialog=document.createElement('dialog');dialog.className='ma-close-dialog';dialog.setAttribute('aria-labelledby','ma-close-title');
    dialog.innerHTML='<header><div><small>OKX · 均线密集专用账户</small><h2 id="ma-close-title">确认实盘平仓</h2></div><button type="button" data-close-dismiss aria-label="关闭平仓面板">×</button></header><div data-close-body></div><footer><button type="button" data-close-dismiss>取消</button><button type="button" data-close-confirm>确认市价平仓</button></footer>';
    document.body.append(dialog);
    dialog.addEventListener('close',()=>{if(dialog.open)return;clearTimeout(timer);flow.reset();document.body.style.overflow=scrollLock;const target=origin?.isConnected?origin:document.querySelector('[data-ma-close="'+(origin?.dataset.maClose||'')+'"]');target?.focus({preventScroll:true});});
    dialog.addEventListener('click',async e=>{const b=e.target.closest('button');if(!b||b.disabled)return;if(b.hasAttribute('data-close-dismiss'))dialog.close();if(b.hasAttribute('data-close-confirm')){const v=flow.version,p=flow.confirm();render();await p;if(!dialog.open||v!==flow.version)return;render();watch();window.dispatchEvent(new Event('spike:manual-order-update'));}});
  }
  function render(){
    const d=flow.value,phase=flow.phase,expired=phase==='preview'&&Date.now()>=d?.expires_ms;
    const status={loading:'正在核对本笔持仓与保护单…',preview:'请核对本笔持仓',submitting:'正在提交平仓确认…',tracking:d?.state==='processing'?'正在核对平仓成交':d?.state==='confirmed'?'已确认，等待执行服务处理':'提交回执待核对，请勿重复提交',done:'本笔已平仓，成交已核对',rejected:'未执行平仓',unknown:'执行结果待核对，请查看开仓记录或交易所',expired:'预览已过期，请关闭后重新平仓',superseded:'预览已被更新，请关闭后重新核对',error:'暂时无法平仓'};
    $('[data-close-body]').innerHTML=(d?`<dl><div><dt>币种 / 方向</dt><dd>${esc(d.symbol.replace('-USDT-SWAP',''))} · ${d.side==='long'?'多头':'空头'}</dd></div><div><dt>本笔平仓数量</dt><dd>${esc(d.size)} 张</dd></div><div><dt>委托方式</dt><dd>市价</dd></div><div><dt>开仓记录</dt><dd>${esc(d.lot_id.slice(0,8))}</dd></div></dl><p>仅平掉这笔系统管理仓位，以实际成交价为准。执行服务会处理本笔保护单并核对成交。</p>`:'')+`<p class="ma-close-status" role="status">${expired?status.expired:status[phase]||'核对中'}${d?.reason?'<br>'+esc(d.reason):''}${flow.error?'<br>'+esc(flow.error):''}</p>`;
    const confirm=$('[data-close-confirm]');confirm.disabled=phase!=='preview'||expired;confirm.hidden=!['preview','submitting','loading'].includes(phase);confirm.textContent=phase==='submitting'?'正在提交…':'确认市价平仓';
    $('footer [data-close-dismiss]').textContent=['preview','loading','expired','error'].includes(phase)?'取消':'关闭';
    $('[data-close-body]').setAttribute('aria-busy',String(['loading','submitting'].includes(phase)));
  }
  async function watch(){clearTimeout(timer);if(!dialog?.open)return;const v=flow.version;await flow.read();if(!dialog.open||v!==flow.version)return;render();if(['preview','tracking'].includes(flow.phase))timer=setTimeout(watch,flow.phase==='preview'?1000:2000);if(terminal.has(flow.phase))window.dispatchEvent(new Event('spike:manual-order-update'));}
  window.SpikeManualClose={async open(id){init();if(dialog.open)return;origin=document.activeElement;scrollLock=document.body.style.overflow;document.body.style.overflow='hidden';dialog.showModal();const p=flow.open(id),v=flow.version;render();await p;if(dialog.open&&v===flow.version){render();watch();}}};
  window.addEventListener('hashchange',()=>{if(dialog?.open)dialog.close();});
})();
