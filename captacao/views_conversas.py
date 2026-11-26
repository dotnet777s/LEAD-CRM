"""Conversas: a caixa de entrada do WhatsApp dentro do CRM (no estilo do Avise Aí): lista com abas e filtros,
conversa com barra de ações, funil na própria conversa, anexos, simulação de parcela, localização e notas."""
import io
from datetime import timedelta

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db.models import Count, Exists, OuterRef, Q, Subquery
from django.http import FileResponse, Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views import View
from django.views.generic import TemplateView

from catalogo.models import Veiculo

from . import audio, fotos, followup, funil, services, utils, whatsapp
from .models import Etiqueta, FollowUp, Historico, Lead, MensagemProgramada, ModeloWhatsApp, RespostaRapida, WhatsAppMensagem
from .views import Base, eh_gestor, leads_visiveis, trigger

ABAS = [("todas", "Todas"), ("meus", "Meus"), ("portais", "Portais"), ("arquivadas", "Arquivadas")]
FILTROS = [("todas", "Todas"), ("nao_lidas", "Não lidas"), ("minhas", "Minhas"), ("agendados", "Agendados"), ("programadas", "Programadas"),
           ("loja", "Esperando cliente"), ("cliente", "Cliente esperando"), ("sem_humano", "Sem humano"), ("sem_conversa", "Sem conversa")]
LIMITE_ARQUIVO = 16 * 1024 * 1024


def _conversas(user, filtro="todas", q="", tag="", aba="todas"):
    msgs = WhatsAppMensagem.objects.filter(lead=OuterRef("pk")).order_by("-criado_em", "-id")
    humano = WhatsAppMensagem.objects.filter(lead=OuterRef("pk"), direcao="out", usuario__isnull=False)
    qs = (leads_visiveis(user).filter(mensagens_wa__isnull=(filtro == "sem_conversa")).distinct()
          .annotate(ultima=Subquery(msgs.values("criado_em")[:1]), ultima_dir=Subquery(msgs.values("direcao")[:1]),
                    ultimo_texto=Subquery(msgs.values("texto")[:1]),
                    ultima_entrada=Subquery(msgs.filter(direcao="in").values("criado_em")[:1]),
                    nao_lidas=Count("mensagens_wa", filter=Q(mensagens_wa__direcao="in", mensagens_wa__lida=False)),
                    tem_humano=Exists(humano)))
    qs = qs.filter(arquivada=True) if aba == "arquivadas" else qs.filter(arquivada=False)
    if aba == "meus":
        qs = qs.filter(vendedor=user)
    elif aba == "portais":
        qs = qs.filter(portal__gt="")
    if filtro == "nao_lidas":
        qs = qs.filter(Q(nao_lidas__gt=0) | Q(marcada_nao_lida=True))
    elif filtro == "minhas":
        qs = qs.filter(vendedor=user)
    elif filtro == "cliente":
        qs = qs.filter(ultima_dir="in")
    elif filtro == "loja":
        qs = qs.filter(ultima_dir="out")
    elif filtro == "programadas":
        qs = qs.filter(programadas__status="pendente")
    elif filtro == "agendados":
        qs = qs.filter(etapa="visita")
    elif filtro == "sem_humano":
        qs = qs.filter(tem_humano=False)
    elif filtro == "sem_conversa":          # leads (ex.: da OLX) que ainda não trocaram nenhuma mensagem
        qs = qs.exclude(etapa__in=funil.fechadas()).exclude(nao_contatar=True)
    if tag.isdigit():
        qs = qs.filter(etiquetas__pk=int(tag))
    q = (q or "").strip()
    if q:
        dig = utils.so_digitos(q)
        cond = Q(nome__icontains=q)
        if dig:
            cond |= Q(telefone__contains=dig)
        qs = qs.filter(cond)
    return qs.prefetch_related("etiquetas").select_related("vendedor").order_by("-fixada", "-ultima")[:200]


def total_nao_lidas(user):
    return (WhatsAppMensagem.objects.filter(direcao="in", lida=False, lead__in=leads_visiveis(user)).values("lead").distinct().count())


