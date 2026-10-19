import test from 'node:test';
import assert from 'node:assert/strict';
import {compareContracts,rankingSnapshot,turnover,turnoverLabel,dayChange} from '../../yoyo/discipline_app/mobile/src/contractRanking.ts';
import type {Quote} from '../../yoyo/discipline_app/mobile/src/api.ts';
const quote=(volume?:string|null,change='0',time=100):Quote=>({last:'100',bid:'99',ask:'101',source:'synthetic',time,today_change:change,volume24h_usdt:volume});
test('volume sorting is numeric, unpinned and puts missing behind valid zero',()=>{
 const quotes={BTC:quote('20'),ETH:quote('100'),SOL:quote('20'),DOGE:quote('900'),ZERO:quote('0'),UNKNOWN:quote()};
 const metrics=rankingSnapshot(quotes,100,'volume');
 assert.deepEqual(Object.keys(quotes).sort((a,b)=>compareContracts(a,b,metrics,'volume')),['DOGE','ETH','BTC','SOL','ZERO','UNKNOWN']);
 for(const value of [null,undefined,'','-1','NaN','Infinity'])assert.equal(turnover(quote(value)),null);
 assert.equal(turnover(quote('0')),0);
});
test('ranking snapshots stay fixed until explicitly replaced, while gains retain daily semantics',()=>{
 const quotes={BTC:quote('100','2'),ETH:quote('200','-1')};
 const frozen=rankingSnapshot(quotes,100,'volume');quotes.BTC.volume24h_usdt='900';
 assert.equal(compareContracts('BTC','ETH',frozen,'volume')>0,true);
 assert.equal(compareContracts('BTC','ETH',rankingSnapshot(quotes,100,'volume'),'volume')<0,true);
 assert.equal(compareContracts('BTC','ETH',rankingSnapshot(quotes,100,'gainers'),'gainers')<0,true);
 assert.equal(compareContracts('BTC','ETH',rankingSnapshot(quotes,100,'losers'),'losers')>0,true);
 assert.equal(dayChange(quote('1','2',100),116),null);
 assert.equal(dayChange(quote('1','2',57599),57601),null);
});
test('compact activity labels distinguish unknown from zero and disclose estimates',()=>{
 assert.equal(turnoverLabel(null),'24h —');
 assert.equal(turnoverLabel(0),'24h ≈ 0 U');
 assert.equal(turnoverLabel(1230000000),'24h ≈ 12.3亿 U');
 assert.equal(turnoverLabel(850000),'24h ≈ 85万 U');
});
