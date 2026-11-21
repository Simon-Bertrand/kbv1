import test from 'node:test';
import assert from 'node:assert/strict';
import {ChatRepository} from '../../store/chat/repository.ts';

test('local chat expires after thirty days and keeps legacy history readable',()=>{
 const values=new Map();globalThis.localStorage={getItem:k=>values.get(k)??null,setItem:(k,v)=>values.set(k,v),removeItem:k=>values.delete(k)};
 try {
  const repo=new ChatRepository('retention-test');const conversation={id:'c',title:'Test',messages:[]};
  repo.save('c',[conversation]);assert.equal(repo.load().activeId,'c');
  const value=JSON.parse(values.get('retention-test'));value.savedAt=Date.now()-31*86400000;
  values.set('retention-test',JSON.stringify(value));assert.equal(repo.load(),null);assert.equal(values.has('retention-test'),false);
  delete value.savedAt;values.set('retention-test',JSON.stringify(value));assert.equal(repo.load().activeId,'c');
 } finally{delete globalThis.localStorage;}
});
