/** Historical replay canvas. It renders only bars in the selected server snapshot. */
import {useEffect,useMemo,useRef} from 'react';
import {CandlestickSeries,ColorType,HistogramSeries,LineStyle,createChart,type IChartApi,type ISeriesApi,type Time,type UTCTimestamp} from 'lightweight-charts';
import {formatReplayPrice,replayBarsForChart,type ReplaySnapshot} from './replayData';

const timeLabel=(time:Time)=>typeof time==='number'?new Intl.DateTimeFormat('zh-CN',{timeZone:'Asia/Shanghai',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false}).format(time*1000):String(time);
const lineValue=(value:unknown):number|null=>{const number=Number(value);return Number.isFinite(number)&&number>0?number:null};

export function ReplayChart({snapshot}:{snapshot:ReplaySnapshot}){
 const host=useRef<HTMLDivElement>(null);
 const graph=useRef<{chart:IChartApi;candles:ISeriesApi<'Candlestick'>;volume:ISeriesApi<'Histogram'>}|null>(null);
 const fittedSession=useRef('');
 const lastBarCount=useRef(0);
 const bars=useMemo(()=>replayBarsForChart(snapshot.bars,snapshot.clock),[snapshot.bars,snapshot.clock]);
 useEffect(()=>{
  if(!host.current)return;
  const chart=createChart(host.current,{
   layout:{background:{type:ColorType.Solid,color:'#fbfcfb'},textColor:'#7d8982',fontSize:10,attributionLogo:true},
   grid:{vertLines:{visible:false},horzLines:{color:'#e8edea',style:LineStyle.Dotted}},
   rightPriceScale:{borderVisible:false,minimumWidth:58,scaleMargins:{top:.12,bottom:.28}},
   timeScale:{borderVisible:false,timeVisible:true,secondsVisible:false,minimumHeight:28,barSpacing:7,rightOffset:3,lockVisibleTimeRangeOnResize:true,tickMarkFormatter:(time:Time)=>timeLabel(time)},
   localization:{locale:'zh-CN',priceFormatter:formatReplayPrice,timeFormatter:timeLabel},
   handleScroll:{mouseWheel:true,pressedMouseMove:true,horzTouchDrag:true,vertTouchDrag:false},
   handleScale:{axisPressedMouseMove:true,mouseWheel:true,pinch:true},
   crosshair:{vertLine:{color:'#86a291',labelBackgroundColor:'#527360'},horzLine:{color:'#86a291',labelBackgroundColor:'#527360'}},
  });
  const candles=chart.addSeries(CandlestickSeries,{upColor:'#1caf83',downColor:'#f05d63',wickUpColor:'#1caf83',wickDownColor:'#f05d63',borderVisible:false,priceLineColor:'#6f927e',priceLineStyle:LineStyle.Dashed,priceFormat:{type:'price',precision:4,minMove:.0001}});
  const volume=chart.addSeries(HistogramSeries,{priceFormat:{type:'volume'},priceScaleId:'volume',lastValueVisible:false,priceLineVisible:false});
  volume.priceScale().applyOptions({scaleMargins:{top:.82,bottom:0},visible:false});
  graph.current={chart,candles,volume};
  const observer=new ResizeObserver(entries=>{const box=entries[0]?.contentRect;if(box&&box.width>0&&box.height>0)chart.resize(Math.floor(box.width),Math.floor(box.height))});
  observer.observe(host.current);
  return()=>{observer.disconnect();chart.remove();graph.current=null};
 },[]);
 useEffect(()=>{
  const current=graph.current;if(!current)return;
  const newSession=fittedSession.current!==snapshot.id;
  const visibleRange=current.chart.timeScale().getVisibleLogicalRange();
  const followTail=!newSession&&visibleRange!==null&&visibleRange.to>=lastBarCount.current-2;
  current.candles.setData(bars.map(bar=>({time:bar.time as UTCTimestamp,open:bar.open,high:bar.high,low:bar.low,close:bar.close})));
  const reference=bars.at(-1)?.close??Number(snapshot.price);
  const precision=Number.isFinite(reference)&&reference>0?Math.min(8,Math.max(2,6-Math.floor(Math.log10(reference)))):4;
  current.candles.applyOptions({priceFormat:{type:'price',precision,minMove:10**-precision}});
  current.volume.setData(bars.map(bar=>({time:bar.time as UTCTimestamp,value:bar.volume,color:bar.close>=bar.open?'#1caf8336':'#f05d6336'})));
  if(bars.length&&newSession){current.chart.timeScale().fitContent();fittedSession.current=snapshot.id}
  else if(bars.length&&followTail)current.chart.timeScale().scrollToRealTime();
  lastBarCount.current=bars.length;
 },[bars,snapshot.id,snapshot.price]);
 useEffect(()=>{
  const series=graph.current?.candles;if(!series)return;
  const rows:Array<{price:unknown;title:string;color:string}>=[];
  const range=snapshot.range;
  if(range){
   rows.push({price:range.high,title:'周一高',color:'#3b82f6'},{price:range.low,title:'周一低',color:'#3b82f6'});
   rows.push({price:range.upper,title:'上方 1.618',color:'#8b5cf6'},{price:range.lower,title:'下方 1.618',color:'#8b5cf6'});
  }
  for(const position of snapshot.positions){
   rows.push({price:position.entry,title:`${position.side==='long'?'多':'空'}入场`,color:'#c47c21'});
   if(position.stop!==undefined)rows.push({price:position.stop,title:'止损',color:'#ef4444'});
   if(position.target!==undefined)rows.push({price:position.target,title:'止盈',color:'#06a6a0'});
  }
  const lines=rows.flatMap(row=>{const price=lineValue(row.price);return price===null?[]:[series.createPriceLine({price,title:row.title,color:row.color,lineWidth:1,lineStyle:LineStyle.Dashed,axisLabelVisible:true})]});
  return()=>{if(graph.current?.candles===series)for(const line of lines)series.removePriceLine(line)};
 },[snapshot.range,snapshot.positions]);
 const last=bars.at(-1);
 return <section className="replay-chart-card" aria-label={`${snapshot.symbol} 历史回放图表`}>
  <div className="replay-chart-meta"><span>{snapshot.symbol.replace('-USDT-SWAP','/USDT')} · {snapshot.period}</span><span>截止 {new Intl.DateTimeFormat('zh-CN',{timeZone:'Asia/Shanghai',hour:'2-digit',minute:'2-digit',hour12:false}).format(snapshot.clock*1000)}</span></div>
  {last&&<div className="replay-ohlc"><span>开 {formatReplayPrice(last.open)}</span><span>高 {formatReplayPrice(last.high)}</span><span>低 {formatReplayPrice(last.low)}</span><span>收 {formatReplayPrice(last.close)}</span></div>}
  <div className="replay-plot"><div ref={host} className="replay-canvas" role="img" aria-label={`${snapshot.symbol} K线和成交量`}/><span className="replay-watermark" aria-hidden="true">{snapshot.symbol.replace('-USDT-SWAP','')}</span>{!bars.length&&<div className="replay-chart-empty">此回放时点没有 K 线</div>}</div>
  <div className="replay-chart-legend"><span><i className="replay-legend-range"/>周一高低与 1.618</span><span><i className="replay-legend-position"/>持仓与保护价</span><span>{bars.length.toLocaleString('zh-CN')} 根 · 历史 K 线</span></div>
 </section>;
}