def _restante(ultima_entrada, agora=None):
    """Tempo que falta para fechar a janela de 24 h: (texto 'HHhMM', segundos) ou (None, 0)."""
    agora = agora or timezone.now()
    if not ultima_entrada:
        return None, 0
    falta = whatsapp.JANELA - (agora - ultima_entrada)
    seg = int(falta.total_seconds())
    if seg <= 0:
        return None, 0
    return f"{seg // 3600}h{(seg % 3600) // 60:02d}", seg


class ConversasView(Base, TemplateView):
    template_name = "captacao/conversas.html"

    def get_context_data(self, **kw):
        ctx = super().get_context_data(**kw)
        g = self.request.GET
        ctx.update(filtros=FILTROS, filtro=g.get("f", "todas"), abas=ABAS, aba=g.get("aba", "todas"), lead_id=g.get("lead", ""),
                   etiquetas=Etiqueta.objects.all(), tag=g.get("tag", ""), api=whatsapp.configurado())
        return ctx


class ListaView(Base, View):
    def get(self, request):
        g = request.GET
        filtro, aba = g.get("f", "todas"), g.get("aba", "todas")
        agora = timezone.now()
        itens = list(_conversas(request.user, filtro, g.get("q", ""), g.get("tag", ""), aba))
        for l in itens:
            l.janela = bool(l.ultima_entrada and (agora - l.ultima_entrada) < whatsapp.JANELA)
            l.restante, seg = _restante(l.ultima_entrada, agora)
            l.urgente = 0 < seg < 7200
            l.hoje = timezone.localtime(l.ultima).date() == timezone.localdate() if l.ultima else True
            l.cor_etapa = funil.cor(l.etapa)
            l.nome_etapa = funil.nome(l.etapa)
            l.cor_av = AVATARES[l.pk % len(AVATARES)]
        return render(request, "captacao/_conversas_lista.html", {
            "itens": itens, "filtro": filtro, "filtros": FILTROS, "abas": ABAS, "aba": aba, "tag": g.get("tag", ""),
            "ativo": g.get("lead", ""), "q": g.get("q", "")})


AVATARES = ["#ec4899", "#a855f7", "#22c55e", "#3b82f6", "#f59e0b", "#14b8a6", "#ef4444", "#6366f1"]


def _eventos_e_mensagens(lead, msgs):
    """Mensagens + eventos do funil (mudança de etapa, atendente, notas) na ordem do tempo, como no Avise Aí."""
    itens = [{"t": "msg", "m": m, "quando": m.criado_em} for m in msgs]
    if msgs:
        ini = msgs[0].criado_em
        for h in lead.historico.select_related("usuario").filter(criado_em__gte=ini, tipo__in=[Historico.Tipo.ETAPA, Historico.Tipo.NOTA,
                                                                                               Historico.Tipo.CONSULTOR])[:60]:
            if h.tipo == Historico.Tipo.ETAPA:
                txt = f"Etapa: {funil.nome(h.de_etapa)} → {funil.nome(h.para_etapa)}"
            else:
                txt = h.texto
            quem = (h.usuario.first_name or h.usuario.username) if h.usuario else ""
            itens.append({"t": "ev", "texto": txt, "quem": quem, "quando": h.criado_em})
    itens.sort(key=lambda i: i["quando"])
    return itens


