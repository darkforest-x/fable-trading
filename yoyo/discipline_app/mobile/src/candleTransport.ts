/** Chart-only transport. Native HTTP avoids WebView CORS; the authenticated
 * service relays public OKX history. Credentials never follow the public fallback.
 * Native requests have finite timeouts and late results are fenced after abort.
 */
import {fetchCandles,type FetchCandles} from './candleData.ts';
type Connection={url:string;token:string;accountId?:string};
type NativeRequest=(options:{url:string;method:'GET';headers:Record<string,string>;connectTimeout:number;readTimeout:number;disableRedirects:boolean;responseType:'json'})=>Promise<{status:number;data:unknown}>;
type Options={connection?:Connection|null;native?:NativeRequest;origin?:string};

export function candleFetcher(options:Options):FetchCandles {
 async function request(url:URL,signal:AbortSignal,headers:Record<string,string>={}) {
  signal.throwIfAborted();
  const controller=new AbortController();
  let rejectAbort:(reason:Error)=>void=()=>{};
  const aborted=new Promise<never>((_,reject)=>{rejectAbort=reject});
  const abort=()=>{controller.abort();rejectAbort(new DOMException('请求已取消','AbortError'))};
  signal.addEventListener('abort',abort,{once:true});
  const timer=setTimeout(abort,10000);
  try {
   const pending=options.native
    ? options.native({url:String(url),method:'GET',headers,connectTimeout:5000,readTimeout:9000,disableRedirects:true,responseType:'json'})
    : fetch(url,{signal:controller.signal,headers,credentials:'omit',cache:'no-store',redirect:'error'}).then(async r=>({status:r.status,data:await r.json()}));
   const response=await Promise.race([pending,aborted]);
   signal.throwIfAborted();
   if(response.status<200||response.status>=300)throw Error('历史K线请求失败');
   return typeof response.data==='string'?JSON.parse(response.data):response.data;
  } finally {clearTimeout(timer);signal.removeEventListener('abort',abort)}
 }
 return (symbol,period,query)=>fetchCandles(symbol,period,query,options.origin,async(publicURL,signal)=>{
  const connection=options.connection;
  if(connection){
   const relay=new URL(connection.url+'/v1/market/candles');
   relay.search=new URLSearchParams({symbol,period,...(query.before?{before:String(query.before)}:{})}).toString();
   try{return await request(relay,signal,{Authorization:'Bearer '+connection.token,'X-Spike-Account':connection.accountId??'primary'})}
   catch{signal.throwIfAborted()}
  }
  return request(publicURL,signal);
 });
}
