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

    def test_vendido_mostra_aviso_e_3_similares_na_faixa(self):
        vendido = novo_veiculo(status="vendido")
        for i, preco in enumerate(["55000", "62000", "64000", "50000", "90000"]):
            novo_veiculo(placa=f"XYZ{i}A00", valor_venda=preco, modelo=f"Sim{i}")
        novo_veiculo(placa="PREP0001", valor_venda="59000", modelo="Prep", status="preparacao")
        r = self.client.get(f"/v/{vendido.pk}-fiat-argo-2022/")
        html = r.content.decode()
        self.assertIn("Este carro foi vendido", html)
        sim = [v.modelo for v, _ in r.context["similares"]]
        self.assertEqual(len(sim), 3)
        self.assertNotIn("Sim4", sim)    # 90 mil: fora de ±15%
        self.assertNotIn("Prep", sim)    # não está em loja
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
