/** Stable virtual catalogue; Stitch 074cfa263587444ba4f8257009bbc6d8. */
import {useDeferredValue,useLayoutEffect,useMemo,useRef,useState} from 'react';
import type {State} from './api';
import {CoinLogo} from './CoinLogo';
import {Icon} from './Icon';
import {feedback} from './feedback';
import {parseFavorites} from './tradeExperience';
import {dayChange,stableRanking,type RankCache,compareContracts,turnoverLabel,type ContractSort} from './contractRanking';
const ROW=80,OVERSCAN=3;
export function ContractOptions({state,search,selected,onSelect,sort='volume'}:{sort?:ContractSort;state:State|null;search:string;selected:string;onSelect:(value:string)=>void}){
 const viewport=useRef<HTMLDivElement>(null);
 const [top,setTop]=useState(0),[height,setHeight]=useState(470),[active,setActive]=useState(0),[rankVersion,setRankVersion]=useState(0);
 const [filter,setFilter]=useState<'all'|'favorites'|'recent'>('all');
 const [favorites,setFavorites]=useState(()=>{try{return parseFavorites(localStorage.getItem('spike-favorites'))}catch{return []}});
 const rankCache=useRef<RankCache|null>(null);
 rankCache.current=stableRanking(rankCache.current,state?.quotes??{},state?.server_time??0,sort,rankVersion);
 const metrics=rankCache.current.metrics;
 const query=useDeferredValue(search.trim().toUpperCase().replaceAll('/','').replaceAll('-',''));
 const recent=useMemo(()=>[...(state?.positions??[]).map(p=>({symbol:p.symbol,time:p.opened??0})),...(state?.pending??[]).map(p=>({symbol:p.symbol,time:p.created??0})),...(state?.closed??[]).map(p=>({symbol:p.symbol,time:p.opened??0}))].sort((a,b)=>b.time-a.time).map(p=>p.symbol),[state?.positions,state?.pending,state?.closed]);
 const values=useMemo(()=>Object.keys(state?.instruments??{}).filter(value=>state!.instruments[value].tradable!==false&&(filter==='all'||filter==='favorites'&&favorites.includes(value)||filter==='recent'&&recent.includes(value))&&value.replaceAll('-','').includes(query)).sort((a,b)=>compareContracts(a,b,metrics,sort)),[state?.instruments,query,sort,metrics,filter,favorites,recent]);
 useLayoutEffect(()=>{setTop(0);setActive(0);if(viewport.current)viewport.current.scrollTop=0},[search,sort,filter,rankVersion]);
 useLayoutEffect(()=>{const node=viewport.current;if(!node)return;const observer=new ResizeObserver(()=>setHeight(node.clientHeight));observer.observe(node);return()=>observer.disconnect()},[]);
 const start=Math.max(0,Math.floor(top/ROW)-OVERSCAN),end=Math.min(values.length,Math.ceil((top+height)/ROW)+OVERSCAN+1);
 const rowId=(index:number)=>`contract-${values[index]}`;
 function move(index:number){const next=Math.max(0,Math.min(values.length-1,index));setActive(next);const node=viewport.current;if(!node)return;if(next*ROW<node.scrollTop)node.scrollTop=next*ROW;else if((next+1)*ROW>node.scrollTop+node.clientHeight)node.scrollTop=(next+1)*ROW-node.clientHeight;setTop(Math.floor(node.scrollTop/ROW)*ROW)}
 function favorite(value:string){const next=favorites.includes(value)?favorites.filter(v=>v!==value):[...favorites,value];setFavorites(next);try{localStorage.setItem('spike-favorites',JSON.stringify(next))}catch{/* Preferences remain usable in memory. */}feedback('light')}
 return <>
  <div className="catalog-tabs" aria-label="合约范围">{(['all','favorites','recent'] as const).map((v,i)=><button key={v} aria-pressed={filter===v} onClick={()=>{setFilter(v);feedback('light')}}>{['全部','自选','最近交易'][i]}</button>)}<button className="refresh-rank" onClick={()=>{setRankVersion(v=>v+1);feedback('light')}}>更新排名</button></div>
  <div ref={viewport} className="searchable-contracts virtual-contracts" role="listbox" aria-label="合约列表" tabIndex={0} aria-activedescendant={active>=start&&active<end?rowId(active):undefined} onScroll={e=>setTop(Math.floor(e.currentTarget.scrollTop/ROW)*ROW)} onKeyDown={e=>{
   if(e.target!==e.currentTarget)return;
   if(['ArrowDown','ArrowUp','Home','End','Enter',' '].includes(e.key)){e.preventDefault();if(e.key==='ArrowDown')move(active+1);else if(e.key==='ArrowUp')move(active-1);else if(e.key==='Home')move(0);else if(e.key==='End')move(values.length-1);else if(values[active])onSelect(values[active])}
  }}>
  {!values.length?<p className="micro catalog-empty">{search?'没有匹配的合约':filter==='favorites'?'点亮星标，添加常用合约':filter==='recent'?'交易过的合约会显示在这里':'暂无可交易合约'}</p>:<div style={{height:Math.max(0,values.length*ROW-10),position:'relative'}}>{values.slice(start,end).map((value,offset)=>{
   const coin=value.replace('-USDT-SWAP',''),index=start+offset,chosen=value===selected;
   const decimals=Math.min(12,Math.max(2,Math.ceil(-Math.log10(Number(state?.instruments[value]?.tick??.01)))));
   const price=state?.quotes[value]?.last,change=state?.quotes[value]?dayChange(state.quotes[value],state.server_time):null;
   return <div className={`contract-row ${chosen?'selected':''}`} style={{position:'absolute',top:index*ROW,height:70}} key={value}>
    <button id={rowId(index)} role="option" aria-selected={chosen} aria-posinset={index+1} aria-setsize={values.length} tabIndex={-1} className="contract-option" onClick={()=>onSelect(value)}><CoinLogo coin={coin}/><span className="contract-option-copy"><b>{coin}<small> / USDT</small></b><span className="contract-volume" title={sort==='volume'?'24h 估算成交额：基准币成交量 × 最新价':undefined}>{sort==='volume'?turnoverLabel(metrics[value]??null):'永续'}</span></span><span className="contract-price">{price?Number(price).toLocaleString('en-US',{minimumFractionDigits:decimals,maximumFractionDigits:decimals}):'—'}<small className={change!==null&&change<0?'short':'long'}>{change===null?'—':`${change>0?'+':''}${change.toFixed(2)}%`}</small></span>{chosen&&<span className="selection-mark"><Icon name="check" size={15}/></span>}</button>
    <button className="favorite-toggle" aria-label={`${favorites.includes(value)?'取消自选':'加入自选'} ${coin}`} aria-pressed={favorites.includes(value)} onClick={()=>favorite(value)}>{favorites.includes(value)?'★':'☆'}</button>
   </div>
  })}</div>}
  </div>
 </>;
}
