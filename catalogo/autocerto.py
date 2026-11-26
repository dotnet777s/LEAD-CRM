"""Sincroniza o estoque com o XML de anúncios do AutoCerto.

- Lê o XML (AUTOCERTO_URL no .env: contém a chave da loja, por isso nunca vai para o Git).
- Cria/atualiza os carros (chave: idveiculo) e troca as fotos por URL; quem sumiu do XML vira "retirado".
- Proteções: XML vazio, malformado ou com menos de metade dos carros de antes NÃO apaga nada (evita derrubar o catálogo
  por um erro momentâneo do AutoCerto).
- Carros que já existiam no ROUTE (mesma placa) mantêm o id público antigo, para os links dos anúncios continuarem valendo."""
import logging
import re
import xml.etree.ElementTree as ET
from decimal import Decimal, InvalidOperation

import requests
from django.conf import settings
from django.db import connection, transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from .models import FotoVeiculo, Veiculo

log = logging.getLogger(__name__)


class ErroEstoque(Exception):
    pass


def baixar_xml(url=None, timeout=45):
    url = url or settings.AUTOCERTO_URL
    if not url:
        raise ErroEstoque("Defina AUTOCERTO_URL no .env.")
    try:
        r = requests.get(url, timeout=timeout, headers={"User-Agent": "TopCarCRM/1.0"})
        r.raise_for_status()
    except requests.RequestException as e:
        raise ErroEstoque(f"Não foi possível ler o XML do AutoCerto: {e.__class__.__name__}") from e
    return r.content


def _t(el, tag):
    x = el.find(tag)
    return (x.text or "").strip() if x is not None and x.text else ""


def _int(txt):
    d = re.sub(r"\D", "", txt or "")
    return int(d) if d else None


def _preco(el):
    bruto = _t(el, "precodecimal") or re.sub(r"[^\d,]", "", _t(el, "preco")).replace(",", ".")
    try:
        v = Decimal(bruto)
    except (InvalidOperation, ValueError):
        return None
    return v if v > 0 else None


