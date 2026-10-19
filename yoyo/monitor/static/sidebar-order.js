/* Direct drag preferences; clicks navigate, completed drops persist immediately. */
(() => {
  'use strict';
  const KEY='spike-sidebar-order-v2';
  const ordered=(saved,defaults)=>[...new Set([...(Array.isArray(saved)?saved:[]).filter(id=>defaults.includes(id)),...defaults])];
  function normalize(saved,defaults){
    const groups=ordered(saved?.groups,defaults.groups),valid=new Set(Object.values(defaults.items).flat()),seen=new Set(),items={};
    for(const group of groups){items[group]=[];for(const id of Array.isArray(saved?.items?.[group])?saved.items[group]:[]){if(valid.has(id)&&!seen.has(id)){items[group].push(id);seen.add(id);}}}
    for(const group of defaults.groups)for(const id of defaults.items[group])if(!seen.has(id)){items[group].push(id);seen.add(id);}
    return {groups,items};
  }
  function moved(list,id,offset){const result=[...list],from=result.indexOf(id),to=from+offset;if(from<0||to<0||to>=result.length)return result;result.splice(to,0,...result.splice(from,1));return result;}
  function relocate(order,id,group,index){
    const result={groups:[...order.groups],items:Object.fromEntries(Object.entries(order.items).map(([g,ids])=>[g,ids.filter(x=>x!==id)]))};
    if(!result.items[group]||!Object.values(order.items).some(ids=>ids.includes(id)))return order;
    result.items[group].splice(Math.max(0,Math.min(index,result.items[group].length)),0,id);return result;
  }
  if(typeof module!=='undefined')module.exports={ordered,normalize,moved,relocate};
  const nav=typeof document!=='undefined'&&document.querySelector('.nav-list');if(!nav)return;
  const groups=new Map([...nav.querySelectorAll('[data-nav-group]')].map(n=>[n.dataset.navGroup,n]));
  const items=new Map([...nav.querySelectorAll('.nav-item[data-view]')].map(n=>[n.dataset.view,n]));
  const defaults={groups:[...groups.keys()],items:Object.fromEntries([...groups].map(([id,n])=>[id,[...n.querySelectorAll('[data-view]')].map(b=>b.dataset.view)]))};
  let order=normalize(null,defaults),dragging=null,suppressClick=false,dropNode=null;
  const status=document.getElementById('nav-order-status');
  try{order=normalize(JSON.parse(localStorage.getItem(KEY)||'null'),defaults);}catch(_){}
  function apply(){for(const group of order.groups){const node=groups.get(group);nav.append(node);for(const id of order.items[group])node.append(items.get(id).closest('.nav-order-row'));}}
  function save(){try{localStorage.setItem(KEY,JSON.stringify(order));status.textContent='菜单顺序已自动保存';}catch(_){status.textContent='顺序已调整，但浏览器未允许保存；刷新后会恢复';}}
  function move(kind,id,offset){if(kind==='group')order.groups=moved(order.groups,id,offset);else{const g=order.groups.find(g=>order.items[g].includes(id));order.items[g]=moved(order.items[g],id,offset);}apply();save();}
  function grip(label){const el=document.createElement('span');el.className='nav-drag-grip';el.textContent='⠿';el.title='拖拽调整'+label+'顺序';el.setAttribute('aria-hidden','true');return el;}
  for(const [group,node] of groups){
    const heading=node.querySelector('.nav-group-label,summary');heading.dataset.sortGroup=group;heading.tabIndex=0;heading.title='拖拽分组；Alt + ↑/↓ 调整顺序';heading.append(grip('分组'));
    for(const id of defaults.items[group]){const b=items.get(id),row=document.createElement('div');row.className='nav-order-row';row.dataset.sortRow=id;b.before(row);row.append(b,grip('菜单'));b.setAttribute('aria-description','可拖拽排序，或按 Alt 加上下箭头；自动保存');}
  }
  function target(node){const row=node?.closest('[data-sort-row]');if(row)return {kind:'item',id:row.dataset.sortRow,group:row.closest('[data-nav-group]').dataset.navGroup,node:row};const h=node?.closest('[data-sort-group]');return h?{kind:'group',id:h.dataset.sortGroup,node:h}:null;}
  nav.addEventListener('click',e=>{if(suppressClick){e.preventDefault();e.stopImmediatePropagation();suppressClick=false;}},true);
  nav.addEventListener('keydown',e=>{if(!e.altKey||!['ArrowUp','ArrowDown'].includes(e.key))return;const t=target(e.target);if(!t)return;e.preventDefault();move(t.kind,t.id,e.key==='ArrowUp'?-1:1);e.target.focus();});
  nav.addEventListener('pointerdown',e=>{if(e.button!==0||e.pointerType==='touch'&&!e.target.closest('.nav-drag-grip'))return;const t=target(e.target);if(!t)return;suppressClick=false;dragging={...t,x:e.clientX,y:e.clientY,pointer:e.pointerId,moved:false};});
  nav.addEventListener('pointermove',e=>{
    if(!dragging||dragging.pointer!==e.pointerId)return;
    if(!dragging.moved&&Math.hypot(e.clientX-dragging.x,e.clientY-dragging.y)>6){dragging.moved=true;nav.setPointerCapture(e.pointerId);dragging.node.classList.add('is-dragging');}
    if(!dragging.moved)return;e.preventDefault();dropNode?.classList.remove('is-drop-target');const t=target(document.elementFromPoint(e.clientX,e.clientY));dropNode=t&&(t.kind===dragging.kind||dragging.kind==='item')?t.node:null;dropNode?.classList.add('is-drop-target');
    const bounds=nav.getBoundingClientRect();if(e.clientY<bounds.top+30)nav.scrollTop-=12;else if(e.clientY>bounds.bottom-30)nav.scrollTop+=12;
  });
  function finish(e,cancel=false){if(!dragging||dragging.pointer!==e.pointerId)return;const from=dragging,t=target(document.elementFromPoint(e.clientX,e.clientY));dragging=null;from.node.classList.remove('is-dragging');dropNode?.classList.remove('is-drop-target');dropNode=null;if(nav.hasPointerCapture(e.pointerId))nav.releasePointerCapture(e.pointerId);suppressClick=from.moved;if(!cancel&&from.moved&&t){if(from.kind==='group'&&t.kind==='group')move('group',from.id,order.groups.indexOf(t.id)-order.groups.indexOf(from.id));else if(from.kind==='item'){const group=t.kind==='group'?t.id:t.group;const index=t.kind==='group'?order.items[group].length:order.items[group].indexOf(t.id);order=relocate(order,from.id,group,index);if(groups.get(group).tagName==='DETAILS')groups.get(group).open=true;apply();save();}}if(from.moved)setTimeout(()=>{suppressClick=false;},0);}
  window.addEventListener('pointerup',e=>finish(e));window.addEventListener('pointercancel',e=>finish(e,true));
  nav.addEventListener('dragstart',e=>e.preventDefault());
  window.addEventListener('storage',e=>{if(e.key!==KEY&&e.key!==null)return;try{order=normalize(JSON.parse(e.newValue||'null'),defaults);apply();}catch(_){}});
  apply();
})();
