/** Read-only monitor modules (owner 2026-11-06): 1.618 paper accounts, market alarms, 12H cloud board.
 * Every request is a GET to a fixed /v1/monitor/* path on the discipline service, which
 * whitelists fields from the loopback monitor. This client cannot reach order, close,
 * stop-loss or account endpoints, and nothing here feeds sizing or risk decisions. */
import {api,type Connection} from './api';
import {monitorPath,type MonitorSegment} from './monitorFormat';
export * from './monitorFormat';

export function monitorGet<T>(connection:Connection,segment:MonitorSegment,query?:Record<string,string>,signal?:AbortSignal){
 // `api` issues GET when no body is supplied; a body is never passed from this module.
 return api<T>(connection,monitorPath(segment,query),undefined,signal);
}
/** Poll cadence per module, matched to how fast the monitor itself refreshes. */
export const MONITOR_POLL_MS:Record<MonitorSegment,number>={paper:10000,alerts:8000,cloud:60000,notifications:8000,monthFirstWeek:10000};
