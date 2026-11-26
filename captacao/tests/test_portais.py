"""Leads da OLX por e-mail. Os três formatos abaixo seguem e-mails reais da OLX (nomes, telefones e ids trocados)."""
from email.message import EmailMessage

from django.test import TestCase, override_settings

from captacao import portais
from captacao.models import Historico, Lead, PortalMensagem

from .helpers import novo_veiculo

OLX = "OLX <noreply@olx.com.br>"

# 1) cliente clicou no WhatsApp do anúncio
WHATSAPP = """Contato recebido!
Oi, Topcar Veiculos Olinda! Chegou um cliente para você

Veja abaixo informações de um cliente que acessou WhatsApp da sua loja.

Dados do contato

Nome: willa
E-mail: willa@exemplo.com
Telefone: 81999990056
Anúncio:

BYD Dolphin Mini (Elétrico) 2026

R$ 119990,00

Lembre-se: quanto mais rápido você retornar, maiores chances de sucesso na sua venda!

Identificador do lead: 7b7a534e-b627-444c-b319-8cd5177b7ea6
"""

# 2) cliente procura um carro parecido
BUSCA = """Oba! Um cliente está procurando um carro como o seu!
Oi, Topcar Veiculos Olinda
Você recebeu uma mensagem da OLX:

 Cliente busca um PEUGEOT 2008 0km ou semi novo, 2022, com valor R$ 56499, características semelhantes a um anúncio em seu estoque. Entre em contato!
Nome: Antonio Marcos da Silva
Email: antonio@exemplo.com
Telefone: 94981704401

Peugeot 2008 Allure 1.6 Flex 16V 5P Aut. 2022

R$ 66990,00

Identificador do lead: 174153c0-d9dd-48ce-a5d5-4edeb35efee3
"""

# 3) interessado que aceitou compartilhar o contato
INTERESSADO = """Tem cliente interessado em um dos seus anúncios!
Oi, Topcar Veiculos Olinda!

Você recebeu um novo interessado e o cliente aceitou compartilhar as informações de contato com você:

BYD Dolphin Mini (Elétrico) 2026

R$ 119990,00

Nome: Bruno Felipe
Email: bruno@exemplo.com
Telefone: 81971086187
Identificador do lead: 7d97e2b2-4326-41d6-bde9-dbc940f19734
"""


def email_de(remetente, assunto, corpo, mid="<a1@x>", html=False):
    m = EmailMessage()
    m["From"], m["Subject"], m["Message-ID"] = remetente, assunto, mid
    m.set_content(corpo, subtype="html" if html else "plain")
    return m


