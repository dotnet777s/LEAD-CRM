"""WhatsApp Business Platform (API oficial da Meta, Cloud API): envio, webhook e janela de 24 h.

Tudo fica desligado até o .env ter WHATSAPP_PHONE_ID e WHATSAPP_TOKEN. Enquanto isso o CRM funciona normalmente
(follow-up vira tarefa do SDR). Quando ligar:
- o cliente que escreve cai no CRM (lead novo ou o já existente) e, se havia follow-up, ele PARA sozinho;
- a equipe responde e o follow-up pode enviar sozinho: texto livre dentro das 24 h, template aprovado fora delas."""
import hashlib
import hmac
import logging
from datetime import timedelta

import requests
from django.conf import settings
from django.db import transaction
from django.utils import timezone

from . import followup, telegram, utils
from .models import Historico, Lead, WhatsAppMensagem

log = logging.getLogger(__name__)
JANELA = timedelta(hours=24)


class WhatsAppErro(Exception):
    pass


class JanelaFechada(WhatsAppErro):
    """Passaram 24 h desde a última mensagem do cliente: só vai template aprovado."""


def configurado():
    return bool(settings.WHATSAPP_PHONE_ID and settings.WHATSAPP_TOKEN)


def _url():
    return f"https://graph.facebook.com/{settings.WHATSAPP_API_VERSION}/{settings.WHATSAPP_PHONE_ID}/messages"


def _post(corpo):
    r = requests.post(_url(), json={"messaging_product": "whatsapp", **corpo}, timeout=20,
                      headers={"Authorization": f"Bearer {settings.WHATSAPP_TOKEN}"})
    try:
        dados = r.json()
    except ValueError:
        dados = {}
    if r.status_code >= 400:
        raise WhatsAppErro((dados.get("error") or {}).get("message") or f"HTTP {r.status_code}")
    return (dados.get("messages") or [{}])[0].get("id", "")


# ---------------------------------------------------------------- janela e telefone
def variantes_telefone(tel):
    """O WhatsApp pode entregar celular BR sem o 9 (55 + DDD + 8 dígitos). Devolve as duas formas para casar com o lead."""
    d = utils.so_digitos(tel)
    out = {d}
    if d.startswith("55") and len(d) == 12:
        out.add(d[:4] + "9" + d[4:])
    elif d.startswith("55") and len(d) == 13 and d[4] == "9":
        out.add(d[:4] + d[5:])
    return out


def janela_aberta(telefone, agora=None):
    agora = agora or timezone.now()
    return WhatsAppMensagem.objects.filter(telefone__in=variantes_telefone(telefone), direcao="in",
                                           criado_em__gte=agora - JANELA).exists()


def lead_do_telefone(telefone):
    return Lead.objects.filter(telefone__in=variantes_telefone(telefone)).order_by("-criado_em").first()


# ---------------------------------------------------------------- envio
def enviar_texto(lead, texto, usuario=None):
    """Texto livre: só dentro da janela de 24 h."""
    if not configurado():
        raise WhatsAppErro("API do WhatsApp não configurada.")
    if not janela_aberta(lead.telefone):
        raise JanelaFechada("Conversa fora da janela de 24 h: use um template aprovado.")
    wamid = _post({"to": lead.telefone, "type": "text", "text": {"body": texto, "preview_url": False}})
    return WhatsAppMensagem.objects.create(lead=lead, telefone=lead.telefone, direcao="out", tipo="text", texto=texto,
                                           wamid=wamid, status=WhatsAppMensagem.Status.ENVIADA, usuario=usuario)


def enviar_template(lead, nome, parametros=(), idioma="pt_BR", usuario=None, texto_ref=""):
    """Template aprovado na Meta (pode ser enviado a qualquer hora). `parametros` preenchem {{1}}, {{2}}..."""
    if not configurado():
        raise WhatsAppErro("API do WhatsApp não configurada.")
    corpo = {"to": lead.telefone, "type": "template", "template": {"name": nome, "language": {"code": idioma}}}
    if parametros:
        corpo["template"]["components"] = [{"type": "body", "parameters": [{"type": "text", "text": str(p)} for p in parametros]}]
    wamid = _post(corpo)
    return WhatsAppMensagem.objects.create(lead=lead, telefone=lead.telefone, direcao="out", tipo="template",
                                           texto=texto_ref or f"[template {nome}]", wamid=wamid,
                                           status=WhatsAppMensagem.Status.ENVIADA, usuario=usuario)


