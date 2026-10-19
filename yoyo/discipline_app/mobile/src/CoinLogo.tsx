/** Public OKX artwork ships inside the native app; fallback is always visible. */
import {memo,useState} from 'react';
const images=import.meta.glob<string>('./assets/coins/*.{png,jpg,webp,gif}',{eager:true,query:'?url',import:'default'});
const bundled=Object.fromEntries(Object.entries(images).map(([path,url])=>[path.split('/').at(-1)!.split('.')[0],url]));
export const CoinLogo=memo(function CoinLogo({coin}:{coin:string}){
 const [loaded,setLoaded]=useState(''),[failed,setFailed]=useState('');
 const source=bundled[coin.toLowerCase()];
 return <span className="coin-logo" aria-hidden="true"><span>{coin.slice(0,2)}</span>{source&&failed!==source&&<img key={source} src={source} alt="" decoding="async" style={{opacity:loaded===source?1:0}} onLoad={()=>setLoaded(source)} onError={()=>setFailed(source)}/>}</span>;
});
