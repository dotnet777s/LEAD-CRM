from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import override_settings
from django.utils import timezone

from captacao import meta, telegram
from captacao.models import EventoFunil, EventoMeta, Historico, Lead, NotificacaoTelegram, TelegramVinculo

from .helpers import FORM_OK, Base, novo_veiculo

UA = {"HTTP_USER_AGENT": "Mozilla/5.0"}
CONTATO = {"nome": "Maria Silva", "telefone": "(81) 99999-8888", "lgpd": "1"}
BOT = override_settings(TELEGRAM_BOT_TOKEN="123:abc", TELEGRAM_REQUALIFICAR_MINUTOS=10)


def iniciar(client, v, ip="200.1.1.1", **extra):
    return client.post(f"/v/{v.pk}/inicio/", {**CONTATO, **extra}, HTTP_X_FORWARDED_FOR=ip, **UA)


@BOT
class LeadIncompletoTests(Base):
    def setUp(self):
        super().setUp()
        self.v = novo_veiculo()
        self.sdr = get_user_model().objects.create_user("sdr", password="x12345678", is_staff=True)
        TelegramVinculo.objects.create(user=self.sdr, chat_id=1)

    def test_contato_deixado_cria_lead_na_coluna_requalificar_sem_avisar_na_hora(self):
        r = iniciar(self.client, self.v)
        self.assertEqual(r.status_code, 200)
        lead = Lead.objects.get()
        self.assertEqual(lead.etapa, Lead.Etapa.REQUALIFICAR)
        self.assertTrue(lead.incompleto)
        self.assertTrue(lead.consent_meta)
        self.assertEqual(NotificacaoTelegram.objects.count(), 0)            # só avisa depois de alguns minutos
        self.assertIn("Começou o formulário", lead.historico.first().texto)
        self.assertEqual(EventoFunil.objects.filter(etapa="contato").count(), 1)
        self.assertEqual(EventoMeta.objects.filter(nome="Lead").count(), 0)  # o Lead da Meta só no fim do formulário

    def test_contato_invalido_ou_sem_aceite_nao_cria(self):
        self.assertEqual(iniciar(self.client, self.v, telefone="123").status_code, 400)
        r = self.client.post(f"/v/{self.v.pk}/inicio/", {"nome": "Ana", "telefone": "(81) 99999-8888"}, **UA)
        self.assertEqual(r.status_code, 400)
        self.assertEqual(Lead.objects.count(), 0)

    def test_honeypot_e_limite_por_ip(self):
        iniciar(self.client, self.v, website="http://spam")
        self.assertEqual(Lead.objects.count(), 0)
        for i in range(10):
            iniciar(self.client, self.v, ip="200.9.9.9", telefone=f"(81) 99999-77{i:02d}")
        self.assertEqual(iniciar(self.client, self.v, ip="200.9.9.9", telefone="(81) 99999-7799").status_code, 429)

    def test_alerta_de_requalificacao_depois_de_10_minutos_uma_vez(self):
        iniciar(self.client, self.v)
        lead = Lead.objects.get()
        agora = timezone.now()
        self.assertEqual(telegram.alertas_requalificar(agora), 0)                       # ainda dentro dos 10 min
        Lead.objects.filter(pk=lead.pk).update(criado_em=agora - timedelta(minutes=12))
        self.assertEqual(telegram.alertas_requalificar(agora), 1)
        self.assertEqual(telegram.alertas_requalificar(agora), 0)                       # uma vez só
        n = NotificacaoTelegram.objects.get(tipo="requalificar")
        self.assertIn("REQUALIFICAR", n.texto)
        self.assertIn("não terminou o formulário", n.texto)
        botoes = n.botoes[0]
        self.assertEqual(botoes[0]["text"], "Requalificar no WhatsApp")
        self.assertIn("wa.me/5581999998888", botoes[0]["url"])
        self.assertIn("simula", botoes[0]["url"])                                       # mensagem pronta para retomar

    def test_terminar_antes_do_alerta_vira_lead_novo_comum(self):
        iniciar(self.client, self.v)
        r = self.client.post(f"/v/{self.v.pk}-fiat-argo-2022/", {**FORM_OK, "event_id": "a" * 32}, HTTP_X_FORWARDED_FOR="200.1.1.2", **UA)
        self.assertEqual(r.status_code, 302)
        lead = Lead.objects.get()                                                      # o mesmo lead, sem duplicar
        self.assertEqual(lead.etapa, Lead.Etapa.NOVO)
        self.assertFalse(lead.incompleto)
        self.assertEqual(lead.respostas["pagamento"], "a_vista")
        self.assertTrue(Historico.objects.filter(lead=lead, texto__startswith="Terminou o formulário").exists())
        n = NotificacaoTelegram.objects.get()
        self.assertIn("NOVO LEAD", n.texto)

    def test_terminar_depois_do_alerta_avisa_que_terminou(self):
        iniciar(self.client, self.v)
        Lead.objects.update(criado_em=timezone.now() - timedelta(minutes=30))
        telegram.alertas_requalificar()
        self.client.post(f"/v/{self.v.pk}-fiat-argo-2022/", {**FORM_OK, "event_id": "b" * 32}, HTTP_X_FORWARDED_FOR="200.1.1.3", **UA)
        textos = list(NotificacaoTelegram.objects.filter(tipo="completo").values_list("texto", flat=True))
        self.assertEqual(len(textos), 1)
        self.assertIn("TERMINOU O FORMULÁRIO", textos[0])

    def test_consultor_que_ja_atendeu_nao_e_movido_ao_terminar(self):
        iniciar(self.client, self.v)
        lead = Lead.objects.get()
        from captacao import services
        services.mudar_etapa(lead, Lead.Etapa.ATENDIMENTO, self.sdr)
        self.client.post(f"/v/{self.v.pk}-fiat-argo-2022/", {**FORM_OK, "event_id": "c" * 32}, HTTP_X_FORWARDED_FOR="200.1.1.4", **UA)
        lead.refresh_from_db()
        self.assertEqual(lead.etapa, Lead.Etapa.ATENDIMENTO)

    def test_lead_incompleto_aparece_no_quadro_em_requalificar(self):
        iniciar(self.client, self.v)
        self.client.force_login(self.sdr)
        html = self.client.get("/quadro/").content.decode()
        self.assertIn('data-etapa="requalificar"', html)
        self.assertIn("Requalificar", html)
        self.assertIn("Incompleto", html)

    def test_avancar_de_requalificar_vai_para_atendimento(self):
        iniciar(self.client, self.v)
        self.assertEqual(Lead.objects.get().proxima_etapa, Lead.Etapa.ATENDIMENTO)


