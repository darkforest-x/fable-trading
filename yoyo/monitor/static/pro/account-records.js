/* An exchange fill receipt is not a fully attributed round-trip trade. */
const number=v=>v!==null&&v!==undefined&&v!==''&&Number.isFinite(Number(v))?Number(v):null;
export function exchangeReceipt(row){
 const hedged=['long','short'].includes(row.posSide);
 const action=hedged?((row.posSide==='long')===(row.side==='buy')?'open':'close'):'';
 const pnl=number(row.pnl),fee=row.feeCcy==='USDT'?number(row.fee):null;
 return {...row,symbol:row.instId,action,price:row.avgPx,quantity:row.accFillSz||row.sz,
  status:row.state,receipt_net_usdt:pnl!==null&&fee!==null?pnl+fee:null,
  fee_currency:row.feeCcy};
}
export function paperReceipt(row){
 const gross=number(row.gross_pnl),fee=number(row.fee);
 return {...row,receipt_net_usdt:gross!==null&&fee!==null?gross-fee:null,fee_currency:'USDT'};
}
