/** In-app alert banner (Stitch alarm-notify, iphone-glass-1.0.0). Alarms stay until dismissed;
 * 1.618 pushes fade after 7 s. Display only: “查看” navigates, it never opens an order ticket. */
import {useEffect} from 'react';
import {Icon} from './Icon';
import {clockText,coin,edgeText,type NotificationEvent} from './monitorFormat';

export function AlarmBanner({event,now,onView,onDismiss}:{event:NotificationEvent;now:number;onView:()=>void;onDismiss:()=>void}){
 const alarm=event.kind==='alarm';
 useEffect(()=>{if(alarm)return;const timer=setTimeout(onDismiss,7000);return()=>clearTimeout(timer)},[event.id]);
 return <div className={`alarm-banner ${alarm?'is-alarm':'is-fib'} ${event.side}`} role={alarm?'alertdialog':'status'} aria-live="assertive" aria-label={alarm?'全市场插针报警':'1.618 触价提醒'}>
  <span className="alarm-banner-icon"><Icon name={alarm?'bell':'fib'} size={20}/></span>
  <span className="alarm-banner-text">
   <b>{alarm?<>{event.test?'测试报警 · ':''}全市场插针 · {edgeText(event.side)}</>:<>1.618 触价 · {coin(event.symbol??'')} {event.side==='upper'?'上沿':'下沿'}</>}</b>
   <small>{alarm?`${event.n} 个币触及 · ${clockText(event.at_ms,now)}`:`已推送 · ${clockText(event.at_ms,now)}`}</small>
  </span>
  <span className="alarm-banner-actions"><button className="view" onClick={onView}>查看</button>{alarm?<button onClick={onDismiss}>知道了</button>:<button aria-label="关闭提醒" className="close" onClick={onDismiss}><Icon name="close" size={15}/></button>}</span>
 </div>;
}