class ConsentimentoTests(Base):
    def setUp(self):
        super().setUp()
        self.v = novo_veiculo()
        self.client.cookies.pop("tc_consent", None)     # visitante que ainda não escolheu

    def abrir(self):
        return self.client.get(f"/v/{self.v.pk}-fiat-argo-2022/", **UA)

    def test_sem_consentimento_nada_vai_para_a_meta_nem_cria_cookies_de_medicao(self):
        r = self.abrir()
        self.assertEqual(EventoMeta.objects.count(), 0)
        self.assertNotIn("tc_ab", r.cookies)
        self.assertNotIn("tc_sid", r.cookies)
        html = r.content.decode()
        self.assertIn("consent:false", html)
        self.assertIn('id="cookies"', html)
        self.assertNotIn("facebook.com/tr", html)                      # sem pixel de imagem escondido
        e = EventoFunil.objects.get()
        self.assertEqual((e.etapa, e.consent, e.sid, e.variante), ("view", False, "", "A"))

    def test_com_consentimento_enfileira_eventos_e_cria_cookies(self):
        self.client.cookies["tc_consent"] = "all"
        r = self.abrir()
        self.assertEqual(set(EventoMeta.objects.values_list("nome", flat=True)), {"PageView", "ViewContent"})
        self.assertIn("tc_ab", r.cookies)
        self.assertIn("tc_sid", r.cookies)
        self.assertIn("consent:true", r.content.decode())

    def test_recusou_nao_envia_lead_nem_contato_para_a_meta(self):
        self.client.cookies["tc_consent"] = "min"
        r = self.client.post(f"/v/{self.v.pk}-fiat-argo-2022/", {**FORM_OK, "event_id": "d" * 32}, HTTP_X_FORWARDED_FOR="200.2.2.2", **UA)
        self.assertEqual(r.status_code, 302)
        lead = Lead.objects.get()
        self.assertFalse(lead.consent_meta)
        self.assertEqual(EventoMeta.objects.count(), 0)
        self.client.post(f"/v/{self.v.pk}/contato/{lead.uuid}/", **UA)
        self.assertEqual(EventoMeta.objects.count(), 0)

    def test_lead_sem_consentimento_nao_gera_eventos_do_funil_de_vendas(self):
        self.client.cookies["tc_consent"] = "min"
        self.client.post(f"/v/{self.v.pk}-fiat-argo-2022/", {**FORM_OK, "event_id": "e" * 32}, HTTP_X_FORWARDED_FOR="200.2.2.3", **UA)
        lead = Lead.objects.get()
        self.assertIsNone(meta.enfileirar_evento_crm(lead, Lead.Etapa.QUALIFICADO))
        Lead.objects.filter(pk=lead.pk).update(consent_meta=True)
        lead.refresh_from_db()
        self.assertIsNotNone(meta.enfileirar_evento_crm(lead, Lead.Etapa.QUALIFICADO))

    def test_aceitar_depois_envia_os_eventos_da_visita_uma_vez(self):
        self.abrir()
        self.assertEqual(EventoMeta.objects.count(), 0)
        self.client.cookies["tc_consent"] = "all"                      # o navegador grava o cookie ao clicar em Aceitar
        for _ in range(2):
            r = self.client.post(f"/v/{self.v.pk}/consentir/", {"event_id": "f" * 32}, **UA)
            self.assertEqual(r.status_code, 200)
        self.assertEqual(set(EventoMeta.objects.values_list("event_id", flat=True)), {"f" * 32, "f" * 32 + "-vc"})

    def test_consentir_sem_cookie_ou_com_id_invalido_falha(self):
        self.assertEqual(self.client.post(f"/v/{self.v.pk}/consentir/", {"event_id": "f" * 32}, **UA).status_code, 400)
        self.client.cookies["tc_consent"] = "all"
        self.assertEqual(self.client.post(f"/v/{self.v.pk}/consentir/", {"event_id": "xx"}, **UA).status_code, 400)

    def test_pagina_obrigado_so_dispara_pixel_com_consentimento(self):
        self.client.post(f"/v/{self.v.pk}-fiat-argo-2022/", {**FORM_OK, "event_id": "9" * 32}, HTTP_X_FORWARDED_FOR="200.2.2.4", **UA)
        lead = Lead.objects.get()
        with override_settings(META_PIXEL_ID="123"):
            sem = self.client.get(f"/v/{self.v.pk}/obrigado/{lead.uuid}/").content.decode()
            self.client.cookies["tc_consent"] = "all"
            com = self.client.get(f"/v/{self.v.pk}/obrigado/{lead.uuid}/").content.decode()
        self.assertNotIn("fbq('track','Lead'", sem)
        self.assertIn("fbq('track','Lead'", com)


