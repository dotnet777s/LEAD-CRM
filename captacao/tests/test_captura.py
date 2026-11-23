from datetime import timedelta

from django.test import override_settings
from django.urls import reverse
from django.utils import timezone

from captacao.models import EventoMeta, Historico, Interacao, Lead

from .helpers import FORM_OK, Base, novo_veiculo


class PaginaTests(Base):
    def test_404_para_id_inexistente(self):
        self.assertEqual(self.client.get("/v/999/").status_code, 404)

    def test_slug_errado_redireciona(self):
        v = novo_veiculo()
        r = self.client.get(f"/v/{v.pk}/")
        self.assertEqual(r.status_code, 301)
        self.assertEqual(r["Location"], f"/v/{v.pk}-fiat-argo-2022/")

    @override_settings(META_PIXEL_ID="123")
    def test_pagina_do_carro(self):
        v = novo_veiculo()
        r = self.client.get(f"/v/{v.pk}-fiat-argo-2022/?utm_content=ad1&fbclid=XYZ", HTTP_USER_AGENT="Mozilla/5.0")
        self.assertEqual(r.status_code, 200)
        html = r.content.decode()
        self.assertIn("R$ 59.900", html)
        self.assertIn('fetchpriority="high"', html)
        self.assertIn('name="utm_content" value="ad1"', html)
        self.assertIn("fbq('init','123')", html)
        self.assertIn("og:image", html)
        self.assertNotIn("foi vendido", html)
        # PageView e ViewContent entram no outbox (CAPI), com ids do mesmo par do Pixel
        nomes = set(EventoMeta.objects.values_list("nome", flat=True))
        self.assertEqual(nomes, {"PageView", "ViewContent"})
        pv = EventoMeta.objects.get(nome="PageView")
        self.assertIn(f"'{pv.event_id}'", html)
        self.assertTrue(pv.payload["user_data"]["fbc"].endswith(".XYZ"))

    def test_bot_nao_gera_evento(self):
        v = novo_veiculo()
        self.client.get(f"/v/{v.pk}-fiat-argo-2022/", HTTP_USER_AGENT="facebookexternalhit/1.1")
        self.assertEqual(EventoMeta.objects.count(), 0)

    def test_vendido_mostra_aviso_e_4_sugeridos_mais_proximos(self):
        vendido = novo_veiculo(status="vendido")
        for i, preco in enumerate(["55000", "62000", "64000", "50000", "90000"]):
            novo_veiculo(placa=f"XYZ{i}A00", valor_venda=preco, modelo=f"Sim{i}")
        novo_veiculo(placa="PREP0001", valor_venda="59000", modelo="Prep", status="preparacao")
        r = self.client.get(f"/v/{vendido.pk}-fiat-argo-2022/")
        html = r.content.decode()
        self.assertIn("Este carro foi vendido", html)
        sim = [v.modelo for v, _ in r.context["similares"]]
        self.assertEqual(len(sim), 4)                       # máximo de 4, os mais próximos em preço
        self.assertEqual(sim, ["Sim1", "Sim2", "Sim0", "Sim3"])   # por proximidade de preço; o de 90 mil fica de fora
        self.assertNotIn("Prep", sim)                       # não está em loja
        self.assertIn('id="form"', html)  # o formulário continua capturando


