/** Only native Keychain/Keystore retain credentials; web previews remain memory-only. */
import {Capacitor,registerPlugin} from '@capacitor/core';
import {SERVICE_URL,type Connection} from './api';
const store=registerPlugin<{get():Promise<{value:string}>;set(data:{value:string}):Promise<void>;clear():Promise<void>}>('DeviceSession');
export const nativeSession=Capacitor.isNativePlatform();
export async function restoreSession():Promise<Connection|null>{
 if(!nativeSession)return null;
 const {value}=await store.get();if(!value)return null;
 const data=JSON.parse(value);
 if(data.url!==SERVICE_URL||typeof data.token!=='string'||data.token.length<32||!['paper','okx_live'].includes(data.mode))return null;
 return {url:SERVICE_URL,token:data.token,mode:data.mode,accountId:typeof data.accountId==='string'?data.accountId:'primary'};
}
export async function rememberSession(connection:Connection){
 if(nativeSession)await store.set({value:JSON.stringify({url:SERVICE_URL,token:connection.token,mode:connection.mode,accountId:connection.accountId??'primary'})});
}
export async function forgetSession(){if(nativeSession)await store.clear()}
