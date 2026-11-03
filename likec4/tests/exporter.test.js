const test = require('node:test');
const assert = require('node:assert/strict');
const { Readable } = require('node:stream');
const { mkdtemp, mkdir, writeFile, readFile, rm, lstat, symlink } = require('node:fs/promises');
const { join } = require('node:path');
const { tmpdir } = require('node:os');
const { createHandler, processJob, safeLocalJoin } = require('../worker/exporter');
const { revisionOf, RequestError } = require('../worker/scope');

const content = 'model {}';
const scope = { diagram_id: '60d2dd6b-1d5e-4b97-a8c2-e713fa178017', storage_path: 'diagrams/likec4/60d2dd6b-1d5e-4b97-a8c2-e713fa178017/source.c4', revision: revisionOf(content) };
const png = Buffer.from('89504e470d0a1a0a00', 'hex');
const request = (overrides = {}, body = scope) => {
  const req = Readable.from([typeof body === 'string' ? body : JSON.stringify(body)]);
  return Object.assign(req, { method: 'POST', url: '/export', headers: {
    'x-likec4-token': 'internal-test-token', 'x-likec4-operation': 'export',
    'x-likec4-diagram-id': scope.diagram_id, 'x-likec4-storage-path': scope.storage_path,
    'x-likec4-revision': scope.revision,
  } }, overrides);
};
const response = () => ({ status: 0, headers: {}, body: '',
  writeHead(status, headers) { this.status = status; this.headers = headers; }, end(body) { this.body = JSON.parse(body); } });
const renderPNG = async work => {
  const outputDir = join(work, 'out'); await mkdir(outputDir);
  const path = join(outputDir, 'index.png'); await writeFile(path, png);
  return { outputDir, pngs: [path] };
};
const environment = (t, values) => {
  const previous = Object.fromEntries(Object.keys(values).map(key => [key, process.env[key]]));
  for (const [key, value] of Object.entries(values)) { if (value === undefined) delete process.env[key]; else process.env[key] = value; }
  t.after(() => { for (const [key, value] of Object.entries(previous)) { if (value === undefined) delete process.env[key]; else process.env[key] = value; } });
};

test('export health and unknown routes do not dispatch work', async () => {
  const handler = createHandler({ run: () => assert.fail('Unexpected job') });
  const health = response(); await handler(request({ method: 'GET', url: '/health' }), health);
  assert.equal(health.status, 200); assert.deepEqual(health.body, { ok: true });
  const missing = response(); await handler(request({ method: 'GET' }), missing);
  assert.equal(missing.status, 404);
});

test('export API enforces token, body scope and JSON before dispatch', async () => {
  const run = () => assert.fail('Unauthorized job dispatched');
  const handler = createHandler({ apiToken: 'internal-test-token', run });
  for (const [req, status, error] of [
    [request({ headers: {} }), 401, 'unauthorized'],
    [request({}, { ...scope, revision: 'a'.repeat(64) }), 403, 'scope_mismatch'],
    [request({}, '{'), 400, 'invalid_json'],
    [request({}, []), 400, 'invalid_json'],
  ]) {
    const res = response(); await handler(req, res);
    assert.equal(res.status, status); assert.equal(res.body.error, error);
  }
});

test('export API dispatches only the authorized scope and redacts unexpected errors', async () => {
  const handler = createHandler({ apiToken: 'internal-test-token', run: async authorized => {
    assert.equal(authorized.diagram_id, scope.diagram_id); assert.equal(authorized.revision, scope.revision);
    return { png_path: 'published.png' };
  } });
  const success = response(); await handler(request(), success);
  assert.equal(success.status, 200); assert.deepEqual(success.body, { ok: true, png_path: 'published.png' });
  for (const [failure, status, code] of [[new Error('private storage secret'), 500, 'export_failed'], [new RequestError(409, 'stale_revision'), 409, 'stale_revision']]) {
    const failing = createHandler({ apiToken: 'internal-test-token', run: async () => { throw failure; } });
    const res = response(); await failing(request(), res);
    assert.equal(res.status, status); assert.deepEqual(res.body, { ok: false, error: code });
  }
});

test('export publication keeps revision-scoped files and metadata and removes working data', async () => {
  const cache = await mkdtemp(join(tmpdir(), 'likec4-export-cache-'));
  let workDir;
  const uploaded = []; let metadata;
  try {
    const result = await processJob(scope, { localDir: cache,
      storage: { read: async path => { assert.equal(path, scope.storage_path); return content; }, write: async (path, bytes) => { assert.deepEqual(bytes, png); uploaded.push(path); } },
      render: async (work, authorized) => { workDir = work; assert.equal(authorized, scope); assert.equal(await readFile(join(work, 'diagram.c4'), 'utf8'), content); return renderPNG(work); },
      metadata: async payload => { metadata = payload; },
    });
    const path = `diagrams/likec4/${scope.diagram_id}/views/${scope.revision}/index.png`;
    assert.deepEqual(uploaded, [path]); assert.deepEqual(result.png_paths, [path]);
    assert.equal(metadata.diagram_id, scope.diagram_id); assert.equal(metadata.revision, scope.revision);
    assert.equal(metadata.png_size, png.length); assert.equal(metadata.path, scope.storage_path);
    assert.deepEqual(await readFile(join(cache, path)), png);
    assert.equal(await readFile(join(cache, `diagrams/likec4/${scope.diagram_id}/revisions/${scope.revision}/source.c4`), 'utf8'), content);
    await assert.rejects(lstat(workDir), { code: 'ENOENT' });
  } finally { await rm(cache, { recursive: true, force: true }); }
});

