const test = require('node:test');
const assert = require('node:assert/strict');
const { Readable } = require('node:stream');
const { mkdtemp, readFile, rm, writeFile } = require('node:fs/promises');
const { join } = require('node:path');
const { tmpdir } = require('node:os');
const { Worker } = require('node:worker_threads');
const {
  DEFAULT_LIMITS, RequestError, revisionOf, authorizeScope, validateSource, readBounded, createQueue,
} = require('../worker/scope');
const { buildFlowMatrix, createHandler, createRenderer } = require('../server');
const CintaLikeC4 = require('../ui/security');

const diagramId = '60d2dd6b-1d5e-4b97-a8c2-e713fa178017';
const storagePath = 'diagrams/likec4/' + diagramId + '/source.c4';
const content = "model { a = component 'A' {} b = component 'B' {} a -> b 'HTTPS 443' }";
const revision = revisionOf(content);
const requestFor = (operation, overrides = {}) => ({
  method: 'GET',
  url: '/c4?file=' + encodeURIComponent(storagePath),
  headers: {
    'x-likec4-token': 'service-secret',
    'x-likec4-diagram-id': diagramId,
    'x-likec4-storage-path': storagePath,
    'x-likec4-revision': revision,
    'x-likec4-operation': operation,
    ...overrides,
  },
});
const fakeResponse = () => ({
  status: 0,
  headers: {},
  body: '',
  writeHead(status, headers) { this.status = status; this.headers = headers; },
  end(body = '') { this.body = String(body); },
});

test('service APIs reject missing or mismatched scope before storage access', async () => {
  const reads = [];
  const handler = createHandler({
    apiToken: 'service-secret',
    storage: { async read(path) { reads.push(path); return content; } },
    renderer: { async render() { return {}; } },
  });
  for (const headers of [
    { 'x-likec4-token': 'wrong' },
    { 'x-likec4-storage-path': 'diagrams/other.c4' },
    { 'x-likec4-operation': 'write' },
  ]) {
    const req = requestFor('read', headers);
    const res = fakeResponse();
    await handler(req, res);
    assert.equal(res.status, headers['x-likec4-token'] === 'wrong' ? 401 : 403);
  }
  assert.deepEqual(reads, []);
});

test('authorized C4 reads are revision bound and cannot remap a registered path', () => {
  const req = requestFor('read');
  const scope = authorizeScope(req, 'service-secret', 'read', storagePath);
  assert.equal(scope.diagram_id, diagramId);
  assert.equal(scope.revision, revision);
  assert.throws(() => authorizeScope(req, 'service-secret', 'read', 'diagrams/default.c4'),
    error => error instanceof RequestError && error.code === 'scope_mismatch');
  assert.throws(() => authorizeScope(requestFor('read', { 'x-likec4-diagram-id': 'default' }),
    'service-secret', 'read', storagePath),
  error => error instanceof RequestError && error.code === 'invalid_scope');
});

test('source and streamed reads stop at the configured byte limit', async () => {
  assert.equal(DEFAULT_LIMITS.source, 1024 * 1024);
  assert.equal(validateSource(content, revision), content);
  assert.throws(() => validateSource(content, '0'.repeat(64)),
    error => error instanceof RequestError && error.code === 'stale_revision');
  const oversized = 'x'.repeat(DEFAULT_LIMITS.source + 1);
  assert.throws(() => validateSource(oversized, revisionOf(oversized)),
    error => error instanceof RequestError && error.status === 413);
  await assert.rejects(
    readBounded(Readable.from([Buffer.alloc(4), Buffer.alloc(5)]), 8),
    error => error instanceof RequestError && error.status === 413,
  );
});

test('flow matrices stay compatible for supported graphs and reject exhaustion before allocation', () => {
  const result = buildFlowMatrix("a = component 'A' {} b = component 'B' {} a -> b 'HTTPS 443'");
  assert.deepEqual(result.nodes, ['a', 'b']);
  assert.deepEqual(result.adjacency, [[0, 1], [0, 0]]);
  assert.equal(result.flows[0].label, 'HTTPS 443');
  const tooManyNodes = Array.from({ length: 512 }, (_, index) => 'n' + index + ' -> n' + (index + 1)).join('\n');
  assert.throws(() => buildFlowMatrix(tooManyNodes),
    error => error instanceof RequestError && error.status === 413 && error.code === 'graph_too_large');
  const tooManyRelations = Array.from({ length: 10001 }, (_, index) => 'a' + index + ' -> b' + index).join('\n');
  assert.throws(() => buildFlowMatrix(tooManyRelations),
    error => error instanceof RequestError && error.status === 413 && error.code === 'graph_too_large');
  assert.throws(() => buildFlowMatrix('x'.repeat(1024 * 1024 + 1)),
    error => error instanceof RequestError && error.status === 413);
});

