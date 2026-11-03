const test = require('node:test');
const assert = require('node:assert/strict');
const { Readable } = require('node:stream');
const { createHandler } = require('../server');
const { RequestError, revisionOf } = require('../worker/scope');

const content = "model { a = component 'API' }";
const diagramId = '60d2dd6b-1d5e-4b97-a8c2-e713fa178017';
const path = `diagrams/likec4/${diagramId}/source.c4`;
const revision = revisionOf(content);
const response = () => ({ status: 0, headers: {}, body: '', writeHead(status, headers) { this.status = status; this.headers = headers; }, end(body = '') { this.body = String(body); } });
const request = (operation = 'read', url = '/c4', body) => Object.assign(Readable.from(body ? [JSON.stringify(body)] : []), {
  method: body ? 'POST' : 'GET', url: `${url}?file=${encodeURIComponent(path)}`,
  headers: { 'x-likec4-token': 'test-token', 'x-likec4-operation': operation, 'x-likec4-diagram-id': diagramId,
    'x-likec4-storage-path': path, 'x-likec4-revision': revision, 'x-likec4-can-write': '1' },
});

test('editor saves only the revision-validated source before rendering the same scope', async () => {
  const writes = [];
  const handler = createHandler({ apiToken: 'test-token', storage: { write: async (...args) => writes.push(args) },
    renderer: { render: async (source, scope) => { assert.deepEqual(writes, [[path, content]]); assert.equal(source, content); assert.equal(scope.revision, revision); return { bundle_url: 'bundles/scoped.js' }; } } });
  const saved = response(); await handler(request('write', '/save', { content }), saved);
  assert.equal(saved.status, 200); assert.equal(JSON.parse(saved.body).revision, revision);
  assert.equal(JSON.parse(saved.body).can_write, true);
  const stale = response(); await handler(request('write', '/save', { content: 'changed source' }), stale);
  assert.equal(stale.status, 409); assert.deepEqual(writes, [[path, content]]);
});

test('flow downloads preserve authorized source context without invoking codegen', async () => {
  const handler = createHandler({ apiToken: 'test-token', storage: { read: async () => content }, renderer: { render: () => assert.fail('Flow download invoked CLI') } });
  for (const route of ['/flow-matrix', '/export-json']) {
    const res = response(); await handler(request('read', route), res);
    assert.equal(res.status, 200);
    assert.equal(JSON.parse(res.body).revision, revision);
    assert.equal(JSON.parse(res.body).file, path);
    assert.ok(res.headers['Content-Disposition'].includes('attachment'));
    if (route === '/export-json') assert.equal(JSON.parse(res.body).content, content);
    else assert.equal(JSON.parse(res.body).content, undefined);
  }
});

test('render errors preserve source availability without exposing internal messages', async () => {
  for (const [failure, status] of [[new Error('private worker details'), 200], [new RequestError(503, 'render_queue_full'), 503]]) {
    const handler = createHandler({ apiToken: 'test-token', storage: { read: async () => content }, renderer: { render: async () => { throw failure; } } });
    const res = response(); await handler(request(), res);
    assert.equal(res.status, status); assert.ok(!res.body.includes('private worker details'));
    if (status === 200) { assert.equal(JSON.parse(res.body).content, content); assert.equal(JSON.parse(res.body).render_error, 'LikeC4 rendering failed'); }
    else assert.equal(JSON.parse(res.body).error, 'render_queue_full');
  }
});

test('static viewer responses enforce nonce sandbox policy and never serve the old global bundle', async () => {
  const handler = createHandler();
  const viewer = response(); await handler({ method: 'GET', url: '/viewer.html' }, viewer);
  assert.equal(viewer.status, 200);
  const nonce = viewer.body.match(/<script nonce="([^"]+)"/)[1];
  assert.ok(viewer.headers['Content-Security-Policy'].includes(`'nonce-${nonce}'`));
  assert.ok(viewer.headers['Content-Security-Policy'].includes('sandbox allow-scripts;'));
  const head = response(); await handler({ method: 'HEAD', url: '/viewer.html' }, head);
  assert.equal(head.status, 200); assert.equal(head.body, '');
  for (const url of ['/likec4-webcomponent.js', '/worker/exporter.js', '/../package.json']) {
    const blocked = response(); await handler({ method: 'GET', url }, blocked);
    assert.equal(blocked.status, 404);
  }
});
