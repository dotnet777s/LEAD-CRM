"""CRM interno: Kanban, detalhe do lead, cadastro rápido e eventos Meta."""
import json
from datetime import timedelta

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models import Count, Q
from django.http import HttpResponse, HttpResponseForbidden
from django.shortcuts import get_object_or_404, render
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.views import View
from django.views.generic import TemplateView

from catalogo.models import Veiculo

from . import followup, services, utils, whatsapp
from .forms import LeadManualForm
from .models import EventoMeta, Lead

COM_FORM = (Lead.Etapa.VISITA, Lead.Etapa.VENDIDO, Lead.Etapa.PERDIDO)   # etapas que pedem dados extras
LIMITE_COLUNA = 60
DIAS_FECHADOS = 30


def eh_gestor(user):
    return user.is_superuser or user.is_staff or user.groups.filter(name="Gestor").exists()


def leads_visiveis(user):
    qs = Lead.objects.select_related("veiculo", "vendedor")
    if eh_gestor(user):
        return qs
    return qs.filter(Q(vendedor=user) | Q(vendedor__isnull=True))   # consultor: os seus + os ainda sem dono


def trigger(resposta, **eventos):
    resposta["HX-Trigger"] = json.dumps({k: True for k in eventos} if eventos else {})
    return resposta


def aplicar_filtros(qs, params):
    if params.get("consultor") == "sem":
        qs = qs.filter(vendedor__isnull=True)
    elif params.get("consultor", "").isdigit():
        qs = qs.filter(vendedor_id=int(params["consultor"]))
    if params.get("classe") in ("A", "B", "C"):
        qs = qs.filter(classe=params["classe"])
    if params.get("carro", "").isdigit():
        qs = qs.filter(veiculo_id=int(params["carro"]))
    if params.get("origem") in Lead.Origem.values:
        qs = qs.filter(origem=params["origem"])
    hoje = timezone.localdate()
    ini = {"hoje": hoje, "7d": hoje - timedelta(days=6), "mes": hoje.replace(day=1)}.get(params.get("periodo"))
    if ini:
        qs = qs.filter(criado_em__date__gte=ini)
    q = (params.get("q") or "").strip()
    if q:
        digitos = utils.so_digitos(q)
        cond = Q(nome__icontains=q)
        if digitos:
            cond |= Q(telefone__contains=digitos)
        qs = qs.filter(cond)
    return qs


def preparar_card(lead, agora):
    seg = int((agora - lead.criado_em).total_seconds())
    lead.criado_ts = int(lead.criado_em.timestamp())
    lead.tempo_txt = (f"{seg // 60} min" if seg < 3600 else f"{seg // 3600} h" if seg < 86400 else f"{seg // 86400} d")
    lead.sla = ("ok" if seg < 300 else "warn" if seg < 1800 else "late") if lead.etapa == Lead.Etapa.NOVO else ""
    prox = lead.proxima_etapa
    lead.prox = Lead.Etapa(prox) if prox else None
    lead.prox_form = prox in COM_FORM if prox else False
    lead.retorno_atrasado = bool(lead.proximo_contato and lead.proximo_contato < agora)
    return lead


def mensagem_inicial(lead, usuario):
    primeiro = lead.nome.split()[0] if lead.nome else ""
    carro = f" sobre o {lead.veiculo.marca} {lead.veiculo.modelo} {lead.veiculo.ano}" if lead.veiculo else ""
    quem = usuario.first_name or usuario.get_username()
    return f"Olá {primeiro}! Aqui é {quem} da TopCar. Vi seu interesse{carro}. Posso te ajudar?"


class Base(LoginRequiredMixin):
    def lead(self, request, pk):
        return get_object_or_404(leads_visiveis(request.user), pk=pk)


# ---- quadro -----------------------------------------------------------------------------------
class KanbanView(Base, TemplateView):
    template_name = "captacao/kanban.html"

    def get_context_data(self, **kw):
        ctx = super().get_context_data(**kw)
        U = get_user_model()
        ctx.update(
            consultores=U.objects.filter(is_active=True).order_by("first_name", "username"),
            carros=Veiculo.objects.filter(status__in=Veiculo.EM_LOJA).order_by("marca", "modelo"),
            n_agora=followup.contagens()["agora"], origens=Lead.Origem.choices, gestor=eh_gestor(self.request.user), periodos=[("", "Tudo"), ("hoje", "Hoje"), ("7d", "7 dias"), ("mes", "Mês")])
        return ctx


