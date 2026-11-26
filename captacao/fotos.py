"""Fotos do estoque: vêm do AutoCerto por URL (tabela estoque_foto) e ficam em cache em disco.

As imagens são redimensionadas para WebP sob demanda (larguras fixas, para ninguém poder pedir tamanhos
aleatórios) e guardadas em disco. Se o ROUTE passar a usar um bucket e preencher `url`, a imagem é baixada de lá.
"""
import io
import logging
from pathlib import Path

import requests
from django.conf import settings

from catalogo.models import FotoArquivo, FotoVeiculo

log = logging.getLogger(__name__)

LARGURAS = (160, 480, 800, 1200)
PROPORCAO_PADRAO = 4 / 3


def pasta_cache():
    p = Path(getattr(settings, "FOTOS_CACHE", settings.BASE_DIR / "media_cache" / "fotos"))
    p.mkdir(parents=True, exist_ok=True)
    return p


def _bytes_originais(foto):
    arq = FotoArquivo.objects.filter(pk=foto.pk).values_list("jpeg", flat=True).first()
    if arq:
        return bytes(arq)
    if foto.url and foto.url.startswith(("http://", "https://")):
        try:
            r = requests.get(foto.url, timeout=15)
            r.raise_for_status()
            return r.content
        except requests.RequestException as e:
            raise FileNotFoundError(str(e)) from e
    raise FileNotFoundError("foto sem arquivo")


def arquivo_webp(foto, largura):
    """Caminho do WebP redimensionado (cria se não existir). Levanta FileNotFoundError se não houver origem."""
    if largura not in LARGURAS:
        raise ValueError("largura inválida")
    destino = pasta_cache() / f"{foto.pk}-{largura}.webp"
    if destino.exists() and destino.stat().st_size:
        return destino
    bruto = _bytes_originais(foto)
    from PIL import Image, ImageOps
    try:
        img = ImageOps.exif_transpose(Image.open(io.BytesIO(bruto))).convert("RGB")
    except Exception as e:   # noqa: BLE001 - arquivo corrompido vira 404
        raise FileNotFoundError(str(e)) from e
    if img.width > largura:
        img = img.resize((largura, round(img.height * largura / img.width)), Image.LANCZOS)
    tmp = destino.with_suffix(".parcial")
    img.save(tmp, "WEBP", quality=78, method=4)
    tmp.replace(destino)
    return destino


def altura_para(foto, largura):
    prop = (foto.altura / foto.largura) if foto.largura and foto.altura else 1 / PROPORCAO_PADRAO
    return round(largura * prop)


def _rid(veiculo_id):
    from catalogo.models import Veiculo
    return Veiculo.objects.filter(pk=veiculo_id).values_list("rid", flat=True).first()


def midia_do_veiculo(veiculo_id):
    """(fotos por ordem, vídeos). Os vídeos ficam na máquina do ROUTE e ainda não chegam ao CRM."""
    rid = _rid(veiculo_id)
    fotos = list(FotoVeiculo.objects.filter(veiculo_rid=rid)) if rid else []
    return sorted(fotos, key=lambda f: (not f.capa, f.ordem, f.pk)), []


def capas(veiculo_ids):
    """{veiculo_id: foto de capa} em poucas consultas."""
    from catalogo.models import Veiculo
    rid_para_id = dict(Veiculo.objects.filter(pk__in=list(veiculo_ids)).values_list("rid", "pk"))
    saida = {}
    for f in FotoVeiculo.objects.filter(veiculo_rid__in=list(rid_para_id)).order_by("-capa", "ordem", "id"):
        saida.setdefault(rid_para_id[f.veiculo_rid], f)
    return saida


def contagem_por_veiculo():
    """{veiculo_id: n_fotos} (uma consulta)."""
    from collections import Counter
    from catalogo.models import Veiculo
    rid_para_id = dict(Veiculo.objects.values_list("rid", "pk"))
    c = Counter()
    for rid in FotoVeiculo.objects.values_list("veiculo_rid", flat=True):
        if rid in rid_para_id:
            c[rid_para_id[rid]] += 1
    return c
