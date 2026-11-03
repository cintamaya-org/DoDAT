#!/usr/bin/env python3
"""Check effective Compose configurations without loading operator secrets."""
import json
import os
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parents[2]
STACKS = [
    ["docker-compose.yml"],
    ["docker-compose.dev.yml"],
    ["docker-compose.scaling.dev.yml"],
    ["deploy/docker-compose.base.yml", "deploy/docker-compose.prod.yml"],
    ["deploy/docker-compose.base.yml", "deploy/docker-compose.test.yml"],
    ["docker-compose.yml", "deploy/demo/docker-compose.demo.yml"],
    ["likec4/docker-compose.yml"],
]


def check_stack(files, uid):
    env = {"PATH": os.environ["PATH"], "APP_UID": uid, "APP_GID": uid,
           "APP_NAME": "isolation_check", "HOST_PORT": "8100", "APP_PORT": "8000",
           "DJANGO_SETTINGS_MODULE": "cintafactory.settings", "APP_MODULE": "cintafactory.wsgi:application"}
    # Supply throwaway values only to mandatory substitutions; defaults retain
    # their real shape. Never print rendered environments or read local .env.
    for path in ROOT.rglob("*compose*.yml"):
        if ".venv" in path.parts:
            continue
        for key in re.findall(r"\$\{([A-Z0-9_]+):\?", path.read_text()):
            env.setdefault(key, "isolation-check-placeholder-0123456789")
    command = ["docker", "compose", "--env-file", "/dev/null"]
    for name in files:
        command += ["-f", name]
    result = subprocess.run(command + ["config", "--format", "json"], cwd=ROOT, env=env, capture_output=True, text=True)
    if result.returncode:
        raise AssertionError(f"Compose configuration failed for {files}: {result.stderr}")
    config = json.loads(result.stdout)
    services = config["services"]
    for name in ("likec4", "likec4-exporter", "likec4-codegen", "likec4-png"):
        if name not in services:
            assert name == "likec4-exporter" and files == ["likec4/docker-compose.yml"]
            continue
        service = services[name]
        assert service["user"] == f"{uid}:{uid}", name
        assert service["read_only"] and service["init"], name
        assert "ALL" in service["cap_drop"], name
        assert "no-new-privileges:true" in service["security_opt"], name
        assert service["pids_limit"] > 0 and int(service["mem_limit"]) > 0 and float(service["cpus"]) > 0, name
        assert not service.get("ports"), name
        assert not any(mount["type"] == "bind" for mount in service["volumes"]), name
        mounts = {mount["target"]: mount for mount in service["volumes"]}
        renderer = name in {"likec4-codegen", "likec4-png"}
        assert mounts["/jobs/input"].get("read_only", False) == renderer, name
        assert mounts["/jobs/output"].get("read_only", False) != renderer, name
        if renderer:
            assert service["network_mode"] == "none" and not service.get("networks"), name
            assert set(service["environment"]) == {"LIKEC4_RENDER_OPERATION"}, name
            assert service["build"]["target"] == "renderer", name
            profile = next(value.split("=", 1)[1] for value in service["security_opt"] if value.startswith("seccomp="))
            profile = json.loads((ROOT / Path(files[0]).parent / profile).read_text())
            assert profile["defaultAction"] == "SCMP_ACT_ERRNO"
            assert any(set(rule["names"]) >= {"clone", "unshare", "setns"} and rule["action"] == "SCMP_ACT_ALLOW" for rule in profile["syscalls"])
            signal_args = {"kill": 1, "tkill": 1, "tgkill": 2, "rt_sigqueueinfo": 1, "rt_tgsigqueueinfo": 2, "pidfd_send_signal": 1}
            for rule in profile["syscalls"]:
                if rule["action"] != "SCMP_ACT_ALLOW":
                    continue
                for syscall in set(rule["names"]) & signal_args.keys():
                    # An unconditional ALLOW overrides a narrower deny in
                    # libseccomp; require bounded allowed ranges instead.
                    assert any(arg["index"] == signal_args[syscall] and
                               (arg["op"], arg["value"]) in {("SCMP_CMP_LT", 19), ("SCMP_CMP_GT", 22)}
                               for arg in rule.get("args", [])), syscall
        else:
            assert service["build"]["target"] == "broker", name
            assert "likec4 dev" not in str(service["command"]), name
            if name == "likec4":
                assert "LIKEC4_METADATA_TOKEN" not in service.get("environment", {}), name
    for name in ("codegen_input", "png_input", "codegen_output", "png_output"):
        options = config["volumes"][name]["driver_opts"]
        assert options["type"] == "tmpfs" and "size=" in options["o"] and "nr_inodes=" in options["o"] and f"uid={uid}" in options["o"], name


if __name__ == "__main__":
    for stack in STACKS:
        for uid in ("1000", "2001"):
            check_stack(stack, uid)
        print(f"PASS: {' + '.join(stack)} (default and custom UID/GID)")
