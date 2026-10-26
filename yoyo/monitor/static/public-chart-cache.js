/* Display-only public history. No balances, fills, live tips or order context.
 * IndexedDB stores structured clones asynchronously (MDN IndexedDB / put API).
 * Same-origin, versioned, bounded, NY-week keyed; all restores revalidate online.
 */
(() => {
 'use strict';
 const STEPS={'1m':60000,'5m':300000,'15m':900000,'30m':1800000,'1H':3600000,'4H':14400000,'12H':43200000,'1D':86400000};
 const finite=Number.isFinite,DAY=86400000,symbolOK=s=>typeof s==='string'&&/^[A-Z0-9]{1,32}-USDT-SWAP$/.test(s);
 function weekAt(now){const day=new Date(new Intl.DateTimeFormat('sv-SE',{timeZone:'America/New_York',year:'numeric',month:'2-digit',day:'2-digit'}).format(now)+'T00:00:00Z');day.setUTCDate(day.getUTCDate()-(day.getUTCDay()+6)%7);return day.toISOString().slice(0,10);}
 function sanitize(kind,value){
  if(!value||!symbolOK(value.symbol)||value.timezone!=='America/New_York')return null;
  if(kind==='levels'){
   const v=value;if(v.state!=='ready'||!/^\d{4}-\d{2}-\d{2}$/.test(v.week)||![v.start_ms,v.end_ms,v.expires_ms,v.high_ms,v.low_ms,v.high,v.low,v.upper,v.lower].every(finite)||v.high<=v.low||v.low<=0||v.start_ms>=v.end_ms||v.end_ms>=v.expires_ms||weekAt(v.start_ms)!==v.week||Math.abs(v.upper-(v.high+.618*(v.high-v.low)))>Math.max(1e-12,v.high*1e-8)||Math.abs(v.lower-(v.low-.618*(v.high-v.low)))>Math.max(1e-12,v.high*1e-8)||![v.high_ms,v.low_ms].every(t=>t>=v.start_ms&&t<v.end_ms))return null;
   return Object.fromEntries(['symbol','timezone','week','state','start_ms','end_ms','expires_ms','anchor_bar','high_ms','low_ms','high','low','upper','lower'].map(k=>[k,v[k]]));
  }
  const v=value,step=STEPS[v.bar],m=v.monday;
  if(!step||['12H','1D'].includes(v.bar)&&v.native_bar!==v.bar+'utc'||v.error||!finite(v.now_ms)||!finite(v.last_close_ms)||!m||![m.start_ms,m.end_ms,m.expires_ms].every(finite)||!Array.isArray(v.candles)||!v.candles.length||v.candles.length>720)return null;
  if(v.last_close_ms>v.now_ms||weekAt(m.start_ms)!==v.week)return null;
  let previous=-Infinity;const candles=[];
  for(const r of v.candles){if(![r.time,r.open,r.high,r.low,r.close,r.volume].every(finite)||r.time<=previous||r.time%step||r.time+step>v.last_close_ms||Math.min(r.open,r.high,r.low,r.close)<=0||r.volume<0||r.high<Math.max(r.open,r.close,r.low)||r.low>Math.min(r.open,r.close))return null;previous=r.time;candles.push(Object.fromEntries(['time','open','high','low','close','volume'].map(k=>[k,r[k]])));}
  return {symbol:v.symbol,bar:v.bar,native_bar:v.native_bar,source:'OKX',timezone:v.timezone,week:v.week,monday:{start_ms:m.start_ms,end_ms:m.end_ms,expires_ms:m.expires_ms},now_ms:v.now_ms,last_close_ms:v.last_close_ms,history_complete:!!v.history_complete,candles};
 }
 function clean(kind,value){try{return ['levels','candles'].includes(kind)?sanitize(kind,value):null}catch{return null}}
 function create({indexedDB,now=Date.now}={}){
  if(indexedDB===undefined){try{indexedDB=typeof window==='undefined'?null:window.indexedDB}catch{indexedDB=null}}
  const candles=new Map(),levels=new Map(),saved=new Map();let opening=null,writes=Promise.resolve();
  const valid=r=>r&&r.version===1&&finite(r.saved)&&now()-r.saved>=0&&now()-r.saved<DAY&&r.value?.week===weekAt(now())&&clean(r.kind,r.value);
  function database(){
   if(!indexedDB)return Promise.resolve(null);
   if(!opening)opening=new Promise(resolve=>{let settled=false,request;const finish=db=>{if(settled){db?.close();return}settled=true;resolve(db)};try{request=indexedDB.open('spike-public-charts',1);request.onupgradeneeded=()=>{if(!request.result.objectStoreNames.contains('snapshots'))request.result.createObjectStore('snapshots',{keyPath:'id'})};request.onsuccess=()=>{const db=request.result;db.onversionchange=()=>{db.close();opening=null};finish(db)};request.onerror=request.onblocked=()=>finish(null)}catch{finish(null)}});
   return opening;
  }
  function save(kind,key,value){
   const data=clean(kind,value);if(!data)return;
   const id=kind+'|'+key,signature=kind==='levels'?JSON.stringify(data):[data.candles[0].time,data.last_close_ms,data.candles.length,data.history_complete].join('|');
   if(saved.get(id)===signature)return;saved.delete(id);saved.set(id,signature);while(saved.size>44)saved.delete(saved.keys().next().value);
   const record={id,kind,version:1,saved:now(),value:data};
   writes=writes.catch(()=>{}).then(async()=>{const db=await database();if(!db)return;await new Promise(resolve=>{try{const tx=db.transaction('snapshots','readwrite'),s=tx.objectStore('snapshots');tx.oncomplete=tx.onerror=tx.onabort=resolve;s.put(record);const all=s.getAll();all.onsuccess=()=>{const counts={candles:0,levels:0};for(const r of all.result.sort((a,b)=>b.saved-a.saved)){if(!valid(r)||++counts[r.kind]>(r.kind==='candles'?12:32))s.delete(r.id)}}}catch{resolve()}})});
  }
  async function restore(kind,key){
   const db=await database();if(!db)return null;
   return new Promise(resolve=>{try{const tx=db.transaction('snapshots','readonly'),r=tx.objectStore('snapshots').get(kind+'|'+key);r.onsuccess=()=>{const record=r.result,value=record?.kind===kind&&record.id===kind+'|'+key?valid(record):null;const identity=value?value.symbol+'|'+value.week+(kind==='candles'?'|'+value.bar:''):'';resolve(identity===key?value:null)};r.onerror=tx.onerror=tx.onabort=()=>resolve(null)}catch{resolve(null)}});
  }
  return {candles,levels,weekAt,saveCandles:value=>save('candles',value.symbol+'|'+value.week+'|'+value.bar,value),saveLevels:value=>save('levels',value.symbol+'|'+value.week,value),restoreCandles:key=>restore('candles',key),restoreLevels:key=>restore('levels',key)};
 }
 if(typeof module!=='undefined')module.exports={create,clean,weekAt};
 if(typeof window!=='undefined')window.SpikePublicChartCache=create();
})();