class FormularioTests(Base):
    def post(self, v, **extra):
        return self.client.post(f"/v/{v.pk}-fiat-argo-2022/", {**FORM_OK, "event_id": "a" * 32, **extra},
                                HTTP_USER_AGENT="Mozilla/5.0", HTTP_X_FORWARDED_FOR="200.1.1.1",
                                HTTP_REFERER="https://l.facebook.com/")

    def test_cria_lead_e_redireciona(self):
        v = novo_veiculo()
        self.client.cookies["_fbp"] = "fb.1.1.123"
        r = self.post(v)
        lead = Lead.objects.get()
        self.assertRedirects(r, f"/v/{v.pk}/obrigado/{lead.uuid}/", fetch_redirect_response=False)
        self.assertEqual((lead.telefone, lead.classe, lead.etapa, lead.origem), ("5581999998888", "A", "novo", "meta_lp"))
        self.assertEqual((lead.veiculo_id, lead.utm_content, lead.fbp, lead.ip), (v.pk, "anuncio-1", "fb.1.1.123", "200.1.1.1"))
        self.assertIsNotNone(lead.consentimento_em)
        ev = EventoMeta.objects.get(nome="Lead")
        self.assertEqual(ev.event_id, "a" * 32)             # mesmo id do Pixel (deduplicação)
        self.assertEqual(ev.payload["user_data"]["client_ip_address"], "200.1.1.1")
        self.assertEqual(ev.status, "pendente")

    def test_mesmo_telefone_em_30_dias_nao_duplica(self):
        v1, v2 = novo_veiculo(), novo_veiculo(placa="DEF2G34", modelo="Mobi")
        self.post(v1)
        self.post(v2, nome="Maria S.", pagamento="financiado", prazo="pesquisando")
        self.assertEqual(Lead.objects.count(), 1)
        lead = Lead.objects.get()
        self.assertEqual(lead.veiculo_id, v2.pk)            # atualiza o carro de interesse
        self.assertEqual(lead.classe, "C")
        self.assertEqual(Interacao.objects.filter(lead=lead).count(), 1)
        self.assertTrue(Historico.objects.filter(lead=lead, tipo="form").exists())

    def test_depois_de_30_dias_cria_novo(self):
        v = novo_veiculo()
        self.post(v)
        Lead.objects.update(criado_em=timezone.now() - timedelta(days=31))
        self.post(v)
        self.assertEqual(Lead.objects.count(), 2)

    def test_honeypot_nao_grava(self):
        v = novo_veiculo()
        self.post(v, website="http://spam")
        self.assertEqual(Lead.objects.count(), 0)

    def test_rate_limit_por_ip(self):
        v = novo_veiculo()
        for i in range(5):
            self.assertEqual(self.post(v, telefone=f"(81) 99999-88{i:02d}").status_code, 302)
        self.assertEqual(self.post(v, telefone="(81) 99999-8899").status_code, 429)

    def test_validacoes(self):
        v = novo_veiculo()
        r = self.post(v, telefone="123")
        self.assertEqual(r.status_code, 400)
        self.assertIn("WhatsApp inválido", r.content.decode())
        self.assertEqual(self.post(v, lgpd="").status_code, 400)
        self.assertEqual(self.post(v, troca="sim", troca_carro="").status_code, 400)
        self.assertEqual(self.post(v, prazo="").status_code, 400)
        self.assertEqual(Lead.objects.count(), 0)

    def test_troca_com_carro(self):
        v = novo_veiculo()
        self.post(v, troca="sim", troca_carro="Gol 2015")
        self.assertEqual(Lead.objects.get().respostas["troca_carro"], "Gol 2015")

    def test_csrf_exigido(self):
        from django.test import Client
        v = novo_veiculo()
        c = Client(enforce_csrf_checks=True)
        self.assertEqual(c.post(f"/v/{v.pk}-fiat-argo-2022/", FORM_OK).status_code, 403)


class ObrigadoTests(Base):
    def criar(self):
        v = novo_veiculo()
        self.client.post(f"/v/{v.pk}-fiat-argo-2022/", {**FORM_OK, "event_id": "b" * 32}, HTTP_USER_AGENT="Mozilla/5.0")
        return v, Lead.objects.get()

    @override_settings(META_PIXEL_ID="123")
    def test_pagina_obrigado_tem_whatsapp_e_pixel(self):
        v, lead = self.criar()
        html = self.client.get(f"/v/{v.pk}/obrigado/{lead.uuid}/").content.decode()
        self.assertIn("https://wa.me/5581960002209?text=", html)
        self.assertIn("Tenho%20interesse%20no%20Fiat%20Argo%202022", html)
        self.assertIn("R%24%2059.900", html)
        self.assertIn("Forma%20de%20pagamento", html)
        self.assertIn("fbq('track','Lead'", html)
        self.assertIn("eventID:'" + "b" * 32 + "'", html)
        self.assertIn("setTimeout", html)
        self.assertIn("1500", html)

    def test_contato_enfileira_uma_vez(self):
        v, lead = self.criar()
        url = f"/v/{v.pk}/contato/{lead.uuid}/"
        self.assertEqual(self.client.post(url).status_code, 200)
        self.client.post(url)
        self.assertEqual(EventoMeta.objects.filter(nome="Contact").count(), 1)

    def test_lead_inexistente(self):
        v = novo_veiculo()
        self.assertEqual(self.client.get(f"/v/{v.pk}/obrigado/00000000-0000-0000-0000-000000000000/").status_code, 404)


