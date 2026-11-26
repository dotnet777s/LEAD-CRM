import json
from datetime import timedelta
from decimal import Decimal
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import override_settings
from django.utils import timezone

from captacao import meta, services, utils
from captacao.models import EventoMeta, Lead

from .helpers import Base, novo_veiculo


def criar_lead(v=None, **kw):
    d = dict(nome="Maria Silva", telefone="5581999998888", veiculo=v, origem="meta_lp", fbp="fb.1.1.1", ip="1.2.3.4",
             user_agent="UA", classe="A", consent_meta=True)
    d.update(kw)
    return Lead.objects.create(**d)


class EtapaEventosTests(Base):
    def setUp(self):
        super().setUp()
        self.v = novo_veiculo()
        self.lead = criar_lead(self.v)

    def test_qualificado_schedule_purchase_uma_vez(self):
        for etapa in ("qualificado", "visita", "qualificado", "visita"):   # volta e repete
            services.mudar_etapa(self.lead, etapa)
        services.mudar_etapa(self.lead, "vendido", veiculo_vendido=self.v, valor_venda=Decimal("58000"))
        self.assertEqual(sorted(EventoMeta.objects.values_list("nome", flat=True)), ["Purchase", "QualifiedLead", "Schedule"])
        p = EventoMeta.objects.get(nome="Purchase").payload
        self.assertEqual((p["action_source"], p["custom_data"]["value"]), ("physical_store", 58000.0))
        self.assertEqual(EventoMeta.objects.get(nome="Schedule").payload["action_source"], "system_generated")
        self.assertEqual(p["event_id"], f"{self.lead.uuid}:Purchase")

    def test_etapas_sem_evento(self):
        for etapa in ("atendimento", "compareceu", "negociacao"):
            services.mudar_etapa(self.lead, etapa)
        self.assertEqual(EventoMeta.objects.count(), 0)
        self.assertIsNotNone(Lead.objects.get().primeira_resposta_em)

    def test_lead_manual_nao_dispara(self):
        l = services.criar_lead_manual("Zé", "5581988887777", self.v, "whatsapp")
        services.mudar_etapa(l, "qualificado")
        self.assertEqual(EventoMeta.objects.count(), 0)

    def test_regras(self):
        with self.assertRaises(services.RegraErro):
            services.mudar_etapa(self.lead, "perdido")
        with self.assertRaises(services.RegraErro):
            services.mudar_etapa(self.lead, "vendido", veiculo_vendido=self.v)
        services.mudar_etapa(self.lead, "perdido", motivo_perda="preco")
        self.assertEqual(Lead.objects.get().motivo_perda, "preco")

    def test_payload_hashes(self):
        services.mudar_etapa(self.lead, "qualificado")
        ev = EventoMeta.objects.get()
        ud = ev.payload["user_data"]
        self.assertEqual(ud["ph"], [utils.sha256("5581999998888")])
        self.assertEqual(ud["fn"], [utils.sha256("maria")])
        self.assertEqual(ud["ln"], [utils.sha256("silva")])
        self.assertEqual(ud["country"], [utils.sha256("br")])
        self.assertEqual((ud["client_ip_address"], ud["client_user_agent"], ud["fbp"]), ("1.2.3.4", "UA", "fb.1.1.1"))
        self.assertNotIn("Maria", json.dumps(ev.payload))


CAPI = dict(META_CAPI_ENABLED=True, META_PIXEL_ID="123", META_CAPI_ACCESS_TOKEN="tok")


