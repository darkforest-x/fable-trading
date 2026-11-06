/** The only monitor write the app can make: switch 1.618 Bark pushes (owner 2026-11-06,
 * “App 内报警 + Bark”, with the switch in settings). It changes notification delivery only:
 * no order, position, risk or account endpoint is reachable from here. */
import {api,type Connection} from './api';
import type {NotificationSnapshot} from './monitorFormat';
export const FIB_BARK_PATH='/v1/monitor/fib-bark';
export function setFibBark(connection:Connection,enabled:boolean){
 return api<NotificationSnapshot>(connection,FIB_BARK_PATH,{enabled});
}
