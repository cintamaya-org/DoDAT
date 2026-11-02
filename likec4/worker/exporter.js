const http = require('http');
const { chmod, lstat, mkdir, mkdtemp, readFile, readdir, rm, stat, writeFile } = require('fs').promises;
const { dirname, join, relative, resolve, sep } = require('path');
const { execFile } = require('child_process');
const { promisify } = require('util');
const { tmpdir } = require('os');
const { seaweedAuthHeaders } = require('./seaweedfs_auth');
const { DEFAULT_LIMITS, RequestError, authorizeScope, validateSource, readResponseText, readJson, outputPrefix, createQueue } = require('./scope');

const execFileAsync = promisify(execFile);
const PNG_SIGNATURE = Buffer.from('89504e470d0a1a0a', 'hex');
const MAX_PNG_BYTES = 32 * 1024 * 1024;
const MAX_PNG_FILES = 128;
const privateRoot = async root => {
  const dir = resolve(root);
  await mkdir(dir, { recursive: true, mode: 0o700 });
  const info = await lstat(dir);
  if (!info.isDirectory() || info.isSymbolicLink() || (process.getuid && info.uid !== process.getuid())) throw new Error('Invalid export root');
  await chmod(dir, 0o700);
  return dir;
};
const createWorkDir = async () => {
  const root = process.env.LIKEC4_EXPORT_TMP ? await privateRoot(process.env.LIKEC4_EXPORT_TMP) : tmpdir();
  return mkdtemp(join(root, 'likec4-export-'));
};
const safeLocalJoin = (base, relativePath) => {
  if (typeof relativePath !== 'string' || !relativePath.split('/').every(part => part && part !== '.' && part !== '..') || /[\\:\x00]/.test(relativePath)) throw new Error('Invalid local output path');
  const root = resolve(base);
  const target = resolve(root, relativePath);
  if (!target.startsWith(`${root}${sep}`)) throw new Error('Invalid local output path');
  return target;
};
const findPngFiles = async (dir, depth = 0) => {
  if (depth > 4) return [];
  const files = [];
  for (const entry of await readdir(dir, { withFileTypes: true })) {
    const path = join(dir, entry.name);
    if (entry.isDirectory()) files.push(...await findPngFiles(path, depth + 1));
    else if (entry.isFile() && entry.name.toLowerCase().endsWith('.png')) files.push(path);
    if (files.length > MAX_PNG_FILES) throw new Error('Too many PNG outputs');
  }
  return files;
};
const exportLikeC4 = async (workDir, exec = execFileAsync) => {
  const outputDir = join(workDir, 'out');
  await mkdir(outputDir, { mode: 0o700 });
  const view = process.env.LIKEC4_EXPORT_VIEW;
  const args = ['export', 'png', '-o', outputDir, '.', ...(view ? ['--filter', view] : [])];
  await exec(process.env.LIKEC4_BIN || 'likec4', args, { cwd: workDir, timeout: Number(process.env.LIKEC4_RENDER_TIMEOUT_MS || 120000), maxBuffer: 1024 * 1024 });
  const pngs = (await findPngFiles(outputDir)).sort();
  if (!pngs.length) throw new Error('No PNG output');
  return { outputDir, pngs };
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
      return readResponseText(response, DEFAULT_LIMITS.source);
    },
    write: async (path, body) => {
      const response = await fetch(url(path), { method: 'PUT', headers: { ...seaweedAuthHeaders(location(path), 'PUT'), 'Content-Type': 'image/png', 'Content-Length': String(body.length) }, body, signal: AbortSignal.timeout(30000), redirect: 'error' });
      if (!response.ok) throw new RequestError(502, 'storage_write_failed');
      if (response.body) await response.body.cancel();
    },
  };
};
const postMetadata = async payload => {
  const url = process.env.LIKEC4_METADATA_URL;
  const token = process.env.LIKEC4_METADATA_TOKEN;
  if (!url || !token) throw new RequestError(503, 'callback_not_configured');
  const response = await fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-LikeC4-Token': token }, body: JSON.stringify(payload), signal: AbortSignal.timeout(30000), redirect: 'error' });
  if (!response.ok) throw new RequestError(response.status === 409 ? 409 : 502, response.status === 409 ? 'stale_revision' : 'metadata_failed');
  if (response.body) await response.body.cancel();
};
const processJob = async (scope, { storage = createStorage(), render = exportLikeC4, metadata = postMetadata, localDir = process.env.LIKEC4_EXPORT_LOCAL_DIR || '/var/likec4-exports' } = {}) => {
  // Hash is checked before invoking CLI or writing any output; stale jobs cannot publish a different source.
  const content = validateSource(await storage.read(scope.storage_path), scope.revision);
  const workDir = await createWorkDir();
  try {
    await writeFile(join(workDir, 'diagram.c4'), content, { encoding: 'utf8', mode: 0o600 });
    const { outputDir, pngs } = await render(workDir);
    if (!pngs.length || pngs.length > MAX_PNG_FILES) throw new Error('Invalid PNG output count');
    const prefix = outputPrefix(scope);
    const paths = [];
    let primary;
    let aggregate = 0;
    for (const png of pngs) {
      const info = await lstat(png);
      const suffix = relative(outputDir, png).split(sep).join('/');
      if (!info.isFile() || info.isSymbolicLink() || info.size > MAX_PNG_BYTES || !suffix.endsWith('.png')) throw new Error('Invalid PNG output');
      const path = `${prefix}/${suffix}`;
      // The exact same containment test governs both remote and optional local output names.
      safeLocalJoin(workDir, path);
      aggregate += info.size;
      if (aggregate > 64 * 1024 * 1024) throw new Error('PNG output too large');
      const bytes = await readFile(png);
      if (!bytes.subarray(0, 8).equals(PNG_SIGNATURE)) throw new Error('Invalid PNG content');
      await storage.write(path, bytes);
      if (localDir) {
        const target = safeLocalJoin(localDir, path);
        await mkdir(dirname(target), { recursive: true, mode: 0o700 });
        await writeFile(target, bytes, { mode: 0o600 });
      }
      paths.push(path);
      if (!primary) primary = { path, bytes };
    }
    if (localDir) {
      const source = safeLocalJoin(localDir, `diagrams/likec4/${scope.diagram_id}/revisions/${scope.revision}/source.c4`);
      await mkdir(dirname(source), { recursive: true, mode: 0o700 });
      await writeFile(source, content, { encoding: 'utf8', mode: 0o600 });
    }
    const payload = {
      diagram_id: scope.diagram_id, path: scope.storage_path, revision: scope.revision,
      size: Buffer.byteLength(content), content_type: 'text/plain',
      png_path: primary.path, png_paths: paths, png_size: primary.bytes.length, png_content_type: 'image/png',
    };
    await metadata(payload);
    return { storage_path: scope.storage_path, diagram_id: scope.diagram_id, revision: scope.revision, png_path: primary.path, png_paths: paths, png_size: primary.bytes.length, png_content_type: 'image/png' };
  } finally { await rm(workDir, { recursive: true, force: true }); }
};
const sendJson = (res, status, payload) => {
  res.writeHead(status, { 'Content-Type': 'application/json', 'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff' });
  res.end(JSON.stringify(payload));
};
const createHandler = ({ apiToken = process.env.LIKEC4_API_TOKEN || '', run = processJob } = {}) => {
  const queue = createQueue(8);
  return async (req, res) => {
    try {
      const url = new URL(req.url, 'http://localhost');
      if (req.method === 'GET' && url.pathname === '/health') return sendJson(res, 200, { ok: true });
      if (req.method !== 'POST' || url.pathname !== '/export') throw new RequestError(404, 'not_found');
      const scope = authorizeScope(req, apiToken, 'export');
      const payload = await readJson(req);
      authorizeScope(req, apiToken, 'export', undefined, payload);
      const result = await queue(() => run(scope));
      return sendJson(res, 200, { ok: true, ...result });
    } catch (err) {
      const status = err instanceof RequestError ? err.status : 500;
      const error = err instanceof RequestError ? err.code : 'export_failed';
      return sendJson(res, status, { ok: false, error });
    }
  };
};
const start = () => {
  const server = http.createServer(createHandler());
  server.requestTimeout = 30000;
  server.headersTimeout = 10000;
  server.listen(Number(process.env.LIKEC4_EXPORT_PORT || 9000), process.env.LIKEC4_EXPORT_HOST || '0.0.0.0');
  return server;
};
if (require.main === module) start();
module.exports = { createHandler, processJob, exportLikeC4, safeLocalJoin, start };
