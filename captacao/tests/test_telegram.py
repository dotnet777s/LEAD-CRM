from datetime import timedelta
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import override_settings
from django.utils import timezone

from captacao import services, telegram
from captacao.models import Lead, NotificacaoTelegram, TelegramVinculo

from .helpers import FORM_OK, Base, novo_veiculo
from .test_funil import criar_lead

U = get_user_model()
BOT = override_settings(TELEGRAM_BOT_TOKEN="123:abc", TELEGRAM_SLA_MINUTOS=5)


def ligar(user, chat_id):
    return TelegramVinculo.objects.create(user=user, chat_id=chat_id)


@BOT
class TelegramTests(Base):
    def setUp(self):
        super().setUp()
        self.gestor = U.objects.create_user("gestor", password="x12345678", is_staff=True, first_name="Gestor")
        self.caio = U.objects.create_user("caio", password="x12345678", first_name="Caio")
        self.ana = U.objects.create_user("ana", password="x12345678", first_name="Ana")
        self.v = novo_veiculo()
        ligar(self.gestor, 1), ligar(self.caio, 2), ligar(self.ana, 3)

    def test_vinculo_por_codigo(self):
        novo = U.objects.create_user("bia", password="x12345678", first_name="Bia")
        codigo = telegram.gerar_codigo(novo)
        with mock.patch("captacao.telegram.api") as api:
            telegram.tratar_update({"message": {"chat": {"id": 99, "type": "private"}, "text": f"/start {codigo}"}})
        self.assertEqual(telegram.usuario_do_chat(99), novo)
        self.assertIn("Pronto", api.call_args.kwargs["text"])
        self.assertIsNone(telegram.vincular(codigo, 100))             # código usado uma vez só

    def test_codigo_expirado_e_invalido(self):
        novo = U.objects.create_user("bia", password="x12345678")
        codigo = telegram.gerar_codigo(novo, agora=timezone.now() - timedelta(hours=1))
        self.assertIsNone(telegram.vincular(codigo, 99))
        self.assertIsNone(telegram.vincular("XXXX", 99))
        self.assertIsNone(telegram.usuario_do_chat(99))

    def test_lead_novo_avisa_o_sdr_e_nao_duplica(self):
        lead = criar_lead(self.v, vendedor=None, respostas={"pagamento": "financiado", "entrada": "10_30", "troca": "nao", "prazo": "semana"})
        self.assertEqual(telegram.avisar_novo_lead(lead), 3)
        self.assertEqual(telegram.avisar_novo_lead(lead), 0)
        n = NotificacaoTelegram.objects.filter(chat_id=2).get()
        self.assertIn("NOVO LEAD", n.texto)
        self.assertIn("Maria Silva", n.texto)
        self.assertIn("Pagamento:", n.texto)
        textos = [b["text"] for b in n.botoes[0]]
        self.assertEqual(textos, ["Chamar no WhatsApp", "Abrir no CRM"])
        self.assertNotIn("callback_data", str(n.botoes))

    def test_lead_com_vendedor_mostra_o_nome(self):
        lead = criar_lead(self.v, vendedor=self.caio)
        self.assertIn("Vendedor: Caio", telegram.texto_lead(lead, "Novo lead"))

    def test_captura_pela_pagina_enfileira_aviso(self):
        self.client.post(f"/v/{self.v.pk}-fiat-argo-2022/", {**FORM_OK, "event_id": "a" * 32}, HTTP_USER_AGENT="Mozilla/5.0")
        self.assertEqual(NotificacaoTelegram.objects.filter(tipo="novo").count(), 3)

    def test_sem_token_nao_enfileira(self):
        with override_settings(TELEGRAM_BOT_TOKEN=""):
            self.assertEqual(telegram.avisar_novo_lead(criar_lead(self.v)), 0)

    def test_fila_envia_e_trata_chat_bloqueado(self):
        lead = criar_lead(self.v)
        telegram.avisar_novo_lead(lead)
        with mock.patch("captacao.telegram.api", return_value={"ok": True}) as api:
            self.assertEqual(telegram.processar_fila(), 3)
        self.assertEqual(api.call_args.kwargs["parse_mode"], "HTML")
        self.assertFalse(NotificacaoTelegram.objects.exclude(status="enviado").exists())
        n = telegram.enfileirar(5, "x", chave="k")[0]
        with mock.patch("captacao.telegram.api", return_value={"ok": False, "error_code": 403, "description": "blocked"}):
            telegram.processar_fila()
        n.refresh_from_db()
        self.assertEqual((n.status, n.motivo), ("erro", "blocked"))

    def test_botao_antigo_assumir_esta_desativado(self):
        lead = criar_lead(self.v, vendedor=None)
        with mock.patch("captacao.telegram.api") as api:
            telegram.tratar_update({"callback_query": {"id": "1", "data": f"assumir:{lead.pk}", "message": {"chat": {"id": 2}}}})
        lead.refresh_from_db()
        self.assertIsNone(lead.vendedor)
        self.assertIn("desativada", api.call_args.kwargs["text"])

    def test_comando_leads_lista_os_novos(self):
        criar_lead(self.v, vendedor=None)
        with mock.patch("captacao.telegram.api") as api:
            telegram.tratar_update({"message": {"chat": {"id": 2, "type": "private"}, "text": "/leads"}})
        self.assertIn("AGUARDANDO ENCAMINHAMENTO", api.call_args.kwargs["text"])

    def test_lembrete_de_retorno_uma_vez(self):
        agora = timezone.now()
        lead = criar_lead(self.v, vendedor=self.caio, proximo_contato=agora - timedelta(minutes=2))
        self.assertEqual(telegram.lembretes_de_retorno(agora), 3)
        self.assertEqual(telegram.lembretes_de_retorno(agora), 0)
        Lead.objects.filter(pk=lead.pk).update(etapa="vendido")
        NotificacaoTelegram.objects.all().delete()
        self.assertEqual(telegram.lembretes_de_retorno(agora), 0)

    def test_alerta_de_lead_parado(self):
        agora = timezone.now()
        lead = criar_lead(self.v, vendedor=self.caio)
        Lead.objects.filter(pk=lead.pk).update(criado_em=agora - timedelta(minutes=10))
        self.assertEqual(telegram.alertas_lead_parado(agora), 3)
        self.assertEqual(telegram.alertas_lead_parado(agora), 0)
        fresco = criar_lead(self.v, telefone="5581900000000")
        self.assertEqual(telegram.alertas_lead_parado(agora), 0)
        self.assertIsNotNone(fresco)

    def test_equipe_gera_codigo(self):
        self.client.force_login(self.gestor)
        novo = U.objects.create_user("bia", password="x12345678")
        r = self.client.post(f"/equipe/{novo.pk}/telegram/", follow=True)
        self.assertEqual(r.status_code, 200)
        self.assertTrue(TelegramVinculo.objects.get(user=novo).codigo)
        self.assertIn("/start", r.content.decode())
        self.client.force_login(self.caio)
        self.assertEqual(self.client.post(f"/equipe/{novo.pk}/telegram/").status_code, 403)
