/* Illustrated study renderer. Figures use explicit editorial data or cited
 * public-domain scans; no figure is evidence of a trading edge. All prose is
 * escaped. Reveal controls derive geometry from visible observations only.
 */
(() => {
  'use strict';
  const esc = v => String(v ?? '').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const text = v => typeof v === 'string' && v.trim().length > 0;
  const strings = v => Array.isArray(v) && v.length > 0 && v.every(text);
  const finite = v => typeof v === 'number' && Number.isFinite(v);
  const bases = {editorial_synthetic:'编者构造数据 · 非实际行情',editorial_schematic:'编者概念图',editorial_calculation:'编者演算',source_scan:'原书扫描图',source_data:'按原书数据重绘'};
  const imagePath = p => typeof p==='string' && /^\/static\/trading-book-figures\/[a-z0-9-]+\.png$/.test(p);
  const sourceURL = value => {try {const u=new URL(value);return u.protocol==='https:'&&!u.username&&!u.password?u.href:'';}catch{return '';}};
  function validateFigure(f) {
    if (!f || !text(f.title) || !text(f.caption) || !Object.hasOwn(bases,f.basis)) throw Error('图表缺少来源或说明');
    if (f.reveal_from!==undefined && (!['line','candles'].includes(f.type)||!Number.isInteger(f.reveal_from)||f.reveal_from<1||f.reveal_from>(f.labels?.length||0))) throw Error('图表揭示位置不正确');
    if (f.type==='flow' && Array.isArray(f.nodes) && f.nodes.length>=2 && f.nodes.every(n=>text(n.label)&&text(n.detail))) return;
    if (f.type==='table' && strings(f.columns) && Array.isArray(f.rows) && f.rows.length && f.rows.every(r=>Array.isArray(r)&&r.length===f.columns.length&&r.every(v=>typeof v==='string'||finite(v)))) return;
    if (f.type==='image' && imagePath(f.path) && f.basis==='source_scan' && Number.isInteger(f.pdf_page)&&f.pdf_page>0) return;
    if (f.type==='footprint' && Array.isArray(f.rows)&&f.rows.length>1&&f.rows.every(r=>finite(r.price)&&finite(r.bid)&&finite(r.ask)&&r.bid>=0&&r.ask>=0)) return;
    if (f.type==='line' && strings(f.labels) && f.labels.length>1 && text(f.y_label) && Array.isArray(f.series)&&f.series.length>0&&f.series.every(s=>text(s.name)&&Array.isArray(s.values)&&s.values.length===f.labels.length&&s.values.every(finite))) return;
    if (f.type==='candles' && strings(f.labels)&&Array.isArray(f.bars)&&f.bars.length===f.labels.length&&f.bars.every(b=>[b.open,b.high,b.low,b.close].every(finite)&&b.low<=Math.min(b.open,b.close)&&b.high>=Math.max(b.open,b.close))) return;
    throw Error('图表数据不正确');
  }
  function validate(study,book) {
    if (!study || study.book_id!==book.id || !study.overview || !text(study.overview.question)||!text(study.overview.thesis)||!strings(study.overview.reading_path)||!strings(study.overview.limits)||!Array.isArray(study.units)||!study.units.length) throw Error('研读单元数据不完整');
    const ids=new Set(),covered=new Set(),chapters=new Set(book.chapters.map(c=>c.id));
    for(const u of study.units) {
      if(!/^study-[a-z0-9-]+$/.test(u.id)||ids.has(u.id)||!text(u.title)||!text(u.question)||!strings(u.chapter_ids)||u.chapter_ids.some(c=>!chapters.has(c))||!Array.isArray(u.sections)||u.sections.length<4||!u.sections.every(s=>text(s.heading)&&strings(s.paragraphs))||!Array.isArray(u.figures)||u.figures.length<2||!Array.isArray(u.exercises)||u.exercises.length<2||!u.exercises.every(e=>text(e.prompt)&&text(e.answer))||!strings(u.takeaways))throw Error('研读单元缺少解释、图表或练习');
      ids.add(u.id);u.chapter_ids.forEach(c=>{if(covered.has(c))throw Error('原章节被重复映射');covered.add(c);});u.figures.forEach(validateFigure);
    }
    if(covered.size!==chapters.size)throw Error('研读单元没有覆盖原章节');
    if(!Array.isArray(study.terms)||!study.terms.every(t=>text(t.term)&&text(t.meaning)&&chapters.has(t.source_chapter))||!Array.isArray(study.synthesis)||!study.synthesis.every(s=>text(s.heading)&&strings(s.paragraphs)))throw Error('全书综合或术语不完整');
    return study;
  }
  const fmt = n => Number(n.toFixed(4)).toLocaleString('zh-CN',{maximumFractionDigits:4});
  function table(columns,rows) {
    return `<div class="bk-table-scroll" tabindex="0" role="region" aria-label="可横向滚动的数据表"><table class="bk-data-table"><thead><tr>${columns.map(c=>`<th scope="col">${esc(c)}</th>`).join('')}</tr></thead><tbody>${rows.map(r=>`<tr>${r.map(v=>`<td>${esc(v)}</td>`).join('')}</tr>`).join('')}</tbody></table></div>`;
  }
  function chart(f,count) {
    const n=Math.max(1,Math.min(count,f.labels.length)),W=680,H=280,L=74,R=22,T=22,B=54;
    const series=f.type==='line'?f.series.map(s=>({...s,values:s.values.slice(0,n)})):[];
    const bars=f.type==='candles'?f.bars.slice(0,n):[];
    const values=bars.length?bars.flatMap(b=>[b.low,b.high]):series.flatMap(s=>s.values);
    const lo=Math.min(...values),hi=Math.max(...values),pad=(hi-lo||Math.abs(hi)*.05||1)*.14,min=lo-pad,max=hi+pad;
    const y=v=>T+(max-v)/(max-min)*(H-T-B),x=i=>L+(i+.5)*(W-L-R)/n;
    const marks=[];
    for(let i=0;i<4;i++){const v=min+(max-min)*i/3,Y=y(v),precision=Math.min(6,Math.max(0,1-Math.floor(Math.log10((max-min)/3))));marks.push(`<line class="bk-gridline" x1="${L}" x2="${W-R}" y1="${Y}" y2="${Y}"/><text x="${L-10}" y="${Y+5}" text-anchor="end">${esc(Number(v.toFixed(precision)).toLocaleString('zh-CN',{maximumFractionDigits:precision}))}</text>`);}
    const step=Math.max(1,Math.ceil(n/6));
    for(let i=0;i<n;i++)if(i%step===0||i===n-1)marks.push(`<text x="${x(i)}" y="${H-B+26}" text-anchor="middle">${esc(f.labels[i].length>8?`时点 ${i+1}`:f.labels[i])}</text>`);
    if(bars.length){const width=Math.min(35,(W-L-R)/n*.56);bars.forEach((b,i)=>{const cls=b.close>=b.open?'bk-up':'bk-down';marks.push(`<line class="${cls}" x1="${x(i)}" x2="${x(i)}" y1="${y(b.high)}" y2="${y(b.low)}"/><rect class="${cls}" x="${x(i)-width/2}" y="${y(Math.max(b.open,b.close))}" width="${width}" height="${Math.max(1,Math.abs(y(b.open)-y(b.close)))}"/>`);});}
    else series.forEach((s,k)=>{marks.push(`<polyline class="bk-series bk-series-${k%4}" points="${s.values.map((v,i)=>`${x(i)},${y(v)}`).join(' ')}"/>`);s.values.forEach((v,i)=>marks.push(`<circle class="bk-series-dot bk-series-${k%4}" cx="${x(i)}" cy="${y(v)}" r="3"/>`));});
    const legend=series.length?`<div class="bk-legend">${series.map((s,i)=>`<span class="bk-series-${i%4}"><i></i>${esc(s.name)}</span>`).join('')}</div>`:'<p class="bk-count">绿色：收盘 ≥ 开盘；橙色：收盘 &lt; 开盘。细线表示最高与最低。</p>';
    const rows=f.type==='line'?f.labels.slice(0,n).map((l,i)=>[l,...series.map(s=>fmt(s.values[i]))]):bars.map((b,i)=>[f.labels[i],...['open','high','low','close'].map(k=>fmt(b[k]))]);
    return `${legend}<p class="bk-axis-title">${esc(f.y_label||'价格（教学单位）')}</p><div class="bk-chart-scroll" tabindex="0" role="region" aria-label="图表，可横向滚动"><svg class="bk-plot" viewBox="0 0 ${W} ${H}" role="img" aria-label="${esc(f.title)}，当前 ${n} 个时点"><title>${esc(f.title)}</title>${marks.join('')}</svg></div><details class="bk-chart-values"><summary>查看当前数据与单位</summary>${table(['时点',...(f.type==='line'?series.map(s=>s.name):['开','高','低','收'])],rows)}</details>`;
  }
  function figure(f,index=0) {
    let body='';
    if(f.type==='flow')body=`<ol class="bk-flow">${f.nodes.map(n=>`<li><strong>${esc(n.label)}</strong><p>${esc(n.detail)}</p></li>`).join('')}</ol>`;
    if(f.type==='table')body=table(f.columns,f.rows);
    if(f.type==='image')body=`<a href="${esc(f.path)}" target="_blank" rel="noopener"><img class="bk-source-image" src="${esc(f.path)}" alt="${esc(f.title)}；${esc(f.caption)}" loading="lazy"></a><p class="bk-count">PDF 第 ${f.pdf_page} 页 · 点击放大核对</p>`;
    if(f.type==='footprint'){
      const totalBid=f.rows.reduce((a,r)=>a+r.bid,0),totalAsk=f.rows.reduce((a,r)=>a+r.ask,0),max=Math.max(1,...f.rows.map(r=>r.bid+r.ask));
      body=`<div class="bk-footprint"><div class="bk-foot-head"><span>价格</span><span>Bid 主动卖</span><span>Ask 主动买</span><span>总量</span></div>${[...f.rows].sort((a,b)=>b.price-a.price).map(r=>`<div class="bk-foot-row"><strong>${fmt(r.price)}</strong><span>${fmt(r.bid)}</span><span>${fmt(r.ask)}</span><span class="bk-volume"><i style="width:${(r.bid+r.ask)/max*100}%"></i><b>${fmt(r.bid+r.ask)}</b></span></div>`).join('')}<p>Bid ${fmt(totalBid)} · Ask ${fmt(totalAsk)} · 总量 ${fmt(totalBid+totalAsk)} · Delta ${fmt(totalAsk-totalBid)}</p></div>`;
    }
    const hasChart=['line','candles'].includes(f.type),count=hasChart?Math.max(1,Math.min(f.labels.length,f.reveal_from||f.labels.length)):0;
    if(hasChart)body=`<p class="bk-chart-hint">左右滑动查看完整图表；下方可展开数值。</p><div data-chart-body>${chart(f,count)}</div><div class="bk-reveal-controls"><button class="bk-button" data-chart-step="-1"${count<=1?' disabled':''}>← 前一步</button><span role="status">已显示 ${count} / ${f.labels.length} 个时点</span><button class="bk-button" data-chart-step="1"${count===f.labels.length?' disabled':''}>下一步 →</button><button class="bk-button" data-chart-reset>从头观察</button></div>`;
    const caption=f.reveal_from?`<details><summary>观察后再看图解</summary><p>${esc(f.caption)}</p></details>`:`<p>${esc(f.caption)}</p>`;
    const refs=(f.source_refs||[]).filter(r=>sourceURL(r.url)).map(r=>`<a href="${esc(sourceURL(r.url))}" target="_blank" rel="noopener noreferrer">${r.printed_page?`原书 p.${esc(r.printed_page)} · `:''}PDF ${esc(r.pdf_page)} ↗</a>`).join(' ');
    return `<figure class="bk-figure" data-figure="${index}"${hasChart?` data-visible="${count}"`:''}><figcaption><strong>${esc(f.title)}</strong><span>${bases[f.basis]}</span></figcaption>${body}<div class="bk-figure-caption">${caption}${refs?`<div class="bk-links">${refs}</div>`:''}</div></figure>`;
  }
  function bindFigures(el,figures) {
    el.querySelectorAll('[data-figure]').forEach(node=>{
      const f=figures[Number(node.dataset.figure)];if(!['line','candles'].includes(f?.type))return;
      const setCount=value=>{const n=Math.max(1,Math.min(f.labels.length,value));node.dataset.visible=String(n);node.querySelector('[data-chart-body]').innerHTML=chart(f,n);node.querySelector('[role="status"]').textContent=`已显示 ${n} / ${f.labels.length} 个时点`;node.querySelector('[data-chart-step="-1"]').disabled=n<=1;node.querySelector('[data-chart-step="1"]').disabled=n>=f.labels.length;};
      node.querySelectorAll('[data-chart-step]').forEach(b=>b.onclick=()=>setCount(Number(node.dataset.visible)+Number(b.dataset.chartStep)));
      node.querySelector('[data-chart-reset]').onclick=()=>setCount(1);
    });
  }
  function figureMarkdown(f) {
    const result=[`#### ${f.title}`,bases[f.basis],f.caption,...(f.source_refs||[]).filter(r=>sourceURL(r.url)).map(r=>`原书${r.printed_page?` p.${r.printed_page}`:''} / [PDF ${r.pdf_page}](${sourceURL(r.url)})`)];
    let columns=[],rows=[];
    if(f.type==='image'){const origin=window.location?.origin;result.push(`![${f.title}](${origin&&/^https?:\/\//.test(origin)?new URL(f.path,origin).href:f.path})`,`原书 PDF 第 ${f.pdf_page} 页`);}
    if(f.type==='flow')result.push(...f.nodes.map((n,i)=>`${i+1}. ${n.label}：${n.detail}`));
    if(f.type==='table'){columns=f.columns;rows=f.rows;}
    if(f.type==='footprint'){columns=['价格','Bid','Ask'];rows=f.rows.map(r=>[r.price,r.bid,r.ask]);}
    if(f.type==='line'){columns=['时点',...f.series.map(s=>s.name)];rows=f.labels.map((l,i)=>[l,...f.series.map(s=>s.values[i])]);}
    if(f.type==='candles'){columns=['时点','开','高','低','收'];rows=f.labels.map((l,i)=>[l,...['open','high','low','close'].map(k=>f.bars[i][k])]);}
    if(columns.length){const row=r=>'| '+r.map(v=>String(v).replaceAll('|','\\|').replaceAll('\n',' ')).join(' | ')+' |';result.push(row(columns),row(columns.map(()=>'---')),...rows.map(row));}
    return result.join('\n\n');
  }
  function markdown(study,book) {
    return [`# ${book.title} · 深度图解`,study.overview.question,study.overview.thesis,...study.overview.reading_path.map((s,i)=>`${i+1}. ${s}`),'## 阅读边界',...study.overview.limits,
      ...study.units.flatMap(u=>[`## ${u.title}`,`原文对应：${u.chapter_ids.map(id=>{const c=book.chapters.find(c=>c.id===id);return sourceURL(c.source_url)?`[${c.source_label}](${sourceURL(c.source_url)})`:c.source_label;}).join('；')}`,u.question,...u.sections.flatMap(s=>[`### ${s.heading}`,...s.paragraphs]),...u.figures.map(figureMarkdown),...u.exercises.flatMap((e,i)=>[`### 练习 ${i+1}`,e.prompt,`解析：${e.answer}`]),...u.takeaways.map(t=>`- ${t}`)]),
      '## 全书综合',...study.synthesis.flatMap(s=>[`### ${s.heading}`,...s.paragraphs]),'## 术语',...study.terms.map(t=>`- ${t.term}：${t.meaning}（原文 ${t.source_chapter}）`)].join('\n\n');
  }
  window.SpikeBookStudy={validate,validateFigure,figure,chart,bindFigures,markdown,table,esc};
  if(typeof module!=='undefined'&&module.exports)module.exports=window.SpikeBookStudy;
})();
