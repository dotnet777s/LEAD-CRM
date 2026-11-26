import io
import tempfile
from unittest import mock

from django.core.cache import cache
from django.core.management import call_command
from django.test import TestCase, override_settings
from PIL import Image

from captacao import fotos
from captacao.models import Lead
from catalogo import autocerto
from catalogo.models import FotoVeiculo, Veiculo

XML = """<?xml version="1.0" encoding="utf-8"?>
<estoque>
  <veiculo><idveiculo>5487470</idveiculo><tipoveiculo>Carro</tipoveiculo><dataCadastro>10/09/2026 07:50:54</dataCadastro><zerokm>N</zerokm>
    <placa>PCO-3712</placa><marca>TOYOTA</marca><modelo>YARIS</modelo><versao><![CDATA[1.3 16V FLEX XL MULTIDRIVE]]></versao>
    <anomodelo>2018/2019</anomodelo><cambio>Automático</cambio><quilometragem>59000</quilometragem><numeroportas>4</numeroportas><cor>Prata</cor>
    <combustivel>Flex</combustivel><preco>R$ 72.990,00</preco><precodecimal>72990</precodecimal><observacoes><![CDATA[Revisado.]]></observacoes><linkVideo />
    <DataAtualizacao>2026-09-10 07:50:54</DataAtualizacao>
    <opcionais><opcional>Alarme</opcional><opcional>Direção Elétrica</opcional></opcionais>
    <fotos><foto><ordem>2</ordem><url>https://www.autocerto.com/fotos/8116/5487470/10_075054.jpg?u=20260910075054</url></foto>
           <foto><ordem>1</ordem><url>https://www.autocerto.com/fotos/8116/5487470/11_075054.jpg?u=20260910075054</url></foto></fotos></veiculo>
  <veiculo><idveiculo>5487471</idveiculo><tipoveiculo>Carro</tipoveiculo><zerokm>S</zerokm><placa>ABC1D23</placa><marca>FIAT</marca><modelo>ARGO</modelo>
    <versao>1.0 DRIVE</versao><anomodelo>2026/2026</anomodelo><quilometragem>0</quilometragem><cor>Branco</cor><preco>Consulte</preco><precodecimal>0</precodecimal>
    <fotos></fotos></veiculo>
</estoque>"""


def jpeg_bytes():
    b = io.BytesIO()
    Image.new("RGB", (1600, 1200), (200, 30, 30)).save(b, "JPEG")
    return b.getvalue()


