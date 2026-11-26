from datetime import date, timedelta
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.utils import timezone

from captacao import avisos, campanhas, classificacao, modelos, services, whatsapp
from captacao.models import (Agendamento, Atividade, Aviso, Campanha, CampanhaEnvio, Etiqueta, Lead, ModeloWhatsApp, Procura,
                             Profissional)

from .helpers import novo_veiculo
from .test_central import API, Logado, lead, resposta_ok


def modelo_ok(nome="retomada", corpo="Olá, {{1}}! {{2}}.", variaveis=("nome", "saudacao"), **kw):
    return ModeloWhatsApp.objects.create(nome=nome, status="APPROVED", corpo=corpo, n_variaveis=len(variaveis), variaveis=list(variaveis), **kw)


class VariaveisTests(TestCase):
    def test_converte_nomes_para_numeros_sem_repetir(self):
        corpo, ordem, ex = modelos.para_meta("Oi {{nome}}, {{saudacao}}! Tchau {{nome}}")
        self.assertEqual((corpo, ordem), ("Oi {{1}}, {{2}}! Tchau {{1}}", ["nome", "saudacao"]))
        self.assertEqual(ex, ["Maria", "Bom dia"])

    def test_variavel_desconhecida(self):
        self.assertEqual(modelos.invalidas("Oi {{nome}} {{xpto}}"), ["xpto"])

    def test_valores(self):
        l = lead(nome="maria aparecida souza", carro_texto="Fiat Argo 2022 · R$ 59.900")
        m = modelo_ok(variaveis=("nome", "carro", "preco"), corpo="{{1}} {{2}} {{3}}", n_variaveis=3) if False else None
        self.assertEqual(modelos.valor("nome", l), "Maria")
        self.assertEqual(modelos.valor("carro", l), "Fiat Argo 2022")
        self.assertEqual(modelos.valor("preco", l), "R$ 59.900")
        self.assertEqual(modelos.valor("remetente", l), "TopCar Veículos")
        self.assertEqual(modelos.valor("descricao", l, {"descricao": "revisão"}), "revisão")
        self.assertIn("/v/", modelos.valor("link", lead(veiculo=novo_veiculo(), telefone="5581911110000")))

    def test_modelo_antigo_sem_nomes_usa_o_primeiro_nome(self):
        m = ModeloWhatsApp.objects.create(nome="x", status="APPROVED", corpo="Olá, {{1}}!", n_variaveis=1)
        self.assertEqual(modelos.parametros(m, lead()), ["Maria"])


@override_settings(**API)
class EnvioModeloTests(TestCase):
    @mock.patch("captacao.whatsapp.requests.post")
    def test_envia_variaveis_e_cabecalho(self, post):
        post.return_value = resposta_ok()
        m = modelo_ok(header_tipo="IMAGE", header_url="https://x/foto.jpg")
        msg = whatsapp.enviar_modelo(lead(), m)
        comp = post.call_args.kwargs["json"]["template"]["components"]
        self.assertEqual(comp[0], {"type": "header", "parameters": [{"type": "image", "image": {"link": "https://x/foto.jpg"}}]})
        self.assertEqual([p["text"] for p in comp[1]["parameters"]][0], "Maria")
        self.assertTrue(msg.texto.startswith("Olá, Maria!"))

    @mock.patch("captacao.whatsapp.requests.post")
    def test_criar_modelo_nomeado(self, post):
        post.return_value = mock.Mock(status_code=200, json=lambda: {"status": "PENDING"})
        whatsapp.criar_modelo_nomeado("retomada", "UTILITY", "Olá, {{nome}}! Aqui é da {{remetente}}.")
        corpo = post.call_args.kwargs["json"]["components"][0]
        self.assertEqual(corpo["text"], "Olá, {{1}}! Aqui é da {{2}}.")
        self.assertEqual(corpo["example"], {"body_text": [["Maria", "TopCar Veículos"]]})
        m = ModeloWhatsApp.objects.get()
        self.assertEqual((m.variaveis, m.corpo_local), (["nome", "remetente"], "Olá, {{nome}}! Aqui é da {{remetente}}."))
        with self.assertRaises(whatsapp.WhatsAppErro):
            whatsapp.criar_modelo_nomeado("x", "UTILITY", "Oi {{inventada}}")

    @mock.patch("captacao.whatsapp.requests.get")
    def test_sincronizar_detecta_cabecalho_de_midia(self, get):
        get.return_value = mock.Mock(status_code=200, json=lambda: {"data": [{"name": "foto", "language": "pt_BR", "status": "APPROVED", "category": "MARKETING",
                                     "components": [{"type": "HEADER", "format": "IMAGE"}, {"type": "BODY", "text": "Oi {{1}}"}]}]})
        whatsapp.sincronizar_modelos()
        self.assertEqual(ModeloWhatsApp.objects.get().header_tipo, "IMAGE")


