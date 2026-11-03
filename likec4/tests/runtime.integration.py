#!/usr/bin/env python3
"""Inspect only this task's disposable renderer containers."""
import argparse
import json
import subprocess

parser = argparse.ArgumentParser()
parser.add_argument("--project", default="cinta-likec4-security")
parser.add_argument("--uid", default="1000")
args = parser.parse_args()
for operation in ("codegen", "png"):
    name = f"{args.project}-likec4-{operation}-1"
    container = json.loads(subprocess.check_output(["docker", "inspect", name]))[0]
    host = container["HostConfig"]
    assert host["NetworkMode"] == "none" and not host["Privileged"]
    assert not host["PidMode"] and host["Init"] and host["ReadonlyRootfs"]
    assert host["Memory"] == 2 * 1024**3 and host["NanoCpus"] == 1500000000
    assert host["PidsLimit"] == 256 and host["ShmSize"] == 512 * 1024**2
    assert "ALL" in host["CapDrop"] and "no-new-privileges:true" in host["SecurityOpt"]
    assert "size=512m" in host["Tmpfs"]["/tmp"]
    assert container["Config"]["User"] == f"{args.uid}:{args.uid}"
    keys = {value.split("=", 1)[0] for value in container["Config"]["Env"]}
    assert not any(key.startswith(("SEAWEEDFS_", "DJANGO_", "DATABASE_", "AWS_")) or "TOKEN" in key for key in keys)
    mounts = {mount["Destination"]: mount for mount in container["Mounts"]}
    assert not mounts["/jobs/input"]["RW"] and mounts["/jobs/output"]["RW"]
    assert not any("docker.sock" in mount["Destination"] for mount in container["Mounts"])
    probe = r"""
      const assert=require('node:assert/strict');const net=require('node:net');
      assert.ok(Object.values(require('node:os').networkInterfaces()).flat().every(x=>x.internal));
      const targets=['1.1.1.1','169.254.169.254','172.17.0.1','web','seaweedfs'];
      Promise.all(targets.map(host=>new Promise((resolve,reject)=>{
        const socket=net.createConnection({host,port:80});
        socket.once('connect',()=>{socket.destroy();reject(new Error('Unexpected egress: '+host));});
        socket.once('error',()=>resolve());socket.setTimeout(750,()=>{socket.destroy();resolve();});
      }))).then(()=>console.log('PASS: offline network probes')).catch(e=>{console.error(e);process.exitCode=1;});
    """
    subprocess.run(["docker", "exec", name, "node", "-e", probe], check=True)
    print(f"PASS: {name} resource, namespace, credential and mount controls")
