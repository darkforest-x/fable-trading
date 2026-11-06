/* Public, cancellable history discovery. Reuses native chart pages and the
 * existing weekly reference service; never simulates fills or writes signals. */
(() => {
  'use strict';
  const STEP=43200000;
  const H=typeof window==='undefined'?require('./chart-history.js'):window.SpikeChartHistory;
  function create({symbol,rangeMode='fib',request=(url,options)=>fetch(url,options),changed=()=>{},status=()=>{}}={}){
    let controller=null,rows=[],items=new Map(),done=new Set(),failed=new Set(),exhausted=false,closed=0;
    let snapshot={running:false,stage:'idle',complete:false,from_ms:null,to_ms:null,weeksDone:0,weeksTotal:null,failedWeeks:0};
    function publish(extra={}){snapshot={...snapshot,...extra,from_ms:rows[0]?.time??null,to_ms:closed||null,weeksDone:done.size,failedWeeks:failed.size};status({...snapshot});}
    async function get(path,params,c){
      const timeout=setTimeout(()=>c.abort(),30000);
      try{const response=await request(path+'?'+new URLSearchParams(params),{signal:c.signal,cache:'no-store'});
        const value=await response.json();if(controller!==c)throw Error('stale');if(!response.ok)throw Object.assign(Error(value.detail||'历史读取失败，请重试'),{status:response.status});return value;
      }finally{clearTimeout(timeout);}
    }
    function candles(value,before=Infinity){
      if(value.symbol!==symbol||value.bar!=='12H'||value.native_bar!=='12Hutc'||!Array.isArray(value.candles))throw Error('12H 历史身份不匹配');
      if(value.candles.some((r,i)=>![r.time,r.open,r.high,r.low,r.close].every(Number.isFinite)||r.time>=before||r.time%STEP||r.low<=0||r.low>Math.min(r.open,r.close)||r.high<Math.max(r.open,r.close,r.low)||i&&r.time<=value.candles[i-1].time))throw Error('12H 历史不完整');
      rows=H.mergeRows(value.candles,rows);closed=Math.max(closed,value.last_close_ms||0,...value.candles.filter(r=>r.confirmed!==false).map(r=>r.time+STEP));
      changed({candles:rows,items:[...items.values()],last_close_ms:closed,bar:'12H'});publish();
    }
    async function start(){
      if(controller)return;const c=new AbortController();controller=c;publish({running:true,stage:'candles',error:'',complete:false});
      try{
        const latest=await get('/api/monday-fib/candles',{symbol,period:'12H',wait_ms:8000},c);candles(latest);
        if(!rows.length)throw Error('12H 行情尚未就绪');
        while(!exhausted&&controller===c){
          const before=rows[0].time,value=await get('/api/monday-fib/history',{symbol,period:'12H',before_ms:before},c);
          if(!['ready','exhausted'].includes(value.state)||value.state==='exhausted'&&value.end_reason!=='exchange_api_boundary')throw Error('历史边界未确认，请重试');
          if(value.has_more!==false&&(!value.candles?.length||value.next_before_ms!==value.candles[0].time))throw Error('历史分页未推进');
          candles(value,before);exhausted=value.has_more===false;
        }
        const weeks=H.weekKeys(rows[0].time,closed-1);publish({stage:'references',weeksTotal:weeks.length});
        for(const week of weeks){
          if(controller!==c)return;if(done.has(week))continue;
          let value;
          try{value=await get('/api/cloud-signals/history',{symbol,week,range_mode:rangeMode},c);}
          catch(e){if(controller!==c||e.name==='AbortError'||!e.status)throw e;failed.add(week);publish();continue;}
          if(value.symbol!==symbol||value.week!==week)throw Error('历史参考身份不匹配');
          if(value.state==='ready'&&Array.isArray(value.items)&&value.items.every(s=>s.symbol===symbol&&(s.range_mode||'fib')===rangeMode)){
            for(const item of value.items)items.set(item.id,item);failed.delete(week);
            // A current week stays provisional and is refreshed next run.
            if(value.complete)done.add(week);
            changed({candles:rows,items:[...items.values()],last_close_ms:closed,bar:'12H'});
          }else if(value.state==='developing'&&week===weeks[0]){failed.delete(week);}else failed.add(week);
          publish();
        }
        publish({stage:'done',complete:exhausted&&failed.size===0,error:failed.size?'部分周次数据未就绪，未计入统计':''});
      }catch(e){if(controller===c)publish({stage:'error',error:e.name==='AbortError'?'历史读取超时，可继续重试':e.message,complete:false});}
      finally{if(controller===c){controller=null;publish({running:false});}}
    }
    function cancel(){const c=controller;if(!c)return;controller=null;c.abort();publish({running:false,stage:'paused'});}
    return {start,cancel,getState:()=>({...snapshot})};
  }
  const api={create};if(typeof module!=='undefined')module.exports=api;if(typeof window!=='undefined')window.SpikeCloudHistoryLoader=api;
})();
