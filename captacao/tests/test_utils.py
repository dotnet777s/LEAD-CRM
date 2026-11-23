from django.test import SimpleTestCase

from captacao import utils


class TelefoneTests(SimpleTestCase):
    def test_normaliza(self):
        for entrada in ("(81) 99999-8888", "81999998888", "+55 81 99999-8888", "5581999998888"):
            self.assertEqual(utils.normalizar_telefone(entrada), "5581999998888")

    def test_fixo(self):
        self.assertEqual(utils.normalizar_telefone("(81) 3333-4444"), "558133334444")

    def test_invalidos(self):
        for ruim in ("", "123", "(81) 89999-8888", "(00) 99999-8888", "99999-8888", "81 9999-88888888"):
            self.assertEqual(utils.normalizar_telefone(ruim), "", ruim)


class HashTests(SimpleTestCase):
    def test_telefone_hash_com_55(self):
        esperado = utils.sha256("5581999998888")
        self.assertEqual(utils.hash_telefone("(81) 99999-8888"), esperado)
        self.assertEqual(len(esperado), 64)

    def test_nome_minusculo_sem_acento(self):
        self.assertEqual(utils.normalizar_nome("  JOSÉ  d'Ávila "), "jose davila")
        self.assertEqual(utils.separar_nome("Maria da Conceição"), ("maria", "conceicao"))
        self.assertEqual(utils.hash_nome("Ação"), utils.sha256("acao"))
        self.assertEqual(utils.separar_nome("Cher"), ("cher", ""))


class FbcTests(SimpleTestCase):
    def test_usa_cookie(self):
        self.assertEqual(utils.montar_fbc("fb.1.1.abc", "xyz"), "fb.1.1.abc")

    def test_monta_do_fbclid(self):
        self.assertEqual(utils.montar_fbc("", "IwAR123", agora_ms=1700000000000), "fb.1.1700000000000.IwAR123")

    def test_vazio(self):
        self.assertEqual(utils.montar_fbc("", ""), "")


class ClasseTests(SimpleTestCase):
    def c(self, pagamento, entrada, prazo):
        return utils.classificar({"pagamento": pagamento, "entrada": entrada, "prazo": prazo})

    def test_a(self):
        self.assertEqual(self.c("a_vista", "sem", "semana"), "A")
        self.assertEqual(self.c("financiado", "10_30", "30_dias"), "A")
        self.assertEqual(self.c("financiado", "acima_30", "semana"), "A")

    def test_c(self):
        self.assertEqual(self.c("a_vista", "acima_30", "pesquisando"), "C")

    def test_b(self):
        self.assertEqual(self.c("financiado", "ate_10", "semana"), "B")
        self.assertEqual(self.c("financiado", "sem", "30_dias"), "B")
        self.assertEqual(self.c("consorcio", "sem", "semana"), "B")


class ValorTests(SimpleTestCase):
    def test_parse(self):
        from decimal import Decimal
        self.assertEqual(utils.parse_valor("59.900,00"), Decimal("59900.00"))
        self.assertEqual(utils.parse_valor("59900,00"), Decimal("59900.00"))
        self.assertEqual(utils.parse_valor("R$ 59900"), Decimal("59900"))
        self.assertEqual(utils.parse_valor("59900.00"), Decimal("59900.00"))
        self.assertIsNone(utils.parse_valor("abc"))
