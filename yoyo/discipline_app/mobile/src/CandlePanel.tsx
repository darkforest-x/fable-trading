import {fibLevels,type FibRange} from './fibData';
import {FibRangePrimitive} from './FibRangePrimitive';
/** Stitch 66260b3f29db4baf9dd0f2835a13e25c. One feed and a lazy Canvas chart.
 * Lightweight Charts v5.2: incremental series.update, bounded history, ResizeObserver.
 */
import { memo, useCallback, useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { CandlestickSeries, HistogramSeries, ColorType, LineStyle, createChart, type IChartApi, type ISeriesApi, type LogicalRange, type UTCTimestamp, type Time } from 'lightweight-charts';
import { Capacitor, CapacitorHttp } from '@capacitor/core';
import { candleFetcher } from './candleTransport';
import { CandleCache, PERIODS, type Candle, type CandlePeriod } from './candleData';
import { CandleSession, type CandleState } from './candleSession';
import { Icon } from './Icon';
import { feedback } from './feedback';
import type { CandleProps } from './CandleModule';
import './candle.css';

const empty: CandleState = { bars:[],status:'loading',error:'',errorKind:'',historyReady:false,loadingOlder:false,canLoadOlder:true,reset:0 };
const statusText = {loading:'加载中',cached:'缓存 · 更新中',connecting:'连接中',live:'实时',polling:'定时更新',delayed:'更新延迟',offline:'暂未连接',paused:'已暂停'};
const clockFormat = new Intl.DateTimeFormat('zh-CN',{timeZone:'Asia/Shanghai',hour:'2-digit',minute:'2-digit',hour12:false});
const fullFormat = new Intl.DateTimeFormat('zh-CN',{timeZone:'Asia/Shanghai',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false});
const dateFormat = new Intl.DateTimeFormat('zh-CN',{timeZone:'Asia/Shanghai',month:'2-digit',day:'2-digit'});
const timeLabel = (time:Time,full=false) => typeof time==='number' ? (full?fullFormat:clockFormat).format(time*1000) : String(time);
const dateLabel = (time:Time) => typeof time==='number' ? dateFormat.format(time*1000) : String(time);
type ChartView = { key:string; range:LogicalRange|null };

const CanvasChart = memo(function CanvasChart({state,symbol,period,tick,entry,stop,view,onOlder,fullscreen,range}:{state:CandleState;symbol:string;period:CandlePeriod;tick?:string;entry?:string;stop?:string;view:React.RefObject<ChartView>;onOlder:()=>void;fullscreen:boolean;range?:FibRange}) {
 const host=useRef<HTMLDivElement>(null),legend=useRef<HTMLDivElement>(null);
 const graph=useRef<{chart:IChartApi;candles:ISeriesApi<'Candlestick'>;volume:ISeriesApi<'Histogram'>}|null>(null);
 const previous=useRef<{bars:Candle[];reset:number}>({bars:[],reset:-1});
 const latest=useRef({state,onOlder});latest.current={state,onOlder};
 const hovering=useRef(false);
 const precision=Math.min(12,Math.max(2,Math.ceil(-Math.log10(Number(tick)||.01))));
 const format=useCallback((value:number)=>value.toLocaleString('en-US',{minimumFractionDigits:precision,maximumFractionDigits:precision}),[precision]);
 const showBar=useCallback((bar?:Candle)=>{if(legend.current)legend.current.textContent=bar?`开 ${format(bar.open)}   高 ${format(bar.high)}   低 ${format(bar.low)}   收 ${format(bar.close)}`:'开 —   高 —   低 —   收 —'},[format]);
 useEffect(()=>{
  if(!host.current)return;
  const chart=createChart(host.current,{
   layout:{background:{type:ColorType.Solid,color:range?'#fbfcfa':'#ffffff'},textColor:'#75847d',fontSize:10,attributionLogo:true},
   grid:{vertLines:{visible:false},horzLines:{color:'#f0f3f0'}},
   rightPriceScale:{borderVisible:false,minimumWidth:58,scaleMargins:{top:.1,bottom:.27}},
   timeScale:{borderVisible:false,timeVisible:true,secondsVisible:false,minimumHeight:range?34:26,barSpacing:6,rightOffset:4,lockVisibleTimeRangeOnResize:true,tickMarkFormatter:(time:Time,type:number)=>type<3?dateLabel(time):timeLabel(time)},
   localization:{locale:'zh-CN',timeFormatter:(time:Time)=>timeLabel(time,true)},
   handleScroll:{mouseWheel:true,pressedMouseMove:true,horzTouchDrag:true,vertTouchDrag:!!range},
   handleScale:{axisPressedMouseMove:true,mouseWheel:true,pinch:true},
   crosshair:{vertLine:{color:'#8aa99b',labelBackgroundColor:'#47685a'},horzLine:{color:'#8aa99b',labelBackgroundColor:'#47685a'}},
  });
  const candles=chart.addSeries(CandlestickSeries,{upColor:'#00c990',downColor:'#ff3b69',wickUpColor:'#00c990',wickDownColor:'#ff3b69',borderVisible:false,priceLineColor:'#819b90',priceLineStyle:LineStyle.Dashed});
  const volume=chart.addSeries(HistogramSeries,{priceFormat:{type:'volume'},priceScaleId:'volume',lastValueVisible:false,priceLineVisible:false});
  volume.priceScale().applyOptions({scaleMargins:{top:.82,bottom:0},visible:false});
  graph.current={chart,candles,volume};
  const observer=new ResizeObserver(entries=>{const box=entries[0]?.contentRect;if(box&&box.width>0&&box.height>0)chart.resize(Math.floor(box.width),Math.floor(box.height))});observer.observe(host.current);
  chart.subscribeCrosshairMove(event=>{const row=event.seriesData.get(candles);hovering.current=!!event.point&&!!row;if(row&&'open'in row)showBar({...row,volume:0,confirmed:false,time:Number(row.time)});else showBar(latest.current.state.bars.at(-1))});
  chart.timeScale().subscribeVisibleLogicalRangeChange(range=>{if(range&&range.from<12&&previous.current.bars.length>=55&&latest.current.state.historyReady&&!latest.current.state.error)latest.current.onOlder()});
  return()=>{view.current={key:symbol+period,range:chart.timeScale().getVisibleLogicalRange()};observer.disconnect();chart.remove();graph.current=null;previous.current={bars:[],reset:-1}};
 },[symbol,period,showBar,view]);
 useEffect(()=>{graph.current?.candles.applyOptions({priceFormat:{type:'price',precision,minMove:Number(tick)||.01}})},[precision,tick]);
 useEffect(()=>{
  const g=graph.current;if(!g||!state.historyReady)return;
  const data=state.bars,old=previous.current;
  const volumeBar=(bar:Candle)=>({time:bar.time as UTCTimestamp,value:bar.volume,color:bar.close>=bar.open?'#00c99033':'#ff3b6933'});
  const reset=old.reset!==state.reset||!old.bars.length||old.bars[0].time!==data[0].time;
  if(reset){
   const range=g.chart.timeScale().getVisibleLogicalRange();
   g.candles.setData(data.map(bar=>({...bar,time:bar.time as UTCTimestamp})));
   g.volume.setData(data.map(volumeBar));
   if(old.bars.length&&range){const shift=data.findIndex(bar=>bar.time===old.bars[0].time);if(shift>=0)g.chart.timeScale().setVisibleLogicalRange({from:range.from+shift,to:range.to+shift});else g.chart.timeScale().setVisibleLogicalRange({from:data.length-55,to:data.length+3})}
   else if(view.current.key===symbol+period&&view.current.range)g.chart.timeScale().setVisibleLogicalRange(view.current.range);
   else g.chart.timeScale().setVisibleLogicalRange({from:data.length-55,to:data.length+3});
  }else{
   const lastTime=old.bars.at(-1)?.time??0;
   for(const bar of data){if(bar.time>=lastTime){g.candles.update({...bar,time:bar.time as UTCTimestamp});g.volume.update(volumeBar(bar))}}
  }
  previous.current={bars:data,reset:state.reset};if(!hovering.current)showBar(data.at(-1));
 },[state.bars,state.reset,state.historyReady,symbol,period,view,showBar]);
 useEffect(()=>{
  const series=graph.current?.candles;if(!series)return;
  const lines=[{price:Number(entry),title:'开仓',color:'#5b8572'},{price:Number(stop),title:'止损',color:'#ff3b69'}].filter(line=>Number.isFinite(line.price)&&line.price>0).map(line=>series.createPriceLine({...line,lineWidth:1,lineStyle:LineStyle.Dashed,axisLabelVisible:true}));
  return()=>{if(graph.current?.candles===series)for(const line of lines)series.removePriceLine(line)};
 },[entry,stop,symbol,period,showBar]);
 useEffect(()=>{
  const series=graph.current?.candles;if(!series||!range)return;
  const primitive=new FibRangePrimitive(range);series.attachPrimitive(primitive);
  const lines=fibLevels(range).map(level=>series.createPriceLine({...level,lineWidth:1,title:'',lineStyle:level.title==='周一高'||level.title==='周一低'?LineStyle.Solid:LineStyle.Dashed,axisLabelVisible:false}));
  return()=>{if(graph.current?.candles!==series)return;series.detachPrimitive(primitive);for(const line of lines)series.removePriceLine(line)};
 },[range,symbol,period,showBar]);
 return <><div className="candle-ohlc" ref={legend} aria-label="K线开高低收">开 —　高 —　低 —　收 —</div><div className={`candle-canvas ${fullscreen?'is-fullscreen':''}`} ref={host} role="img" aria-label={`${symbol.replace('-USDT-SWAP','/USDT')} ${PERIODS[period]} K线与成交量`} onTouchStart={e=>e.stopPropagation()}/>{state.historyReady&&<button className="candle-realtime" onClick={()=>{graph.current?.chart.timeScale().scrollToRealTime();feedback('light')}}>回到最新</button>}</>;
});

export default function CandlePanel({symbol,connection,tick,entry,stop,period,onPeriod,onCollapse,embedded=false,range}:CandleProps&{embedded?:boolean;range?:FibRange;period:CandlePeriod;onPeriod:(period:CandlePeriod)=>void;onCollapse:()=>void}) {
 const identity=symbol+period;
 const [snapshot,setSnapshot]=useState({identity,state:empty}),[fullscreen,setFullscreen]=useState(false);
 // Never paint the previous symbol/period while its replacement effect is starting.
 const state=snapshot.identity===identity?snapshot.state:empty;
 const feed=useRef<CandleSession|null>(null),panel=useRef<HTMLDivElement>(null),fullButton=useRef<HTMLButtonElement>(null);
 const view=useRef<ChartView>({key:'',range:null});
 useEffect(()=>{
  let disk:Storage|undefined;try{disk=localStorage}catch{}
  const session=new CandleSession({symbol,period,cache:new CandleCache(disk),fetchPage:candleFetcher({connection,native:Capacitor.isNativePlatform()?options=>CapacitorHttp.request(options):undefined,origin:import.meta.env.VITE_SPIKE_CANDLES_HTTP||'https://www.okx.com'}),socket:()=>new WebSocket(import.meta.env.VITE_SPIKE_CANDLES_WS||'wss://ws.okx.com/ws/v5/business'),onChange:state=>setSnapshot({identity:symbol+period,state})});
  feed.current=session;setSnapshot({identity:symbol+period,state:empty});
  const visibility=()=>{if(document.hidden)session.stop();else session.start()};
  document.addEventListener('visibilitychange',visibility);visibility();
  return()=>{document.removeEventListener('visibilitychange',visibility);session.stop();feed.current=null};
 },[symbol,period,connection?.url,connection?.token,connection?.accountId]);
 useEffect(()=>{
  if(!fullscreen)return;
  const root=document.getElementById('root'),oldInert=root?.inert,overflow=document.body.style.overflow;
  if(root)root.inert=true;document.body.style.overflow='hidden';panel.current?.focus();
  const keys=(event:KeyboardEvent)=>{
   if(event.key==='Escape'){event.preventDefault();setFullscreen(false)}
   if(event.key==='Tab'){
    const controls=Array.from(panel.current?.querySelectorAll<HTMLElement>('button:not(:disabled),a[href],summary')??[]).filter(control=>control.getClientRects().length>0);const first=controls[0],last=controls.at(-1);
    if(event.shiftKey&&(document.activeElement===first||document.activeElement===panel.current)){event.preventDefault();last?.focus()}
    else if(!event.shiftKey&&(document.activeElement===last||document.activeElement===panel.current)){event.preventDefault();first?.focus()}
   }
  };
  document.addEventListener('keydown',keys);
  return()=>{if(root)root.inert=oldInert??false;document.body.style.overflow=overflow;document.removeEventListener('keydown',keys);fullButton.current?.focus({preventScroll:true})};
 },[fullscreen]);
 const loadOlder=useCallback(()=>{void feed.current?.loadOlder()},[]);
 const content=<div ref={panel} className={`candle-panel ${fullscreen?'candle-fullscreen':''}`} data-no-swipe role={fullscreen?'dialog':undefined} aria-modal={fullscreen||undefined} aria-label={fullscreen?'全屏K线':undefined} tabIndex={fullscreen?-1:undefined}>
  <header className="candle-heading"><b>{fullscreen?symbol.replace('-USDT-SWAP','/USDT'):'K线'}</b><span className={`candle-status ${state.status==='live'?'live':''}`} role="status"><i/>{statusText[state.status]}</span>{fullscreen?<button aria-label="退出全屏" onClick={()=>{setFullscreen(false);feedback('light')}}><Icon name="close" size={20}/></button>:<><button ref={fullButton} aria-label="全屏查看K线" onClick={()=>{setFullscreen(true);feedback('light')}}><svg aria-hidden="true" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6"><path d="M8 3H3v5m13-5h5v5M3 16v5h5m13-5v5h-5"/></svg></button><button aria-label="收起K线" onClick={onCollapse}><Icon name="chevron" size={16}/></button></>}</header>
  <div className="candle-periods" aria-label="K线周期">{(embedded?['5m','15m','1H','4H'] as CandlePeriod[]:Object.keys(PERIODS) as CandlePeriod[]).map(value=><button key={value} aria-pressed={value===period} onClick={()=>{if(value!==period)onPeriod(value)}}>{PERIODS[value]}</button>)}</div>
  <div className="candle-plot"><CanvasChart key={symbol+period} state={state} symbol={symbol} period={period} tick={tick} entry={entry} stop={stop} view={view} onOlder={loadOlder} fullscreen={fullscreen} range={range}/>{!state.historyReady&&<div className="candle-empty"><span>{state.error?'历史暂不可用':'正在补齐历史K线…'}</span>{!!state.error&&<button onClick={()=>void feed.current?.refresh()}>重试</button>}</div>}</div>
  <footer className="candle-footer"><span>{range?`纽约周一基准 · 图表北京时间 · ${statusText[state.status]}`:'OKX · 北京时间'}</span><button disabled={state.loadingOlder||!state.historyReady||!state.canLoadOlder} onClick={loadOlder}>{state.loadingOlder?'加载历史…':state.canLoadOlder?'更早K线':'历史已加载'}</button></footer>
  {state.error&&state.historyReady&&<button className="candle-error" onClick={()=>state.errorKind==='older'?loadOlder():void feed.current?.refresh()}>{state.error}</button>}
  <details className="candle-credit"><summary>图表来源</summary><a href="https://www.tradingview.com/" target="_blank" rel="noreferrer">TradingView Lightweight Charts™</a><small>Copyright (с) 2025 TradingView, Inc.</small></details>
 </div>;
 return fullscreen?createPortal(content,document.body):content;
}
