"""Fotos do estoque (somente leitura) vindas do bucket B2 privado.

A chave no bucket é "<pasta>/<arquivo>" (pasta padrão: takes). As imagens são redimensionadas para WebP
sob demanda (larguras fixas, para ninguém poder pedir tamanhos aleatórios) e guardadas em disco.
"""
import io
import logging
import os
from pathlib import Path

from django.conf import settings

from catalogo.models import TakeVeiculo

log = logging.getLogger(__name__)

LARGURAS = (160, 480, 800, 1200)
PROPORCAO_PADRAO = 4 / 3
_cliente = None


def configurado():
    b = settings.B2
    return bool(b["bucket"] and b["key_id"] and b["app_key"] and b["endpoint"])


def cliente_s3():
    global _cliente
    if _cliente is None and configurado():
        import boto3
        from botocore.config import Config
        os.environ.setdefault("AWS_REQUEST_CHECKSUM_CALCULATION", "when_required")
        os.environ.setdefault("AWS_RESPONSE_CHECKSUM_VALIDATION", "when_required")
        b = settings.B2
        _cliente = boto3.client("s3", endpoint_url=b["endpoint"], aws_access_key_id=b["key_id"],
                                aws_secret_access_key=b["app_key"], region_name=b["region"] or None,
                                config=Config(signature_version="s3v4", s3={"addressing_style": "virtual"}))
    return _cliente


def chave(caminho):
    pasta = getattr(settings, "B2_PASTA_TAKES", "takes")
    return f"{pasta}/{caminho}" if pasta else caminho


def url_assinada(caminho, expira=3600):
    c = cliente_s3()
    if not c or not caminho:
        return ""
    return c.generate_presigned_url("get_object", Params={"Bucket": settings.B2["bucket"], "Key": chave(caminho)},
                                    ExpiresIn=expira)


def pasta_cache():
    p = Path(getattr(settings, "FOTOS_CACHE", settings.BASE_DIR / "media_cache" / "fotos"))
    p.mkdir(parents=True, exist_ok=True)
    return p


def arquivo_webp(take, largura):
    """Caminho do WebP redimensionado (cria se não existir). Levanta FileNotFoundError se não houver origem."""
    if largura not in LARGURAS:
        raise ValueError("largura inválida")
    destino = pasta_cache() / f"{take.pk}-{largura}.webp"
    if destino.exists() and destino.stat().st_size:
        return destino
    c = cliente_s3()
    if not c:
        raise FileNotFoundError("B2 não configurado")
    try:
        bruto = c.get_object(Bucket=settings.B2["bucket"], Key=chave(take.arquivo))["Body"].read()
    except Exception as e:   # noqa: BLE001 - qualquer falha do bucket vira 404 para o visitante
        raise FileNotFoundError(str(e)) from e
    from PIL import Image, ImageOps
    try:
        import pillow_heif
        pillow_heif.register_heif_opener()
    except ImportError:
        pass
    img = ImageOps.exif_transpose(Image.open(io.BytesIO(bruto))).convert("RGB")
    if img.width > largura:
        img = img.resize((largura, round(img.height * largura / img.width)), Image.LANCZOS)
    tmp = destino.with_suffix(".parcial")
    img.save(tmp, "WEBP", quality=78, method=4)
    tmp.replace(destino)
    return destino


def altura_para(take, largura):
    prop = (take.altura / take.largura) if take.largura and take.altura else 1 / PROPORCAO_PADRAO
    return round(largura * prop)


def midia_do_veiculo(veiculo_id):
    """Fotos ativas (por ordem) e o primeiro vídeo, se houver. Só leitura."""
    takes = list(TakeVeiculo.objects.filter(veiculo_id=veiculo_id, ativo=True))
    fotos = [t for t in takes if t.foto]
    videos = [t for t in takes if not t.foto and t.arquivo.lower().endswith((".mp4", ".mov", ".webm"))]
    return fotos, videos


def capas(veiculo_ids):
    """{veiculo_id: primeira foto} em uma consulta."""
    saida = {}
    for t in TakeVeiculo.objects.filter(veiculo_id__in=list(veiculo_ids), ativo=True, foto=True).order_by("ordem", "pk"):
        saida.setdefault(t.veiculo_id, t)
    return saida