@override_settings(**API)
class AvisosTests(Logado):
    def setUp(self):
        super().setUp()
        self.m = modelo_ok()
        self.l1, self.l2 = lead(), lead(nome="Zé", telefone="5581911110000")

    def test_telas(self):
        for u in ("/enviar/", "/historico/", "/historico/?ver=recorrentes", "/historico/?ver=arquivados", "/campanhas/nova/", "/conta/", "/conta/senha/",
                  "/agenda/cadastros/"):
            self.assertEqual(self.client.get(u).status_code, 200, u)

    def test_criar_avisos_e_historico(self):
        hoje = timezone.localdate().isoformat()
        r = self.client.post("/enviar/", {"contatos": [self.l1.pk, self.l2.pk], "modelo": self.m.pk, "data": hoje, "hora": "23:59", "recorrencia": "semanal"})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(Aviso.objects.count(), 2)
        self.assertContains(self.client.get("/historico/"), "Maria")
        self.assertContains(self.client.get("/historico/?ver=recorrentes"), "Semanal")

    def test_exige_contato_e_modelo_aprovado(self):
        r = self.client.post("/enviar/", {"modelo": self.m.pk, "data": "2030-01-01"})
        self.assertIn("erro", r["Location"])
        self.assertEqual(Aviso.objects.count(), 0)

    @mock.patch("captacao.whatsapp.requests.post")
    def test_recorrencia_agenda_a_proxima(self, post):
        post.return_value = resposta_ok()
        a = Aviso.objects.create(lead=self.l1, modelo=self.m, enviar_em=timezone.now() - timedelta(minutes=1), recorrencia="semanal")
        self.assertEqual(avisos.rodar(), 1)
        a.refresh_from_db()
        self.assertEqual(a.status, "enviado")
        prox = Aviso.objects.get(status="pendente")
        self.assertEqual(prox.enviar_em.date(), (a.enviar_em + timedelta(days=7)).date())

    def test_meses(self):
        d = timezone.make_aware(timezone.datetime(2026, 1, 31, 10, 0))
        self.assertEqual(avisos.proxima_data(d, "mensal").day, 28)
        self.assertEqual(avisos.proxima_data(d, "anual").year, 2027)
        self.assertEqual(avisos.proxima_data(d, "trimestral").month, 4)
        self.assertIsNone(avisos.proxima_data(d, ""))

    @mock.patch("captacao.whatsapp.requests.post")
    def test_falha_e_reenviar_cancelar_arquivar(self, post):
        post.return_value = mock.Mock(status_code=400, json=lambda: {"error": {"message": "Business eligibility payment issue"}})
        a = Aviso.objects.create(lead=self.l1, modelo=self.m, enviar_em=timezone.now() - timedelta(minutes=1))
        avisos.rodar()
        a.refresh_from_db()
        self.assertEqual((a.status, a.erro), ("falhou", "Business eligibility payment issue"))
        self.assertContains(self.client.get("/historico/"), "payment issue")
        self.client.post(f"/historico/{a.pk}/reenviar/")
        a.refresh_from_db()
        self.assertEqual(a.status, "pendente")
        self.client.post(f"/historico/{a.pk}/cancelar/")
        a.refresh_from_db()
        self.assertEqual(a.status, "cancelado")
        self.client.post(f"/historico/{a.pk}/arquivar/")
        self.assertContains(self.client.get("/historico/?ver=arquivados"), "Maria")

    @mock.patch("captacao.whatsapp.requests.post")
    def test_quem_saiu_nao_recebe_e_limite_diario(self, post):
        post.return_value = resposta_ok()
        self.l1.nao_contatar = True
        self.l1.save()
        a = Aviso.objects.create(lead=self.l1, modelo=self.m, enviar_em=timezone.now() - timedelta(minutes=1))
        self.assertEqual(avisos.rodar(), 0)
        a.refresh_from_db()
        self.assertEqual(a.status, "cancelado")
        with override_settings(WHATSAPP_LIMITE_DIARIO=0):
            Aviso.objects.create(lead=self.l2, modelo=self.m, enviar_em=timezone.now() - timedelta(minutes=1))
            self.assertEqual(avisos.rodar(), 0)
        post.assert_not_called()


