import test from 'node:test';
import assert from 'node:assert/strict';
import {ChoiceQueue} from '../src/photobook_workshop/ui/save-queue.js';
const book={bookId:'sample',optionsHash:'a'.repeat(64),pages:[{id:'one',options:[{id:'a'},{id:'b'}]},{id:'two',options:[]}]};
const state=()=>({schemaVersion:1,bookId:book.bookId,optionsHash:book.optionsHash,choices:{}});
function setup(send,stored=null,initial=state()){let snapshot;const q=new ChoiceQueue(book,initial,stored,{send,persist:s=>{snapshot=s;},changed:()=>{}});return {q,journal:()=>snapshot};}
const reply=(cmd,current={optionId:cmd.optionId,note:cmd.note,revision:cmd.baseRevision+1})=>({status:200,data:{saved:true,pageId:cmd.pageId,acceptedRevision:cmd.baseRevision+1,current}});

test('new draft survives a delayed acknowledgement and saves next',async()=>{
 let release,started;const waiting=new Promise(r=>started=r),calls=[];
 const {q}=setup(async cmd=>{calls.push(cmd);if(calls.length===1){started();await new Promise(r=>release=r);}return reply(cmd);});
 q.edit('one',{optionId:'a'});const work=q.pump();await waiting;q.edit('one',{note:'A newer thought.'});release();await work;
 assert.equal(calls.length,2);assert.equal(calls[1].baseRevision,1);assert.equal(calls[1].note,'A newer thought.');assert.equal(q.pending(),0);assert.equal(q.saved('one').note,'A newer thought.');
});

test('lost acknowledgement retries same immutable operation after reload',async()=>{
 let first;const a=setup(async cmd=>{first=structuredClone(cmd);throw Error('lost reply');});a.q.edit('one',{optionId:'a'});await a.q.pump();
 const server=state();server.choices.one={optionId:'a',note:'',revision:1};let retry;
 const b=setup(async cmd=>{retry=cmd;return reply(cmd);},a.journal(),server);await b.q.pump();assert.deepEqual(retry,first);assert.equal(b.q.pending(),0);
});

test('late replay after another tab saves preserves newer draft as explicit conflict',async()=>{
 let first;const a=setup(async cmd=>{first=structuredClone(cmd);throw Error('lost');});a.q.edit('one',{optionId:'a'});await a.q.pump();a.q.edit('one',{note:'Keep this draft.'});
 const server=state();server.choices.one={optionId:'b',note:'Other tab',revision:2};const calls=[];
 const b=setup(async cmd=>{calls.push(cmd);return calls.length===1?reply(cmd,server.choices.one):reply(cmd);},a.journal(),server);await b.q.pump();
 assert.deepEqual(calls[0],first);assert.equal(b.q.choice('one').note,'Keep this draft.');assert.equal(b.q.entries.one.conflict.revision,2);assert.equal(calls.length,1);
 b.q.resolve('one',true);await b.q.pump();assert.equal(calls[1].baseRevision,2);assert.equal(b.q.saved('one').note,'Keep this draft.');
});

test('stale tab cannot silently overwrite and choosing saved clears its draft',async()=>{
 const current={optionId:'b',note:'Saved elsewhere',revision:1};const {q}=setup(async()=>({status:409,data:{current}}));q.edit('one',{optionId:'a'});await q.pump();assert.equal(q.pending(),1);assert.equal(q.choice('one').optionId,'a');q.resolve('one',false);assert.equal(q.choice('one').optionId,'b');assert.equal(q.pending(),0);
});

test('unselected preview is never a vote and notes stay distinct',()=>{const {q}=setup(async()=>{});assert.equal(q.choice('one').optionId,null);q.edit('two',{note:'Text-page note'});assert.equal(q.export().choices.two.optionId,null);assert.equal(q.export().pending,true);});
test('blocked browser storage is visible while a confirmed server save is retained',async()=>{const q=new ChoiceQueue(book,state(),null,{send:async c=>reply(c),persist:()=>{throw Error('quota');},changed:()=>{}});q.edit('one',{optionId:'a'});assert.equal(q.durable,false);await q.pump();assert.equal(q.saved('one').optionId,'a');assert.equal(q.pending(),0);assert.equal(q.durable,false);});
test('initial version mismatch and corrupt journal fail closed',()=>{assert.throws(()=>setup(async()=>{},null,{...state(),optionsHash:'wrong'}));assert.throws(()=>setup(async()=>{},{schemaVersion:1,bookId:book.bookId,optionsHash:book.optionsHash,entries:{one:{draft:{}}}}));});

test('an unchanged focused editor keeps its displayed base through background refresh',async()=>{
 const calls=[];const {q}=setup(async c=>{calls.push(c);return reply(c);});const displayed=q.saved('one');
 const newer=state();newer.choices.one={optionId:'b',note:'Their newly saved note',revision:1};q.refresh(newer);
 q.edit('one',{note:'My text from the old editor'},displayed);await q.pump();
 assert.equal(calls.length,0);assert.equal(q.entries.one.conflict.revision,1);assert.equal(q.choice('one').note,'My text from the old editor');
});

test('out-of-order snapshots and conflicts never downgrade an observed revision',()=>{
 const {q}=setup(async()=>{});q.edit('one',{optionId:'a'});
 const third=state();third.choices.one={optionId:'b',note:'Newest',revision:3};q.refresh(third);
 const second=state();second.choices.one={optionId:'a',note:'Older',revision:2};q.refresh(second);
 assert.equal(q.saved('one').revision,3);assert.equal(q.entries.one.conflict.revision,3);q.resolve('one',false);assert.equal(q.choice('one').note,'Newest');
});

test('delayed older ACK cannot automatically continue past newer observed server state',async()=>{
 let release,started;const arrived=new Promise(r=>started=r),calls=[];const {q}=setup(async c=>{calls.push(c);if(calls.length===1){started();await new Promise(r=>release=r);}return reply(c);});
 q.edit('one',{optionId:'a'});const working=q.pump();await arrived;q.edit('one',{note:'New local draft'});
 const newer=state();newer.choices.one={optionId:'b',note:'A newer server version',revision:2};q.refresh(newer);release();await working;
 assert.equal(calls.length,1);assert.equal(q.entries.one.conflict.revision,2);assert.equal(q.choice('one').note,'New local draft');
});
