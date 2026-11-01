'use strict';
const test=require('node:test'),assert=require('node:assert/strict');
const {bindOutside}=require('../../yoyo/monitor/static/yolo-preview-sheet.js');

function fixture(){
  const listeners=new Map(),calls=[];
  let opened=true,layer=null,hidden=false;
  const doc={
    addEventListener(type,fn,capture=false){const list=listeners.get(type)||[];list.push({fn,capture});listeners.set(type,list);},
    removeEventListener(type,fn,capture=false){listeners.set(type,(listeners.get(type)||[]).filter(l=>l.fn!==fn||l.capture!==capture));},
    querySelector(){return layer;}
  };
  const node=parent=>({parent,isConnected:true,contains(target){for(let n=target;n;n=n.parent)if(n===this)return true;return false;},closest(){return hidden?{}:null;}});
  const panel=node(),trigger=node(),inside=node(panel),triggerIcon=node(trigger),outside=node();
  const options={panel,triggers:[trigger],isOpen:()=>opened,dismiss:restore=>{calls.push(restore);opened=false;},env:{document:doc}};
  const binding=bindOutside(options);
  const send=(type,target=outside,extra={})=>{
    const event={target,button:0,isPrimary:true,key:'Escape',defaultPrevented:false,
      preventDefault(){this.defaultPrevented=true;},stopPropagation(){this.stopped=true;},...extra};
    for(const {fn} of [...(listeners.get(type)||[])])fn(event);
    return event;
  };
  return {binding,options,doc,panel,trigger,inside,triggerIcon,outside,calls,listeners,send,
    reopen(){opened=true;},layer(value){layer=value;},hide(value=true){hidden=value;}};
}

test('outside primary press dismisses through owner without stealing focus or swallowing chart input',()=>{
  const f=fixture(),e=f.send('pointerdown');
  assert.deepEqual(f.calls,[false]);assert.equal(e.defaultPrevented,false);assert.equal(e.stopped,undefined);
  assert.equal(f.listeners.get('pointerdown')[0].capture,true,'chart bubbling handlers cannot block dismissal');
  f.send('pointerdown');assert.equal(f.calls.length,1);f.binding.destroy();
});

test('panel controls, trigger descendants and composed-path contents stay open',()=>{
  const f=fixture();
  for(const target of [f.panel,f.inside,f.trigger,f.triggerIcon])f.send('pointerdown',target);
  f.send('pointerdown',f.outside,{composedPath:()=>[f.inside,f.panel,f.doc]});
  assert.deepEqual(f.calls,[]);f.binding.destroy();
});

test('a drag that starts inside may end outside without dismissing',()=>{
  const f=fixture();f.send('pointerdown',f.inside);f.send('pointermove');f.send('pointerup');
  assert.deepEqual(f.calls,[]);f.binding.destroy();
});

test('right clicks and secondary fingers are ignored',()=>{
  const f=fixture();f.send('pointerdown',f.outside,{button:2});f.send('pointerdown',f.outside,{isPrimary:false});
  assert.deepEqual(f.calls,[]);f.binding.destroy();
});

test('nested dialogs and popovers receive outside clicks and Escape before their parent',()=>{
  const f=fixture();
  for(const layer of [{tagName:'DIALOG',open:true},{popover:'auto'}]){
    f.layer(layer);f.send('pointerdown');const e=f.send('keydown');assert.equal(e.defaultPrevented,false);
  }
  assert.deepEqual(f.calls,[]);f.layer(null);f.send('keydown');assert.deepEqual(f.calls,[true]);f.binding.destroy();
});

test('Escape restores focus, while already handled Escape and other keys do nothing',()=>{
  const f=fixture();f.send('keydown',f.inside,{defaultPrevented:true});f.send('keydown',f.inside,{key:'Enter'});
  assert.deepEqual(f.calls,[]);const e=f.send('keydown');assert.deepEqual(f.calls,[true]);assert.equal(e.defaultPrevented,true);
  f.binding.destroy();
});

test('one Escape dismisses only one registered panel',()=>{
  const f=fixture(),other=[];
  const second=bindOutside({...f.options,isOpen:()=>true,dismiss:value=>other.push(value)});
  f.send('keydown');assert.deepEqual(f.calls,[true]);assert.deepEqual(other,[]);second.destroy();f.binding.destroy();
});

test('hidden pages, chart focus mode and detached panels do not dismiss',()=>{
  const f=fixture();f.hide();f.send('pointerdown');f.send('keydown');
  f.hide(false);f.panel.isConnected=false;f.send('pointerdown');f.send('keydown');
  assert.deepEqual(f.calls,[]);f.binding.destroy();
});

test('destroy removes all listeners and leaves owner state alone',()=>{
  const f=fixture();f.binding.destroy();f.send('pointerdown');f.send('keydown');
  assert.deepEqual(f.calls,[]);for(const list of f.listeners.values())assert.equal(list.length,0);
});