class PrivacidadeTests(Base):
    def test_publica(self):
        r = self.client.get(reverse("captacao:privacidade"))
        self.assertEqual(r.status_code, 200)
        self.assertIn("LGPD".lower(), r.content.decode().lower().replace("lei geral de proteção de dados", "lgpd"))


class ConversaoTests(Base):
    """Elementos de conversão da página: troca, FIPE, interesse real, equipamentos e parecidos."""

    def abrir(self, v):
        from django.core.cache import cache
        cache.clear()      # a página guarda os dados por 60 s
        r = self.client.get(f"/v/{v.pk}-fiat-argo-2022/", HTTP_USER_AGENT="Mozilla/5.0")
        self.assertEqual(r.status_code, 200)
        return r.content.decode()

    def test_aceitamos_troca_em_varios_pontos(self):
        html = self.abrir(novo_veiculo())
        self.assertIn("Aceitamos carro ou moto como entrada", html)
        self.assertIn("Carro ou moto na entrada", html)
        self.assertIn("Aceitam carro ou moto como entrada?", html)
        self.assertIn("Aceitamos carro ou moto como parte do pagamento", html)
        self.assertIn("Tem carro ou moto para dar de entrada?", html)

    def test_selo_abaixo_da_fipe_so_com_dado_real(self):
        from decimal import Decimal
        self.assertNotIn("abaixo da tabela FIPE", self.abrir(novo_veiculo()))                 # sem FIPE: não aparece
        self.assertNotIn("abaixo da tabela FIPE", self.abrir(novo_veiculo(fipe=Decimal("59500"))))   # FIPE menor que o preço
        html = self.abrir(novo_veiculo(fipe=Decimal("65900")))
        self.assertIn("R$ 6.000 abaixo da tabela FIPE", html)

    def test_interesse_24h_a_partir_de_2_pedidos_reais(self):
        from .test_funil import criar_lead
        v = novo_veiculo()
        criar_lead(v, telefone="5581999990000")
        self.assertNotIn("pediram simulação deste carro", self.abrir(v))               # 1 pedido: não mostra
        criar_lead(v, telefone="5581999990001")
        self.assertIn("2 pessoas</b> pediram simulação deste carro nas últimas 24 horas", self.abrir(v))

    def test_interesse_7_dias_so_a_partir_de_3(self):
        from .test_funil import criar_lead
        v = novo_veiculo()
        for i in range(2):
            l = criar_lead(v, telefone=f"558199999200{i}")
            Lead.objects.filter(pk=l.pk).update(criado_em=timezone.now() - timedelta(days=3))
        self.assertNotIn("pediram simulação deste carro", self.abrir(v))
        l = criar_lead(v, telefone="5581999992009")
        Lead.objects.filter(pk=l.pk).update(criado_em=timezone.now() - timedelta(days=3))
        self.assertIn("3 pessoas</b> pediram simulação deste carro nos últimos 7 dias", self.abrir(v))

    def test_interesse_ignora_leads_antigos(self):
        from .test_funil import criar_lead
        v = novo_veiculo()
        for i in range(4):
            l = criar_lead(v, telefone=f"558199999100{i}")
            Lead.objects.filter(pk=l.pk).update(criado_em=timezone.now() - timedelta(days=10))
        self.assertNotIn("pediram simulação deste carro", self.abrir(v))

    def test_equipamentos_viram_cartoes_com_categoria(self):
        v = novo_veiculo(itens_serie=["Seis airbags", "Central multimídia com Apple CarPlay", "Ar-condicionado digital", "Tapetes de borracha"],
                         opcionais=[])
        html = self.abrir(v)
        self.assertIn("Equipamentos", html)
        self.assertIn("Seis airbags", html)
        self.assertIn("Segurança", html)
        self.assertIn("Tecnologia", html)
        self.assertIn("Conforto", html)
        self.assertIn("Tapetes de borracha", html)       # sem categoria: vai para a lista simples

    def test_parecidos_aparecem_tambem_em_carro_disponivel(self):
        v = novo_veiculo()
        outro = novo_veiculo(modelo="Mobi", placa="XYZ1A11", valor_venda="61000")
        html = self.abrir(v)
        self.assertIn("Você também pode gostar", html)
        self.assertIn(f"/v/{outro.pk}-", html)

    def test_carro_sem_preco_nao_quebra(self):
        v = novo_veiculo(valor_venda=None)
        r = self.client.get(f"/v/{v.pk}-fiat-argo-2022/", HTTP_USER_AGENT="Mozilla/5.0")
        self.assertEqual(r.status_code, 200)
        self.assertIn("Sob consulta", r.content.decode())


