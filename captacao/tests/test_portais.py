"""Leads dos portais por e-mail. Os e-mails abaixo são MODELOS inventados no formato mais comum (rótulo: valor);
quando chegarem e-mails reais de cada portal, acrescentar um teste por portal."""
from email.message import EmailMessage

from django.test import TestCase

from captacao import portais
from captacao.models import Historico, Lead, PortalMensagem

from .helpers import novo_veiculo


def email_de(remetente, assunto, corpo, mid="<a1@x>", html=False):
    m = EmailMessage()
    m["From"], m["Subject"], m["Message-ID"] = remetente, assunto, mid
    m.set_content(corpo, subtype="html" if html else "plain")
    return m


CORPO_OLX = """Você recebeu um contato pelo seu anúncio.

Nome: Carlos Souza
Telefone: (81) 99876-5432
E-mail: carlos@exemplo.com
Anúncio: Fiat Argo 1.0 Drive 2022
Mensagem: Ainda está disponível? Aceita troca?
"""


class PortaisTests(TestCase):
    def test_olx_cria_lead(self):
        r = portais.processar(email_de("OLX <no-reply@olx.com.br>", "Novo contato no anúncio", CORPO_OLX))
        self.assertEqual(r.status, PortalMensagem.Status.CRIADO)
        l = r.lead
        self.assertEqual((l.nome, l.telefone, l.portal, l.origem), ("Carlos Souza", "5581998765432", "olx", Lead.Origem.PORTAL))
        self.assertEqual(l.email, "carlos@exemplo.com")
        self.assertIn("Argo", l.carro_texto)
        self.assertIn("Aceita troca", l.observacao)
        self.assertEqual(l.etapa, Lead.Etapa.NOVO)
        self.assertTrue(Historico.objects.filter(lead=l, texto__icontains="OLX").exists())

    def test_mesmo_email_nao_duplica(self):
        m = email_de("OLX <no-reply@olx.com.br>", "x", CORPO_OLX)
        portais.processar(m)
        portais.processar(m)
        self.assertEqual(Lead.objects.count(), 1)
        self.assertEqual(PortalMensagem.objects.count(), 1)

    def test_telefone_existente_atualiza_e_nao_duplica(self):
        portais.processar(email_de("OLX <no-reply@olx.com.br>", "x", CORPO_OLX, "<1@x>"))
        r = portais.processar(email_de("Napista <leads@napista.com.br>", "Contato", CORPO_OLX.replace("OLX", "Napista"), "<2@x>"))
        self.assertEqual(r.status, PortalMensagem.Status.ATUALIZADO)
        self.assertEqual(Lead.objects.count(), 1)
        self.assertEqual(Historico.objects.filter(lead=r.lead, texto__icontains="Napista").count(), 1)

    def test_sem_telefone_fica_para_revisao(self):
        r = portais.processar(email_de("Mobiauto <x@mobiauto.com.br>", "Contato", "Nome: Ana\nMensagem: oi"))
        self.assertEqual(r.status, PortalMensagem.Status.SEM_TELEFONE)
        self.assertEqual(Lead.objects.count(), 0)

    def test_nao_portal_e_ignorado(self):
        r = portais.processar(email_de("Banco <a@banco.com>", "Fatura", "Telefone: (81) 99876-5432"))
        self.assertEqual(r.status, PortalMensagem.Status.IGNORADO)
        self.assertEqual(Lead.objects.count(), 0)

    def test_encaminhado_identifica_pelo_texto(self):
        r = portais.processar(email_de("Loja <loja@topcarpe.com.br>", "Fwd: Contato Mobiauto", CORPO_OLX))
        self.assertEqual(r.portal, "mobiauto")

    def test_html_sem_texto_simples(self):
        corpo = "<html><body><p>Nome: Bia</p><p>Celular: 81 98888-7777</p><p>Veículo: Onix 2020</p></body></html>"
        r = portais.processar(email_de("Napista <a@napista.com.br>", "Lead", corpo, "<h@x>", html=True))
        self.assertEqual((r.lead.nome, r.lead.telefone), ("Bia", "5581988887777"))

    def test_telefone_sem_rotulo_no_texto(self):
        r = portais.processar(email_de("OLX <a@olx.com.br>", "Contato", "Cliente quer falar. Ligue 81 98888-7777 hoje.", "<t@x>"))
        self.assertEqual(r.lead.telefone, "5581988887777")

    def test_liga_ao_estoque_pela_placa(self):
        v = novo_veiculo(placa="ABC1D23")
        r = portais.processar(email_de("OLX <a@olx.com.br>", "Contato", CORPO_OLX + "Placa: ABC1D23\n", "<p@x>"))
        self.assertEqual(r.lead.veiculo_id, v.pk)

    def test_leitura_desligada_sem_credenciais(self):
        self.assertIsNone(portais.ler_caixa())
