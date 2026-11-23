"""Páginas públicas: captura do carro, obrigado (WhatsApp), contato (Meta), fotos e privacidade."""
import random
import re
import uuid
from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.core.cache import cache
from django.db.models import F, Func
from django.http import FileResponse, Http404, HttpResponse, HttpResponsePermanentRedirect, HttpResponseRedirect, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views import View
from django.views.decorators.cache import cache_control

from catalogo.models import Veiculo

from . import fotos, meta, services, utils
from .forms import CapturaForm, ContatoInicialForm
from .models import EventoFunil, Lead

CACHE_SEGUNDOS = 60
LIMITE_ENVIOS = 5          # por IP
JANELA_ENVIOS = 10 * 60    # segundos
BOTS = re.compile(r"bot|crawl|spider|slurp|facebookexternalhit|whatsapp|preview|headless|curl|python-requests", re.I)


def url_publica(request, caminho=None):
    """URL com o domínio oficial (SITE_URL). O host que o app recebe pode ser o de um repasse (ex.: um IP), e o que vai
    para a Meta como origem do evento precisa ser a página real."""
    return settings.SITE_URL + (caminho if caminho is not None else request.path)


def ip_do_cliente(request):
    """IP do visitante. Atrás da Cloudflare o IP que o servidor enxerga é o dela; o do visitante vem em CF-Connecting-IP
    (o servidor só aceita conexões da Cloudflare, então o cabeçalho não pode ser forjado de fora)."""
    cf = request.META.get("HTTP_CF_CONNECTING_IP", "").strip()
    if cf:
        return cf
    xff = request.META.get("HTTP_X_FORWARDED_FOR", "")
    return (xff.split(",")[0].strip() if xff else request.META.get("REMOTE_ADDR", "")) or ""


COOKIE_CONSENT = "tc_consent"        # "all" = aceitou cookies de estatística e marketing (Pixel, CAPI, Clarity, teste A/B)
ETAPAS_DO_NAVEGADOR = {"comecou", "q_pagamento", "q_entrada", "q_troca", "q_prazo"}


def consentiu(request):
    return request.COOKIES.get(COOKIE_CONSENT) == "all"


def variante_ab(request):
    """Variante A/B do visitante. Só com consentimento (o cookie é dele); sem consentimento, sempre A e sem registro."""
    if not settings.AB_ATIVO or not consentiu(request):
        request._ab_valor = "A"
        return "A"
    v = request.COOKIES.get("tc_ab")
    if v not in settings.AB_VARIANTES:
        v = random.choice(sorted(settings.AB_VARIANTES))
        request._ab_novo = True
    request._ab_valor = v
    return v


def sid_visitante(request):
    """Identificador anônimo da visita (cookie próprio), só com consentimento."""
    if not consentiu(request):
        request._sid_valor = ""
        return ""
    sid = request.COOKIES.get("tc_sid", "")
    if not re.fullmatch(r"[0-9a-f]{32}", sid):
        sid = uuid.uuid4().hex
        request._sid_novo = True
    request._sid_valor = sid
    return sid


def registrar_funil(request, pk, etapa):
    """Grava um passo do formulário. Com consentimento leva identificador e variante; sem, é anônimo (variante A)."""
    if request.user.is_authenticated or BOTS.search(request.META.get("HTTP_USER_AGENT", "")):
        return
    consent = consentiu(request)
    sid = sid_visitante(request)
    if sid and EventoFunil.objects.filter(sid=sid, etapa=etapa, veiculo_id=pk).exists():
        return
    EventoFunil.objects.create(veiculo_id=pk, etapa=etapa, variante=variante_ab(request), sid=sid, consent=consent)


def _gravar_cookies(request, resp):
    if getattr(request, "_ab_novo", False):
        resp.set_cookie("tc_ab", request._ab_valor, max_age=60 * 86400, samesite="Lax")
    if getattr(request, "_sid_novo", False):
        resp.set_cookie("tc_sid", request._sid_valor, max_age=60 * 86400, samesite="Lax")
    return resp


def info_requisicao(request, fbclid=""):
    """IP, user agent e cookies de tracking da Meta para o user_data da CAPI."""
    fbclid = fbclid or request.GET.get("fbclid", "")
    return {"ip": ip_do_cliente(request), "ua": request.META.get("HTTP_USER_AGENT", "")[:500],
            "fbp": request.COOKIES.get("_fbp", ""),
            "fbc": utils.montar_fbc(request.COOKIES.get("_fbc", ""), fbclid)}


