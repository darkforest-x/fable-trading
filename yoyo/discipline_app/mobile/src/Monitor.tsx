/** 监控 tab (owner 2026-11-06, Stitch iphone-glass-1.0.0: monitor-alerts 7e2f6b9148ec429b9ba732eaaffb35ed).
 * Three read-only workbench modules. Data comes only from `monitorGet` (GET /v1/monitor/*).
 * No control here submits orders, closes positions, acknowledges alarms or changes monitor
 * settings; tapping a coin only opens it on the 合约 page, where every rule still applies. */
import {useCallback,useEffect,useRef,useState} from 'react';
import type {Connection} from './api';
import {CoinLogo} from './CoinLogo';
import {Dialog,Spinner} from './Dialog';
import {Icon} from './Icon';
import {PageHeader} from './PageHeader';
import {SegmentLens} from './SegmentLens';
import {feedback} from './feedback';
import {REFRESH_EVENT,type RefreshRequest} from './refreshEvent';
import {MONITOR_POLL_MS,clockText,coin,groupByDay,monitorGet,priceText,reasonText,signalStatus,signedPercent,stageText,tone,
 type AlertSnapshot,type CloudFilter,type CloudSnapshot,type MonitorSegment,type PaperAccount,type PaperSnapshot} from './monitorData';

const SEGMENTS:[MonitorSegment,string][]=[['paper','模拟盘'],['alerts','报警'],['cloud','云带']];
const SEGMENT_KEY='spike-monitor-segment';
type Loaded<T>={data?:T;error?:string;at?:number};
// Session memory only: monitor snapshots are display data and never persisted to disk.
const memory=new Map<string,unknown>();
const money=(v:number|null|undefined)=>v===null||v===undefined||!Number.isFinite(v)?'—':v.toLocaleString('en-US',{minimumFractionDigits:2,maximumFractionDigits:2});

function useMonitor<T>(connection:Connection|null,key:string,enabled:boolean,poll:number,load:(c:Connection,signal:AbortSignal)=>Promise<T>){
 const [state,setState]=useState<Loaded<T>>(()=>({data:memory.get(key) as T|undefined}));
 const latest=useRef(load);latest.current=load;
 const run=useCallback(async(signal?:AbortSignal)=>{
  if(!connection)return;
  const controller=new AbortController();signal?.addEventListener('abort',()=>controller.abort());
  const timer=setTimeout(()=>controller.abort(),12000);
  try{const data=await latest.current(connection,controller.signal);memory.set(key,data);setState({data,at:Date.now()})}
  catch(e){if(!signal?.aborted)setState(s=>({...s,error:(e as Error).message||'监控暂不可用'}))}
  finally{clearTimeout(timer)}
 },[connection,key]);
 useEffect(()=>{setState({data:memory.get(key) as T|undefined})},[key]);
 useEffect(()=>{
  if(!enabled||!connection)return;
  const controller=new AbortController();let timer:ReturnType<typeof setTimeout>;
  const tick=async()=>{if(document.hidden)return;await run(controller.signal);if(!controller.signal.aborted)timer=setTimeout(tick,poll)};
  const visibility=()=>{clearTimeout(timer);if(!document.hidden)void tick()};
  document.addEventListener('visibilitychange',visibility);void tick();
  const refresh=(event:Event)=>(event as CustomEvent<RefreshRequest>).detail.tasks.push(run(controller.signal));
  window.addEventListener(REFRESH_EVENT,refresh);
  return()=>{controller.abort();clearTimeout(timer);document.removeEventListener('visibilitychange',visibility);window.removeEventListener(REFRESH_EVENT,refresh)};
 },[enabled,connection,poll,run]);
 return state;
}

function Status({loading,error,stale}:{loading:boolean;error?:string;stale:boolean}){
 if(error)return <p className="monitor-error" role="status"><Icon name="refresh" size={15}/>{stale?`${error} · 显示上次数据`:error}</p>;
 return loading?<p className="monitor-loading" role="status"><Spinner/>正在读取</p>:null;
}

