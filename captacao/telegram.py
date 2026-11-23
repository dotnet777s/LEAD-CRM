"""Bot do Telegram: avisos de lead novo, lembretes de retorno e alerta de lead parado.

Nada é enviado dentro de uma requisição: tudo entra na fila `NotificacaoTelegram` e o worker
(`manage.py telegram_bot`) envia, trata os comandos/botões e roda as verificações periódicas.
"""
import html
import logging
import secrets
from datetime import timedelta

import requests
from django.conf import settings
from django.utils import timezone

from . import utils
from .models import Lead, NotificacaoTelegram, TelegramVinculo

log = logging.getLogger(__name__)
MAX_TENTATIVAS = 5
VALIDADE_CODIGO_MIN = 30
ALFABETO = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def configurado():
    return bool(settings.TELEGRAM_BOT_TOKEN)


def api(metodo, **dados):
    """Chama a Bot API. Devolve o JSON; levanta requests.RequestException em falha de rede."""
    url = f"https://api.telegram.org/bot{settings.TELEGRAM_BOT_TOKEN}/{metodo}"
    r = requests.post(url, json=dados, timeout=dados.get("timeout", 0) + 15)
    try:
        return r.json()
    except ValueError:
        return {"ok": False, "description": f"HTTP {r.status_code}"}


def esc(txt):
    return html.escape(str(txt or ""), quote=False)


# ---------------------------------------------------------------- vínculo usuário <-> chat
def gerar_codigo(user, agora=None):
    agora = agora or timezone.now()
    v, _ = TelegramVinculo.objects.get_or_create(user=user)
    v.codigo = "".join(secrets.choice(ALFABETO) for _ in range(8))
    v.codigo_expira_em = agora + timedelta(minutes=VALIDADE_CODIGO_MIN)
    v.save(update_fields=["codigo", "codigo_expira_em"])
    return v.codigo


def vincular(codigo, chat_id, agora=None):
    """Consome o código e liga o chat ao usuário. Devolve o usuário ou None."""
    agora = agora or timezone.now()
    codigo = (codigo or "").strip().upper()
    if not codigo:
        return None
    v = (TelegramVinculo.objects.select_related("user")
         .filter(codigo=codigo, codigo_expira_em__gte=agora, user__is_active=True).first())
    if not v:
        return None
    TelegramVinculo.objects.filter(chat_id=chat_id).exclude(pk=v.pk).update(chat_id=None)   # um chat, um usuário
    v.chat_id, v.codigo, v.codigo_expira_em, v.vinculado_em = chat_id, "", None, agora
    v.save()
    return v.user


def usuario_do_chat(chat_id):
    v = TelegramVinculo.objects.select_related("user").filter(chat_id=chat_id, user__is_active=True).first()
    return v.user if v else None


# ---------------------------------------------------------------- fila
def enfileirar(chat_id, texto, botoes=None, lead=None, tipo="", chave=None):
    """Idempotente por `chave`. Devolve (notificacao, criada)."""
    if chave:
        existente = NotificacaoTelegram.objects.filter(chave=chave).first()
        if existente:
            return existente, False
    n = NotificacaoTelegram.objects.create(chave=chave, tipo=tipo, chat_id=chat_id, texto=texto[:4000],
                                           botoes=botoes or [], lead=lead)
    return n, True


def _chats():
    """Quem recebe os avisos: o acesso do SDR (todo chat vinculado a um usuário ativo)."""
    return list(TelegramVinculo.objects.select_related("user").filter(chat_id__isnull=False, user__is_active=True))


CLASSES = {"A": ("🔥", "Quente"), "B": ("🟡", "Morno"), "C": ("❄️", "Frio")}


def _quando(dt):
    """'Hoje, 14:32' / 'Ontem, 09:10' / '12/11, 16:00' (horário de Recife)."""
    dt = timezone.localtime(dt)
    dia = dt.date()
    hoje = timezone.localdate()
    rotulo = "Hoje" if dia == hoje else "Ontem" if dia == hoje - timedelta(days=1) else dia.strftime("%d/%m")
    return f"{rotulo}, {dt:%H:%M}"


