/** Stitch 4066f7b336e24b40b9c9334f8abedd22: compact local watchlist controls. */
import {useState} from 'react';
import {Dialog} from './Dialog';
import {CoinLogo} from './CoinLogo';
import {Icon} from './Icon';
import {touchLabel,latestFibTouch,type FibContract} from './fibData';
import {feedback} from './feedback';
export function FibChooser({rows,symbol,week,clock,favorites,onSelect,onFavorite,onDelete,onRestore,onDismiss}:{rows:FibContract[];symbol:string;week:string;clock:number;favorites:string[];onSelect:(symbol:string)=>void;onFavorite:(symbol:string)=>void;onDelete:(symbol:string)=>void;onRestore:(symbol:string)=>void;onDismiss:()=>void}){
 const [search,setSearch]=useState(''),[onlyFavorites,setOnlyFavorites]=useState(false),[deleted,setDeleted]=useState('');
 const visible=rows.filter(r=>(!onlyFavorites||favorites.includes(r.symbol))&&r.symbol.includes(search.trim().toUpperCase()));
 return <Dialog label="已触价合约" busy={false} onDismiss={onDismiss}>{dismiss=><>
  <h2>已触价合约</h2><p className="muted">{week} · 最新触价优先</p>
  <label className="input-shell"><input aria-label="搜索已触价合约" placeholder="搜索合约" value={search} onChange={e=>setSearch(e.target.value)}/></label>
  <div className="catalog-tabs fib-filter" aria-label="已触价列表筛选">{[false,true].map(value=><button key={String(value)} aria-pressed={onlyFavorites===value} className={onlyFavorites===value?'active':''} onClick={()=>{setOnlyFavorites(value);feedback('light')}}>{value?'收藏':'全部'}</button>)}</div>
  <div className="fib-choices">{visible.map(row=>{const coin=row.symbol.replace('-USDT-SWAP','');return <div className="fib-choice" key={row.symbol} data-selected={row.symbol===symbol}>
   <button className="fib-choice-select" aria-label={`查看 ${coin} K线`} aria-pressed={row.symbol===symbol} onClick={()=>{onSelect(row.symbol);dismiss()}}><CoinLogo coin={coin}/><span className="fib-choice-copy"><b>{coin}<small>/USDT</small></b><span>{touchLabel(latestFibTouch(row),clock)}</span></span></button>
   <button className="fib-favorite" aria-label={`${favorites.includes(row.symbol)?'取消收藏':'收藏'} ${coin}`} aria-pressed={favorites.includes(row.symbol)} onClick={()=>{onFavorite(row.symbol);feedback('light')}}><Icon name="star" size={19}/></button>
   <button className="fib-delete" aria-label={`删除 ${coin}`} onClick={()=>{onDelete(row.symbol);setDeleted(row.symbol);feedback('medium')}}><Icon name="trash" size={18}/></button>
  </div>})}{!visible.length&&<p className="fib-list-empty">{search?'没有匹配的合约':onlyFavorites?'暂无收藏，点星标即可收藏':'列表已清空'}</p>}</div>
  {deleted&&<div className="fib-undo" role="status"><span>已删除 {deleted.replace('-USDT-SWAP','')}</span><button onClick={()=>{onRestore(deleted);setDeleted('');feedback('light')}}>撤销</button></div>}
 </>}</Dialog>;
}
