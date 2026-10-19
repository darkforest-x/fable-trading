import {navigation,notice,$,on,contains,replaceLeaf,api,poll,num,base,source,text,rewrite,pct,rememberSources,action,dialog,esc} from './core.js';
import {bindProfile} from './workspace-controls.js';
navigation();
if(document.body.dataset.proMode==='connected')bindProfile();
const page=document.body.dataset.proPage;
if(document.body.dataset.proMode==='reference') {
 // Exact design content is intentionally isolated from all account and order APIs.
 document.title='Stitch 原稿 · '+page;
 for(const b of document.querySelectorAll('button:not([data-pro-bound])'))on(b,()=>notice('当前为 Stitch 原稿，数值为设计样例。真实数据工作区：'+location.pathname));
} else {
 document.title='Spike Pro · '+page;
 try {
  rememberSources();
  const module=await import('./'+page+'.js');
  await module.mount();
 } catch(e) {
  console.error(e);
  // Never reveal the source's fabricated financial figures on initialization failure.
  const main=$('main');main.replaceChildren();const msg=document.createElement('p');msg.className='p-space-xl text-error';msg.textContent='页面接入失败：'+e.message;main.append(msg);
 } finally {$('#pro-pending')?.remove();}
}
if(document.body.dataset.proMode==='connected'){
 const header=$('header'),errors=new Map();
 rewrite(header,{'$89,420':'—','$3,340':'—','+2.85%':'—','+1.92%':'—','0.0102%':'—','72 贪婪':'未接入'});
 const count=$('button[title="系统诊断与通知"]',header)?.nextElementSibling;if(count)text(count,'•');
 if(source('$89,420',header))poll(async()=>{const d=await api('/api/research/paper-desk/symbols');const btc=d.symbols?.find(x=>x.symbol==='BTC-USDT-SWAP'),eth=d.symbols?.find(x=>x.symbol==='ETH-USDT-SWAP');rewrite(header,{'$89,420':num(btc?.last),'$3,340':num(eth?.last),'+2.85%':pct(btc?.change_24h_percent),'+1.92%':pct(eth?.change_24h_percent)});},30000);
 if(header){for(const e of header.querySelectorAll('span,div')){if(e.children.length||e.classList.contains('material-symbols-outlined'))continue;if(/Senior Quant|Quant Trader|ALPHA-PROP|Live Sub-Account|\d+ms|#08|BINANCE|VIP|9842/.test(e.textContent))textShell(e,/ms/.test(e.textContent)?'连接中':'本机工作区');}const status=[...header.querySelectorAll('span')].find(e=>e.textContent.includes('连接中'));poll(async()=>{const d=await api('/api/health');if(status)status.textContent=errors.size?'数据待更新':d.service_alive?'后台在线':'后台不可达';},30000);}
 document.addEventListener('pro-data-error',e=>{errors.set(e.detail.source,e.detail.message);document.body.dataset.dataError=[...errors.values()].join('；');const s=source('巡检引擎 120ms',header)||source('18ms',header);text(s,'数据待更新');if(s){s.title=document.body.dataset.dataError;action(s,()=>dialog('数据连接状态',`<p>${esc(document.body.dataset.dataError||'各数据接口已恢复')}</p>`,[{label:'重新读取',run:d=>{d.close();document.dispatchEvent(new Event('pro-refresh'));}}]),'查看数据连接状态与重试');}});document.addEventListener('pro-data-ok',e=>{errors.delete(e.detail.source);if(!errors.size)delete document.body.dataset.dataError;});
}
function textShell(e,v){e.textContent=v;}