@override_settings(**API)
class CampanhaTiposTests(Logado):
    def setUp(self):
        super().setUp()
        self.m = modelo_ok()
        self.a, self.b = lead(), lead(nome="Zé", telefone="5581911110000", etapa=Lead.Etapa.VENDIDO)
        self.tag = Etiqueta.objects.create(nome="VIP")

    def test_assistente_abre_todos_os_tipos(self):
        for t in ("todos", "regua", "tag", "etapa", "cadastro", "aniversario", "avise", "item"):
            self.assertEqual(self.client.get(f"/campanhas/nova/{t}/").status_code, 200, t)
        self.assertEqual(self.client.get("/campanhas/nova/xyz/").status_code, 404)

    def test_todos_inclui_vendidos_e_espalha_no_dia(self):
        self.client.post("/campanhas/nova/todos/", {"nome": "Base", "modelo": self.m.pk, "espalhar": "on"})
        c = Campanha.objects.get()
        self.assertEqual((c.tipo, c.envios.count()), ("todos", 2))
        horas = list(c.envios.order_by("enviar_em").values_list("enviar_em", flat=True))
        self.assertLess(horas[0], horas[1])

    def test_por_tag_e_por_etapa(self):
        self.a.etiquetas.add(self.tag)
        self.client.post("/campanhas/nova/tag/", {"nome": "T", "modelo": self.m.pk, "etiquetas": [self.tag.pk]})
        self.assertEqual(Campanha.objects.get(tipo="tag").envios.get().lead, self.a)
        self.client.post("/campanhas/nova/etapa/", {"nome": "E", "modelo": self.m.pk, "etapas": ["vendido"]})
        self.assertEqual(Campanha.objects.get(tipo="etapa").envios.get().lead, self.b)
        self.assertContains(self.client.post("/campanhas/nova/etapa/", {"nome": "E", "modelo": self.m.pk}), "ao menos uma etapa")

    def test_cadastro_limita_31_dias(self):
        r = self.client.post("/campanhas/nova/cadastro/", {"nome": "C", "modelo": self.m.pk, "de": "2026-01-01", "ate": "2026-03-01"})
        self.assertContains(r, "31 dias")
        hoje = timezone.localdate()
        self.client.post("/campanhas/nova/cadastro/", {"nome": "C", "modelo": self.m.pk, "de": (hoje - timedelta(days=5)).isoformat(), "ate": hoje.isoformat()})
        self.assertEqual(Campanha.objects.get(tipo="cadastro").envios.count(), 2)           # cadastro inclui vendidos: ambos chegaram hoje
        self.assertEqual(Campanha.objects.get(tipo="cadastro").filtro["todos"], True)

    @override_settings(SITE_URL="https://contato.topcarpe.com.br")
    def test_item_leva_dados_do_carro(self):
        v = novo_veiculo()
        self.client.post("/campanhas/nova/item/", {"nome": "I", "modelo": self.m.pk, "veiculo": v.pk})
        e = Campanha.objects.get(tipo="item").envios.first()
        self.assertEqual(e.extras["carro"], "Fiat Argo 2022")
        self.assertIn("R$ 59.900", e.extras["preco"])
        self.assertIn("/v/", e.extras["link"])

    @mock.patch("captacao.whatsapp.requests.post")
    def test_regua_inscreve_quem_recebe_a_etiqueta_depois_de_ativa(self, post):
        post.return_value = resposta_ok()
        m2 = modelo_ok(nome="segundo", corpo="Passando de novo, {{1}}", variaveis=("nome",))
        self.client.post("/campanhas/nova/regua/", {"nome": "Régua", "etiquetas": [self.tag.pk], "modelo_1": self.m.pk, "dias_1": "0",
                                                    "modelo_2": m2.pk, "dias_2": "3"})
        c = Campanha.objects.get(tipo="regua")
        self.assertEqual(c.config["passos"][1], {"dias": 3, "modelo": m2.pk})
        self.assertEqual(campanhas.ao_etiquetar(self.a, self.tag), 0)               # rascunho: ninguém entra
        campanhas.iniciar(c)
        self.client.post(f"/lead/{self.a.pk}/etiqueta/add/", {"etiqueta": self.tag.pk})
        self.assertEqual(c.envios.count(), 2)
        self.assertEqual(sorted(c.envios.values_list("passo", flat=True)), [1, 2])
        self.assertEqual(campanhas.ao_etiquetar(self.a, self.tag), 0)               # não duplica
        self.assertEqual(campanhas.rodar_lote(), 1)                                 # o passo de 3 dias ainda não venceu
        self.assertEqual(post.call_args.kwargs["json"]["template"]["name"], "retomada")

    def test_aniversario_envia_so_no_dia(self):
        hoje = timezone.localdate()
        self.a.aniversario = date(1990, hoje.month, hoje.day)
        self.a.save()
        self.b.aniversario = date(1990, (hoje.month % 12) + 1, 1)
        self.b.save()
        c = campanhas.criar("Niver", self.m, {}, tipo="aniversario")
        campanhas.iniciar(c)
        self.assertEqual(campanhas.rodar_automaticas(), 1)
        self.assertEqual(campanhas.rodar_automaticas(), 0)                          # no mesmo dia não repete
        self.assertEqual(c.envios.get().lead, self.a)

    def test_avise_quando_chegar(self):
        self.a.carro_texto = "Fiat Argo 2022 · R$ 59.900"
        self.a.save()
        services.mudar_etapa(self.a, Lead.Etapa.SEM_ESTOQUE)
        p = Procura.objects.get()
        self.assertEqual(p.descricao, "Fiat Argo 2022")
        c = campanhas.criar("Avise", self.m, {}, tipo="avise")
        campanhas.iniciar(c)
        self.assertEqual(campanhas.rodar_automaticas(), 0)                          # ainda não tem no estoque
        novo_veiculo()                                                              # entra um Fiat Argo
        self.assertEqual(campanhas.rodar_automaticas(), 1)
        p.refresh_from_db()
        self.assertIsNotNone(p.avisada_em)
        self.assertIn("Argo", c.envios.get().extras["carro"])
        self.assertEqual(campanhas.rodar_automaticas(), 0)

    def test_so_gestor_cria_campanha(self):
        self.client.force_login(get_user_model().objects.create_user("vend", password="x"))
        self.assertEqual(self.client.get("/campanhas/nova/todos/").status_code, 403)


