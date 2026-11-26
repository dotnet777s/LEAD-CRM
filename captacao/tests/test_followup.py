import hashlib
import hmac
import json
from datetime import datetime, timedelta, timezone as tz
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone

from captacao import followup, services, whatsapp
from captacao.models import FollowUp, FollowUpConfig, FollowUpPasso, Historico, Lead, WhatsAppMensagem

from .helpers import novo_veiculo


def novo_lead(**kw):
    dados = dict(nome="Maria Souza", telefone="5581999998888", carro_texto="Fiat Argo 2022 · R$ 59.900", origem=Lead.Origem.PORTAL)
    dados.update(kw)
    return Lead.objects.create(**dados)


# segunda-feira 10h (America/Recife) em UTC: 13h
SEGUNDA_10H = datetime(2026, 11, 23, 13, 0, tzinfo=tz.utc)


class MotorTests(TestCase):
    def test_primeiro_contato_inicia_e_agenda_uma_hora_depois(self):
        l = novo_lead()
        fu = followup.iniciar(l, SEGUNDA_10H)
        self.assertEqual(fu.passo_atual, 1)
        self.assertEqual(fu.proximo_em, SEGUNDA_10H + timedelta(hours=1))

    def test_fora_do_horario_vai_para_a_manha_seguinte(self):
        l = novo_lead()
        noite = datetime(2026, 11, 23, 23, 30, tzinfo=tz.utc)      # 20:30 em Recife
        fu = followup.iniciar(l, noite)
        loc = timezone.localtime(fu.proximo_em)
        self.assertEqual((loc.day, loc.hour), (24, 8))

    def test_domingo_pula_para_segunda(self):
        l = novo_lead()
        sab = datetime(2026, 11, 28, 22, 0, tzinfo=tz.utc)         # sábado 19h em Recife
        fu = followup.iniciar(l, sab)
        self.assertEqual(timezone.localtime(fu.proximo_em).weekday(), 0)

    def test_nao_duplica_e_respeita_config_desligada(self):
        l = novo_lead()
        a = followup.iniciar(l, SEGUNDA_10H)
        self.assertIsNone(followup.iniciar(l, SEGUNDA_10H))
        self.assertEqual(l.followups.count(), 1)
        cfg = FollowUpConfig.obter()
        cfg.ativo = False
        cfg.save()
        self.assertIsNone(followup.iniciar(novo_lead(telefone="5581999990000"), SEGUNDA_10H))
        self.assertTrue(a)

    def test_sequencia_ate_esgotar(self):
        l = novo_lead()
        fu = followup.iniciar(l, SEGUNDA_10H)
        agora = SEGUNDA_10H
        for n in range(1, 5):
            agora = fu.proximo_em
            self.assertIsNotNone(followup.registrar_tentativa(fu, agora=agora))
            fu.refresh_from_db()
        self.assertEqual(fu.status, FollowUp.Status.ESGOTADO)
        self.assertEqual(fu.tentativas.count(), 4)
        self.assertIsNone(fu.proximo_em)

    def test_proxima_tentativa_e_no_dia_seguinte_as_10h(self):
        fu = followup.iniciar(novo_lead(), SEGUNDA_10H)
        followup.registrar_tentativa(fu, agora=fu.proximo_em)
        fu.refresh_from_db()
        loc = timezone.localtime(fu.proximo_em)
        self.assertEqual((loc.day, loc.hour), (24, 10))

    def test_cliente_respondeu_para_e_credita_a_variante(self):
        l = novo_lead()
        fu = followup.iniciar(l, SEGUNDA_10H)
        followup.registrar_tentativa(fu, agora=fu.proximo_em)
        self.assertEqual(followup.cliente_respondeu(l), 1)
        fu.refresh_from_db()
        self.assertEqual(fu.status, FollowUp.Status.RESPONDEU)
        self.assertTrue(fu.tentativas.get().respondeu)
        self.assertEqual(followup.desempenho()[0]["respostas"], 1)

    def test_avancar_de_etapa_encerra(self):
        l = novo_lead()
        followup.iniciar(l, SEGUNDA_10H)
        services.mudar_etapa(l, Lead.Etapa.QUALIFICADO)
        self.assertEqual(l.followups.get().status, FollowUp.Status.ENCERRADO)

    def test_mensagem_usa_primeiro_nome_e_carro(self):
        fu = followup.iniciar(novo_lead(nome="maria aparecida souza"), SEGUNDA_10H)
        txt = followup.montar_mensagem(fu)
        self.assertIn("Maria", txt)
        self.assertNotIn("Aparecida", txt)
        self.assertIn("Fiat Argo 2022", txt)
        self.assertNotIn("{", txt)

    def test_pausar_e_retomar_e_pular(self):
        fu = followup.iniciar(novo_lead(), SEGUNDA_10H)
        followup.pausar(fu)
        self.assertEqual(fu.status, FollowUp.Status.PAUSADO)
        followup.retomar(fu, agora=SEGUNDA_10H)
        self.assertEqual(fu.status, FollowUp.Status.ATIVO)
        followup.pular(fu, agora=SEGUNDA_10H)
        self.assertEqual(fu.passo_atual, 2)

    def test_fila_ordena_quentes_primeiro(self):
        a = novo_lead(telefone="5581999991111", classe="C")
        b = novo_lead(telefone="5581999992222", classe="A")
        fa, fb = followup.iniciar(a, SEGUNDA_10H), followup.iniciar(b, SEGUNDA_10H)
        agora = SEGUNDA_10H + timedelta(hours=3)
        self.assertEqual([f.lead_id for f in followup.devidos(agora)], [b.pk, a.pk])
        self.assertTrue(fa and fb)


class TelasTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("sdr", password="x", is_staff=True)
        self.client.force_login(self.user)

    def test_telas_abrem(self):
        l = novo_lead()
        followup.iniciar(l, timezone.now() - timedelta(hours=3))
        for url in ("/", "/followup/", "/followup/lista/", "/followup/configurar/", "/portais/", f"/lead/{l.pk}/"):
            self.assertEqual(self.client.get(url).status_code, 200, url)
        self.assertIn("Maria", self.client.get("/followup/lista/").content.decode())

    def test_acoes_do_follow_up(self):
        l = novo_lead()
        fu = followup.iniciar(l, timezone.now() - timedelta(hours=3))
        r = self.client.post(f"/followup/{fu.pk}/enviei/")
        self.assertEqual(r.status_code, 204)
        fu.refresh_from_db()
        self.assertEqual(fu.passo_atual, 2)
        self.assertEqual(self.client.post(f"/followup/{fu.pk}/respondeu/").status_code, 204)
        fu.refresh_from_db()
        self.assertEqual(fu.status, FollowUp.Status.RESPONDEU)
        self.assertEqual(self.client.post(f"/followup/{fu.pk}/xyz/").status_code, 404)

    def test_card_do_quadro_mostra_selo(self):
        l = novo_lead(etapa=Lead.Etapa.ATENDIMENTO, portal="olx")
        followup.iniciar(l, timezone.now() - timedelta(hours=3))
        html = self.client.get("/quadro/").content.decode()
        self.assertIn("Follow-up", html)
        self.assertIn("OLX", html)

    def test_configurar_salva_e_exige_gestor(self):
        followup.garantir_passos()
        p1 = FollowUpPasso.objects.get(ordem=1)
        dados = {"ativo": "on", "primeiro_nome": "on", "hora_inicio": "9", "hora_fim": "19", f"p{p1.pk}_ativo": "on",
                 f"p{p1.pk}_horas": "2", f"p{p1.pk}_dias": "0", f"p{p1.pk}_v0": "Oi {nome}", f"p{p1.pk}_v1": "", f"p{p1.pk}_v2": ""}
        for p in FollowUpPasso.objects.exclude(pk=p1.pk):
            dados[f"p{p.pk}_ativo"] = "on"
            dados[f"p{p.pk}_dias"] = "1"
            dados[f"p{p.pk}_v0"] = "Texto"
        r = self.client.post("/followup/configurar/", dados)
        self.assertContains(r, "Configuração salva")
        p1.refresh_from_db()
        self.assertEqual((p1.espera_horas, p1.variantes, FollowUpConfig.obter().hora_inicio), (2, ["Oi {nome}"], 9))
        comum = get_user_model().objects.create_user("vend", password="x")
        self.client.force_login(comum)
        self.assertEqual(self.client.get("/followup/configurar/").status_code, 403)


SEGREDO = "segredo-teste"


def assinar(corpo):
    return "sha256=" + hmac.new(SEGREDO.encode(), corpo, hashlib.sha256).hexdigest()


def evento_msg(de="558199998888", texto="Oi, ainda tem o Argo?", wamid="wamid.1"):
    return {"entry": [{"changes": [{"value": {"contacts": [{"wa_id": de, "profile": {"name": "Cliente Teste"}}],
                                              "messages": [{"from": de, "id": wamid, "type": "text", "text": {"body": texto}}]}}]}]}


