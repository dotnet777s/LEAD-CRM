"""Funil e etiquetas (configuração), etiquetas no lead, mensagens programadas e visão geral."""
from collections import Counter
from datetime import timedelta

from django.db.models import Count
from django.http import HttpResponse, HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.views import View
from django.views.generic import TemplateView

from . import campanhas, classificacao, services, utils, whatsapp
from .models import Etiqueta, Lead, MensagemProgramada, RegraClassificacao, WhatsAppMensagem
from .views import Base, eh_gestor, leads_visiveis, render_detalhe, trigger
from .views_conversas import _resposta

COR_PADRAO = "#2ee66b"


# ---------------------------------------------------------------- etiquetas no lead
class EtiquetaLeadView(Base, View):
    """POST: acao = add | remove, etiqueta = id. `volta` = conversa (padrão) ou detalhe (ficha)."""
    def post(self, request, pk, acao):
        lead = self.lead(request, pk)
        e = Etiqueta.objects.filter(pk=request.POST.get("etiqueta", "")).first() if request.POST.get("etiqueta", "").isdigit() else None
        if e and acao == "add":
            lead.etiquetas.add(e)
            campanhas.ao_etiquetar(lead, e)
        elif e and acao == "remove":
            lead.etiquetas.remove(e)
        elif acao not in ("add", "remove"):
            return HttpResponse(status=404)
        if request.POST.get("volta") == "detalhe":
            return trigger(render_detalhe(request, lead), quadroAtualizar=1)
        return _resposta(request, lead)


# ---------------------------------------------------------------- mensagens programadas
class ProgramarView(Base, View):
    def post(self, request, pk):
        lead = self.lead(request, pk)
        texto = (request.POST.get("texto") or "").strip()[:1500]
        quando = parse_datetime(request.POST.get("quando", ""))
        if not texto or not quando:
            return _resposta(request, lead, "Informe o texto e o dia/hora da mensagem programada.")
        quando = timezone.make_aware(quando) if timezone.is_naive(quando) else quando
        if quando <= timezone.now():
            return _resposta(request, lead, "Escolha um horário no futuro.")
        MensagemProgramada.objects.create(lead=lead, texto=texto, enviar_em=quando, criada_por=request.user)
        services.adicionar_nota(lead, f"Mensagem programada para {timezone.localtime(quando):%d/%m %H:%M}: {texto[:120]}", request.user)
        return _resposta(request, lead)


class ProgramadaCancelarView(Base, View):
    def post(self, request, pk):
        p = get_object_or_404(MensagemProgramada, pk=pk, lead__in=leads_visiveis(request.user), status="pendente")
        p.status = "cancelada"
        p.save(update_fields=["status"])
        return _resposta(request, p.lead)


# ---------------------------------------------------------------- configurar funil e etiquetas
class FunilConfigView(Base, TemplateView):
    template_name = "captacao/funil_config.html"

    def get_context_data(self, **kw):
        classificacao.garantir_padroes()
        ctx = super().get_context_data(**kw)
        ctx.update(etiquetas=Etiqueta.objects.annotate(n=Count("leads")), regras=RegraClassificacao.objects.select_related("etiqueta"),
                   etapas=Lead.Etapa.choices, motivos=Lead.MotivoPerda.choices, gestor=eh_gestor(self.request.user),
                   msg=self.request.GET.get("msg", ""), etapas_resumo=_resumo_etapas(self.request.user))
        return ctx


def _resumo_etapas(user):
    contagem = dict(leads_visiveis(user).values_list("etapa").annotate(n=Count("id")))
    total = sum(contagem.values()) or 1
    return [{"nome": rotulo, "n": contagem.get(v, 0), "pct": round(100 * contagem.get(v, 0) / total)} for v, rotulo in Lead.Etapa.choices]


class FunilAcaoView(Base, View):
    def post(self, request, acao, pk=0):
        if not eh_gestor(request.user):
            return HttpResponseForbidden()
        p = request.POST
        if acao == "etiqueta":
            nome = (p.get("nome") or "").strip()[:40]
            cor = p.get("cor") if (p.get("cor") or "").startswith("#") and len(p.get("cor")) == 7 else COR_PADRAO
            if nome and not Etiqueta.objects.filter(nome__iexact=nome).exists():
                Etiqueta.objects.create(nome=nome, cor=cor, bloqueia_followup="bloqueia" in p, ordem=Etiqueta.objects.count())
        elif acao == "etiqueta_excluir":
            Etiqueta.objects.filter(pk=pk).delete()
        elif acao == "etiqueta_editar":
            e = get_object_or_404(Etiqueta, pk=pk)
            e.bloqueia_followup = "bloqueia" in p
            if (p.get("cor") or "").startswith("#") and len(p.get("cor")) == 7:
                e.cor = p["cor"]
            e.save()
        elif acao == "regra":
            nome, palavras = (p.get("nome") or "").strip()[:60], (p.get("palavras") or "").strip()
            etapa = p.get("etapa") if p.get("etapa") in Lead.Etapa.values else ""
            motivo = p.get("motivo_perda") if p.get("motivo_perda") in Lead.MotivoPerda.values else ""
            if etapa == Lead.Etapa.PERDIDO and not motivo:
                return redirect("/funil/configurar/?msg=Para levar a Perdido, escolha o motivo.")
            if nome and palavras:
                RegraClassificacao.objects.create(
                    nome=nome, palavras=palavras, etapa=etapa, motivo_perda=motivo if etapa == Lead.Etapa.PERDIDO else "",
                    etiqueta=Etiqueta.objects.filter(pk=p.get("etiqueta", "")).first() if p.get("etiqueta", "").isdigit() else None,
                    ordem=RegraClassificacao.objects.count())
        elif acao == "regra_excluir":
            RegraClassificacao.objects.filter(pk=pk).delete()
        elif acao == "regra_ativar":
            r = get_object_or_404(RegraClassificacao, pk=pk)
            r.ativa = not r.ativa
            r.save(update_fields=["ativa"])
        else:
            return HttpResponse(status=404)
        return redirect("/funil/configurar/?msg=Salvo")


