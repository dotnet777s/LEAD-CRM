from datetime import timedelta
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone

from captacao import classificacao, followup, portais, whatsapp
from captacao.models import (Etiqueta, FollowUp, FollowUpConfig, Historico, Lead, MensagemProgramada, RegraClassificacao,
                             WhatsAppMensagem)

from .test_central import API, Logado, lead, msg, resposta_ok
from .test_portais import OLX, WHATSAPP, email_de


def ativar_followup():
    """Como se o follow-up por estágio tivesse sido ligado há um dia (só vale para quem entra na etapa depois)."""
    c = FollowUpConfig.obter()
    c.vale_desde = timezone.now() - timedelta(days=1)
    c.save()
    return c


def msg_cliente(l, texto, wamid="x1"):
    payload = {"entry": [{"changes": [{"value": {"messages": [{"from": l.telefone, "id": wamid, "type": "text", "text": {"body": texto}}]}}]}]}
    whatsapp.processar_webhook(payload)
    l.refresh_from_db()


class FollowUpPorEstagioTests(TestCase):
    def setUp(self):
        ativar_followup()

    def test_lead_na_etapa_marcada_entra_sozinho(self):
        l = lead()
        self.assertEqual(followup.sincronizar(), 1)
        fu = l.followups.get()
        self.assertEqual(fu.status, FollowUp.Status.ATIVO)
        self.assertEqual(followup.sincronizar(), 0)                 # não repete

    def test_primeira_tentativa_conta_da_entrada_na_etapa(self):
        l = lead()
        Lead.objects.filter(pk=l.pk).update(etapa_desde=timezone.now() - timedelta(hours=5))
        followup.sincronizar()
        self.assertLessEqual(l.followups.get().proximo_em, timezone.now())     # já venceu: aparece em "Fazer agora"
        self.assertEqual(len(followup.devidos()), 1)

    def test_etapa_fora_do_gatilho_nao_entra(self):
        lead(etapa=Lead.Etapa.QUALIFICADO)
        lead(telefone="5581911110000", etapa=Lead.Etapa.VISITA)
        self.assertEqual(followup.sincronizar(), 0)

    def test_so_vale_para_quem_entrou_depois_da_ativacao(self):
        l = lead()
        Lead.objects.filter(pk=l.pk).update(etapa_desde=timezone.now() - timedelta(days=10))
        self.assertEqual(followup.sincronizar(), 0)

    def test_etiqueta_que_impede_e_pedido_de_sair(self):
        a, b = lead(), lead(telefone="5581911110000", nao_contatar=True)
        a.etiquetas.add(Etiqueta.objects.create(nome="Não contatar", bloqueia_followup=True))
        self.assertEqual(followup.sincronizar(), 0)
        self.assertEqual(b.followups.count(), 0)

    def test_quem_ja_respondeu_depois_de_entrar_fica_de_fora(self):
        l = lead()
        msg(l, "in", "oi")
        self.assertEqual(followup.sincronizar(), 0)

    def test_sair_da_etapa_encerra_o_follow_up(self):
        from captacao import services
        l = lead()
        followup.sincronizar()
        services.mudar_etapa(l, Lead.Etapa.QUALIFICADO)
        self.assertEqual(l.followups.get().status, FollowUp.Status.ENCERRADO)

    def test_abrir_whatsapp_nao_inicia_mais_por_conta_propria(self):
        from captacao import services
        l = lead()
        services.marcar_atendimento(l)
        self.assertEqual(l.followups.count(), 0)

    def test_inicio_manual_vale_em_qualquer_etapa_aberta(self):
        l = lead(etapa=Lead.Etapa.QUALIFICADO)
        self.assertIsNone(followup.iniciar(l))
        self.assertIsNotNone(followup.iniciar(l, manual=True))

    def test_mudar_as_etapas_na_configuracao(self):
        gestor = get_user_model().objects.create_user("g", password="x", is_staff=True)
        self.client.force_login(gestor)
        followup.garantir_passos()
        dados = {"ativo": "on", "hora_inicio": "8", "hora_fim": "20", "etapas": ["novo", "qualificado"]}
        from captacao.models import FollowUpPasso
        for p in FollowUpPasso.objects.all():
            dados[f"p{p.pk}_ativo"] = "on"
            dados[f"p{p.pk}_v0"] = "Oi {nome}"
            dados[f"p{p.pk}_dias"] = "1"
            dados[f"p{p.pk}_horas"] = "0"
        self.client.post("/followup/configurar/", dados)
        c = FollowUpConfig.obter()
        self.assertEqual(c.etapas_gatilho, ["novo", "qualificado"])
        self.assertGreater(c.vale_desde, timezone.now() - timedelta(minutes=1))     # novo gatilho vale daqui para a frente


