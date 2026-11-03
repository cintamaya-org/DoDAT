// File-only boundary between credentialed brokers and networkless renderers.
const { constants } = require('fs');
const { open, lstat, writeFile, rename, rm, readdir } = require('fs').promises;
const { randomUUID } = require('crypto');
const { join } = require('path');
const { RequestError, validateSource } = require('./scope');

const VERSION = 1;
const DEADLINE_MS = 120000;
const MAX_OUTPUT = 64 * 1024 * 1024;
const MAX_FILE = 32 * 1024 * 1024;
const MAX_BUNDLE = 8 * 1024 * 1024;
const ID = /^[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}$/;
const safePath = value => typeof value === 'string' && value.length <= 500 &&
  value.split('/').length <= 5 && value.split('/').every(part => /^[A-Za-z0-9_.-]+$/.test(part) && part !== '.' && part !== '..');
const delay = ms => new Promise(resolve => setTimeout(resolve, ms));

// Anchor every component to an open directory descriptor. lstat followed by
// readFile would let a compromised renderer swap a directory or grow a file.
const withDirectory = async (root, parts, action) => {
  const handles = [];
  try {
    const anchored = /^\/proc\/self\/fd\/[0-9]+$/.test(root) ? `${root}/.` : root;
    let handle = await open(anchored, constants.O_RDONLY | constants.O_DIRECTORY | constants.O_NOFOLLOW);
    handles.push(handle);
    for (const part of parts) {
      if (!safePath(part) || part.includes('/')) throw new Error('Invalid render path');
      handle = await open(`/proc/self/fd/${handle.fd}/${part}`, constants.O_RDONLY | constants.O_DIRECTORY | constants.O_NOFOLLOW);
      handles.push(handle);
    }
    return await action(`/proc/self/fd/${handle.fd}`);
  } finally { for (const handle of handles.reverse()) await handle.close(); }
};
const readFileAt = async (root, name, maxBytes) => {
  if (!safePath(name)) throw new Error('Invalid render path');
  const parts = name.split('/');
  const file = parts.pop();
  return withDirectory(root, parts, async dir => {
    const handle = await open(`${dir}/${file}`, constants.O_RDONLY | constants.O_NOFOLLOW | constants.O_NONBLOCK);
    try {
      const stat = await handle.stat();
      if (!stat.isFile() || stat.nlink !== 1 || stat.size > maxBytes) throw new Error('Invalid render file');
      const bytes = Buffer.alloc(Math.min(stat.size + 1, maxBytes + 1));
      let size = 0;
      while (size < bytes.length) {
        const { bytesRead } = await handle.read(bytes, size, bytes.length - size, null);
        if (!bytesRead) break;
        size += bytesRead;
      }
      if (size !== stat.size) throw new Error('Render file changed');
      return bytes.subarray(0, size);
    } finally { await handle.close(); }
  });
};
const inventory = async (root, prefix = '', entries = [], budget = { remaining: 256 }) => withDirectory(root, prefix ? prefix.split('/') : [], async dir => {
  const names = await readdir(dir, { withFileTypes: true });
  if (names.length > 256) throw new Error('Too many render files');
  for (const entry of names) {
    if (--budget.remaining < 0) throw new Error('Too many render entries');
    const name = prefix ? `${prefix}/${entry.name}` : entry.name;
    if (!safePath(name) || entry.isSymbolicLink()) throw new Error('Invalid render output');
    if (entry.isDirectory()) await inventory(root, name, entries, budget);
    else if (entry.isFile()) entries.push(name);
    else throw new Error('Invalid render output');
    if (entries.length > 129) throw new Error('Too many render files');
  }
  return entries;
});
const validateRequest = request => {
  if (!request || request.version !== VERSION || !ID.test(request.job_id || '') ||
      !ID.test(request.diagram_id || '') || !['codegen', 'png'].includes(request.operation) ||
      !Number.isSafeInteger(request.deadline) || request.deadline <= Date.now() || request.deadline > Date.now() + DEADLINE_MS ||
      typeof request.view !== 'string' || request.view.length > 128) throw new Error('Invalid render request');
  validateSource(request.content, request.revision);
  return request;
};
const collectResult = async (root, request) => {
  const result = JSON.parse((await readFileAt(root, 'result.json', 65536)).toString('utf8'));
  if (result.version !== VERSION || result.job_id !== request.job_id || result.diagram_id !== request.diagram_id ||
      result.revision !== request.revision || result.operation !== request.operation || typeof result.ok !== 'boolean') throw new Error('Invalid render result');
  if (!result.ok) throw new RequestError(502, 'render_failed');
  if (!Array.isArray(result.files) || !result.files.length || result.files.length > 128 ||
      new Set(result.files).size !== result.files.length || result.files.some(name => !safePath(name) || name === 'result.json')) throw new Error('Invalid render manifest');
  const actual = (await inventory(root)).sort();
  if (JSON.stringify(actual) !== JSON.stringify([...result.files, 'result.json'].sort())) throw new Error('Unexpected render output');
  if (request.operation === 'codegen' && (result.files.length !== 1 || result.files[0] !== 'likec4-webcomponent.js')) throw new Error('Invalid bundle manifest');
  let total = 0;
  const files = [];
  for (const name of result.files) {
    if (request.operation === 'png' && !name.endsWith('.png')) throw new Error('Invalid PNG name');
    const bytes = await readFileAt(root, name, Math.min(request.operation === 'codegen' ? MAX_BUNDLE : MAX_FILE, MAX_OUTPUT - total));
    total += bytes.length;
    if (request.operation === 'png' && !bytes.subarray(0, 8).equals(Buffer.from('89504e470d0a1a0a', 'hex'))) throw new Error('Invalid PNG content');
    files.push({ name, bytes });
  }
  return files;
};
const createRenderClient = ({ input = process.env.LIKEC4_RENDER_INPUT || '/jobs/input', output = process.env.LIKEC4_RENDER_OUTPUT || '/jobs/output', timeout = DEADLINE_MS, pollMs = 50 } = {}) => async (content, scope, operation) => {
  validateSource(content, scope.revision);
  const jobId = randomUUID();
  const request = validateRequest({ version: VERSION, job_id: jobId, diagram_id: scope.diagram_id,
    revision: scope.revision, operation, content, view: operation === 'png' ? process.env.LIKEC4_EXPORT_VIEW || '' : '',
    deadline: Date.now() + Math.min(timeout, DEADLINE_MS) });
  const temporary = join(input, `.${jobId}.tmp`);
  const published = join(input, `${jobId}.json`);
  const started = Date.now();
  let outcome = 'error';
  try {
    // Recover bounded input storage after a broker crash; no live request is
    // removed. Only broker-owned names in the input mount are eligible.
    for (const name of await readdir(input)) {
      if (/^\.[a-f0-9-]{36}\.tmp$/.test(name)) {
        try {
          if ((await lstat(join(input, name))).mtimeMs < Date.now() - DEADLINE_MS) await rm(join(input, name), { force: true });
        } catch (error) { if (error.code !== 'ENOENT') throw error; }
        continue;
      }
      if (!name.endsWith('.json') || !ID.test(name.slice(0, -5))) continue;
      try {
        const old = JSON.parse((await readFileAt(input, name, 6 * 1024 * 1024 + 65536)).toString('utf8'));
        if (Number.isSafeInteger(old.deadline) && old.deadline < Date.now()) await rm(join(input, name), { force: true });
      } catch (error) { if (error.code !== 'ENOENT') throw error; }
    }
    await writeFile(temporary, JSON.stringify(request), { flag: 'wx', mode: 0o600 });
    await rename(temporary, published);
    while (Date.now() < request.deadline) {
      try {
        // Output is mounted read-only. Pin its job directory while consuming it.
        const files = await withDirectory(output, [jobId], root => collectResult(root, request));
        if (Date.now() >= request.deadline) break;
        outcome = 'success';
        return files;
      } catch (error) { if (error.code !== 'ENOENT') throw error; }
      await delay(pollMs);
    }
    outcome = 'timeout';
    throw new RequestError(504, 'render_timeout');
  } catch (error) {
    if (['ENOSPC', 'EDQUOT'].includes(error.code)) throw new RequestError(503, 'render_storage_full');
    throw error;
  } finally {
    // Removal is the acknowledgement/cancellation. Renderer owns output cleanup.
    await rm(temporary, { force: true });
    await rm(published, { force: true });
    console.info(JSON.stringify({ event: 'likec4.render', job_id: jobId, diagram_id: scope.diagram_id,
      operation, outcome, duration_ms: Date.now() - started }));
  }
};

module.exports = { VERSION, DEADLINE_MS, MAX_OUTPUT, MAX_BUNDLE, ID, safePath, delay, readFileAt, inventory, validateRequest, collectResult, createRenderClient };
