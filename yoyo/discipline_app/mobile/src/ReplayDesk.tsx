import {ThemeToggle} from './ThemeToggle';
/** App-only paper replay desk. Every request stays under /v1/replay/*.
 * Replay sessions are independent snapshots and never use live order/position APIs.
 */
import {useCallback,useEffect,useMemo,useRef,useState} from 'react';
import {ApiError,api,SERVICE_URL,type Connection} from './api';
import {Icon} from './Icon';
import {ReplayChart} from './ReplayChart';
import {beijingStartToUtcSeconds,currentBeijingInputs,formatReplayPrice,formatReplayTime,formatReplayValue,isCancellableReplayOrder,isPendingReplayOrder,normalizeReplaySymbol,REPLAY_PERIODS,replayFillReasonLabel,replayFillRoleLabel,replayNumber,replayOrderStatusLabel,validReplayCash,type ReplayCatalog,type ReplayClosedTrade,type ReplayFill,type ReplayOrder,type ReplayPeriod,type ReplaySessionRow,type ReplaySnapshot,type ReplaySourceId} from './replayData';
import './replay.css';

type CommandAction='advance'|'order'|'close'|'cancel'|'monday';
type ReplayCommand={request_id:string;version:number;action:CommandAction;params:Record<string,unknown>};
type PendingCommand={sessionId:string;body:ReplayCommand;conflict?:boolean;automated?:boolean;beforeFills?:number};
type PendingCreate={request_id:string;body:{request_id:string;symbol:string;period:ReplayPeriod;source:ReplaySourceId;start:number;cash:string}};
type ReplaySessionResponse={items:ReplaySessionRow[]};
const freshId=()=>crypto.randomUUID().replaceAll('-','');
const numberText=(value:unknown,digits=2)=>formatReplayValue(value,digits);

