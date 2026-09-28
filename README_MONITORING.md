# SPDX-FileCopyrightText: 2026 Cintamaya <contact@cintamaya.com>
# SPDX-FileCopyrightText: 2026 Baptiste COQUELET <github.com/BaptisteCoquelet>
# SPDX-License-Identifier: CC-BY-ND-4.0

# CintaFactory Monitoring Guide

This document explains the current monitoring and observability system for the local platform.

## Scope
- Metrics: Prometheus + cAdvisor + app `/metrics`
- Logs: Loki + Promtail
- Dashboards: Grafana
- Edge/runtime context: Traefik + Docker Compose services

## Components

### Prometheus
- Purpose: collect metrics, evaluate alert rules.
- Compose file: `cintafactory/docker-compose.observability.dev.yml`
- Config: `cintafactory/deploy/observability/prometheus/prometheus.yml`
- Rules: `cintafactory/deploy/observability/prometheus/rules/cinta-alerts.yml`
- UI: `http://localhost:9090`
- Alerts page: `http://localhost:9090/alerts`

Current scrape targets:
- `prometheus:9090` (`job="prometheus"`)
- `web:8000/metrics` (`job="cintafactory-web"`, `service="web"`)
- `cadvisor:8080` (`job="cadvisor"`, `service="cadvisor"`)

### Grafana
- Purpose: visualize metrics/logs in dashboards and Explore.
- Provisioned datasources:
  - Prometheus (`uid=prometheus`)
  - Loki (`uid=loki`)
- Provisioning path: `cintafactory/deploy/observability/grafana/provisioning`
- Dashboards path: `cintafactory/deploy/observability/grafana/dashboards`
- UI: `http://localhost:3000`
- Default credentials: `admin` / `admin` (unless overridden by env vars)

Key dashboards in folder `Cinta Platform`:
- `Cinta Service Overview`
- `Cinta Reliability and SLO`
- `Cinta Capacity and Scaling`
- `Cinta Logs and Correlation`
- `Cinta Architecture and Interactions`
- `Cinta Platform Deep Dive` (broad operational view: targets, traffic, dependencies, async, containers, logs)

### Loki
- Purpose: store and query logs.
- Config: `cintafactory/deploy/observability/loki/config.yml`
- API base URL: `http://localhost:3100`
- Readiness endpoint: `http://localhost:3100/ready`
- Typical access path: Grafana `Explore` -> datasource `Loki`

### Promtail
- Purpose: collect container logs from Docker and push to Loki.
- Config: `cintafactory/deploy/observability/promtail/config.yml`
- Discovery: Docker socket (`/var/run/docker.sock`)
- Labeling includes:
  - `container`
  - `service`
  - `project`
  - `container_id`
  - `container_number`
  - `stream`

### cAdvisor
- Purpose: container CPU/memory/network metrics for capacity dashboards.
- Exposed on host: `http://localhost:8088`
- Scraped by Prometheus as `job="cadvisor"`.

### Traefik
- Purpose: edge reverse proxy for scaled topology.
- Compose file: `docker-compose.scaling.dev.yml`
- Dynamic routes: `deploy/traefik/dynamic.scaling.yml`
- Main app entrypoint: `http://localhost:8101`
- Dashboard path (through Traefik): `http://localhost:8101/traefik/dashboard/`

Important:
- Traefik is not the metrics/log backend. It routes HTTP traffic to app services.
- Monitoring stack runs separately and observes app/runtime behavior.

## Data Flow
1. App and runtime emit metrics (`/metrics`, cAdvisor endpoints).
2. Prometheus scrapes metrics on interval and evaluates alert rules.
3. Docker logs are discovered by Promtail and pushed to Loki.
4. Grafana queries Prometheus and Loki for dashboards and ad-hoc troubleshooting.
5. Traefik sits at ingress and can be correlated through logs/metrics context.

## Startup Order
1. Start app stack first (`core-dev` or `scaling`).
2. Start observability stack.
3. Open Grafana and verify datasources are healthy.

Commands:
```bash
docker compose -f docker-compose.scaling.dev.yml up -d --build
docker compose -f cintafactory/docker-compose.observability.dev.yml up -d
```

Stop:
```bash
docker compose -f cintafactory/docker-compose.observability.dev.yml down
docker compose -f docker-compose.scaling.dev.yml down
```

## Quick Health Checks
```bash
curl -fsS http://localhost:9090/-/healthy && echo prometheus_ok
curl -fsS http://localhost:3100/ready && echo loki_ok
curl -fsS http://localhost:3000/api/health && echo grafana_ok
curl -fsS http://localhost:8101/health/ready && echo app_ready
```

Prometheus target check:
- Open `http://localhost:9090/targets`
- Confirm `cintafactory-web` and `cadvisor` are `UP`

## Alert Set (Current)
Defined in `cintafactory/deploy/observability/prometheus/rules/cinta-alerts.yml`:
- `CintaWebMetricsDown`
- `CintaCadvisorDown`
- `CintaDependencyDown`
- `CintaHighWeb5xxRate`
- `CintaAsyncQueueBacklogHigh`
- `CintaAsyncWorkersMissing`

Interpretation:
- `0 active` in Prometheus UI is normal when system is healthy.
- Rules being listed means rule loading is working.

## Troubleshooting

### Grafana panel shows "No data"
- Check Prometheus `targets` page.
- Verify query labels match current metrics labels (`container_id` is used for capacity dashboards).
- Confirm time range is not too narrow.

### Prometheus has missing targets
- Ensure app stack is running and on expected network.
- Ensure observability stack network matches `APP_MONITORING_NETWORK` (default `cintaarchifactory_app`).

### Loki has no logs
- Check `promtail` container is running.
- Verify Docker socket mount exists in `promtail`.
- Query labels endpoint: `http://localhost:3100/loki/api/v1/labels`

### Traefik route errors on app URLs
- Check Traefik logs and web container health.
- Validate dynamic route file: `deploy/traefik/dynamic.scaling.yml`
- Confirm `web` service is healthy before Traefik startup completes.

## Related Files
- `README_dev.md`
- `SERVICES_STATUS_AND_INTERACTIONS.md`
- `cintafactory/docker-compose.observability.dev.yml`
- `docker-compose.scaling.dev.yml`