class ClassificacaoTests(TestCase):
    def test_cliente_que_responde_sai_de_novo(self):
        l = lead()
        msg_cliente(l, "Bom dia")
        self.assertEqual(l.etapa, Lead.Etapa.ATENDIMENTO)
        self.assertTrue(l.historico.filter(texto__icontains="Funil automático").exists())

    def test_palavras_poem_etiqueta_sem_acento_e_sem_caixa(self):
        l = lead()
        msg_cliente(l, "Qual o PREÇO? Aceita troca?")
        nomes = set(l.etiquetas.values_list("nome", flat=True))
        self.assertTrue({"Interessado", "Troca"} <= nomes)

    def test_regra_leva_para_qualificado(self):
        l = lead(etapa=Lead.Etapa.ATENDIMENTO)
        msg_cliente(l, "Meu CPF é 000 e minha data de nascimento 01/01/1990")
        self.assertEqual(l.etapa, Lead.Etapa.QUALIFICADO)
        self.assertIn("Financiamento", set(l.etiquetas.values_list("nome", flat=True)))

    def test_a_etapa_so_avanca(self):
        l = lead(etapa=Lead.Etapa.VISITA)
        msg_cliente(l, "meu cpf e data de nascimento")
        self.assertEqual(l.etapa, Lead.Etapa.VISITA)

    def test_comprou_outro_vai_para_perdido_com_motivo(self):
        l = lead(etapa=Lead.Etapa.ATENDIMENTO)
        msg_cliente(l, "Já comprei outro carro, obrigado")
        self.assertEqual((l.etapa, l.motivo_perda), (Lead.Etapa.PERDIDO, Lead.MotivoPerda.COMPROU_OUTRO))

    def test_vendido_nao_e_mexido(self):
        l = lead(etapa=Lead.Etapa.VENDIDO)
        msg_cliente(l, "não quero mais")
        self.assertEqual(l.etapa, Lead.Etapa.VENDIDO)

    def test_regra_desligada_nao_dispara(self):
        classificacao.garantir_padroes()
        RegraClassificacao.objects.update(ativa=False)
        l = lead()
        msg_cliente(l, "quanto custa?")
        self.assertEqual(l.etiquetas.count(), 0)

    def test_palavra_inteira(self):
        classificacao.garantir_padroes()
        self.assertNotIn("Pediu preço ou condições", classificacao.aplicar_regras(lead(), "valorizado demais"))
        self.assertIn("Pediu preço ou condições", classificacao.aplicar_regras(lead(telefone="5581911110000"), "qual o valor?"))

    def test_lead_de_portal_ganha_etiqueta_de_origem(self):
        r = portais.processar(email_de(OLX, "x", WHATSAPP))
        self.assertIn("OLX", set(r.lead.etiquetas.values_list("nome", flat=True)))

    def test_padroes_nao_duplicam(self):
        classificacao.garantir_padroes()
        classificacao.garantir_padroes()
        self.assertEqual(Etiqueta.objects.filter(nome="Interessado").count(), 1)