def _carro(lead):
    """Linha(s) do veículo; com o carro vinculado mostra preço, km, cor e onde está."""
    v = lead.veiculo
    if not v:
        return esc(lead.carro_texto or "Sem carro definido")
    local = v.local
    detalhe = " · ".join(x for x in (utils.preco_brl(v.valor_venda), f"{v.km:,}".replace(",", ".") + " km" if v.km else "",
                                     v.cor_fmt, f"em {local}" if local else "") if x)
    return f"<b>{esc(v.marca_fmt)} {esc(v.modelo_fmt)} {v.ano}</b>\n{esc(detalhe)}"


def texto_lead(lead, titulo):
    r = lead.respostas or {}
    emoji, temp = CLASSES.get(lead.classe, ("", ""))
    cab = f"{emoji} <b>{esc(titulo.upper())}</b>" + (f" · Classe {lead.classe} ({temp})" if temp else "")
    quer = "Agendar visita" if r.get("intencao") == "visita" else "Simular"
    linhas = [cab, "", f"👤 <b>{esc(lead.nome)}</b>", f"📱 {esc(lead.telefone_formatado)}",
              f"🕒 {_quando(lead.criado_em)}", "", f"🚗 {_carro(lead)}", ""]
    resumo = ""
    perfil, partes = [], []
    if r.get("pagamento"):
        pag = utils.rotulo(utils.PAGAMENTO, r.get("pagamento"))
        perfil.append(f"• Pagamento: {esc(pag)}"); partes.append(pag)
    if r.get("entrada"):
        ent = utils.rotulo(utils.ENTRADA, r.get("entrada"))
        perfil.append(f"• Entrada: {esc(ent)}"); partes.append(f"entrada {ent.lower()}")
    if r.get("troca"):
        troca = "Não" if r.get("troca") != "sim" else (f"Sim ({r['troca_carro']})" if r.get("troca_carro") else "Sim")
        perfil.append(f"• Carro/moto na troca: {esc(troca)}"); partes.append(f"troca: {troca.lower()}")
    if r.get("prazo"):
        prazo = utils.rotulo(utils.PRAZO, r.get("prazo"))
        perfil.append(f"• Compra: {esc(prazo)}"); partes.append(f"compra: {prazo.lower()}")
    if r.get("simulacao"):
        perfil.append(f"• Simulação no site: {esc(utils.simulacao_txt(r['simulacao']))}")
        partes.append(f"simulou {utils.simulacao_txt(r['simulacao'])}")
    if r.get("parcial"):
        linhas += [f"⏳ <i>Não terminou o formulário (falta troca e entrada). {esc('Quer: ' + quer)}</i>" if perfil
                   else f"⏳ <i>Deixou o contato e não terminou o formulário. {esc('Quer: ' + quer)}</i>", ""]
    if perfil:
        linhas += ["💬 <b>Perfil do cliente</b>", f"• Quer: {quer}"] + perfil + [""]
        resumo = " · ".join(partes)
    origem = lead.get_origem_display() + (f" · {lead.utm_campaign}" if lead.utm_campaign else "")
    linhas.append(f"📣 {esc(origem)}")
    if lead.utm_content:
        linhas.append(f"🎯 Anúncio: {esc(lead.utm_content)}")
    if lead.vendedor_id:
        linhas.append(f"🤝 Vendedor: {esc(lead.vendedor.first_name or lead.vendedor.username)}")
    # bloco para copiar e colar no grupo dos vendedores
    carro_txt = f"{lead.veiculo.marca_fmt} {lead.veiculo.modelo_fmt} {lead.veiculo.ano}" if lead.veiculo else (lead.carro_texto or "sem carro definido")
    copia = [f"{lead.nome} · {lead.telefone_formatado}", carro_txt] + ([resumo] if resumo else [])
    linhas += ["", "📋 <i>Para o grupo:</i>", f"<pre>{esc(chr(10).join(copia))}</pre>"]
    return "\n".join(linhas)


def botoes_lead(lead):
    from django.conf import settings as st
    linha = []
    if lead.whatsapp_url:
        linha.append({"text": "Chamar no WhatsApp", "url": lead.whatsapp_url})
    linha.append({"text": "Abrir no CRM", "url": f"{st.SITE_URL}/lead/{lead.pk}/"})
    return [linha]


