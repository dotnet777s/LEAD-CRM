from django.test import override_settings

from captacao.models import EventoMeta

from .helpers import Base, novo_veiculo

UA = "Mozilla/5.0"


class CatalogoTests(Base):
    def test_publico_sem_login_e_so_disponiveis_com_foto_e_preco(self):
        a = novo_veiculo(unidade="Olinda")
        b = novo_veiculo(marca="Chevrolet", modelo="Onix", unidade="Recife", valor_venda=85000)
        novo_veiculo(modelo="Vendido", status="vendido")
        novo_veiculo(modelo="SemFoto", fotos=0)
        novo_veiculo(modelo="SemPreco", valor_venda=None)
        r = self.client.get("/catalogo/", HTTP_USER_AGENT=UA)
        self.assertEqual(r.status_code, 200)
        html = r.content.decode()
        self.assertEqual(html.count('class="card"'), 2)
        for nome in ("Vendido", "SemFoto", "SemPreco"):
            self.assertNotIn(f">{nome}<", html)
        self.assertIn(f"/v/{a.pk}-", html)
        self.assertIn(f"/v/{b.pk}-", html)
        self.assertIn("Olinda", html)
        self.assertIn("Recife", html)
        self.assertIn('data-marca="chevrolet"', html)
        self.assertIn("R$ 85.000", html)

    def test_filtros_seo_e_sem_numeros_de_venda(self):
        novo_veiculo(valor_venda=45000)
        novo_veiculo(valor_venda=95000)
        html = self.client.get("/catalogo/", HTTP_USER_AGENT=UA).content.decode()
        self.assertIn('data-faixa="ate50"', html)
        self.assertIn('data-faixa="acima90"', html)
        self.assertNotIn('data-faixa="50a70"', html)
        self.assertIn("application/ld+json", html)
        self.assertIn('rel="canonical"', html)
        self.assertNotIn("vendidos", html.lower())

    def test_vazio(self):
        r = self.client.get("/catalogo/", HTTP_USER_AGENT=UA)
        self.assertEqual(r.status_code, 200)
        self.assertNotIn('class="card"', r.content.decode())

    @override_settings(META_PIXEL_ID="123")
    def test_evento_so_com_consentimento(self):
        novo_veiculo()
        self.client.get("/catalogo/", HTTP_USER_AGENT=UA)
        com = EventoMeta.objects.count()
        self.assertEqual(com, 1)
        self.client.cookies["tc_consent"] = "min"
        self.client.get("/catalogo/", HTTP_USER_AGENT=UA)
        self.assertEqual(EventoMeta.objects.count(), com)

    def test_consentir(self):
        self.assertEqual(self.client.post("/catalogo/consentir/", {"event_id": "zz"}, HTTP_USER_AGENT=UA).status_code, 400)
        r = self.client.post("/catalogo/consentir/", {"event_id": "a" * 32}, HTTP_USER_AGENT=UA)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(EventoMeta.objects.count(), 1)
