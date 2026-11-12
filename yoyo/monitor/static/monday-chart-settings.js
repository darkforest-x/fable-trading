/* Compact settings popover for the Monday 1.618 display. */
(() => {
  'use strict';
  const Model=typeof module!=='undefined'&&module.exports?require('./monday-chart-appearance.js'):window.SpikeMondayChartAppearance;
  const names={'周一高':'周一高点','周一低':'周一低点','上 1.618':'上方 1.618','下 1.618':'下方 1.618'};
  const esc=value=>String(value).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const THEME=()=>typeof document!=='undefined'&&document.documentElement.dataset.theme==='dark'?'dark':'light';
  function html(drawings='',platform){
    const tabs=[['indicators','指标'],['candles','K线'],['lines','区间线']];
    const runtimePlatform=platform??(typeof navigator!=='undefined'?(navigator.userAgentData?.platform||navigator.platform||navigator.userAgent):'');
    const windowsLink=/windows|win32|win64/i.test(String(runtimePlatform))?'<a class="mcs-windows-link" href="/static/spike-chart-link-windows.zip" download aria-label="安装或更新 Windows 本机 TradingView 入口">安装 / 更新本机 TV 入口</a>':'';
    const indicatorControls='<button type="button" data-rp-action="ma" role="switch" aria-checked="false" aria-label="六均线"><span>六均线</span><i aria-hidden="true"></i></button><button type="button" data-rp-action="internal" role="switch" aria-checked="false" aria-label="区间点位"><span>区间点位</span><i aria-hidden="true"></i></button><button type="button" class="mcs-switch mcs-monthly-switch" data-mcs-monthly-enabled role="switch" aria-checked="true" aria-label="月首周 1.618 区间"><span>月首周 1.618 区间</span><i aria-hidden="true"></i></button>'+drawings;
    const candleRows=Object.entries({up:'上涨',down:'下跌',monday:'周一',weekend:'周末'}).map(([key,label])=>`<label class="mcs-color-row"><span>${label}</span><input type="color" data-mcs-candle-color="${key}" aria-label="${label} K 线颜色"></label>`).join('');
    const lineRows=Model.LABELS.map(label=>`<article class="mcs-line" data-mcs-line="${esc(label)}"><div class="mcs-line-heading"><strong>${names[label]}</strong><div class="mcs-line-color"><input type="color" data-mcs-line-color="${esc(label)}" aria-label="${names[label]}颜色"><button type="button" data-mcs-default="${esc(label)}" aria-pressed="true" title="跟随主题默认颜色">默认</button></div></div><div class="mcs-line-options"><label>线型<select data-mcs-style="${esc(label)}" aria-label="${names[label]}线型"><option value="solid">实线</option><option value="dash">虚线</option><option value="dot">点线</option></select></label><label>粗细<select data-mcs-width="${esc(label)}" aria-label="${names[label]}粗细"><option value="1">1 px</option><option value="2">2 px</option><option value="3">3 px</option></select></label></div></article>`).join('');
    return `<nav class="mcs-tabs" role="tablist" aria-label="图表显示设置">${tabs.map(([id,label],i)=>`<button type="button" role="tab" id="mcs-tab-${id}" aria-controls="mcs-content-${id}" aria-selected="${i===0}" tabindex="${i===0?'0':'-1'}" data-mcs-tab="${id}">${label}</button>`).join('')}</nav>
      <section class="mcs-content" role="tabpanel" id="mcs-content-indicators" aria-labelledby="mcs-tab-indicators" data-mcs-content="indicators">${indicatorControls}</section>
      <section class="mcs-content" role="tabpanel" id="mcs-content-candles" aria-labelledby="mcs-tab-candles" data-mcs-content="candles" hidden><header><strong>K 线颜色</strong></header><button type="button" class="mcs-switch" role="switch" aria-checked="false" data-mcs-candle-enabled><span>自定义 K 线配色</span><i aria-hidden="true"></i></button><div class="mcs-candle-colors" data-mcs-candle-fields>${candleRows}</div>${windowsLink}</section>
      <section class="mcs-content" role="tabpanel" id="mcs-content-lines" aria-labelledby="mcs-tab-lines" data-mcs-content="lines" hidden><header><strong>周区间线</strong><small>颜色默认跟随主题</small></header><div class="mcs-lines">${lineRows}</div></section>`;
  }
  function create({panel,storage,onChange=()=>{},document:doc=typeof document!=='undefined'?document:null}={}){
    if(!panel)throw Error('monday_chart_settings_panel_required');
    let store=storage;
    if(!store)try{store=doc?.defaultView?.localStorage||globalThis.localStorage;}catch{}
    let state=Model.read(store),tab='indicators',dead=false;
    const copy=()=>Model.normalize(state);
    function update(){
      panel.querySelectorAll('[data-mcs-tab]').forEach(button=>{
        const active=button.dataset.mcsTab===tab;button.setAttribute('aria-selected',String(active));button.tabIndex=active?0:-1;
      });
      panel.querySelectorAll('[data-mcs-content]').forEach(section=>{section.hidden=section.dataset.mcsContent!==tab;});
      const theme=THEME(),toggle=panel.querySelector('[data-mcs-candle-enabled]');
      if(toggle)toggle.setAttribute('aria-checked',String(state.candleEnabled));
      const monthlyToggle=panel.querySelector('[data-mcs-monthly-enabled]');
      if(monthlyToggle)monthlyToggle.setAttribute('aria-checked',String(state.monthlyEnabled));
      panel.querySelectorAll('[data-mcs-candle-color]').forEach(input=>{input.value=state.candleColors[input.dataset.mcsCandleColor];input.disabled=!state.candleEnabled;});
      for(const label of Model.LABELS){
        const line=state.lines[label],color=panel.querySelector(`[data-mcs-line-color="${label}"]`),reset=panel.querySelector(`[data-mcs-default="${label}"]`),style=panel.querySelector(`[data-mcs-style="${label}"]`),width=panel.querySelector(`[data-mcs-width="${label}"]`);
        if(color)color.value=line.color||Model.defaultsForTheme(theme)[label];
        if(reset)reset.setAttribute('aria-pressed',String(line.color===null));
        if(style)style.value=line.style;
        if(width)width.value=String(line.width);
      }
    }
    function changed(){
      state=Model.normalize(state);Model.write(store,state);update();onChange(copy());
    }
    function setMonthlyEnabled(value){
      const enabled=!!value;if(state.monthlyEnabled===enabled)return;
      state.monthlyEnabled=enabled;changed();
    }
    function click(event){
      const button=event.target.closest?.('button');if(!button||!panel.contains(button))return;
      if(button.dataset.mcsTab){tab=button.dataset.mcsTab;update();return;}
      if(button.hasAttribute('data-mcs-monthly-enabled')){setMonthlyEnabled(!state.monthlyEnabled);return;}
      if(button.hasAttribute('data-mcs-candle-enabled')){state.candleEnabled=!state.candleEnabled;changed();return;}
      if(button.dataset.mcsDefault){state.lines[button.dataset.mcsDefault].color=null;changed();}
    }
    function change(event){
      const input=event.target;
      if(input.matches?.('[data-mcs-candle-color]'))state.candleColors[input.dataset.mcsCandleColor]=input.value;
      else if(input.matches?.('[data-mcs-line-color]'))state.lines[input.dataset.mcsLineColor].color=input.value;
      else if(input.matches?.('[data-mcs-style]'))state.lines[input.dataset.mcsStyle].style=input.value;
      else if(input.matches?.('[data-mcs-width]'))state.lines[input.dataset.mcsWidth].width=Number(input.value);
      else return;
      changed();
    }
    function keydown(event){
      const current=event.target.closest?.('[data-mcs-tab]');if(!current)return;
      const buttons=Array.from(panel.querySelectorAll('[data-mcs-tab]')),index=buttons.indexOf(current);if(index<0)return;
      let next=index;
      if(event.key==='ArrowRight')next=(index+1)%buttons.length;
      else if(event.key==='ArrowLeft')next=(index+buttons.length-1)%buttons.length;
      else if(event.key==='Home')next=0;
      else if(event.key==='End')next=buttons.length-1;
      else return;
      event.preventDefault();tab=buttons[next].dataset.mcsTab;update();buttons[next].focus();
    }
    panel.addEventListener('click',click);panel.addEventListener('change',change);panel.addEventListener('keydown',keydown);update();onChange(copy());
    const observer=doc?.documentElement&&typeof MutationObserver!=='undefined'?new MutationObserver(()=>{
      const followsTheme=Model.LABELS.some(label=>state.lines[label].color===null);
      update();if(followsTheme)onChange(copy());
    }):null;
    observer?.observe(doc.documentElement,{attributes:true,attributeFilter:['data-theme']});
    return {getState:copy,setMonthlyEnabled,activeCount(){return Number(state.candleEnabled)+Number(state.monthlyEnabled)+Model.LABELS.filter(label=>{const line=state.lines[label];return line.color!==null||line.width!==1||line.style!=='solid';}).length;},destroy(){if(dead)return;dead=true;observer?.disconnect();panel.removeEventListener('click',click);panel.removeEventListener('change',change);panel.removeEventListener('keydown',keydown);},refresh:update};
  }
  const api={html,create};
  if(typeof module!=='undefined')module.exports=api;
  if(typeof window!=='undefined')window.SpikeMondayChartSettings=api;
})();
