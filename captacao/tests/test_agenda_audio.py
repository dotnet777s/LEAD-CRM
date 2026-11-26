import subprocess
from datetime import timedelta
from unittest import mock

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from django.utils import timezone

from captacao import agendamentos, audio, whatsapp
from captacao.models import (AgendaConfig, Agendamento, AgendamentoMensagem, Lead, ModeloWhatsApp, Profissional, WhatsAppMensagem)

from .test_central import API, Logado, lead, msg, resposta_ok


def agendar(client, l, horas=48, **extra):
    inicio = timezone.localtime(timezone.now() + timedelta(hours=horas)).strftime("%Y-%m-%dT%H:%M")
    return client.post("/agenda/novo/", {"lead": l.pk, "inicio": inicio, **extra})


@override_settings(**API)
class MensagensDoAgendamentoTests(Logado):
    @mock.patch("captacao.whatsapp.requests.post")
    def test_marcar_envia_a_confirmacao_e_programa_lembretes_e_agradecimento(self, post):
        post.return_value = resposta_ok()
        l = lead()
        msg(l)                                                         # janela aberta
        agendar(self.client, l, 72)
        a = Agendamento.objects.get()
        tipos = {m.tipo: m for m in a.mensagens.all()}
        self.assertEqual(set(tipos), {"confirmacao", "lembrete_24h", "lembrete_2h", "pos"})
        self.assertEqual(tipos["confirmacao"].status, "enviada")
        enviado = post.call_args.kwargs["json"]["text"]["body"]
        self.assertIn("Olá, Maria!", enviado)
        self.assertIn("Shopping do Automóvel", enviado)
        self.assertEqual((tipos["lembrete_24h"].status, tipos["pos"].status), ("pendente", "pendente"))
        self.assertEqual(tipos["lembrete_24h"].enviar_em, a.inicio - timedelta(hours=24))

    @mock.patch("captacao.whatsapp.requests.post")
    def test_fora_da_janela_usa_o_modelo_aprovado_ou_avisa(self, post):
        post.return_value = resposta_ok()
        l = lead()
        agendar(self.client, l, 72)                                    # sem mensagem do cliente: janela fechada
        m = AgendamentoMensagem.objects.get(tipo="confirmacao")
        self.assertEqual(m.status, "falhou")
        self.assertIn("sem modelo aprovado", m.erro)
        mod = ModeloWhatsApp.objects.create(nome="confirma_visita", status="APPROVED", corpo="Olá, {{1}}! Visita {{2}} às {{3}}.",
                                            n_variaveis=3, variaveis=["nome", "dia_semana", "hora"])
        cfg = AgendaConfig.obter()
        cfg.modelo_confirmacao = mod
        cfg.save()
        self.client.post(f"/agenda/mensagem/{m.pk}/reenviar/")
        m.refresh_from_db()
        self.assertEqual(m.status, "enviada")
        params = post.call_args.kwargs["json"]["template"]["components"][0]["parameters"]
        self.assertEqual(params[0]["text"], "Maria")
        self.assertIn("-feira", params[1]["text"] + "-feira")

    @mock.patch("captacao.whatsapp.requests.post")
    def test_lembrete_so_sai_na_hora_e_agendamento_cancelado_nao_envia(self, post):
        post.return_value = resposta_ok()
        l = lead()
        msg(l)
        agendar(self.client, l, 72)
        a = Agendamento.objects.get()
        self.assertEqual(agendamentos.rodar(), 0)                       # lembretes ainda não venceram
        AgendamentoMensagem.objects.filter(tipo="lembrete_24h").update(enviar_em=timezone.now() - timedelta(minutes=1))
        self.assertEqual(agendamentos.rodar(), 1)
        post.reset_mock()
        self.client.post(f"/agenda/{a.pk}/cancelado/")
        AgendamentoMensagem.objects.filter(tipo="lembrete_2h").update(enviar_em=timezone.now() - timedelta(minutes=1))
        self.assertEqual(agendamentos.rodar(), 0)
        post.assert_not_called()
        self.assertEqual(AgendamentoMensagem.objects.get(tipo="lembrete_2h").status, "cancelada")

    @mock.patch("captacao.whatsapp.requests.post")
    def test_agradecimento_so_depois_de_realizado(self, post):
        post.return_value = resposta_ok()
        l = lead()
        msg(l)
        agendar(self.client, l, 3)
        a = Agendamento.objects.get()
        AgendamentoMensagem.objects.filter(tipo="pos").update(enviar_em=timezone.now() - timedelta(minutes=1))
        self.assertEqual(agendamentos.rodar(), 0)                       # equipe ainda não marcou "realizado"
        self.client.post(f"/agenda/{a.pk}/realizado/")
        self.assertEqual(agendamentos.rodar(), 1)
        self.assertIn("Obrigado pela visita", post.call_args.kwargs["json"]["text"]["body"])

    def test_sem_api_fica_programada_com_link_do_celular(self):
        with override_settings(WHATSAPP_PHONE_ID="", WHATSAPP_TOKEN=""):
            l = lead()
            agendar(self.client, l, 72)
            m = AgendamentoMensagem.objects.get(tipo="confirmacao")
            self.assertEqual(m.status, "pendente")
            html = self.client.get("/agenda/?s=0").content.decode() + self.client.get("/agenda/?s=1").content.decode()
            self.assertIn("wa.me/5581999998888?text=", html)
            self.client.post(f"/agenda/mensagem/{m.pk}/marcar_enviada/")
            m.refresh_from_db()
            self.assertEqual(m.status, "enviada")

    def test_nao_contatar_nao_recebe(self):
        l = lead(nao_contatar=True)
        msg(l)
        agendar(self.client, l, 72)
        self.assertEqual(AgendamentoMensagem.objects.get(tipo="confirmacao").status, "cancelada")

    def test_configurar_as_mensagens(self):
        self.assertContains(self.client.get("/agenda/mensagens/"), "Confirmação ao agendar")
        self.client.post("/agenda/mensagens/", {"txt_confirmacao": "Oi {nome}, te esperamos {dia}!", "txt_lembrete": "L", "txt_pos": "P", "lembrete_2h": "on"})
        c = AgendaConfig.obter()
        self.assertEqual((c.txt_confirmacao, c.confirmar, c.lembrete_24h, c.lembrete_2h, c.pos_visita), ("Oi {nome}, te esperamos {dia}!", False, False, True, False))

    @mock.patch("captacao.whatsapp.requests.post")
    def test_nada_de_lembrete_passado(self, post):
        post.return_value = resposta_ok()
        l = lead()
        msg(l)
        agendar(self.client, l, 1)                                      # daqui a 1 h: sem lembrete de 24 h nem de 2 h
        self.assertEqual(set(AgendamentoMensagem.objects.values_list("tipo", flat=True)), {"confirmacao", "pos"})


