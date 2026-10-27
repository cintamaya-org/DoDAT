// Django owns user authorization; this service accepts only explicitly scoped calls.
const { createServer } = require('http');
const { access, mkdir, mkdtemp, readFile, readdir, rename, rm, writeFile, lstat, chmod } = require('fs').promises;
const { createReadStream } = require('fs');
const { execFile } = require('child_process');
const { tmpdir } = require('os');
const { extname, join, basename, resolve, sep } = require('path');
const { promisify } = require('util');
const { seaweedAuthHeaders } = require('./worker/seaweedfs_auth');
const { DEFAULT_LIMITS, RequestError, authorizeScope, validateSource, readResponseText, readJson, createQueue } = require('./worker/scope');

const execFileAsync = promisify(execFile);
const MIME = { '.html': 'text/html; charset=utf-8', '.js': 'application/javascript; charset=utf-8', '.css': 'text/css; charset=utf-8', '.json': 'application/json; charset=utf-8', '.svg': 'image/svg+xml', '.png': 'image/png', '.ico': 'image/x-icon', '.woff2': 'font/woff2' };
const bundleName = 'likec4-webcomponent.js';
const assetPath = value => typeof value === 'string' && value.split('/').every(part => /^[a-zA-Z0-9_.-]+$/.test(part) && part !== '.' && part !== '..');
const privateDirectory = async dir => {
  await mkdir(dir, { recursive: true, mode: 0o700 });
  const stat = await lstat(dir);
  if (!stat.isDirectory() || stat.isSymbolicLink() || (process.getuid && stat.uid !== process.getuid())) throw new Error('Invalid render directory');
  await chmod(dir, 0o700);
  return dir;
};
const manifestFiles = async (dir, prefix = '') => {
  const files = [];
  for (const entry of await readdir(dir, { withFileTypes: true })) {
    const name = prefix ? `${prefix}/${entry.name}` : entry.name;
    if (!assetPath(name)) continue;
    if (entry.isDirectory()) files.push(...await manifestFiles(join(dir, entry.name), name));
    else if (entry.isFile() && MIME[extname(entry.name)]) files.push(name);
  }
  return files;
};
const createRenderer = ({ rootDir, exec = execFileAsync, binary = process.env.LIKEC4_BIN || 'likec4', timeout = 120000, maxPending = 8 } = {}) => {
  const enqueue = createQueue(maxPending);
  const inflight = new Map();
  let rootPromise;
  const root = () => {
    if (!rootPromise) rootPromise = rootDir ? privateDirectory(resolve(rootDir)) : mkdtemp(join(tmpdir(), 'likec4-scoped-'));
    return rootPromise;
  };
  const directory = async scope => join(await root(), scope.diagram_id, scope.revision);
  const render = (content, scope) => {
    validateSource(content, scope.revision);
    const key = `${scope.diagram_id}/${scope.revision}`;
    if (inflight.has(key)) return inflight.get(key);
    const job = enqueue(async () => {
      const dir = await directory(scope);
      try {
        const manifest = JSON.parse(await readFile(join(dir, 'manifest.json'), 'utf8'));
        if (manifest.includes(bundleName)) {
          await access(join(dir, 'output', bundleName));
          return { bundle_url: `bundles/${key}/${bundleName}`, revision: scope.revision };
        }
      } catch { /* No published output for this revision yet. */ }
      await privateDirectory(dir);
      const work = await mkdtemp(join(dir, 'work-'));
      const output = join(work, 'output');
      await mkdir(output, { mode: 0o700 });
      try {
        await writeFile(join(work, 'diagram.c4'), content, { encoding: 'utf8', mode: 0o600 });
        await exec(binary, ['codegen', 'webcomponent', '-o', join(output, bundleName)], { cwd: work, timeout, maxBuffer: 1024 * 1024 });
        const files = await manifestFiles(output);
        if (!files.includes(bundleName)) throw new Error('Missing generated bundle');
        await rename(output, join(dir, 'output'));
        await writeFile(join(dir, 'manifest.json'), JSON.stringify(files), { mode: 0o600 });
        return { bundle_url: `bundles/${key}/${bundleName}`, revision: scope.revision };
      } finally { await rm(work, { recursive: true, force: true }); }
    }).finally(() => inflight.delete(key));
    inflight.set(key, job);
    return job;
  };
  const asset = async (scope, relativePath) => {
    if (!assetPath(relativePath)) throw new RequestError(404, 'asset_not_found');
    const dir = await directory(scope);
    let manifest;
    try { manifest = JSON.parse(await readFile(join(dir, 'manifest.json'), 'utf8')); }
    catch { throw new RequestError(404, 'asset_not_found'); }
    if (!Array.isArray(manifest) || !manifest.includes(relativePath)) throw new RequestError(404, 'asset_not_found');
    const path = join(dir, 'output', relativePath);
    const stat = await lstat(path);
    if (!stat.isFile() || stat.isSymbolicLink()) throw new RequestError(404, 'asset_not_found');
    return path;
  };
  return { render, asset };
};
const createStorage = () => {
  const filer = String(process.env.SEAWEEDFS_FILER_URL || '').replace(/\/+$/, '');
  const base = String(process.env.SEAWEEDFS_BASE_DIR || '').replace(/^\/+|\/+$/g, '');
  const location = path => base ? `${base}/${path}` : path;
  const url = path => {
    if (!/^https?:\/\//.test(filer)) throw new RequestError(503, 'storage_not_configured');
    return `${filer}/${location(path).split('/').map(encodeURIComponent).join('/')}`;
  };
  return {
    read: async path => {
      const response = await fetch(url(path), { headers: seaweedAuthHeaders(location(path), 'GET'), signal: AbortSignal.timeout(30000), redirect: 'error' });
      if (response.status === 404) throw new RequestError(404, 'source_not_found');
      if (!response.ok) throw new RequestError(502, 'storage_read_failed');
      return readResponseText(response);
    },
    write: async (path, content) => {
      const body = Buffer.from(content, 'utf8');
      const response = await fetch(url(path), { method: 'PUT', headers: { ...seaweedAuthHeaders(location(path), 'PUT'), 'Content-Type': 'text/plain; charset=utf-8', 'Content-Length': String(body.length) }, body, signal: AbortSignal.timeout(30000), redirect: 'error' });
      if (!response.ok) throw new RequestError(502, 'storage_write_failed');
      if (response.body) await response.body.cancel();
    },
  };
};
const stripComments = (text) => {
  let result = '';
  for (let i = 0; i < text.length; i++) {
    const ch = text[i];
    const next = text[i + 1];
    if (ch === '/' && next === '*') {
      i += 2;
      while (i < text.length) {
        if (text[i] === '\n') {
          result += '\n';
        }
        if (text[i] === '*' && text[i + 1] === '/') {
          i++;
          break;
        }
        i++;
      }
      continue;
    }
    if (ch === '/' && next === '/') {
      i += 2;
      while (i < text.length && text[i] !== '\n') {
        i++;
      }
      if (i < text.length) {
        result += text[i];
      }
      continue;
    }
    result += ch;
  }
  return result;
};

const findClosingBrace = (text, openIndex) => {
  let depth = 0;
  let inSingle = false;
  let inDouble = false;
  for (let i = openIndex; i < text.length; i++) {
    const ch = text[i];
    const prev = i > 0 ? text[i - 1] : '';
    if (ch === '\'' && !inDouble && prev !== '\\') {
      inSingle = !inSingle;
    } else if (ch === '"' && !inSingle && prev !== '\\') {
      inDouble = !inDouble;
    }
    if (inSingle || inDouble) continue;
    if (ch === '{') depth++;
    else if (ch === '}') {
      depth--;
      if (depth === 0) return i;
    }
  }
  return -1;
};

const isIdentifierStart = (ch) => (
  (ch >= 'A' && ch <= 'Z') ||
  (ch >= 'a' && ch <= 'z') ||
  ch === '_'
);

const isIdentifierChar = (ch) => isIdentifierStart(ch) || (ch >= '0' && ch <= '9') || ch === '-';
const isWhitespace = (ch) => ch === ' ' || ch === '\t' || ch === '\n' || ch === '\r' || ch === '\f';

const parseKeyValues = (text) => {
  const entries = [];

  for (let i = 0; i < text.length;) {
    if (!isIdentifierStart(text[i])) {
      i++;
      continue;
    }

    const keyStart = i;
    i++;
    while (i < text.length && isIdentifierChar(text[i])) {
      i++;
    }
    const key = text.slice(keyStart, i);

    const whitespaceStart = i;
    while (i < text.length && isWhitespace(text[i])) {
      i++;
    }
    if (i === whitespaceStart || text[i] !== '\'') {
      i = keyStart + 1;
      continue;
    }

    const valueStart = ++i;
    while (i < text.length && text[i] !== '\'') {
      i++;
    }
    if (i >= text.length) {
      break;
    }

    entries.push({ key, value: text.slice(valueStart, i).trim() });
    i++;
  }
  return entries;
};

const parseRelations = (text, maxRelations = DEFAULT_LIMITS.relations) => {
  const relations = [];

  for (let i = 0; i < text.length;) {
    if (!isIdentifierStart(text[i])) {
      i++;
      continue;
    }

    const fromStart = i;
    i++;
    while (i < text.length && isIdentifierChar(text[i])) {
      i++;
    }
    const from = text.slice(fromStart, i);

    while (i < text.length && isWhitespace(text[i])) {
      i++;
    }
    if (text[i] !== '-' || text[i + 1] !== '>') {
      i = fromStart + 1;
      continue;
    }
    i += 2;

    while (i < text.length && isWhitespace(text[i])) {
      i++;
    }
    if (!isIdentifierStart(text[i])) {
      i = fromStart + 1;
      continue;
    }

    const toStart = i;
    i++;
    while (i < text.length && isIdentifierChar(text[i])) {
      i++;
    }
    const to = text.slice(toStart, i);

    while (i < text.length && isWhitespace(text[i])) {
      i++;
    }

    let label = null;
    const quote = text[i];
    if (quote === '\'' || quote === '"') {
      const labelStart = i + 1;
      let labelEnd = labelStart;
      while (labelEnd < text.length && text[labelEnd] !== quote) {
        labelEnd++;
      }
      if (labelEnd < text.length) {
        label = text.slice(labelStart, labelEnd);
        i = labelEnd + 1;
      }
    }

    if (relations.length >= maxRelations) throw new RequestError(413, 'graph_too_large');
    relations.push({ from, to, label });
  }

  return relations;
};

const parseComponents = (content, alreadyCleaned = false) => {
  const cleaned = alreadyCleaned ? content : stripComments(content);
  const components = [];
  const compRegex = /component\s+([A-Za-z_][\w-]*)\s*'([^']*)'\s*\{/g;
  let match;

  while ((match = compRegex.exec(cleaned)) !== null) {
    const name = match[1];
    const title = match[2];
    const openIdx = match.index + match[0].lastIndexOf('{');
    const closeIdx = findClosingBrace(cleaned, openIdx);
    if (closeIdx === -1) continue;
    const body = cleaned.slice(openIdx + 1, closeIdx);

    // Extract metadata blocks
    const metadata = {};
    const metaRegex = /metadata\s*\{/g;
    const spans = [];
    let metaMatch;
    while ((metaMatch = metaRegex.exec(body)) !== null) {
      const metaOpen = metaMatch.index + metaMatch[0].lastIndexOf('{');
      const metaClose = findClosingBrace(body, metaOpen);
      if (metaClose === -1) break;
      const metaBody = body.slice(metaOpen + 1, metaClose);
      parseKeyValues(metaBody).forEach(({ key, value }) => {
        metadata[key] = value;
      });
      spans.push({ start: metaMatch.index, end: metaClose });
      metaRegex.lastIndex = metaClose + 1;
    }

    // Remove metadata spans to keep only top-level props
    let bodyWithoutMetadata = '';
    let cursor = 0;
    for (const span of spans) {
      bodyWithoutMetadata += body.slice(cursor, span.start);
      cursor = span.end + 1;
    }
    bodyWithoutMetadata += body.slice(cursor);

    const props = {};
    parseKeyValues(bodyWithoutMetadata).forEach(({ key, value }) => {
      props[key] = value;
    });

    components.push({ name, title, props, metadata });
  }

  return components;
};

