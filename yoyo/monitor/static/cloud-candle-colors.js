/* Display preferences only. Times are chart seconds; calendar is NY bar-open day. */
(() => {
  'use strict';
  const KEY='spike.cloud.candle-colors.v1';
  const DEFAULTS=Object.freeze({up:'#8B9099',down:'#8B9099',monday:'#30D158',weekend:'#8B9099'});
  const weekday=new Intl.DateTimeFormat('en-US',{timeZone:'America/New_York',weekday:'short'});
  function hex(value){
    if(typeof value!=='string')return null;
    let s=value.trim().replace(/^#/,'');
    if(/^[\da-f]{3}$/i.test(s))s=s.split('').map(c=>c+c).join('');
    return /^[\da-f]{6}$/i.test(s)?'#'+s.toUpperCase():null;
  }
  function normalize(value){return Object.fromEntries(Object.keys(DEFAULTS).map(k=>[k,hex(value?.[k])||DEFAULTS[k]]));}
  function read(storage){try{
    const saved=JSON.parse(storage.getItem(KEY)),value=normalize(saved);
    // Only the complete legacy default palette follows the new default.
    // A versioned save can intentionally choose gray without being migrated again.
    if(saved&&!saved.paletteVersion&&Object.keys(DEFAULTS).every(k=>hex(saved[k])==='#8B9099')){
      value.monday=DEFAULTS.monday;write(storage,value);
    }
    return value;
  }catch{return normalize();}}
  function write(storage,value){try{storage.setItem(KEY,JSON.stringify({...normalize(value),paletteVersion:2}));return true;}catch{return false;}}
  function category(time){const day=weekday.format(new Date(time*1000));return day==='Mon'?'monday':day==='Sat'||day==='Sun'?'weekend':null;}
  function colorizer(value){
    const palette=normalize(value),days=new Map();
    return row=>{
      if(!days.has(row.time)){if(days.size>4096)days.clear();days.set(row.time,category(row.time));}
      return palette[days.get(row.time)||(row.close>=row.open?'up':'down')];
    };
  }
  const api={KEY,DEFAULTS,hex,normalize,read,write,category,colorizer};
  if(typeof module!=='undefined')module.exports=api;
  if(typeof window!=='undefined')window.SpikeCloudCandleColors=api;
})();
