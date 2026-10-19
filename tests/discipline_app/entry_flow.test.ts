import test from 'node:test';
import assert from 'node:assert/strict';
import {priceAtTick,parseEntryIntent,entryResolved,entryPollDelay} from '../../yoyo/discipline_app/mobile/src/entryFlow.ts';

test('latest price uses exact tick arithmetic for low-price coins and non-power-of-ten ticks',()=>{
 assert.equal(priceAtTick('0.000000123456','.00000001'),'0.00000012');
 assert.equal(priceAtTick('81073.123','0.1'),'81073.1');
 assert.equal(priceAtTick('1.239','.05'),'1.25');
 assert.equal(priceAtTick('100.000','0.01'),'100');
 for(const value of ['','NaN','1e6','-1','0'])assert.equal(priceAtTick(value,'.01'),null);
 assert.equal(priceAtTick('1','0'),null);
});
test('recovery preserves submitted direction and order type while accepting legacy records',()=>{
 const value={id:'a'.repeat(32),symbol:'ETH-USDT-SWAP',side:'short',kind:'limit'};
 assert.deepEqual(parseEntryIntent(JSON.stringify(value)),value);
 assert.deepEqual(parseEntryIntent(JSON.stringify({id:value.id,symbol:value.symbol})),{id:value.id,symbol:value.symbol});
 assert.equal(parseEntryIntent('{bad'),null);
 assert.equal(parseEntryIntent(JSON.stringify({...value,id:'bad'})),null);
});
test('faster accepted-request polling does not shorten the absent-request fencing window',()=>{
 assert.equal(entryPollDelay('processing'),1000);
 assert.equal(entryPollDelay('not_found'),3000);
 for(const status of ['processing','not_found','unknown'])assert.equal(entryResolved(status),false);
 for(const status of ['filled','pending','rejected','interrupted'])assert.equal(entryResolved(status),true);
});
