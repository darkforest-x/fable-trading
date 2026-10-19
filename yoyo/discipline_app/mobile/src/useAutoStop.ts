/** Debounced reverse-R calculation; stale replies cannot overwrite manual edits. */
import {useEffect,useRef,useState} from 'react';
import {api,type Connection,type Ticket} from './api';
import {feedback} from './feedback';
export function useAutoStop({connection,key,ticket,enabled,onApply,onError}:{connection:Connection|null;key:string;ticket:Ticket;enabled:boolean;onApply:(value:string,side:'long'|'short')=>void;onError:(message:string)=>void}){
 const [mode,setMode]=useState<'long'|'short'|null>(null),[working,setWorking]=useState(false),[calculated,setCalculated]=useState(''),[revision,setRevision]=useState(0);
 const current=useRef({key,ticket,onApply,onError});current.current={key,ticket,onApply,onError};
 const sequence=useRef(0);
 useEffect(()=>{
  const seq=++sequence.current;
  if(!mode||!connection||!enabled){setWorking(false);return}
  setWorking(true);
  const controller=new AbortController();
  const timer=setTimeout(async()=>{
   try{const result=await api<{stop:string}>(connection,'/v1/auto-stop',{...current.current.ticket,side:mode,stop:''},controller.signal);
    if(sequence.current===seq&&current.current.key===key){setCalculated(`${key}:${mode}`);current.current.onApply(result.stop,mode);feedback('success')}
   }catch(e){if(sequence.current===seq&&!controller.signal.aborted){setCalculated('');current.current.onError((e as Error).message)}}
   finally{if(sequence.current===seq)setWorking(false)}
  },350);
  return()=>{clearTimeout(timer);controller.abort();sequence.current++};
 },[connection,key,mode,enabled,revision]);
 function manual(){sequence.current++;setMode(null);setWorking(false);setCalculated('')}
 function select(side:'long'|'short'){sequence.current++;setCalculated('');setWorking(true);setMode(side);setRevision(v=>v+1);feedback('light')}
 return {mode,working,ready:!mode||enabled&&calculated===`${key}:${mode}`,manual,select};
}