@override_settings(**CAPI)
class WorkerTests(Base):
    def evento(self):
        services.mudar_etapa(criar_lead(novo_veiculo()), "qualificado")
        return EventoMeta.objects.get()

    @staticmethod
    def resp(code=200):
        return mock.Mock(status_code=code, text="{}")

    def test_envia_e_nao_reenvia(self):
        ev = self.evento()
        with mock.patch("captacao.meta.requests.post", return_value=self.resp()) as post:
            self.assertEqual(meta.rodar_lote()["enviados"], 1)
        ev.refresh_from_db()
        self.assertEqual(ev.status, "enviado")
        self.assertTrue(post.call_args.args[0].endswith("/123/events"))
        with mock.patch("captacao.meta.requests.post") as post2:
            meta.rodar_lote()
        post2.assert_not_called()

    def test_retry_exponencial_e_erro_final(self):
        ev = self.evento()
        agora, esperas = timezone.now(), []
        with mock.patch("captacao.meta.requests.post", return_value=self.resp(500)):
            for _ in range(5):
                ev.refresh_from_db()
                meta.processar(ev, agora=agora)
                ev.refresh_from_db()
                esperas.append(int((ev.proxima_tentativa_em - agora).total_seconds()))
        self.assertEqual(esperas[:4], [60, 120, 240, 480])
        self.assertEqual((ev.status, ev.tentativas), ("erro", 5))

    def test_fora_da_janela_nao_envia(self):
        ev = self.evento()
        ev.payload["event_time"] = int((timezone.now() - timedelta(days=8)).timestamp())
        ev.save()
        with mock.patch("captacao.meta.requests.post") as post:
            meta.rodar_lote()
        post.assert_not_called()
        ev.refresh_from_db()
        self.assertEqual(ev.status, "nao_enviado")
        self.assertIn("janela", ev.motivo)

    @override_settings(META_CAPI_ENABLED=False)
    def test_desligada_nao_envia(self):
        self.evento()
        with mock.patch("captacao.meta.requests.post") as post:
            self.assertTrue(meta.rodar_lote().get("desligado"))
        post.assert_not_called()

    @override_settings(META_TEST_EVENT_CODE="TEST1")
    def test_test_event_code(self):
        self.evento()
        with mock.patch("captacao.meta.requests.post", return_value=self.resp()) as post:
            meta.rodar_lote()
        self.assertEqual(post.call_args.kwargs["json"]["test_event_code"], "TEST1")


