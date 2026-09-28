# SPDX-FileCopyrightText: 2026 Cintamaya <contact@cintamaya.com>
# SPDX-FileCopyrightText: 2026 Baptiste COQUELET <github.com/BaptisteCoquelet>
# SPDX-License-Identifier: CC BY-ND 4.0

# CintaFactory Developer Guide

Tests de charge et génération de données synthétiques : [`docs/LoadTesting.md`](docs/LoadTesting.md).

## Stack Packs
Use these packs depending on what you need to work on.

1. `core-dev` pack
- Compose file: `docker-compose.dev.yml`
- Includes: Django web, Postgres, drawio, drawio-export, likec4, likec4-exporter, clamav, seaweedfs
- Use for: normal feature development

2. `scaling` pack
- Compose file: `docker-compose.scaling.dev.yml`
- Includes: traefik, web, worker, pgbouncer, exporters, db, storage/scanner
- Use for: scale, readiness, worker and network topology validation

3. `observability` pack
- Compose file: `cintafactory/docker-compose.observability.dev.yml`
- Includes: Grafana, Prometheus, Loki, Promtail, cAdvisor
- Use for: dashboards, metrics, logs, capacity monitoring

4. `full-platform` pack
- Start `scaling` pack, then `observability` pack
- Use for: end-to-end ops/reliability validation

## Command Pack: core-dev
Start:
```bash
docker compose -f docker-compose.dev.yml up -d --build
```

Stop:
```bash
docker compose -f docker-compose.dev.yml down
```

Logs:
```bash
docker compose -f docker-compose.dev.yml logs -f
```

Migrate:
```bash
docker compose -f docker-compose.dev.yml exec -T web python manage.py migrate
```

Create admin:
```bash
docker compose -f docker-compose.dev.yml exec web python manage.py createsuperuser
```

Run all tests:
```bash
docker compose -f docker-compose.dev.yml exec -T web python manage.py test --keepdb --noinput
```

URLs:
- App: `http://localhost:8101`
- Traefik dashboard: `http://localhost:8101/traefik/dashboard/`
- draw.io UI: `http://localhost:8102`
- draw.io export: `http://localhost:8103`

## Command Pack: scaling
Start:
```bash
docker compose -f docker-compose.scaling.dev.yml up -d --build
```

Stop:
```bash
docker compose -f docker-compose.scaling.dev.yml down
```

Logs:
```bash
docker compose -f docker-compose.scaling.dev.yml logs -f
```

Scale example:
```bash
docker compose -f docker-compose.scaling.dev.yml up -d --scale web=2 --scale worker=2 --scale likec4-exporter=2 --scale drawio-export=2
```

Readiness check:
```bash
docker compose -f docker-compose.scaling.dev.yml exec -T web python manage.py check_runtime_dependencies --profile web --json-output
```

Targeted scaling tests:
```bash
docker compose -f docker-compose.scaling.dev.yml exec -T web python manage.py test --keepdb --noinput cintafactory.tests_ops.tests_health cintafactory.tests_ops.tests_async_worker
```

URL:
- Through Traefik: `http://localhost:8101`

## Command Pack: observability
Important:
- Start `core-dev` or `scaling` first.
- Observability stack expects Docker network `cintaarchifactory_app` unless overridden with `APP_MONITORING_NETWORK`.

Start:
```bash
docker compose -f cintafactory/docker-compose.observability.dev.yml up -d
```

Stop:
```bash
docker compose -f cintafactory/docker-compose.observability.dev.yml down
```

Logs:
```bash
docker compose -f cintafactory/docker-compose.observability.dev.yml logs -f
```

Dashboard asset validation:
```bash
deploy/scripts/test_dashboard_assets.sh
```

Dashboard test suite:
```bash
docker compose -f docker-compose.dev.yml exec -T web python manage.py test --keepdb --noinput cintafactory.tests_ops.tests_dashboard_assets
```

URLs:
- Grafana: `http://localhost:3000` (`admin` / `admin`)
- Prometheus: `http://localhost:9090`
- Loki ready: `http://localhost:3100/ready`
- cAdvisor: `http://localhost:8088`

Prometheus alerts:
- Rule file path: `cintafactory/deploy/observability/prometheus/rules/cinta-alerts.yml`
- Check active alerts: `http://localhost:9090/alerts`

Grafana folder:
- `Cinta Platform`

## Command Pack: full-platform
Start everything (recommended order):
```bash
docker compose -f docker-compose.scaling.dev.yml up -d --build
docker compose -f cintafactory/docker-compose.observability.dev.yml up -d
```

Stop everything:
```bash
docker compose -f cintafactory/docker-compose.observability.dev.yml down
docker compose -f docker-compose.scaling.dev.yml down
```

Smoke checks:
```bash
docker compose -f docker-compose.scaling.dev.yml exec -T web python manage.py check_runtime_dependencies --profile web --json-output
curl -fsS http://localhost:3000/api/health >/dev/null && echo grafana_ok
curl -fsS http://localhost:9090/-/healthy >/dev/null && echo prometheus_ok
curl -fsS http://localhost:3100/ready >/dev/null && echo loki_ok
```

## App Health and Metrics Endpoints
- Liveness: `http://localhost:8101/health/live`
- Readiness: `http://localhost:8101/health/ready`
- Metrics: `http://localhost:8101/metrics`

## References
- `params_dev/PLAN6_DASHBOARD_RUNBOOK.md`
- `params_dev/PLAN5_ALERTING_RUNBOOK.md`
- `params_dev/PLAN5_BACKUP_DR_RUNBOOK.md`
