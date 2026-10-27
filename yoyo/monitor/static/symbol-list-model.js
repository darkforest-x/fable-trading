/* Shared public instrument ordering. Missing/expired ticker values sort last. */
(() => {
  'use strict';
  const number=(q,key,now=Date.now())=>q?.expires_ms>now&&q[key]!=null&&q[key]!==''&&Number.isFinite(Number(q[key]))?Number(q[key]):null;
  function rankedSymbols(symbols,quotes,order='default',query='',now=Date.now()){
    const rows=[...new Set(symbols)].filter(s=>s.replace('-USDT-SWAP','').toLowerCase().includes(query.trim().toLowerCase()));
    if(order==='default')return rows;
    const key=order==='volume'?'volume_usdt_24h':'change_24h_percent',direction=order==='losers'?1:-1;
    return rows.sort((a,b)=>{const x=number(quotes.get(a),key,now),y=number(quotes.get(b),key,now);if(x===null||y===null)return x===y?a.localeCompare(b):x===null?1:-1;return direction*(x-y)||a.localeCompare(b);});
  }
  function validateCatalog(data){
    const rows=data?.symbols;
    if(!Array.isArray(rows)||!rows.length||!rows.every(r=>r&&/^[A-Z0-9]{1,32}-USDT-SWAP$/.test(r.symbol))||new Set(rows.map(r=>r.symbol)).size!==rows.length)throw Error('币种列表返回格式不完整');
    return rows;
  }
  const api={number,rankedSymbols,validateCatalog};
  if(typeof module!=='undefined'&&module.exports)module.exports=api;else window.SpikeSymbolList=api;
})();
