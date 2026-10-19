/* Source-grounded curriculum, chronological message replay and local OHLC practice.
 * Replay scaling uses only revealed bars. Learning answers never place orders.
 * Canvas and modal APIs: developer.mozilla.org Web/API/Canvas_API and HTML dialog. */
(() => {
  "use strict";
  const core=window.SpikeRektproofCore;
  let lastChart=null;
  const link=(url,text,esc)=> /^https:\/\//.test(url||"")?`<a href="${esc(url)}" target="_blank" rel="noopener noreferrer">${esc(text)} ↗</a>`:esc(text);
  function image(path,title,c,source) {
    const original=[...(c.data.threads||[]).flatMap(t=>t.images||[]),...(c.data.channel?.messages||[]).flatMap(m=>m.images||[])].find(im=>im.path===path);
    return `<figure class="rp-original-wrap"><button type="button" class="rp-original" data-rp-image="${c.esc(path)}" data-rp-caption="${c.esc(title)}"><img src="${c.base}/${c.esc(path)}" loading="lazy" alt="${c.esc(title)}"><span>查看原图 · 可放大</span></button>${source||original?`<figcaption>${link(source||original.source_url||'https://x.com/RektProof/status/'+original.post_id,(source||original?.source_url||'').includes('discord.com/')?'该图对应频道消息（需频道权限）':'该图对应原帖',c.esc)}</figcaption>`:''}</figure>`;
  }
  function filterSources(threads,query='',topic='') {
    const terms=String(query).trim().toLowerCase().split(/\s+/).filter(Boolean);
    return threads.filter(t=>{
      if(topic && t.topic!==topic)return false;
      const text=[t.title,t.topic,t.summary,t.exercise,t.note,...(t.posts||[]).flatMap(p=>[p.id,p.published_at,p.text])].join(' ').toLowerCase();
      return terms.every(term=>text.includes(term));
    });
  }
  function gallery(c) {
    const threads=c.data.threads||[],topics=[...new Set(threads.map(t=>t.topic).filter(Boolean))];
    const posts=threads.flatMap(t=>t.posts||[]),images=threads.flatMap(t=>t.images||[]);
    return `<section class="ms-section rp-public-library"><span class="rp-kicker">原帖 → 中文导读 → 练习</span><h2>公开推文研读库</h2><p>已整理 <b>${threads.length} 组 · ${posts.length} 条推文 · ${images.length} 处原图引用</b>，按文件内容去重为 ${new Set(images.map(im=>im.sha256||im.path)).size} 幅图片。覆盖 2019–2026 年，按主题读、按发布顺序核对；这是已找到的资料，不是全账号导出。</p><p class="quiet-text">归档日期 2026-10-15。原图保留作者标注并默认大图；每组列出核验范围。逐帖链接可读原文，导读与练习是本页整理。</p><div class="rp-source-controls"><label>搜索公开资料<input id="rp-source-query" type="search" placeholder="主题、币种、年份、原文关键词…"></label><label>资料主题<select id="rp-source-topic"><option value="">全部主题</option>${topics.map(topic=>`<option value="${c.esc(topic)}">${c.esc(topic)}</option>`).join('')}</select></label></div><p id="rp-source-count" role="status" aria-live="polite">显示 ${threads.length} / ${threads.length} 组</p><div class="rp-actions"><button class="rp-btn" data-study-chapter="c22">先读：公开资料研读顺序</button><button class="rp-btn" data-study-tab="cases">进入案例拆解</button></div><p id="rp-source-empty" hidden>没有匹配资料。可清空搜索或切换主题。</p>${threads.map(t=>`<details class="rp-source-group" data-source-id="${c.esc(t.id)}"><summary><span class="rp-chip">${c.esc(t.topic||'资料')}</span> ${c.esc(t.title)} <small>${t.posts.length} 条 · ${t.images.length} 处图</small></summary><p>${c.esc(t.summary||'')}</p><p class="rp-reference"><b>读完应能回答：</b>${c.esc(t.exercise||'')}</p><div class="rp-actions">${(t.chapters||[]).map(id=>`<button class="rp-btn" data-study-chapter="${c.esc(id)}">${c.esc(c.data.chapter_titles?.[id]||id)}</button>`).join('')}</div><p>${link(t.url,'X 原帖',c.esc)} · ${link(t.mirror,t.mirror_label||'可读镜像',c.esc)}${t.verified_on_x_at?' · 已在 X 核对图片归属':t.x_verified_post_ids?.length?` · ${t.x_verified_post_ids.length}/${t.posts.length} 条已核对 X`:' · 来源为原帖嵌入数据 / 可读镜像，尚未逐条在 X 复核'}</p>${t.note?`<p class="rp-case-limit">${c.esc(t.note)}</p>`:''}${t.posts.map((p,i)=>`<article class="rp-source-post"><h3>第 ${i+1} 条 · ${c.esc((p.published_at||'').slice(0,19))} UTC</h3><p>${link(p.url,'阅读该条原文',c.esc)}${p.media_total>p.images.length?` · 本地选存 ${p.images.length} / ${p.media_total} 张图，其余见原帖`:''}</p><div class="rp-gallery">${p.images.map((im,j)=>image(im.path,`${t.title} · 第 ${i+1} 条 / 图 ${j+1}`,c,p.url)).join('')}</div>${!p.images.length?'<p class="quiet-text">这条更新未收录图片，文字请见原帖。</p>':''}</article>`).join('')}</details>`).join('')}</section>
    <section class="ms-section"><h2>其他资料入口与待补内容</h2><p>${link('https://rektproof.com/account','官方会员教育入口',c.esc)}：已确认登录入口，课程正文未登录核验。${link('https://t.me/RektProof','Telegram 频道',c.esc)}：已确认入口，本轮未取得可连续阅读的历史消息。${link('https://threadreaderapp.com/user/RektProof','Thread Reader 索引',c.esc)}：用于找回长帖，索引本身不代表全部 X 历史。</p><p>本轮未确认可归属于作者的官方 YouTube 教学系列；搜索中的同名内容与第三方课程销售页未编入作者资料。仍缺会员课程全文、频道 2026 年 7–9 月历史、其余附件以及可核对数量和费用的完整成交账单。</p>${c.data.channel?`<details class="rp-source-group"><summary>2026 频道案例附件 · ${c.data.channel.messages.reduce((n,m)=>n+m.images.length,0)} 张</summary><p class="rp-case-limit">${c.esc(c.data.channel.identity_note)} 取回日期 2026-10-15；保留社区水印与当前附件版本，没有平台编辑历史。</p>${c.data.channel.messages.filter(m=>m.images.length).map(m=>`<p>${c.esc(m.published_at)} · ${link(m.url,'频道消息',c.esc)}</p><div class="rp-gallery">${m.images.map((im,i)=>image(im.path,`频道附件 ${i+1} · ${m.published_at}`,c)).join('')}</div>`).join('')}</details>`:''}</section>`;
  }
  function learn(c) {
    const modules=c.data.modules||[];
    return `<section class="ms-section rp-study-intro"><span class="rp-kicker">REKTPROOF · 学习与练习</span><h2>从看懂形态，到独立解释一笔交易</h2><p>按背景 → 结构 → 入场 → 管理 → 失效 → 复盘学习。读完只代表阅读进度；能在遮住后续信息时写出判断，才开始检验是否理解。</p><div class="rp-actions"><button class="rp-btn primary" data-study-tab="replay">开始回放练习</button><button class="rp-btn" data-study-tab="cases">看完整案例</button><button class="rp-btn" data-study-tab="about">看作者原图</button></div></section>
    <div class="rp-course-grid">${modules.map((m,i)=>`<section class="rp-course-card"><span class="rp-kicker">阶段 ${i+1}</span><h2>${c.esc(m.title)}</h2><p>${c.esc(m.description)}</p><ul>${m.tasks.map(t=>`<li>${c.esc(t)}</li>`).join("")}</ul><div class="rp-actions">${m.chapters.map(id=>`<button class="rp-btn" data-study-chapter="${c.esc(id)}">${c.esc((c.data.chapter_titles||{})[id]||id)}</button>`).join("")}</div><label class="rp-stage-check"><input type="checkbox" data-study-stage="${c.esc(m.id)}"${c.store.learning[m.id]?" checked":""}> 我已完成本阶段练习（自评）</label></section>`).join("")}</div>
    <section class="ms-section rp-study-boundary"><h2>这套学习体系的边界</h2><p>作者公开定义、作者自报的交易、我们的整理推断和你的实际规则分别标注。会员课程未登录核验；Discord 已补回 5 条 2026 年案例的 42 个节点与 17 张频道原附件；2026 年 7–9 月及其余附件仍待补齐，页面不宣称已收集他的全部交易。</p><p>案例中的获利、亏损和 R 保留自报属性。原样周一区间机械回测仍为负；本次新增教学材料和练习，没有重新回测或改变实盘准入。</p></section>`;
  }
  function stepHTML(s,c,withReference=true) {
    return `<article class="rp-case-step"><header><span class="rp-kicker">${c.esc(s.at||"时间未确认")}</span><h3>${c.esc(s.title)}</h3></header><p>${c.esc(s.text)}</p>${s.images?.length?`<div class="rp-gallery">${s.images.map((p,i)=>image(p,`${s.title} · 原图 ${i+1}`,c,s.source)).join("")}</div>`:""}${withReference&&s.reference?`<p class="rp-reference"><b>学习检查：</b>${c.esc(s.reference)}</p>`:""}${withReference&&s.archive_id?`<p class="quiet-text">存档 ID：${c.esc(s.archive_id)} · ${c.esc(s.attachment_status||"原附件未取得")}</p>`:""}${withReference&&s.source?`<p>${link(s.source,"该条原始来源",c.esc)}</p>`:""}</article>`;
  }
  function cases(c) {
    return `<section class="ms-section"><h2>交易过程、行情跟踪与教学拆解</h2><p class="quiet-text">完整展示现有证据链，不把缺失的成交、仓位变化或费用补写成事实。按作者当时的消息保留修订、取消和亏损。账户日志包含多笔交易，行情跟踪没有完整成交记录；两者分别标注。</p></section>${(c.data.cases||[]).map(x=>`<details class="rp-case" id="rp-case-${c.esc(x.id)}"><summary><span class="rp-chip">${c.esc(x.scope||x.kind)}</span> ${c.esc(x.title)} <small>${x.steps.length} 个节点</small></summary><p>${c.esc(x.summary)}</p><p class="rp-case-limit"><b>证据边界：</b>${c.esc(x.limitations)}</p>${x.steps.map(s=>stepHTML(s,c)).join("")}<div class="rp-case-result"><h3>结果与复盘</h3><p>${c.esc(x.result)}</p></div>${x.kind==="发布时序"?`<button class="rp-btn" data-study-case="${c.esc(x.id)}">遮住后续，从第一条练习</button>`:'<p class="quiet-text">事后标注图已包含答案，适合拆解复述，不用于预测能力评分。</p>'}</details>`).join("")}`;
  }
  function replayState(c) {
    const r=c.store.replay;
    if(!["messages","candles"].includes(r.mode))r.mode="messages";
    const list=(c.data.cases||[]).filter(x=>x.kind==="发布时序");
    if(!list.some(x=>x.id===r.caseId)){r.caseId=list[0]?.id;r.step=0;r.completed=false;}
    if(!c.data.candles?.some(x=>x.id===r.marketId)){r.marketId=c.data.candles?.[0]?.id;r.cursor=c.data.candles?.[0]?.start||60;}
    const current=list.find(x=>x.id===r.caseId);
    if(current?.revision && r.caseRevision!==current.revision+':'+current.id){
      r.sourceUpdated=!!(r.step||r.completed);r.step=0;r.completed=false;
      r.caseRevision=current.revision+':'+current.id;
    }
    r.step=Math.max(0,Math.min((current?.steps.length||1)-1,Math.floor(Number(r.step)||0)));if(r.step!==(current?.steps.length||1)-1)r.completed=false;
    const market=c.data.candles?.find(x=>x.id===r.marketId);if(market)r.cursor=Math.max(market.start,Math.min(market.bars.length,Math.floor(Number(r.cursor)||market.start)));
    return r;
  }
  function draftHTML(c) {
    const r=c.store.replay;
    return `<div class="rp-answer"><label>当前判断<select id="rp-choice">${[["wait","继续等待"],["long","寻找多头机会"],["short","寻找空头机会"],["manage","管理已有仓位"],["exit","退出或取消计划"]].map(([v,l])=>`<option value="${v}"${r.choice===v?" selected":""}>${l}</option>`).join("")}</select></label><label>依据、关键价位与失效条件<textarea id="rp-reason" rows="3" maxlength="8000" placeholder="只根据当前已揭示的信息，写下为什么；哪种变化会让你认错？">${c.esc(r.reason||"")}</textarea></label><p id="rp-answer-feedback" role="status" aria-live="polite"></p></div>`;
  }
  function replay(c) {
    const r=replayState(c), list=(c.data.cases||[]).filter(x=>x.kind==="发布时序");
    const header=`<section class="ms-section"><h2>遮住后续，先写判断</h2><p>消息回放按原发布顺序揭示后续更新；逐 K 回放使用独立历史行情。先记录理由，再对照过程。练习记录只代表你的学习过程。</p><div class="rp-actions"><button class="rp-btn" data-study-mode="messages" aria-pressed="${r.mode==="messages"}">消息时序回放</button><button class="rp-btn" data-study-mode="candles" aria-pressed="${r.mode==="candles"}">真实行情逐 K 回放</button></div></section>`;
    let body="";
    if(r.mode==="messages") {
      const x=list.find(x=>x.id===r.caseId);if(!x)return header+'<p>尚无可回放消息。</p>';
      const index=Math.max(0,Math.min(x.steps.length-1,Number(r.step)||0)),s=x.steps[index];
      body=`<section class="ms-section"><label>回放案例<select id="rp-replay-case">${list.map((x,i)=>`<option value="${c.esc(x.id)}"${x.id===r.caseId?" selected":""}>案例 ${i+1} · ${c.esc(x.replay_title||"交易更新")}</option>`).join("")}</select></label><p class="quiet-text">节点 ${index+1} / ${x.steps.length} · ${r.completed?"已到结尾":"后续更新尚未显示"} · 已核对消息时间为 UTC；未核对的频道记录保留时间缺口</p>${x.revision?'<p class="quiet-text">频道附件为 2026-10-15 取回版本，按消息发布时间揭示；平台编辑历史未取得。图示工具价与实际成交分别判断。</p>':''}${r.sourceUpdated?'<p class="rp-reference">案例补入了中间消息，已回到首节点。旧判断记录与未提交草稿均保留，请核对草稿后继续。</p>':''}${stepHTML(s,c,false)}${r.completed?`<div class="rp-reference"><h3>对照与复盘</h3><ol>${x.steps.map(p=>`<li><b>${c.esc(p.title)}</b>：${c.esc(p.reference||"")}</li>`).join("")}</ol><p>${c.esc(x.result)}</p><p>${c.esc(x.limitations)}</p></div>`:draftHTML(c)}<div class="rp-actions"><button class="rp-btn primary" data-study-next${r.completed?" disabled":""}>${index===x.steps.length-1?"记录判断并查看复盘":"记录判断并揭示下一条"}</button><button class="rp-btn" data-study-restart>重新练习（保留旧记录）</button></div></section>`;
    } else {
      const x=c.data.candles?.find(x=>x.id===r.marketId);if(!x)return header+'<p>尚无本地行情练习。</p>';
      const cursor=Math.max(x.start,Math.min(x.bars.length,Number(r.cursor)||x.start)),seen=core.visibleBars(x.bars,cursor),last=seen.at(-1);
      body=`<section class="ms-section"><label>行情练习<select id="rp-replay-market">${c.data.candles.map(x=>`<option value="${c.esc(x.id)}"${x.id===r.marketId?" selected":""}>${c.esc(x.title)}</option>`).join("")}</select></label><p>${c.esc(x.prompt)}</p><p class="quiet-text">${c.esc(x.symbol)} · ${c.esc(x.timeframe)} · ${c.esc(x.source)}</p><p role="status">逐 K 可自由推进；若已写判断，推进前会先保存。<br>已揭示 ${cursor} / ${x.bars.length} 根 · 当前闭合时间 ${c.esc(last?.time||"")} UTC</p><canvas id="rp-replay-chart" role="img" aria-label="仅显示已揭示K线的历史行情，纵轴只使用当前可见数据"></canvas><div class="rp-actions"><button class="rp-btn" data-study-bars="1"${cursor>=x.bars.length?" disabled":""}>推进一根</button><button class="rp-btn" data-study-bars="12"${cursor>=x.bars.length?" disabled":""}>推进一小时</button><button class="rp-btn" data-study-restart>重新练习</button></div>${draftHTML(c)}<button class="rp-btn primary" data-study-record>保存当前判断</button><details><summary>当前已揭示的 OHLC（最近 12 根）</summary><div class="rp-table-wrap"><table class="rp-table"><thead><tr><th>闭合时间 UTC</th><th>开</th><th>高</th><th>低</th><th>收</th></tr></thead><tbody>${seen.slice(-12).map(b=>`<tr><td>${c.esc(b.time)}</td><td>${b.open}</td><td>${b.high}</td><td>${b.low}</td><td>${b.close}</td></tr>`).join("")}</tbody></table></div></details>${cursor===x.bars.length?'<p class="rp-reference">本段已走完。回看你的判断：有没有在未确认时预判？失效后是否更新计划？练习不自动计算作者胜率或收益。</p>':""}</section>`;
    }
    const attempts=Object.values(c.store.attempts).slice().reverse();
    return header+body+`<section class="ms-section"><h2>我的判断记录 · ${attempts.length} 条</h2><p class="quiet-text">旧判断保留，后续走势不改写当时的理由。结果不作盈利或掌握程度的自动评分。</p>${attempts.slice(0,40).map(a=>`<details class="rp-attempt"><summary>${c.esc(String(a.saved_at||'未记录时间').replace('T',' ').slice(0,19))} · ${c.esc(a.label)} · ${c.esc(({wait:"继续等待",long:"寻找多头机会",short:"寻找空头机会",manage:"管理已有仓位",exit:"退出或取消计划"})[a.choice]||a.choice)} · UTC</summary><p>当时截止：${c.esc(a.cutoff)}</p><p>${c.esc(a.reason)}</p></details>`).join("")||'<p class="quiet-text">还没有判断记录。写下第一条依据，再揭示后续。</p>'}</section>`;
  }
  function drawChart(canvas,x,cursor) {
    const all=core.visibleBars(x.bars,cursor),width=Math.max(290,canvas.clientWidth),height=320,ratio=Math.min(window.devicePixelRatio||1,2);
    canvas.width=width*ratio;canvas.height=height*ratio;
    const ctx=canvas.getContext("2d");if(!ctx)return;ctx.scale(ratio,ratio);
    const style=getComputedStyle(canvas),ink=style.color,grid=style.getPropertyValue('--line')||'#777';
    const bars=all.slice(width<500?-40:-90),[lo,hi]=core.domain(bars,bars.length),left=12,right=70,top=18,bottom=35,plotW=width-left-right,plotH=height-top-bottom;
    ctx.clearRect(0,0,width,height);ctx.font='11px system-ui';ctx.lineWidth=1;
    const Y=p=>top+(hi-p)/(hi-lo)*plotH;
    for(let i=0;i<=4;i++){const p=lo+(hi-lo)*i/4,y=Y(p);ctx.strokeStyle=grid;ctx.beginPath();ctx.moveTo(left,y);ctx.lineTo(width-right,y);ctx.stroke();ctx.fillStyle=ink;ctx.fillText(p.toFixed(p<10?4:1),width-right+5,y+4);}
    const gap=plotW/Math.max(1,bars.length);
    bars.forEach((b,i)=>{const x0=left+(i+.5)*gap;ctx.strokeStyle=ctx.fillStyle=b.close>=b.open?'#168363':'#c54d4d';ctx.beginPath();ctx.moveTo(x0,Y(b.high));ctx.lineTo(x0,Y(b.low));ctx.stroke();ctx.fillRect(x0-gap*.3,Math.min(Y(b.open),Y(b.close)),Math.max(1,gap*.6),Math.max(1,Math.abs(Y(b.open)-Y(b.close))));});
    ctx.fillStyle=ink;if(bars.length){ctx.fillText(bars[0].time.slice(5,16),left,height-12);ctx.textAlign='right';ctx.fillText(bars.at(-1).time.slice(5,16)+' UTC',width-right,height-12);}
  }
  function bind(root,c) {
    const r=c.store.replay;
    const query=root.querySelector('#rp-source-query'),topic=root.querySelector('#rp-source-topic');
    const filter=()=>{
      const matches=filterSources(c.data.threads||[],query?.value||'',topic?.value||''),ids=new Set(matches.map(t=>t.id));
      root.querySelectorAll('[data-source-id]').forEach(el=>{el.hidden=!ids.has(el.dataset.sourceId);});
      const count=root.querySelector('#rp-source-count');if(count)count.textContent=`显示 ${matches.length} / ${(c.data.threads||[]).length} 组`;
      const empty=root.querySelector('#rp-source-empty');if(empty)empty.hidden=matches.length>0;
    };
    query?.addEventListener('input',filter);topic?.addEventListener('change',filter);
    root.querySelectorAll('[data-study-tab]').forEach(b=>b.addEventListener('click',()=>{r.mode=r.mode||'messages';c.store.study_tab=b.dataset.studyTab;root.querySelector(`[data-rp-tab="${b.dataset.studyTab}"]`)?.click();}));
    root.querySelectorAll('[data-study-chapter]').forEach(b=>b.addEventListener('click',()=>c.open(b.dataset.studyChapter)));
    root.querySelectorAll('[data-rp-image]').forEach(b=>b.addEventListener('click',()=>c.showImage(`${c.base}/${b.dataset.rpImage}`,b.dataset.rpCaption)));
    root.querySelectorAll('[data-study-stage]').forEach(b=>b.addEventListener('change',()=>{c.store.learning[b.dataset.studyStage]=b.checked;c.save();}));
    root.querySelectorAll('[data-study-case]').forEach(b=>b.addEventListener('click',()=>{Object.assign(r,{caseId:b.dataset.studyCase,mode:'messages',step:0,completed:false,reason:'',session:c.uid()});c.save();root.querySelector('[data-rp-tab="replay"]').click();}));
    root.querySelectorAll('[data-study-mode]').forEach(b=>b.addEventListener('click',()=>{r.mode=b.dataset.studyMode;c.save();c.render();}));
    for(const id of ['rp-replay-case','rp-replay-market'])root.querySelector('#'+id)?.addEventListener('change',e=>{if(id.endsWith('case')){r.caseId=e.target.value;r.step=0;r.completed=false;}else{r.marketId=e.target.value;r.cursor=c.data.candles.find(x=>x.id===r.marketId).start;}r.reason='';r.session=c.uid();c.save();c.render();});
    root.querySelector('#rp-choice')?.addEventListener('change',e=>{r.choice=e.target.value;c.save();});
    root.querySelector('#rp-reason')?.addEventListener('input',e=>{r.reason=e.target.value;c.save();});
    function answer(){
      const feedback=root.querySelector('#rp-answer-feedback');
      if(!String(r.reason||'').trim()){if(feedback)feedback.textContent='请先写下依据和失效条件。';return false;}
      const x=r.mode==='messages'?c.data.cases.find(x=>x.id===r.caseId):c.data.candles.find(x=>x.id===r.marketId);
      const id=c.uid(),cutoff=r.mode==='messages'?x.steps[r.step||0].at:x.bars[Math.min(x.bars.length,r.cursor)-1].time;
      c.store.attempts[id]={id,session:r.session||(r.session=c.uid()),mode:r.mode,case_id:x.id,label:x.replay_title||x.title,cutoff,choice:r.choice||'wait',reason:r.reason,saved_at:new Date().toISOString()};
      if(!c.save()){delete c.store.attempts[id];if(feedback)feedback.textContent='保存失败，尚未揭示后续。请复制当前判断。';return false;}return true;
    }
    root.querySelector('[data-study-next]')?.addEventListener('click',()=>{if(!answer())return;const x=c.data.cases.find(x=>x.id===r.caseId);if((r.step||0)<x.steps.length-1)r.step=(r.step||0)+1;else r.completed=true;r.reason='';r.sourceUpdated=false;c.save();c.render();});
    root.querySelector('[data-study-record]')?.addEventListener('click',()=>{if(answer()){r.reason='';c.save();c.render();}});
    root.querySelectorAll('[data-study-bars]').forEach(b=>b.addEventListener('click',()=>{const x=c.data.candles.find(x=>x.id===r.marketId);if(String(r.reason||'').trim()){if(!answer())return;r.reason='';}r.cursor=Math.min(x.bars.length,(Number(r.cursor)||x.start)+Number(b.dataset.studyBars));c.save();c.render();}));
    root.querySelector('[data-study-restart]')?.addEventListener('click',()=>{r.step=0;r.completed=false;r.reason='';r.session=c.uid();const x=c.data.candles?.find(x=>x.id===r.marketId);r.cursor=x?.start||60;c.save();c.render();});
    const canvas=root.querySelector('#rp-replay-chart');
    if(canvas){const x=c.data.candles.find(x=>x.id===r.marketId);drawChart(canvas,x,Math.max(x.start,Math.min(x.bars.length,r.cursor||x.start)));lastChart=()=>{if(canvas.isConnected)drawChart(canvas,x,r.cursor||x.start);};}
  }
  window.addEventListener('resize',()=>lastChart?.());
  window.SpikeRektproofStudy={gallery,image,filterSources,bind,render:c=>c.tab==='learn'?learn(c):c.tab==='cases'?cases(c):replay(c)};
})();
