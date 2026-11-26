"""Conversas: a caixa de entrada do WhatsApp dentro do CRM (lista, conversa, resposta, modelos, fotos do carro, notas)."""
import io

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db.models import Count, OuterRef, Q, Subquery
from django.http import FileResponse, Http404, HttpResponse
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.utils import timezone
from django.views import View
from django.views.generic import TemplateView

from catalogo.models import Veiculo

from . import fotos, followup, services, utils, whatsapp
from .models import Historico, Lead, ModeloWhatsApp, RespostaRapida, WhatsAppMensagem
from .views import Base, eh_gestor, leads_visiveis, trigger

FILTROS = [("todas", "Todas"), ("nao_lidas", "Não lidas"), ("minhas", "Minhas"), ("cliente", "Cliente esperando"),
           ("loja", "Esperando cliente")]


def _conversas(user, filtro="todas", q=""):
    msgs = WhatsAppMensagem.objects.filter(lead=OuterRef("pk")).order_by("-criado_em", "-id")
    qs = (leads_visiveis(user).filter(mensagens_wa__isnull=False).distinct()
          .annotate(ultima=Subquery(msgs.values("criado_em")[:1]), ultima_dir=Subquery(msgs.values("direcao")[:1]),
                    ultimo_texto=Subquery(msgs.values("texto")[:1]),
                    nao_lidas=Count("mensagens_wa", filter=Q(mensagens_wa__direcao="in", mensagens_wa__lida=False))))
    if filtro == "nao_lidas":
        qs = qs.filter(nao_lidas__gt=0)
    elif filtro == "minhas":
        qs = qs.filter(vendedor=user)
    elif filtro == "cliente":
        qs = qs.filter(ultima_dir="in")
    elif filtro == "loja":
        qs = qs.filter(ultima_dir="out")
    q = (q or "").strip()
    if q:
        dig = utils.so_digitos(q)
        cond = Q(nome__icontains=q)
        if dig:
            cond |= Q(telefone__contains=dig)
        qs = qs.filter(cond)
    return qs.order_by("-ultima")[:200]


def total_nao_lidas(user):
    return (WhatsAppMensagem.objects.filter(direcao="in", lida=False, lead__in=leads_visiveis(user)).values("lead").distinct().count())


class ConversasView(Base, TemplateView):
    template_name = "captacao/conversas.html"

    def get_context_data(self, **kw):
        ctx = super().get_context_data(**kw)
        ctx.update(filtros=FILTROS, filtro=self.request.GET.get("f", "todas"), lead_id=self.request.GET.get("lead", ""),
                   api=whatsapp.configurado())
        return ctx


class ListaView(Base, View):
    def get(self, request):
        filtro = request.GET.get("f", "todas")
        agora = timezone.now()
        itens = list(_conversas(request.user, filtro, request.GET.get("q", "")))
        for l in itens:
            l.janela = (agora - l.ultima) < whatsapp.JANELA if l.ultima else False
        return render(request, "captacao/_conversas_lista.html", {
            "itens": itens, "filtro": filtro, "filtros": FILTROS, "ativo": request.GET.get("lead", ""), "q": request.GET.get("q", "")})


def _contexto_conversa(request, lead, erro=""):
    msgs = list(lead.mensagens_wa.select_related("usuario").order_by("-criado_em", "-id")[:80])[::-1]
    fu = followup.ativo_do_lead(lead)
    if fu:
        fu.pos, fu.total = followup.posicao(fu)
    return {"lead": lead, "mensagens": msgs, "api": whatsapp.configurado(), "janela": whatsapp.janela_aberta(lead.telefone),
            "rapidas": RespostaRapida.objects.all(), "modelos": ModeloWhatsApp.objects.filter(status="APPROVED"),
            "carros": Veiculo.objects.filter(status__in=Veiculo.EM_LOJA, total_fotos__gt=0).order_by("marca", "modelo"),
            "erro": erro, "fu": fu, "etapas": Lead.Etapa, "funil": [(e.value, e.label) for e in Lead.FUNIL] + [("perdido", "Perdido")],
            "consultores": get_user_model().objects.filter(is_active=True),
            "gestor": eh_gestor(request.user)}


def _marcar_lidas(lead):
    nao = list(lead.mensagens_wa.filter(direcao="in", lida=False))
    if nao:
        lead.mensagens_wa.filter(direcao="in", lida=False).update(lida=True)
        whatsapp.confirmar_leitura(nao[-1].wamid)


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
        ctx = _contexto_conversa(request, lead)
        return render(request, "captacao/_mensagens.html", ctx)


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
    """Muda a etapa pelo painel da conversa (etapas que pedem dados extras abrem a ficha completa)."""
    def post(self, request, pk):
        lead = self.lead(request, pk)
        etapa = request.POST.get("etapa", "")
        if etapa in ("visita", "vendido", "perdido"):
            return HttpResponse(status=400)
        try:
            services.mudar_etapa(lead, etapa, request.user)
        except services.RegraErro as e:
            return _resposta(request, lead, str(e))
        return _resposta(request, lead)


class MidiaView(Base, View):
    """Mostra a imagem/áudio/documento que o cliente mandou (baixado da Meta na hora, só para quem está logado)."""
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