# Equipamentos em cartões: (padrão, ícone, categoria). O que não casar vira item simples na lista.
EQUIPAMENTOS = (
    (re.compile(r"airbag|freio|abs|estabilidade|tra[cç][aã]o|cinto|isofix|rebatimento", re.I), "shield", "Segurança"),
    (re.compile(r"multim[ií]dia|tela|carplay|android auto|bluetooth|usb|som|display|painel digital", re.I), "screen", "Tecnologia"),
    (re.compile(r"ar[- ]condicionado|climatizador|ar digital", re.I), "snow", "Conforto"),
    (re.compile(r"chave|alarme|trava|partida|presen[cç]a|keyless", re.I), "key", "Praticidade"),
    (re.compile(r"farol|led|lanterna|neblina|drl", re.I), "light", "Iluminação"),
    (re.compile(r"dire[cç][aã]o|volante|piloto|cruise|sensor|c[aâ]mera|estacionamento|ré", re.I), "wheel", "Condução"),
)
PREFIXOS = re.compile(r"^(adiciona|inclui|incluso|acrescenta|com|c/)\s+", re.I)
MAX_RESTO = 10
NUMEROS = {"um": "1", "uma": "1", "dois": "2", "duas": "2", "três": "3", "tres": "3", "quatro": "4", "cinco": "5", "seis": "6",
           "sete": "7", "oito": "8", "nove": "9", "dez": "10"}


def _chave(t):
    """Texto comparável: minúsculas e números por extenso viram dígitos ('Seis airbags' == '6 airbags')."""
    return " ".join(NUMEROS.get(w, w) for w in t.lower().split())


def _limpo(t):
    t = PREFIXOS.sub("", str(t).strip())
    return (t[:1].upper() + t[1:]) if t else ""


def equipamentos(veiculo):
    """([{icone, texto, categoria}], [texto restante]) a partir dos itens de série e opcionais do ROUTE.
    Um cartão por categoria (o texto mais curto que casar); itens que repetem um cartão ficam de fora da lista."""
    vistos, itens = set(), []
    for t in list(veiculo.opcionais or []) + list(veiculo.itens_serie or []):
        t = _limpo(t)
        if t and t.lower() not in vistos:
            vistos.add(t.lower())
            itens.append(t)
    cartoes = []
    for padrao, icone, cat in EQUIPAMENTOS:
        candidatos = [t for t in itens if padrao.search(t) and len(t) <= 56]
        if candidatos:
            cartoes.append({"icone": icone, "texto": min(candidatos, key=len), "categoria": cat})
    escolhidos = [_chave(c["texto"]) for c in cartoes]
    resto = [t for t in itens if not any(e in _chave(t) or _chave(t) in e for e in escolhidos)]
    return cartoes, resto[:MAX_RESTO]


ETIQUETA_CORTE = re.compile(r"\s+(com|para|de série|em dois|na cor|nativo|embarcado)\b.*$|[,(;:].*$", re.I)


def etiqueta_curta(t):
    """Texto curto para etiqueta: corta no primeiro 'com/para/...' ou pontuação. Vazio se ainda ficar longo demais."""
    c = ETIQUETA_CORTE.sub("", t).strip()
    c = c if len(c) >= 4 else t.strip()
    return c if len(c) <= 32 else ""


def etiquetas(itens):
    saida, vistos = [], set()
    for t in itens:
        c = etiqueta_curta(t)
        if c and _chave(c) not in vistos:
            vistos.add(_chave(c))
            saida.append(c)
    return saida[:12]


def _interesse(pk):
    """Pedidos de simulação REAIS deste carro: (quantidade, janela). 24 h a partir de 2; senão 7 dias a partir de 3.
    Nunca inventado: sem pedidos suficientes, devolve (0, "")."""
    agora = timezone.now()
    base = Lead.objects.filter(veiculo_id=pk)
    n24 = base.filter(criado_em__gte=agora - timedelta(hours=24)).count()
    if n24 >= 2:
        return n24, "nas últimas 24 horas"
    n7 = base.filter(criado_em__gte=agora - timedelta(days=7)).count()
    return (n7, "nos últimos 7 dias") if n7 >= 3 else (0, "")


def _sinais(interesse, janela):
    """Frases da notificação de pulso: só pedidos de simulação REAIS. Sem pedidos suficientes, não há notificação.
    (A loja decidiu não exibir números de vendas na página.)"""
    return [f"{interesse} pessoas pediram simulação deste carro {janela}"] if interesse else []


def _abaixo_da_fipe(v):
    if v.fipe and v.valor_venda and v.fipe > v.valor_venda and (v.fipe - v.valor_venda) / v.fipe >= Decimal("0.01"):
        return utils.preco_brl(v.fipe - v.valor_venda)
    return ""