export function ReplayDesk({connection,onBack}:{connection:Connection|null;onBack:()=>void}){
 const replayConnection=useMemo<Connection>(()=>({url:connection?.url??SERVICE_URL,token:connection?.token??''}),[connection?.url,connection?.token]);
 const [catalog,setCatalog]=useState<ReplayCatalog|null>(null),[sessions,setSessions]=useState<ReplaySessionRow[]>([]);
 const [selectedId,setSelectedId]=useState(''),[snapshot,setSnapshot]=useState<ReplaySnapshot|null>(null);
 const [date,setDate]=useState(()=>currentBeijingInputs().date),[time,setTime]=useState(()=>currentBeijingInputs().time);
 const [symbol,setSymbol]=useState('BTC-USDT-SWAP'),[period,setPeriod]=useState<ReplayPeriod>('15m'),[source,setSource]=useState<ReplaySourceId>('okx_local'),[cash,setCash]=useState('');
 const [orderSide,setOrderSide]=useState<'long'|'short'>('long'),[orderKind,setOrderKind]=useState<'market'|'limit'>('market'),[notional,setNotional]=useState(''),[limitPrice,setLimitPrice]=useState(''),[stop,setStop]=useState(''),[target,setTarget]=useState('');
 const [mondayNotional,setMondayNotional]=useState(''),[takeProfit,setTakeProfit]=useState<'none'|'near'|'far'>('none');
 const [speed,setSpeed]=useState<1|5|10>(1),[playing,setPlaying]=useState(false),[busy,setBusy]=useState(false),[loading,setLoading]=useState(true),[error,setError]=useState(''),[notice,setNotice]=useState('');
 const [pendingCreate,setPendingCreate]=useState<PendingCreate|null>(null),[activePending,setActivePending]=useState<PendingCommand|null>(null);
 const requestInFlight=useRef(false),mounted=useRef(true),generation=useRef(0),activeId=useRef(''),snapshotRef=useRef<ReplaySnapshot|null>(null),playingRef=useRef(false),pendingCommands=useRef(new Map<string,PendingCommand>());
 snapshotRef.current=snapshot;activeId.current=selectedId;playingRef.current=playing;
 const periodChoices=(catalog?.periods??REPLAY_PERIODS).filter(value=>(REPLAY_PERIODS as readonly string[]).includes(value)) as ReplayPeriod[];
 const sources=catalog?.sources??[];
 const localPairs=(catalog?.local??[]).filter(row=>row.period===period);
 const localSymbols=[...new Set(localPairs.map(row=>row.symbol))];
 const currentLocal=localPairs.some(row=>{
  try{return normalizeReplaySymbol(row.symbol)===normalizeReplaySymbol(symbol)}catch{return false}
 });

 const syncSessions=useCallback(async()=>{
  if(requestInFlight.current)return;
  requestInFlight.current=true;if(mounted.current)setBusy(true);
  try{const result=await api<ReplaySessionResponse>(replayConnection,'/v1/replay/sessions');if(mounted.current&&Array.isArray(result.items))setSessions(result.items)}
  catch(cause){if(mounted.current)setError((cause as Error).message||'历史练习列表读取失败')}
  finally{requestInFlight.current=false;if(mounted.current)setBusy(false)}
 },[replayConnection]);

 const readSession=useCallback(async(id:string,expectedGeneration:number,keepCommand=true)=>{
  const next=await api<ReplaySnapshot>(replayConnection,`/v1/replay/sessions/${encodeURIComponent(id)}`);
  if(!mounted.current||generation.current!==expectedGeneration||activeId.current!==id)return;
  snapshotRef.current=next;setSnapshot(next);setSelectedId(id);setError('');
  const pending=keepCommand?pendingCommands.current.get(id)??null:null;
  setActivePending(pending);
  if(!keepCommand&&pendingCommands.current.has(id))pendingCommands.current.delete(id);
  return next;
 },[replayConnection]);

 useEffect(()=>{
  mounted.current=true;let active=true;
  const load=async()=>{
   setLoading(true);
   try{
    const result=await api<ReplayCatalog>(replayConnection,'/v1/replay/catalog');
    if(!active)return;
    setCatalog(result);
    const nextSource=(result.sources.find(item=>item.id==='okx_local')??result.sources[0])?.id;
    if(nextSource)setSource(nextSource);
    if(result.periods.includes('15m'))setPeriod('15m');else if(result.periods[0])setPeriod(result.periods[0]);
    const local=result.local.find(item=>item.period===(result.periods.includes('15m')?'15m':result.periods[0]));
    if(local)setSymbol(local.symbol);
   }catch(cause){if(active)setError((cause as Error).message||'回放目录读取失败')}
   try{const list=await api<ReplaySessionResponse>(replayConnection,'/v1/replay/sessions');if(active&&Array.isArray(list.items))setSessions(list.items)}
   catch(cause){if(active)setError(previous=>previous||((cause as Error).message||'历史练习列表读取失败'))}
   finally{if(active)setLoading(false)}
  };
  void load();
  return()=>{active=false;mounted.current=false;generation.current++;playingRef.current=false};
 },[replayConnection]);

 const pause=useCallback(()=>{playingRef.current=false;setPlaying(false)},[]);
 useEffect(()=>{
  const visibility=()=>{if(document.hidden)pause()};
  document.addEventListener('visibilitychange',visibility);
  return()=>document.removeEventListener('visibilitychange',visibility);
 },[pause]);

 const updateSessionsFromSnapshot=useCallback((next:ReplaySnapshot)=>{
  setSessions(rows=>{
   const row:ReplaySessionRow={id:next.id,symbol:next.symbol,period:next.period,source:next.source,clock:next.clock,initial_cash:next.initial_cash,version:next.version};
   return rows.some(item=>item.id===next.id)?rows.map(item=>item.id===next.id?row:item):[row,...rows];
  });
 },[]);

 async function chooseSession(id:string){
  if(!id){startNewSession();return}
  if(id===selectedId||requestInFlight.current)return;
  pause();setNotice('');setError('');setSnapshot(null);setSelectedId(id);activeId.current=id;snapshotRef.current=null;
  const currentGeneration=++generation.current;
  setActivePending(pendingCommands.current.get(id)??null);
  setBusy(true);requestInFlight.current=true;
  try{await readSession(id,currentGeneration)}catch(cause){if(mounted.current&&generation.current===currentGeneration)setError((cause as Error).message||'历史练习读取失败')}
  finally{requestInFlight.current=false;if(mounted.current)setBusy(false)}
 }

 function startNewSession(){
  if(requestInFlight.current||pendingCreate)return;
  pause();generation.current++;activeId.current='';snapshotRef.current=null;setSelectedId('');setSnapshot(null);setActivePending(null);setError('');setNotice('');
 }

 async function retryCreate(record=pendingCreate){
  if(!record||requestInFlight.current)return;
  pause();setError('');setNotice('');setBusy(true);requestInFlight.current=true;
  const currentGeneration=generation.current;
  try{
   const created=await api<ReplaySnapshot>(replayConnection,'/v1/replay/sessions',record.body);
   if(!mounted.current||generation.current!==currentGeneration)return;
   setPendingCreate(null);setSnapshot(created);snapshotRef.current=created;setSelectedId(created.id);activeId.current=created.id;setActivePending(null);setError('');setNotice(created.notice?.trim()||'已创建独立回放，原有记录保留。');updateSessionsFromSnapshot(created);
  }catch(cause){
   if(!mounted.current||generation.current!==currentGeneration)return;
   if(cause instanceof ApiError&&cause.status<500){setPendingCreate(null);setError(cause.message)}
   else setError('创建结果尚未确认。可继续原操作，或重新查询回放记录。');
  }finally{
   requestInFlight.current=false;if(mounted.current)setBusy(false);
  }
 }

 async function createSession(event:React.FormEvent){
  event.preventDefault();if(requestInFlight.current)return;
  setError('');setNotice('');
  let normalized:string,start:number;
  try{
   normalized=normalizeReplaySymbol(symbol);start=beijingStartToUtcSeconds(date,time);
   if(!validReplayCash(cash))throw new Error('初始资金必须是大于 0 的 USDT 数值');
   if(!sources.some(item=>item.id===source))throw new Error('请选择回放数据源');
   if(!periodChoices.includes(period))throw new Error('请选择回放周期');
   if(source==='okx_local'&&!localPairs.some(row=>normalizeReplaySymbol(row.symbol)===normalized))throw new Error('该本地数据源没有这个币种和周期，请从可用币种中选择或改用 OKX 在线历史');
  }catch(cause){setError((cause as Error).message);return}
  const request_id=freshId();
  const record:PendingCreate={request_id,body:{request_id,symbol:normalized,period,source,start,cash:cash.trim()}};
  setPendingCreate(record);
  await retryCreate(record);
 }

 async function postCommand(record:PendingCommand){
  if(requestInFlight.current||record.conflict)return;
  requestInFlight.current=true;setBusy(true);setError('');
  const currentGeneration=generation.current;
  try{
   const next=await api<ReplaySnapshot>(replayConnection,`/v1/replay/sessions/${encodeURIComponent(record.sessionId)}/commands`,record.body);
   pendingCommands.current.delete(record.sessionId);
   if(!mounted.current)return;
   if(generation.current===currentGeneration&&activeId.current===record.sessionId){
    const newFill=!!record.automated&&next.fills.length>(record.beforeFills??0);
    if(newFill)pause();
    snapshotRef.current=next;setSnapshot(next);setActivePending(null);setError('');setNotice(next.notice?.trim()||(newFill?'本批出现成交，回放已暂停。':record.body.action==='advance'?'':`${commandLabel(record.body.action)}已完成`));updateSessionsFromSnapshot(next);
   }
  }catch(cause){
   if(!mounted.current)return;
   pause();
   if(cause instanceof ApiError&&cause.status===409&&cause.message.includes('另一处推进')){
    const conflict:PendingCommand={...record,conflict:true};
    pendingCommands.current.set(record.sessionId,conflict);
    if(generation.current===currentGeneration&&activeId.current===record.sessionId){
     setActivePending(conflict);
     try{await readSession(record.sessionId,currentGeneration,false);if(!mounted.current||generation.current!==currentGeneration||activeId.current!==record.sessionId)return;pendingCommands.current.delete(record.sessionId);setActivePending(null);setNotice('进度版本已变化，已重新读取回放。原操作未重复提交。')}
     catch(refreshError){if(mounted.current&&generation.current===currentGeneration&&activeId.current===record.sessionId)setError(`版本冲突；重新读取失败：${(refreshError as Error).message}`)}
    }
   }else if(cause instanceof ApiError&&cause.status===409){
    pendingCommands.current.delete(record.sessionId);
    if(generation.current===currentGeneration&&activeId.current===record.sessionId){
     setActivePending(null);
     try{await readSession(record.sessionId,currentGeneration,false);if(mounted.current&&generation.current===currentGeneration&&activeId.current===record.sessionId)setError(cause.message)}
     catch(refreshError){if(mounted.current&&generation.current===currentGeneration&&activeId.current===record.sessionId)setError(`${cause.message}；重新读取进度失败：${(refreshError as Error).message}`)}
    }
   }else if(cause instanceof ApiError&&cause.status<500){
    pendingCommands.current.delete(record.sessionId);
    if(generation.current===currentGeneration&&activeId.current===record.sessionId){setActivePending(null);setError(cause.message)}
   }else{
    pendingCommands.current.set(record.sessionId,record);
    if(generation.current===currentGeneration&&activeId.current===record.sessionId){setActivePending(record);setError('操作结果尚未确认。点击“继续原操作”会沿用本次委托，避免重复开单。')}
   }
  }finally{requestInFlight.current=false;if(mounted.current)setBusy(false)}
 }

 function submitCommand(action:CommandAction,params:Record<string,unknown>,automated=false){
  const current=snapshotRef.current;
  if(!current||requestInFlight.current||pendingCommands.current.has(current.id))return;
  if(action!=='advance'||!automated)pause();
  setNotice('');
  const record:PendingCommand={sessionId:current.id,body:{request_id:freshId(),version:current.version,action,params},automated,beforeFills:current.fills.length};
  pendingCommands.current.set(current.id,record);setActivePending(record);
  void postCommand(record);
 }

 function retryPendingCommand(){const record=activePending;if(record&&!record.conflict)void postCommand(record)}

 useEffect(()=>{
  if(!playing||!snapshot||activePending)return;
  const timer=window.setInterval(()=>{
   if(document.hidden||requestInFlight.current||pendingCommands.current.has(snapshot.id))return;
   submitCommand('advance',{count:speed},true);
  },1000);
  return()=>window.clearInterval(timer);
 },[playing,speed,snapshot?.id,activePending?.body.request_id]);

 const onOrder=()=>{
  if(!notional.trim()||!Number.isFinite(Number(notional))||Number(notional)<=0){setError('名义金额必须是大于 0 的 USDT 数值');return}
  if(orderKind==='limit'&&(!limitPrice.trim()||!Number.isFinite(Number(limitPrice))||Number(limitPrice)<=0)){setError('限价委托必须填写有效价格');return}
  for(const [name,value] of [['止损',stop],['止盈',target]] as const)if(value.trim()&&(!Number.isFinite(Number(value))||Number(value)<=0)){setError(`${name}价格必须是大于 0 的数值`);return}
  const latestPrice=replayNumber(snapshotRef.current?.price);
  const expectedPrice=orderKind==='limit'?Number(limitPrice):latestPrice;
  if(orderKind==='limit'&&((orderSide==='long'&&expectedPrice>=latestPrice)||(orderSide==='short'&&expectedPrice<=latestPrice))){setError('限价须保持被动：做多低于最新收盘价，做空高于最新收盘价');return}
  if(orderSide==='long'&&stop.trim()&&Number(stop)>=expectedPrice){setError('做多止损价必须低于预计成交价');return}
  if(orderSide==='long'&&target.trim()&&Number(target)<=expectedPrice){setError('做多止盈价必须高于预计成交价');return}
  if(orderSide==='short'&&stop.trim()&&Number(stop)<=expectedPrice){setError('做空止损价必须高于预计成交价');return}
  if(orderSide==='short'&&target.trim()&&Number(target)>=expectedPrice){setError('做空止盈价必须低于预计成交价');return}
  submitCommand('order',{side:orderSide,kind:orderKind,notional:notional.trim(),...(orderKind==='limit'?{price:limitPrice.trim()}:{}),...(stop.trim()?{stop:stop.trim()}:{}),...(target.trim()?{target:target.trim()}:{})});
 };

 const onMonday=()=>{
  if(!mondayNotional.trim()||!Number.isFinite(Number(mondayNotional))||Number(mondayNotional)<=0){setError('上下挂单的每侧名义金额必须大于 0');return}
  if(!snapshot?.range.upper||!snapshot.range.lower){setError('当前练习没有可用的周一 1.618 上下价位');return}
  submitCommand('monday',{notional:mondayNotional.trim(),take_profit:takeProfit});
 };

 const activeSession=sessions.find(item=>item.id===selectedId);
 const activeOrders=(snapshot?.orders??[]).filter(isPendingReplayOrder);
 const historicalOrders=(snapshot?.orders??[]).filter(order=>!isPendingReplayOrder(order));
 const range=snapshot?.range;
 const closed=[...(snapshot?.closed??[])].reverse();
 const fills=[...(snapshot?.fills??[])].reverse();
 const hasUncertainCreate=!!pendingCreate;

 return <div className="app replay-app" data-no-swipe>
  <main className="replay-main" data-no-swipe>
   <header className="replay-header"><button className="replay-back" aria-label="返回 1.618" onClick={onBack}><Icon name="chevron" size={20}/></button><div><span className="replay-kicker">独立模拟盘</span><h1>模拟盘 · 历史回放</h1></div><ThemeToggle/><span className="replay-badge"><i/>仅模拟</span></header>
   <section className="replay-disclaimer"><Icon name="shield" size={17}/><span>初始资金不是实盘余额。1x 名义本金；OHLC 顺序 O → H → L → C；限价 maker 0.02%，市价/止损 taker 0.05%；不含滑点、资金费与真实盘口队列。</span></section>

   <section className="replay-session-picker card">
    <div className="replay-section-heading"><div><span className="replay-eyebrow">历史回放</span><h2>{snapshot?`${snapshot.symbol.replace('-USDT-SWAP','')} · ${snapshot.period}`:'选择或新建练习'}</h2></div><div className="replay-session-actions"><button className="replay-new-session-button" disabled={busy||hasUncertainCreate} onClick={startNewSession}>新建回放</button><button className="replay-icon-button" aria-label="刷新历史回放列表" disabled={busy||loading} onClick={()=>void syncSessions()}><Icon name="refresh" size={18}/></button></div></div>
    <label className="replay-field"><span>恢复历史回放</span><select aria-label="选择历史回放" disabled={busy||loading||hasUncertainCreate} value={selectedId} onChange={event=>void chooseSession(event.target.value)}><option value="">新建或选择历史练习</option>{sessions.map(item=><option key={item.id} value={item.id}>{item.symbol.replace('-USDT-SWAP','')} · {item.period} · {formatReplayTime(item.clock)} · {item.source==='okx_local'?'本地':'OKX 在线'}</option>)}</select></label>
    {activeSession&&<p className="replay-session-meta">初始 {numberText(activeSession.initial_cash)} USDT · 截止 {formatReplayTime(snapshot?.clock??activeSession.clock)}</p>}
   </section>

   {!snapshot&&<details className="replay-new-session card" open>
    <summary><span><b>新建独立回放</b><small>新时点、币种或周期都会创建新的独立练习</small></span><Icon name="chevron" size={17}/></summary>
    <form onSubmit={event=>void createSession(event)}>
     <label className="replay-field"><span>数据源</span><select aria-label="回放数据源" value={source} onChange={event=>setSource(event.target.value as ReplaySourceId)} disabled={busy||hasUncertainCreate} required>{sources.map(item=><option key={item.id} value={item.id}>{item.label}</option>)}</select></label>
     <div className="replay-form-row">
      <label className="replay-field replay-symbol-field"><span>合约代码</span><input aria-label="合约代码" list="replay-local-symbols" autoCapitalize="characters" autoComplete="off" value={symbol} onChange={event=>setSymbol(event.target.value)} placeholder="BTC-USDT-SWAP" disabled={busy||hasUncertainCreate} required/><datalist id="replay-local-symbols">{localSymbols.map(value=><option key={value} value={value}/>)}</datalist></label>
      <label className="replay-field replay-period-field"><span>周期</span><select aria-label="回放周期" value={period} onChange={event=>setPeriod(event.target.value as ReplayPeriod)} disabled={busy||hasUncertainCreate} required>{periodChoices.map(value=><option key={value} value={value}>{value}</option>)}</select></label>
     </div>
     {source==='okx_local'&&<p className={`replay-local-hint ${currentLocal?'available':''}`} role="status">{currentLocal?`本地可用 · ${normalizeReplaySymbol(symbol)} · ${period}`:localSymbols.length?`本地此周期可用：${localSymbols.slice(0,5).join('、')}${localSymbols.length>5?'…':''}`:'本地没有此周期数据，可改选 OKX 在线历史。'}</p>}
     {source==='okx_online'&&catalog?.warning&&<p className="replay-warning">{catalog.warning}</p>}
     <label className="replay-field"><span>回放开始日期 · 北京时间</span><input aria-label="回放开始日期（北京时间）" type="date" value={date} onChange={event=>setDate(event.target.value)} disabled={busy||hasUncertainCreate} required/></label>
     <label className="replay-field"><span>回放开始时间 · 北京时间</span><input aria-label="回放开始时间（北京时间）" type="time" step="60" value={time} onChange={event=>setTime(event.target.value)} disabled={busy||hasUncertainCreate} required/></label>
     <label className="replay-field"><span>初始资金 · USDT</span><input aria-label="初始资金 USDT" type="number" min="0.01" step="any" inputMode="decimal" value={cash} onChange={event=>setCash(event.target.value)} disabled={busy||hasUncertainCreate} placeholder="例如 10000" required/></label>
     {source==='okx_local'&&!currentLocal&&<p className="replay-warning">请选取本地可用币种与周期，或切换到 OKX 在线历史。</p>}
     <p className="replay-field-note">每次新建都会保留旧练习记录。回放时间按 UTC+8 转换，不读取设备时区。</p>
     {hasUncertainCreate&&<div className="replay-uncertain"><span>创建结果尚未确认，可继续原操作或重新查询回放记录。</span><button type="button" disabled={busy} onClick={()=>void retryCreate()}>继续原操作</button></div>}
     <button className="replay-primary" disabled={busy||loading||!sources.length||hasUncertainCreate||source==='okx_local'&&!currentLocal}>{busy?'正在创建…':'创建回放练习'}<Icon name="arrow" size={17}/></button>
    </form>
   </details>}

   {snapshot&&<>
    <section className="replay-clock-card"><span><Icon name="clock" size={15}/>回放截止 · 北京时间</span><b>{formatReplayTime(snapshot.clock,{year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit'})}</b><small>{snapshot.source==='okx_local'?'本地 OKX 历史':'OKX 在线历史'}</small></section>
    <ReplayChart snapshot={snapshot}/>
    <section className={`replay-range-card card range-${range?.state??'missing'}`}>
     <div className="replay-section-heading"><div><span className="replay-eyebrow">周一区间 · 1.618</span><h2>{range?.state==='ready'?'区间已锁定':range?.state==='developing'?'区间形成中':'周一数据缺失'}</h2></div><span className="replay-range-week">{range?.week??'—'}</span></div>
     <div className="replay-levels"><div><small>周一高</small><b>{formatReplayPrice(range?.high)}</b></div><div><small>上方 1.618</small><b>{formatReplayPrice(range?.upper)}</b></div><div><small>周一低</small><b>{formatReplayPrice(range?.low)}</b></div><div><small>下方 1.618</small><b>{formatReplayPrice(range?.lower)}</b></div></div>
     <p>{range?.state==='ready'?`有效期至 ${formatReplayTime(range.expires)}`:range?.state==='developing'?`周一窗口结束于 ${formatReplayTime(range.end)}`:'此回放没有完整周一区间，不能挂 1.618 双向单。'}</p>
    </section>

    <section className="replay-controls card" aria-label="回放控制">
     <div className="replay-play-row"><button className={`replay-play ${playing?'is-playing':''}`} disabled={busy||!!activePending||!snapshot.bars.length} onClick={()=>{if(playing)pause();else{playingRef.current=true;setPlaying(true)}}}>{playing?'暂停':'播放'}<span>{playing?'Ⅱ':'▶'}</span></button><button className="replay-step" disabled={busy||!!activePending} onClick={()=>submitCommand('advance',{count:1})}>单步前进</button><div className="replay-speed" aria-label="播放速度">{([1,5,10] as const).map(value=><button key={value} aria-pressed={speed===value} onClick={()=>setSpeed(value)}>{value}x</button>)}</div></div>
     <div className="replay-progress"><span>{snapshot.bars.length.toLocaleString('zh-CN')} 根</span><span>时钟 {formatReplayTime(snapshot.clock)}</span><span>收盘 {formatReplayPrice(snapshot.price)}</span></div><small className="replay-speed-note">每秒推进所选根数；本批出现成交后自动暂停。</small>
    </section>

    <section className="replay-account-grid" aria-label="模拟账户资金">
     <div><small>净值</small><b>{numberText(snapshot.equity)} <i>USDT</i></b></div><div><small>可用资金</small><b>{numberText(snapshot.available)} <i>USDT</i></b></div><div><small>现金</small><b>{numberText(snapshot.cash)} <i>USDT</i></b></div><div><small>累计费用</small><b>{numberText(snapshot.fees)} <i>USDT</i></b></div>
    </section>

    <section className="replay-order-entry card">
     <div className="replay-section-heading"><div><span className="replay-eyebrow">模拟委托</span><h2>下单</h2></div><span className="replay-small-tag">仅此练习</span></div>
     <div className="replay-segment"><button aria-pressed={orderSide==='long'} onClick={()=>setOrderSide('long')}>做多</button><button aria-pressed={orderSide==='short'} onClick={()=>setOrderSide('short')}>做空</button></div>
     <div className="replay-segment replay-kind"><button aria-pressed={orderKind==='market'} onClick={()=>setOrderKind('market')}>市价</button><button aria-pressed={orderKind==='limit'} onClick={()=>setOrderKind('limit')}>限价</button></div>
     <label className="replay-field"><span>名义金额 · USDT</span><input aria-label="模拟委托名义金额 USDT" type="number" min="0.01" step="any" inputMode="decimal" value={notional} onChange={event=>setNotional(event.target.value)} required/></label>
     {orderKind==='limit'&&<label className="replay-field"><span>限价</span><input aria-label="模拟限价" type="number" min="0.00000001" step="any" inputMode="decimal" value={limitPrice} onChange={event=>setLimitPrice(event.target.value)} placeholder="USDT" required/></label>}
     <div className="replay-form-row"><label className="replay-field"><span>止损 · 可选</span><input aria-label="模拟止损价格" type="number" min="0.00000001" step="any" inputMode="decimal" value={stop} onChange={event=>setStop(event.target.value)} placeholder="不自动设置"/></label><label className="replay-field"><span>止盈 · 可选</span><input aria-label="模拟止盈价格" type="number" min="0.00000001" step="any" inputMode="decimal" value={target} onChange={event=>setTarget(event.target.value)} placeholder="不自动设置"/></label></div>
     <button className="replay-primary" disabled={busy||!!activePending} onClick={onOrder}>{busy?'正在提交…':orderSide==='long'?'模拟开多':'模拟开空'}<Icon name="arrow" size={17}/></button>
    </section>

    <section className="replay-monday-entry card">
     <div className="replay-section-heading"><div><span className="replay-eyebrow">周一策略</span><h2>上下 1.618 双向挂单</h2></div></div>
     <p className="replay-field-note">上沿挂空、下沿挂多；每周每方向限一次，已越过的方向跳过。下周纽约周一到期，不自动填止损。</p>
     <label className="replay-field"><span>每侧名义金额 · USDT</span><input aria-label="双向挂单每侧名义金额 USDT" type="number" min="0.01" step="any" inputMode="decimal" value={mondayNotional} onChange={event=>setMondayNotional(event.target.value)} required/></label>
     <label className="replay-field"><span>止盈选择</span><select aria-label="双向挂单止盈" value={takeProfit} onChange={event=>setTakeProfit(event.target.value as 'none'|'near'|'far')}><option value="none">不设置</option><option value="near">近侧周一边界</option><option value="far">远侧周一边界</option></select></label>
     <button className="replay-secondary" disabled={busy||!!activePending||range?.state!=='ready'||!range?.upper||!range?.lower} onClick={onMonday}>挂出上下 1.618</button>
    </section>

    <section className="replay-position-list card">
     <div className="replay-section-heading"><div><span className="replay-eyebrow">当前仓位</span><h2>持仓 <small>{snapshot.positions.length}</small></h2></div></div>
     {!snapshot.positions.length?<p className="replay-empty-copy">当前没有模拟持仓。</p>:snapshot.positions.map(position=><article className="replay-position" key={position.id}>
      <div className="replay-position-title"><b className={position.side}>{position.side==='long'?'做多':'做空'} · {snapshot.symbol.replace('-USDT-SWAP','')}</b><span>浮盈 {numberText(position.unrealized)} U</span></div>
      <div className="replay-position-values"><span>入场 <b>{formatReplayPrice(position.entry)}</b></span><span>数量 <b>{numberText(position.remaining,6)} / {numberText(position.quantity,6)}</b></span><span>名义 <b>{numberText(position.notional)} U</b></span><span>止损 / 止盈 <b>{formatReplayPrice(position.stop)} / {formatReplayPrice(position.target)}</b></span></div>
      <div className="replay-position-actions"><button disabled={busy||!!activePending} onClick={()=>submitCommand('close',{position_id:position.id,fraction:0.5})}>平半</button><button disabled={busy||!!activePending} onClick={()=>submitCommand('close',{position_id:position.id,fraction:1})}>全平</button></div>
     </article>)}
    </section>

    <section className="replay-order-list card">
     <div className="replay-section-heading"><div><span className="replay-eyebrow">活动委托</span><h2>待成交委托 <small>{activeOrders.length}</small></h2></div></div>
     {!activeOrders.length?<p className="replay-empty-copy">当前没有待成交委托。</p>:activeOrders.map((order:ReplayOrder)=><ReplayOrderRow key={order.id} order={order} cancellable={isCancellableReplayOrder(order)} busy={busy||!!activePending} onCancel={()=>submitCommand('cancel',{order_id:order.id})}/>)}
     {!!historicalOrders.length&&<details className="replay-order-history"><summary>历史委托 <span>{historicalOrders.length}</span></summary>{historicalOrders.slice().reverse().map(order=><ReplayOrderRow key={order.id} order={order} cancellable={false} busy={busy||!!activePending} onCancel={()=>{}}/>)}</details>}
    </section>

    <section className="replay-statistics card">
     <div className="replay-section-heading"><div><span className="replay-eyebrow">模拟结果</span><h2>统计</h2></div></div>
     <p className="replay-field-note">胜率与盈亏比按完整平仓后的扣费结果统计；回撤按每根 K 线收盘及操作后的净值计算。</p>
     <div className="replay-stats-grid"><Metric label="净盈亏" value={`${numberText(snapshot.statistics.net_pnl)} U`}/><Metric label="收益率" value={`${numberText(snapshot.statistics.return_pct)}%`}/><Metric label="交易 / 胜率" value={`${snapshot.statistics.trades} / ${snapshot.statistics.win_rate===null?'—':`${numberText(snapshot.statistics.win_rate)}%`}`}/><Metric label="盈亏比 / PF" value={`${numberText(snapshot.statistics.payoff)} / ${numberText(snapshot.statistics.profit_factor)}`}/><Metric label="最大回撤" value={`${numberText(snapshot.statistics.max_drawdown)} U · ${numberText(snapshot.statistics.max_drawdown_pct)}%`}/><Metric label="最长连亏" value={`${snapshot.statistics.longest_loss_streak} 笔`}/></div>
    </section>

    <section className="replay-ledger card"><div className="replay-section-heading"><div><span className="replay-eyebrow">成交明细</span><h2>逐笔成交 <small>{fills.length}</small></h2></div></div>{!fills.length?<p className="replay-empty-copy">产生的模拟成交会显示在这里。</p>:fills.map((fill:ReplayFill)=><FillRow key={fill.id} fill={fill}/>)}</section>
    <section className="replay-ledger card"><div className="replay-section-heading"><div><span className="replay-eyebrow">已平仓</span><h2>交易记录 <small>{closed.length}</small></h2></div></div>{!closed.length?<p className="replay-empty-copy">尚无已完成交易。</p>:closed.map((trade:ReplayClosedTrade)=><ClosedRow key={trade.id} trade={trade}/>)}</section>
   </>}

   {activePending&&<section className="replay-uncertain replay-command-status" role="status"><span>{activePending.conflict?'该操作遇到版本冲突，请重新读取回放进度。':'操作结果尚未确认，可继续原操作。'}</span>{activePending.conflict?<button disabled={busy} onClick={()=>{const id=activePending.sessionId;const currentGeneration=generation.current;setBusy(true);requestInFlight.current=true;void readSession(id,currentGeneration,false).then(()=>{if(!mounted.current||generation.current!==currentGeneration||activeId.current!==id)return;pendingCommands.current.delete(id);setActivePending(null);setError('')}).catch(cause=>{if(mounted.current&&generation.current===currentGeneration&&activeId.current===id)setError((cause as Error).message)}).finally(()=>{requestInFlight.current=false;if(mounted.current)setBusy(false)})}}>重新读取</button>:<button disabled={busy} onClick={retryPendingCommand}>继续原操作</button>}</section>}
   {notice&&<p className="replay-notice" role="status">{notice}</p>}
   {error&&<div className="replay-error" role="alert"><Icon name="shield" size={17}/><span>{error}</span><button aria-label="关闭错误提示" onClick={()=>setError('')}><Icon name="close" size={16}/></button></div>}
   {loading&&<p className="replay-loading" role="status">正在读取回放目录与历史练习…</p>}
   <footer className="replay-footer"><button onClick={onBack}><Icon name="chevron" size={16}/>返回 1.618</button><small>独立历史回放 · 不读取实盘账户余额</small></footer>
  </main>
 </div>;
}

function commandLabel(action:CommandAction){return ({advance:'单步推进',order:'模拟委托',close:'平仓',cancel:'撤单',monday:'周一 1.618 委托'})[action]}
function Metric({label,value}:{label:string;value:string}){return <div className="replay-metric"><small>{label}</small><b>{value}</b></div>}
function ReplayOrderRow({order,cancellable,busy,onCancel}:{order:ReplayOrder;cancellable:boolean;busy:boolean;onCancel:()=>void}){return <article className="replay-order-row"><div><b>{order.side==='long'?'做多':'做空'} · {formatReplayPrice(order.price)}</b><small>{order.tag==='monday1618'?'周一 1.618':replayOrderStatusLabel(order.status)} · {numberText(order.notional)} USDT{order.expires?` · 至 ${formatReplayTime(order.expires)}`:''}</small></div>{cancellable?<button aria-label={`撤销委托 ${order.id}`} disabled={busy} onClick={onCancel}>撤单</button>:<span className="replay-order-status">{replayOrderStatusLabel(order.status)}</span>}</article>}
function FillRow({fill}:{fill:ReplayFill}){return <article className="replay-fill-row"><div><b>{replayFillRoleLabel(fill.role)} · {fill.side==='long'?'做多':'做空'} · {replayFillReasonLabel(fill.reason)}</b><small>{formatReplayTime(fill.time)} · 仓位 {fill.position_id.slice(0,8)}</small></div><span>{formatReplayPrice(fill.price)}<small>{numberText(fill.quantity,6)} 数量 · 费 {numberText(fill.fee)} U</small></span></article>}
function ClosedRow({trade}:{trade:ReplayClosedTrade}){return <article className="replay-fill-row"><div><b>{trade.side==='long'?'做多':'做空'} · {formatReplayTime(trade.opened)} → {formatReplayTime(trade.closed)}</b><small>入场 {formatReplayPrice(trade.entry)} · 平仓 {formatReplayPrice(trade.exit)}</small></div><span className={replayNumber(trade.net_pnl)>=0?'profit':'loss'}>{numberText(trade.net_pnl)} U<small>费用 {numberText(trade.fees)} U</small></span></article>}