test('render queue applies backpressure at its configured pending bound', async () => {
  const enqueue = createQueue(1);
  let release;
  const active = enqueue(() => new Promise(resolve => { release = resolve; }));
  const waiting = enqueue(() => 'second');
  await assert.rejects(enqueue(() => 'third'),
    error => error instanceof RequestError && error.code === 'render_queue_full');
  await Promise.resolve();
  release('first');
  assert.equal(await active, 'first');
  assert.equal(await waiting, 'second');
});

test('matrix parsing does not rescan long nonmatching relation or metadata tokens', async () => {
  const script = `
    const { buildFlowMatrix } = require(${JSON.stringify(require.resolve('../server'))});
    const token = 'x'.repeat(200000);
    for (const source of [token, token + ' -> 123', "component a 'A' { metadata { " + token + ' } }']) {
      const result = buildFlowMatrix(source);
      if (result.flows.length) process.exit(1);
    }
    const unclosed = "component a 'A' {".repeat(10000);
    if (buildFlowMatrix(unclosed).components.length) process.exit(1);
    try {
      buildFlowMatrix(unclosed + '}'.repeat(10000));
      process.exit(1);
    } catch (error) {
      if (!(error.status === 413 && error.code === 'graph_too_large')) throw error;
    }
  `;
  await new Promise((resolve, reject) => {
    const worker = new Worker(script, { eval: true });
    const timer = setTimeout(() => {
      worker.terminate();
      reject(new Error('Matrix parsing exceeded its time budget'));
    }, 2000);
    worker.once('error', error => { clearTimeout(timer); reject(error); });
    worker.once('exit', code => {
      clearTimeout(timer);
      if (code === 0) resolve();
      else reject(new Error('Matrix parser worker exited with code ' + code));
    });
  });
});

test('relation scanning preserves valid tokens after malformed prefixes', () => {
  const result = buildFlowMatrix("ignored -> 123 api-gateway -> data-store 'TLS' next -> last \"HTTPS\"");
  assert.deepEqual(result.flows, [
    { from: 'api-gateway', to: 'data-store', label: 'TLS' },
    { from: 'next', to: 'last', label: 'HTTPS' },
  ]);
  assert.deepEqual(buildFlowMatrix('a->b').flows, []);
  const nested = buildFlowMatrix("component outer 'Outer' { component inner 'Inner' { metadata { owner 'Team' } } }");
  assert.deepEqual(nested.components.map(component => component.name), ['outer', 'inner']);
  assert.equal(nested.components[1].metadata.owner, 'Team');
});

test('renderer stores bundles under immutable diagram and revision namespaces', async () => {
  const root = await mkdtemp(join(tmpdir(), 'likec4-test-'));
  try {
    const renderer = createRenderer({
      rootDir: join(root, 'renders'),
      exec: async (_binary, args, options) => {
        const output = args[args.indexOf('-o') + 1];
        await writeFile(output, '// ' + options.cwd);
      },
    });
    const firstScope = { diagram_id: diagramId, revision, storage_path: storagePath };
    const secondId = 'd5db9f54-dca2-4507-84d2-8f8fc0f74775';
    const secondScope = { diagram_id: secondId, revision, storage_path: 'diagrams/likec4/' + secondId + '/source.c4' };
    const first = await renderer.render(content, firstScope);
    const second = await renderer.render(content, secondScope);
    assert.notEqual(first.bundle_url, second.bundle_url);
    const firstPath = await renderer.asset(firstScope, 'likec4-webcomponent.js');
    const secondPath = await renderer.asset(secondScope, 'likec4-webcomponent.js');
    assert.notEqual(firstPath, secondPath);
    assert.match(await readFile(firstPath, 'utf8'), new RegExp(diagramId));
    assert.match(await readFile(secondPath, 'utf8'), new RegExp(secondId));
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test('browser bundle URLs accept only the same-origin scoped route', () => {
  const valid = 'bundles/' + diagramId + '/' + revision + '/likec4-webcomponent.js';
  assert.equal(
    CintaLikeC4.scopedBundleUrl(valid, 'https://app.example/diagrams/likec4/editor/'),
    'https://app.example/diagrams/likec4/editor/' + valid,
  );
  for (const url of [
    'https://attacker.example/bundles/x.js',
    '//attacker.example/bundles/x.js',
    'bundles/' + diagramId + '/' + revision + '/../../evil.js',
    'bundles/' + diagramId + '/' + revision + '/other.js',
  ]) {
    assert.throws(() => CintaLikeC4.scopedBundleUrl(url, 'https://app.example/diagrams/likec4/editor/'));
  }
});
