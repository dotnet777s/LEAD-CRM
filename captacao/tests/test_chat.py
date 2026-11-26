from datetime import timedelta
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import override_settings
from django.utils import timezone

from captacao import followup, funil, whatsapp
from captacao.models import FollowUp, Lead, WhatsAppMensagem

from .helpers import novo_veiculo
from .test_central import API, Logado, lead, msg, resposta_ok


class ChatTests(Logado):
    def test_abas_e_filtros(self):
        a = lead(nome="Normal")
        msg(a)
        b = lead(nome="Portal", telefone="5581911110000", portal="olx")
        msg(b)
        c = lead(nome="Arquivada", telefone="5581922220000", arquivada=True)
        msg(c)
        d = lead(nome="Minha", telefone="5581933330000", vendedor=self.user)
        msg(d)
        tudo = self.client.get("/conversas/lista/").content.decode()
        self.assertNotIn("Arquivada", tudo)
        self.assertIn("Normal", tudo)
        self.assertIn("Arquivada", self.client.get("/conversas/lista/?aba=arquivadas").content.decode())
        portais = self.client.get("/conversas/lista/?aba=portais").content.decode()
        self.assertIn("Portal", portais)
        self.assertNotIn("Normal", portais)
        meus = self.client.get("/conversas/lista/?aba=meus").content.decode()
        self.assertIn("Minha", meus)
        self.assertNotIn("Normal", meus)

    def test_sem_humano_e_agendados(self):
        a = lead(nome="SemHumano")
        msg(a)
        b = lead(nome="ComHumano", telefone="5581911110000")
        msg(b, "out", "oi", usuario=self.user)
        c = lead(nome="Agendada", telefone="5581922220000", etapa="visita")
        msg(c)
        sh = self.client.get("/conversas/lista/?f=sem_humano").content.decode()
        self.assertIn("SemHumano", sh)
        self.assertNotIn("ComHumano", sh)
        self.assertIn("Agendada", self.client.get("/conversas/lista/?f=agendados").content.decode())

    def test_fixada_vai_para_o_topo_e_mostra_chips_da_etapa(self):
        antiga = lead(nome="Antiga", telefone="5581911110000", fixada=True)
        msg(antiga, quando=timezone.now() - timedelta(days=2))
        msg(lead(nome="Recente"))
        html = self.client.get("/conversas/lista/").content.decode()
        self.assertLess(html.index("Antiga"), html.index("Recente"))
        self.assertIn("Novo", html)                                   # chip da etapa
        self.assertIn("#64748b", html)                                # cor da etapa vinda do funil

    def test_contagem_da_janela_na_lista(self):
        l = lead()
        msg(l, quando=timezone.now() - timedelta(hours=3))
        self.assertIn("20h", self.client.get("/conversas/lista/").content.decode())
        l2 = lead(nome="Velha", telefone="5581911110000")
        msg(l2, quando=timezone.now() - timedelta(hours=30))
        self.assertNotIn("⏰ 0", self.client.get("/conversas/lista/").content.decode())

    def test_acoes_da_conversa(self):
        l = lead()
        msg(l)
        self.client.post(f"/conversas/{l.pk}/acao/fixar/")
        self.client.post(f"/conversas/{l.pk}/acao/comentario/", {"texto": "Prefere cor branca"})
        self.client.post(f"/conversas/{l.pk}/acao/renomear/", {"nome": "Maria Souza Lima"})
        self.client.post(f"/conversas/{l.pk}/acao/atendente/", {"atendente": self.user.pk})
        l.refresh_from_db()
        self.assertEqual((l.fixada, l.comentario_fixo, l.nome, l.vendedor_id), (True, "Prefere cor branca", "Maria Souza Lima", self.user.pk))
        html = self.client.get(f"/conversas/{l.pk}/").content.decode()
        self.assertIn("Prefere cor branca", html)
        self.assertIn("Desafixar conversa", html)
        self.client.post(f"/conversas/{l.pk}/acao/finalizar/")
        l.refresh_from_db()
        self.assertTrue(l.arquivada)
        self.assertContains(self.client.get(f"/conversas/{l.pk}/"), "Reabrir")
        self.client.post(f"/conversas/{l.pk}/acao/finalizar/")
        l.refresh_from_db()
        self.assertFalse(l.arquivada)
        self.assertEqual(self.client.post(f"/conversas/{l.pk}/acao/xyz/").status_code, 404)

    def test_marcar_como_nao_lida_e_abrir_limpa(self):
        l = lead()
        msg(l, lida=True)
        self.assertEqual(self.client.post(f"/conversas/{l.pk}/acao/nao_lida/").status_code, 204)
        l.refresh_from_db()
        self.assertTrue(l.marcada_nao_lida)
        self.assertIn("nl", self.client.get("/conversas/lista/?f=nao_lidas").content.decode())
        self.client.get(f"/conversas/{l.pk}/")
        l.refresh_from_db()
        self.assertFalse(l.marcada_nao_lida)

    def test_pausar_follow_up_pela_conversa(self):
        l = lead()
        msg(l)
        fu = followup.iniciar(l, manual=True)
        self.assertContains(self.client.get(f"/conversas/{l.pk}/"), "Pausar follow-up")
        self.client.post(f"/conversas/{l.pk}/acao/pausar_followup/")
        fu.refresh_from_db()
        self.assertEqual(fu.status, FollowUp.Status.PAUSADO)
        self.client.post(f"/conversas/{l.pk}/acao/pausar_followup/")
        fu.refresh_from_db()
        self.assertEqual(fu.status, FollowUp.Status.ATIVO)

    def test_simular_e_localizacao(self):
        v = novo_veiculo()
        l = lead(veiculo=v)
        r = self.client.get(f"/conversas/{l.pk}/simular/").json()
        self.assertIn("Simulação do Fiat Argo 2022", r["texto"])
        self.assertIn("R$ 59.900", r["texto"])
        self.assertIn("estimados", r["texto"])
        self.assertIn("erro", self.client.get(f"/conversas/{lead(telefone='5581911110000').pk}/simular/").json())
        loc = self.client.get(f"/conversas/{l.pk}/localizacao/").json()["texto"]
        self.assertIn("Shopping do Automóvel", loc)
        self.assertIn("google.com/maps", loc)

    def test_nova_conversa(self):
        self.assertContains(self.client.get("/conversas/nova/"), "Nova conversa")
        r = self.client.post("/conversas/nova/", {"telefone": "(81) 99999-1234", "nome": "Cliente Novo"})
        l = Lead.objects.get(telefone="5581999991234")
        self.assertEqual((r.status_code, r["HX-Redirect"]), (204, f"/conversas/?lead={l.pk}"))
        self.client.post("/conversas/nova/", {"telefone": "81999991234"})                    # já existe: abre o mesmo
        self.assertEqual(Lead.objects.filter(telefone="5581999991234").count(), 1)
        self.assertContains(self.client.post("/conversas/nova/", {"telefone": "123"}), "telefone válido")
        self.assertContains(self.client.post("/conversas/nova/", {"telefone": "81988887777"}), "nome")

    def test_eventos_do_funil_aparecem_na_conversa(self):
        from captacao import services
        l = lead()
        msg(l, quando=timezone.now() - timedelta(minutes=5))
        services.mudar_etapa(l, "atendimento", self.user)
        html = self.client.get(f"/conversas/{l.pk}/mensagens/").content.decode()
        self.assertIn("Etapa: Novo → Em atendimento", html)

    def test_barra_do_funil_lista_as_etapas_do_funil(self):
        l = lead()
        msg(l)
        funil.criar("Aguardando crédito")
        html = self.client.get(f"/conversas/{l.pk}/").content.decode()
        for nome in ("Em atendimento", "Aguardando crédito", "Convertido", "Perdido"):
            self.assertIn(nome, html)
        funil.invalidar()

    @override_settings(**API)
    @mock.patch("captacao.whatsapp.requests.post")
    def test_enviar_arquivo(self, post):
        from django.core.files.uploadedfile import SimpleUploadedFile
        post.side_effect = [mock.Mock(status_code=200, json=lambda: {"id": "MEDIA1"}), resposta_ok("w9")]
        l = lead()
        msg(l)
        self.client.post(f"/conversas/{l.pk}/arquivo/", {"arquivo": SimpleUploadedFile("foto.jpg", b"\xff\xd8x", content_type="image/jpeg")})
        m = l.mensagens_wa.filter(direcao="out").get()
        self.assertEqual((m.tipo, m.midia_id, m.wamid), ("image", "MEDIA1", "w9"))
        self.assertEqual(post.call_args.kwargs["json"]["image"], {"id": "MEDIA1"})
        self.assertIn("/media", post.call_args_list[0].args[0])

    @override_settings(**API)
    @mock.patch("captacao.whatsapp.requests.post")
    def test_arquivo_fora_da_janela_e_muito_grande(self, post):
        from django.core.files.uploadedfile import SimpleUploadedFile
        l = lead()
        msg(l, quando=timezone.now() - timedelta(hours=30))
        r = self.client.post(f"/conversas/{l.pk}/arquivo/", {"arquivo": SimpleUploadedFile("a.pdf", b"x", content_type="application/pdf")})
        self.assertContains(r, "24 h")
        post.assert_not_called()

    def test_agenda_preseleciona_o_contato_da_conversa(self):
        l = lead()
        html = self.client.get(f"/agenda/?lead={l.pk}").content.decode()
        self.assertIn(f'value="{l.pk}" selected', html)
