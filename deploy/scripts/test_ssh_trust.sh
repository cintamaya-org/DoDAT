#!/usr/bin/env bash
set -euo pipefail

# Exercise the real workflow setup locally; no SSH connection or deployment secret.
python3 - <<'PY'
import os
from pathlib import Path
import subprocess
import tempfile
import textwrap

workflow = Path('.github/workflows/deploy.yml').read_text()
step = workflow.split('- name: Configuration de la connexion SSH', 1)[1]
block = step.split('        run: |\n', 1)[1].split('\n      #', 1)[0]
script = textwrap.dedent(block)
assert 'ssh-keyscan' not in workflow
assert workflow.count('StrictHostKeyChecking=yes') == 3
assert workflow.count('BatchMode=yes') == 3

with tempfile.TemporaryDirectory(prefix='cinta-ssh-trust-') as directory:
    root = Path(directory)
    key = root / 'host-key'
    subprocess.run(['ssh-keygen', '-q', '-t', 'ed25519', '-N', '', '-f', str(key)], check=True)
    public_key = key.with_suffix('.pub').read_text().strip()
    isolated_script = script.replace('~/.ssh', str(root / 'client'))
    env = {**os.environ, 'VPS_HOST': 'vps.example.test', 'VPS_USER': 'test-user',
           'VPS_SSH_KEY': 'unused-local-test-key',
           'VPS_KNOWN_HOSTS': 'vps.example.test ' + public_key}
    subprocess.run(['bash', '-c', isolated_script], env=env, check=True, capture_output=True)
    for known_hosts in ('', 'other.example.test ' + public_key):
        result = subprocess.run(
            ['bash', '-c', isolated_script], env={**env, 'VPS_KNOWN_HOSTS': known_hosts},
            capture_output=True,
        )
        assert result.returncode != 0, 'Missing or wrong-host trust must fail closed'
print('SSH setup: pinned host accepted; missing/wrong-host pin rejected; strict flags required.')
PY
