"use strict";
const test=require("node:test"),assert=require("node:assert/strict"),fs=require("node:fs"),path=require("node:path"),vm=require("node:vm");
const base=path.resolve(__dirname,"../../yoyo/monitor/static");
const sandbox={window:{localStorage:{getItem(){return null;}}},module:{exports:{}},URL,Date,Set,AbortController,setTimeout,clearTimeout};
vm.runInNewContext(fs.readFileSync(path.join(base,"trading-books-study.js"),"utf8"),sandbox);
const study=sandbox.module.exports;
vm.runInNewContext(fs.readFileSync(path.join(base,"trading-books.js"),"utf8"),sandbox);
const api=sandbox.module.exports,catalog=JSON.parse(fs.readFileSync(path.join(base,"trading-books.json"),"utf8"));
const fresh=()=>({chapters:{},last:null});
const note=(text="我的笔记")=>({status:"reading",notes:text,stage:-1,next_review:""});
test("catalog distinguishes acquired originals, editorial coverage and pending books",()=>{
  assert.equal(api.validateCatalog(catalog),catalog);
  for(const b of catalog.books){if(b.rating!==null){assert.ok(b.rating>=4.1);assert.ok(b.rating_count>=1000);assert.ok(api.safeURL(b.rating_url));}else assert.equal(b.rating_count,null);assert.ok(api.safeURL(b.source_url));}
  const b=catalog.books[0];assert.equal(b.total_chapters,24);assert.equal(b.reviewed_chapters,24);
  assert.equal(catalog.books.filter(b=>b.resource_type==="book").length,19);
  assert.equal(catalog.books.filter(b=>b.resource_type==="book" && b.source_status==="full_text_available").length,4);
  const bad=JSON.parse(JSON.stringify(catalog));bad.books[0].reviewed_chapters=3;assert.throws(()=>api.validateCatalog(bad));
  bad.books[0].reviewed_chapters=24;bad.books[0].chapters.push(bad.books[0].chapters[0]);assert.throws(()=>api.validateCatalog(bad));
});
test("filter combines terms and theme; sorting does not mutate the book order",()=>{
  const before=catalog.books.map(b=>b.id).join();
  assert.equal(api.filterBooks(catalog.books,"DOUGLAS").length,1);
  assert.equal(api.filterBooks(catalog.books,"MARK DOUGLAS","交易心理")[0].id,"trading-in-the-zone");
  assert.equal(api.filterBooks(catalog.books,"Murphy","交易心理").length,0);
  assert.equal(api.filterBooks(catalog.books,"","","popular")[0].id,"reminiscences-1923");
  assert.equal(api.filterBooks(catalog.books,"","","rating")[0].id,"best-loser-wins-2022");
  assert.equal(before,catalog.books.map(b=>b.id).join());
});
test("progress merges preserve local conflicts and allow new chapters without inventing read status",()=>{
  const current={chapters:{"book:I":note()},last:"book"},incoming={chapters:{"book:I":note("远端"),"book:II":note("新章")},last:"other"};
  const merged=api.merge(current,incoming);
  assert.equal(merged.chapters["book:I"].notes,"我的笔记");assert.equal(merged.chapters["book:II"].notes,"新章");assert.equal(merged.last,"book");assert.equal(Object.keys(current.chapters).length,1);
});
test("invalid imports fail before replacing notes",()=>{
  for(const value of [null,[],{chapters:[]},{chapters:{"bad:key":{...note(),stage:99}}},{chapters:{"bad:key":{...note(),next_review:"2026-02-30"}}},{chapters:{"bad:key":{...note(),notes:[]}}},{chapters:{"bad:key":{...note(),status:"verified"}}},JSON.parse('{"chapters":{"__proto__":{}}}')])assert.throws(()=>api.normalize(value));
  assert.equal(api.normalize(fresh()).last,null);
});
test("notes are plain text and only credential-free HTTPS source links render",()=>{
  assert.equal(api.esc('<img src=x onerror="alert(1)">'),"&lt;img src=x onerror=&quot;alert(1)&quot;&gt;");
  for(const value of ["javascript:alert(1)","https://user:pass@example.com","file:///tmp/a","http://example.com"])assert.equal(api.safeURL(value),"");
  assert.equal(api.safeURL("https://www.gutenberg.org/ebooks/60979"),"https://www.gutenberg.org/ebooks/60979");
});
test("booklet exports distinguish complete and partial coverage with attribution",()=>{
  const value=api.booklet(catalog.books[0]);assert.match(value,/图解研读小册/);assert.match(value,/深度图解/);assert.match(value,/24 \/ 24/);assert.match(value,/#XXIV/);assert.match(value,/练习（编者）/);assert.doesNotMatch(value,/已完成全书通读/);
  const partial={...catalog.books[0],study:undefined,reviewed_chapters:3,chapters:catalog.books[0].chapters.slice(0,3),source_note:"仅完成前三章"};assert.match(api.booklet(partial),/小册草稿/);assert.match(api.booklet(partial),/3 \/ 24/);
  for(const b of catalog.books.filter(b=>b.source_status!=="full_text_available"))assert.equal(b.chapters.length,0);
  assert.deepEqual(catalog.books.filter(b=>b.reviewed_chapters>0).map(b=>b.reviewed_chapters),[24,12,14,42]);
});
test("review intervals use calendar days and cap at the last stage",()=>{
  assert.equal(api.nextDate(0,new Date(2026,9,15,12)),"2026-10-16");assert.equal(api.nextDate(1,new Date(2026,9,15,12)),"2026-10-18");assert.equal(api.nextDate(99,new Date(2026,9,15,12)),"2026-11-14");
});
test("original reader maps every chapter to its source without rewriting the text",()=>{
  const book=catalog.books[0],original=JSON.parse(fs.readFileSync(path.join(base,"trading-book-originals/reminiscences-1923.json"),"utf8"));
  assert.equal(api.validateOriginal(original,book),original);
  const source=fs.readFileSync(path.join(base,"trading-book-originals/reminiscences-1923.txt"),"utf8").replace(/\r\n/g,"\n");
  const headings=[...source.matchAll(/^_([IVXL]+)_\s*$/gm)];
  assert.equal(headings.length,24);
  for(let i=0;i<headings.length;i++){
    const start=headings[i].index+headings[i][0].length,end=headings[i+1]?.index??source.indexOf("Transcriber’s Notes",start);
    assert.equal(original.chapters[i].id,headings[i][1]);
    assert.equal(original.chapters[i].paragraphs.join("").replace(/\s/g,""),source.slice(start,end).replace(/\s/g,""));
  }
  assert.ok(/full project gutenberg.*license/i.test(source),"the complete source retains its Project Gutenberg license");
  for(const alter of [x=>x.chapters.pop(),x=>x.chapters[1]=x.chapters[0],x=>x.chapters[0].paragraphs=[],x=>x.source_sha256="wrong"]){const bad=JSON.parse(JSON.stringify(original));alter(bad);assert.throws(()=>api.validateOriginal(bad,book));}
});

test("specialist entries with unknown ratings remain searchable and sort behind known ratings",()=>{
  const found=api.filterBooks(catalog.books,"Wyckoff");assert.ok(found.length>=5);
  const sorted=api.filterBooks(catalog.books,"","","rating");const firstUnknown=sorted.findIndex(b=>b.rating===null);
  assert.equal(firstUnknown,6);assert.ok(sorted.slice(firstUnknown).every(b=>b.rating===null));
  assert.equal(api.filterBooks(catalog.books,"SMC","","priority","course")[0].id,"ict-official-mentorship");
  const fullBooks=api.filterBooks(catalog.books,"","","priority","book","full");assert.equal(fullBooks.length,4);
  assert.ok(api.filterBooks(catalog.books,"","","priority","book","borrow").some(b=>b.id==="market-wizards-1989"));
  const book=catalog.books.find(b=>b.id==="trading-order-flow-valtos");assert.match(api.rating(book),/评分待核对/);assert.doesNotMatch(api.rating(book),/0.00/);
});
test("acquired originals without a booklet expose the source and never claim a completed read",()=>{
  const book={...catalog.books.find(b=>b.id==="trading-order-flow-valtos"),chapters:[],total_chapters:null,reviewed_chapters:0};
  const html=api.sourcePanel(book,"original");assert.match(html,/TradingOrderFlow768.pdf/);assert.match(html,/尚未逐章/);assert.match(html,/原文与阅读入口/);assert.doesNotMatch(html,/data-books-done/);
  assert.match(api.sourcePanel(book,"book"),/原文可读，小册待整理/);
  const paid=catalog.books.find(b=>b.id==="best-loser-wins-2022");assert.equal(paid.full_text_url,undefined);assert.match(api.sourcePanel(paid,"original"),/media\/9191231/);
  const bad=JSON.parse(JSON.stringify(catalog));bad.books[6].full_text_url=null;assert.throws(()=>api.validateCatalog(bad));
});

test("public-domain PDF originals preserve source bytes and match every booklet chapter",()=>{
  const crypto=require('node:crypto');
  for(const id of ['wyckoff-tape-1910','wyckoff-invest-1922']){
    const b=catalog.books.find(b=>b.id===id),prefix=path.join(base,'trading-book-originals',id);
    assert.equal(crypto.createHash('sha256').update(fs.readFileSync(prefix+'.pdf')).digest('hex'),b.source_sha256);
    const original=JSON.parse(fs.readFileSync(prefix+'.json','utf8'));assert.equal(api.validateOriginal(original,b),original);
    const text=fs.readFileSync(prefix+'.txt','utf8');
    for(let i=0;i<b.chapters.length;i++){
      const c=b.chapters[i];assert.ok(c.pdf_page<=c.pdf_end_page && c.pdf_end_page<=b.pdf_pages);
      assert.ok(text.replace(/\s/g,'').includes(original.chapters[i].paragraphs.join('').replace(/\s/g,'')));
      assert.equal(api.readerURL(b,c),`${b.pdf_reader_url}#page=${c.pdf_page}&view=FitH`);
      assert.match(api.originalView(b,c),/正在载入完整扫描件/);
      assert.ok(c.sections.every(s=>s.paragraphs.every(p=>p.length>15)) && c.exercise && c.answer);
    }
  }
});

test("author-hosted PDF remains external and page anchors reject invalid versions or unsafe URLs",()=>{
  const b=catalog.books.find(b=>b.id==='trading-order-flow-valtos');assert.equal(b.year,2015);assert.equal(b.total_chapters,42);
  assert.equal(b.pdf_reader_url,b.full_text_url);assert.equal(b.original_path,undefined);
  assert.equal(fs.existsSync(path.join(base,'trading-book-originals',b.id+'.pdf')),false);
  assert.match(api.originalView(b,b.chapters[40]),/TradingOrderFlow768.pdf#page=140/);
  for(const page of [0,152,1.5,undefined])assert.equal(api.readerURL(b,{pdf_page:page}),'');
  for(const url of ['javascript:alert(1)','/static/other.pdf','https://evil.example/book.pdf'])assert.equal(api.readerURL({...b,pdf_reader_url:url},b.chapters[0]),'');
  const invalid=JSON.parse(JSON.stringify(catalog));invalid.books.find(x=>x.id===b.id).chapters[0].pdf_page=999;assert.throws(()=>api.validateCatalog(invalid));
});


test("PDF loading verifies content before creating a reusable reader and supports failure retry",async()=>{
  const crypto=require('node:crypto'),bytes=Buffer.from('%PDF-1.4 test fixture');
  sandbox.window.crypto=crypto.webcrypto;sandbox.Blob=Blob;sandbox.Uint8Array=Uint8Array;
  let calls=0;sandbox.fetch=async()=>{calls++;return {ok:true,headers:new Headers(),arrayBuffer:async()=>bytes.buffer.slice(bytes.byteOffset,bytes.byteOffset+bytes.byteLength)}};
  const source=catalog.books.find(b=>b.id==='wyckoff-tape-1910');
  const b={...source,id:'reader-test',pdf_reader_url:'/static/trading-book-originals/reader-test.pdf',source_sha256:crypto.createHash('sha256').update(bytes).digest('hex')};
  await api.loadPDF(b);assert.equal(calls,1);assert.match(api.originalView(b,b.chapters[0]),/src="blob:/);
  await api.loadPDF(b);assert.equal(calls,1);
  const retry={...b,id:'reader-retry',pdf_reader_url:'/static/trading-book-originals/reader-retry.pdf',source_sha256:'wrong'};
  await api.loadPDF(retry);assert.match(api.originalView(retry,retry.chapters[0]),/扫描件与已核对版本不一致/);assert.doesNotMatch(api.originalView(retry,retry.chapters[0]),/src="blob:/);
  retry.source_sha256=b.source_sha256;await api.loadPDF(retry);assert.match(api.originalView(retry,retry.chapters[0]),/src="blob:/);
});


test("a local author PDF is hash checked and displayed without uploading or changing the official source",async()=>{
  const crypto=require('node:crypto'),bytes=Buffer.from('%PDF-1.4 local-only fixture');
  const original=catalog.books.find(b=>b.id==='trading-order-flow-valtos');
  const b={...original,id:'local-author-test',source_sha256:crypto.createHash('sha256').update(bytes).digest('hex')};
  const before=JSON.stringify(b);sandbox.fetch=async()=>{throw new Error('must not use network')};
  await api.installPDF(b,bytes);assert.equal(JSON.stringify(b),before);
  const html=api.originalView(b,b.chapters[0]);assert.match(html,/src="blob:/);assert.match(html,/当前显示本地文件/);assert.match(html,/TradingOrderFlow768.pdf/);
  await assert.rejects(()=>api.installPDF(b,Buffer.from('different edition')),/版本不一致/);
  assert.match(api.originalView(b,b.chapters[0]),/src="blob:/);
});

test('deep units cover every source chapter once while preserving legacy progress keys',()=>{
  const books=catalog.books.filter(b=>b.study);assert.equal(books.length,4);
  assert.equal(books.reduce((n,b)=>n+b.study.units.length,0),31);
  for(const b of books){
    const chapters=b.study.units.flatMap(u=>u.chapter_ids);
    assert.equal(new Set(chapters).size,b.chapters.length);
    assert.deepEqual([...chapters].sort(),b.chapters.map(c=>c.id).sort());
    const oldKey=`${b.id}:${b.chapters[0].id}`,newKey=`${b.id}:${b.study.units[0].id}`;
    const merged=api.merge({chapters:{[oldKey]:note('原章节笔记')},last:b.id},{chapters:{[newKey]:note('新单元笔记')},last:b.id});
    assert.equal(merged.chapters[oldKey].notes,'原章节笔记');assert.equal(merged.chapters[newKey].notes,'新单元笔记');
    const bad=JSON.parse(JSON.stringify(b.study));bad.units[1].chapter_ids.push(bad.units[0].chapter_ids[0]);assert.throws(()=>study.validate(bad,b),/重复映射/);
    bad.units[1].chapter_ids.pop();bad.units.pop();assert.throws(()=>study.validate(bad,b),/没有覆盖/);
  }
});
test('study routes restore exact units and legacy chapters independently',()=>{
  const b=catalog.books[0],unit=b.study.units[3];
  assert.equal(api.resolveReading(b,unit.chapter_ids[0],unit.id,'study').unit,unit.id);
  assert.equal(api.resolveReading(b,'XXIV',null,'chapters').studyMode,'chapters');
  assert.equal(api.resolveReading(b,'XXIV',null,'chapters').chapter,'XXIV');
  assert.equal(api.resolveReading(b,'XXIV','bad','study').unit,b.study.units.at(-1).id);
  assert.equal(api.resolveReading(b,'bad','bad','study').chapter,'I');
});
test('all figures render with explicit provenance and only public-domain scan assets',()=>{
  let count=0;
  for(const b of catalog.books.filter(b=>b.study))for(const u of b.study.units)for(const f of u.figures){
    study.validateFigure(f);assert.ok(study.figure(f));count++;
    if(f.type==='image'){
      assert.match(b.id,/^wyckoff-/);const bytes=fs.readFileSync(path.join(base,f.path.replace('/static/','')));
      assert.equal(bytes.subarray(1,4).toString(),'PNG');assert.ok(f.source_refs[0].url.includes(`#page=${f.pdf_page}`));
    }
    assert.ok(u.exercises.every(e=>e.prompt&&e.answer));
  }
  assert.equal(count,65);
});
test('reveal charts use no future data for geometry, labels or the data table',()=>{
  const f={type:'line',title:'练习',caption:'解析',basis:'editorial_synthetic',y_label:'价格',labels:['当前','未来标签'],series:[{name:'价格',values:[10,999999]}],reveal_from:1};
  const initial=study.chart(f,1),changed=structuredClone(f);changed.series[0].values[1]=-888888;
  assert.equal(study.chart(changed,1),initial);assert.doesNotMatch(initial,/未来标签|999,999|999999/);
  assert.match(study.chart(f,2),/未来标签/);
  for(const value of [0,3,1.5,NaN])assert.throws(()=>study.validateFigure({...f,reveal_from:value}));
  const html=study.figure({...f,title:'<img onerror="alert(1)">',source_refs:[{url:'javascript:alert(1)',pdf_page:1}]});
  assert.doesNotMatch(html,/<img onerror|href="javascript/);assert.match(html,/&lt;img/);
});
test('footprint totals match its synthetic candle and source contradictions remain explicit',()=>{
  const b=catalog.books.find(b=>b.id==='trading-order-flow-valtos'),u=b.study.units.find(u=>u.id==='study-005');
  const candle=u.figures.find(f=>f.type==='candles'),foot=u.figures.find(f=>f.type==='footprint');
  assert.equal(candle.bars.length,1);study.validateFigure(candle);
  assert.equal(foot.rows.reduce((s,r)=>s+r.bid,0),670);assert.equal(foot.rows.reduce((s,r)=>s+r.ask,0),550);
  assert.match(study.figure(foot),/总量 1,220 · Delta -120/);
  const bad=structuredClone(candle);bad.bars[0].high=bad.bars[0].low;assert.throws(()=>study.validateFigure(bad));
  const exported=api.booklet(b);assert.match(exported,/0.699/);assert.match(exported,/43.1/);assert.match(exported,/250 ticks/);assert.doesNotMatch(exported,/\b1\.12\b/);
});
test('deep exports include all exercises and figure data, rejecting unsafe source links',()=>{
  for(const b of catalog.books.filter(b=>b.study)){
    const out=api.booklet(b);
    for(const u of b.study.units){assert.ok(out.includes(u.title));for(const f of u.figures)assert.ok(out.includes(f.title));for(const e of u.exercises)assert.ok(out.includes(e.answer));}
  }
  const bad=structuredClone(catalog);bad.books[0].chapters[0].source_url='javascript:alert(1)';assert.throws(()=>api.validateCatalog(bad));assert.doesNotMatch(api.booklet(bad.books[0]),/javascript:/);
  const shallow={...catalog.books[0],study:undefined};assert.equal(api.bookletLabel(shallow),'章节提纲 · 待深化');
});
