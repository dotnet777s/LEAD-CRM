"""Central de atendimento: fila de follow-up, configuração, leads dos portais, webhook e envio do WhatsApp (API oficial)."""
import json
from urllib.parse import quote

from django.conf import settings
from django.http import HttpResponse, HttpResponseForbidden, JsonResponse
from django.shortcuts import get_object_or_404, render
from django.utils import timezone
from django.views import View
from django.views.decorators.csrf import csrf_exempt
from django.views.generic import TemplateView

from . import followup, services, whatsapp
from .models import FollowUp, FollowUpConfig, FollowUpPasso, Lead, PortalMensagem
from .views import Base, eh_gestor, leads_visiveis, render_detalhe, trigger

ABAS = [("agora", "Fazer agora"), ("agendados", "Agendados"), ("pausados", "Pausados"), ("esgotados", "Sem resposta"),
        ("responderam", "Responderam")]


def _itens(aba, user, agora):
    visiveis = leads_visiveis(user).values_list("pk", flat=True)
    qs = FollowUp.objects.select_related("lead", "lead__veiculo", "lead__vendedor").filter(lead_id__in=visiveis)
    if aba == "agora":
        lista = [f for f in followup.devidos(agora) if f.lead_id in set(visiveis)]
    elif aba == "agendados":
        lista = list(qs.filter(status="ativo", proximo_em__gt=agora).order_by("proximo_em")[:150])
    elif aba == "pausados":
        lista = list(qs.filter(status="pausado")[:150])
    elif aba == "esgotados":
        lista = list(qs.filter(status="esgotado").order_by("-finalizado_em")[:100])
    else:
        lista = list(qs.filter(status="respondeu").order_by("-finalizado_em")[:100])
    for f in lista:
        f.pos, f.total = followup.posicao(f)
        f.texto = followup.montar_mensagem(f, usuario=user) if f.status in ("ativo", "pausado") else ""
        f.letra = followup.LETRAS[f.variante % 3]
        f.wa = f"https://wa.me/{f.lead.telefone}?text={quote(f.texto)}" if f.texto else f.lead.whatsapp_url
        f.atrasado = bool(f.proximo_em and f.proximo_em < agora)
        f.janela = whatsapp.janela_aberta(f.lead.telefone, agora) if whatsapp.configurado() else False
    return lista


class FollowUpView(Base, TemplateView):
    template_name = "captacao/followup.html"

    def get_context_data(self, **kw):
        ctx = super().get_context_data(**kw)
        aba = self.request.GET.get("aba", "agora")
        ctx.update(aba=aba if aba in dict(ABAS) else "agora", abas=ABAS, contagens=followup.contagens(), gestor=eh_gestor(self.request.user),
                   n_agora=followup.contagens()["agora"])
        return ctx


class FollowUpListaView(Base, View):
    def get(self, request):
        aba = request.GET.get("aba", "agora")
        aba = aba if aba in dict(ABAS) else "agora"
        agora = timezone.now()
        return render(request, "captacao/_followup_lista.html", {
            "itens": _itens(aba, request.user, agora), "aba": aba, "abas": ABAS, "contagens": followup.contagens(agora),
            "api": whatsapp.configurado(), "auto": FollowUpConfig.obter().envio_automatico})


class FollowUpAcaoView(Base, View):
    def post(self, request, pk, acao):
        fu = get_object_or_404(FollowUp.objects.select_related("lead"), pk=pk, lead__in=leads_visiveis(request.user))
        u = request.user
        if acao == "enviei":
            followup.registrar_tentativa(fu, usuario=u)
            services.marcar_atendimento(fu.lead, u)
        elif acao == "enviar_api":
            if not whatsapp.enviar_followup(fu):
                return HttpResponse("Não foi possível enviar pela API: sem conexão, ou conversa fora das 24 h sem template aprovado.",
                                    status=409)
        elif acao == "respondeu":
            followup.cliente_respondeu(fu.lead, usuario=u)
        elif acao == "pausar":
            followup.pausar(fu, u)
        elif acao == "retomar":
            followup.retomar(fu, u)
        elif acao == "pular":
            followup.pular(fu, u)
        elif acao == "perdido":
            try:
                services.mudar_etapa(fu.lead, Lead.Etapa.PERDIDO, u, motivo_perda=Lead.MotivoPerda.SEM_RESPOSTA)
            except services.RegraErro as e:
                return HttpResponse(str(e), status=400)
        else:
            return HttpResponse(status=404)
        return trigger(HttpResponse(status=204), followupAtualizar=1, quadroAtualizar=1)


class LeadFollowUpView(Base, View):
    """No painel do lead: iniciar, parar ou marcar 'respondeu' (acao = iniciar | parar)."""
    def post(self, request, pk, acao):
        lead = self.lead(request, pk)
        if acao == "iniciar":
            followup.iniciar(lead)
        elif acao == "parar":
            followup.cliente_respondeu(lead, usuario=request.user)
        else:
            return HttpResponse(status=404)
        return trigger(render_detalhe(request, lead), quadroAtualizar=1, followupAtualizar=1)