def _contexto_conversa(request, lead, erro=""):
    msgs = list(lead.mensagens_wa.select_related("usuario").order_by("-criado_em", "-id")[:80])[::-1]
    fu = followup.ativo_do_lead(lead)
    if fu:
        fu.pos, fu.total = followup.posicao(fu)
    ultima_in = lead.mensagens_wa.filter(direcao="in").order_by("-criado_em").values_list("criado_em", flat=True).first()
    restante, seg = _restante(ultima_in)
    etapa = funil.mapa().get(lead.etapa)
    return {"lead": lead, "itens": _eventos_e_mensagens(lead, msgs), "api": whatsapp.configurado(), "janela": seg > 0,
            "restante": restante, "urgente": 0 < seg < 7200,
            "rapidas": RespostaRapida.objects.all(), "modelos": ModeloWhatsApp.objects.filter(status="APPROVED"),
            "carros": Veiculo.objects.filter(status__in=Veiculo.EM_LOJA, total_fotos__gt=0).order_by("marca", "modelo"),
            "erro": erro, "etiquetas_todas": Etiqueta.objects.all(), "programadas": lead.programadas.filter(status="pendente"), "fu": fu,
            "etapas_lista": funil.todas(), "etapa_atual": etapa, "cor_av": AVATARES[lead.pk % len(AVATARES)],
            "atendentes": get_user_model().objects.filter(is_active=True).order_by("first_name", "username"),
            "gestor": eh_gestor(request.user)}


def _marcar_lidas(lead):
    nao = list(lead.mensagens_wa.filter(direcao="in", lida=False))
    if nao:
        lead.mensagens_wa.filter(direcao="in", lida=False).update(lida=True)
        whatsapp.confirmar_leitura(nao[-1].wamid)
    if lead.marcada_nao_lida:
        lead.marcada_nao_lida = False
        lead.save(update_fields=["marcada_nao_lida"])


class ConversaView(Base, View):
    def get(self, request, pk):
        lead = self.lead(request, pk)
        _marcar_lidas(lead)
        return render(request, "captacao/_conversa.html", _contexto_conversa(request, lead))


class MensagensView(Base, View):
    """Só as bolhas (atualiza sozinha a cada poucos segundos)."""
    def get(self, request, pk):
        lead = self.lead(request, pk)
        _marcar_lidas(lead)
        return render(request, "captacao/_mensagens.html", _contexto_conversa(request, lead))


def _resposta(request, lead, erro=""):
    r = render(request, "captacao/_conversa.html", _contexto_conversa(request, lead, erro))
    return trigger(r, conversasAtualizar=1)


class EnviarView(Base, View):
    def post(self, request, pk):
        lead = self.lead(request, pk)
        texto = (request.POST.get("texto") or "").strip()[:1500]
        if not texto:
            return _resposta(request, lead)
        try:
            whatsapp.enviar_texto(lead, texto, request.user)
            services.marcar_atendimento(lead, request.user)
        except whatsapp.WhatsAppErro as e:
            return _resposta(request, lead, str(e))
        return _resposta(request, lead)


class EnviarArquivoView(Base, View):
    """Anexo (foto, vídeo, áudio ou documento) enviado pelo CRM, dentro da janela de 24 h."""
    def post(self, request, pk):
        lead = self.lead(request, pk)
        arq = request.FILES.get("arquivo")
        if not arq:
            return _resposta(request, lead, "Escolha um arquivo.")
        if arq.size > LIMITE_ARQUIVO:
            return _resposta(request, lead, "O arquivo passa de 16 MB.")
        conteudo, nome, mime = arq.read(), arq.name, arq.content_type or "application/octet-stream"
        try:
            if request.POST.get("gravado"):                 # gravação do microfone: converte para OGG/Opus (mensagem de voz)
                conteudo, nome, mime = audio.para_ogg_opus(conteudo, mime), "audio.ogg", "audio/ogg"
            whatsapp.enviar_arquivo(lead, conteudo, nome, mime, (request.POST.get("legenda") or "")[:900], request.user)
            services.marcar_atendimento(lead, request.user)
        except (whatsapp.WhatsAppErro, audio.ErroAudio) as e:
            return _resposta(request, lead, str(e))
        return _resposta(request, lead)


class ModeloEnvioView(Base, View):
    def post(self, request, pk):
        lead = self.lead(request, pk)
        modelo = ModeloWhatsApp.objects.filter(pk=request.POST.get("modelo", ""), status="APPROVED").first() if request.POST.get("modelo", "").isdigit() else None
        if not modelo:
            return _resposta(request, lead, "Escolha um modelo aprovado.")
        if lead.nao_contatar:
            return _resposta(request, lead, "Este cliente pediu para não receber mensagens.")
        try:
            whatsapp.enviar_modelo(lead, modelo, request.user)
            services.marcar_atendimento(lead, request.user)
        except whatsapp.WhatsAppErro as e:
            return _resposta(request, lead, str(e))
        return _resposta(request, lead)


