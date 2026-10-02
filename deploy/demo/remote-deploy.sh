#!/usr/bin/env bash
# Déploiement de la démo, exécuté SUR le VPS par .github/workflows/deploy-demo.yml.
#
# Entrées :
#   - ${BASE_DIR}/demo.env          écrit par la CI à chaque déploiement
#                                   (DEMO_DOMAIN, DEMO_ADMIN_*, DEMO_VERSION)
#   - ${BASE_DIR}/demo-secrets.env  généré au premier déploiement puis conservé
#                                   (clé Django, mot de passe Postgres, tokens LikeC4, clés SeaweedFS)
set -euo pipefail

BASE_DIR="${BASE_DIR:-/opt/cintafactory}"
APP_DIR="${BASE_DIR}/demo"
CI_ENV_FILE="${BASE_DIR}/demo.env"
SECRETS_FILE="${BASE_DIR}/demo-secrets.env"
COMPOSE=(docker compose -p demo_factory -f docker-compose.yml -f deploy/demo/docker-compose.demo.yml)

cd "${APP_DIR}"

if [ ! -f "${CI_ENV_FILE}" ]; then
  echo "${CI_ENV_FILE} introuvable : il doit être déposé par la CI." >&2
  exit 1
fi

# Les secrets applicatifs ne quittent jamais le VPS : générés une fois, réutilisés ensuite.
if [ ! -f "${SECRETS_FILE}" ]; then
  echo "Premier déploiement : génération de ${SECRETS_FILE}"
  (
    umask 077
    {
      echo "DJANGO_SECRET_KEY=$(openssl rand -hex 48)"
      echo "POSTGRES_PASSWORD=$(openssl rand -hex 32)"
      echo "LIKEC4_METADATA_TOKEN=$(openssl rand -hex 32)"
      echo "LIKEC4_API_TOKEN=$(openssl rand -hex 32)"
      echo "SEAWEEDFS_JWT_WRITE_KEY=$(openssl rand -hex 32)"
      echo "SEAWEEDFS_JWT_READ_KEY=$(openssl rand -hex 32)"
      echo "SEAWEEDFS_VOLUME_JWT_WRITE_KEY=$(openssl rand -hex 32)"
      echo "SEAWEEDFS_VOLUME_JWT_READ_KEY=$(openssl rand -hex 32)"
    } > "${SECRETS_FILE}"
  )
fi

set -a
# shellcheck disable=SC1090
source "${CI_ENV_FILE}"
# shellcheck disable=SC1090
source "${SECRETS_FILE}"
set +a

: "${DEMO_DOMAIN:?DEMO_DOMAIN manquant}"
: "${DEMO_ADMIN_USERNAME:?DEMO_ADMIN_USERNAME manquant}"
: "${DEMO_ADMIN_EMAIL:?DEMO_ADMIN_EMAIL manquant}"
: "${DEMO_ADMIN_PASSWORD:?DEMO_ADMIN_PASSWORD manquant}"

export APP_NAME="demo_factory"
export DJANGO_SETTINGS_MODULE="cintafactory.settings"
export APP_MODULE="cintafactory.wsgi:application"
export DJANGO_DEBUG=0
# "web" est requis pour les appels internes de likec4 vers http://web:8000.
export DJANGO_ALLOWED_HOSTS="${DEMO_DOMAIN},web,localhost,127.0.0.1"
export APP_PORT=8000
export HOST_PORT=8000
export POSTGRES_DB="cintafactory"
export POSTGRES_USER="cintafactory"
export SEAWEEDFS_ALLOWED_ORIGINS="https://${DEMO_DOMAIN}"

docker network inspect swag-network >/dev/null 2>&1 || docker network create swag-network

# VPS partagé : on ne construit pas les images si le disque risque de saturer
# (un disque plein ferait tomber les autres services de la machine).
MIN_FREE_GB="${MIN_FREE_GB:-8}"
free_gb="$(( $(df --output=avail -k / | tail -1) / 1024 / 1024 ))"
if [ "${free_gb}" -lt "${MIN_FREE_GB}" ]; then
  echo "::error::Seulement ${free_gb} Go libres sur le VPS (minimum ${MIN_FREE_GB} Go). Libérer de l'espace avant de déployer." >&2
  exit 1
fi

if ! docker exec swag test -f /config/nginx/proxy-confs/dodat-demo.subdomain.conf 2>/dev/null; then
  echo "::warning::Proxy SWAG absent : copier deploy/demo/swag/dodat-demo.subdomain.conf dans /opt/swag/config/nginx/proxy-confs/."
fi

echo "Déploiement de la version ${DEMO_VERSION:-inconnue} sur ${DEMO_DOMAIN}"
"${COMPOSE[@]}" up -d --build --remove-orphans

# L'entrypoint applique les migrations avant de lancer gunicorn :
# dès que l'appli répond, la base est à jour.
echo "Attente du démarrage de l'application..."
ready=0
for _ in $(seq 1 60); do
  code="$(curl -s -o /dev/null -w '%{http_code}' \
    -H "Host: ${DEMO_DOMAIN}" -H "X-Forwarded-Proto: https" \
    "http://127.0.0.1:${HOST_PORT}/" || true)"
  if [ "${code}" -ge 200 ] && [ "${code}" -lt 400 ]; then
    ready=1
    break
  fi
  sleep 5
done

if [ "${ready}" != "1" ]; then
  echo "L'application ne répond pas après 5 minutes (dernier code HTTP : ${code})." >&2
  "${COMPOSE[@]}" ps >&2
  "${COMPOSE[@]}" logs --tail 150 web >&2
  exit 1
fi

# Compte de démo : créé au premier passage, mot de passe réaligné sur le secret à chaque déploiement.
"${COMPOSE[@]}" exec -T -w /app/cintafactory \
  -e DEMO_ADMIN_USERNAME -e DEMO_ADMIN_EMAIL -e DEMO_ADMIN_PASSWORD \
  web python manage.py shell -c '
import os
from django.contrib.auth import get_user_model

user, created = get_user_model().objects.get_or_create(username=os.environ["DEMO_ADMIN_USERNAME"])
user.email = os.environ["DEMO_ADMIN_EMAIL"]
user.is_active = user.is_staff = user.is_superuser = True
user.set_password(os.environ["DEMO_ADMIN_PASSWORD"])
user.save()
print("Compte demo cree." if created else "Compte demo mis a jour.")
'

docker image prune -f >/dev/null

"${COMPOSE[@]}" ps
echo "Démo disponible sur https://${DEMO_DOMAIN}"