def avisar_novo_lead(lead):
    """Lead novo: avisa o SDR (todo chat vinculado)."""
    if not configurado():
        return 0
    n = 0
    for v in _chats():
        _, criada = enfileirar(v.chat_id, texto_lead(lead, "Novo lead"), botoes_lead(lead), lead, "novo", f"novo:{lead.pk}:{v.chat_id}")
        n += criada
    return n


def avisar_lead_completo(lead):
    """Quem tinha deixado só o contato terminou o formulário. Se o SDR já foi avisado do formulário incompleto,
    manda 'terminou o formulário'; se não (terminou rápido), é um lead novo comum."""
    if not configurado():
        return 0
    ja_avisado = NotificacaoTelegram.objects.filter(lead=lead, tipo="requalificar").exists()
    n = 0
    for v in _chats():
        _, criada = enfileirar(v.chat_id, texto_lead(lead, "Lead terminou o formulário" if ja_avisado else "Novo lead"),
                               botoes_lead(lead), lead, "completo", f"completo:{lead.pk}:{v.chat_id}")
        n += criada
    return n


def botoes_requalificar(lead):
    from django.conf import settings as st
    linha = []
    if lead.whatsapp_requalificar_url:
        linha.append({"text": "Requalificar no WhatsApp", "url": lead.whatsapp_requalificar_url})
    linha.append({"text": "Abrir no CRM", "url": f"{st.SITE_URL}/lead/{lead.pk}/"})
    return [linha]


def alertas_requalificar(agora=None):
    """Lead que deixou o contato e NÃO terminou o formulário depois de alguns minutos: avisa o SDR para requalificar
    (uma vez por lead). Fica na coluna 'Requalificar' do funil."""
    agora = agora or timezone.now()
    espera = timedelta(minutes=settings.TELEGRAM_REQUALIFICAR_MINUTOS)
    qs = Lead.objects.filter(etapa=Lead.Etapa.REQUALIFICAR, criado_em__lte=agora - espera,
                             criado_em__gte=agora - timedelta(hours=24))
    n = 0
    for lead in qs:
        if not lead.incompleto:
            continue
        for v in _chats():
            _, criada = enfileirar(v.chat_id, texto_lead(lead, "Requalificar: não terminou o formulário"),
                                   botoes_requalificar(lead), lead, "requalificar", f"requalificar:{lead.pk}:{v.chat_id}")
            n += criada
    return n


# ---------------------------------------------------------------- envio
def enviar(n, agora=None):
    agora = agora or timezone.now()
    n.tentativas += 1
    dados = {"chat_id": n.chat_id, "text": n.texto, "parse_mode": "HTML", "disable_web_page_preview": True}
    if n.botoes:
        dados["reply_markup"] = {"inline_keyboard": n.botoes}
    try:
        r = api("sendMessage", **dados)
        ok, motivo, codigo = bool(r.get("ok")), r.get("description", ""), r.get("error_code")
    except requests.RequestException as e:
        ok, motivo, codigo = False, f"rede: {e.__class__.__name__}", None
    if ok:
        n.status, n.enviado_em, n.motivo = NotificacaoTelegram.Status.ENVIADO, agora, ""
    elif codigo in (400, 403) or n.tentativas >= MAX_TENTATIVAS:      # chat bloqueado/inválido: não adianta repetir
        n.status, n.motivo = NotificacaoTelegram.Status.ERRO, motivo[:300]
    else:
        n.motivo = motivo[:300]
        n.proxima_tentativa_em = agora + timedelta(seconds=30 * 2 ** (n.tentativas - 1))
    n.save()
    return n.status


def processar_fila(limite=30, agora=None):
    agora = agora or timezone.now()
    pend = NotificacaoTelegram.objects.filter(status="pendente", proxima_tentativa_em__lte=agora).order_by("criado_em")[:limite]
    return sum(1 for n in pend if enviar(n, agora) == NotificacaoTelegram.Status.ENVIADO)


