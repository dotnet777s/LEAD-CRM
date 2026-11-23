#!/usr/bin/env bash
# Atualiza o ADCRM no servidor depois de um push no GitHub. Rode como root: bash /opt/adcrm/deploy/atualizar.sh
set -euo pipefail
APP_DIR=/opt/adcrm
U="sudo -u adcrm"

$U git -C "$APP_DIR" pull --ff-only
$U "$APP_DIR/.venv/bin/pip" install -r "$APP_DIR/requirements.txt" -q
$U "$APP_DIR/.venv/bin/python" "$APP_DIR/manage.py" migrate --noinput
$U "$APP_DIR/.venv/bin/python" "$APP_DIR/manage.py" collectstatic --noinput -v0
systemctl restart adcrm-web adcrm-telegram adcrm-meta
systemctl --no-pager --lines=0 status adcrm-web adcrm-telegram adcrm-meta | grep -E "●|Active"