class QuadroView(Base, View):
    def get(self, request):
        agora = timezone.now()
        base = aplicar_filtros(leads_visiveis(request.user), request.GET)
        corte = agora - timedelta(days=DIAS_FECHADOS)
        fus = {f.lead_id: f for f in followup.FollowUp.objects.filter(status__in=["ativo", "pausado"])}
        colunas = []
        for etapa in Lead.ABERTAS + [Lead.Etapa.VENDIDO, Lead.Etapa.PERDIDO]:
            qs = base.filter(etapa=etapa)
            if etapa in (Lead.Etapa.VENDIDO, Lead.Etapa.PERDIDO):
                qs = qs.filter(etapa_desde__gte=corte)
            total = qs.count()
            ordem = "criado_em" if etapa == Lead.Etapa.NOVO else "-etapa_desde"
            cards = [preparar_card(l, agora) for l in qs.order_by(ordem)[:LIMITE_COLUNA]]
            for l in cards:
                l.fu = fus.get(l.pk)
                if l.fu:
                    l.fu_devido = bool(l.fu.status == "ativo" and l.fu.proximo_em and l.fu.proximo_em <= agora)
                    l.fu_pos = followup.posicao(l.fu)
            colunas.append({"etapa": etapa.value, "nome": etapa.label, "total": total, "cards": cards})
        ultimo_novo = base.filter(etapa=Lead.Etapa.NOVO).order_by("-id").values_list("id", flat=True).first() or 0
        return render(request, "captacao/_quadro.html", {
            "colunas": colunas, "ultimo_novo": ultimo_novo, "com_form": [e.value for e in COM_FORM]})


# ---- detalhe e ações --------------------------------------------------------------------------
def render_detalhe(request, lead, erro_wa=""):
    agora = timezone.now()
    preparar_card(lead, agora)
    fu = followup.ativo_do_lead(lead)
    if fu:
        fu.pos, fu.total = followup.posicao(fu)
        fu.texto = followup.montar_mensagem(fu, usuario=request.user)
    return render(request, "captacao/_detalhe.html", {
        "lead": lead, "gestor": eh_gestor(request.user), "etapas": Lead.Etapa,
        "etapas_funil": [(e.value, e.label) for e in Lead.Etapa],
        "historico": lead.historico.select_related("usuario")[:40],
        "eventos": lead.eventos_meta.all()[:10], "interacoes": lead.interacoes.all()[:5],
        "resumo": services.resumo_respostas(lead.respostas) if lead.respostas else "",
        "mensagem": mensagem_inicial(lead, request.user),
        "consultores": get_user_model().objects.filter(is_active=True).order_by("first_name", "username"),
        "com_form": [e.value for e in COM_FORM],
        "fu": fu, "ultimo_fu": lead.followups.exclude(status__in=["ativo", "pausado"]).order_by("-id").first(),
        "conversa": list(lead.mensagens_wa.order_by("-criado_em")[:20])[::-1], "wa_ligado": whatsapp.configurado(),
        "wa_janela": whatsapp.configurado() and whatsapp.janela_aberta(lead.telefone), "erro_wa": erro_wa})


class DetalheView(Base, View):
    def get(self, request, pk):
        return render_detalhe(request, self.lead(request, pk))


class NotaView(Base, View):
    def post(self, request, pk):
        lead = self.lead(request, pk)
        services.adicionar_nota(lead, request.POST.get("texto", ""), request.user)
        return render_detalhe(request, lead)


class ConsultorView(Base, View):
    def post(self, request, pk):
        lead = self.lead(request, pk)
        if not eh_gestor(request.user) and lead.vendedor_id not in (None, request.user.pk):
            return HttpResponseForbidden()
        alvo = request.POST.get("vendedor", "")
        if not eh_gestor(request.user):                  # consultor só "assume" o lead para si
            alvo = str(request.user.pk)
        novo = get_user_model().objects.filter(pk=alvo).first() if alvo.isdigit() else None
        services.trocar_consultor(lead, novo, request.user)
        return trigger(render_detalhe(request, lead), quadroAtualizar=1)


class RetornoView(Base, View):
    def post(self, request, pk):
        lead = self.lead(request, pk)
        bruto = request.POST.get("proximo_contato", "")
        dt = parse_datetime(bruto) if bruto else None
        lead.proximo_contato = timezone.make_aware(dt) if dt and timezone.is_naive(dt) else dt
        lead.save(update_fields=["proximo_contato", "atualizado_em"])
        return trigger(render_detalhe(request, lead), quadroAtualizar=1)


class WhatsAppView(Base, View):
    """Clique em 'WhatsApp': tira o lead de Novo (marca a primeira resposta) e abre a conversa no cliente."""
    def post(self, request, pk):
        lead = self.lead(request, pk)
        services.marcar_atendimento(lead, request.user)
        return trigger(HttpResponse(status=204), quadroAtualizar=1)