class TesteABTests(Base):
    def setUp(self):
        super().setUp()
        self.v = novo_veiculo()

    def abrir(self, **cookies):
        for k, val in cookies.items():
            self.client.cookies[k] = val
        return self.client.get(f"/v/{self.v.pk}-fiat-argo-2022/", **UA).content.decode()

    def test_variante_a_tem_os_botoes_atuais(self):
        html = self.abrir(tc_ab="A")
        self.assertIn('data-intencao="simular">Simular</a>', html)
        self.assertIn('data-intencao="visita">Agendar visita</a>', html)

    def test_variante_b_muda_o_texto_dos_botoes(self):
        html = self.abrir(tc_ab="B")
        self.assertIn('data-intencao="simular">Falar com consultor</a>', html)
        self.assertIn('data-intencao="visita">Ver na loja</a>', html)
        self.assertIn("Fale com um consultor no WhatsApp", html)

    def test_sem_consentimento_sempre_variante_a(self):
        self.client.cookies.pop("tc_consent", None)
        html = self.abrir(tc_ab="B")
        self.assertIn('data-intencao="simular">Simular</a>', html)

    def test_visitante_novo_recebe_variante_e_ela_fica_gravada(self):
        r = self.client.get(f"/v/{self.v.pk}-fiat-argo-2022/", **UA)
        self.assertIn(r.cookies["tc_ab"].value, ("A", "B"))

    @override_settings(AB_ATIVO=False)
    def test_teste_desligado_so_mostra_a(self):
        self.assertIn('data-intencao="simular">Simular</a>', self.abrir(tc_ab="B"))

    def test_lead_guarda_a_variante(self):
        self.client.cookies["tc_ab"] = "B"
        self.client.post(f"/v/{self.v.pk}-fiat-argo-2022/", {**FORM_OK, "event_id": "8" * 32}, HTTP_X_FORWARDED_FOR="200.3.3.3", **UA)
        self.assertEqual(Lead.objects.get().respostas["variante"], "B")