class KanbanTests(Base):
    def setUp(self):
        super().setUp()
        U = get_user_model()
        self.gestor = U.objects.create_user("gestor", password="x12345678", is_staff=True)
        self.vend = U.objects.create_user("vend", password="x12345678", first_name="Caio")
        self.outro = U.objects.create_user("outro", password="x12345678")
        self.v = novo_veiculo()
        self.l1 = criar_lead(self.v, vendedor=self.vend, nome="Do Caio")
        self.l2 = criar_lead(self.v, vendedor=self.outro, nome="Do Outro", telefone="5581999990000")
        self.l3 = criar_lead(self.v, nome="Sem Dono", telefone="5581999990001")

    def test_consultor_ve_os_seus_e_os_sem_dono(self):
        self.client.force_login(self.vend)
        html = self.client.get("/quadro/").content.decode()
        self.assertIn("Do Caio", html)
        self.assertIn("Sem Dono", html)
        self.assertNotIn("Do Outro", html)
        self.assertEqual(self.client.get(f"/lead/{self.l2.pk}/").status_code, 404)

    def test_gestor_ve_todos_e_filtra(self):
        self.client.force_login(self.gestor)
        html = self.client.get("/quadro/").content.decode()
        for n in ("Do Caio", "Do Outro", "Sem Dono"):
            self.assertIn(n, html)
        filtrado = self.client.get("/quadro/?q=Caio").content.decode()
        self.assertIn("Do Caio", filtrado)
        self.assertNotIn("Do Outro", filtrado)

    def test_mover_direto_e_com_formulario(self):
        self.client.force_login(self.vend)
        r = self.client.post(f"/lead/{self.l1.pk}/mover-direto/", {"etapa": "qualificado"})
        self.assertEqual(r.status_code, 204)
        self.assertIn("quadroAtualizar", r["HX-Trigger"])
        self.l1.refresh_from_db()
        self.assertEqual(self.l1.etapa, "qualificado")
        self.assertEqual(self.client.post(f"/lead/{self.l1.pk}/mover-direto/", {"etapa": "vendido"}).status_code, 400)
        r = self.client.post(f"/lead/{self.l1.pk}/mover/", {"etapa": "perdido"})
        self.assertEqual(r.status_code, 200)
        self.assertIn("motivo", r.content.decode().lower())
        self.client.post(f"/lead/{self.l1.pk}/mover/", {"etapa": "perdido", "motivo": "preco"})
        self.l1.refresh_from_db()
        self.assertEqual((self.l1.etapa, self.l1.motivo_perda), ("perdido", "preco"))

    def test_vendido_com_valor_brasileiro(self):
        self.client.force_login(self.vend)
        self.client.post(f"/lead/{self.l1.pk}/mover/", {"etapa": "vendido", "valor": "58.500,00", "veiculo_vendido": self.v.pk})
        self.l1.refresh_from_db()
        self.assertEqual(self.l1.valor_venda, Decimal("58500.00"))
        self.assertTrue(EventoMeta.objects.filter(nome="Purchase").exists())

    def test_whatsapp_marca_atendimento_e_assume(self):
        self.client.force_login(self.vend)
        self.client.post(f"/lead/{self.l3.pk}/whatsapp/")
        self.l3.refresh_from_db()
        self.assertEqual((self.l3.etapa, self.l3.vendedor_id), ("atendimento", self.vend.pk))
        self.assertIsNotNone(self.l3.primeira_resposta_em)

    def test_sla_no_card_novo(self):
        self.client.force_login(self.gestor)
        self.assertIn('class="sla ok"', self.client.get("/quadro/").content.decode())

    def test_detalhe_nota_e_novo_lead(self):
        self.client.force_login(self.gestor)
        self.assertEqual(self.client.get(f"/lead/{self.l1.pk}/").status_code, 200)
        self.client.post(f"/lead/{self.l1.pk}/nota/", {"texto": "Ligar amanhã"})
        self.assertTrue(self.l1.historico.filter(tipo="nota").exists())
        r = self.client.post("/lead/novo/", {"nome": "Novo", "telefone": "(81) 98888-7777", "origem": "loja"})
        self.assertEqual(r.status_code, 204)
        self.assertEqual(Lead.objects.get(nome="Novo").origem, "loja")

    def test_excluir_so_gestor(self):
        self.client.force_login(self.vend)
        self.assertEqual(self.client.post(f"/lead/{self.l1.pk}/excluir/").status_code, 403)
        self.client.force_login(self.gestor)
        self.client.post(f"/lead/{self.l1.pk}/excluir/")
        self.assertFalse(Lead.objects.filter(pk=self.l1.pk).exists())

    def test_painel_eventos_e_kanban_carregam(self):
        self.client.force_login(self.gestor)
        services.mudar_etapa(self.l1, "qualificado")
        services.mudar_etapa(self.l1, "visita", visita_em=timezone.now())
        for url in ("/", "/painel/", "/painel/?periodo=7d", "/meta/"):
            self.assertEqual(self.client.get(url).status_code, 200, url)
        self.assertEqual([f["n"] for f in self.client.get("/painel/").context["funil"]][:3], [3, 1, 1])
        self.client.post("/painel/investimento/", {"mes": timezone.localdate().replace(day=1).isoformat(), "valor": "1.300,00"})
        self.assertEqual(self.client.get("/painel/").context["custo_lead"], Decimal("433.33"))


class CarrosTests(Base):
    def test_lista_paginas_de_captura(self):
        v = novo_veiculo()
        novo_veiculo(placa="OUT1A00", modelo="Vendido1", status="vendido")
        self.client.force_login(get_user_model().objects.create_user("g", password="x12345678", is_staff=True))
        r = self.client.get("/carros/")
        self.assertEqual(r.status_code, 200)
        html = r.content.decode()
        self.assertIn(f"/v/{v.pk}-fiat-argo-2022/", html)
        self.assertNotIn("Vendido1", html)


