# Deploy no Hostinger KVM 1 (Ubuntu)

## 1. Antes de entrar no servidor
1. **Neon:** rode `docs/neon_acesso_crm.sql` (cria o usuário `crm_app`, só leitura no estoque, e o esquema `crm`) e **troque a senha do dono** do banco.
2. **Telegram:** no @BotFather envie `/revoke` e guarde o token novo.
3. **Hostinger (hPanel > VPS):** instale o sistema **Ubuntu 24.04** (sem painel). Anote o IP. Defina a senha do root ou cadastre a sua chave SSH.
4. **DNS:** crie um registro **A** `leads` apontando para o IP do VPS (no DNS de `topcarpe.site`). Se usar Cloudflare, deixe a nuvem **cinza** (somente DNS) por enquanto.
5. **GitHub (repositório privado):** crie um token de leitura (Settings > Developer settings > Fine-grained tokens, só este repositório, permissão *Contents: Read-only*).

## 2. Instalar (uma vez)
```bash
ssh root@IP_DO_VPS
curl -fsSL -H "Authorization: token SEU_TOKEN" https://raw.githubusercontent.com/dotnet777s/LEAD-CRM/main/deploy/instalar.sh -o instalar.sh
REPO_URL="https://dotnet777s:SEU_TOKEN@github.com/dotnet777s/LEAD-CRM.git" DOMINIO="leads.topcarpe.site" bash instalar.sh
```
A primeira execução cria `/opt/adcrm/.env` e para. Edite-o (`nano /opt/adcrm/.env`): `DATABASE_URL` (usuário `crm_app`), `TELEGRAM_BOT_TOKEN`, e depois rode o mesmo comando `bash instalar.sh` **de novo**. Ele aplica as migrações, sobe o site e os dois workers, configura o HTTPS e o firewall.

Crie o primeiro gestor:
```bash
sudo -u adcrm /opt/adcrm/.venv/bin/python /opt/adcrm/manage.py createsuperuser
```

## 3. Conferir
```bash
systemctl status adcrm-web adcrm-telegram adcrm-meta   # os três "active (running)"
sudo -u adcrm /opt/adcrm/.venv/bin/python /opt/adcrm/manage.py estoque_status
journalctl -u adcrm-web -f                              # logs do site
```
Abra `https://leads.topcarpe.site/entrar/` e `https://leads.topcarpe.site/v/1-chevrolet-onix-2025/`.

## 4. Atualizar depois de um push
```bash
bash /opt/adcrm/deploy/atualizar.sh
```

## 5. Depois do deploy
- Em Meta Business: **verificar o domínio** `topcarpe.site` e configurar o Pixel (`META_PIXEL_ID`, `META_CAPI_ACCESS_TOKEN`, `META_CAPI_ENABLED=1`).
- No CRM, tela **Equipe**: gere o código do Telegram e envie `/start CODIGO` ao bot.
- Ative os backups semanais (snapshots) no hPanel do VPS.
