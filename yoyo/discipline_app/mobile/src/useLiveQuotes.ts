/** Public quotes stay out of execution state; hidden pages release the entire feed. */
import {useEffect,useState} from 'react';
import {createQuoteFeed,type QuoteSnapshot} from './quoteFeed';
const EMPTY:QuoteSnapshot={};
export function useLiveQuotes(symbols:string[],enabled:boolean){
 const key=[...new Set(symbols)].sort().join(',');
 const [snapshot,setSnapshot]=useState<{key:string;quotes:QuoteSnapshot}>({key:'',quotes:EMPTY});
 useEffect(()=>{
  setSnapshot({key,quotes:EMPTY});if(!enabled||!key)return;
  let mounted=true;
  const feed=createQuoteFeed(key.split(','),quotes=>{if(mounted)setSnapshot({key,quotes})});
  const visibility=()=>{if(document.hidden)feed.stop();else feed.start()};
  document.addEventListener('visibilitychange',visibility);visibility();
  return()=>{mounted=false;feed.stop();document.removeEventListener('visibilitychange',visibility)};
 },[key,enabled]);
 return enabled&&snapshot.key===key?snapshot.quotes:EMPTY;
}
