"""Páginas públicas: captura do carro, obrigado (WhatsApp), contato (Meta), fotos e privacidade."""
import re
import uuid
from decimal import Decimal

from django.conf import settings
from django.core.cache import cache
from django.db.models import F, Func
from django.http import FileResponse, Http404, HttpResponse, HttpResponsePermanentRedirect, HttpResponseRedirect, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views import View
from django.views.decorators.cache import cache_control

from catalogo.models import Veiculo

from . import fotos, meta, services, utils
from .forms import CapturaForm
from .models import Lead

CACHE_SEGUNDOS = 60
LIMITE_ENVIOS = 5          # por IP
JANELA_ENVIOS = 10 * 60    # segundos
BOTS = re.compile(r"bot|crawl|spider|slurp|facebookexternalhit|whatsapp|preview|headless|curl|python-requests", re.I)


def ip_do_cliente(request):
    xff = request.META.get("HTTP_X_FORWARDED_FOR", "")
    return (xff.split(",")[0].strip() if xff else request.META.get("REMOTE_ADDR", "")) or ""


def info_requisicao(request, fbclid=""):
    """IP, user agent e cookies de tracking da Meta para o user_data da CAPI."""
    fbclid = fbclid or request.GET.get("fbclid", "")
    return {"ip": ip_do_cliente(request), "ua": request.META.get("HTTP_USER_AGENT", "")[:500],
            "fbp": request.COOKIES.get("_fbp", ""),
            "fbc": utils.montar_fbc(request.COOKIES.get("_fbc", ""), fbclid)}


def _dados_do_veiculo(pk):
    """Consultas pesadas da página, em cache por 60 s (o HTML em si não é cacheado: cada visita tem seu event_id)."""
    chave = f"captacao:veiculo:{pk}"
    dados = cache.get(chave)
    if dados:
        return dados
    veiculo = get_object_or_404(Veiculo, pk=pk)
    disponivel = veiculo.em_loja and veiculo.possui_fotos
    lista, videos = fotos.midia_do_veiculo(pk)
    similares = []
    if not disponivel:
        preco = veiculo.valor_venda
        qs = (Veiculo.objects.filter(status__in=Veiculo.EM_LOJA, total_fotos__gt=0,
                                     valor_venda__gte=preco * Decimal("0.85"), valor_venda__lte=preco * Decimal("1.15"))
              .exclude(pk=pk))
        similares = sorted(qs, key=lambda v: (abs(v.ano - veiculo.ano), abs(v.valor_venda - preco)))[:3]
    capa = fotos.capas([v.pk for v in similares])
    dados = {
        "veiculo": veiculo, "disponivel": disponivel, "fotos": lista, "video": videos[0] if videos else None,
        "similares": [(v, capa.get(v.pk)) for v in similares],
        "itens": list(veiculo.itens_serie or [])[:14], "diferenciais": list(veiculo.opcionais or [])[:5],
    }
    cache.set(chave, dados, CACHE_SEGUNDOS)
    return dados


def _contexto_pagina(request, pk, form=None):
    dados = _dados_do_veiculo(pk)
    v = dados["veiculo"]
    destaque = dados["fotos"][0] if dados["fotos"] else None
    form = form or CapturaForm()
    grupos = []
    for campo, titulo, lista in (("pagamento", "Como pretende pagar?", utils.PAGAMENTO),
                                 ("entrada", "Quanto tem de entrada?", utils.ENTRADA),
                                 ("troca", "Tem carro na troca?", utils.TROCA),
                                 ("prazo", "Quando pretende comprar?", utils.PRAZO)):
        atual = form.data.get(campo) if form.is_bound else None
        grupos.append({"campo": campo, "titulo": titulo, "erros": form.errors.get(campo, []) if form.is_bound else [],
                       "opcoes": [(val, rot, val == atual) for val, rot in lista]})
    similares = []
    for sv, st in dados["similares"]:
        sv.slug, sv.preco_fmt = utils.slug_veiculo(sv), utils.preco_brl(sv.valor_venda)
        similares.append((sv, st))
    return {**dados, "similares": similares, "slug": utils.slug_veiculo(v), "destaque": destaque, "loja": settings.LOJA,
            "destaque_h": fotos.altura_para(destaque, 800) if destaque else 600,
            "galeria": [{"pk": t.pk, "h": fotos.altura_para(t, 480)} for t in dados["fotos"]],
            "pixel_id": settings.META_PIXEL_ID, "site_url": settings.SITE_URL, "form": form, "grupos": grupos,
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
        ctx.update(event_id=event_id, event_id_vc=f"{event_id}-vc", url_pagina=request.build_absolute_uri(request.path),
                   utm={k: request.GET.get(k, "") for k in ("utm_source", "utm_medium", "utm_campaign", "utm_term",
                                                            "utm_content", "fbclid")})
        if not request.user.is_authenticated and not BOTS.search(request.META.get("HTTP_USER_AGENT", "")):
            info = info_requisicao(request)
            url = ctx["url_pagina"]
            meta.enfileirar_evento_web("PageView", event_id, info, url=url)
            meta.enfileirar_evento_web("ViewContent", ctx["event_id_vc"], info, veiculo=v, url=url)
        return render(request, "captacao/captura.html", ctx)

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
            ctx.update(event_id=event_id, event_id_vc=f"{event_id}-vc", url_pagina=request.build_absolute_uri(request.path),
                       utm=_tracking_do_form(request), reabrir_form=True)
            return render(request, "captacao/captura.html", ctx, status=400)

        cache.set(chave_rate, n + 1, JANELA_ENVIOS)
        info = info_requisicao(request, request.POST.get("fbclid", ""))
        tracking = {**_tracking_do_form(request), "ip": ip, "user_agent": info["ua"], "fbp": info["fbp"], "fbc": info["fbc"],
                    "url_origem": request.META.get("HTTP_REFERER", "") or request.build_absolute_uri(request.path),
                    "event_id": event_id}
        lead, _ = services.capturar_lead(veiculo, form.cleaned_data["nome"], form.cleaned_data["telefone"],
                                         form.respostas(), tracking)
        meta.enfileirar_evento_web("Lead", event_id, info, veiculo=veiculo, lead=lead,
                                   url=request.build_absolute_uri(reverse("captacao:captura", args=[pk, utils.slug_veiculo(veiculo)])))
        return redirect("captacao:obrigado", pk=pk, lead_uuid=lead.uuid)


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
        "lead": lead, "veiculo": veiculo, "pixel_id": settings.META_PIXEL_ID, "loja": settings.LOJA,
        "wa_url": utils.link_whatsapp(settings.LOJA["whatsapp"], texto),
        "event_id_lead": lead.event_id_lead, "event_id_contact": _contact_event_id(lead),
        "preco": float(veiculo.valor_venda), "pk": pk})


class ContatoView(View):
    """Beacon do clique no WhatsApp: enfileira o Contact da CAPI com o MESMO event_id do Pixel."""
    def post(self, request, pk, lead_uuid):
        lead = get_object_or_404(Lead, uuid=lead_uuid)
        url = request.build_absolute_uri(reverse("captacao:obrigado", args=[pk, lead.uuid]))
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