class CarroEnvioView(Base, View):
    """Manda a foto principal do carro com preço e o link da página (dentro da janela de 24 h)."""
    def post(self, request, pk):
        lead = self.lead(request, pk)
        carro = Veiculo.objects.filter(pk=request.POST.get("carro", "")).first() if request.POST.get("carro", "").isdigit() else None
        if not carro:
            return _resposta(request, lead, "Escolha um carro.")
        capa = fotos.capas([carro.pk]).get(carro.pk)
        link = f"{settings.SITE_URL}{reverse('captacao:captura', args=[carro.pk, utils.slug_veiculo(carro)])}"
        legenda = (f"{carro.marca_fmt} {carro.modelo_fmt} {carro.versao_fmt} {carro.ano}".replace("  ", " ").strip()
                   + f"\n{utils.preco_brl(carro.valor_venda)}\nVeja fotos e simule: {link}")
        try:
            if capa:
                foto_url = f"{settings.SITE_URL}{reverse('captacao:foto', args=[capa.pk, 1200])}"
                whatsapp.enviar_imagem(lead, foto_url, legenda, request.user)
            else:
                whatsapp.enviar_texto(lead, legenda, request.user)
            if not lead.veiculo_id:
                lead.veiculo = carro
                lead.carro_texto = f"{carro.marca_fmt} {carro.modelo_fmt} {carro.ano} · {utils.preco_brl(carro.valor_venda)}"
                lead.save(update_fields=["veiculo", "carro_texto", "atualizado_em"])
            services.marcar_atendimento(lead, request.user)
        except whatsapp.WhatsAppErro as e:
            return _resposta(request, lead, str(e))
        return _resposta(request, lead)


class NotaInternaView(Base, View):
    def post(self, request, pk):
        lead = self.lead(request, pk)
        services.adicionar_nota(lead, request.POST.get("texto", ""), request.user)
        return _resposta(request, lead)


class EtapaView(Base, View):
    """Muda a etapa pela própria conversa (etapas que pedem dados extras abrem o formulário)."""
    def post(self, request, pk):
        lead = self.lead(request, pk)
        etapa = request.POST.get("etapa", "")
        if funil.com_form(etapa):
            return HttpResponse(status=400)
        try:
            services.mudar_etapa(lead, etapa, request.user)
        except services.RegraErro as e:
            return _resposta(request, lead, str(e))
        return _resposta(request, lead)


# ---- barra de ações da conversa -----------------------------------------------------------------
class AcaoConversaView(Base, View):
    """acao = atendente | fixar | nao_lida | finalizar | comentario | pausar_followup | renomear"""
    def post(self, request, pk, acao):
        lead = self.lead(request, pk)
        p = request.POST
        if acao == "atendente":
            novo = get_user_model().objects.filter(pk=p.get("atendente", "")).first() if p.get("atendente", "").isdigit() else None
            if not eh_gestor(request.user) and novo and novo.pk != request.user.pk:
                novo = request.user
            services.trocar_consultor(lead, novo, request.user)
        elif acao == "fixar":
            lead.fixada = not lead.fixada
            lead.save(update_fields=["fixada", "atualizado_em"])
        elif acao == "nao_lida":
            lead.marcada_nao_lida = True
            lead.save(update_fields=["marcada_nao_lida"])
            return HttpResponse(status=204, headers={"HX-Trigger": "conversasAtualizar"})
        elif acao == "finalizar":
            lead.arquivada = not lead.arquivada
            lead.save(update_fields=["arquivada", "atualizado_em"])
            services.adicionar_nota(lead, "Conversa finalizada" if lead.arquivada else "Conversa reaberta", request.user)
        elif acao == "comentario":
            lead.comentario_fixo = (p.get("texto") or "").strip()[:300]
            lead.save(update_fields=["comentario_fixo", "atualizado_em"])
        elif acao == "renomear":
            nome = (p.get("nome") or "").strip()[:120]
            if nome:
                lead.nome = nome
                lead.save(update_fields=["nome", "atualizado_em"])
        elif acao == "pausar_followup":
            fu = lead.followups.filter(status__in=["ativo", "pausado"]).first()
            if fu:
                (followup.retomar if fu.status == "pausado" else followup.pausar)(fu, request.user)
        else:
            return HttpResponse(status=404)
        return _resposta(request, lead)