class AgendaConfigTests(Logado):
    def test_cadastros_e_agendamento(self):
        self.client.post("/agenda/cadastros/profissional/", {"nome": "Carlos"})
        self.client.post("/agenda/cadastros/atividade/", {"nome": "Test drive", "duracao": "45"})
        prof, ativ, l = Profissional.objects.get(), Atividade.objects.get(), lead()
        inicio = timezone.localtime(timezone.now() + timedelta(days=1)).strftime("%Y-%m-%dT10:00")
        r = self.client.post("/agenda/novo/", {"lead": l.pk, "atividade": ativ.pk, "profissional": prof.pk, "inicio": inicio})
        self.assertEqual(r.status_code, 302)
        a = Agendamento.objects.get()
        self.assertEqual(int((a.fim - a.inicio).total_seconds() // 60), 45)
        l.refresh_from_db()
        self.assertEqual(l.etapa, Lead.Etapa.VISITA)
        self.assertContains(self.client.get("/agenda/"), "Carlos")
        # conflito de horário para o mesmo profissional
        outro = lead(nome="Outro", telefone="5581911110000")
        r = self.client.post("/agenda/novo/", {"lead": outro.pk, "profissional": prof.pk, "inicio": inicio})
        self.assertIn("erro", r["Location"])
        self.assertEqual(Agendamento.objects.count(), 1)
        self.client.post(f"/agenda/{a.pk}/realizado/")
        l.refresh_from_db()
        self.assertEqual(l.etapa, Lead.Etapa.COMPARECEU)

    def test_alternar_e_so_gestor(self):
        self.client.post("/agenda/cadastros/profissional/", {"nome": "Ana"})
        p = Profissional.objects.get()
        self.client.post(f"/agenda/cadastros/profissional_alternar/{p.pk}/")
        p.refresh_from_db()
        self.assertFalse(p.ativo)
        self.client.force_login(get_user_model().objects.create_user("vend", password="x"))
        self.assertEqual(self.client.post("/agenda/cadastros/profissional/", {"nome": "X"}).status_code, 403)


class ContaEOutrosTests(Logado):
    def test_conta_salva_dados(self):
        self.client.post("/conta/", {"first_name": "Maria", "last_name": "Silva", "email": "m@x.com"})
        self.user.refresh_from_db()
        self.assertEqual((self.user.first_name, self.user.email), ("Maria", "m@x.com"))

    def test_trocar_senha(self):
        self.user.set_password("antiga123!")
        self.user.save()
        self.client.force_login(self.user)
        r = self.client.post("/conta/senha/", {"old_password": "antiga123!", "new_password1": "NovaSenha#2026x", "new_password2": "NovaSenha#2026x"})
        self.assertEqual(r.status_code, 302)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password("NovaSenha#2026x"))

    def test_aniversario_na_ficha_e_na_importacao(self):
        l = lead()
        self.client.post(f"/lead/{l.pk}/aniversario/", {"aniversario": "1990-05-17"})
        l.refresh_from_db()
        self.assertEqual(l.aniversario, date(1990, 5, 17))
        csv = "nome;telefone;aniversario\nJoão;(81) 98888-1111;17/05/1985\n".encode()
        self.client.post("/contatos/importar/", {"arquivo": SimpleUploadedFile("c.csv", csv)})
        self.assertEqual(Lead.objects.get(telefone="5581988881111").aniversario, date(1985, 5, 17))

    def test_manifest_e_icones(self):
        from django.contrib.staticfiles import finders
        self.assertTrue(finders.find("captacao/manifest.webmanifest"))
        self.assertTrue(finders.find("captacao/icone-512.png"))
        self.assertContains(self.client.get("/"), "manifest.webmanifest")

    def test_modelos_tela_nova_e_cabecalho(self):
        m = ModeloWhatsApp.objects.create(nome="foto", status="APPROVED", corpo="Oi", header_tipo="IMAGE")
        self.assertContains(self.client.get("/modelos/"), "Pré-visualização")
        self.client.post("/modelos/cabecalho/", {"modelo": m.pk, "url": "https://x/foto.jpg"})
        m.refresh_from_db()
        self.assertEqual(m.header_url, "https://x/foto.jpg")
        r = self.client.post("/modelos/cabecalho/", {"modelo": m.pk, "url": "http://inseguro"}, follow=True)
        self.assertContains(r, "https")
