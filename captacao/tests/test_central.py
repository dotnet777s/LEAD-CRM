from datetime import timedelta
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.utils import timezone

from captacao import campanhas, followup, whatsapp
from captacao.models import (Campanha, CampanhaEnvio, Lead, ModeloWhatsApp, RespostaRapida, WhatsAppMensagem)

from .helpers import novo_veiculo

API = dict(WHATSAPP_PHONE_ID="123", WHATSAPP_TOKEN="t", WHATSAPP_WABA_ID="999", WHATSAPP_APP_SECRET="s", WHATSAPP_VERIFY_TOKEN="v")


def lead(**kw):
    d = dict(nome="Maria Souza", telefone="5581999998888", carro_texto="Fiat Argo 2022 · R$ 59.900")
    d.update(kw)
    return Lead.objects.create(**d)


def msg(l, direcao="in", texto="oi", lida=None, quando=None, **kw):
    return WhatsAppMensagem.objects.create(lead=l, telefone=l.telefone, direcao=direcao, texto=texto,
                                           lida=(direcao == "out") if lida is None else lida,
                                           criado_em=quando or timezone.now(), **kw)


def resposta_ok(wamid="w1"):
    return mock.Mock(status_code=200, json=lambda: {"messages": [{"id": wamid}]})


class Logado(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("gestor", password="x", is_staff=True)
        self.client.force_login(self.user)


class TelasTests(Logado):
    def test_todas_as_telas_abrem(self):
        l = lead()
        msg(l)
        for url in ("/", "/conversas/", "/conversas/lista/", f"/conversas/{l.pk}/", f"/conversas/{l.pk}/mensagens/", "/conversas/badge/",
                    "/contatos/", "/contatos/exportar/", "/agenda/", "/agenda/?s=1", "/modelos/", "/campanhas/", "/campanhas/previa/",
                    "/whatsapp/", "/followup/", "/portais/", "/painel/", "/carros/", "/equipe/", "/meta/", "/painel/formulario/"):
            self.assertEqual(self.client.get(url).status_code, 200, url)

    def test_menu_mostra_badges(self):
        l = lead()
        msg(l, lida=False)
        msg(lead(telefone="5581988887777", nome="Outro"), lida=False)
        html = self.client.get("/").content.decode()
        self.assertIn('<b class="pt">2</b>', html)

    def test_tela_exige_login(self):
        self.client.logout()
        self.assertEqual(self.client.get("/conversas/").status_code, 302)


class ConversasTests(Logado):
    def test_lista_filtra_nao_lidas_e_ordena(self):
        a, b = lead(nome="Antiga"), lead(nome="Recente", telefone="5581988887777")
        msg(a, lida=True, quando=timezone.now() - timedelta(hours=5))
        msg(b, lida=False)
        html = self.client.get("/conversas/lista/").content.decode()
        self.assertLess(html.index("Recente"), html.index("Antiga"))
        so = self.client.get("/conversas/lista/?f=nao_lidas").content.decode()
        self.assertIn("Recente", so)
        self.assertNotIn("Antiga", so)

    def test_filtros_de_quem_falou_por_ultimo(self):
        c = lead(nome="Esperando loja", telefone="5581977776666")
        msg(c, "out", "Enviei a proposta")
        self.assertIn("Esperando loja", self.client.get("/conversas/lista/?f=loja").content.decode())
        self.assertNotIn("Esperando loja", self.client.get("/conversas/lista/?f=cliente").content.decode())

    def test_abrir_a_conversa_marca_como_lida(self):
        l = lead()
        m = msg(l, lida=False)
        self.assertEqual(self.client.get(f"/conversas/{l.pk}/").status_code, 200)
        m.refresh_from_db()
        self.assertTrue(m.lida)
        self.assertEqual(self.client.get("/conversas/badge/").content, b"")

    def test_busca_por_telefone(self):
        l = lead()
        msg(l)
        self.assertIn("Maria", self.client.get("/conversas/lista/?q=99999").content.decode())
        self.assertNotIn("Maria", self.client.get("/conversas/lista/?q=zzz").content.decode())

    @override_settings(**API)
    @mock.patch("captacao.whatsapp.requests.post")
    def test_responder_dentro_da_janela(self, post):
        post.return_value = resposta_ok()
        l = lead()
        msg(l)
        r = self.client.post(f"/conversas/{l.pk}/enviar/", {"texto": "Olá, tudo bem?"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(l.mensagens_wa.filter(direcao="out").get().texto, "Olá, tudo bem?")
        l.refresh_from_db()
        self.assertEqual(l.etapa, Lead.Etapa.ATENDIMENTO)         # responder tira o lead de "Novo"
        self.assertEqual(l.followups.count(), 1)                  # e começa o follow-up

    @override_settings(**API)
    @mock.patch("captacao.whatsapp.requests.post")
    def test_fora_da_janela_recusa_texto_e_aceita_modelo(self, post):
        post.return_value = resposta_ok()
        l = lead()
        msg(l, quando=timezone.now() - timedelta(hours=30))
        self.assertContains(self.client.post(f"/conversas/{l.pk}/enviar/", {"texto": "x"}), "24 h")
        post.assert_not_called()
        mod = ModeloWhatsApp.objects.create(nome="retomada", status="APPROVED", corpo="Olá, {{1}}!", n_variaveis=1)
        self.client.post(f"/conversas/{l.pk}/modelo/", {"modelo": mod.pk})
        corpo = post.call_args.kwargs["json"]
        self.assertEqual((corpo["type"], corpo["template"]["name"]), ("template", "retomada"))
        self.assertEqual(corpo["template"]["components"][0]["parameters"][0]["text"], "Maria")

    @override_settings(**API)
    @mock.patch("captacao.whatsapp.requests.post")
    def test_nao_envia_a_quem_pediu_para_sair(self, post):
        l = lead(nao_contatar=True)
        mod = ModeloWhatsApp.objects.create(nome="retomada", status="APPROVED", corpo="Oi", n_variaveis=0)
        self.client.post(f"/conversas/{l.pk}/modelo/", {"modelo": mod.pk})
        post.assert_not_called()

    @override_settings(**API, SITE_URL="https://contato.topcarpe.com.br")
    @mock.patch("captacao.whatsapp.requests.post")
    def test_enviar_foto_do_carro(self, post):
        post.return_value = resposta_ok()
        l = lead()
        msg(l)
        v = novo_veiculo()
        self.client.post(f"/conversas/{l.pk}/carro/", {"carro": v.pk})
        corpo = post.call_args.kwargs["json"]
        self.assertEqual(corpo["type"], "image")
        self.assertIn("https://contato.topcarpe.com.br/foto/", corpo["image"]["link"])
        self.assertIn("R$ 59.900", corpo["image"]["caption"])
        self.assertIn("/v/", corpo["image"]["caption"])
        l.refresh_from_db()
        self.assertEqual(l.veiculo_id, v.pk)

    def test_nota_interna_nao_vai_ao_cliente(self):
        l = lead()
        self.client.post(f"/conversas/{l.pk}/nota/", {"texto": "Cliente quer Argo branco"})
        self.assertTrue(l.historico.filter(texto__icontains="Argo branco").exists())
        self.assertEqual(WhatsAppMensagem.objects.count(), 0)

    def test_mudar_etapa_pela_conversa(self):
        l = lead()
        self.client.post(f"/conversas/{l.pk}/etapa/", {"etapa": "qualificado"})
        l.refresh_from_db()
        self.assertEqual(l.etapa, Lead.Etapa.QUALIFICADO)
        self.assertEqual(self.client.post(f"/conversas/{l.pk}/etapa/", {"etapa": "vendido"}).status_code, 400)

    @override_settings(**API)
    @mock.patch("captacao.whatsapp.requests.get")
    def test_midia_do_cliente(self, get):
        l = lead()
        m = msg(l, tipo="image", midia_id="MID1", midia_tipo="image/jpeg")
        get.side_effect = [mock.Mock(status_code=200, json=lambda: {"url": "https://x/y", "mime_type": "image/jpeg"}),
                           mock.Mock(status_code=200, content=b"\xff\xd8jpg")]
        r = self.client.get(f"/whatsapp/midia/{m.pk}/")
        self.assertEqual((r.status_code, b"".join(r.streaming_content)), (200, b"\xff\xd8jpg"))
        self.assertEqual(r["Content-Type"], "image/jpeg")

    def test_mensagem_recebida_com_midia_e_pedido_de_saida(self):
        from captacao.tests.test_followup import SEGREDO, assinar, evento_msg  # noqa: F401
        l = lead()
        payload = {"entry": [{"changes": [{"value": {"messages": [
            {"from": "5581999998888", "id": "m1", "type": "image", "image": {"id": "IMG9", "mime_type": "image/png", "caption": "olha"}},
            {"from": "5581999998888", "id": "m2", "type": "text", "text": {"body": "SAIR"}}]}}]}]}
        whatsapp.processar_webhook(payload)
        m1 = WhatsAppMensagem.objects.get(wamid="m1")
        self.assertEqual((m1.midia_id, m1.midia_tipo, m1.lida, m1.texto), ("IMG9", "image/png", False, "[imagem] olha"))
        l.refresh_from_db()
        self.assertTrue(l.nao_contatar)
        self.assertEqual(followup.iniciar(l), None)               # e não entra mais em follow-up


class ContatosTests(Logado):
    def test_busca_e_filtros(self):
        lead(nome="Zeca Teste")
        lead(nome="Ana", telefone="5581977770000", origem="portal")
        self.assertContains(self.client.get("/contatos/?q=zeca"), "Zeca Teste")
        self.assertNotContains(self.client.get("/contatos/?q=zeca"), "Ana")
        self.assertContains(self.client.get("/contatos/?origem=portal"), "Ana")

    def test_importar_csv_ignora_invalidos_e_repetidos(self):
        lead(telefone="5581999998888")
        csv = "nome;telefone\nJoão;(81) 98888-1111\nRepetido;81999998888\nSem fone;abc\n".encode()
        r = self.client.post("/contatos/importar/", {"arquivo": SimpleUploadedFile("c.csv", csv)})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(Lead.objects.filter(telefone="5581988881111", origem="outro").count(), 1)
        self.assertEqual(Lead.objects.count(), 2)
        self.assertContains(self.client.get(r["Location"]), "1 contato(s) importado(s)")

    def test_exportar_exige_gestor(self):
        lead()
        r = self.client.get("/contatos/exportar/")
        self.assertIn("Maria Souza", r.content.decode())
        self.client.force_login(get_user_model().objects.create_user("vend", password="x"))
        self.assertEqual(self.client.get("/contatos/exportar/").status_code, 403)


class AgendaTests(Logado):
    def test_semana_mostra_visita_e_resultado(self):
        l = lead(etapa=Lead.Etapa.VISITA, visita_em=timezone.now() + timedelta(hours=2))
        self.assertContains(self.client.get("/agenda/"), "Maria")
        l.visita_em = timezone.now() - timedelta(hours=5)
        l.save()
        self.assertContains(self.client.get("/agenda/"), "o cliente veio?")
        self.client.post(f"/lead/{l.pk}/visita/compareceu/")
        l.refresh_from_db()
        self.assertEqual(l.etapa, Lead.Etapa.COMPARECEU)

    def test_nao_veio_volta_para_atendimento_com_follow_up(self):
        l = lead(etapa=Lead.Etapa.VISITA, visita_em=timezone.now() - timedelta(hours=5))
        self.client.post(f"/lead/{l.pk}/visita/faltou/")
        l.refresh_from_db()
        self.assertEqual(l.etapa, Lead.Etapa.ATENDIMENTO)
        self.assertEqual(l.followups.count(), 1)
        self.assertTrue(l.historico.filter(texto__icontains="Não compareceu").exists())


@override_settings(**API)
class ModelosTests(Logado):
    @mock.patch("captacao.whatsapp.requests.get")
    def test_sincronizar(self, get):
        get.return_value = mock.Mock(status_code=200, json=lambda: {"data": [
            {"name": "retomada", "language": "pt_BR", "category": "UTILITY", "status": "APPROVED",
             "components": [{"type": "BODY", "text": "Olá, {{1}}! Tudo bem?"}]}]})
        self.client.post("/modelos/sincronizar/")
        m = ModeloWhatsApp.objects.get()
        self.assertEqual((m.nome, m.status, m.n_variaveis, m.aprovado), ("retomada", "APPROVED", 1, True))

    @mock.patch("captacao.whatsapp.requests.post")
    def test_criar_modelo_valida_exemplos(self, post):
        post.return_value = mock.Mock(status_code=200, json=lambda: {"status": "PENDING"})
        r = self.client.post("/modelos/criar/", {"nome": "Retomada Interesse", "corpo": "Olá, {{1}}!", "exemplos": ""}, follow=True)
        self.assertContains(r, "exemplo")
        post.assert_not_called()
        self.client.post("/modelos/criar/", {"nome": "Retomada Interesse", "corpo": "Olá, {{1}}!", "exemplos": "Maria", "categoria": "UTILITY"})
        self.assertEqual(ModeloWhatsApp.objects.get().nome, "retomada_interesse")
        self.assertEqual(post.call_args.kwargs["json"]["components"][0]["example"], {"body_text": [["Maria"]]})

    def test_respostas_rapidas(self):
        self.client.post("/modelos/rapida/", {"titulo": "Endereço", "texto": "Loja 11, Shopping do Automóvel"})
        r = RespostaRapida.objects.get()
        self.assertContains(self.client.get("/modelos/"), "Shopping do Automóvel")
        self.client.post(f"/modelos/rapida/{r.pk}/excluir/")
        self.assertEqual(RespostaRapida.objects.count(), 0)


@override_settings(**API, WHATSAPP_LIMITE_DIARIO=2)
class CampanhasTests(Logado):
    def setUp(self):
        super().setUp()
        self.modelo = ModeloWhatsApp.objects.create(nome="retomada", status="APPROVED", corpo="Olá, {{1}}!", n_variaveis=1)
        for i in range(4):
            lead(nome=f"Lead {i}", telefone=f"558199990{i:03d}")
        lead(nome="Não quer", telefone="5581999990999", nao_contatar=True)
        lead(nome="Vendido", telefone="5581999990888", etapa=Lead.Etapa.VENDIDO)

    def test_publico_exclui_quem_saiu_e_fechados(self):
        self.assertEqual(campanhas.publico({}).count(), 4)
        self.assertEqual(campanhas.publico({"etapas": ["vendido"]}).count(), 1)

    def test_criar_pelo_painel_e_previa(self):
        self.assertContains(self.client.get("/campanhas/previa/"), "<b>4</b>")
        self.client.post("/campanhas/criar/", {"nome": "Teste", "modelo": self.modelo.pk})
        c = Campanha.objects.get()
        self.assertEqual((c.status, c.envios.count()), ("rascunho", 4))
        self.assertContains(self.client.get("/campanhas/"), "Teste")

    def test_so_inicia_com_modelo_aprovado(self):
        pend = ModeloWhatsApp.objects.create(nome="novo", status="PENDING", corpo="x")
        c = campanhas.criar("x", pend, {})
        with self.assertRaises(whatsapp.WhatsAppErro):
            campanhas.iniciar(c)

    @mock.patch("captacao.whatsapp.requests.post")
    def test_envia_em_lotes_e_respeita_o_limite_diario(self, post):
        post.return_value = resposta_ok()
        c = campanhas.criar("Retomada", self.modelo, {})
        campanhas.iniciar(c)
        self.assertEqual(campanhas.rodar_lote(), 2)               # limite diário = 2
        self.assertEqual(campanhas.rodar_lote(), 0)               # já bateu o limite
        c.refresh_from_db()
        self.assertEqual((c.status, c.envios.filter(status="enviado").count(), c.envios.filter(status="pendente").count()), ("enviando", 2, 2))
        CampanhaEnvio.objects.filter(status="enviado").update(enviado_em=timezone.now() - timedelta(hours=25))
        self.assertEqual(campanhas.rodar_lote(), 2)
        c.refresh_from_db()
        self.assertEqual(c.status, Campanha.Status.CONCLUIDA)

    @mock.patch("captacao.whatsapp.requests.post")
    def test_falha_nao_trava_a_fila(self, post):
        post.side_effect = [mock.Mock(status_code=400, json=lambda: {"error": {"message": "numero invalido"}}), resposta_ok(), resposta_ok(), resposta_ok()]
        c = campanhas.criar("x", self.modelo, {})
        campanhas.iniciar(c)
        self.assertEqual(campanhas.rodar_lote(), 2)       # a falha não gasta o limite nem trava os demais
        self.assertEqual(c.envios.filter(status="falhou").get().erro, "numero invalido")

    @mock.patch("captacao.whatsapp.requests.post")
    def test_pedido_de_saida_depois_de_criar_a_campanha(self, post):
        c = campanhas.criar("x", self.modelo, {})
        Lead.objects.update(nao_contatar=True)
        campanhas.iniciar(c)
        self.assertEqual(campanhas.rodar_lote(), 0)
        post.assert_not_called()
        self.assertEqual(c.envios.filter(status="falhou").count(), 4)

    def test_so_gestor_cria(self):
        self.client.force_login(get_user_model().objects.create_user("vend", password="x"))
        self.assertEqual(self.client.post("/campanhas/criar/", {"nome": "x", "modelo": self.modelo.pk}).status_code, 403)


class ConexaoTests(Logado):
    def test_desconectado(self):
        self.assertContains(self.client.get("/whatsapp/"), "Não conectado")

    @override_settings(**API)
    @mock.patch("captacao.whatsapp.requests.get")
    def test_conectado_mostra_numero_e_qualidade(self, get):
        get.return_value = mock.Mock(status_code=200, json=lambda: {
            "display_phone_number": "+55 81 6000-2209", "verified_name": "Topcar", "quality_rating": "GREEN", "messaging_limit_tier": "TIER_1K"})
        html = self.client.get("/whatsapp/").content.decode()
        self.assertIn("Conectado", html)
        self.assertIn("GREEN", html)
