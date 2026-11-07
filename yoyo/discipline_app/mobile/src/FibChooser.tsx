/** 最新触价 list (workbench replica, Stitch fib-list 5676697171fc49b2a25b25985033631d).
 * Search, 全部/上沿/下沿/收藏, coin rows with the touched edge and price; star and hide stay local. */
import {useState} from 'react';
import {Dialog} from './Dialog';
import {CoinLogo} from './CoinLogo';
import {Icon} from './Icon';
import {SegmentLens} from './SegmentLens';
import {latestFibTouch,type FibContract} from './fibData';
import {feedback} from './feedback';

type Filter='all'|'upper'|'lower'|'favorites';
const FILTERS:[Filter,string][]=[['all','全部'],['upper','上沿'],['lower','下沿'],['favorites','收藏']];
const when=(ms:number)=>new Intl.DateTimeFormat('zh-CN',{timeZone:'Asia/Shanghai',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false}).format(ms);
const priceOf=(value:number|null)=>value===null||!Number.isFinite(value)?'—':value>=1000?value.toLocaleString('en-US',{maximumFractionDigits:1}):String(Number(value.toPrecision(5)));

export function FibChooser({rows,symbol,week,favorites,onSelect,onFavorite,onDelete,onRestore,onDismiss}:{rows:FibContract[];symbol:string;week:string;clock:number;favorites:string[];onSelect:(symbol:string)=>void;onFavorite:(symbol:string)=>void;onDelete:(symbol:string)=>void;onRestore:(symbol:string)=>void;onDismiss:()=>void}){
 const [search,setSearch]=useState(''),[filter,setFilter]=useState<Filter>('all'),[deleted,setDeleted]=useState('');
 const query=search.trim().toUpperCase();
 const visible=rows.filter(r=>{const side=latestFibTouch(r).side;return (filter==='all'||(filter==='favorites'?favorites.includes(r.symbol):side===filter))&&r.symbol.includes(query)});
 return <Dialog label="最新触价" busy={false} onDismiss={onDismiss}>{dismiss=><div className="fib-list-sheet">
  <div className="fib-list-title"><h2>最新触价</h2><span className="count-pill t-num">{rows.length} 个</span><button className="sheet-close" aria-label="关闭" onClick={dismiss}><Icon name="close" size={16}/></button></div>
  <p className="micro">{week} 纽约周 · 最新触价在前</p>
  <label className="input-shell fib-search"><Icon name="search" size={16}/><input aria-label="搜索已触价合约" placeholder="搜索已触价合约" value={search} autoCapitalize="characters" onChange={e=>setSearch(e.target.value)}/>{search&&<button className="clear-search" aria-label="清空搜索" onClick={()=>setSearch('')}><Icon name="close" size={15}/></button>}</label>
  <div className="segment fib-list-filter" role="tablist" aria-label="触价筛选">{FILTERS.map(([key,label])=><button key={key} role="tab" aria-selected={filter===key} aria-pressed={filter===key} className={filter===key?'active':''} onClick={()=>{if(filter!==key){setFilter(key);feedback('light')}}}>{label}</button>)}<SegmentLens value={filter}/></div>
  <div className="fib-list-card">{visible.map(row=>{const coin=row.symbol.replace('-USDT-SWAP',''),touch=latestFibTouch(row),fav=favorites.includes(row.symbol);return <div className="fib-list-row" key={row.symbol} data-selected={row.symbol===symbol}>
   <button className="fib-list-select" aria-label={`查看 ${coin} K线`} aria-pressed={row.symbol===symbol} onClick={()=>{onSelect(row.symbol);dismiss()}}>
    <CoinLogo coin={coin}/><span className="row-main"><b>{coin}<small>/USDT</small></b><small className="t-num">{when(touch.occurred_ms)}</small></span>
    <span className="row-end"><i className={`edge-pill ${touch.side}`}>{touch.side==='upper'?'上沿':'下沿'} 1.618</i><b className="t-num">{priceOf(row.price)}</b></span>
   </button>
   <button className={`fib-favorite ${fav?'on':''}`} aria-label={`${fav?'取消收藏':'收藏'} ${coin}`} aria-pressed={fav} onClick={()=>{onFavorite(row.symbol);feedback('light')}}><Icon name="star" size={18}/></button>
   <button className="fib-delete" aria-label={`隐藏 ${coin}`} onClick={()=>{onDelete(row.symbol);setDeleted(row.symbol);feedback('medium')}}><Icon name="trash" size={17}/></button>
  </div>})}{!visible.length&&<p className="fib-list-empty">{query?'没有匹配的合约':filter==='favorites'?'暂无收藏，点星标即可收藏':'暂无触价'}</p>}</div>
  {deleted&&<div className="fib-undo" role="status"><span>已隐藏 {deleted.replace('-USDT-SWAP','')}</span><button onClick={()=>{onRestore(deleted);setDeleted('');feedback('light')}}>撤销</button></div>}
  <p className="micro fib-list-foot">点击列表切换图表 · 收藏与隐藏只保存在本机</p>
 </div>}</Dialog>;
}