class MedicaoDoFunilTests(Base):
    def setUp(self):
        super().setUp()
        self.v = novo_veiculo()

    def test_etapas_do_navegador_sao_registradas_uma_vez_por_visitante(self):
        self.client.get(f"/v/{self.v.pk}-fiat-argo-2022/", **UA)
        for _ in range(2):
            r = self.client.post(f"/v/{self.v.pk}/etapa/", {"etapa": "q_pagamento"}, **UA)
            self.assertEqual(r.status_code, 200)
        self.assertEqual(EventoFunil.objects.filter(etapa="q_pagamento").count(), 1)
        self.assertEqual(self.client.post(f"/v/{self.v.pk}/etapa/", {"etapa": "enviou"}, **UA).status_code, 400)   # só o servidor grava "enviou"
        self.assertEqual(self.client.post(f"/v/{self.v.pk}/etapa/", {"etapa": "lixo"}, **UA).status_code, 400)

    def test_envio_completo_registra_enviou(self):
        self.client.get(f"/v/{self.v.pk}-fiat-argo-2022/", **UA)
        self.client.post(f"/v/{self.v.pk}-fiat-argo-2022/", {**FORM_OK, "event_id": "7" * 32}, HTTP_X_FORWARDED_FOR="200.4.4.4", **UA)
        self.assertEqual(set(EventoFunil.objects.values_list("etapa", flat=True)), {"view", "enviou"})

    def test_bots_nao_entram_na_medicao(self):
        self.client.get(f"/v/{self.v.pk}-fiat-argo-2022/", HTTP_USER_AGENT="Googlebot/2.1")
        self.assertEqual(EventoFunil.objects.count(), 0)

    def test_painel_do_formulario(self):
        for var, n_view, n_env in (("A", 120, 10), ("B", 120, 25)):
            for i in range(n_view):
                EventoFunil.objects.create(veiculo_id=self.v.pk, etapa="view", variante=var, sid=f"{var}{i:031d}", consent=True)
            for i in range(n_env):
                EventoFunil.objects.create(veiculo_id=self.v.pk, etapa="enviou", variante=var, sid=f"{var}{i:031d}", consent=True)
        EventoFunil.objects.create(veiculo_id=self.v.pk, etapa="view", variante="A", sid="", consent=False)
        self.client.force_login(get_user_model().objects.create_user("g", password="x12345678", is_staff=True))
        r = self.client.get("/painel/formulario/")
        self.assertEqual(r.status_code, 200)
        a, b, anon = r.context["tabelas"]
        self.assertEqual((a["visitas"], a["enviou"], a["taxa"]), (120, 10, 8.3))
        self.assertEqual((b["visitas"], b["enviou"], b["taxa"]), (120, 25, 20.8))
        self.assertEqual(anon["visitas"], 1)
        self.assertIn("A variante B converte mais", r.context["veredito"])

    def test_painel_com_amostra_pequena_nao_conclui(self):
        EventoFunil.objects.create(veiculo_id=self.v.pk, etapa="view", variante="A", sid="a" * 32, consent=True)
        self.client.force_login(get_user_model().objects.create_user("g", password="x12345678", is_staff=True))
        self.assertIn("Amostra pequena", self.client.get("/painel/formulario/").context["veredito"])

    def test_painel_exige_login(self):
        self.client.logout()
        self.assertEqual(self.client.get("/painel/formulario/").status_code, 302)


class PaginaDoFormularioTests(Base):
    def abrir(self):
        from django.core.cache import cache
        cache.clear()
        v = novo_veiculo(fotos=5)
        return self.client.get(f"/v/{v.pk}-fiat-argo-2022/", **UA).content.decode()

    def test_contato_vem_primeiro_e_o_botao_de_enviar_fica_na_ultima_pergunta(self):
        html = self.abrir()
        self.assertLess(html.index("data-contato"), html.index('data-campo="pagamento"'))
        self.assertGreater(html.index('id="enviar"'), html.index('data-campo="prazo"'))
        self.assertEqual(html.count('id="enviar"'), 1)
        self.assertIn('id="contato-ok"', html)

    def test_garantias_ficam_depois_do_formulario(self):
        html = self.abrir()
        self.assertGreater(html.index('class="garantias"'), html.index('id="form"'))

    def test_carrossel_menor_e_automatico_sem_pausa_por_rolagem(self):
        html = self.abrir()
        self.assertIn("aspect-ratio:16/10", html)
        self.assertIn("setInterval(function(){if(d.hidden||sobre||!visivel||Date.now()-ult<6000)return;ir(idx+1);},3800)", html)
        self.assertNotIn("'wheel'", html)                              # rolar a página não para mais o carrossel

    def test_adaptacao_para_aparelho_fraco(self):
        html = self.abrir()
        self.assertIn("classList.add('leve')", html)
        self.assertIn(".leve .fundo", html)
