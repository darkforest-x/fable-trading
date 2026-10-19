import {useAutoStop} from './useAutoStop';
import {entryAvailability} from './entryAvailability';
import {TradeProgress,type EntryProgress} from './TradeProgress';
import {useCloseRecovery} from './useCloseRecovery';
import {TradeActivity} from './TradeActivity';
import {useLiveQuotes} from './useLiveQuotes';
import {ClosePosition} from './ClosePosition';
import {PageHeader} from './PageHeader';
import {DisciplineBudget} from './DisciplineBudget';
import {PositionCard} from './PositionCard';
import {MAIN_TABS,useSwipeNav} from './useSwipeNav';
import {useEffect,useRef,useState,type CSSProperties} from 'react';
import {restoreSession,rememberSession,forgetSession,nativeSession} from './deviceSession';
import {marginRoom,ticketEstimate,allocatedMargin} from './ticketEstimate';
import {AccountSettings,type Account} from './AccountSettings';
import {displayCache,loadStatistics} from './statisticsData';
import {positionAmount} from './positionAmount';
import {ContractOptions} from './ContractOptions';
import {CoinLogo} from './CoinLogo';
import {Icon,type IconName} from './Icon';
import {Statistics} from './Statistics';
import {Dialog,Spinner} from './Dialog';
import {Notification,type Notice} from './Notification';
import {feedback,feedbackEnabled,setFeedbackEnabled} from './feedback';
import {fieldIssue,issueForMessage,errorCopy,type FieldIssue} from './ticketValidation';
import {useKeyboard} from './useKeyboard';
import {api,ApiError,SERVICE_URL,authorizationScope,type Connection,type Plan,type Position,type State,type Ticket} from './api';

const fmt=(v:string|number|undefined,d=2)=>v===undefined?'—':Number(v).toLocaleString('en-US',{maximumFractionDigits:d,minimumFractionDigits:d});
const small=(v:string)=>v.replace('-USDT-SWAP','');
const id=()=>crypto.randomUUID().replaceAll('-','');
const marginLabel=(mode?:string)=>mode==='cross'?'全仓':'逐仓';
type Tab='合约'|'持仓'|'统计'|'纪律'|'设置';
type ExchangeStatus={connected:boolean;environment:'okx_live'|'okx_demo';execution_enabled:boolean;verified_at?:number;trade_permission?:boolean;position_mode?:string;error?:string};
type Preview={plan:Plan;policy_revision:number;ticket:Ticket;request_id:string;preview_id?:string};

