/** 通知 sheet (Stitch alarm-notify). Lists market alarms and this week's 1.618 touches with their
 * push outcome; tapping opens the coin. Read-only: acknowledging alarms stays in the workbench. */
import {useState} from 'react';
import {Dialog} from './Dialog';
import {Icon} from './Icon';
import {feedback} from './feedback';
import {clockText,coin,edgeText,groupByDay,pushStatusText,type NotificationEvent,type NotificationSnapshot} from './monitorFormat';

type Filter='all'|'alarm'|'fib';
export function NotificationCenter({data,error,onOpen,onDismiss}:{data:NotificationSnapshot|null;error:string;onOpen:(event:NotificationEvent)=>void;onDismiss:()=>void}){
 const [filter,setFilter]=useState<Filter>('all'),[all,setAll]=useState(false);
 const rows=(data?.events??[]).filter(e=>(filter==='all'||e.kind===filter)&&(all||e.pushed||e.kind==='alarm'));
 return <Dialog label="通知" busy={false} onDismiss={onDismiss}>{dismiss=><div className="notify-sheet">
  <h2>通知</h2>
  <div className="segment notify-filter" role="tablist" aria-label="通知类型">{([['all','全部'],['alarm','报警'],['fib','1.618']] as const).map(([k,l])=><button key={k} role="tab" aria-selected={filter===k} aria-pressed={filter===k} className={filter===k?'active':''} onClick={()=>{setFilter(k);feedback('light')}}>{l}</button>)}</div>
  <label className="notify-all"><input type="checkbox" checked={all} onChange={e=>setAll(e.target.checked)}/><span>显示未推送的触价</span></label>
  {error&&<p className="monitor-error" role="status"><Icon name="refresh" size={15}/>{error}</p>}
  {!data&&!error&&<p className="monitor-loading" role="status">正在读取</p>}
  {data&&!rows.length&&<p className="monitor-empty">暂无通知</p>}
  {groupByDay(rows,e=>e.at_ms,data?.now_ms).map(group=><section key={group.label} className="notify-day"><h3>{group.label}</h3>{group.rows.map(e=><button key={e.id} className="notify-row" onClick={()=>{onOpen(e);dismiss()}}>
   <span className={`notify-icon ${e.kind}`}><Icon name={e.kind==='alarm'?'bell':'fib'} size={18}/></span>
   <span className="row-main"><b>{e.kind==='alarm'?`全市场插针 · ${edgeText(e.side)} · ${e.n} 币`:`${coin(e.symbol??'')} · ${edgeText(e.side)}`}</b><small>{clockText(e.at_ms,data?.now_ms)}{e.kind==='alarm'&&e.active?' · 进行中':''}</small></span>
   <span className={`push-pill ${e.pushed?'pushed':e.kind==='alarm'&&e.test?'test':'muted'}`}>{e.pushed&&e.kind==='fib'?'Bark ':''}{pushStatusText(e)}</span>
  </button>)}</section>)}
  <p className="micro">App 关闭时的推送经 Bark 送达。确认报警仍在监控工作台操作。</p>
 </div>}</Dialog>;
}
