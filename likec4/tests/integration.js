const assert = require('node:assert/strict');
const { writeFile, open, rm } = require('node:fs/promises');
const { randomUUID } = require('node:crypto');
const { createRequire } = require('node:module');
const { createRenderClient } = require('/opt/likec4/worker/render_protocol');
const { revisionOf } = require('/opt/likec4/worker/scope');
const { networkInterfaces } = require('node:os');

const source = `specification { element component }
model { api = component 'API <script>alert(1)</script>' { icon aws:lambda } }
views { view index { include * } }`;
const run = async () => {
  await require('./lifecycle.integration')();
  assert.ok(Object.values(networkInterfaces()).flat().every(address => address.internal));
  await assert.rejects(writeFile('/opt/likec4/server.js', '// overwritten'));
  const clients = Object.fromEntries(['codegen', 'png'].map(operation => [operation,
    createRenderClient({ input: `/jobs/${operation}/input`, output: `/jobs/${operation}/output` })]));
  const scope = { diagram_id: randomUUID(), revision: revisionOf(source) };
  const bundle = await clients.codegen(source, scope, 'codegen');
  assert.equal(bundle[0].name, 'likec4-webcomponent.js');
  assert.ok(bundle[0].bytes.length > 1000);
  const images = await clients.png(source, scope, 'png');
  assert.ok(images.length > 0);
  assert.ok(images.every(image => image.bytes.subarray(0, 8).toString('hex') === '89504e470d0a1a0a'));
  // Exercise the production exporter broker against the real offline worker;
  // storage and metadata stay local test doubles, with no service credentials.
  process.env.LIKEC4_RENDER_INPUT = '/jobs/png/input';
  process.env.LIKEC4_RENDER_OUTPUT = '/jobs/png/output';
  const { processJob } = require('/opt/likec4/worker/exporter');
  const brokerScope = { ...scope, storage_path: `diagrams/likec4/${scope.diagram_id}/source.c4` };
  const published = [];
  let metadata;
  const exported = await processJob(brokerScope, { localDir: '',
    storage: { read: async path => { assert.equal(path, brokerScope.storage_path); return source; },
      write: async (path, bytes) => { assert.ok(bytes.subarray(0, 8).equals(images[0].bytes.subarray(0, 8))); published.push(path); } },
    metadata: async payload => { metadata = payload; },
  });
  assert.deepEqual(exported.png_paths, published);
  assert.equal(metadata.path, brokerScope.storage_path);
  assert.equal(metadata.revision, scope.revision);
  console.log(JSON.stringify({ event: 'integration.export_broker_ok', png_count: published.length }));
  const browserRequire = createRequire(require.resolve('/opt/likec4/node_modules/likec4/package.json'));
  const { chromium } = browserRequire('playwright');
  assert.equal(browserRequire('playwright/package.json').version, '1.64.0');
  console.log(JSON.stringify({ event: 'integration.render_ok', bundle_bytes: bundle[0].bytes.length, png_count: images.length }));
  await require('./browser.integration')(chromium, bundle[0].bytes, scope, source);
  // Saturate only the disposable input tmpfs, then prove fail-closed recovery.
  const pressure = '/jobs/codegen/input/.storage-pressure';
  const handle = await open(pressure, 'wx');
  let written = 0;
  try {
    try {
      while (written < 64 * 1024 * 1024) { await handle.write(Buffer.alloc(1024 * 1024)); written += 1024 * 1024; }
      assert.fail('IPC storage was not bounded');
    } catch (error) { assert.equal(error.code, 'ENOSPC'); }
    await assert.rejects(clients.codegen(source, scope, 'codegen'), error => error.code === 'render_storage_full');
  } finally { await handle.close(); await rm(pressure, { force: true }); }
  await clients.codegen(source, scope, 'codegen');
  const large = `specification { element component }\nmodel {\n${Array.from({ length: 40 }, (_, index) => `n${index} = component 'Node ${index}'`).join('\n')}\n${Array.from({ length: 39 }, (_, index) => `n${index} -> n${index + 1}`).join('\n')}\n}\nviews { view index { include * } }`;
  const largeScope = { diagram_id: randomUUID(), revision: revisionOf(large) };
  assert.ok((await clients.png(large, largeScope, 'png')).length > 0);
  console.log(JSON.stringify({ event: 'integration.recovery_ok', cases: ['full_storage', 'next_job', '40_node_graph'] }));
};
run().catch(error => { console.error(error); process.exitCode = 1; });