class EquipeTests(Base):
    def setUp(self):
        super().setUp()
        U = get_user_model()
        self.gestor = U.objects.create_user("gestor", password="x12345678", is_staff=True)
        self.cons = U.objects.create_user("cons", password="x12345678")

    def test_gestor_cria_consultor_que_consegue_entrar(self):
        self.client.force_login(self.gestor)
        r = self.client.post("/equipe/", {"nome": "Caio Souza", "usuario": "Caio", "senha": "SenhaForte#2025"})
        self.assertEqual(r.status_code, 302)
        u = get_user_model().objects.get(username="caio")
        self.assertEqual(u.first_name, "Caio Souza")
        self.assertFalse(u.is_staff)
        self.client.logout()
        self.assertTrue(self.client.login(username="caio", password="SenhaForte#2025"))

    def test_validacoes_e_permissao(self):
        self.client.force_login(self.gestor)
        self.assertEqual(self.client.post("/equipe/", {"nome": "X", "usuario": "cons", "senha": "SenhaForte#2025"}).status_code, 400)
        self.assertEqual(self.client.post("/equipe/", {"nome": "X", "usuario": "novo", "senha": "123"}).status_code, 400)
        self.client.force_login(self.cons)
        self.assertEqual(self.client.get("/equipe/").status_code, 403)
        self.assertEqual(self.client.post("/equipe/", {"nome": "X", "usuario": "novo", "senha": "SenhaForte#2025"}).status_code, 403)

    def test_desativar_e_redefinir_senha(self):
        self.client.force_login(self.gestor)
        self.client.post(f"/equipe/{self.cons.pk}/ativo/")
        self.cons.refresh_from_db()
        self.assertFalse(self.cons.is_active)
        self.client.post(f"/equipe/{self.cons.pk}/senha/", {"senha": "OutraSenha#2025"})
        self.cons.refresh_from_db()
        self.assertTrue(self.cons.check_password("OutraSenha#2025"))
        self.client.post(f"/equipe/{self.gestor.pk}/ativo/")
        self.gestor.refresh_from_db()
        self.assertTrue(self.gestor.is_active)


class MidiasTests(Base):
    def test_lista_e_janela_de_fotos(self):
        v = novo_veiculo(fotos=3)
        self.client.force_login(get_user_model().objects.create_user("g", password="x12345678", is_staff=True))
        self.assertEqual(self.client.get("/carros/").status_code, 200)
        r = self.client.get(f"/carros/{v.pk}/midias/?aba=fotos")
        self.assertEqual(r.status_code, 200)
        html = r.content.decode()
        self.assertIn("Fotos (3)", html)
        self.assertNotIn("Vídeos (", html)

    def test_janela_de_videos_avisa_que_ficam_no_autocerto(self):
        v = novo_veiculo()
        self.client.force_login(get_user_model().objects.create_user("g", password="x12345678", is_staff=True))
        html = self.client.get(f"/carros/{v.pk}/midias/?aba=videos").content.decode()
        self.assertIn("Vídeos (0)", html)
        self.assertIn("AutoCerto", html)
        self.assertNotIn("Fotos (", html)

    def test_recife_e_olinda_aparecem_com_o_local(self):
        a, b = novo_veiculo(unidade="Recife"), novo_veiculo(unidade="Olinda")
        self.client.force_login(get_user_model().objects.create_user("g", password="x12345678", is_staff=True))
        html = self.client.get("/carros/").content.decode()
        self.assertIn(f"/v/{a.pk}-", html)
        self.assertIn(f"/v/{b.pk}-", html)
        self.assertIn("está em Recife", html)
        self.assertIn("está em Olinda", html)

    def test_foto_vem_do_banco_e_e_redimensionada(self):
        import io
        from PIL import Image
        from catalogo.models import FotoArquivo, FotoVeiculo
        v = novo_veiculo()
        f = FotoVeiculo.objects.get(veiculo_rid=v.rid)
        buf = io.BytesIO()
        Image.new("RGB", (1600, 1200), "red").save(buf, "JPEG")
        FotoArquivo.objects.create(foto=f, jpeg=buf.getvalue())
        with self.settings(FOTOS_CACHE=__import__("tempfile").mkdtemp()):
            r = self.client.get(f"/foto/{f.pk}/480.webp")
            self.assertEqual(r.status_code, 200)
            self.assertEqual(r["Content-Type"], "image/webp")
            self.assertEqual(self.client.get(f"/foto/{f.pk}/333.webp").status_code, 404)
