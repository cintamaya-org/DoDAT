const { createHash, timingSafeEqual } = require('crypto');

const DEFAULT_LIMITS = Object.freeze({ source: 1024 * 1024, nodes: 512, relations: 10000, response: 8 * 1024 * 1024 });
class RequestError extends Error {
  constructor(status, code) { super(code); this.status = status; this.code = code; }
}
const revisionOf = content => createHash('sha256').update(content).digest('hex');
const validStoragePath = value => typeof value === 'string' && value.length <= 500 &&
  value.endsWith('.c4') && !/[\\\x00-\x20\x7f:?#%]/.test(value) &&
  value.split('/').every(part => part && part !== '.' && part !== '..');
const tokensMatch = (provided, expected) => timingSafeEqual(
  createHash('sha256').update(String(provided || '')).digest(),
  createHash('sha256').update(String(expected || '')).digest(),
);
const authorizeScope = (req, expectedToken, operation, file, payload) => {
  if (!expectedToken) throw new RequestError(503, 'service_not_configured');
  const bearer = String(req.headers.authorization || '');
  const provided = bearer.toLowerCase().startsWith('bearer ') ? bearer.slice(7).trim() : req.headers['x-likec4-token'];
  if (!provided || !tokensMatch(provided, expectedToken)) throw new RequestError(401, 'unauthorized');
  const scope = {
    diagram_id: req.headers['x-likec4-diagram-id'],
    storage_path: req.headers['x-likec4-storage-path'],
    revision: req.headers['x-likec4-revision'],
    operation: req.headers['x-likec4-operation'],
  };
  if (!/^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/.test(scope.diagram_id || '') ||
      !validStoragePath(scope.storage_path) || !/^[a-f0-9]{64}$/.test(scope.revision || '') || scope.operation !== operation) {
    throw new RequestError(403, 'invalid_scope');
  }
  if (file !== undefined && file !== scope.storage_path) throw new RequestError(403, 'scope_mismatch');
  if (payload && ['diagram_id', 'storage_path', 'revision'].some(key => payload[key] !== scope[key])) {
    throw new RequestError(403, 'scope_mismatch');
  }
  return scope;
};
const validateSource = (content, revision, limit = DEFAULT_LIMITS.source) => {
  if (typeof content !== 'string') throw new RequestError(400, 'invalid_source');
  if (Buffer.byteLength(content, 'utf8') > limit) throw new RequestError(413, 'source_too_large');
  if (revisionOf(content) !== revision) throw new RequestError(409, 'stale_revision');
  return content;
};
const readBounded = async (stream, maxBytes, code = 'source_too_large') => {
  let size = 0;
  const chunks = [];
  try {
    const iterable = typeof stream.iterator === 'function' ? stream.iterator({ destroyOnReturn: false }) : stream;
    for await (const raw of iterable) {
      const chunk = Buffer.isBuffer(raw) ? raw : Buffer.from(raw);
      size += chunk.length;
      if (size > maxBytes) throw new RequestError(413, code);
      chunks.push(chunk);
    }
  } catch (err) {
    if (typeof stream.cancel === 'function') await stream.cancel().catch(() => {});
    else if (typeof stream.resume === 'function') stream.resume();
    throw err;
  }
  return Buffer.concat(chunks, size);
};
const readResponseText = async (response, maxBytes = DEFAULT_LIMITS.source) => {
  const declared = response.headers.get('content-length');
  if (declared && Number(declared) > maxBytes) {
    if (response.body) await response.body.cancel().catch(() => {});
    throw new RequestError(413, 'source_too_large');
  }
  return (await readBounded(response.body || [], maxBytes)).toString('utf8');
};
const readJson = async (req, maxBytes = DEFAULT_LIMITS.source + 65536) => {
  const raw = await readBounded(req, maxBytes, 'payload_too_large');
  try {
    const payload = JSON.parse(raw.toString('utf8') || '{}');
    if (!payload || Array.isArray(payload) || typeof payload !== 'object') throw new Error();
    return payload;
  } catch { throw new RequestError(400, 'invalid_json'); }
};
const outputPrefix = scope => `diagrams/likec4/${scope.diagram_id}/views/${scope.revision}`;
const createQueue = (maxPending = 8) => {
  let active = false;
  const pending = [];
  const next = () => {
    if (active || !pending.length) return;
    active = true;
    const { task, resolve, reject } = pending.shift();
    Promise.resolve().then(task).then(resolve, reject).finally(() => { active = false; next(); });
  };
  return task => {
    if (active && pending.length >= maxPending) return Promise.reject(new RequestError(503, 'render_queue_full'));
    return new Promise((resolve, reject) => { pending.push({ task, resolve, reject }); next(); });
  };
};
module.exports = { DEFAULT_LIMITS, RequestError, revisionOf, validStoragePath, authorizeScope, validateSource, readBounded, readResponseText, readJson, outputPrefix, createQueue };
