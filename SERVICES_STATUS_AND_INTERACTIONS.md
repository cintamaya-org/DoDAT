# SPDX-FileCopyrightText: 2026 Cintamaya <contact@cintamaya.com>
# SPDX-FileCopyrightText: 2026 Baptiste COQUELET <github.com/BaptisteCoquelet>
# SPDX-License-Identifier: CC-BY-ND-4.0

# CintaFactory Services and Interactions

This document reflects the current runtime topology, including scaled application services, monitoring services, and reliability operations.

## Services and Roles

| Service | Main role | Main outbound calls |
| --- | --- | --- |
| `traefik` | Edge reverse proxy / entrypoint in scaled topology | `web` |
| `web` (Django) | Main UI/API, health/metrics endpoints, orchestration | `pgbouncer`, `seaweedfs`, `clamav`, `drawio`, `drawio-export`, `likec4`, `likec4-exporter` |
| `worker` (Django async) | Executes async jobs (`exports.likec4`, `exports.drawio`, `exports.pdf`) | `pgbouncer`, `seaweedfs`, `clamav`, `drawio-export`, `likec4-exporter` |
| `pgbouncer` | PostgreSQL connection pooler | `db` |
| `db` (PostgreSQL) | Persistent relational data | None |
| `seaweedfs` | Object/file storage for media, diagrams, exports | None |
| `clamav` | Antivirus scanning daemon | None |
| `drawio` | draw.io editor UI | `drawio-export` |
| `drawio-export` | draw.io rendering/export worker | None |
| `likec4` | LikeC4 editor + preview service | `seaweedfs`, `web` (metadata callback) |
| `likec4-exporter` | LikeC4 PNG export worker | `seaweedfs`, `web` (metadata callback), LikeC4 CLI subprocess |
| `prometheus` | Metrics scraper and alert rule evaluator | `web` (`/metrics`), `cadvisor`, internal self-scrape |
| `cadvisor` | Container-level CPU/memory/network metrics exporter | Docker/container runtime stats |
| `loki` | Central log storage and query backend | Local TSDB/chunks storage |
| `promtail` | Docker log collector and forwarder | `loki` (`/loki/api/v1/push`) |
| `grafana` | Dashboards and ad-hoc metrics/log exploration | `prometheus`, `loki` |

## Runtime Interaction Diagram (Scaled Topology)

```mermaid
flowchart LR
    Browser[Browser]
    Traefik[traefik :80]
    Web[Django web :8000]
    Worker[Django worker]
    Pool[pgbouncer :6432]
    DB[(PostgreSQL db :5432)]
    SW[(SeaweedFS filer :8888)]
    AV[ClamAV clamd :3310]
    Drawio[drawio :8080]
    DrawioExp[drawio-export :8000]
    L4[likec4 editor :4173 / preview :5173]
    L4Exp[likec4-exporter :9000]

    Browser -->|HTTP UI/API| Traefik
    Traefik -->|forward| Web
    Browser -->|LikeC4 via Django proxy| Traefik

    Web -->|DB pooled connections| Pool
    Worker -->|DB pooled connections| Pool
    Pool -->|SQL| DB

    Web -->|PUT/GET/HEAD/DELETE files| SW
    Worker -->|read/write export assets| SW
    Web -->|PING/SCAN| AV
    Worker -->|scan + validation| AV

    Web -->|draw.io proxy/embed| Drawio
    Web -->|export requests| DrawioExp
    Worker -->|draw.io async export| DrawioExp

    Web -->|authenticated proxy| L4
    Web -->|enqueue export POST /export| L4Exp
    Worker -->|async export POST /export| L4Exp

    Drawio -->|EXPORT_URL /export| DrawioExp
    L4 -->|read/write .c4| SW
    L4 -->|POST metadata| Web
    L4Exp -->|read .c4 + write PNGs| SW
    L4Exp -->|POST export metadata| Web
```

## Monitoring-Only Diagram

