/* Stitch 57fe402b0685484680381d5f41a514ce: paper-only partial/full close sheet.
 * Estimates follow the existing engine's entry-notional fee convention; the
 * server owns fresh pricing, revision checks and durable request deduplication.
 */
(() => {
  'use strict';
  const finite=Number.isFinite;
  function estimate(p,percentage){
    if(!p||p.taken!==1||p.stage==='closed'||!finite(p.remaining_notional)||p.remaining_notional<=0||!finite(p.entry)||p.entry<=0||!finite(percentage)||percentage<=0||percentage>100)return null;
    if([3060,5060,6060,6061,7060].includes(p.track)&&(!finite(p.margin_leverage)||p.margin_leverage<=0))return null;
    const amount=p.remaining_notional*percentage/100,quantity=amount/p.entry;
    const rawPnl=finite(p.mark)&&p.mark>0?p.side*(p.mark-p.entry)*quantity-amount*(p.track===7060?.0017:.0007):null;
    return {amount,quantity,remaining:(p.remaining_notional-amount)/p.entry,entryFee:amount*.0002,exitFee:amount*.0005,
      pnl:rawPnl===null?null:[3060,5060,6060,6061,7060].includes(p.track)?Math.max(rawPnl,-amount/p.margin_leverage):rawPnl};
  }
  function createSubmitter({send,uuid=()=>crypto.randomUUID()}){
    let pending=null,key='',id='';
    return {submit(position,percentage){
      if(pending)return pending;
      if(!estimate(position,percentage))return Promise.reject(Error('请选择有效的平仓比例'));
      const next=position.id+'|'+position.close_revision+'|'+percentage;
      if(key!==next){key=next;id=uuid();}
      const payload={percentage,expected_revision:position.close_revision,request_id:id};
      pending=Promise.resolve().then(()=>send(position.id,payload)).finally(()=>{pending=null;});
      return pending;
    }};
  }
  const api={estimate,createSubmitter};
  if(typeof module!=='undefined'&&module.exports){module.exports=api;return;}
  const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const number=v=>finite(v)?v.toLocaleString('en-US',{maximumSignificantDigits:8}):'—';
  const money=v=>finite(v)?`${v>0?'+':''}${v.toLocaleString('en-US',{minimumFractionDigits:2,maximumFractionDigits:4})}`:'—';
  function create({root,onChanged}){
    const dialog=document.createElement('dialog');dialog.className='rp-close rp-glass';dialog.setAttribute('aria-labelledby','rp-close-title');
    dialog.innerHTML=`<header class="rp-record-heading" data-yd-sheet-drag><strong id="rp-close-title">模拟平仓</strong><button type="button" data-close-action="cancel" aria-label="关闭模拟平仓">×</button></header><div class="rp-close-body"><p data-close="identity"></p><div class="rp-close-presets" role="group" aria-label="选择平仓比例">${[25,50,75,100].map(p=>`<button type="button" data-close-percent="${p}">${p===100?'全平':p+'%'}</button>`).join('')}</div><label class="rp-close-percent">占剩余仓位 <span><input type="number" aria-label="自定义平仓比例" min="0.01" max="100" step="any" value="100" inputmode="decimal"> %</span></label><dl data-close="estimate"></dl><p class="rp-close-hint">按最新报价模拟成交；净盈亏含分摊开仓费与本次平仓费。</p><p data-close="status" role="status"></p><div class="rp-close-actions"><button type="button" data-close-action="cancel">取消</button><button type="button" data-close-action="submit">确认模拟平仓</button></div></div>`;
    root.append(dialog);
    let position=null,busy=false,success=false,opener=null,revision=0;
    const el=id=>dialog.querySelector(`[data-close="${id}"]`),input=dialog.querySelector('input'),submit=dialog.querySelector('[data-close-action="submit"]');
    const sheet=window.SpikeYoloPreviewSheet.bind({panel:dialog,dismiss:()=>hide()});
    const sender=createSubmitter({send:async(id,payload)=>{
      const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),20000);
      try{const response=await fetch(`/api/monday-reclaim-paper/positions/${id}/close`,{method:'POST',headers:{'content-type':'application/json','x-spike-action':'close-reclaim-paper'},body:JSON.stringify(payload),signal:controller.signal});
        const data=await response.json();if(!response.ok)throw Error(typeof data.detail==='string'?data.detail:'平仓请求未通过校验');return data;
      }catch(error){if(error.name==='AbortError'||error instanceof TypeError)throw Error('暂未收到成交确认，请点重试确认；同一请求不会重复减仓。');throw error;}
      finally{clearTimeout(timer);}
    }});
    const field=(label,value)=>`<div><dt>${label}</dt><dd>${value}</dd></div>`;
    function render(){
      const pct=Number(input.value),e=estimate(position,pct),base=position?.symbol.replace('-USDT-SWAP','')||'';
      dialog.querySelector('.rp-close-hint').textContent='按最新报价模拟成交；净盈亏含分摊开仓费与本次平仓费。'+([3060,5060,6060,6061,7060].includes(position?.track)?` ${position.track===7060?'live-allin3':position.track===5060?'allin3':[6060,6061].includes(position.track)?'v3add20':'spike'} 本次亏损按平仓份额的保证金封顶。`:'')+([5060,7060].includes(position?.track)?' 完全平仓后的补充本金以后台记录为准，不计入本次交易盈亏。':'')+(position?.track===7060?' 非目标退出另扣0.10%滑点情景。':'');
      el('identity').innerHTML=`<b>${esc(base)} / USDT</b> <span class="${position?.side===1?'rp-positive':'rp-negative'}">${position?.side===1?'做多':'做空'}</span> <small>${({60:'60m',5:'5m',1060:'v3',2060:'v3add · 主单',2061:'v3add · 加仓',3060:'spike',4060:'v3lock',5060:'allin3',7060:'live-allin3',6060:'v3add20 · 主单',6061:'v3add20 · 加仓'}[position?.track]||'未知策略')} 模拟</small>`;
      dialog.querySelectorAll('[data-close-percent]').forEach(b=>{b.setAttribute('aria-pressed',String(Number(b.dataset.closePercent)===pct));b.disabled=busy||success;});
      input.disabled=busy||success;
      el('estimate').innerHTML=field('当前持仓',`${number(position?.remaining_notional/position?.entry)} ${esc(base)}`)+field('拟平仓数量',`${number(e?.quantity)} ${esc(base)}`)+field('平仓后剩余',`${number(e?.remaining)} ${esc(base)}`)+field('参考价格',`${number(position?.mark)} USDT`)+field('预计平仓费 · Taker 0.05%',`${number(e?.exitFee)} USDT`)+field('分摊开仓费 · Maker 0.02%',`${number(e?.entryFee)} USDT`)+field('预计已实现净盈亏',`<b class="${e?.pnl>0?'rp-positive':e?.pnl<0?'rp-negative':''}">${money(e?.pnl)} USDT</b>`);
      submit.disabled=busy||(!success&&(!e||e.pnl===null));submit.textContent=busy?'撮合确认中…':success?'完成':'确认模拟平仓';
      dialog.querySelectorAll('[data-close-action="cancel"]').forEach(b=>b.disabled=busy);
    }
    function hide(force=false){if(busy&&!force)return;sheet.reset();dialog.close();if(!force&&opener?.isConnected)opener.focus({preventScroll:true});}
    async function confirm(){
      if(success){hide();return;}if(busy||!estimate(position,Number(input.value)))return;
      busy=true;el('status').textContent='正在提交模拟平仓…';render();const current=revision;
      try{const result=await sender.submit(position,Number(input.value));onChanged();if(current!==revision)return;
        success=true;el('status').textContent=`已平 ${number(result.closed_quantity)} ${position.symbol.replace('-USDT-SWAP','')} · 剩余 ${number(result.remaining_quantity)} · 本次净盈亏 ${money(result.pnl)} USDT`;}
      catch(error){if(current===revision)el('status').textContent=error.message;onChanged();}
      finally{busy=false;if(current===revision)render();}
    }
    dialog.addEventListener('click',event=>{const b=event.target.closest('button');if(!b)return;if(b.dataset.closePercent){input.value=b.dataset.closePercent;el('status').textContent='';render();}if(b.dataset.closeAction==='cancel')hide();if(b.dataset.closeAction==='submit')confirm();});
    input.addEventListener('input',()=>{el('status').textContent='';render();});
    dialog.addEventListener('cancel',event=>{event.preventDefault();hide();});
    dialog.addEventListener('keydown',event=>event.stopPropagation());
    return {open(p){if(busy)return;position={...p};revision++;success=false;input.value='100';el('status').textContent='';opener=document.activeElement;sheet.reset();render();dialog.showModal();submit.focus();},hide:()=>hide(true)};
  }
  window.SpikeReclaimClose={...api,create};
})();
