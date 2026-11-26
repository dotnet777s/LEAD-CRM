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
from .models import Historico, Lead, ModeloWhatsApp, WhatsAppMensagem

log = logging.getLogger(__name__)
JANELA = timedelta(hours=24)
MIDIAS = ("image", "audio", "video", "document", "sticker")
PEDIDOS_DE_SAIDA = {"sair", "parar", "pare", "cancelar", "stop", "nao quero receber", "não quero receber"}


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
    tipo = m.get("type", "text")
    midia = m.get(tipo) if isinstance(m.get(tipo), dict) else {}
    if midia.get("caption"):
        texto = f"{texto} {midia['caption']}".strip()
    com_midia = tipo in MIDIAS
    msg = WhatsAppMensagem.objects.create(lead=lead, telefone=telefone, direcao="in", tipo=tipo, texto=texto, wamid=wamid,
                                          status=WhatsAppMensagem.Status.RECEBIDA, criado_em=agora, lida=False,
                                          midia_id=(midia.get("id") or "") if com_midia else "",
                                          midia_tipo=(midia.get("mime_type") or "")[:100] if com_midia else "")
    if tipo == "text" and texto.strip().lower().strip("!. ") in PEDIDOS_DE_SAIDA and not lead.nao_contatar:
        lead.nao_contatar = True
        lead.save(update_fields=["nao_contatar", "atualizado_em"])
        Historico.objects.create(lead=lead, tipo=Historico.Tipo.NOTA, criado_em=agora, texto="Cliente pediu para não receber mais mensagens")
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


# ---------------------------------------------------------------- mídia, imagens e leitura
def baixar_midia(media_id):
    """Baixa uma mídia recebida (a Meta exige o token). Devolve (bytes, mime)."""
    if not configurado():
        raise WhatsAppErro("API do WhatsApp não configurada.")
    h = {"Authorization": f"Bearer {settings.WHATSAPP_TOKEN}"}
    r = requests.get(f"https://graph.facebook.com/{settings.WHATSAPP_API_VERSION}/{media_id}", headers=h, timeout=20)
    dados = r.json() if r.status_code < 400 else {}
    if not dados.get("url"):
        raise WhatsAppErro("Mídia indisponível (expirou ou foi removida).")
    arq = requests.get(dados["url"], headers=h, timeout=40)
    if arq.status_code >= 400:
        raise WhatsAppErro("Não foi possível baixar a mídia.")
    return arq.content, dados.get("mime_type", "application/octet-stream")


def enviar_imagem(lead, link, legenda="", usuario=None):
    """Imagem por link público (ex.: foto do carro). Só dentro da janela de 24 h."""
    if not configurado():
        raise WhatsAppErro("API do WhatsApp não configurada.")
    if not janela_aberta(lead.telefone):
        raise JanelaFechada("Conversa fora da janela de 24 h: use um template aprovado.")
    wamid = _post({"to": lead.telefone, "type": "image", "image": {"link": link, "caption": legenda[:1000]}})
    return WhatsAppMensagem.objects.create(lead=lead, telefone=lead.telefone, direcao="out", tipo="image", texto=legenda or "[imagem]",
                                           wamid=wamid, status=WhatsAppMensagem.Status.ENVIADA, usuario=usuario)


def confirmar_leitura(wamid):
    """Avisa a Meta que a loja leu a mensagem (o cliente vê o 'azul'). Melhor esforço: falha não atrapalha."""
    if not (configurado() and wamid):
        return
    try:
        _post({"status": "read", "message_id": wamid})
    except WhatsAppErro:
        pass


# ---------------------------------------------------------------- modelos (templates) da Meta
def _corpo_e_variaveis(componentes):
    import re
    corpo = next((c.get("text", "") for c in componentes if c.get("type") == "BODY"), "")
    return corpo, len(set(re.findall(r"\{\{(\d+)\}\}", corpo)))