class BotoesParcelaProvasTests(Base):
    def abrir(self, v):
        from django.core.cache import cache
        cache.clear()
        r = self.client.get(f"/v/{v.pk}-fiat-argo-2022/", HTTP_USER_AGENT="Mozilla/5.0")
        self.assertEqual(r.status_code, 200)
        return r.content.decode()

    def sinais(self, html):
        import json
        import re
        m = re.search(r'<script id="sinais" type="application/json">(.*?)</script>', html, re.S)
        return json.loads(m.group(1)) if m else []

    def test_dois_botoes_simular_e_agendar(self):
        html = self.abrir(novo_veiculo())
        self.assertIn('data-intencao="simular"', html)
        self.assertIn('data-intencao="visita"', html)
        self.assertNotIn("Receber simulação", html)           # o botão único foi trocado pelos dois

    def test_carro_indisponivel_mantem_so_o_aviso(self):
        html = self.abrir(novo_veiculo(status="vendido"))
        self.assertNotIn('data-intencao="visita"', html)
        self.assertIn("Quero ser avisado", html)

    def test_parcela_estimada_com_aviso(self):
        from decimal import Decimal
        html = self.abrir(novo_veiculo(valor_venda=Decimal("68990")))
        self.assertIn("Parcelas a partir de <b>R$ 1.386/mês</b> em 60x, com entrada de 30% (R$ 20.697)", html)
        self.assertIn("Estimativa ilustrativa", html)

    def test_calculo_da_parcela(self):
        from decimal import Decimal
        from captacao import utils
        self.assertEqual(utils.parcela_estimada(Decimal("100000"), 30, 60, 0), (Decimal("1167"), Decimal("30000")))
        self.assertEqual(utils.parcela_estimada(None), (None, None))

    def test_numeros_de_vendas_nunca_aparecem_mesmo_reais(self):
        """Decisão da loja: não exibir quantos carros ou quantos do modelo já foram vendidos."""
        v = novo_veiculo()
        for i in range(2):
            novo_veiculo(status="vendido", placa=f"VEN{i}A00")           # mesmo modelo (Argo)
        for i in range(20):
            novo_veiculo(status="vendido", placa=f"OUT{i}A00", modelo="Outro")
        html = self.abrir(v)
        self.assertNotIn("carros vendidos", html)
        self.assertNotIn("já vendidos", html)
        self.assertNotIn("Já vendemos", html)
        self.assertNotIn("data-conta=", html)
        self.assertNotIn('id="pulso"', html)                      # sem pedidos reais, sem notificação
        self.assertEqual(self.sinais(html), [])

    def test_blocos_de_condicoes_e_garantias_em_destaque(self):
        html = self.abrir(novo_veiculo())
        self.assertIn('class="bene"', html)
        self.assertIn("até 60x", html)
        self.assertIn("até 21x", html)
        self.assertIn("Aceitamos carro ou moto como entrada", html)
        self.assertIn('class="garantias"', html)
        for rotulo, valor in (("Garantia", "90 dias"), ("Revisado", "Revisão minuciosa"),
                              ("Vistoria cautelar", "Laudo cautelar 100% aprovado")):
            self.assertIn(f"<small>{rotulo}</small><b>{valor}</b>", html)

    def test_sem_vendas_nao_mostra_destaque_de_modelo(self):
        html = self.abrir(novo_veiculo())
        self.assertNotIn("já vendidos", html)
        self.assertNotIn("carros vendidos", html)

    def test_pulso_so_com_dados_reais(self):
        from .test_funil import criar_lead
        v = novo_veiculo()
        self.assertNotIn('id="pulso"', self.abrir(v))                    # sem dados reais: não existe notificação
        for i in range(2):
            criar_lead(v, telefone=f"558199998000{i}")
        html = self.abrir(v)
        self.assertIn('id="pulso"', html)
        self.assertIn("2 pessoas pediram simulação deste carro nas últimas 24 horas", self.sinais(html))

    def test_pagina_indisponivel_nao_tem_pulso_nem_destaques(self):
        html = self.abrir(novo_veiculo(status="vendido"))
        self.assertNotIn('id="pulso"', html)

    def test_etiquetas_curtas_nos_demais_equipamentos(self):
        v = novo_veiculo(itens_serie=["Rodas aro 15 com calotas esportivas em dois tons", "Wi-Fi embarcado",
                                      "Retrovisores externos elétricos na cor da carroceria com luz de seta"], opcionais=[])
        html = self.abrir(v)
        self.assertIn('<div class="tags">', html)
        self.assertIn("<span>Rodas aro 15</span>", html)
        self.assertIn("<span>Wi-Fi</span>", html)
        self.assertNotIn("calotas esportivas", html)

    def test_intencao_visita_vai_para_o_lead_e_para_a_mensagem(self):
        import urllib.parse
        v = novo_veiculo()
        r = self.client.post(f"/v/{v.pk}-fiat-argo-2022/", {**FORM_OK, "event_id": "b" * 32, "intencao": "visita"},
                             HTTP_USER_AGENT="Mozilla/5.0", HTTP_X_FORWARDED_FOR="200.1.1.9")
        self.assertEqual(r.status_code, 302)
        lead = Lead.objects.get()
        self.assertEqual(lead.respostas.get("intencao"), "visita")
        self.assertIn("Quer agendar visita", lead.historico.first().texto)
        texto = urllib.parse.unquote(self.client.get(r["Location"]).context["wa_url"].split("text=")[1])
        self.assertIn("gostaria de agendar uma visita", texto)

    def test_sem_intencao_vira_simular(self):
        v = novo_veiculo()
        self.client.post(f"/v/{v.pk}-fiat-argo-2022/", {**FORM_OK, "event_id": "c" * 32},
                         HTTP_USER_AGENT="Mozilla/5.0", HTTP_X_FORWARDED_FOR="200.1.1.8")
        self.assertEqual(Lead.objects.get().respostas.get("intencao"), "simular")