def _dados_do_veiculo(pk):
    """Consultas pesadas da página, em cache por 60 s (o HTML em si não é cacheado: cada visita tem seu event_id)."""
    chave = f"captacao:veiculo:{pk}"
    dados = cache.get(chave)
    if dados:
        return dados
    veiculo = get_object_or_404(Veiculo, pk=pk)
    disponivel = veiculo.em_loja and veiculo.possui_fotos and veiculo.valor_venda is not None
    lista, videos = fotos.midia_do_veiculo(pk)
    similares = []
    if veiculo.valor_venda is not None:
        preco = veiculo.valor_venda
        qs = (Veiculo.objects.filter(status__in=Veiculo.EM_LOJA, total_fotos__gt=0, valor_venda__isnull=False)
              .exclude(pk=pk))
        # os mais próximos em preço e ano; se não houver parecidos, completa com outros carros da loja
        similares = sorted(qs, key=lambda v: (abs(v.valor_venda - preco) / preco, abs(v.ano - veiculo.ano)))[:4]
    capa = fotos.capas([v.pk for v in similares])
    equip = equipamentos(veiculo)
    parcela, entrada = utils.parcela_estimada(veiculo.valor_venda, settings.FINANCIAMENTO_ENTRADA_PCT,
                                              settings.FINANCIAMENTO_PARCELAS, settings.FINANCIAMENTO_TAXA_AM)
    interesse = _interesse(pk)
    dados = {
        "parcela_fmt": utils.preco_brl(parcela) if parcela else "", "entrada_fmt": utils.preco_brl(entrada) if entrada else "",
        "parcelas_n": settings.FINANCIAMENTO_PARCELAS, "entrada_pct": settings.FINANCIAMENTO_ENTRADA_PCT,
        "veiculo": veiculo, "disponivel": disponivel, "fotos": lista, "video": videos[0] if videos else None,
        "similares": [(v, capa.get(v.pk)) for v in similares],
        "equip": equip[0], "itens_extra": etiquetas(equip[1]), "interesse": interesse[0], "interesse_janela": interesse[1], "sinais": _sinais(interesse[0], interesse[1]), "fipe_abaixo": _abaixo_da_fipe(veiculo),
    }
    cache.set(chave, dados, CACHE_SEGUNDOS)
    return dados


def _contexto_pagina(request, pk, form=None):
    dados = _dados_do_veiculo(pk)
    consent = consentiu(request)
    variante = variante_ab(request)
    sid_visitante(request)
    v = dados["veiculo"]
    destaque = dados["fotos"][0] if dados["fotos"] else None
    form = form or CapturaForm()
    def opcoes(campo, lista):
        atual = form.data.get(campo) if form.is_bound else None
        return [(val, rot, val == atual) for val, rot in lista]

    grupos = []        # as duas perguntas de um toque; troca e entrada são um passo opcional depois do contato
    for campo, titulo, lista in (("pagamento", "Como pretende pagar?", utils.PAGAMENTO),
                                 ("prazo", "Quando pretende comprar?", utils.PRAZO)):
        grupos.append({"campo": campo, "titulo": titulo, "erros": form.errors.get(campo, []) if form.is_bound else [],
                       "opcoes": opcoes(campo, lista)})
    extras = {"troca": opcoes("troca", utils.TROCA), "entrada": opcoes("entrada", utils.ENTRADA)}

    similares = []
    for sv, st in dados["similares"]:
        sv.slug, sv.preco_fmt = utils.slug_veiculo(sv), utils.preco_brl(sv.valor_venda)
        similares.append((sv, st))
    return {**dados, "similares": similares, "slug": utils.slug_veiculo(v), "destaque": destaque, "loja": settings.LOJA,
            "destaque_h": fotos.altura_para(destaque, 800) if destaque else 600,
            "galeria": [{"pk": t.pk, "h": fotos.altura_para(t, 480)} for t in dados["fotos"]],
            "pixel_id": settings.META_PIXEL_ID, "site_url": settings.SITE_URL, "form": form, "grupos": grupos,
            "consent": consent, "variante": variante, "ab": settings.AB_VARIANTES[variante], "extras": extras,
            "clarity_id": settings.CLARITY_ID if consent else "",
            "preco_fmt": utils.preco_brl(v.valor_venda)}


def _tracking_do_form(request):
    g = request.POST
    return {k: g.get(k, "")[:500] for k in ("utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content", "fbclid")}


