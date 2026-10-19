/* Page-owned reads and idle resources. Mutations are never retried or cancelled. */
(function(scope){
  'use strict';
  function createRequests({timeoutMs=15000,backoffMs=1000}={}){
    const pending=new Map(),failures=new Map();
    function json(url,options={}){
      const read=!options.method||options.method==='GET',key=url;
      if(read&&pending.has(key))return pending.get(key).promise;
      const failure=failures.get(key);
      if(read&&failure?.until>Date.now())return Promise.reject(failure.error);
      const controller=new AbortController(),entry={controller,read,cancelled:false};
      let expired=false;
      const timer=setTimeout(()=>{expired=true;controller.abort();},timeoutMs);
      if(read)pending.set(key,entry);
      entry.promise=(async()=>{
        try{
          const response=await fetch(url,{cache:'no-store',...options,signal:controller.signal});
          const data=await response.json();
          if(!response.ok)throw Error(Array.isArray(data.detail)?data.detail.map(x=>x.msg).join('；'):data.detail||`读取失败 ${response.status}`);
          failures.delete(key);return data;
        }catch(error){
          if(expired)error=Error('请求超时，请稍后重试');
          if(read&&!entry.cancelled){const count=(failure?.count||0)+1;failures.set(key,{count,until:Date.now()+Math.min(30000,backoffMs*2**Math.min(count-1,5)),error});if(failures.size>100)failures.delete(failures.keys().next().value);}
          throw error;
        }finally{clearTimeout(timer);if(pending.get(key)===entry)pending.delete(key);}
      })();
      return entry.promise;
    }
    return {json,cancel(){for(const entry of pending.values()){entry.cancelled=true;entry.controller.abort();}pending.clear();}};
  }
  function idleRelease(release,delay=60000){
    let timer=null,wasActive=true;
    return {setActive(active){if(active===wasActive)return;wasActive=active;clearTimeout(timer);timer=null;if(!active)timer=setTimeout(()=>{timer=null;release();},delay);},dispose(){clearTimeout(timer);timer=null;release();}};
  }
  // Cache only display data. Execution quotes retain their own freshness gates.
  class BoundedCache extends Map {
    constructor(limit){super();this.limit=limit;}
    get(key){if(!super.has(key))return;const value=super.get(key);super.delete(key);super.set(key,value);return value;}
    set(key,value){super.delete(key);super.set(key,value);while(this.size>this.limit)super.delete(this.keys().next().value);return this;}
  }
  const samePoint=(a,b)=>a===b||!!a&&!!b&&Object.keys(a).length===Object.keys(b).length&&Object.keys(a).every(k=>Object.is(a[k],b[k]));
  function createSeriesSync(){
    const snapshots=new WeakMap();
    return (series,rows)=>{
      const previous=snapshots.get(series);
      if(previous?.length===rows.length&&rows.every((r,i)=>samePoint(r,previous[i])))return false;
      // Only the last point may change during an append. Corrections to older
      // history, removed points and rolling windows require a complete reset.
      const append=previous?.length&&rows.length>=previous.length&&previous.slice(0,-1).every((r,i)=>samePoint(r,rows[i]))&&rows[previous.length-1]?.time===previous.at(-1).time;
      if(append){for(let i=previous.length-1;i<rows.length;i++)if(!samePoint(rows[i],previous[i]))series.update(rows[i]);}
      else series.setData(rows);
      snapshots.set(series,rows);return true;
    };
  }
  function frameTask(callback,{request=fn=>requestAnimationFrame(fn),cancel=id=>cancelAnimationFrame(id)}={}){
    let handle=null,args;
    const run=(...next)=>{args=next;if(handle===null)handle=request(()=>{handle=null;callback(...args);});};
    run.cancel=()=>{if(handle!==null)cancel(handle);handle=null;args=null;};
    return run;
  }
  function virtualWindow(count,top,height,rowHeight=70,overscan=5){
    const first=Math.max(0,Math.min(count-1,Math.floor(Math.max(0,top)/rowHeight)));
    const start=Math.max(0,first-overscan),end=Math.min(count,first+Math.ceil(Math.max(height,rowHeight)/rowHeight)+overscan);
    return {start,end,before:start*rowHeight,after:(count-end)*rowHeight};
  }
  const api={createRequests,idleRelease,BoundedCache,createSeriesSync,frameTask,virtualWindow,samePoint};
  if(typeof module!=='undefined')module.exports=api;else scope.SpikeRuntime=api;
})(typeof window!=='undefined'?window:globalThis);
