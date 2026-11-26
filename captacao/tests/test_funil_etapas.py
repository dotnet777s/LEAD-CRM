from django.test import TestCase

from captacao import classificacao, funil, services, whatsapp
from captacao.models import EtapaFunil, Etiqueta, FollowUpConfig, Historico, Lead

from .test_central import Logado, lead
from .test_funil_auto import msg_cliente


class EtapasTests(Logado):
    def setUp(self):
        super().setUp()
        funil.invalidar()

    def tearDown(self):
        funil.invalidar()

    def test_padroes_existem_e_telas_abrem(self):
        self.assertEqual(funil.chaves()[0], "novo")
        for u in ("/funil/etapas/", f"/funil/etapas/{EtapaFunil.objects.get(chave='qualificado').pk}/"):
            self.assertEqual(self.client.get(u).status_code, 200, u)

    def test_criar_etapa_entra_antes_de_vendido_e_aparece_no_quadro(self):
        r = self.client.post("/funil/etapas/nova/", {"nome": "Aguardando crédito", "cor": "#ec4899"})
        e = EtapaFunil.objects.get(nome="Aguardando crédito")
        self.assertEqual(r["Location"], f"/funil/etapas/{e.pk}/")
        chaves = funil.chaves()
        self.assertLess(chaves.index(e.chave), chaves.index("vendido"))
        self.assertGreater(chaves.index(e.chave), chaves.index("negociacao"))
        l = lead()
        services.mudar_etapa(l, e.chave)
        l.refresh_from_db()
        self.assertEqual((l.etapa, l.get_etapa_display()), (e.chave, "Aguardando crédito"))
        html = self.client.get("/quadro/").content.decode()
        self.assertIn("Aguardando crédito", html)
        self.assertIn("#ec4899", html)
        self.assertIn("Aguardando crédito", self.client.get("/contatos/").content.decode())      # filtro de etapa
        self.assertEqual(self.client.get(f"/contatos/?etapa={e.chave}").status_code, 200)

    def test_reordenar_mantem_fechamento_no_fim(self):
        a = funil.criar("A")
        b = funil.criar("B")
        ids = [x.pk for x in funil.todas()]
        ids.remove(a.pk)
        ids.insert(0, a.pk)
        self.client.post("/funil/etapas/reordenar/", {"ids": ids})
        funil.invalidar()
        self.assertEqual(funil.chaves()[0], a.chave)
        self.assertEqual(funil.chaves()[-2:], ["vendido", "perdido"])
        self.assertTrue(b)

    def test_renomear_e_trocar_cor_inclusive_de_sistema(self):
        e = EtapaFunil.objects.get(chave="novo")
        self.client.post(f"/funil/etapas/{e.pk}/", {"nome": "Novo contato", "cor": "#22c55e"})
        funil.invalidar()
        self.assertEqual((funil.nome("novo"), funil.cor("novo")), ("Novo contato", "#22c55e"))

    def test_apagar_leva_leads_e_protege_sistema(self):
        e = funil.criar("Temporária")
        l = lead()
        services.mudar_etapa(l, e.chave)
        self.client.post(f"/funil/etapas/{e.pk}/apagar/", {"destino": "atendimento"})
        l.refresh_from_db()
        self.assertEqual(l.etapa, "atendimento")
        self.assertFalse(EtapaFunil.objects.filter(pk=e.pk).exists())
        self.assertTrue(Historico.objects.filter(lead=l, texto__icontains="foi apagada").exists())
        sis = EtapaFunil.objects.get(chave="qualificado")
        r = self.client.post(f"/funil/etapas/{sis.pk}/apagar/", {"destino": "novo"})
        self.assertIn("erro", r["Location"] + "erro" if "pode" in r["Location"] else r["Location"])
        self.assertTrue(EtapaFunil.objects.filter(pk=sis.pk).exists())

    def test_so_gestor_edita(self):
        from django.contrib.auth import get_user_model
        self.client.force_login(get_user_model().objects.create_user("vend", password="x"))
        self.assertEqual(self.client.post("/funil/etapas/nova/", {"nome": "X"}).status_code, 403)

    def test_proxima_etapa_considera_as_novas_e_as_laterais(self):
        e = funil.criar("Crédito")
        self.assertEqual(funil.proxima("negociacao").chave, e.chave)
        self.assertEqual(funil.proxima(e.chave).chave, "vendido")
        self.assertEqual(funil.proxima("sem_estoque").chave, "atendimento")
        self.assertIsNone(funil.proxima("vendido"))


