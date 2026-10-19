/** Account activity uses exchange/book timestamps in seconds, always Beijing time. */
const beijing = new Intl.DateTimeFormat('sv-SE', {
 timeZone:'Asia/Shanghai', year:'numeric', month:'2-digit', day:'2-digit',
 hour:'2-digit', minute:'2-digit', second:'2-digit', hourCycle:'h23',
});
export function activityTime(seconds?:number){
 if(!seconds||!Number.isFinite(seconds)||seconds<0)return '—';
 const date=new Date(seconds*1000);
 return Number.isNaN(date.getTime())?'—':beijing.format(date);
}
export function coinQuantity(value?:string){
 if(!value||!Number.isFinite(Number(value)))return '—';
 return Number(value).toLocaleString('en-US',{maximumFractionDigits:12});
}
