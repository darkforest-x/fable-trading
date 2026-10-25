/** Gesture decisions are independent of rendering and never wrap the tab strip. */
export const MAIN_TABS=['合约','纪律','1.618','持仓','统计'] as const;
export type SwipeAxis='pending'|'horizontal'|'vertical';
export function swipeAxis(axis:SwipeAxis,dx:number,dy:number):SwipeAxis{
 if(axis!=='pending')return axis;
 if(Math.max(Math.abs(dx),Math.abs(dy))<10)return 'pending';
 return Math.abs(dx)>Math.abs(dy)*1.2?'horizontal':'vertical';
}
export function swipeNeighbor(tab:string,dx:number){
 const i=MAIN_TABS.findIndex(v=>v===tab),next=i+(dx<0?1:-1);
 return dx!==0&&i>=0&&next>=0&&next<MAIN_TABS.length?MAIN_TABS[next]:null;
}
export function swipeOffset(tab:string,dx:number,width:number){
 const travel=Math.abs(dx),limit=Math.max(1,width);
 const offset=travel<=limit?travel:limit+(travel-limit)*.2;
 return Math.sign(dx)*offset*(swipeNeighbor(tab,dx)?1:.35);
}
export function swipeDestination(tab:string,dx:number,dy:number,elapsed:number,velocity=dx/Math.max(1,elapsed)){
 const travel=Math.abs(dx),flick=travel>=16&&Math.abs(velocity)>.45&&Math.sign(velocity)===Math.sign(dx);
 if((travel<75&&!flick)||travel<Math.abs(dy)*1.2)return null;
 return swipeNeighbor(tab,dx);
}
