/** Stitch 29ef83710fe44599b3efc0c0be7e49ab: default, chart-first Monday workspace. */
import {lazy,Suspense,useEffect,useMemo,useRef,useState} from 'react';
import {api,SERVICE_URL,type Connection,type State} from './api';
import {Dialog} from './Dialog';
import {Icon} from './Icon';
import {fibRangeFor,fibSelection,latestFibTouch,parseFibSnapshot,touchLabel,type FibContract,type FibSnapshot} from './fibData';
import type {CandlePeriod} from './candleData';
import './fib.css';
const CandlePanel=lazy(()=>import('./CandlePanel'));
export function FibHome({connection,state,onQuick,locked}:{connection:Connection|null;state:State|null;onQuick:(row:FibContract,period:CandlePeriod)=>void;locked:boolean}){
 const [data,setData]=useState<FibSnapshot|null>(null),[symbol,setSymbol]=useState(''),[error,setError]=useState('');
 const [period,setPeriod]=useState<CandlePeriod>('15m'),[chooser,setChooser]=useState(false),[search,setSearch]=useState('');
 const [revision,setRevision]=useState(0),[clock,setClock]=useState(Date.now());
 const previous=useRef<{week?:string;symbol:string}>({symbol:''});
 useEffect(()=>{let active=true,generation=0,timer:ReturnType<typeof setTimeout>,controller:AbortController|null=null;
  const poll=async()=>{if(!active||document.hidden)return;const version=++generation;controller=new AbortController();const timeout=setTimeout(()=>controller?.abort(),9000);
   try{const next=parseFibSnapshot(await api<FibSnapshot>({url:SERVICE_URL,token:''},'/v1/market/monday-fib',undefined,controller.signal));if(active&&version===generation){const selected=fibSelection(previous.current.symbol,previous.current.week,next);previous.current={week:next.week,symbol:selected};setData(next);setSymbol(selected);setError('');setClock(Date.now())}}
   catch{if(active&&version===generation)setError('触价监控暂未连接')}
   finally{clearTimeout(timeout);if(active&&version===generation&&!document.hidden)timer=setTimeout(poll,5000)}
  };
  const visibility=()=>{generation++;clearTimeout(timer);controller?.abort();if(!document.hidden)void poll()};
  document.addEventListener('visibilitychange',visibility);void poll();return()=>{active=false;clearTimeout(timer);controller?.abort();document.removeEventListener('visibilitychange',visibility)};
 },[revision]);
 useEffect(()=>{const timer=setInterval(()=>setClock(Date.now()),30000);return()=>clearInterval(timer)},[]);
 const row=data?.items.find(r=>r.symbol===symbol),touch=row?latestFibTouch(row):null;
 const range=useMemo(()=>row&&data?fibRangeFor(row,data):undefined,[symbol,data?.monday_end_ms,data?.next_week_ms,row?.high,row?.low,row?.upper,row?.lower]);
 const live=!!data?.connected&&!!data.last_message_ms&&clock-data.last_message_ms<90000&&!error;
 function select(value:string){setSymbol(value);previous.current.symbol=value;setChooser(false)}
 const recent=data?.items[0];
 const tick=state?.instruments[symbol]?.tick??(row?String(10**-Math.min(12,Math.max(2,...[row.high,row.low].map(n=>String(n).split('.')[1]?.length??0)))):undefined);
 return <section className="fib-home" aria-label="1.618 行情" data-no-swipe>
  <div className="fib-screen" inert={chooser}>
   <header className="fib-top"><button className="fib-symbol" aria-label="选择已触价合约" onClick={()=>{setSearch('');setChooser(true)}} disabled={!data?.items.length}><i className={live?'live':''}/><b>{symbol?symbol.replace('-USDT-SWAP',''):'1.618'}<small>{symbol?'/USDT':''}</small></b><Icon name="chevron" size={13}/></button></header>
   {row&&touch?<>
    <div className="fib-selected"><span>{touchLabel(touch,clock)}</span>{error&&<span>连接延迟</span>}</div>
    {recent&&recent.symbol!==symbol&&<button className="fib-new" onClick={()=>select(recent.symbol)}>查看最新触价 · {recent.symbol.replace('-USDT-SWAP','')} <Icon name="arrow" size={12}/></button>}
    <div className="fib-chart"><Suspense fallback={<div className="fib-empty">正在加载图表…</div>}><CandlePanel symbol={symbol} connection={connection} tick={tick} period={period} onPeriod={setPeriod} onCollapse={()=>{}} embedded range={range}/></Suspense></div>
    <div className="fib-quick"><button disabled={locked||!!error} onClick={()=>onQuick(row,period)}>快捷开单 <span>· 1R</span><Icon name="arrow" size={17}/></button></div>
   </>:<div className="fib-empty"><Icon name="trade" size={34}/><h2>{error?'监控暂不可用':data?'本周暂无触价':'正在读取触价记录'}</h2><p>{error?'稍后重试，已有交易保护保持独立运行。':data&&!data.monday_complete?'纽约周一结束后锁定高低点。':'首次触及区间外 1.618 的合约会显示在这里。'}</p>{error&&<button onClick={()=>setRevision(n=>n+1)}>重新加载</button>}</div>}
  </div>
  {chooser&&<Dialog label="已触价合约" busy={false} onDismiss={()=>setChooser(false)}><h2>已触价合约</h2><p className="muted">{data?.week} · 最新触价优先</p><label className="input-shell"><input aria-label="搜索已触价合约" placeholder="搜索合约" value={search} onChange={e=>setSearch(e.target.value)}/></label><div className="fib-choices">{data?.items.filter(r=>r.symbol.includes(search.trim().toUpperCase())).map(r=><button key={r.symbol} aria-pressed={r.symbol===symbol} onClick={()=>select(r.symbol)}><b>{r.symbol.replace('-USDT-SWAP','/USDT')}</b><span>{touchLabel(latestFibTouch(r),clock)}</span></button>)}</div></Dialog>}
 </section>;
}
