#!/usr/bin/env bash
# Instalação do ADCRM num VPS Ubuntu 22.04/24.04 (Hostinger KVM 1). Rode como root:
#   REPO_URL="https://github.com/dotnet777s/LEAD-CRM.git" DOMINIO="leads.topcarpe.site" bash instalar.sh
# Repositório privado: use REPO_URL="https://USUARIO:TOKEN@github.com/dotnet777s/LEAD-CRM.git" (token só com leitura).
set -euo pipefail

REPO_URL="${REPO_URL:-}"
DOMINIO="${DOMINIO:-leads.topcarpe.site}"
APP_DIR=/opt/adcrm
APP_USER=adcrm

if [ "$(id -u)" -ne 0 ]; then echo "Rode como root."; exit 1; fi

echo "==> Pacotes do sistema"
export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get install -y python3 python3-venv python3-dev git curl ufw debian-keyring debian-archive-keyring apt-transport-https gnupg

echo "==> Caddy (HTTPS automático)"
if ! command -v caddy >/dev/null; then
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' > /etc/apt/sources.list.d/caddy-stable.list
  apt-get update -y
  apt-get install -y caddy
fi

echo "==> Usuário e código"
id "$APP_USER" >/dev/null 2>&1 || useradd --system --create-home --home-dir "$APP_DIR" --shell /usr/sbin/nologin "$APP_USER"
if [ ! -f "$APP_DIR/manage.py" ]; then
  [ -n "$REPO_URL" ] || { echo "Código não encontrado em $APP_DIR e REPO_URL não definida."; exit 1; }
  sudo -u "$APP_USER" git clone "$REPO_URL" "$APP_DIR/app_tmp"
  shopt -s dotglob
  mv "$APP_DIR"/app_tmp/* "$APP_DIR"/
  rmdir "$APP_DIR/app_tmp"
fi
chown -R "$APP_USER":"$APP_USER" "$APP_DIR"
[ -f "$APP_DIR/.env" ] && chmod 600 "$APP_DIR/.env"

echo "==> Ambiente Python"
sudo -u "$APP_USER" python3 -m venv "$APP_DIR/.venv"
sudo -u "$APP_USER" "$APP_DIR/.venv/bin/pip" install --upgrade pip
sudo -u "$APP_USER" "$APP_DIR/.venv/bin/pip" install -r "$APP_DIR/requirements.txt"

echo "==> Arquivo .env"
if [ ! -f "$APP_DIR/.env" ]; then
  cp "$APP_DIR/deploy/env.producao.example" "$APP_DIR/.env"
  SEGREDO=$("$APP_DIR/.venv/bin/python" -c "import secrets;print(secrets.token_urlsafe(60))")
  sed -i "s|^DJANGO_SECRET_KEY=.*|DJANGO_SECRET_KEY=${SEGREDO}|" "$APP_DIR/.env"
  sed -i "s|^DJANGO_ALLOWED_HOSTS=.*|DJANGO_ALLOWED_HOSTS=${DOMINIO}|" "$APP_DIR/.env"
  sed -i "s|^SITE_URL=.*|SITE_URL=https://${DOMINIO}|" "$APP_DIR/.env"
  chown "$APP_USER":"$APP_USER" "$APP_DIR/.env"
  chmod 600 "$APP_DIR/.env"
  echo
  echo ">>> EDITE $APP_DIR/.env (DATABASE_URL, TELEGRAM_BOT_TOKEN, Meta) e rode este script de novo."
  exit 0
fi
grep -q '^DATABASE_URL=postgres' "$APP_DIR/.env" || { echo "DATABASE_URL não definida em $APP_DIR/.env"; exit 1; }

echo "==> Banco e arquivos estáticos"
sudo -u "$APP_USER" "$APP_DIR/.venv/bin/python" "$APP_DIR/manage.py" migrate --noinput
sudo -u "$APP_USER" "$APP_DIR/.venv/bin/python" "$APP_DIR/manage.py" collectstatic --noinput
sudo -u "$APP_USER" "$APP_DIR/.venv/bin/python" "$APP_DIR/manage.py" check --deploy || true

echo "==> Serviços (site + worker Telegram + worker Meta)"
cp "$APP_DIR"/deploy/adcrm-web.service "$APP_DIR"/deploy/adcrm-telegram.service "$APP_DIR"/deploy/adcrm-meta.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now adcrm-web adcrm-telegram adcrm-meta

echo "==> Caddy"
sed "s|__DOMINIO__|${DOMINIO}|g" "$APP_DIR/deploy/Caddyfile" > /etc/caddy/Caddyfile
systemctl reload caddy || systemctl restart caddy

echo "==> Firewall (SSH, HTTP, HTTPS)"
ufw allow OpenSSH >/dev/null
ufw allow 80,443/tcp >/dev/null
ufw --force enable >/dev/null

echo
echo "Pronto. Crie o primeiro gestor:"
echo "  sudo -u $APP_USER $APP_DIR/.venv/bin/python $APP_DIR/manage.py createsuperuser"
echo "Depois acesse https://${DOMINIO}/entrar/"
