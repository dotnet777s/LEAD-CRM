from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import override_settings
from django.utils import timezone

from captacao import meta, telegram
from captacao.models import EventoFunil, EventoMeta, Historico, Lead, NotificacaoTelegram, TelegramVinculo

from .helpers import FORM_OK, Base, novo_veiculo

UA = {"HTTP_USER_AGENT": "Mozilla/5.0"}
CONTATO = {"nome": "Maria Silva", "telefone": "(81) 99999-8888", "lgpd": "1", "pagamento": "financiado", "prazo": "semana"}
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
        self.assertIn("Não terminou o formulário (falta troca e entrada)", n.texto)
        self.assertIn("Pagamento: Financiado", n.texto)                              # o que ele já respondeu aparece
        self.assertIn("Compra: Esta semana", n.texto)
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

    def test_painel_do_formulario_compara_as_variantes(self):
        for var, n_view, n_env in (("A", 120, 10), ("B", 120, 25)):
            for i in range(n_view):
                EventoFunil.objects.create(veiculo_id=self.v.pk, etapa="view", variante=var, sid=f"{var}{i:031d}", consent=True)
            for i in range(n_env):
                EventoFunil.objects.create(veiculo_id=self.v.pk, etapa="contato", variante=var, sid=f"{var}{i:031d}", consent=True)
        EventoFunil.objects.create(veiculo_id=self.v.pk, etapa="view", variante="A", sid="", consent=False)
        self.client.force_login(get_user_model().objects.create_user("g", password="x12345678", is_staff=True))
        r = self.client.get("/painel/formulario/")
        self.assertEqual(r.status_code, 200)
        ab = r.context["ab"]
        self.assertEqual((ab["A"]["visitas"], ab["A"]["leads"], ab["A"]["taxa"]), (120, 10, 8.3))
        self.assertEqual((ab["B"]["visitas"], ab["B"]["leads"], ab["B"]["taxa"]), (120, 25, 20.8))
        self.assertIn("A variante B converte mais", r.context["veredito"])
        self.assertEqual(r.context["veredito_tom"], "bom")
        self.assertEqual(r.context["kpis"]["visitas"], 241)              # 240 com cookies + 1 anônimo

    def test_painel_com_amostra_pequena_nao_conclui(self):
        EventoFunil.objects.create(veiculo_id=self.v.pk, etapa="view", variante="A", sid="a" * 32, consent=True)
        self.client.force_login(get_user_model().objects.create_user("g", password="x12345678", is_staff=True))
        r = self.client.get("/painel/formulario/")
        self.assertIn("Amostra pequena", r.context["veredito"])
        self.assertTrue(r.context["poucos_dados"])

    def test_painel_exige_login(self):
        self.client.logout()
        self.assertEqual(self.client.get("/painel/formulario/").status_code, 302)


class PaginaDoFormularioTests(Base):
    def abrir(self):
        from django.core.cache import cache
        cache.clear()
        v = novo_veiculo(fotos=5)
        return self.client.get(f"/v/{v.pk}-fiat-argo-2022/", **UA).content.decode()

    def test_ordem_pagamento_prazo_contato_e_passo_opcional(self):
        html = self.abrir()
        ordem = [html.index(m) for m in ('data-campo="pagamento"', 'data-campo="prazo"', '<fieldset class="passo" data-contato>', '<fieldset class="passo" data-extras>')]
        self.assertEqual(ordem, sorted(ordem))
        self.assertGreater(html.index('id="enviar"'), html.index('<fieldset class="passo" data-extras>'))        # enviar só no último passo
        self.assertEqual(html.count('id="enviar"'), 1)
        self.assertIn('id="contato-ok"', html)
        self.assertIn("Responda 2 perguntas rápidas", html)

    def test_linha_de_confianca_junto_ao_botao_do_contato(self):
        html = self.abrir()
        self.assertIn("Garantia de 90 dias · Revisado · Vistoria cautelar", html)
        i = html.index('class="confianca"')
        self.assertLess(i, html.index('id="contato-ok"'))                               # logo acima do botão
        self.assertGreater(i, html.index('<fieldset class="passo" data-contato>'))

    def test_troca_e_entrada_no_passo_opcional(self):
        html = self.abrir()
        extras = html[html.index('<fieldset class="passo" data-extras>'):]
        self.assertIn("Opcional. Se preferir, conversamos isso no WhatsApp.", extras)
        self.assertIn('name="troca"', extras)
        self.assertIn('name="entrada"', extras)
        self.assertIn('id="entrada-bloco"', extras)

    def test_selo_online_sempre_ligado_sem_horario_de_retorno(self):
        html = self.abrir()
        self.assertIn('class="aberto on"', html)
        self.assertIn("Atendimento online pelo WhatsApp", html)
        self.assertNotIn("Respondemos a partir", html)

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


