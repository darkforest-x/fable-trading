/* Render the downloadable cloud trade images with the SAME chart, trade labels and
 * summary as the interactive replay. Requires Playwright + Chrome and a running
 * loopback monitor. Usage: node scripts/export_cloud_replay.cjs URL PNG_DIR [id,...] */
const fs=require('node:fs/promises');
const path=require('node:path');
const {chromium}=require('playwright');
const [origin,directory,ids]=process.argv.slice(2);
if(!origin || !directory || !['127.0.0.1','localhost'].includes(new URL(origin).hostname))
  throw Error('Expected a loopback monitor URL and output directory');

const styles=['/static/styles.css','/vision-assets/chart-tools.css','/static/range-chart.css',
  '/static/monday-reclaim-paper.css','/static/glass-system.css','/static/strategy-backtest.css'];
const modules=['/vision-assets/chart-tools.js','/static/chart-style.js','/static/range-chart.js',
  '/static/monday-fib-chart.js','/static/reclaim-chart.js','/static/strategy-backtest.js'];
const html='<!doctype html><html lang="zh-CN" data-theme="light"><meta charset="utf-8">'+
  styles.map(url=>'<link rel="stylesheet" href="'+url+'">').join('')+
  '<body class="reclaim-paper-active"><main id="monday-reclaim-paper" class="is-backtest is-replay" style="--bt-header:0px;--bt-footer:0px"><div class="rp-wallpaper" data-rp="chart"></div><section class="bt-summary" data-rp="backtest-summary"></section></main>'+
  modules.map(url=>'<script src="'+url+'"></script>').join('')+'</body></html>';

async function run(){
  const {records}=await (await fetch(origin+'/static/strategy-booklet/cloud-v17-history/manifest.json')).json();
  const wanted=ids?new Set(ids.split(',').map(Number)):null;
  const pending=records.filter(r=>!wanted || wanted.has(Number(r.id.slice(-6))));
  if(!pending.length || (wanted && wanted.size!==pending.length))throw Error('Unknown trade IDs');
  await fs.mkdir(directory,{recursive:true});
  const browser=await chromium.launch({channel:'chrome',args:['--disable-background-timer-throttling','--disable-renderer-backgrounding','--disable-backgrounding-occluded-windows']});
  try {
    // The replay API allows two simultaneous candle reads.
    let done=0;
    async function work(){
      const page=await browser.newPage({viewport:{width:1280,height:620},deviceScaleFactor:1,locale:'zh-CN'});
      const errors=[];
      page.on('pageerror',e=>errors.push(e.message));
      await page.route(origin+'/cloud-image-export',r=>r.fulfill({contentType:'text/html',body:html}));
      await page.goto(origin+'/cloud-image-export');
      await page.evaluate(async()=>{
        await window.SpikeChartTools.library();
        const chartArea=document.querySelector('[data-rp=chart]'),summary=document.querySelector('.bt-summary');
        window.exportReplay=window.SpikeReclaimChart.create(chartArea,msg=>{window.exportMessage=msg;},null,row=>{
          summary.innerHTML=window.SpikeStrategyBacktest.summaryHTML(row,{source:'OKX',mode:'历史回测 · 12H',costNote:'已扣手续费'});
          summary.querySelector('.bt-executions')?.remove(); // A downloaded image has no expandable controls.
          summary.parentNode.style.setProperty('--bt-summary-height',summary.offsetHeight+'px');
          window.renderedTrade=row.id;
        });
      });
      for(let record; (record=pending.shift());){
        const id=Number(record.id.slice(-6));
        await page.evaluate(r=>{
          window.renderedTrade=null;
          window.exportReplay.showReplay('live-cloud-v17',{id:Number(r.id.slice(-6)),symbol:r.symbol+'USDT',
            side:r.side,entry_ms:r.entryMs,exit_ms:r.exitMs,pnl:r.netPnl,return_pct:r.netReturnPct,kind:'cloud-v17'},
            '12H',false,false);
        },record);
        try {
          await page.waitForFunction(id=>window.renderedTrade===id,id,{timeout:14000});
          await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
          if(errors.length)throw Error(errors.join('; '));
          const status=await page.evaluate(()=>({error:window.exportMessage,text:document.querySelector('.bt-summary').innerText,
            width:document.querySelector('.rp-wallpaper canvas')?.clientWidth}));
          if(status.error||!status.text.includes(record.symbol)||!(status.width>0))throw Error(JSON.stringify(status));
          await page.screenshot({path:path.join(directory,record.id+'.png'),timeout:12000});
        } catch(e) {
          throw Error(record.id+': '+e+' ('+await page.locator('body').innerText()+')');
        }
        if(++done%200===0)console.log(done+' completed');
      }
      await page.close();
    }
    await Promise.all([work(),work()]);
    console.log(done+' shared replay images');
  }finally{await browser.close();}
}
run().catch(e=>{console.error(e);process.exitCode=1;});
