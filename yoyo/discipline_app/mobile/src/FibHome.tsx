import {ThemeToggle} from './ThemeToggle';
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
import {CHART_FOLLOW_EVENT} from './chartPalette';
const CHART_PREFS_KEY='spike-fib-chart-v1';
const FIB_PERIODS:CandlePeriod[]=['1m','5m','15m','1H','4H'];
const touchTime=(ms:number)=>new Intl.DateTimeFormat('zh-CN',{timeZone:'Asia/Shanghai',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false}).format(ms).replace(/\//g,'/');
const CandlePanel=lazy(()=>import('./CandlePanel'));
const noCollapse=()=>{};
export const FibHome=memo(function FibHome({connection,state,onQuick,onOpenReplay,locked,active,covered,focus}:{connection:Connection|null;state:State|null;onQuick:(row:FibContract,period:CandlePeriod)=>void;onOpenReplay:()=>void;locked:boolean;active:boolean;covered:boolean;focus?:{symbol:string;nonce:number}}){
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
 // A notification can ask for a coin; selection rules (this week's touched rows) still apply on the next poll.
 useEffect(()=>{if(focus?.symbol){setSymbol(focus.symbol);previous.current.symbol=focus.symbol;if(current)cache.write(current,focus.symbol,period)}},[focus?.nonce]);
 function changePeriod(value:CandlePeriod){if(value!==period)feedback('light');setPeriod(value);if(current)cache.write(current,symbol,value)}
 const recent=visibleRows[0];
 const tick=state?.instruments[selectedSymbol]?.tick??(row?String(10**-Math.min(12,Math.max(2,...[row.high,row.low].map(n=>String(n).split('.')[1]?.length??0)))):undefined);
 const [chartPrefs,setChartPrefs]=useState(()=>{try{const v=JSON.parse(localStorage.getItem(CHART_PREFS_KEY)??'{}');return {internal:v.internal===true,averages:v.averages===true}}catch{return {internal:false,averages:false}}});
 function toggleChart(key:'internal'|'averages'){feedback('light');setChartPrefs(prev=>{const next={...prev,[key]:!prev[key]};try{localStorage.setItem(CHART_PREFS_KEY,JSON.stringify(next))}catch{}return next})}
 const follow=()=>{window.dispatchEvent(new Event(CHART_FOLLOW_EVENT));feedback('light')};
 const decimals=Math.min(12,Math.max(2,Math.ceil(-Math.log10(Number(tick)||.01))));
 const coin=selectedSymbol.replace('-USDT-SWAP','');
 // Workbench 1.618 replica (Stitch fib-replica 7b01226870fd488091328c4844117791): full-bleed chart,
 // floating price card, 列表 capsule and one control dock. Orders still go through the app ticket.
 return <section className="fib-home fib-replica" aria-label="1.618 行情" hidden={!active} inert={!active} data-no-swipe>
  <div className="fib-screen" inert={chooser}><div className="fib-refresh-zone" aria-hidden="true"/>
   {row&&touch?<>
    <div className="fib-chart"><Suspense fallback={<div className="fib-empty">正在加载图表…</div>}><CandlePanel symbol={selectedSymbol} connection={connection} tick={tick} period={period} onPeriod={changePeriod} onCollapse={noCollapse} active={enabled&&!chooser} embedded range={range} internal={chartPrefs.internal} averages={chartPrefs.averages}/></Suspense></div>
    <div className="fib-price-card">
     <div className="fib-price-head"><b>{coin}</b><span className="fib-tag">USDT 永续</span>{!fresh&&<span className="fib-cache" role="status">{error?'离线缓存':'更新中'}</span>}</div>
     <strong className="t-num">{row.price!==null&&Number.isFinite(row.price)?row.price.toFixed(decimals):'—'}</strong>
     <p className={`fib-touch ${touch.side}`}><i/>{touch.side==='upper'?'上沿':'下沿'} 1.618 · {touchTime(touch.occurred_ms)}<button onClick={follow}>跟随最新</button></p>
    </div>
    <button className="fib-list-button" aria-label="打开触价列表" onClick={()=>{setChooser(true);feedback('light')}} disabled={!current}><i className={live?'live':''}/>列表<b className="t-num">{visibleRows.length}</b></button>
    {recent&&recent.symbol!==selectedSymbol&&<button className="fib-new" onClick={()=>select(recent.symbol)}>最新触价 · {recent.symbol.replace('-USDT-SWAP','')} <Icon name="arrow" size={12}/></button>}
    <div className="fib-dock" role="group" aria-label="图表控制">
     <label className="fib-period"><select aria-label="K线周期" value={period} onChange={e=>changePeriod(e.target.value as CandlePeriod)}>{FIB_PERIODS.map(p=><option key={p} value={p}>{p}</option>)}</select><Icon name="chevron" size={12}/></label>
     <label className="fib-toggle"><span>内部线</span><input type="checkbox" role="switch" aria-label="内部线 0.5 · 0.618 · 0.705" checked={chartPrefs.internal} onChange={()=>toggleChart('internal')}/><i aria-hidden="true"/></label>
     <label className="fib-toggle"><span>六均线</span><input type="checkbox" role="switch" aria-label="六均线 SMA / EMA 20 · 60 · 120" checked={chartPrefs.averages} onChange={()=>toggleChart('averages')}/><i aria-hidden="true"/></label>
     <button className="fib-follow" onClick={follow}><Icon name="refresh" size={14}/>跟随最新</button>
    </div>
    <div className="fib-actions"><button className="fib-quick-main" disabled={locked||!fresh||!live} onClick={()=>onQuick(row,period)}><Icon name="arrow" size={16}/>快捷开单<span>· 1R</span></button><button className="fib-replay-chip" onClick={onOpenReplay}><i/>模拟盘</button></div>
   </>:<div className="fib-empty"><Icon name="fib" size={34}/><h2>{error?'监控暂不可用':current?'本周暂无触价':'正在读取触价记录'}</h2><p>{error?'稍后重试，已有交易保护保持独立运行。':current&&!current.monday_complete?'纽约周一结束后锁定高低点。':'首次触及区间外 1.618 的合约会显示在这里。'}</p>{error&&<button onClick={()=>setRevision(n=>n+1)}>重新加载</button>}<button className="fib-replay-chip" onClick={onOpenReplay}><i/>模拟盘</button></div>}
  </div>
  {chooser&&<FibChooser rows={visibleRows} symbol={selectedSymbol} week={current?.week??''} clock={clock} favorites={listPrefs.favorites} onSelect={select} onFavorite={coin=>updatePrefs(p=>favoriteFibSymbol(p,coin))} onDelete={coin=>updatePrefs(p=>hideFibSymbol(p,current?.week??'',coin))} onRestore={coin=>updatePrefs(p=>restoreFibSymbol(p,current?.week??'',coin))} onDismiss={()=>setChooser(false)}/>}

 </section>;
});