class DashboardTests(Base):
    """O dashboard do formulário: funil, maior vazamento, comparação com o período anterior, listas de ação."""

    def setUp(self):
        super().setUp()
        self.v = novo_veiculo()
        self.client.force_login(get_user_model().objects.create_user("g", password="x12345678", is_staff=True))

    def eventos(self, etapa, n, prefixo, dias_atras=0, var="A", vid=None):
        quando = timezone.now() - timedelta(days=dias_atras)
        for i in range(n):
            e = EventoFunil.objects.create(veiculo_id=vid or self.v.pk, etapa=etapa, variante=var, sid=f"{prefixo}{i:030d}"[:32], consent=True)
            EventoFunil.objects.filter(pk=e.pk).update(criado_em=quando)

    def funil_padrao(self, **kw):
        for etapa, n in (("view", 200), ("comecou", 80), ("q_pagamento", 60), ("q_prazo", 50), ("contato", 20), ("enviou", 14)):
            self.eventos(etapa, n, etapa[:2], **kw)

    def test_funil_com_perdas_e_maior_vazamento(self):
        self.funil_padrao()
        ctx = self.client.get("/painel/formulario/").context
        linhas = {l["chave"]: l for l in ctx["linhas"]}
        self.assertEqual(linhas["view"]["n"], 200)
        self.assertEqual(linhas["comecou"]["perdeu_n"], 120)
        self.assertEqual(linhas["comecou"]["perdeu_pct"], 60.0)
        self.assertEqual(linhas["contato"]["perdeu_n"], 30)
        v = ctx["vazamento"]
        self.assertEqual((v["de"], v["para"], v["n"]), ("Abriu a página", "Começou o formulário", 120))       # maior perda em número de pessoas
        self.assertIn("nem toca no formulário", v["dica"])

    def test_vazamento_no_contato_recomenda_reforcar_a_confianca(self):
        for etapa, n in (("view", 100), ("comecou", 95), ("q_pagamento", 90), ("q_prazo", 85), ("contato", 20), ("enviou", 15)):
            self.eventos(etapa, n, etapa[:2])
        v = self.client.get("/painel/formulario/").context["vazamento"]
        self.assertEqual((v["para"], v["n"]), ("Deixou nome e WhatsApp (virou lead)", 65))
        self.assertIn("nome e WhatsApp", v["dica"])

    def test_leads_que_nao_fizeram_o_ultimo_passo_vao_para_requalificar(self):
        for etapa, n in (("view", 100), ("comecou", 95), ("q_pagamento", 90), ("q_prazo", 85), ("contato", 80), ("enviou", 10)):
            self.eventos(etapa, n, etapa[:2])
        v = self.client.get("/painel/formulario/").context["vazamento"]
        self.assertEqual(v["para"], "Terminou o formulário")
        self.assertIn("coluna Requalificar", v["dica"])

    def test_poucas_visitas_nao_apontam_vazamento(self):
        self.eventos("view", 5, "vv")
        ctx = self.client.get("/painel/formulario/").context
        self.assertIsNone(ctx["vazamento"])
        self.assertTrue(ctx["poucos_dados"])

    def test_troca_e_entrada_sao_opcionais_e_ficam_fora_da_conta_de_perdas(self):
        self.funil_padrao()
        self.eventos("q_troca", 8, "tr")
        self.eventos("q_entrada", 5, "en")
        ctx = self.client.get("/painel/formulario/").context
        self.assertEqual([l["chave"] for l in ctx["linhas"]], ["view", "comecou", "q_pagamento", "q_prazo", "contato", "enviou"])
        lat = {x["nome"]: x for x in ctx["laterais"]}
        self.assertEqual(lat["Respondeu a troca (opcional)"]["n"], 8)
        self.assertEqual(lat["Respondeu a entrada (opcional)"]["pct"], 25.0)          # 5 de 20 leads

    def test_comparacao_com_o_periodo_anterior(self):
        self.eventos("view", 100, "at")
        self.eventos("contato", 10, "at")
        self.eventos("view", 50, "an", dias_atras=40)
        self.eventos("contato", 10, "an", dias_atras=40)
        k = self.client.get("/painel/formulario/?dias=30").context["kpis"]
        self.assertEqual((k["visitas"], k["conversao"]), (100, 10.0))
        self.assertEqual(k["visitas_d"], 100.0)                           # de 50 para 100 visitas
        self.assertEqual(k["conversao_d"], -10.0)                         # de 20% para 10%: -10 pontos

    def test_serie_por_dia_tem_um_ponto_para_cada_dia(self):
        self.eventos("view", 3, "sd")
        s = self.client.get("/painel/formulario/?dias=7").context["serie"]
        self.assertEqual((len(s["rotulos"]), len(s["visitas"]), len(s["enviados"])), (7, 7, 7))
        self.assertEqual(s["visitas"][-1], 3)

    def test_filtro_por_carro(self):
        outro = novo_veiculo(modelo="Mobi", placa="MOB1A11")
        self.eventos("view", 7, "aa")
        self.eventos("view", 4, "bb", vid=outro.pk)
        self.assertEqual(self.client.get("/painel/formulario/").context["kpis"]["visitas"], 11)
        self.assertEqual(self.client.get(f"/painel/formulario/?carro={outro.pk}").context["kpis"]["visitas"], 4)

    def test_tabela_por_carro_sinaliza_trafego_sem_conversao(self):
        self.eventos("view", 25, "tr")
        c = self.client.get("/painel/formulario/").context["carros"]
        self.assertEqual((c[0]["visitas"], c[0]["enviou"], c[0]["alerta"]), (25, 0, True))

    def test_lista_de_incompletos_e_por_anuncio(self):
        for i in range(3):
            self.client.post(f"/v/{self.v.pk}/inicio/", {"nome": f"Pessoa {i}", "telefone": f"(81) 99999-60{i:02d}", "lgpd": "1", "pagamento": "a_vista", "prazo": "semana"},
                             HTTP_USER_AGENT="Mozilla/5.0", HTTP_X_FORWARDED_FOR=f"200.7.7.{i}", **{"HTTP_REFERER": "x"})
        Lead.objects.update(utm_content="anuncio-teste")
        ctx = self.client.get("/painel/formulario/").context
        self.assertEqual(ctx["incompletos"], 3)
        self.assertEqual(len(ctx["lista_incompletos"]), 3)
        self.assertIn("wa.me/55819999960", ctx["lista_incompletos"][0]["url"])
        self.assertEqual(ctx["por_anuncio"][0]["total"], 3)
        self.assertEqual(ctx["por_anuncio"][0]["incompletos"], 3)

    def test_pagina_renderiza_com_dados_e_sem_dados(self):
        r = self.client.get("/painel/formulario/")
        self.assertContains(r, "Ainda não há visitas medidas")
        self.funil_padrao()
        r = self.client.get("/painel/formulario/")
        html = r.content.decode()
        self.assertIn("Maior vazamento", html)
        self.assertIn("Funil do formulário", html)
        self.assertIn('id="g-visitas"', html)
        self.assertIn("Ver como tabela", html)                          # versão em tabela de cada gráfico
        self.assertNotIn("None", html.split("<style>")[0] + html.split("</style>")[1])


