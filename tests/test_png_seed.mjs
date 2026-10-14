import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import test from 'node:test';
import {pngSeed} from '../webui/static/png-seed.mjs';

test('reads the actual seed from a valid PNG fixture',()=>{
  const bytes = readFileSync(new URL('./fixtures/seed-42.png',import.meta.url));
  assert.equal(pngSeed(bytes),'42');
  assert.equal(pngSeed(bytes.subarray(0,40)),null);
});

function metadataPNG(text) {
  const content = new TextEncoder().encode('parameters\0'+text);
  const bytes = new Uint8Array(8+12+content.length);
  bytes.set([137,80,78,71,13,10,26,10]);
  new DataView(bytes.buffer).setUint32(8,content.length);
  bytes.set(new TextEncoder().encode('tEXt'),12); bytes.set(content,16);
  return bytes;
}

test('accepts zero and ignores prompt text containing another seed',()=>{
  assert.equal(pngSeed(metadataPNG('Seed: 99\nSDCPP: {"prompt":"Seed: 123", "seed":0}')),'0');
});

test('missing, invalid or unsafe metadata does not break image display',()=>{
  for (const text of ['Seed: 42','SDCPP: invalid','SDCPP: {"seed":-1}','SDCPP: {"seed":9007199254740992}']) {
    assert.equal(pngSeed(metadataPNG(text)),null);
  }
  assert.equal(pngSeed(new Uint8Array([1,2,3])),null);
});
