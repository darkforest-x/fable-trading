/** Pure gesture boundary used by the touch handler. */
export const MAIN_TABS=['合约','纪律','持仓','统计'] as const;
export function swipeDestination(tab:string,dx:number,dy:number,elapsed:number){
 if(elapsed>700||Math.abs(dx)<65||Math.abs(dx)<Math.abs(dy)*1.6)return null;
 const i=MAIN_TABS.findIndex(v=>v===tab),next=i+(dx<0?1:-1);
 return i>=0&&next>=0&&next<MAIN_TABS.length?MAIN_TABS[next]:null;
}
