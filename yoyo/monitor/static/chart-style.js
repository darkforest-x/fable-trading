/* Shared presentation for the six source-owned SMA/EMA series. No trading calculations. */
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
  const api={lines,price,legend,legendPrice};
  if(typeof window!=='undefined')window.SpikeChartStyle=api;
  if(typeof module!=='undefined')module.exports=api;
})();
