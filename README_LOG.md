# SPDX-FileCopyrightText: 2026 Cintamaya <contact@cintamaya.com>
# SPDX-FileCopyrightText: 2026 Baptiste COQUELET <github.com/BaptisteCoquelet>
# SPDX-License-Identifier: CC BY-ND 4.0

# CintaFactory Log Guide

## Quick Start

Core dev stack:
```bash
docker compose -f docker-compose.dev.yml logs -f
```

Scaling stack:
```bash
docker compose -f docker-compose.scaling.dev.yml logs -f
```

Observability stack:
```bash
docker compose -f cintafactory/docker-compose.observability.dev.yml logs -f
```

## Service Logs

Follow one service only:
```bash
docker compose -f docker-compose.dev.yml logs -f web
```

Show recent lines:
```bash
docker compose -f docker-compose.dev.yml logs --tail=120 web
```

## Useful Notes

- Django/Gunicorn logs are written to container stdout/stderr.
- Promtail ships container logs to Loki for Grafana Explore.
- If logs are empty, confirm the stack is running with `docker compose ... ps`.
