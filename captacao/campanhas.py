"""Campanhas: disparo de um template aprovado para um grupo de leads, respeitando o limite diário da conta.

Regras de segurança (a Meta pune quem incomoda): nunca envia a quem pediu para não receber, só usa template APROVADO,
envia aos poucos (lote por ciclo do worker) e para sozinho ao atingir o limite diário configurado."""
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from . import whatsapp
from .models import Campanha, CampanhaEnvio, Lead

LOTE = 15


def publico(filtro):
    """Leads que recebem a campanha. Filtros: etapas (lista), origem, portal, dias_parado (sem atualização há N dias)."""
    qs = Lead.objects.filter(nao_contatar=False).exclude(telefone="")
    etapas = [e for e in (filtro.get("etapas") or []) if e in Lead.Etapa.values]
    qs = qs.filter(etapa__in=etapas) if etapas else qs.exclude(etapa__in=[Lead.Etapa.VENDIDO, Lead.Etapa.PERDIDO])
    if filtro.get("origem") in Lead.Origem.values:
        qs = qs.filter(origem=filtro["origem"])
    if filtro.get("portal") in Lead.Portal.values:
        qs = qs.filter(portal=filtro["portal"])
    if str(filtro.get("etiqueta") or "").isdigit():
        qs = qs.filter(etiquetas__pk=int(filtro["etiqueta"]))
    dias = int(filtro.get("dias_parado") or 0)
    if dias > 0:
        qs = qs.filter(atualizado_em__lte=timezone.now() - timedelta(days=dias))
    return qs.order_by("-criado_em")


@transaction.atomic
def criar(nome, modelo, filtro, usuario=None):
    c = Campanha.objects.create(nome=nome[:100], modelo=modelo, filtro=filtro, criada_por=usuario)
    CampanhaEnvio.objects.bulk_create([CampanhaEnvio(campanha=c, lead=l) for l in publico(filtro)[:2000]])
    return c


def numeros(c):
    q = c.envios
    return {"total": q.count(), "enviados": q.filter(status="enviado").count(), "falhas": q.filter(status="falhou").count(),
            "fila": q.filter(status="pendente").count()}


def iniciar(c, agora=None):
    if not c.modelo.aprovado:
        raise whatsapp.WhatsAppErro("O modelo ainda não foi aprovado pela Meta.")
    if not c.envios.filter(status="pendente").exists():
        raise whatsapp.WhatsAppErro("Não há ninguém na fila desta campanha.")
    c.status, c.iniciada_em = Campanha.Status.ENVIANDO, c.iniciada_em or (agora or timezone.now())
    c.save(update_fields=["status", "iniciada_em"])


def pausar(c):
    if c.status == Campanha.Status.ENVIANDO:
        c.status = Campanha.Status.PAUSADA
        c.save(update_fields=["status"])


def enviados_24h(agora=None):
    agora = agora or timezone.now()
    return CampanhaEnvio.objects.filter(status="enviado", enviado_em__gte=agora - timedelta(hours=24)).count()


def rodar_lote(agora=None, limite=LOTE):
    """Chamado pelo worker. Devolve quantas mensagens saíram neste ciclo."""
    agora = agora or timezone.now()
    if not whatsapp.configurado():
        return 0
    folga = settings.WHATSAPP_LIMITE_DIARIO - enviados_24h(agora)
    enviados = 0
    for c in Campanha.objects.filter(status=Campanha.Status.ENVIANDO).select_related("modelo"):
        for e in c.envios.filter(status="pendente").select_related("lead")[:limite * 4]:
            if e.lead.nao_contatar:                      # quem pediu para sair não gasta o limite
                e.status, e.erro = CampanhaEnvio.Status.FALHOU, "Pediu para não receber mensagens."
            elif enviados >= limite or folga <= 0:
                break
            else:
                try:
                    whatsapp.enviar_modelo(e.lead, c.modelo)
                    e.status, e.enviado_em = CampanhaEnvio.Status.ENVIADO, agora
                    enviados += 1
                    folga -= 1
                except whatsapp.WhatsAppErro as erro:
                    e.status, e.erro = CampanhaEnvio.Status.FALHOU, str(erro)[:300]
            e.save(update_fields=["status", "erro", "enviado_em"])
        if not c.envios.filter(status="pendente").exists():
            c.status = Campanha.Status.CONCLUIDA
            c.save(update_fields=["status"])
    return enviados
