"""Meta Conversions API: montagem do payload, outbox (EventoMeta) e envio com retry exponencial.

Nada aqui roda na thread do formulário além de gravar a linha do outbox; o envio é do worker
(`manage.py enviar_eventos_meta`). Tokens e dados pessoais em texto puro nunca vão para o log.
"""
import json
import logging
from datetime import timedelta

import requests
from django.conf import settings
from django.db import IntegrityError
from django.utils import timezone

from . import utils
from .models import EventoMeta, Historico, Lead

log = logging.getLogger(__name__)

MAX_TENTATIVAS = 5
BASE_RETRY_SEGUNDOS = 60            # 1, 2, 4, 8 min entre as 5 tentativas
JANELA_EVENTO_DIAS = 7              # a Meta recusa event_time mais antigo que ~7 dias (confira na documentação atual)

# etapa do CRM -> (evento, action_source)
EVENTOS_CRM = {
    Lead.Etapa.QUALIFICADO: ("QualifiedLead", "system_generated"),
    Lead.Etapa.VISITA: ("Schedule", "system_generated"),
    Lead.Etapa.VENDIDO: ("Purchase", "physical_store"),
}


def user_data(lead=None, ip="", user_agent="", fbp="", fbc=""):
    """Campos com hash (ph, fn, ln, external_id, country) e sem hash (ip, ua, fbp, fbc)."""
    ud = {"country": [utils.sha256("br")]}
    if lead is not None:
        ip, user_agent, fbp, fbc = lead.ip or "", lead.user_agent, lead.fbp, lead.fbc
        if lead.telefone:
            ud["ph"] = [utils.hash_telefone(lead.telefone)]
        fn, ln = utils.separar_nome(lead.nome)
        if fn:
            ud["fn"] = [utils.sha256(fn)]
        if ln:
            ud["ln"] = [utils.sha256(ln)]
        ud["external_id"] = [utils.sha256(str(lead.uuid))]
    if ip:
        ud["client_ip_address"] = str(ip)
    if user_agent:
        ud["client_user_agent"] = user_agent
    if fbp:
        ud["fbp"] = fbp
    if fbc:
        ud["fbc"] = fbc
    return ud


def custom_data(veiculo=None, valor=None, classe=None, extra=None):
    cd = {"content_type": "vehicle", "currency": "BRL"}
    if veiculo is not None:
        cd["content_ids"] = [str(veiculo.pk)]
        cd["value"] = float(valor if valor is not None else (veiculo.valor_venda or 0))
    elif valor is not None:
        cd["value"] = float(valor)
    if classe:
        cd["lead_class"] = classe
    cd.update(extra or {})
    return cd


def montar_evento(nome, event_id, user, custom, action_source="website", url="", quando=None):
    evento = {
        "event_name": nome,
        "event_time": int((quando or timezone.now()).timestamp()),
        "event_id": event_id,
        "action_source": action_source,
        "user_data": user,
        "custom_data": custom,
    }
    if url and action_source == "website":
        evento["event_source_url"] = url
    return evento


def enfileirar(nome, event_id, payload, lead=None):
    """Grava no outbox. Idempotente: o mesmo event_id só entra uma vez. Devolve (evento, criado)."""
    try:
        return EventoMeta.objects.get_or_create(
            event_id=event_id, defaults={"nome": nome, "payload": payload, "lead": lead})
    except IntegrityError:   # corrida entre duas requisições
        return EventoMeta.objects.get(event_id=event_id), False


def enfileirar_evento_web(nome, event_id, request_info, veiculo=None, lead=None, url="", extra=None):
    """PageView / ViewContent / Lead / Contact (navegador + servidor com o mesmo event_id)."""
    ud = user_data(lead=lead, ip=request_info.get("ip", ""), user_agent=request_info.get("ua", ""),
                   fbp=request_info.get("fbp", ""), fbc=request_info.get("fbc", ""))
    cd = custom_data(veiculo=veiculo, classe=lead.classe if lead else None, extra=extra)
    return enfileirar(nome, event_id, montar_evento(nome, event_id, ud, cd, "website", url), lead)


def enfileirar_evento_crm(lead, etapa):
    """QualifiedLead / Schedule / Purchase: no máximo um por lead (event_id = '<uuid>:<evento>').
    Só leads de anúncio entram. Devolve o EventoMeta ou None."""
    if etapa not in EVENTOS_CRM or not lead.de_anuncio:
        return None
    nome, fonte = EVENTOS_CRM[etapa]
    event_id = f"{lead.uuid}:{nome}"
    if nome == "Purchase":
        veiculo, valor = lead.veiculo_vendido or lead.veiculo, lead.valor_venda
    else:
        veiculo, valor = lead.veiculo, None
    cd = custom_data(veiculo=veiculo, valor=valor, classe=lead.classe)
    ev = montar_evento(nome, event_id, user_data(lead=lead), cd, fonte)
    ev, criado = enfileirar(nome, event_id, ev, lead)
    if criado:
        Historico.objects.create(lead=lead, tipo=Historico.Tipo.META, texto=f"{nome} na fila para a Meta")
    return ev


