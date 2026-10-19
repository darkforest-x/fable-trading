/* Shared workspace actions; retain the exported shell and its visual tokens. */
import {dialog,form,selectField,storage,route,esc,$,action} from './core.js';

const screens=[['market','市场观察'],['ma-detection','均线密集检测'],['signals','信号中心'],['trading','交易与执行'],['account','账户与持仓'],['vision-research','视觉研究'],['research','研究工作区'],['learning','知识与复盘'],['runtime','系统运行'],['mobile','紧凑工作台']];
export function workspaceMenu(separate=false){
 const ref=document.body.dataset.proMode==='reference'?'?reference=true':'';
 return dialog(separate?'多窗口工作区':'切换工作区',`<p>${separate?'选择要在独立标签页中打开的工作区；各页共用后台数据。':'选择完整工作区。'}</p><div class="grid grid-cols-2 gap-space-md">${screens.map(([id,name])=>`<a class="p-space-md rounded bg-surface-container-high text-primary" href="/pro/${id}${ref}" ${separate?'target="_blank" rel="noopener"':''}>${esc(name)}${separate?' ↗':''}</a>`).join('')}</div>`);
}
export function preferences(){return form('终端偏好',selectField('sound','通知提示音',[{id:'true',name:'开启'},{id:'false',name:'关闭'}],String(Boolean(storage('sound'))))+selectField('shortcuts','键盘快捷键',[{id:'true',name:'开启'},{id:'false',name:'关闭'}],String(storage('shortcuts')!==false)),async(v,d)=>{storage('sound',v.sound==='true');storage('shortcuts',v.shortcuts==='true');window.dispatchEvent(new Event('pro-preferences-changed'));d.close();});}
export function profile(){return dialog('本机工作区',`<p>账户、模拟记录和执行权限分别管理。</p>`,[{label:'账户与持仓',run:()=>route('account')},{label:'Pro 实盘配置',run:async d=>{d.close();const {liveSettings}=await import('./live-controls.js');return liveSettings();}},{label:'终端偏好',run:d=>{d.close();preferences();}}]);}
export function bindProfile(){const image=$('header img[alt="Profile"]');if(image)action(image.parentElement,profile,'本机工作区菜单');}
