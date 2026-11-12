/* Per-page display preferences for the Monday 1.618 chart. */
(() => {
  'use strict';
  const KEY='spike.monday-chart-appearance.v1';
  const LABELS=Object.freeze(['周一高','周一低','上 1.618','下 1.618']);
  const CANDLE_DEFAULTS=Object.freeze({up:'#8B9099',down:'#8B9099',monday:'#30D158',weekend:'#8B9099'});
  const DEFAULTS=Object.freeze({
    candleEnabled:false,
    monthlyEnabled:true,
    candleColors:CANDLE_DEFAULTS,
    lines:Object.freeze(Object.fromEntries(LABELS.map(label=>[label,Object.freeze({color:null,width:1,style:'solid'})])))
  });
  const THEME_COLORS=Object.freeze({
    light:Object.freeze({'周一高':'#9399A2','周一低':'#9399A2','上 1.618':'#17191D','下 1.618':'#17191D'}),
    dark:Object.freeze({'周一高':'#9399A2','周一低':'#9399A2','上 1.618':'#E3E7EC','下 1.618':'#E3E7EC'})
  });
  const STYLE_CODES=Object.freeze({solid:0,dot:1,dash:2});
  function hex(value){
    if(typeof value!=='string')return null;
    let s=value.trim().replace(/^#/,'');
    if(/^[\da-f]{3}$/i.test(s))s=s.split('').map(c=>c+c).join('');
    return /^[\da-f]{6}$/i.test(s)?'#'+s.toUpperCase():null;
  }
  function normalize(value){
    const source=value&&typeof value==='object'&&!Array.isArray(value)?value:{};
    const candleColors=Object.fromEntries(Object.keys(CANDLE_DEFAULTS).map(key=>[key,hex(source.candleColors?.[key])||CANDLE_DEFAULTS[key]]));
    const lines=Object.fromEntries(LABELS.map(label=>{
      const line=source.lines?.[label]&&typeof source.lines[label]==='object'?source.lines[label]:{};
      return [label,{color:line.color===null?null:hex(line.color),width:[1,2,3].includes(line.width)?line.width:1,style:Object.hasOwn(STYLE_CODES,line.style)?line.style:'solid'}];
    }));
    return {candleEnabled:source.candleEnabled===true,monthlyEnabled:source.monthlyEnabled===undefined?true:source.monthlyEnabled===true,candleColors,lines};
  }
  function read(storage){try{
    const saved=JSON.parse(storage.getItem(KEY)),value=normalize(saved);
    if(saved&&!saved.paletteVersion&&Object.keys(CANDLE_DEFAULTS).every(k=>hex(saved.candleColors?.[k])==='#8B9099')){
      value.candleColors.monday=CANDLE_DEFAULTS.monday;write(storage,value);
    }
    return value;
  }catch{return normalize();}}
  function write(storage,value){try{storage.setItem(KEY,JSON.stringify({...normalize(value),paletteVersion:2}));return true;}catch{return false;}}
  function defaultsForTheme(theme='light'){
    return THEME_COLORS[theme==='dark'?'dark':'light'];
  }
  function colorFor(line,theme='light'){
    const custom=hex(line?.color);
    return custom||defaultsForTheme(theme)[line?.label]||'#9399A2';
  }
  function applyLevels(levels,appearance,theme='light'){
    const settings=normalize(appearance),defaults=defaultsForTheme(theme);
    return (Array.isArray(levels)?levels:[]).map(level=>{
      if(!LABELS.includes(level?.label))return level;
      const setting=settings.lines[level.label];
      return {...level,color:setting.color||defaults[level.label],lineWidth:setting.width,lineStyle:STYLE_CODES[setting.style],startMarkerVisible:false};
    });
  }
  function candleColorizer(appearance,helper){
    const settings=normalize(appearance);
    if(!settings.candleEnabled)return null;
    const colors=helper||(typeof window!=='undefined'?window.SpikeCloudCandleColors:null);
    return colors?.colorizer?colors.colorizer(settings.candleColors):null;
  }
  const api={KEY,LABELS,CANDLE_DEFAULTS,DEFAULTS,THEME_COLORS,STYLE_CODES,hex,normalize,read,write,defaultsForTheme,colorFor,applyLevels,candleColorizer};
  if(typeof module!=='undefined')module.exports=api;
  if(typeof window!=='undefined')window.SpikeMondayChartAppearance=api;
})();
