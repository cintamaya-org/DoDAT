const test = require('node:test');
const assert = require('node:assert/strict');
const { EventEmitter } = require('node:events');
const { readFileSync } = require('node:fs');
const { basename } = require('node:path');
const { runInNewContext } = require('node:vm');

const filename = require.resolve('../worker/process_supervisor');
const source = readFileSync(filename, 'utf8');
const entrypoint = '/opt/likec4/worker/renderer.js';

// Run the actual module with a fake /proc, process, and clock. No host PID can
// reach process.kill, and descendant retries need no wall-clock sleeps.
const fixture = ({ init = `/sbin/docker-init\0node\0${entrypoint}\0`,
  argv = ['node', entrypoint], processes = {}, kill, spawn } = {}) => {
  const children = new Map(Object.entries(processes));
  const child = new EventEmitter();
  const killed = []; const spawned = []; const timers = []; const cleared = [];
  const module = { exports: {} };
  const modules = {
    child_process: { spawn: (...args) => { spawned.push(args); if (spawn) spawn(child); return child; } },
    fs: {
      readdirSync: path => {
        assert.equal(path, '/proc');
        return ['1', '2', 'self', 'net', ...children.keys()];
      },
      readFileSync: path => {
        if (path === '/proc/1/cmdline') return init;
        const match = /^\/proc\/(\d+)\/stat$/.exec(path);
        assert.ok(match, `Unexpected read: ${path}`);
        const state = children.get(match[1]);
        if (state instanceof Error) throw state;
        return `${match[1]} (renderer (child)) ${state} 1 0 0`;
      },
    },
    path: { basename },
  };
  runInNewContext(source, {
    module,
    require: name => { assert.ok(modules[name], `Unexpected module: ${name}`); return modules[name]; },
    process: {
      pid: 2, argv,
      kill: (pid, signal) => {
        killed.push([pid, signal]);
        if (kill) kill(pid, children);
        else children.delete(String(pid));
      },
    },
    Date: { now: () => 1000 },
    setTimeout: (callback, delay) => {
      const timer = { callback, delay }; timers.push(timer);
      if (delay === 20) queueMicrotask(callback);
      return timer;
    },
    clearTimeout: timer => cleared.push(timer),
  }, { filename });
  return { ...module.exports, child, children, killed, spawned, timers, cleared };
};
const options = { cwd: '/job', env: { LANG: 'C' }, deadline: 2000 };

test('namespace check accepts only Docker init running this entrypoint', () => {
  fixture().assertPrivateNamespace();
  for (const config of [{ init: `/sbin/init\0${entrypoint}\0` },
    { init: '/sbin/docker-init\0another-script.js\0' }, { argv: ['node'] }]) {
    assert.throws(() => fixture(config).assertPrivateNamespace(), /own PID namespace and Docker init/);
  }
});

test('child discovery skips init, self, zombies, and processes that disappeared', () => {
  const disappeared = code => Object.assign(new Error('gone'), { code });
  const supervisor = fixture({ processes: {
    3: 'S', 4: 'Z', 5: disappeared('ENOENT'), 6: disappeared('ESRCH'), 7: 'R',
  } });
  assert.deepEqual(Array.from(supervisor.liveChildren()), [3, 7]);
});

test('child discovery propagates unexpected proc errors', () => {
  const error = Object.assign(new Error('proc denied'), { code: 'EACCES' });
  assert.throws(() => fixture({ processes: { 3: error } }).liveChildren(), error);
});

test('supervision refuses unsafe namespace before spawning or killing', async () => {
  const supervisor = fixture({ init: '/sbin/init\0', processes: { 3: 'R' } });
  await assert.rejects(supervisor.supervise('likec4', [], options), /own PID namespace/);
  assert.deepEqual(supervisor.spawned, []);
  assert.deepEqual(supervisor.killed, []);
});

test('successful command uses isolated process group and cleans up descendants', async () => {
  const supervisor = fixture({ processes: { 3: 'R', 4: 'Z' } });
  const pending = supervisor.supervise('likec4', ['export', 'png'], options);
  supervisor.child.emit('exit', 0);
  await pending;
  assert.deepEqual(supervisor.spawned[0].slice(0, 2), ['likec4', ['export', 'png']]);
  const config = supervisor.spawned[0][2];
  assert.equal(config.cwd, options.cwd);
  assert.equal(config.env, options.env);
  assert.equal(config.detached, true);
  assert.equal(config.stdio, 'ignore');
  assert.deepEqual(supervisor.killed, [[3, 'SIGKILL']]);
  assert.equal(supervisor.timers[0].delay, 1000);
  assert.deepEqual(supervisor.cleared, [supervisor.timers[0]]);
});

