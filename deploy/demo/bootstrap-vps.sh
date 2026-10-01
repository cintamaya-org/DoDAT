#!/usr/bin/env bash
# Préparation initiale d'un VPS OVH (Ubuntu 22.04/24.04 ou Debian 12) pour la démo.
# À lancer UNE fois, en root, sur le VPS :
#
#   sudo DEPLOY_PUBKEY="ssh-ed25519 AAAA... github-actions-demo" bash bootstrap-vps.sh
#
# Variables optionnelles :
#   DEPLOY_USER=deploy           utilisateur utilisé par GitHub Actions
#   SWAP_SIZE=4G                 swap créé s'il n'en existe aucun (ClamAV + navigateurs headless sont gourmands)
#   DISABLE_SSH_PASSWORD=1       désactive l'authentification SSH par mot de passe
#                                (ne le faire qu'après avoir vérifié sa propre connexion par clé)
set -euo pipefail

DEPLOY_USER="${DEPLOY_USER:-deploy}"
SWAP_SIZE="${SWAP_SIZE:-4G}"
BASE_DIR="/opt/cintafactory"

if [ "$(id -u)" -ne 0 ]; then
  echo "Ce script doit être lancé en root (sudo)." >&2
  exit 1
fi
: "${DEPLOY_PUBKEY:?Fournir DEPLOY_PUBKEY (clé publique SSH utilisée par GitHub Actions)}"

# shellcheck disable=SC1091
. /etc/os-release
case "${ID}" in
  ubuntu|debian) ;;
  *) echo "Distribution non supportée : ${ID}" >&2; exit 1 ;;
esac

echo "==> Paquets système"
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get upgrade -y
apt-get install -y ca-certificates curl gnupg rsync openssl ufw fail2ban unattended-upgrades
dpkg-reconfigure -f noninteractive unattended-upgrades

echo "==> Docker Engine + plugin compose (dépôt officiel Docker)"
if ! command -v docker >/dev/null 2>&1; then
  install -m 0755 -d /etc/apt/keyrings
  curl -fsSL "https://download.docker.com/linux/${ID}/gpg" -o /etc/apt/keyrings/docker.asc
  chmod a+r /etc/apt/keyrings/docker.asc
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/${ID} ${VERSION_CODENAME} stable" \
    > /etc/apt/sources.list.d/docker.list
  apt-get update
  apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
fi

# Rotation des logs des conteneurs pour ne pas remplir le disque.
if [ ! -f /etc/docker/daemon.json ]; then
  cat > /etc/docker/daemon.json <<'EOF'
{
  "log-driver": "json-file",
  "log-opts": { "max-size": "10m", "max-file": "3" }
}
EOF
fi
systemctl enable --now docker
systemctl restart docker

echo "==> Utilisateur de déploiement '${DEPLOY_USER}'"
if ! id "${DEPLOY_USER}" >/dev/null 2>&1; then
  useradd --create-home --shell /bin/bash "${DEPLOY_USER}"
fi
# Le groupe docker donne des droits équivalents à root sur la machine : réserver ce compte à la CI.
usermod -aG docker "${DEPLOY_USER}"
install -d -m 700 -o "${DEPLOY_USER}" -g "${DEPLOY_USER}" "/home/${DEPLOY_USER}/.ssh"
auth_keys="/home/${DEPLOY_USER}/.ssh/authorized_keys"
touch "${auth_keys}"
grep -qxF "${DEPLOY_PUBKEY}" "${auth_keys}" || echo "${DEPLOY_PUBKEY}" >> "${auth_keys}"
chown "${DEPLOY_USER}:${DEPLOY_USER}" "${auth_keys}"
chmod 600 "${auth_keys}"

install -d -m 750 -o "${DEPLOY_USER}" -g "${DEPLOY_USER}" "${BASE_DIR}" "${BASE_DIR}/demo"

echo "==> Pare-feu (SSH, HTTP, HTTPS)"
ufw default deny incoming
ufw default allow outgoing
ufw allow OpenSSH
ufw allow 80/tcp
ufw allow 443/tcp
ufw allow 443/udp
ufw --force enable

systemctl enable --now fail2ban

if [ "${DISABLE_SSH_PASSWORD:-0}" = "1" ]; then
  echo "==> Désactivation de l'authentification SSH par mot de passe"
  cat > /etc/ssh/sshd_config.d/90-cintafactory.conf <<'EOF'
PasswordAuthentication no
KbdInteractiveAuthentication no
PermitRootLogin prohibit-password
EOF
  systemctl reload ssh 2>/dev/null || systemctl reload sshd
fi

if [ -z "$(swapon --show --noheadings)" ]; then
  echo "==> Création d'un swap de ${SWAP_SIZE}"
  fallocate -l "${SWAP_SIZE}" /swapfile
  chmod 600 /swapfile
  mkswap /swapfile
  swapon /swapfile
  grep -q '^/swapfile ' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
fi

docker network inspect swag-network >/dev/null 2>&1 || docker network create swag-network

echo
echo "VPS prêt. Empreinte SSH à copier dans le secret GitHub DEMO_VPS_KNOWN_HOSTS"
echo "(à générer depuis votre poste : ssh-keyscan -t ed25519 <IP_DU_VPS>)"
echo "Empreintes locales pour comparaison :"
for key in /etc/ssh/ssh_host_*_key.pub; do ssh-keygen -lf "${key}"; done
