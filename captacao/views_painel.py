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
        from . import fotos as fotos_mod
        n_fotos, n_videos = fotos_mod.contagem_por_veiculo(), Counter()     # vídeos ficam no ROUTE
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
        ctx.update(v=v, aba=aba, fotos=lista, marcado=v.possui_fotos, videos=videos)
        return ctx


ROTULO_ETAPA = {"view": "Abriu a página", "comecou": "Começou o formulário", "contato": "Deixou nome e WhatsApp",
                "q_pagamento": "Respondeu o pagamento", "q_entrada": "Respondeu a entrada", "q_troca": "Respondeu a troca",
                "q_prazo": "Respondeu o prazo", "enviou": "Enviou o formulário"}
# A pergunta de entrada some para quem paga à vista: fica fora da conta de perdas.
ETAPAS_PRINCIPAIS = ["view", "comecou", "contato", "q_pagamento", "q_troca", "q_prazo", "enviou"]
DICA_VAZAMENTO = {
    "comecou": "A maioria vê a página e nem toca no formulário. Revise o que vem antes dele: foto principal, preço, botões e o quanto o formulário aparece sem rolar.",
    "contato": "Começam a preencher e param antes de deixar o contato. Pedir nome e WhatsApp pesa: reforce o benefício (simulação sem compromisso) e confira o texto do aceite.",
    "q_pagamento": "Deixam o contato e não respondem nenhuma pergunta. São leads incompletos: o SDR deve requalificar pelo WhatsApp (lista abaixo).",
    "q_troca": "Respondem o pagamento e param antes da pergunta de troca. Veja se a pergunta de entrada está confusa ou lenta.",
    "q_prazo": "Param na pergunta de troca. Considere deixá-la opcional ou mais simples.",
    "enviou": "Respondem tudo e não enviam. Confira o botão de enviar e o carregamento da página seguinte.",
}


def _p_valor(n1, c1, n2, c2):
    """Teste z de duas proporções (conversão de A contra B). Devolve (z, p) ou (None, None) se não der para calcular."""
    import math
    if n1 < 1 or n2 < 1:
        return None, None
    p1, p2, p = c1 / n1, c2 / n2, (c1 + c2) / (n1 + n2)
    erro = math.sqrt(p * (1 - p) * (1 / n1 + 1 / n2))
    if erro == 0:
        return None, None
    z = (p1 - p2) / erro
    return z, math.erfc(abs(z) / math.sqrt(2))


def _contar(eventos):
    """{etapa: pessoas}. Com cookies conta visitantes únicos (sid); sem cookies não há como deduplicar: conta visitas."""
    unicos, anonimos = defaultdict(set), Counter()
    for etapa, sid, consent in eventos:
        if consent and sid:
            unicos[etapa].add(sid)
        else:
            anonimos[etapa] += 1
    return {e: len(unicos[e]) + anonimos[e] for e in ROTULO_ETAPA}


def _pct(n, total, casas=1):
    return round(100 * n / total, casas) if total else None


