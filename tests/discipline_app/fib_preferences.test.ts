import {test} from 'node:test';
import assert from 'node:assert/strict';
import {parseFibPreferences,favoriteFibSymbol,hideFibSymbol,restoreFibSymbol,visibleFibSymbol} from '../../yoyo/discipline_app/mobile/src/fibPreferences.ts';

test('favorites and hidden symbols survive serialization without changing the source feed',()=>{
 const original=parseFibPreferences(null),symbol='BTC-USDT-SWAP',week='2026-10-19';
 const saved=parseFibPreferences(JSON.stringify(hideFibSymbol(favoriteFibSymbol(original,symbol),week,symbol)));
 assert.deepEqual(original,{favorites:[],hidden:{week:'',symbols:[]}});
 assert.deepEqual(saved.favorites,[symbol]);assert.equal(visibleFibSymbol(saved,week,symbol),false);
 assert.equal(visibleFibSymbol(saved,'2026-10-26',symbol),true);
 assert.equal(visibleFibSymbol(restoreFibSymbol(saved,week,symbol),week,symbol),true);
 assert.deepEqual(favoriteFibSymbol(saved,symbol).favorites,[]);
});
test('deleting in a new week replaces old hiding, and stale undo cannot affect it',()=>{
 let saved=hideFibSymbol(parseFibPreferences(null),'2026-10-19','BTC-USDT-SWAP');
 saved=hideFibSymbol(saved,'2026-10-26','ETH-USDT-SWAP');
 assert.equal(visibleFibSymbol(saved,'2026-10-26','BTC-USDT-SWAP'),true);
 assert.deepEqual(restoreFibSymbol(saved,'2026-10-19','ETH-USDT-SWAP'),saved);
});
test('malformed or unrelated local storage cannot inject symbols into preferences',()=>{
 for(const raw of ['broken','null','[]'])assert.deepEqual(parseFibPreferences(raw),{favorites:[],hidden:{week:'',symbols:[]}});
 assert.deepEqual(parseFibPreferences(JSON.stringify({favorites:['BTC-USDT-SWAP','BTC-USDT-SWAP','<script>',null],hidden:{week:1,symbols:'ETH-USDT-SWAP'}})),{favorites:['BTC-USDT-SWAP'],hidden:{week:'',symbols:[]}});
});