class FormularioOpcionalTests(Base):
    """Troca e entrada são opcionais: o lead completa só com pagamento, prazo e contato."""

    def setUp(self):
        super().setUp()
        self.v = novo_veiculo()

    def post(self, **extra):
        dados = {"nome": "Maria Silva", "telefone": "(81) 99999-8888", "lgpd": "1", "pagamento": "financiado", "prazo": "semana",
                 "event_id": "a" * 32, **extra}
        return self.client.post(f"/v/{self.v.pk}-fiat-argo-2022/", dados, HTTP_USER_AGENT="Mozilla/5.0", HTTP_X_FORWARDED_FOR="200.8.8.8")

    def test_envio_sem_troca_e_sem_entrada(self):
        self.assertEqual(self.post().status_code, 302)
        lead = Lead.objects.get()
        self.assertNotIn("troca", lead.respostas)
        self.assertNotIn("entrada", lead.respostas)
        self.assertEqual(lead.respostas["pagamento"], "financiado")

    def test_mensagem_do_whatsapp_so_resume_o_que_foi_respondido(self):
        import urllib.parse
        r = self.post()
        texto = urllib.parse.unquote(self.client.get(r["Location"]).context["wa_url"].split("text=")[1])
        self.assertIn("Forma de pagamento: Financiado", texto)
        self.assertIn("Previsão de compra: Esta semana", texto)
        self.assertNotIn("Entrada:", texto)
        self.assertNotIn("na troca", texto)

    def test_troca_sim_exige_o_modelo(self):
        r = self.post(troca="sim", troca_carro="")
        self.assertEqual(r.status_code, 400)
        self.assertEqual(Lead.objects.count(), 0)
        self.assertEqual(self.post(troca="sim", troca_carro="CG 160 2020").status_code, 302)

    def test_pagamento_e_prazo_continuam_obrigatorios(self):
        self.assertEqual(self.post(pagamento="").status_code, 400)
        self.assertEqual(self.post(prazo="").status_code, 400)

    def test_contato_parcial_ja_leva_pagamento_e_prazo_e_classifica(self):
        r = self.client.post(f"/v/{self.v.pk}/inicio/", {"nome": "Maria Silva", "telefone": "(81) 99999-8888", "lgpd": "1",
                                                         "pagamento": "a_vista", "prazo": "semana"},
                             HTTP_USER_AGENT="Mozilla/5.0", HTTP_X_FORWARDED_FOR="200.8.8.9")
        self.assertEqual(r.status_code, 200)
        lead = Lead.objects.get()
        self.assertEqual((lead.respostas["pagamento"], lead.respostas["prazo"], lead.classe), ("a_vista", "semana", "A"))
        self.assertTrue(lead.incompleto)

    def test_contato_parcial_sem_as_duas_respostas_nao_cria_lead(self):
        r = self.client.post(f"/v/{self.v.pk}/inicio/", {"nome": "Maria Silva", "telefone": "(81) 99999-8888", "lgpd": "1"},
                             HTTP_USER_AGENT="Mozilla/5.0", HTTP_X_FORWARDED_FOR="200.8.8.10")
        self.assertEqual(r.status_code, 400)
        self.assertEqual(Lead.objects.count(), 0)


