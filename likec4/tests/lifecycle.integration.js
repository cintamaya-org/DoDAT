const assert = require('node:assert/strict');
const { mkdtemp, readFile, rm } = require('node:fs/promises');
const { tmpdir } = require('node:os');
const { join } = require('node:path');
const { supervise, liveChildren } = require('/opt/likec4/worker/process_supervisor');

module.exports = async () => {
  const work = await mkdtemp(join(tmpdir(), 'likec4-lifecycle-'));
  try {
    for (const hangs of [true, false]) {
      const code = `const {spawn}=require('child_process');
        const child=spawn(process.execPath,['-e','setInterval(()=>{},1000)'],{detached:true,stdio:'ignore'});
        require('fs').writeFileSync('detached.pid',String(child.pid)); child.unref();
        ${hangs ? 'setInterval(()=>{},1000);' : ''}`;
      const task = supervise(process.execPath, ['-e', code], { cwd: work, env: { PATH: process.env.PATH }, deadline: Date.now() + 1000 });
      if (hangs) await assert.rejects(task, /deadline exceeded/);
      else await task;
      const pid = Number(await readFile(join(work, 'detached.pid'), 'utf8'));
      assert.ok(!liveChildren().includes(pid), 'Detached Chromium-like process survived cleanup');
      assert.deepEqual(liveChildren(), []);
    }
    for (const signal of ['SIGSTOP', 'SIGTSTP', 'SIGTTIN', 'SIGTTOU']) {
      const code = `try { process.kill(process.ppid,'${signal}'); process.exit(2); } catch (error) { process.exit(error.code === 'EPERM' ? 0 : 3); }`;
      await supervise(process.execPath, ['-e', code], { cwd: work, env: {}, deadline: Date.now() + 1000 });
    }
    await supervise(process.execPath, ['-e', 'process.exit(0)'], { cwd: work, env: {}, deadline: Date.now() + 1000 });
    console.log(JSON.stringify({ event: 'integration.lifecycle_ok', cases: ['timeout', 'detached_groups', 'early_exit', 'supervisor_stop_denied', 'recovery'] }));
  } finally { await rm(work, { recursive: true, force: true }); }
};
