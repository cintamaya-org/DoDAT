const test = require('node:test');
const assert = require('node:assert/strict');
const { mkdtemp, mkdir, writeFile, readFile, readdir, rm, symlink, link, utimes } = require('fs').promises;
const { tmpdir } = require('os');
const { join } = require('path');
const { randomUUID } = require('crypto');
const { execFileSync } = require('child_process');
const { revisionOf } = require('../worker/scope');
const { VERSION, readFileAt, collectResult, createRenderClient } = require('../worker/render_protocol');
const { runOnce, render } = require('../worker/renderer');

const diagramId = '60d2dd6b-1d5e-4b97-a8c2-e713fa178017';
const content = 'model {}';
const scope = { diagram_id: diagramId, revision: revisionOf(content) };
const png = Buffer.from('89504e470d0a1a0a', 'hex');
const fixture = async fn => {
  const root = await mkdtemp(join(tmpdir(), 'likec4-protocol-'));
  const input = join(root, 'input'); const output = join(root, 'output');
  await mkdir(input); await mkdir(output);
  try { await fn({ root, input, output }); } finally { await rm(root, { recursive: true, force: true }); }
};
const request = (operation = 'codegen') => ({ version: VERSION, job_id: randomUUID(), ...scope, operation, content, view: '', deadline: Date.now() + 10000 });
const publish = async (output, req, files, changes = {}) => {
  const root = join(output, req.job_id); await mkdir(root);
  for (const [name, bytes] of Object.entries(files)) await writeFile(join(root, name), bytes);
  await writeFile(join(root, 'result.json'), JSON.stringify({ ...req, content: undefined, ok: true, files: Object.keys(files), ...changes }));
  return root;
};

test('file jobs retain scope, publish atomically, and acknowledge output for cleanup', () => fixture(async options => {
  const client = createRenderClient({ ...options, pollMs: 2 });
  let running;
  const interval = setInterval(() => {
    if (running) return; // Model the production worker's awaited serial loop.
    running = runOnce({ ...options, operation: 'codegen', execute: async req => {
      assert.equal(req.content, content); assert.equal(req.revision, scope.revision);
      return [{ name: 'likec4-webcomponent.js', bytes: Buffer.from('// valid') }];
    } }).finally(() => { running = undefined; });
  }, 5);
  try {
    const files = await client(content, scope, 'codegen');
    assert.equal(files[0].bytes.toString(), '// valid');
    assert.deepEqual(await readdir(options.input), []);
  } finally { clearInterval(interval); if (running) await running; }
  await runOnce({ ...options, operation: 'codegen' });
  assert.deepEqual(await readdir(options.output), []);
}));

test('results reject forged identity, revision, operation and manifest entries', () => fixture(async ({ output }) => {
  for (const changes of [{ revision: 'f'.repeat(64) }, { diagram_id: randomUUID() }, { job_id: randomUUID() },
    { operation: 'png' }, { files: ['../outside'] }, { files: ['likec4-webcomponent.js', 'likec4-webcomponent.js'] }]) {
    const req = request();
    const root = await publish(output, req, { 'likec4-webcomponent.js': '// valid' }, changes);
    await assert.rejects(collectResult(root, req));
  }
}));

test('unexpected files, special types, symlink ancestors and hardlinks are rejected', () => fixture(async ({ root, output }) => {
  const req = request(); const dir = await publish(output, req, { 'likec4-webcomponent.js': '// valid' });
  await writeFile(join(dir, 'extra'), 'unexpected');
  await assert.rejects(collectResult(dir, req));
  await rm(join(dir, 'extra'));
  await writeFile(join(root, 'outside'), 'secret');
  await rm(join(dir, 'likec4-webcomponent.js'));
  await symlink(join(root, 'outside'), join(dir, 'likec4-webcomponent.js'));
  await assert.rejects(collectResult(dir, req));
  await symlink(root, join(dir, 'alias'));
  await assert.rejects(readFileAt(dir, 'alias/outside', 10));
  await rm(join(dir, 'likec4-webcomponent.js'));
  await link(join(root, 'outside'), join(dir, 'likec4-webcomponent.js'));
  await assert.rejects(readFileAt(dir, 'likec4-webcomponent.js', 10));
  execFileSync('mkfifo', [join(dir, 'pipe')]);
  await assert.rejects(readFileAt(dir, 'pipe', 10));
}));

test('bundle bytes and PNG signatures/counts stay bounded', () => fixture(async ({ output }) => {
  const req = request(); const dir = await publish(output, req, { 'likec4-webcomponent.js': Buffer.alloc(8 * 1024 * 1024 + 1) });
  await assert.rejects(collectResult(dir, req));
  const image = request('png'); const images = await publish(output, image, { 'index.png': Buffer.from('not a PNG') });
  await assert.rejects(collectResult(images, image));
  await writeFile(join(images, 'index.png'), png);
  assert.equal((await collectResult(images, image))[0].bytes.length, 8);
}));

test('timeout cancels input; missing worker never falls back to local CLI', () => fixture(async options => {
  const client = createRenderClient({ ...options, timeout: 30, pollMs: 2 });
  await assert.rejects(client(content, scope, 'codegen'), error => error.code === 'render_timeout');
  assert.deepEqual(await readdir(options.input), []);
}));

test('partial input, expired jobs and output from a previous worker are not executed', () => fixture(async options => {
  const expired = request(); expired.deadline = Date.now() - 1;
  await writeFile(join(options.input, `${expired.job_id}.json`), JSON.stringify(expired));
  await writeFile(join(options.input, `.${randomUUID()}.tmp`), '{');
  const orphan = randomUUID(); await mkdir(join(options.output, orphan));
  let called = false;
  await runOnce({ ...options, operation: 'codegen', execute: async () => { called = true; } });
  assert.equal(called, false);
  assert.deepEqual(await readdir(options.output), []);
}));

test('renderer writes only diagram.c4 and enables Chromium sandbox with fixed arguments', () => fixture(async ({ root }) => {
  const req = request('png');
  const work = join(root, 'work'); await mkdir(work);
  await render(req, work, async (args, cwd) => {
    assert.equal(cwd, work);
    assert.equal(await readFile(join(cwd, 'diagram.c4'), 'utf8'), content);
    assert.ok(args.includes('--chromium-sandbox'));
    assert.ok(!args.includes('--no-sandbox'));
    await writeFile(join(cwd, 'output', 'index.png'), png);
  });
  assert.deepEqual((await readdir(work)).sort(), ['diagram.c4', 'output']);
}));

test('broker restart removes expired requests/staging and rejects late results', () => fixture(async options => {
  const old = request(); old.deadline = Date.now() - 1;
  await writeFile(join(options.input, `${old.job_id}.json`), JSON.stringify(old));
  const stage = join(options.input, `.${randomUUID()}.tmp`);
  await writeFile(stage, 'abandoned');
  const past = new Date(Date.now() - 240000); await utimes(stage, past, past);
  const client = createRenderClient({ ...options, timeout: 20, pollMs: 2 });
  await assert.rejects(client(content, scope, 'codegen'), error => error.code === 'render_timeout');
  assert.deepEqual(await readdir(options.input), []);
  await publish(options.output, old, { 'likec4-webcomponent.js': '// too late' });
  await runOnce({ ...options, operation: 'codegen', execute: async () => { assert.fail('Expired job executed'); } });
  assert.deepEqual(await readdir(options.output), []);
}));
