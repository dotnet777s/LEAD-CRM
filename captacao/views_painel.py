"""Painel de resultados: leads, funil, tempo de resposta, rankings, perdas e custos."""
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from decimal import Decimal

from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models import Count
from django.http import HttpResponseForbidden, HttpResponseRedirect
from django.urls import reverse
from django.utils import timezone
from django.views import View
from django.views.generic import TemplateView

from catalogo.models import Veiculo

from .utils import parse_valor
from .models import Historico, Investimento, Lead
from .views import eh_gestor, leads_visiveis

ETAPAS_FUNIL = [("leads", "Leads", None), ("qualificados", "Qualificados", Lead.Etapa.QUALIFICADO),
                ("visitas", "Visitas agendadas", Lead.Etapa.VISITA), ("compareceram", "Compareceram", Lead.Etapa.COMPARECEU),
                ("vendas", "Vendas", Lead.Etapa.VENDIDO)]


def _data(txt):
    try:
        return datetime.strptime(txt or "", "%Y-%m-%d").date()
    except ValueError:
        return None


def intervalo(params):
    hoje = timezone.localdate()
    p = params.get("periodo") or "mes"
    if p == "hoje":
        return p, hoje, hoje
    if p == "7d":
        return p, hoje - timedelta(days=6), hoje
    if p == "30d":
        return p, hoje - timedelta(days=29), hoje
    if p == "custom":
        de, ate = _data(params.get("de")), _data(params.get("ate"))
        if de and ate:
            return p, min(de, ate), max(de, ate)
    return "mes", hoje.replace(day=1), hoje


def etapas_alcancadas(leads):
    """{lead_id: índice mais alto do funil que o lead já alcançou} (usa o histórico, não só a etapa atual)."""
    ordem = {e.value: i for i, e in enumerate(Lead.FUNIL)}
    melhor = {l.pk: ordem.get(l.etapa, 0) for l in leads}
    ids = list(melhor)
    for lid, para in Historico.objects.filter(lead_id__in=ids, tipo=Historico.Tipo.ETAPA).values_list("lead_id", "para_etapa"):
        melhor[lid] = max(melhor[lid], ordem.get(para, 0))
    return melhor


def contar_funil(leads):
    alc = etapas_alcancadas(leads)
    ordem = {e.value: i for i, e in enumerate(Lead.FUNIL)}
    saida = []
    for chave, nome, etapa in ETAPAS_FUNIL:
        minimo = ordem[etapa.value] if etapa else 0
        saida.append({"chave": chave, "nome": nome, "n": sum(1 for v in alc.values() if v >= minimo)})
    anterior = None
    for linha in saida:
        linha["taxa"] = round(100 * linha["n"] / anterior, 1) if anterior else None
        anterior = linha["n"] or None
    topo = saida[0]["n"] or 1
    for linha in saida:
        linha["largura"] = max(4, round(100 * linha["n"] / topo)) if saida[0]["n"] else 0
    return saida, alc


class PainelView(LoginRequiredMixin, TemplateView):
    template_name = "captacao/painel.html"

    def get_context_data(self, **kw):
        ctx = super().get_context_data(**kw)
        g = self.request.GET
        periodo, de, ate = intervalo(g)
        base = leads_visiveis(self.request.user).filter(origem=Lead.Origem.META_LP)
        if g.get("carro", "").isdigit():
            base = base.filter(veiculo_id=int(g["carro"]))
        if g.get("anuncio"):
            base = base.filter(utm_content=g["anuncio"])
        leads = list(base.filter(criado_em__date__gte=de, criado_em__date__lte=ate))

        # leads por dia e classe
        por_dia = defaultdict(Counter)
        for l in leads:
            por_dia[timezone.localtime(l.criado_em).date()][l.classe] += 1
        dias, d = [], de
        while d <= ate and len(dias) < 120:
            c = por_dia.get(d, Counter())
            dias.append({"dia": d, "A": c["A"], "B": c["B"], "C": c["C"], "total": sum(c.values())})
            d += timedelta(days=1)
        maximo = max([x["total"] for x in dias] + [1])
        for x in dias:
            x["h"] = round(100 * x["total"] / maximo)
        por_classe = Counter(l.classe for l in leads)

        funil, alc = contar_funil(leads)
        ordem = {e.value: i for i, e in enumerate(Lead.FUNIL)}

        # tempo de primeira resposta
        resp = [(l.primeira_resposta_em - l.criado_em).total_seconds() for l in leads if l.primeira_resposta_em]
        media_min = round(sum(resp) / len(resp) / 60, 1) if resp else None
        ate5 = sum(1 for s in resp if s <= 300)
        pct5 = round(100 * ate5 / len(leads)) if leads else None

        # rankings
        def ranking(chave_fn, nome_fn):
            grupos = defaultdict(lambda: {"leads": 0, "qualificados": 0, "vendas": 0})
            for l in leads:
                k = chave_fn(l)
                grupos[k]["leads"] += 1
                grupos[k]["qualificados"] += alc[l.pk] >= ordem["qualificado"]
                grupos[k]["vendas"] += l.etapa == Lead.Etapa.VENDIDO
            linhas = [{"nome": nome_fn(k), **v} for k, v in grupos.items()]
            return sorted(linhas, key=lambda x: (-x["vendas"], -x["qualificados"], -x["leads"]))[:10]

        anuncios = ranking(lambda l: l.utm_content or "", lambda k: k or "(sem anúncio)")
        carros = ranking(lambda l: l.carro_texto.split(" · ")[0] or "(sem carro)", lambda k: k)
        perdas = Counter(l.get_motivo_perda_display() for l in leads if l.etapa == Lead.Etapa.PERDIDO)

        # custos: usa o mês de 'ate' (a verba é mensal), com os mesmos filtros de carro/anúncio
        mes = ate.replace(day=1)
        inv = Investimento.objects.filter(mes=mes).first()
        leads_mes = list(base.filter(criado_em__date__gte=mes, criado_em__date__lte=ate))
        alc_mes = etapas_alcancadas(leads_mes)
        n_l, n_q = len(leads_mes), sum(1 for v in alc_mes.values() if v >= ordem["qualificado"])
        n_v = sum(1 for l in leads_mes if l.etapa == Lead.Etapa.VENDIDO)
        custo = lambda n: round(inv.valor / n, 2) if inv and n else None  # noqa: E731

        ctx.update(
            periodo=periodo, de=de, ate=ate, dias=dias, por_classe=por_classe, total=len(leads), funil=funil,
            media_min=media_min, pct5=pct5, anuncios=anuncios, carros=carros, perdas=perdas.most_common(),
            mes=mes, investimento=inv, custo_lead=custo(n_l), custo_qualificado=custo(n_q), custo_venda=custo(n_v),
            n_mes=(n_l, n_q, n_v), gestor=eh_gestor(self.request.user),
            veiculos=Veiculo.objects.filter(pk__in=base.values_list("veiculo_id", flat=True)).order_by("marca", "modelo"),
            lista_anuncios=sorted(set(base.exclude(utm_content="").values_list("utm_content", flat=True))),
            sel_carro=g.get("carro", ""), sel_anuncio=g.get("anuncio", ""),
            periodos=[("hoje", "Hoje"), ("7d", "7 dias"), ("30d", "30 dias"), ("mes", "Este mês"), ("custom", "Datas")],
            receita=sum((l.valor_venda or Decimal(0)) for l in leads if l.etapa == Lead.Etapa.VENDIDO))
        return ctx


