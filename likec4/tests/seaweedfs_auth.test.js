const test = require('node:test');
const assert = require('node:assert/strict');
const { createHmac } = require('node:crypto');
const { buildFilerJwt, seaweedAuthHeaders } = require('../worker/seaweedfs_auth');

const decode = token => JSON.parse(Buffer.from(token.split('.')[1], 'base64url'));

test('filer JWT authenticates exact path, method and bounded lifetime', t => {
  t.mock.method(Date, 'now', () => 1700000000000);
  const token = buildFilerJwt({ key: 'test-key', path: '//media/source.c4', method: 'get', ttlSeconds: 7 });
  const [header, payload, signature] = token.split('.');
  assert.deepEqual(JSON.parse(Buffer.from(header, 'base64url')), { alg: 'HS256', typ: 'JWT' });
  assert.deepEqual(decode(token), { allowed_prefixes: ['/media/source.c4'], allowed_methods: ['GET'], iat: 1700000000, exp: 1700000007 });
  assert.equal(signature, createHmac('sha256', 'test-key').update(`${header}.${payload}`).digest('base64url'));
});

test('missing signing key never generates a misleading bearer token', () => {
  assert.equal(buildFilerJwt({ key: '', path: 'source.c4', method: 'GET' }), '');
});

test('invalid lifetimes use safe defaults and negative lifetime stays positive', t => {
  t.mock.method(Date, 'now', () => 1700000000000);
  for (const [ttlSeconds, expected] of [[undefined, 60], ['invalid', 60], [-10, 1]]) {
    const claims = decode(buildFilerJwt({ key: 'test-key', path: 'source.c4', method: 'GET', ttlSeconds }));
    assert.equal(claims.exp - claims.iat, expected);
  }
});

test('read and write operations use separate signing credentials', t => {
  const keys = ['SEAWEEDFS_JWT_READ_KEY', 'SEAWEEDFS_JWT_WRITE_KEY'];
  const previous = keys.map(key => process.env[key]);
  t.after(() => keys.forEach((key, index) => { if (previous[index] === undefined) delete process.env[key]; else process.env[key] = previous[index]; }));
  process.env.SEAWEEDFS_JWT_READ_KEY = 'read-test-key';
  process.env.SEAWEEDFS_JWT_WRITE_KEY = 'write-test-key';
  for (const method of ['GET', 'HEAD', 'put', 'POST', 'DELETE']) {
    const token = seaweedAuthHeaders('media/source.c4', method).Authorization.slice(7);
    const [header, payload, signature] = token.split('.');
    const key = ['GET', 'HEAD'].includes(method) ? 'read-test-key' : 'write-test-key';
    assert.equal(signature, createHmac('sha256', key).update(`${header}.${payload}`).digest('base64url'));
    assert.deepEqual(decode(token).allowed_methods, [method.toUpperCase()]);
  }
  delete process.env.SEAWEEDFS_JWT_READ_KEY;
  assert.deepEqual(seaweedAuthHeaders('source.c4'), {});
});