```mermaid
flowchart LR
    Web[Django web :8000 /metrics]
    Cad[cAdvisor :8080/metrics]
    Prom[Prometheus :9090]
    PT[promtail]
    Loki[Loki :3100]
    Graf[Grafana :3000]
    Dock[Docker API/socket]
    Ops[Operator]

    Prom -->|scrape /metrics| Web
    Prom -->|scrape /metrics| Cad
    Prom -->|self-scrape| Prom

    PT -->|discover containers| Dock
    PT -->|push logs| Loki

    Graf -->|query PromQL| Prom
    Graf -->|query LogQL| Loki
    Ops -->|view dashboards/explore| Graf
    Ops -->|inspect alerts/targets| Prom
```

## Network Separation

The deployment uses explicit Docker networks to reduce lateral movement and exposure:

1. `edge`
- Public entrypoint network.
- Only `traefik` is attached to this network for inbound traffic.

2. `app`
- Internal application network for `web`, `worker`, exporters, and supporting services.
- Service-to-service traffic (proxying, export calls, metadata callbacks) stays here.

3. `data`
- Restricted data-plane network.
- `db` is on `data` only.
- `pgbouncer` bridges `app` <-> `data` so app services never connect directly to DB network endpoints.

4. `app_monitoring`
- Monitoring network used by `prometheus`, `grafana`, `loki`, `promtail`, and `cadvisor`.
- In this project it is configured as an external network (default name `cintaarchifactory_app`) so observability can scrape app services.

```mermaid
flowchart LR
    subgraph EDGE["edge network (public)"]
        Traefik[traefik]
    end

    subgraph APP["app network (internal app traffic)"]
        Web[web]
        Worker[worker]
        Drawio[drawio]
        DrawioExp[drawio-export]
        L4[likec4]
        L4Exp[likec4-exporter]
        AV[clamav]
        SW[seaweedfs]
        Pool[pgbouncer]
    end

    subgraph DATA["data network (restricted DB plane)"]
        DB[(db)]
        PoolData[pgbouncer]
    end

    subgraph MON["app_monitoring network (metrics/logs plane)"]
        Prom[prometheus]
        Graf[grafana]
        Loki[loki]
        PT[promtail]
        Cad[cadvisor]
    end

    Traefik --> Web
    Web --> Pool
    Worker --> Pool
    Pool --> PoolData
    PoolData --> DB
    Prom --> Web
    Prom --> Cad
    Graf --> Prom
    Graf --> Loki
    PT --> Loki
```

## Security Measures

Current hardening controls in the runtime and app layers:

1. Ingress and exposure controls
- Only edge service (`traefik`) publishes host ports in scaled topology.
- Internal services are private (`expose`) and reachable only through internal networks.

2. Least-privilege container profile
- `security_opt: no-new-privileges:true` on hardened services.
- `cap_drop: [ALL]` on `web`, `worker`, `likec4`, `likec4-exporter`.
- Non-root runtime for LikeC4 services (`user: APP_UID:APP_GID`).
- `read_only: true` and `tmpfs: /tmp` for LikeC4 services.

3. App and API surface protections
- Proxy hardening on draw.io and LikeC4 proxy routes (allowlists/path validation/body size limits).
- Security headers middleware and request correlation IDs.
- Token-based protection for LikeC4 metadata callback and authenticated proxying.

4. File and dependency safety
- ClamAV scan before attachment acceptance (fail-closed behavior).
- SeaweedFS interaction checks and readiness gates.
- Dependency-aware `/health/ready` prevents routing to non-ready instances.

5. Secret and policy controls
- Strict secret checks available for hardened runtime (`DJANGO_ENFORCE_STRICT_SECRETS=1`).
- CI secret scanning in workflow.

```mermaid
flowchart TD
    Ingress[Public Request]
    Edge[traefik edge]
    Auth[Auth/Token + proxy validation]
    App[web/worker runtime]
    AV[ClamAV scan gate]
    Storage[SeaweedFS]
    DB[(PostgreSQL via pgbouncer)]

    Ingress --> Edge
    Edge --> Auth
    Auth --> App
    App --> AV
    App --> Storage
    App --> DB
```