@override_settings(WHATSAPP_APP_SECRET=SEGREDO, WHATSAPP_VERIFY_TOKEN="tok", WHATSAPP_PHONE_ID="123", WHATSAPP_TOKEN="t")
class WhatsAppTests(TestCase):
    def post(self, payload, assinatura=None):
        corpo = json.dumps(payload).encode()
        return self.client.post("/whatsapp/webhook/", corpo, content_type="application/json",
                                HTTP_X_HUB_SIGNATURE_256=assinatura if assinatura is not None else assinar(corpo))

    def test_verificacao_do_webhook(self):
        ok = self.client.get("/whatsapp/webhook/", {"hub.mode": "subscribe", "hub.verify_token": "tok", "hub.challenge": "999"})
        self.assertEqual((ok.status_code, ok.content), (200, b"999"))
        self.assertEqual(self.client.get("/whatsapp/webhook/", {"hub.mode": "subscribe", "hub.verify_token": "x"}).status_code, 403)

    def test_assinatura_invalida_recusa(self):
        self.assertEqual(self.post(evento_msg(), "sha256=errado").status_code, 403)
        self.assertEqual(WhatsAppMensagem.objects.count(), 0)

    def test_mensagem_do_cliente_para_o_follow_up(self):
        l = novo_lead(telefone="5581999998888")
        fu = followup.iniciar(l, SEGUNDA_10H)
        self.assertEqual(self.post(evento_msg()).status_code, 200)     # 558199998888 = mesmo número sem o 9
        fu.refresh_from_db()
        self.assertEqual(fu.status, FollowUp.Status.RESPONDEU)
        m = WhatsAppMensagem.objects.get()
        self.assertEqual((m.lead_id, m.direcao), (l.pk, "in"))
        self.assertTrue(Historico.objects.filter(lead=l, texto__icontains="respondeu").exists())

    def test_reenvio_do_webhook_nao_duplica(self):
        novo_lead()
        self.post(evento_msg())
        self.post(evento_msg())
        self.assertEqual(WhatsAppMensagem.objects.count(), 1)

    def test_numero_desconhecido_vira_lead(self):
        r = self.post(evento_msg(de="5581977776666", wamid="wamid.9"))
        self.assertEqual(r.status_code, 200)
        l = Lead.objects.get()
        self.assertEqual((l.origem, l.nome, l.telefone), (Lead.Origem.WHATSAPP, "Cliente Teste", "5581977776666"))

    def test_status_de_entrega_atualiza(self):
        l = novo_lead()
        WhatsAppMensagem.objects.create(lead=l, telefone=l.telefone, direcao="out", wamid="w.out", status="sent")
        self.post({"entry": [{"changes": [{"value": {"statuses": [{"id": "w.out", "status": "read"}]}}]}]})
        self.assertEqual(WhatsAppMensagem.objects.get().status, "read")

    @mock.patch("captacao.whatsapp.requests.post")
    def test_envio_so_dentro_da_janela_de_24h(self, post):
        post.return_value = mock.Mock(status_code=200, json=lambda: {"messages": [{"id": "w1"}]})
        l = novo_lead()
        with self.assertRaises(whatsapp.JanelaFechada):
            whatsapp.enviar_texto(l, "Olá")
        WhatsAppMensagem.objects.create(lead=l, telefone=l.telefone, direcao="in", texto="oi")
        m = whatsapp.enviar_texto(l, "Olá")
        self.assertEqual((m.wamid, m.status), ("w1", "sent"))
        self.assertEqual(post.call_args.kwargs["json"]["text"]["body"], "Olá")

    @mock.patch("captacao.whatsapp.requests.post")
    def test_followup_automatico_usa_template_fora_da_janela(self, post):
        post.return_value = mock.Mock(status_code=200, json=lambda: {"messages": [{"id": "w2"}]})
        l = novo_lead()
        fu = followup.iniciar(l, timezone.now() - timedelta(hours=3))
        self.assertFalse(whatsapp.enviar_followup(fu))                  # sem janela e sem template: fica para a equipe
        FollowUpPasso.objects.filter(ordem=1).update(template_nome="followup_1")
        self.assertTrue(whatsapp.enviar_followup(fu))
        self.assertEqual(post.call_args.kwargs["json"]["template"]["name"], "followup_1")
        fu.refresh_from_db()
        self.assertEqual(fu.passo_atual, 2)

    @mock.patch("captacao.whatsapp.requests.post")
    def test_automatico_so_com_a_opcao_ligada(self, post):
        followup.iniciar(novo_lead(), timezone.now() - timedelta(hours=3))
        self.assertEqual(whatsapp.rodar_followup_automatico(), 0)
        post.assert_not_called()

    @override_settings(WHATSAPP_APP_SECRET="")
    def test_sem_segredo_recusa_tudo(self):
        self.assertEqual(self.post(evento_msg(), "sha256=qualquer").status_code, 403)

    def test_api_desligada_por_padrao(self):
        with override_settings(WHATSAPP_PHONE_ID="", WHATSAPP_TOKEN=""):
            self.assertFalse(whatsapp.configurado())
            with self.assertRaises(whatsapp.WhatsAppErro):
                whatsapp.enviar_texto(novo_lead(), "x")

    def test_veiculo_helper_importado(self):
        self.assertTrue(novo_veiculo())
