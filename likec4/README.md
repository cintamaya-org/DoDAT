# LikeC4 editor and offline renderers

Django authorizes every diagram request. The `likec4` editor broker reads/saves
source in SeaweedFS; `likec4-exporter` publishes validated PNGs and metadata.
Neither broker executes the CLI. Two separate containers generate webcomponents
and PNGs from a single `diagram.c4`, with no network or service credentials.
The PNG renderer uses loopback internally for Vite/Chromium.

Generated JavaScript runs only in an iframe with `sandbox="allow-scripts"` and
an enforced response-header CSP. The trusted editor fetches authorized bundle
bytes and transfers them using a nonce-bound MessageChannel. The viewer has no
privileged request bridge, parent DOM access, cookies, or persistent storage.
External images/fonts are blocked; missing assets use browser fallbacks. The
editor still uses explicitly allowed Monaco CDN resources.

## Build and run

Use Docker Compose 2.20+ with `include` support. From repository root, start the
normal application stack with its existing environment configuration:

```bash
docker compose up -d --build
```

The production/test, demo, development, scaling and standalone files share
`compose.brokers.yml` and `compose.renderers.yml`. Renderer services are
`likec4-codegen` and `likec4-png`; do not publish their ports or attach networks.
The standalone file is an internal editor service, not an authenticated public
application. Configure its SeaweedFS connection and use the Django proxy.

Explicit image targets, when needed:

```bash
docker build --target broker -t likec4-broker:local likec4
docker build --target renderer -t likec4-renderer:local likec4
```

Toolchain versions are committed: LikeC4 1.60.0, Playwright/Chromium 1.64.0,
pnpm 12.6.0 and Graphviz 2.42.2-9ubuntu0.1. The base image is digest-pinned;
Node comes from that image. Change the manifest/lock, matching browser image,
seccomp profile and runtime tests together. Version overrides and `latest`
fallbacks are intentionally absent. The seccomp profile is Playwright v1.64.0's
profile plus `chroot`, required by Chromium inside its unprivileged user namespace, and filters denying job-control stop signals so a child cannot suspend its supervisor deadline.
No `SYS_ADMIN`, host IPC, or sandbox bypass is required.

## Jobs and writable storage

Job requests/results use versioned JSON and random IDs with atomic publication.
Input mounts are writable only to brokers; output mounts only to renderers.
Both are tmpfs-backed, size/inode bounded, and shared only within each pair.
Clients check identity/revision, file count/size, path containment and PNG
signatures before copying results to private broker storage. Renderer subprocess
cleanup covers separate process groups inside a dedicated PID namespace; `init:
true` is required. Sources never include uploaded project/configuration files.

Each broker serializes rendering with eight pending jobs. A dispatched job has a
120-second deadline. Broken/full IPC storage fails closed; expired requests,
acknowledged results and abandoned staging files are cleaned up. Restart may
lose jobs and caches; generated bundle cache is bounded to 128 MiB/64 revisions with one-hour expiry. Source remains in SeaweedFS and can regenerate output.

Each renderer has 2 GiB memory, 1.5 CPUs, 256 PIDs, 512 MiB shared memory and
512 MiB temporary storage. Brokers have 512 MiB memory, 0.5 CPU and 64 PIDs.
`APP_UID`/`APP_GID` default to 1000; build and runtime must use matching values.
Changing IDs requires recreating **only the four disposable IPC volumes**.
An existing optional PNG cache volume must have matching ownership; do not
remove application/media/database volumes. Source code stays root-owned.

## Credentials and containment

Only brokers receive storage keys and the internal API token. Only exporter
receives the Django metadata callback token. Offline workers receive neither.
Their network namespace has loopback only. Brokers retain network access needed
for authorized storage and callbacks. No Docker socket is mounted into services.

The viewer prevents access to the application's origin and blocks remote asset
loading. It does not guarantee zero browser traffic: a compromised script could
navigate its own sandboxed frame. Browser/CLI vulnerabilities remain residual
risks; these boundaries reduce their authority and impact.

## Verification and rollback

Unit tests use Node 22 in CI and require no package installation. CI writes
`coverage/likec4.lcov` with Node's built-in coverage reporter; SonarQube imports
it alongside Python's `coverage.xml`. Tests are classified separately from
application code. Generated/vendor files are excluded from analysis; startup
wiring and viewer HTML are excluded only from unit coverage. Permission checks,
settings validation and render workers remain in coverage scope. See
[`sonar-project.properties`](../sonar-project.properties) and
[`.coveragerc`](../.coveragerc) for the explicit exclusions.

```bash
node --test likec4/tests/*.test.js
python3 deploy/scripts/test_likec4_isolation.py
docker build --target renderer -t cintafactory-likec4-security:renderer likec4
docker compose --env-file /dev/null -p cinta-likec4-security -f likec4/tests/compose.integration.yml up -d --no-build likec4-codegen likec4-png
docker compose --env-file /dev/null -p cinta-likec4-security -f likec4/tests/compose.integration.yml run --rm --no-deps integration
docker compose --env-file /dev/null -p cinta-likec4-security -f likec4/tests/compose.integration.yml down -v
```

Integration tests exercise real offline codegen/PNG, visible diagram rendering,
hostile labels, browser isolation, descendant cleanup and recovery. CI also
scans built images for fixable HIGH/CRITICAL vulnerabilities.

Stage changes on test deployment before public demo/production. If isolation
fails, set `LIKEC4_RENDER_ENABLED=0` for brokers and `LIKEC4_EXPORT_ENABLED=0` for
Django, then recreate those services. Authorized source reads/saves remain
available; new rendering is refused. Running jobs retain their deadline. Keep
previous images for investigation; do not restore same-origin execution or
network-enabled renderers as an automatic fallback.
