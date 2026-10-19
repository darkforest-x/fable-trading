/** Bounded, persistent display cache. Never used for order/risk decisions. */
type StoragePort=Pick<Storage,'getItem'|'setItem'|'removeItem'|'key'|'length'>;
type Entry<T>={savedAt:number;data:T};
export class DisplayCache {
 private memory=new Map<string,Entry<unknown>>();
 private flights=new Map<string,Promise<unknown>>();
 private generation=0;
 private storage?:StoragePort;
 private now:()=>number;
 private prefix:string;
 private maxAge:number;
 constructor(storage?:StoragePort,now=Date.now,prefix='spike-display-v1:',maxAge=86400000){this.storage=storage;this.now=now;this.prefix=prefix;this.maxAge=maxAge}
 peek<T>(key:string):Entry<T>|undefined{
  let entry=this.memory.get(key) as Entry<T>|undefined;
  if(!entry){try{const raw=this.storage?.getItem(this.prefix+key);if(raw)entry=JSON.parse(raw)}catch{/* Storage may be unavailable. */}}
  if(!entry||!Number.isFinite(entry.savedAt)||this.now()<entry.savedAt||this.now()-entry.savedAt>this.maxAge){this.memory.delete(key);try{this.storage?.removeItem(this.prefix+key)}catch{}return}
  this.memory.set(key,entry);return entry;
 }
 async load<T>(key:string,loader:()=>Promise<T>,freshMs=10000):Promise<T>{
  const entry=this.peek<T>(key);
  if(entry&&this.now()-entry.savedAt<freshMs)return entry.data;
  const existing=this.flights.get(key);if(existing)return existing as Promise<T>;
  const generation=this.generation;
  const flight=loader().then(data=>{
   if(generation===this.generation){
    const saved={savedAt:this.now(),data};this.memory.set(key,saved);
    try{this.storage?.setItem(this.prefix+key,JSON.stringify(saved));this.prune()}catch{/* Quota/full/private mode retains session memory. */}
   }
   return data;
  }).finally(()=>{if(this.flights.get(key)===flight)this.flights.delete(key)});
  this.flights.set(key,flight);return flight;
 }
 private prune(){
  const entries=[...this.memory.entries()].sort((a,b)=>b[1].savedAt-a[1].savedAt);
  for(const [key] of entries.slice(24)){this.memory.delete(key);this.storage?.removeItem(this.prefix+key)}
  if(this.storage){
   const persisted:{key:string;savedAt:number}[]=[];
   for(let i=0;i<this.storage.length;i++){const key=this.storage.key(i);if(key?.startsWith(this.prefix)){try{persisted.push({key,savedAt:JSON.parse(this.storage.getItem(key)!).savedAt})}catch{persisted.push({key,savedAt:0})}}}
   persisted.sort((a,b)=>b.savedAt-a.savedAt).forEach((item,i)=>{if(i>=24||this.now()-item.savedAt>this.maxAge||item.savedAt>this.now()||!Number.isFinite(item.savedAt))this.storage?.removeItem(item.key)});
  }
 }
 clear(){
  this.generation++;this.memory.clear();this.flights.clear();
  try{if(this.storage)for(let i=this.storage.length-1;i>=0;i--){const key=this.storage.key(i);if(key?.startsWith(this.prefix))this.storage.removeItem(key)}}catch{}
 }
}
