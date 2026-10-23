/** Deliberate downward drag or a short downward flick on a sheet header.
 * Horizontal/upward movement and slow short drags must spring back.
 */
export function shouldDismissSheet(dx:number,dy:number,velocity=0){
 return dy>Math.abs(dx)*1.5&&(dy>=64||(dy>=28&&velocity>=.65));
}