test('stale source cannot render or publish', async () => {
  await assert.rejects(processJob(scope, { localDir: false,
    storage: { read: async () => 'different source', write: () => assert.fail('Published stale source') },
    render: () => assert.fail('Rendered stale source'), metadata: () => assert.fail('Published stale metadata'),
  }), error => error.code === 'stale_revision');
});

test('invalid PNGs and output counts fail before publication and clean working directories', async () => {
  for (const [kind, message] of [
    ['empty', 'Invalid PNG output count'], ['too_many', 'Invalid PNG output count'],
    ['signature', 'Invalid PNG content'], ['extension', 'Invalid PNG output'], ['symlink', 'Invalid PNG output'],
  ]) {
    let workDir;
    await assert.rejects(processJob(scope, { localDir: false,
      storage: { read: async () => content, write: () => assert.fail(`Published ${kind} output`) },
      metadata: () => assert.fail('Published invalid metadata'),
      render: async work => {
        workDir = work;
        const outputDir = join(work, 'out'); await mkdir(outputDir);
        const file = join(outputDir, kind === 'extension' ? 'index.svg' : 'index.png');
        await writeFile(file, kind === 'signature' ? 'not PNG' : png);
        if (kind === 'symlink') { const alias = join(outputDir, 'alias.png'); await symlink(file, alias); return { outputDir, pngs: [alias] }; }
        return { outputDir, pngs: kind === 'empty' ? [] : Array(kind === 'too_many' ? 129 : 1).fill(file) };
      },
    }), { message });
    await assert.rejects(lstat(workDir), { code: 'ENOENT' });
  }
});

test('storage and callback requests are scoped and metadata conflicts remain stale-revision failures', async t => {
  environment(t, { SEAWEEDFS_FILER_URL: 'http://storage.test/', SEAWEEDFS_BASE_DIR: '/media/',
    SEAWEEDFS_JWT_READ_KEY: 'read-test-key', SEAWEEDFS_JWT_WRITE_KEY: 'write-test-key',
    LIKEC4_METADATA_URL: 'http://app.test/metadata', LIKEC4_METADATA_TOKEN: 'callback-test-token' });
  let callbackStatus = 204;
  const calls = [];
  t.mock.method(globalThis, 'fetch', async (url, options) => {
    calls.push({ url, options }); assert.equal(options.redirect, 'error');
    if (url === 'http://app.test/metadata') {
      assert.equal(options.headers['X-LikeC4-Token'], 'callback-test-token');
      assert.equal(JSON.parse(options.body).revision, scope.revision);
      return new Response(null, { status: callbackStatus });
    }
    assert.ok(url.startsWith('http://storage.test/media/diagrams/likec4/'));
    const jwt = options.headers.Authorization.split(' ')[1];
    const claims = JSON.parse(Buffer.from(jwt.split('.')[1], 'base64url'));
    assert.deepEqual(claims.allowed_methods, [options.method || 'GET']);
    return new Response(options.method ? null : content, { status: options.method ? 204 : 200 });
  });
  await processJob(scope, { localDir: false, render: renderPNG });
  assert.equal(calls.length, 3);
  callbackStatus = 409;
  await assert.rejects(processJob(scope, { localDir: false, render: renderPNG }), error => error.status === 409 && error.code === 'stale_revision');
});

test('storage read failures do not reach rendering', async t => {
  environment(t, { SEAWEEDFS_FILER_URL: 'http://storage.test', SEAWEEDFS_BASE_DIR: '' });
  let status = 404;
  t.mock.method(globalThis, 'fetch', async () => new Response(null, { status }));
  for (const [value, code] of [[404, 'source_not_found'], [503, 'storage_read_failed']]) {
    status = value;
    await assert.rejects(processJob(scope, { localDir: false, render: () => assert.fail('Rendered missing source') }), error => error.code === code);
  }
});

test('local export paths cannot escape their output root', () => {
  assert.equal(safeLocalJoin('/tmp/export', 'project/index.png'), '/tmp/export/project/index.png');
  for (const path of ['', '/outside.png', '../outside.png', 'project/../outside.png', 'project\\index.png', 'project:index.png', 'a\x00.png']) {
    assert.throws(() => safeLocalJoin('/tmp/export', path));
  }
});
