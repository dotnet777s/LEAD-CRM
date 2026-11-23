from decimal import Decimal

from django.core.cache import cache
from django.test import TestCase
from django.utils import timezone

from catalogo.models import FotoVeiculo, Veiculo

_seq = iter(range(1, 10**6))


def novo_veiculo(**kw):
    agora = timezone.now()
    n = next(_seq)
    dados = dict(rid=f"p{n:08d}", marca="Fiat", modelo="Argo", versao="1.0", ano_modelo=2022, placa="ABC1D23", km=30000,
                 valor_venda=Decimal("59900"), status="disponivel", unidade="Olinda", total_fotos=1,
                 atualizado_em=agora, itens_serie=["Ar-condicionado"], opcionais=["Único dono"])
    kw = dict(kw)
    fotos = kw.pop("fotos", 1)
    dados.update(kw)
    dados.setdefault("total_fotos", fotos)
    dados["total_fotos"] = kw.get("total_fotos", fotos)
    v = Veiculo.objects.create(**dados)
    for i in range(fotos):
        FotoVeiculo.objects.create(id=f"f{n:08d}{i}", veiculo_rid=v.rid, ordem=i, capa=(i == 0), largura=1600, altura=1200)
    return v


FORM_OK = {"nome": "Maria Silva", "telefone": "(81) 99999-8888", "pagamento": "a_vista", "entrada": "sem",
           "troca": "nao", "prazo": "semana", "lgpd": "1", "utm_content": "anuncio-1", "utm_campaign": "camp"}


class Base(TestCase):
    def setUp(self):
        cache.clear()
