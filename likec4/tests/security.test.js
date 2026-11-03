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
      renderJob: async (_content, scope) => [{ name: 'likec4-webcomponent.js', bytes: Buffer.from('// ' + scope.diagram_id) }],
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

test('viewer policy gives direct visits an opaque origin and no remote asset access', async () => {
  const { viewerResponse } = require('../worker/viewer_policy');
  const html = await readFile(join(__dirname, '../ui/viewer.html'), 'utf8');
  const first = viewerResponse(html); const second = viewerResponse(html);
  assert.notEqual(first.policy, second.policy);
  assert.ok(first.policy.includes('sandbox allow-scripts;'));
  assert.ok(!first.policy.includes('allow-same-origin'));
  assert.ok(first.policy.includes("connect-src 'none'"));
  assert.ok(!first.body.includes('__VIEWER_NONCE__'));
  assert.ok(!first.body.includes('src="security.js"'));
});

test('trusted bundle fetch is text-only, bounded, and rejects redirects/non-JavaScript', async () => {
  const good = await CintaLikeC4.fetchBundle('/bundle', async (_url, options) => {
    assert.equal(options.redirect, 'error'); assert.equal(options.credentials, 'same-origin');
    return new Response('// valid', { headers: { 'Content-Type': 'application/javascript' } });
  });
  assert.equal(good, '// valid');
  await assert.rejects(CintaLikeC4.fetchBundle('/bundle', async () => new Response('<html>', { headers: { 'Content-Type': 'text/html' } })));
  await assert.rejects(CintaLikeC4.fetchBundle('/bundle', async () => new Response('x', { headers: { 'Content-Type': 'application/javascript', 'Content-Length': String(8 * 1024 * 1024 + 1) } })));
  await assert.rejects(CintaLikeC4.fetchBundle('/bundle', async () => new Response(Buffer.alloc(8 * 1024 * 1024 + 1), { headers: { 'Content-Type': 'application/javascript' } })));
});

test('disabled rendering preserves authorized source reads and never submits a job', async () => {
  const previous = process.env.LIKEC4_RENDER_ENABLED;
  process.env.LIKEC4_RENDER_ENABLED = '0';
  try {
    const handler = createHandler({ apiToken: 'service-secret', storage: { read: async () => content },
      renderer: { render: async () => { assert.fail('Disabled renderer was called'); } } });
    const res = fakeResponse();
    await handler(requestFor('read'), res);
    assert.equal(res.status, 200);
    assert.equal(JSON.parse(res.body).content, content);
    assert.equal(JSON.parse(res.body).render_error, 'Diagram rendering disabled');
  } finally {
    if (previous === undefined) delete process.env.LIKEC4_RENDER_ENABLED;
    else process.env.LIKEC4_RENDER_ENABLED = previous;
  }
});

test('bundle cache evicts old revisions within its byte budget and regenerates them', async () => {
  const root = await mkdtemp(join(tmpdir(), 'likec4-cache-'));
  let calls = 0;
  try {
    const renderer = createRenderer({ rootDir: root, maxCacheBytes: 8, renderJob: async () => {
      calls++; return [{ name: 'likec4-webcomponent.js', bytes: Buffer.from('//abc') }];
    } });
    const first = { diagram_id: diagramId, revision };
    const second = { diagram_id: 'd5db9f54-dca2-4507-84d2-8f8fc0f74775', revision };
    await renderer.render(content, first);
    await renderer.render(content, second);
    await assert.rejects(renderer.asset(first, 'likec4-webcomponent.js'));
    await renderer.render(content, first);
    assert.equal(calls, 3);
  } finally { await rm(root, { recursive: true, force: true }); }
});

test('disabled exporter rejects new jobs before processing', async () => {
  const previous = process.env.LIKEC4_RENDER_ENABLED;
  process.env.LIKEC4_RENDER_ENABLED = '0';
  try {
    const { createHandler: createExporter } = require('../worker/exporter');
    const handler = createExporter({ apiToken: 'service-secret', run: async () => { assert.fail('Disabled export ran'); } });
    const req = Readable.from([JSON.stringify({ diagram_id: diagramId, storage_path: storagePath, revision })]);
    Object.assign(req, requestFor('export'), { method: 'POST', url: '/export' });
    const res = fakeResponse();
    await handler(req, res);
    assert.equal(res.status, 503);
    assert.equal(JSON.parse(res.body).error, 'render_disabled');
  } finally {
    if (previous === undefined) delete process.env.LIKEC4_RENDER_ENABLED;
    else process.env.LIKEC4_RENDER_ENABLED = previous;
  }
});

test('status labels keep hostile markup as text for plain and accented messages', () => {
  const element = { replaceChildren(...children) { this.children = children; }, set innerHTML(_value) { assert.fail('HTML assignment'); } };
  const documentRef = { createElement: tag => { assert.equal(tag, 'strong'); return { textContent: '' }; } };
  const text = '<img src=x onerror=alert(1)>';
  CintaLikeC4.setStatusText(element, text, false, documentRef);
  assert.equal(element.textContent, text);
  CintaLikeC4.setStatusText(element, text, true, documentRef);
  assert.equal(element.children[0].textContent, text);
});

test('bundle fetch retains HTTP status for regeneration and rejects malformed UTF-8', async () => {
  await assert.rejects(CintaLikeC4.fetchBundle('/bundle', async () => new Response(null, { status: 404 })), error => error.status === 404);
  await assert.rejects(CintaLikeC4.fetchBundle('/bundle', async () => new Response(Buffer.from([0xc3]), { headers: { 'Content-Type': 'application/javascript' } })), TypeError);
});

test('render queue releases a failed job so pending work can proceed', async () => {
  const enqueue = createQueue(1);
  const failed = enqueue(async () => { throw new Error('render failed'); });
  const next = enqueue(() => 'recovered');
  await assert.rejects(failed, /render failed/);
  assert.equal(await next, 'recovered');
});

test('failed codegen leaves no published asset and the same revision can retry', async () => {
  const root = await mkdtemp(join(tmpdir(), 'likec4-render-retry-'));
  let attempt = 0;
  const scope = { diagram_id: diagramId, revision };
  try {
    const renderer = createRenderer({ rootDir: root, renderJob: async () => {
      if (++attempt === 1) throw new Error('worker unavailable');
      return [{ name: 'likec4-webcomponent.js', bytes: Buffer.from('// recovered') }];
    } });
    await assert.rejects(renderer.render(content, scope), /worker unavailable/);
    await assert.rejects(renderer.asset(scope, 'likec4-webcomponent.js'));
    await renderer.render(content, scope);
    assert.equal(await readFile(await renderer.asset(scope, 'likec4-webcomponent.js'), 'utf8'), '// recovered');
    assert.equal(attempt, 2);
  } finally { await rm(root, { recursive: true, force: true }); }
});
