/** Stitch 29ef83710fe44599b3efc0c0be7e49ab: default, chart-first Monday workspace. */
import {lazy,memo,Suspense,useEffect,useMemo,useRef,useState} from 'react';
import {api,SERVICE_URL,type Connection,type State} from './api';
import {Dialog} from './Dialog';
import {Icon} from './Icon';
import {feedback} from './feedback';
import {fibRangeFor,fibSelection,latestFibTouch,parseFibSnapshot,touchLabel,type FibContract,type FibSnapshot} from './fibData';
import type {CandlePeriod} from './candleData';
import {browserFibCache,currentFibWeek} from './fibCache';
import './fib.css';
const CandlePanel=lazy(()=>import('./CandlePanel'));
const noCollapse=()=>{};
export const FibHome=memo(function FibHome({connection,state,onQuick,locked,active,covered}:{connection:Connection|null;state:State|null;onQuick:(row:FibContract,period:CandlePeriod)=>void;locked:boolean;active:boolean;covered:boolean}){
 const [cache]=useState(()=>browserFibCache(SERVICE_URL)),[saved]=useState(()=>cache.read());
 const [data,setData]=useState<FibSnapshot|null>(saved?.data??null),[symbol,setSymbol]=useState(saved?.symbol??''),[error,setError]=useState(''),[fresh,setFresh]=useState(false);
 const [period,setPeriod]=useState<CandlePeriod>(saved?.period??'15m'),[chooser,setChooser]=useState(false),[search,setSearch]=useState('');
 const [revision,setRevision]=useState(0),[clock,setClock]=useState(Date.now());
 const previous=useRef<{week?:string;symbol:string}>({week:saved?.data.week,symbol:saved?.symbol??''});
 const preferences=useRef({symbol,period});preferences.current={symbol,period};
 const enabled=active&&!covered;
 useEffect(()=>{if(!enabled){setFresh(false);return}let active=true,generation=0,timer:ReturnType<typeof setTimeout>,controller:AbortController|null=null;
  const poll=async()=>{if(!active||document.hidden)return;setClock(Date.now());const version=++generation;controller=new AbortController();const timeout=setTimeout(()=>controller?.abort(),9000);
   try{const next=parseFibSnapshot(await api<FibSnapshot>({url:SERVICE_URL,token:''},'/v1/market/monday-fib',undefined,controller.signal));if(!currentFibWeek(next))throw Error('区间周次过期');if(active&&version===generation){const selected=fibSelection(previous.current.symbol,previous.current.week,next);previous.current={week:next.week,symbol:selected};setData(next);setSymbol(selected);setError('');setFresh(true);setClock(Date.now());cache.write(next,selected,preferences.current.period)}}
   catch{if(active&&version===generation){setError('触价监控暂未连接');setFresh(false)}}
   finally{clearTimeout(timeout);if(active&&version===generation&&!document.hidden)timer=setTimeout(poll,5000)}
  };
  const visibility=()=>{setFresh(false);generation++;clearTimeout(timer);controller?.abort();if(!document.hidden)void poll()};
  document.addEventListener('visibilitychange',visibility);void poll();return()=>{active=false;clearTimeout(timer);controller?.abort();document.removeEventListener('visibilitychange',visibility)};
 },[revision,enabled,cache]);
 useEffect(()=>{if(!active)return;setClock(Date.now());const timer=setInterval(()=>setClock(Date.now()),30000);return()=>clearInterval(timer)},[active]);
 const current=data&&currentFibWeek(data,Math.max(clock,Date.now()))?data:null;
 const row=current?.items.find(r=>r.symbol===symbol),touch=row?latestFibTouch(row):null;
 const range=useMemo(()=>row&&data?fibRangeFor(row,data):undefined,[symbol,data?.monday_end_ms,data?.next_week_ms,row?.high,row?.low,row?.upper,row?.lower]);
 const live=fresh&&!!current?.connected&&!!current.last_message_ms&&clock-current.last_message_ms<90000&&!error;
 function select(value:string){if(value!==symbol)feedback('light');setSymbol(value);previous.current.symbol=value;if(current)cache.write(current,value,period)}
 function changePeriod(value:CandlePeriod){if(value!==period)feedback('light');setPeriod(value);if(current)cache.write(current,symbol,value)}
 const recent=current?.items[0];
 const tick=state?.instruments[symbol]?.tick??(row?String(10**-Math.min(12,Math.max(2,...[row.high,row.low].map(n=>String(n).split('.')[1]?.length??0)))):undefined);
 return <section className="fib-home" aria-label="1.618 行情" hidden={!active} inert={!active} data-no-swipe>
  <div className="fib-screen" inert={chooser}>
   <header className="fib-top"><button className="fib-symbol" aria-label="选择已触价合约" onClick={()=>{setSearch('');setChooser(true)}} disabled={!current?.items.length}><i className={live?'live':''}/><b>{row?symbol.replace('-USDT-SWAP',''):'1.618'}<small>{row?'/USDT':''}</small></b><Icon name="chevron" size={13}/></button></header>
   {row&&touch?<>
    <div className="fib-selected"><span>{touchLabel(touch,clock)}</span>{!fresh&&<span role="status">{error?'离线缓存':'缓存 · 更新中'}</span>}</div>
    {recent&&recent.symbol!==symbol&&<button className="fib-new" onClick={()=>select(recent.symbol)}>查看最新触价 · {recent.symbol.replace('-USDT-SWAP','')} <Icon name="arrow" size={12}/></button>}
    <div className="fib-chart"><Suspense fallback={<div className="fib-empty">正在加载图表…</div>}><CandlePanel symbol={symbol} connection={connection} tick={tick} period={period} onPeriod={changePeriod} onCollapse={noCollapse} active={enabled&&!chooser} embedded range={range}/></Suspense></div>
    <div className="fib-quick"><button disabled={locked||!fresh||!live} onClick={()=>onQuick(row,period)}>快捷开单 <span>· 1R</span><Icon name="arrow" size={17}/></button></div>
   </>:<div className="fib-empty"><Icon name="trade" size={34}/><h2>{error?'监控暂不可用':current?'本周暂无触价':'正在读取触价记录'}</h2><p>{error?'稍后重试，已有交易保护保持独立运行。':current&&!current.monday_complete?'纽约周一结束后锁定高低点。':'首次触及区间外 1.618 的合约会显示在这里。'}</p>{error&&<button onClick={()=>setRevision(n=>n+1)}>重新加载</button>}</div>}
  </div>
  {chooser&&<Dialog label="已触价合约" busy={false} onDismiss={()=>setChooser(false)}>{dismiss=><><h2>已触价合约</h2><p className="muted">{current?.week} · 最新触价优先</p><label className="input-shell"><input aria-label="搜索已触价合约" placeholder="搜索合约" value={search} onChange={e=>setSearch(e.target.value)}/></label><div className="fib-choices">{current?.items.filter(r=>r.symbol.includes(search.trim().toUpperCase())).map(r=><button key={r.symbol} aria-pressed={r.symbol===symbol} onClick={()=>{select(r.symbol);dismiss()}}><b>{r.symbol.replace('-USDT-SWAP','/USDT')}</b><span>{touchLabel(latestFibTouch(r),clock)}</span></button>)}</div></>}</Dialog>}
 </section>;
});
