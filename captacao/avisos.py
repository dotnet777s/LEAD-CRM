"""Avisos: envio de um modelo a um ou mais contatos, em data e hora marcadas, com recorrência (tela Enviar)."""
import calendar
from datetime import timedelta

from django.conf import settings
from django.utils import timezone

from . import campanhas, whatsapp
from .models import Aviso

MESES = {"mensal": 1, "trimestral": 3, "semestral": 6, "anual": 12}
DIAS = {"diaria": 1, "semanal": 7, "quinzenal": 14}


def somar_meses(dt, n):
    m = dt.month - 1 + n
    ano, mes = dt.year + m // 12, m % 12 + 1
    return dt.replace(year=ano, month=mes, day=min(dt.day, calendar.monthrange(ano, mes)[1]))


def proxima_data(dt, recorrencia):
    if recorrencia in DIAS:
        return dt + timedelta(days=DIAS[recorrencia])
    if recorrencia in MESES:
        return somar_meses(timezone.localtime(dt), MESES[recorrencia])
    return None


def criar(leads, modelo, enviar_em, recorrencia="", usuario=None, origem=""):
    return Aviso.objects.bulk_create([Aviso(lead=l, modelo=modelo, enviar_em=enviar_em, recorrencia=recorrencia,
                                            criado_por=usuario, origem=origem) for l in leads])


def rodar(agora=None, limite=30):
    """Worker: envia os avisos vencidos (respeitando o limite diário da conta) e agenda a próxima ocorrência dos recorrentes."""
    agora = agora or timezone.now()
    if not whatsapp.configurado():
        return 0
    folga = settings.WHATSAPP_LIMITE_DIARIO - campanhas.enviados_24h(agora)
    n = 0
    for a in Aviso.objects.filter(status="pendente", enviar_em__lte=agora).select_related("lead", "modelo")[:limite]:
        if a.lead.nao_contatar:
            a.status, a.erro = "cancelado", "Cliente pediu para não receber mensagens."
        elif folga <= 0:
            break
        else:
            try:
                whatsapp.enviar_modelo(a.lead, a.modelo, a.criado_por)
                a.status, a.enviado_em = "enviado", agora
                n += 1
                folga -= 1
            except whatsapp.WhatsAppErro as e:
                a.status, a.erro = "falhou", str(e)[:300]
        a.save(update_fields=["status", "erro", "enviado_em"])
        if a.recorrencia and a.status != "cancelado":
            proxima = proxima_data(a.enviar_em, a.recorrencia)
            if proxima:
                Aviso.objects.create(lead=a.lead, modelo=a.modelo, enviar_em=proxima, recorrencia=a.recorrencia, criado_por=a.criado_por, origem=a.origem)
    return n
