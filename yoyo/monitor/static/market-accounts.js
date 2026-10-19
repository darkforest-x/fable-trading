/* Market-page account summaries reuse the existing GET-only account readers.
 * Accounts remain separate; signal reference R and truncated ledgers are never
 * summed into account profit. Source timestamps govern freshness independently
 * of request success and execution-worker readiness.
 * Stitch source: project 8978791379095391159, split desktop ce937c6c3f794297a3e408a802e8ddda.
 * Mobile: 5d8179264c614f16a25cd87c3b9582fa. Exports and source IDs:
 * output/stitch-market-split-20261018/source.json; prior mobile source retained.
 */
(() => {
  'use strict';
  const ACCOUNTS = [
    {id:'manual',title:'均线密集 · 手动账户',short:'手动账户',scope:'全资产折合 · USDT',url:'/api/manual-live/dashboard?view=workspace',href:'#yolo-detect?tab=account',equity:'总权益 · 折合'},
    {id:'signals',title:'信号中心 · 自动账户',short:'自动账户',scope:'USDT',url:'/api/okx-execution/status',href:'#signals?pane=account',equity:'USDT 权益'},
  ];
  const finite=x=>x!==null&&x!==undefined&&x!==''&&typeof x!=='boolean'&&Number.isFinite(Number(x));
  const num=(x,d=2)=>finite(x)?Number(x).toLocaleString('zh-CN',{minimumFractionDigits:d,maximumFractionDigits:d}):'—';
  const time=x=>finite(x)&&Number(x)>0?new Date(Number(x)).toLocaleTimeString('zh-CN',{timeZone:'Asia/Shanghai',hour12:false}):'—';
  const workerNames={running:'执行服务运行中',stopped:'执行服务已停止',not_started:'执行服务未启动',unresponsive:'执行心跳待核验',unknown:'执行状态待核验'};
  function valid(id,value) {
    if(!value?.account||!['ready','loading','stale','error','unconfigured'].includes(value.account.status))return false;
    return id==='manual'?value.account_scope==='yolo_manual':value.read_only===true&&!value.account_scope&&!!value.ledger;
  }
  function project(id,snapshot,error,now=Date.now()) {
    const denied=error==='restricted',a=denied?null:snapshot?.account;
    const data=a?.status==='unconfigured'?null:a?.data;
    const updated=finite(a?.updated_ms)&&Number(a.updated_ms)>0?Number(a.updated_ms):null;
    const stale=!!data&&(!!error||a.status!=='ready'||!updated||now-updated>60000||updated>now+5000);
    let status=denied?'账户访问未获授权':error?'读取失败 · 自动重试':
      a?.status==='unconfigured'?'账户未配置':!data?'正在读取账户':stale?'数据过时 · 待更新':'账户数据已同步';
    if(!error&&a?.status==='error')status=data?'更新失败 · 上次快照':'账户读取失败 · 自动重试';
    const stats=id==='manual'&&!denied&&snapshot?.executions?.status==='ready'?snapshot.executions.statistics:null;
    const worker=!denied&&snapshot?.ledger?.worker?.status;
    const activity=id==='manual'?'逐笔手动确认':workerNames[worker]||'执行状态待核验';
    const note=denied?'请从已有账户入口核对访问权限。':
      stats?`已结算净收益 ${num(stats.realized_net_usdt)} U · 开仓 ${num(stats.filled,0)} / 已平 ${num(stats.closed,0)} 笔${stats.unattributed?' · '+num(stats.unattributed,0)+' 笔收益待归属':''}`:
      id==='signals'?'账户实际资金，与信号参考收益分开记录。':'账户统计正在读取。';
    return {status,stale,synced:!!data&&!stale&&!error,activity,note,settlement:stats?{net:stats.realized_net_usdt,filled:stats.filled,closed:stats.closed,unattributed:stats.unattributed}:null,
      executionWarning:id==='signals'&&worker!=='running',
      updated:updated?`更新 ${time(updated)}${a.refreshing?' · 更新中':''}`:'等待首次快照',
      metrics:[data?(id==='manual'?data.total_equity_usdt:data.balance?.eq):null,data?.balance?.availEq,data?.balance?.upl,data?.position_count]};
  }
  class Feed {
    constructor(account,request,notify,{set=(...args)=>setTimeout(...args),clear=id=>clearTimeout(id)}={}) {
      Object.assign(this,{account,request,notify,set,clear,active:false,version:0,snapshot:null,error:null,flight:null});
    }
    setActive(value) {
      if(this.active===value)return;
      this.active=value;this.version++;this.clear(this.timer);this.clear(this.timeout);this.controller?.abort();this.controller=null;this.flight=null;
      if(value)this.refresh();
    }
    refresh() {
      if(!this.active||this.flight)return this.flight;
      this.clear(this.timer);
      const version=this.version,controller=this.controller=new AbortController();
      const timeout=this.timeout=this.set(()=>controller.abort(),12000);
      // Defer the read so even a synchronous request failure cannot leave flight set.
      this.flight=Promise.resolve().then(async()=>{
        try {
          const response=await this.request(this.account.url,{cache:'no-store',signal:controller.signal});
          if(version!==this.version||!this.active)return;
          if(response.status===401||response.status===403){this.snapshot=null;this.error='restricted';return;}
          if(!response.ok)throw Error('unavailable');
          const value=await response.json();
          if(version!==this.version||!this.active)return;
          if(!valid(this.account.id,value)){this.snapshot=null;throw Error('invalid account');}
          this.snapshot=value;this.error=null;
        } catch(error) {
          if(version===this.version&&this.active)this.error='unavailable';
        } finally {
          this.clear(timeout);
          if(version===this.version&&this.active){
            this.flight=null;this.controller=null;this.notify();
            this.timer=this.set(()=>this.refresh(),this.snapshot?.account?.status==='loading'?3000:15000);
          }
        }
      });
      return this.flight;
    }
  }
  if(typeof module!=='undefined')module.exports={ACCOUNTS,Feed,project,valid};
  if(typeof document==='undefined')return;
  let active=false,host=null,selected='manual',settlementOpen=false;
  const feeds=ACCOUNTS.map(a=>new Feed(a,(...args)=>fetch(...args),render));
  function selectAccount(id){
    if(!ACCOUNTS.some(a=>a.id===id))return;
    selected=id;render();
  }
  function mount(){
    const next=document.getElementById('market-account-overview');
    if(!next)return;
    if(host!==next||!next.querySelector('[data-market-account]')){
      host=next;
      host.innerHTML=`<h2 class="visually-hidden">实盘账户 · 独立核算</h2><button type="button" class="market-account-disclosure" data-account-disclosure aria-expanded="false" aria-controls="market-account-grid">实盘账户 <span>展开详情 ⌄</span></button>
        <div class="market-account-switch" role="group" aria-label="选择实盘账户">${ACCOUNTS.map(a=>`<button type="button" data-select-account="${a.id}" aria-pressed="${a.id===selected}" aria-controls="market-account-${a.id}"><span class="market-account-dot" aria-hidden="true"></span>${a.short}<small>${a.id==='manual'?'折合':'USDT'}</small></button>`).join('')}</div>
        <div class="market-account-grid" id="market-account-grid">${ACCOUNTS.map(a=>`<article class="market-account" id="market-account-${a.id}" data-market-account="${a.id}" aria-labelledby="market-account-${a.id}-title">
          <header><div class="market-account-identity"><h3 id="market-account-${a.id}-title" title="${a.title} · ${a.scope}">${a.id==='manual'?'均线 · 手动':'信号 · 自动'}</h3><span class="market-account-scope">${a.scope}</span></div><div class="market-account-actions">${a.id==='manual'?'<button type="button" id="market-account-settlement-toggle" data-toggle-settlement aria-expanded="false" aria-controls="market-account-settlement-panel">结算明细</button>':''}<a href="${a.href}" aria-label="查看${a.title}详情">详情 ↗</a></div></header>
          <dl class="market-account-metrics">${[a.equity,'可用保证金','持仓浮盈亏','真实持仓'].map((label,i)=>`<div${i===0?' class="market-account-equity"':''}><dt title="${label}" aria-label="${label}">${[a.id==='manual'?'折合USDT':'USDT权益','可用','浮盈亏','持仓'][i]}</dt><dd><span data-account-metric="${i}">—</span><small>${i===3?'个':'USDT'}</small></dd></div>`).join('')}</dl>
          <div class="market-account-state"><span data-account-worker></span><span data-account-status role="status"></span><time data-account-updated></time></div>
          ${a.id==='manual'?'<section class="market-account-settlement-panel" id="market-account-settlement-panel" role="region" aria-labelledby="market-account-settlement-toggle" hidden><div class="market-account-settlement" data-account-settlement hidden><span>已结算净收益 <b data-settlement-net>—</b><small>USDT</small></span><span>开仓 <b data-settlement-filled>—</b> / 已平 <b data-settlement-closed>—</b> 笔</span><span class="market-account-unattributed" data-settlement-unattributed></span></div><span data-account-note></span></section>':''}
        </article>`).join('')}</div>
        <button type="button" class="market-account-preview" data-other-account><span class="market-account-preview-top"><span data-preview-title></span><strong><span data-preview-equity>—</span><small>USDT</small></strong><span aria-hidden="true">→</span></span><span class="market-account-preview-state"><span data-preview-worker></span><span data-preview-status></span></span></button>`;
      host.addEventListener('click',event=>{
        const disclosure=event.target.closest('[data-account-disclosure]');
        if(disclosure){const open=disclosure.getAttribute('aria-expanded')!=='true';disclosure.setAttribute('aria-expanded',String(open));disclosure.querySelector('span').textContent=open?'收起详情 ⌃':'展开详情 ⌄';host.classList.toggle('accounts-expanded',open);return;}
        if(event.target.closest('[data-toggle-settlement]')){settlementOpen=!settlementOpen;render();return;}
        const button=event.target.closest('button[data-select-account],button[data-other-account]');
        if(!button||!host.contains(button))return;
        selectAccount(button.dataset.selectAccount||button.dataset.otherAccount);
      });
    }
    render();
  }
  function render(){
    if(!host?.isConnected)return;
    for(const feed of feeds){
      const row=host.querySelector(`[data-market-account="${feed.account.id}"]`);if(!row)continue;
      const value=project(feed.account.id,feed.snapshot,feed.error);
      const text=(selector,value)=>{const el=row.querySelector(selector);if(el.textContent!==value)el.textContent=value;};
      row.classList.toggle('is-selected',feed.account.id===selected);
      host.querySelector(`[data-select-account="${feed.account.id}"]`).setAttribute('aria-pressed',String(feed.account.id===selected));
      row.classList.toggle('is-stale',value.stale);
      text('[data-account-status]',value.status);text('[data-account-worker]',value.activity);
      const warning=value.stale||!!feed.error||['unconfigured','error'].includes(feed.snapshot?.account?.status);
      row.querySelector('[data-account-status]').classList.toggle('is-warning',warning);
      row.querySelector('[data-account-status]').classList.toggle('is-synced',value.synced);
      row.querySelector('[data-account-worker]').classList.toggle('is-warning',value.executionWarning);
      value.metrics.forEach((v,i)=>{
        text(`[data-account-metric="${i}"]`,num(v,i===3?0:2));
        const el=row.querySelector(`[data-account-metric="${i}"]`);
        el.classList.toggle('is-positive',i===2&&finite(v)&&Number(v)>0);
        el.classList.toggle('is-negative',i===2&&finite(v)&&Number(v)<0);
      });
      text('[data-account-updated]',value.updated);
      if(feed.account.id==='manual'){
        row.querySelector('[data-toggle-settlement]').setAttribute('aria-expanded',String(settlementOpen));
        row.querySelector('#market-account-settlement-panel').hidden=!settlementOpen;
        text('[data-account-note]',value.note);
        row.querySelector('[data-account-note]').hidden=!!value.settlement;
        row.querySelector('[data-account-settlement]').hidden=!value.settlement;
      }
      if(feed.account.id==='manual'&&value.settlement){
        text('[data-settlement-net]',num(value.settlement.net));
        text('[data-settlement-filled]',num(value.settlement.filled,0));
        text('[data-settlement-closed]',num(value.settlement.closed,0));
        text('[data-settlement-unattributed]',finite(value.settlement.unattributed)?`${num(value.settlement.unattributed,0)} 笔收益待归属`:'收益归属待核验');
        row.querySelector('[data-settlement-unattributed]').hidden=finite(value.settlement.unattributed)&&Number(value.settlement.unattributed)===0;
      }
      if(feed.account.id!==selected){
        const preview=host.querySelector('[data-other-account]');
        preview.dataset.otherAccount=feed.account.id;
        preview.setAttribute('aria-label',`切换到${feed.account.title}`);
        preview.setAttribute('aria-controls',`market-account-${feed.account.id}`);
        for(const [key,val] of Object.entries({title:feed.account.short,equity:num(value.metrics[0]),worker:value.activity,status:value.status})){
          const el=preview.querySelector(`[data-preview-${key}]`);if(el.textContent!==val)el.textContent=val;
        }
        preview.classList.toggle('is-stale',value.stale);
        preview.querySelector('[data-preview-status]').classList.toggle('is-warning',warning);
        preview.querySelector('[data-preview-status]').classList.toggle('is-synced',value.synced);
        preview.querySelector('[data-preview-worker]').classList.toggle('is-warning',value.executionWarning);
      }
    }
  }
  function sync(){for(const feed of feeds)feed.setActive(active&&!document.hidden);if(active)mount();}
  document.addEventListener('visibilitychange',sync);
  window.SpikeMarketAccounts={mount,setActive(value){active=value;sync();}};
})();
