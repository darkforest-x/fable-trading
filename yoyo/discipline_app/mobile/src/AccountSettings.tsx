/** Stitch 980219 / 545a56: compact settings with credentials in a secondary screen. */
import {useEffect,useState} from 'react';
import {api,type Connection} from './api';
import {Icon} from './Icon';
import {feedback} from './feedback';
import {Spinner} from './Dialog';
export type Account={id:string;name:string;environment:string;connected?:boolean;positions?:number;pending?:number};
export function AccountSettings({connection,busy,onSwitch,onError}:{connection:Connection;busy:boolean;onSwitch:(account:Account)=>Promise<void>;onError:(error:string)=>void}){
 const [accounts,setAccounts]=useState<Account[]>([]),[screen,setScreen]=useState<'summary'|'manage'|'add'>('summary'),[saving,setSaving]=useState(false);
 const [switching,setSwitching]=useState<string|null>(null);
 const [name,setName]=useState(''),[key,setKey]=useState(''),[secret,setSecret]=useState(''),[passphrase,setPassphrase]=useState('');
 useEffect(()=>{let active=true;api<{items:Account[]}>(connection,'/v1/accounts').then(r=>{if(active)setAccounts(r.items)}).catch(e=>{if(active)onError(e.message)});return()=>{active=false}},[connection]);
 const current=accounts.find(a=>a.id===(connection.accountId??'primary'));
 const clear=()=>{setKey('');setSecret('');setPassphrase('')};
 async function save(event:React.FormEvent){
  event.preventDefault();if(saving||busy)return;setSaving(true);feedback('light');
  try{const account=await api<Account>(connection,'/v1/accounts',{name,key,secret,passphrase});clear();setName('');setAccounts(value=>[...value,account]);setScreen('manage');feedback('success')}
  catch(e){onError((e as Error).message);feedback('warning')}finally{setSaving(false)}
 }
 if(screen==='add')return <section className="card account-settings"><button className="plain back-link" disabled={saving} onClick={()=>{clear();setScreen('manage');feedback('light')}}>‹ 返回账户管理</button><h2>添加 OKX 账户</h2><p className="micro">实盘 · USDT 永续合约</p><form onSubmit={save} autoComplete="off">{[['账户名称',name,setName,'text'],['API Key',key,setKey,'password'],['Secret Key',secret,setSecret,'password'],['Passphrase',passphrase,setPassphrase,'password']].map(([label,value,setter,type])=><label className="field" key={label as string}><span className="field-label">{label as string}</span><span className="input-shell"><input aria-label={label as string} type={type as string} value={value as string} onChange={e=>(setter as (v:string)=>void)(e.target.value)} autoComplete="off" autoCapitalize="none" autoCorrect="off" spellCheck={false} maxLength={label==='账户名称'?30:256} required disabled={saving}/></span></label>)}<p className="micro">使用读取、交易权限，不要开启提币权限。验证只读取账户；不会下单或更改 OKX 设置。</p><button className="primary" disabled={saving||busy}>{saving?<><Spinner/>正在验证…</>:'验证并保存'}</button><p className="micro"><Icon name="lock" size={13}/>凭据通过 HTTPS 交给你的 Mac 私有保存；不会保存在手机或显示在账户列表。</p></form></section>;
 return <section className="card account-settings"><div className="section-heading"><h2>{screen==='manage'?'交易账户管理':'交易账户'}</h2>{screen==='manage'&&<button className="plain" disabled={busy} onClick={()=>{setScreen('summary');feedback('light')}}>完成</button>}</div>
 <div className="account-current"><span className="account-avatar"><Icon name="positions" size={22}/></span><span><b>{current?.name??'正在读取账户'}</b><small>{connection.mode==='okx_live'?'OKX 实盘':'模拟账户'}</small></span><span className="freshness connected"><i/>当前账户</span></div>
 <div className="account-switch-list">{accounts.map(a=><button key={a.id} disabled={busy||saving||!!switching||a.id===current?.id} aria-busy={switching===a.id} aria-pressed={a.id===current?.id} className={a.id===current?.id?'selected':''} onClick={()=>{setSwitching(a.id);void onSwitch(a).finally(()=>setSwitching(null))}}><span>{a.name}</span>{switching===a.id?<Spinner/>:a.id===current?.id?<Icon name="check" size={16}/>:<Icon name="chevron" size={16}/>}</button>)}</div>
 <p className="micro">切换只改变当前查看与下单的账户。各账户的风控记录和持仓保护独立保留。</p>
 {screen==='summary'?<button className="settings-link" disabled={busy} onClick={()=>{setScreen('manage');feedback('light')}}><span>管理交易账户</span><Icon name="chevron" size={16}/></button>:<button className="soft-button" disabled={busy||connection.mode!=='okx_live'} onClick={()=>{setScreen('add');feedback('light')}}>＋ 添加账户</button>}
 </section>;
}
