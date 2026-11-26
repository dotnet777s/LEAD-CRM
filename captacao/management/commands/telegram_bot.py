import logging
import time

import requests
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from catalogo import autocerto
from captacao import avisos, campanhas, followup, telegram, whatsapp

log = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Worker do bot do Telegram: comandos, botões, fila de avisos, lembretes e alerta de lead parado."

    def add_arguments(self, p):
        p.add_argument("--uma-vez", action="store_true", help="roda um ciclo e sai (teste)")

    def handle(self, *a, **o):
        if not telegram.configurado():
            raise CommandError("Defina TELEGRAM_BOT_TOKEN no .env.")
        self.stdout.write("Bot do Telegram em execução (Ctrl+C para parar).")
        offset, ultima_verificacao, ultimo_estoque = None, 0, 0
        while True:
            try:
                dados = {"timeout": 0 if o["uma_vez"] else 20, "allowed_updates": ["message", "callback_query"]}
                if offset:
                    dados["offset"] = offset
                r = telegram.api("getUpdates", **dados)
                for u in r.get("result", []) if r.get("ok") else []:
                    offset = u["update_id"] + 1
                    try:
                        telegram.tratar_update(u)
                    except Exception:  # noqa: BLE001 - um update ruim não derruba o bot
                        log.exception("telegram: erro ao tratar update")
                if time.time() - ultima_verificacao > 30 or o["uma_vez"]:
                    telegram.lembretes_de_retorno()
                    telegram.alertas_lead_parado()
                    telegram.alertas_requalificar()
                    telegram.alertas_followup()
                    try:
                        whatsapp.rodar_followup_automatico()     # só age com a API conectada e o envio automático ligado
                    except Exception:  # noqa: BLE001
                        log.exception("followup automático falhou")
                    try:
                        followup.sincronizar()                   # leads que entraram numa etapa com follow-up
                        whatsapp.enviar_programadas()
                        campanhas.rodar_automaticas()
                        avisos.rodar()
                        campanhas.rodar_lote()
                    except Exception:  # noqa: BLE001
                        log.exception("campanhas falhou")
                    ultima_verificacao = time.time()
                    if settings.AUTOCERTO_URL and time.time() - ultimo_estoque > settings.ESTOQUE_SINCRONIZAR_MIN * 60:
                        ultimo_estoque = time.time()
                        try:
                            autocerto.sincronizar()
                            autocerto.aquecer_capas()
                        except Exception:  # noqa: BLE001 - o catálogo segue com o último estoque bom
                            log.exception("estoque: falha ao sincronizar com o AutoCerto")
                enviados = telegram.processar_fila()
                if o["uma_vez"]:
                    self.stdout.write(f"Ciclo concluído. Enviados: {enviados}")
                    return
            except KeyboardInterrupt:
                return
            except requests.RequestException:
                log.warning("telegram: sem rede, tentando de novo em 5 s")
                time.sleep(5)
