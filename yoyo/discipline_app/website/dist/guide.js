(() => {
  "use strict";
  const $ = id => document.getElementById(id);
  const element = (name, text, cls) => {
    const el = document.createElement(name);
    if (text != null) el.textContent = text;
    if (cls) el.className = cls;
    return el;
  };
  const numbered = index => String(index + 1).padStart(2, "0");
  const formatPrice = new Intl.NumberFormat("en-US", {minimumFractionDigits:1, maximumFractionDigits:1});
  const formatDate = new Intl.DateTimeFormat("zh-CN", {timeZone:"UTC",month:"2-digit",day:"2-digit",hour:"2-digit",minute:"2-digit",hourCycle:"h23"});
  const controls = (selector, key, value) => document.querySelectorAll(selector).forEach(b => b.setAttribute("aria-pressed", String(b.dataset[key] === value)));
  function read(bar) {
    if (!bar || !Number.isFinite(bar.t)) return;
    $("bar-stamp").textContent = formatDate.format(bar.t) + " UTC";
    for (const v of document.querySelectorAll("[data-quote]")) v.textContent = formatPrice.format(bar[v.dataset.quote]);
  }
  const chart = window.SpikeChart?.mount($("market-plot"),{scene:"chart",onRead:read});
  function period(value) {
    chart?.setPeriod(value);
    controls("[data-period]","period",value);
  }
  for (const b of document.querySelectorAll("[data-period]")) b.addEventListener("click", () => period(b.dataset.period));
  for (const b of document.querySelectorAll("[data-palette]")) b.addEventListener("click", () => {
    chart?.setPalette(b.dataset.palette);controls("[data-palette]","palette",b.dataset.palette);
  });
  function refreshExample() {
    const pair = chart?.getExampleTrade?.();
    if(pair?.entry && pair?.exit) $("trade-stamp").textContent =
      "示意入场 "+formatDate.format(pair.entry.t)+" → 示意出场 "+formatDate.format(pair.exit.t)+"（UTC）。点开图表，对照两个点之间的行情。";
  }
  const extent=$("chart-extent");
  function fitLevels(on){extent.setAttribute("aria-pressed",String(on));}
  extent.addEventListener("click",()=>{
    const whole=extent.getAttribute("aria-pressed")!=="true";
    fitLevels(whole);chart?.setFullFib(whole);
  });
  $("chart-reset").addEventListener("click",()=>{
    fitLevels(false);chart?.setScene("chart");chart?.reset();period("15m");chart?.setPalette("workbench");
    controls("[data-palette]","palette","workbench");$("back-from-review").hidden=true;refreshExample();
  });
  $("open-review").addEventListener("click",()=>{
    fitLevels(false);period("15m");chart?.setScene("review");refreshExample();$("back-from-review").hidden=false;
  });
  refreshExample();
  if(window.SpikeChart?.ready) window.SpikeChart.ready.then(refreshExample).catch(()=>{
    $("market-source").textContent="历史切片暂未加载，请刷新后重试。";
  });
  const catalog = window.SpikeCatalog;
  if(!catalog) return;
  function menu(entries, parent, draw, card) {
    const node=$(parent), buttons=[];
    node.replaceChildren();
    function select(i) {
      buttons.forEach((b,j) => b.setAttribute("aria-pressed",String(j===i)));
      draw(entries[i],i);
    }
    entries.forEach((item,i)=>{
      const b=element("button"); b.type="button";
      b.append(element("span",numbered(i),"index-num"),element("span",item.title,"index-name"));
      if(card) b.append(element("small",item.category));
      b.append(element("span","↗","index-arrow"));
      b.addEventListener("click",()=>select(i));node.append(b);buttons.push(b);
    });
    if(entries.length) select(0);
  }
  menu(catalog.monitors,"monitor-menu",item=>{
    const p=$("monitor-panel"), box=element("div",null,"monitor-table"),table=element("table");
    const thead=table.createTHead(),head=thead.insertRow();
    for (const word of item.columns){const h=element("th",word);h.scope="col";head.append(h);}
    const body=table.createTBody();
    for (const row of item.rows){const tr=body.insertRow();for (const word of row) tr.append(element("td",word));}
    box.append(table);
    p.replaceChildren(element("span",item.kicker,"micro"),element("h3",item.title),element("p",item.summary), box, element("small",item.note,"panel-note"));
  },false);
  const archive=window.SpikeExcerpts;
  const utc = (ms) => new Date(ms).toISOString().slice(0,16).replace("T"," ");
  function renderBook(item) {
    const i=catalog.books.indexOf(item);
    const p=$("book-panel"), lists=[];
    for (const [title,lines] of [["观察规则",item.rule],["阅读边界",item.boundary]]) {
      const section=element("section"), ul=element("ul");
      for(const line of lines)ul.append(element("li",line));
      section.append(element("h4",title),ul);lists.push(section);
    }
    const history=archive?.books[item.id];
    if(history?.rows.length) {
      const archiveDetails=element("details",null,"excerpt");
      archiveDetails.append(element("summary","历史开单摘录 · 账本共 "+history.count.toLocaleString("zh-CN")+" 笔"));
      const table=element("table"), row=table.createTHead().insertRow();
      for(const word of ["标的 / 方向","入场 UTC","出场 UTC"]){const h=element("th",word);h.scope="col";row.append(h);}
      const body=table.createTBody();
      for(const [coin,start,end,side] of history.rows){
        const tr=body.insertRow();
        for(const word of [coin.replace(/USDT$/,"")+" · "+side,utc(start),utc(end)]) tr.append(element("td",word));
      }
      archiveDetails.append(table,element("small","来自该版本冻结的回测账本，不是客户或前向账户成交。"));lists.push(archiveDetails);
    }
    p.replaceChildren(element("span","NOTE "+numbered(i)+" / "+item.category,"micro"),element("h3",item.title),element("p",item.summary), ...lists);
  }
  function findBooks() {
    const query=$("book-find").value.trim().toLowerCase();
    const found=catalog.books.filter(x=>[x.title,x.id,x.category,x.summary,...x.rule].join(" ").toLowerCase().includes(query));
    $("book-total").textContent=found.length+" / "+catalog.books.length+" 本";
    menu(found,"book-menu",renderBook,true);
    if(!found.length) $("book-panel").replaceChildren(element("h3","没有找到匹配的小册"),element("p","试试「插针」「云带」或「前向模拟」。"));
  }
  $("book-find").addEventListener("input",findBooks);findBooks();
  if(archive?.charts.length) {
    $("archive-count").textContent=archive.archiveCount.toLocaleString("zh-CN");
    const btns=archive.charts.map((chart,i)=>{
      const btn=element("button",numbered(i)+"  "+chart.symbol);btn.type="button";
      btn.addEventListener("click",()=>showChart(i));$("archive-tabs").append(btn);return btn;
    });
    function showChart(i){
      const c=archive.charts[i],url="assets/"+c.file;
      $("archive-image").src=url;$("archive-image").alt=c.symbol+" · "+c.side+" · 12H 原始历史回测图";
      $("archive-zoom").href=url;
      $("archive-caption").textContent=c.symbol+" · "+c.side+" · 入场 "+utc(c.entry)+" UTC · 点图放大 ↗";
      btns.forEach((b,k)=>b.setAttribute("aria-pressed",String(k===i)));
    }
    showChart(0);
  }
})();
