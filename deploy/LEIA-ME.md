# Deploy no Hostinger KVM 1 (Ubuntu)

## 1. Antes de entrar no servidor
1. **Neon:** rode `docs/neon_acesso_crm.sql` (cria o usuário `crm_app`, só leitura no estoque, e o esquema `crm`) e **troque a senha do dono** do banco.
2. **Telegram:** no @BotFather envie `/revoke` e guarde o token novo.
3. **Hostinger (hPanel > VPS):** instale o sistema **Ubuntu 24.04** (sem painel). Anote o IP. Defina a senha do root ou cadastre a sua chave SSH.
4. **DNS (Cloudflare):** na zona `topcarpe.com.br`, registro **A**, nome `contato`, valor = IP do VPS, proxy **desligado** (nuvem cinza, "somente DNS"). Se já existir um registro `contato`, edite-o em vez de criar outro.
5. **GitHub (repositório privado):** crie um token de leitura (Settings > Developer settings > Fine-grained tokens, só este repositório, permissão *Contents: Read-only*).

## 2. Instalar (uma vez)
```bash
ssh root@IP_DO_VPS
curl -fsSL -H "Authorization: token SEU_TOKEN" https://raw.githubusercontent.com/dotnet777s/LEAD-CRM/main/deploy/instalar.sh -o instalar.sh
REPO_URL="https://dotnet777s:SEU_TOKEN@github.com/dotnet777s/LEAD-CRM.git" DOMINIO="contato.topcarpe.com.br" bash instalar.sh
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
Abra `https://contato.topcarpe.com.br/entrar/` e `https://contato.topcarpe.com.br/v/1-chevrolet-onix-2025/`.

## 4. Atualizar depois de um push
```bash
bash /opt/adcrm/deploy/atualizar.sh
```

## 5. Depois do deploy
- Em Meta Business: **verificar o domínio** `topcarpe.com.br` e configurar o Pixel (`META_PIXEL_ID`, `META_CAPI_ACCESS_TOKEN`, `META_CAPI_ENABLED=1`).
- No CRM, tela **Equipe**: gere o código do Telegram e envie `/start CODIGO` ao bot.
- Ative os backups semanais (snapshots) no hPanel do VPS.

## Cloudflare na frente (como está hoje)
- O DNS do domínio fica na Cloudflare com o proxy ligado. A Cloudflare entrega o HTTPS; o trecho até este servidor é HTTP.
- O ideal é o registro **A** `contato` apontar **direto** para o IP deste servidor. Se houver um repasse por outra máquina, ela deve preservar o cabeçalho `Host` (ou o `ALLOWED_HOSTS` precisa listar o IP).
- O app usa `CF-Connecting-IP` como IP do visitante (limite de envios e qualidade dos eventos da Meta). Por isso o ideal é aceitar conexões só da Cloudflare (firewall) para ninguém forjar esse cabeçalho.
