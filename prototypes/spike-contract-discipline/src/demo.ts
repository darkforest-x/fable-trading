/** Local interaction examples only, not an exchange adapter or approved policy.
 * Uses entered prices and a synthetic total cost allowance; no market lookahead.
 */
export type Side='long'|'short';
export const EXAMPLE={equity:5000,riskFraction:.005,costRate:.002,leverage:3,maxEntries:3} as const;
export type Plan={quantity:number;notional:number;margin:number;priceRisk:number;costs:number;plannedLoss:number;budget:number};
export function sizeTicket(t:{price:number;stop:number;side:Side;fraction:number;step:number}):Plan{
 const {price,stop,side,fraction,step}=t;
 if(![price,stop,fraction,step].every(Number.isFinite)||price<=0||stop<=0||step<=0||fraction<=0||fraction>1)throw Error('请输入有效价格、止损和仓位比例');
 const distance=side==='long'?price-stop:stop-price;
 if(distance<=0)throw Error(side==='long'?'多单止损必须低于开仓价':'空单止损必须高于开仓价');
 const budget=EXAMPLE.equity*EXAMPLE.riskFraction;
 const raw=Math.min(budget/(distance+price*EXAMPLE.costRate),EXAMPLE.equity/price)*fraction;
 const quantity=Math.floor(raw/step)*step;
 if(quantity<step)throw Error('允许数量低于演示最小单位，跳过本次开仓');
 const notional=quantity*price,priceRisk=quantity*distance,costs=notional*EXAMPLE.costRate;
 return {quantity,notional,margin:notional/EXAMPLE.leverage,priceRisk,costs,plannedLoss:priceRisk+costs,budget};
}
export function entryBlock(s:{entries:number;occupied:boolean;locked:boolean;cooling:boolean}):string|null{
 if(s.locked)return '连亏停手已触发，今天只允许减仓或平仓';
 if(s.cooling)return '平仓后的冷静期内暂停开仓';
 if(s.occupied)return '已有仓位或待成交委托，不允许叠加开仓';
 if(s.entries>=EXAMPLE.maxEntries)return '今日开仓次数已用完';
 return null;
}
