const { spawn } = require('child_process');
const { readFileSync, readdirSync } = require('fs');
const { basename } = require('path');

// Each renderer owns a private PID namespace containing only Docker's init,
// this supervisor and its job. Never perform namespace-wide cleanup on a host
// or when imported by an unrelated process (including ordinary unit tests).
const assertPrivateNamespace = () => {
  const init = readFileSync('/proc/1/cmdline', 'utf8').split('\0');
  if (basename(init[0]) !== 'docker-init' || !process.argv[1] || !init.includes(process.argv[1])) {
    throw new Error('Renderer requires its own PID namespace and Docker init');
  }
};
const liveChildren = () => readdirSync('/proc').filter(name => /^[0-9]+$/.test(name)).map(Number).filter(pid => {
  if (pid === 1 || pid === process.pid) return false;
  try {
    const stat = readFileSync(`/proc/${pid}/stat`, 'utf8');
    return stat.slice(stat.lastIndexOf(')') + 2).split(' ')[0] !== 'Z';
  } catch (error) { if (error.code === 'ENOENT' || error.code === 'ESRCH') return false; throw error; }
});
const killChildren = () => {
  assertPrivateNamespace();
  for (const pid of liveChildren()) {
    try { process.kill(pid, 'SIGKILL'); } catch (error) { if (error.code !== 'ESRCH') throw error; }
  }
};
const reapChildren = async () => {
  // Chromium launches detached groups. Killing only the CLI process group
  // misses those descendants; init reaps adopted children after SIGKILL.
  for (let attempt = 0; attempt < 100; attempt++) {
    killChildren();
    if (!liveChildren().length) return;
    await new Promise(resolve => setTimeout(resolve, 20));
  }
  throw new Error('Renderer could not terminate job descendants');
};
const supervise = async (binary, args, { cwd, env, deadline }) => {
  assertPrivateNamespace();
  try {
    await new Promise((resolve, reject) => {
      const child = spawn(binary, args, { cwd, env, detached: true, stdio: 'ignore' });
      let timedOut = false;
      const timer = setTimeout(() => {
        timedOut = true;
        try { killChildren(); } catch (error) { reject(error); }
      }, Math.max(1, deadline - Date.now()));
      child.once('error', error => { clearTimeout(timer); reject(error); });
      child.once('exit', code => {
        clearTimeout(timer);
        if (code === 0 && !timedOut) resolve();
        else reject(new Error(timedOut ? 'Render deadline exceeded' : 'Render command failed'));
      });
    });
  } finally {
    try { await reapChildren(); }
    catch { const error = new Error('Renderer cleanup failed'); error.fatal = true; throw error; }
  }
};

module.exports = { assertPrivateNamespace, liveChildren, supervise };
