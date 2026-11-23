from decimal import Decimal

from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from catalogo.models import Veiculo
from captacao.models import Lead


def novo_veiculo(**kw):
    dados = dict(rid="pfund0001", marca="Fiat", modelo="Argo", versao="1.0", ano_modelo=2022, placa="ABC1D23", km=30000,
                 valor_venda=Decimal("59900"), status="disponivel", unidade="Olinda", total_fotos=1,
                 atualizado_em=timezone.now())
    dados.update(kw)
    return Veiculo.objects.create(**dados)


class FundacaoTests(TestCase):
    def test_tabelas_do_estoque_sao_nao_gerenciadas(self):
        self.assertEqual(Veiculo._meta.db_table, "veiculo")
        # nos testes as tabelas existem (settings_test); em producao a migration e no-op
        self.assertTrue(settings.CATALOGO_CRIAR_TABELAS)

    def test_lead_liga_ao_veiculo_e_calcula_propriedades(self):
        v = novo_veiculo()
        lead = Lead.objects.create(nome="Maria", telefone="5581999998888", veiculo=v)
        self.assertEqual(lead.telefone_formatado, "(81) 99999-8888")
        self.assertEqual(lead.whatsapp_url, "https://wa.me/5581999998888")
        self.assertEqual(lead.proxima_etapa, Lead.Etapa.ATENDIMENTO)
        self.assertTrue(lead.de_anuncio)
        self.assertEqual(v.leads_crm.count(), 1)

    def test_lead_manual_nao_e_de_anuncio(self):
        lead = Lead.objects.create(nome="Jo", telefone="5581911112222", origem=Lead.Origem.LOJA)
        self.assertFalse(lead.de_anuncio)

    def test_ano_normalizado(self):
        self.assertEqual(Veiculo(ano_modelo=27).ano, 2027)
        self.assertEqual(Veiculo(ano_modelo=1819).ano, 2019)
        self.assertEqual(Veiculo(ano_modelo=2022).ano, 2022)


class AcessoTests(TestCase):
    def test_crm_exige_login(self):
        r = self.client.get("/")
        self.assertEqual(r.status_code, 302)
        self.assertIn("/entrar/", r["Location"])

    def test_login_e_privacidade_sao_publicos(self):
        self.assertEqual(self.client.get("/entrar/").status_code, 200)
        # /v/ e /privacidade/ ainda nao tem rota (Fase 3), mas NAO podem redirecionar para o login
        self.assertEqual(self.client.get("/privacidade/").status_code, 200)
        self.assertEqual(self.client.get("/v/1-x/").status_code, 404)

    def test_logado_ve_o_crm(self):
        u = get_user_model().objects.create_user("vend", password="x12345678")
        self.client.force_login(u)
        self.assertEqual(self.client.get("/").status_code, 200)