function AccountCard({account,onOpen}:{account:PaperAccount;onOpen:()=>void}){
 return <button className="glass monitor-account" onClick={onOpen}>
  <span className="account-key">{account.label}</span>
  <b className="t-num">{money(account.equity)}<small> U</small></b>
  <span className={`t-num ${tone(account.return_pct)}`}>{signedPercent(account.return_pct)}</span>
  <small className="account-foot">已平 {account.stats.closed??'—'} · 胜 {account.stats.wins??'—'}</small>
 </button>;
}

function PaperModule({connection,active,onOpenSymbol}:{connection:Connection|null;active:boolean;onOpenSymbol:(s:string)=>void}){
 const [detail,setDetail]=useState<PaperAccount|null>(null);
 const overview=useMonitor<PaperSnapshot>(connection,'paper:live-wick6x3',active,MONITOR_POLL_MS.paper,(c,s)=>monitorGet(c,'paper',{track:'live-wick6x3'},s));
 const sheet=useMonitor<PaperSnapshot>(connection,`paper:${detail?.key??'none'}`,active&&!!detail,MONITOR_POLL_MS.paper,(c,s)=>monitorGet(c,'paper',{track:detail!.key},s));
 const data=overview.data;
 const live=data?.accounts.filter(a=>a.group==='live')??[],replica=data?.accounts.filter(a=>a.group==='replica')??[];
 const open=(a:PaperAccount)=>{setDetail(a);feedback('light')};
 return <>
  <p className="monitor-note glass-chip"><Icon name="shield" size={14}/>模拟账户 · 只读 · 不涉及真实资金 · 净值不含浮盈</p>
  <Status loading={!data&&!overview.error} error={overview.error} stale={!!data}/>
  {!!live.length&&<section className="monitor-section"><h2>live- 前向模拟 <span>{live.length}</span></h2><div className="monitor-grid">{live.map(a=><AccountCard key={a.key} account={a} onOpen={()=>open(a)}/>)}</div></section>}
  {!!replica.length&&<section className="monitor-section"><h2>回测复刻 <span>{replica.length}</span></h2><div className="glass monitor-list">{replica.map(a=><button key={a.key} className="monitor-row" onClick={()=>open(a)}><span className="row-main"><b>{a.label}</b><small>已平 {a.stats.closed??'—'} · 胜 {a.stats.wins??'—'}</small></span><span className="row-end t-num"><b>{money(a.equity)}</b><small className={tone(a.return_pct)}>{signedPercent(a.return_pct)}</small></span><Icon name="chevron" size={16}/></button>)}</div></section>}
  {data&&<section className="monitor-section"><h2>今日触价 <span>{data.signals.length}</span></h2>{data.signals.length?<div className="glass monitor-list">{data.signals.map(s=><button key={`${s.id}-${s.symbol}`} className="monitor-row" onClick={()=>onOpenSymbol(s.symbol)}><CoinLogo coin={coin(s.symbol)}/><span className="row-main"><b>{coin(s.symbol)} <i className={`side-pill ${s.side}`}>{s.side==='long'?'做多':'做空'}</i></b><small>{signalStatus(s)} · 1.618 {priceText(s.level)}</small></span><span className="row-end t-num"><b>{s.score===null?'—':s.score.toFixed(2)}</b><small>{clockText(s.observed_ms,data.now_ms)}</small></span></button>)}</div>:<p className="monitor-empty glass">今天还没有触价</p>}</section>}
  {detail&&<Dialog label={`${detail.label} 模拟账户`} busy={false} onDismiss={()=>setDetail(null)}>{()=>{const d=sheet.data?.detail.key===detail.key?sheet.data.detail:undefined;const a=sheet.data?.accounts.find(x=>x.key===detail.key)??detail;return <div className="monitor-sheet">
   <span className="eyebrow">模拟账户 · 只读</span><h2>{detail.label}</h2>
   <div className="monitor-sheet-hero"><b className="t-num">{money(a.equity)}<small> U</small></b><span className={`t-num ${tone(a.return_pct)}`}>{signedPercent(a.return_pct)}</span><small>起始 {money(a.start_equity)} U · 已平 {a.stats.closed??'—'} 笔 · 平均 {a.stats.mean_net_bp===null?'—':a.stats.mean_net_bp.toFixed(1)}bp</small></div>
   <Status loading={!d&&!sheet.error} error={sheet.error} stale={!!d}/>
   {d&&<><h3>持仓 <span>{d.open.length}</span></h3>{d.open.length?d.open.map(p=><div key={p.id??p.symbol} className="monitor-position"><CoinLogo coin={coin(p.symbol)}/><span className="row-main"><b>{coin(p.symbol)} <i className={`side-pill ${p.side}`}>{p.side==='long'?'做多':'做空'}</i></b><small>{stageText(p.stage)} · 入场 {priceText(p.entry)} · 止损 {priceText(p.stop)}</small></span><span className={`row-end t-num ${tone(p.unrealized_bp)}`}><b>{p.unrealized_bp===null||p.unrealized_bp===undefined?'—':`${p.unrealized_bp>0?'+':''}${p.unrealized_bp.toFixed(0)}bp`}</b><small>目标 {priceText(p.target)}</small></span></div>):<p className="monitor-empty">当前没有模拟持仓</p>}
   <h3>今日平仓 <span>{d.closed.length}</span></h3>{d.closed.length?d.closed.map(p=><div key={p.id??p.symbol} className="monitor-position"><CoinLogo coin={coin(p.symbol)}/><span className="row-main"><b>{coin(p.symbol)} <i className={`side-pill ${p.side}`}>{p.side==='long'?'做多':'做空'}</i></b><small>{reasonText(p.reason)} · {clockText(p.exit_ms,sheet.data?.now_ms)}</small></span><span className={`row-end t-num ${tone(p.pnl)}`}><b>{p.pnl===null||p.pnl===undefined?'—':`${p.pnl>0?'+':''}${money(p.pnl)}`}</b><small>{p.net_bp===null||p.net_bp===undefined?'':`${p.net_bp>0?'+':''}${p.net_bp.toFixed(0)}bp`}</small></span></div>):<p className="monitor-empty">今天没有平仓</p>}</>}
   <p className="micro">监控工作台的模拟账本前向复刻，含回测口径评分；不是可下单的实盘策略。</p>
  </div>}}</Dialog>}
 </>;
}