class SobreEmpresaTests(Base):
    def abrir(self):
        from django.core.cache import cache
        cache.clear()
        v = novo_veiculo()
        return self.client.get(f"/v/{v.pk}-fiat-argo-2022/", HTTP_USER_AGENT="Mozilla/5.0").content.decode()

    def test_secao_sobre_com_numeros_da_empresa(self):
        html = self.abrir()
        self.assertIn("Sobre a TopCar", html)
        self.assertIn("há mais de 10 anos no mercado de seminovos", html)
        self.assertIn("mais de 60 opções disponíveis", html)
        self.assertIn("todo o estoque conta com garantia de 90 dias, revisão minuciosa e vistoria cautelar", html)
        self.assertIn("<b>+10</b>", html)
        self.assertIn("<b>+60</b>", html)

    def test_contatos_da_loja(self):
        html = self.abrir()
        self.assertIn('href="https://wa.me/5581960002209"', html)
        self.assertIn("(81) 96000-2209", html)
        self.assertIn('href="tel:+558121370011"', html)
        self.assertIn("(81) 2137-0011", html)
        self.assertIn('href="https://www.instagram.com/topcarveiculos.pe/"', html)
        self.assertIn("@topcarveiculos.pe", html)

    def test_garantia_90_dias_revisao_e_vistoria_em_toda_a_pagina(self):
        html = self.abrir()
        self.assertIn("Garantia de 90 dias", html)
        self.assertIn("Todo o estoque conta com garantia.", html)
        self.assertIn("Os carros têm garantia?", html)
        self.assertIn("Revisão minuciosa", html)
        self.assertIn("Laudo cautelar 100% aprovado", html)
        self.assertIn("Vistoria cautelar", html)


