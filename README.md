# CRM de captação TopCar (leads.topcarpe.site)

Página de captura por carro (`/v/<id>-<slug>/`) + Meta Pixel/Conversions API + Kanban + painel. Unidade: Olinda.
O estoque do ROUTE (`veiculo`, `veiculo_foto`, `veiculo_foto_arquivo`) é **somente leitura** (app `catalogo`, `managed=False`); tudo novo vive em `captacao_*`.

## Rodar local (SQLite)
```
python -m venv .venv && .venv\Scripts\pip install -r requirements.txt
# .env:  DJANGO_DEBUG=1  e  CATALOGO_CRIAR_TABELAS=1  (cria as tabelas do estoque só no SQLite local)
.venv\Scripts\python manage.py migrate
.venv\Scripts\python manage.py createsuperuser
.venv\Scripts\python manage.py popular_demo      # carros e leads de exemplo (só local)
.venv\Scripts\python manage.py runserver
```
Abra `http://127.0.0.1:8001/` (CRM, login) e `/v/1/` (página do carro).

## Produção (Neon)
Defina `DATABASE_URL` (aí `CATALOGO_CRIAR_TABELAS` é sempre ignorado: nenhuma tabela do ROUTE é criada), `DJANGO_SECRET_KEY`,
`DJANGO_DEBUG=0`, `DJANGO_ALLOWED_HOSTS`, `SITE_URL`. Depois `migrate` (só cria `captacao_*`) e `collectstatic`.
Comando de start: `gunicorn config.wsgi:application --workers 1 --threads 4`.

## Variáveis (ver `.env.example`)
| Variável | Para quê |
|---|---|
| `META_PIXEL_ID`, `META_CAPI_ACCESS_TOKEN`, `META_GRAPH_API_VERSION` | Pixel e CAPI |
| `META_CAPI_ENABLED` | `1` liga o envio (desligado, os eventos ficam na fila) |
| `META_TEST_EVENT_CODE` | só em teste (aba "Testar eventos") |
| `LOJA_WHATSAPP` | número da loja (padrão 5581960002209) |

## Worker da Meta
Nada é enviado na requisição. Rode em outro processo (PC da loja ou serviço separado; o Render free não mantém processos):
```
python manage.py enviar_eventos_meta --loop --intervalo 5
```
Retry exponencial (1, 2, 4, 8 min; 5 tentativas). Eventos com mais de 7 dias ficam "não enviado" (`JANELA_EVENTO_DIAS` em `captacao/meta.py`; confira na doc da Meta).
Teste: `python manage.py meta_testar --codigo TEST123` e confira em Gerenciador de Eventos > Testar eventos.

## Telegram (avisos aos consultores)
Bot criado no @BotFather; coloque `TELEGRAM_BOT_TOKEN` (e `TELEGRAM_BOT_USERNAME`) no `.env`. Worker (outro processo, junto do worker da Meta):
```
python manage.py telegram_bot
```
- **Vincular:** em **Equipe**, o gestor clica em "Gerar código" na linha do usuário; a pessoa abre o bot e envia `/start CODIGO` (vale 30 min).
- **Lead novo:** chega para todos os vinculados (ou só para o dono, se já tiver) com os botões **Assumir** (o primeiro a clicar fica com o lead) e **WhatsApp**.
- **Lembrete:** na hora do "próximo contato" o dono recebe o aviso.
- **Lead parado:** lead em "Novo" há mais de `TELEGRAM_SLA_MINUTOS` (padrão 5) avisa o gestor e o dono, uma vez.
- Comandos: `/leads` (seus leads em aberto), `/ajuda`. Se o token vazar, gere outro com `/revoke` no @BotFather.

## Mídias do carro
Aba **Carros**: cada carro mostra a contagem de fotos e vídeos e o botão **Mídias** (fotos e vídeos em seções separadas, lidos do estoque). Os vídeos ficam no ROUTE e ainda não aparecem aqui. Para conferir a conexão: `python manage.py estoque_status`.

## Carro herói
É só apontar o anúncio para outra página `/v/<id>/`. Não há configuração.

## Usuários
Crie com `createsuperuser` ou pelo `/admin/`. Gestor = `is_staff`, superusuário ou grupo "Gestor" (vê todos os leads, troca consultor, exclui dados).
Consultor vê os seus leads e os ainda sem dono.

## LGPD
Consentimento (data/IP) gravado no lead. Exclusão: botão no detalhe do lead (gestor) ou `manage.py excluir_lead <telefone>`.

## Testes
`python manage.py test captacao --settings=config.settings_test`

## Checklist de validação
- [ ] `/v/<id>/` no celular, Lighthouse Performance ≥ 90
- [ ] Enviar o formulário: lead em "Novo" em até 30 s e WhatsApp com a mensagem certa
- [ ] "Testar eventos": PageView, ViewContent, Lead e Contact do navegador e do servidor, deduplicados
- [ ] Mover para Qualificado, Visita agendada e Vendido envia QualifiedLead, Schedule e Purchase uma vez cada
- [ ] `sqlmigrate catalogo 0001` não gera SQL; nenhum segredo no repositório

## Deploy
Veja `deploy/LEIA-ME.md` (Hostinger KVM 1, Ubuntu: site, workers do Telegram e da Meta, HTTPS pelo Caddy).
