/** Display-only notional estimates; never used to size or submit an order. */
type PositionValue={remaining:string;quantity:string;entry:string};
type QuoteValue={last:string;time:number};
const positive=(value:string|undefined)=>value!==undefined&&Number.isFinite(Number(value))&&Number(value)>0;

export function positionAmount(position:PositionValue,quote:QuoteValue|undefined,online:boolean,serverTime:number,maxAge=15){
 const quantity=Number(position.remaining);
 const quoted=positive(quote?.last);
 const price=quoted?Number(quote!.last):positive(position.entry)?Number(position.entry):NaN;
 const amount=Number.isFinite(quantity)&&quantity>=0?quantity*price:NaN;
 const fresh=quoted&&online&&Number.isFinite(quote!.time)&&serverTime-quote!.time>=0&&serverTime-quote!.time<=maxAge;
 return {value:Number.isFinite(amount)?amount:undefined,caption:quoted?(fresh?'按最新价格估算':'按上次报价估算'):'按开仓均价估算'};
}

export function initialPositionAmount(position:PositionValue){
 const quantity=Number(position.quantity);
 const value=quantity*Number(position.entry);
 return quantity>=0&&positive(position.entry)&&Number.isFinite(value)?value:undefined;
}