def _erro_meta(r):
    e = (r.json() or {}).get("error") or {}
    return e.get("error_user_msg") or e.get("message") or f"HTTP {r.status_code}"


def sincronizar_modelos():
    """Traz da Meta os templates da conta (nome, status de aprovação, texto). Devolve quantos foram atualizados."""
    if not (configurado() and settings.WHATSAPP_WABA_ID):
        raise WhatsAppErro("Falta WHATSAPP_WABA_ID e/ou a conexão da API.")
    h = {"Authorization": f"Bearer {settings.WHATSAPP_TOKEN}"}
    url = f"https://graph.facebook.com/{settings.WHATSAPP_API_VERSION}/{settings.WHATSAPP_WABA_ID}/message_templates"
    params = {"limit": 100, "fields": "name,language,category,status,components"}
    n = 0
    while url:
        r = requests.get(url, headers=h, params=params, timeout=20)
        if r.status_code >= 400:
            raise WhatsAppErro(_erro_meta(r))
        dados = r.json()
        for t in dados.get("data", []):
            corpo, nv = _corpo_e_variaveis(t.get("components", []))
            ModeloWhatsApp.objects.update_or_create(
                nome=t["name"], idioma=t.get("language", "pt_BR"),
                defaults=dict(categoria=t.get("category", ""), status=t.get("status", ""), corpo=corpo, n_variaveis=nv,
                              atualizado_em=timezone.now()))
            n += 1
        url, params = (dados.get("paging") or {}).get("next"), None
    return n


def criar_modelo(nome, categoria, corpo, exemplos=(), idioma="pt_BR"):
    """Envia um template novo para aprovação da Meta (a aprovação leva de minutos a horas)."""
    if not (configurado() and settings.WHATSAPP_WABA_ID):
        raise WhatsAppErro("Falta WHATSAPP_WABA_ID e/ou a conexão da API.")
    componente = {"type": "BODY", "text": corpo}
    if exemplos:
        componente["example"] = {"body_text": [list(exemplos)]}
    r = requests.post(f"https://graph.facebook.com/{settings.WHATSAPP_API_VERSION}/{settings.WHATSAPP_WABA_ID}/message_templates",
                      headers={"Authorization": f"Bearer {settings.WHATSAPP_TOKEN}"}, timeout=20,
                      json={"name": nome, "language": idioma, "category": categoria, "components": [componente]})
    if r.status_code >= 400:
        raise WhatsAppErro(_erro_meta(r))
    ModeloWhatsApp.objects.update_or_create(nome=nome, idioma=idioma, defaults=dict(
        categoria=categoria, status=r.json().get("status", "PENDING"), corpo=corpo, n_variaveis=len(exemplos),
        atualizado_em=timezone.now()))
    return True


def enviar_modelo(lead, modelo, usuario=None):
    """Envia um ModeloWhatsApp ao lead. {{1}} vira o primeiro nome."""
    primeiro = (lead.nome or "").split()[0].title() if lead.nome else "tudo bem"
    params = ([primeiro] + [""] * (modelo.n_variaveis - 1)) if modelo.n_variaveis else []
    return enviar_template(lead, modelo.nome, params, modelo.idioma, usuario, texto_ref=modelo.corpo.replace("{{1}}", primeiro))


def info_numero():
    """Dados do número na Meta (nome verificado, qualidade, limite diário). Devolve dict ou levanta WhatsAppErro."""
    if not configurado():
        raise WhatsAppErro("API do WhatsApp não configurada.")
    r = requests.get(f"https://graph.facebook.com/{settings.WHATSAPP_API_VERSION}/{settings.WHATSAPP_PHONE_ID}", timeout=20,
                     headers={"Authorization": f"Bearer {settings.WHATSAPP_TOKEN}"},
                     params={"fields": "display_phone_number,verified_name,quality_rating,messaging_limit_tier,code_verification_status"})
    if r.status_code >= 400:
        raise WhatsAppErro(_erro_meta(r))
    return r.json()