# ---- envio ------------------------------------------------------------------------------------
def endpoint():
    return f"https://graph.facebook.com/{settings.META_GRAPH_API_VERSION}/{settings.META_PIXEL_ID}/events"


def pode_enviar():
    return bool(settings.META_CAPI_ENABLED and settings.META_PIXEL_ID and settings.META_CAPI_ACCESS_TOKEN)


def _corpo(evento_meta, test_code=""):
    corpo = {"data": [evento_meta.payload], "access_token": settings.META_CAPI_ACCESS_TOKEN}
    code = test_code or settings.META_TEST_EVENT_CODE
    if code:
        corpo["test_event_code"] = code
    return corpo


def processar(ev, agora=None, sessao=None):
    """Tenta enviar um evento. Atualiza status/tentativas/resposta. Devolve o status final."""
    agora = agora or timezone.now()
    quando = ev.payload.get("event_time", 0)
    if quando < (agora - timedelta(days=JANELA_EVENTO_DIAS)).timestamp():
        ev.status = EventoMeta.Status.NAO_ENVIADO
        ev.motivo = f"event_time fora da janela de {JANELA_EVENTO_DIAS} dias da Meta"
        ev.save(update_fields=["status", "motivo"])
        return ev.status

    ev.tentativas += 1
    try:
        r = (sessao or requests).post(endpoint(), json=_corpo(ev), timeout=15)
        ok = r.status_code == 200
        ev.resposta = r.text[:2000]
        motivo = "" if ok else f"HTTP {r.status_code}"
    except requests.RequestException as e:
        ok, motivo = False, f"falha de rede: {type(e).__name__}"
        ev.resposta = ""

    if ok:
        ev.status, ev.enviado_em, ev.motivo = EventoMeta.Status.ENVIADO, agora, ""
    elif ev.tentativas >= MAX_TENTATIVAS:
        ev.status, ev.motivo = EventoMeta.Status.ERRO, motivo
    else:
        ev.motivo = motivo
        ev.proxima_tentativa_em = agora + timedelta(seconds=BASE_RETRY_SEGUNDOS * 2 ** (ev.tentativas - 1))
    ev.save()
    return ev.status


def reservar_lote(limite=50, agora=None):
    """Pega eventos vencidos e os 'reserva' (empurra a próxima tentativa) para dois workers não enviarem o mesmo."""
    agora = agora or timezone.now()
    ids = list(EventoMeta.objects.filter(status=EventoMeta.Status.PENDENTE, proxima_tentativa_em__lte=agora)
               .order_by("criado_em").values_list("pk", flat=True)[:limite])
    reservados = []
    for pk in ids:
        n = EventoMeta.objects.filter(pk=pk, status=EventoMeta.Status.PENDENTE, proxima_tentativa_em__lte=agora) \
            .update(proxima_tentativa_em=agora + timedelta(minutes=5))
        if n:
            reservados.append(pk)
    return list(EventoMeta.objects.filter(pk__in=reservados).order_by("criado_em"))


def rodar_lote(limite=50, sessao=None):
    """Um ciclo do worker. Devolve {'enviados': n, 'erros': n, 'pendentes': n, 'descartados': n}."""
    if not pode_enviar():
        return {"desligado": True}
    contagem = {"enviados": 0, "erros": 0, "pendentes": 0, "descartados": 0}
    for ev in reservar_lote(limite):
        s = processar(ev, sessao=sessao)
        chave = {"enviado": "enviados", "erro": "erros", "pendente": "pendentes", "nao_enviado": "descartados"}[s]
        contagem[chave] += 1
    return contagem


def enviar_teste(test_code, nome="PageView"):
    """Envia um evento direto (sem outbox) com test_event_code para conferir em 'Testar eventos'."""
    evento = montar_evento(nome, f"teste-{int(timezone.now().timestamp())}",
                           {"client_user_agent": "TopCarCRM/teste", "client_ip_address": "127.0.0.1",
                            "country": [utils.sha256("br")]},
                           {"currency": "BRL", "value": 1.0, "content_type": "vehicle"},
                           "website", settings.SITE_URL + "/")
    corpo = {"data": [evento], "access_token": settings.META_CAPI_ACCESS_TOKEN, "test_event_code": test_code}
    r = requests.post(endpoint(), json=corpo, timeout=15)
    return r.status_code, r.text[:500]
