import test from 'node:test';
import assert from 'node:assert/strict';
import { imageError } from '../src/shared/image-error.js';
test('image failure retains the actual server diagnostic and never reads a successful binary', async () => {
 const original = globalThis.fetch;
 try {
  globalThis.fetch = async () => new Response(JSON.stringify({detail: 'Fichier absent du volume OCR.'}), {status:404,headers:{'content-type':'application/json'}});
  assert.equal(await imageError('/image'), 'Fichier absent du volume OCR.');
  let cancelled=false;
  globalThis.fetch=async()=>({ok:true,body:{cancel:async()=>{cancelled=true;}}});
  assert.match(await imageError('/image'), /format/);
  assert.ok(cancelled);
  globalThis.fetch=async()=>{throw Error('offline')};
  assert.match(await imageError('/image'), /injoignable/);
 } finally {globalThis.fetch=original;}
});