# ---------------------------------------------------------------- visão geral
UF_POR_DDD = {
    **{d: "SP" for d in (11, 12, 13, 14, 15, 16, 17, 18, 19)}, **{d: "RJ" for d in (21, 22, 24)}, **{d: "ES" for d in (27, 28)},
    **{d: "MG" for d in (31, 32, 33, 34, 35, 37, 38)}, **{d: "PR" for d in (41, 42, 43, 44, 45, 46)}, **{d: "SC" for d in (47, 48, 49)},
    **{d: "RS" for d in (51, 53, 54, 55)}, **{d: "DF" for d in (61,)}, **{d: "GO" for d in (62, 64)}, 63: "TO", **{d: "MT" for d in (65, 66)},
    67: "MS", **{d: "AC" for d in (68,)}, 69: "RO", **{d: "BA" for d in (71, 73, 74, 75, 77)}, 79: "SE", **{d: "PE" for d in (81, 87)},
    **{d: "AL" for d in (82,)}, **{d: "PB" for d in (83,)}, **{d: "RN" for d in (84,)}, **{d: "CE" for d in (85, 88)}, **{d: "PI" for d in (86, 89)},
    **{d: "PA" for d in (91, 93, 94)}, 92: "AM", 97: "AM", 95: "RR", 96: "AP", **{d: "MA" for d in (98, 99)},
}
DIAS = ["Seg", "Ter", "Qua", "Qui", "Sex", "Sáb", "Dom"]


class VisaoGeralView(Base, TemplateView):
    template_name = "captacao/visao_geral.html"

    def get_context_data(self, **kw):
        ctx = super().get_context_data(**kw)
        try:
            dias = max(1, min(int(self.request.GET.get("dias", 30)), 365))
        except ValueError:
            dias = 30
        agora = timezone.now()
        ini = agora - timedelta(days=dias)
        leads = list(leads_visiveis(self.request.user).filter(criado_em__gte=ini).prefetch_related("etiquetas"))
        total = len(leads)
        etapas = Counter(l.etapa for l in leads)
        funil = [{"nome": rotulo, "n": etapas.get(v, 0), "pct": round(100 * etapas.get(v, 0) / total) if total else 0}
                 for v, rotulo in Lead.Etapa.choices]
        ufs = Counter(UF_POR_DDD.get(int(l.telefone[2:4]), "?") for l in leads if l.telefone[:2] == "55" and l.telefone[2:4].isdigit())
        origem = Counter((l.get_portal_display() if l.portal else l.get_origem_display()) for l in leads)
        dia_sem = Counter(timezone.localtime(l.criado_em).weekday() for l in leads)
        hora = Counter(timezone.localtime(l.criado_em).hour for l in leads)
        carros = Counter(l.carro_texto.split(" · ")[0] for l in leads if l.carro_texto)
        etiquetas = Counter(e.nome for l in leads for e in l.etiquetas.all())
        ids = [l.pk for l in leads]
        com_humano = set(WhatsAppMensagem.objects.filter(lead_id__in=ids, direcao="out").values_list("lead_id", flat=True)) | {
            l.pk for l in leads if l.primeira_resposta_em}
        sem_resposta = sum(1 for l in leads if l.pk not in com_humano)
        tempos = [(l.primeira_resposta_em - l.criado_em).total_seconds() / 60 for l in leads if l.primeira_resposta_em]
        ctx.update(
            dias=dias, total=total, funil=funil, sem_humano=sem_resposta, vendidos=etapas.get("vendido", 0), perdidos=etapas.get("perdido", 0),
            por_dia=round(total / dias, 1), tempo_medio=round(sum(tempos) / len(tempos)) if tempos else None,
            ufs=_barras(ufs.most_common(10)), origens=_barras(origem.most_common(8)), carros=_barras(carros.most_common(10)),
            etiquetas=_barras(etiquetas.most_common(10)), semana=_barras([(DIAS[i], dia_sem.get(i, 0)) for i in range(7)]),
            horas=_barras([(f"{h:02d}h", hora.get(h, 0)) for h in range(24)]), pico_hora=max(hora, key=hora.get) if hora else None)
        return ctx


def _barras(pares):
    m = max((n for _, n in pares), default=0) or 1
    return [{"nome": k, "n": n, "pct": max(2, round(100 * n / m)) if n else 0} for k, n in pares]