class InvestimentoView(LoginRequiredMixin, View):
    def post(self, request):
        if not eh_gestor(request.user):
            return HttpResponseForbidden()
        mes = _data(request.POST.get("mes"))
        valor = parse_valor(request.POST.get("valor", ""))
        if mes and valor is not None and valor >= 0:
            Investimento.objects.update_or_create(mes=mes.replace(day=1), defaults={"valor": valor})
        return HttpResponseRedirect(request.META.get("HTTP_REFERER") or reverse("captacao:painel"))


VIDEO_EXT = (".mp4", ".mov", ".webm")


class CarrosView(LoginRequiredMixin, TemplateView):
    """Páginas de captura do estoque de Olinda: link para copiar/colar no anúncio e leads de cada carro."""
    template_name = "captacao/carros.html"

    def get_context_data(self, **kw):
        from django.conf import settings
        from . import utils
        ctx = super().get_context_data(**kw)
        q = (self.request.GET.get("q") or "").strip().lower()
        from catalogo.models import TakeVeiculo
        n_fotos, n_videos = Counter(), Counter()
        for vid, foto, arq in TakeVeiculo.objects.filter(ativo=True).values_list("veiculo_id", "foto", "arquivo"):
            if foto:
                n_fotos[vid] += 1
            elif arq.lower().endswith(VIDEO_EXT):
                n_videos[vid] += 1
        leads = dict(Lead.objects.filter(veiculo_id__isnull=False).values_list("veiculo_id").annotate(n=Count("id")))
        carros = []
        for v in Veiculo.objects.filter(status__in=Veiculo.EM_LOJA).order_by("marca", "modelo"):
            if q and q not in f"{v.marca} {v.modelo} {v.versao} {v.ano} {v.placa}".lower():
                continue
            caminho = reverse("captacao:captura", args=[v.pk, utils.slug_veiculo(v)])
            carros.append({"v": v, "url": settings.SITE_URL + caminho, "caminho": caminho, "leads": leads.get(v.pk, 0),
                           "fotos": n_fotos[v.pk], "videos": n_videos[v.pk],
                           "preco": utils.preco_brl(v.valor_venda)})
        ctx.update(carros=carros, q=q, utm="utm_source=facebook&utm_medium=paid&utm_campaign={{campaign.name}}&utm_content={{ad.name}}")
        return ctx


class MidiasView(LoginRequiredMixin, TemplateView):
    """Mídias do carro no estoque, fotos e vídeos separados (só leitura; vêm do portal do estoque)."""
    template_name = "captacao/_midias.html"

    def get_context_data(self, pk, **kw):
        from django.shortcuts import get_object_or_404
        from . import fotos as fotos_mod
        ctx = super().get_context_data(**kw)
        v = get_object_or_404(Veiculo, pk=pk)
        lista, videos = fotos_mod.midia_do_veiculo(pk)
        aba = self.request.GET.get("aba", "")
        ctx.update(v=v, aba=aba, fotos=lista, configurado=fotos_mod.configurado(), marcado=v.possui_fotos,
                   videos=[{"take": t, "url": fotos_mod.url_assinada(t.arquivo, 3600)} for t in videos])
        return ctx
