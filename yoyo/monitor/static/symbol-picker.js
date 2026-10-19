/* Shared searchable coin list. Selection edits a field only; its owner keeps validation and actions. */
(() => {
  'use strict';
  const base=value=>String(value||'').toUpperCase().replace(/[-/]?USDT(?::USDT)?(?:-SWAP)?$/,'');
  function matches(items,query){const q=base(query.trim());return items.filter(r=>base(r.symbol).includes(q)).sort((a,b)=>Number(base(b.symbol)===q)-Number(base(a.symbol)===q)||Number(base(b.symbol).startsWith(q))-Number(base(a.symbol).startsWith(q))||a.symbol.localeCompare(b.symbol));}
  function selection(value,symbol,multiple=false,format='base'){
    const next=format==='swap'?symbol:format==='pair'?base(symbol)+'USDT':base(symbol);
    return multiple?value.replace(/[^,，\s]*$/,next):next;
  }
  function nextIndex(current,length,key){
    if(!length)return -1;
    if(current<0)return key==='ArrowUp'?length-1:0;
    return (current+(key==='ArrowDown'?1:-1)+length)%length;
  }
  if(typeof module!=='undefined')module.exports={base,matches,selection,nextIndex};
  if(typeof document==='undefined')return;
  const selector='#symbol-search,#watch-search,#lines-search,#candle-symbol-search,#yd-result-search,#yd-feed-search,#yd-symbol,#ys-symbol,#v130-search,#vr-trade-search,#vr-market-search,input[name="symbols"],input[name="paper_symbols"],#langlang-workspace input[name="symbol"],[data-symbol-picker]';
  const options=new WeakMap(),requests=window.SpikeRuntime?.createRequests();const catalogs=new Map(),inflight=new Map();let active=null,list=null,index=-1,shown=[],version=0;
  const esc=v=>String(v).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  function load(scope='detect'){
    const saved=catalogs.get(scope);if(saved&&Date.now()-saved.at<300000)return Promise.resolve(saved.data);
    if(!inflight.has(scope)){
      const path=scope==='detect'?'/api/yolo-detect/symbols':'/api/research/paper-desk/symbols';
      const request=(requests?requests.json(path):fetch(path).then(r=>{if(!r.ok)throw Error('币种列表暂不可用');return r.json();})).then(raw=>{
        const data=scope==='detect'?raw:{items:raw.symbols};catalogs.set(scope,{data,at:Date.now()});return data;
      }).finally(()=>inflight.delete(scope));inflight.set(scope,request);
    }
    return inflight.get(scope);
  }
  function close(){version++;if(active){active.setAttribute('aria-expanded','false');active.removeAttribute('aria-activedescendant');}list?.remove();list=null;active=null;shown=[];index=-1;}
  function place(){
    if(!active?.isConnected||!active.getClientRects().length){close();return;}
    const r=active.getBoundingClientRect(),v=window.visualViewport,bottom=(v?.offsetTop||0)+(v?.height||innerHeight),top=v?.offsetTop||0;
    const below=bottom-r.bottom-8,above=r.top-top-8,up=below<160&&above>below,height=Math.min(280,Math.max(80,up?above:below));
    Object.assign(list.style,{left:Math.max(8,Math.min(r.left,innerWidth-240))+'px',width:Math.min(Math.max(r.width,240),innerWidth-16)+'px',maxHeight:height+'px',top:(up?Math.max(top+8,r.top-height-4):r.bottom+4)+'px'});
  }
  function paint(items){
    if(!active||!list)return;
    const multi=['symbols','paper_symbols'].includes(active.name),query=multi?active.value.split(/[,，\s]/).at(-1):active.value;
    shown=matches(items,query);index=-1;active.removeAttribute('aria-activedescendant');
    list.innerHTML=(shown.length?shown.map((r,i)=>`<div role="option" id="spike-symbol-option-${i}" data-symbol-option="${i}" aria-selected="false"><strong>${esc(base(r.symbol))}</strong><span>${esc(r.name||r.symbol)}</span></div>`).join(''):'<div class="spike-symbol-empty" role="status">没有匹配币种，可修改搜索或保留输入。</div>');
    place();
  }
  async function show(input){
    if(active!==input){close();active=input;input.setAttribute('role','combobox');input.setAttribute('aria-autocomplete','list');input.setAttribute('aria-controls','spike-symbol-options');input.autocomplete='off';
      if(input.hasAttribute('list')){input.dataset.symbolList=input.getAttribute('list');input.removeAttribute('list');}
      list=document.createElement('div');list.id='spike-symbol-options';list.className='spike-symbol-options';list.setAttribute('role','listbox');list.setAttribute('aria-label','币种候选');list.setAttribute('popover','manual');
      (input.closest('dialog')||document.body).append(list);list.showPopover?.();
      list.addEventListener('pointerdown',e=>{const row=e.target.closest('[data-symbol-option]');if(row){e.preventDefault();pick(Number(row.dataset.symbolOption));}});
    }
    input.setAttribute('aria-expanded','true');const token=++version;
    const own=options.get(input),dl=document.getElementById(input.dataset.symbolList);
    if(own||dl){paint(own||[...dl.options].map(r=>({symbol:r.value,name:r.label})));return;}
    list.innerHTML='<div class="spike-symbol-empty" role="status">正在加载币种…</div>';place();
    try{const data=await load(/^(yd-|ys-)/.test(input.id)?'detect':'all');if(token===version)paint(data.items);}catch(e){if(token===version&&list)list.innerHTML='<div class="spike-symbol-empty" role="status">列表读取失败，仍可输入；重新展开可重试。</div>';}
  }
  function pick(i){
    if(!shown[i]||!active)return;const symbol=shown[i].symbol,input=active,multiple=['symbols','paper_symbols'].includes(input.name);
    const format=input.dataset.symbolFormat||(multiple?(input.dataset.symbolList==='research-symbol-options'||input.name==='symbols'&&input.closest('[data-pipeline-run-form]')?'pair':'swap'):'base');
    input.value=selection(input.value,shown[i].symbol,multiple,format);close();input.focus();input.dispatchEvent(new Event('input',{bubbles:true}));input.dispatchEvent(new Event('change',{bubbles:true}));close();input.dispatchEvent(new CustomEvent('symbolselect',{bubbles:true,detail:{symbol}}));
  }
  function enhance(node){
    const inputs=node.matches?.(selector)?[node]:[...(node.querySelectorAll?.(selector)||[])];
    for(const input of inputs){input.setAttribute('role','combobox');input.setAttribute('aria-autocomplete','list');if(!input.hasAttribute('aria-expanded'))input.setAttribute('aria-expanded','false');input.autocomplete='off';}
  }
  enhance(document);
  new MutationObserver(records=>{for(const r of records)for(const node of r.addedNodes)if(node.nodeType===1&&(node.matches?.(selector)||node.querySelector?.(selector)))enhance(node);}).observe(document.body,{childList:true,subtree:true});
  document.addEventListener('focusin',e=>{if(e.target.matches(selector))show(e.target);});
  document.addEventListener('click',e=>{if(e.target.matches(selector)&&active!==e.target)show(e.target);});
  document.addEventListener('input',e=>{if(e.target.matches(selector))show(e.target);});
  document.addEventListener('keydown',e=>{
    if(e.target!==active)return;
    if(e.key==='Escape'){e.preventDefault();e.stopPropagation();close();return;}
    if(e.key==='Tab'){close();return;}
    if(e.key==='Enter'&&index>=0){e.preventDefault();e.stopPropagation();pick(index);return;}
    if(['ArrowDown','ArrowUp'].includes(e.key)){e.preventDefault();e.stopPropagation();if(!shown.length)return;index=nextIndex(index,shown.length,e.key);
      list.querySelectorAll('[role=option]').forEach((r,i)=>r.setAttribute('aria-selected',String(i===index)));const row=list.children[index];active.setAttribute('aria-activedescendant',row.id);row.scrollIntoView({block:'nearest'});
    }
  },true);
  document.addEventListener('pointerdown',e=>{if(active&&e.target!==active&&!list?.contains(e.target))close();},true);
  document.addEventListener('focusout',e=>{if(e.target===active)close();});
  window.addEventListener('hashchange',close);window.addEventListener('pagehide',close);
  window.addEventListener('resize',()=>{if(list)place();});window.visualViewport?.addEventListener('resize',()=>{if(list)place();});
  document.addEventListener('scroll',e=>{if(list&&!list.contains(e.target))place();},true);
  window.SpikeSymbols={load,close,setOptions(input,rows){if(input)options.set(input,rows.map(r=>typeof r==='string'?{symbol:r}:r));}};
})();
