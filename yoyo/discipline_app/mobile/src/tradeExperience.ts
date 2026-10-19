/** Account-scoped recovery and local-only catalogue preferences. No credentials. */
export type CloseAttempt={id:string;position:string;symbol:string;percent:number;created:number};
export function parseCloseAttempt(raw:string|null):CloseAttempt|null{
 try{const v=JSON.parse(raw??'null');return v&&/^[a-zA-Z0-9]{16,80}$/.test(v.id)&&typeof v.position==='string'&&v.position.length>0&&typeof v.symbol==='string'&&Number.isInteger(v.percent)&&v.percent>=1&&v.percent<=100&&Number.isFinite(v.created)?v:null}catch{return null}
}
export function parseFavorites(raw:string|null):string[]{
 try{const values=JSON.parse(raw??'[]');return Array.isArray(values)?[...new Set(values.filter((s):s is string=>typeof s==='string'&&/^[A-Z0-9]+-USDT-SWAP$/.test(s)))].slice(0,500):[]}catch{return []}
}
export function countdown(seconds:number){
 const s=Math.max(0,Math.ceil(seconds));return [Math.floor(s/3600),Math.floor(s/60)%60,s%60].map(v=>String(v).padStart(2,'0')).join(':');
}