class SimularView(Base, View):
    """Texto pronto com a simulação de parcela do carro do lead (vai para a caixa de mensagem para revisar e enviar)."""
    def get(self, request, pk):
        lead = self.lead(request, pk)
        v = lead.veiculo
        if not v or not v.valor_venda:
            return JsonResponse({"texto": "", "erro": "Defina o carro de interesse na ficha para simular a parcela."})
        parcela, entrada = utils.parcela_estimada(v.valor_venda, settings.FINANCIAMENTO_ENTRADA_PCT, settings.FINANCIAMENTO_PARCELAS,
                                                  settings.FINANCIAMENTO_TAXA_AM)
        carro = f"{v.marca_fmt} {v.modelo_fmt} {v.ano}"
        texto = (f"Simulação do {carro} ({utils.preco_brl(v.valor_venda)}): entrada de {utils.preco_brl(entrada)} "
                 f"({settings.FINANCIAMENTO_ENTRADA_PCT}%) e {settings.FINANCIAMENTO_PARCELAS}x de {utils.preco_brl(parcela)}*. "
                 "*Valores estimados, sujeitos à análise de crédito. Posso preparar uma simulação com a entrada que você preferir.")
        return JsonResponse({"texto": texto})


class LocalizacaoView(Base, View):
    def get(self, request, pk):
        self.lead(request, pk)
        loja = settings.LOJA
        return JsonResponse({"texto": f"Estamos na {loja.get('endereco', '')}. Como chegar: {loja.get('mapa_url', '')}\n{loja.get('horario', '')}".strip()})


class NovaConversaView(Base, View):
    """Botão '+': abre uma conversa com um contato existente ou cria o contato pelo número."""
    def get(self, request):
        return render(request, "captacao/_nova_conversa.html", {"erro": ""})

    def post(self, request):
        p = request.POST
        tel = utils.normalizar_telefone(p.get("telefone", ""))
        nome = (p.get("nome") or "").strip()
        if not tel:
            return render(request, "captacao/_nova_conversa.html", {"erro": "Informe um telefone válido com DDD.", "dados": p})
        lead = Lead.objects.filter(telefone=tel).order_by("-criado_em").first()
        if not lead:
            if not nome:
                return render(request, "captacao/_nova_conversa.html", {"erro": "Informe o nome do contato.", "dados": p})
            lead = services.criar_lead_manual(nome, tel, None, Lead.Origem.WHATSAPP, request.user)
        r = HttpResponse(status=204)
        r["HX-Redirect"] = f"/conversas/?lead={lead.pk}"
        return r


class MidiaView(Base, View):
    """Mostra a imagem/áudio/documento da conversa (baixado da Meta na hora, só para quem está logado)."""
    def get(self, request, pk):
        m = get_object_or_404(WhatsAppMensagem, pk=pk, lead__in=leads_visiveis(request.user))
        if not m.midia_id:
            raise Http404
        try:
            dados, mime = whatsapp.baixar_midia(m.midia_id)
        except whatsapp.WhatsAppErro as e:
            return HttpResponse(str(e), status=404)
        r = FileResponse(io.BytesIO(dados), content_type=m.midia_tipo or mime)
        r["Cache-Control"] = "private, max-age=3600"
        return r


class BadgeView(Base, View):
    """Número de conversas não lidas no menu (atualiza sozinho)."""
    def get(self, request):
        n = total_nao_lidas(request.user)
        return HttpResponse(f'<b class="pt">{n}</b>' if n else "")