class AudioTests(Logado):
    def test_ogg_opus_passa_direto(self):
        self.assertEqual(audio.para_ogg_opus(b"abc", "audio/ogg;codecs=opus"), b"abc")

    def test_converte_webm_para_ogg_com_ffmpeg_de_verdade(self):
        exe = audio._ffmpeg()
        if not exe:
            self.skipTest("sem ffmpeg")
        webm = subprocess.run([exe, "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", "sine=frequency=440:duration=1", "-c:a", "libopus",
                               "-f", "webm", "pipe:1"], capture_output=True).stdout
        ogg = audio.para_ogg_opus(webm, "audio/webm;codecs=opus")
        self.assertTrue(ogg.startswith(b"OggS"))

    def test_audio_corrompido_da_erro(self):
        if not audio._ffmpeg():
            self.skipTest("sem ffmpeg")
        with self.assertRaises(audio.ErroAudio):
            audio.para_ogg_opus(b"isto nao e audio", "audio/webm")

    @override_settings(**API)
    @mock.patch("captacao.whatsapp.requests.post")
    def test_audio_gravado_e_convertido_e_enviado_como_ogg(self, post):
        post.side_effect = [mock.Mock(status_code=200, json=lambda: {"id": "AUD1"}), resposta_ok("wa1")]
        l = lead()
        msg(l)
        with mock.patch("captacao.audio.para_ogg_opus", return_value=b"OggSfake") as conv:
            self.client.post(f"/conversas/{l.pk}/arquivo/", {"arquivo": SimpleUploadedFile("audio.webm", b"webm", content_type="audio/webm"), "gravado": "1"})
        conv.assert_called_once()
        m = l.mensagens_wa.filter(direcao="out").get()
        self.assertEqual((m.tipo, m.midia_id, m.midia_tipo), ("audio", "AUD1", "audio/ogg"))
        self.assertEqual(post.call_args_list[0].kwargs["files"]["file"][2], "audio/ogg")
        self.assertEqual(post.call_args.kwargs["json"]["audio"], {"id": "AUD1"})

    @override_settings(**API)
    def test_erro_de_conversao_aparece_para_o_usuario(self):
        l = lead()
        msg(l)
        with mock.patch("captacao.audio.para_ogg_opus", side_effect=audio.ErroAudio("Não consegui converter o áudio gravado.")):
            r = self.client.post(f"/conversas/{l.pk}/arquivo/", {"arquivo": SimpleUploadedFile("a.webm", b"x", content_type="audio/webm"), "gravado": "1"})
        self.assertContains(r, "converter o áudio")

    @override_settings(**API)
    def test_conversa_mostra_microfone_e_previa_de_anexo(self):
        l = lead()
        msg(l)
        html = self.client.get(f"/conversas/{l.pk}/").content.decode()
        for trecho in ('id="mic"', 'id="anexo-prev"', 'data-anexo="image/*,video/*"', 'id="gravando"'):
            self.assertIn(trecho, html)

    def test_filtro_sem_conversa_lista_leads_sem_mensagens(self):
        a = lead(nome="OLX novo", portal="olx")
        b = lead(nome="Já conversa", telefone="5581911110000")
        msg(b)
        c = lead(nome="Vendido", telefone="5581922220000", etapa="vendido")
        html = self.client.get("/conversas/lista/?f=sem_conversa").content.decode()
        self.assertIn("OLX novo", html)
        self.assertNotIn("Já conversa", html)
        self.assertNotIn("Vendido", html)
        self.assertTrue(a and c)