class TelasFunilTests(Logado):
    def test_etiqueta_na_conversa_e_na_ficha(self):
        l = lead()
        classificacao.garantir_padroes()
        e = Etiqueta.objects.get(nome="Quente")
        self.client.post(f"/lead/{l.pk}/etiqueta/add/", {"etiqueta": e.pk})
        self.assertIn(e, l.etiquetas.all())
        self.assertContains(self.client.get(f"/lead/{l.pk}/"), "Quente")
        self.assertContains(self.client.get(f"/conversas/{l.pk}/"), "Quente")
        self.client.post(f"/lead/{l.pk}/etiqueta/remove/", {"etiqueta": e.pk, "volta": "detalhe"})
        self.assertEqual(l.etiquetas.count(), 0)

    def test_quadro_mostra_e_filtra_por_etiqueta(self):
        a, b = lead(nome="Com etiqueta"), lead(nome="Sem etiqueta", telefone="5581911110000")
        e = Etiqueta.objects.create(nome="VIP", cor="#ff0000")
        a.etiquetas.add(e)
        html = self.client.get("/quadro/").content.decode()
        self.assertIn("VIP", html)
        filtrado = self.client.get(f"/quadro/?etiqueta={e.pk}").content.decode()
        self.assertIn("Com etiqueta", filtrado)
        self.assertNotIn("Sem etiqueta", filtrado)
        self.assertContains(self.client.get(f"/contatos/?etiqueta={e.pk}"), "Com etiqueta")
        self.assertNotContains(self.client.get(f"/contatos/?etiqueta={e.pk}"), "Sem etiqueta")

    def test_tela_do_funil_e_cadastros(self):
        self.assertContains(self.client.get("/funil/configurar/"), "Regras automáticas")
        self.client.post("/funil/configurar/etiqueta/", {"nome": "Pós-venda", "cor": "#123456", "bloqueia": "on"})
        e = Etiqueta.objects.get(nome="Pós-venda")
        self.assertTrue(e.bloqueia_followup)
        self.client.post("/funil/configurar/regra/", {"nome": "Garantia", "palavras": "garantia, revisão", "etiqueta": e.pk})
        r = RegraClassificacao.objects.get(nome="Garantia")
        self.assertEqual(r.etiqueta, e)
        self.client.post("/funil/configurar/regra/", {"nome": "Perde", "palavras": "tchau", "etapa": "perdido"})
        self.assertFalse(RegraClassificacao.objects.filter(nome="Perde").exists())          # perdido exige motivo
        self.client.post(f"/funil/configurar/regra_ativar/{r.pk}/")
        r.refresh_from_db()
        self.assertFalse(r.ativa)
        self.client.post(f"/funil/configurar/etiqueta_excluir/{e.pk}/")
        self.assertFalse(Etiqueta.objects.filter(pk=e.pk).exists())

    def test_so_gestor_configura(self):
        self.client.force_login(get_user_model().objects.create_user("vend", password="x"))
        self.assertEqual(self.client.post("/funil/configurar/etiqueta/", {"nome": "X"}).status_code, 403)

    def test_visao_geral(self):
        l = lead(telefone="5581999998888")
        lead(telefone="5511988887777", nome="Paulista", carro_texto="Onix 2020 · R$ 50.000", portal="olx", origem="portal")
        msg(l, "out", "oi")
        r = self.client.get("/visao-geral/?dias=30")
        self.assertContains(r, "PE")
        self.assertContains(r, "SP")
        self.assertContains(r, "Onix 2020")
        self.assertEqual(self.client.get("/visao-geral/?dias=abc").status_code, 200)


@override_settings(**API)
class ProgramadasTests(Logado):
    @mock.patch("captacao.whatsapp.requests.post")
    def test_programar_cancelar_e_enviar(self, post):
        post.return_value = resposta_ok()
        l = lead()
        msg(l, "in", "oi")
        futuro = timezone.localtime(timezone.now() + timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M")
        self.client.post(f"/conversas/{l.pk}/programar/", {"texto": "Bom dia!", "quando": futuro})
        p = MensagemProgramada.objects.get()
        self.assertEqual(p.status, "pendente")
        self.assertEqual(whatsapp.enviar_programadas(), 0)                       # ainda não é a hora
        MensagemProgramada.objects.update(enviar_em=timezone.now() - timedelta(minutes=1))
        self.assertEqual(whatsapp.enviar_programadas(), 1)
        self.assertEqual(post.call_args.kwargs["json"]["text"]["body"], "Bom dia!")
        p.refresh_from_db()
        self.assertEqual(p.status, "enviada")

    def test_horario_no_passado_e_recusado(self):
        l = lead()
        passado = timezone.localtime(timezone.now() - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M")
        self.client.post(f"/conversas/{l.pk}/programar/", {"texto": "x", "quando": passado})
        self.assertEqual(MensagemProgramada.objects.count(), 0)

    @mock.patch("captacao.whatsapp.requests.post")
    def test_fora_da_janela_falha_com_motivo(self, post):
        l = lead()
        p = MensagemProgramada.objects.create(lead=l, texto="x", enviar_em=timezone.now() - timedelta(minutes=1))
        whatsapp.enviar_programadas()
        p.refresh_from_db()
        self.assertEqual(p.status, "falhou")
        self.assertIn("24 h", p.erro)

    def test_cancelar(self):
        l = lead()
        p = MensagemProgramada.objects.create(lead=l, texto="x", enviar_em=timezone.now() + timedelta(hours=1))
        self.client.post(f"/conversas/programada/{p.pk}/cancelar/")
        p.refresh_from_db()
        self.assertEqual(p.status, "cancelada")

    @mock.patch("captacao.whatsapp.requests.post")
    def test_assinar_webhook(self, post):
        post.return_value = mock.Mock(status_code=200, json=lambda: {"success": True})
        r = self.client.post("/whatsapp/assinar/")
        self.assertIn("ativado", r["Location"])
        self.assertTrue(post.call_args.args[0].endswith("/999/subscribed_apps"))
