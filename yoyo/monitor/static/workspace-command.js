/* Route-only workspace search. Uses the existing navigation buttons so history,
 * lazy loading and focus handling follow the same path as sidebar navigation.
 * Never invokes account, order, scan, or other mutation APIs.
 */
(() => {
  'use strict';
  const trigger=document.getElementById('workspace-search');
  const dialog=document.getElementById('workspace-command');
  const input=document.getElementById('workspace-command-input');
  const results=document.getElementById('workspace-command-results');
  if(!trigger||!dialog||!input||!results)return;
  let previous=null;
  const aliases={market:'行情 btc eth',signals:'信号 账户 自动', 'yolo-detect':'均线 yolo 检测 账户 手动', 'trading-books':'书籍 阅读', 'paper-desk':'模拟 交易',system:'健康 服务 状态'};
  const items=()=>Array.from(document.querySelectorAll('.nav-list .nav-item[data-view]')).map(node=>({
    node, view:node.dataset.view, title:node.getAttribute('aria-label')||node.textContent.trim(),
    group:({'market':'市场观察','signals':'信号与检测','accounts':'账户与交易','research':'研究工作区','learn':'学习与复盘','manage':'运行管理'})[node.closest('.nav-group')?.dataset.navGroup]||'研究工具'
  }));
  function render(){
    const query=input.value.trim().toLocaleLowerCase();
    const matches=items().filter(item=>(item.title+' '+item.view+' '+(aliases[item.view]||'')).toLocaleLowerCase().includes(query));
    results.replaceChildren();
    for(const item of matches){
      const button=document.createElement('button');button.type='button';
      const title=document.createElement('span');title.textContent=item.title;
      const group=document.createElement('small');group.textContent=item.group;
      button.append(title,group);
      button.addEventListener('click',()=>{
        // Prevent a close event from stealing focus back from the destination.
        previous=null;dialog.close();item.node.click();
        document.getElementById('main')?.focus({preventScroll:true});
      });
      results.append(button);
    }
    if(!matches.length){const empty=document.createElement('p');empty.textContent='没有匹配的页面，试试“信号”或“均线”。';results.append(empty);}
  }
  function open(){
    if(document.querySelector('dialog[open]')||document.body.classList.contains('nav-open')||document.body.classList.contains('workspace-fullscreen-open'))return;
    previous=document.activeElement;input.value='';render();dialog.showModal();input.focus();
  }
  trigger.addEventListener('click',open);
  document.getElementById('workspace-command-close').addEventListener('click',()=>dialog.close());
  input.addEventListener('input',render);
  dialog.addEventListener('close',()=>{previous?.focus({preventScroll:true});previous=null;});
  dialog.addEventListener('click',event=>{if(event.target===dialog){const r=dialog.getBoundingClientRect();if(event.clientX<r.left||event.clientX>r.right||event.clientY<r.top||event.clientY>r.bottom)dialog.close();}});
  dialog.addEventListener('keydown',event=>{
    if(event.isComposing)return;
    // Search inputs otherwise consume the first Escape to clear their value.
    if(event.key==='Escape'){event.preventDefault();dialog.close();return;}
    const buttons=Array.from(results.querySelectorAll('button'));
    const index=buttons.indexOf(document.activeElement);
    if(event.key==='ArrowDown'&&buttons.length){event.preventDefault();buttons[(index+1)%buttons.length].focus();}
    if(event.key==='ArrowUp'&&buttons.length){event.preventDefault();if(index<=0)input.focus();else buttons[index-1].focus();}
    if(event.key==='Enter'&&document.activeElement===input&&buttons.length){event.preventDefault();buttons[0].click();}
  });
  document.addEventListener('keydown',event=>{
    if(event.defaultPrevented||event.isComposing||event.altKey||!(event.metaKey||event.ctrlKey)||event.key.toLowerCase()!=='k')return;
    if(document.querySelector('dialog[open]'))return;
    event.preventDefault();open();
  });
})();
