/* Read-only patrol overlays, Stitch screen b845bc7cedc348af88c7223b08929c3d.
 * The stored detection owns the NY week; delayed responses never own a new chart.
 * Extrema-anchored rays show all four levels first; detail fit is explicit.
 */
(() => {
  'use strict';
  function levels(value,{symbol,detection,at}){
    if(value.symbol!==symbol||value.detection_id!==detection||value.as_of_ms!==at||value.timezone!=='America/New_York')throw Error('区间与当前形态不匹配，请重试');
    if(value.state!=='ready')return [];
    if(![value.start_ms,value.end_ms,value.expires_ms,value.high,value.low,value.upper,value.lower,value.high_ms,value.low_ms].every(Number.isFinite)||
      !(value.start_ms<value.end_ms&&value.end_ms<=at&&at<value.expires_ms&&value.high>value.low&&value.low>0))throw Error('区间数据不完整，请重试');
    if([value.high_ms,value.low_ms].some(t=>t<value.start_ms||t>=value.end_ms))throw Error('周一锚点不完整，请重试');
    return [
      {label:'上 1.618',price:value.upper,color:'#c93248',start_ms:value.high_ms},
      {label:'周一高',price:value.high,color:'#528cc1',start_ms:value.high_ms},
      {label:'周一低',price:value.low,color:'#528cc1',start_ms:value.low_ms},
      {label:'下 1.618',price:value.lower,color:'#087f69',start_ms:value.low_ms},
    ];
  }
  function fitRange(info,rows,fit){
    if(!fit||!rows.length||!info?.priceRange)return info;
    return {...info,priceRange:{minValue:Math.min(info.priceRange.minValue,...rows.map(r=>r.price)),maxValue:Math.max(info.priceRange.maxValue,...rows.map(r=>r.price))}};
  }
  function mount({chart,getRows,series,host,url,symbol,detection,at,format,fetcher=fetch}){
    let disposed=false,controller=null,primitive=null,rows=[],fit=true;
    const identity={symbol,detection,at};
    const escape=value=>String(value).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
    const autoscale=base=>fitRange(base(),rows,fit);
    const clear=()=>{if(primitive)series.detachPrimitive(primitive);primitive=null;rows=[];};
    const paint=(text,retry=false)=>{host.innerHTML=`<span class="yd-range-state">${escape(text)}</span>${retry?'<button type="button" data-range-retry>重试区间</button>':''}`;};
    async function load(){
      controller?.abort();const request=controller=new AbortController();
      clear();fit=true;series.applyOptions({autoscaleInfoProvider:autoscale});paint('正在读取形态所在周的区间…');
      const timeout=setTimeout(()=>request.abort(),15000);
      try{
        const response=await fetcher(url,{signal:request.signal,cache:'no-store'});
        if(!response.ok)throw Error('周一区间暂不可用');
        const value=await response.json();
        if(disposed||request!==controller)return;
        rows=levels(value,identity);
        if(!rows.length){paint(value.state==='developing'?'形态确认时周一区间尚未形成':'该周周一数据不完整，暂不画区间',value.state!=='developing');return;}
        const owner={chart,series,get rows(){return getRows();},levelWindow:{start_ms:value.start_ms,end_ms:value.expires_ms},activeLevels:rows.map(r=>({...r,axisLabelVisible:false,inlinePriceVisible:true,inlineNameVisible:true})),scaleLevels:()=>rows};
        primitive=new window.SpikeRangeChart.WeekLevels(owner);series.attachPrimitive(primitive);
        series.applyOptions({autoscaleInfoProvider:autoscale});series.priceScale().applyOptions({autoScale:true});
        host.innerHTML=`<span class="yd-range-week">周一区间 ${escape(value.week)} · 纽约</span>`+rows.map(r=>`<span class="yd-range-price" style="color:${r.color}"><i aria-hidden="true"></i>${r.label} <b>${escape(format(r.price))}</b></span>`).join('')+'<button type="button" data-range-fit aria-pressed="true">形态细节</button>';
      }catch(e){if(!disposed&&request===controller)paint(e.name==='AbortError'?'区间读取超时，K 线仍可查看':e.message,true);}
      finally{clearTimeout(timeout);}
    }
    const click=event=>{
      if(event.target.closest('[data-range-retry]'))load();
      const button=event.target.closest('[data-range-fit]');
      if(button){fit=!fit;button.setAttribute('aria-pressed',String(fit));button.textContent=fit?'形态细节':'完整区间';series.applyOptions({autoscaleInfoProvider:autoscale});series.priceScale().applyOptions({autoScale:true});}
    };
    host.addEventListener('click',click);load();
    return {destroy(){disposed=true;controller?.abort();host.removeEventListener('click',click);clear();series.applyOptions({autoscaleInfoProvider:undefined});host.replaceChildren();}};
  }
  if(typeof module!=='undefined')module.exports={levels,fitRange,mount};
  if(typeof window!=='undefined')window.SpikeYoloChartLevels={mount};
})();