function AlertsModule({connection,active,onOpenSymbol}:{connection:Connection|null;active:boolean;onOpenSymbol:(s:string)=>void}){
 const state=useMonitor<AlertSnapshot>(connection,'alerts',active,MONITOR_POLL_MS.alerts,(c,s)=>monitorGet(c,'alerts',{},s));
 const [expanded,setExpanded]=useState<number|null>(null);
 const data=state.data;
 const live=data?.alerts.filter(a=>a.active)??[],history=data?.alerts.filter(a=>!a.active)??[];
 const chips=(symbols:string[],all:boolean,id:number|null)=><div className="coin-chips">{(all?symbols:symbols.slice(0,6)).map(s=><button key={s} className="glass-chip" onClick={()=>onOpenSymbol(s)}>{coin(s)}</button>)}{!all&&symbols.length>6&&<button className="glass-chip more" onClick={()=>{setExpanded(id);feedback('light')}}>+{symbols.length-6}</button>}</div>;
 const range=(a:{first_ms:number|null;last_ms:number|null})=>`${clockText(a.first_ms,data?.now_ms)} – ${clockText(a.last_ms,data?.now_ms).split(' ').at(-1)}`;
 return <>
  {data&&<p className="glass-chip monitor-status"><i className={data.worker_alive?'ok':'off'}/>{data.worker_alive?'报警服务运行中':'报警服务未运行'}<span>{data.window_minutes??5} 分钟内 ≥{data.threshold??'—'} 币触及 1.618</span></p>}
  <Status loading={!data&&!state.error} error={state.error} stale={!!data}/>
  {live.map(a=><section key={a.id??a.first_ms} className={`glass alert-hero ${a.side}`}><div className="alert-hero-top"><span className="pulse-label"><i/>进行中</span><span className="glass-chip t-num">{range(a)}</span></div><h2>全市场插针 · {a.side==='lower'?'下沿':'上沿'} 1.618 <Icon name={a.side==='lower'?'down':'up'} size={18}/></h2><p className="alert-count"><b className="t-num">{a.n}</b>个币 {data?.window_minutes??5} 分钟内触及</p>{chips(a.symbols,expanded===a.id,a.id)}<p className="micro">点币种在合约页查看走势；开仓仍走纪律规则。</p></section>)}
  {data&&!live.length&&<p className="monitor-empty glass">当前没有进行中的报警</p>}
  {!!history.length&&<section className="monitor-section"><h2>历史报警 <span>{history.length}</span></h2>{groupByDay(history,a=>a.first_ms,data?.now_ms).map(g=><div key={g.label} className="monitor-day"><h3>{g.label}</h3>{g.rows.map(a=><div key={a.id??a.first_ms} className="glass alert-row"><button className="monitor-row" aria-expanded={expanded===a.id} onClick={()=>{setExpanded(expanded===a.id?null:a.id);feedback('light')}}><span className={`alert-icon ${a.side}`}><Icon name={a.side==='lower'?'down':'up'} size={18}/></span><span className="row-main"><b>{a.side==='lower'?'下沿':'上沿'} 1.618 · {a.n} 币</b><small className="t-num">{range(a)}</small></span><span className={`state-pill ${a.test?'test':a.acknowledged?'done':'open'}`}>{a.test?'测试':a.acknowledged?'已确认':'未确认'}</span></button>{expanded===a.id&&chips(a.symbols,true,a.id)}</div>)}</div>)}</section>}
  <p className="micro monitor-foot">只读显示。确认报警、测试推送与通知设置仍在监控工作台操作。</p>
 </>;
}

