// Runs ONLY inside the credential-free container with network_mode: none.
const { mkdir, mkdtemp, readFile, readdir, rename, rm, writeFile } = require('fs').promises;
const { join } = require('path');
const { tmpdir } = require('os');
const { VERSION, ID, MAX_OUTPUT, MAX_BUNDLE, delay, readFileAt, inventory, validateRequest } = require('./render_protocol');
const { assertPrivateNamespace, supervise } = require('./process_supervisor');

const runCommand = (args, cwd, deadline) => {
  // No ambient environment reaches CLI, Vite, Chromium or executable config.
  const env = { PATH: process.env.PATH, HOME: '/tmp', TMPDIR: '/tmp', XDG_CACHE_HOME: '/tmp/cache',
    NODE_ENV: 'production', LANG: 'C.UTF-8', PLAYWRIGHT_BROWSERS_PATH: '/ms-playwright' };
  return supervise('/opt/likec4/node_modules/.bin/likec4', args, { cwd, env, deadline });
};
const render = async (request, work, exec = runCommand) => {
  const output = join(work, 'output');
  await mkdir(output, { mode: 0o700 });
  await writeFile(join(work, 'diagram.c4'), request.content, { flag: 'wx', mode: 0o600 });
  const args = request.operation === 'codegen'
    ? ['codegen', 'webcomponent', '-o', join(output, 'likec4-webcomponent.js'), '.']
    : ['export', 'png', '--chromium-sandbox', '-o', output, '.', ...(request.view ? ['--filter', request.view] : [])];
  await exec(args, work, request.deadline);
  const files = await inventory(output);
  if (!files.length || files.length > 128) throw new Error('Invalid output count');
  if (request.operation === 'codegen' && (files.length !== 1 || files[0] !== 'likec4-webcomponent.js')) throw new Error('Bundle must be self-contained');
  let total = 0;
  const result = [];
  for (const name of files.sort()) {
    if (request.operation === 'png' && !name.endsWith('.png')) throw new Error('Unexpected output');
    const bytes = await readFileAt(output, name, Math.min(request.operation === 'codegen' ? MAX_BUNDLE : 32 * 1024 * 1024, MAX_OUTPUT - total));
    total += bytes.length;
    result.push({ name, bytes });
  }
  return result;
};
const runOnce = async ({ input, output, operation, execute = render }) => {
  const names = await readdir(input);
  if (names.length > 32) throw new Error('Input queue too large');
  const requests = new Set(names.filter(name => ID.test(name.slice(0, -5)) && name.endsWith('.json')).map(name => name.slice(0, -5)));
  // Broker deletion acknowledges output or cancels a timed out job. Cleanup also
  // runs after a renderer restart, without broker write access to this volume.
  for (const name of await readdir(output)) {
    if ((ID.test(name) && !requests.has(name)) || /^\.[a-f0-9-]{36}\.tmp$/.test(name)) await rm(join(output, name), { recursive: true, force: true });
  }
  for (const id of requests) {
    try { await readFileAt(output, `${id}/result.json`, 65536); continue; }
    catch (error) { if (error.code !== 'ENOENT') throw error; }
    let request;
    try {
      request = validateRequest(JSON.parse((await readFileAt(input, `${id}.json`, 6 * 1024 * 1024 + 65536)).toString('utf8')));
      if (request.job_id !== id || request.operation !== operation) throw new Error('Unexpected operation');
    } catch { continue; } // Expired/invalid requests are removed by their broker.
    const stage = join(output, `.${id}.tmp`);
    const work = await mkdtemp(join(tmpdir(), 'likec4-render-'));
    const result = { version: VERSION, job_id: id, diagram_id: request.diagram_id, revision: request.revision, operation, ok: false, files: [] };
    const started = Date.now();
    try {
      await mkdir(stage, { mode: 0o700 });
      const files = await execute(request, work);
      for (const { name, bytes } of files) {
        const parts = name.split('/'); parts.pop();
        if (parts.length) await mkdir(join(stage, ...parts), { recursive: true, mode: 0o700 });
        await writeFile(join(stage, name), bytes, { flag: 'wx', mode: 0o600 });
      }
      result.ok = Date.now() < request.deadline;
      result.files = files.map(file => file.name);
    } catch (error) {
      if (error.fatal) throw error;
      // Publish a bounded generic failure; never return tool logs.
    }
    finally { await rm(work, { recursive: true, force: true }); }
    try {
      await writeFile(join(stage, 'result.json'), JSON.stringify(result), { flag: 'wx', mode: 0o600 });
      await rename(stage, join(output, id));
    } finally { await rm(stage, { recursive: true, force: true }); }
    const peak = await readFile('/sys/fs/cgroup/memory.peak', 'utf8').catch(() => '');
    console.info(JSON.stringify({ event: 'likec4.worker_render', job_id: id, diagram_id: request.diagram_id,
      operation, outcome: result.ok ? 'success' : 'error', duration_ms: Date.now() - started,
      container_memory_peak_bytes: /^\d+\s*$/.test(peak) ? Number(peak.trim()) : undefined }));
    return;
  }
};
const start = async () => {
  assertPrivateNamespace();
  const operation = process.env.LIKEC4_RENDER_OPERATION;
  if (!['codegen', 'png'].includes(operation)) throw new Error('Renderer operation is required');
  await mkdir('/tmp/likec4-vite', { recursive: true, mode: 0o700 });
  await mkdir('/tmp/cache', { recursive: true, mode: 0o700 });
  const input = process.env.LIKEC4_RENDER_INPUT || '/jobs/input';
  const output = process.env.LIKEC4_RENDER_OUTPUT || '/jobs/output';
  // A broker crash must not leave valid-looking jobs consumable indefinitely.
  while (true) {
    try { await runOnce({ input, output, operation }); }
    catch (error) {
      console.warn(JSON.stringify({ event: 'likec4.worker_error', operation }));
      if (error.fatal) process.exit(1); // Docker tears down the private PID namespace.
    }
    await delay(50);
  }
};
if (require.main === module) start().catch(() => process.exit(1));
module.exports = { runCommand, render, runOnce };
