/* Public quote metadata for the patrol browser. No account or order API is used.
 * Stitch: projects/8978791379095391159/screens/d70c4b10a20f478bae161de14e5e5c49.
 */
(() => {
  'use strict';
  const valid=s=>/^[A-Z0-9]{1,32}-USDT-SWAP$/.test(String(s||''));
  function volume(value){if(value==null||value==='')return null;const n=Number(value);return Number.isFinite(n)&&n>=0?n:null;}
  function rank(row){return volume(row.volume_usdt_24h)??-1;}
  function amount(value){const n=volume(value);if(n===null)return '—';for(const [base,suffix] of [[1e9,'B'],[1e6,'M'],[1e3,'K']])if(n>=base)return (n/base).toFixed(2)+suffix;return n.toFixed(2);}
  function logo(symbol){return valid(symbol)?'https://static.okx.com/cdn/oksupport/asset/currency/icon/'+encodeURIComponent(symbol.slice(0,-10).toLowerCase())+'.png':'';}
  function mondayLink(symbol,timeframe){if(!valid(symbol))return '#monday-fib';const tf=timeframe==='1Dutc'?'1D':timeframe;return '#monday-fib?'+new URLSearchParams({symbol,timeframe:['1m','5m','15m','30m','1H','4H','1D'].includes(tf)?tf:'15m'});}
  function join(rows,quotes){return rows.map(row=>({...row,volume_usdt_24h:quotes.get(row.symbol)?.volume_usdt_24h??null}));}
  const api={volume,rank,amount,logo,mondayLink,join};
  if(typeof module!=='undefined')module.exports=api;
  if(typeof window!=='undefined')window.SpikeYoloMarket=api;
})();
