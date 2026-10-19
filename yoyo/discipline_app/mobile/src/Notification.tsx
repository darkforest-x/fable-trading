/** Action feedback stays outside page flow; dismissal never cancels reconciliation.
 * Design: Stitch 8c65927db3dc44899d78baa7e409be05.
 */
import {Dialog,Spinner} from './Dialog';
import {Icon} from './Icon';
import {useCallback,useEffect,useRef,useState} from 'react';
import {feedback} from './feedback';

export type Notice={text:string;kind:'info'|'success'|'progress';presentation?:'dialog'|'toast'};
export function Notification({notice,onDismiss}:{notice:Notice;onDismiss:()=>void}){
 const dismissRef=useRef(onDismiss);dismissRef.current=onDismiss;
 const latest=useRef(notice);latest.current=notice;
 const [closing,setClosing]=useState(false);
 const exitTimer=useRef<ReturnType<typeof setTimeout>|null>(null);
 const closeToast=useCallback(()=>{
  if(exitTimer.current!==null)return;
  if(matchMedia('(prefers-reduced-motion: reduce)').matches){dismissRef.current();return}
  const current=latest.current;setClosing(true);
  exitTimer.current=setTimeout(()=>{exitTimer.current=null;if(latest.current===current)dismissRef.current()},140);
 },[]);
 useEffect(()=>{
  setClosing(false);if(notice.presentation!=='toast')return;
  const timer=setTimeout(closeToast,3500);
  return()=>{clearTimeout(timer);if(exitTimer.current!==null)clearTimeout(exitTimer.current);exitTimer.current=null};
 },[notice,closeToast]);
 if(notice.presentation==='toast')return <aside className={`status-toast ${closing?'toast-closing':''}`} aria-label="操作提示"><Icon name="check" size={18}/><span role="status" aria-live="polite">{notice.text}</span><button type="button" aria-label="关闭提示" onClick={()=>{feedback('light');closeToast()}}><Icon name="close" size={17}/></button></aside>;
 const title=notice.kind==='success'?'操作成功':notice.kind==='progress'?'正在核对':'操作提示';
 return <Dialog label={title} variant="notice" busy={false} onDismiss={onDismiss}>{dismiss=><>
  <span className="alert-icon notice-icon">{notice.kind==='progress'?<Spinner/>:<Icon name={notice.kind==='success'?'check':'shield'} size={25}/>}</span>
  <h2>{title}</h2><p className="alert-detail" role="status" aria-live="polite" aria-atomic="true">{notice.text}</p>
  <button className="primary" onClick={dismiss}>知道了</button>
 </>}</Dialog>;
}