## Scalability

Scalability is implemented by separating roles and reducing bottlenecks:

1. Tier split and independent scaling
- `web` handles synchronous HTTP/UI/API traffic.
- `worker` handles asynchronous heavy jobs (`likec4`, `drawio`, `pdf` exports).
- `web` and `worker` can be scaled independently.

2. Reverse-proxy front door
- `traefik` fronts the app and provides buffering/compression/timeouts.
- Keeps app instances behind a single stable ingress.

3. Database connection pooling
- `pgbouncer` protects PostgreSQL from connection storms.
- Supports higher concurrency from scaled app/worker replicas.

4. Async queue model in DB
- Async job contract in `async_jobs` with retry and DLQ states.
- Worker pulls jobs in external-runner mode; heavy work is removed from request path.

5. Operational scaling readiness
- Health checks for `web`, `worker`, exporters, DB, and dependencies.
- Metrics + alerting + DR drills provide feedback for tuning thresholds and capacity.

```mermaid
flowchart LR
    User[Users]
    Edge[traefik]
    Web[web replicas]
    Worker[worker replicas]
    Queue[(async_jobs in DB)]
    Exporters[drawio-export + likec4-exporter]
    Pool[pgbouncer]
    DB[(PostgreSQL)]

    User --> Edge --> Web
    Web --> Queue
    Worker --> Queue
    Worker --> Exporters
    Web --> Pool --> DB
    Worker --> Pool --> DB
```

## Reliability and Operations Interactions

1. `web -> /health/live`, `web -> /health/ready`
Purpose: liveness/readiness gates for routing and dependency-aware startup.

2. `web -> /metrics`
Purpose: expose Prometheus-style runtime metrics for requests, jobs, dependencies, and baseline events.

3. `worker <-> async_jobs table`
Purpose: execute queued jobs with status transitions (`queued`, `running`, `succeeded`, `dead_lettered`, etc.).

4. `run_check_runtime_alerts -> metrics + DB state`
Purpose: evaluate alert categories (queue backlog, scan failures, DB saturation, SeaweedFS errors, auth/token failures).

5. `run_backup_dr_validation -> db + seaweedfs`
Purpose: validate Postgres PITR prerequisites and SeaweedFS consistency/probe checks.

6. `run_dr_game_day -> backup validation + alert evaluation`
Purpose: game-day closure flow measuring RTO/RPO and emitting actionable gaps/recommendations.

7. `prometheus -> web/cadvisor`
Purpose: periodic scrape loop for app and container telemetry, plus alert rule evaluation.

8. `promtail -> loki`
Purpose: collect container stdout/stderr logs with Docker metadata labels and store/query in Loki.

9. `grafana -> prometheus/loki`
Purpose: dashboards and Explore workflow combining metrics and logs for incident triage.

## Key Functional Flows

```mermaid
sequenceDiagram
    participant U as User Browser
    participant T as traefik
    participant W as Django web
    participant Q as async_jobs table
    participant R as Django worker
    participant E as likec4-exporter
    participant S as SeaweedFS

    U->>T: Trigger export action
    T->>W: Forward request
    W->>Q: Enqueue async export job
    W-->>U: Return job_id/status_url
    R->>Q: Claim queued job
    R->>E: POST /export
    E->>S: Read .c4 and write PNG
    E->>W: POST metadata callback
    R->>Q: Mark succeeded/dead_lettered
```

```mermaid
sequenceDiagram
    participant Ops as Operator
    participant W as Django command runtime
    participant DB as PostgreSQL
    participant S as SeaweedFS
    participant A as Alert evaluator

    Ops->>W: run_dr_game_day --json-output
    W->>DB: PITR checks (archive_mode/archive_command/wal_level)
    W->>S: Consistency + probe validation
    W->>A: Evaluate active alerts/playbook linkage
    W-->>Ops: RTO/RPO report + gaps + recommendations
```
