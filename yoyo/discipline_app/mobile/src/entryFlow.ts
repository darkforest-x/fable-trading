/** Draft helpers: exact price ticks and truthful asynchronous execution states. */
export type EntryIntent={id:string;symbol:string;side?:'long'|'short';kind?:'market'|'limit'};
export type EntryProgress=EntryIntent&{status:string};
export const entryResolved=(status:string)=>['pending','partially_filled','filled','closed','rejected','cancelled','interrupted'].includes(status);
export const entryPollDelay=(status:string)=>status==='not_found'?3000:1000;

// Quotes are decimal strings. Avoid floating-point rounding for tiny tick sizes.
export function priceAtTick(value:string|undefined,tick:string|undefined):string|null{
 const parse=(raw:string|undefined)=>{
  if(!raw||raw.length>32||!/^\d*\.?\d+$/.test(raw))return null;
  const [whole,part='']=raw.split('.');
  return {units:BigInt((whole||'0')+part),scale:part.length};
 };
 const price=parse(value),step=parse(tick);
 if(!price||!step||price.units<=0n||step.units<=0n)return null;
 const scale=Math.max(price.scale,step.scale),p=price.units*10n**BigInt(scale-price.scale),t=step.units*10n**BigInt(scale-step.scale);
 const rounded=((p+t/2n)/t)*t;
 if(rounded<=0n)return null;
 const digits=rounded.toString().padStart(scale+1,'0');
 return scale?`${digits.slice(0,-scale)}.${digits.slice(-scale)}`.replace(/\.?0+$/,''):digits;
}

export function parseEntryIntent(raw:string|null):EntryIntent|null{
 try{
  const x=JSON.parse(raw??'null');
  if(!x||typeof x.id!=='string'||!/^[a-zA-Z0-9]{16,80}$/.test(x.id)||typeof x.symbol!=='string'||!/^[A-Z0-9]{1,40}-USDT-SWAP$/.test(x.symbol))return null;
  return {id:x.id,symbol:x.symbol,...(['long','short'].includes(x.side)?{side:x.side}:{}),...(['market','limit'].includes(x.kind)?{kind:x.kind}:{})};
 }catch{return null}
}
