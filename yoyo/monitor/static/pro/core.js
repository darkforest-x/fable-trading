/* Data and behavior only. Visual vocabulary belongs to the Stitch export. */
export const $=(s,r=document)=>r?.querySelector(s)||null, $$=(s,r=document)=>[...(r?.querySelectorAll(s)||[])];
export const text=(el,value)=>{if(el)el.textContent=value??'—';};
export const finite=v=>v!==null&&v!==undefined&&v!==''&&Number.isFinite(Number(v));
export const num=(v,d=2)=>finite(v)?Number(v).toLocaleString('en-US',{maximumFractionDigits:d}):'—';
export const compact=v=>finite(v)?Number(v).toLocaleString('en-US',{notation:'compact',maximumFractionDigits:2}):'—';
export const pct=v=>finite(v)?`${Number(v)>0?'+':''}${num(v,2)}%`:'—';
export const time=v=>v?new Date(Number(v)||v).toLocaleString('zh-CN',{hour12:false}):'—';
export const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
export const params=new URLSearchParams(location.search);
export const base=v=>String(v||'').split('-')[0];
export function route(page,query={}){const p=new URLSearchParams(query);location.assign('/pro/'+page+(p.size?'?'+p:''));}
export function byText(label,root=document,selector='button'){return $$(selector,root).find(e=>e.textContent.trim()===label);}
export function contains(label,root=document,selector='button'){return $$(selector,root).find(e=>e.textContent.includes(label));}
export function on(el,fn,type='click'){
 if(!el)return;el.dataset.proBound='true';const old=el._proHandlers?.[type];if(old)el.removeEventListener(type,old);el._proHandlers ||= {};
 const handler=async e=>{if(type==='click'&&el.tagName==='BUTTON')e.preventDefault();if(type==='click'&&el.getAttribute('aria-busy')==='true')return;const before=el.getAttribute('aria-busy');
  try{const result=fn(e);if(result?.then){if(type==='click')el.setAttribute('aria-busy','true');await result;}}
  catch(err){if(err.name!=='AbortError')notice(err.message);}
  finally{if(type==='click'){if(before===null)el.removeAttribute('aria-busy');else el.setAttribute('aria-busy',before);}}
 };el._proHandlers[type]=handler;el.addEventListener(type,handler);
}
export function action(el,fn,label){if(!el)return;el.tabIndex=0;el.setAttribute('role','button');if(label)el.setAttribute('aria-label',label);on(el,fn);on(el,e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();el.click();}},'keydown');}
export function active(el){if(!el)return;for(const b of $$(':scope > button',el.parentElement)){b.setAttribute('aria-pressed',String(b===el));b.classList.toggle('bg-surface-container-highest',b===el);b.classList.toggle('text-primary',b===el);}}
export async function api(path,options={}){
 const ctl=new AbortController(),abort=()=>ctl.abort();let timedOut=false;const timeout=setTimeout(()=>{timedOut=true;abort();},options.timeout||15000);options.signal?.addEventListener('abort',abort,{once:true});if(options.signal?.aborted)abort();
 try{const r=await fetch(path,{cache:'no-store',...options,signal:ctl.signal,headers:{Accept:'application/json',...(options.body?{'Content-Type':'application/json'}:{}),...options.headers}});const data=await r.json();if(!r.ok){const d=data.detail;const error=Error(typeof d==='string'?d:d?.message||d?.title||JSON.stringify(d||data));error.status=r.status;error.detail=d||data;throw error;}return data;}catch(e){if(timedOut&&!options.signal?.aborted)throw Error('请求超时，未收到后台回执。读取操作可以重试；已提交的交易请先核对委托记录。');throw e;}finally{clearTimeout(timeout);options.signal?.removeEventListener('abort',abort);}
}
let pollSequence=0;
export function poll(fn,ms=15000){
 const source=String(++pollSequence);let timer,stopped=false,running=null;
 const tick=()=>{if(stopped||document.hidden)return Promise.resolve();if(running)return running;clearTimeout(timer);
  running=(async()=>{try{await fn();document.dispatchEvent(new CustomEvent('pro-data-ok',{detail:{source}}));}catch(e){if(e.name!=='AbortError'){console.warn('Pro data:',e.message);document.dispatchEvent(new CustomEvent('pro-data-error',{detail:{source,message:e.message}}));}}finally{if(!stopped)timer=setTimeout(tick,ms);}})().finally(()=>running=null);return running;
 };
 const visible=()=>{clearTimeout(timer);if(!document.hidden)tick();};document.addEventListener('visibilitychange',visible);document.addEventListener('pro-refresh',tick);
 window.addEventListener('pagehide',()=>{stopped=true;clearTimeout(timer);document.removeEventListener('visibilitychange',visible);document.removeEventListener('pro-refresh',tick);},{once:true});tick();return tick;
}
export function download(name,data,type='application/json'){const a=document.createElement('a');a.href=URL.createObjectURL(new Blob([typeof data==='string'?data:JSON.stringify(data,null,2)],{type}));a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(a.href),1000);}
export function dialog(title,body,actions=[]){
 const previous=document.activeElement,d=document.createElement('dialog');d.className='bg-surface-container-low text-on-surface rounded-xl p-space-xl border border-outline-variant max-w-2xl w-full max-h-[90vh] overflow-auto';d.setAttribute('aria-label',title);
 d.innerHTML=`<div class="flex items-center justify-between gap-space-xl mb-space-lg"><h2 class="font-headline-md text-headline-md">${esc(title)}</h2><button type="button" aria-label="关闭" class="text-on-surface-variant"><span class="material-symbols-outlined">close</span></button></div><div class="font-body-md text-body-md flex flex-col gap-space-md">${body}</div><div class="flex justify-end gap-space-md mt-space-xl"></div>`;
 document.body.append(d);const close=()=>d.close();$('button',d).onclick=close;d.addEventListener('click',e=>{if(e.target===d){const r=d.getBoundingClientRect();if(e.clientX<r.left||e.clientX>r.right||e.clientY<r.top||e.clientY>r.bottom)close();}});d.addEventListener('close',()=>{d.remove();previous?.focus();},{once:true});
 const foot=d.lastElementChild;for(const action of actions){const b=document.createElement('button');b.className=action.primary?'px-space-lg py-space-md bg-primary-container text-on-primary rounded-lg font-semibold':'px-space-lg py-space-md bg-surface-container-high text-on-surface rounded-lg';b.textContent=action.label;b.type='button';b.onclick=async()=>{b.disabled=true;try{await action.run(d);}catch(e){text($('[data-dialog-error]',d),e.message);if(!$('[data-dialog-error]',d)){const p=document.createElement('p');p.dataset.dialogError='';p.className='text-error';p.textContent=e.message;foot.before(p);}}finally{b.disabled=false;}};foot.append(b);}d.showModal();return d;
}
export function notice(message){return dialog('操作说明',`<p>${esc(message)}</p>`,[{label:'知道了',run:d=>d.close()}]);}
export function empty(container,message='暂无记录'){if(!container)return;container.replaceChildren();const p=document.createElement('p');p.className='p-space-xl text-on-surface-variant font-body-sm text-body-sm';p.textContent=message;container.append(p);}
export function values(root,selector,list){$$(selector,root).forEach((el,i)=>text(el,list[i]??'—'));}
export function storage(key,value){try{if(arguments.length>1)localStorage.setItem('spike-pro:'+key,JSON.stringify(value));else return JSON.parse(localStorage.getItem('spike-pro:'+key)||'null');}catch{return null;}}
export function replaceLeaf(root,old,value){for(const el of $$('*',root))if(!el.children.length&&el.textContent.trim()===old)text(el,value);}
export function navigation(){
 const paths={'market-watch':'market','ma-convergence':'ma-detection','signal-radar':'signals','execution-orders':'trading','visual-ai-lab':'vision-research','academy-playbook':'learning','research-workspace':'research','account-center':'account','system-runtime':'runtime','ma-density-scanner':'ma-detection','signal-hub':'signals','trading-accounts':'account','knowledge-replay':'learning','system-telemetry':'runtime','market-leaders':'market','positions-monitor':'account','signals-radar':'signals','instant-terminal':'trading','portfolio-ops':'runtime'};
 const labels={'市场观察':'market','均线密集检测':'ma-detection','信号中心':'signals','交易与账户':'trading','视觉研究工作区':'vision-research','研究工作区':'research','知识与复盘':'learning','系统与运行':'runtime',candlestick_chart:'market',grain:'ma-detection',sensors:'signals',account_balance_wallet:'account',terminal:'research',history_edu:'learning',settings_input_component:'runtime'};
 for(const a of $$('header a,aside a,nav a')){const page=paths[a.dataset.path]||labels[a.textContent.trim()];if(page)a.href='/pro/'+page+(document.body.dataset.proMode==='reference'?'?reference=true':'');}
 for(const b of $$('aside button')){const label=b.title;on(b,()=>{if(/热键/.test(label))return notice('F2：打开交易工作区或定位下单数量　/：定位当前页搜索　Esc：关闭浮层。快捷键可在终端偏好中关闭。');if(/布局|多屏/.test(label))return import('./workspace-controls.js').then(m=>m.workspaceMenu(/多屏/.test(label)));if(/偏好/.test(label))return import('./workspace-controls.js').then(m=>m.preferences());if(/订单流/.test(label))return route('market');if(/脚本/.test(label))return route('research');if(/风控/.test(label))return route('account');return route('runtime');});}
 const quick=()=>{if(document.body.dataset.proPage==='trading'){$('#orderQty')?.scrollIntoView({block:'center'});$('#orderQty')?.focus();}else route('trading');};on(contains('快速开单',$('header'))||contains('快捷开单',$('header')),quick);on(byText('search',$('header')),()=>route('market'));
 on($('header [title="系统诊断与通知"]')||byText('notifications',$('header')),()=>route('runtime'));
 document.addEventListener('keydown',e=>{if(storage('shortcuts')===false||$('dialog[open]')||document.activeElement?.isContentEditable)return;if(e.key==='F2'){e.preventDefault();quick();}else if(e.key==='/'&&!/INPUT|TEXTAREA|SELECT/.test(document.activeElement?.tagName)){e.preventDefault();$('main input[type="text"],main input:not([type])')?.focus();}});
}
// Replace only data-bearing source leaves, retaining every containing component.
export function pendingData(root){
 for(const el of $$('*',root)){
  if(el.closest('button,select,option,label,code,svg,textarea')||el.classList.contains('material-symbols-outlined'))continue;
  for(const node of [...el.childNodes]){if(node.nodeType!==3)continue;const t=node.textContent.trim();if(!t)continue;
   if(/\$|[+-]?\d[\d,.]*%|\d[\d,.]*\s*(ms|μs|GB|TB|img\/s|条|对标的|分钟前|分钟|席位)|#ORD|#EXP|#[0-9]+|\d+,\d{3}|\d+\.\d+|Epoch:|\d+\/\d+|WebSocket.*\d/.test(t)||/^[-+]?\d+(\.\d+)?$/.test(t))node.textContent='—';
  }
 }
}

export const requestId=()=>crypto.randomUUID();
export const post=(path,value,options={})=>api(path,{method:'POST',body:JSON.stringify(value),...options});
export const fieldClass='w-full bg-surface-container-lowest text-on-surface rounded p-space-sm border border-outline-variant';
export function field(name,label,value='',type='text'){return `<label class="flex flex-col gap-space-xs">${esc(label)}<input aria-label="${esc(label)}" name="${esc(name)}" type="${esc(type)}" value="${esc(value)}" class="${fieldClass}"></label>`;}
export function selectField(name,label,items,value=''){return `<label class="flex flex-col gap-space-xs">${esc(label)}<select aria-label="${esc(label)}" name="${esc(name)}" class="${fieldClass}">${items.map(x=>`<option value="${esc(x.id)}" ${x.id===value?'selected':''}>${esc(x.name)}</option>`).join('')}</select></label>`;}
export function form(title,body,save,label='保存'){
 const d=dialog(title,`<form class="flex flex-col gap-space-md">${body}</form>`,[{label:'取消',run:d=>d.close()},{label,primary:true,run:async d=>{const f=$('form',d);if(!f.reportValidity())return;await save(Object.fromEntries(new FormData(f)),d);}}]);
 // Enter must use the same validation and pending guard as the visible action.
 $('form',d).addEventListener('submit',e=>{e.preventDefault();d.lastElementChild.lastElementChild?.click();});return d;
}
export function inspect(title,data){return dialog(title,`<pre class="font-code-snippet text-code-snippet overflow-auto max-h-96 whitespace-pre-wrap">${esc(typeof data==='string'?data:JSON.stringify(data,null,2))}</pre>`,[{label:'导出',run:()=>download('spike-pro-record.json',data)},{label:'关闭',run:d=>d.close()}]);}
export function table(tableEl,rows,cells,actions){
 if(!tableEl)return;const body=$('tbody',tableEl)||tableEl.appendChild(document.createElement('tbody'));let template=tableEl._template;if(!template){template=$('tr',body)?.cloneNode(true);tableEl._template=template;}
 const columns=$$('th',tableEl).length;if(!template){empty(body);return;}
 const frag=document.createDocumentFragment();for(const row of rows){const tr=template.cloneNode(true);const tds=$$('td',tr);cells(row).forEach((v,i)=>{if(tds[i]){tds[i].replaceChildren();if(v instanceof Node)tds[i].append(v);else tds[i].innerHTML=v??'—';}});if(actions)actions(tr,row,tds);frag.append(tr);}body.replaceChildren(frag);if(!rows.length){const tr=document.createElement('tr'),td=document.createElement('td');td.colSpan=columns;td.className='p-space-xl text-on-surface-variant';td.textContent='暂无记录';tr.append(td);body.append(tr);}
}
export function rowActions(template,actions){const box=document.createElement('div');box.className='flex items-center gap-space-xs';const originals=template?$$('button',template):[];actions.forEach((a,i)=>{const b=originals[i]?.cloneNode(true)||document.createElement('button');b.className ||= 'px-space-sm py-space-xs bg-surface-container-high text-primary rounded';text(b,a.label);on(b,a.run);box.append(b);});return box;}
export function line(svg,points,{color='#00e5ff'}={}){if(!svg)return;svg.replaceChildren();if(!points.length)return;const vb=(svg.getAttribute('viewBox')||'0 0 400 160').split(/\s+/).map(Number);const w=vb[2],h=vb[3],lo=Math.min(...points),hi=Math.max(...points),range=hi-lo||1;const p=document.createElementNS('http://www.w3.org/2000/svg','polyline');p.setAttribute('points',points.map((v,i)=>`${i*w/Math.max(1,points.length-1)},${h-5-(v-lo)/range*(h-10)}`).join(' '));p.setAttribute('fill','none');p.setAttribute('stroke',color);p.setAttribute('stroke-width','2');svg.append(p);}
export async function workspace(key,change){const r=await api('/api/pro/workspace/'+key);if(!change)return r.value;return (await api('/api/pro/workspace/'+key,{method:'PUT',body:JSON.stringify({revision:r.revision,value:await change(r.value)})})).value;}
export function toggleLabel(button,label){if(!button)return;const icon=$('.material-symbols-outlined',button);const saved=icon?.cloneNode(true);button.replaceChildren();if(saved)button.append(saved,document.createTextNode(' '));button.append(document.createTextNode(label));}
export function statusTitle(el,status){if(el)el.title=status;}

// Bind content to its source label without changing the exported component tree.
// Mixed icon/text labels need node identity, not a replacement of their parent.
const sourceTextNodes=[];
export function rememberSources(root=document.body){
 for(const el of $$('*',root)){
  if(el.matches('script,style,.material-symbols-outlined'))continue;
  if(!el.children.length)el.dataset.proSource=el.textContent.trim();
  else for(const node of el.childNodes)if(node.nodeType===3&&node.textContent.trim())sourceTextNodes.push({node,label:node.textContent.trim()});
 }
}
export function source(label,root=document){return $$('[data-pro-source]',root).find(e=>e.dataset.proSource===label);}
export function rewrite(root,map){for(const [old,value] of Object.entries(map)){const el=source(old,root);if(el)text(el,value);else for(const item of sourceTextNodes)if(item.label===old&&root?.contains(item.node))item.node.textContent=value??'—';}}
export function pages(container,getRows,draw,size=8){
 let index=0;const back=document.createElement('button'),next=document.createElement('button'),count=document.createElement('span');
 back.className=next.className='px-space-sm py-space-xs rounded bg-surface-container-high text-primary';text(back,'‹');text(next,'›');back.setAttribute('aria-label','上一页');next.setAttribute('aria-label','下一页');container.replaceChildren(back,count,next);
 const render=(reset=false)=>{if(reset)index=0;const all=getRows();index=Math.min(index,Math.max(0,Math.ceil(all.length/size)-1));text(count,`${all.length?index*size+1:0}–${Math.min(all.length,(index+1)*size)} / ${all.length}`);back.disabled=index===0;next.disabled=(index+1)*size>=all.length;draw(all.slice(index*size,(index+1)*size));};on(back,()=>{index--;render();});on(next,()=>{index++;render();});return render;
}
