/** Public display stream. Never supplies the server's execution/authorization state. */
import {useEffect,useState} from 'react';
import type {Quote} from './api';
type LiveQuote=Quote&{received:number};
export function useLiveQuotes(symbols:string[],enabled:boolean){
 const key=[...new Set(symbols)].sort().join(',');
 const [quotes,setQuotes]=useState<Record<string,LiveQuote>>({});
 useEffect(()=>{
  setQuotes({});if(!enabled||!key)return;
  let socket:WebSocket|null=null,disposed=false,retry=0;
  let reconnect:ReturnType<typeof setTimeout>,flush:ReturnType<typeof setTimeout>|undefined,heartbeat:ReturnType<typeof setInterval>;
  let pending:Record<string,LiveQuote>={},lastMessage=0;
  const stop=()=>{clearTimeout(reconnect);clearTimeout(flush);flush=undefined;clearInterval(heartbeat);if(socket){socket.onclose=null;socket.close();socket=null}pending={};setQuotes({})};
  const start=()=>{
   if(disposed||document.hidden||socket)return;
   socket=new WebSocket('wss://ws.okx.com/ws/v5/public');const current=socket;
   current.onopen=()=>{retry=0;lastMessage=Date.now();current.send(JSON.stringify({op:'subscribe',args:key.split(',').map(instId=>({channel:'tickers',instId}))}));heartbeat=setInterval(()=>{if(Date.now()-lastMessage>12000){setQuotes({});current.close()}else if(current.readyState===WebSocket.OPEN)current.send('ping')},5000)};
   current.onmessage=event=>{lastMessage=Date.now();if(event.data==='pong')return;try{const message=JSON.parse(event.data);for(const row of message.data??[]){if(!key.split(',').includes(row.instId))continue;const last=Number(row.last),bid=Number(row.bidPx),ask=Number(row.askPx),time=Number(row.ts)/1000;if(![last,bid,ask,time].every(Number.isFinite)||Math.min(last,bid,ask)<=0||bid>ask||Math.abs(Date.now()/1000-time)>15)continue;pending[row.instId]={last:row.last,bid:row.bidPx,ask:row.askPx,time,source:'OKX public WebSocket',received:Date.now(),today_change:Number(row.sodUtc8)>0?String((last/Number(row.sodUtc8)-1)*100):null};}if(!flush)flush=setTimeout(()=>{setQuotes(previous=>({...previous,...pending}));pending={};flush=undefined},100)}catch{/* Malformed public data never changes display. */}};
   current.onerror=()=>current.close();
   current.onclose=()=>{clearInterval(heartbeat);clearTimeout(flush);flush=undefined;pending={};socket=null;setQuotes({});if(!disposed&&!document.hidden)reconnect=setTimeout(start,Math.min(15000,1000*2**retry++))};
  };
  const visibility=()=>{stop();if(!document.hidden)start()};document.addEventListener('visibilitychange',visibility);start();
  return()=>{disposed=true;stop();document.removeEventListener('visibilitychange',visibility)};
 },[key,enabled]);
 return Object.fromEntries(Object.entries(quotes).filter(([,q])=>Date.now()-q.received<10000));
}
