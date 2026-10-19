/** Only a deliberate downward movement starting on the handle dismisses a sheet. */
export function shouldDismissSheet(dx:number,dy:number){return dy>=64&&dy>Math.abs(dx)*1.5}