class ApresentacaoTests(Base):
    def abrir(self, **kw):
        from django.core.cache import cache
        cache.clear()
        v = novo_veiculo(**kw)
        return self.client.get(f"/v/{v.pk}-fiat-argo-2022/", HTTP_USER_AGENT="Mozilla/5.0").content.decode()

    def test_versao_ao_lado_do_modelo_sem_ano_no_titulo(self):
        html = self.abrir(versao="1.0 LT")
        self.assertIn('<h1>Argo <span class="vers">1.0 LT</span></h1>', html)

    def test_blocos_ano_cor_cambio_sem_quilometragem(self):
        html = self.abrir(cor="VERMELHO", cambio="AUTOMATICO", km=48000)
        ordem = [html.index(f"<small>{t}</small>") for t in ("Ano", "Cor", "Câmbio")]
        self.assertEqual(ordem, sorted(ordem))
        self.assertIn("<small>Câmbio</small><b>Automático</b>", html)
        self.assertIn("<small>Ano</small><b>2022</b>", html)
        self.assertNotIn("Quilometragem", html)
        self.assertNotIn("48.000", html)                      # o km não aparece nem nas descrições da página

    def test_cambio_some_quando_o_estoque_nao_informa(self):
        html = self.abrir(cambio=None)
        self.assertNotIn("<small>Câmbio</small>", html)

    def test_normaliza_nome_do_cambio(self):
        from catalogo.models import Veiculo
        for bruto, esperado in (("manual de 6 marchas", "Manual"), ("AUTOMATICO", "Automático"), ("automático", "Automático"),
                                ("CVT", "CVT"), ("Automatizado", "Automatizado"), ("", ""), (None, "")):
            self.assertEqual(Veiculo(cambio=bruto).cambio_fmt, esperado, bruto)

    def test_carrossel_direto_na_pagina_sem_janela_ampliada(self):
        from catalogo.models import FotoVeiculo
        html = self.abrir(fotos=4)
        self.assertIn('id="trilho"', html)
        self.assertEqual(html.count('class="slide"'), 4)
        self.assertIn('id="contador"', html)
        self.assertIn("1 / 4", html)
        self.assertEqual(html.count("data-foto="), 4)            # miniaturas que rolam o carrossel
        self.assertIn('id="ant"', html)
        self.assertIn('id="prox"', html)
        import re
        self.assertEqual(len(re.findall(r'<img[^>]*fetchpriority="high"', html)), 1)    # só a primeira foto tem prioridade
        self.assertNotIn("<dialog", html)
        self.assertNotIn("showModal", html)
        self.assertNotIn("data-abrir", html)
        self.assertEqual(FotoVeiculo.objects.count(), 4)

    def test_uma_foto_nao_mostra_controles_do_carrossel(self):
        html = self.abrir(fotos=1)
        self.assertEqual(html.count('class="slide"'), 1)
        self.assertNotIn('id="prox"', html)
        self.assertNotIn('id="contador"', html)

    def test_fundo_decorativo_e_fontes_proprias(self):
        html = self.abrir()
        self.assertIn('class="fundo"', html)
        self.assertIn("fonts/sora-latin.woff2", html)
        self.assertIn("fonts/inter-latin.woff2", html)
        self.assertNotIn("fonts.googleapis.com", html)      # fontes hospedadas no próprio site (sem terceiros)
