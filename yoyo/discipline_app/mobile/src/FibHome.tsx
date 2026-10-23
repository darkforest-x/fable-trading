/** Stitch 29ef83710fe44599b3efc0c0be7e49ab: default, chart-first Monday workspace. */
import {lazy,memo,Suspense,useEffect,useMemo,useRef,useState} from 'react';
import {api,SERVICE_URL,type Connection,type State} from './api';
import {REFRESH_EVENT,type RefreshRequest} from './refreshEvent';
import {FibChooser} from './FibChooser';
import {favoriteFibSymbol,hideFibSymbol,restoreFibSymbol,parseFibPreferences,visibleFibSymbol,type FibPreferences} from './fibPreferences';
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
 const [period,setPeriod]=useState<CandlePeriod>(saved?.period??'15m'),[chooser,setChooser]=useState(false);
 const [revision,setRevision]=useState(0),[clock,setClock]=useState(Date.now());
 const preferenceKey='spike-fib-preferences-v1:'+SERVICE_URL;
 const [listPrefs,setListPrefs]=useState(()=>{try{return parseFibPreferences(localStorage.getItem(preferenceKey))}catch{return parseFibPreferences(null)}});
 const listRef=useRef(listPrefs);listRef.current=listPrefs;
 function updatePrefs(change:(value:FibPreferences)=>FibPreferences){setListPrefs(value=>{const next=change(value);try{localStorage.setItem(preferenceKey,JSON.stringify(next))}catch{}return next})}
 const previous=useRef<{week?:string;symbol:string}>({week:saved?.data.week,symbol:saved?.symbol??''});
 const preferences=useRef({symbol,period});preferences.current={symbol,period};
 const enabled=active&&!covered;
 useEffect(()=>{if(!enabled){setFresh(false);return}let active=true,generation=0,timer:ReturnType<typeof setTimeout>,controller:AbortController|null=null;
  const poll=async(manual=false)=>{if(!active||document.hidden)return;setClock(Date.now());const version=++generation,requestController=new AbortController();controller=requestController;const timeout=setTimeout(()=>requestController.abort(),9000);
   try{const next=parseFibSnapshot(await api<FibSnapshot>({url:SERVICE_URL,token:''},'/v1/market/monday-fib',undefined,requestController.signal));if(!currentFibWeek(next))throw Error('区间周次过期');if(active&&version===generation){const selected=fibSelection(previous.current.symbol,previous.current.week,{...next,items:next.items.filter(row=>visibleFibSymbol(listRef.current,next.week,row.symbol))});previous.current={week:next.week,symbol:selected};setData(next);setSymbol(selected);setError('');setFresh(true);setClock(Date.now());cache.write(next,selected,preferences.current.period)}}
   catch{if(active&&version===generation){setError('触价监控暂未连接');setFresh(false)}if(manual)throw Error('触价监控暂未连接')}
   finally{clearTimeout(timeout);if(active&&version===generation&&!document.hidden)timer=setTimeout(()=>void poll(),5000)}
  };
  const visibility=()=>{setFresh(false);generation++;clearTimeout(timer);controller?.abort();if(!document.hidden)void poll()};
  const reload=(event:Event)=>{clearTimeout(timer);generation++;controller?.abort();(event as CustomEvent<RefreshRequest>).detail.tasks.push(poll(true))};
  window.addEventListener(REFRESH_EVENT,reload);
  document.addEventListener('visibilitychange',visibility);void poll();return()=>{window.removeEventListener(REFRESH_EVENT,reload);active=false;clearTimeout(timer);controller?.abort();document.removeEventListener('visibilitychange',visibility)};
 },[revision,enabled,cache]);
 useEffect(()=>{if(!active)return;setClock(Date.now());const timer=setInterval(()=>setClock(Date.now()),30000);return()=>clearInterval(timer)},[active]);
 const current=data&&currentFibWeek(data,Math.max(clock,Date.now()))?data:null;
 const visibleRows=current?.items.filter(r=>visibleFibSymbol(listPrefs,current.week,r.symbol))??[];
 const row=visibleRows.find(r=>r.symbol===symbol)??visibleRows[0],touch=row?latestFibTouch(row):null;
 const selectedSymbol=row?.symbol??'';
 useEffect(()=>{if(current&&symbol!==(row?.symbol??'')){const next=row?.symbol??'';setSymbol(next);previous.current.symbol=next;cache.write(current,next,period)}},[row?.symbol,current?.week,symbol]);
 const range=useMemo(()=>row&&data?fibRangeFor(row,data):undefined,[selectedSymbol,data?.monday_end_ms,data?.next_week_ms,row?.high,row?.low,row?.upper,row?.lower]);
 const live=fresh&&!!current?.connected&&!!current.last_message_ms&&clock-current.last_message_ms<90000&&!error;
 function select(value:string){if(value!==symbol)feedback('light');setSymbol(value);previous.current.symbol=value;if(current)cache.write(current,value,period)}
 function changePeriod(value:CandlePeriod){if(value!==period)feedback('light');setPeriod(value);if(current)cache.write(current,symbol,value)}
 const recent=visibleRows[0];
 const tick=state?.instruments[selectedSymbol]?.tick??(row?String(10**-Math.min(12,Math.max(2,...[row.high,row.low].map(n=>String(n).split('.')[1]?.length??0)))):undefined);
 return <section className="fib-home" aria-label="1.618 行情" hidden={!active} inert={!active} data-no-swipe>
  <div className="fib-screen" inert={chooser}><div className="fib-refresh-zone" aria-hidden="true"/>
   <header className="fib-top"><button className="fib-symbol" aria-label="选择已触价合约" onClick={()=>setChooser(true)} disabled={!current}><i className={live?'live':''}/><b>{row?selectedSymbol.replace('-USDT-SWAP',''):'1.618'}<small>{row?'/USDT':''}</small></b><Icon name="chevron" size={13}/></button></header>
   {row&&touch?<>
    <div className="fib-selected"><span>{touchLabel(touch,clock)}</span>{!fresh&&<span role="status">{error?'离线缓存':'缓存 · 更新中'}</span>}</div>
    {recent&&recent.symbol!==selectedSymbol&&<button className="fib-new" onClick={()=>select(recent.symbol)}>查看最新触价 · {recent.symbol.replace('-USDT-SWAP','')} <Icon name="arrow" size={12}/></button>}
    <div className="fib-chart"><Suspense fallback={<div className="fib-empty">正在加载图表…</div>}><CandlePanel symbol={selectedSymbol} connection={connection} tick={tick} period={period} onPeriod={changePeriod} onCollapse={noCollapse} active={enabled&&!chooser} embedded range={range}/></Suspense></div>
    <div className="fib-quick"><button disabled={locked||!fresh||!live} onClick={()=>onQuick(row,period)}>快捷开单 <span>· 1R</span><Icon name="arrow" size={17}/></button></div>
   </>:<div className="fib-empty"><Icon name="trade" size={34}/><h2>{error?'监控暂不可用':current?'本周暂无触价':'正在读取触价记录'}</h2><p>{error?'稍后重试，已有交易保护保持独立运行。':current&&!current.monday_complete?'纽约周一结束后锁定高低点。':'首次触及区间外 1.618 的合约会显示在这里。'}</p>{error&&<button onClick={()=>setRevision(n=>n+1)}>重新加载</button>}</div>}
  </div>
  {chooser&&<FibChooser rows={visibleRows} symbol={selectedSymbol} week={current?.week??''} clock={clock} favorites={listPrefs.favorites} onSelect={select} onFavorite={coin=>updatePrefs(p=>favoriteFibSymbol(p,coin))} onDelete={coin=>updatePrefs(p=>hideFibSymbol(p,current?.week??'',coin))} onRestore={coin=>updatePrefs(p=>restoreFibSymbol(p,current?.week??'',coin))} onDismiss={()=>setChooser(false)}/>}

 </section>;
});