@override_settings(FOTOS_CACHE=tempfile.mkdtemp(prefix="fotos_teste_"))
class AutoCertoTests(TestCase):
    def setUp(self):
        cache.clear()

    def test_interpreta_o_xml(self):
        a, b = autocerto.interpretar(XML.encode())
        self.assertEqual((a["rid"], a["placa"], a["marca"], a["ano_modelo"], a["km"]), ("5487470", "PCO3712", "TOYOTA", 2019, 59000))
        self.assertEqual(str(a["valor_venda"]), "72990")
        self.assertEqual(a["opcionais"], ["Alarme", "Direção Elétrica"])
        self.assertEqual([o for o, _ in a["fotos"]], [1, 2])                 # ordenadas
        self.assertTrue(b["zero_km"])
        self.assertIsNone(b["valor_venda"])                                   # "Consulte" / 0 não vira preço

    def test_xml_invalido_ou_vazio_nao_altera_nada(self):
        with self.assertRaises(autocerto.ErroEstoque):
            autocerto.interpretar(b"<html>erro</html>")
        with self.assertRaises(autocerto.ErroEstoque):
            autocerto.interpretar(b"nao e xml")
        with self.assertRaises(autocerto.ErroEstoque):
            autocerto.sincronizar(b"<estoque></estoque>", ids_antigos={})

    def test_sincroniza_cria_atualiza_e_retira(self):
        r = autocerto.sincronizar(XML.encode(), ids_antigos={})
        self.assertEqual((r["novos"], r["atualizados"], r["retirados"], r["total"]), (2, 0, 0, 2))
        yaris = Veiculo.objects.get(rid="5487470")
        self.assertEqual((yaris.status, yaris.total_fotos, yaris.marca_fmt, yaris.ano), ("disponivel", 2, "Toyota", 2019))
        fs = list(FotoVeiculo.objects.filter(veiculo_rid="5487470"))
        self.assertEqual([f.capa for f in sorted(fs, key=lambda f: f.ordem)], [True, False])
        self.assertTrue(all(len(f.pk) <= 24 for f in fs))
        # segunda rodada: um preço mudou e o Argo saiu do XML
        novo = XML.replace("<precodecimal>72990</precodecimal>", "<precodecimal>70990</precodecimal>")
        novo = novo[:novo.index("<veiculo><idveiculo>5487471")] + "</estoque>"
        r = autocerto.sincronizar(novo.encode(), ids_antigos={})
        self.assertEqual((r["novos"], r["atualizados"], r["retirados"]), (0, 1, 1))
        self.assertEqual(str(Veiculo.objects.get(rid="5487470").valor_venda), "70990.00")
        self.assertEqual(Veiculo.objects.get(rid="5487471").status, "retirado")

    def test_preserva_o_id_publico_antigo_pela_placa(self):
        autocerto.sincronizar(XML.encode(), ids_antigos={"PCO3712": 7})
        self.assertEqual(Veiculo.objects.get(rid="5487470").pk, 7)               # link /v/7-... continua valendo
        self.assertGreater(Veiculo.objects.get(rid="5487471").pk, 1000)          # o novo não colide com ids do ROUTE

    def test_nao_derruba_o_catalogo_se_o_xml_vier_pela_metade(self):
        for i in range(12):
            Veiculo.objects.create(rid=f"x{i}", marca="FIAT", modelo="UNO", ano_modelo=2020, status="disponivel")
        with self.assertRaises(autocerto.ErroEstoque):
            autocerto.sincronizar(XML.encode(), ids_antigos={})
        self.assertEqual(Veiculo.objects.filter(status="disponivel").count(), 12)

    def test_carro_que_saiu_do_xml_fica_retirado_e_referencia_invalida_e_limpa(self):
        v = Veiculo.objects.create(rid="velho", marca="VW", modelo="GOL", ano_modelo=2015, status="disponivel")
        l = Lead.objects.create(nome="A", telefone="5581999998888", veiculo=v, carro_texto="VW Gol 2015 · R$ 30.000")
        orfao = Lead.objects.create(nome="B", telefone="5581911110000", veiculo_id=99999, carro_texto="Carro do ROUTE")
        autocerto.sincronizar(XML.encode(), ids_antigos={})
        l.refresh_from_db()
        orfao.refresh_from_db()
        self.assertEqual((l.veiculo_id, Veiculo.objects.get(pk=v.pk).status), (v.pk, "retirado"))     # histórico preservado
        self.assertIsNone(orfao.veiculo_id)                                                              # apontava para id que não existe
        self.assertEqual(orfao.carro_texto, "Carro do ROUTE")

    @mock.patch("captacao.fotos.requests.get")
    def test_foto_vem_da_url_do_autocerto_e_fica_em_cache(self, get):
        get.return_value = mock.Mock(content=jpeg_bytes(), raise_for_status=lambda: None)
        autocerto.sincronizar(XML.encode(), ids_antigos={})
        capa = FotoVeiculo.objects.get(veiculo_rid="5487470", capa=True)
        caminho = fotos.arquivo_webp(capa, 480)
        self.assertEqual(Image.open(caminho).width, 480)
        fotos.arquivo_webp(capa, 480)
        self.assertEqual(get.call_count, 1)                                       # segunda vez sai do cache
        self.assertEqual(self.client.get(f"/foto/{capa.pk}/800.webp").status_code, 200)
        self.assertEqual(autocerto.aquecer_capas(), 1)

    def test_catalogo_publico_mostra_o_estoque_do_autocerto(self):
        autocerto.sincronizar(XML.encode(), ids_antigos={})
        html = self.client.get("/catalogo/", HTTP_USER_AGENT="Mozilla/5.0").content.decode()
        self.assertIn("Yaris", html)
        self.assertIn("R$ 72.990", html)
        self.assertNotIn("Argo", html)                                            # sem preço: fora do catálogo
        self.assertIn("/foto/5487470-1-", html)

    def test_comando_com_arquivo(self):
        import os, tempfile
        with tempfile.NamedTemporaryFile("wb", suffix=".xml", delete=False) as f:
            f.write(XML.encode())
        try:
            out = io.StringIO()
            call_command("sincronizar_estoque", arquivo=f.name, sem_fotos=True, stdout=out)
            self.assertIn("2 novos", out.getvalue())
        finally:
            os.unlink(f.name)

    @override_settings(AUTOCERTO_URL="")
    def test_sem_url_configurada(self):
        with self.assertRaises(autocerto.ErroEstoque):
            autocerto.baixar_xml()


class EstoqueTelasTests(TestCase):
    def setUp(self):
        from django.contrib.auth import get_user_model
        self.user = get_user_model().objects.create_user("g", password="x", is_staff=True)
        self.client.force_login(self.user)

    def test_carros_mostra_a_fonte_e_atualiza_agora(self):
        with override_settings(AUTOCERTO_URL="https://x/xml"):
            self.assertContains(self.client.get("/carros/"), "Estoque do AutoCerto")
            with mock.patch("catalogo.autocerto.baixar_xml", return_value=XML.encode()), mock.patch("catalogo.autocerto.aquecer_capas", return_value=0):
                r = self.client.post("/carros/sincronizar/")
            self.assertEqual(Veiculo.objects.filter(status="disponivel").count(), 2)
            self.assertIn("Estoque atualizado", self.client.get(r["Location"]).content.decode())

    def test_so_gestor_atualiza_e_erro_e_mostrado(self):
        with mock.patch("catalogo.autocerto.baixar_xml", return_value=b"<estoque></estoque>"):
            r = self.client.post("/carros/sincronizar/")
        self.assertIn("erro", r["Location"])
        from django.contrib.auth import get_user_model
        self.client.force_login(get_user_model().objects.create_user("v", password="x"))
        self.assertEqual(self.client.post("/carros/sincronizar/").status_code, 403)

    def test_depois_da_primeira_carga_o_route_nao_e_mais_consultado(self):
        with mock.patch("catalogo.autocerto.ids_antigos_do_route", return_value={}) as lido:
            autocerto.sincronizar(XML.encode())          # 1ª carga: consulta (para preservar ids)
            autocerto.sincronizar(XML.encode())          # demais: nunca mais
        self.assertEqual(lido.call_count, 1)
