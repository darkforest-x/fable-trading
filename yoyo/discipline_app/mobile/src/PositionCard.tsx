/** Stitch ba69e302d34d4f3e86f6faaf1ca766c7; PnL/ROI use remaining size, before fees. */
import type {Position,State} from './api';
import {CoinLogo} from './CoinLogo';
import {Icon} from './Icon';
import {Spinner} from './Dialog';
const fmt=(v:number|undefined,d=2)=>v===undefined?'—':v.toLocaleString('en-US',{minimumFractionDigits:d,maximumFractionDigits:d});
export function PositionCard({pos,state,online,busy,entryStopBusy=false,compact=false,onEntryStop,onClose}:{pos:Position;state:State;online:boolean;busy:boolean;entryStopBusy?:boolean;compact?:boolean;onEntryStop:(p:Position)=>void;onClose:(p:Position)=>void}){
 const q=state.quotes[pos.symbol],entry=Number(pos.entry),remaining=Number(pos.remaining),direction=pos.side==='long'?1:-1;
 const fresh=online&&!!q&&state.server_time-q.time>=0&&state.server_time-q.time<=(state.policy?.quote_max_age_seconds??15);
 const pnl=q?(Number(q.last)-entry)*remaining*direction:undefined;
 const margin=entry*remaining/pos.leverage,roi=pnl!==undefined&&margin>0?pnl/margin*100:undefined;
 const protectedEntry=(Number(pos.stop)-entry)*direction>=0;
 const allowed=fresh&&!!q&&(Number(q.last)-entry)*direction>0&&!protectedEntry&&(state.mode!=='okx_live'||pos.protection==='exchange-verified');
 const decimals=Math.min(12,Math.max(2,Math.ceil(-Math.log10(Number(state.instruments[pos.symbol]?.tick??.01)))));
 const coin=pos.symbol.replace('-USDT-SWAP','');
 return <article className={`position-lite ${compact?'compact':''}`}>
  <div className="position-lite-head"><CoinLogo coin={coin}/><b>{coin}<small> / USDT</small></b><span className={direction===1?'long':'short'}>{direction===1?'多':'空'} · {pos.leverage}x</span></div>
  <div className={`position-profit ${pnl!==undefined&&pnl<0?'short':'long'}`}><div><b>{pnl!==undefined&&pnl>0?'+':''}{fmt(pnl)}<small> USDT</small></b><span>浮动盈亏{!fresh?' · 待更新':''}</span></div><div><b>{roi!==undefined&&roi>0?'+':''}{fmt(roi)}<small>%</small></b><span>收益率</span></div></div>
  <dl className="position-lite-stats"><div><dt>持仓金额 · U</dt><dd>{fmt((q?Number(q.last):entry)*remaining)}</dd></div><div><dt>开仓均价</dt><dd>{fmt(entry,decimals)}</dd></div><div><dt>当前止损</dt><dd>{fmt(Number(pos.stop),decimals)}</dd></div></dl>
  <div className="position-lite-actions"><button disabled={busy||!allowed} aria-busy={entryStopBusy} onClick={()=>onEntryStop(pos)}>{entryStopBusy?<><Spinner/>设置中…</>:<><Icon name="shield" size={15}/>{protectedEntry?'已保护开仓价':'止损到开仓价'}</>}</button><button disabled={busy||!online} onClick={()=>onClose(pos)}>平仓</button></div>
  {!compact&&<details className="position-detail"><summary>自动保护与计算口径<Icon name="chevron" size={13}/></summary><p>{pos.policy.breakeven_r}R 成本保本 · {pos.policy.take_profits.map(([r,f],i)=>`${r}R ${i===pos.policy.take_profits.length-1?'全平':`平 ${Number(f)*100}%`}`).join(' · ')}</p><p>初始风险 1R = {fmt(Number(pos.risk))} USDT · 当前 {fmt(pos.current_r===undefined?undefined:Number(pos.current_r))} R</p><p>{state.mode==='okx_live'?(pos.protection==='exchange-verified'?'交易所保护已核对':'交易所保护待核对'):'服务端模拟保护'} · {pos.breakeven?'已移成本保本':'自动保本待触发'}</p><p>浮盈按最新价和剩余仓位估算，未扣费用；收益率＝浮盈 ÷ 剩余仓位开仓保证金。移到开仓价不含手续费。</p></details>}
 </article>;
}