function CloudModule({connection,active,onOpenSymbol}:{connection:Connection|null;active:boolean;onOpenSymbol:(s:string)=>void}){
 const [filter,setFilter]=useState<CloudFilter>({side:'all',scope:'live'});
 const state=useMonitor<CloudSnapshot>(connection,`cloud:${filter.side}:${filter.scope}`,active,MONITOR_POLL_MS.cloud,(c,s)=>monitorGet(c,'cloud',{side:filter.side,scope:filter.scope},s));
 const data=state.data;
 const pick=(next:Partial<CloudFilter>)=>{setFilter(f=>({...f,...next}));feedback('light')};
 return <>
  <p className="monitor-note glass-chip warn"><Icon name="lock" size={14}/>12H EMA20/50 云带 · 只供观察，不作开单依据</p>
  <div className="monitor-filters"><div className="filter-capsules">{([['all','全部'],['long','做多'],['short','做空']] as const).map(([v,l])=><button key={v} aria-pressed={filter.side===v} onClick={()=>pick({side:v})}>{l}</button>)}</div><button className="glass-chip scope" aria-pressed={filter.scope==='all'} onClick={()=>pick({scope:filter.scope==='live'?'all':'live'})}>{filter.scope==='live'?'本周':'含历史'}</button></div>
  {data&&<p className="monitor-caption t-num">下次 12H 收盘 {clockText(data.next_close_ms,data.now_ms)} · 已扫描 {data.status.completed??'—'}/{data.status.total??'—'}{data.status.worker_alive?'':' · 扫描未运行'}</p>}
  <Status loading={!data&&!state.error} error={state.error} stale={!!data}/>
  {data&&!data.items.length&&<p className="monitor-empty glass">{filter.scope==='live'?'本周暂无云带信号':'暂无信号'}</p>}
  {data?.items.map(s=><button key={s.id} className="glass cloud-card" onClick={()=>onOpenSymbol(s.symbol)}>
   <span className="cloud-head"><CoinLogo coin={coin(s.symbol)}/><b>{coin(s.symbol)}<small> / USDT</small></b><i className={`side-pill ${s.side}`}>{s.side==='long'?'做多':'做空'}</i><i className="tf-pill">{s.timeframe}</i><i className={`fresh-pill ${s.historical?'old':'new'}`}>{s.historical?'历史':'最新'}</i></span>
   <span className="cloud-figures t-num"><span><small>入场</small><b>{priceText(s.entry)}</b></span><span><small>止损</small><b className="down">{priceText(s.stop)}</b>{s.stop_pct!==null&&<em>−{Math.abs(s.stop_pct).toFixed(1)}%</em>}</span><span><small>目标</small><b className="up">{priceText(s.target)}</b></span></span>
   <span className="cloud-foot t-num"><span>R:R {s.planned_rr===null?'—':s.planned_rr.toFixed(1)}</span><span>确认 {clockText(s.confirmed_ms,data.now_ms)}</span></span>
  </button>)}
  <p className="micro monitor-foot">云带评分 2026-11-05 已在全部周期被否决，这里仅作盘面观察。</p>
 </>;
}