class OlxTests(TestCase):
    def test_clicou_no_whatsapp(self):
        v = novo_veiculo(marca="BYD", modelo="Dolphin Mini", ano_modelo=2026, valor_venda=119990)
        r = portais.processar(email_de(OLX, "Tem cliente interessado em um de seus anúncios!", WHATSAPP))
        self.assertEqual(r.status, PortalMensagem.Status.CRIADO)
        l = r.lead
        self.assertEqual((l.nome, l.telefone, l.portal, l.origem), ("Willa", "5581999990056", "olx", Lead.Origem.PORTAL))
        self.assertEqual(l.email, "willa@exemplo.com")
        self.assertEqual(l.veiculo_id, v.pk)              # ligado ao estoque pelo preço + modelo
        self.assertEqual(l.etapa, Lead.Etapa.NOVO)
        self.assertEqual(r.identificador, "7b7a534e-b627-444c-b319-8cd5177b7ea6")
        h = Historico.objects.get(lead=l)
        self.assertIn("OLX", h.texto)
        self.assertIn("clicou no WhatsApp", h.texto)

    def test_sem_carro_igual_no_estoque_guarda_o_titulo(self):
        r = portais.processar(email_de(OLX, "x", INTERESSADO))
        self.assertIsNone(r.lead.veiculo_id)
        self.assertIn("BYD Dolphin Mini (Elétrico) 2026", r.lead.carro_texto)

    def test_cliente_busca_carro_parecido(self):
        r = portais.processar(email_de(OLX, "Oba! Um cliente está procurando um carro como o seu!", BUSCA))
        l = r.lead
        self.assertEqual((l.nome, l.telefone, l.email), ("Antonio Marcos da Silva", "5594981704401", "antonio@exemplo.com"))
        self.assertIn("Peugeot 2008 Allure", l.carro_texto)
        self.assertIn("Cliente busca um PEUGEOT 2008", l.observacao)
        self.assertIn("parecido", Historico.objects.get(lead=l).texto)

    def test_interessado_compartilhou_contato(self):
        l = portais.processar(email_de(OLX, "x", INTERESSADO)).lead
        self.assertEqual((l.nome, l.telefone), ("Bruno Felipe", "5581971086187"))

    def test_mesmo_email_nao_duplica(self):
        m = email_de(OLX, "x", WHATSAPP)
        portais.processar(m)
        portais.processar(m)
        self.assertEqual((Lead.objects.count(), PortalMensagem.objects.count()), (1, 1))

    def test_mesmo_identificador_em_outro_email_nao_duplica(self):
        portais.processar(email_de(OLX, "x", WHATSAPP, "<1@x>"))
        r = portais.processar(email_de(OLX, "x", WHATSAPP, "<2@x>"))
        self.assertEqual(r.status, PortalMensagem.Status.IGNORADO)
        self.assertEqual(Lead.objects.count(), 1)

    def test_telefone_existente_nao_duplica_o_card(self):
        portais.processar(email_de(OLX, "x", WHATSAPP, "<1@x>"))
        r = portais.processar(email_de(OLX, "x", WHATSAPP.replace("7b7a534e", "00000000"), "<2@x>"))
        self.assertEqual(r.status, PortalMensagem.Status.ATUALIZADO)
        self.assertEqual(Lead.objects.count(), 1)
        self.assertEqual(Historico.objects.filter(lead=r.lead, texto__icontains="Novo contato").count(), 1)

    def test_sem_telefone_fica_para_revisao(self):
        r = portais.processar(email_de(OLX, "x", "Nome: Ana\nEmail: a@b.com\n\nFiat Argo 2022\n\nR$ 59990,00\n"))
        self.assertEqual(r.status, PortalMensagem.Status.SEM_TELEFONE)
        self.assertEqual(Lead.objects.count(), 0)

    def test_outros_portais_so_registram_por_enquanto(self):
        for remetente in ("Napista <leads@napista.com.br>", "Mobiauto <x@mobiauto.com.br>", "Banco <a@banco.com>"):
            r = portais.processar(email_de(remetente, "Contato", WHATSAPP, f"<{remetente[:3]}@x>"))
            self.assertEqual(r.status, PortalMensagem.Status.IGNORADO)
        self.assertEqual(Lead.objects.count(), 0)

    @override_settings(PORTAIS_ATIVOS=("olx", "napista"))
    def test_portal_pode_ser_ligado_depois(self):
        r = portais.processar(email_de("Napista <leads@napista.com.br>", "Contato", WHATSAPP, "<n@x>"))
        self.assertEqual(r.status, PortalMensagem.Status.CRIADO)
        self.assertEqual(r.lead.portal, "napista")

    def test_email_so_em_html(self):
        corpo = ("<html><body><p>Nome: Bia</p><p>Email: bia@x.com</p><p>Telefone: 81988887777</p>"
                 "<p>Fiat Argo 2022</p><p>R$ 59990,00</p><p>Identificador do lead: abcdef12-0000</p></body></html>")
        r = portais.processar(email_de(OLX, "Lead", corpo, "<h@x>", html=True))
        self.assertEqual((r.lead.nome, r.lead.telefone), ("Bia", "5581988887777"))
        self.assertIn("Argo", r.lead.carro_texto)

    def test_leitura_desligada_sem_credenciais(self):
        self.assertIsNone(portais.ler_caixa())
