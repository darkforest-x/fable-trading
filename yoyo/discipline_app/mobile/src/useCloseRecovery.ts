/** Restore an immutable close identity before polling; never send on restoration. */
import {useEffect,useRef,useState} from 'react';
import {api,type Connection} from './api';
import {parseCloseAttempt,type CloseAttempt} from './tradeExperience';
export function useCloseRecovery(connection:Connection|null,onSettled:(status:string)=>void){
 const scope=connection?`spike-close:${connection.cacheScope}:${connection.mode}:${connection.accountId??'primary'}`:'';
 const [record,setRecord]=useState<{scope:string;attempt:CloseAttempt}|null>(null);
 const attempt=record?.scope===scope?record.attempt:null;
 const [status,setStatus]=useState('checking'),[nonce,setNonce]=useState(0);
 const settled=useRef(onSettled);settled.current=onSettled;
 useEffect(()=>{if(!scope){setRecord(null);return}let saved:CloseAttempt|null=null;try{saved=parseCloseAttempt(localStorage.getItem(scope))}catch{/* Unavailable local storage never triggers a write. */}setRecord(saved?{scope,attempt:saved}:null);setStatus('checking')},[scope]);
 useEffect(()=>{
  if(!connection||!attempt)return;
  let active=true,missing=0;let timer:ReturnType<typeof setTimeout>;
  const poll=async()=>{
   if(!document.hidden)try{
    let value=await api<{status:string}>(connection,`/v1/close-requests/${attempt.id}`);
    if(value.status==='not_found'){missing++;if(missing>=6)value=await api(connection,`/v1/close-requests/${attempt.id}/abandon-unreceived`,{})}else missing=0;
    if(!active)return;
    if(['filled','cancelled','rejected','not_received'].includes(value.status)){
     localStorage.removeItem(scope);setRecord(null);setStatus(value.status);settled.current(value.status);return;
    }
    setStatus(value.status);
   }catch{if(active)setStatus('checking')}
   if(active)timer=setTimeout(poll,3000);
  };
  void poll();return()=>{active=false;clearTimeout(timer)};
 },[connection,attempt?.id,nonce,scope]);
 function begin(value:CloseAttempt){localStorage.setItem(scope,JSON.stringify(value));setRecord({scope,attempt:value});setStatus('checking')}
 return {attempt,status,begin,check:()=>setNonce(v=>v+1)};
}