def enviar_followup(fu, agora=None):
    """Envia o passo atual pela API: texto na janela de 24 h, senão o template do passo (se houver).
    Devolve True se enviou; False se não deu (fica como tarefa para a equipe)."""
    passo = followup.passo_do(fu)
    if not passo or not configurado():
        return False
    texto = followup.montar_mensagem(fu, passo)
    try:
        if janela_aberta(fu.lead.telefone, agora):
            enviar_texto(fu.lead, texto)
        elif passo.template_nome:
            primeiro = (fu.lead.nome or "").split()[0].title() if fu.lead.nome else ""
            enviar_template(fu.lead, passo.template_nome, [primeiro or "tudo bem"], texto_ref=texto)
        else:
            return False
    except WhatsAppErro as e:
        log.warning("whatsapp: follow-up %s não enviado: %s", fu.pk, e)
        return False
    followup.registrar_tentativa(fu, agora=agora, via_api=True, mensagem=texto)
    return True


def rodar_followup_automatico(agora=None):
    """Chamado pelo worker: envia sozinho os passos vencidos (só com 'envio automático' ligado e a API conectada)."""
    from .models import FollowUpConfig
    if not configurado() or not FollowUpConfig.obter().envio_automatico:
        return 0
    return sum(1 for fu in followup.devidos(agora) if enviar_followup(fu, agora))


# ---------------------------------------------------------------- webhook
def assinatura_valida(corpo_bytes, cabecalho):
    """X-Hub-Signature-256 = sha256=HMAC(app secret, corpo). Sem o segredo configurado, recusa tudo."""
    segredo = settings.WHATSAPP_APP_SECRET
    if not segredo or not cabecalho.startswith("sha256="):
        return False
    esperado = hmac.new(segredo.encode(), corpo_bytes, hashlib.sha256).hexdigest()
    return hmac.compare_digest(esperado, cabecalho.split("=", 1)[1])


def _texto_da_mensagem(m):
    tipo = m.get("type", "text")
    if tipo == "text":
        return (m.get("text") or {}).get("body", "")
    if tipo == "button":
        return (m.get("button") or {}).get("text", "[botão]")
    if tipo == "interactive":
        i = m.get("interactive") or {}
        return ((i.get("button_reply") or i.get("list_reply") or {}).get("title")) or "[resposta]"
    return {"image": "[imagem]", "audio": "[áudio]", "video": "[vídeo]", "document": "[documento]", "sticker": "[figurinha]",
            "location": "[localização]"}.get(tipo, f"[{tipo}]")


@transaction.atomic
def _entrada(m, nome_perfil, agora):
    wamid = m.get("id", "")
    if wamid and WhatsAppMensagem.objects.filter(wamid=wamid).exists():
        return None                                   # a Meta reenvia: não duplica
    telefone = utils.so_digitos(m.get("from", ""))
    lead = lead_do_telefone(telefone)
    novo = False
    if lead is None:
        tel = utils.normalizar_telefone(telefone) or telefone
        lead = Lead.objects.create(nome=(nome_perfil or "Cliente WhatsApp")[:120], telefone=tel[:13], origem=Lead.Origem.WHATSAPP,
                                   criado_em=agora, etapa_desde=agora)
        Historico.objects.create(lead=lead, tipo=Historico.Tipo.CRIADO, criado_em=agora, texto="Cliente chamou direto no WhatsApp")
        novo = True
    texto = _texto_da_mensagem(m)
    msg = WhatsAppMensagem.objects.create(lead=lead, telefone=telefone, direcao="in", tipo=m.get("type", "text"), texto=texto,
                                          wamid=wamid, status=WhatsAppMensagem.Status.RECEBIDA, criado_em=agora)
    parou = followup.cliente_respondeu(lead, agora=agora)
    try:
        if novo:
            telegram.avisar_novo_lead(lead)
        else:
            telegram.avisar_mensagem_whatsapp(lead, texto, wamid, parou > 0)
    except Exception:  # noqa: BLE001 - aviso nunca impede de gravar a mensagem
        log.exception("whatsapp: falha no aviso do Telegram")
    return msg


def _status(s):
    wamid, st = s.get("id", ""), s.get("status", "")
    mapa = {"sent": "sent", "delivered": "delivered", "read": "read", "failed": "failed"}
    if wamid and st in mapa:
        erro = ((s.get("errors") or [{}])[0].get("title") or "")[:300] if st == "failed" else ""
        WhatsAppMensagem.objects.filter(wamid=wamid, direcao="out").update(status=mapa[st], erro=erro)


def processar_webhook(payload, agora=None):
    """Processa o JSON do webhook (mensagens do cliente e status das enviadas). Devolve quantas mensagens novas entraram."""
    agora = agora or timezone.now()
    n = 0
    for entrada in payload.get("entry", []):
        for mudanca in entrada.get("changes", []):
            v = mudanca.get("value", {})
            perfis = {c.get("wa_id"): (c.get("profile") or {}).get("name", "") for c in v.get("contacts", [])}
            for m in v.get("messages", []):
                if _entrada(m, perfis.get(m.get("from"), ""), agora):
                    n += 1
            for s in v.get("statuses", []):
                _status(s)
    return n
