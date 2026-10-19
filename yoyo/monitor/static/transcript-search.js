/* One normalized transcript index, bounded result pages, and a worker off the UI thread.
 * The same engine is exported for parity tests; fallback yields between chunks.
 */
(function(scope){
  'use strict';
  function index(transcripts,sessions){
    const metadata=new Map(sessions.map(s=>[s.id,s])), rows=[];
    for(const [id,segments] of Object.entries(transcripts.segments||{})){
      const meta=metadata.get(id);if(!meta)continue;
      segments.forEach((segment,i)=>{
        const text=[segments[i-1]?.[1],segment[1],segments[i+1]?.[1]].filter(Boolean).join(' ');
        rows.push({id,time:segment[0],text,lower:text.toLowerCase(),center:segment[1].toLowerCase(),kind:meta.kind,date:meta.date||''});
      });
    }
    return rows.sort((a,b)=>a.date.localeCompare(b.date)||a.time-b.time);
  }
  async function search(rows,query,kind,offset=0,limit=50,yielding=false){
    const terms=query.trim().toLowerCase().split(/\s+/).filter(Boolean),hits=[];let total=0;
    if(!terms.length)return {total,hits};
    for(let i=0;i<rows.length;i++){
      const row=rows[i];
      if((kind==='all'||row.kind===kind)&&terms.every(t=>row.lower.includes(t))&&terms.some(t=>row.center.includes(t))){
        if(total>=offset&&hits.length<limit)hits.push({id:row.id,time:row.time,text:row.text});total++;
      }
      if(yielding&&i%1500===1499)await new Promise(resolve=>setTimeout(resolve,0));
    }
    return {total,hits};
  }
  function highlight(text,query){
    const escape=value=>String(value).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
    const terms=query.trim().split(/\s+/).filter(Boolean).map(t=>t.replace(/[.*+?^${}()|[\]\\]/g,'\\$&'));
    if(!terms.length)return escape(text);
    return String(text).split(new RegExp('('+terms.join('|')+')','gi')).map((part,i)=>i%2?`<mark>${escape(part)}</mark>`:escape(part)).join('');
  }
  if(typeof module!=='undefined'&&module.exports){module.exports={index,search,highlight};return;}
  if(typeof document==='undefined'){
    let rows=[];
    scope.onmessage=async({data})=>{try{
      if(data.type==='init')rows=index(data.transcripts,data.sessions);
      else scope.postMessage({id:data.id,result:await search(rows,data.query,data.kind,data.offset,data.limit)});
    }catch(error){scope.postMessage({id:data.id,error:error.message});}};
    return;
  }
  const workerURL=document.currentScript.src;
  function create(transcripts,sessions){
    let worker=null,rows=null,sequence=0,disposed=false;const pending=new Map();
    const fallback=async job=>{rows ||= index(transcripts,sessions);return search(rows,job.query,job.kind,job.offset,job.limit,true);};
    try{
      worker=new Worker(workerURL);
      worker.postMessage({type:'init',transcripts,sessions});
      worker.onmessage=({data})=>{const job=pending.get(data.id);if(!job)return;pending.delete(data.id);clearTimeout(job.timer);data.error?fallback(job).then(job.resolve,job.reject):job.resolve(data.result);};
      worker.onerror=()=>{worker?.terminate();worker=null;for(const job of pending.values()){clearTimeout(job.timer);fallback(job).then(job.resolve,job.reject);}pending.clear();};
    }catch{worker=null;}
    return {dispose(){
      disposed=true;worker?.terminate();worker=null;rows=null;transcripts=null;sessions=null;
      for(const job of pending.values()){clearTimeout(job.timer);job.reject(Object.assign(Error('搜索已取消'),{name:'AbortError'}));}pending.clear();
    },search(query,kind,offset=0,limit=50){
      if(disposed)return Promise.reject(Object.assign(Error('搜索已取消'),{name:'AbortError'}));
      const job={query,kind,offset,limit};if(!worker)return fallback(job);
      return new Promise((resolve,reject)=>{const id=++sequence;Object.assign(job,{resolve,reject});
        job.timer=setTimeout(()=>{pending.delete(id);fallback(job).then(resolve,reject);},8000);
        pending.set(id,job);
        try {worker.postMessage({type:'search',id,...{query,kind,offset,limit}});}
        catch {clearTimeout(job.timer);pending.delete(id);fallback(job).then(resolve,reject);}
      });
    }};
  }
  scope.SpikeTranscriptSearch={create,highlight};
})(typeof self!=='undefined'?self:this);