for (const code of [1, null]) test(`command failure (${code}) still cleans descendants`, async () => {
  const supervisor = fixture({ processes: { 3: 'R' } });
  const pending = supervisor.supervise('likec4', [], options);
  supervisor.child.emit('exit', code);
  await assert.rejects(pending, /Render command failed/);
  assert.deepEqual(supervisor.killed, [[3, 'SIGKILL']]);
  assert.deepEqual(supervisor.cleared, [supervisor.timers[0]]);
});

test('spawn error propagates and clears deadline before descendant cleanup', async () => {
  const supervisor = fixture({ processes: { 3: 'R' } });
  const error = new Error('spawn failed');
  const pending = supervisor.supervise('likec4', [], options);
  supervisor.child.emit('error', error);
  await assert.rejects(pending, error);
  assert.deepEqual(supervisor.killed, [[3, 'SIGKILL']]);
  assert.deepEqual(supervisor.cleared, [supervisor.timers[0]]);
});

test('synchronous spawn failure still cleans descendants', async () => {
  const error = new Error('spawn failed');
  const supervisor = fixture({ processes: { 3: 'R' }, spawn: () => { throw error; } });
  await assert.rejects(supervisor.supervise('likec4', [], options), error);
  assert.deepEqual(supervisor.killed, [[3, 'SIGKILL']]);
});

test('deadline rejects even when killed child reports successful exit', async () => {
  const supervisor = fixture({ processes: { 3: 'R' } });
  const pending = supervisor.supervise('likec4', [], { ...options, deadline: 999 });
  assert.equal(supervisor.timers[0].delay, 1);
  supervisor.timers[0].callback();
  supervisor.child.emit('exit', 0);
  await assert.rejects(pending, /Render deadline exceeded/);
  assert.deepEqual(supervisor.killed, [[3, 'SIGKILL']]);
});

test('cleanup ignores descendants that disappear during SIGKILL', async () => {
  const supervisor = fixture({ processes: { 3: 'R' }, kill: (pid, children) => {
    children.delete(String(pid));
    throw Object.assign(new Error('gone'), { code: 'ESRCH' });
  } });
  const pending = supervisor.supervise('likec4', [], options);
  supervisor.child.emit('exit', 0);
  await pending;
  assert.deepEqual(supervisor.killed, [[3, 'SIGKILL']]);
});

test('deadline kill errors propagate after final cleanup succeeds', async () => {
  const error = Object.assign(new Error('kill denied'), { code: 'EPERM' });
  let attempts = 0;
  const supervisor = fixture({ processes: { 3: 'R' }, kill: (pid, children) => {
    if (++attempts === 1) throw error;
    children.delete(String(pid));
  } });
  const pending = supervisor.supervise('likec4', [], options);
  supervisor.timers[0].callback();
  await assert.rejects(pending, error);
  assert.equal(attempts, 2);
});

test('cleanup retries until descendants leave proc', async () => {
  let attempts = 0;
  const supervisor = fixture({ processes: { 3: 'R' }, kill: (pid, children) => {
    if (++attempts === 3) children.delete(String(pid));
  } });
  const pending = supervisor.supervise('likec4', [], options);
  supervisor.child.emit('exit', 0);
  await pending;
  assert.equal(attempts, 3);
  assert.deepEqual(supervisor.timers.slice(1).map(timer => timer.delay), [20, 20]);
});

test('unreapable descendants mark renderer cleanup failure as fatal', async () => {
  const supervisor = fixture({ processes: { 3: 'R' }, kill: () => {} });
  const pending = supervisor.supervise('likec4', [], options);
  supervisor.child.emit('exit', 0);
  await assert.rejects(pending, error => error.message === 'Renderer cleanup failed' && error.fatal === true);
  assert.equal(supervisor.killed.length, 100);
});

test('unexpected cleanup signal errors are fatal', async () => {
  const supervisor = fixture({ processes: { 3: 'R' }, kill: () => {
    throw Object.assign(new Error('kill denied'), { code: 'EPERM' });
  } });
  const pending = supervisor.supervise('likec4', [], options);
  supervisor.child.emit('exit', 0);
  await assert.rejects(pending, error => error.message === 'Renderer cleanup failed' && error.fatal === true);
  assert.deepEqual(supervisor.killed, [[3, 'SIGKILL']]);
});
