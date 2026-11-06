/** Foreground notification watcher (owner 2026-11-06). Polls the read-only feed every 8 s while the app
 * is visible and connected, on any tab; raises one in-app alert per new alarm / fresh 1.618 push.
 * Seen ids persist per authorization so reopening the app never replays old alarms. */
import {useCallback,useEffect,useRef,useState} from 'react';
import type {Connection} from './api';
import {MONITOR_POLL_MS,monitorGet} from './monitorData';
import {freshAlerts,type AlertPrefs,type NotificationEvent,type NotificationSnapshot} from './monitorFormat';

const SEEN_LIMIT=300;
const keyFor=(c:Connection,name:string)=>`spike-notify-${name}:${c.cacheScope??'uncached'}`;

export function useNotifications(connection:Connection|null,enabled:boolean,prefs:AlertPrefs,onAlert:(event:NotificationEvent)=>void){
 const [data,setData]=useState<NotificationSnapshot|null>(null),[error,setError]=useState('');
 const [openedAt,setOpenedAt]=useState(0);
 const seen=useRef<Set<string>|null>(null),latest=useRef({prefs,onAlert});latest.current={prefs,onAlert};
 useEffect(()=>{
  setData(null);setError('');seen.current=null;setOpenedAt(0);
  if(!connection)return;
  try{const raw=localStorage.getItem(keyFor(connection,'seen'));if(raw)seen.current=new Set(JSON.parse(raw));setOpenedAt(Number(localStorage.getItem(keyFor(connection,'opened')))||0)}catch{seen.current=null}
 },[connection?.cacheScope]);
 const load=useCallback(async(signal?:AbortSignal)=>{
  if(!connection)return;
  try{
   const next=await monitorGet<NotificationSnapshot>(connection,'notifications',{},signal);
   const first=!seen.current,result=freshAlerts(next.events,seen.current,next.now_ms,latest.current.prefs);
   seen.current=result.seen;
   // First contact seeds history as read, so the badge only counts what arrives from now on.
   if(first){setOpenedAt(next.now_ms);try{localStorage.setItem(keyFor(connection,'opened'),String(next.now_ms))}catch{}}
   try{localStorage.setItem(keyFor(connection,'seen'),JSON.stringify([...result.seen].slice(-SEEN_LIMIT)))}catch{}
   setData(next);setError('');
   for(const event of result.alerts)latest.current.onAlert(event);
  }catch(e){if(!signal?.aborted)setError((e as Error).message||'通知暂不可用')}
 },[connection]);
 useEffect(()=>{
  if(!enabled||!connection)return;
  const controller=new AbortController();let timer:ReturnType<typeof setTimeout>;
  const tick=async()=>{if(document.hidden)return;await load(controller.signal);if(!controller.signal.aborted)timer=setTimeout(tick,MONITOR_POLL_MS.notifications)};
  const visibility=()=>{clearTimeout(timer);if(!document.hidden)void tick()};
  document.addEventListener('visibilitychange',visibility);void tick();
  return()=>{controller.abort();clearTimeout(timer);document.removeEventListener('visibilitychange',visibility)};
 },[enabled,connection,load]);
 const markRead=useCallback(()=>{
  const now=data?.now_ms??Date.now();setOpenedAt(now);
  if(connection)try{localStorage.setItem(keyFor(connection,'opened'),String(now))}catch{}
 },[connection,data?.now_ms]);
 const unread=data?.events.filter(e=>e.pushed&&(e.at_ms??0)>openedAt).length??0;
 return {data,setData,error,unread,markRead,reload:load};
}
