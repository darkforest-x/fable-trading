'use strict';
const test=require('node:test'),assert=require('node:assert/strict');
const {normalize,moved,relocate}=require('../../yoyo/monitor/static/sidebar-order.js');
const defaults={groups:['trade','study'],items:{trade:['market','signals','paper-desk'],study:['learning','knowledge']}};
test('saved menu and group order survives with new entries appended once',()=>{
 const result=normalize({groups:['study','trade'],items:{trade:['paper-desk','market'],study:['knowledge','learning']}},defaults);
 assert.deepEqual(result,{groups:['study','trade'],items:{trade:['paper-desk','market','signals'],study:['knowledge','learning']}});
});
test('cross-group preferences survive without duplicate or unknown routes',()=>{
 const result=normalize({groups:['nope','trade','trade'],items:{trade:['knowledge','signals','signals','__proto__'],study:'bad'}},defaults);
 assert.deepEqual(result,{groups:['trade','study'],items:{trade:['knowledge','signals','market','paper-desk'],study:['learning']}});
 assert.deepEqual(normalize(null,defaults),defaults);
});
test('up down and drag offsets move exactly one item and preserve boundaries',()=>{
 assert.deepEqual(moved(['a','b','c'],'c',-2),['c','a','b']);
 assert.deepEqual(moved(['a','b','c'],'a',1),['b','a','c']);
 assert.deepEqual(moved(['a','b'],'a',-1),['a','b']);
 assert.deepEqual(moved(['a','b'],'missing',1),['a','b']);
});

test('a cross-group drop moves exactly one existing menu and normalizes on reload',()=>{
 const next=relocate(defaults,'paper-desk','study',1);
 assert.deepEqual(next.items,{trade:['market','signals'],study:['learning','paper-desk','knowledge']});
 assert.deepEqual(normalize(next,defaults),next);
 assert.deepEqual(relocate(defaults,'unknown','study',1),defaults);
});