const buildFlowMatrix = (content, limits = DEFAULT_LIMITS) => {
  if (typeof content !== 'string' || Buffer.byteLength(content, 'utf8') > limits.source) throw new RequestError(413, 'graph_too_large');
  const cleaned = stripComments(content);
  const flows = [];
  const nodes = [];
  const index = new Map();
  const addNode = (name) => {
    if (!index.has(name)) {
      if (nodes.length >= limits.nodes) throw new RequestError(413, 'graph_too_large');
      index.set(name, nodes.length);
      nodes.push(name);
    }
  };

  parseRelations(cleaned, limits.relations).forEach(({ from, to, label }) => {
    if (flows.length >= limits.relations) throw new RequestError(413, 'graph_too_large');
    flows.push({ from, to, label });
    addNode(from);
    addNode(to);
  });

  const adjacency = nodes.map(() => nodes.map(() => 0));
  const labels = nodes.map(() => nodes.map(() => null));

  flows.forEach(({ from, to, label }) => {
    const i = index.get(from);
    const j = index.get(to);
    if (i !== undefined && j !== undefined) {
      adjacency[i][j] = 1;
      labels[i][j] = label;
    }
  });

  const components = parseComponents(cleaned, true);

  return { nodes, adjacency, labels, flows, components };
};


