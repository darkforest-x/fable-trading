/* Shared reading controls and truthful browser-local persistence feedback. */
(() => {
  'use strict';
  const states=new Map(), mounted=new Map(), disclosure=new Map();
  function feedback(key) {
    const config=mounted.get(key), status=states.get(key);if(!config)return;
    const el=config.root.querySelector('[data-reader-save]');if(!el)return;
    el.querySelector('[role=status]').textContent=status?.error || (status?.saved?'已保存在本浏览器':'笔记与进度保存在本浏览器，可导出备份。');
    el.classList.toggle('save-failed',Boolean(status?.error));
    el.querySelector('[data-save-retry]').hidden=!status?.error;
  }
  function write(key, value, blocked=false) {
    try {
      if(blocked)throw new Error('Unreadable prior data');
      window.localStorage.setItem(key,JSON.stringify(value));states.set(key,{saved:true});feedback(key);return true;
    } catch {
      states.set(key,{error:blocked?'旧进度无法读取，已停止覆盖。请导出当前编辑备份。':'保存失败，编辑仅留在当前页。请重试或导出备份，暂勿关闭页面。'});feedback(key);return false;
    }
  }
  function fold(el, selector, title, id, compact) {
    const content=el.querySelector(selector);if(!content||content.parentElement?.classList.contains('reader-disclosure'))return;
    const details=document.createElement('details');details.className='reader-disclosure';
    details.open=disclosure.has(id)?disclosure.get(id):!compact;
    const summary=document.createElement('summary');summary.textContent=title;
    content.before(details);details.append(summary,content);
    details.addEventListener('toggle',()=>disclosure.set(id,details.open));
    if(selector.includes('nav')||selector.includes('toc'))details.classList.add('reader-directory');
  }
  function mount(config) {
    const {root,view,key,chapters,current,open,reader,toc,overview}=config;if(!root)return;
    mounted.set(key,config);
    const compact=window.matchMedia('(max-width: 760px)').matches;
    if(overview)fold(root,overview,'阅读进度与资料概览',view+':overview',true);
    if(toc)fold(root,toc,'章节目录 · 点击展开',view+':toc',compact);
    const article=root.querySelector(reader);
    if(article&&chapters?.length){
      const index=chapters.findIndex(c=>c.id===current), bar=document.createElement('nav');
      bar.className='reader-chapter-bar';bar.setAttribute('aria-label','章节翻页');
      const label=document.createElement('span');label.textContent=`${index+1} / ${chapters.length} · ${chapters[index]?.title||'当前章节'}`;
      const controls=document.createElement('div');
      for(const [offset,text] of [[-1,'上一章'],[1,'下一章']]){
        const button=document.createElement('button');button.type='button';button.textContent=text;
        const chapter=chapters[index+offset];button.disabled=!chapter;
        button.onclick=()=>open(chapter.id);controls.append(button);
      }
      bar.append(label,controls);article.prepend(bar);
    }
    const status=document.createElement('div');status.className='reader-save';status.dataset.readerSave='';
    const message=document.createElement('span');message.setAttribute('role','status');
    const retry=document.createElement('button');retry.type='button';retry.dataset.saveRetry='';retry.textContent='重试保存';retry.onclick=config.save;
    const backup=document.createElement('button');backup.type='button';backup.textContent='导出进度';backup.onclick=()=>{
      const blob=new Blob([JSON.stringify(config.payload(),null,2)],{type:'application/json'}),url=URL.createObjectURL(blob),link=document.createElement('a');
      link.href=url;link.download=`${view}-progress-${new Date().toISOString().slice(0,10)}.json`;link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
    };
    status.append(message,retry);if(!config.hasExport)status.append(backup);root.prepend(status);
    if(config.blocked&&!states.has(key))states.set(key,{error:'旧进度无法读取，已停止覆盖。请导出当前编辑备份。'});
    feedback(key);
    window.SpikeNavigation?.settled();
  }
  window.SpikeReading={write,mount};
})();
