from decimal import Decimal

from django.core.cache import cache
from django.test import TestCase
from django.utils import timezone

from catalogo.models import TakeVeiculo, Veiculo


def novo_veiculo(**kw):
    agora = timezone.now()
    dados = dict(marca="Fiat", modelo="Argo", versao="1.0", ano_modelo=2022, placa="ABC1D23", km=30000,
                 valor_venda=Decimal("59900"), status="loja_olinda", possui_fotos=True,
                 criado_em=agora, atualizado_em=agora, ficha={"itens_serie": ["Ar-condicionado"], "diferenciais": ["Único dono"]})
    dados.update(kw)
    v = Veiculo.objects.create(**dados)
    TakeVeiculo.objects.create(veiculo=v, arquivo="2026/01/a.jpg", foto=True, ativo=True, ordem=0, largura=1600, altura=1200)
    return v


FORM_OK = {"nome": "Maria Silva", "telefone": "(81) 99999-8888", "pagamento": "a_vista", "entrada": "sem",
           "troca": "nao", "prazo": "semana", "lgpd": "1", "utm_content": "anuncio-1", "utm_campaign": "camp"}


class Base(TestCase):
    def setUp(self):
        cache.clear()