export default function App(){
 const settingsOrigin=useRef<Tab>('合约');
 const [navDirection,setNavDirection]=useState(1);
 const [tab,setTab]=useState<Tab>('合约'),[connection,setConnection]=useState<Connection|null>(null),[state,setState]=useState<State|null>(null),[online,setOnline]=useState(false);
 const [token,setToken]=useState(''),[error,setError]=useState(''),[message,setMessage]=useState<Notice|null>(null),[busy,setBusy]=useState(false);
 const [symbol,setSymbol]=useState('BTC-USDT-SWAP'),[kind,setKind]=useState<'market'|'limit'>('market'),[price,setPrice]=useState(''),[stop,setStop]=useState(''),[allocation,setAllocation]=useState(100),[leverage,setLeverage]=useState(100);
 const [preview,setPreview]=useState<Preview|null>(null),[closeTarget,setCloseTarget]=useState<Position|null>(null),[uncertain,setUncertain]=useState(false);
 const live=(state?.mode??connection?.mode)==='okx_live';
 const [restoring,setRestoring]=useState(nativeSession);
 const [pendingEntry,setPendingEntry]=useState<{id:string;symbol:string}|null>(null);
 const pendingKey=(c:Connection)=>`spike-pending:${c.cacheScope}:${c.mode}:${c.accountId??'primary'}`;
 useEffect(()=>{let active=true;void restoreSession().then(async c=>{if(!c)return;c.cacheScope=await authorizationScope(c.url,c.token);if(active)setConnection(c)}).catch(()=>{if(active)notify('本机授权暂不可读，请在设置中重新连接')}).finally(()=>{if(active)setRestoring(false)});return()=>{active=false}},[]);
 useEffect(()=>{if(!connection){setPendingEntry(null);return}try{const raw=localStorage.getItem(pendingKey(connection));const value=raw?JSON.parse(raw):null;setPendingEntry(value&&typeof value.id==='string'&&typeof value.symbol==='string'?value:null)}catch{setPendingEntry(null)}},[connection]);
 const [entryProgress,setEntryProgress]=useState<EntryProgress|null>(null);
 const [fieldError,setFieldError]=useState<FieldIssue|null>(null);
 const [clockTick,setClockTick]=useState(0);
 useEffect(()=>{if(!state||state.cooldown_until<=state.server_time)return;const timer=setInterval(()=>setClockTick(v=>v+1),1000);return()=>clearInterval(timer)},[state?.cooldown_until]);
 const stateReceivedAt=useRef({time:0,local:Date.now()});
 if(state&&stateReceivedAt.current.time!==state.server_time)stateReceivedAt.current={time:state.server_time,local:Date.now()};
 const displayNow=(state?.server_time??0)+(Date.now()-stateReceivedAt.current.local)/1000;
 void clockTick;
 const closeRecovery=useCloseRecovery(connection,status=>{notify(status==='filled'?'平仓成交已确认':status==='not_received'?'平仓请求未送达，已结束等待':status==='cancelled'?'平仓委托已取消，请查看剩余持仓':'平仓请求被拒绝，请查看持仓','info','toast');void refresh().catch(()=>setOnline(false))});
 const [liveConsent,setLiveConsent]=useState(false);
 const noticeModal=!!message&&message.presentation!=='toast';
 const [action,setAction]=useState<{kind:'cancel'|'entry-stop';id:string}|null>(null);
 const searchInput=useRef<HTMLInputElement>(null);
 useEffect(()=>setLiveConsent(false),[preview?.request_id,closeTarget?.id]);
 function notify(text:string,kind:Notice['kind']='info',presentation:Notice['presentation']='toast'){setMessage({text,kind,presentation})}
 useEffect(()=>{if(error)setMessage(null)},[error]);
 const terminalEntry=useRef('');
 const command=useRef(false);
 const epoch=useRef(0);
 const [confirmTrades,setConfirmTrades]=useState(()=>localStorage.getItem('spike-confirm-trades')==='true');
 const [search,setSearch]=useState(''),[contractSort,setContractSort]=useState<'default'|'gainers'|'losers'>('default');
 const editing=useKeyboard();
 useEffect(()=>{if(connection&&!document.hidden)void loadStatistics(connection,30).catch(()=>{})},[connection]);
 const [exchangeStatus,setExchangeStatus]=useState<ExchangeStatus|null>(null);
 useEffect(()=>{
  setExchangeStatus(null);
  if(!connection||tab!=='设置')return;
  let active=true;
  api<ExchangeStatus>(connection,'/v1/exchange-status').then(value=>{if(active)setExchangeStatus(value)}).catch(()=>{if(active)setExchangeStatus({connected:false,environment:'okx_live',execution_enabled:false,error:'核验记录暂不可用'})});
  return()=>{active=false};
 },[connection,tab]);
 const [sheet,setSheet]=useState<'symbol'|'leverage'|null>(null);
 const [draftLeverage,setDraftLeverage]=useState(100);
 const errorDetail=errorCopy(error);
 function dismissError(){setError('');if(!preview&&!closeTarget&&errorDetail.field){requestAnimationFrame(()=>document.querySelector<HTMLInputElement>(`input[aria-label="${errorDetail.field}"]`)?.focus())}}
 const [haptics,setHaptics]=useState(feedbackEnabled),[reviewSide,setReviewSide]=useState<'long'|'short'|null>(null);
 useEffect(()=>{window.scrollTo({top:0});},[tab]);
 const stream=useLiveQuotes([symbol,...(state?.positions.map(p=>p.symbol)??[])],import.meta.env.VITE_SPIKE_LIVE_QUOTES!=='off'&&!!connection&&live&&(tab==='合约'||tab==='持仓'));
 const displayState=state?{...state,quotes:{...state.quotes,...stream},server_time:Math.max(state.server_time,...Object.values(stream).map(q=>q.time))}:null;
 const selectedQuote=stream[symbol]??state?.quotes[symbol];
 const existing=state?.positions.find(pos=>pos.symbol===symbol);
 useEffect(()=>{if(existing){setStop(existing.stop);setLeverage(existing.leverage)}},[existing?.id,existing?.stop,existing?.leverage]);
 const priceDecimals=(coin:string)=>Math.min(12,Math.max(2,Math.ceil(-Math.log10(Number(state?.instruments[coin]?.tick??.01)))));
 const priceFmt=(value:string|undefined,coin=symbol)=>fmt(value,priceDecimals(coin));
 const holdingAmount=(pos:Position)=>positionAmount(pos,state?.quotes[pos.symbol],online,state?.server_time??0,state?.policy?.quote_max_age_seconds??15);
 const fresh=!!selectedQuote&&!!state&&state.server_time-selectedQuote.time<= (state.policy?.quote_max_age_seconds??15)&&online;
 const p=state?.policy;
 const policyCap=symbol==='BTC-USDT-SWAP'||symbol==='ETH-USDT-SWAP'?(p?.major_leverage??100):(p?.alt_leverage??20);
 const cap=Math.min(policyCap,Number(state?.instruments[symbol]?.max_leverage??policyCap));
 useEffect(()=>setDraftLeverage(value=>Math.min(value,cap)),[cap]);
 const mode=p?marginLabel(p.margin_mode):'全仓';
 const proposal=state?.policy_proposal;
 const exposureText=p?(p.major_exposure_multiple&&p.alt_exposure_multiple?`BTC / ETH ${p.major_allowance_scope==='independent'?'各 ':''}${p.major_exposure_multiple}倍 · 山寨合计 ${p.alt_exposure_multiple}倍`:'旧规则未配置名义上限'):'BTC / ETH 10倍 · 山寨 5倍';
 useEffect(()=>{setLeverage(existing?.leverage??cap)},[cap,symbol,state?.policy_revision,existing?.leverage]);
 const room=marginRoom(state,symbol);
 const fraction=String(allocatedMargin(room,allocation));
 const stopContext=JSON.stringify([connection?.cacheScope,connection?.accountId,connection?.mode,symbol,kind,price,allocation,leverage,state?.policy_revision]);
 const [stopFeedback,setStopFeedback]=useState<{side:'long'|'short';value:string;context:string}|null>(null);
 const automatic=useAutoStop({connection,key:stopContext,ticket:{symbol,side:'long',kind,price,stop:'',fraction,leverage,sizing_mode:'margin'},enabled:online&&fresh&&room>0&&!existing&&!pendingEntry&&!state?.blockers.length,
  onApply:(value,side)=>{setStop(value);setFieldError(null);setStopFeedback({side,value,context:stopContext})},onError:message=>setFieldError(issueForMessage(message)??{field:'stop',message})});
 const stopApplied=!!stopFeedback&&stopFeedback.context===stopContext&&stopFeedback.value===stop;
 const autoStopSide=automatic.working?automatic.mode:null;
 const inferredSide=automatic.mode??(existing?.side==='short'?'short':existing?.side==='long'?'long':Number(stop)>Number(kind==='limit'?price:selectedQuote?.last)?'short':'long');
 const estimate=automatic.ready?ticketEstimate(displayState,{symbol,side:inferredSide,kind,price,stop,fraction,leverage,sizing_mode:'margin'}):null;
 const availability=entryAvailability({state,symbol,connected:!!connection,online,fresh,restoring,pending:!!pendingEntry||!!closeRecovery.attempt,room,now:displayNow});
 const blocked=!!availability;
 const reason=availability?.text??'';
 function inlineIssue(issue:FieldIssue|null,focus=false){setFieldError(issue);if(issue&&focus)requestAnimationFrame(()=>document.querySelector<HTMLElement>(`[data-field="${issue.field}"]`)?.focus())}
 function validateField(field:FieldIssue['field']){const issue=fieldIssue({symbol,side:inferredSide,kind,price,stop,fraction,leverage,sizing_mode:'margin'},displayState);if(issue?.field===field)inlineIssue(issue)}
 useEffect(()=>{setFieldError(null);automatic.manual()},[connection?.cacheScope,connection?.accountId,symbol,existing?.id]);
 useEffect(()=>setEntryProgress(null),[connection?.cacheScope,connection?.accountId]);
 useEffect(()=>{if(!connection||!entryProgress||!['pending','partially_filled'].includes(entryProgress.status))return;let active=true;let timer:ReturnType<typeof setTimeout>;const poll=async()=>{try{const result=await api<{status:string}>(connection,`/v1/order-requests/${entryProgress.id}`);if(active&&result.status!==entryProgress.status){setEntryProgress({...entryProgress,status:result.status});void refresh().catch(()=>{})}}catch{}if(active)timer=setTimeout(poll,3000)};timer=setTimeout(poll,3000);return()=>{active=false;clearTimeout(timer)}},[connection,entryProgress?.id,entryProgress?.status]);


 useEffect(()=>{
  if(!connection)return;
  const generation=epoch.current;
  let active=true,running=false;let controller:AbortController|null=null;
  let timer:ReturnType<typeof setTimeout>;
  const poll=async()=>{
   if(!active||running||document.hidden)return;
   running=true;controller=new AbortController();
   const timeout=setTimeout(()=>controller?.abort(),10000);
   try{const next=await api<State>(connection,'/v1/state',undefined,controller.signal);if(active&&generation===epoch.current){if(next.mode!==(connection.mode??'paper')||next.account_id!==(connection.accountId??'primary'))throw Error('账户或服务模式已变化，请重新连接');setState(next);setOnline(true)}}
   catch(e){if(active){setOnline(false);if(e instanceof ApiError&&e.status===401){setConnection(null);notify('设备授权已失效，请在设置中重新授权')}}}finally{running=false;clearTimeout(timeout);if(active&&!document.hidden)timer=setTimeout(poll,2000)}
  };
  const visibility=()=>{clearTimeout(timer);setOnline(false);if(!document.hidden)void poll()};
  document.addEventListener('visibilitychange',visibility);
  void poll();return()=>{active=false;clearTimeout(timer);controller?.abort();document.removeEventListener('visibilitychange',visibility)};
 },[connection]);

 async function refresh(){if(connection){const next=await api<State>(connection,'/v1/state');if(next.mode!==(connection.mode??'paper')||next.account_id!==(connection.accountId??'primary'))throw Error('账户或服务模式已变化，请重新连接');setState(next);setOnline(true)}}
 async function connect(event:React.FormEvent){
  event.preventDefault();setError('');setBusy(true);
  try{const url=SERVICE_URL;const c:Connection={url,token:token.trim()};if(c.token.length<32)throw Error('服务令牌格式不正确');
   const health=await api<{service:string;mode:string;live_enabled:boolean}>({url,token:''},'/health');
   if(health.service!=='spike-discipline'||!['paper','okx_live'].includes(health.mode))throw Error('不是独立纪律服务');c.mode=health.mode as 'paper'|'okx_live';
   c.cacheScope=await authorizationScope(c.url,c.token);const next=await api<State>(c,'/v1/state');if(next.mode!==c.mode)throw Error('账户模式不匹配');c.accountId=next.account_id??'primary';await rememberSession(c);epoch.current++;setConnection(c);setState(next);setOnline(true);setToken('');setTab('合约');notify('已连接','success','toast');
   feedback('light');
  }catch(e){setError((e as Error).message);feedback('error')}finally{setBusy(false)}
 }
 useEffect(()=>{
  if(!connection||!pendingEntry)return;
  let active=true;let timer:ReturnType<typeof setTimeout>;
  const c=connection,key=pendingKey(c),identity=pendingEntry.id;
  let missingReads=0;
  const poll=async()=>{
   if(!active||document.hidden){if(active)timer=setTimeout(poll,3000);return}
   try{const result=await api<{id:string;status:string;message?:string}>(c,`/v1/order-requests/${identity}`);
    if(result.status==='not_found'){
     missingReads++;
     if(active&&missingReads>=6)await api(c,`/v1/order-requests/${identity}/abandon-unreceived`,{});
    }else missingReads=0;
    if(active&&result.id===identity&&['pending','partially_filled','filled','closed','rejected','cancelled','interrupted'].includes(result.status)){
     terminalEntry.current=identity;localStorage.removeItem(key);setPendingEntry(null);setEntryProgress({id:identity,symbol:pendingEntry.symbol,status:result.status});
     if(['pending','partially_filled','filled','closed'].includes(result.status)){notify(result.status==='pending'?'限价单已挂出，可在持仓页撤单':result.status==='partially_filled'?'部分成交，剩余委托继续等待':result.status==='filled'?'已成交，保护状态请查看持仓':'订单已完成','success');feedback('success')}else{const issue=issueForMessage(result.message??'');if(issue)inlineIssue(issue,true);else setError(result.message??'本次委托未成交');feedback('warning')}
     void refresh().catch(()=>setOnline(false));return;
    }
   }catch{/* Read-only reconciliation continues; never resend an order on timeout. */}
   if(active)timer=setTimeout(poll,3000);
  };
  void poll();return()=>{active=false;clearTimeout(timer)};
 },[connection,pendingEntry?.id]);
 async function submitEntry(order:{ticket:Ticket;request_id:string;policy_revision:number;preview_id?:string}){
  const c=connection!;
  if(live){
   const pending={id:order.request_id,symbol:order.ticket.symbol};
   // Persist the identity before transmission, so app restarts never create a retry order.
   localStorage.setItem(pendingKey(c),JSON.stringify(pending));setPendingEntry(pending);setEntryProgress({...pending,status:'processing'});setPreview(null);setUncertain(false);
   try{await api(c,'/v1/order-requests',{request_id:order.request_id,policy_revision:order.policy_revision,ticket:order.ticket,preview_id:order.preview_id??null,confirmed_live:true});/* Inline card follows the durable request. */}
   catch(e){if(terminalEntry.current===order.request_id)return;if(e instanceof ApiError&&e.status<500){localStorage.removeItem(pendingKey(c));setPendingEntry(null);setEntryProgress({...pending,status:'rejected'});const issue=issueForMessage(e.message);if(issue)inlineIssue(issue,true);else setError(e.message)}else setEntryProgress({...pending,status:'checking'})}
   return;
  }
  try{await api(c,'/v1/orders',{request_id:order.request_id,policy_revision:order.policy_revision,ticket:order.ticket});setPreview(null);notify('模拟委托已受理','success');await refresh();feedback('success')}
  catch(e){setError((e as Error).message)}
 }
 async function review(side:'long'|'short'){
  if(!connection||command.current||pendingEntry||!automatic.ready)return;setError('');setBusy(true);setReviewSide(side);command.current=true;feedback('light');
  try{const ticket:Ticket={symbol,side,kind,price:price.trim(),stop:stop.trim(),fraction,leverage,sizing_mode:'margin'};const invalid=fieldIssue(ticket,displayState);if(invalid){inlineIssue(invalid,true);return}setFieldError(null);(document.activeElement as HTMLElement)?.blur();if(live&&!confirmTrades){await submitEntry({ticket,request_id:id(),policy_revision:state!.policy_revision});return}const result=await api<{plan:Plan;policy_revision:number;preview_id?:string}>(connection,'/v1/preview',ticket);const order={...result,ticket,request_id:id()};setUncertain(false);if(confirmTrades)setPreview(order);else await submitEntry(order)}
  catch(e){const text=(e as Error).message;const issue=issueForMessage(text);if(issue)inlineIssue(issue,true);else setError(text);feedback('warning')}finally{setBusy(false);setReviewSide(null);command.current=false}
 }
 async function confirm(){
  if(!connection||!preview||command.current||(confirmTrades&&live&&!liveConsent))return;command.current=true;setBusy(true);setError('');
  try{await submitEntry(preview)}finally{command.current=false;setBusy(false)}
 }
 async function switchAccount(account:Account){
  if(!connection||command.current||pendingEntry)return;command.current=true;setBusy(true);setError('');epoch.current++;feedback('light');
  try{const nextConnection={...connection,accountId:account.id};const next=await api<State>(nextConnection,'/v1/state');if(next.account_id!==account.id||next.mode!==connection.mode)throw Error('账户响应不匹配');await rememberSession(nextConnection);setConnection(nextConnection);setState(next);setOnline(true);setPreview(null);setCloseTarget(null);setUncertain(false);setStop('');setPrice('');setAllocation(100);notify(`已切换至 ${account.name}`,'success','toast');feedback('light')}
  catch(e){setError((e as Error).message);setConnection({...connection})}finally{command.current=false;setBusy(false)}
 }
 async function cancelOrder(identity:string){
  if(!connection||command.current)return;command.current=true;setBusy(true);setError('');setAction({kind:'cancel',id:identity});feedback('light');
  try{await api(connection,`/v1/orders/${identity}/cancel`,{request_id:id()});notify('撤单已提交，成交与次数正在核对','success');feedback('success');await refresh().catch(()=>setOnline(false))}
  catch(e){setError((e as Error).message);feedback('warning')}finally{command.current=false;setBusy(false);setAction(null)}
 }
 const closeKey=useRef('');

 const [closePercent,setClosePercent]=useState(50),[closeLocked,setCloseLocked]=useState(false);
 async function close(target=closeTarget,recovered=false){
  const prior=closeRecovery.attempt;
  if(!connection||command.current||(!target&&!prior))return;
  if(prior&&!recovered)return;
  const identity=recovered?prior!.position:target!.id,percent=recovered?prior!.percent:closePercent,key=recovered?prior!.id:closeKey.current;
  command.current=true;setBusy(true);setError('');
  try{
   if(live&&!recovered){try{closeRecovery.begin({id:key,position:identity,symbol:target!.symbol,percent,created:Date.now()})}catch{setError('本机无法保存恢复记录，本次平仓尚未发送');return}}
   if(live)setCloseTarget(null);
   await api(connection,`/v1/positions/${identity}/close`,{request_id:key,fraction:String(percent/100),...(live?{confirmed_live:true}:{})});
   setCloseLocked(false);setCloseTarget(null);
   if(!live)notify(percent===100?'模拟持仓已平仓':'模拟仓位已部分平仓','success');
   closeRecovery.check();feedback('success');await refresh().catch(()=>setOnline(false));
  }catch(e){if(live){closeRecovery.check();notify('正在核对原平仓请求，请查看进度','progress')}else{setCloseTarget(target);setError((e as Error).message);feedback('warning')}}
  finally{command.current=false;setBusy(false)}
 }
 function autoStop(side:'long'|'short'){
  const issue=fieldIssue({symbol,side,kind,price,stop:'',fraction,leverage,sizing_mode:'margin'},displayState,true);
  if(issue){inlineIssue(issue,true);return}setFieldError(null);automatic.select(side);
 }
 async function entryStop(pos:Position){
  if(!connection||command.current)return;command.current=true;setBusy(true);setAction({kind:'entry-stop',id:pos.id});feedback('light');
  try{await api(connection,`/v1/positions/${pos.id}/entry-stop`,{request_id:id(),confirmed_live:live});notify('止损保护已核对，开仓价不包含手续费','success');await refresh().catch(()=>setOnline(false));feedback('success')}
  catch(e){setError((e as Error).message);feedback('warning')}finally{command.current=false;setBusy(false);setAction(null)}
 }
 function requestClose(pos:Position){if(command.current)return;if(closeRecovery.attempt){notify('请先等待原平仓请求完成核对');return}closeKey.current=id();setClosePercent(50);setCloseLocked(false);setCloseTarget(pos);feedback('light')}

 function nav(next:Tab){if(next!==tab){feedback('light');if(next==='设置')settingsOrigin.current=tab;setNavDirection(next==='设置'?1:tab==='设置'?-1:MAIN_TABS.indexOf(next as typeof MAIN_TABS[number])>MAIN_TABS.indexOf(tab as typeof MAIN_TABS[number])?1:-1)}setTab(next);setError('');setMessage(null)}
 function choose(next:string){automatic.manual();if(next!==symbol)feedback('light');setSymbol(next);setAllocation(100);setPrice('');setStop('')}
 function dismissPreview(){setPreview(null);if(uncertain){setTab('持仓');void refresh().catch(()=>setOnline(false))}}
 function closeProgress(){return <section className="trade-progress close-recovery" aria-label="平仓请求进度"><div className="progress-heading"><Spinner/><b>{closeRecovery.attempt?.symbol.replace('-USDT-SWAP','')} · 平仓 {closeRecovery.attempt?.percent}%</b></div><p role="status">{closeRecovery.status==='ready'?'原请求尚未发送，可继续提交':'正在核对原平仓请求'}</p><div className="recovery-actions"><button disabled={busy} onClick={()=>{closeRecovery.check();feedback('light')}}>刷新核对</button>{closeRecovery.status==='ready'&&<button disabled={busy} onClick={()=>void close(null,true)}>以原请求重试</button>}</div></section>}
 const metrics=(plan:Plan)=><dl className="metrics">{[['初始止损价格',`${priceFmt(plan.stop)} USDT`],['入场价格边界',`${priceFmt(plan.entry)} USDT`],['名义仓位',`${fmt(plan.notional)} USDT`],['本单名义 / 净值',`${fmt(plan.notional_equity_multiple)} 倍`],['预计保证金',`${fmt(plan.margin)} USDT`],['价格止损风险',`${fmt(plan.price_risk)} USDT`],['成本预留',`${fmt(plan.costs)} USDT`],['初始计划风险 1R',`${fmt(plan.risk)} USDT`]].map(([k,v])=><div key={k}><dt>{k}</dt><dd>{v}</dd></div>)}</dl>;


 const riskPercent=Number(p?.risk_fraction??0.05)*100;
 const steps=[{r:p?.breakeven_r??'1',label:'成本保本'},{r:p?.take_profits[0][0]??'3',label:`平原始 ${Number(p?.take_profits[0][1]??.6)*100}%`},{r:p?.take_profits.at(-1)?.[0]??'5',label:'全部余仓'}];
 const swipe=useSwipeNav(tab,!!sheet||!!preview||!!closeTarget||!!error||noticeModal||editing||busy,nav);
 const navIcons:Record<Tab,IconName>={'合约':'trade','持仓':'positions','统计':'trend','纪律':'shield','设置':'link'};
 const ruleGroups=[
  {title:'单笔与仓位',icon:'trade' as IconName,rows:[['单笔计划风险',p?`净值的 ${riskPercent}%`:'待连接服务'],['保证金模式',p?mode:'待连接服务'],['杠杆上限',p?`BTC / ETH ${p.major_leverage}x · 山寨 ${p.alt_leverage}x`:'待连接服务'],...(p?.major_allowance_scope==='independent'?[['BTC 独立额度',`保证金 ${Number(p.major_margin_fraction??0)*100}% · 名义 ${p.major_exposure_multiple}倍`],['ETH 独立额度',`保证金 ${Number(p.major_margin_fraction??0)*100}% · 名义 ${p.major_exposure_multiple}倍`],['山寨币共享额度',`保证金 ${Number(p.alt_margin_fraction??0)*100}% · 名义 ${p.alt_exposure_multiple}倍`]]:[['保证金占比上限',p?`BTC / ETH 合计 ${Number(p.major_margin_fraction??0)*100}% · 山寨合计 ${Number(p.alt_margin_fraction??0)*100}%`:'待连接服务']]),['同时持仓与挂单',p?`合计最多 ${p.max_positions} 笔`:'待连接服务'],['每日基础次数',p?`${p.max_entries} 次`:'待连接服务'],['盈利平仓奖励',p?(p.profit_entry_bonus?'整笔净盈利平仓，当天 +1 次':'未启用'):'待连接服务'],['奖励计次','每笔仅一次，分批止盈不重复计'],['额度计算',p?.major_allowance_scope==='independent'?'BTC、ETH 各自独立，山寨币共享；持仓与挂单合计占用':'同类持仓与挂单共享保证金额度']]},
  {title:'停手与冷静',icon:'clock' as IconName,rows:[['连续净亏损',p?`${p.loss_streak} 笔停手`:'待连接服务'],['日亏停手线',p?`当日起始净值的 ${Number(p.daily_loss_fraction)*100}%`:'待连接服务'],['冷静期触发',p?`连亏 ${p.cooldown_after_losses??1} 笔后 ${p.loss_cooldown_seconds/3600} 小时`:'待连接服务'],['盈利 / 分批止盈','不触发冷静期']]},
  {title:'自动保护',icon:'shield' as IconName,rows:[['成本保本',p?`${p.breakeven_r}R`:'待连接服务'],['分批止盈',p?`${p.take_profits.map(([r,f],i)=>`${r}R ${i===p.take_profits.length-1?'全部余仓':`平原始 ${Number(f)*100}%`}`).join(' · ')}`:'待连接服务']]}
 ];

 return <div className={`app ${tab==='合约'?'trade-page':''} ${editing?'keyboard-open':''}`}>
  <div inert={!!preview||!!closeTarget||!!error||!!sheet||noticeModal}>
  <main key={tab} className="page-transition" style={{'--page-offset':`${navDirection*12}px`} as CSSProperties} {...swipe}>
   {tab==='合约'&&<>
    <section className="quote-hero" aria-label="合约行情">
     <div className="trade-heading"><button className="symbol-picker" aria-label="选择合约" aria-haspopup="dialog" onClick={()=>{setSearch('');setSheet('symbol');feedback('light')}}><CoinLogo key={symbol} coin={small(symbol)}/><b>{small(symbol)}<small>/USDT</small></b><span className="down-chevron"/></button><span className="neutral">永续</span><span className="instrument-mode">{mode}</span><button className="settings-gear" aria-label="设置" onClick={()=>nav('设置')}><Icon name="settings" size={21}/></button></div>
     <div className="price-row"><strong className={!fresh?'stale':''}>{priceFmt(selectedQuote?.last)}</strong><span>USDT</span><span className={`freshness ${fresh?'connected':''}`}><i/>{fresh?'实时':selectedQuote?'待更新':'待连接'}</span></div>
    </section>
    <DisciplineBudget state={state} online={online} onOpen={()=>nav('纪律')}/>
    {connection&&reason&&<button className="guard-strip" onClick={()=>nav(availability?.tab??'纪律')}><Icon name="lock" size={18}/><span>{reason}</span><Icon name="chevron" size={16}/></button>}
    <TradeProgress entry={pendingEntry?{...pendingEntry,status:'checking'}:entryProgress} state={state} onOpen={()=>nav('持仓')} onDismiss={()=>setEntryProgress(null)}/>{closeRecovery.attempt&&closeProgress()}
    <section className="trade-card" aria-label="下单设置">
     <div className="segment" aria-label="委托类型"><button aria-pressed={kind==='market'} className={kind==='market'?'active':''} onClick={()=>{if(kind!=='market')feedback('light');setKind('market')}}>市价</button><button aria-pressed={kind==='limit'} className={kind==='limit'?'active':''} onClick={()=>{if(kind!=='limit')feedback('light');setKind('limit')}}>限价</button></div>
     <button className="leverage-field" data-field="leverage" disabled={!!existing} aria-label={`调整杠杆，当前 ${leverage} 倍`} aria-haspopup="dialog" onClick={()=>{setDraftLeverage(leverage);setSheet('leverage');feedback('light')}}><span>杠杆</span><b>{leverage}<small>x</small></b><span className="label-note">{existing?'沿用持仓':`最高 ${cap}x`}</span><Icon name="chevron" size={15}/></button>

     {kind==='limit'&&<label className="field"><span className="field-label">委托价格</span><span className="input-shell"><input data-field="price" aria-invalid={fieldError?.field==='price'} aria-describedby={fieldError?.field==='price'?'field-price-error':undefined} onBlur={()=>validateField('price')} aria-label="委托价格" inputMode="decimal" enterKeyHint="done" autoComplete="off" onKeyDown={e=>{if(e.key==='Enter')e.currentTarget.blur()}} value={price} onChange={e=>{setPrice(e.target.value);setFieldError(null)}} placeholder="输入委托价格"/><span>USDT</span></span>{fieldError?.field==='price'&&<span id="field-price-error" className="field-error" role="status">{fieldError.message}</span>}</label>}
     <div className={`field stop-field ${stopApplied?'stop-applied':''}`}>
      <span className="field-label">{existing?'当前止损':'初始止损'} <em>{existing?'沿用':'必填'}</em>{!existing&&<small>{(['long','short'] as const).map(side=><button key={side} type="button" aria-label={side==='long'?'多单 1R':'空单 1R'} aria-busy={autoStopSide===side} aria-pressed={automatic.mode===side} className={autoStopSide===side?'calculating':automatic.mode===side?'applied':''} disabled={blocked||busy} onClick={()=>void autoStop(side)}>{autoStopSide===side?<><Spinner/>计算中</>:<>{stopApplied&&stopFeedback?.side===side&&<Icon name="check" size={12}/>} {side==='long'?'多单':'空单'} 1R</>}</button>)}</small>}</span>
      <span className="input-shell"><input data-field="stop" aria-invalid={fieldError?.field==='stop'} onBlur={()=>validateField('stop')} aria-label="初始止损" aria-describedby={fieldError?.field==='stop'?'field-stop-error':stopApplied?'stop-applied-status':undefined} readOnly={!!existing} inputMode="decimal" enterKeyHint="done" autoComplete="off" onKeyDown={e=>{if(e.key==='Enter')e.currentTarget.blur()}} value={stop} onChange={e=>{automatic.manual();setStop(e.target.value);setStopFeedback(null);setFieldError(null)}} placeholder="输入止损价格"/>{stopApplied&&<span className="stop-applied-mark" aria-hidden="true"><Icon name="check" size={14}/></span>}<span>USDT</span></span>
      {automatic.mode&&<span className="auto-mode-note">{automatic.mode==='long'?'多单':'空单'} 1R · {automatic.working?'重新计算中':automatic.ready?'自动更新':'等待有效参数'}<button onClick={()=>automatic.manual()}>转为手动</button></span>}{fieldError?.field==='stop'&&<span id="field-stop-error" className="field-error" role="status">{fieldError.message}</span>}<span id="stop-applied-status" className="sr-only" role="status" aria-live="polite">{stopApplied?`${stopFeedback?.side==='long'?'多单':'空单'} 1R 止损已填入，含成本预留`:''}</span>
     </div>
     <div className="budget"><div className="slider-heading"><label htmlFor="position-size">仓位额度 <small>当前可用</small></label><output htmlFor="position-size">{allocation}<small>%</small></output></div><input data-field="sizing" aria-describedby={fieldError?.field==='sizing'?'field-sizing-error':undefined} id="position-size" aria-label="仓位额度" aria-valuetext={`${allocation}% 可用额度，保证金占账户净值 ${fmt(Number(fraction)*100,2)}%`} type="range" min="0" max="100" step="1" disabled={!state||room<=0} value={allocation} onChange={e=>{setAllocation(Number(e.target.value));setFieldError(null);feedback('tick')}} style={{'--fill':`${allocation}%`} as CSSProperties}/><div className="slider-scale"><span>0%</span><span>保证金占净值 {state?fmt(Number(fraction)*100,2):'—'}%</span></div></div>
     {fieldError&&['sizing','leverage'].includes(fieldError.field)&&<p id="field-sizing-error" className="field-error" role="status">{fieldError.message}</p>}<div className="ticket-estimate" aria-live="polite">{[[existing?'本次持仓金额':'持仓金额',estimate?.notional],['预计保证金',estimate?.margin],[existing?'合并止损金额':'预计止损',estimate?.risk]].map(([label,value])=><div key={String(label)}><span>{String(label)}</span><b>{fmt(value as number|undefined)}<small> USDT</small></b></div>)}</div>
     <div className="equity"><span>{live?`${state?.account_name??'主账户'} · USDT 净值`:'模拟净值'}</span><b>{fmt(state?.equity)} <small>USDT</small></b></div>
     <div className="order-actions"><button className="buy" disabled={blocked||busy||!automatic.ready||existing?.side==='short'||automatic.mode==='short'} onClick={()=>review('long')}>{reviewSide==='long'?<><Spinner/>正在校验…</>:existing?.side==='long'?'买入加仓':'买入开多'}</button><button className="sell" disabled={blocked||busy||!automatic.ready||existing?.side==='long'||automatic.mode==='long'} onClick={()=>review('short')}>{reviewSide==='short'?<><Spinner/>正在校验…</>:existing?.side==='short'?'卖出加仓':'卖出开空'}</button></div>
     {!connection?<button className="primary connect-action" onClick={()=>nav('设置')}><Icon name="link" size={17}/>连接纪律服务<Icon name="arrow" size={17}/></button>:null}
    </section>
    {!!state?.positions.length&&<section className="home-positions"><div className="section-heading"><h2>当前持仓 <span>{state.positions.length}</span></h2><button className="plain" onClick={()=>nav('持仓')}>全部持仓 ›</button></div>{state.positions.map(pos=><PositionCard key={pos.id} pos={pos} state={displayState!} online={online} busy={busy||!!closeRecovery.attempt} entryStopBusy={action?.kind==='entry-stop'&&action.id===pos.id} compact onEntryStop={entryStop} onClose={requestClose}/>)}</section>}
    <section className="protection-preview" aria-label="自动保护计划"><div className="section-heading"><h2>自动保护</h2><span>{p?'按服务端规则':'连接后校验规则'}</span></div><div className="protection-rail">{steps.map(t=><div key={t.r}><span className="rail-node"><i/></span><b>{t.r}R</b><small>{t.label}</small></div>)}</div><p>R 固定为开仓时的计划风险</p></section>
    <div className="overview"><button onClick={()=>nav('持仓')}><span>持仓</span><b>{state?.positions.length??'—'}</b></button><button onClick={()=>nav('持仓')}><span>挂单</span><b>{state?.pending.length??'—'}</b></button><button onClick={()=>nav('纪律')}><span>今日申请</span><b>{state?.daily?.entries??'—'}<small> / {state?.discipline?.entries_limit??p?.max_entries??3}</small></b></button></div>
    <p className="disclaimer">{live?'真实资金交易。市价按价格边界成交，限价挂单等待成交；止损和止盈以交易所回执为准，实际损失可能超出预算。':'计划风险含成本预留，实际损失可能超出预算。当前没有交易所真实保护单。'}</p>
   </>}
   {tab==='持仓'&&<>
    <PageHeader title="持仓保护" subtitle={`${state?.account_name??'当前账户'} · 每笔交易都有退出计划。`} onSettings={()=>nav('设置')}/>
    <TradeProgress entry={pendingEntry?{...pendingEntry,status:'checking'}:entryProgress} state={state} onOpen={()=>document.querySelector('.activity-list')?.scrollIntoView({block:'start'})} onDismiss={()=>setEntryProgress(null)}/>{closeRecovery.attempt&&closeProgress()}{!state?<section className="empty card"><span className="empty-icon"><Icon name="positions" size={32}/></span><h2>连接后查看持仓</h2><p>仓位与保护记录保存在你的服务端。</p><button className="primary" onClick={()=>nav('设置')}>连接纪律服务</button></section>:!state.positions.length&&<section className="empty card"><span className="empty-icon"><Icon name="positions" size={32}/></span><h2>暂时没有持仓</h2><p>先设好止损，再开始一笔有计划的交易。</p><button className="soft-button" onClick={()=>nav('合约')}>查看合约<Icon name="arrow" size={16}/></button></section>}
    {state?.positions.map(pos=><PositionCard key={pos.id} pos={pos} state={displayState!} online={online} busy={busy||!!closeRecovery.attempt} entryStopBusy={action?.kind==='entry-stop'&&action.id===pos.id} onEntryStop={entryStop} onClose={requestClose}/>)}
    {state&&<TradeActivity state={state} busy={busy} cancelling={action?.kind==='cancel'?action.id:undefined} onCancel={cancelOrder} price={priceFmt}/>}
    <p className="disclaimer">{live?'已确认的交易所止损止盈不依赖手机在线。Mac 离线期间不能执行新的 1R 保本调整或应急指令。':'计划风险固定，实际损失可能超出预算。服务停机期间不会执行自动退出。'}</p>
   </>}
   {tab==='统计'&&<Statistics overview={state} online={online} connection={connection} onConnect={()=>nav('设置')}/>}
   {tab==='纪律'&&<>
    <PageHeader title="交易纪律" subtitle="把规则提前定好，把执行交给系统。" onSettings={()=>nav('设置')}/>
    <div className={reason?'warning':'healthy'}><Icon name={reason?'lock':'shield'} size={18}/><span>{reason||'服务端规则生效中'}</span></div>
    <DisciplineBudget state={state} online={online} onOpen={()=>{}}/>
    <section className="card discipline-summary"><span className="badge"><Icon name="lock" size={13}/>{p?'当前生效规则':'连接后读取规则'}</span><div className="summary-grid"><div><b>{p?riskPercent:'—'}<small>%</small></b><span>单笔计划风险</span></div><div><b>{p?.max_entries??'—'}<small>次</small></b><span>每日基础次数</span></div></div><p>{p?.profit_entry_bonus?'盈利全平 +1 次 · 日亏与连亏停手优先':'刷新、退出或换手机，记录依然保留。'}</p></section>
    {proposal&&<section className="card pending-policy-card"><span className="badge"><Icon name="clock" size={13}/>待生效变更</span><h2>{marginLabel(proposal.policy.margin_mode)} · 单笔风险 {Number(proposal.policy.risk_fraction)*100}%</h2><p>账户名义上限：BTC / ETH {proposal.policy.major_exposure_multiple}倍 · 山寨 {proposal.policy.alt_exposure_multiple}倍</p><p>最早 {new Date(proposal.effective_after*1000).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai',hour12:false})}（北京时间）生效，届时需无持仓、无挂单。</p><small>当前规则、次数和冷静期继续保留。</small></section>}
    {ruleGroups.map(group=><section className="card rule-card" key={group.title}><h2><Icon name={group.icon} size={18}/>{group.title}</h2><div className="rules">{group.rows.map(([k,v])=><div key={k}><span>{k}</span><b>{v}</b></div>)}</div></section>)}
    <section className="card rule-card"><h2><Icon name="clock" size={18}/>当前记录</h2><dl className="metrics"><div><dt>今日已用次数</dt><dd>{state?.daily?.entries??'—'} / {state?.discipline?.entries_limit??p?.max_entries??3}</dd></div><div><dt>今日盈利奖励</dt><dd>+{state?.discipline?.entries_bonus??0} 次</dd></div><div><dt>连续净亏损</dt><dd>{state?.loss_streak??'—'} 笔</dd></div><div><dt>冷静期结束</dt><dd>{!state?'待连接':state.cooldown_until>state.server_time?new Date(state.cooldown_until*1000).toLocaleString('zh-CN'):'未触发'}</dd></div></dl></section>
    <details className="explainer"><summary>规则计算与运行边界<Icon name="chevron" size={16}/></summary><p>每日按北京时间 00:00 切换。未成交撤单经交易所确认后返还次数，已成交不返还。规则以当前生效版本为准。</p><p>保证金占比按账户净值计算：BTC/ETH 合计不超过 10%，山寨合计不超过 5%；同类已有持仓与挂单占用会扣除，单笔计划止损风险仍不超过净值 5%。名义仓位另保留总额度校验：BTC/ETH 名义金额 ÷ 10 + 山寨名义金额 ÷ 5，不得超过净值。开仓与成交前检查，并预留入场费用与不利价差；行情变化仍可能使已有仓位超过比例上限。</p><p>R 按确认的开仓计划固定，包含费用与滑点预留。止盈比例按原始仓位计算。成本预留费率每边 {Number(p?.fee_rate??.0005)*100}%，滑点预留 {Number(p?.slippage_rate??.001)*100}%，未计资金费，不代表你的实际交易所费率。</p><p>日亏停手线不代替每笔止损，多个持仓风险会叠加。{live?'资金费、滑点与全仓强平仍受交易所规则影响；实盘止损不是最大亏损保证。':'当前模拟不计算交易所真实强平价。'}</p></details>
   </>}
   {tab==='设置'&&<>
    <PageHeader title="设置与账户" subtitle="账户、操作习惯，都在这里。" onBack={()=>nav(settingsOrigin.current)}/>
    {!connection&&<form className="card connection-form" onSubmit={connect}><h2>设备授权</h2><p className="micro">首次授权后，App 会记住这台设备并自动连接。</p><details><summary>首次设备配置</summary><label className="field"><span className="field-label">服务访问令牌<Icon name="lock" size={14}/></span><span className="input-shell"><input aria-label="服务访问令牌" type="password" autoComplete="off" value={token} onChange={e=>setToken(e.target.value)} placeholder="仅首次填写" required/></span></label><button className="primary" disabled={busy}>{busy?<><Spinner/>正在连接…</>:'连接'}<Icon name="arrow" size={18}/></button><p className="micro">{nativeSession?'授权保存在本机安全存储中。':'网页预览仅在本次会话保留授权。'}</p></details></form>}
    {connection&&<AccountSettings connection={connection} busy={busy||!!pendingEntry} onSwitch={switchAccount} onError={setError}/>}
    <section className="card experience-settings"><h2>交易操作</h2><label className="haptics-setting"><span><b>开仓二次确认</b><small>开仓可跳过二次确认；平仓选择比例后提交，风控仍执行。</small></span><input type="checkbox" role="switch" aria-label="开仓二次确认" checked={confirmTrades} disabled={busy} onChange={e=>{setConfirmTrades(e.target.checked);localStorage.setItem('spike-confirm-trades',String(e.target.checked));feedback('light')}}/><i aria-hidden="true"/></label></section>
    {connection&&<details className="card connection-card service-details"><summary>连接状态 <span className={`freshness ${online?'connected':''}`}><i/>{online?'已连接':'已中断'}</span></summary><p className="micro">{exchangeStatus?.error??'已有持仓由服务器持续管理。'}</p><button className="soft-button" disabled={busy} onClick={()=>{void forgetSession().then(()=>{epoch.current++;displayCache.clear();setConnection(null);setOnline(false);setState(null);notify('已清除本机授权；服务端继续管理已有持仓','info','toast')}).catch(()=>setError('本机授权未清除，请重试'))}}>清除本机授权</button></details>}
    <section className="card experience-settings"><h2>体验设置</h2><label className="haptics-setting"><span><b>触感反馈</b><small>仅手机端，切换与重要操作时轻触反馈</small></span><input type="checkbox" role="switch" aria-label="触感反馈" checked={haptics} onChange={e=>{setHaptics(e.target.checked);setFeedbackEnabled(e.target.checked)}}/><i aria-hidden="true"/></label><p className="micro">行情刷新不触发震动。动画遵循系统的减少动态效果设置。</p></section>
    <div className="connection-promises"><p><Icon name="shield" size={18}/><span><b>{live?'独立实盘风控记录':'独立的模拟账户'}</b><small>模拟与实盘记录分开，重启不会清空风控。</small></span></p><p><Icon name="clock" size={18}/><span><b>Mac 在线，保护才持续</b><small>关闭 App 后服务继续管理；Mac 睡眠或断网会暂停新的保护调整。</small></span></p></div>
    <p className="disclaimer">{live?'实盘仅按你点击提交的指令执行。不能限制你在 OKX 官方 App 中的操作。':'仅模拟交易，未启用实盘。不能限制 OKX 官方 App 中的操作。'}</p>
   </>}
  </main>
  {editing&&<div className="keyboard-toolbar"><span>填写完成后继续</span><button onPointerDown={e=>e.preventDefault()} onClick={()=>{(document.activeElement as HTMLElement)?.blur();feedback('light')}}>完成<Icon name="check" size={17}/></button></div>}
  <nav hidden={editing} className={`bottom-nav ${tab==='设置'?'nav-settings':''}`} style={{'--nav-index':Math.max(0,MAIN_TABS.indexOf(tab as typeof MAIN_TABS[number]))} as CSSProperties} aria-label="主导航">{MAIN_TABS.map(t=><button key={t} aria-current={tab===t?'page':undefined} className={tab===t?'active':''} onClick={()=>nav(t)}><Icon name={navIcons[t]} size={21}/><span>{t}</span></button>)}</nav>
  </div>
  {!error&&preview&&<Dialog label={live?'确认实盘订单':'确认模拟订单'} busy={busy} onDismiss={dismissPreview}>{dismiss=><><span className="eyebrow">确认交易计划</span><h2>{live?'确认实盘订单':'确认模拟订单'}</h2>{live&&<span className="badge live-mode">真实资金 · OKX 实盘执行</span>}<div className="position-head"><b>{small(preview.ticket.symbol)}USDT</b><span className={preview.ticket.side==='long'?'long':'short'}>{preview.ticket.side==='long'?'开多':'开空'} · {marginLabel(preview.plan.margin_mode)} {preview.ticket.leverage}x</span></div>{metrics(preview.plan)}{preview.plan.exposure_limited&&<p className="micro">本单已按账户剩余名义仓位额度缩小。</p>}<div className="warning"><Icon name="shield" size={18}/><span>{live?`${preview.ticket.kind==='limit'?'限价挂单等待成交，可在持仓页撤单':'全部成交或取消'}；保护异常会尝试平掉余仓，实际损失可能超出预算。`:'提交时重新校验，这不是实盘订单。'}</span></div>{uncertain&&<p className="error">上次请求结果未确认。可用同一编号重试，服务端会去重。也可先查看持仓核对。</p>}{error&&<p className="error" role="alert">{error}</p>}{live&&confirmTrades&&<label className="live-consent"><input type="checkbox" checked={liveConsent} onChange={e=>setLiveConsent(e.target.checked)}/><span>我确认使用真实资金执行本次交易</span></label>}<button className="primary" disabled={busy||(confirmTrades&&live&&!liveConsent)} onClick={confirm}>{busy?<><Spinner/>正在提交…</>:uncertain?'以同一请求重试':live?'确认实盘开仓':'确认模拟开仓'}</button><button className="plain" disabled={busy} onClick={dismiss}>{uncertain?'先查看服务端持仓':'返回修改'}</button></>}</Dialog>}
  {!error&&closeTarget&&<ClosePosition position={closeTarget} state={state} online={online} percent={closePercent} onPercent={setClosePercent} busy={busy} locked={closeLocked} onClose={()=>void close()} onDismiss={()=>setCloseTarget(null)}/>}
  {!error&&sheet==='symbol'&&<Dialog key="symbol-sheet" label="选择合约" busy={false} onDismiss={()=>setSheet(null)}>{dismiss=><><h2>选择合约</h2><p className="muted sheet-intro">全部 USDT 永续 · {Object.values(state?.instruments??{}).filter(i=>i.tradable!==false).length} 个合约</p><label className="input-shell search-contract"><input ref={searchInput} aria-label="搜索合约" value={search} onChange={e=>setSearch(e.target.value)} placeholder="搜索币种，例如 BTC、DOGE" autoComplete="off" autoCapitalize="characters"/>{search&&<button type="button" className="clear-search" aria-label="清空搜索" onPointerDown={e=>e.preventDefault()} onClick={()=>{setSearch('');searchInput.current?.focus();feedback('light')}}><Icon name="close" size={17}/></button>}</label><div className="contract-sort"><span>今日 · 北京时间</span>{(['default','gainers','losers'] as const).map((v,i)=><button key={v} aria-pressed={contractSort===v} onClick={()=>{setContractSort(v);feedback('light')}}>{['默认','涨幅 ↓','跌幅 ↑'][i]}</button>)}</div><ContractOptions sort={contractSort} state={state} search={search} selected={symbol} onSelect={value=>{choose(value);dismiss()}}/>{!connection&&<p className="micro">连接后读取 OKX 合约列表。</p>}<p className="micro">切换合约会清空委托价格和止损。</p></>}</Dialog>}

  {!error&&sheet==='leverage'&&<Dialog key="leverage-sheet" label="调整杠杆" busy={false} onDismiss={()=>setSheet(null)}>{dismiss=><><h2>杠杆</h2><p className="muted sheet-intro">{small(symbol)}USDT · 当前{mode}规则</p><div className="budget leverage-budget"><div className="slider-heading"><label htmlFor="leverage-slider">调整倍数</label><output htmlFor="leverage-slider">{draftLeverage}<small>x</small></output></div><input id="leverage-slider" aria-label="杠杆倍数" type="range" min="1" max={cap} step="1" value={draftLeverage} onChange={e=>{setDraftLeverage(Number(e.target.value));feedback('tick')}} style={{'--fill':`${cap>1?(draftLeverage-1)/(cap-1)*100:0}%`} as CSSProperties}/><div className="slider-scale"><span>1x</span><span>{cap}x</span></div><div className="leverage-stepper"><button aria-label="杠杆减一" disabled={draftLeverage<=1} onClick={()=>{setDraftLeverage(v=>Math.max(1,v-1));feedback('tick')}}>−</button><span>可逐倍微调</span><button aria-label="杠杆加一" disabled={draftLeverage>=cap} onClick={()=>{setDraftLeverage(v=>Math.min(cap,v+1));feedback('tick')}}>＋</button></div></div><p className="micro">杠杆影响保证金，仓位仍由止损风险与当前规则核定。{p?.margin_mode==='isolated'?'逐仓模式下，较远的止损可能需要降低杠杆。':''}</p><button className="primary" onClick={()=>{setLeverage(Math.min(cap,draftLeverage));dismiss();feedback('light')}}>确认 {Math.min(cap,draftLeverage)}x</button></>}</Dialog>}
  {!error&&!preview&&!closeTarget&&!sheet&&message&&<Notification notice={message} onDismiss={()=>setMessage(null)}/>}
  {error&&<Dialog key="operation-error" label={errorDetail.title} variant="alert" busy={false} onDismiss={dismissError}>{dismiss=><><span className="alert-icon"><Icon name="shield" size={25}/></span><h2>{uncertain&&preview?'请求结果尚未确认':errorDetail.title}</h2><p className="alert-detail">{errorDetail.detail}</p>{uncertain&&preview&&<p className="muted">服务端可能已经受理。返回确认页后可使用同一请求编号重试，或查看持仓核对。</p>}<button className="primary" onClick={dismiss}>{preview?'返回订单确认':closeTarget?'返回平仓确认':errorDetail.field||errorDetail.rules?'返回修改':'知道了'}<Icon name="arrow" size={17}/></button>{errorDetail.rules&&!preview&&!closeTarget&&<button className="plain" onClick={()=>nav('纪律')}>查看当前规则</button>}</>}</Dialog>}
 </div>;
}