class CapturaView(View):
    def get(self, request, pk, slug=""):
        ctx = _contexto_pagina(request, pk)
        v = ctx["veiculo"]
        if slug != ctx["slug"]:
            return HttpResponsePermanentRedirect(reverse("captacao:captura", args=[pk, ctx["slug"]]) + _qs(request))
        event_id = uuid.uuid4().hex
        ctx.update(event_id=event_id, event_id_vc=f"{event_id}-vc", url_pagina=url_publica(request),
                   utm={k: request.GET.get(k, "") for k in ("utm_source", "utm_medium", "utm_campaign", "utm_term",
                                                            "utm_content", "fbclid")})
        if not request.user.is_authenticated and not BOTS.search(request.META.get("HTTP_USER_AGENT", "")):
            registrar_funil(request, pk, "view")
            if ctx["consent"]:          # sem consentimento, nada é enviado à Meta (nem pelo servidor)
                info = info_requisicao(request)
                url = ctx["url_pagina"]
                meta.enfileirar_evento_web("PageView", event_id, info, url=url)
                meta.enfileirar_evento_web("ViewContent", ctx["event_id_vc"], info, veiculo=v, url=url)
        return _gravar_cookies(request, render(request, "captacao/captura.html", ctx))

    def post(self, request, pk, slug=""):
        veiculo = get_object_or_404(Veiculo, pk=pk)
        form = CapturaForm(request.POST)
        ip = ip_do_cliente(request)

        if request.POST.get("website"):                       # honeypot: finge sucesso e não grava nada
            return HttpResponseRedirect(reverse("captacao:captura", args=[pk, utils.slug_veiculo(veiculo)]))
        chave_rate = f"captacao:rate:{ip}"
        n = cache.get(chave_rate, 0)
        if n >= LIMITE_ENVIOS:
            return HttpResponse("Muitas tentativas. Tente de novo em alguns minutos.", status=429)

        event_id = request.POST.get("event_id", "")
        if not re.fullmatch(r"[0-9a-f]{32}", event_id):
            event_id = uuid.uuid4().hex

        if not form.is_valid():
            ctx = _contexto_pagina(request, pk, form)
            ctx.update(event_id=event_id, event_id_vc=f"{event_id}-vc", url_pagina=url_publica(request),
                       utm=_tracking_do_form(request), reabrir_form=True)
            return render(request, "captacao/captura.html", ctx, status=400)

        cache.set(chave_rate, n + 1, JANELA_ENVIOS)
        info = info_requisicao(request, request.POST.get("fbclid", ""))
        tracking = {**_tracking_do_form(request), "ip": ip, "user_agent": info["ua"], "fbp": info["fbp"], "fbc": info["fbc"],
                    "url_origem": request.META.get("HTTP_REFERER", "") or url_publica(request),
                    "event_id": event_id, "consent_meta": consentiu(request)}
        respostas = form.respostas()
        respostas["variante"] = variante_ab(request)
        lead, _ = services.capturar_lead(veiculo, form.cleaned_data["nome"], form.cleaned_data["telefone"], respostas, tracking)
        if consentiu(request):
            meta.enfileirar_evento_web("Lead", event_id, info, veiculo=veiculo, lead=lead,
                                       url=url_publica(request, reverse("captacao:captura", args=[pk, utils.slug_veiculo(veiculo)])))
        registrar_funil(request, pk, "enviou")
        return _gravar_cookies(request, redirect("captacao:obrigado", pk=pk, lead_uuid=lead.uuid))


LIMITE_INICIOS = 10       # contatos parciais por IP

class IniciarView(View):
    """1ª etapa: o cliente deixou nome e WhatsApp. Salva o lead (parcial) sem esperar o fim do formulário."""
    def post(self, request, pk):
        veiculo = get_object_or_404(Veiculo, pk=pk)
        form = ContatoInicialForm(request.POST)
        if request.POST.get("website"):                         # honeypot
            return JsonResponse({"ok": True})
        ip = ip_do_cliente(request)
        chave = f"captacao:rate_ini:{ip}"
        n = cache.get(chave, 0)
        if n >= LIMITE_INICIOS:
            return JsonResponse({"ok": False}, status=429)
        if not form.is_valid():
            return JsonResponse({"ok": False, "erros": list(form.errors)}, status=400)
        cache.set(chave, n + 1, JANELA_ENVIOS)
        info = info_requisicao(request, request.POST.get("fbclid", ""))
        tracking = {**_tracking_do_form(request), "ip": ip, "user_agent": info["ua"], "fbp": info["fbp"], "fbc": info["fbc"],
                    "url_origem": request.META.get("HTTP_REFERER", "") or url_publica(request),
                    "event_id": "", "consent_meta": consentiu(request)}
        services.capturar_lead(veiculo, form.cleaned_data["nome"], form.cleaned_data["telefone"],
                               form.respostas_parciais(variante_ab(request)), tracking, parcial=True)
        registrar_funil(request, pk, "contato")
        return _gravar_cookies(request, JsonResponse({"ok": True}))


