/* Local presentation state for owner-authored task panels. Keeps existing DOM,
 * forms, event owners and chart instances intact; never submits actions.
 */
(() => {
  'use strict';
  const remembered=new Map(), disclosures=new Map(), inspectorState=new Map(), bound=new WeakSet();
  let inspectorId=0;
  function mount(root){
    if(bound.has(root))return;
    const panels=[...root.children].filter(n=>n.hasAttribute('data-work-panel'));
    if(panels.length<2)return;
    bound.add(root);
    const key=root.dataset.workTabs,nav=document.createElement('nav');
    nav.className='work-panel-tabs';nav.setAttribute('role','tablist');nav.setAttribute('aria-label',root.dataset.workLabel||'内容分区');
    const buttons=panels.map((panel,index)=>{
      const id=key+'-panel-'+index,button=document.createElement('button');
      panel.id=panel.id||id;panel.setAttribute('role','tabpanel');
      button.type='button';button.id=id+'-tab';button.textContent=panel.dataset.workPanel;button.setAttribute('role','tab');button.setAttribute('aria-controls',panel.id);panel.setAttribute('aria-labelledby',button.id);nav.append(button);return button;
    });
    function select(index,focus=false){
      remembered.set(key,index);
      panels.forEach((panel,i)=>{panel.hidden=i!==index;buttons[i].setAttribute('aria-selected',String(i===index));buttons[i].tabIndex=i===index?0:-1;});
      if(focus)buttons[index].focus();
      window.SpikeWorkbench?.refresh();
      // Existing chart owners already respond to resize; do not recreate them.
      window.dispatchEvent(new Event('resize'));
    }
    buttons.forEach((button,index)=>{
      button.addEventListener('click',()=>select(index));
      button.addEventListener('keydown',e=>{if(!['ArrowLeft','ArrowRight','Home','End'].includes(e.key))return;e.preventDefault();select(e.key==='Home'?0:e.key==='End'?buttons.length-1:(index+(e.key==='ArrowRight'?1:-1)+buttons.length)%buttons.length,true);});
    });
    root.prepend(nav);select(Math.min(remembered.get(key)||0,panels.length-1));
  }
  function inspector(root){
    if(bound.has(root))return;
    bound.add(root);
    const button=document.createElement('button');
    button.type='button';button.className='work-inspector-toggle';
    const label=root.querySelector('h2')?.textContent || '筛选与设置';
    const content=document.createElement('div');content.className='work-inspector-content';
    content.id='work-inspector-'+(++inspectorId);button.setAttribute('aria-controls',content.id);
    while(root.firstChild)content.append(root.firstChild);
    root.append(button,content);
    // Keep primary search visible and preserve the original event owners.
    if(root.classList.contains('signal-query-inspector')){
      const search=content.querySelector('.search-field'),summary=content.querySelector('.signal-result-summary');
      if(search)root.prepend(search);
      if(summary)root.append(summary);
    }
    const key=(root.closest('.view-panel')?.id||'')+'|'+root.className;
    let expanded=inspectorState.has(key)?inspectorState.get(key):!(window.matchMedia?.('(max-width:700px)').matches??true);
    function update(){
      const selected=root.dataset.inspectorDetail||[...content.querySelectorAll('input[type=search],select')].filter(el=>el.tagName==='SELECT'?el.selectedIndex>0:el.value.trim()).map(el=>el.tagName==='SELECT'?el.options[el.selectedIndex]?.text:el.value.trim()).filter(Boolean).join(' / ');
      button.textContent=label+(selected?' · '+selected:'');
      button.setAttribute('aria-expanded',String(expanded));
      root.dataset.compactCollapsed=String(!expanded);content.hidden=!expanded;
    }
    root.addEventListener('spike:inspector-label',update);content.addEventListener('change',update);content.addEventListener('input',update);
    button.addEventListener('click',()=>{expanded=!expanded;inspectorState.set(key,expanded);update();if(expanded)window.SpikeMotion?.enter(content);});update();
  }
  function disclosure(root){
    if(bound.has(root))return;
    bound.add(root);
    const key=root.dataset.workDisclosure;
    root.open=disclosures.get(key)||false;
    root.addEventListener('toggle',()=>disclosures.set(key,root.open));
  }
  function enhance(scope){
    if(scope.matches?.('[data-work-tabs]'))mount(scope);
    scope.querySelectorAll?.('[data-work-tabs]').forEach(mount);
    for(const [selector,fn] of [['.work-inspector',inspector],['[data-work-disclosure]',disclosure]]){
      if(scope.matches?.(selector))fn(scope);
      scope.querySelectorAll?.(selector).forEach(fn);
    }
  }
  enhance(document);
  new MutationObserver(records=>{for(const record of records)for(const node of record.addedNodes)if(node.nodeType===1&&(node.matches?.('[data-work-tabs],.work-inspector,[data-work-disclosure]')||node.querySelector?.('[data-work-tabs],.work-inspector,[data-work-disclosure]')))enhance(node);}).observe(document.getElementById('main'),{childList:true,subtree:true});
  window.SpikePanels={mount};
})();
