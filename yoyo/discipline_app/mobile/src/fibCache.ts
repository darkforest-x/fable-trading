/** Public display cache only. Never persists credentials, balances or order decisions.
 * A NY calendar-week boundary invalidates old anchors even when the phone is offline.
 */
import {fibSelection,parseFibSnapshot,type FibSnapshot} from './fibData.ts';
import type {CandlePeriod} from './candleData.ts';
type Disk=Pick<Storage,'getItem'|'setItem'>;
type Saved={data:FibSnapshot;symbol:string;period:CandlePeriod};
const periods=['5m','15m','1H','4H'];
const nyDate=new Intl.DateTimeFormat('en-CA',{timeZone:'America/New_York',year:'numeric',month:'2-digit',day:'2-digit'});
export function currentNYWeek(now:number){
 const parts=nyDate.formatToParts(now),part=(name:string)=>Number(parts.find(p=>p.type===name)?.value);
 const date=new Date(Date.UTC(part('year'),part('month')-1,part('day')));
 date.setUTCDate(date.getUTCDate()-(date.getUTCDay()+6)%7);
 return date.toISOString().slice(0,10);
}
export function currentFibWeek(data:FibSnapshot,now=Date.now()){
 return data.week===currentNYWeek(now)&&Number.isFinite(data.week_start_ms)&&Number.isFinite(data.next_week_ms)
  &&now>=data.week_start_ms&&now<data.next_week_ms;
}
export class FibCache{
 private key:string;
 private disk:Disk|undefined;
 private now:()=>number;
 constructor(disk:Disk|undefined,origin:string,now=Date.now){this.disk=disk;this.now=now;this.key='spike-fib-display-v1:'+origin}
 read():Saved|null{
  try{
   const raw=this.disk?.getItem(this.key);if(!raw||raw.length>500000)return null;
   const saved=JSON.parse(raw),data=parseFibSnapshot(saved.data);
   if(!Number.isFinite(saved.at)||saved.at>this.now()||!currentFibWeek(data,this.now())||data.now_ms>this.now()||!periods.includes(saved.period))return null;
   return {data:{...data,connected:false,last_message_ms:null},symbol:fibSelection(saved.symbol,data.week,data),period:saved.period};
  }catch{return null}
 }
 write(data:FibSnapshot,symbol:string,period:CandlePeriod){
  if(!this.disk||!currentFibWeek(data,this.now())||!periods.includes(period))return;
  try{this.disk.setItem(this.key,JSON.stringify({at:this.now(),data,symbol,period}))}catch{/* Storage denial/full never blocks chart or refresh. */}
 }
}
export function browserFibCache(origin:string){let disk:Storage|undefined;try{disk=localStorage}catch{}return new FibCache(disk,origin)}
