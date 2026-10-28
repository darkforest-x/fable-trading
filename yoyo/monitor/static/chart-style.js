/* Shared six-line presentation and display-only causal close averages. */
(() => {
  'use strict';
  const lines=Object.freeze([
    {key:'sma20',label:'SMA 20',color:'#5c65aa',period:20},
    {key:'sma60',label:'SMA 60',color:'#087f69',period:60},
    {key:'sma120',label:'SMA 120',color:'#586d81',period:120},
    {key:'ema20',label:'EMA 20',color:'#8955b6',period:20},
    {key:'ema60',label:'EMA 60',color:'#a87512',period:60},
    {key:'ema120',label:'EMA 120',color:'#bd3a52',period:120},
  ]);
  const price=value=>Number.isFinite(value)?value.toLocaleString('en-US',{maximumFractionDigits:Math.abs(value)>=100?2:Math.abs(value)>=1?4:8}):'—';
  const legendPrice=value=>Number.isFinite(value)?value.toLocaleString('en-US',{maximumSignificantDigits:6}):'缺样';
  function legend(row={}) {
    return lines.map(line=>`<span class="chart-ma-value" style="--ma-color:${line.color}" title="${line.label} · ${Number.isFinite(row[line.key])?row[line.key]:'历史不足'}"><i aria-hidden="true"></i><span>${line.label}</span><b>${legendPrice(row[line.key])}</b></span>`).join('');
  }
  const initial=()=>lines.map(()=>({sum:0,count:0,ema:null,closes:[]}));
  const checkpoint=state=>state.map(s=>({...s,closes:s.closes.slice()}));
  // Causal close-only averages. A gap resets the contiguous warm-up; no partial windows.
  function advance(state,row,step,previous){
      const point={},stamp=row.time??row.t;
      lines.forEach((line,i)=>{
        const s=state[i],n=line.period;
        if(!Number.isFinite(row.close)||(previous!==undefined&&stamp-previous!==step)){s.sum=0;s.count=0;s.ema=null;s.closes=[];}
        if(!Number.isFinite(row.close)){point[line.key]=null;return;}
        s.count++;s.sum+=row.close;s.closes.push(row.close);if(s.closes.length>n)s.sum-=s.closes.shift();
        s.ema=s.ema==null?row.close:2/(n+1)*row.close+(1-2/(n+1))*s.ema;
        point[line.key]=s.count>=n?(line.key.startsWith('sma')?s.sum/n:s.ema):null;
      });return point;
  }
  function averages(rows,step){
    const state=initial();let previous;
    return rows.map(row=>{const values=advance(state,row,step,previous);previous=row.time??row.t;return {...row,...values};});
  }
  // Display-only cache reads timestamp + close for every prefix candle. Restore
  // the state BEFORE the old tip: a forming candle may be replaced many times.
  // Corrections, prepends, rolling windows and period changes rebuild the state.
  function createAverages(){
    let inputs=[],values=[],beforeTip=initial(),previousStep;
    return (rows,step)=>{
      const n=inputs.length;
      const reusable=step===previousStep&&n>0&&rows.length>=n&&
        inputs.slice(0,-1).every((p,i)=>Object.is(p.time,rows[i].time??rows[i].t)&&Object.is(p.close,rows[i].close))&&
        Object.is(inputs[n-1].time,rows[n-1].time??rows[n-1].t);
      const start=reusable?n-1:0,state=reusable?checkpoint(beforeTip):initial();
      values=reusable?values.slice(0,start):[];
      let previous=start?(rows[start-1].time??rows[start-1].t):undefined;
      for(let i=start;i<rows.length;i++){
        if(i===rows.length-1)beforeTip=checkpoint(state);
        values.push(advance(state,rows[i],step,previous));previous=rows[i].time??rows[i].t;
      }
      inputs=rows.map(row=>({time:row.time??row.t,close:row.close}));previousStep=step;
      // Only indicator values are cached; fresh OHLC, volume and confirmation
      // metadata always come from the current input, including old corrections.
      return rows.map((row,i)=>({...row,...values[i]}));
    };
  }
  const api={lines,price,legend,legendPrice,averages,createAverages};
  if(typeof window!=='undefined')window.SpikeChartStyle=api;
  if(typeof module!=='undefined')module.exports=api;
})();
