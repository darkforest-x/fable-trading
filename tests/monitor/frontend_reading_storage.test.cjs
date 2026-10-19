'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
const source=fs.readFileSync(path.join(__dirname,'../../yoyo/monitor/static/reading-tools.js'),'utf8');
test('failed browser writes retain editable data, retry succeeds, unreadable prior data cannot be overwritten',()=>{
 let fail=true;const stored=new Map([['study','existing']]);const window={localStorage:{setItem(k,v){if(fail)throw Error('QuotaExceededError');stored.set(k,v);}}};
 vm.runInNewContext(source,{window});const notes={chapters:{a:{notes:'draft'}}};
 assert.equal(window.SpikeReading.write('study',notes),false);assert.equal(stored.get('study'),'existing');assert.equal(notes.chapters.a.notes,'draft');
 fail=false;assert.equal(window.SpikeReading.write('study',notes),true);assert.equal(JSON.parse(stored.get('study')).chapters.a.notes,'draft');
 assert.equal(window.SpikeReading.write('study',{chapters:{}},true),false);assert.equal(JSON.parse(stored.get('study')).chapters.a.notes,'draft');
});
