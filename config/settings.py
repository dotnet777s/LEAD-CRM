"""
CRM de captação TopCar (contato.topcarpe.com.br). Tudo vem de variáveis de ambiente (ou do .env, que NÃO vai pro git).

O banco é o mesmo Postgres do estoque (Neon). As tabelas estoque_* são só LEITURA (app `catalogo`, managed=False).
Tudo que é novo mora em tabelas captacao_*.
"""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


def _carregar_dotenv():
    caminho = BASE_DIR / ".env"
    if not caminho.exists():
        return
    for linha in caminho.read_text(encoding="utf-8-sig").splitlines():
        linha = linha.strip()
        if not linha or linha.startswith("#") or "=" not in linha:
            continue
        k, v = linha.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_carregar_dotenv()


def env(nome, padrao=""):
    return os.environ.get(nome, padrao)


def env_bool(nome, padrao=False):
    return env(nome, "1" if padrao else "0").strip().lower() in ("1", "true", "sim", "yes", "on")


DEBUG = env_bool("DJANGO_DEBUG", True)
SECRET_KEY = env("DJANGO_SECRET_KEY") or ("dev-inseguro-nao-usar-em-producao" if DEBUG else "")
if not SECRET_KEY:
    raise RuntimeError("Defina DJANGO_SECRET_KEY em produção.")

SITE_URL = env("SITE_URL", "https://contato.topcarpe.com.br").rstrip("/")
ALLOWED_HOSTS = [h.strip() for h in env("DJANGO_ALLOWED_HOSTS", "contato.topcarpe.com.br").split(",") if h.strip()]
if env("RENDER_EXTERNAL_HOSTNAME"):
    ALLOWED_HOSTS.append(env("RENDER_EXTERNAL_HOSTNAME"))
if DEBUG:
    ALLOWED_HOSTS += ["127.0.0.1", "localhost", "testserver"]
CSRF_TRUSTED_ORIGINS = [f"https://{h}" for h in ALLOWED_HOSTS if h not in ("127.0.0.1", "localhost", "testserver")]

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "catalogo",
    "captacao",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "captacao.middleware.LoginObrigatorioMiddleware",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"

TEMPLATES = [{
    "BACKEND": "django.template.backends.django.DjangoTemplates",
    "DIRS": [],
    "APP_DIRS": True,
    "OPTIONS": {"context_processors": [
        "django.template.context_processors.request",
        "django.contrib.auth.context_processors.auth",
        "django.contrib.messages.context_processors.messages",
        "captacao.middleware.contexto_crm",
    ]},
}]

if env("DATABASE_URL"):
    import dj_database_url
    DATABASES = {"default": dj_database_url.parse(env("DATABASE_URL"), conn_max_age=60, conn_health_checks=True,
                                                  ssl_require=True)}
    if "-pooler" in env("DATABASE_URL"):      # Neon com PgBouncer (modo transação): sem cursores no servidor
        DATABASES["default"]["DISABLE_SERVER_SIDE_CURSORS"] = True
else:
    DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": BASE_DIR / "db.sqlite3"}}
DEFAULT_AUTO_FIELD = "django.db.models.AutoField"

# True só nos testes: cria as tabelas estoque_* num banco vazio. NUNCA em produção (o estoque é de outro sistema).
DATABASE_URL_DEFINIDA = bool(env("DATABASE_URL"))
CATALOGO_CRIAR_TABELAS = env_bool("CATALOGO_CRIAR_TABELAS", False) and not env("DATABASE_URL")

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
]

LANGUAGE_CODE = "pt-br"
TIME_ZONE = "America/Recife"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedStaticFilesStorage" if not DEBUG
                    else "django.contrib.staticfiles.storage.StaticFilesStorage"},
}

LOGIN_URL = "captacao:entrar"
LOGIN_REDIRECT_URL = "captacao:kanban"
LOGOUT_REDIRECT_URL = "captacao:entrar"

# --- Loja (só unidade Olinda) ---
LOJA = {
    "nome": "TopCar Veículos",
    "unidade": "Olinda",
    "whatsapp": env("LOJA_WHATSAPP", "5581960002209"),
    "endereco": "Loja 11 - Shopping do Automóvel, Olinda - PE",
    "horario": "Seg a sex 08h às 18h · Sáb 08h às 17h",
    "mapa_url": "https://www.google.com/maps/search/?api=1&query=Shopping+do+Automovel+Olinda+PE",
    "whatsapp_fmt": "(81) 96000-2209",
    "telefone": "(81) 2137-0011",
    "telefone_tel": "+558121370011",
    "instagram": "@topcarveiculos.pe",
    "instagram_url": "https://www.instagram.com/topcarveiculos.pe/",
    "garantia": "90 dias",
    "anos": "10",            # "mais de 10 anos de mercado"
    "opcoes": "60",          # "mais de 60 opções disponíveis"
}

