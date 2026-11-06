#!/usr/bin/env bash
set -euo pipefail

# Détecte la racine du projet Django (dev: /app, prod: /app/cintafactory)
if [ -f /app/manage.py ]; then
  APP_DIR=/app
elif [ -f /app/cintafactory/manage.py ]; then
  APP_DIR=/app/cintafactory
else
  echo "ERROR: manage.py introuvable dans /app ou /app/cintafactory" >&2
  exit 1
fi

MANAGE_PY="${APP_DIR}/manage.py"
GUNICORN_CONF="${APP_DIR}/gunicorn.conf.py"

# Assure un import Django correct (cintafactory.*) via CWD
cd "$APP_DIR"

# Wait for an authenticated PostgreSQL connection without logging credentials.
python "$MANAGE_PY" wait_for_database --timeout "${DATABASE_WAIT_TIMEOUT:-120}"

# Migrations / static (désactivables via variables)
if [ "${RUN_MIGRATIONS:-1}" = "1" ]; then
  echo "Migrating..."
  python "$MANAGE_PY" migrate --noinput
fi


if [ "${COLLECT_STATIC:-1}" = "1" ]; then
  echo "Collecting static..."
  python "$MANAGE_PY" collectstatic --noinput || true
fi

: "${APP_MODULE:=cintafactory.wsgi:application}"

exec gunicorn "$APP_MODULE" --config "$GUNICORN_CONF"
