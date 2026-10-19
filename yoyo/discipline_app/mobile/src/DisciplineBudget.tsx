/** Stitch f9d37f90d1c9483b8913d6a7f1b753dc: one compact row, server-owned limits. */
import type {State} from './api';
export function DisciplineBudget({state,online,onOpen}:{state:State|null;online:boolean;onOpen:()=>void}){
 const d=state?.discipline;
 const fresh=online&&(!state?.private_at||state.server_time-state.private_at<=15)&&!state?.private_error;
 const halted=!!state?.daily?.locked_reason||!!state&&state.cooldown_until>state.server_time;
 return <button className={`discipline-budget ${halted?'halted':''} ${!fresh?'stale-budget':''}`} onClick={onOpen} aria-label={`查看纪律剩余额度${!fresh?'，等待更新':''}${halted?'，当前已停手':''}`}>
  <span className="budget-cells">
   <span><small>今日可开</small><b>{d?d.entries_remaining:'—'}<em>次</em></b></span>
   <span><small>再连亏停手</small><b>{d?d.losses_remaining:'—'}<em>笔</em></b></span>
   <span><small>日亏剩余{!fresh&&d?' · 上次':''}</small><b>{d?Number(d.daily_loss_remaining).toLocaleString('en-US',{maximumFractionDigits:2}):'—'}<em>U</em></b></span>
  </span>
 </button>;
}