class EtapaView(View):
    """Passos do questionário (medição do funil). Anônimo sem consentimento."""
    def post(self, request, pk):
        get_object_or_404(Veiculo, pk=pk)
        etapa = request.POST.get("etapa", "")
        if etapa not in ETAPAS_DO_NAVEGADOR:
            return JsonResponse({"ok": False}, status=400)
        chave = f"captacao:rate_etapa:{ip_do_cliente(request)}"
        n = cache.get(chave, 0)
        if n >= 80:
            return JsonResponse({"ok": False}, status=429)
        cache.set(chave, n + 1, JANELA_ENVIOS)
        variante_ab(request), sid_visitante(request)
        registrar_funil(request, pk, etapa)
        return _gravar_cookies(request, JsonResponse({"ok": True}))


class ConsentirView(View):
    """O visitante acabou de aceitar os cookies: agora (e só agora) vão para a fila da Meta o PageView e o ViewContent
    desta visita, com os mesmos ids que o navegador usou."""
    def post(self, request, pk):
        veiculo = get_object_or_404(Veiculo, pk=pk)
        if not consentiu(request) or request.user.is_authenticated or BOTS.search(request.META.get("HTTP_USER_AGENT", "")):
            return JsonResponse({"ok": False}, status=400)
        event_id = request.POST.get("event_id", "")
        if not re.fullmatch(r"[0-9a-f]{32}", event_id):
            return JsonResponse({"ok": False}, status=400)
        info = info_requisicao(request)
        url = request.POST.get("url", "")[:500] or url_publica(request, reverse("captacao:captura", args=[pk, utils.slug_veiculo(veiculo)]))
        meta.enfileirar_evento_web("PageView", event_id, info, url=url)
        meta.enfileirar_evento_web("ViewContent", f"{event_id}-vc", info, veiculo=veiculo, url=url)
        variante_ab(request), sid_visitante(request)
        return _gravar_cookies(request, JsonResponse({"ok": True}))


def _qs(request):
    q = request.META.get("QUERY_STRING", "")
    return f"?{q}" if q else ""


def _contact_event_id(lead):
    return f"{lead.uuid}:contact:{(lead.event_id_lead or 'x')[:8]}"


@cache_control(private=True, no_store=True)
def obrigado(request, pk, lead_uuid):
    lead = get_object_or_404(Lead, uuid=lead_uuid)
    veiculo = lead.veiculo or get_object_or_404(Veiculo, pk=pk)
    texto = utils.mensagem_whatsapp(veiculo, lead.respostas or {}, lead.nome)
    return render(request, "captacao/obrigado.html", {
        "lead": lead, "veiculo": veiculo, "pixel_id": settings.META_PIXEL_ID, "consent": consentiu(request), "loja": settings.LOJA,
        "wa_url": utils.link_whatsapp(settings.LOJA["whatsapp"], texto),
        "event_id_lead": lead.event_id_lead, "event_id_contact": _contact_event_id(lead),
        "preco": float(veiculo.valor_venda or 0), "pk": pk})


class ContatoView(View):
    """Beacon do clique no WhatsApp: enfileira o Contact da CAPI com o MESMO event_id do Pixel."""
    def post(self, request, pk, lead_uuid):
        lead = get_object_or_404(Lead, uuid=lead_uuid)
        if not consentiu(request):
            return JsonResponse({"ok": True})
        url = url_publica(request, reverse("captacao:obrigado", args=[pk, lead.uuid]))
        meta.enfileirar_evento_web("Contact", _contact_event_id(lead), info_requisicao(request),
                                   veiculo=lead.veiculo, lead=lead, url=url)
        return JsonResponse({"ok": True})


def privacidade(request):
    return render(request, "captacao/privacidade.html", {"loja": settings.LOJA})


@cache_control(public=True, max_age=31536000, immutable=True)
def foto(request, pk, largura):
    take = get_object_or_404(fotos.FotoVeiculo, pk=pk)
    try:
        caminho = fotos.arquivo_webp(take, largura)
    except ValueError:
        raise Http404
    except FileNotFoundError:
        raise Http404
    return FileResponse(open(caminho, "rb"), content_type="image/webp")
