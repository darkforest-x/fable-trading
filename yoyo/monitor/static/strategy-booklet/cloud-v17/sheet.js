import {renderForward} from './book.js';
import {bookAsset} from './data.js';

// Bind the cloud data to the five existing reader sections. Their owner controls the
// modal, focus, navigation, chart lifecycle, and cancellation just as for every track.
export function cloudSheet({el, fetchJSON, isCurrent, setupHistory, esc, tableHTML, openReplay}) {
  let poll, loadForward;
  const title = text => '<div class="rs-section-title"><h2>'+esc(text)+'</h2></div>';
  const paras = lines => lines.map(line=>'<p>'+esc(line)+'</p>').join('');
  const file = name => esc(bookAsset(name));
  const scroll = node => node?.scrollIntoView({block:'start',behavior:'instant'});

  async function refresh() {
    if (loadForward) return loadForward;
    loadForward = (async()=>{
      const body = el('[data-cloud-forward]');
      try {
        const state = await fetchJSON('/static/strategy-booklet/live-cloud-v17-forward.json');
        if (isCurrent()) renderForward(body,state);
      } catch {
        if (isCurrent()) body.innerHTML='<p class="rs-warning">当前读不到模拟账户，稍后会自动重试。</p>';
      } finally {loadForward = null;}
    })();
    return loadForward;
  }

  async function show(options) {
    const [guide, overview, path] = await Promise.all([
      fetchJSON(bookAsset('guide.json')), fetchJSON(bookAsset('overview.json')),
      fetchJSON('/static/strategy-booklet/live-cloud-v17-history.json')
    ]);
    if (!isCurrent()) return;
    const metrics = overview.metrics;
    el('#rs-overview').innerHTML='<div class="rs-section-title"><h1>云带 v1.7<em>12H 收盘 · ETH/BTC 逆向</em></h1></div>'+
      '<div class="rs-facts">'+guide.facts.map(f=>'<div><small>'+esc(f.label)+'</small><b>'+esc(f.value)+' <small>'+esc(f.unit)+'</small></b></div>').join('')+'</div><p>'+esc(guide.intro)+'</p>';
    el('#rs-rules').innerHTML=title('从确认到退出')+tableHTML(guide.direction.headers,guide.direction.rows)+
      '<ol class="rs-flow">'+guide.flow.map(r=>'<li><b>'+esc(r.title)+'</b><span>'+esc(r.text)+'</span></li>').join('')+'</ol>';
    el('#rs-sources').innerHTML=title('口径与来源')+paras([guide.sources[0],guide.sources[1],guide.sources[3]])+
      '<h3>原始资料</h3><p>'+esc(overview.experiment)+' · opposite · 1.5 倍</p><p><a href="'+file('funded.csv')+'" download>资金单 CSV</a>　<a href="'+file('handbook.md')+'" download>文字小册</a>　<a href="'+file('cloud-v17-charts.zip')+'" download>全部开仓图 ZIP</a></p>';
    const research=el('#rs-research');
    research.innerHTML=title('历史回测')+'<div class="rs-breadth-tools"><button type="button" class="rs-text-button" data-cloud-replay>查看回测交易与 K 线 · '+metrics.trades.toLocaleString('en-US')+' 笔 ›</button></div><div data-cloud-history></div><p class="rs-chart-status" role="status"></p>'+
      '<h3>怎样读这三笔交易</h3>'+guide.cases.map(c=>'<details class="rs-detail-table"><summary>'+esc(c.title)+'</summary><p>'+esc(c.text)+'</p><button type="button" class="rs-text-button" data-cloud-example="'+esc(c.id)+'">查看这笔开仓图 ›</button></details>').join('')+
      '<h3>收益和回撤</h3>'+paras(guide.risk)+'<p><a href="'+file('equity.png')+'" target="_blank" rel="noopener">查看完整资金路径原图 ↗</a></p>';
    el('[data-cloud-replay]').addEventListener('click',()=>openReplay());
    for (const button of research.querySelectorAll('[data-cloud-example]')) button.addEventListener('click',()=>{
      openReplay(new URLSearchParams({trade:button.dataset.cloudExample}));
    });
    setupHistory(path);
    el('#rs-forward').innerHTML=title('前向资金')+'<div data-cloud-forward><p>正在读取账户…</p></div><button type="button" class="rs-text-button" data-cloud-refresh>刷新账户</button>'+paras([guide.sources[2]]);
    el('[data-cloud-refresh]').addEventListener('click',refresh);
    void refresh();
    poll=setInterval(()=>{if(isCurrent()&&!document.hidden)void refresh();},30000);
    if (options.chapter && options.chapter!=='overview') scroll(el('#rs-'+options.chapter));
  }
  function dispose() {clearInterval(poll);}
  return {show,dispose};
}