class MoverView(Base, View):
    def _form(self, request, lead, etapa, erro=""):
        carros = list(Veiculo.objects.filter(Q(status__in=Veiculo.EM_LOJA) | Q(pk=lead.veiculo_id)
                                             | Q(status="vendido", data_venda__gte=timezone.localdate() - timedelta(days=60)))
                      .order_by("marca", "modelo"))
        ini = timezone.localtime(lead.visita_em) if lead.visita_em else timezone.localtime() + timedelta(days=1)
        r = render(request, "captacao/_mover.html", {
            "lead": lead, "etapa": etapa, "etapa_nome": Lead.Etapa(etapa).label, "erro": erro, "carros": carros,
            "motivos": Lead.MotivoPerda.choices, "visita_sugerida": ini.strftime("%Y-%m-%dT%H:%M"),
            "valor_sugerido": (lead.veiculo.valor_venda if lead.veiculo else "")})
        return r

    def get(self, request, pk):
        etapa = request.GET.get("etapa", "")
        if etapa not in [e.value for e in COM_FORM]:
            return HttpResponse(status=400)
        return self._form(request, self.lead(request, pk), etapa)

    def post(self, request, pk):
        lead = self.lead(request, pk)
        etapa = request.POST.get("etapa", "")
        try:
            kw = {}
            if etapa == Lead.Etapa.PERDIDO:
                kw["motivo_perda"] = request.POST.get("motivo", "")
            elif etapa == Lead.Etapa.VENDIDO:
                kw["valor_venda"] = utils.parse_valor(request.POST.get("valor", ""))
                if kw["valor_venda"] is None:
                    raise services.RegraErro("Valor da venda inválido.")
                vid = request.POST.get("veiculo_vendido", "")
                kw["veiculo_vendido"] = Veiculo.objects.filter(pk=vid).first() if vid.isdigit() else None
            elif etapa == Lead.Etapa.VISITA:
                dt = parse_datetime(request.POST.get("visita_em", ""))
                if not dt:
                    raise services.RegraErro("Informe o dia e a hora da visita.")
                kw["visita_em"] = timezone.make_aware(dt) if timezone.is_naive(dt) else dt
            services.mudar_etapa(lead, etapa, request.user, **kw)
        except services.RegraErro as e:
            return self._form(request, lead, etapa, str(e))
        r = HttpResponse(status=204)
        return trigger(r, quadroAtualizar=1, fecharDlg=1)


class MoverDiretoView(Base, View):
    """Etapas sem dados extras (arrastar ou 'Avançar')."""
    def post(self, request, pk):
        lead = self.lead(request, pk)
        etapa = request.POST.get("etapa", "")
        if etapa in [e.value for e in COM_FORM]:
            return HttpResponse(status=400)
        try:
            services.mudar_etapa(lead, etapa, request.user)
        except services.RegraErro as e:
            return HttpResponse(str(e), status=400)
        return trigger(HttpResponse(status=204), quadroAtualizar=1)


class NovoLeadView(Base, View):
    def get(self, request):
        return render(request, "captacao/_novo.html", {
            "origens": [c for c in Lead.Origem.choices if c[0] != Lead.Origem.META_LP],
            "carros": Veiculo.objects.filter(status__in=Veiculo.EM_LOJA).order_by("marca", "modelo"), "erros": {}})

    def post(self, request):
        f = LeadManualForm(request.POST)
        if not f.is_valid():
            r = render(request, "captacao/_novo.html", {
                "origens": [c for c in Lead.Origem.choices if c[0] != Lead.Origem.META_LP], "erros": f.errors,
                "carros": Veiculo.objects.filter(status__in=Veiculo.EM_LOJA).order_by("marca", "modelo"), "dados": request.POST})
            return r
        d = f.cleaned_data
        veiculo = Veiculo.objects.filter(pk=d["veiculo"]).first() if d["veiculo"] else None
        services.criar_lead_manual(d["nome"], d["telefone"], veiculo, d["origem"], request.user, d["observacao"])
        return trigger(HttpResponse(status=204), quadroAtualizar=1, fecharDlg=1)


class ExcluirView(Base, View):
    """LGPD: exclusão dos dados do lead (somente gestor)."""
    def post(self, request, pk):
        if not eh_gestor(request.user):
            return HttpResponseForbidden()
        services.excluir_dados_do_lead(self.lead(request, pk))
        return trigger(HttpResponse(status=204), quadroAtualizar=1, fecharDlg=1)


# ---- eventos Meta -----------------------------------------------------------------------------
class EventosMetaView(Base, TemplateView):
    template_name = "captacao/eventos.html"

    def get_context_data(self, **kw):
        ctx = super().get_context_data(**kw)
        ctx["contagem"] = dict(EventoMeta.objects.values_list("status").annotate(n=Count("id")))
        ctx["eventos"] = EventoMeta.objects.select_related("lead")[:150]
        ctx["capi_ligada"] = settings.META_CAPI_ENABLED and bool(settings.META_PIXEL_ID and settings.META_CAPI_ACCESS_TOKEN)
        ctx["gestor"] = eh_gestor(self.request.user)
        return ctx


class ReenviarEventoView(Base, View):
    def post(self, request, pk):
        if not eh_gestor(request.user):
            return HttpResponseForbidden()
        ev = get_object_or_404(EventoMeta, pk=pk)
        if ev.status in (EventoMeta.Status.ERRO, EventoMeta.Status.NAO_ENVIADO):
            ev.status, ev.tentativas, ev.proxima_tentativa_em = EventoMeta.Status.PENDENTE, 0, timezone.now()
            ev.save(update_fields=["status", "tentativas", "proxima_tentativa_em"])
        return render(request, "captacao/_evento_linha.html", {"e": ev, "gestor": True})
