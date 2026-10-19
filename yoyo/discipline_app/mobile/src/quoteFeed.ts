/** Public display-only ticker feed. Each connection owns its callbacks and timers. */
import type {Quote} from './api';
export type LiveQuote=Quote&{received:number};
export type QuoteSnapshot=Record<string,LiveQuote>;
type Socket=Pick<WebSocket,'onopen'|'onmessage'|'onerror'|'onclose'|'readyState'|'send'|'close'>;
export function createQuoteFeed(symbols:string[],publish:(value:QuoteSnapshot)=>void,connect:()=>Socket=()=>new WebSocket('wss://ws.okx.com/ws/v5/public'),now=Date.now){
 const selected=new Set(symbols);
 let socket:Socket|null=null,active=false,retry=0,quotes:QuoteSnapshot={},pending:QuoteSnapshot={};
 let reconnect:ReturnType<typeof setTimeout>|undefined,flush:ReturnType<typeof setTimeout>|undefined,heartbeat:ReturnType<typeof setInterval>|undefined,opening:ReturnType<typeof setTimeout>|undefined;
 const clearSocket=()=>{
  clearTimeout(flush);clearTimeout(opening);clearInterval(heartbeat);flush=undefined;pending={};
  const previous=socket;socket=null;
  if(previous){previous.onopen=previous.onmessage=previous.onclose=previous.onerror=null;try{previous.close()}catch{/* A failed public socket cannot affect the account. */}}
 };
 const clearQuotes=()=>{if(Object.keys(quotes).length){quotes={};publish(quotes)}};
 const failed=()=>{clearSocket();clearQuotes();if(active)reconnect=setTimeout(open,Math.min(15000,1000*2**retry++))};
 function open(){
  if(!active||socket)return;
  let current:Socket;
  try{current=connect();socket=current}catch{failed();return}
  let lastMessage=now(),lastPing=now();
  const owns=()=>active&&socket===current;
  opening=setTimeout(()=>{if(owns())failed()},8000);
  current.onopen=()=>{
   if(!owns())return;clearTimeout(opening);lastMessage=now();
   try{current.send(JSON.stringify({op:'subscribe',args:[...selected].map(instId=>({channel:'tickers',instId}))}))}catch{failed();return}
   heartbeat=setInterval(()=>{
    if(!owns())return;
    const fresh=Object.fromEntries(Object.entries(quotes).filter(([,q])=>now()-q.received<10000));
    if(Object.keys(fresh).length!==Object.keys(quotes).length){quotes=fresh;publish(quotes)}
    if(now()-lastMessage>12000){failed();return}
    try{if(current.readyState===1&&now()-lastPing>=5000){current.send('ping');lastPing=now()}}catch{failed()}
   },2000);
  };
  current.onmessage=event=>{
   if(!owns())return;lastMessage=now();if(event.data==='pong')return;
   try{
    const message=JSON.parse(event.data);if(!Array.isArray(message.data))return;
    for(const row of message.data){
     if(!selected.has(row.instId))continue;
     const last=Number(row.last),bid=Number(row.bidPx),ask=Number(row.askPx),time=Number(row.ts)/1000;
     if(![last,bid,ask,time].every(Number.isFinite)||Math.min(last,bid,ask)<=0||bid>ask||Math.abs(now()/1000-time)>15)continue;
     const prior=pending[row.instId]??quotes[row.instId];if(prior&&time<=prior.time)continue;
     retry=0;pending[row.instId]={last:row.last,bid:row.bidPx,ask:row.askPx,time,source:'OKX public WebSocket',received:now(),today_change:Number(row.sodUtc8)>0?String((last/Number(row.sodUtc8)-1)*100):null};
    }
    if(Object.keys(pending).length&&!flush)flush=setTimeout(()=>{
     flush=undefined;if(!owns())return;quotes={...quotes,...pending};pending={};publish(quotes);
    },100);
   }catch{/* Malformed public data never changes display. */}
  };
  current.onerror=current.onclose=()=>{if(owns())failed()};
 }
 return {
  start(){if(active||!selected.size)return;active=true;open()},
  stop(){active=false;clearTimeout(reconnect);clearSocket();clearQuotes()},
 };
}