# ---------------------------------------------------------------- verificações periódicas
def lembretes_de_retorno(agora=None):
    """Avisa o SDR quando chega a hora do 'próximo contato' (uma vez por horário marcado)."""
    agora = agora or timezone.now()
    n = 0
    qs = (Lead.objects.select_related("vendedor")
          .filter(proximo_contato__lte=agora, proximo_contato__gte=agora - timedelta(hours=12))
          .exclude(etapa__in=[Lead.Etapa.VENDIDO, Lead.Etapa.PERDIDO]))
    for lead in qs:
        for v in _chats():
            _, criada = enfileirar(v.chat_id, texto_lead(lead, "Hora de retornar este lead"), botoes_lead(lead),
                                   lead, "retorno", f"retorno:{lead.pk}:{int(lead.proximo_contato.timestamp())}:{v.chat_id}")
            n += criada
    return n


def alertas_lead_parado(agora=None):
    """Lead ainda em 'Novo' depois de TELEGRAM_SLA_MINUTOS: avisa o SDR (uma vez por lead)."""
    agora = agora or timezone.now()
    limite = timedelta(minutes=settings.TELEGRAM_SLA_MINUTOS)
    n = 0
    qs = Lead.objects.select_related("vendedor").filter(etapa=Lead.Etapa.NOVO, criado_em__lte=agora - limite,
                                                        criado_em__gte=agora - timedelta(hours=6))
    for lead in qs:
        for v in _chats():
            _, criada = enfileirar(v.chat_id, texto_lead(lead, f"Sem atendimento há {settings.TELEGRAM_SLA_MINUTOS} min"),
                                   botoes_lead(lead), lead, "sla", f"sla:{lead.pk}:{v.chat_id}")
            n += criada
    return n


# ---------------------------------------------------------------- comandos e botões
AJUDA = ("Comandos:\n/leads: leads novos aguardando encaminhamento\n/ajuda: esta mensagem\n\n"
         "Cada lead novo chega aqui com o perfil do cliente. Encaminhe ao vendedor pelo CRM.")


def _responder(chat_id, texto, botoes=None):
    dados = {"chat_id": chat_id, "text": texto, "parse_mode": "HTML", "disable_web_page_preview": True}
    if botoes:
        dados["reply_markup"] = {"inline_keyboard": botoes}
    try:
        api("sendMessage", **dados)
    except requests.RequestException:
        log.warning("telegram: falha ao responder")


def tratar_mensagem(msg):
    chat_id = (msg.get("chat") or {}).get("id")
    texto = (msg.get("text") or "").strip()
    if not chat_id or (msg.get("chat") or {}).get("type") != "private":
        return
    partes = texto.split(maxsplit=1)
    cmd = partes[0].split("@")[0].lower() if partes else ""
    if cmd == "/start":
        user = vincular(partes[1] if len(partes) > 1 else "", chat_id)
        if user:
            _responder(chat_id, f"Pronto, {esc(user.first_name or user.username)}! Você vai receber aqui os avisos de leads da TopCar.\n\n{AJUDA}")
        elif usuario_do_chat(chat_id):
            _responder(chat_id, AJUDA)
        else:
            _responder(chat_id, "Para ativar, peça ao gestor o código na tela Equipe do CRM e envie: /start CODIGO")
    elif cmd == "/leads":
        user = usuario_do_chat(chat_id)
        if not user:
            _responder(chat_id, "Este chat ainda não está vinculado. Peça o código ao gestor.")
            return
        abertos = Lead.objects.filter(etapa=Lead.Etapa.NOVO, vendedor__isnull=True).order_by("-criado_em")[:10]
        if not abertos:
            _responder(chat_id, "Nenhum lead novo aguardando encaminhamento.")
        for lead in abertos:
            _responder(chat_id, texto_lead(lead, "Aguardando encaminhamento"), botoes_lead(lead))
    else:
        _responder(chat_id, AJUDA if usuario_do_chat(chat_id) else "Envie /start CODIGO para ativar.")


def tratar_botao(cb):
    """Os botões de aviso são só links; botões antigos (ex.: Assumir) respondem que foram desativados."""
    try:
        api("answerCallbackQuery", callback_query_id=cb["id"], text="Esta ação foi desativada. Use o CRM.")
    except (requests.RequestException, KeyError):
        pass


def tratar_update(u):
    if "callback_query" in u:
        tratar_botao(u["callback_query"])
    elif "message" in u:
        tratar_mensagem(u["message"])
