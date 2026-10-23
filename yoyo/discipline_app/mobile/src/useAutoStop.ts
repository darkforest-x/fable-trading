/** Wick-first defaults for 1.618; other tickets retain reverse-R calculation. */
import {useEffect,useRef,useState} from 'react';
import {api,type Connection,type Ticket} from './api';
import {feedback} from './feedback';
import type {CandlePeriod} from './candleData';
export function useAutoStop({connection,key,ticket,enabled,period,onApply,onError}:{connection:Connection|null;key:string;ticket:Ticket;enabled:boolean;period?:CandlePeriod;onApply:(value:string,side:'long'|'short')=>void;onError:(message:string)=>void}){
 const [mode,setMode]=useState<'long'|'short'|null>(null),[working,setWorking]=useState(false),[calculated,setCalculated]=useState(''),[revision,setRevision]=useState(0);
 const current=useRef({key,ticket,onApply,onError});current.current={key,ticket,onApply,onError};
 const sequence=useRef(0);
 useEffect(()=>{
  const seq=++sequence.current;
  if(!mode||!connection||!enabled){setWorking(false);return}
  setWorking(true);
  const controller=new AbortController();
  const timer=setTimeout(async()=>{
   const signal=AbortSignal.any([controller.signal,AbortSignal.timeout(12000)]);
   try{
    const stop=period
     ?(await api<{stop_loss:string}>(connection,`/v1/market/monday-wick?${new URLSearchParams({symbol:current.current.ticket.symbol,period,side:mode})}`,undefined,signal)).stop_loss
     :(await api<{stop:string}>(connection,'/v1/auto-stop',{...current.current.ticket,side:mode,stop:''},signal)).stop;
    if(typeof stop!=='string'||!Number.isFinite(Number(stop))||Number(stop)<=0)throw Error('插针止损暂不可用，请重试或手动填写');
    if(sequence.current===seq&&current.current.key===key){setCalculated(`${key}:${mode}`);current.current.onApply(stop,mode);feedback('success')}
   }catch(e){if(sequence.current===seq&&!controller.signal.aborted){setCalculated('');current.current.onError(signal.aborted?'止损读取超时，请重试或手动填写':(e as Error).message)}}
   finally{if(sequence.current===seq)setWorking(false)}
  },350);
  return()=>{clearTimeout(timer);controller.abort();sequence.current++};
 },[connection,key,mode,enabled,period,revision]);
 function manual(){sequence.current++;setMode(null);setWorking(false);setCalculated('')}
 function select(side:'long'|'short'){sequence.current++;setCalculated('');setWorking(true);setMode(side);setRevision(v=>v+1);feedback('light')}
 return {mode,working,ready:!mode||enabled&&calculated===`${key}:${mode}`,manual,select};
}