export function Monitor({connection,active,covered,onOpenSymbol,onConnect,segmentRequest,unread,onOpenNotifications}:{connection:Connection|null;active:boolean;covered:boolean;onOpenSymbol:(symbol:string)=>void;onConnect:()=>void;segmentRequest?:{segment:MonitorSegment;nonce:number};unread:number;onOpenNotifications:()=>void}){
 const [segment,setSegment]=useState<MonitorSegment>(()=>{try{const v=localStorage.getItem(SEGMENT_KEY);return v==='alerts'||v==='cloud'?v:'paper'}catch{return 'paper'}});
 const choose=(next:MonitorSegment)=>{if(next===segment)return;setSegment(next);feedback('light');try{localStorage.setItem(SEGMENT_KEY,next)}catch{}};
 useEffect(()=>{if(segmentRequest&&segmentRequest.segment!=='notifications'){setSegment(segmentRequest.segment);try{localStorage.setItem(SEGMENT_KEY,segmentRequest.segment)}catch{}}},[segmentRequest?.nonce]);
 if(!active)return null;
 const live=active&&!covered;
 return <div className="monitor-page">
  <PageHeader title="监控" subtitle="模拟盘、全市场报警与云带，只读。" actions={connection&&<button className="settings-gear notify-bell" aria-label={unread?`通知，${unread} 条未读`:"通知"} onClick={onOpenNotifications}><Icon name="bell" size={20}/>{unread>0&&<i className="badge-dot">{unread>9?"9+":unread}</i>}</button>}/>
  <div className="segment monitor-segments" role="tablist" aria-label="监控模块" data-index={SEGMENTS.findIndex(([k])=>k===segment)}>{SEGMENTS.map(([key,label])=><button key={key} role="tab" aria-selected={segment===key} aria-pressed={segment===key} className={segment===key?'active':''} onClick={()=>choose(key)}>{label}</button>)}<SegmentLens value={segment}/></div>
  {!connection?<section className="empty card"><span className="empty-icon"><Icon name="bell" size={32}/></span><h2>连接后查看监控</h2><p>监控数据经纪律服务只读转发。</p><button className="primary" onClick={onConnect}>连接纪律服务</button></section>:
   segment==='paper'?<PaperModule connection={connection} active={live} onOpenSymbol={onOpenSymbol}/>:
   segment==='alerts'?<AlertsModule connection={connection} active={live} onOpenSymbol={onOpenSymbol}/>:
   <CloudModule connection={connection} active={live} onOpenSymbol={onOpenSymbol}/>}
 </div>;
}
