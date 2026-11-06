/* Stitch 9353b5cab5ae46bea72b3c8ff8787200: compact layers / colors / parameters. */
(() => {
  'use strict';
  const RANGE_MODE_KEY='spike.cloud.range-mode.v1';
  const RANGE_MODES=['fib','monday'];
  function readRangeMode(storage){try{return storage?.getItem(RANGE_MODE_KEY)==='monday'?'monday':'fib';}catch{return 'fib';}}
  function create(dialog,chart,onStops=()=>{},onRange=()=>{}){
    const C=window.SpikeCloudCandleColors,space=dialog.querySelector('.cs-chart-space'),button=dialog.querySelector('[data-cs-layers]'),menu=dialog.querySelector('.cs-layer-menu');
    const S=window.SpikeCloudStopSettings;
    let palette=C.normalize(),storage=null;
    try{storage=window.localStorage;palette=C.read(storage);}catch{}
    let stopSettings=S.read(storage),rangeMode=readRangeMode(storage);
    const tabs=['layers','colors','params'];
    const labels=[['up','上涨 K 线'],['down','下跌 K 线'],['monday','周一 K 线'],['weekend','周末 K 线']];
    const layers=menu.innerHTML;
    menu.id='cs-chart-settings';menu.setAttribute('role','dialog');menu.setAttribute('aria-label','图表设置');
    button.setAttribute('aria-label','图表设置');button.title='图层、配色与止损参数';button.setAttribute('aria-controls',menu.id);button.setAttribute('aria-haspopup','dialog');
    menu.innerHTML=`<div class="cs-settings-head"><div class="cs-settings-tabs" role="tablist" aria-label="图表设置分类">${[['layers','图层'],['colors','配色'],['params','参数']].map(([key,label])=>`<button type="button" id="cs-tab-${key}" role="tab" data-settings-tab="${key}" aria-controls="cs-panel-${key}" aria-selected="${key==='params'}" tabindex="${key==='params'?0:-1}">${label}</button>`).join('')}</div><span class="cs-save-state" role="status">自动保存</span></div><section id="cs-panel-layers" role="tabpanel" aria-labelledby="cs-tab-layers" hidden>${layers}</section><section id="cs-panel-colors" role="tabpanel" aria-labelledby="cs-tab-colors" hidden><div class="cs-color-note"><span>按纽约开盘日 · 与周一区间一致</span><button type="button" data-colors-reset>恢复默认</button></div><div class="cs-color-preview" role="img" aria-label="K 线配色示意">${labels.map(([key])=>`<i data-color-preview="${key}"></i>`).join('')}</div><div class="cs-color-rows">${labels.map(([key,label])=>`<div class="cs-color-row"><label class="cs-color-swatch" title="选择${label}颜色"><input type="color" data-color-picker="${key}" aria-label="${label}颜色选择器"></label><label for="cs-color-${key}">${label}</label><input id="cs-color-${key}" class="cs-color-hex" type="text" data-color-hex="${key}" aria-label="${label}色值" aria-describedby="cs-color-help" spellcheck="false" autocomplete="off" maxlength="7" placeholder="#8B9099"></div>`).join('')}</div><p id="cs-color-help" class="cs-color-help">周一默认亮绿；信号关键 K 线亮蓝优先</p></section>`;
    menu.insertAdjacentHTML('beforeend',`<section id="cs-panel-params" role="tabpanel" aria-labelledby="cs-tab-params"><div class="cs-stop-heading"><strong>止损比例</strong><button type="button" data-stops-reset>恢复默认</button></div>${[['btcEth','BTC / ETH'],['other','其他币种']].map(([key,label])=>`<div class="cs-stop-row"><label for="cs-stop-${key}">${label}</label><div class="cs-stop-input"><input id="cs-stop-${key}" type="number" inputmode="decimal" min="0.1" max="50" step="0.1" data-stop-percent="${key}" aria-label="${label}止损比例" aria-describedby="cs-stop-${key}-error"><span aria-hidden="true">%</span></div><small id="cs-stop-${key}-error" role="status" hidden>请输入 0.1–50 之间的数值</small></div>`).join('')}<div class="cs-range-row"><span class="cs-range-label">信号区间</span><div class="cs-range-switch" role="group" aria-label="信号区间">${[['fib','1.618'],['monday','周一高低点']].map(([key,label])=>`<button type="button" data-range-mode="${key}" aria-pressed="${key===rangeMode}">${label}</button>`).join('')}</div></div><p class="cs-range-help">信号边界、参考入场与目标同步切换；止损按参考入场价计算。</p><p class="cs-stop-help">止损按参考入场价固定距离计算 · 本机保存<span>仅调整参考图，不改变通知或订单</span></p></section>`);
    space.append(menu);
    function paintStops(){
      for(const input of menu.querySelectorAll('[data-stop-percent]')){input.value=stopSettings[input.dataset.stopPercent];input.removeAttribute('aria-invalid');menu.querySelector('#'+input.id+'-error').hidden=true;}
    }
    function paintRangeMode(){
      for(const option of menu.querySelectorAll('[data-range-mode]'))option.setAttribute('aria-pressed',String(option.dataset.rangeMode===rangeMode));
    }
    function saveRangeMode(next){
      if(!RANGE_MODES.includes(next)||next===rangeMode)return;
      rangeMode=next;paintRangeMode();
      let saved=false;try{storage?.setItem(RANGE_MODE_KEY,rangeMode);saved=!!storage;}catch{}
      menu.querySelector('.cs-save-state').textContent=saved?'已保存':'本次有效';
      onRange(rangeMode);position();
    }
    function saveStops(){
      menu.querySelector('.cs-save-state').textContent=S.write(storage,stopSettings)?'已保存':'本次有效';
      onStops({...stopSettings});position();
    }
    function changeStop(input){
      const valid=S.isValidPercentage(input.value);input.setAttribute('aria-invalid',String(!valid));menu.querySelector('#'+input.id+'-error').hidden=valid;
      if(valid){const key=input.dataset.stopPercent,value=Number(input.value);input.value=value;if(value!==stopSettings[key]){stopSettings={...stopSettings,[key]:value};saveStops();}}
      position();
    }
    function paint(){
      for(const [key] of labels){
        menu.querySelector(`[data-color-picker="${key}"]`).value=palette[key];
        const input=menu.querySelector(`[data-color-hex="${key}"]`);
        if(document.activeElement!==input){input.value=palette[key];input.removeAttribute('aria-invalid');}
        menu.querySelector(`[data-color-preview="${key}"]`).style.color=palette[key];
      }
      chart.palette(palette);
    }
    function save(){menu.querySelector('.cs-save-state').textContent=C.write(storage,palette)?'已保存':'本次有效';menu.querySelector('.cs-color-help').textContent='周一默认亮绿；信号关键 K 线亮蓝优先';paint();}
    function position(){
      if(menu.hidden)return;
      const area=space.getBoundingClientRect(),anchor=button.getBoundingClientRect(),margin=8;
      const above=anchor.top-area.top-margin,below=area.bottom-anchor.bottom-margin;
      const down=above<menu.scrollHeight+margin&&below>above;
      menu.style.maxHeight=Math.max(80,(down?below:above)-margin)+'px';
      const width=menu.offsetWidth,height=menu.offsetHeight;
      menu.style.left=Math.max(margin,Math.min(area.width-width-margin,anchor.right-area.left-width))+'px';
      menu.style.top=Math.max(margin,Math.min(area.height-height-margin,down?anchor.bottom-area.top+margin:anchor.top-area.top-height-margin))+'px';
    }
    function close(focus=false){menu.hidden=true;button.setAttribute('aria-expanded','false');if(focus)button.focus({preventScroll:true});}
    function tab(key,focus=false){
      for(const b of menu.querySelectorAll('[data-settings-tab]')){const selected=b.dataset.settingsTab===key;b.setAttribute('aria-selected',String(selected));b.tabIndex=selected?0:-1;menu.querySelector('#cs-panel-'+b.dataset.settingsTab).hidden=!selected;if(selected&&focus)b.focus();}
      position();
    }
    button.addEventListener('click',()=>{if(!menu.hidden){close();return;}menu.hidden=false;button.setAttribute('aria-expanded','true');position();menu.querySelector('[aria-selected=true]').focus({preventScroll:true});});
    menu.addEventListener('click',e=>{
      const t=e.target.closest('[data-settings-tab]');if(t)tab(t.dataset.settingsTab);
      const range=e.target.closest('[data-range-mode]');if(range)saveRangeMode(range.dataset.rangeMode);
      if(e.target.closest('[data-colors-reset]')){palette=C.normalize();save();}
      if(e.target.closest('[data-stops-reset]')){stopSettings=S.normalize();paintStops();saveStops();}
    });
    menu.addEventListener('keydown',e=>{
      if(e.key==='Escape'){e.preventDefault();e.stopPropagation();close(true);return;}
      if(e.target.matches('[data-settings-tab]')&&['ArrowLeft','ArrowRight','Home','End'].includes(e.key)){e.preventDefault();const index=tabs.indexOf(e.target.dataset.settingsTab);tab(tabs[e.key==='Home'?0:e.key==='End'?tabs.length-1:(index+(e.key==='ArrowRight'?1:tabs.length-1))%tabs.length],true);}
      if(e.target.matches('[data-color-hex],[data-stop-percent]')&&e.key==='Enter'){e.preventDefault();e.target.blur();}
    });
    menu.addEventListener('change',e=>{if(e.target.matches('[data-stop-percent]'))changeStop(e.target);});
    menu.addEventListener('input',e=>{
      if(e.target.matches('[data-stop-percent]')){e.target.removeAttribute('aria-invalid');menu.querySelector('#'+e.target.id+'-error').hidden=true;}
      const key=e.target.dataset.colorPicker||e.target.dataset.colorHex;if(!key)return;
      // Partial hex input remains editable; only complete valid colors repaint.
      const color=C.hex(e.target.value);e.target.removeAttribute('aria-invalid');
      if(color){palette={...palette,[key]:color};save();}
    });
    menu.addEventListener('focusout',e=>{
      if(e.target.matches('[data-stop-percent]')){changeStop(e.target);return;}
      const key=e.target.dataset.colorHex;if(!key)return;
      if(!C.hex(e.target.value)){e.target.setAttribute('aria-invalid','true');menu.querySelector('.cs-color-help').textContent='请输入有效色值，例如 #8B9099';}
      else{e.target.value=palette[key];menu.querySelector('.cs-color-help').textContent='周一默认亮绿；信号关键 K 线亮蓝优先';}
    });
    dialog.addEventListener('pointerdown',e=>{if(!menu.hidden&&!menu.contains(e.target)&&!button.contains(e.target))close();});
    window.addEventListener('storage',e=>{
      if(e.key===S.KEY||e.key===null){stopSettings=S.read(storage);paintStops();onStops({...stopSettings});}
      if(e.key===RANGE_MODE_KEY||e.key===null){const next=readRangeMode(storage);if(next!==rangeMode){rangeMode=next;paintRangeMode();onRange(rangeMode);}}
    });
    paint();paintStops();paintRangeMode();
    return {close,isOpen:()=>!menu.hidden,stops:()=>({...stopSettings}),rangeMode:()=>rangeMode};
  }
  window.SpikeCloudChartSettings={create};
})();