# --- Meta Pixel / Conversions API ---
META_PIXEL_ID = env("META_PIXEL_ID")
META_CAPI_ACCESS_TOKEN = env("META_CAPI_ACCESS_TOKEN")
META_GRAPH_API_VERSION = env("META_GRAPH_API_VERSION", "v23.0")
META_TEST_EVENT_CODE = env("META_TEST_EVENT_CODE")
META_CAPI_ENABLED = env_bool("META_CAPI_ENABLED", False)

# --- Medição do formulário, teste A/B e consentimento (LGPD) ---
AB_ATIVO = env_bool("AB_ATIVO", True)
AB_VARIANTES = {    # o que muda no teste: o texto dos dois botões principais
    "A": {"b1": "Simular", "b2": "Agendar visita", "s1": "Simular", "s2": "Agendar",
          "t1": "Receba a simulação no WhatsApp", "t2": "Agende a sua visita", "e1": "Receber no WhatsApp", "e2": "Agendar pelo WhatsApp"},
    "B": {"b1": "Falar com consultor", "b2": "Ver na loja", "s1": "Consultor", "s2": "Visitar",
          "t1": "Fale com um consultor no WhatsApp", "t2": "Combine a sua visita à loja", "e1": "Falar no WhatsApp", "e2": "Combinar visita"},
}
CLARITY_ID = env("CLARITY_ID")     # Microsoft Clarity (mapa de calor). Só carrega com o consentimento do visitante.

# --- Parcela estimada exibida na página (ilustrativa: a aprovação e a taxa reais dependem do banco) ---
FINANCIAMENTO_TAXA_AM = float(env("FINANCIAMENTO_TAXA_AM", "1.99") or 1.99)     # % ao mês
FINANCIAMENTO_ENTRADA_PCT = int(env("FINANCIAMENTO_ENTRADA_PCT", "30") or 30)
FINANCIAMENTO_PARCELAS = int(env("FINANCIAMENTO_PARCELAS", "60") or 60)

# --- Telegram (avisos aos consultores) ---
TELEGRAM_BOT_TOKEN = env("TELEGRAM_BOT_TOKEN")
TELEGRAM_BOT_USERNAME = env("TELEGRAM_BOT_USERNAME", "leadsreceiverbot")
TELEGRAM_SLA_MINUTOS = int(env("TELEGRAM_SLA_MINUTOS", "5") or 5)
# Caixa de e-mail que recebe os leads dos portais (Napista, Mobiauto, OLX). Vazio = leitura desligada.
PORTAIS_IMAP_HOST = env("PORTAIS_IMAP_HOST")
PORTAIS_IMAP_PORTA = int(env("PORTAIS_IMAP_PORTA", "993") or 993)
PORTAIS_IMAP_USER = env("PORTAIS_IMAP_USER")
PORTAIS_IMAP_PASS = env("PORTAIS_IMAP_PASS")
# Portais cujos e-mails viram lead (os demais são lidos e ignorados). Hoje só a OLX; depois: "olx,napista,mobiauto".
PORTAIS_ATIVOS = tuple(p.strip() for p in (env("PORTAIS_ATIVOS", "olx") or "olx").split(",") if p.strip())
PORTAIS_IMAP_PASTA = env("PORTAIS_IMAP_PASTA", "INBOX") or "INBOX"
TELEGRAM_REQUALIFICAR_MINUTOS = int(env("TELEGRAM_REQUALIFICAR_MINUTOS", "10") or 10)   # espera antes de avisar do formulário incompleto


# Acesso por http://IP (só para testar antes de apontar o domínio). Desligue depois: ACESSO_HTTP_TEMPORARIO=0.
ACESSO_HTTP_TEMPORARIO = env_bool("ACESSO_HTTP_TEMPORARIO", False)
if ACESSO_HTTP_TEMPORARIO:
    CSRF_TRUSTED_ORIGINS += [f"http://{h}" for h in ALLOWED_HOSTS if h not in ("127.0.0.1", "localhost", "testserver")]
if not DEBUG:
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    SESSION_COOKIE_SECURE = not ACESSO_HTTP_TEMPORARIO
    CSRF_COOKIE_SECURE = not ACESSO_HTTP_TEMPORARIO

LOGGING = {
    "version": 1, "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "root": {"handlers": ["console"], "level": "INFO"},
}
