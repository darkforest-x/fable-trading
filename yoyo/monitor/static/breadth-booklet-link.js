/* The research booklet lives in the same strategies drawer as the simulation accounts. */
(() => {
  'use strict';
  const id='breadth-ema20',chapters=['overview','research','rules','forward','sources','stats','replay'];
  function read(search){
    const q=new URLSearchParams(search);if(q.get('book')!==id)return null;
    return {chapter:chapters.includes(q.get('chapter'))?q.get('chapter'):'overview',eventId:q.get('event')||null,
      scope:q.get('cost')==='funded'?'2026_actual_funded':'all_history_fee_only'};
  }
  function write(options){
    const url=new URL(location.href);
    if(options){
      url.searchParams.set('book',id);
      if(chapters.includes(options.chapter)&&options.chapter!=='overview')url.searchParams.set('chapter',options.chapter);else url.searchParams.delete('chapter');
      if(options.eventId)url.searchParams.set('event',options.eventId);else url.searchParams.delete('event');
      if(options.scope==='2026_actual_funded')url.searchParams.set('cost','funded');else url.searchParams.delete('cost');
    }else if(url.searchParams.get('book')===id){for(const k of ['book','chapter','event','cost'])url.searchParams.delete(k);}else return;
    if(url.href!==location.href)history.replaceState(history.state,'',url.href);
  }
  function legacyTarget(search){
    const old=new URLSearchParams(search),next=new URLSearchParams({book:id}),event=old.get('event');
    const chapter=event?'replay':({history:'replay',stats:'stats',rules:'rules'}[old.get('tab')]);
    if(chapter)next.set('chapter',chapter);if(event)next.set('event',event);
    if(old.get('tab')==='stats'||event?.includes('|2026-'))next.set('cost','funded');
    return '/?'+next+'#signals?pane=reclaim';
  }
  if(typeof module!=='undefined'&&module.exports){module.exports={read,legacyTarget};return;}
  if(location.pathname==='/static/strategy-booklet/breadth-ema20.html'){location.replace(legacyTarget(location.search));return;}
  window.SpikeBreadthBookRoute={read,write};
})();