class SemSimuladorTests(Base):
    """O simulador de parcela foi descartado: nem a prévia por URL existe mais."""

    def test_pagina_nao_tem_simulador_nem_com_o_parametro(self):
        from django.core.cache import cache
        v = novo_veiculo(valor_venda="68990")
        for query in ("", "?simulador=1"):
            cache.clear()
            html = self.client.get(f"/v/{v.pk}-fiat-argo-2022/{query}", HTTP_USER_AGENT="Mozilla/5.0").content.decode()
            self.assertNotIn('id="simbox"', html)
            self.assertNotIn("Simule a sua parcela", html)
            self.assertIn("Parcelas a partir de", html)                       # fica a parcela estimada fixa

    def test_campos_do_simulador_sao_ignorados(self):
        v = novo_veiculo()
        r = self.client.post(f"/v/{v.pk}-fiat-argo-2022/", {
            "nome": "Maria Silva", "telefone": "(81) 99999-8888", "lgpd": "1", "pagamento": "financiado", "prazo": "semana",
            "event_id": "b" * 32, "sim_entrada": "20697", "sim_parcelas": "60", "sim_parcela": "1386"},
            HTTP_USER_AGENT="Mozilla/5.0", HTTP_X_FORWARDED_FOR="200.9.1.1")
        self.assertEqual(r.status_code, 302)
        self.assertNotIn("simulacao", Lead.objects.get().respostas)


class IpDoVisitanteTests(Base):
    def test_ip_vem_do_cabecalho_da_cloudflare(self):
        from django.test import RequestFactory
        from captacao.views_publico import ip_do_cliente
        rf = RequestFactory()
        self.assertEqual(ip_do_cliente(rf.get("/", HTTP_CF_CONNECTING_IP="177.1.2.3", HTTP_X_FORWARDED_FOR="172.67.0.1")), "177.1.2.3")
        self.assertEqual(ip_do_cliente(rf.get("/", HTTP_X_FORWARDED_FOR="200.5.5.5, 10.0.0.1")), "200.5.5.5")
        self.assertEqual(ip_do_cliente(rf.get("/", REMOTE_ADDR="1.2.3.4")), "1.2.3.4")

    def test_limite_de_envios_e_por_visitante_e_nao_pelo_ip_da_cloudflare(self):
        v = novo_veiculo()
        def contato(n, ip_visitante):
            return self.client.post(f"/v/{v.pk}/inicio/", {"nome": "Maria", "telefone": f"(81) 99999-70{n:02d}", "lgpd": "1",
                                                          "pagamento": "a_vista", "prazo": "semana"},
                                    HTTP_USER_AGENT="Mozilla/5.0", HTTP_CF_CONNECTING_IP=ip_visitante, HTTP_X_FORWARDED_FOR="172.67.0.1")
        for i in range(10):
            self.assertEqual(contato(i, "177.9.9.9").status_code, 200)
        self.assertEqual(contato(10, "177.9.9.9").status_code, 429)              # o 11º do mesmo visitante é barrado
        self.assertEqual(contato(11, "177.8.8.8").status_code, 200)              # outro visitante, mesma borda da Cloudflare: passa


class UrlDoEventoTests(Base):
    def test_url_enviada_a_meta_usa_o_dominio_oficial_mesmo_com_host_de_repasse(self):
        from django.conf import settings
        v = novo_veiculo()
        self.client.get(f"/v/{v.pk}-fiat-argo-2022/", HTTP_USER_AGENT="Mozilla/5.0", HTTP_HOST="testserver")
        urls = {e.payload["data"][0]["event_source_url"] if "data" in e.payload else e.payload["event_source_url"]
                for e in EventoMeta.objects.all()}
        self.assertTrue(urls)
        for u in urls:
            self.assertTrue(u.startswith(settings.SITE_URL), u)