class LeadEnviarView(Base, View):
    """Resposta livre pelo CRM (API oficial, dentro das 24 h)."""
    def post(self, request, pk):
        lead = self.lead(request, pk)
        texto = (request.POST.get("texto") or "").strip()[:1500]
        if texto:
            try:
                whatsapp.enviar_texto(lead, texto, request.user)
                services.marcar_atendimento(lead, request.user)
            except whatsapp.WhatsAppErro as e:
                resp = render_detalhe(request, lead, erro_wa=str(e))
                return resp
        return trigger(render_detalhe(request, lead), quadroAtualizar=1)


# ---- configuração (gestor) ------------------------------------------------------------------------
class FollowUpConfigView(Base, View):
    def _ctx(self, request, salvo=False, erro=""):
        followup.garantir_passos()
        passos = list(FollowUpPasso.objects.all())
        for p in passos:
            v = list(p.variantes) + [""] * 3
            p.v = v[:3]
        return {"cfg": FollowUpConfig.obter(), "passos": passos, "salvo": salvo, "erro": erro, "api": whatsapp.configurado(),
                "desempenho": followup.desempenho(), "contagens": followup.contagens(), "n_agora": followup.contagens()["agora"],
                "webhook": f"{settings.SITE_URL}/whatsapp/webhook/", "tem_verify": bool(settings.WHATSAPP_VERIFY_TOKEN),
                "tem_segredo": bool(settings.WHATSAPP_APP_SECRET)}

    def get(self, request):
        if not eh_gestor(request.user):
            return HttpResponseForbidden()
        return render(request, "captacao/followup_config.html", self._ctx(request))

    def post(self, request):
        if not eh_gestor(request.user):
            return HttpResponseForbidden()
        p = request.POST
        cfg = FollowUpConfig.obter()
        try:
            ini, fim = int(p.get("hora_inicio", 8)), int(p.get("hora_fim", 20))
            if not (0 <= ini < fim <= 24):
                raise ValueError("A hora inicial precisa ser menor que a final.")
            cfg.hora_inicio, cfg.hora_fim = ini, fim
            cfg.ativo, cfg.enviar_domingo = "ativo" in p, "enviar_domingo" in p
            cfg.primeiro_nome, cfg.envio_automatico = "primeiro_nome" in p, "envio_automatico" in p
            for passo in FollowUpPasso.objects.all():
                k = f"p{passo.pk}_"
                passo.ativo = (k + "ativo") in p
                passo.espera_dias = max(0, int(p.get(k + "dias") or 0))
                passo.espera_horas = max(0, int(p.get(k + "horas") or 0))
                h = (p.get(k + "hora") or "").strip()
                passo.hora_envio = int(h) if h else None
                if passo.hora_envio is not None and not 0 <= passo.hora_envio <= 23:
                    raise ValueError("A hora fixa precisa estar entre 0 e 23.")
                vs = [(p.get(f"{k}v{i}") or "").strip()[:900] for i in range(3)]
                passo.variantes = [v for v in vs if v]
                if passo.ativo and not passo.variantes:
                    raise ValueError(f"A tentativa {passo.ordem} precisa de ao menos uma mensagem.")
                passo.template_nome = (p.get(k + "template") or "").strip()[:100]
                passo.save()
            cfg.save()
        except ValueError as e:
            return render(request, "captacao/followup_config.html", self._ctx(request, erro=str(e)))
        return render(request, "captacao/followup_config.html", self._ctx(request, salvo=True))


# ---- leads dos portais ---------------------------------------------------------------------------
class PortaisView(Base, TemplateView):
    template_name = "captacao/portais.html"

    def get_context_data(self, **kw):
        ctx = super().get_context_data(**kw)
        msgs = PortalMensagem.objects.select_related("lead")[:150]
        ctx.update(mensagens=msgs, n_agora=followup.contagens()["agora"],
                   revisar=PortalMensagem.objects.filter(status=PortalMensagem.Status.SEM_TELEFONE).count(),
                   ligado=bool(settings.PORTAIS_IMAP_HOST and settings.PORTAIS_IMAP_USER), ativos=settings.PORTAIS_ATIVOS)
        return ctx


# ---- webhook da API oficial ----------------------------------------------------------------------
@csrf_exempt
def webhook_whatsapp(request):
    if request.method == "GET":                       # verificação ao cadastrar o webhook na Meta
        if (settings.WHATSAPP_VERIFY_TOKEN and request.GET.get("hub.mode") == "subscribe"
                and request.GET.get("hub.verify_token") == settings.WHATSAPP_VERIFY_TOKEN):
            return HttpResponse(request.GET.get("hub.challenge", ""), content_type="text/plain")
        return HttpResponse(status=403)
    if request.method != "POST":
        return HttpResponse(status=405)
    if not whatsapp.assinatura_valida(request.body, request.META.get("HTTP_X_HUB_SIGNATURE_256", "")):
        return HttpResponse(status=403)
    try:
        payload = json.loads(request.body or b"{}")
    except ValueError:
        return HttpResponse(status=400)
    whatsapp.processar_webhook(payload)
    return JsonResponse({"ok": True})