class FunilFormView(LoginRequiredMixin, TemplateView):
    """Dashboard do formulário da página de captura: onde as pessoas desistem, o que fazer e qual variante (A/B) converte mais."""
    template_name = "captacao/funil_form.html"

    def get_context_data(self, **kw):
        from .models import EventoFunil
        ctx = super().get_context_data(**kw)
        req = self.request
        try:
            dias = int(req.GET.get("dias", 30))
        except ValueError:
            dias = 30
        dias = dias if dias in (7, 14, 30, 90) else 30
        try:
            carro = int(req.GET.get("carro") or 0)
        except ValueError:
            carro = 0
        agora = timezone.now()
        desde = agora - timedelta(days=dias)
        antes = desde - timedelta(days=dias)
        base = EventoFunil.objects.all()
        if carro:
            base = base.filter(veiculo_id=carro)
        todos = list(base.filter(criado_em__gte=antes).values_list("etapa", "sid", "consent", "variante", "veiculo_id", "criado_em"))
        atuais = [e for e in todos if e[5] >= desde]
        anteriores = [e for e in todos if e[5] < desde]

        # ---- funil principal
        cont = _contar([(e[0], e[1], e[2]) for e in atuais])
        visitas = cont["view"]
        linhas, anterior = [], None
        for i, etapa in enumerate(ETAPAS_PRINCIPAIS):
            n = cont[etapa]
            perdeu_n = max(0, (anterior or 0) - n) if i else 0
            linhas.append({"chave": etapa, "nome": ROTULO_ETAPA[etapa], "n": n, "pct": _pct(n, visitas),
                           "perdeu_n": perdeu_n, "perdeu_pct": _pct(perdeu_n, anterior) if i and anterior else None,
                           "largura": round(100 * n / visitas, 1) if visitas else 0})
            anterior = n
        elegiveis = [l for l in linhas[1:] if (l["perdeu_n"] or 0) > 0 and visitas >= 20]
        vazamento = None
        if elegiveis:
            pior = max(elegiveis, key=lambda l: l["perdeu_n"])
            de = linhas[linhas.index(pior) - 1]
            vazamento = {"chave": pior["chave"], "de": de["nome"], "para": pior["nome"], "n": pior["perdeu_n"],
                         "pct": pior["perdeu_pct"], "dica": DICA_VAZAMENTO.get(pior["chave"], "")}
            pior["vazamento"] = True
        entrada = {"nome": ROTULO_ETAPA["q_entrada"], "n": cont["q_entrada"], "pct": _pct(cont["q_entrada"], visitas)}

        # ---- período anterior (para as setas de comparação)
        cont_ant = _contar([(e[0], e[1], e[2]) for e in anteriores])
        conv, conv_ant = _pct(cont["enviou"], visitas), _pct(cont_ant["enviou"], cont_ant["view"])
        contato, contato_ant = _pct(cont["contato"], visitas), _pct(cont_ant["contato"], cont_ant["view"])

        def delta(atual, ant, pontos=False):
            if atual is None or ant is None:
                return None
            if pontos:
                return round(atual - ant, 1)
            return round(100 * (atual - ant) / ant, 1) if ant else None

        kpis = {"visitas": visitas, "visitas_d": delta(visitas, cont_ant["view"]),
                "contato": contato, "contato_d": delta(contato, contato_ant, True), "contato_n": cont["contato"],
                "conversao": conv, "conversao_d": delta(conv, conv_ant, True), "enviou": cont["enviou"]}

        # ---- série por dia (duas séries, duas escalas: dois gráficos)
        dia_view, dia_env = Counter(), Counter()
        for etapa, sid, consent, var, vid, criado in atuais:
            d = timezone.localtime(criado).date()
            if etapa == "view":
                dia_view[d] += 1
            elif etapa == "enviou":
                dia_env[d] += 1
        hoje = timezone.localdate()
        dias_lista = [hoje - timedelta(days=i) for i in range(dias - 1, -1, -1)]
        serie = {"rotulos": [d.strftime("%d/%m") for d in dias_lista],
                 "visitas": [dia_view[d] for d in dias_lista], "enviados": [dia_env[d] for d in dias_lista]}

        # ---- teste A/B (só quem aceitou os cookies tem variante)
        ab = {}
        for var in ("A", "B"):
            ev = [(e[0], e[1], e[2]) for e in atuais if e[3] == var and e[2] and e[1]]
            c = _contar(ev)
            ab[var] = {"visitas": c["view"], "contato": c["contato"], "enviou": c["enviou"], "c": c,
                       "taxa_contato": _pct(c["contato"], c["view"]), "taxa": _pct(c["enviou"], c["view"])}
        veredito, tom, p = "", "neutro", None
        a, b = ab["A"], ab["B"]
        if min(a["visitas"], b["visitas"]) < 100:
            veredito = "Amostra pequena (menos de 100 visitas por variante): ainda não dá para concluir qual é melhor."
        else:
            z, p = _p_valor(a["visitas"], a["enviou"], b["visitas"], b["enviou"])
            if p is None:
                veredito = "Sem dados suficientes para comparar."
            elif p < 0.05:
                veredito, tom = f"A variante {'A' if z > 0 else 'B'} converte mais, com diferença estatisticamente confiável (p = {p:.3f}).", "bom"
            else:
                veredito = f"Ainda não há diferença confiável entre A e B (p = {p:.2f}). Continue o teste."
        maior = max(ab["A"]["taxa"] or 0, ab["B"]["taxa"] or 0)       # as barras comparam A com B (a maior ocupa a largura toda)
        for var in ("A", "B"):
            ab[var]["largura"] = round(100 * (ab[var]["taxa"] or 0) / maior, 1) if maior else 0
        for var in ("A", "B"):
            c = ab[var].pop("c")
            ab[var]["retencao"] = [{"nome": ROTULO_ETAPA[e], "pct": _pct(c[e], c["view"])} for e in ETAPAS_PRINCIPAIS]

        # ---- por carro
        por = defaultdict(list)
        for etapa, sid, consent, var, vid, criado in atuais:
            por[vid].append((etapa, sid, consent))
        nomes = {v.pk: v.nome for v in Veiculo.objects.filter(pk__in=[k for k in por if k])}
        incompletos_carro = Counter(Lead.objects.filter(etapa=Lead.Etapa.REQUALIFICAR).values_list("veiculo_id", flat=True))
        carros = []
        for vid, evs in por.items():
            c = _contar(evs)
            carros.append({"nome": nomes.get(vid, f"Carro {vid}"), "pk": vid, "visitas": c["view"], "contato": c["contato"],
                           "enviou": c["enviou"], "conv": _pct(c["enviou"], c["view"]), "incompletos": incompletos_carro.get(vid, 0),
                           "alerta": c["view"] >= 20 and c["enviou"] == 0})
        carros.sort(key=lambda x: -x["visitas"])

        # ---- leads incompletos agora e por anúncio
        incompletos_qs = Lead.objects.filter(etapa=Lead.Etapa.REQUALIFICAR).order_by("criado_em")
        lista = [{"nome": l.nome, "carro": (l.carro_texto or "").split(" · ")[0], "telefone": l.telefone_formatado,
                  "ha": _ha(agora - l.criado_em), "url": l.whatsapp_requalificar_url, "pk": l.pk} for l in incompletos_qs[:8]]
        anuncios = defaultdict(lambda: {"total": 0, "incompletos": 0})
        for utm, resp in Lead.objects.filter(criado_em__gte=desde).exclude(utm_content="").values_list("utm_content", "respostas"):
            anuncios[utm]["total"] += 1
            anuncios[utm]["incompletos"] += 1 if (resp or {}).get("parcial") else 0
        por_anuncio = sorted(({"nome": k, **v, "pct_inc": _pct(v["incompletos"], v["total"], 0)} for k, v in anuncios.items()),
                             key=lambda x: -x["total"])[:8]
        recuperados = Historico.objects.filter(criado_em__gte=desde, texto__startswith="Terminou o formulário").count()

        ctx.update(
            dias=dias, carro=carro, opcoes_dias=[(7, "7 dias"), (14, "14 dias"), (30, "30 dias"), (90, "90 dias")],
            lista_carros=sorted(({"pk": k, "nome": v} for k, v in nomes.items()), key=lambda x: x["nome"]),
            kpis=kpis, linhas=linhas, entrada=entrada, vazamento=vazamento, poucos_dados=visitas < 30, serie=serie,
            ab=ab, veredito=veredito, veredito_tom=tom, carros=carros[:10], incompletos=incompletos_qs.count(),
            lista_incompletos=lista, por_anuncio=por_anuncio, recuperados=recuperados,
            sem_dados=not todos)
        return ctx


def _ha(delta):
    """'há 12 min', 'há 3 h', 'há 2 dias'."""
    seg = int(delta.total_seconds())
    if seg < 3600:
        return f"há {max(1, seg // 60)} min"
    if seg < 86400:
        return f"há {seg // 3600} h"
    d = seg // 86400
    return "há 1 dia" if d == 1 else f"há {d} dias"