const send = (res, status, body, headers = {}) => {
  res.writeHead(status, { 'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff', ...headers });
  res.end(body);
};
const sendJson = (res, payload, headers = {}) => {
  const body = JSON.stringify(payload);
  if (Buffer.byteLength(body) > DEFAULT_LIMITS.response) throw new RequestError(413, 'graph_too_large');
  return send(res, 200, body, { 'Content-Type': MIME['.json'], ...headers });
};
const createHandler = ({ storage = createStorage(), renderer = createRenderer({ rootDir: process.env.LIKEC4_PREVIEW_DIR }), apiToken = process.env.LIKEC4_API_TOKEN || '' } = {}) => async (req, res) => {
  try {
    const url = new URL(req.url, 'http://localhost');
    const path = url.pathname;
    const file = url.searchParams.get('file');
    const api = ['/c4', '/export-json', '/flow-matrix', '/save'].includes(path);
    if (api) {
      const write = path === '/save';
      if (req.method !== (write ? 'POST' : 'GET')) throw new RequestError(405, 'method_not_allowed');
      const scope = authorizeScope(req, apiToken, write ? 'write' : 'read', file);
      let content;
      if (write) {
        const payload = await readJson(req);
        content = validateSource(payload.content, scope.revision);
        await storage.write(scope.storage_path, content);
      } else {
        content = validateSource(await storage.read(scope.storage_path), scope.revision);
      }
      if (path === '/export-json' || path === '/flow-matrix') {
        const matrix = buildFlowMatrix(content);
        const payload = { file: scope.storage_path, revision: scope.revision, flows: matrix.flows, components: matrix.components, matrix: { nodes: matrix.nodes, adjacency: matrix.adjacency, labels: matrix.labels } };
        if (path === '/export-json') payload.content = content;
        return sendJson(res, payload, { 'Content-Disposition': 'attachment; filename="flows.json"' });
      }
      let output;
      try { output = await renderer.render(content, scope); }
      catch (err) {
        if (err instanceof RequestError) throw err;
        output = { render_error: 'LikeC4 rendering failed' };
        console.warn(JSON.stringify({ event: 'likec4.render_failed', diagram_id: scope.diagram_id }));
      }
      return sendJson(res, { ok: true, can_write: req.headers['x-likec4-can-write'] === '1', content, file: scope.storage_path, revision: scope.revision, size: Buffer.byteLength(content), content_type: 'text/plain', png_path: null, ...output });
    }
    const bundle = path.match(/^\/bundles\/([a-f0-9-]{36})\/([a-f0-9]{64})\/(.+)$/);
    if (bundle) {
      if (!['GET', 'HEAD'].includes(req.method)) throw new RequestError(405, 'method_not_allowed');
      const scope = authorizeScope(req, apiToken, 'bundle');
      if (bundle[1] !== scope.diagram_id || bundle[2] !== scope.revision) throw new RequestError(403, 'scope_mismatch');
      const filePath = await renderer.asset(scope, bundle[3]);
      res.writeHead(200, { 'Content-Type': MIME[extname(filePath)], 'Cache-Control': 'private, no-store', 'X-Content-Type-Options': 'nosniff' });
      if (req.method === 'HEAD') return res.end();
      const stream = createReadStream(filePath);
      stream.on('error', () => res.destroy());
      return stream.pipe(res);
    }
    // Static UI is deliberately a fixed allowlist; the checked-in old global bundle is never served.
    if (!['GET', 'HEAD'].includes(req.method)) throw new RequestError(405, 'method_not_allowed');
    const assets = { '/': 'index.html', '/index.html': 'index.html', '/viewer.html': 'viewer.html', '/security.js': 'security.js' };
    const name = assets[path];
    if (!name) throw new RequestError(404, 'not_found');
    const body = await readFile(join(__dirname, 'ui', name));
    return send(res, 200, req.method === 'HEAD' ? '' : body, { 'Content-Type': MIME[extname(name)] });
  } catch (err) {
    const status = err instanceof RequestError ? err.status : 500;
    const code = err instanceof RequestError ? err.code : 'internal_error';
    return send(res, status, JSON.stringify({ ok: false, error: code }), { 'Content-Type': MIME['.json'] });
  }
};
const start = () => {
  const server = createServer(createHandler());
  server.requestTimeout = 30000;
  server.headersTimeout = 10000;
  server.listen(process.env.LIKEC4_EDITOR_PORT || 4173);
  return server;
};
if (require.main === module) start();
module.exports = { buildFlowMatrix, parseRelations, createHandler, createRenderer, createStorage, start };