def _data(txt):
    from datetime import datetime
    for fmt in ("%d/%m/%Y %H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            return timezone.make_aware(datetime.strptime(txt, fmt))
        except ValueError:
            continue
    return None


def interpretar(conteudo):
    """Lista de dicts (um por veículo) a partir do XML. Levanta ErroEstoque se o XML não for o esperado."""
    try:
        raiz = ET.fromstring(conteudo)
    except ET.ParseError as e:
        raise ErroEstoque("O XML do AutoCerto veio malformado.") from e
    if raiz.tag != "estoque":
        raise ErroEstoque("O XML do AutoCerto não tem o formato esperado (<estoque>).")
    carros = []
    for v in raiz.findall("veiculo"):
        rid = _t(v, "idveiculo")
        if not rid:
            continue
        ano = re.findall(r"\d{4}", _t(v, "anomodelo"))
        fotos = []
        for f in v.findall("fotos/foto"):
            url = _t(f, "url")
            if url.startswith("https://") or url.startswith("http://"):
                fotos.append((_int(_t(f, "ordem")) or len(fotos) + 1, url))
        carros.append({
            "rid": rid[:20], "placa": re.sub(r"[^A-Za-z0-9]", "", _t(v, "placa")).upper()[:10], "marca": _t(v, "marca")[:60],
            "modelo": _t(v, "modelo")[:80], "versao": _t(v, "versao")[:120], "ano_modelo": int(ano[-1]) if ano else 0,
            "km": _int(_t(v, "quilometragem")), "valor_venda": _preco(v), "cor": _t(v, "cor")[:30], "cambio": _t(v, "cambio")[:30],
            "combustivel": _t(v, "combustivel")[:30], "portas": _int(_t(v, "numeroportas")), "zero_km": _t(v, "zerokm").upper() == "S",
            "opcionais": [(o.text or "").strip() for o in v.findall("opcionais/opcional") if (o.text or "").strip()],
            "observacoes": _t(v, "observacoes"), "video_url": _t(v, "linkVideo")[:300],
            "atualizado_em": _data(_t(v, "DataAtualizacao")), "cadastrado_em": _data(_t(v, "dataCadastro")),
            "fotos": sorted(fotos),
        })
    return carros


def ids_antigos_do_route():
    """{placa: id público antigo} lido das tabelas do ROUTE (só leitura). Vazio se não existirem (SQLite, testes, etc.)."""
    if connection.vendor != "postgresql":
        return {}
    try:
        with connection.cursor() as c:
            c.execute("SELECT to_regclass('public.veiculo')")
            if not c.fetchone()[0]:
                return {}
            c.execute("SELECT topcrm_id, placa FROM public.veiculo WHERE placa IS NOT NULL")
            return {re.sub(r"[^A-Za-z0-9]", "", p or "").upper(): i for i, p in c.fetchall() if p}
    except Exception:  # noqa: BLE001 - sem acesso ao ROUTE: segue sem preservar ids
        log.warning("estoque: não consegui ler os ids antigos do ROUTE")
        return {}


def _token_foto(url):
    m = re.search(r"u=(\d+)", url)
    return (m.group(1)[-6:] if m else str(abs(hash(url)))[:6])


@transaction.atomic
def sincronizar(conteudo=None, ids_antigos=None, agora=None, limpar=True):
    """Aplica o XML ao banco. Devolve {'novos', 'atualizados', 'retirados', 'total'}."""
    agora = agora or timezone.now()
    carros = interpretar(conteudo if conteudo is not None else baixar_xml())
    if not carros:
        raise ErroEstoque("O XML não trouxe nenhum veículo: o estoque não foi alterado.")
    ativos_antes = Veiculo.objects.filter(status=Veiculo.DISPONIVEL).count()
    if ativos_antes >= 10 and len(carros) < ativos_antes / 2:
        raise ErroEstoque(f"O XML trouxe só {len(carros)} carros (eram {ativos_antes}): não alterei o estoque. Confira no AutoCerto.")
    # As tabelas do ROUTE só são consultadas na PRIMEIRA carga (para manter os ids dos links de anúncios). Depois disso o CRM
    # não lê mais nada do estoque do ROUTE: tudo vem do AutoCerto.
    antigos = ids_antigos if ids_antigos is not None else (ids_antigos_do_route() if not Veiculo.objects.exists() else {})
    existentes = {v.rid: v for v in Veiculo.objects.all()}
    proximo_id = max([0, 1000] + list(Veiculo.objects.values_list("id", flat=True)) + list(antigos.values())) + 1
    novos = atualizados = 0
    vistos = set()
    for d in carros:
        fotos = d.pop("fotos")
        vistos.add(d["rid"])
        d.update(status=Veiculo.DISPONIVEL, total_fotos=len(fotos), sincronizado_em=agora)
        v = existentes.get(d["rid"])
        if v:
            for k, val in d.items():
                setattr(v, k, val)
            v.save()
            atualizados += 1
        else:
            pk = antigos.get(d["placa"]) if d["placa"] else None
            if pk is None or Veiculo.objects.filter(pk=pk).exists():
                pk, proximo_id = proximo_id, proximo_id + 1
            v = Veiculo.objects.create(id=pk, **d)
            novos += 1
        FotoVeiculo.objects.filter(veiculo_rid=v.rid).delete()
        FotoVeiculo.objects.bulk_create([
            FotoVeiculo(id=f"{v.rid}-{ordem}-{_token_foto(url)}"[:24], veiculo_rid=v.rid, ordem=ordem, capa=(i == 0), url=url)
            for i, (ordem, url) in enumerate(fotos)])
    retirados = Veiculo.objects.filter(status=Veiculo.DISPONIVEL).exclude(rid__in=vistos).update(status=Veiculo.RETIRADO, sincronizado_em=agora)
    if limpar:
        _limpar_referencias()
    return {"novos": novos, "atualizados": atualizados, "retirados": retirados, "total": len(carros)}


def _limpar_referencias():
    """Leads que apontavam para um carro que não existe mais no estoque ficam sem carro (o texto do carro é mantido)."""
    from captacao.models import Lead
    ids = set(Veiculo.objects.values_list("id", flat=True))
    Lead.objects.filter(veiculo__isnull=False).exclude(veiculo_id__in=ids).update(veiculo=None)
    Lead.objects.filter(veiculo_vendido__isnull=False).exclude(veiculo_vendido_id__in=ids).update(veiculo_vendido=None)


def aquecer_capas(limite=80):
    """Baixa e guarda em cache as capas (480 e 800 px) para o catálogo abrir rápido. Devolve quantas capas ficaram prontas."""
    from captacao import fotos as fotos_mod
    n = 0
    for f in FotoVeiculo.objects.filter(capa=True, veiculo_rid__in=Veiculo.objects.filter(status=Veiculo.DISPONIVEL).values("rid"))[:limite]:
        try:
            for w in (480, 800):
                fotos_mod.arquivo_webp(f, w)
            n += 1
        except (FileNotFoundError, ValueError):
            continue
    return n
