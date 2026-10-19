/** Action feedback stays outside page flow; dismissal never cancels reconciliation.
 * Design: Stitch 8c65927db3dc44899d78baa7e409be05.
 */
import {Dialog,Spinner} from './Dialog';
import {Icon} from './Icon';
import {useEffect,useRef} from 'react';
import {feedback} from './feedback';

export type Notice={text:string;kind:'info'|'success'|'progress';presentation?:'dialog'|'toast'};
export function Notification({notice,onDismiss}:{notice:Notice;onDismiss:()=>void}){
 const dismissRef=useRef(onDismiss);dismissRef.current=onDismiss;
 useEffect(()=>{if(notice.presentation!=='toast')return;const timer=setTimeout(()=>dismissRef.current(),5000);return()=>clearTimeout(timer)},[notice]);
 if(notice.presentation==='toast')return <aside className="status-toast" aria-label="操作提示"><Icon name="check" size={18}/><span role="status" aria-live="polite">{notice.text}</span><button type="button" aria-label="关闭提示" onClick={()=>{feedback('light');onDismiss()}}><Icon name="close" size={17}/></button></aside>;
 const title=notice.kind==='success'?'操作成功':notice.kind==='progress'?'正在核对':'操作提示';
 return <Dialog label={title} variant="notice" busy={false} onDismiss={onDismiss}>{dismiss=><>
  <span className="alert-icon notice-icon">{notice.kind==='progress'?<Spinner/>:<Icon name={notice.kind==='success'?'check':'shield'} size={25}/>}</span>
  <h2>{title}</h2><p className="alert-detail" role="status" aria-live="polite" aria-atomic="true">{notice.text}</p>
  <button className="primary" onClick={dismiss}>知道了</button>
 </>}</Dialog>;
}