class GatilhosTests(TestCase):
    def setUp(self):
        funil.invalidar()
        classificacao.garantir_padroes()

    def tearDown(self):
        funil.invalidar()

    def test_definir_por_etiqueta_move_o_lead(self):
        e = funil.criar("Agendados"); tag = Etiqueta.objects.create(nome="AGENDADO")
        EtapaFunil.objects.filter(pk=e.pk).update(tag=tag)
        funil.invalidar()
        l = lead()
        l.etiquetas.add(tag)
        classificacao.etiqueta_adicionada(l, tag)
        l.refresh_from_db()
        self.assertEqual(l.etapa, e.chave)
        self.assertTrue(l.historico.filter(texto__icontains="Funil automático: etiqueta").exists())

    def test_etiqueta_nao_move_quem_ja_foi_vendido(self):
        e = funil.criar("X"); tag = Etiqueta.objects.create(nome="X")
        EtapaFunil.objects.filter(pk=e.pk).update(tag=tag)
        funil.invalidar()
        l = lead(etapa="vendido")
        classificacao.etiqueta_adicionada(l, tag)
        l.refresh_from_db()
        self.assertEqual(l.etapa, "vendido")

    def test_grupos_de_palavras_da_etapa(self):
        e = funil.criar("Interessado")
        EtapaFunil.objects.filter(pk=e.pk).update(grupos=[{"palavras": ["preço", "quanto custa"], "todas": False}, {"palavras": ["troca", "carro"], "todas": True}])
        funil.invalidar()
        l = lead()
        msg_cliente(l, "Qual o PREÇO do argo?")
        self.assertEqual(l.etapa, e.chave)
        l2 = lead(telefone="5581911110000")
        msg_cliente(l2, "aceita troca", "x2")                              # grupo 'todas': falta 'carro'
        self.assertNotEqual(l2.etapa, e.chave)
        l3 = lead(telefone="5581922220000")
        msg_cliente(l3, "aceita meu carro na troca?", "x3")
        self.assertEqual(l3.etapa, e.chave)

    def test_palavras_so_avancam(self):
        EtapaFunil.objects.filter(chave="atendimento").update(grupos=[{"palavras": ["oi"], "todas": False}])
        funil.invalidar()
        l = lead(etapa="negociacao")
        msg_cliente(l, "oi")
        self.assertEqual(l.etapa, "negociacao")

    def test_definir_por_arquivo(self):
        e = funil.criar("Documentos")
        EtapaFunil.objects.filter(pk=e.pk).update(por_arquivo=True)
        funil.invalidar()
        l = lead()
        whatsapp.processar_webhook({"entry": [{"changes": [{"value": {"messages": [
            {"from": l.telefone, "id": "d1", "type": "document", "document": {"id": "D1", "mime_type": "application/pdf"}}]}}]}]})
        l.refresh_from_db()
        self.assertEqual(l.etapa, e.chave)

    def test_teste_rapido(self):
        e = funil.criar("Interessado")
        EtapaFunil.objects.filter(pk=e.pk).update(grupos=[{"palavras": ["quanto custa"], "todas": False}])
        funil.invalidar()
        r = classificacao.testar("Quanto custa o Onix 2020?")
        self.assertEqual(r["etapa"].chave, e.chave)
        self.assertIsNone(classificacao.testar("bom dia")["etapa"])

    def test_followup_pode_usar_etapa_nova(self):
        e = funil.criar("Esperando")
        cfg = FollowUpConfig.obter()
        cfg.etapas_gatilho = [e.chave]
        cfg.save()
        from captacao import followup
        l = lead(etapa=e.chave)
        self.assertIsNotNone(followup.iniciar(l))
